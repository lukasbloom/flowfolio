"""Single source of truth for the (instrument_type → automatic price source) map.

Both the create-instrument schema validator and the front-end form derive
their behavior from this table:

    | instrument_type | automatic source | manual mode? |
    | --------------- | ---------------- | ------------ |
    | stock           | finnhub          | yes          |
    | etf             | finnhub          | yes          |
    | fund            | ft               | yes          |
    | crypto          | coingecko        | yes          |
    | stablecoin      | coingecko        | yes          |
    | cash            | na (forced)      | NO           |
    | metal           | (no automatic)   | yes (only)   |

Keep this in sync with `frontend/lib/instrument-eligibility.ts` —
`AUTOMATIC_SOURCE_BY_TYPE` is mirrored verbatim there so the create form
can derive the wire-format `price_source` string from a (type, mode) pair
without round-tripping the server.
"""
from __future__ import annotations

from typing import Literal

# Per-type automatic source. None marks a type with no automatic mode at all
# (currently only `metal`, which only supports manual price entry).
AUTOMATIC_SOURCE_BY_TYPE: dict[str, str | None] = {
    "stock": "finnhub",
    "etf": "finnhub",
    "fund": "ft",
    "crypto": "coingecko",
    "stablecoin": "coingecko",
    "cash": "na",        # cash has no manual mode — price_source is forced to "na"
    "metal": None,       # metal has no automatic mode
}


# Sources permitted in addition to the automatic + manual defaults. European
# ETFs and exchange-traded commodities (the gold ETC, modelled as `metal`) can be
# priced from FT.com tear-sheets, so `ft` is an allowed explicit choice for them
# even though it is not their default automatic source.
EXTRA_SOURCES_BY_TYPE: dict[str, set[str]] = {
    "etf": {"ft"},
    "metal": {"ft"},
}


def allowed_sources_for(instrument_type: str) -> set[str]:
    """Set of `price_source` values accepted for a given `instrument_type`.

    Derived from AUTOMATIC_SOURCE_BY_TYPE plus the manual rule, plus any
    EXTRA_SOURCES_BY_TYPE:
      - cash      -> only {"na"}
      - metal     -> {"manual", "ft"}
      - etf       -> {"finnhub", "manual", "ft"}
      - all others -> {automatic source, "manual"}
    """
    auto = AUTOMATIC_SOURCE_BY_TYPE.get(instrument_type)
    if instrument_type == "cash":
        return {"na"}
    extra = EXTRA_SOURCES_BY_TYPE.get(instrument_type, set())
    if instrument_type == "metal":
        return {"manual"} | extra
    if auto is None:
        return {"manual"} | extra
    return {auto, "manual"} | extra


def resolve_price_source(
    instrument_type: str,
    mode: Literal["automatic", "manual"],
) -> str:
    """Resolve a (type, mode) pair into the wire-format `price_source` enum.

    Raises ValueError for any combination disallowed by the canonical table.
    """
    if instrument_type not in AUTOMATIC_SOURCE_BY_TYPE:
        raise ValueError(f"Unknown instrument_type: {instrument_type!r}")
    auto = AUTOMATIC_SOURCE_BY_TYPE[instrument_type]
    if mode == "automatic":
        if auto is None:
            raise ValueError(
                f"{instrument_type!r} has no automatic price source — use mode='manual'"
            )
        return auto
    if mode == "manual":
        if instrument_type == "cash":
            raise ValueError(
                "cash has no manual mode — price_source is forced to 'na'"
            )
        return "manual"
    raise ValueError(f"mode must be 'automatic' or 'manual', got {mode!r}")
