"""Integration test for /api/perf?include_closed flag semantics.

Asserts:
1. Default (no flag) returns only open rows; every row has status='open'.
2. include_closed=1 returns both; closed rows have status='closed' + last_close/last_close_date set.
3. include_closed=0 is equivalent to default.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, Instrument, LotAlloc, PriceQuote, Transaction
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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_OPEN_SYMBOL = "AAPL"
_CLOSED_SYMBOL = "MSFT"

# Use fixed past dates so TWRR window includes at least 2 quotes.
_BUY_DATE = date(2025, 1, 1)
_SELL_DATE = date(2025, 6, 1)
_TODAY = date.today()


async def _seed_open_and_closed_position(maker) -> None:
    """Seed one open instrument (buy still held) + one closed instrument (buy + full sell).

    Open:  AAPL — BUY 10 @ EUR 100 on 2025-01-01; price quotes at buy date and today.
    Closed: MSFT — BUY 5 @ EUR 200 on 2025-01-01; SELL 5 @ EUR 250 on 2025-06-01
            with LotAlloc linking sell → buy.  Quotes at buy date and sell date.
    """
    async with maker() as session:
        account = Account(name="Revolut", account_type="broker", currency="EUR")
        aapl = Instrument(
            symbol=_OPEN_SYMBOL,
            name="Apple Inc",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        msft = Instrument(
            symbol=_CLOSED_SYMBOL,
            name="Microsoft Corp",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, aapl, msft])
        await session.flush()

        # ── Open position: BUY AAPL (never sold) ──
        buy_aapl = Transaction(
            account_id=account.id,
            instrument_id=aapl.id,
            txn_type="buy",
            date=_BUY_DATE,
            quantity=Decimal("10"),
            unit_price=Decimal("100"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("1000"),
        )
        session.add(buy_aapl)
        await session.flush()

        # Price quotes for AAPL: at buy date and for the past 7 days (enough history).
        for days_ago in range(6, -1, -1):
            session.add(
                PriceQuote(
                    instrument_id=aapl.id,
                    date=_TODAY - timedelta(days=days_ago),
                    price=Decimal("120"),
                    currency="EUR",
                    source="manual",
                )
            )
        # Add quote at buy date for TWRR window calculation.
        session.add(
            PriceQuote(
                instrument_id=aapl.id,
                date=_BUY_DATE,
                price=Decimal("100"),
                currency="EUR",
                source="manual",
            )
        )

        # ── Closed position: BUY then full SELL MSFT ──
        buy_msft = Transaction(
            account_id=account.id,
            instrument_id=msft.id,
            txn_type="buy",
            date=_BUY_DATE,
            quantity=Decimal("5"),
            unit_price=Decimal("200"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("1000"),
        )
        session.add(buy_msft)
        await session.flush()

        sell_msft = Transaction(
            account_id=account.id,
            instrument_id=msft.id,
            txn_type="sell",
            date=_SELL_DATE,
            quantity=Decimal("-5"),
            unit_price=Decimal("250"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
        )
        session.add(sell_msft)
        await session.flush()

        session.add(
            LotAlloc(
                sell_txn_id=sell_msft.id,
                buy_txn_id=buy_msft.id,
                quantity=Decimal("5"),
                realized_gain_eur=Decimal("250"),
            )
        )

        # Price quotes for MSFT: at buy date and sell date (two distinct dates → TWRR works).
        session.add(
            PriceQuote(
                instrument_id=msft.id,
                date=_BUY_DATE,
                price=Decimal("200"),
                currency="EUR",
                source="manual",
            )
        )
        session.add(
            PriceQuote(
                instrument_id=msft.id,
                date=_SELL_DATE,
                price=Decimal("250"),
                currency="EUR",
                source="manual",
            )
        )

        await session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_perf_default_excludes_closed(authed_client):
    """GET /api/perf (no flag) returns only open rows; closed instrument is absent."""
    client, maker = authed_client
    await _seed_open_and_closed_position(maker)

    resp = await client.get("/api/perf")
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    symbols = {r["instrument_symbol"] for r in rows}
    assert _CLOSED_SYMBOL not in symbols, (
        f"closed {_CLOSED_SYMBOL} leaked into default /api/perf: {symbols}"
    )
    assert _OPEN_SYMBOL in symbols

    for r in rows:
        assert r["status"] == "open", f"non-open row without include_closed flag: {r}"


@pytest.mark.asyncio
async def test_perf_include_closed_returns_both(authed_client):
    """GET /api/perf?include_closed=1 returns open + closed rows with correct discriminators."""
    client, maker = authed_client
    await _seed_open_and_closed_position(maker)

    resp = await client.get("/api/perf?include_closed=1")
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    symbols = {r["instrument_symbol"] for r in rows}
    assert _OPEN_SYMBOL in symbols, f"open instrument missing: {symbols}"
    assert _CLOSED_SYMBOL in symbols, f"closed instrument missing: {symbols}"

    open_rows = [r for r in rows if r["status"] == "open"]
    closed_rows = [r for r in rows if r["status"] == "closed"]
    assert open_rows, f"no open rows in response: {rows}"
    assert closed_rows, f"no closed rows in response: {rows}"

    msft = next(r for r in closed_rows if r["instrument_symbol"] == _CLOSED_SYMBOL)
    assert msft["last_close"] is not None, f"last_close should be populated for closed row: {msft}"
    assert msft["last_close_date"] is not None, f"last_close_date should be set: {msft}"
    assert msft["status"] == "closed", msft


@pytest.mark.asyncio
async def test_perf_include_closed_custom_range(authed_client):
    """?timeframe=custom&from=...&to=...&include_closed=1 returns both branches."""
    client, maker = authed_client
    await _seed_open_and_closed_position(maker)

    # Pick a window that covers the AAPL price history (last 7 days) so the open
    # row has sufficient history. Sell happened 2025-06-01 — closed row appears
    # regardless of timeframe (include_closed branch is timeframe-invariant).
    today = date.today()
    frm = (today - timedelta(days=6)).isoformat()
    to_ = today.isoformat()
    resp = await client.get(
        f"/api/perf?timeframe=custom&from={frm}&to={to_}&include_closed=1"
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()

    symbols = {r["instrument_symbol"] for r in rows}
    assert _OPEN_SYMBOL in symbols
    assert _CLOSED_SYMBOL in symbols


@pytest.mark.asyncio
async def test_perf_include_closed_zero_equivalent_to_default(authed_client):
    """GET /api/perf?include_closed=0 behaves identically to omitting the flag."""
    client, maker = authed_client
    await _seed_open_and_closed_position(maker)

    resp_default = await client.get("/api/perf")
    resp_explicit = await client.get("/api/perf?include_closed=0")
    assert resp_default.status_code == 200
    assert resp_explicit.status_code == 200

    # Compare symbol-status pairs — timestamps may differ slightly.
    def shape(r):
        return sorted((x["instrument_symbol"], x["status"]) for x in r.json())

    assert shape(resp_default) == shape(resp_explicit), (
        f"include_closed=0 differs from default:\n"
        f"  default:  {shape(resp_default)}\n"
        f"  explicit: {shape(resp_explicit)}"
    )
