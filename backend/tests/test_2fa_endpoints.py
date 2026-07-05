"""Tests for the 2FA endpoints (status, setup, enable, disable).

Mirrors the `authed` fixture pattern from test_password_change.py.
Validates:
- GET /2fa reports disabled before any setup
- POST /2fa/setup returns a secret, an otpauth:// URI, and an inline SVG QR,
  without flipping the enabled flag
- POST /2fa/enable with a valid TOTP code flips status to enabled
- POST /2fa/enable with a wrong code returns 400
- POST /2fa/disable with the correct password clears the secret and disables,
  so a later setup call issues a brand new secret
- POST /2fa/disable with the wrong password returns 401
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
async def test_status_disabled_initially(authed):
    client, _maker = authed
    resp = await client.get("/api/auth/2fa")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"enabled": False}


@pytest.mark.asyncio
async def test_setup_returns_secret_uri_and_qr_without_enabling(authed):
    client, _maker = authed
    resp = await client.post("/api/auth/2fa/setup")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["secret"]
    assert body["otpauth_uri"].startswith("otpauth://")
    assert body["qr_svg"].startswith("data:image/svg+xml")

    status = await client.get("/api/auth/2fa")
    assert status.json() == {"enabled": False}


@pytest.mark.asyncio
async def test_enable_with_valid_code_flips_status(authed):
    client, _maker = authed
    setup = await client.post("/api/auth/2fa/setup")
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()

    resp = await client.post("/api/auth/2fa/enable", json={"code": code})
    assert resp.status_code == 200, resp.text

    status = await client.get("/api/auth/2fa")
    assert status.json() == {"enabled": True}


@pytest.mark.asyncio
async def test_enable_with_wrong_code_returns_400(authed):
    client, _maker = authed
    await client.post("/api/auth/2fa/setup")

    resp = await client.post("/api/auth/2fa/enable", json={"code": "000000"})
    assert resp.status_code == 400

    status = await client.get("/api/auth/2fa")
    assert status.json() == {"enabled": False}


@pytest.mark.asyncio
async def test_disable_with_correct_password_clears_secret(authed):
    client, _maker = authed
    setup = await client.post("/api/auth/2fa/setup")
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    await client.post("/api/auth/2fa/enable", json={"code": code})

    resp = await client.post("/api/auth/2fa/disable", json={"password": "test-password-123"})
    assert resp.status_code == 200, resp.text

    status = await client.get("/api/auth/2fa")
    assert status.json() == {"enabled": False}

    # The secret is actually cleared: a fresh setup issues a NEW secret.
    resetup = await client.post("/api/auth/2fa/setup")
    assert resetup.json()["secret"] != secret


@pytest.mark.asyncio
async def test_disable_with_wrong_password_returns_401(authed):
    client, _maker = authed
    setup = await client.post("/api/auth/2fa/setup")
    secret = setup.json()["secret"]
    code = pyotp.TOTP(secret).now()
    await client.post("/api/auth/2fa/enable", json={"code": code})

    resp = await client.post("/api/auth/2fa/disable", json={"password": "wrong-password"})
    assert resp.status_code == 401

    # Still enabled: a failed disable must not touch state.
    status = await client.get("/api/auth/2fa")
    assert status.json() == {"enabled": True}
