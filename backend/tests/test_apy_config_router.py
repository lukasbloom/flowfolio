"""/api/apy-config router tests."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, ApyConfig, Instrument, Transaction
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
        yield c, maker

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
    await seed_admin_password(maker, "test-password-123")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()


async def _seed_pair(maker) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:8]
    async with maker() as s:
        acct = Account(name=f"Revolut {suffix}", account_type="broker")
        inst = Instrument(
            symbol=f"ETH-{suffix}",
            name="Ethereum",
            instrument_type="crypto",
            base_currency="EUR",
            price_source="coingecko",
        )
        s.add_all([acct, inst])
        await s.commit()
        return acct.id, inst.id


def _body(account_id: str, instrument_id: str, effective_from: str = "2025-01-01"):
    return {
        "account_id": account_id,
        "instrument_id": instrument_id,
        "apy_rate": "0.023700",
        "effective_from": effective_from,
        "compounding": "daily_simple",
    }


@pytest.mark.asyncio
async def test_post_unauthenticated_401(unauthed_client):
    resp = await unauthed_client.post(
        "/api/apy-config",
        json=_body("acct", "inst"),
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_post_first_config_creates_open_row(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)

    resp = await client.post("/api/apy-config", json=_body(account_id, instrument_id))

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["effective_to"] is None
    assert Decimal(data["apy_rate"]) == Decimal("0.023700")


@pytest.mark.asyncio
async def test_post_second_config_closes_prior_row(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)

    first = await client.post("/api/apy-config", json=_body(account_id, instrument_id))
    second_body = _body(account_id, instrument_id, "2025-06-01")
    second_body["apy_rate"] = "0.050000"
    second = await client.post("/api/apy-config", json=second_body)

    assert first.status_code == 201
    assert second.status_code == 201, second.text
    rows = (await client.get(
        f"/api/apy-config?account_id={account_id}&instrument_id={instrument_id}"
    )).json()
    assert [row["effective_from"] for row in rows] == ["2025-01-01", "2025-06-01"]
    assert rows[0]["effective_to"] == "2025-05-31"
    assert rows[1]["effective_to"] is None


@pytest.mark.asyncio
async def test_post_second_config_with_earlier_effective_from_rejected(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)

    await client.post("/api/apy-config", json=_body(account_id, instrument_id, "2025-06-01"))
    resp = await client.post("/api/apy-config", json=_body(account_id, instrument_id))

    assert resp.status_code == 400
    assert "must be after prior open row" in resp.text


@pytest.mark.asyncio
async def test_post_duplicate_effective_from_returns_409(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)

    first = await client.post("/api/apy-config", json=_body(account_id, instrument_id))
    duplicate = await client.post("/api/apy-config", json=_body(account_id, instrument_id))

    assert first.status_code == 201
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_get_list_filters_by_pair(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)
    other_account_id, other_instrument_id = await _seed_pair(maker)
    await client.post("/api/apy-config", json=_body(account_id, instrument_id))
    await client.post(
        "/api/apy-config", json=_body(account_id, instrument_id, "2025-06-01")
    )
    await client.post("/api/apy-config", json=_body(other_account_id, other_instrument_id))

    resp = await client.get(
        f"/api/apy-config?account_id={account_id}&instrument_id={instrument_id}"
    )

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert [row["effective_from"] for row in rows] == ["2025-01-01", "2025-06-01"]
    assert all(row["account_id"] == account_id for row in rows)
    assert all(row["instrument_id"] == instrument_id for row in rows)


@pytest.mark.asyncio
async def test_get_one_404(authed_client):
    client, _ = authed_client
    resp = await client.get("/api/apy-config/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_apy_rate_succeeds(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)
    created = await client.post("/api/apy-config", json=_body(account_id, instrument_id))
    config_id = created.json()["id"]

    resp = await client.patch(f"/api/apy-config/{config_id}", json={"apy_rate": "0.040000"})

    assert resp.status_code == 200
    assert Decimal(resp.json()["apy_rate"]) == Decimal("0.040000")


@pytest.mark.asyncio
async def test_patch_effective_from_rejected_400(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)
    created = await client.post("/api/apy-config", json=_body(account_id, instrument_id))
    config_id = created.json()["id"]

    resp = await client.patch(
        f"/api/apy-config/{config_id}", json={"effective_from": "2025-02-01"}
    )

    assert resp.status_code == 400
    assert "effective_from is immutable" in resp.text


@pytest.mark.asyncio
async def test_delete_unreferenced_succeeds(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)
    created = await client.post("/api/apy-config", json=_body(account_id, instrument_id))
    config_id = created.json()["id"]

    resp = await client.delete(f"/api/apy-config/{config_id}")

    assert resp.status_code == 204
    async with maker() as s:
        assert await s.get(ApyConfig, config_id) is None


@pytest.mark.asyncio
async def test_delete_referenced_returns_409(authed_client):
    client, maker = authed_client
    account_id, instrument_id = await _seed_pair(maker)
    created = await client.post("/api/apy-config", json=_body(account_id, instrument_id))
    config_id = created.json()["id"]
    async with maker() as s:
        s.add(
            Transaction(
                account_id=account_id,
                instrument_id=instrument_id,
                txn_type="yield",
                date=date(2025, 1, 2),
                quantity=Decimal("0.001"),
                unit_price=Decimal("100.00"),
                price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("0.10"),
                source="accrual",
                apy_config_id=config_id,
            )
        )
        await s.commit()

    resp = await client.delete(f"/api/apy-config/{config_id}")

    assert resp.status_code == 409
    assert "PATCH effective_to" in resp.text
