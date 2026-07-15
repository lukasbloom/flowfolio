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

from decimal import Decimal

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

# --- FIFO lot semantics ---------------------------------------------------------
# An adjustment plays either role depending on its sign: a negative
# reconciliation trim consumes open lots like a sell, a positive top-up is a
# buy-lot equivalent. The sets below are the coarse SQL-side type filters; the
# sign refinement happens in Python via the predicates, because quantity is
# TEXT-backed (DecimalText) and a SQL sign comparison would type-juggle the
# text against a number.
LOT_CONSUMING_TXN_TYPES: frozenset[str] = DISPOSAL_TXN_TYPES | {"adjustment"}
LOT_SOURCE_TXN_TYPES: frozenset[str] = frozenset({"buy", "adjustment"})
# Every txn type that can change lot attribution on a pair (yield never does).
LOT_AFFECTING_TXN_TYPES: frozenset[str] = LOT_CONSUMING_TXN_TYPES | LOT_SOURCE_TXN_TYPES

_ZERO = Decimal("0")


def is_lot_consuming(txn_type: str, quantity: Decimal) -> bool:
    """True for rows that consume open lots: sell, spend, or a negative adjustment."""
    return txn_type in DISPOSAL_TXN_TYPES or (
        txn_type == "adjustment" and quantity < _ZERO
    )


def is_lot_source(txn_type: str, quantity: Decimal) -> bool:
    """True for rows that open lots: buy, or a positive adjustment."""
    return txn_type in LOT_SOURCE_TXN_TYPES and quantity > _ZERO


def signed_quantity(txn_type: str, quantity: Decimal) -> Decimal:
    """Stored-sign convention. Disposals (sell/spend) store negative. Adjustments
    keep their caller-supplied sign (trims negative, top-ups positive). Everything
    else stores the positive magnitude."""
    if txn_type in DISPOSAL_TXN_TYPES:
        return -abs(quantity)
    if txn_type == "adjustment":
        return quantity
    return abs(quantity)
