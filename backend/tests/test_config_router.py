"""Tests for the boot-flags GET /api/config endpoint.

Validates:
- GET /api/config returns 200 with {demo, app_version} and NO session cookie
- demo reflects settings.demo_mode (toggled both ways)
- the payload exposes no secret/key/password field
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import config as cfg_module
from app.main import app


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_config_returns_boot_flags_without_cookie(client):
    """GET /api/config is reachable with no session and returns the two flags."""
    resp = await client.get("/api/config")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"demo", "app_version"}
    assert isinstance(body["demo"], bool)
    assert body["app_version"] == cfg_module.settings.app_version


@pytest.mark.asyncio
async def test_config_demo_reflects_setting(client):
    """demo equals settings.demo_mode under both values."""
    original = cfg_module.settings.demo_mode
    try:
        cfg_module.settings.demo_mode = True
        resp = await client.get("/api/config")
        assert resp.json()["demo"] is True

        cfg_module.settings.demo_mode = False
        resp = await client.get("/api/config")
        assert resp.json()["demo"] is False
    finally:
        cfg_module.settings.demo_mode = original


@pytest.mark.asyncio
async def test_config_exposes_no_secret_fields(client):
    """The payload must not leak any secret/key/password value."""
    resp = await client.get("/api/config")
    keys = {k.lower() for k in resp.json()}
    for forbidden in ("secret", "key", "password", "token", "hash"):
        assert not any(forbidden in k for k in keys)
