"""Tests for POST /api/auth/password (change-password endpoint).

Mirrors the `authed` fixture pattern from test_update_status_router.py.
Validates:
- correct current + valid new password -> 200, and the NEW password logs in
  while the OLD one no longer does
- wrong current password -> 401
- new password shorter than 8 chars -> 422
- end-to-end session revocation: a pre-change session cookie is rejected by
  AuthMiddleware after the change (epoch bumped), while the caller's own
  cookie (re-issued on the change response) keeps working
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def authed():
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
    app.state.token_epoch = 0
    await seed_admin_password(maker, "test-password-123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield c, maker

    app.dependency_overrides.clear()
    app.state.token_epoch = 0
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest.mark.asyncio
async def test_change_password_happy_path(authed):
    client, _maker = authed
    resp = await client.post(
        "/api/auth/password",
        json={"current_password": "test-password-123", "new_password": "new-password-456"},
    )
    assert resp.status_code == 200, resp.text

    # The new password now logs in; the old one no longer does.
    login_new = await client.post("/api/auth/login", json={"password": "new-password-456"})
    assert login_new.status_code == 200
    login_old = await client.post("/api/auth/login", json={"password": "test-password-123"})
    assert login_old.status_code == 401


@pytest.mark.asyncio
async def test_change_password_wrong_current_returns_401(authed):
    client, _maker = authed
    resp = await client.post(
        "/api/auth/password",
        json={"current_password": "wrong-password", "new_password": "new-password-456"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_change_password_too_short_returns_422(authed):
    client, _maker = authed
    resp = await client.post(
        "/api/auth/password",
        json={"current_password": "test-password-123", "new_password": "short1"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_change_password_revokes_stale_session_but_keeps_caller(authed):
    """End-to-end revocation, driven through the ASGI client + AuthMiddleware.

    A cookie minted before the change (epoch 0) must be rejected afterward
    (epoch bumped to 1), but the change response re-issues the CALLER's own
    cookie at the new epoch, so the same client is not logged out by its own
    password change.
    """
    client, _maker = authed
    stale_cookie = client.cookies.get("session")
    assert stale_cookie is not None

    resp = await client.post(
        "/api/auth/password",
        json={"current_password": "test-password-123", "new_password": "new-password-456"},
    )
    assert resp.status_code == 200, resp.text
    # The change response must re-set the caller's own session cookie.
    set_cookie_header = resp.headers.get("set-cookie", "")
    assert "session=" in set_cookie_header

    # A DIFFERENT client carrying the pre-change cookie is now rejected by the
    # middleware (the stored epoch bumped, so validate_session_token fails).
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"session": stale_cookie}
    ) as stale_client:
        stale_resp = await stale_client.get("/api/update-status")
        assert stale_resp.status_code == 401

    # The original client's cookie jar was updated by the change response's
    # Set-Cookie, so it stays authenticated.
    ok_resp = await client.get("/api/update-status")
    assert ok_resp.status_code == 200
