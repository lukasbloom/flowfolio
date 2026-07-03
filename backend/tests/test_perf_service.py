from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models import Account, FxRate, Instrument, LotAlloc, PriceQuote, Transaction
from app.services.perf import (
    calculate_open_lot_basis,
    calculate_twrr,
    get_performance_rows,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _seed_account_instrument(
    session: AsyncSession,
    *,
    account_name: str = "Revolut",
    symbol: str = "FLOW",
    instrument_name: str = "Flow Test",
    instrument_type: str = "stock",
    base_currency: str = "EUR",
) -> tuple[Account, Instrument]:
    account = Account(name=account_name, account_type="broker", currency="EUR")
    instrument = Instrument(
        symbol=symbol,
        name=instrument_name,
        instrument_type=instrument_type,
        base_currency=base_currency,
        price_source="manual",
    )
    session.add_all([account, instrument])
    await session.flush()
    return account, instrument


async def _buy(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    qty: str,
    unit_price: str,
    trade_date: date,
    currency: str = "EUR",
    fx_rate_to_eur: str = "1",
    cost_basis_eur: str | None = None,
) -> Transaction:
    quantity = Decimal(qty)
    price = Decimal(unit_price)
    fx_rate = Decimal(fx_rate_to_eur)
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="buy",
        date=trade_date,
        quantity=quantity,
        unit_price=price,
        price_currency=currency,
        fx_rate_to_eur=fx_rate,
        cost_basis_eur=(
            Decimal(cost_basis_eur) if cost_basis_eur is not None else quantity * price / fx_rate
        ),
    )
    session.add(txn)
    await session.flush()
    return txn


async def _sell(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    qty: str,
    unit_price: str,
    trade_date: date,
    currency: str = "EUR",
    fx_rate_to_eur: str = "1",
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="sell",
        date=trade_date,
        quantity=-Decimal(qty),
        unit_price=Decimal(unit_price),
        price_currency=currency,
        fx_rate_to_eur=Decimal(fx_rate_to_eur),
    )
    session.add(txn)
    await session.flush()
    return txn


async def _yield(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    qty: str,
    cost_basis_eur: str,
    trade_date: date,
) -> Transaction:
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="yield",
        date=trade_date,
        quantity=Decimal(qty),
        unit_price=None,
        price_currency=None,
        fx_rate_to_eur=None,
        cost_basis_eur=Decimal(cost_basis_eur),
        source="accrual",
    )
    session.add(txn)
    await session.flush()
    return txn


async def _quote(
    session: AsyncSession,
    instrument: Instrument,
    *,
    quote_date: date,
    price: str,
    currency: str = "EUR",
    source: str = "manual",
    fetched_at: datetime | None = None,
) -> PriceQuote:
    quote = PriceQuote(
        instrument_id=instrument.id,
        date=quote_date,
        price=Decimal(price),
        currency=currency,
        source=source,
        fetched_at=fetched_at or datetime.combine(quote_date, datetime.min.time()),
    )
    session.add(quote)
    await session.flush()
    return quote


@pytest.mark.asyncio
async def test_twrr_known_value_yield_internal_returns_five_percent(session):
    account, instrument = await _seed_account_instrument(session)
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="100",
        trade_date=date(2025, 1, 1),
        cost_basis_eur="100",
    )
    await _yield(
        session,
        account,
        instrument,
        qty="0.05",
        cost_basis_eur="5",
        trade_date=date(2025, 7, 1),
    )
    await _quote(session, instrument, quote_date=date(2025, 1, 1), price="100")
    await _quote(session, instrument, quote_date=date(2025, 7, 1), price="100")
    await _quote(session, instrument, quote_date=date(2026, 1, 1), price="100")

    result = await calculate_twrr(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        start=date(2025, 1, 1),
        end=date(2026, 1, 1),
    )

    assert result.twrr == Decimal("0.05")
    assert date(2025, 7, 1) not in result.boundary_dates


@pytest.mark.asyncio
async def test_twrr_subperiod_uses_boundary_prices_not_window_end(session):
    """Regression: TWRR must price each sub-period boundary individually."""
    account, instrument = await _seed_account_instrument(session)
    # Buy 1 unit at t0, second buy of 1 unit at mid-window.
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="100",
        trade_date=date(2025, 1, 1),
        cost_basis_eur="100",
    )
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="120",
        trade_date=date(2025, 7, 1),
        cost_basis_eur="120",
    )
    # Non-flat price series: 100 → 120 (mid) → 150 (end).
    await _quote(session, instrument, quote_date=date(2025, 1, 1), price="100")
    await _quote(session, instrument, quote_date=date(2025, 7, 1), price="120")
    await _quote(session, instrument, quote_date=date(2026, 1, 1), price="150")

    result = await calculate_twrr(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        start=date(2025, 1, 1),
        end=date(2026, 1, 1),
    )

    # Sub-period 1: 100 → 120 = +20% (1 unit)
    # Sub-period 2: 240 → 300 = +25% (2 units; 240 = 2*120 at start of sub-period)
    # Linked: 1.20 * 1.25 = 1.50 → twrr = 0.50
    assert result.twrr is not None
    assert result.twrr == Decimal("0.5")
    assert date(2025, 7, 1) in result.boundary_dates


@pytest.mark.asyncio
async def test_twrr_total_loss_does_not_crash_annualization(session):
    """Regression: a -100% TWRR over a multi-year window must not raise.

    Decimal raises InvalidOperation for 0 ** fractional. The service should
    short-circuit annualization and return the raw period TWRR instead of
    500ing the whole /api/perf endpoint.
    """
    account, instrument = await _seed_account_instrument(session)
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="100",
        trade_date=date(2024, 1, 1),
        cost_basis_eur="100",
    )
    # Price collapses to zero over a >365-day window.
    await _quote(session, instrument, quote_date=date(2024, 1, 1), price="100")
    await _quote(session, instrument, quote_date=date(2024, 6, 1), price="50")
    # Use a tiny epsilon as the end price — sub-period quote lookup needs >0.
    await _quote(session, instrument, quote_date=date(2025, 7, 1), price="0.0000000001")

    result = await calculate_twrr(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        start=date(2024, 1, 1),
        end=date(2025, 7, 1),  # 547 days, > 365 → triggers annualization branch
    )

    # Must not raise. This is the regression guard. Exact DecimalText
    # storage changed the path: the 1e-10 end price no longer truncates to 0
    # (old Numeric(20,8) did), so the annualization base (1 + twrr) is a tiny
    # positive number rather than exactly 0. Annualization therefore runs and
    # returns a finite, near-total-loss TWRR instead of taking the
    # base<=0 short-circuit. Either way the endpoint does not 500.
    assert result.twrr is not None
    assert result.twrr.is_finite()
    assert result.twrr <= Decimal("-0.999")
    assert result.twrr_annualized is True


@pytest.mark.asyncio
async def test_perf_missing_fx_rate_emits_reason_not_500(session):
    """Regression: a USD holding with no FX cache must not 500."""
    account, instrument = await _seed_account_instrument(
        session, symbol="USDX", instrument_name="USD Equity", base_currency="USD"
    )
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="100",
        trade_date=date(2025, 1, 1),
        currency="USD",
        fx_rate_to_eur="1.10",
        cost_basis_eur="90.91",
    )
    await _quote(session, instrument, quote_date=date(2026, 1, 1), price="120", currency="USD")
    # Note: no FxRate rows seeded.

    rows = await get_performance_rows(
        session, timeframe="1y", display_currency="EUR", today=date(2026, 1, 1)
    )

    assert len(rows) == 1
    assert rows[0].twrr_reason == "missing_fx"
    assert rows[0].current_value is None


@pytest.mark.asyncio
async def test_open_lot_basis_excludes_consumed_fifo_lots_and_yield_qty(session):
    account, instrument = await _seed_account_instrument(session)
    buy_a = await _buy(
        session,
        account,
        instrument,
        qty="10",
        unit_price="10",
        trade_date=date(2025, 1, 1),
        cost_basis_eur="100",
    )
    buy_b = await _buy(
        session,
        account,
        instrument,
        qty="10",
        unit_price="20",
        trade_date=date(2025, 2, 1),
        cost_basis_eur="200",
    )
    sell = await _sell(
        session,
        account,
        instrument,
        qty="12",
        unit_price="20",
        trade_date=date(2025, 3, 1),
    )
    await _yield(
        session,
        account,
        instrument,
        qty="1",
        cost_basis_eur="5",
        trade_date=date(2025, 4, 1),
    )
    session.add_all(
        [
            LotAlloc(sell_txn_id=sell.id, buy_txn_id=buy_a.id, quantity=Decimal("10")),
            LotAlloc(sell_txn_id=sell.id, buy_txn_id=buy_b.id, quantity=Decimal("2")),
        ]
    )
    await session.flush()

    basis = await calculate_open_lot_basis(session, account.id, instrument.id)

    assert basis.open_buy_quantity == Decimal("8")
    assert basis.open_buy_basis_eur == Decimal("160")
    assert basis.open_quantity == Decimal("9")
    assert basis.avg_cost_eur == Decimal("17.777777777777777778")


@pytest.mark.asyncio
async def test_insufficient_history_returns_null_twrr_reason(session):
    account, instrument = await _seed_account_instrument(session)
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="100",
        trade_date=date(2026, 1, 1),
        cost_basis_eur="100",
    )
    for day in range(1, 5):
        await _quote(session, instrument, quote_date=date(2026, 1, day), price="100")

    rows = await get_performance_rows(
        session,
        timeframe="1m",
        display_currency="EUR",
        today=date(2026, 1, 4),
    )

    assert len(rows) == 1
    assert rows[0].twrr is None
    assert rows[0].twrr_reason == "insufficient_history"


@pytest.mark.asyncio
async def test_multicurrency_percent_return_uses_locked_cost_and_current_fx(session):
    account, instrument = await _seed_account_instrument(
        session, symbol="USD", instrument_name="USD Equity", base_currency="USD"
    )
    await _buy(
        session,
        account,
        instrument,
        qty="2",
        unit_price="50",
        trade_date=date(2025, 1, 1),
        currency="USD",
        fx_rate_to_eur="1.25",
        cost_basis_eur="80",
    )
    await _quote(
        session,
        instrument,
        quote_date=date(2026, 1, 1),
        price="75",
        currency="USD",
    )
    session.add(
        FxRate(
            date=date(2026, 1, 1),
            base_currency="EUR",
            quote_currency="USD",
            rate=Decimal("1.50"),
            source="manual",
        )
    )
    await session.flush()

    rows = await get_performance_rows(
        session,
        timeframe="1y",
        display_currency="EUR",
        today=date(2026, 1, 1),
    )

    assert len(rows) == 1
    assert rows[0].open_buy_basis == Decimal("80")
    assert rows[0].current_value == Decimal("100")
    assert rows[0].percent_return == Decimal("0.25")


@pytest.mark.asyncio
async def test_perf_currency_usd_converts_avg_cost_and_current_price_to_usd(session):
    """?currency=USD must convert avg_cost (EUR-denominated basis)
    and current_price (native quote currency) into USD using the FX rate
    at as_of. Without the fix, both fields were returned as their raw
    EUR / native values regardless of display_currency."""
    account, instrument = await _seed_account_instrument(
        session, symbol="USD", instrument_name="USD Equity", base_currency="USD"
    )
    await _buy(
        session,
        account,
        instrument,
        qty="2",
        unit_price="50",
        trade_date=date(2025, 1, 1),
        currency="USD",
        fx_rate_to_eur="1.25",
        cost_basis_eur="80",
    )
    await _quote(
        session,
        instrument,
        quote_date=date(2026, 1, 1),
        price="75",
        currency="USD",
    )
    session.add(
        FxRate(
            date=date(2026, 1, 1),
            base_currency="EUR",
            quote_currency="USD",
            rate=Decimal("1.50"),
            source="manual",
        )
    )
    await session.flush()

    eur_rows = await get_performance_rows(
        session,
        timeframe="1y",
        display_currency="EUR",
        today=date(2026, 1, 1),
    )
    usd_rows = await get_performance_rows(
        session,
        timeframe="1y",
        display_currency="USD",
        today=date(2026, 1, 1),
    )

    assert len(eur_rows) == 1 and len(usd_rows) == 1
    eur, usd = eur_rows[0], usd_rows[0]

    # avg_cost: open-lot basis is 80 EUR for 2 units -> 40 EUR/unit.
    # At FX 1.50 -> 60 USD/unit.
    assert eur.avg_cost == Decimal("40")
    assert usd.avg_cost == Decimal("60")

    # current_price: native USD quote = 75. EUR-mode converts 75 USD / 1.50 = 50 EUR.
    # USD-mode keeps 75 USD (identity conversion when from==to).
    assert eur.current_price == Decimal("50")
    assert usd.current_price == Decimal("75")

    # The two responses MUST differ for at least one numeric headline field.
    assert eur.avg_cost != usd.avg_cost
    assert eur.current_price != usd.current_price


@pytest.mark.asyncio
async def test_perf_currency_usd_missing_fx_falls_through_with_reason(session):
    """Regression: when EUR/USD FX is missing, a USD holding with
    ?currency=USD must NOT 500 — the row falls through with
    twrr_reason='missing_fx' (the avg_cost EUR->USD conversion is the
    branch that needs FX)."""
    account, instrument = await _seed_account_instrument(
        session, symbol="USDX", instrument_name="USD Equity", base_currency="USD"
    )
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="100",
        trade_date=date(2025, 1, 1),
        currency="USD",
        fx_rate_to_eur="1.10",
        cost_basis_eur="90.91",
    )
    await _quote(session, instrument, quote_date=date(2026, 1, 1), price="120", currency="USD")
    # Note: no FxRate rows seeded.

    rows = await get_performance_rows(
        session, timeframe="1y", display_currency="USD", today=date(2026, 1, 1)
    )

    assert len(rows) == 1
    assert rows[0].twrr_reason == "missing_fx"
    # current_price stays in native USD (identity conversion needs no FX);
    # avg_cost requires EUR->USD FX which is missing.
    assert rows[0].avg_cost is None


@pytest.mark.asyncio
async def test_perf_custom_range_matches_equivalent_preset_window(session):
    """A custom range covering the same window as a preset
    should produce the same row shape (matching TWRR over the equivalent window)."""
    account, instrument = await _seed_account_instrument(session)
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="100",
        trade_date=date(2025, 12, 25),
        cost_basis_eur="100",
    )
    # 7 quote days so insufficient_history doesn't trip.
    for day in range(1, 8):
        await _quote(session, instrument, quote_date=date(2025, 12, day + 24), price="100")
    await _quote(session, instrument, quote_date=date(2026, 1, 1), price="100")

    preset_rows = await get_performance_rows(
        session,
        timeframe="1m",
        display_currency="EUR",
        today=date(2026, 1, 1),
    )
    custom_rows = await get_performance_rows(
        session,
        timeframe="custom",
        display_currency="EUR",
        from_date=date(2025, 12, 2),  # 30 days before 2026-01-01, same as "1m"
        to_date=date(2026, 1, 1),
    )

    assert len(preset_rows) == 1
    assert len(custom_rows) == 1
    p, c = preset_rows[0], custom_rows[0]
    assert c.quantity == p.quantity
    assert c.current_value == p.current_value
    assert c.twrr == p.twrr
    assert c.twrr_reason == p.twrr_reason


@pytest.mark.asyncio
async def test_perf_custom_range_missing_dates_raises_value_error(session):
    """Service-level guard mirrors the router's 422."""
    with pytest.raises(ValueError, match="from_date and to_date"):
        await get_performance_rows(
            session,
            timeframe="custom",
            display_currency="EUR",
        )


@pytest.mark.asyncio
async def test_perf_current_price_null_drops_fetched_at(session):
    """Regression: when current_price ends up None because its native
    -> display FX conversion failed, current_price_fetched_at must travel
    with it (null) so freshness consumers don't treat the row as priced."""
    account, instrument = await _seed_account_instrument(
        session, symbol="USDY", instrument_name="USD Equity", base_currency="USD"
    )
    await _buy(
        session,
        account,
        instrument,
        qty="1",
        unit_price="100",
        trade_date=date(2025, 1, 1),
        currency="USD",
        fx_rate_to_eur="1.10",
        cost_basis_eur="90.91",
    )
    await _quote(session, instrument, quote_date=date(2026, 1, 1), price="120", currency="USD")
    # No FxRate rows — display USD->EUR will need FX and fail.

    rows = await get_performance_rows(
        session, timeframe="1y", display_currency="EUR", today=date(2026, 1, 1)
    )

    assert len(rows) == 1
    assert rows[0].current_price is None
    assert rows[0].current_price_fetched_at is None
    assert rows[0].twrr_reason == "missing_fx"
