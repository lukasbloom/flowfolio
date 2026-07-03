"""Decimal storage fidelity regression (plan 006).

SQLite has no decimal type: SQLAlchemy's Numeric binds Decimal values as
floats, so >15-significant-digit money values were silently corrupted at the
DB boundary and SQL SUM() did float arithmetic. These tests pin the
exact-Decimal contract that the DecimalText storage type + Python-side
aggregation must satisfy.

Test 1 (round-trip) FAILS before the column swap (Step 3) — that failure is
the bug. Tests 2/3 exercise the service-level Python aggregation that must
return exact Decimals after the SQL sums move to Python.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.services.closed import _closed_holding_set
from app.services.perf import calculate_open_lot_basis


@pytest.mark.asyncio
async def test_high_precision_quantity_round_trips_exactly(
    db_session, make_account, make_instrument, make_transaction
):
    """An 18-decimal crypto quantity must read back bit-for-bit identical."""
    acct = await make_account(db_session)
    inst = await make_instrument(db_session, symbol="USDC", instrument_type="stablecoin")

    qty = Decimal("1012.542910340662615454")
    txn = await make_transaction(
        db_session,
        account=acct,
        instrument=inst,
        txn_type="buy",
        date=date(2026, 1, 1),
        quantity=qty,
        unit_price=Decimal("1"),
        fx_rate_to_eur=Decimal("1"),
    )
    await db_session.commit()

    # The unmaskable contract: the value must be STORED losslessly. With
    # Numeric(...) the sqlite dialect binds Decimal as a float and SQLite
    # stores REAL (1012.5429103406626 — digits beyond ~16 sig figs gone). The
    # ORM read path partially masks this via the column's scale-aware result
    # formatter, so we assert on the raw storage layer instead, which is what
    # SQL-level SUM/HAVING actually operate on.
    row = (
        await db_session.execute(
            text("SELECT typeof(quantity), quantity FROM 'transaction' WHERE id = :id"),
            {"id": txn.id},
        )
    ).one()
    stored_type, stored_value = row
    assert stored_type == "text", f"quantity stored as {stored_type!r}, not text"
    assert stored_value == "1012.542910340662615454"

    # And the ORM round-trip must also be exact.
    txn_id = txn.id
    db_session.expunge_all()
    refetched = await db_session.get(type(txn), txn_id)
    assert refetched is not None
    assert refetched.quantity == qty
    assert format(refetched.quantity, "f") == "1012.542910340662615454"


@pytest.mark.asyncio
async def test_decimal_sum_is_exact(
    db_session, make_account, make_instrument, make_transaction
):
    """Ten buys of 0.1 must sum to exactly Decimal('1'), not 0.9999999999...."""
    acct = await make_account(db_session)
    inst = await make_instrument(db_session, symbol="ETH", instrument_type="crypto")

    for i in range(10):
        await make_transaction(
            db_session,
            account=acct,
            instrument=inst,
            txn_type="buy",
            date=date(2026, 1, 1 + i),
            quantity=Decimal("0.1"),
            unit_price=Decimal("1"),
            fx_rate_to_eur=Decimal("1"),
        )
    await db_session.commit()

    basis = await calculate_open_lot_basis(db_session, acct.id, inst.id)
    assert basis.open_quantity == Decimal("1")


@pytest.mark.asyncio
async def test_fully_closed_position_classifies_as_zero(
    db_session, make_account, make_instrument, make_transaction
):
    """A buy and an equal sell of an 18-decimal quantity must net to exactly
    zero so the closed-set classifier sees the holding as CLOSED — float dust
    would otherwise leave a non-zero residual and mis-classify it as open."""
    acct = await make_account(db_session)
    inst = await make_instrument(db_session, symbol="BTC", instrument_type="crypto")

    qty = Decimal("0.123456789012345678")
    await make_transaction(
        db_session,
        account=acct,
        instrument=inst,
        txn_type="buy",
        date=date(2026, 1, 1),
        quantity=qty,
        unit_price=Decimal("1"),
        fx_rate_to_eur=Decimal("1"),
    )
    await make_transaction(
        db_session,
        account=acct,
        instrument=inst,
        txn_type="sell",
        date=date(2026, 1, 2),
        quantity=qty,
        unit_price=Decimal("1"),
        fx_rate_to_eur=Decimal("1"),
    )
    await db_session.commit()

    closed = await _closed_holding_set(db_session)
    assert (acct.id, inst.id) in closed
