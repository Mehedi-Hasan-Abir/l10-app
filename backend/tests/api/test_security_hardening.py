"""H2 hardening: the sliding-window limiter, the login rate limit over HTTP, and the production config guard.

The shared test settings switch the login limiter off (`login_rate_limit_per_minute=0`) because nearly every
test signs in from the same address; the API tests here build their own app with a limit of 3.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from pydantic import ValidationError

from app.clock import ManualClock
from app.config import DEV_SECRET_KEY, Settings
from app.main import create_app
from app.ratelimit import SlidingWindowLimiter

STRONG_SECRET = "s3cr3t-" + "x" * 32  # 39 chars, not the dev default


class FakeClock:
    """A monotonic clock the test moves by hand (seconds)."""

    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --- SlidingWindowLimiter -------------------------------------------------------------------------------


def test_limiter_allows_up_to_the_limit_then_blocks_the_next_hit() -> None:
    limiter = SlidingWindowLimiter(3, 60, clock=FakeClock())
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") == 60  # blocked; the oldest hit leaves the window in 60 s
    assert limiter.hit("a") == 60  # and a blocked hit does not extend the block


def test_limiter_retry_after_counts_down_and_recovers_after_the_window() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(3, 60, clock=clock)
    for _ in range(3):
        assert limiter.hit("a") is None

    clock.advance(10)
    assert limiter.hit("a") == pytest.approx(50)
    clock.advance(49)
    assert limiter.hit("a") == pytest.approx(1)  # hammering while blocked never moves the deadline
    clock.advance(1)  # exactly one window after the first hits
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") is not None


def test_limiter_window_slides_instead_of_resetting_all_at_once() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(3, 60, clock=clock)
    assert limiter.hit("a") is None  # t=0
    clock.advance(30)
    assert limiter.hit("a") is None  # t=30
    clock.advance(20)
    assert limiter.hit("a") is None  # t=50
    clock.advance(5)
    assert limiter.hit("a") == pytest.approx(5)  # t=55: three hits in the window; the t=0 one expires at 60
    clock.advance(5)
    assert limiter.hit("a") is None  # t=60: only the t=0 hit expired, so exactly one slot opened
    assert limiter.hit("a") == pytest.approx(30)  # t=60: the t=30 hit is next to expire


def test_limiter_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter(2, 60, clock=FakeClock())
    assert limiter.hit("a") is None and limiter.hit("a") is None
    assert limiter.hit("a") is not None
    assert limiter.hit("b") is None  # b has its own budget
    assert limiter.hit("b") is None
    assert limiter.hit("b") is not None
    assert len(limiter) == 2


def test_limiter_with_limit_zero_is_disabled() -> None:
    limiter = SlidingWindowLimiter(0, 60, clock=FakeClock())
    assert all(limiter.hit("a") is None for _ in range(1_000))
    assert len(limiter) == 0  # nothing is even recorded


def test_limiter_drops_expired_keys_so_memory_stays_bounded() -> None:
    clock = FakeClock()
    limiter = SlidingWindowLimiter(5, 60, clock=clock)
    for n in range(500):
        limiter.hit(f"203.0.113.{n}")
    assert len(limiter) == 500

    clock.advance(61)  # every hit above has left the window
    limiter.hit("198.51.100.1")
    assert len(limiter) == 1


def test_limiter_rejects_nonsense_configuration() -> None:
    with pytest.raises(ValueError):
        SlidingWindowLimiter(-1, 60)
    with pytest.raises(ValueError):
        SlidingWindowLimiter(3, 0)


# --- POST /api/auth/login over HTTP ---------------------------------------------------------------------


@pytest.fixture
async def limited_app(
    test_settings: Settings, frozen_clock: ManualClock, migrated_db: None
) -> AsyncIterator[FastAPI]:
    """The real app, built the way conftest builds it, but allowing only 3 sign-ins per minute per address."""
    settings = test_settings.model_copy(update={"login_rate_limit_per_minute": 3})
    application = create_app(settings=settings, clock=frozen_clock)
    async with application.router.lifespan_context(application):
        yield application


def _client(application: FastAPI, host: str = "203.0.113.7") -> httpx.AsyncClient:
    transport = ASGITransport(app=application, client=(host, 50_000))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def limited_client(limited_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with _client(limited_app) as c:
        yield c


async def _login(client: httpx.AsyncClient, user_id: str = "u2") -> httpx.Response:
    return await client.post("/api/auth/login", json={"user_id": user_id})


async def test_fourth_login_in_a_minute_is_429_with_retry_after(limited_client: httpx.AsyncClient) -> None:
    for _ in range(3):
        assert (await _login(limited_client)).status_code == 200

    blocked = await _login(limited_client)
    assert blocked.status_code == 429
    body = blocked.json()
    assert set(body) == {"detail"}
    assert body["detail"]["code"] == "rate_limited"
    retry_after = blocked.headers["retry-after"]
    assert retry_after.isdigit() and 1 <= int(retry_after) <= 60
    assert body["detail"]["message"] == f"Too many sign-in attempts; try again in {retry_after} s"
    assert "set-cookie" not in blocked.headers  # a blocked attempt never signs anybody in


async def test_unknown_user_attempts_count_and_are_limited_before_the_lookup(
    limited_client: httpx.AsyncClient,
) -> None:
    for _ in range(3):
        response = await _login(limited_client, "nobody")
        assert (response.status_code, response.json()["detail"]["code"]) == (404, "user_not_found")

    # The budget is spent by probing, so even a valid user is refused ...
    assert (await _login(limited_client, "u2")).status_code == 429
    # ... and a further unknown id gets 429, not 404: the limiter runs before the user lookup.
    assert (await _login(limited_client, "nobody")).status_code == 429


async def test_malformed_login_bodies_count_too(limited_client: httpx.AsyncClient) -> None:
    for _ in range(3):
        response = await limited_client.post("/api/auth/login", json={"user_id": "u2", "role": "admin"})
        assert response.status_code == 422
    assert (await _login(limited_client)).status_code == 429


async def test_limit_is_per_client_address(limited_app: FastAPI) -> None:
    async with _client(limited_app, "203.0.113.7") as noisy, _client(limited_app, "198.51.100.9") as other:
        for _ in range(3):
            await _login(noisy)
        assert (await _login(noisy)).status_code == 429
        assert (await _login(other)).status_code == 200  # a different address has its own budget


async def test_only_login_is_limited(limited_client: httpx.AsyncClient) -> None:
    for _ in range(4):
        await _login(limited_client)
    assert (await _login(limited_client)).status_code == 429

    assert (await limited_client.get("/api/health")).status_code == 200
    assert (await limited_client.get("/api/auth/users")).status_code == 200
    assert (await limited_client.get("/api/auth/me")).status_code == 200  # signed in by the first logins
    assert (await limited_client.post("/api/auth/logout")).status_code == 204


async def test_login_works_again_once_the_window_has_passed(limited_app: FastAPI) -> None:
    clock = FakeClock()
    limited_app.state.login_limiter = SlidingWindowLimiter(3, 60, clock=clock)  # same limiter, fake time
    async with _client(limited_app) as client:
        for _ in range(3):
            assert (await _login(client)).status_code == 200
        blocked = await _login(client)
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "60"

        clock.advance(59)
        assert (await _login(client)).status_code == 429
        clock.advance(1)
        assert (await _login(client)).status_code == 200


async def test_limiter_is_created_lazily_from_the_settings_on_app_state(limited_app: FastAPI) -> None:
    assert not hasattr(limited_app.state, "login_limiter")
    async with _client(limited_app) as client:
        await _login(client)
    limiter = limited_app.state.login_limiter
    assert isinstance(limiter, SlidingWindowLimiter)
    assert (limiter.limit, limiter.window_s) == (3, 60)


async def test_test_settings_disable_the_limiter(client: httpx.AsyncClient, app: FastAPI) -> None:
    assert app.state.settings.login_rate_limit_per_minute == 0
    for _ in range(25):
        assert (await _login(client)).status_code == 200


async def test_openapi_documents_the_login_429(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/api/openapi.json")).json()
    response = spec["paths"]["/api/auth/login"]["post"]["responses"]["429"]
    assert response["content"]["application/json"]["schema"]["$ref"].endswith("/ErrorBody")
    assert response["headers"]["Retry-After"]["schema"] == {"type": "integer", "minimum": 1}


# --- production configuration guard ---------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    """Settings that ignore any `.env` file; explicit values beat environment variables."""
    return Settings(_env_file=None, **overrides)


def test_production_with_dev_secret_and_insecure_cookie_is_refused_with_every_problem() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(app_env="production", secret_key=DEV_SECRET_KEY, cookie_secure=False)
    assert len(excinfo.value.errors()) == 1  # one error that lists everything
    message = str(excinfo.value)
    assert "APP_ENV=production" in message
    assert "SECRET_KEY is the insecure development default" in message
    assert "COOKIE_SECURE must be true" in message


def test_production_refuses_a_short_secret() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(app_env="production", secret_key="x" * 31, cookie_secure=True)
    message = str(excinfo.value)
    assert "SECRET_KEY is too short (31 chars)" in message
    assert "x" * 31 not in message  # the offending value is never echoed into logs
    assert "COOKIE_SECURE" not in message  # only the real problem is reported


def test_production_refuses_an_insecure_cookie_alone() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(app_env="production", secret_key=STRONG_SECRET, cookie_secure=False)
    message = str(excinfo.value)
    assert "COOKIE_SECURE must be true" in message
    assert "SECRET_KEY" not in message


def test_production_with_a_strong_secret_and_secure_cookie_is_accepted() -> None:
    settings = _settings(app_env="production", secret_key=STRONG_SECRET, cookie_secure=True)
    assert settings.app_env == "production"
    assert _settings(app_env="production", secret_key="x" * 32, cookie_secure=True).secret_key == "x" * 32


@pytest.mark.parametrize("env", ["development", "test"])
def test_non_production_keeps_the_developer_friendly_defaults(env: str) -> None:
    settings = _settings(app_env=env, secret_key=DEV_SECRET_KEY, cookie_secure=False)
    assert (settings.secret_key, settings.cookie_secure) == (DEV_SECRET_KEY, False)


def test_defaults_are_development_with_a_ten_per_minute_login_limit() -> None:
    fields = Settings.model_fields
    assert fields["app_env"].default == "development"
    assert fields["login_rate_limit_per_minute"].default == 10
    assert fields["secret_key"].default == DEV_SECRET_KEY


def test_unknown_app_env_and_negative_rate_limit_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(app_env="staging")
    with pytest.raises(ValidationError):
        _settings(login_rate_limit_per_minute=-1)
    assert _settings(login_rate_limit_per_minute=0).login_rate_limit_per_minute == 0


def test_environment_variables_drive_the_guard_and_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", DEV_SECRET_KEY)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

    monkeypatch.setenv("SECRET_KEY", STRONG_SECRET)
    monkeypatch.setenv("COOKIE_SECURE", "true")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_PER_MINUTE", "0")
    settings = Settings(_env_file=None)
    assert (settings.app_env, settings.login_rate_limit_per_minute) == ("production", 0)
