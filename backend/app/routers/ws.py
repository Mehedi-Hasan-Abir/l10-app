"""`WS /ws/teams/{team_id}`: server-push notifications for one team (PLAN section 7).

Mounted outside `/api` (the edge proxy routes `/ws/*` to the API). The socket only notifies; state lives in
Postgres and is changed through REST. A client that missed anything reconnects, gets a snapshot, and
invalidates its cached queries.

Handshake failures are close codes on an accepted socket (not an HTTP 403 before the upgrade) so a browser can
read them: `4401` not signed in, `4403` bad Origin or no access to the team. Nothing is sent before the close.
A DB session is opened only for the checks and the snapshot; it is never held for the socket's lifetime.

Presence is refcounted per user: `online` flips (and a `meeting` message is pushed) only when a user's FIRST
socket opens or their LAST socket closes, so a second tab or a remount stays silent.
"""

import asyncio
import contextlib
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketState

from app.auth import authenticate_websocket, load_team_context, origin_allowed
from app.errors import AppError
from app.realtime.hub import Hub
from app.services import meetings as meetings_service

log = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

PING_INTERVAL_S = 25.0
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403


async def _reject(websocket: WebSocket, code: int, reason: str) -> None:
    await websocket.accept()
    await websocket.close(code=code, reason=reason)


async def _ping_forever(websocket: WebSocket, hub: Hub, team_id: str) -> None:
    while True:
        await asyncio.sleep(PING_INTERVAL_S)
        if not await hub.send_to(team_id, websocket, {"type": "ping"}):
            return


@router.websocket("/ws/teams/{team_id}")
async def team_socket(websocket: WebSocket, team_id: str) -> None:
    """Server push for a team: `snapshot` on connect, then `meeting`, `invalidate` and `ping` messages."""
    app_state = websocket.app.state
    if not origin_allowed(websocket):
        await _reject(websocket, CLOSE_FORBIDDEN, "origin_not_allowed")
        return

    async with app_state.session_factory() as session:
        user = await authenticate_websocket(websocket, session)
        if user is None:
            await _reject(websocket, CLOSE_UNAUTHENTICATED, "not_authenticated")
            return
        try:
            await load_team_context(session, user, team_id, write=False)
        except AppError:
            await _reject(websocket, CLOSE_FORBIDDEN, "team_forbidden")
            return
        user_id = user.id

    hub = app_state.hub
    clock = app_state.clock
    factory = app_state.session_factory
    await websocket.accept()
    came_online = await hub.connect(team_id, user_id, websocket)
    pinger = asyncio.create_task(_ping_forever(websocket, hub, team_id))
    try:
        # Snapshot under the team lock: any state message that follows was built after it.
        async with hub.team_lock(team_id):
            snapshot = await meetings_service.snapshot_message(factory, hub, clock, team_id)
            await hub.send_to(team_id, websocket, snapshot)
        # Presence is refcounted per user: tell the team only when this user's state actually flipped
        # (a second tab or a remount of the same user stays silent). No-op without an active meeting.
        if came_online:
            await meetings_service.publish_meeting_state(hub, factory, clock, team_id, exclude=websocket)
        while True:
            message = await websocket.receive()  # the server ignores client messages; this detects close
            if message["type"] == "websocket.disconnect":
                break
    except Exception:  # abrupt client drop or a failed read: fall through to cleanup
        log.debug("websocket for team %s ended", team_id, exc_info=True)
    finally:
        pinger.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pinger
        went_offline = await hub.disconnect(team_id, user_id, websocket)
        if went_offline:  # the user's LAST socket closed
            await meetings_service.publish_meeting_state(hub, factory, clock, team_id)
        if websocket.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close()
