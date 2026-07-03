"""Reconciliation tests."""
import pytest
from datetime import date
from decimal import Decimal

from app.models.reconciliation import Reconciliation
from app.services.reconciliation import build_preview, save_event
from app.schemas.reconciliation import (
    ReconciliationCreate,
    HoldingSnapshotEntry,
    DriftDecision,
)


@pytest.mark.asyncio
async def test_preview_returns_holdings_at_date(
    db_session, make_account, make_instrument, make_transaction
):
    """Preview returns one row per non-zero holding at snapshot_date."""
    account = await make_account(db_session, name="XTB")
    btc = await make_instrument(db_session, symbol="BTC", name="Bitcoin", instrument_type="crypto", price_currency="USD")
    eth = await make_instrument(db_session, symbol="ETH", name="Ethereum", instrument_type="crypto", price_currency="USD")
    await make_transaction(db_session, account=account, instrument=btc, txn_type="buy", date=date(2026, 1, 1), quantity=Decimal("10"), unit_price=Decimal("40000"), price_currency="USD", fx_rate_to_eur=Decimal("0.92"))
    await make_transaction(db_session, account=account, instrument=eth, txn_type="buy", date=date(2026, 3, 1), quantity=Decimal("5"), unit_price=Decimal("3000"), price_currency="USD", fx_rate_to_eur=Decimal("0.92"))
    await db_session.commit()

    rows = await build_preview(db_session, account_id=account.id, snapshot_date=date(2026, 2, 1))

    symbols = {r.instrument_symbol for r in rows}
    assert symbols == {"BTC"}, f"Expected only BTC at 2026-02-01, got {symbols}"
    btc_row = next(r for r in rows if r.instrument_symbol == "BTC")
    assert btc_row.app_qty == Decimal("10")


@pytest.mark.asyncio
async def test_accept_writes_adjustment_and_recomputes_fifo(
    db_session, make_account, make_instrument, make_transaction
):
    """Back-dated negative adjustment recomputes FIFO for later sells.

    Setup: buy 10 BTC on 2026-01-01, sell 3 BTC on 2026-04-15 (FIFO matches against
    the 10 BTC lot). Snapshot on 2026-04-01 says 2 BTC; app computes 7. Server-derived
    delta = snapshot − app = 2 − 7 = -5 → adjustment quantity = -5.
    """
    from app.models.transaction import Transaction
    from app.models.lot_alloc import LotAlloc
    from sqlalchemy import select

    account = await make_account(db_session, name="XTB")
    btc = await make_instrument(db_session, symbol="BTC", name="Bitcoin", instrument_type="crypto", price_currency="USD")

    buy = await make_transaction(db_session, account=account, instrument=btc, txn_type="buy", date=date(2026, 1, 1), quantity=Decimal("10"), unit_price=Decimal("40000"), price_currency="USD", fx_rate_to_eur=Decimal("0.92"))
    sell = await make_transaction(db_session, account=account, instrument=btc, txn_type="sell", date=date(2026, 4, 15), quantity=Decimal("3"), unit_price=Decimal("60000"), price_currency="USD", fx_rate_to_eur=Decimal("0.92"))
    await db_session.commit()

    # Sanity: existing FIFO matched the 3 BTC sell against the 10 BTC buy.
    result = await db_session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == sell.id))
    allocs_before = result.scalars().all()
    assert len(allocs_before) == 1
    assert allocs_before[0].buy_txn_id == buy.id
    assert allocs_before[0].quantity == Decimal("3")

    payload = ReconciliationCreate(
        account_id=account.id,
        snapshot_date=date(2026, 4, 1),
        notes=None,
        holdings=[HoldingSnapshotEntry(instrument_id=btc.id, snapshot_qty=Decimal("2"))],
        decisions=[DriftDecision(instrument_id=btc.id, action="accept")],
    )
    await save_event(db_session, payload)

    adj_result = await db_session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.instrument_id == btc.id,
            Transaction.txn_type == "adjustment",
        )
    )
    adj = adj_result.scalar_one()
    assert adj.quantity == Decimal("-5")
    assert adj.date == date(2026, 4, 1)
    assert adj.source == "adjustment"
    assert adj.reconciliation_id is not None

    # FIFO must have been recomputed for the later sell.
    result_after = await db_session.execute(select(LotAlloc).where(LotAlloc.sell_txn_id == sell.id))
    allocs_after = result_after.scalars().all()
    assert sum(a.quantity for a in allocs_after) == Decimal("3"), "Sell still consumes 3 BTC total after recompute"


@pytest.mark.asyncio
async def test_reject_links_real_txn_to_event(
    db_session, make_account, make_instrument
):
    """Real txn (created via the extended POST /api/transactions schema) carries reconciliation_id FK."""
    from app.models.transaction import Transaction
    from sqlalchemy import select

    account = await make_account(db_session, name="XTB")
    btc = await make_instrument(db_session, symbol="BTC", name="Bitcoin", instrument_type="crypto", price_currency="USD")

    event = Reconciliation(
        account_id=account.id,
        snapshot_date=date(2026, 5, 5),
        notes=None,
        holdings_snapshot=[],
    )
    db_session.add(event)
    await db_session.flush()

    # Simulate the real txn the Reject drawer would post (with reconciliation_id).
    txn = Transaction(
        account_id=account.id,
        instrument_id=btc.id,
        txn_type="buy",
        date=date(2026, 5, 5),
        quantity=Decimal("2"),
        unit_price=Decimal("60000"),
        price_currency="USD",
        fx_rate_to_eur=Decimal("0.92"),
        fee_eur=Decimal("0"),
        notes="from reject-drift",
        source="manual",
        reconciliation_id=event.id,
    )
    db_session.add(txn)
    await db_session.commit()

    result = await db_session.execute(select(Transaction).where(Transaction.id == txn.id))
    loaded = result.scalar_one()
    assert loaded.reconciliation_id == event.id


@pytest.mark.asyncio
async def test_dismiss_writes_zero_qty_adjustment(
    db_session, make_account, make_instrument, make_transaction
):
    """Dismiss: dismissing drift writes a zero-qty adjustment row with notes='dismissed: ...'."""
    from app.models.transaction import Transaction
    from sqlalchemy import select

    account = await make_account(db_session, name="XTB")
    btc = await make_instrument(db_session, symbol="BTC", name="Bitcoin", instrument_type="crypto", price_currency="USD")
    await make_transaction(db_session, account=account, instrument=btc, txn_type="buy", date=date(2026, 1, 1), quantity=Decimal("1"), unit_price=Decimal("40000"), price_currency="USD", fx_rate_to_eur=Decimal("0.92"))
    await db_session.commit()

    payload = ReconciliationCreate(
        account_id=account.id,
        snapshot_date=date(2026, 5, 5),
        notes=None,
        holdings=[HoldingSnapshotEntry(instrument_id=btc.id, snapshot_qty=Decimal("1.05"))],
        decisions=[DriftDecision(instrument_id=btc.id, action="dismiss", dismiss_reason="broker rounding noise")],
    )
    await save_event(db_session, payload)

    result = await db_session.execute(
        select(Transaction).where(
            Transaction.account_id == account.id,
            Transaction.instrument_id == btc.id,
            Transaction.txn_type == "adjustment",
        )
    )
    adj = result.scalar_one()
    assert adj.quantity == Decimal("0")
    assert "dismissed:" in (adj.notes or "")
    assert "broker rounding noise" in (adj.notes or "")


@pytest.mark.asyncio
async def test_last_reconciled_max(db_session, make_account):
    """Badge: MAX(snapshot_date) per account is the source of truth."""
    from sqlalchemy import select, func

    account = await make_account(db_session, name="XTB")
    for snap in (date(2026, 3, 1), date(2026, 4, 15), date(2026, 4, 1)):
        db_session.add(Reconciliation(
            account_id=account.id,
            snapshot_date=snap,
            notes=None,
            holdings_snapshot=[],
        ))
    await db_session.commit()

    stmt = select(func.max(Reconciliation.snapshot_date)).where(Reconciliation.account_id == account.id)
    result = await db_session.execute(stmt)
    assert result.scalar_one() == date(2026, 4, 15)


# --- Endpoint tests ---------------------------------------------------------
# Use the same in-memory client pattern as tests/test_api_accounts.py so the
# existing AuthMiddleware is exercised end-to-end.

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import config as _cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app as _fastapi_app


@pytest_asyncio.fixture
async def client():
    """HTTP client with login + isolated in-memory SQLite per test."""
    _original_password = _cfg_module.settings.app_password
    _cfg_module.settings.app_password = "test-password-recon"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_db():
        async with Maker() as s:
            yield s

    _fastapi_app.dependency_overrides[get_db] = _override_db
    await seed_admin_password(Maker, "test-password-recon")

    transport = ASGITransport(app=_fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post("/api/auth/login", json={"password": "test-password-recon"})
        assert login.status_code == 200, "fixture login must succeed"
        # Yield BOTH client and a session-maker so tests can seed DB rows
        # against the same engine the app sees.
        c._test_session_maker = Maker  # type: ignore[attr-defined]
        yield c

    _fastapi_app.dependency_overrides.clear()
    await engine.dispose()
    _cfg_module.settings.app_password = _original_password


from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402  (used by fixture)
from tests.conftest import seed_admin_password


@pytest.mark.asyncio
async def test_get_preview_endpoint(client):
    """GET /api/reconciliation/preview returns the diff payload."""
    Maker = client._test_session_maker  # type: ignore[attr-defined]
    async with Maker() as s:
        from app.models.account import Account
        from app.models.instrument import Instrument
        from app.models.transaction import Transaction

        account = Account(name="XTB", account_type="broker", is_banked=True, currency="EUR")
        s.add(account)
        await s.flush()
        btc = Instrument(
            symbol="BTC", name="Bitcoin", instrument_type="crypto",
            base_currency="USD", price_source="manual", risk_level="Medium",
        )
        s.add(btc)
        await s.flush()
        s.add(Transaction(
            account_id=account.id, instrument_id=btc.id, txn_type="buy",
            date=date(2026, 1, 1), quantity=Decimal("10"),
            unit_price=Decimal("40000"), price_currency="USD",
            fx_rate_to_eur=Decimal("0.92"),
            cost_basis_eur=(Decimal("10") * Decimal("40000")) / Decimal("0.92"),
            fee_eur=Decimal("0"), source="manual",
        ))
        await s.commit()
        account_id = account.id

    resp = await client.get(
        f"/api/reconciliation/preview"
        f"?account_id={account_id}&snapshot_date=2026-02-01"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["account_id"] == account_id
    assert body["snapshot_date"] == "2026-02-01"
    assert any(r["instrument_symbol"] == "BTC" for r in body["rows"])
    assert body["last_reconciled_date"] is None


@pytest.mark.asyncio
async def test_post_events_endpoint(client):
    """POST /api/reconciliation/events writes event + adjustment txn."""
    from sqlalchemy import select as _select
    Maker = client._test_session_maker  # type: ignore[attr-defined]
    async with Maker() as s:
        from app.models.account import Account
        from app.models.instrument import Instrument
        from app.models.transaction import Transaction

        account = Account(name="XTB", account_type="broker", is_banked=True, currency="EUR")
        s.add(account)
        await s.flush()
        btc = Instrument(
            symbol="BTC", name="Bitcoin", instrument_type="crypto",
            base_currency="USD", price_source="manual", risk_level="Medium",
        )
        s.add(btc)
        await s.flush()
        s.add(Transaction(
            account_id=account.id, instrument_id=btc.id, txn_type="buy",
            date=date(2026, 1, 1), quantity=Decimal("10"),
            unit_price=Decimal("40000"), price_currency="USD",
            fx_rate_to_eur=Decimal("0.92"),
            cost_basis_eur=(Decimal("10") * Decimal("40000")) / Decimal("0.92"),
            fee_eur=Decimal("0"), source="manual",
        ))
        await s.commit()
        account_id = account.id
        btc_id = btc.id

    # Note: NO delta_qty in the decisions payload — server derives it from
    # snapshot_qty (9.5) minus the computed app_qty (10) using Python Decimal.
    payload = {
        "account_id": account_id,
        "snapshot_date": "2026-04-01",
        "notes": "phase-5 endpoint test",
        "holdings": [{"instrument_id": btc_id, "snapshot_qty": "9.5"}],
        "decisions": [{
            "instrument_id": btc_id,
            "action": "accept",
        }],
    }
    resp = await client.post("/api/reconciliation/events", json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["account_id"] == account_id
    event_id = body["id"]

    # Confirm adjustment row landed with reconciliation_id FK populated.
    async with Maker() as s:
        from app.models.transaction import Transaction
        adj_result = await s.execute(
            _select(Transaction).where(
                Transaction.account_id == account_id,
                Transaction.txn_type == "adjustment",
                Transaction.reconciliation_id == event_id,
            )
        )
        adj = adj_result.scalar_one()
        assert adj.quantity == Decimal("-0.5")
        assert adj.source == "adjustment"


@pytest.mark.asyncio
async def test_transactions_passthrough_reconciliation_id(client):
    """Router-level: POST /api/transactions persists reconciliation_id FK."""
    from sqlalchemy import select as _select
    Maker = client._test_session_maker  # type: ignore[attr-defined]
    async with Maker() as s:
        from app.models.account import Account
        from app.models.instrument import Instrument

        account = Account(name="XTB", account_type="broker", is_banked=True, currency="EUR")
        s.add(account)
        await s.flush()
        btc = Instrument(
            symbol="BTC", name="Bitcoin", instrument_type="crypto",
            base_currency="USD", price_source="manual", risk_level="Medium",
        )
        s.add(btc)
        await s.flush()
        # Pre-create a reconciliation event so the FK has a target.
        event = Reconciliation(
            account_id=account.id,
            snapshot_date=date(2026, 5, 5),
            notes=None,
            holdings_snapshot=[],
        )
        s.add(event)
        await s.commit()
        account_id = account.id
        btc_id = btc.id
        event_id = event.id

    resp = await client.post("/api/transactions", json={
        "account_id": account_id,
        "instrument_id": btc_id,
        "txn_type": "buy",
        "date": "2026-05-05",
        "quantity": "2",
        "unit_price": "60000",
        "price_currency": "USD",
        "fx_rate_to_eur": "0.92",
        "fee_eur": "0",
        "notes": "from reject-drift",
        "reconciliation_id": event_id,
    })
    assert resp.status_code == 201, resp.text
    txn_id = resp.json()["id"]

    async with Maker() as s:
        from app.models.transaction import Transaction
        loaded = (await s.execute(
            _select(Transaction).where(Transaction.id == txn_id)
        )).scalar_one()
        assert loaded.reconciliation_id == event_id


@pytest.mark.asyncio
async def test_accounts_carries_last_reconciled_date(client):
    """Badge: GET /api/accounts row carries last_reconciled_date = MAX(snapshot_date)."""
    Maker = client._test_session_maker  # type: ignore[attr-defined]
    async with Maker() as s:
        from app.models.account import Account

        a1 = Account(name="A1", account_type="broker", is_banked=True, currency="EUR")
        a2 = Account(name="A2", account_type="broker", is_banked=True, currency="EUR")
        s.add_all([a1, a2])
        await s.flush()
        # a1 has reconciliations; a2 has none.
        for snap in (date(2026, 3, 1), date(2026, 4, 15), date(2026, 4, 1)):
            s.add(Reconciliation(
                account_id=a1.id, snapshot_date=snap, notes=None, holdings_snapshot=[],
            ))
        await s.commit()
        a1_id = a1.id
        a2_id = a2.id

    resp = await client.get("/api/accounts")
    assert resp.status_code == 200, resp.text
    rows = {row["id"]: row for row in resp.json()}
    assert rows[a1_id]["last_reconciled_date"] == "2026-04-15"
    assert rows[a2_id]["last_reconciled_date"] is None


@pytest.mark.asyncio
async def test_post_events_endpoint_404_on_missing_account(client):
    """Router-level safety: POST events with bogus account_id → 404."""
    payload = {
        "account_id": "does-not-exist",
        "snapshot_date": "2026-04-01",
        "notes": None,
        "holdings": [],
        "decisions": [],
    }
    resp = await client.post("/api/reconciliation/events", json=payload)
    assert resp.status_code == 404, resp.text


@pytest.mark.asyncio
async def test_get_preview_endpoint_422_future_date(client):
    """Router-level safety: future snapshot_date → 422."""
    Maker = client._test_session_maker  # type: ignore[attr-defined]
    async with Maker() as s:
        from app.models.account import Account
        a = Account(name="XTB", account_type="broker", is_banked=True, currency="EUR")
        s.add(a)
        await s.commit()
        account_id = a.id

    resp = await client.get(
        f"/api/reconciliation/preview?account_id={account_id}&snapshot_date=2099-01-01"
    )
    assert resp.status_code == 422, resp.text
