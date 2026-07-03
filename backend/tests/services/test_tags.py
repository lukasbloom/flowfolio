"""Tests for tags + holding-tag CRUD API.

Covers all 11 behavior requirements for this API.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, HoldingTag, Instrument, Transaction
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def authed_client():
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
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post("/api/auth/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _create_account_and_instrument(maker):
    """Helper: create an Account and Instrument in the DB, return their ids."""
    from datetime import date
    from decimal import Decimal

    async with maker() as s:
        account = Account(name="Test Account", account_type="broker", currency="EUR")
        instrument = Instrument(
            symbol="BTC",
            name="Bitcoin",
            instrument_type="crypto",
            risk_level="High",
            base_currency="EUR",
            price_source="coingecko",
        )
        s.add_all([account, instrument])
        await s.flush()
        txn = Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type="buy",
            date=date.today(),
            quantity=Decimal("1"),
            unit_price=Decimal("30000"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("30000"),
        )
        s.add(txn)
        await s.commit()
        return account.id, instrument.id


# ---------------------------------------------------------------------------
# Test 1: GET /api/tags on empty DB returns { "tags": [] }
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_tags_empty_returns_empty_list(authed_client):
    client, _ = authed_client

    response = await client.get("/api/tags")

    assert response.status_code == 200
    assert response.json() == {"tags": []}


# ---------------------------------------------------------------------------
# Test 2: POST /api/tags creates tag; subsequent GET returns it
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_post_tag_creates_and_get_returns_it(authed_client):
    client, _ = authed_client

    create_resp = await client.post("/api/tags", json={"name": "growth"})
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["name"] == "growth"
    assert "id" in body

    get_resp = await client.get("/api/tags")
    assert get_resp.status_code == 200
    tags = get_resp.json()["tags"]
    assert len(tags) == 1
    assert tags[0]["name"] == "growth"
    assert tags[0]["id"] == body["id"]


# ---------------------------------------------------------------------------
# Test 3: POST /api/tags with duplicate name → HTTP 409
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_post_tag_duplicate_returns_409(authed_client):
    client, _ = authed_client

    first = await client.post("/api/tags", json={"name": "growth"})
    assert first.status_code == 201

    second = await client.post("/api/tags", json={"name": "growth"})
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Test 4: POST /api/tags with empty name → HTTP 422 (Pydantic min_length)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_post_tag_empty_name_returns_422(authed_client):
    client, _ = authed_client

    response = await client.post("/api/tags", json={"name": ""})

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Test 5: POST /api/holdings/{account_id}/{instrument_id}/tags → 204; row exists in DB
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_apply_holding_tag_creates_row(authed_client):
    client, maker = authed_client

    account_id, instrument_id = await _create_account_and_instrument(maker)
    tag_resp = await client.post("/api/tags", json={"name": "long-term"})
    assert tag_resp.status_code == 201
    tag_id = tag_resp.json()["id"]

    apply_resp = await client.post(
        f"/api/holdings/{account_id}/{instrument_id}/tags",
        json={"tag_id": tag_id},
    )
    assert apply_resp.status_code == 204

    # Verify row exists in DB
    async with maker() as s:
        row = await s.get(HoldingTag, (account_id, instrument_id, tag_id))
        assert row is not None


# ---------------------------------------------------------------------------
# Test 6: Idempotent apply — calling twice yields only one row
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_apply_holding_tag_is_idempotent(authed_client):
    client, maker = authed_client

    account_id, instrument_id = await _create_account_and_instrument(maker)
    tag_resp = await client.post("/api/tags", json={"name": "value"})
    assert tag_resp.status_code == 201
    tag_id = tag_resp.json()["id"]

    for _ in range(2):
        resp = await client.post(
            f"/api/holdings/{account_id}/{instrument_id}/tags",
            json={"tag_id": tag_id},
        )
        assert resp.status_code == 204

    # Only one row should exist
    async with maker() as s:
        result = await s.execute(
            select(HoldingTag).where(
                HoldingTag.account_id == account_id,
                HoldingTag.instrument_id == instrument_id,
                HoldingTag.tag_id == tag_id,
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# Test 7: DELETE /api/holdings/{account_id}/{instrument_id}/tags/{tag_id} → 204; row removed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_remove_holding_tag_deletes_row(authed_client):
    client, maker = authed_client

    account_id, instrument_id = await _create_account_and_instrument(maker)
    tag_resp = await client.post("/api/tags", json={"name": "short-term"})
    assert tag_resp.status_code == 201
    tag_id = tag_resp.json()["id"]

    await client.post(
        f"/api/holdings/{account_id}/{instrument_id}/tags",
        json={"tag_id": tag_id},
    )

    del_resp = await client.delete(
        f"/api/holdings/{account_id}/{instrument_id}/tags/{tag_id}"
    )
    assert del_resp.status_code == 204

    async with maker() as s:
        row = await s.get(HoldingTag, (account_id, instrument_id, tag_id))
        assert row is None


# ---------------------------------------------------------------------------
# Test 8: DELETE /api/tags/{tag_id} → 204; tag AND holding_tag rows removed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_tag_cascades_holding_tag_rows(authed_client):
    client, maker = authed_client

    account_id, instrument_id = await _create_account_and_instrument(maker)
    tag_resp = await client.post("/api/tags", json={"name": "risky"})
    assert tag_resp.status_code == 201
    tag_id = tag_resp.json()["id"]

    # Apply the tag
    await client.post(
        f"/api/holdings/{account_id}/{instrument_id}/tags",
        json={"tag_id": tag_id},
    )

    # Delete the tag
    del_resp = await client.delete(f"/api/tags/{tag_id}")
    assert del_resp.status_code == 204

    # Tag gone from GET
    get_resp = await client.get("/api/tags")
    assert all(t["id"] != tag_id for t in get_resp.json()["tags"])

    # holding_tag row also gone
    async with maker() as s:
        row = await s.get(HoldingTag, (account_id, instrument_id, tag_id))
        assert row is None


# ---------------------------------------------------------------------------
# Test 9: DELETE /api/tags/{nonexistent_id} → 404
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_nonexistent_tag_returns_404(authed_client):
    client, _ = authed_client

    response = await client.delete("/api/tags/does-not-exist-id")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Test 10: POST /api/holdings/{nonexistent_account}/{instrument_id}/tags → 422
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_apply_holding_tag_nonexistent_account_returns_422(authed_client):
    client, maker = authed_client

    _, instrument_id = await _create_account_and_instrument(maker)
    tag_resp = await client.post("/api/tags", json={"name": "speculative"})
    assert tag_resp.status_code == 201
    tag_id = tag_resp.json()["id"]

    response = await client.post(
        f"/api/holdings/nonexistent-account-id/{instrument_id}/tags",
        json={"tag_id": tag_id},
    )
    # FK violation → 422 (documented choice: 422)
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Test 11: GET /api/tags returns tags sorted alphabetically
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_tags_sorted_alphabetically(authed_client):
    client, _ = authed_client

    for name in ["zebra", "apple", "mango"]:
        resp = await client.post("/api/tags", json={"name": name})
        assert resp.status_code == 201

    get_resp = await client.get("/api/tags")
    names = [t["name"] for t in get_resp.json()["tags"]]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# TagResponse.holdings_count enrichment
#
# Behavior contract:
#   - list_tags_with_counts() on empty table returns []
#   - When tags exist with no holdings, every count is 0
#   - holdings_count == 1 after attaching a tag to ONE (account, instrument) pair
#   - holdings_count == 2 after attaching a tag to TWO different pairs
#   - GET /api/tags response includes holdings_count integer per tag
#   - Existing TagFilterChip-style consumers (read only id+name) keep working
# ---------------------------------------------------------------------------


async def _create_second_account(maker, instrument_id):
    """Create a SECOND account holding the SAME instrument so we can verify
    holdings_count counts distinct (account, instrument) HoldingTag rows."""
    from datetime import date
    from decimal import Decimal

    async with maker() as s:
        account = Account(
            name="Second Test Account",
            account_type="broker",
            currency="EUR",
        )
        s.add(account)
        await s.flush()
        txn = Transaction(
            account_id=account.id,
            instrument_id=instrument_id,
            txn_type="buy",
            date=date.today(),
            quantity=Decimal("0.5"),
            unit_price=Decimal("31000"),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal("15500"),
        )
        s.add(txn)
        await s.commit()
        return account.id


@pytest.mark.asyncio
async def test_holdings_count_service_returns_empty_for_empty_db(authed_client):
    """list_tags_with_counts on empty tag table returns []."""
    from app.services.tags import list_tags_with_counts

    _, maker = authed_client
    async with maker() as s:
        rows = await list_tags_with_counts(s)
    assert rows == []


@pytest.mark.asyncio
async def test_holdings_count_returns_zero_for_unattached_tags(authed_client):
    """GET /api/tags surfaces holdings_count=0 when no holding-tag rows exist."""
    client, _ = authed_client

    for name in ("growth", "spec"):
        r = await client.post("/api/tags", json={"name": name})
        assert r.status_code == 201

    r = await client.get("/api/tags")
    assert r.status_code == 200
    body = r.json()
    assert [t["name"] for t in body["tags"]] == ["growth", "spec"]
    assert all(t["holdings_count"] == 0 for t in body["tags"])


@pytest.mark.asyncio
async def test_holdings_count_increments_when_tag_attached_to_one_holding(
    authed_client,
):
    """Attaching a tag to ONE (account, instrument) pair sets holdings_count=1
    for that tag while sibling tags remain at 0."""
    client, maker = authed_client

    account_id, instrument_id = await _create_account_and_instrument(maker)

    growth_resp = await client.post("/api/tags", json={"name": "growth"})
    spec_resp = await client.post("/api/tags", json={"name": "spec"})
    assert growth_resp.status_code == 201
    assert spec_resp.status_code == 201
    growth_id = growth_resp.json()["id"]

    apply_resp = await client.post(
        f"/api/holdings/{account_id}/{instrument_id}/tags",
        json={"tag_id": growth_id},
    )
    assert apply_resp.status_code == 204

    r = await client.get("/api/tags")
    assert r.status_code == 200
    by_name = {t["name"]: t for t in r.json()["tags"]}
    assert by_name["growth"]["holdings_count"] == 1
    assert by_name["spec"]["holdings_count"] == 0


@pytest.mark.asyncio
async def test_holdings_count_counts_distinct_account_pairs(authed_client):
    """holdings_count == number of distinct (account, instrument) HoldingTag rows
    pointing at the tag — proven by attaching the same tag to the same instrument
    held in TWO different accounts."""
    client, maker = authed_client

    account_a_id, instrument_id = await _create_account_and_instrument(maker)
    account_b_id = await _create_second_account(maker, instrument_id)

    tag_resp = await client.post("/api/tags", json={"name": "growth"})
    assert tag_resp.status_code == 201
    tag_id = tag_resp.json()["id"]

    for acct_id in (account_a_id, account_b_id):
        apply = await client.post(
            f"/api/holdings/{acct_id}/{instrument_id}/tags",
            json={"tag_id": tag_id},
        )
        assert apply.status_code == 204

    r = await client.get("/api/tags")
    assert r.status_code == 200
    by_name = {t["name"]: t for t in r.json()["tags"]}
    assert by_name["growth"]["holdings_count"] == 2


@pytest.mark.asyncio
async def test_holdings_count_payload_is_backwards_additive(authed_client):
    """Existing consumers that read only {id, name} continue to work — the new
    field has a default, never breaks shape, and id+name keep their semantics."""
    client, _ = authed_client

    create_resp = await client.post("/api/tags", json={"name": "additive"})
    assert create_resp.status_code == 201

    r = await client.get("/api/tags")
    assert r.status_code == 200
    tag = r.json()["tags"][0]
    # All previous fields still present and correctly typed
    assert isinstance(tag["id"], str) and len(tag["id"]) > 0
    assert tag["name"] == "additive"
    # New field is present and is an int
    assert "holdings_count" in tag
    assert isinstance(tag["holdings_count"], int)
