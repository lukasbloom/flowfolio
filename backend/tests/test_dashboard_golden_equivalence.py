"""Golden equivalence snapshots for the six dashboard read-path services.

These tests lock the byte-identity contract for the N+1 batching refactor:
``perf``, ``closed``, ``allocation``, ``concentration``, ``realized``, and
``contributions`` must return JSON-identical output before and after the
refactor across every (timeframe x currency) combination exercised here.

Committed FIRST, against the UNMODIFIED services, so the inline EXPECTED
snapshots below ARE the contract. They are NOT an approval-file scheme that
could drift silently — every expected string is a committed literal asserted
with ``==``. If a refactor changes one, the diff is loud and the refactor is
wrong (per the plan's HARD RULE: behavior-preserving).

Clock pinning: the golden fixture freezes on 2026-04-30 (see
``test_golden_portfolio_fixture.py::test_fx_2026_04_30_frozen`` and the clock
module docstring). ``app.core.clock`` binds ``_FIXED`` at import time, but the
services look up ``clock.today`` as a module attribute on each call, so a
runtime ``monkeypatch.setattr("app.core.clock.today", ...)`` takes effect for
the services that call ``clock.today()`` directly (allocation / concentration /
realized / contributions). ``perf``/``closed`` accept explicit dates and are
pinned via the ``today=`` argument / fixed window.

Serialization: schema-returning services dump via the router's Pydantic schema
``.model_dump(mode="json")`` then ``json.dumps(..., sort_keys=True)``. ``perf``
returns a list of ``PerfRow`` dataclasses; we route each through the router's
``PerfHoldingResponse`` schema (``model_validate``) so the snapshot is exactly
the bytes the HTTP layer would emit.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.schemas.allocation import AllocationResponse
from app.schemas.closed import ClosedPositionRow
from app.schemas.concentration import ConcentrationResponse
from app.schemas.contributions import ContributionBucket, SeriesPoint
from app.schemas.perf import PerfHoldingResponse
from app.schemas.realized import RealizedPerHolding, RealizedTotals
from app.services.allocation import get_allocation_slices
from app.services.closed import get_closed_positions
from app.services.concentration import get_concentration_offenders
from app.services.contributions import get_contribution_segments, get_cost_basis_series
from app.services.perf import get_performance_rows
from app.services.realized import get_realized_per_holding, get_realized_totals
from tests._golden_seed import seed_golden

FROZEN_TODAY = date(2026, 4, 30)


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
    """Pin clock.today/now to the fixture's frozen instant for direct callers."""
    from datetime import datetime

    monkeypatch.setattr("app.core.clock.today", lambda: FROZEN_TODAY)
    monkeypatch.setattr(
        "app.core.clock.now", lambda: datetime(2026, 4, 30, 12, 0, 0)
    )


# ---------------------------------------------------------------------------
# Serialization helpers — produce the canonical JSON string for each service.
# ---------------------------------------------------------------------------


def _dump(obj) -> str:
    """Canonical, sorted-key JSON for a Pydantic model or list of models."""
    if isinstance(obj, list):
        payload = [m.model_dump(mode="json") for m in obj]
    else:
        payload = obj.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True)


async def _perf_snapshot(session, **kwargs) -> str:
    rows = await get_performance_rows(session, **kwargs)
    schemas = [PerfHoldingResponse.model_validate(r) for r in rows]
    return _dump(schemas)


async def _closed_snapshot(session, **kwargs) -> str:
    rows = await get_closed_positions(session, **kwargs)
    schemas = [ClosedPositionRow.model_validate(r) for r in rows]
    return _dump(schemas)


async def _allocation_snapshot(session, dimension, **kwargs) -> str:
    resp: AllocationResponse = await get_allocation_slices(session, dimension, **kwargs)
    return _dump(resp)


async def _concentration_snapshot(session, **kwargs) -> str:
    resp: ConcentrationResponse = await get_concentration_offenders(session, **kwargs)
    return _dump(resp)


async def _realized_per_holding_snapshot(session, **kwargs) -> str:
    rows = await get_realized_per_holding(session, **kwargs)
    schemas = [RealizedPerHolding.model_validate(r) for r in rows]
    return _dump(schemas)


async def _realized_totals_snapshot(session, **kwargs) -> str:
    totals: RealizedTotals = await get_realized_totals(session, **kwargs)
    return _dump(totals)


async def _contributions_snapshot(session, **kwargs) -> str:
    cost_basis, value = await get_cost_basis_series(session, **kwargs)
    cb = [SeriesPoint.model_validate(p) for p in cost_basis]
    vv = [SeriesPoint.model_validate(p) for p in value]
    return json.dumps(
        {"cost_basis": [m.model_dump(mode="json") for m in cb],
         "value": [m.model_dump(mode="json") for m in vv]},
        sort_keys=True,
    )


async def _contribution_segments_snapshot(session, **kwargs) -> str:
    rows = await get_contribution_segments(session, **kwargs)
    schemas = [ContributionBucket.model_validate(r) for r in rows]
    return _dump(schemas)


# ---------------------------------------------------------------------------
# Parametrized equivalence tests. Each asserts the live service output equals
# the committed baseline in tests/_golden_expected.py::EXPECTED. The keys mirror
# the capture matrix; every one of the six endpoints appears in >= 2 param
# combinations (perf: 4, closed: 2, allocation: 5, concentration: 2,
# realized: 4, contributions: 4).
# ---------------------------------------------------------------------------

from tests._golden_expected import EXPECTED  # noqa: E402


@pytest.mark.parametrize(
    "key, timeframe, currency, kw",
    [
        ("perf_eur_1m", "1m", "EUR", {"today": FROZEN_TODAY}),
        ("perf_usd_1y", "1y", "USD", {"today": FROZEN_TODAY}),
        ("perf_eur_all", "all", "EUR", {"today": FROZEN_TODAY}),
        (
            "perf_usd_custom",
            "custom",
            "USD",
            {"from_date": date(2025, 4, 30), "to_date": date(2026, 4, 30)},
        ),
    ],
)
async def test_perf_equivalence(golden_session, key, timeframe, currency, kw):
    got = await _perf_snapshot(
        golden_session, timeframe=timeframe, display_currency=currency, **kw
    )
    assert got == EXPECTED[key]


@pytest.mark.parametrize(
    "key, currency",
    [("closed_eur", "EUR"), ("closed_usd", "USD")],
)
async def test_closed_equivalence(golden_session, key, currency):
    got = await _closed_snapshot(golden_session, display_currency=currency)
    assert got == EXPECTED[key]


@pytest.mark.parametrize(
    "key, dimension, currency",
    [
        ("alloc_eur_type", "type", "EUR"),
        ("alloc_eur_risk", "risk", "EUR"),
        ("alloc_eur_account", "account", "EUR"),
        ("alloc_eur_banked", "banked", "EUR"),
        ("alloc_usd_type", "type", "USD"),
    ],
)
async def test_allocation_equivalence(golden_session, key, dimension, currency):
    got = await _allocation_snapshot(golden_session, dimension, display_currency=currency)
    assert got == EXPECTED[key]


@pytest.mark.parametrize(
    "key, currency",
    [("conc_eur", "EUR"), ("conc_usd", "USD")],
)
async def test_concentration_equivalence(golden_session, key, currency):
    got = await _concentration_snapshot(golden_session, display_currency=currency)
    assert got == EXPECTED[key]


@pytest.mark.parametrize(
    "key, currency",
    [("realized_ph_eur", "EUR"), ("realized_ph_usd", "USD")],
)
async def test_realized_per_holding_equivalence(golden_session, key, currency):
    got = await _realized_per_holding_snapshot(golden_session, display_currency=currency)
    assert got == EXPECTED[key]


@pytest.mark.parametrize(
    "key, currency",
    [("realized_tot_eur", "EUR"), ("realized_tot_usd", "USD")],
)
async def test_realized_totals_equivalence(golden_session, key, currency):
    got = await _realized_totals_snapshot(golden_session, display_currency=currency)
    assert got == EXPECTED[key]


@pytest.mark.parametrize(
    "key, currency",
    [("contrib_series_eur", "EUR"), ("contrib_series_usd", "USD")],
)
async def test_contributions_series_equivalence(golden_session, key, currency):
    got = await _contributions_snapshot(golden_session, display_currency=currency)
    assert got == EXPECTED[key]


@pytest.mark.parametrize(
    "key, period, currency",
    [
        ("contrib_seg_eur", "month", "EUR"),
        ("contrib_seg_usd", "year", "USD"),
    ],
)
async def test_contribution_segments_equivalence(golden_session, key, period, currency):
    got = await _contribution_segments_snapshot(
        golden_session, period=period, display_currency=currency
    )
    assert got == EXPECTED[key]
