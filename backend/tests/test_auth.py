"""Tests for single-user authentication.

Validates:
- bcrypt password verification (no plaintext storage)
- POST /api/auth/login sets HTTP-only session cookie on correct password
- POST /api/auth/login returns 401 on wrong password
- AuthMiddleware blocks unauthenticated requests on protected routes
- Authenticated requests with a valid session cookie pass through
- POST /api/auth/logout clears the cookie
- /api/healthcheck remains public (so Docker depends_on healthcheck never breaks)
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app, assert_production_safety
from app.routers import auth as auth_router
from tests.conftest import seed_admin_password


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear the login throttle so a failed-password test can't leak a lockout."""
    auth_router._reset_rate_limiter()
    yield
    auth_router._reset_rate_limiter()


@pytest_asyncio.fixture
async def client():
    """Async HTTP client whose login verifies against a seeded DB password."""
    original_password = cfg_module.settings.app_password
    cfg_module.settings.app_password = "test-password-123"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    await seed_admin_password(maker, "test-password-123")

    # Register a test-only protected endpoint so middleware can be exercised
    # without depending on /api/accounts (which is built in a parallel plan).
    if not any(getattr(r, "path", None) == "/api/_test/protected" for r in app.router.routes):
        @app.get("/api/_test/protected")
        async def _test_protected() -> dict[str, str]:
            return {"status": "ok"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest.mark.asyncio
async def test_protected_endpoint_without_cookie(client):
    """Any protected endpoint without a session cookie returns 401."""
    resp = await client.get("/api/_test/protected")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_wrong_password(client):
    """Wrong password returns 401."""
    resp = await client.post("/api/auth/login", json={"password": "wrong-password"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_correct_password_sets_cookie(client):
    """Correct password returns 200 and sets an HTTP-only session cookie."""
    resp = await client.post(
        "/api/auth/login", json={"password": "test-password-123"}
    )
    assert resp.status_code == 200
    assert "session" in resp.cookies
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie_header


@pytest.mark.asyncio
async def test_authenticated_request_succeeds(client):
    """After login, protected endpoints return 200."""
    login = await client.post(
        "/api/auth/login", json={"password": "test-password-123"}
    )
    assert login.status_code == 200
    resp = await client.get("/api/_test/protected")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_logout_clears_cookie(client):
    """Logout clears the session cookie; subsequent requests are 401."""
    await client.post("/api/auth/login", json={"password": "test-password-123"})
    logout = await client.post("/api/auth/logout")
    assert logout.status_code == 200
    # The deletion cookie must carry the same attributes the login cookie
    # was set with (path + samesite + httponly) so browsers reliably overwrite
    # the existing secure/strict cookie. An expired (Max-Age=0) deletion is sent.
    delete_header = logout.headers.get("set-cookie", "")
    assert "session=" in delete_header
    assert "Path=/" in delete_header
    assert "SameSite=strict" in delete_header
    assert "HttpOnly" in delete_header
    # Clear cookies to simulate the browser dropping the cleared cookie
    client.cookies.clear()
    resp = await client.get("/api/_test/protected")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_healthcheck_is_public(client):
    """/api/healthcheck must remain public so Docker healthcheck never breaks."""
    resp = await client.get("/api/healthcheck")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def _settings(**overrides):
    """Build a minimal settings-like object for assert_production_safety."""
    base = dict(
        app_env="production",
        fixed_now=None,
        secret_key="a-strong-random-secret",
        app_password="a-strong-password",
        demo_mode=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_production_boot_guard_rejects_default_secret_key():
    """Production must refuse to boot with the published default SECRET_KEY."""
    with pytest.raises(RuntimeError, match="published defaults"):
        assert_production_safety(_settings(secret_key="change-me-in-production"))


def test_production_boot_guard_rejects_default_password():
    """Production must refuse to boot with the published default APP_PASSWORD."""
    with pytest.raises(RuntimeError, match="published defaults"):
        assert_production_safety(_settings(app_password="changeme"))


def test_production_boot_guard_still_catches_fixed_now():
    """The pre-existing fixed_now guard must remain active."""
    with pytest.raises(RuntimeError, match="FLOWFOLIO_FIXED_NOW"):
        assert_production_safety(_settings(fixed_now="2026-04-30T12:00:00Z"))


def test_production_boot_guard_passes_with_strong_values():
    """Strong secrets in production must not raise."""
    assert_production_safety(_settings()) is None


def test_boot_guard_noop_in_development():
    """Default secrets are tolerated outside production (dev/test convenience)."""
    assert_production_safety(
        _settings(app_env="development", secret_key="change-me-in-production",
                  app_password="changeme")
    ) is None


@pytest.mark.asyncio
async def test_password_not_stored_plaintext():
    """The DB-stored admin password hash must be a bcrypt hash, not plaintext."""
    from app.services.setup_state import (
        claim_admin_password,
        get_admin_password_hash,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await claim_admin_password(s, "test-password-123")
        await s.commit()
        stored = await get_admin_password_hash(s)
    await engine.dispose()

    assert stored != "test-password-123"
    # bcrypt hashes start with $2a$, $2b$ or $2y$ and are 60 chars long.
    assert stored.startswith("$2")
    assert len(stored) == 60


def test_hash_verify_roundtrip():
    """hash_password output verifies against the same plaintext and not others."""
    from app.core.security import hash_password, verify_password

    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed) is True
    assert verify_password("wrong password", hashed) is False


def test_verify_password_malformed_hash_returns_false():
    """A non-bcrypt 'hash' must yield False, not raise (bcrypt raises ValueError)."""
    from app.core.security import verify_password

    assert verify_password("x", "not-a-hash") is False
