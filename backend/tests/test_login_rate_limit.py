"""Tests for the in-process login brute-force throttle.

Validates, PER source IP:
- 5 wrong passwords arm a lockout; the 6th attempt returns 429 even with the
  correct password
- the 429 response carries a Retry-After header
- once the lockout expires, the correct password succeeds and clears all state
- fewer than threshold failures followed by a success works and resets state
- a correct password mid-2FA does NOT reset an in-progress /login/2fa
  failure streak (the streak only resets when login actually completes)

Plus the per-IP DoS fix: failures from one source never block a correct login
from another, and the tracked-IP table is memory-bounded.

The rate limiter is a per-IP table in module state (single-user, single-worker
design), so every test resets it via the autouse fixture to stay isolated. The
default ASGITransport peer is 127.0.0.1 (SYNTH_IP), so any request without an
X-Forwarded-For header keys on that address.
"""
from __future__ import annotations

import time

import pyotp
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

# The ASGITransport default peer address; requests with no X-Forwarded-For key
# on this. IP_A/IP_B are distinct documentation-range sources for the DoS tests.
SYNTH_IP = "127.0.0.1"
IP_A = "203.0.113.7"
IP_B = "198.51.100.9"


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
    state = auth_router._throttle_by_ip[SYNTH_IP]
    assert state.locked_until > time.monotonic()

    # Simulate the cooldown having elapsed.
    state.locked_until = time.monotonic() - 1.0

    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 200
    assert "session" in resp.cookies
    # Successful login drops this IP's entry entirely (all throttle state cleared).
    assert SYNTH_IP not in auth_router._throttle_by_ip


@pytest.mark.asyncio
async def test_below_threshold_then_success_resets(client):
    """Fewer than threshold failures followed by a success works and clears state."""
    for _ in range(4):
        resp = await client.post("/api/auth/login", json={"password": BAD})
        assert resp.status_code == 401
    state = auth_router._throttle_by_ip[SYNTH_IP]
    assert state.failed_attempts == 4
    assert state.locked_until == 0.0  # not yet locked

    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 200
    assert SYNTH_IP not in auth_router._throttle_by_ip


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


@pytest.mark.asyncio
async def test_correct_password_mid_2fa_does_not_reset_failure_streak(client):
    """A correct-password /login while 2FA is pending must NOT reset the streak.

    Regression test: /login used to call _reset_rate_limiter() before checking
    is_totp_enabled, so an attacker who already knows the password could wipe
    an in-progress /login/2fa failure streak between batches of TOTP guesses,
    and the lockout would never accumulate. This enrolls 2FA, racks up 4
    failed /login/2fa attempts (one below _LOCKOUT_THRESHOLD), posts the
    correct password to /login mid-2FA (must NOT reset the streak), then
    registers one more failure to prove it is the 5th and arms the lockout.

    Fails on the old code: the pre-fix /login reset _failed_attempts to 0
    before returning twofa_required, so the assertion right after that call
    (_failed_attempts == 4) would fail, and the streak would restart at 1
    instead of reaching 5.
    """
    login = await client.post("/api/auth/login", json={"password": GOOD})
    assert login.status_code == 200, login.text

    setup = await client.post("/api/auth/2fa/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()

    enable = await client.post("/api/auth/2fa/enable", json={"code": code})
    assert enable.status_code == 200, enable.text

    await client.post("/api/auth/logout")
    client.cookies.clear()

    # 4 failed /login/2fa attempts: below the lockout threshold.
    for _ in range(4):
        resp = await client.post(
            "/api/auth/login/2fa",
            json={"pre_auth_token": "bad", "code": "000000"},
        )
        assert resp.status_code == 401
    assert auth_router._throttle_by_ip[SYNTH_IP].failed_attempts == 4
    assert auth_router._throttle_by_ip[SYNTH_IP].locked_until == 0.0

    # Correct password mid-2FA: must return twofa_required and NOT reset
    # the failure streak (the whole point of this regression test).
    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 200, resp.text
    assert resp.json()["twofa_required"] == "true"
    assert auth_router._throttle_by_ip[SYNTH_IP].failed_attempts == 4
    assert auth_router._throttle_by_ip[SYNTH_IP].locked_until == 0.0

    # One more failure is now the 5th and arms the lockout.
    resp = await client.post(
        "/api/auth/login/2fa",
        json={"pre_auth_token": "bad", "code": "000000"},
    )
    assert resp.status_code == 401
    assert auth_router._throttle_by_ip[SYNTH_IP].failed_attempts == 5
    assert auth_router._throttle_by_ip[SYNTH_IP].locked_until > time.monotonic()

    # The next request is now blocked regardless of endpoint.
    resp = await client.post(
        "/api/auth/login/2fa",
        json={"pre_auth_token": "bad", "code": "000000"},
    )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_failures_from_one_ip_do_not_block_another(client):
    """DoS regression: a lockout armed by IP A must NOT block a correct login
    from IP B. This is the whole point of per-IP keying and fails on the old
    global-counter code (where B would hit A's lockout and get 429)."""
    a = {"x-forwarded-for": IP_A}
    b = {"x-forwarded-for": IP_B}
    for _ in range(5):
        resp = await client.post("/api/auth/login", json={"password": BAD}, headers=a)
        assert resp.status_code == 401
    # IP A is armed; the owner logging in from IP B still succeeds.
    resp = await client.post("/api/auth/login", json={"password": GOOD}, headers=b)
    assert resp.status_code == 200
    assert "session" in resp.cookies


@pytest.mark.asyncio
async def test_attacker_relock_does_not_block_owner(client):
    """An attacker who keeps re-arming IP A's lockout never blocks the owner on
    IP B, and the owner's success leaves A's lockout fully intact."""
    a = {"x-forwarded-for": IP_A}
    b = {"x-forwarded-for": IP_B}
    for _ in range(5):
        resp = await client.post("/api/auth/login", json={"password": BAD}, headers=a)
        assert resp.status_code == 401
    # Expire A's lock, then one more failure re-arms it inside the window.
    auth_router._throttle_by_ip[IP_A].locked_until = time.monotonic() - 1.0
    resp = await client.post("/api/auth/login", json={"password": BAD}, headers=a)
    assert resp.status_code == 401
    assert auth_router._throttle_by_ip[IP_A].locked_until > time.monotonic()

    # The owner from IP B is unaffected...
    resp = await client.post("/api/auth/login", json={"password": GOOD}, headers=b)
    assert resp.status_code == 200
    # ...and the owner's success only cleared B, so A stays locked.
    assert IP_B not in auth_router._throttle_by_ip
    assert auth_router._throttle_by_ip[IP_A].locked_until > time.monotonic()


@pytest.mark.asyncio
async def test_per_ip_lockout_still_blocks_that_ip(client):
    """The old lockout semantic survives, now scoped to the offending IP: 5
    failures from A block a correct password FROM A with 429."""
    a = {"x-forwarded-for": IP_A}
    for _ in range(5):
        resp = await client.post("/api/auth/login", json={"password": BAD}, headers=a)
        assert resp.status_code == 401
    resp = await client.post("/api/auth/login", json={"password": GOOD}, headers=a)
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_2fa_failures_lock_only_same_ip_login(client):
    """/login/2fa failures from IP A lock A's /login too (shared per-IP
    throttle), but a different IP keeps its own independent counter."""
    a = {"x-forwarded-for": IP_A}
    b = {"x-forwarded-for": IP_B}
    for _ in range(5):
        resp = await client.post(
            "/api/auth/login/2fa",
            json={"pre_auth_token": "bad", "code": "000000"},
            headers=a,
        )
        assert resp.status_code == 401
    # A's /login is blocked by the 2FA-armed lockout...
    resp = await client.post("/api/auth/login", json={"password": GOOD}, headers=a)
    assert resp.status_code == 429
    # ...while B is untouched.
    resp = await client.post("/api/auth/login", json={"password": GOOD}, headers=b)
    assert resp.status_code == 200


def test_tracked_ip_table_is_bounded():
    """Registering failures from more than _MAX_TRACKED_IPS distinct sources
    evicts the stalest entries so the table stays memory-bounded. Exercised at
    the unit level: routing thousands of bcrypt-verified logins through HTTP
    would add minutes for no extra coverage of the eviction path."""
    for i in range(auth_router._MAX_TRACKED_IPS + 10):
        auth_router._register_failure(f"10.0.{i // 256}.{i % 256}")
    assert len(auth_router._throttle_by_ip) <= auth_router._MAX_TRACKED_IPS
