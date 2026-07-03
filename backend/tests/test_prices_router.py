"""/api/prices router tests."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Instrument, PriceQuote
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


async def _seed_instrument(maker) -> str:
    async with maker() as s:
        inst = Instrument(
            symbol="IE00BYX5NX33",
            name="MSCI World Fund",
            instrument_type="fund",
            base_currency="EUR",
            price_source="ft",
        )
        s.add(inst)
        await s.commit()
        return inst.id


def _manual_body(instrument_id: str, price: str = "13.00", on_date: str = "2025-01-15"):
    return {
        "instrument_id": instrument_id,
        "date": on_date,
        "price": price,
        "currency": "EUR",
    }


@pytest.mark.asyncio
async def test_post_manual_nav_creates_row(authed_client):
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)

    resp = await client.post("/api/prices/manual", json=_manual_body(instrument_id))

    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["source"] == "manual"
    assert Decimal(data["price"]) == Decimal("13.00")


@pytest.mark.asyncio
async def test_post_manual_nav_upserts_same_day(authed_client):
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)

    first = await client.post("/api/prices/manual", json=_manual_body(instrument_id))
    second = await client.post(
        "/api/prices/manual", json=_manual_body(instrument_id, "14.00")
    )

    assert first.status_code == 201
    assert second.status_code == 201, second.text
    assert first.json()["id"] == second.json()["id"]
    assert Decimal(second.json()["price"]) == Decimal("14.00")
    async with maker() as s:
        count = await s.scalar(select(func.count()).select_from(PriceQuote))
    assert count == 1


@pytest.mark.asyncio
async def test_post_manual_nav_negative_price_422(authed_client):
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)

    resp = await client.post("/api/prices/manual", json=_manual_body(instrument_id, "-1"))

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_latest_no_quotes_returns_404(authed_client):
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)

    resp = await client.get(f"/api/prices/{instrument_id}/latest")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_latest_manual_today_wins_over_api_today(authed_client):
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    today = date.today()
    async with maker() as s:
        s.add_all(
            [
                PriceQuote(
                    instrument_id=instrument_id,
                    date=today,
                    price=Decimal("100.00"),
                    currency="EUR",
                    source="finnhub",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=today,
                    price=Decimal("99.00"),
                    currency="EUR",
                    source="manual",
                ),
            ]
        )
        await s.commit()

    resp = await client.get(f"/api/prices/{instrument_id}/latest")

    assert resp.status_code == 200
    assert resp.json()["source"] == "manual"
    assert Decimal(resp.json()["price"]) == Decimal("99.00")


@pytest.mark.asyncio
async def test_get_latest_yesterday_api_returned_when_no_manual_today(authed_client):
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    async with maker() as s:
        s.add(
            PriceQuote(
                instrument_id=instrument_id,
                date=date.today() - timedelta(days=1),
                price=Decimal("101.00"),
                currency="EUR",
                source="finnhub",
            )
        )
        await s.commit()

    resp = await client.get(f"/api/prices/{instrument_id}/latest")

    assert resp.status_code == 200
    assert resp.json()["source"] == "finnhub"


@pytest.mark.asyncio
async def test_get_history_filters_by_source(authed_client):
    """Default ordering is ASC (chronological). NavHistoryTab opts back into
    DESC via the ?order=desc query param (covered separately below)."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    async with maker() as s:
        s.add_all(
            [
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 16),
                    price=Decimal("14.00"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 15),
                    price=Decimal("13.00"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 15),
                    price=Decimal("13.10"),
                    currency="EUR",
                    source="ft",
                ),
            ]
        )
        await s.commit()

    resp = await client.get(f"/api/prices/{instrument_id}/history?source=manual")

    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert [row["source"] for row in rows] == ["manual", "manual"]
    # Default ordering is now ASC (chronological).
    assert [row["date"] for row in rows] == ["2025-01-15", "2025-01-16"]


@pytest.mark.asyncio
async def test_get_history_default_returns_all_rows(authed_client):
    """No query params → return every row for the instrument (no 50-row default,
    no 500-row cap). Backwards-compat fix: the old 50-row default was a bug."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    today = date.today()
    async with maker() as s:
        s.add_all(
            [
                PriceQuote(
                    instrument_id=instrument_id,
                    date=today - timedelta(days=offset),
                    price=Decimal("10.00"),
                    currency="EUR",
                    source="finnhub",
                )
                for offset in range(600)
            ]
        )
        await s.commit()

    resp = await client.get(f"/api/prices/{instrument_id}/history")

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 600


@pytest.mark.asyncio
async def test_get_history_timeframe_1m_filters_to_last_30_days(authed_client):
    """timeframe=1m → range_start = today - 30 days; rows older than 30 days excluded."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    today = date.today()
    offsets = [0, 15, 29, 31, 60, 120]
    async with maker() as s:
        s.add_all(
            [
                PriceQuote(
                    instrument_id=instrument_id,
                    date=today - timedelta(days=offset),
                    price=Decimal("10.00"),
                    currency="EUR",
                    source="finnhub",
                )
                for offset in offsets
            ]
        )
        await s.commit()

    resp = await client.get(f"/api/prices/{instrument_id}/history?timeframe=1m")

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    expected_dates = sorted(
        (today - timedelta(days=o)).isoformat() for o in [0, 15, 29]
    )
    assert [row["date"] for row in rows] == expected_dates


@pytest.mark.asyncio
async def test_get_history_custom_range_filters_inclusive(authed_client):
    """timeframe=custom + from + to → inclusive on both ends."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    async with maker() as s:
        s.add_all(
            [
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2024, 1, 1),
                    price=Decimal("10.00"),
                    currency="EUR",
                    source="finnhub",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2024, 3, 15),
                    price=Decimal("11.00"),
                    currency="EUR",
                    source="finnhub",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2024, 6, 30),
                    price=Decimal("12.00"),
                    currency="EUR",
                    source="finnhub",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2024, 7, 1),
                    price=Decimal("13.00"),
                    currency="EUR",
                    source="finnhub",
                ),
            ]
        )
        await s.commit()

    resp = await client.get(
        f"/api/prices/{instrument_id}/history"
        "?timeframe=custom&from=2024-01-01&to=2024-06-30"
    )

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [row["date"] for row in rows] == [
        "2024-01-01",
        "2024-03-15",
        "2024-06-30",
    ]


@pytest.mark.asyncio
async def test_get_history_custom_missing_dates_422(authed_client):
    """timeframe=custom without from/to → 422 with the same string /api/networth uses."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)

    resp = await client.get(
        f"/api/prices/{instrument_id}/history?timeframe=custom"
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "custom timeframe requires both from and to dates"


@pytest.mark.asyncio
async def test_get_history_from_after_to_422(authed_client):
    """from > to → 422 with the same string /api/networth uses."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)

    resp = await client.get(
        f"/api/prices/{instrument_id}/history"
        "?timeframe=custom&from=2024-06-30&to=2024-01-01"
    )

    assert resp.status_code == 422
    assert resp.json()["detail"] == "from must be on or before to"


@pytest.mark.asyncio
async def test_get_history_source_filter_still_works(authed_client):
    """NavHistoryTab passes ?source=manual&limit=50&order=desc — verify the
    source filter still selects only manual rows."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    async with maker() as s:
        s.add_all(
            [
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 10),
                    price=Decimal("10.00"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 11),
                    price=Decimal("11.00"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 12),
                    price=Decimal("12.00"),
                    currency="EUR",
                    source="finnhub",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 13),
                    price=Decimal("13.00"),
                    currency="EUR",
                    source="finnhub",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 14),
                    price=Decimal("14.00"),
                    currency="EUR",
                    source="finnhub",
                ),
            ]
        )
        await s.commit()

    resp = await client.get(f"/api/prices/{instrument_id}/history?source=manual")

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 2
    assert all(row["source"] == "manual" for row in rows)


@pytest.mark.asyncio
async def test_get_history_ordering_is_chronological_ascending(authed_client):
    """Default ordering is date ASC then fetched_at ASC — matches the chart's
    expected reading order."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    async with maker() as s:
        s.add_all(
            [
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 15),
                    price=Decimal("13.00"),
                    currency="EUR",
                    source="finnhub",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 10),
                    price=Decimal("10.00"),
                    currency="EUR",
                    source="finnhub",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 20),
                    price=Decimal("20.00"),
                    currency="EUR",
                    source="finnhub",
                ),
            ]
        )
        await s.commit()

    resp = await client.get(f"/api/prices/{instrument_id}/history")

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [row["date"] for row in rows] == [
        "2025-01-10",
        "2025-01-15",
        "2025-01-20",
    ]


@pytest.mark.asyncio
async def test_get_history_order_desc_param_for_nav_history_tab(authed_client):
    """NavHistoryTab needs newest-first rows so its `slice(0, 5)` shows recent
    NAVs. Endpoint accepts ?order=desc to opt back into the old DESC ordering."""
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    async with maker() as s:
        s.add_all(
            [
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 15),
                    price=Decimal("13.00"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 10),
                    price=Decimal("10.00"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_id,
                    date=date(2025, 1, 20),
                    price=Decimal("20.00"),
                    currency="EUR",
                    source="manual",
                ),
            ]
        )
        await s.commit()

    resp = await client.get(
        f"/api/prices/{instrument_id}/history?source=manual&order=desc"
    )

    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [row["date"] for row in rows] == [
        "2025-01-20",
        "2025-01-15",
        "2025-01-10",
    ]


@pytest.mark.asyncio
async def test_delete_manual_succeeds(authed_client):
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    created = await client.post("/api/prices/manual", json=_manual_body(instrument_id))
    quote_id = created.json()["id"]

    resp = await client.delete(f"/api/prices/manual/{quote_id}")

    assert resp.status_code == 204
    async with maker() as s:
        assert await s.get(PriceQuote, quote_id) is None


@pytest.mark.asyncio
async def test_delete_non_manual_400(authed_client):
    client, maker = authed_client
    instrument_id = await _seed_instrument(maker)
    async with maker() as s:
        row = PriceQuote(
            instrument_id=instrument_id,
            date=date(2025, 1, 15),
            price=Decimal("13.00"),
            currency="EUR",
            source="finnhub",
        )
        s.add(row)
        await s.commit()
        quote_id = row.id

    resp = await client.delete(f"/api/prices/manual/{quote_id}")

    assert resp.status_code == 400
    assert "only manual quotes are deletable" in resp.text


@pytest.mark.asyncio
async def test_unauthenticated_401(unauthed_client):
    resp = await unauthed_client.post(
        "/api/prices/manual",
        json=_manual_body("inst"),
    )
    assert resp.status_code == 401
