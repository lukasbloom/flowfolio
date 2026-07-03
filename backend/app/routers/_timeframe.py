"""Shared timeframe-window validation for the read-model routers.

`validate_custom_timeframe` collapses the byte-identical custom-timeframe 422
guard that perf.py and closed.py both inlined:

    if timeframe == "custom":
        if from_date is None or to_date is None:
            raise HTTPException(422, "custom timeframe requires from and to dates")
        if from_date > to_date:
            raise HTTPException(422, "from must be <= to")

NOTE: networth.py is intentionally NOT routed through this helper. Its guard
differs in behavior, not just wording — networth validates `from_date > to_date`
for EVERY timeframe (not only "custom"), and uses different message strings
("custom timeframe requires both from and to dates" / "from must be on or before
to"). Folding it in here would change when networth's from>to check fires, so
networth keeps its own inline guard (documented at that call site).
"""
from __future__ import annotations

from datetime import date

from fastapi import HTTPException


def validate_custom_timeframe(
    timeframe: str, from_date: date | None, to_date: date | None
) -> None:
    """Raise HTTPException(422) for an invalid custom window; no-op otherwise.

    Preserves perf/closed's exact messages and control flow.
    """
    if timeframe == "custom":
        if from_date is None or to_date is None:
            raise HTTPException(
                status_code=422,
                detail="custom timeframe requires from and to dates",
            )
        if from_date > to_date:
            raise HTTPException(status_code=422, detail="from must be <= to")
