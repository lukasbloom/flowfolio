from app.schemas._serializers import DecimalORMModel, DecimalStr


class RealizedTotals(DecimalORMModel):

    currency: str
    lifetime: DecimalStr
    this_year: DecimalStr


class RealizedPerHolding(DecimalORMModel):

    instrument_id: str
    instrument_symbol: str
    realized_eur: DecimalStr


class RealizedResponse(DecimalORMModel):

    totals: RealizedTotals
    per_holding: list[RealizedPerHolding]
