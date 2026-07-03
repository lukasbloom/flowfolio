"""Shared value types for the pricing providers.

`HistoricalPrice` was redefined identically (frozen dataclass, fields
`date: date` then `price: Decimal`) in alpha_vantage, coingecko, binance, and
twelve_data. Defined once here and imported by all four; field names and order
match the former per-module copies exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class HistoricalPrice:
    date: date
    price: Decimal
