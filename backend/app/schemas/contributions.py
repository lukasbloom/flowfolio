from datetime import date

from app.schemas._serializers import DecimalORMModel, DecimalStr


class SeriesPoint(DecimalORMModel):

    date: date
    value: DecimalStr


class ContributionBucket(DecimalORMModel):

    period_label: str
    period_start: date
    deposits: DecimalStr
    spendings: DecimalStr
    realized_gains: DecimalStr
    yield_amount: DecimalStr


class ContributionsResponse(DecimalORMModel):

    currency: str
    period: str
    cost_basis_series: list[SeriesPoint]
    portfolio_value_series: list[SeriesPoint]
    buckets: list[ContributionBucket]
