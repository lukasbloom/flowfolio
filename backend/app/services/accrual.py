"""Daily yield accrual with 90-day backfill and JobRun idempotency guard.

Caller-commits convention: this service stages yield transactions and successful
JobRun updates. The caller commits on success. Failure markers are committed
inside accrue_for_date before re-raising so operators can see and clear them.

Balance for accrual day D = buys + prior yields - sells through D-1.
Yield cost basis is locked at create time using the accrual-date price and
FX. fx_rate_to_eur stores an EUR-base rate (USD per 1 EUR), so USD-denominated
cost basis divides by that rate.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import httpx
from sqlalchemy import null, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.models.apy_config import ApyConfig
from app.models.fx_rate import FxRate
from app.models.instrument import Instrument
from app.models.job_runs import JobRun
from app.models.price_quote import PriceQuote
from app.models.transaction import Transaction
from app.services.fx import get_or_fetch_fx_rate
from app.services.pricing.dispatcher import StaleQuoteError

logger = logging.getLogger(__name__)
ACCRUAL_JOB_NAME = "accrual"
DAYS_PER_YEAR = Decimal("365")
EUR_IDENTITY = Decimal("1")

# Backend defensive gate: only accrue yield for instrument types where
# the APY model makes sense. Mirrors frontend canHaveApy() in
# frontend/lib/instrument-eligibility.ts.
YIELD_ELIGIBLE_TYPES = frozenset({"cash", "stablecoin", "crypto"})


async def _get_balance_through(
    session: AsyncSession, account_id: str, instrument_id: str, through_date: date
) -> Decimal:
    """Sum signed quantity through and including through_date.

    quantity is TEXT-backed (DecimalText) — sum in Python; a SQL SUM would
    coerce the text values back to float (see plan 006).
    """
    stmt = select(Transaction.quantity).where(
        Transaction.account_id == account_id,
        Transaction.instrument_id == instrument_id,
        Transaction.date <= through_date,
        Transaction.deleted_at.is_(null()),
    )
    result = await session.execute(stmt)
    return sum((q for (q,) in result), Decimal("0"))


async def _active_apy_configs_for_date(
    session: AsyncSession, on_date: date
) -> list[ApyConfig]:
    """APY configs effective on on_date.

    First accrual is on effective_from + 1, so on_date must be
    strictly greater than effective_from.
    """
    stmt = select(ApyConfig).where(
        ApyConfig.effective_from < on_date,
        or_(ApyConfig.effective_to.is_(None), ApyConfig.effective_to >= on_date),
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _price_for_accrual(
    session: AsyncSession,
    instrument: Instrument,
    on_date: date,
) -> tuple[Decimal, str]:
    """Return cached price for on_date or latest prior quote.

    Manual rows win for same-date reads. Accrual does not live-fetch prices; the
    22:00 UTC price-refresh cron owns provider fetches.
    """
    stmt = (
        select(PriceQuote)
        .where(PriceQuote.instrument_id == instrument.id, PriceQuote.date == on_date)
        .order_by((PriceQuote.source == "manual").desc(), PriceQuote.fetched_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is not None:
        return Decimal(str(row.price)), row.currency

    stmt = (
        select(PriceQuote)
        .where(PriceQuote.instrument_id == instrument.id, PriceQuote.date <= on_date)
        .order_by(PriceQuote.date.desc(), PriceQuote.fetched_at.desc())
        .limit(1)
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise StaleQuoteError(
            f"no price_quote available for {instrument.symbol} on or before {on_date}"
        )
    logger.info(
        "accrual_using_stale_price",
        extra={
            "symbol": instrument.symbol,
            "accrual_date": on_date.isoformat(),
            "price_date": row.date.isoformat(),
        },
    )
    return Decimal(str(row.price)), row.currency


async def _fx_rate_to_eur_on_date(
    session: AsyncSession,
    http_client: httpx.AsyncClient,
    quote_currency: str,
    on_date: date,
) -> Decimal:
    """Return EUR-base rate, e.g. USD per 1 EUR, for quote_currency on on_date."""
    if quote_currency == "EUR":
        return EUR_IDENTITY
    fx_row: FxRate = await get_or_fetch_fx_rate(
        session, http_client, on_date, base="EUR", quote=quote_currency
    )
    return Decimal(str(fx_row.rate))


async def _mark_failed_job_run(
    session: AsyncSession, accrual_date: date, error: Exception
) -> None:
    # Construct directly with status="failed" — no intervening code reads the
    # row's status between construction and commit, so the former
    # status="running" then reassign-to-"failed" dance was redundant.
    job = JobRun(
        job_name=ACCRUAL_JOB_NAME,
        run_date=accrual_date,
        status="failed",
        completed_at=clock.now(),
        notes=f"{type(error).__name__}: {error}"[:500],
    )
    session.add(job)
    await session.commit()


async def accrue_for_date(
    session: AsyncSession,
    accrual_date: date,
    http_client: Optional[httpx.AsyncClient] = None,
) -> list[Transaction]:
    """Generate yield txns for accrual_date. Idempotent via JobRun UNIQUE guard."""
    stmt = select(JobRun).where(
        JobRun.job_name == ACCRUAL_JOB_NAME, JobRun.run_date == accrual_date
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        logger.info(
            "accrual_skip_existing_job_run",
            extra={"date": accrual_date.isoformat(), "status": existing.status},
        )
        return []

    job = JobRun(job_name=ACCRUAL_JOB_NAME, run_date=accrual_date, status="running")
    session.add(job)
    try:
        await session.flush()
    except IntegrityError:
        # another worker owns this date; skip without recording our own failure row.
        await session.rollback()
        return []

    created: list[Transaction] = []
    own_client = http_client is None
    if http_client is None:
        http_client = httpx.AsyncClient()

    try:
        configs = await _active_apy_configs_for_date(session, accrual_date)
        for cfg in configs:
            balance = await _get_balance_through(
                session,
                cfg.account_id,
                cfg.instrument_id,
                accrual_date - timedelta(days=1),
            )
            if balance <= 0:
                continue

            yield_qty = (balance * Decimal(str(cfg.apy_rate)) / DAYS_PER_YEAR).quantize(
                Decimal("0.000000000000000001")
            )
            if yield_qty <= 0:
                continue

            instrument = await session.get(Instrument, cfg.instrument_id)
            if instrument is None:
                logger.warning("accrual_orphan_apy_config", extra={"apy_config_id": cfg.id})
                continue

            # Backend defensive gate: skip yield accrual for instruments
            # whose type isn't yield-eligible. Frontend already hides the APY
            # config tab for non-eligible types, so any
            # ApyConfig pointing at a non-eligible instrument was either
            # historical bad data or direct-to-API misuse. Logging at WARNING
            # so it shows up in normal operation if it happens.
            if instrument.instrument_type not in YIELD_ELIGIBLE_TYPES:
                logger.warning(
                    "accrual_skipped_non_yield_eligible_type",
                    extra={
                        "apy_config_id": cfg.id,
                        "instrument_id": instrument.id,
                        "instrument_type": instrument.instrument_type,
                    },
                )
                continue

            price, price_currency = await _price_for_accrual(session, instrument, accrual_date)
            fx_rate = await _fx_rate_to_eur_on_date(
                session, http_client, price_currency, accrual_date
            )
            cost_basis_eur = (yield_qty * price / fx_rate).quantize(
                Decimal("0.00000001")
            )

            txn = Transaction(
                account_id=cfg.account_id,
                instrument_id=cfg.instrument_id,
                txn_type="yield",
                date=accrual_date,
                quantity=yield_qty,
                unit_price=price,
                price_currency=price_currency,
                fx_rate_to_eur=fx_rate,
                cost_basis_eur=cost_basis_eur,
                fee_eur=Decimal("0"),
                source="accrual",
                apy_config_id=cfg.id,
                notes=f"auto-accrual {accrual_date.isoformat()} @ {cfg.apy_rate} APY",
            )
            session.add(txn)
            created.append(txn)

        job.status = "ok"
        job.completed_at = clock.now()
        await session.flush()
        return created
    except Exception as exc:
        await session.rollback()
        try:
            await _mark_failed_job_run(session, accrual_date, exc)
        except IntegrityError:
            # A concurrent worker inserted the JobRun between our rollback and
            # the marker insert.  Rather than losing the audit trail, UPDATE the
            # existing row to status='failed' so operators can investigate.
            await session.rollback()
            existing = (
                await session.execute(
                    select(JobRun).where(
                        JobRun.job_name == ACCRUAL_JOB_NAME,
                        JobRun.run_date == accrual_date,
                    )
                )
            ).scalar_one()
            existing.status = "failed"
            existing.notes = f"{type(exc).__name__}: {exc}"[:500]
            existing.completed_at = clock.now()
            await session.commit()
        raise
    finally:
        if own_client:
            await http_client.aclose()


async def run_accrual_with_backfill(
    session: AsyncSession,
    today: Optional[date] = None,
    backfill_days: int = 90,
) -> dict[date, int]:
    """Walk back up to backfill_days; accrue any date that has no JobRun."""
    today = today or clock.today()
    summary: dict[date, int] = {}
    async with httpx.AsyncClient() as client:
        for offset in range(backfill_days, -1, -1):
            day = today - timedelta(days=offset)
            try:
                created = await accrue_for_date(session, day, http_client=client)
                summary[day] = len(created)
                await session.commit()
            except Exception as exc:
                logger.error(
                    "accrual_day_failed",
                    extra={"date": day.isoformat(), "err": str(exc)},
                )
                summary[day] = 0
    return summary
