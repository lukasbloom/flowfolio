from datetime import date, datetime
from datetime import (
    date as _date_t,  # alias used in `TransactionUpdate.date` to dodge a name-shadow that breaks Pydantic v2 re-evaluation
)
from decimal import Decimal
from typing import Optional

from pydantic import field_validator, model_validator

from app.core.enums import ACQUISITION_TXN_TYPES, TXN_SOURCE_SET, TXN_TYPE_SET
from app.schemas._serializers import DecimalModel, DecimalORMModel, DecimalStr, FxRateStr

# Derived as plain `set(...)` from app.core.enums so validator error messages
# keep rendering `{...}` (not `frozenset({...})`) — see app.core.enums note.
VALID_TXN_TYPES = set(TXN_TYPE_SET)
VALID_SOURCES = set(TXN_SOURCE_SET)


class TransactionCreate(DecimalModel):

    account_id: str
    instrument_id: str
    txn_type: str
    date: date
    # Use Decimal for all monetary inputs — never float
    quantity: DecimalStr
    unit_price: Optional[DecimalStr] = None
    price_currency: Optional[str] = None
    # fx_rate_to_eur: optional. When omitted for a USD txn the server fetches it
    # from ECB automatically; it defaults to 1.0 for EUR-denominated txns.
    fx_rate_to_eur: Optional[FxRateStr] = None
    fee_eur: DecimalStr = Decimal("0")
    notes: Optional[str] = None
    source: Optional[str] = "manual"
    trade_pair_id: Optional[str] = None
    reconciliation_id: Optional[str] = None

    @field_validator("txn_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in VALID_TXN_TYPES:
            raise ValueError(f"txn_type must be one of {VALID_TXN_TYPES}")
        # Adjustment rows are emitted by the reconciliation engine only, never
        # user-creatable. Yield rows may be created manually via the YieldForm.
        # Manual yield rows carry source="manual" (default); the daily APScheduler accrual
        # job sets source="accrual" and prepends "auto-accrual " to notes. EditTxnDialog
        # branches on the notes prefix to render the read-only
        # ActionBanner.
        if v == "adjustment":
            raise ValueError(
                "adjustment transactions are created by the reconciliation engine, not manually."
            )
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: Optional[str]) -> Optional[str]:
        # None → default to "manual" (the YieldForm and most callers omit `source`).
        # The daily accrual job sets source="accrual" explicitly. An e2e
        # fixture posts source="accrual" with a notes prefix to mimic an accrual row.
        if v is None:
            return "manual"
        if v not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _validate_sell_and_spend(self) -> "TransactionCreate":
        # Sell must go through /api/trades endpoint (linked sell+buy)
        if self.txn_type == "sell":
            raise ValueError(
                "Use POST /api/trades to record a sell — sells must be paired with what you received."
            )
        # Spend transactions must not have a trade_pair_id
        if self.txn_type == "spend" and self.trade_pair_id is not None:
            raise ValueError("spend transactions must not have a trade_pair_id.")
        return self

    @model_validator(mode="after")
    def _validate_priced_txn_has_price(self) -> "TransactionCreate":
        """Buys and spends must carry unit_price + price_currency.

        Yield and adjustment txns are system-generated and intentionally
        allowed to omit a price — they're caught earlier by the txn_type
        validator anyway. For user-created buy/spend rows, omitting the
        price means the chart can't value the position between the trade
        date and the first market quote, silently rendering 0. Catch the
        problem at create-time instead of at chart-time.
        """
        if self.txn_type in ACQUISITION_TXN_TYPES:
            missing = [
                field
                for field, value in (
                    ("unit_price", self.unit_price),
                    ("price_currency", self.price_currency),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"{self.txn_type} transactions require {', '.join(missing)} — "
                    "without it the chart can't value this position."
                )
        return self

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, v: Decimal) -> Decimal:
        if v <= Decimal("0"):
            raise ValueError("quantity must be positive (sign is inferred from txn_type)")
        return v

    @model_validator(mode="after")
    def validate_currency_supported(self) -> "TransactionCreate":
        """Only EUR/USD txn currencies. fx_rate_to_eur may be omitted,
        the POST /api/transactions handler auto-fetches from Frankfurter when
        price_currency='USD' and fx_rate_to_eur is None."""
        if (
            self.price_currency is not None
            and self.price_currency not in {"EUR", "USD"}
        ):
            raise ValueError(
                f"price_currency must be 'EUR' or 'USD', got {self.price_currency!r}"
            )
        return self


class TransactionUpdate(DecimalModel):

    # txn_type is intentionally NOT mutable via PUT. Sells
    # only enter the system as part of an atomic linked trade through
    # POST /api/trades. Allowing PUT to change a buy into a sell would bypass
    # the trade_pair_id requirement and trip ck_txn_trade_pair_required at
    # COMMIT time (a 500 instead of a clean 422).
    date: Optional[_date_t] = None
    quantity: Optional[DecimalStr] = None
    unit_price: Optional[DecimalStr] = None
    price_currency: Optional[str] = None
    fx_rate_to_eur: Optional[FxRateStr] = None
    fee_eur: Optional[DecimalStr] = None
    notes: Optional[str] = None


class LotAllocResponse(DecimalORMModel):

    id: str
    sell_txn_id: str
    buy_txn_id: str
    quantity: DecimalStr
    realized_gain_eur: Optional[DecimalStr]
    created_at: datetime


class TransactionResponse(DecimalORMModel):

    id: str
    account_id: str
    account_name: Optional[str] = None
    instrument_id: str
    instrument_symbol: Optional[str] = None
    # Hydrated alongside instrument_symbol so the txn
    # ledger can render quantities with the correct per-type precision
    # without a second fetch.
    instrument_type: Optional[str] = None
    display_decimals: Optional[int] = None
    txn_type: str
    date: date
    quantity: DecimalStr
    unit_price: Optional[DecimalStr]
    price_currency: Optional[str]
    fx_rate_to_eur: Optional[FxRateStr]
    cost_basis_eur: Optional[DecimalStr]
    fee_eur: DecimalStr
    notes: Optional[str]
    source: str
    apy_config_id: Optional[str]
    deleted_at: Optional[datetime] = None
    trade_pair_id: Optional[str] = None
    reconciliation_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    lot_allocs: list[LotAllocResponse] = []
    lot_alloc_count: int = 0  # count of lot_alloc rows touching this txn
