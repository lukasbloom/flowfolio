from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models import Account, FxRate, HoldingTag, Instrument, PriceQuote, Tag, Transaction
from app.services.networth import (
    DailyPoint,
    NetWorthMarker,
    _snap_marker_dates,
    aggregate_points,
    build_markers,
    get_networth_series,
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
    symbol: str = "FLOW",
    base_currency: str = "EUR",
) -> tuple[Account, Instrument]:
    account = Account(name=f"{symbol} Account", account_type="broker", currency="EUR")
    instrument = Instrument(
        symbol=symbol,
        name=f"{symbol} Test",
        instrument_type="stock",
        base_currency=base_currency,
        price_source="manual",
    )
    session.add_all([account, instrument])
    await session.flush()
    return account, instrument


async def _txn(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    txn_type: str,
    trade_date: date,
    quantity: str,
    unit_price: str | None = "100",
    cost_basis_eur: str | None = None,
) -> Transaction:
    price = Decimal(unit_price) if unit_price is not None else None
    qty = Decimal(quantity)
    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type=txn_type,
        date=trade_date,
        quantity=qty,
        unit_price=price,
        price_currency="EUR" if price is not None else None,
        fx_rate_to_eur=Decimal("1") if price is not None else None,
        cost_basis_eur=Decimal(cost_basis_eur) if cost_basis_eur is not None else None,
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
    source: str = "manual",
) -> PriceQuote:
    quote = PriceQuote(
        instrument_id=instrument.id,
        date=quote_date,
        price=Decimal(price),
        currency=instrument.base_currency,
        source=source,
        fetched_at=datetime.combine(quote_date, datetime.min.time()),
    )
    session.add(quote)
    await session.flush()
    return quote


async def _fx(session: AsyncSession, fx_date: date, rate: str) -> FxRate:
    fx_rate = FxRate(
        date=fx_date,
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal(rate),
        source="manual",
    )
    session.add(fx_rate)
    await session.flush()
    return fx_rate


@pytest.mark.asyncio
async def test_replay_daily_value_carries_forward_weekend_price(session):
    account, instrument = await _seed_account_instrument(session)
    friday = date(2026, 1, 2)
    monday = date(2026, 1, 5)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=friday,
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
    )
    await _quote(session, instrument, quote_date=friday, price="100")
    await _quote(session, instrument, quote_date=monday, price="110")

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="EUR",
        start=friday,
        end=monday,
    )

    assert series.aggregation == "daily"
    assert [point.date for point in series.points] == [
        date(2026, 1, 2),
        date(2026, 1, 3),
        date(2026, 1, 4),
        date(2026, 1, 5),
    ]
    assert [point.value for point in series.points] == [
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("110"),
    ]
    assert series.warnings == []


@pytest.mark.asyncio
async def test_usd_display_uses_fx_rate_for_each_point_date(session):
    account, instrument = await _seed_account_instrument(session)
    start = date(2026, 1, 1)
    end = date(2026, 1, 2)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=start,
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
    )
    await _quote(session, instrument, quote_date=start, price="100")
    await _quote(session, instrument, quote_date=end, price="100")
    await _fx(session, start, "1.10")
    await _fx(session, end, "1.20")

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="USD",
        start=start,
        end=end,
    )

    assert [point.value for point in series.points] == [Decimal("110"), Decimal("120")]


@pytest.mark.asyncio
async def test_markers_value_converted_to_usd_when_display_usd(session):
    """Regression: markers must be in the chart's display currency."""
    account, instrument = await _seed_account_instrument(session)
    trade_date = date(2026, 1, 1)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=trade_date,
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
    )
    await _quote(session, instrument, quote_date=trade_date, price="100")
    await _fx(session, trade_date, "1.20")

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="USD",
        start=trade_date,
        end=trade_date,
    )

    buys = [m for m in series.markers if m.type == "buy"]
    assert len(buys) == 1
    # 100 EUR cost basis * 1.20 EUR/USD = 120 USD
    assert buys[0].value == Decimal("120.00")


@pytest.mark.asyncio
async def test_sell_marker_uses_gross_proceeds_not_zero(session):
    """Regression: sell markers must show proceeds, not 0."""
    account, instrument = await _seed_account_instrument(session)
    buy_date = date(2026, 1, 1)
    sell_date = date(2026, 1, 5)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=buy_date,
        quantity="2",
        unit_price="50",
        cost_basis_eur="100",
    )
    # Sell 1 unit at 60 EUR — gross proceeds = 60 EUR.
    sell = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="sell",
        date=sell_date,
        quantity=Decimal("-1"),
        unit_price=Decimal("60"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        cost_basis_eur=None,  # Sells don't stamp cost_basis_eur.
    )
    session.add(sell)
    await _quote(session, instrument, quote_date=buy_date, price="50")
    await _quote(session, instrument, quote_date=sell_date, price="60")

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="EUR",
        start=buy_date,
        end=sell_date,
    )

    sells = [m for m in series.markers if m.type == "sell"]
    assert len(sells) == 1
    assert sells[0].value == Decimal("60")


@pytest.mark.asyncio
async def test_networth_missing_fx_appends_warning_not_500(session):
    """Regression: USD holding + EUR display + no FX cache must not 500."""
    account, instrument = await _seed_account_instrument(
        session, symbol="USDX", base_currency="USD"
    )
    start = date(2026, 1, 1)
    end = date(2026, 1, 2)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=start,
        quantity="1",
        unit_price="100",
        cost_basis_eur="90.91",
    )
    await _quote(session, instrument, quote_date=start, price="100")
    await _quote(session, instrument, quote_date=end, price="100")
    # No _fx() seeded — replay must degrade to a warning, not raise.

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="EUR",
        start=start,
        end=end,
    )

    assert any(w.startswith("missing_fx:") for w in series.warnings)
    # Series still emits the points; the offending holding contributes 0 EUR.
    assert [p.date for p in series.points] == [start, end]


def test_aggregation_resolution_by_timeframe():
    start = date(2025, 1, 1)
    points = [
        DailyPoint(date=start + timedelta(days=offset), value=Decimal(offset))
        for offset in range(400)
    ]

    assert len(aggregate_points(points[:31], "1m", start, start + timedelta(days=30))) == 31
    assert len(aggregate_points(points[:91], "3m", start, start + timedelta(days=90))) == 91
    assert len(aggregate_points(points[:366], "1y", start, start + timedelta(days=365))) <= 54
    assert len(aggregate_points(points, "all", start, start + timedelta(days=399))) <= 14
    assert len(
        aggregate_points(points[:120], "custom", start, start + timedelta(days=119))
    ) <= 19


def test_marker_rollup_buy_sell_raw_yield_aggregated():
    instrument = Instrument(
        id="instrument-1",
        symbol="FLOW",
        name="Flow Test",
        instrument_type="stock",
        base_currency="EUR",
        price_source="manual",
    )
    txns = [
        Transaction(
            account_id="account-1",
            instrument_id=instrument.id,
            txn_type="buy",
            date=date(2026, 1, 1),
            quantity=Decimal("2"),
            unit_price=Decimal("50"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("100"),
        ),
        Transaction(
            account_id="account-1",
            instrument_id=instrument.id,
            txn_type="sell",
            date=date(2026, 1, 15),
            quantity=Decimal("-1"),
            unit_price=Decimal("60"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("60"),
        ),
        Transaction(
            account_id="account-1",
            instrument_id=instrument.id,
            txn_type="yield",
            date=date(2026, 1, 2),
            quantity=Decimal("0.1"),
            unit_price=None,
            price_currency=None,
            fx_rate_to_eur=None,
            cost_basis_eur=Decimal("5"),
        ),
        Transaction(
            account_id="account-1",
            instrument_id=instrument.id,
            txn_type="yield",
            date=date(2026, 1, 3),
            quantity=Decimal("0.2"),
            unit_price=None,
            price_currency=None,
            fx_rate_to_eur=None,
            cost_basis_eur=Decimal("7"),
        ),
    ]
    for txn in txns:
        setattr(txn, "_networth_instrument", instrument)

    # Equivalent to the original test: under timeframe="1y" the old
    # _yield_rollup_key grouped by (year, month). The two yields on Jan 2
    # and Jan 3 fall in the same monthly bucket, so monthly aggregation
    # preserves the merged-into-one-marker assertion below.
    markers = build_markers(txns, "monthly")

    buy_markers = [marker for marker in markers if marker.type == "buy"]
    sell_markers = [marker for marker in markers if marker.type == "sell"]
    yield_markers = [marker for marker in markers if marker.type == "yield"]
    assert len(buy_markers) == 1
    assert len(sell_markers) == 1
    assert len(yield_markers) == 1
    assert buy_markers[0].instrument_symbol == "FLOW"
    assert buy_markers[0].quantity == Decimal("2")
    assert sell_markers[0].quantity == Decimal("-1")
    assert yield_markers[0].quantity == Decimal("0.3")
    assert yield_markers[0].value == Decimal("12")
    assert yield_markers[0].count == 2


@pytest.mark.asyncio
async def test_marker_dates_snap_to_point_dates_weekly(session):
    # Custom range >90 days forces weekly aggregation. The buy and yield
    # txns fall on a Tuesday and Thursday — neither is the bucket anchor
    # (anchor = latest daily point in the iso-week, which on this range is
    # the Sunday end-of-week or the range_end if partial). Markers must
    # still land on a date that exists in points[].
    account, instrument = await _seed_account_instrument(session)
    start = date(2026, 1, 1)
    end = date(2026, 5, 31)  # 150 days → weekly
    buy_day = date(2026, 2, 17)  # Tuesday
    yield_day = date(2026, 3, 12)  # Thursday
    await _txn(
        session, account, instrument,
        txn_type="buy", trade_date=buy_day, quantity="1",
        unit_price="100", cost_basis_eur="100",
    )
    await _txn(
        session, account, instrument,
        txn_type="yield", trade_date=yield_day, quantity="0.05",
        unit_price=None, cost_basis_eur="3",
    )
    await _quote(session, instrument, quote_date=buy_day, price="100")

    series = await get_networth_series(
        session, timeframe="custom", display_currency="EUR", start=start, end=end,
    )

    assert series.aggregation == "weekly"
    point_dates = {p.date for p in series.points}
    assert series.markers, "expected at least one marker"
    txn_dates_by_type = {"buy": buy_day, "yield": yield_day}
    for marker in series.markers:
        assert marker.date in point_dates, (
            f"{marker.type} marker date {marker.date} not in aggregated point dates"
        )
        # Stronger: the snap must preserve iso-week — drifting to a
        # different week would still satisfy the in-set assertion above.
        original = txn_dates_by_type.get(marker.type)
        if original is not None:
            assert marker.date.isocalendar()[:2] == original.isocalendar()[:2], (
                f"{marker.type} marker {marker.date} drifted from {original}'s iso-week"
            )


@pytest.mark.asyncio
async def test_marker_dates_snap_to_point_dates_monthly(session):
    # timeframe="all" forces monthly. Bucket anchor is the latest daily
    # point in each calendar month — for past months that's the last day,
    # for the current month that's range_end. Markers placed mid-month
    # must snap to those anchors.
    account, instrument = await _seed_account_instrument(session)
    buy_day = date(2025, 6, 15)
    yield_day = date(2025, 9, 10)
    await _txn(
        session, account, instrument,
        txn_type="buy", trade_date=buy_day, quantity="1",
        unit_price="100", cost_basis_eur="100",
    )
    await _txn(
        session, account, instrument,
        txn_type="yield", trade_date=yield_day, quantity="0.05",
        unit_price=None, cost_basis_eur="3",
    )
    await _quote(session, instrument, quote_date=buy_day, price="100")

    series = await get_networth_series(
        session, timeframe="all", display_currency="EUR",
    )

    assert series.aggregation == "monthly"
    point_dates = {p.date for p in series.points}
    assert series.markers, "expected at least one marker"
    txn_dates_by_type = {"buy": buy_day, "yield": yield_day}
    for marker in series.markers:
        assert marker.date in point_dates
        # Stronger: snapped date must stay in the same calendar month as
        # the original txn — drifting to another month would still
        # satisfy the in-set assertion above.
        original = txn_dates_by_type.get(marker.type)
        if original is not None:
            assert (marker.date.year, marker.date.month) == (
                original.year,
                original.month,
            ), f"{marker.type} marker {marker.date} drifted from {original}'s month"


@pytest.mark.asyncio
async def test_instrument_filter_isolates_one_holding(session):
    # Two instruments held simultaneously. With instrument_id set, the
    # series must reflect ONLY the filtered instrument's value and markers,
    # not the combined portfolio.
    account, alpha = await _seed_account_instrument(session, symbol="ALPHA")
    beta = Instrument(
        symbol="BETA", name="Beta Test", instrument_type="stock",
        base_currency="EUR", price_source="manual",
    )
    session.add(beta)
    await session.flush()

    buy_day = date.today() - timedelta(days=5)
    await _txn(
        session, account, alpha,
        txn_type="buy", trade_date=buy_day, quantity="2",
        unit_price="100", cost_basis_eur="200",
    )
    await _txn(
        session, account, beta,
        txn_type="buy", trade_date=buy_day, quantity="1",
        unit_price="500", cost_basis_eur="500",
    )
    await _quote(session, alpha, quote_date=buy_day, price="100")
    await _quote(session, beta, quote_date=buy_day, price="500")

    full = await get_networth_series(
        session, timeframe="1m", display_currency="EUR",
    )
    alpha_only = await get_networth_series(
        session, timeframe="1m", display_currency="EUR",
        instrument_id=alpha.id,
    )

    last_full = full.points[-1].value
    last_alpha = alpha_only.points[-1].value
    assert last_full == Decimal("700")  # 2*100 + 1*500
    assert last_alpha == Decimal("200")  # only ALPHA: 2*100

    # Markers must only mention ALPHA when filtered.
    alpha_marker_symbols = {m.instrument_symbol for m in alpha_only.markers}
    assert alpha_marker_symbols == {"ALPHA"}


@pytest.mark.asyncio
async def test_buy_marker_uses_unit_price_fallback_when_cost_basis_eur_zero(session):
    # Symmetric to the sell-proceeds fallback: when cost_basis_eur is
    # null/zero on a buy (e.g. legacy import that only stored share
    # counts), the marker tooltip must still surface a useful number by
    # computing `quantity * unit_price / fx_rate` — same formula sells
    # already use.
    account, instrument = await _seed_account_instrument(session)
    buy_day = date.today() - timedelta(days=10)
    await _txn(
        session, account, instrument,
        txn_type="buy", trade_date=buy_day, quantity="5",
        unit_price="160", cost_basis_eur=None,  # No cost basis recorded.
    )
    await _quote(session, instrument, quote_date=buy_day, price="160")

    series = await get_networth_series(
        session, timeframe="1m", display_currency="EUR",
    )

    buy_markers = [m for m in series.markers if m.type == "buy"]
    assert len(buy_markers) == 1
    # quantity * unit_price / fx_rate_to_eur = 5 * 160 / 1 = 800
    assert buy_markers[0].value == Decimal("800")


@pytest.mark.asyncio
async def test_buy_marker_value_zero_when_neither_cost_basis_nor_unit_price(session):
    # Truly unpriced row (in-kind transfer with no metadata). Marker
    # value gracefully falls back to 0 — tooltip will show "= 0,00 €",
    # but at least we don't crash and the line still values the
    # position via the synthetic-quote path elsewhere.
    account, instrument = await _seed_account_instrument(session)
    buy_day = date.today() - timedelta(days=10)
    await _txn(
        session, account, instrument,
        txn_type="buy", trade_date=buy_day, quantity="5",
        unit_price=None, cost_basis_eur=None,
    )

    series = await get_networth_series(
        session, timeframe="1m", display_currency="EUR",
    )

    buy_markers = [m for m in series.markers if m.type == "buy"]
    assert len(buy_markers) == 1
    assert buy_markers[0].value == Decimal("0")


@pytest.mark.asyncio
async def test_replay_falls_back_to_transaction_unit_price_when_no_quote(session):
    # Buy on day D with unit_price=100 EUR and NO PriceQuote rows seeded.
    # Without the synthetic-quote fallback, the replay would skip the
    # holding and emit `missing_price:` warnings → all values stuck at 0.
    # With the fallback, every day from D onwards values at qty * unit_price.
    account, instrument = await _seed_account_instrument(session)
    buy_day = date(2026, 4, 1)
    end_day = date(2026, 4, 5)
    await _txn(
        session, account, instrument,
        txn_type="buy", trade_date=buy_day, quantity="2",
        unit_price="100", cost_basis_eur="200",
    )
    # Note: no _quote() call — entire range has zero PriceQuote rows.

    series = await get_networth_series(
        session, timeframe="custom", display_currency="EUR",
        start=buy_day, end=end_day,
    )

    assert series.aggregation == "daily"
    assert [p.value for p in series.points] == [Decimal("200")] * 5
    assert all(not w.startswith("missing_price:") for w in series.warnings), series.warnings


@pytest.mark.asyncio
async def test_real_quote_takes_precedence_over_transaction_fallback(session):
    # If a real PriceQuote exists for a date, it must win over the synthetic
    # fallback. Buy at 100 on day D; quote of 110 on day D+1; assert day D
    # values at 100 (synthetic) and days D+1..D+3 value at 110 (real quote).
    account, instrument = await _seed_account_instrument(session)
    buy_day = date(2026, 4, 1)
    quote_day = date(2026, 4, 2)
    end_day = date(2026, 4, 4)
    await _txn(
        session, account, instrument,
        txn_type="buy", trade_date=buy_day, quantity="1",
        unit_price="100", cost_basis_eur="100",
    )
    await _quote(session, instrument, quote_date=quote_day, price="110")

    series = await get_networth_series(
        session, timeframe="custom", display_currency="EUR",
        start=buy_day, end=end_day,
    )

    assert [p.value for p in series.points] == [
        Decimal("100"),  # buy_day — synthetic from txn.unit_price
        Decimal("110"),  # quote_day — real PriceQuote
        Decimal("110"),  # carry-forward
        Decimal("110"),  # carry-forward
    ]


@pytest.mark.asyncio
async def test_missing_quote_and_no_priced_txn_still_warns(session):
    # If a position exists but neither a PriceQuote nor a priced txn is
    # available (e.g. a yield accrual on a position that never had a
    # priced buy), the original missing_price warning must still fire —
    # this is the fall-through branch under the synthetic fallback.
    account, instrument = await _seed_account_instrument(session)
    yield_day = date(2026, 4, 1)
    end_day = date(2026, 4, 3)
    # Yield txn — unit_price=None by construction (see _txn helper default
    # behavior when unit_price=None below).
    await _txn(
        session, account, instrument,
        txn_type="yield", trade_date=yield_day, quantity="0.5",
        unit_price=None, cost_basis_eur="0",
    )

    series = await get_networth_series(
        session, timeframe="custom", display_currency="EUR",
        start=yield_day, end=end_day,
    )

    assert any(w.startswith("missing_price:") for w in series.warnings), series.warnings
    # All values should be 0 since the holding was skipped.
    assert all(p.value == Decimal("0") for p in series.points)


@pytest.mark.asyncio
async def test_marker_dates_unchanged_when_daily(session):
    # Regression guard: 1m timeframe is daily-aggregated, so the snap step
    # must be an identity — markers stay on their raw transaction dates.
    account, instrument = await _seed_account_instrument(session)
    buy_day = date.today() - timedelta(days=10)
    await _txn(
        session, account, instrument,
        txn_type="buy", trade_date=buy_day, quantity="1",
        unit_price="100", cost_basis_eur="100",
    )
    await _quote(session, instrument, quote_date=buy_day, price="100")

    series = await get_networth_series(
        session, timeframe="1m", display_currency="EUR",
    )

    assert series.aggregation == "daily"
    buy_markers = [m for m in series.markers if m.type == "buy"]
    assert len(buy_markers) == 1
    assert buy_markers[0].date == buy_day


def test_snap_marker_dates_keeps_orphan_markers_when_bucket_missing():
    """Defensive branch: a marker whose bucket has no anchor in the map
    keeps its original date instead of being dropped or crashing.

    In production the replay covers `[range_start, range_end]` so every
    marker date sits in a bucket that has an anchor — but if a future
    refactor breaks that invariant, the snap should fail soft.
    """
    orphan = NetWorthMarker(
        date=date(2030, 1, 1),  # No anchor for this bucket.
        type="buy",
        instrument_id="i1",
        instrument_symbol="X",
        quantity=Decimal("1"),
        value=Decimal("100"),
        count=1,
    )
    snapped = _snap_marker_dates([orphan], anchors_by_key={}, aggregation="weekly")
    assert len(snapped) == 1
    assert snapped[0].date == date(2030, 1, 1)
    # And the original marker must not be mutated.
    assert orphan.date == date(2030, 1, 1)


# ---------------------------------------------------------------------------
# include_cost_basis + tag_filter
# ---------------------------------------------------------------------------


async def _tag_holding(
    session: AsyncSession, account: Account, instrument: Instrument, name: str
) -> None:
    tag = Tag(name=name, color="#22c55e")
    session.add(tag)
    await session.flush()
    session.add(
        HoldingTag(account_id=account.id, instrument_id=instrument.id, tag_id=tag.id)
    )
    await session.flush()


@pytest.mark.asyncio
async def test_include_cost_basis_false_returns_empty_series(session):
    """Regression guard: legacy callers (no flag) keep getting cost_basis_series=[]."""
    account, instrument = await _seed_account_instrument(session)
    trade_date = date(2026, 1, 1)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=trade_date,
        quantity="1",
        unit_price="100",
        cost_basis_eur="100",
    )
    await _quote(session, instrument, quote_date=trade_date, price="100")

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="EUR",
        start=trade_date,
        end=trade_date,
    )

    assert series.cost_basis_series == []


@pytest.mark.asyncio
async def test_include_cost_basis_true_returns_aligned_series(session):
    """`cost_basis_series` length matches `points` and dates align bucket-for-bucket."""
    account, instrument = await _seed_account_instrument(session)
    start = date(2026, 1, 1)
    end = date(2026, 1, 5)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=start,
        quantity="2",
        unit_price="50",
        cost_basis_eur="100",
    )
    await _quote(session, instrument, quote_date=start, price="50")
    await _quote(session, instrument, quote_date=end, price="60")

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="EUR",
        start=start,
        end=end,
        include_cost_basis=True,
    )

    assert len(series.cost_basis_series) == len(series.points)
    assert [p.date for p in series.cost_basis_series] == [p.date for p in series.points]
    # Cost basis stays at 100 EUR across the range (no sells); value moves
    # from 100 → 120 once the 2026-01-05 quote lands.
    assert all(p.value == Decimal("100") for p in series.cost_basis_series)


@pytest.mark.asyncio
async def test_usd_cost_basis_series_constant_between_transaction_dates(session):
    """Regression: cost-basis-line-drifts-daily (networth endpoint).

    The USD-display cost-basis series must hold FLAT between transaction dates
    even when EUR/USD moves every day — each open lot is converted at ITS OWN
    transaction-date rate (transaction-time FX), not re-converted at each chart
    day's rate. Kept consistent with the parallel contributions regression.
    """
    account, instrument = await _seed_account_instrument(session, base_currency="USD")
    start = date(2026, 1, 1)
    end = date(2026, 1, 5)
    # USD-priced buy: cost_basis_eur stamped (110 USD / 1.10 = 100 EUR), with
    # fx_rate_to_eur = the EUR/USD rate locked at the trade date.
    buy = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="buy",
        date=start,
        quantity=Decimal("1"),
        unit_price=Decimal("110"),
        price_currency="USD",
        fx_rate_to_eur=Decimal("1.10"),
        cost_basis_eur=Decimal("100"),
    )
    session.add(buy)
    await session.flush()
    await _quote(session, instrument, quote_date=start, price="110")

    # FX moves every day after the buy — the drift trigger.
    await _fx(session, start, "1.10")
    await _fx(session, start + timedelta(days=1), "1.20")
    await _fx(session, start + timedelta(days=2), "1.30")
    await _fx(session, start + timedelta(days=3), "1.40")
    await _fx(session, end, "1.50")

    series = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="USD",
        start=start,
        end=end,
        include_cost_basis=True,
    )

    cost_values = [p.value for p in series.cost_basis_series]
    # 100 EUR * 1.10 (transaction-date rate) = 110 USD, flat across the window.
    assert cost_values[0] == Decimal("110.00000000")
    assert len(set(cost_values)) == 1


@pytest.mark.asyncio
async def test_tag_filter_restricts_value_and_cost_basis(session):
    """Tag filter narrows BOTH series to the tagged subset."""
    # Two holdings; only the first is tagged "Crypto". Both buy 100 EUR worth on the
    # same day with a quote that locks value to cost basis.
    account_a, instrument_a = await _seed_account_instrument(session, symbol="AAA")
    account_b, instrument_b = await _seed_account_instrument(session, symbol="BBB")
    trade_date = date(2026, 1, 1)
    await _txn(
        session,
        account_a,
        instrument_a,
        txn_type="buy",
        trade_date=trade_date,
        quantity="2",
        unit_price="50",
        cost_basis_eur="100",
    )
    await _txn(
        session,
        account_b,
        instrument_b,
        txn_type="buy",
        trade_date=trade_date,
        quantity="5",
        unit_price="50",
        cost_basis_eur="250",
    )
    await _quote(session, instrument_a, quote_date=trade_date, price="50")
    await _quote(session, instrument_b, quote_date=trade_date, price="50")
    await _tag_holding(session, account_a, instrument_a, "Crypto")

    tagged = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="EUR",
        start=trade_date,
        end=trade_date,
        tag_filter="Crypto",
        include_cost_basis=True,
    )
    untagged = await get_networth_series(
        session,
        timeframe="custom",
        display_currency="EUR",
        start=trade_date,
        end=trade_date,
        include_cost_basis=True,
    )

    # Tagged: only A (cost basis 100, value 100).
    assert tagged.points[-1].value == Decimal("100")
    assert tagged.cost_basis_series[-1].value == Decimal("100")
    # Untagged: A + B (cost basis 350, value 350).
    assert untagged.points[-1].value == Decimal("350")
    assert untagged.cost_basis_series[-1].value == Decimal("350")


@pytest.mark.asyncio
async def test_cost_basis_parity_with_contributions_service(session):
    """Same instrument scope ⇒ same per-bucket cost basis as contributions.get_cost_basis_series."""
    from app.services.contributions import get_cost_basis_series

    account, instrument = await _seed_account_instrument(session)
    buy_date = date.today() - timedelta(days=10)
    sell_date = date.today() - timedelta(days=2)
    await _txn(
        session,
        account,
        instrument,
        txn_type="buy",
        trade_date=buy_date,
        quantity="10",
        unit_price="50",
        cost_basis_eur="500",
    )
    sell = await _txn(
        session,
        account,
        instrument,
        txn_type="sell",
        trade_date=sell_date,
        quantity="-4",
        unit_price="60",
    )
    # Pin a FIFO allocation so _cost_basis_at sees a consumed lot.
    from app.models import LotAlloc as _LA

    # Find the buy id for the alloc.
    buy_id = (await session.execute(
        select(Transaction.id).where(Transaction.txn_type == "buy")
    )).scalar_one()
    session.add(
        _LA(
            buy_txn_id=buy_id,
            sell_txn_id=sell.id,
            quantity=Decimal("4"),
            realized_gain_eur=Decimal("40"),
        )
    )
    await session.flush()
    await _quote(session, instrument, quote_date=buy_date, price="50")
    await _quote(session, instrument, quote_date=sell_date, price="60")

    nw = await get_networth_series(
        session,
        timeframe="all",
        display_currency="EUR",
        include_cost_basis=True,
    )
    contrib_basis, _ = await get_cost_basis_series(session)

    nw_by_date = {p.date: p.value for p in nw.cost_basis_series}
    # Spot-check three anchor dates: pre-sell, post-sell, today.
    pre = buy_date + timedelta(days=1)
    post = sell_date
    today_ = date.today()
    contrib_by_date = {p.date: p.value for p in contrib_basis}
    for anchor in (pre, post, today_):
        if anchor in nw_by_date and anchor in contrib_by_date:
            assert nw_by_date[anchor] == contrib_by_date[anchor], (
                f"cost basis drift at {anchor}: networth={nw_by_date[anchor]} "
                f"contributions={contrib_by_date[anchor]}"
            )
