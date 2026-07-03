"""
Tests that fx_rate_to_eur is locked at insert time and never mutated by subsequent queries.
This guards against the common pitfall of applying the current FX rate to historical cost basis.
"""
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models import Account, Instrument, Transaction


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_fx_rate_locked_on_insert(session):
    """
    fx_rate_to_eur must be stored and retrieved with full NUMERIC precision.
    Updating unrelated columns must not change the stored FX rate.
    """
    acct = Account(name="TestBroker", account_type="broker", currency="EUR")
    inst = Instrument(
        symbol="AAPL", name="Apple", instrument_type="stock",
        base_currency="USD", price_source="finnhub",
    )
    session.add_all([acct, inst])
    await session.flush()

    original_fx = Decimal("1.123456789")  # 9 decimal places — within NUMERIC(20,10)
    txn = Transaction(
        account_id=acct.id,
        instrument_id=inst.id,
        txn_type="buy",
        date=date(2024, 3, 15),
        quantity=Decimal("100"),
        unit_price=Decimal("150.50"),
        price_currency="USD",
        fx_rate_to_eur=original_fx,
        cost_basis_eur=(Decimal("100") * Decimal("150.50") / original_fx).quantize(
            Decimal("0.00000001")
        ),
    )
    session.add(txn)
    await session.commit()

    # Re-fetch from DB to confirm precision is preserved
    result = await session.execute(select(Transaction).where(Transaction.id == txn.id))
    fetched = result.scalar_one()

    assert fetched.fx_rate_to_eur == original_fx, (
        f"FX rate must be preserved exactly: expected {original_fx}, got {fetched.fx_rate_to_eur}"
    )
    assert isinstance(fetched.fx_rate_to_eur, Decimal), (
        f"fx_rate_to_eur must be Decimal, not {type(fetched.fx_rate_to_eur)}"
    )

    # Simulate updating notes (an unrelated field) — fx_rate must not change
    fetched.notes = "Updated notes"
    await session.commit()

    result2 = await session.execute(select(Transaction).where(Transaction.id == txn.id))
    fetched2 = result2.scalar_one()
    assert fetched2.fx_rate_to_eur == original_fx, (
        "fx_rate_to_eur must not change after updating an unrelated field"
    )
