from datetime import date
from typing import Optional

from pydantic import model_validator

from app.schemas._serializers import DecimalORMModel, DecimalStr, FxRateStr


class TradeLeg(DecimalORMModel):
    account_id: str
    instrument_id: str
    quantity: DecimalStr        # always positive at the API; service signs the sell side negative
    unit_price: DecimalStr
    price_currency: str         # 'EUR' or 'USD'
    fx_rate_to_eur: Optional[FxRateStr] = None
    fee_eur: Optional[DecimalStr] = None


class TradeCreate(DecimalORMModel):
    sold: TradeLeg
    received: TradeLeg
    date: date                   # shared by both legs
    notes: Optional[str] = None  # applies to both rows

    @model_validator(mode="after")
    def _diff_instruments(self) -> "TradeCreate":
        if self.sold.instrument_id == self.received.instrument_id:
            raise ValueError("Sold and received instruments must differ.")
        return self


class TradeResponse(DecimalORMModel):
    trade_pair_id: str
    sold_txn_id: str
    received_txn_id: str
