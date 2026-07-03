"""Centralized non-enum constants shared across services, schemas, and routers.

Previously these were redefined module-by-module (and a couple had drifted —
see UNIT_SCALE below). Defining them once here removes the copy-paste while
preserving every distinct *value*; intention-revealing names are kept where two
domains genuinely use different scales.
"""
from __future__ import annotations

from decimal import Decimal

# --- Decimal sentinels --------------------------------------------------------
ZERO = Decimal("0")
ONE = Decimal("1")

# --- Quantization scales ------------------------------------------------------
# Ratios are quantised to a fixed 16-decimal precision and NOT
# normalised — Decimal.normalize() collapses small values like 0.0001 to
# scientific notation ("1E-4"), which strict JSON numeric parsers reject.
RATIO_SCALE = Decimal("0.0000000000000001")  # 1e-16

# IMPORTANT: two different "unit scales" exist on purpose — they are NOT the
# same number and must never be conflated:
#   - PERF_UNIT_SCALE (1e-18): perf.py avg_cost / unit-price quantization.
#   - VALUE_SCALE (1e-8): networth & contributions monetary-value quantization.
# perf.py keeps its own module-level UNIT_SCALE alias for readability; networth
# and contributions use VALUE_SCALE. Centralized here so the literals live in
# one place, but the distinct names preserve the distinct quantization intent.
PERF_UNIT_SCALE = Decimal("0.000000000000000001")  # 1e-18
VALUE_SCALE = Decimal("0.00000001")  # 1e-8

# --- Currency -----------------------------------------------------------------
# Only EUR/USD are supported end-to-end. Single source of truth for the
# schema validators and the router query-param pattern below.
VALID_CURRENCIES = frozenset({"EUR", "USD"})
# Regex literal used by router Query(pattern=...) declarations. Built from
# VALID_CURRENCIES so it cannot drift from the validator set. Sorted for a
# stable, deterministic alternation order.
CURRENCY_PATTERN = "^(" + "|".join(sorted(VALID_CURRENCIES)) + ")$"

# --- Timeframes ---------------------------------------------------------------
# Preset lookback windows in days; None = unbounded ("all"). networth additionally
# supports a "custom" window (resolved from explicit from/to dates), so it extends
# this mapping with {"custom": None} at its call site.
TIMEFRAMES = {"1m": 30, "3m": 90, "1y": 365, "all": None}
