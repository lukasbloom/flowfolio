"""Characterization + perf guard for the net-worth O(N^2) day-replay optimization.

The reference in ``fixtures/networth_equivalence.json`` was captured from the
UNMODIFIED ``get_networth_series`` on the golden dataset (deterministic uuid5 ids,
frozen clock), so it IS the byte-identity contract: the forward-cursor refactor
must reproduce it exactly across the timeframe x currency x cost-basis matrix.

``test_heavy_call_under_ceiling`` fails on the current O(N^2) code and passes once
the scans are amortized (a loose ceiling, generous vs the ~sub-100ms target, to
avoid CI flakiness).
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.schemas.networth import NetWorthResponse
from app.services.networth import get_networth_series
from tests._golden_seed import seed_golden

FROZEN_TODAY = date(2026, 4, 30)
CUSTOM_RANGE = (date(2024, 1, 1), FROZEN_TODAY)

_REF = json.loads(
    (Path(__file__).parent / "fixtures" / "networth_equivalence.json").read_text()
)
_CASES = _REF["cases"]
_EXPECTED = _REF["expected"]


@pytest_asyncio.fixture
async def golden_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        await seed_golden(s)
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def pin_clock(monkeypatch):
    monkeypatch.setattr("app.core.clock.today", lambda: FROZEN_TODAY)
    monkeypatch.setattr("app.core.clock.now", lambda: datetime(2026, 4, 30, 12, 0, 0))


def _dump(series) -> str:
    return json.dumps(
        NetWorthResponse.model_validate(series).model_dump(mode="json"),
        sort_keys=True,
    )


async def _run(session: AsyncSession, case: dict):
    start = end = None
    if case["timeframe"] == "custom":
        start, end = CUSTOM_RANGE
    return await get_networth_series(
        session,
        timeframe=case["timeframe"],
        display_currency=case["currency"],
        start=start,
        end=end,
        instrument_ids=case["instrument_ids"],
        include_cost_basis=case["include_cost_basis"],
    )


@pytest.mark.parametrize("idx", range(len(_CASES)), ids=[
    f"{c['timeframe']}-{c['currency']}-cb{int(c['include_cost_basis'])}"
    f"{'-filtered' if c['instrument_ids'] else ''}"
    for c in _CASES
])
@pytest.mark.asyncio
async def test_output_matches_reference(golden_session, idx):
    case = _CASES[idx]
    key = (
        f"{idx}:{case['timeframe']}:{case['currency']}"
        f":cb={case['include_cost_basis']}:ids={case['instrument_ids']}"
    )
    series = await _run(golden_session, case)
    assert _dump(series) == _EXPECTED[key], f"networth output drifted for case {key}"


@pytest.mark.asyncio
async def test_heavy_call_under_ceiling(golden_session):
    """The 1y/USD/include_cost_basis call (the dashboard's heaviest) must not
    regress to the O(N^2) day-replay.

    This is a coarse complexity gate, not a micro-benchmark. It runs on shared
    CI runners where absolute wall-clock varies several-fold (the amortized call
    was ~0.1s locally but 0.54s on CI), so the ceiling sits well above that and
    only trips on a catastrophic reintroduction of the quadratic scan, which is
    multiple seconds on this ~15k-quote golden dataset. The byte-identity
    equivalence tests above are the precise correctness guard; this one only
    guards against the O(N^2) blow-up coming back.
    """
    t0 = time.perf_counter()
    await get_networth_series(
        golden_session, timeframe="1y", display_currency="USD", include_cost_basis=True
    )
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, (
        f"networth 1y/USD/cost-basis took {elapsed*1000:.0f}ms (O(N^2) regression?)"
    )
