"""Tests for the in-process login brute-force throttle.

Validates:
- 5 wrong passwords arm a lockout; the 6th attempt returns 429 even with the
  correct password
- the 429 response carries a Retry-After header
- once the lockout expires, the correct password succeeds and clears all state
- fewer than threshold failures followed by a success works and resets state

The rate limiter is module-global state (single-user, single-worker design), so
every test resets it via the autouse fixture to stay isolated.
"""
from __future__ import annotations

import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.routers import auth as auth_router
from tests.conftest import seed_admin_password

GOOD = "test-password-123"
BAD = "wrong-password"


@pytest.fixture(autouse=True)
def _reset_limiter():
    """Clear throttle state before and after every test so order can't leak."""
    auth_router._reset_rate_limiter()
    yield
    auth_router._reset_rate_limiter()


@pytest_asyncio.fixture
async def client():
    """Async HTTP client whose login verifies against a seeded DB password."""
    original_password = cfg_module.settings.app_password
    cfg_module.settings.app_password = GOOD

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    await seed_admin_password(maker, GOOD)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest.mark.asyncio
async def test_lockout_after_five_failures_blocks_correct_password(client):
    """5 wrong passwords lock the endpoint; the 6th attempt is 429 even if correct."""
    for _ in range(5):
        resp = await client.post("/api/auth/login", json={"password": BAD})
        assert resp.status_code == 401

    # Correct password now, but the lockout is armed.
    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_lockout_carries_retry_after(client):
    """The 429 response includes a Retry-After header with a positive integer."""
    for _ in range(5):
        await client.post("/api/auth/login", json={"password": BAD})
    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 429
    assert "retry-after" in resp.headers
    assert int(resp.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_lockout_expiry_allows_success_and_resets(client):
    """After the lockout window passes, the correct password succeeds and resets state."""
    for _ in range(5):
        await client.post("/api/auth/login", json={"password": BAD})
    assert auth_router._locked_until > time.monotonic()

    # Simulate the cooldown having elapsed.
    auth_router._locked_until = time.monotonic() - 1.0

    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 200
    assert "session" in resp.cookies
    # Successful login clears all throttle state.
    assert auth_router._failed_attempts == 0
    assert auth_router._locked_until == 0.0
    assert auth_router._last_failure_at == 0.0


@pytest.mark.asyncio
async def test_below_threshold_then_success_resets(client):
    """Fewer than threshold failures followed by a success works and clears state."""
    for _ in range(4):
        resp = await client.post("/api/auth/login", json={"password": BAD})
        assert resp.status_code == 401
    assert auth_router._failed_attempts == 4
    assert auth_router._locked_until == 0.0  # not yet locked

    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 200
    assert auth_router._failed_attempts == 0


@pytest.mark.asyncio
async def test_2fa_failures_lock_out_login(client):
    """5 failed /login/2fa attempts arm the shared lockout that blocks /login.

    /login/2fa checks the lockout before touching the pre-auth token or the
    TOTP code, and calls _register_failure() on any bad request, so a garbage
    body trips the same counter /login uses. No 2FA enrollment is needed.
    """
    for _ in range(5):
        resp = await client.post(
            "/api/auth/login/2fa",
            json={"pre_auth_token": "bad", "code": "000000"},
        )
        assert resp.status_code == 401

    # The /login/2fa failures armed the shared lockout, so /login is blocked
    # even with the correct password.
    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_login_failures_lock_out_2fa(client):
    """5 failed /login attempts arm the shared lockout that blocks /login/2fa."""
    for _ in range(5):
        resp = await client.post("/api/auth/login", json={"password": BAD})
        assert resp.status_code == 401

    # The /login failures armed the shared lockout, so /login/2fa is blocked
    # regardless of the pre-auth token or code supplied.
    resp = await client.post(
        "/api/auth/login/2fa",
        json={"pre_auth_token": "bad", "code": "000000"},
    )
    assert resp.status_code == 429
