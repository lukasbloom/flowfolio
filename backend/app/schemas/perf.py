from datetime import date, datetime
from typing import Literal

from app.schemas._serializers import DecimalORMModel, DecimalStr


class PerfHoldingResponse(DecimalORMModel):

    account_id: str
    account_name: str
    instrument_id: str
    instrument_symbol: str
    instrument_name: str
    instrument_type: str
    # Per-row override for quantity-decimal rendering.
    # None → frontend uses DEFAULT_DECIMALS_BY_TYPE[instrument_type].
    display_decimals: int | None = None
    risk_level: str | None = None
    is_banked: bool
    quantity: DecimalStr
    avg_cost: DecimalStr | None
    current_price: DecimalStr | None
    current_price_fetched_at: datetime | None
    percent_return: DecimalStr | None
    realized_eur: DecimalStr | None = None
    twrr: DecimalStr | None
    twrr_annualized: bool
    twrr_period_days: int | None
    twrr_reason: str | None

    # Open/closed discriminator for the unified /api/perf?include_closed=1 surface.
    status: Literal["open", "closed"]

    # Closed-only fields (None for open rows). Mirrors ClosedPositionRow shape.
    last_close: DecimalStr | None = None
    last_close_date: date | None = None
    twrr_window_days: int | None = None
