"""Reconciliation Pydantic schemas.

Decimal serialization via the DecimalStr annotated type per project convention.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import field_validator, model_validator

from app.schemas._serializers import DecimalModel, DecimalORMModel, DecimalStr, FxRateStr


class HoldingSnapshotEntry(DecimalModel):
    """One row of the user-typed snapshot — what the broker reports."""

    instrument_id: str
    snapshot_qty: DecimalStr


class DriftDecision(DecimalModel):
    """One row's decision at save time — accept / reject / dismiss / matched.

    Note: there is intentionally NO `delta_qty` field on this schema. The
    reconciliation service derives delta_qty server-side using Python Decimal
    from `payload.holdings[i].snapshot_qty - app_qty` (where app_qty is
    recomputed at save time from the recorded transactions). This keeps all
    money/qty arithmetic on the Python side per CLAUDE.md "NEVER float for
    money".
    """

    instrument_id: str
    action: Literal["accept", "reject", "dismiss", "matched"]
    dismiss_reason: Optional[str] = None
    rejected_txn_id: Optional[str] = None


class RejectedTxnPayload(DecimalModel):
    """Reject-txn payload staged by the drawer — quantity is intentionally absent.

    The reconciliation service derives the quantity server-side from
    abs(holdings[i].snapshot_qty − app_qty) using Python Decimal to satisfy the
    CLAUDE.md 'NEVER float for money' invariant and to mirror the accept path.

    txn_type is validated to the three types allowed through this path
    (accept is handled as adjustment; yields cannot be
    manually created from reconciliation; sell is a documented stub).
    """

    instrument_id: str
    txn_type: Literal["buy", "sell", "spend"]
    txn_date: Optional[date] = None   # defaults to snapshot_date if None
    unit_price: DecimalStr
    price_currency: Literal["EUR", "USD"] = "EUR"
    fx_rate_to_eur: Optional[FxRateStr] = None
    fee_eur: DecimalStr = Decimal("0")
    notes: Optional[str] = None

    @field_validator("unit_price")
    @classmethod
    def validate_unit_price(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("unit_price must be positive")
        return v

    @field_validator("fee_eur")
    @classmethod
    def validate_fee(cls, v: Decimal) -> Decimal:
        if v < Decimal("0"):
            raise ValueError("fee_eur must be non-negative")
        return v


class ReconciliationCreate(DecimalModel):
    """Request body for POST /api/reconciliation."""

    account_id: str
    snapshot_date: date
    notes: Optional[str] = None
    holdings: list[HoldingSnapshotEntry]
    decisions: list[DriftDecision]
    # Reject-txn payloads written server-side inside the same
    # DB transaction as the event + adjustments. quantity is intentionally
    # omitted from RejectedTxnPayload — the service derives it from
    # abs(snapshot_qty − app_qty) using Python Decimal.
    rejected_txns: list[RejectedTxnPayload] = []

    @field_validator("snapshot_date")
    @classmethod
    def validate_not_future(cls, v: date) -> date:
        # Compare in the user's local calendar, not UTC — see clock.today_local.
        from app.core import clock
        if v > clock.today_local():
            raise ValueError("snapshot_date cannot be in the future")
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 2000:
            raise ValueError("notes must not exceed 2000 characters")
        return v

    @field_validator("rejected_txns")
    @classmethod
    def validate_rejected_txns_limit(
        cls, v: list["RejectedTxnPayload"]
    ) -> list["RejectedTxnPayload"]:
        # DOS guard for a single-user app, the attacker is a typo.
        if len(v) > 200:
            raise ValueError("rejected_txns must not exceed 200 entries")
        return v

    @model_validator(mode="after")
    def reject_txns_must_reference_holdings(self) -> "ReconciliationCreate":
        """Every rejected_txn must reference an instrument the user
        actually included in the holdings array.

        Without this guard, the server-derived delta lookup falls back to
        ZERO when the instrument is missing from holdings, which then
        computes delta_abs = abs(0 - app_qty) = app_qty — silently writing
        a destructive reject row for the user's full historical position.
        The frontend never produces this state; the validator hardens the
        contract at the API boundary.
        """
        holding_ids = {h.instrument_id for h in self.holdings}
        for r in self.rejected_txns:
            if r.instrument_id not in holding_ids:
                raise ValueError(
                    f"rejected_txns references instrument_id "
                    f"{r.instrument_id!r} not present in holdings array"
                )
        return self


class ReconciliationPreviewRow(DecimalModel):
    """One row of GET /api/reconciliation/preview output."""

    instrument_id: str
    instrument_symbol: str
    instrument_name: str
    # instrument_type is required (preview always joins
    # Instrument); display_decimals is the optional per-row override.
    instrument_type: str
    display_decimals: int | None = None
    price_currency: Optional[str] = None
    app_qty: DecimalStr
    app_value_eur: Optional[DecimalStr] = None
    has_price: bool


class ReconciliationPreviewResponse(DecimalModel):
    """GET /api/reconciliation/preview response."""

    account_id: str
    snapshot_date: date
    rows: list[ReconciliationPreviewRow]
    last_reconciled_date: Optional[date] = None


class ReconciliationResponse(DecimalORMModel):
    """POST /api/reconciliation response (the saved event)."""

    id: str
    account_id: str
    snapshot_date: date
    created_at: datetime
    notes: Optional[str] = None
    holdings_snapshot: list[dict[str, Any]]
    # IDs of reject txns written server-side as part of this event.
    rejected_txn_ids: list[str] = []
