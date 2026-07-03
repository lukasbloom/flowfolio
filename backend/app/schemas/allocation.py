from app.schemas._serializers import DecimalORMModel, DecimalStr


class AllocationSlice(DecimalORMModel):

    label: str
    value: DecimalStr
    percent: DecimalStr


class AllocationResponse(DecimalORMModel):

    dimension: str
    currency: str
    total: DecimalStr
    slices: list[AllocationSlice]
