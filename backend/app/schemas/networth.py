from datetime import date

from pydantic import Field

from app.schemas._serializers import DecimalORMModel, DecimalStr


class NetWorthPointResponse(DecimalORMModel):

    date: date
    value: DecimalStr


class NetWorthMarkerResponse(DecimalORMModel):

    date: date
    type: str
    instrument_id: str | None
    instrument_symbol: str | None
    # Optional per-marker context so the chart tooltip
    # can format quantity with the correct precision. Aggregate markers
    # (no single instrument) leave both fields None and the formatter
    # falls back to the 8-decimal legacy default.
    instrument_type: str | None = None
    display_decimals: int | None = None
    quantity: DecimalStr | None
    # Marker amount is rendered in the chart's display currency
    # (EUR or USD), not always EUR. Field renamed accordingly.
    value: DecimalStr
    count: int


class NetWorthResponse(DecimalORMModel):

    points: list[NetWorthPointResponse]
    markers: list[NetWorthMarkerResponse]
    aggregation: str
    warnings: list[str]
    # Cost-basis series (same {date, value} shape as
    # ``points``) layered onto /api/networth when the caller passes
    # ``include_cost_basis=true``. Defaults to ``[]`` so any existing caller
    # (e.g. the instrument-detail page) that omits the flag sees no behavior
    # change. Field is optional in OpenAPI for the same reason.
    cost_basis_series: list[NetWorthPointResponse] = Field(default_factory=list)
