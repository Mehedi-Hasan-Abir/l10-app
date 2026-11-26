"""Observability basics (HARDENING H4): JSON logs, a request id on every request, optional Sentry.

* `configure_logging`      one JSON object per line on stdout, via the root logger. Idempotent.
* `RequestContextMiddleware`  pure ASGI. For HTTP requests only: reads or creates `X-Request-ID`, keeps it in
  a `ContextVar` (so every log record emitted while the request runs carries it), echoes it on the response
  and writes ONE access record. WebSocket and lifespan scopes pass through untouched.
* `init_sentry`            does nothing unless `SENTRY_DSN` is set.

The access record holds method, path (never the query string), status, duration and, when the request carried
a valid session cookie, the user id. Cookies, headers and bodies are never logged. The user id comes from
verifying the signed cookie (an HMAC check, no database work). Level: INFO, except a successful
`GET /api/health` / `GET /api/ready` (DEBUG: the probes poll every few seconds), WARNING for 5xx and ERROR
when the app raised.
"""

import json
import logging
import os
import re
import sys
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

import sentry_sdk
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.auth import COOKIE_NAME, read_session

REQUEST_ID_HEADER = "X-Request-ID"
# A client-supplied id is accepted only in this shape (fullmatch: `$` would also accept a trailing newline).
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_HANDLER_MARK = "_l10_json_handler"
# Healthchecks and load balancers poll these: a successful hit is logged at DEBUG, a failing one is not.
_PROBE_PATHS = frozenset({"/api/health", "/api/ready"})

access_log = logging.getLogger("app.access")
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """The id of the HTTP request being served, or None outside one."""
    return request_id_var.get()


# --- logging --------------------------------------------------------------------------------------------

# Only these `extra=` fields reach the output, so a stray attribute can never leak into the logs.
_EXTRA_FIELDS = ("method", "path", "status", "duration_ms", "user_id")


class JsonFormatter(logging.Formatter):
    """One JSON object per record: ts, level, logger, msg, request_id, then the allowed extras, then exc."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds")
        payload: dict[str, Any] = {
            "ts": ts.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id is not None:
            payload["request_id"] = request_id
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever `sys.stdout` is at emit time (like logging's own stderr handler), so a stream that
    was swapped later (test capture, a redirect) is never written after it was closed."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self) -> Any:
        return sys.stdout


def _resolve_level(level: str | int | None) -> int:
    raw = os.environ.get("LOG_LEVEL", "INFO") if level is None else level
    if isinstance(raw, int):
        return raw
    resolved = logging.getLevelNamesMapping().get(raw.strip().upper())
    return resolved if resolved is not None else logging.INFO


def configure_logging(level: str | int | None = None) -> None:
    """JSON logs on the root logger. `level` defaults to the `LOG_LEVEL` env var (INFO); an unknown name
    falls back to INFO. Safe to call again (e.g. every `create_app()` in tests): it reuses its own handler."""
    root = logging.getLogger()
    handler = next((h for h in root.handlers if getattr(h, _HANDLER_MARK, False)), None)
    if handler is None:
        handler = _StdoutHandler()
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)
    handler.setFormatter(JsonFormatter())
    root.setLevel(_resolve_level(level))


# --- request context ------------------------------------------------------------------------------------


def _request_id_for(scope: Scope) -> str:
    supplied = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if supplied is not None and _REQUEST_ID_RE.fullmatch(supplied):
        return supplied
    return uuid.uuid4().hex


def _user_id_for(scope: Scope) -> str | None:
    """The user behind the request, if known without touching the database: an id a dependency stored in
    `request.state.user_id`, else the id inside a validly signed session cookie."""
    try:
        state = scope.get("state") or {}
        if state.get("user_id"):
            return str(state["user_id"])
        settings = getattr(getattr(scope.get("app"), "state", None), "settings", None)
        if settings is None:
            return None
        return read_session(settings, HTTPConnection(scope).cookies.get(COOKIE_NAME))
    except Exception:  # logging must never break a request
        return None


def _access_level(method: str, path: str, status: int, error: Exception | None) -> int:
    """ERROR when the app raised, WARNING for 5xx, DEBUG for a successful liveness/readiness probe (they fire
    every few seconds and would drown the real traffic), INFO for everything else."""
    if error:
        return logging.ERROR
    if status >= 500:
        return logging.WARNING
    if status < 400 and method == "GET" and path in _PROBE_PATHS:
        return logging.DEBUG
    return logging.INFO


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":  # websocket and lifespan are not ours
            await self.app(scope, receive, send)
            return

        request_id = _request_id_for(scope)
        token = request_id_var.set(request_id)
        if sentry_sdk.is_initialized():
            sentry_sdk.set_tag("request_id", request_id)
        started = time.perf_counter_ns()
        status = 500  # what the client sees if the app dies before it starts a response
        error: Exception | None = None

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            error = exc
            raise
        finally:
            duration_ms = (time.perf_counter_ns() - started) // 10_000 / 100  # 0.01 ms steps
            method, path = scope["method"], scope["path"]  # `path` excludes the query string
            level = _access_level(method, path, status, error)
            access_log.log(
                level,
                "%s %s %d",
                method,
                path,
                status,
                exc_info=error,
                extra={
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": duration_ms,
                    "user_id": _user_id_for(scope),
                },
            )
            request_id_var.reset(token)


# --- sentry ---------------------------------------------------------------------------------------------


def init_sentry() -> bool:
    """Start Sentry only when `SENTRY_DSN` is set. No PII, no request bodies, tracing off unless
    `SENTRY_TRACES_SAMPLE_RATE` (0..1) says otherwise. Returns whether Sentry was started."""
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    try:
        traces_sample_rate = min(1.0, max(0.0, float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0"))))
    except ValueError:
        traces_sample_rate = 0.0
    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        max_request_body_size="never",
        traces_sample_rate=traces_sample_rate,
    )
    return True
