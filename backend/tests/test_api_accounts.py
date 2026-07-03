"""Tests for /api/accounts CRUD endpoints.

Each test uses an in-memory SQLite engine + dependency override on get_db.
The HTTP client logs in once so AuthMiddleware lets requests through.
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
async def client():
    # Reset cached bcrypt hash + override password so we can login deterministically
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
        # Login so subsequent requests carry a valid session cookie
        login = await c.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200, "fixture login must succeed"
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest.mark.asyncio
async def test_create_account(client):
    resp = await client.post(
        "/api/accounts", json={"name": "Revolut", "account_type": "broker"}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Revolut"
    assert "id" in data


@pytest.mark.asyncio
async def test_list_accounts(client):
    await client.post("/api/accounts", json={"name": "XTB", "account_type": "broker"})
    resp = await client.get("/api/accounts")
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


@pytest.mark.asyncio
async def test_get_account_404(client):
    resp = await client.get("/api/accounts/nonexistent")
    assert resp.status_code == 404
