"""H4 observability: request ids, JSON access logs, `/api/ready`, WebSocket pass-through, Sentry opt-in.

The JSON log lines are read from the real output path (root handler -> formatter -> `sys.stdout`) with
`capsys`, so these tests also prove the ContextVar reaches the formatter.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import sentry_sdk
from fastapi import FastAPI
from httpx import ASGITransport

from app.config import Settings
from app.main import create_app
from app.observability import JsonFormatter, configure_logging, get_request_id, init_sentry
from tests.api.helpers import Login, ws_connect

LogLines = Callable[[], list[dict[str, Any]]]
HEX_ID = re.compile(r"[0-9a-f]{32}")


@pytest.fixture(autouse=True)
def _restore_root_logging() -> Iterator[None]:
    """`configure_logging` is process-wide; give every test back the level it found."""
    root = logging.getLogger()
    level = root.level
    yield
    root.setLevel(level)


@pytest.fixture
def log_lines(capsys: pytest.CaptureFixture[str]) -> LogLines:
    """Everything logged since the last call, parsed. Every stdout line must be one JSON object."""
    capsys.readouterr()  # drop what the fixtures logged

    def read() -> list[dict[str, Any]]:
        return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]

    return read


def access_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [line for line in lines if line["logger"] == "app.access"]


# --- request id -----------------------------------------------------------------------------------------


async def test_request_id_is_generated_when_absent_and_echoed(client: httpx.AsyncClient) -> None:
    first = await client.get("/api/health")
    second = await client.get("/api/health")
    assert HEX_ID.fullmatch(first.headers["X-Request-ID"])
    assert HEX_ID.fullmatch(second.headers["X-Request-ID"])
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


@pytest.mark.parametrize("supplied", ["abc-123_X.y", "a", "9" * 64])
async def test_valid_client_request_id_is_propagated(client: httpx.AsyncClient, supplied: str) -> None:
    response = await client.get("/api/health", headers={"X-Request-ID": supplied})
    assert response.headers["X-Request-ID"] == supplied


@pytest.mark.parametrize(
    "supplied",
    ["a" * 65, "has space", "semi;colon", "slash/slash", 'quote"', "caf\u00e9".encode(), "", "<script>"],
    ids=["too-long", "space", "semicolon", "slash", "quote", "non-ascii", "empty", "markup"],
)
async def test_invalid_client_request_id_is_replaced(
    client: httpx.AsyncClient, supplied: str | bytes
) -> None:
    response = await client.get("/api/health", headers={"X-Request-ID": supplied})
    assert response.status_code == 200
    assert HEX_ID.fullmatch(response.headers["X-Request-ID"])


async def test_request_id_header_survives_error_handlers(client: httpx.AsyncClient) -> None:
    """The AppError (401) and validation (422) handlers still answer, with their bodies and the header."""
    unauthenticated = await client.get("/api/auth/me", headers={"X-Request-ID": "req-401"})
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["detail"]["code"] == "not_authenticated"
    assert unauthenticated.headers["X-Request-ID"] == "req-401"

    invalid = await client.post(
        "/api/auth/login", json={"unexpected": 1}, headers={"X-Request-ID": "req-422"}
    )
    assert invalid.status_code == 422
    assert isinstance(invalid.json()["detail"], list)
    assert invalid.headers["X-Request-ID"] == "req-422"

    missing = await client.get("/api/nope")
    assert missing.status_code == 404
    assert HEX_ID.fullmatch(missing.headers["X-Request-ID"])


# --- JSON logs ------------------------------------------------------------------------------------------


async def test_access_log_line_is_json_with_the_request_fields(
    client: httpx.AsyncClient, log_lines: LogLines
) -> None:
    response = await client.get("/api/auth/users", headers={"X-Request-ID": "trace-me-1"})
    (line,) = access_lines(log_lines())
    assert line["request_id"] == "trace-me-1" == response.headers["X-Request-ID"]
    assert line["method"] == "GET"
    assert line["path"] == "/api/auth/users"
    assert line["status"] == 200
    assert isinstance(line["duration_ms"], int | float) and line["duration_ms"] >= 0
    assert line["level"] == "INFO"
    assert line["msg"] == "GET /api/auth/users 200"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", line["ts"])
    assert "user_id" not in line  # anonymous


@pytest.mark.parametrize("probe", ["/api/health", "/api/ready"])
async def test_successful_probes_log_at_debug_so_info_stays_quiet(
    client: httpx.AsyncClient, log_lines: LogLines, probe: str
) -> None:
    """Liveness/readiness polls are noise at INFO: silent by default, still available at LOG_LEVEL=DEBUG."""
    configure_logging("INFO")
    log_lines()
    assert (await client.get(probe, headers={"X-Request-ID": "probe-info"})).status_code == 200
    assert access_lines(log_lines()) == []

    configure_logging("DEBUG")
    log_lines()
    response = await client.get(probe, headers={"X-Request-ID": "probe-debug"})
    (line,) = access_lines(log_lines())
    assert line["level"] == "DEBUG"
    assert line["msg"] == f"GET {probe} 200"
    assert line["request_id"] == "probe-debug" == response.headers["X-Request-ID"]


async def test_only_successful_probe_gets_are_demoted(client: httpx.AsyncClient, log_lines: LogLines) -> None:
    """A probe that fails (405 on a POST) and every other endpoint keep INFO; a failed ready is WARNING
    (see `test_ready_is_503_when_the_database_is_unreachable`)."""
    configure_logging("INFO")
    log_lines()
    assert (await client.post("/api/health")).status_code == 405
    assert (await client.get("/api/auth/users")).status_code == 200
    health_post, users = access_lines(log_lines())
    assert (health_post["path"], health_post["status"], health_post["level"]) == ("/api/health", 405, "INFO")
    assert (users["path"], users["status"], users["level"]) == ("/api/auth/users", 200, "INFO")


async def test_access_log_has_user_id_but_never_cookie_query_or_body(
    login: Login, log_lines: LogLines
) -> None:
    u2 = await login("u2")
    cookie_value = next(iter(u2.cookies.jar)).value
    assert cookie_value
    log_lines()  # discard the login request

    response = await u2.get("/api/auth/me", params={"token": "sekret-query-value"})
    assert response.status_code == 200
    post = await u2.post("/api/auth/login", json={"user_id": "sekret-body-value"})
    assert post.status_code == 404  # unknown user

    lines = log_lines()
    raw = json.dumps([line for line in lines if line["logger"] != "httpx"])  # the test client logs its URLs
    for secret in (cookie_value, "sekret-query-value", "sekret-body-value", "l10_session"):
        assert secret not in raw
    me, _login = access_lines(lines)
    assert me["path"] == "/api/auth/me"  # no query string
    assert me["user_id"] == "u2"
    assert me["status"] == 200


async def test_tampered_session_cookie_gives_no_user_id(
    client: httpx.AsyncClient, log_lines: LogLines
) -> None:
    response = await client.get("/api/auth/me", headers={"Cookie": "l10_session=forged.value.here"})
    assert response.status_code == 401
    (line,) = access_lines(log_lines())
    assert line["status"] == 401
    assert "user_id" not in line


async def test_app_logs_during_a_request_carry_its_request_id(app: FastAPI, log_lines: LogLines) -> None:
    inner = logging.getLogger("app.test.inner")

    async def noisy() -> dict[str, str | None]:
        inner.info("doing the work")
        await asyncio.sleep(0)  # the id must survive a task switch
        inner.warning("still working")
        return {"seen": get_request_id()}

    app.add_api_route("/api/_test/noisy", noisy)
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/_test/noisy", headers={"X-Request-ID": "ctx-42"})
    assert response.json() == {"seen": "ctx-42"}

    lines = log_lines()
    inner_lines = [line for line in lines if line["logger"] == "app.test.inner"]
    assert [line["msg"] for line in inner_lines] == ["doing the work", "still working"]
    assert {line["request_id"] for line in inner_lines} == {"ctx-42"}
    assert get_request_id() is None  # reset once the request is over


async def test_unhandled_exception_is_logged_as_500_and_still_raised(
    app: FastAPI, log_lines: LogLines
) -> None:
    async def boom() -> None:
        raise RuntimeError("kaput")

    app.add_api_route("/api/_test/boom", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        response = await c.get("/api/_test/boom", headers={"X-Request-ID": "boom-1"})
    assert response.status_code == 500

    (line,) = access_lines(log_lines())
    assert line["status"] == 500
    assert line["level"] == "ERROR"
    assert line["request_id"] == "boom-1"
    assert "RuntimeError: kaput" in line["exc"]
    assert get_request_id() is None


def test_formatter_omits_request_id_outside_a_request_and_ignores_unlisted_extras() -> None:
    record = logging.LogRecord("some.logger", logging.WARNING, __file__, 1, "hello %s", ("world",), None)
    record.cookie = "must-not-appear"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["msg"] == "hello world"
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "some.logger"
    assert "request_id" not in payload
    assert "must-not-appear" not in json.dumps(payload)


def test_configure_logging_is_idempotent_and_honours_the_level(monkeypatch: pytest.MonkeyPatch) -> None:
    root = logging.getLogger()

    def ours() -> list[logging.Handler]:
        return [h for h in root.handlers if getattr(h, "_l10_json_handler", False)]

    configure_logging("WARNING")
    configure_logging("WARNING")
    create_app()  # create_app() configures logging too; tests build many apps
    create_app()
    assert len(ours()) == 1
    assert isinstance(ours()[0].formatter, JsonFormatter)

    configure_logging("DEBUG")
    assert root.level == logging.DEBUG
    configure_logging("not-a-level")
    assert root.level == logging.INFO

    monkeypatch.setenv("LOG_LEVEL", "error")
    configure_logging()
    assert root.level == logging.ERROR
    assert len(ours()) == 1


# --- readiness ------------------------------------------------------------------------------------------


async def test_ready_is_200_with_the_test_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert HEX_ID.fullmatch(response.headers["X-Request-ID"])
    assert (await client.get("/api/health")).json() == {"status": "ok"}


async def test_ready_is_503_when_the_database_is_unreachable(
    test_settings: Settings, log_lines: LogLines
) -> None:
    unreachable = test_settings.model_copy(
        update={"database_url": "postgresql+asyncpg://l10:l10@127.0.0.1:1/l10_test"}
    )
    application = create_app(settings=unreachable, clock=None)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as c:
            response = await c.get("/api/ready", headers={"X-Request-ID": "ready-down"})
            health = await c.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
    assert response.headers["X-Request-ID"] == "ready-down"
    assert health.status_code == 200  # liveness does not depend on the database

    ready_line = next(line for line in access_lines(log_lines()) if line["path"] == "/api/ready")
    assert ready_line["status"] == 503
    assert ready_line["level"] == "WARNING"


async def test_ready_is_503_when_the_database_hangs(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class HangingSession:
        async def __aenter__(self) -> "HangingSession":
            await asyncio.sleep(30)
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr("app.main.READY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(app.state, "session_factory", HangingSession)
    response = await asyncio.wait_for(client.get("/api/ready"), timeout=5)
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


async def test_ready_is_documented_in_openapi_under_the_health_tag(client: httpx.AsyncClient) -> None:
    paths = (await client.get("/api/openapi.json")).json()["paths"]
    assert paths["/api/health"]["get"]["tags"] == ["health"]
    ready = paths["/api/ready"]["get"]
    assert ready["tags"] == ["health"]
    assert {"200", "503"} <= set(ready["responses"])


# --- websockets pass through ----------------------------------------------------------------------------


async def test_websocket_connects_through_the_middleware_without_an_access_line(
    app: FastAPI, login: Login, log_lines: LogLines
) -> None:
    u2 = await login("u2")
    log_lines()
    async with ws_connect(app, u2) as ws:
        assert await ws.receive_json(timeout=3) == {"type": "snapshot", "meeting": None}
    assert [line for line in access_lines(log_lines()) if line["path"].startswith("/ws/")] == []


# --- sentry ---------------------------------------------------------------------------------------------


def test_init_sentry_is_a_noop_without_a_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry() is False
    monkeypatch.setenv("SENTRY_DSN", "   ")
    assert init_sentry() is False
    create_app()
    assert calls == []


@pytest.mark.parametrize(
    ("rate", "expected"), [(None, 0.0), ("0.25", 0.25), ("oops", 0.0), ("7", 1.0), ("-1", 0.0)]
)
def test_init_sentry_with_a_dsn_never_sends_pii(
    monkeypatch: pytest.MonkeyPatch, rate: str | None, expected: float
) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setenv("SENTRY_DSN", "https://public@sentry.invalid/1")
    if rate is None:
        monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    else:
        monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", rate)
    assert init_sentry() is True
    (kwargs,) = calls
    assert kwargs["dsn"] == "https://public@sentry.invalid/1"
    assert kwargs["send_default_pii"] is False
    assert kwargs["max_request_body_size"] == "never"
    assert kwargs["traces_sample_rate"] == expected


async def test_request_id_is_tagged_on_sentry_events_when_sentry_is_active(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    tags: dict[str, str] = {}
    monkeypatch.setattr(sentry_sdk, "is_initialized", lambda: True)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda key, value: tags.__setitem__(key, value))
    await client.get("/api/health", headers={"X-Request-ID": "sentry-1"})
    assert tags == {"request_id": "sentry-1"}
