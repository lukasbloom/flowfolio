"""Tests for two-step login (password -> TOTP code when 2FA is enabled).

Mirrors the `authed`-style fixture pattern from test_2fa_endpoints.py, but this
suite needs BOTH an unauthenticated client (for POST /login itself) and the
ability to enroll 2FA before exercising the second step, so the fixture yields
a plain client plus the sessionmaker rather than a pre-logged-in one.

Validates:
- 2FA OFF: POST /login sets the session cookie exactly as before (no
  `twofa_required` key in the body)
- 2FA ON: POST /login returns 200 {twofa_required, pre_auth_token} and sets
  NO session cookie
- POST /login/2fa with a fresh TOTP code sets the session cookie, and that
  cookie authenticates a protected route
- POST /login/2fa with a wrong code returns 401 and sets no cookie
- POST /login/2fa with a pre_auth_token that is actually a normal session
  token is rejected (401), since validate_pre_auth_token checks the `stage`
  claim
"""
from __future__ import annotations

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
    app.state.token_epoch = 0
    await seed_admin_password(maker, GOOD)

    # Register a test-only protected endpoint so we can prove the cookie set
    # by /login/2fa actually authenticates, without depending on a real
    # business router (mirrors test_auth.py's own test-only route).
    if not any(getattr(r, "path", None) == "/api/_test/protected" for r in app.router.routes):
        @app.get("/api/_test/protected")
        async def _test_protected() -> dict[str, str]:
            return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    app.state.token_epoch = 0
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _enroll_totp(client: AsyncClient) -> str:
    """Log in, enroll + enable 2FA, then log out. Returns the TOTP secret."""
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
    return secret


@pytest.mark.asyncio
async def test_login_with_2fa_off_sets_cookie_unchanged(client):
    """2FA OFF: POST /login behaves exactly as before login/2fa existed."""
    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 200, resp.text
    assert "session" in resp.cookies
    body = resp.json()
    assert body == {"status": "ok"}
    assert "twofa_required" not in body


@pytest.mark.asyncio
async def test_login_with_2fa_on_returns_pre_auth_token_no_cookie(client):
    """2FA ON: POST /login returns twofa_required + pre_auth_token, no cookie."""
    await _enroll_totp(client)

    resp = await client.post("/api/auth/login", json={"password": GOOD})
    assert resp.status_code == 200, resp.text
    assert "session" not in resp.cookies
    body = resp.json()
    assert body["twofa_required"] == "true"
    assert body["pre_auth_token"]


@pytest.mark.asyncio
async def test_login_2fa_with_fresh_code_sets_cookie_and_authenticates(client):
    """POST /login/2fa with a valid code sets the session cookie."""
    secret = await _enroll_totp(client)

    step1 = await client.post("/api/auth/login", json={"password": GOOD})
    pre_auth_token = step1.json()["pre_auth_token"]

    code = pyotp.TOTP(secret).now()
    step2 = await client.post(
        "/api/auth/login/2fa", json={"pre_auth_token": pre_auth_token, "code": code}
    )
    assert step2.status_code == 200, step2.text
    assert "session" in step2.cookies
    assert step2.json() == {"status": "ok"}

    protected = await client.get("/api/_test/protected")
    assert protected.status_code == 200


@pytest.mark.asyncio
async def test_login_2fa_with_wrong_code_returns_401_no_cookie(client):
    """POST /login/2fa with a wrong code returns 401 and sets no cookie."""
    await _enroll_totp(client)

    step1 = await client.post("/api/auth/login", json={"password": GOOD})
    pre_auth_token = step1.json()["pre_auth_token"]

    step2 = await client.post(
        "/api/auth/login/2fa",
        json={"pre_auth_token": pre_auth_token, "code": "000000"},
    )
    assert step2.status_code == 401
    assert "session" not in step2.cookies


@pytest.mark.asyncio
async def test_login_2fa_rejects_a_session_token_as_pre_auth_token(client):
    """A normal session token is not a valid pre_auth_token."""
    secret = await _enroll_totp(client)

    step1 = await client.post("/api/auth/login", json={"password": GOOD})
    pre_auth_token = step1.json()["pre_auth_token"]

    code = pyotp.TOTP(secret).now()
    step2 = await client.post(
        "/api/auth/login/2fa", json={"pre_auth_token": pre_auth_token, "code": code}
    )
    assert step2.status_code == 200
    session_token = step2.cookies["session"]

    # Now try to use the SESSION token (not a pre_auth_token) as the
    # pre_auth_token for a fresh /login/2fa call.
    step3 = await client.post(
        "/api/auth/login/2fa",
        json={"pre_auth_token": session_token, "code": pyotp.TOTP(secret).now()},
    )
    assert step3.status_code == 401
