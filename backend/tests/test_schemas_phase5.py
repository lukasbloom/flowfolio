"""
Pydantic schema tests.

Verifies:
- All six new reconciliation schemas exist and instantiate cleanly.
- ReconciliationCreate rejects future snapshot_date.
- ReconciliationCreate caps notes at 2000 chars.
- TransactionCreate accepts optional reconciliation_id.
- TransactionResponse exposes reconciliation_id.
- AccountResponse exposes last_reconciled_date (nullable).
- TransactionCreate guard against manual adjustment is preserved (yield is now allowed).
- Decimal serialization is preserved.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError


def test_holding_snapshot_entry_round_trips_decimal_as_string():
    from app.schemas.reconciliation import HoldingSnapshotEntry

    e = HoldingSnapshotEntry(instrument_id="i1", snapshot_qty=Decimal("1.5"))
    j = json.loads(e.model_dump_json())
    assert j["snapshot_qty"] == "1.5"


def test_drift_decision_action_literal_enforced():
    from app.schemas.reconciliation import DriftDecision

    DriftDecision(instrument_id="i1", action="accept")
    DriftDecision(instrument_id="i1", action="reject", rejected_txn_id="t1")
    DriftDecision(instrument_id="i1", action="dismiss", dismiss_reason="user choice")
    DriftDecision(instrument_id="i1", action="matched")
    with pytest.raises(ValidationError):
        DriftDecision(instrument_id="i1", action="not-a-real-action")


def test_drift_decision_has_no_delta_qty_field():
    from app.schemas.reconciliation import DriftDecision

    # Per plan: server derives delta_qty server-side; client never sends it.
    assert "delta_qty" not in DriftDecision.model_fields


def test_reconciliation_create_rejects_future_snapshot_date():
    from app.schemas.reconciliation import ReconciliationCreate

    with pytest.raises(ValidationError) as ei:
        ReconciliationCreate(
            account_id="a1",
            snapshot_date=date(2999, 1, 1),
            holdings=[],
            decisions=[],
        )
    assert "snapshot_date cannot be in the future" in str(ei.value)


def test_reconciliation_create_accepts_today_and_past():
    from app.schemas.reconciliation import ReconciliationCreate

    ReconciliationCreate(
        account_id="a1", snapshot_date=date.today(), holdings=[], decisions=[]
    )
    ReconciliationCreate(
        account_id="a1", snapshot_date=date(2020, 1, 1), holdings=[], decisions=[]
    )


def test_reconciliation_create_notes_length_capped():
    from app.schemas.reconciliation import ReconciliationCreate

    long_note = "x" * 2001
    with pytest.raises(ValidationError) as ei:
        ReconciliationCreate(
            account_id="a1",
            snapshot_date=date(2026, 5, 1),
            notes=long_note,
            holdings=[],
            decisions=[],
        )
    assert "2000 characters" in str(ei.value)


def test_reconciliation_preview_response_serializes_decimals_as_strings():
    from app.schemas.reconciliation import (
        ReconciliationPreviewResponse,
        ReconciliationPreviewRow,
    )

    row = ReconciliationPreviewRow(
        instrument_id="i1",
        instrument_symbol="BTC",
        instrument_name="Bitcoin",
        instrument_type="crypto",
        price_currency="USD",
        app_qty=Decimal("0.5"),
        app_value_eur=Decimal("12345.67"),
        has_price=True,
    )
    resp = ReconciliationPreviewResponse(
        account_id="a1",
        snapshot_date=date(2026, 5, 1),
        rows=[row],
        last_reconciled_date=date(2026, 4, 1),
    )
    j = json.loads(resp.model_dump_json())
    assert j["rows"][0]["app_qty"] == "0.5"
    assert j["rows"][0]["app_value_eur"] == "12345.67"


def test_reconciliation_response_round_trip():
    from app.schemas.reconciliation import ReconciliationResponse

    r = ReconciliationResponse(
        id="r1",
        account_id="a1",
        snapshot_date=date(2026, 5, 1),
        created_at=datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
        notes="ok",
        holdings_snapshot=[{"instrument_id": "i1", "snapshot_qty": "1.5"}],
    )
    j = json.loads(r.model_dump_json())
    assert j["holdings_snapshot"][0]["snapshot_qty"] == "1.5"


def test_transaction_create_accepts_reconciliation_id():
    from app.schemas.transaction import TransactionCreate

    txn = TransactionCreate(
        account_id="a1",
        instrument_id="i1",
        txn_type="buy",
        date=date(2026, 5, 1),
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        reconciliation_id="r1",
    )
    assert txn.reconciliation_id == "r1"


def test_transaction_create_reconciliation_id_optional():
    from app.schemas.transaction import TransactionCreate

    txn = TransactionCreate(
        account_id="a1",
        instrument_id="i1",
        txn_type="buy",
        date=date(2026, 5, 1),
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
    )
    assert txn.reconciliation_id is None


def test_transaction_create_adjustment_guard_preserved():
    """Critical: manual creation of adjustment txns must still be rejected.

    The guard was narrowed from {yield, adjustment} → {adjustment}.
    Yield is now user-creatable; adjustment remains reconciliation-engine-only.
    """
    from app.schemas.transaction import TransactionCreate

    with pytest.raises(ValidationError) as ei:
        TransactionCreate(
            account_id="a1",
            instrument_id="i1",
            txn_type="adjustment",
            date=date(2026, 5, 1),
            quantity=Decimal("1"),
            unit_price=None,
            price_currency=None,
        )
    # The new message says "reconciliation engine" instead of "created by the system"
    assert "reconciliation" in str(ei.value).lower()


def test_transaction_response_exposes_reconciliation_id():
    from app.schemas.transaction import TransactionResponse

    fields = TransactionResponse.model_fields
    assert "reconciliation_id" in fields


def test_account_response_exposes_last_reconciled_date():
    from app.schemas.account import AccountResponse

    fields = AccountResponse.model_fields
    assert "last_reconciled_date" in fields
    # Must be optional (nullable) for accounts that have never been reconciled.
    resp = AccountResponse(
        id="a1",
        name="Revolut",
        account_type="broker",
        is_banked=True,
        currency="EUR",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert resp.last_reconciled_date is None


def test_transaction_create_buy_requires_unit_price():
    """A buy without unit_price must be rejected at the API boundary
    so the chart never has to value an unpriced position."""
    from app.schemas.transaction import TransactionCreate
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError) as exc:
        TransactionCreate(
            account_id="a1",
            instrument_id="i1",
            txn_type="buy",
            date=date(2026, 5, 1),
            quantity=Decimal("10"),
            unit_price=None,  # The bug we are guarding against.
            price_currency=None,
        )
    msg = str(exc.value)
    assert "unit_price" in msg
    assert "price_currency" in msg


def test_transaction_create_spend_requires_unit_price():
    """Same rule for spends — without a price, realized P&L is unknowable."""
    from app.schemas.transaction import TransactionCreate
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError) as exc:
        TransactionCreate(
            account_id="a1",
            instrument_id="i1",
            txn_type="spend",
            date=date(2026, 5, 1),
            quantity=Decimal("0.5"),
            unit_price=None,
            price_currency=None,
        )
    assert "spend" in str(exc.value)


def test_transaction_create_buy_with_price_currency_only_still_rejected():
    """Both fields required together — neither alone is enough."""
    from app.schemas.transaction import TransactionCreate
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError) as exc:
        TransactionCreate(
            account_id="a1",
            instrument_id="i1",
            txn_type="buy",
            date=date(2026, 5, 1),
            quantity=Decimal("10"),
            unit_price=None,
            price_currency="EUR",
        )
    assert "unit_price" in str(exc.value)
