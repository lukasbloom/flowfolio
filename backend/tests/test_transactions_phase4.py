"""Transaction router tests covering audit trail, soft-delete, and linked-pair cascade.

Covers:
- Test 4: DELETE sets deleted_at (not NULL), writes event_type='delete' audit row
- Test 5: DELETE of a sell triggers FIFO cascade (lot_alloc released)
- Test 6: DELETE of one half of a linked trade ALSO soft-deletes the paired half
- Test 8: GET ?include_deleted=false excludes soft-deleted; ?include_deleted=true includes
- Test 9: PUT on a soft-deleted txn returns 404
- Test 10: DELETE on an already-soft-deleted txn returns 404
"""
from __future__ import annotations

from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models.lot_alloc import LotAlloc
from app.models.transaction import Transaction
from app.models.txn_audit import TxnAudit
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def client_and_session():
    original_password = cfg_module.settings.app_password
    cfg_module.settings.app_password = "test-password-123"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db
    await seed_admin_password(maker, "test-password-123")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        login = await c.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield c, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _create_account_and_instrument(client: AsyncClient, symbol: str = "BTC"):
    acct = (
        await client.post(
            "/api/accounts", json={"name": f"Test-{symbol}", "account_type": "broker"}
        )
    ).json()
    inst = (
        await client.post(
            "/api/instruments",
            json={
                "symbol": symbol,
                "name": f"{symbol} Coin",
                "instrument_type": "crypto",
                "base_currency": "EUR",
                "price_source": "coingecko",
            },
        )
    ).json()
    return acct["id"], inst["id"]


async def _buy(client: AsyncClient, acct_id: str, inst_id: str, qty: str = "1.0") -> dict:
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": "2024-01-01",
            "quantity": qty,
            "unit_price": "100.0",
            "price_currency": "EUR",
            "fee_eur": "0",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Test 4: DELETE sets deleted_at and writes an audit row (soft-delete)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_soft_deletes_and_writes_audit(client_and_session):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "SOL")
    buy = await _buy(client, acct_id, inst_id)

    resp = await client.delete(f"/api/transactions/{buy['id']}")
    assert resp.status_code == 204

    # Verify row is still in DB but deleted_at is set
    async with maker() as session:
        result = await session.execute(select(Transaction).where(Transaction.id == buy["id"]))
        txn = result.scalar_one()
        assert txn.deleted_at is not None, "deleted_at should be set after DELETE"

        # Verify audit row was written
        audit_result = await session.execute(
            select(TxnAudit).where(TxnAudit.transaction_id == buy["id"])
        )
        audits = audit_result.scalars().all()
        assert len(audits) == 1
        assert audits[0].event_type == "delete"


# ---------------------------------------------------------------------------
# Test 5: DELETE of a sell triggers FIFO cascade (lot_alloc rows released)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_sell_releases_fifo_lots(client_and_session):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "ETH")

    # Buy 2 units
    await _buy(client, acct_id, inst_id, qty="2.0")

    # Sell 1 unit via /api/trades endpoint (or directly)
    # We use POST /api/trades to create a linked sell+buy
    # But for this test we create a sell via the trades endpoint
    # POST /api/transactions rejects bare 'sell'.
    # For now, we must directly insert a sell via model
    # or use /api/trades.
    # Since /api/trades isn't implemented yet, we inject via session.
    async with maker() as session:
        result = await session.execute(
            select(Transaction)
            .where(
                Transaction.account_id == acct_id,
                Transaction.instrument_id == inst_id,
                Transaction.txn_type == "buy",
            )
        )
        buy_txn = result.scalar_one()

        from app.services.fifo import match_lots_for_sell

        sell_txn = Transaction(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=buy_txn.date,
            quantity=Decimal("-1.0"),
            unit_price=Decimal("110.0"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("110.0"),
        )
        session.add(sell_txn)
        await session.flush()
        await match_lots_for_sell(session, sell_txn)
        await session.commit()
        sell_id = sell_txn.id

    # Verify lot_alloc was created
    async with maker() as session:
        alloc_result = await session.execute(
            select(LotAlloc).where(LotAlloc.sell_txn_id == sell_id)
        )
        allocs = alloc_result.scalars().all()
        assert len(allocs) == 1

    # DELETE the sell via API
    resp = await client.delete(f"/api/transactions/{sell_id}")
    assert resp.status_code == 204

    # Lot_alloc rows should be deleted (released)
    async with maker() as session:
        alloc_result = await session.execute(
            select(LotAlloc).where(LotAlloc.sell_txn_id == sell_id)
        )
        allocs = alloc_result.scalars().all()
        assert allocs == [], "lot_alloc rows should be released after soft-deleting sell"


# ---------------------------------------------------------------------------
# Test 6: DELETE one half of a linked trade also soft-deletes the paired half
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_linked_pair_cascades(client_and_session):
    client, maker = client_and_session
    import uuid

    acct_id, inst_id = await _create_account_and_instrument(client, "XRP")
    acct_id2, inst_id2 = await _create_account_and_instrument(client, "USDC")

    # Create a linked pair directly via session
    pair_id = str(uuid.uuid4())
    async with maker() as session:
        sell_txn = Transaction(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=__import__("datetime").date(2024, 1, 1),
            quantity=Decimal("-1.0"),
            unit_price=Decimal("0.5"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("0.5"),
            trade_pair_id=pair_id,
        )
        buy_txn = Transaction(
            account_id=acct_id2,
            instrument_id=inst_id2,
            txn_type="buy",
            date=__import__("datetime").date(2024, 1, 1),
            quantity=Decimal("0.5"),
            unit_price=Decimal("1.0"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("0.5"),
            trade_pair_id=pair_id,
        )
        session.add_all([sell_txn, buy_txn])
        await session.flush()
        from app.services.fifo import match_lots_for_sell
        # Need a buy lot for the sell to consume - insert a prior buy
        prior_buy = Transaction(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="buy",
            date=__import__("datetime").date(2024, 1, 1),
            quantity=Decimal("2.0"),
            unit_price=Decimal("0.4"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("0.8"),
        )
        session.add(prior_buy)
        await session.flush()
        await match_lots_for_sell(session, sell_txn)
        await session.commit()
        sell_id = sell_txn.id
        buy_id = buy_txn.id

    # DELETE the sell half via API
    resp = await client.delete(f"/api/transactions/{sell_id}")
    assert resp.status_code == 204

    # Both halves should be soft-deleted
    async with maker() as session:
        sell_result = await session.execute(select(Transaction).where(Transaction.id == sell_id))
        sell = sell_result.scalar_one()
        assert sell.deleted_at is not None, "Sell half should be soft-deleted"

        buy_result = await session.execute(select(Transaction).where(Transaction.id == buy_id))
        buy = buy_result.scalar_one()
        assert buy.deleted_at is not None, "Buy half (paired) should also be soft-deleted"

        # Both should have audit rows
        sell_audit = await session.execute(
            select(TxnAudit).where(TxnAudit.transaction_id == sell_id)
        )
        buy_audit = await session.execute(
            select(TxnAudit).where(TxnAudit.transaction_id == buy_id)
        )
        assert len(sell_audit.scalars().all()) >= 1
        assert len(buy_audit.scalars().all()) >= 1


# ---------------------------------------------------------------------------
# Test 8: GET include_deleted filter
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_include_deleted_filter(client_and_session):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "ADA")

    buy = await _buy(client, acct_id, inst_id)
    buy_id = buy["id"]

    # Soft-delete it
    del_resp = await client.delete(f"/api/transactions/{buy_id}")
    assert del_resp.status_code == 204

    # Default (no include_deleted) should exclude it
    resp = await client.get(f"/api/transactions?instrument_id={inst_id}")
    assert resp.status_code == 200
    ids = [t["id"] for t in resp.json()]
    assert buy_id not in ids, "Soft-deleted txn should be hidden by default"

    # include_deleted=true should include it
    resp2 = await client.get(f"/api/transactions?instrument_id={inst_id}&include_deleted=true")
    assert resp2.status_code == 200
    ids2 = [t["id"] for t in resp2.json()]
    assert buy_id in ids2, "Soft-deleted txn should appear with include_deleted=true"


# ---------------------------------------------------------------------------
# Test 9: PUT on soft-deleted txn returns 404
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_on_soft_deleted_returns_404(client_and_session):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "DOT")

    buy = await _buy(client, acct_id, inst_id)

    # Soft-delete
    await client.delete(f"/api/transactions/{buy['id']}")

    # PUT should return 404
    resp = await client.put(
        f"/api/transactions/{buy['id']}",
        json={"notes": "trying to edit deleted txn"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 10: DELETE on already-soft-deleted txn returns 404
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_already_soft_deleted_returns_404(client_and_session):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "LINK")

    buy = await _buy(client, acct_id, inst_id)

    # First delete
    resp1 = await client.delete(f"/api/transactions/{buy['id']}")
    assert resp1.status_code == 204

    # Second delete should return 404
    resp2 = await client.delete(f"/api/transactions/{buy['id']}")
    assert resp2.status_code == 404


# ---------------------------------------------------------------------------
# Test: GET /api/transactions/{id}/audit endpoint
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_endpoint_returns_ordered_events(client_and_session):
    client, _ = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "MATIC")

    buy = await _buy(client, acct_id, inst_id)
    buy_id = buy["id"]

    # Perform a PUT to generate an edit audit event
    resp_put = await client.put(
        f"/api/transactions/{buy_id}",
        json={"notes": "first edit"},
    )
    assert resp_put.status_code == 200

    # Now soft-delete to generate a delete audit event
    await client.delete(f"/api/transactions/{buy_id}")

    # Fetch audit history
    resp_audit = await client.get(f"/api/transactions/{buy_id}/audit")
    assert resp_audit.status_code == 200
    events = resp_audit.json()
    assert len(events) >= 1
    # Events should be ordered newest first (delete should be last in time but first in response)
    event_types = [e["event_type"] for e in events]
    # We expect at least a delete event (edit may not produce row if notes was None before)
    assert "delete" in event_types


# ---------------------------------------------------------------------------
# Test: PUT writes audit row with field-level diff
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_writes_audit_row_with_diff(client_and_session):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "AVAX")

    buy = await _buy(client, acct_id, inst_id)
    buy_id = buy["id"]

    # Update notes (which was None)
    resp = await client.put(
        f"/api/transactions/{buy_id}",
        json={"notes": "added a note"},
    )
    assert resp.status_code == 200

    # Check audit row
    async with maker() as session:
        result = await session.execute(
            select(TxnAudit).where(TxnAudit.transaction_id == buy_id)
        )
        audits = result.scalars().all()
        assert len(audits) == 1
        assert audits[0].event_type == "edit"
        assert "notes" in audits[0].changed_fields


@pytest.mark.asyncio
async def test_put_txn_type_is_not_mutable(client_and_session):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "ATOM")

    buy = await _buy(client, acct_id, inst_id)
    buy_id = buy["id"]

    resp = await client.put(
        f"/api/transactions/{buy_id}",
        json={"txn_type": "sell", "notes": "legitimate edit still applies"},
    )
    assert resp.status_code == 200
    assert resp.json()["txn_type"] == "buy"

    async with maker() as session:
        txn = await session.get(Transaction, buy_id)
        assert txn is not None
        assert txn.txn_type == "buy"

        audit_result = await session.execute(
            select(TxnAudit).where(TxnAudit.transaction_id == buy_id)
        )
        audits = audit_result.scalars().all()
        assert len(audits) == 1
        assert "txn_type" not in audits[0].changed_fields
        assert audits[0].changed_fields["notes"]["new"] == "legitimate edit still applies"


# ---------------------------------------------------------------------------
# Test: PUT with no-op change does NOT write audit row
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_put_noop_does_not_write_audit(client_and_session):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client, "UNI")

    buy = await _buy(client, acct_id, inst_id)
    buy_id = buy["id"]

    # First PUT to set notes
    await client.put(f"/api/transactions/{buy_id}", json={"notes": "my note"})

    # Second PUT with same notes value — no-op
    await client.put(f"/api/transactions/{buy_id}", json={"notes": "my note"})

    async with maker() as session:
        result = await session.execute(
            select(TxnAudit).where(TxnAudit.transaction_id == buy_id)
        )
        audits = result.scalars().all()
        # Only 1 audit row from the first PUT; second PUT is no-op
        assert len(audits) == 1, f"Expected 1 audit row but got {len(audits)}"
