"""Clock abstraction that pins time for snapshot test runs.

Reads FLOWFOLIO_FIXED_NOW from app.core.config.settings (NOT directly from
os.environ — keep the config surface single-sourced). When set, every call to
now()/today() returns the same fixed instant. When unset, falls through to
real wallclock.

The frozen instant is bound at IMPORT TIME ONLY — no per-request override hook.
Patching the env mid-process has no effect; this is intentional
(prevents mid-suite leaks).

All datetimes returned are NAIVE UTC to match the existing call-site contract
(every `datetime.utcnow()` site this module replaces returned a naive UTC
datetime). The frozen instant `2026-04-30T12:00:00Z` parses to a tz-aware
datetime; we strip tzinfo before returning.

NOTE: this module is the only legitimate caller of `datetime.utcnow()` and
`date.today()` in `backend/app/`.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import settings

# The app's local calendar timezone. This is a self-hosted single-user tool;
# the user lives in Spain, so "today" from the user's perspective is the
# Europe/Madrid date, not UTC. Single-sourced here and referenced by every
# user-facing date boundary (reconciliation snapshot_date, CoinGecko daily
# bucketing, see services/pricing/coingecko.py). Change in one place
# if the deployment ever relocates.
LOCAL_TZ = ZoneInfo("Europe/Madrid")


def _parse_fixed_now() -> datetime | None:
    raw = settings.fixed_now
    if not raw:
        return None
    # Accept "2026-04-30T12:00:00Z" — fromisoformat() requires +00:00 in py<3.11
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    # Strip tzinfo so the return type matches existing datetime.utcnow() callers.
    return parsed.replace(tzinfo=None)


_FIXED: datetime | None = _parse_fixed_now()


def now() -> datetime:
    """Return the wall-clock UTC datetime, or the pinned instant if FIXED_NOW is set."""
    return _FIXED if _FIXED is not None else datetime.utcnow()


def today() -> date:
    """Return the wall-clock UTC date, or the pinned date if FIXED_NOW is set."""
    return _FIXED.date() if _FIXED is not None else date.today()


def today_local() -> date:
    """Return today's date in the app's LOCAL_TZ (Europe/Madrid).

    Use this — NOT today() — for any boundary the user reasons about in their
    own calendar, most importantly the reconciliation snapshot_date "not in the
    future" guard. today() is UTC and runs up to 2h behind Madrid (CEST = UTC+2),
    so between local midnight and 02:00 a snapshot dated "today" by the browser
    is rejected as future against the UTC date. Comparing in LOCAL_TZ matches
    the frontend, which derives its date from the browser's local clock.

    When FIXED_NOW is pinned (test stack), returns its date for determinism —
    the frozen instant is naive UTC, but the suite only asserts whole dates.
    """
    if _FIXED is not None:
        return _FIXED.date()
    return datetime.now(LOCAL_TZ).date()
