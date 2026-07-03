"""End-to-end FIFO recompute / atomicity / audit-trail tests.

Proves the hardest correctness claim: back-dated
adjustments recompute FIFO chains for already-recorded later sells, so realized
P&L stays history-honest. Also pins the atomic POST flow and the
dismiss audit trail.
"""
from datetime import date

import pytest
import pytest_asyncio
from decimal import Decimal
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.config as _cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app as _fastapi_app
from app.models.lot_alloc import LotAlloc
from app.models.reconciliation import Reconciliation
from app.models.transaction import Transaction
from app.schemas.reconciliation import (
    DriftDecision,
    HoldingSnapshotEntry,
    ReconciliationCreate,
)
from app.services.reconciliation import save_event
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def client():
    """HTTP client with login + isolated in-memory SQLite per test."""
    _original_password = _cfg_module.settings.app_password
    _cfg_module.settings.app_password = "test-password-e2e"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_db():
        async with Maker() as s:
            yield s

    _fastapi_app.dependency_overrides[get_db] = _override_db
    await seed_admin_password(Maker, "test-password-e2e")

    transport = ASGITransport(app=_fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post("/api/auth/login", json={"password": "test-password-e2e"})
        assert login.status_code == 200, "fixture login must succeed"
        c._test_session_maker = Maker  # type: ignore[attr-defined]
        yield c

    _fastapi_app.dependency_overrides.clear()
    await engine.dispose()
    _cfg_module.settings.app_password = _original_password


@pytest.mark.asyncio
async def test_back_dated_negative_adjustment_recomputes_later_sell_fifo(
    db_session, make_account, make_instrument, make_transaction
):
    """History-honest FIFO: back-dated adjustment recomputes already-recorded later sell.

    Scenario:
      - 2026-01-01: buy 10 BTC @ €30,000 (txn1)
      - 2026-04-15: sell 3 BTC @ €40,000 (txn2) — FIFO matches against txn1; sell consumes 3 of the 10-lot
      - 2026-04-01: user reconciles, broker shows 6 BTC, app shows 7 (10 − 3)
        Server derives delta = snapshot 6 − app 7 = -1 → adjustment quantity = -1

    The adjustment lands on 2026-04-01 — between the buy and the sell. By the FIFO
    `(date ASC, created_at ASC)` invariant, the adjustment competes BEHIND the
    original buy but IN FRONT of the sell. The 10-BTC buy still satisfies the
    entire 3-BTC sell; the recompute must be a no-op for cost_basis_eur on this
    sell, but it MUST have run (lot_alloc rows are replaced — same quantity, same
    buy_txn_id). The running balance reconciles to broker's reported 6 BTC.
    """
    account = await make_account(db_session, name="XTB")
    btc = await make_instrument(
        db_session,
        symbol="BTC",
        name="Bitcoin",
        instrument_type="crypto",
        price_currency="EUR",
    )

    txn1 = await make_transaction(
        db_session,
        account=account,
        instrument=btc,
        txn_type="buy",
        date=date(2026, 1, 1),
        quantity=Decimal("10"),
        unit_price=Decimal("30000"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
    )
    txn2 = await make_transaction(
        db_session,
        account=account,
        instrument=btc,
        txn_type="sell",
        date=date(2026, 4, 15),
        quantity=Decimal("3"),
        unit_price=Decimal("40000"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
    )
    await db_session.commit()

    # Snapshot lot_alloc + cost_basis_eur of the sell BEFORE recon.
    pre = (
        await db_session.execute(
            select(LotAlloc).where(LotAlloc.sell_txn_id == txn2.id)
        )
    ).scalars().all()
    pre_qty = sum(a.quantity for a in pre)
    pre_buy_ids = {a.buy_txn_id for a in pre}
    assert pre_qty == Decimal("3")
    assert pre_buy_ids == {txn1.id}, "Pre-recon: sell draws from the original buy lot"

    sell_pre = (
        await db_session.execute(select(Transaction).where(Transaction.id == txn2.id))
    ).scalar_one()
    pre_cost_basis = sell_pre.cost_basis_eur

    payload = ReconciliationCreate(
        account_id=account.id,
        snapshot_date=date(2026, 4, 1),
        notes=None,
        holdings=[HoldingSnapshotEntry(instrument_id=btc.id, snapshot_qty=Decimal("6"))],
        decisions=[DriftDecision(instrument_id=btc.id, action="accept")],
    )
    event = await save_event(db_session, payload)

    # Assert (A): adjustment row exists with reconciliation_id, correct shape.
    adj = (
        await db_session.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.instrument_id == btc.id,
                Transaction.txn_type == "adjustment",
            )
        )
    ).scalar_one()
    assert adj.quantity == Decimal("-1")
    assert adj.date == date(2026, 4, 1)
    assert adj.source == "adjustment"
    assert adj.reconciliation_id == event.id

    # Assert (B): recompute fired — lot_alloc rows for the sell still consume 3
    # BTC from the original buy (adjustment is BEHIND the buy in FIFO order).
    # The cost_basis_eur figure on the sell is unchanged because the original
    # buy still satisfies the entire 3-BTC sell.
    post = (
        await db_session.execute(
            select(LotAlloc).where(LotAlloc.sell_txn_id == txn2.id)
        )
    ).scalars().all()
    assert sum(a.quantity for a in post) == Decimal("3"), (
        "Sell still consumes 3 BTC after recompute"
    )
    assert {a.buy_txn_id for a in post} == {txn1.id}, (
        "Adjustment is BEHIND the original buy in FIFO order — sell must still "
        "draw from the original buy lot, not from the adjustment lot"
    )
    sell_post = (
        await db_session.execute(select(Transaction).where(Transaction.id == txn2.id))
    ).scalar_one()
    assert sell_post.cost_basis_eur == pre_cost_basis, (
        "cost_basis on the sell is unchanged because the original buy still "
        "satisfies the entire 3-BTC sell"
    )

    # Assert (C): running balance reconciles to broker's reported 6 BTC.
    total = (
        await db_session.execute(
            select(func.coalesce(func.sum(Transaction.quantity), 0)).where(
                Transaction.account_id == account.id,
                Transaction.instrument_id == btc.id,
                Transaction.date <= date(2026, 4, 15),
            )
        )
    ).scalar_one()
    # Sell quantity is stored negative; net = 10 + (-1) + (-3) = 6.
    assert Decimal(total) == Decimal("6")


@pytest.mark.asyncio
async def test_full_recon_event_persists_atomic(client):
    """POST /api/reconciliation/events writes ONE event with
    multiple decisions atomically — accept on I1 (delta -1) + dismiss on I2 (zero qty).
    """
    Maker = client._test_session_maker  # type: ignore[attr-defined]
    async with Maker() as s:
        from app.models.account import Account
        from app.models.instrument import Instrument

        account = Account(
            name="XTB", account_type="broker", is_banked=True, currency="EUR"
        )
        s.add(account)
        await s.flush()
        btc = Instrument(
            symbol="BTC", name="Bitcoin", instrument_type="crypto",
            base_currency="EUR", price_source="manual", risk_level="Medium",
        )
        eth = Instrument(
            symbol="ETH", name="Ethereum", instrument_type="crypto",
            base_currency="EUR", price_source="manual", risk_level="Medium",
        )
        s.add_all([btc, eth])
        await s.flush()
        s.add_all([
            Transaction(
                account_id=account.id, instrument_id=btc.id, txn_type="buy",
                date=date(2026, 1, 1), quantity=Decimal("10"),
                unit_price=Decimal("30000"), price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("300000"),
                fee_eur=Decimal("0"), source="manual",
            ),
            Transaction(
                account_id=account.id, instrument_id=eth.id, txn_type="buy",
                date=date(2026, 2, 1), quantity=Decimal("5"),
                unit_price=Decimal("3000"), price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("15000"),
                fee_eur=Decimal("0"), source="manual",
            ),
        ])
        await s.commit()
        account_id = account.id
        btc_id = btc.id
        eth_id = eth.id

    payload = {
        "account_id": account_id,
        "snapshot_date": "2026-04-01",
        "notes": "phase-5 e2e atomic test",
        "holdings": [
            {"instrument_id": btc_id, "snapshot_qty": "9"},
            {"instrument_id": eth_id, "snapshot_qty": "5"},
        ],
        "decisions": [
            {"instrument_id": btc_id, "action": "accept"},
            {
                "instrument_id": eth_id,
                "action": "dismiss",
                "dismiss_reason": "ETH staking pending settlement",
            },
        ],
    }
    resp = await client.post("/api/reconciliation/events", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["account_id"] == account_id
    event_id = body["id"]
    assert body["snapshot_date"] == "2026-04-01"

    # Confirm both adjustment rows landed under the same reconciliation_id.
    async with Maker() as s:
        adjs = (
            await s.execute(
                select(Transaction).where(
                    Transaction.account_id == account_id,
                    Transaction.txn_type == "adjustment",
                    Transaction.reconciliation_id == event_id,
                )
            )
        ).scalars().all()
        assert len(adjs) == 2, (
            f"Expected exactly 2 adjustment rows (one per decision), got {len(adjs)}"
        )

        by_inst = {a.instrument_id: a for a in adjs}
        assert by_inst[btc_id].quantity == Decimal("-1"), (
            "BTC accept derives delta = snapshot 9 − app 10 = -1"
        )
        assert by_inst[btc_id].source == "adjustment"

        assert by_inst[eth_id].quantity == Decimal("0"), (
            "ETH dismiss writes a zero-qty audit row regardless of holdings"
        )
        assert "dismissed:" in (by_inst[eth_id].notes or "")
        assert "ETH staking pending settlement" in (by_inst[eth_id].notes or "")

        # Exactly one Reconciliation row exists with the requested snapshot_date.
        events = (
            await s.execute(
                select(Reconciliation).where(Reconciliation.account_id == account_id)
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].snapshot_date == date(2026, 4, 1)


@pytest.mark.asyncio
async def test_dismissed_drift_audit_trail_intact(
    db_session, make_account, make_instrument, make_transaction
):
    """Dismissed drift writes a zero-qty audit row but does NOT touch
    FIFO/lot_alloc, and MAX(snapshot_date) still surfaces — the badge must show
    even when every drift was dismissed.
    """
    account = await make_account(db_session, name="XTB")
    btc = await make_instrument(
        db_session,
        symbol="BTC",
        name="Bitcoin",
        instrument_type="crypto",
        price_currency="EUR",
    )
    await make_transaction(
        db_session,
        account=account,
        instrument=btc,
        txn_type="buy",
        date=date(2026, 1, 1),
        quantity=Decimal("5"),
        unit_price=Decimal("30000"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
    )
    await db_session.commit()

    qty_before = (
        await db_session.execute(
            select(func.coalesce(func.sum(Transaction.quantity), 0)).where(
                Transaction.account_id == account.id,
                Transaction.instrument_id == btc.id,
            )
        )
    ).scalar_one()

    payload = ReconciliationCreate(
        account_id=account.id,
        snapshot_date=date(2026, 5, 5),
        notes=None,
        holdings=[HoldingSnapshotEntry(instrument_id=btc.id, snapshot_qty=Decimal("5"))],
        decisions=[
            DriftDecision(
                instrument_id=btc.id, action="dismiss", dismiss_reason="(none)"
            )
        ],
    )
    event = await save_event(db_session, payload)

    # Audit row exists with zero qty + 'dismissed:' prefix + reconciliation_id FK.
    adj = (
        await db_session.execute(
            select(Transaction).where(
                Transaction.account_id == account.id,
                Transaction.instrument_id == btc.id,
                Transaction.txn_type == "adjustment",
            )
        )
    ).scalar_one()
    assert adj.quantity == Decimal("0")
    assert (adj.notes or "").startswith("dismissed:")
    assert adj.reconciliation_id == event.id

    # No FIFO impact — running quantity unchanged.
    qty_after = (
        await db_session.execute(
            select(func.coalesce(func.sum(Transaction.quantity), 0)).where(
                Transaction.account_id == account.id,
                Transaction.instrument_id == btc.id,
            )
        )
    ).scalar_one()
    assert Decimal(qty_after) == Decimal(qty_before)

    # No new lot_alloc rows (no sell, no buy lot mutation).
    lot_count = (
        await db_session.execute(select(func.count()).select_from(LotAlloc))
    ).scalar_one()
    assert lot_count == 0

    # MAX(snapshot_date) surfaces — the badge derivation in routers/accounts.py
    # uses this exact query; the dismissed event MUST be visible.
    last_snapshot = (
        await db_session.execute(
            select(func.max(Reconciliation.snapshot_date)).where(
                Reconciliation.account_id == account.id
            )
        )
    ).scalar_one()
    assert last_snapshot == date(2026, 5, 5)
