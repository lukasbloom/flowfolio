#!/usr/bin/env python
"""Backfill historical prices for FT-priced instruments via Yahoo (history-only).

FT.com serves only the current NAV, so funds/ETFs/the gold ETC (price_source="ft")
have no price history and their per-holding TWRR is suppressed by the 7-day guard.
This pulls daily EUR history from Yahoo (the documented history-only exception to
the Yahoo blacklist — see app/services/pricing/yahoo.py) and stores it as
`price_quote` rows tagged source="yahoo". Live pricing stays on FT.

Yahoo aggressively rate-limits bursts (and IP-bans hosts that poll it constantly),
so this spaces calls out and backs off on 429. Run it ONCE; re-runs skip dates
already stored. Run from a non-throttled IP (e.g. your VPS) if the dev IP is hot.

Usage (after `docker compose up -d --build api` so the image has the Yahoo code):

    docker compose run --rm --no-deps -v "$PWD/scripts:/app/scripts:ro" \
        api python /app/scripts/backfill_ft_history.py

Local dev:

    DATABASE_URL=... SECRET_KEY=x PYTHONPATH=./backend \
        backend/.venv/bin/python scripts/backfill_ft_history.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent.resolve()

SPACING_SECONDS = 10        # between instruments
BACKOFF_SECONDS = (45, 90, 150)  # per-instrument retry waits on 429


async def _run() -> int:
    import httpx
    from sqlalchemy import func, select

    from app.core.database import AsyncSessionLocal
    from app.models.instrument import Instrument
    from app.models.transaction import Transaction
    from app.services.backfill import backfill_instrument_history
    from app.services.pricing.errors import PriceProviderRateLimited

    today = date.today()
    async with AsyncSessionLocal() as s:
        insts = (await s.execute(
            select(Instrument).where(Instrument.price_source == "ft").order_by(Instrument.symbol)
        )).scalars().all()
        plan = []
        for inst in insts:
            first_buy = (await s.execute(
                select(func.min(Transaction.date)).where(
                    Transaction.instrument_id == inst.id, Transaction.txn_type == "buy"
                )
            )).scalar()
            plan.append((inst.id, inst.symbol, first_buy or date(2023, 1, 1)))

    print(f"Backfilling {len(plan)} FT instruments via Yahoo (history-only)...", flush=True)
    failures = 0
    for idx, (iid, symbol, start) in enumerate(plan):
        for attempt in range(len(BACKOFF_SECONDS) + 1):
            try:
                async with AsyncSessionLocal() as s, httpx.AsyncClient() as client:
                    inst = await s.get(Instrument, iid)
                    res = await backfill_instrument_history(s, client, inst, start, today)
                    await s.commit()
                print(f"  {symbol:<11} {res.status:<22} inserted={res.inserted_prices} "
                      f"skipped={res.skipped_existing}", flush=True)
                break
            except PriceProviderRateLimited:
                if attempt == len(BACKOFF_SECONDS):
                    print(f"  {symbol:<11} gave up (rate limited)", flush=True)
                    failures += 1
                    break
                wait = BACKOFF_SECONDS[attempt]
                print(f"  {symbol:<11} 429 — backoff {wait}s", flush=True)
                await asyncio.sleep(wait)
            except Exception as exc:  # noqa: BLE001
                print(f"  {symbol:<11} ERROR {type(exc).__name__}: {exc}", flush=True)
                failures += 1
                break
        if idx < len(plan) - 1:
            await asyncio.sleep(SPACING_SECONDS)
    print("DONE" if not failures else f"DONE with {failures} failure(s) — re-run to retry", flush=True)
    return 1 if failures else 0


def main() -> int:
    if "DATABASE_URL" not in os.environ:
        print("ERROR: set DATABASE_URL before running.", file=sys.stderr)
        return 2
    if (_REPO_ROOT / "backend" / "app").is_dir():
        sys.path.insert(0, str(_REPO_ROOT / "backend"))
    elif (_REPO_ROOT / "app").is_dir():
        sys.path.insert(0, str(_REPO_ROOT))
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
