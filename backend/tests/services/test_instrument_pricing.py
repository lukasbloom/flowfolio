"""Unit tests for `app.services.instrument_pricing`.

Covers the canonical (instrument_type, price_source) mapping and the
cross-field rejection rules wired into InstrumentCreate.
"""
from __future__ import annotations

import pytest

from app.services.instrument_pricing import (
    AUTOMATIC_SOURCE_BY_TYPE,
    allowed_sources_for,
    resolve_price_source,
)


@pytest.mark.parametrize(
    ("instrument_type", "expected_source"),
    [
        ("stock", "finnhub"),
        ("etf", "finnhub"),
        ("fund", "ft"),
        ("crypto", "coingecko"),
        ("stablecoin", "coingecko"),
        ("cash", "na"),
    ],
)
def test_resolve_automatic_per_type(instrument_type: str, expected_source: str) -> None:
    assert resolve_price_source(instrument_type, "automatic") == expected_source


@pytest.mark.parametrize(
    "instrument_type",
    ["stock", "etf", "fund", "crypto", "stablecoin", "metal"],
)
def test_resolve_manual_per_type(instrument_type: str) -> None:
    assert resolve_price_source(instrument_type, "manual") == "manual"


def test_cash_manual_rejected() -> None:
    with pytest.raises(ValueError, match="cash has no manual mode"):
        resolve_price_source("cash", "manual")


def test_metal_automatic_rejected() -> None:
    with pytest.raises(ValueError, match="no automatic price source"):
        resolve_price_source("metal", "automatic")


def test_unknown_type_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown instrument_type"):
        resolve_price_source("nonsense", "automatic")


def test_unknown_mode_rejected() -> None:
    with pytest.raises(ValueError, match="mode must be"):
        # Cast away the Literal — runtime guard exists for exactly this case.
        resolve_price_source("stock", "random")  # type: ignore[arg-type]


def test_automatic_source_by_type_covers_all_known_types() -> None:
    """Guard rail: keep the map in sync with the model's INSTRUMENT_TYPES tuple."""
    from app.models.instrument import INSTRUMENT_TYPES

    assert set(AUTOMATIC_SOURCE_BY_TYPE.keys()) == set(INSTRUMENT_TYPES)


def test_allowed_sources_for_each_type() -> None:
    assert allowed_sources_for("stock") == {"finnhub", "manual"}
    # etf + metal additionally allow ft (European ETFs / gold ETC via FT tear-sheets)
    assert allowed_sources_for("etf") == {"finnhub", "manual", "ft"}
    assert allowed_sources_for("fund") == {"ft", "manual"}
    assert allowed_sources_for("crypto") == {"coingecko", "manual"}
    assert allowed_sources_for("stablecoin") == {"coingecko", "manual"}
    assert allowed_sources_for("cash") == {"na"}
    assert allowed_sources_for("metal") == {"manual", "ft"}
