"""FIFO lot-allocation integrity across transaction mutations.

Characterization tests pin behavior that must NOT change (a sell's alloc + its
realized gain, delete-sell re-opens the buy). Regression tests cover the bugs
Plan 001 fixes: deleting/editing a buy now re-runs FIFO for the whole pair, and
editing a sell re-matches every sell of the pair in FIFO order.

Sells are inserted via the session + match_lots_for_sell (bare sells are
rejected at POST /api/transactions), then mutated through the PUT/DELETE API.
All instruments are EUR (fx=1) so realized_gain = (sell_price - buy_price) * qty.
"""
from __future__ import annotations

from datetime import date
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
from app.services.fifo import match_lots_for_sell
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def client_and_session():
    original_password = cfg_module.settings.app_password
    cfg_module.settings.app_password = "test-password-123"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    # foreign_keys=ON so ON DELETE CASCADE on lot_alloc fires.
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


async def _create_account_and_instrument(client: AsyncClient):
    acct = (
        await client.post(
            "/api/accounts", json={"name": "TestAccount", "account_type": "broker"}
        )
    ).json()
    inst = (
        await client.post(
            "/api/instruments",
            json={
                "symbol": "BTC",
                "name": "Bitcoin",
                "instrument_type": "crypto",
                "base_currency": "EUR",
                "price_source": "coingecko",
            },
        )
    ).json()
    return acct["id"], inst["id"]


async def _buy(client, acct_id, inst_id, *, day, qty, price):
    resp = await client.post(
        "/api/transactions",
        json={
            "account_id": acct_id,
            "instrument_id": inst_id,
            "txn_type": "buy",
            "date": f"2026-01-{day:02d}",
            "quantity": qty,
            "unit_price": price,
            "price_currency": "EUR",
            "fee_eur": "0",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _make_sell(maker, acct_id, inst_id, *, day, qty, price):
    """Insert a sell + run FIFO via the session (POST rejects bare sells)."""
    async with maker() as session:
        sell = Transaction(
            account_id=acct_id,
            instrument_id=inst_id,
            txn_type="sell",
            date=date(2026, 1, day),
            quantity=Decimal(qty) * Decimal("-1"),
            unit_price=Decimal(price),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
        )
        session.add(sell)
        await session.flush()
        await match_lots_for_sell(session, sell)
        await session.commit()
        return sell.id


async def _allocs_for_sell(maker, sell_id):
    async with maker() as session:
        res = await session.execute(
            select(LotAlloc).where(LotAlloc.sell_txn_id == sell_id)
        )
        return res.scalars().all()


async def _all_alloc_tuples(maker):
    async with maker() as session:
        res = await session.execute(select(LotAlloc))
        return {
            (a.sell_txn_id, a.buy_txn_id, a.quantity) for a in res.scalars().all()
        }


# ---------------------------------------------------------------------------
# Step 1: characterization, behavior that must NOT change
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_char_sell_creates_single_alloc_with_expected_gain(client_and_session):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy = await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    sell_id = await _make_sell(maker, acct_id, inst_id, day=3, qty="40", price="20")

    allocs = await _allocs_for_sell(maker, sell_id)
    assert len(allocs) == 1
    assert allocs[0].buy_txn_id == buy["id"]
    assert allocs[0].quantity == Decimal("40")
    assert allocs[0].realized_gain_eur == Decimal("400")  # (20 - 10) * 40


@pytest.mark.asyncio
async def test_char_delete_sell_reopens_buy(client_and_session):
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy = await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    sell_id = await _make_sell(maker, acct_id, inst_id, day=3, qty="40", price="20")

    resp = await client.delete(f"/api/transactions/{sell_id}")
    assert resp.status_code == 204

    # All allocs released; the buy is fully open again.
    assert await _all_alloc_tuples(maker) == set()
    async with maker() as session:
        res = await session.execute(
            select(LotAlloc).where(LotAlloc.buy_txn_id == buy["id"])
        )
        assert res.scalars().all() == []


# ---------------------------------------------------------------------------
# Step 5: regression tests for the bugs this plan fixes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_consumed_buy_blocked_with_422(client_and_session):
    """buy(100), sell(40), sell(30); deleting the buy 422s (sells uncoverable)
    and rolls everything back."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy = await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    await _make_sell(maker, acct_id, inst_id, day=3, qty="40", price="20")
    await _make_sell(maker, acct_id, inst_id, day=5, qty="30", price="25")

    before = await _all_alloc_tuples(maker)
    resp = await client.delete(f"/api/transactions/{buy['id']}")
    assert resp.status_code == 422, resp.text

    # Allocations unchanged and the buy is NOT soft-deleted (rollback fired).
    assert await _all_alloc_tuples(maker) == before
    async with maker() as session:
        txn = await session.get(Transaction, buy["id"])
        assert txn.deleted_at is None


@pytest.mark.asyncio
async def test_delete_buy_covered_by_other_lot(client_and_session):
    """buy A(100), buy B(100), sell(40) consumes A. Deleting A re-matches the
    sell onto B and recomputes realized gain against B's price."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy_a = await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    buy_b = await _buy(client, acct_id, inst_id, day=2, qty="100", price="12")
    sell_id = await _make_sell(maker, acct_id, inst_id, day=3, qty="40", price="20")

    allocs = await _allocs_for_sell(maker, sell_id)
    assert {a.buy_txn_id for a in allocs} == {buy_a["id"]}

    resp = await client.delete(f"/api/transactions/{buy_a['id']}")
    assert resp.status_code == 204, resp.text

    allocs = await _allocs_for_sell(maker, sell_id)
    assert len(allocs) == 1
    assert allocs[0].buy_txn_id == buy_b["id"]
    assert allocs[0].quantity == Decimal("40")
    assert allocs[0].realized_gain_eur == Decimal("320")  # (20 - 12) * 40


@pytest.mark.asyncio
async def test_edit_buy_price_updates_realized_gain(client_and_session):
    """buy(100)@10, sell(40)@20 -> realized 400. Editing the buy price to 15
    recomputes the alloc's realized gain to 200."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy = await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    sell_id = await _make_sell(maker, acct_id, inst_id, day=3, qty="40", price="20")

    allocs = await _allocs_for_sell(maker, sell_id)
    assert allocs[0].realized_gain_eur == Decimal("400")

    resp = await client.put(
        f"/api/transactions/{buy['id']}", json={"unit_price": "15"}
    )
    assert resp.status_code == 200, resp.text

    allocs = await _allocs_for_sell(maker, sell_id)
    assert len(allocs) == 1
    assert allocs[0].buy_txn_id == buy["id"]
    assert allocs[0].realized_gain_eur == Decimal("200")  # (20 - 15) * 40


@pytest.mark.asyncio
async def test_enlarge_earlier_sell_succeeds(client_and_session):
    """buy(100), sell(40) day 3, sell(30) day 5. Enlarging the day-3 sell to 60
    succeeds under FIFO (total 90 <= 100); the old self-only rematch mis-rejected
    it. Both sells' allocs sum to 90."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy = await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    s1 = await _make_sell(maker, acct_id, inst_id, day=3, qty="40", price="20")
    s2 = await _make_sell(maker, acct_id, inst_id, day=5, qty="30", price="25")

    resp = await client.put(f"/api/transactions/{s1}", json={"quantity": "60"})
    assert resp.status_code == 200, resp.text

    a1 = await _allocs_for_sell(maker, s1)
    a2 = await _allocs_for_sell(maker, s2)
    assert sum(a.quantity for a in a1) == Decimal("60")
    assert sum(a.quantity for a in a2) == Decimal("30")
    assert sum(a.quantity for a in a1) + sum(a.quantity for a in a2) == Decimal("90")
    assert {a.buy_txn_id for a in a1 + a2} == {buy["id"]}


@pytest.mark.asyncio
async def test_over_enlarge_earlier_sell_rejected(client_and_session):
    """Enlarging the day-3 sell to 80 (total 110 > 100) 422s and rolls back:
    original allocations and the sell's stored quantity are unchanged."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    s1 = await _make_sell(maker, acct_id, inst_id, day=3, qty="40", price="20")
    await _make_sell(maker, acct_id, inst_id, day=5, qty="30", price="25")

    before = await _all_alloc_tuples(maker)
    resp = await client.put(f"/api/transactions/{s1}", json={"quantity": "80"})
    assert resp.status_code == 422, resp.text

    assert await _all_alloc_tuples(maker) == before
    async with maker() as session:
        txn = await session.get(Transaction, s1)
        assert txn.quantity == Decimal("-40")  # rollback kept the original


@pytest.mark.asyncio
async def test_backdate_sell_recomputes_preserving_fifo(client_and_session):
    """buy A(100)@10 day 1, buy B(100)@20 day 4, sell(150) day 5 consumes all of
    A + 50 of B. Back-dating the sell to day 2 triggers a full recompute (rows
    replaced) that preserves FIFO order and identical realized gains."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy_a = await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    buy_b = await _buy(client, acct_id, inst_id, day=4, qty="100", price="20")
    sell_id = await _make_sell(maker, acct_id, inst_id, day=5, qty="150", price="30")

    before = await _allocs_for_sell(maker, sell_id)
    before_ids = {a.id for a in before}
    before_by_buy = {a.buy_txn_id: a.quantity for a in before}
    assert before_by_buy[buy_a["id"]] == Decimal("100")
    assert before_by_buy[buy_b["id"]] == Decimal("50")

    resp = await client.put(
        f"/api/transactions/{sell_id}", json={"date": "2026-01-02"}
    )
    assert resp.status_code == 200, resp.text

    after = await _allocs_for_sell(maker, sell_id)
    after_ids = {a.id for a in after}
    # Recompute fired: the old alloc rows were deleted and recreated.
    assert before_ids.isdisjoint(after_ids)

    after_by_buy = {a.buy_txn_id: a for a in after}
    assert after_by_buy[buy_a["id"]].quantity == Decimal("100")
    assert after_by_buy[buy_b["id"]].quantity == Decimal("50")
    # Realized gains recomputed identically: A:(30-10)*100=2000, B:(30-20)*50=500.
    assert after_by_buy[buy_a["id"]].realized_gain_eur == Decimal("2000")
    assert after_by_buy[buy_b["id"]].realized_gain_eur == Decimal("500")


# ---------------------------------------------------------------------------
# Delete-all-then-rematch regression: rematching disposals one at a time let a
# later, not-yet-rematched sell's stale allocs count as consumption, producing
# non-FIFO lot attribution and wrong per-sell realized_gain_eur. Pair totals
# stayed conserved, so nothing errored. These pin exact attribution + gains.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_buy_reattributes_all_later_sells_fifo(client_and_session):
    """Repro 1: A(50)@10, B(50)@20, C(50)@30; S1(50)@40 (->A), S2(50)@40 (->B).
    Deleting A must re-run FIFO for BOTH sells together: S1->B (gain 1000),
    S2->C (gain 500). The one-at-a-time rematch left S1 on C and S2 on B."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy_a = await _buy(client, acct_id, inst_id, day=1, qty="50", price="10")
    buy_b = await _buy(client, acct_id, inst_id, day=2, qty="50", price="20")
    buy_c = await _buy(client, acct_id, inst_id, day=3, qty="50", price="30")
    s1 = await _make_sell(maker, acct_id, inst_id, day=4, qty="50", price="40")
    s2 = await _make_sell(maker, acct_id, inst_id, day=5, qty="50", price="40")

    # Precondition: S1 consumed A, S2 consumed B.
    assert {a.buy_txn_id for a in await _allocs_for_sell(maker, s1)} == {buy_a["id"]}
    assert {a.buy_txn_id for a in await _allocs_for_sell(maker, s2)} == {buy_b["id"]}

    resp = await client.delete(f"/api/transactions/{buy_a['id']}")
    assert resp.status_code == 204, resp.text

    a1 = await _allocs_for_sell(maker, s1)
    a2 = await _allocs_for_sell(maker, s2)
    assert len(a1) == 1
    assert a1[0].buy_txn_id == buy_b["id"]
    assert a1[0].quantity == Decimal("50")
    assert a1[0].realized_gain_eur == Decimal("1000")  # (40 - 20) * 50
    assert len(a2) == 1
    assert a2[0].buy_txn_id == buy_c["id"]
    assert a2[0].quantity == Decimal("50")
    assert a2[0].realized_gain_eur == Decimal("500")  # (40 - 30) * 50


@pytest.mark.asyncio
async def test_enlarge_earlier_sell_reattributes_later_sell_fifo(client_and_session):
    """Repro 2: A(50)@10, B(50)@20; S1(30)@30, S2(50)@30. Enlarging S1 to 50 must
    give S1={A:50} gain 1000 and S2={B:50} gain 500. The one-at-a-time rematch
    left S1={A:30,B:20} gain 800 because S2's stale A-alloc still counted."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy_a = await _buy(client, acct_id, inst_id, day=1, qty="50", price="10")
    buy_b = await _buy(client, acct_id, inst_id, day=2, qty="50", price="20")
    s1 = await _make_sell(maker, acct_id, inst_id, day=3, qty="30", price="30")
    s2 = await _make_sell(maker, acct_id, inst_id, day=4, qty="50", price="30")

    resp = await client.put(f"/api/transactions/{s1}", json={"quantity": "50"})
    assert resp.status_code == 200, resp.text

    a1 = await _allocs_for_sell(maker, s1)
    a2 = await _allocs_for_sell(maker, s2)
    assert len(a1) == 1
    assert a1[0].buy_txn_id == buy_a["id"]
    assert a1[0].quantity == Decimal("50")
    assert a1[0].realized_gain_eur == Decimal("1000")  # (30 - 10) * 50
    assert len(a2) == 1
    assert a2[0].buy_txn_id == buy_b["id"]
    assert a2[0].quantity == Decimal("50")
    assert a2[0].realized_gain_eur == Decimal("500")  # (30 - 20) * 50


@pytest.mark.asyncio
async def test_shrink_buy_reattributes_later_sells_fifo(client_and_session):
    """Repro 3: A(100)@10, B(100)@20; S1(30)@30, S2(50)@30. Shrinking A to 60 must
    give S1={A:30} gain 600 and S2={A:30,B:20} gains 600+200. The one-at-a-time
    rematch left S1={A:10,B:20} gain 400 because S2's stale A:50 still counted."""
    client, maker = client_and_session
    acct_id, inst_id = await _create_account_and_instrument(client)
    buy_a = await _buy(client, acct_id, inst_id, day=1, qty="100", price="10")
    buy_b = await _buy(client, acct_id, inst_id, day=2, qty="100", price="20")
    s1 = await _make_sell(maker, acct_id, inst_id, day=3, qty="30", price="30")
    s2 = await _make_sell(maker, acct_id, inst_id, day=4, qty="50", price="30")

    resp = await client.put(
        f"/api/transactions/{buy_a['id']}", json={"quantity": "60"}
    )
    assert resp.status_code == 200, resp.text

    a1 = await _allocs_for_sell(maker, s1)
    a2 = {a.buy_txn_id: a for a in await _allocs_for_sell(maker, s2)}
    assert len(a1) == 1
    assert a1[0].buy_txn_id == buy_a["id"]
    assert a1[0].quantity == Decimal("30")
    assert a1[0].realized_gain_eur == Decimal("600")  # (30 - 10) * 30
    assert set(a2) == {buy_a["id"], buy_b["id"]}
    assert a2[buy_a["id"]].quantity == Decimal("30")
    assert a2[buy_a["id"]].realized_gain_eur == Decimal("600")  # (30 - 10) * 30
    assert a2[buy_b["id"]].quantity == Decimal("20")
    assert a2[buy_b["id"]].realized_gain_eur == Decimal("200")  # (30 - 20) * 20
