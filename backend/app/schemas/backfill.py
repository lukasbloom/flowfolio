"""Response schemas for the price-history backfill endpoints.

Two endpoints feed dialogs in the UI:

- `GET /api/instruments/backfill-preview` — summary counts the bulk dialog uses
  to show the user how many instruments are eligible, how many synthetic
  funds will be skipped, and the earliest first-transaction date the bulk
  loop will sweep from.
- `POST /api/instruments/backfill-all` — per-instrument breakdown of what
  happened during the bulk loop. Each item carries the same `status` token
  the per-instrument endpoint already surfaces (`ok`,
  `manual_history_required`, `no_history_available`) plus two new tokens
  specific to the bulk path: `no_transactions` (instrument has zero
  recorded transactions) and `rate_limited` (upstream provider returned a
  429 mid-loop). The endpoint itself returns 200 even when individual
  items fail — `rate_limited_count` exposes the count so the FE can toast
  a warning summary.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class BackfillPreviewResponse(BaseModel):
    eligible_count: int
    synthetic_count: int
    earliest_first_txn_date: date | None
    estimated_api_calls: int


class BulkBackfillItem(BaseModel):
    instrument_id: str
    symbol: str
    status: str
    inserted_prices: int
    skipped_existing: int


class BulkBackfillResponse(BaseModel):
    items: list[BulkBackfillItem]
    total_inserted_prices: int
    total_inserted_fx_rates: int
    rate_limited_count: int
