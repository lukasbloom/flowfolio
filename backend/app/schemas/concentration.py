from pydantic import BaseModel, ConfigDict

from app.schemas._serializers import DecimalORMModel, DecimalStr


class ConcentrationOffender(DecimalORMModel):

    instrument_id: str
    instrument_symbol: str
    percent: DecimalStr


class ConcentrationResponse(DecimalORMModel):

    threshold: DecimalStr
    offenders: list[ConcentrationOffender]


class MutedHolding(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    instrument_id: str
    instrument_symbol: str
    instrument_name: str | None = None
