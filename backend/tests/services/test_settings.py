from __future__ import annotations

from datetime import datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import UserSetting
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
    async with maker() as session:
        session.add(UserSetting(key="concentration_threshold", value="0.25"))
        await session.commit()

    async def override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    await seed_admin_password(maker, "test-password-123")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest.mark.asyncio
async def test_get_settings_returns_seeded_threshold(authed_client):
    client, _ = authed_client

    response = await client.get("/api/settings")

    assert response.status_code == 200, response.text
    assert response.json() == {"settings": {"concentration_threshold": "0.25"}}


@pytest.mark.asyncio
async def test_get_settings_never_exposes_admin_password_hash(authed_client):
    """Setup-owned secrets must never leak through GET /api/settings."""
    client, _ = authed_client

    settings_out = (await client.get("/api/settings")).json()["settings"]

    assert "admin_password_hash" not in settings_out
    assert "setup_complete" not in settings_out


@pytest.mark.asyncio
async def test_put_settings_updates_threshold(authed_client):
    client, _ = authed_client

    response = await client.put(
        "/api/settings/concentration_threshold", json={"value": "0.30"}
    )
    after = await client.get("/api/settings")

    assert response.status_code == 204, response.text
    assert after.json()["settings"]["concentration_threshold"] == "0.30"


@pytest.mark.asyncio
async def test_put_unknown_key_returns_422(authed_client):
    client, _ = authed_client

    response = await client.put("/api/settings/unknown_key", json={"value": "0.30"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_non_numeric_threshold_returns_422(authed_client):
    client, _ = authed_client

    response = await client.put(
        "/api/settings/concentration_threshold", json={"value": "not_a_number"}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_threshold_above_range_returns_422(authed_client):
    client, _ = authed_client

    response = await client.put(
        "/api/settings/concentration_threshold", json={"value": "1.5"}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_threshold_below_range_returns_422(authed_client):
    client, _ = authed_client

    response = await client.put(
        "/api/settings/concentration_threshold", json={"value": "0.005"}
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_put_updates_updated_at_column(authed_client):
    client, maker = authed_client
    async with maker() as session:
        row = await session.get(UserSetting, "concentration_threshold")
        row.updated_at = datetime(2026, 1, 1)
        await session.commit()

    response = await client.put(
        "/api/settings/concentration_threshold", json={"value": "0.35"}
    )

    assert response.status_code == 204, response.text
    async with maker() as session:
        row = (
            await session.execute(
                select(UserSetting).where(UserSetting.key == "concentration_threshold")
            )
        ).scalar_one()
        assert row.updated_at != datetime(2026, 1, 1)
