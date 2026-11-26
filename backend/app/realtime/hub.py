"""In-process pub/sub of team events to WebSocket connections (PLAN section 7).

* One `Hub` per API process (created in the app lifespan, `app.state.hub`). A scale-out path (Postgres
  LISTEN/NOTIFY or Redis fan-out behind the same interface) belongs in the decision record, not here.
* The hub only *notifies*. Postgres owns meeting state; every message is built from a fresh DB read.
* asyncio-safe: connection tables are only mutated synchronously between awaits; every connection has its own
  send lock (a socket never sees interleaved frames); a per-team lock keeps lazily built messages in order.
* Publishing never raises: a socket that errors or stalls is dropped, the others still get the message.
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from starlette.websockets import WebSocket

Message = dict[str, Any]
log = logging.getLogger(__name__)


@dataclass(eq=False)
class _Connection:
    ws: WebSocket
    user_id: str
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Hub:
    #: A client that cannot take a frame within this many seconds is dropped, so it cannot stall the team.
    SEND_TIMEOUT_S = 5.0

    def __init__(self) -> None:
        self._connections: dict[str, list[_Connection]] = {}
        self._team_locks: dict[str, asyncio.Lock] = {}

    # --- membership ---------------------------------------------------------------------------------------

    async def connect(self, team_id: str, user_id: str, ws: WebSocket) -> bool:
        """Register an accepted socket; call `disconnect` (in a `finally`) when it closes.

        Presence is refcounted per user: returns True only when this is the user's FIRST open socket on the
        team, i.e. their online state flipped to online (a second tab or a StrictMode remount returns False).
        """
        was_online = user_id in self.online_user_ids(team_id)
        self._connections.setdefault(team_id, []).append(_Connection(ws=ws, user_id=user_id))
        return not was_online

    async def disconnect(self, team_id: str, user_id: str, ws: WebSocket) -> bool:
        """Unregister a socket (idempotent). Returns True when the user has no socket left on the team, i.e.
        their online state flipped to offline."""
        self._drop(team_id, ws)
        return user_id not in self.online_user_ids(team_id)

    def online_user_ids(self, team_id: str) -> set[str]:
        return {c.user_id for c in self._connections.get(team_id, ())}

    def has_connections(self, team_id: str) -> bool:
        return bool(self._connections.get(team_id))

    def _drop(self, team_id: str, ws: WebSocket) -> None:
        remaining = [c for c in self._connections.get(team_id, ()) if c.ws is not ws]
        if remaining:
            self._connections[team_id] = remaining
        else:
            self._connections.pop(team_id, None)

    # --- sending ------------------------------------------------------------------------------------------

    def team_lock(self, team_id: str) -> asyncio.Lock:
        """Serializes build-and-send sequences of one team so clients see state messages in build order."""
        return self._team_locks.setdefault(team_id, asyncio.Lock())

    async def _send(self, team_id: str, conn: _Connection, message: Message) -> bool:
        try:
            async with conn.lock:
                await asyncio.wait_for(conn.ws.send_json(message), timeout=self.SEND_TIMEOUT_S)
        except Exception:  # dead, closing or stalled socket: drop it, never propagate
            self._drop(team_id, conn.ws)
            return False
        return True

    async def send_to(self, team_id: str, ws: WebSocket, message: Message) -> bool:
        """Send to one registered socket (the connect-time snapshot). False if it is gone."""
        for conn in list(self._connections.get(team_id, ())):
            if conn.ws is ws:
                return await self._send(team_id, conn, message)
        return False

    async def publish(self, team_id: str, message: Message, *, exclude: WebSocket | None = None) -> None:
        """Send `message` to every socket of the team (except `exclude`), concurrently.

        Dead sockets are dropped silently; this never raises.
        """
        conns = [c for c in self._connections.get(team_id, ()) if c.ws is not exclude]
        if conns:
            await asyncio.gather(*(self._send(team_id, c, message) for c in conns))

    async def publish_lazy(
        self,
        team_id: str,
        build: Callable[[], Awaitable[Message | None]],
        *,
        exclude: WebSocket | None = None,
    ) -> None:
        """Build a message *after* taking the team lock, then publish it (skipped when nobody listens).

        Because each message is built from the database under the lock, messages reach every client in the
        order they were built, so an older state can never overtake a newer one.
        """
        if not self.has_connections(team_id):
            return
        async with self.team_lock(team_id):
            try:
                message = await build()
            except Exception:  # a failed read must never fail the request whose commit triggered it
                log.exception("could not build realtime message for team %s", team_id)
                return
            if message is not None:
                await self.publish(team_id, message, exclude=exclude)

    async def close_all(self) -> None:
        """Shutdown helper: close every socket (best effort)."""
        for team_id, conns in list(self._connections.items()):
            for conn in conns:
                with contextlib.suppress(Exception):
                    await conn.ws.close(code=1001)
            self._connections.pop(team_id, None)
