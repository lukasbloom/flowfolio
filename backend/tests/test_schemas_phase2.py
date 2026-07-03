"""
Pydantic schema tests.

Verifies:
- Decimal-as-string JSON serialization round-trip on every Response model.
- field_validator rejects invalid sources / currencies / non-positive amounts.
- ApyConfigCreate enforces the fraction-not-percentage convention (apy_rate ≤ 1).
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.apy_config import ApyConfigCreate, ApyConfigResponse, ApyConfigUpdate
from app.schemas.fx_rate import FxRateCreate, FxRateResponse
from app.schemas.price_quote import (
    ManualNavOverride,
    PriceQuoteCreate,
    PriceQuoteResponse,
)

# ---------------------------------------------------------------------------
# Decimal-as-string JSON round-trip
# ---------------------------------------------------------------------------


def test_price_quote_response_serializes_price_as_string():
    resp = PriceQuoteResponse(
        id="00000000-0000-0000-0000-000000000001",
        instrument_id="00000000-0000-0000-0000-000000000002",
        date=date(2026, 4, 30),
        price=Decimal("180.50000000"),
        currency="USD",
        source="finnhub",
        fetched_at=datetime(2026, 4, 30, 22, 0, tzinfo=timezone.utc),
    )
    payload = json.loads(resp.model_dump_json())
    assert isinstance(payload["price"], str)
    assert payload["price"] == "180.50000000"


def test_fx_rate_response_serializes_rate_as_string():
    # FxRateStr quantizes to 4dp with banker's rounding + strips trailing
    # zeros. Decimal("1.0712340000") → quantize to 4dp → "1.0712" (trailing zeros stripped).
    # The old expectation "1.0712340000" is superseded by the new FxRateStr wire contract.
    resp = FxRateResponse(
        id="00000000-0000-0000-0000-000000000001",
        date=date(2026, 4, 30),
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.0712340000"),
        source="frankfurter",
        fetched_at=datetime(2026, 4, 30, 16, 0, tzinfo=timezone.utc),
    )
    payload = json.loads(resp.model_dump_json())
    assert isinstance(payload["rate"], str)
    assert payload["rate"] == "1.0712"


def test_apy_config_response_serializes_apy_rate_as_string():
    resp = ApyConfigResponse(
        id="00000000-0000-0000-0000-000000000001",
        account_id="00000000-0000-0000-0000-000000000002",
        instrument_id="00000000-0000-0000-0000-000000000003",
        apy_rate=Decimal("0.023700"),
        effective_from=date(2026, 1, 1),
        effective_to=None,
        compounding="daily_simple",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    payload = json.loads(resp.model_dump_json())
    assert isinstance(payload["apy_rate"], str)
    assert payload["apy_rate"] == "0.023700"


# ---------------------------------------------------------------------------
# from_attributes=True — Response models construct from ORM-shaped objects
# ---------------------------------------------------------------------------


class _Stub:
    """Stand-in for an ORM row (Pydantic from_attributes=True consumes it)."""

    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_price_quote_response_from_attributes():
    row = _Stub(
        id="abc",
        instrument_id="def",
        date=date(2026, 4, 30),
        price=Decimal("180.50"),
        currency="USD",
        source="finnhub",
        fetched_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
    )
    resp = PriceQuoteResponse.model_validate(row)
    payload = json.loads(resp.model_dump_json())
    assert isinstance(payload["price"], str)


def test_fx_rate_response_from_attributes():
    row = _Stub(
        id="abc",
        date=date(2026, 4, 30),
        base_currency="EUR",
        quote_currency="USD",
        rate=Decimal("1.0712340000"),
        source="frankfurter",
        fetched_at=datetime(2026, 4, 30, tzinfo=timezone.utc),
    )
    resp = FxRateResponse.model_validate(row)
    payload = json.loads(resp.model_dump_json())
    assert isinstance(payload["rate"], str)


# ---------------------------------------------------------------------------
# Validators reject invalid input
# ---------------------------------------------------------------------------


def test_price_quote_create_rejects_invalid_source():
    with pytest.raises(ValidationError):
        PriceQuoteCreate(
            instrument_id="abc",
            date=date(2026, 4, 30),
            price=Decimal("100"),
            currency="USD",
            source="yahoo",
        )


def test_price_quote_create_rejects_invalid_currency():
    with pytest.raises(ValidationError):
        PriceQuoteCreate(
            instrument_id="abc",
            date=date(2026, 4, 30),
            price=Decimal("100"),
            currency="GBP",
            source="finnhub",
        )


def test_price_quote_create_rejects_non_positive_price():
    with pytest.raises(ValidationError):
        PriceQuoteCreate(
            instrument_id="abc",
            date=date(2026, 4, 30),
            price=Decimal("0"),
            currency="USD",
            source="finnhub",
        )


def test_price_quote_create_accepts_all_valid_sources():
    for src in ("finnhub", "alpha_vantage", "coingecko", "ft", "manual"):
        PriceQuoteCreate(
            instrument_id="abc",
            date=date(2026, 4, 30),
            price=Decimal("100"),
            currency="USD",
            source=src,
        )


def test_fx_rate_create_rejects_invalid_currency():
    with pytest.raises(ValidationError):
        FxRateCreate(
            date=date(2026, 4, 30),
            base_currency="GBP",
            quote_currency="USD",
            rate=Decimal("1.07"),
            source="frankfurter",
        )


def test_fx_rate_create_rejects_non_positive_rate():
    with pytest.raises(ValidationError):
        FxRateCreate(
            date=date(2026, 4, 30),
            base_currency="EUR",
            quote_currency="USD",
            rate=Decimal("0"),
            source="frankfurter",
        )


def test_fx_rate_create_rejects_invalid_source():
    with pytest.raises(ValidationError):
        FxRateCreate(
            date=date(2026, 4, 30),
            base_currency="EUR",
            quote_currency="USD",
            rate=Decimal("1.07"),
            source="ecb_direct",
        )


def test_apy_config_create_rejects_zero_rate():
    with pytest.raises(ValidationError):
        ApyConfigCreate(
            account_id="abc",
            instrument_id="def",
            apy_rate=Decimal("0"),
            effective_from=date(2026, 1, 1),
        )


def test_apy_config_create_rejects_negative_rate():
    with pytest.raises(ValidationError):
        ApyConfigCreate(
            account_id="abc",
            instrument_id="def",
            apy_rate=Decimal("-0.01"),
            effective_from=date(2026, 1, 1),
        )


def test_apy_config_create_rejects_percentage_value():
    """0.0237 is the expected fraction. Receiving 2.37 (percentage) is a bug
    upstream — schema must reject values > 1."""
    with pytest.raises(ValidationError):
        ApyConfigCreate(
            account_id="abc",
            instrument_id="def",
            apy_rate=Decimal("2.37"),
            effective_from=date(2026, 1, 1),
        )


def test_apy_config_update_accepts_none_rate():
    # Partial update: omitting apy_rate should not trigger the validator.
    upd = ApyConfigUpdate(effective_to=date(2026, 6, 30))
    assert upd.apy_rate is None
    assert upd.effective_to == date(2026, 6, 30)


def test_apy_config_update_validates_when_rate_provided():
    with pytest.raises(ValidationError):
        ApyConfigUpdate(apy_rate=Decimal("-1"))


def test_manual_nav_override_round_trip_decimal_as_string():
    body = ManualNavOverride(
        instrument_id="abc",
        date=date(2026, 4, 30),
        price=Decimal("13.000000"),
        currency="EUR",
        note="FT scrape failed; entered manually from MyInvestor app.",
    )
    payload = json.loads(body.model_dump_json())
    assert isinstance(payload["price"], str)
    assert payload["price"] == "13.000000"


def test_manual_nav_override_rejects_invalid_currency():
    with pytest.raises(ValidationError):
        ManualNavOverride(
            instrument_id="abc",
            date=date(2026, 4, 30),
            price=Decimal("13"),
            currency="GBP",
        )
