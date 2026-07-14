"""Shared pytest fixtures for the backend test suite.

The reconciliation tests are the first to require shared factory
fixtures across multiple test modules. Earlier modules (test_fifo.py,
test_accrual.py) defined their own per-file `session` fixture and
inline `_seed_*` helpers; that pattern is preserved (those modules
continue to use their own fixtures), and these new fixtures live
alongside as the project-wide convention going forward.

Conventions
-----------
- `db_session`: in-memory async SQLite session, schema created from
  `Base.metadata`. WAL + foreign_keys pragmas are applied via
  `attach_sqlite_pragmas` so ON DELETE CASCADE behaves like prod.
- `make_account`, `make_instrument`: thin ORM constructors, no Pydantic.
- `make_transaction`: thin ORM constructor that ALSO runs FIFO matching
  for `txn_type="sell"` (mirrors the production POST /api/transactions
  router). Accepts positive `quantity` for sells and stores the row as
  negative — same sign convention as the API layer.
- `make_adjustment_txn`: bypasses the Pydantic guard at
  schemas/transaction.py:36-42 (which forbids manual `txn_type=adjustment`)
  by constructing the row directly. Used by the future reconciliation
  service tests.
"""
from __future__ import annotations

# ruff: noqa: E402 — APP_ENV must be set before the app package is imported
# below, so the app imports intentionally follow that statement.
import os

# The native suite runs in development, mirroring compose.test.yml's explicit
# APP_ENV=development. APP_ENV now defaults to production, which marks the session
# cookie Secure — and the ASGI test client speaks plain HTTP, so a Secure cookie
# would never be sent back and every cookie-authenticated router test would 401.
# Set BEFORE any app import so the module-level `settings = Settings()` singleton
# reads it. test_app_env_default.py controls APP_ENV per-test via monkeypatch.
os.environ["APP_ENV"] = "development"

# A 32+ char SECRET_KEY so every JWT the suite signs (login, session-epoch,
# pre-auth tokens) clears PyJWT's InsecureKeyLengthWarning floor. The bare
# config default ("change-me-in-production", 24 chars) used to sign every one
# of those tokens under a short key, one warning per token. Set BEFORE any app
# import for the same reason as APP_ENV above. test_secret_key_bootstrap.py
# clears and restores this per-test to exercise the unset/short-key paths.
os.environ["SECRET_KEY"] = "test-suite-secret-key-32-chars-minimum"

from datetime import date as date_t
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.models.account import Account
from app.models.instrument import Instrument
from app.models.transaction import Transaction
from app.services.fifo import match_lots_for_sell


async def seed_admin_password(maker, password: str) -> None:
    """Seed the DB-backed admin password into a test database.

    The admin password now lives in user_setting.admin_password_hash (the DB is
    the source of truth — see app/services/setup_state.py). Router-level test
    fixtures that previously relied on the env-cached hash must seed this row so
    POST /api/auth/login can verify against it. Mirrors what the boot-time
    APP_PASSWORD pre-seed does in production (main.lifespan).

    `maker` is an async_sessionmaker bound to the same engine the test's get_db
    override yields from.
    """
    from app.services.setup_state import claim_admin_password

    async with maker() as s:
        await claim_admin_password(s, password)
        await s.commit()


@pytest_asyncio.fixture
async def db_session():
    """In-memory async SQLite session, fresh schema per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def make_account():
    async def _make(
        session: AsyncSession,
        *,
        name: str = "Test",
        account_type: str = "broker",
        is_banked: bool = True,
        currency: str = "EUR",
    ) -> Account:
        acct = Account(
            name=name,
            account_type=account_type,
            is_banked=is_banked,
            currency=currency,
        )
        session.add(acct)
        await session.flush()
        return acct

    return _make


@pytest.fixture
def make_instrument():
    async def _make(
        session: AsyncSession,
        *,
        symbol: str = "BTC",
        name: str = "Bitcoin",
        instrument_type: str = "crypto",
        # This kwarg is named `price_currency` to mirror the
        # transaction column; on the model the field is `base_currency`.
        # Accept both spellings so test bodies can use either.
        price_currency: str | None = None,
        base_currency: str = "USD",
        price_source: str = "manual",
        risk_level: str = "Medium",
    ) -> Instrument:
        currency = price_currency if price_currency is not None else base_currency
        inst = Instrument(
            symbol=symbol,
            name=name,
            instrument_type=instrument_type,
            base_currency=currency,
            price_source=price_source,
            risk_level=risk_level,
        )
        session.add(inst)
        await session.flush()
        return inst

    return _make


@pytest.fixture
def make_transaction():
    """Create a Transaction directly via ORM and (for sells) run FIFO matching.

    Accepts POSITIVE `quantity` for sells; stores them as negative — same
    sign convention as the production POST /api/transactions router.
    """

    async def _make(
        session: AsyncSession,
        *,
        account: Account,
        instrument: Instrument,
        txn_type: str = "buy",
        date: date_t,
        quantity: Decimal,
        unit_price: Decimal | None = None,
        price_currency: str | None = None,
        fx_rate_to_eur: Decimal | None = None,
        cost_basis_eur: Decimal | None = None,
        fee_eur: Decimal = Decimal("0"),
        notes: str | None = None,
        source: str = "manual",
    ) -> Transaction:
        # Sells stored as negative quantity (same convention as router).
        stored_qty = -quantity if txn_type == "sell" else quantity

        # Pre-compute cost_basis_eur for buys when not explicitly provided.
        if (
            cost_basis_eur is None
            and unit_price is not None
            and fx_rate_to_eur is not None
            and fx_rate_to_eur != Decimal("0")
            and txn_type in ("buy", "yield")
        ):
            cost_basis_eur = (quantity * unit_price) / fx_rate_to_eur

        txn = Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type=txn_type,
            date=date,
            quantity=stored_qty,
            unit_price=unit_price,
            price_currency=price_currency,
            fx_rate_to_eur=fx_rate_to_eur,
            cost_basis_eur=cost_basis_eur,
            fee_eur=fee_eur,
            notes=notes,
            source=source,
        )
        session.add(txn)
        await session.flush()

        # Mirror router behavior: run FIFO matching for sells.
        if txn_type == "sell":
            await match_lots_for_sell(session, txn)
            await session.flush()

        return txn

    return _make


@pytest.fixture
def make_adjustment_txn():
    """Bypass the manual-create Pydantic guard for `txn_type=adjustment`.

    The schemas/transaction.py guard forbids manual API callers from
    creating adjustment rows; the reconciliation service writes them via
    ORM directly (mirrors services/accrual.py:206-219 for `yield`).
    This fixture lets tests construct adjustment rows the same way.
    """

    async def _make(
        session: AsyncSession,
        *,
        account: Account,
        instrument: Instrument,
        snapshot_date: date_t,
        delta_qty: Decimal,
        reconciliation_id: str | None = None,
        notes: str = "",
    ) -> Transaction:
        txn = Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type="adjustment",
            date=snapshot_date,
            quantity=delta_qty,
            unit_price=None,
            price_currency=None,
            fx_rate_to_eur=None,
            cost_basis_eur=None,
            fee_eur=Decimal("0"),
            notes=notes,
            source="adjustment",
        )
        # `reconciliation_id` column is added by the reconciliation migration;
        # set the attribute only if the model defines it (so this fixture
        # doesn't break before the migration ships).
        if reconciliation_id is not None and hasattr(txn, "reconciliation_id"):
            txn.reconciliation_id = reconciliation_id  # type: ignore[attr-defined]
        session.add(txn)
        await session.flush()
        return txn

    return _make


# Suppress unused-import warning while still importing the symbol so the
# alias is exported for static analyzers that scan the module.
_ = Any
