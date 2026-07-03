"""GET /api/version tests.

Mirrors the ASGITransport + AsyncClient auth fixture from test_fx_router.py.
The endpoint is a trivial settings read (no DB), but stays behind AuthMiddleware
like the other data routers, so an authenticated session is required.
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
async def authed_client():
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest_asyncio.fixture
async def unauthed_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_version_unauthenticated_401(unauthed_client):
    resp = await unauthed_client.get("/api/version")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_version_returns_app_version(authed_client):
    resp = await authed_client.get("/api/version")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"version": cfg_module.settings.app_version}


@pytest.mark.asyncio
async def test_get_version_reflects_build_stamp(authed_client, monkeypatch):
    """The handler reads the live settings.app_version (the build-arg stamp)."""
    monkeypatch.setattr(cfg_module.settings, "app_version", "v9.9.9")
    resp = await authed_client.get("/api/version")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"version": "v9.9.9"}
