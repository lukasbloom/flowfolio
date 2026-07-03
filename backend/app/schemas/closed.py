from datetime import date

from app.schemas._serializers import DecimalORMModel, DecimalStr


class ClosedPositionRow(DecimalORMModel):

    account_id: str
    account_name: str
    instrument_id: str
    instrument_symbol: str
    instrument_name: str | None = None
    # Closed positions previously returned no
    # instrument_type, so the frontend table had to fall back to the
    # legacy 8-decimal max. Add it as a required field (the join to
    # Instrument is already eagerly hydrated in services/closed.py) plus
    # the per-row override.
    instrument_type: str
    display_decimals: int | None = None
    quantity: DecimalStr
    avg_cost: DecimalStr | None
    last_close: DecimalStr | None
    last_close_date: date | None
    percent_return: DecimalStr | None
    realized_eur: DecimalStr | None
    twrr: DecimalStr | None
    twrr_window_days: int
    twrr_annualized: bool
