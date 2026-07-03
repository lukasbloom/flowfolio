"""Audit trail writer for transaction edits and soft-deletes.

Caller-commits contract (mirrors backend/app/services/fifo.py):
    Must be called INSIDE an open DB transaction (caller is responsible for
    commit). The service stages new rows via session.add(...) but never calls
    session.commit() / session.rollback().
"""
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.models.txn_audit import TxnAudit

# Fields whose changes are recorded in the audit trail. Listed verbatim so
# adding a new column to Transaction in a later phase doesn't silently start
# writing audit rows for it.
AUDITED_FIELDS = (
    "txn_type",
    "account_id",
    "instrument_id",
    "date",
    "quantity",
    "unit_price",
    "price_currency",
    "fx_rate_to_eur",
    "fee_eur",
    "notes",
    "trade_pair_id",
)


def _stringify(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):  # date
        return value.isoformat()
    return str(value)


def compute_field_diff(
    before: Transaction, after_payload: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Return {field: {old: stringified, new: stringified}} for fields that actually changed."""
    diff: dict[str, dict[str, Any]] = {}
    for field in AUDITED_FIELDS:
        if field not in after_payload:
            continue
        before_val = getattr(before, field, None)
        after_val = after_payload[field]
        if _stringify(before_val) != _stringify(after_val):
            diff[field] = {"old": _stringify(before_val), "new": _stringify(after_val)}
    return diff


async def write_audit_event(
    session: AsyncSession,
    txn_id: str,
    event_type: str,
    changed_fields: dict[str, Any],
) -> TxnAudit:
    if event_type not in ("edit", "delete"):
        raise ValueError(f"event_type must be 'edit' or 'delete', got {event_type!r}")
    audit = TxnAudit(
        transaction_id=txn_id,
        event_type=event_type,
        changed_fields=changed_fields,
    )
    session.add(audit)
    return audit
