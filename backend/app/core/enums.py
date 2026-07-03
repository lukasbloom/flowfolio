"""Canonical, dependency-free enum membership sets.

This module must NOT import SQLAlchemy (or any model) — it is the shared source
of truth that BOTH the ORM models and the Pydantic schemas import, so it has to
sit below both layers in the dependency graph.

Membership was verified identical to the former per-layer copies:
- INSTRUMENT_TYPES: models.instrument.INSTRUMENT_TYPES tuple == schemas VALID_TYPES set
- PRICE_SOURCES: models.instrument.PRICE_SOURCES tuple == schemas VALID_SOURCES set
- RISK_LEVELS: models.instrument.RISK_LEVELS tuple == schemas VALID_RISK_LEVELS set
- TXN_TYPES: models.transaction.TXN_TYPES tuple == schemas VALID_TXN_TYPES set
- TXN_SOURCES: models.transaction.TXN_SOURCES tuple == schemas (transaction) VALID_SOURCES set

NOTE on string rendering: the schema validators render their allowed-set in the
error message (e.g. f"instrument_type must be one of {VALID_TYPES}"). A plain
`set` reprs as `{...}` whereas a `frozenset` reprs as `frozenset({...})`. To keep
those error strings byte-identical, the schema layer derives a plain `set()` from
these frozensets rather than using them directly in the message. The ordered
tuples preserve the historical model-layer declaration order (documentary only —
nothing iterates them for behavior).
"""
from __future__ import annotations

# --- Instrument enums ---------------------------------------------------------
INSTRUMENT_TYPES: tuple[str, ...] = (
    "stock",
    "etf",
    "fund",
    "crypto",
    "stablecoin",
    "cash",
    "metal",
)
PRICE_SOURCES: tuple[str, ...] = ("finnhub", "coingecko", "ft", "manual", "na")
RISK_LEVELS: tuple[str, ...] = ("High", "Medium", "Low", "Liquid")

INSTRUMENT_TYPE_SET: frozenset[str] = frozenset(INSTRUMENT_TYPES)
PRICE_SOURCE_SET: frozenset[str] = frozenset(PRICE_SOURCES)
RISK_LEVEL_SET: frozenset[str] = frozenset(RISK_LEVELS)

# --- Transaction enums --------------------------------------------------------
TXN_TYPES: tuple[str, ...] = ("buy", "sell", "spend", "yield", "adjustment")
TXN_SOURCES: tuple[str, ...] = ("manual", "accrual", "adjustment")

TXN_TYPE_SET: frozenset[str] = frozenset(TXN_TYPES)
TXN_SOURCE_SET: frozenset[str] = frozenset(TXN_SOURCES)

# --- Derived txn-type subsets -------------------------------------------------
# Disposal events reduce a position: sells and spends. Replaces the magic
# ("sell", "spend") / {"sell", "spend"} literals scattered across the
# transactions router and reconciliation service.
DISPOSAL_TXN_TYPES: frozenset[str] = frozenset({"sell", "spend"})
# Acquisition-side events that the priced-txn / cost-segment paths treat alike:
# buys and spends. Replaces the {"buy", "spend"} literals.
ACQUISITION_TXN_TYPES: frozenset[str] = frozenset({"buy", "spend"})
