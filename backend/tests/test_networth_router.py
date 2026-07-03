from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, HoldingTag, Instrument, PriceQuote, Tag, Transaction
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
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _seed_networth(maker) -> None:
    async with maker() as session:
        account = Account(name="Revolut", account_type="broker", currency="EUR")
        instrument = Instrument(
            symbol="FLOW",
            name="Flow Test",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, instrument])
        await session.flush()
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=instrument.id,
                txn_type="buy",
                date=date(2026, 1, 1),
                quantity=Decimal("2"),
                unit_price=Decimal("50"),
                price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("100"),
                notes="private broker note",
            )
        )
        session.add(
            PriceQuote(
                instrument_id=instrument.id,
                date=date(2026, 1, 1),
                price=Decimal("50"),
                currency="EUR",
                source="manual",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_get_networth_authenticated_returns_points_and_markers(authed_client):
    client, maker = authed_client
    await _seed_networth(maker)

    resp = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["aggregation"] == "daily"
    assert body["points"] == [{"date": "2026-01-01", "value": "100.00000000"}]
    assert body["markers"][0]["type"] == "buy"
    # Txn quantity stored exact (DecimalText), no scale padding.
    assert body["markers"][0]["quantity"] == "2"
    assert "notes" not in body["markers"][0]
    assert body["warnings"] == []


@pytest.mark.asyncio
async def test_get_networth_custom_requires_from_and_to(authed_client):
    client, _ = authed_client

    resp = await client.get("/api/networth?timeframe=custom")

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_networth_custom_rejects_reversed_range(authed_client):
    client, _ = authed_client

    resp = await client.get(
        "/api/networth?timeframe=custom&from=2026-02-01&to=2026-01-01"
    )

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Repeatable instrument_id filter
# ---------------------------------------------------------------------------


async def _seed_two_instruments(maker) -> tuple[str, str]:
    """Two instruments with one buy + one quote each on the same date.

    Returns (instrument_a_id, instrument_b_id). Both instruments use EUR so
    the test can assert on the raw EUR cost basis without FX-rate noise.
    """
    async with maker() as session:
        account = Account(name="Revolut", account_type="broker", currency="EUR")
        instrument_a = Instrument(
            symbol="AAA",
            name="Alpha Test",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        instrument_b = Instrument(
            symbol="BBB",
            name="Beta Test",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, instrument_a, instrument_b])
        await session.flush()

        # Two distinct holdings worth 100 EUR (A) and 250 EUR (B) on 2026-01-01.
        session.add_all(
            [
                Transaction(
                    account_id=account.id,
                    instrument_id=instrument_a.id,
                    txn_type="buy",
                    date=date(2026, 1, 1),
                    quantity=Decimal("2"),
                    unit_price=Decimal("50"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("100"),
                ),
                Transaction(
                    account_id=account.id,
                    instrument_id=instrument_b.id,
                    txn_type="buy",
                    date=date(2026, 1, 1),
                    quantity=Decimal("5"),
                    unit_price=Decimal("50"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("250"),
                ),
                PriceQuote(
                    instrument_id=instrument_a.id,
                    date=date(2026, 1, 1),
                    price=Decimal("50"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_b.id,
                    date=date(2026, 1, 1),
                    price=Decimal("50"),
                    currency="EUR",
                    source="manual",
                ),
            ]
        )
        await session.commit()
        return instrument_a.id, instrument_b.id


def _last_value(payload) -> Decimal:
    return Decimal(payload["points"][-1]["value"])


@pytest.mark.asyncio
async def test_networth_single_instrument_id_compat(authed_client):
    """Single ?instrument_id=<uuid> still narrows to that instrument's series.

    Regression guard for the param shape change from `str | None` to
    `list[str]` — FastAPI must continue parsing a single occurrence into a
    one-element list.
    """
    client, maker = authed_client
    inst_a, inst_b = await _seed_two_instruments(maker)

    resp_single = await client.get(
        f"/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
        f"&instrument_id={inst_a}"
    )
    resp_unfiltered = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
    )

    assert resp_single.status_code == 200, resp_single.text
    assert resp_unfiltered.status_code == 200, resp_unfiltered.text
    # Single-instrument value = A's cost basis (100). Unfiltered = A + B (350).
    assert _last_value(resp_single.json()) == Decimal("100.00000000")
    assert _last_value(resp_unfiltered.json()) == Decimal("350.00000000")


@pytest.mark.asyncio
async def test_networth_multi_instrument_id_sums(authed_client):
    """Two repeated ?instrument_id=<a>&instrument_id=<b> sums to A + B last value."""
    client, maker = authed_client
    inst_a, inst_b = await _seed_two_instruments(maker)

    resp_a = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
        f"&instrument_id={inst_a}"
    )
    resp_b = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
        f"&instrument_id={inst_b}"
    )
    resp_both = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
        f"&instrument_id={inst_a}&instrument_id={inst_b}"
    )

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    assert resp_both.status_code == 200
    last_a = _last_value(resp_a.json())
    last_b = _last_value(resp_b.json())
    last_both = _last_value(resp_both.json())
    assert abs(last_both - (last_a + last_b)) < Decimal("0.01")


@pytest.mark.asyncio
async def test_networth_empty_instrument_id_list_equals_full_portfolio(authed_client):
    """No instrument_id param returns the same series as today's portfolio-wide call."""
    client, maker = authed_client
    await _seed_two_instruments(maker)

    resp_unfiltered = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
    )

    assert resp_unfiltered.status_code == 200
    body = resp_unfiltered.json()
    # Unfiltered = sum of both holdings (100 + 250 = 350) at 2026-01-01.
    assert body["points"] == [{"date": "2026-01-01", "value": "350.00000000"}]


@pytest.mark.asyncio
async def test_networth_unknown_instrument_id_returns_zero_series_not_500(authed_client):
    """A syntactically valid but unknown UUID returns 200 with all-zero values, not 500."""
    client, maker = authed_client
    await _seed_two_instruments(maker)

    unknown_id = "00000000-0000-0000-0000-000000000000"
    resp = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
        f"&instrument_id={unknown_id}"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Either an empty points list or all-zero values is acceptable.
    if body["points"]:
        for point in body["points"]:
            assert Decimal(point["value"]) == Decimal("0")


# ---------------------------------------------------------------------------
# include_cost_basis + tag query params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_networth_default_returns_empty_cost_basis_series(authed_client):
    """Regression: a call WITHOUT include_cost_basis still emits cost_basis_series=[]."""
    client, maker = authed_client
    await _seed_networth(maker)

    resp = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cost_basis_series"] == []


@pytest.mark.asyncio
async def test_networth_include_cost_basis_returns_aligned_series(authed_client):
    """include_cost_basis=true returns cost_basis_series with len == points and aligned dates."""
    client, maker = authed_client
    await _seed_networth(maker)

    resp = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
        "&include_cost_basis=true"
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["cost_basis_series"]) == len(body["points"]) > 0
    assert [p["date"] for p in body["cost_basis_series"]] == [p["date"] for p in body["points"]]
    # Single 100 EUR buy with no sells — cost basis equals 100 on 2026-01-01.
    assert Decimal(body["cost_basis_series"][0]["value"]) == Decimal("100")


async def _seed_two_tagged_instruments(maker) -> None:
    """Two instruments where only the first is tagged "Crypto".

    Lets the tag-filter test assert that ``tag=Crypto`` shrinks the response
    to A's contribution while the unfiltered call sums both.
    """
    async with maker() as session:
        account = Account(name="Revolut", account_type="broker", currency="EUR")
        instrument_a = Instrument(
            symbol="AAA",
            name="Alpha",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        instrument_b = Instrument(
            symbol="BBB",
            name="Beta",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, instrument_a, instrument_b])
        await session.flush()
        tag = Tag(name="Crypto", color="#22c55e")
        session.add(tag)
        await session.flush()
        session.add_all(
            [
                HoldingTag(
                    account_id=account.id,
                    instrument_id=instrument_a.id,
                    tag_id=tag.id,
                ),
                Transaction(
                    account_id=account.id,
                    instrument_id=instrument_a.id,
                    txn_type="buy",
                    date=date(2026, 1, 1),
                    quantity=Decimal("2"),
                    unit_price=Decimal("50"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("100"),
                ),
                Transaction(
                    account_id=account.id,
                    instrument_id=instrument_b.id,
                    txn_type="buy",
                    date=date(2026, 1, 1),
                    quantity=Decimal("5"),
                    unit_price=Decimal("50"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("250"),
                ),
                PriceQuote(
                    instrument_id=instrument_a.id,
                    date=date(2026, 1, 1),
                    price=Decimal("50"),
                    currency="EUR",
                    source="manual",
                ),
                PriceQuote(
                    instrument_id=instrument_b.id,
                    date=date(2026, 1, 1),
                    price=Decimal("50"),
                    currency="EUR",
                    source="manual",
                ),
            ]
        )
        await session.commit()


@pytest.mark.asyncio
async def test_networth_tag_filter_narrows_both_series(authed_client):
    """?tag=Crypto narrows both points and cost_basis_series to the tagged subset."""
    client, maker = authed_client
    await _seed_two_tagged_instruments(maker)

    resp_filtered = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
        "&include_cost_basis=true&tag=Crypto"
    )
    resp_unfiltered = await client.get(
        "/api/networth?timeframe=custom&currency=EUR&from=2026-01-01&to=2026-01-01"
        "&include_cost_basis=true"
    )

    assert resp_filtered.status_code == 200, resp_filtered.text
    assert resp_unfiltered.status_code == 200, resp_unfiltered.text
    body_f = resp_filtered.json()
    body_u = resp_unfiltered.json()
    # Tagged: only A → 100 EUR. Unfiltered: A + B → 350 EUR. Both series shrink.
    assert Decimal(body_f["points"][-1]["value"]) == Decimal("100")
    assert Decimal(body_f["cost_basis_series"][-1]["value"]) == Decimal("100")
    assert Decimal(body_u["points"][-1]["value"]) == Decimal("350")
    assert Decimal(body_u["cost_basis_series"][-1]["value"]) == Decimal("350")
    # And the responses differ — proves the tag is actually wired through.
    assert body_f["cost_basis_series"] != body_u["cost_basis_series"]
