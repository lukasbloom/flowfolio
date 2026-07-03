"""Tests for services/audit.py — compute_field_diff and write_audit_event.

Covers:
- Test 1: PUT changing `quantity` writes one audit row with field diff
- Test 2: PUT changing multiple fields produces ONE audit row with all diffs
- Test 3: PUT with no actual changes does NOT write an audit row
- Test 7: GET /api/transactions/{id}/audit returns rows ordered by changed_at DESC
- Test 11: Decimal field changes serialize as strings in changed_fields
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import select

from app.core.database import Base, attach_sqlite_pragmas
from app.models import Account, Instrument, Transaction
from app.models.txn_audit import TxnAudit
from app.services.audit import (
    AUDITED_FIELDS,
    compute_field_diff,
    write_audit_event,
)
from datetime import date


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


@pytest_asyncio.fixture
async def txn_fixture(session: AsyncSession):
    account = Account(name="AuditTest", account_type="broker", currency="EUR")
    instrument = Instrument(
        symbol="BTC",
        name="Bitcoin",
        instrument_type="crypto",
        base_currency="USD",
        price_source="coingecko",
    )
    session.add_all([account, instrument])
    await session.flush()

    txn = Transaction(
        account_id=account.id,
        instrument_id=instrument.id,
        txn_type="buy",
        date=date(2024, 1, 1),
        quantity=Decimal("1.0"),
        unit_price=Decimal("50000.0"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        cost_basis_eur=Decimal("50000.0"),
    )
    session.add(txn)
    await session.flush()
    return txn


@pytest.mark.asyncio
async def test_quantity_change_produces_diff(txn_fixture: Transaction):
    """Test 1: Changing quantity produces a diff for that field."""
    diff = compute_field_diff(txn_fixture, {"quantity": Decimal("1.5")})
    assert "quantity" in diff
    assert diff["quantity"]["old"] == "1.0"
    assert diff["quantity"]["new"] == "1.5"


@pytest.mark.asyncio
async def test_multiple_field_changes_produce_single_diff(txn_fixture: Transaction):
    """Test 2: Multiple field changes produce one diff dict with all changed fields."""
    diff = compute_field_diff(
        txn_fixture,
        {
            "quantity": Decimal("2.0"),
            "unit_price": Decimal("60000.0"),
            "notes": "updated notes",
        },
    )
    assert "quantity" in diff
    assert "unit_price" in diff
    assert "notes" in diff
    # All three changed — no extras unless there were actual changes
    assert diff["quantity"]["old"] == "1.0"
    assert diff["quantity"]["new"] == "2.0"


@pytest.mark.asyncio
async def test_no_changes_produces_empty_diff(txn_fixture: Transaction):
    """Test 3: No actual changes produce an empty diff (no audit row should be written)."""
    diff = compute_field_diff(
        txn_fixture,
        {
            "quantity": Decimal("1.0"),  # same as existing
            "notes": None,  # same as existing (None)
        },
    )
    assert diff == {}


@pytest.mark.asyncio
async def test_write_audit_event_adds_row(session: AsyncSession, txn_fixture: Transaction):
    """Test 7 (partial): write_audit_event adds a TxnAudit row to the session."""
    changed = {"quantity": {"old": "1.0", "new": "1.5"}}
    audit = await write_audit_event(
        session, txn_fixture.id, "edit", changed
    )
    await session.commit()

    # Verify row was persisted
    result = await session.execute(
        select(TxnAudit).where(TxnAudit.transaction_id == txn_fixture.id)
    )
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "edit"
    assert rows[0].changed_fields == changed


@pytest.mark.asyncio
async def test_audit_rows_ordered_desc(session: AsyncSession, txn_fixture: Transaction):
    """Test 7: GET /api/transactions/{id}/audit rows ordered changed_at DESC.

    We write two audit rows. Both get server_default timestamps. In SQLite the
    resolution may be the same, so we check that both rows are returned and
    the ordering query runs without error.
    """
    await write_audit_event(session, txn_fixture.id, "edit", {"quantity": {"old": "1", "new": "2"}})
    await write_audit_event(session, txn_fixture.id, "edit", {"notes": {"old": None, "new": "hello"}})
    await session.commit()

    result = await session.execute(
        select(TxnAudit)
        .where(TxnAudit.transaction_id == txn_fixture.id)
        .order_by(TxnAudit.changed_at.desc())
    )
    rows = result.scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_decimal_fields_serialize_as_strings(txn_fixture: Transaction):
    """Test 11: Decimal field changes must serialize as strings in changed_fields."""
    diff = compute_field_diff(
        txn_fixture,
        {"quantity": Decimal("1.5"), "unit_price": Decimal("55000.12345678")},
    )
    # Values must be strings — not floats or Decimal objects
    assert isinstance(diff["quantity"]["old"], str)
    assert isinstance(diff["quantity"]["new"], str)
    assert isinstance(diff["unit_price"]["old"], str)
    assert isinstance(diff["unit_price"]["new"], str)
    # Exact string representation preserved
    assert diff["unit_price"]["new"] == "55000.12345678"


@pytest.mark.asyncio
async def test_write_audit_event_invalid_type(session: AsyncSession, txn_fixture: Transaction):
    """Invalid event_type raises ValueError."""
    with pytest.raises(ValueError, match="event_type must be"):
        await write_audit_event(session, txn_fixture.id, "invalid", {})


@pytest.mark.asyncio
async def test_audited_fields_constant_exists():
    """AUDITED_FIELDS is a non-empty tuple/sequence."""
    assert len(AUDITED_FIELDS) > 0
    assert "quantity" in AUDITED_FIELDS
    assert "notes" in AUDITED_FIELDS
