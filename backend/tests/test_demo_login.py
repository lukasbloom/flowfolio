"""Tests for the demo-only GET /api/auth/demo-login mint.

Validates:
- with demo_mode on: 303 redirect to /track + a session cookie validate_session_token accepts
- with demo_mode off: 404 and no cookie (credential-free entry is scoped to demo)
- the normal password path is untouched: a wrong password still returns 401
- /api/auth/demo-login is auth-exempt (reachable with no session)
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.auth import SESSION_COOKIE_NAME, validate_session_token
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.routers import auth as auth_router
from tests.conftest import seed_admin_password


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    auth_router._reset_rate_limiter()
    yield
    auth_router._reset_rate_limiter()


@pytest_asyncio.fixture
async def client():
    """Client with a seeded DB password and demo_mode restored after each test."""
    original_password = cfg_module.settings.app_password
    original_demo = cfg_module.settings.demo_mode
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password
    cfg_module.settings.demo_mode = original_demo


@pytest.mark.asyncio
async def test_demo_login_mints_session_when_demo_on(client):
    """Demo on: redirect to /track with a session cookie the validator accepts."""
    cfg_module.settings.demo_mode = True
    resp = await client.get("/api/auth/demo-login")
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/track"
    token = resp.cookies.get(SESSION_COOKIE_NAME)
    assert token is not None
    assert validate_session_token(token, current_epoch=0) is True
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "HttpOnly" in set_cookie_header
    assert "SameSite=strict" in set_cookie_header


@pytest.mark.asyncio
async def test_demo_login_404_when_demo_off(client):
    """Demo off: 404 and no session cookie — entry is scoped strictly to demo."""
    cfg_module.settings.demo_mode = False
    resp = await client.get("/api/auth/demo-login")
    assert resp.status_code == 404
    assert resp.cookies.get(SESSION_COOKIE_NAME) is None


@pytest.mark.asyncio
async def test_password_login_unchanged(client):
    """The normal password path is untouched: a wrong password still returns 401."""
    cfg_module.settings.demo_mode = True  # demo on must not weaken the password path
    resp = await client.post("/api/auth/login", json={"password": "wrong-password"})
    assert resp.status_code == 401
