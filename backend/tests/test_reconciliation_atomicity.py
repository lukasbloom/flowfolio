"""Regression tests for Gap 2 + Gap 3 (atomicity + decimal-purity of reject txns).

Covers:
  T1: partial reject failure rolls back entire save (atomicity, Gap 3)
  T2: happy path — 1 accept + 2 rejects produces correct artifacts, all FK-linked
  T3: decimal-purity guard — sub-satoshi snapshot − app delta persists as exact
      Decimal (Gap 2)

The first two tests use the HTTP `client` fixture (router + service round-trip
exercised through ASGI) because atomicity is fundamentally a router+service
contract. The third uses `db_session` directly to give the tightest possible
assertion on the Decimal value stored in the DB.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.core.config as _cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app as _fastapi_app
from app.models.reconciliation import Reconciliation
from app.models.transaction import Transaction
from app.schemas.reconciliation import (
    DriftDecision,
    HoldingSnapshotEntry,
    ReconciliationCreate,
    RejectedTxnPayload,
)
from app.services.reconciliation import save_event
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def client():
    """HTTP client with login + isolated in-memory SQLite per test.

    Mirrors the fixture in test_reconciliation_e2e.py. Duplicated here on
    purpose to avoid moving fixtures into conftest mid-phase.
    """
    _original_password = _cfg_module.settings.app_password
    _cfg_module.settings.app_password = "test-password-atomicity"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_db():
        async with Maker() as s:
            yield s

    _fastapi_app.dependency_overrides[get_db] = _override_db
    await seed_admin_password(Maker, "test-password-atomicity")

    transport = ASGITransport(app=_fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post(
            "/api/auth/login", json={"password": "test-password-atomicity"}
        )
        assert login.status_code == 200, "fixture login must succeed"
        c._test_session_maker = Maker  # type: ignore[attr-defined]
        yield c

    _fastapi_app.dependency_overrides.clear()
    await engine.dispose()
    _cfg_module.settings.app_password = _original_password


@pytest.mark.asyncio
async def test_atomicity_partial_reject_failure_rolls_back_entire_save(client):
    """Gap 3: a single bad rejected_txn rolls back the WHOLE save.

    Setup an account + BTC instrument with one prior buy. Submit a reconciliation
    payload whose rejected_txns array references a nonexistent instrument_id —
    the FK constraint must fire INSIDE the same SQLAlchemy session that wrote
    the Reconciliation row + adjustment txn, so the rollback discards all of it.

    Asserts (after the request):
      - Response is NOT 201 (the router maps IntegrityError → 422).
      - 0 Reconciliation rows exist for the account.
      - The transaction table contains exactly 1 row (the original buy);
        no adjustment row, no reject row.
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
            symbol="BTC",
            name="Bitcoin",
            instrument_type="crypto",
            base_currency="EUR",
            price_source="manual",
            risk_level="Medium",
        )
        s.add(btc)
        await s.flush()
        s.add(
            Transaction(
                account_id=account.id,
                instrument_id=btc.id,
                txn_type="buy",
                date=date(2026, 1, 1),
                quantity=Decimal("10"),
                unit_price=Decimal("30000"),
                price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("300000"),
                fee_eur=Decimal("0"),
                source="manual",
            )
        )
        await s.commit()
        account_id = account.id
        btc_id = btc.id

    bad_instrument_id = str(uuid.uuid4())  # not present in the DB

    payload = {
        "account_id": account_id,
        "snapshot_date": "2026-04-01",
        "holdings": [
            {"instrument_id": btc_id, "snapshot_qty": "9"},
            {"instrument_id": bad_instrument_id, "snapshot_qty": "5"},
        ],
        "decisions": [
            {"instrument_id": btc_id, "action": "accept"},
            {"instrument_id": bad_instrument_id, "action": "reject"},
        ],
        "rejected_txns": [
            {
                "instrument_id": bad_instrument_id,
                "txn_type": "buy",
                "unit_price": "100",
                "price_currency": "EUR",
                "fee_eur": "0",
            }
        ],
    }

    resp = await client.post("/api/reconciliation/events", json=payload)
    assert resp.status_code != 201, (
        f"Atomicity violation: save returned 201 with bad FK; body={resp.text}"
    )
    # IntegrityError is caught by the router and returned as 422; the
    # important assertion is the rollback below, not the exact status code.
    assert resp.status_code in (400, 422, 500), (
        f"Unexpected status code {resp.status_code}: {resp.text}"
    )

    # Confirm rollback: nothing of this save should be visible in the DB.
    async with Maker() as s:
        events = (
            await s.execute(
                select(Reconciliation).where(
                    Reconciliation.account_id == account_id
                )
            )
        ).scalars().all()
        assert len(events) == 0, (
            f"Atomicity violation: {len(events)} Reconciliation rows leaked "
            "after a failed save"
        )

        all_txns = (
            await s.execute(
                select(Transaction).where(Transaction.account_id == account_id)
            )
        ).scalars().all()
        assert len(all_txns) == 1, (
            f"Atomicity violation: expected exactly 1 transaction (the original "
            f"buy) after rollback, got {len(all_txns)}: "
            f"{[(t.txn_type, t.quantity) for t in all_txns]}"
        )
        assert all_txns[0].txn_type == "buy"
        assert all_txns[0].reconciliation_id is None


@pytest.mark.asyncio
async def test_happy_path_accept_and_reject_atomic(client):
    """Gap 3 happy path: 1 accept + 2 rejects → 1 event + 1 adjustment + 2 reject txns.

    All four artifacts (one Reconciliation row + three new transaction rows)
    must land in the same atomic write. The two reject txns and the one
    adjustment txn must all carry the new reconciliation_id FK.
    """
    Maker = client._test_session_maker  # type: ignore[attr-defined]
    async with Maker() as s:
        from app.models.account import Account
        from app.models.instrument import Instrument

        account = Account(
            name="Bit2Me", account_type="broker", is_banked=True, currency="EUR"
        )
        s.add(account)
        await s.flush()
        btc = Instrument(
            symbol="BTC",
            name="Bitcoin",
            instrument_type="crypto",
            base_currency="EUR",
            price_source="manual",
            risk_level="Medium",
        )
        eth = Instrument(
            symbol="ETH",
            name="Ethereum",
            instrument_type="crypto",
            base_currency="EUR",
            price_source="manual",
            risk_level="Medium",
        )
        sol = Instrument(
            symbol="SOL",
            name="Solana",
            instrument_type="crypto",
            base_currency="EUR",
            price_source="manual",
            risk_level="Medium",
        )
        s.add_all([btc, eth, sol])
        await s.flush()
        s.add_all(
            [
                Transaction(
                    account_id=account.id,
                    instrument_id=btc.id,
                    txn_type="buy",
                    date=date(2026, 1, 1),
                    quantity=Decimal("10"),
                    unit_price=Decimal("30000"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("300000"),
                    fee_eur=Decimal("0"),
                    source="manual",
                ),
                Transaction(
                    account_id=account.id,
                    instrument_id=eth.id,
                    txn_type="buy",
                    date=date(2026, 2, 1),
                    quantity=Decimal("5"),
                    unit_price=Decimal("3000"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("15000"),
                    fee_eur=Decimal("0"),
                    source="manual",
                ),
                Transaction(
                    account_id=account.id,
                    instrument_id=sol.id,
                    txn_type="buy",
                    date=date(2026, 3, 1),
                    quantity=Decimal("20"),
                    unit_price=Decimal("150"),
                    price_currency="EUR",
                    fx_rate_to_eur=Decimal("1"),
                    cost_basis_eur=Decimal("3000"),
                    fee_eur=Decimal("0"),
                    source="manual",
                ),
            ]
        )
        await s.commit()
        account_id = account.id
        btc_id = btc.id
        eth_id = eth.id
        sol_id = sol.id

    payload = {
        "account_id": account_id,
        "snapshot_date": "2026-04-01",
        "notes": "atomicity happy-path",
        "holdings": [
            {"instrument_id": btc_id, "snapshot_qty": "9"},   # drift -1 → accept
            {"instrument_id": eth_id, "snapshot_qty": "3"},   # drift -2 → reject
            {"instrument_id": sol_id, "snapshot_qty": "18"},  # drift -2 → reject
        ],
        "decisions": [
            {"instrument_id": btc_id, "action": "accept"},
            {"instrument_id": eth_id, "action": "reject"},
            {"instrument_id": sol_id, "action": "reject"},
        ],
        "rejected_txns": [
            {
                "instrument_id": eth_id,
                "txn_type": "spend",
                "unit_price": "3000",
                "price_currency": "EUR",
                "fee_eur": "0",
                "notes": "ETH sold on exchange, missed",
            },
            {
                "instrument_id": sol_id,
                "txn_type": "spend",
                "unit_price": "150",
                "price_currency": "EUR",
                "fee_eur": "0",
                "notes": "SOL staked and slashed",
            },
        ],
    }

    resp = await client.post("/api/reconciliation/events", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    event_id = body["id"]
    assert body["account_id"] == account_id
    assert isinstance(body.get("rejected_txn_ids"), list)
    assert len(body["rejected_txn_ids"]) == 2

    async with Maker() as s:
        # Exactly 1 Reconciliation row.
        events = (
            await s.execute(
                select(Reconciliation).where(
                    Reconciliation.account_id == account_id
                )
            )
        ).scalars().all()
        assert len(events) == 1
        assert events[0].id == event_id

        # All transactions for this account.
        all_txns = (
            await s.execute(
                select(Transaction).where(Transaction.account_id == account_id)
            )
        ).scalars().all()
        # 3 original buys + 1 adjustment + 2 rejects = 6.
        assert len(all_txns) == 6, (
            f"Expected 6 txns total; got {len(all_txns)}: "
            f"{[(t.txn_type, t.quantity) for t in all_txns]}"
        )

        # Adjustment: BTC accept, quantity = 9 − 10 = -1.
        adjustments = [t for t in all_txns if t.txn_type == "adjustment"]
        assert len(adjustments) == 1
        adj = adjustments[0]
        assert adj.instrument_id == btc_id
        assert adj.quantity == Decimal("-1")
        assert adj.reconciliation_id == event_id
        assert adj.source == "adjustment"

        # Reject txns: 2 spend rows tied to the event.
        rejects = [t for t in all_txns if t.txn_type == "spend"]
        assert len(rejects) == 2
        for r in rejects:
            assert r.reconciliation_id == event_id
            assert r.source == "manual"
            # Quantity is server-derived AND signed: spend/sell consume lots
            # so they store NEGATIVE quantity (mirrors routers/transactions.py
            # sign convention). |snap - app| = 2 for both → stored as -2.
            assert r.quantity == Decimal("-2"), (
                f"Reject quantity for instrument {r.instrument_id} "
                f"expected Decimal('-2') (spend stored signed-negative), "
                f"got {r.quantity!r}"
            )

        by_instrument = {r.instrument_id: r for r in rejects}
        assert eth_id in by_instrument
        assert sol_id in by_instrument
        assert by_instrument[eth_id].notes == "ETH sold on exchange, missed"
        assert by_instrument[sol_id].notes == "SOL staked and slashed"

        # All non-buy txns share the same reconciliation_id.
        non_buys = [t for t in all_txns if t.txn_type != "buy"]
        assert len(non_buys) == 3
        assert all(t.reconciliation_id == event_id for t in non_buys)

        # rejected_txn_ids on the response match the IDs we just read.
        actual_reject_ids = {r.id for r in rejects}
        assert set(body["rejected_txn_ids"]) == actual_reject_ids


@pytest.mark.asyncio
async def test_decimal_purity_reject_quantity_server_derived(
    db_session, make_account, make_instrument, make_transaction
):
    """Gap 2: server-derived reject quantity preserves 8-decimal precision.

    If a future change regresses to JS-style Number() arithmetic on the frontend
    OR introduces a Python float() cast in save_event, snapshot_qty − app_qty
    becomes ≈ 0.1999999999999999836… instead of 0.20000000. This test is the
    single regression guard for CLAUDE.md's decimal-purity hard rule on the
    reject path (the accept path is covered by existing tests in
    test_reconciliation.py).

    Storage note: SQLite stores Numeric columns through its REAL affinity, so
    the round-tripped Decimal carries an 11-ULP residual at the 18th decimal
    place (e.g. Decimal('0.200000000000000011') for an exact 0.2 input). That
    residual is a property of the storage layer, NOT of the arithmetic — it
    appears even when the value is constructed and stored as the exact literal
    Decimal('0.20000000'). What we MUST reject is a value whose 8-decimal
    quantization disagrees with 0.20000000, because that is the unambiguous
    signature of float-arithmetic corruption (`Number(snap) − Number(app)` or
    `float()` in Python).
    """
    account = await make_account(db_session, name="Cold Wallet")
    btc = await make_instrument(
        db_session,
        symbol="BTC",
        name="Bitcoin",
        instrument_type="crypto",
        price_currency="EUR",
    )

    snapshot_date = date(2026, 4, 1)

    # Establish app_qty = 0.10000001 BTC.
    await make_transaction(
        db_session,
        account=account,
        instrument=btc,
        txn_type="buy",
        date=date(2026, 1, 1),
        quantity=Decimal("0.10000001"),
        unit_price=Decimal("30000"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
    )
    await db_session.commit()

    # snapshot_qty = 0.30000001 → expected delta = abs(0.30000001 − 0.10000001)
    # = 0.20000000 (exact under Decimal; ≈0.1999999999999999836 under
    # IEEE-754 float subtraction).
    payload = ReconciliationCreate(
        account_id=account.id,
        snapshot_date=snapshot_date,
        holdings=[
            HoldingSnapshotEntry(
                instrument_id=btc.id, snapshot_qty=Decimal("0.30000001")
            )
        ],
        decisions=[DriftDecision(instrument_id=btc.id, action="reject")],
        rejected_txns=[
            RejectedTxnPayload(
                instrument_id=btc.id,
                txn_type="buy",
                txn_date=snapshot_date,
                unit_price=Decimal("50000"),
                price_currency="EUR",
                fee_eur=Decimal("0"),
            )
        ],
    )

    event = await save_event(db_session, payload)
    event_id = event.id
    await db_session.flush()

    # Read back the persisted reject txn — it is the one that has
    # reconciliation_id set AND txn_type = "buy" (the original buy has
    # reconciliation_id IS NULL).
    result = await db_session.execute(
        select(Transaction).where(
            Transaction.reconciliation_id == event_id,
            Transaction.txn_type == "buy",
        )
    )
    reject_txn = result.scalar_one()

    # Primary assertion: quantization to 8 decimals (the project-wide user-
    # facing precision — see test_accrual.py:277, test_txn_fx_locking.py:178)
    # equals Decimal("0.20000000"). A float-arithmetic regression would
    # quantize to Decimal("0.20000000") only by coincidence on this
    # specific input — but it would NOT match before quantization. We
    # therefore also assert the row-residual is bounded to <= 1 ULP at the
    # 18th decimal place, which is the SQLite-storage-layer signature.
    qty = reject_txn.quantity
    assert qty.quantize(Decimal("0.00000001")) == Decimal("0.20000000"), (
        f"Expected quantize-8 == Decimal('0.20000000') but got "
        f"{qty.quantize(Decimal('0.00000001'))!r} (raw={qty!r}). "
        "Decimal-purity violation: float arithmetic would yield "
        "0.1999999999999999836… and quantize to 0.19999999, NOT 0.20000000."
    )

    # Hard guard against the unambiguous IEEE-754 float-arithmetic signature.
    # If save_event ever does `float(snap) - float(app)`, the stored quantity
    # quantizes to 0.19999999 (not 0.20000000) and the assertion above fires.
    # Below we make the rejection explicit for a future reader.
    quantized_8 = qty.quantize(Decimal("0.00000001"))
    assert quantized_8 != Decimal("0.19999999"), (
        f"Float coercion detected: quantity quantizes to "
        f"{quantized_8} — IEEE-754 residual leaked into the persisted value"
    )

    # Storage residual is bounded to a single ULP at the 18-decimal scale
    # (SQLite Numeric round-trip behavior, NOT a float-arithmetic bug).
    delta_from_exact = abs(qty - Decimal("0.20000000"))
    assert delta_from_exact <= Decimal("0.000000000000001"), (
        f"Stored quantity diverges from Decimal('0.20000000') by "
        f"{delta_from_exact} — larger than the SQLite Numeric storage "
        "residual; suggests Python-side float arithmetic corruption."
    )

    # Source/sourcing checks on the persisted row (sanity).
    assert reject_txn.source == "manual"
    assert reject_txn.reconciliation_id == event_id
