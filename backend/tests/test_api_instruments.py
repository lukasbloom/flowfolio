"""Tests for /api/instruments CRUD endpoints.

Tests authenticate via AuthMiddleware so the cookie session lets requests pass.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, Instrument, PriceQuote, Transaction
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def client():
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
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest_asyncio.fixture
async def client_with_maker():
    """Variant of the `client` fixture that also yields the session maker so tests
    can seed Account / Instrument / Transaction rows directly via SQLAlchemy
    (matching the seeding pattern in test_networth_router.py)."""
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


@pytest.mark.asyncio
async def test_create_instrument_crypto(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "BTC",
            "name": "Bitcoin",
            "instrument_type": "crypto",
            "base_currency": "USD",
            "price_source": "coingecko",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["instrument_type"] == "crypto"


# ---------------------------------------------------------------------------
# Duplicate-symbol → clean 409
#
# A second POST with the same (symbol, instrument_type) collides with the
# Instrument `UniqueConstraint("symbol", "instrument_type")`. Before the guard
# this surfaced as an unhandled 500 IntegrityError; the router now catches it
# and returns a clean 409 with a human-readable `detail`. The frontend already
# routes 409 → inline `serverError`.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_instrument_duplicate_symbol_returns_409(client):
    """Second POST with the same (symbol, type) → 409 (not 500) with a detail."""
    payload = {
        "symbol": "BTC",
        "name": "Bitcoin",
        "instrument_type": "crypto",
        "base_currency": "USD",
        "price_source": "coingecko",
    }
    first = await client.post("/api/instruments", json=payload)
    assert first.status_code == 201, first.text

    second = await client.post("/api/instruments", json=payload)
    assert second.status_code == 409, second.text
    detail = second.json().get("detail")
    assert isinstance(detail, str) and detail, "409 must carry a non-empty detail string"


@pytest.mark.asyncio
async def test_update_instrument_rename_to_duplicate_symbol_returns_409(client):
    """PUT renaming an instrument's symbol onto an existing (symbol, type)
    collision → 409 (not 500), via the same guard on update_instrument."""
    a = await client.post(
        "/api/instruments",
        json={
            "symbol": "AAA",
            "name": "Alpha",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert a.status_code == 201, a.text
    b = await client.post(
        "/api/instruments",
        json={
            "symbol": "BBB",
            "name": "Beta",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert b.status_code == 201, b.text
    b_id = b.json()["id"]

    # Rename BBB → AAA (same instrument_type) → collides with the AAA row.
    rename = await client.put(
        f"/api/instruments/{b_id}",
        json={
            "symbol": "AAA",
            "name": "Beta",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert rename.status_code == 409, rename.text
    detail = rename.json().get("detail")
    assert isinstance(detail, str) and detail


@pytest.mark.asyncio
async def test_invalid_instrument_type(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "X",
            "name": "X",
            "instrument_type": "invalid_type",
            "base_currency": "EUR",
        },
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Immediacy + one-submit price regressions
#
# These pin the BACKEND contracts the frontend relies on:
#  - POST returns the full InstrumentResponse (with an `id`) and the
#    new instrument is immediately present in GET /api/instruments — so the
#    frontend's optimistic ["instruments"] prepend reflects a real, listable
#    row (selectable in +Add without a manual refresh).
#  - a manual-priced instrument plus a POST /api/prices/manual with
#    its id (exactly what the create form now does in one submit) persists a
#    price the latest-quote read returns.
# Assertions stay on the API contract (status codes + body), not FE cache
# internals — the frontend has no component test runner.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_created_instrument_is_immediately_listable(client):
    """Contract: a freshly POSTed instrument returns a full
    InstrumentResponse and appears in a subsequent GET /api/instruments."""
    create = await client.post(
        "/api/instruments",
        json={
            "symbol": "IMMED",
            "name": "Immediately Listable",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    # Full InstrumentResponse the frontend optimistically prepends.
    assert body.get("id"), "201 response must include a non-empty id"
    assert body["symbol"] == "IMMED"
    assert body["instrument_type"] == "stock"
    new_id = body["id"]

    listing = await client.get("/api/instruments")
    assert listing.status_code == 200, listing.text
    listed_ids = {row["id"] for row in listing.json()}
    assert new_id in listed_ids, (
        "newly created instrument must be immediately listable/selectable "
        "without any special step"
    )


@pytest.mark.asyncio
async def test_one_submit_manual_price_persists(client):
    """Contract: create a manual-priced instrument, then write a
    manual price via /api/prices/manual with that id (what the create form
    now does in one submit), and assert the price persisted and reads back."""
    create = await client.post(
        "/api/instruments",
        json={
            "symbol": "VWRL2",
            "name": "Vanguard FTSE All-World (manual)",
            "instrument_type": "fund",
            "base_currency": "EUR",
            "price_source": "ft",
        },
    )
    assert create.status_code == 201, create.text
    inst_id = create.json()["id"]

    price_resp = await client.post(
        "/api/prices/manual",
        json={
            "instrument_id": inst_id,
            "date": "2026-04-30",
            "price": "123.45",
            "currency": "EUR",
            "note": None,
        },
    )
    assert price_resp.status_code == 201, price_resp.text
    written = price_resp.json()
    assert written["instrument_id"] == inst_id
    assert written["price"] == "123.45"
    assert written["source"] == "manual"

    # The written price reads back via the latest-quote endpoint.
    history = await client.get(
        f"/api/prices/{inst_id}/history", params={"source": "manual"}
    )
    assert history.status_code == 200, history.text
    rows = history.json()
    assert any(r["price"] == "123.45" and r["date"] == "2026-04-30" for r in rows), (
        "the one-submit manual price must be persisted and readable"
    )


# --- held=true filter regression suite ---
#
# The five cases below pin the behavior of the new `?held=true` query parameter
# on GET /api/instruments. The default branch (no param) and the explicit
# held=false branch must remain byte-equivalent to today, so existing callers
# (notably the missing-price warning hint in NetWorthChart.tsx that relies on
# the full instrument list) are unaffected.

EUR_BUY_KW = dict(
    txn_type="buy",
    unit_price=Decimal("1"),
    price_currency="EUR",
    fx_rate_to_eur=Decimal("1"),
    cost_basis_eur=Decimal("0"),
)


async def _seed_held_universe(maker) -> dict[str, str]:
    """Seed the universe used by the held=true regression tests.

    Layout:
      - HELD     : single buy of qty 10 (one account) — currently held.
      - SOLD     : buy 10 + sell -10 (net 0) — fully sold.
      - NEVER    : zero transactions — never held.
      - SPLIT    : buy 5 in account A + buy 3 in account B (net 8 across both
                   accounts) — held, must NOT duplicate in the response.
      - PARTIAL  : buy 10 + sell -3 (net 7) — partially sold, still held.
      - SOFTDEL  : buy 10 + sell -10 (net 0 raw) BUT the sell is soft-deleted,
                   so the live SUM is 10 — must appear in held=true.

    Returns a dict mapping logical name → instrument id so tests can assert
    membership precisely.
    """
    async with maker() as session:
        account_a = Account(name="Acct A", account_type="broker", currency="EUR")
        account_b = Account(name="Acct B", account_type="broker", currency="EUR")

        held = Instrument(
            symbol="HELD",
            name="Held Position",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        sold = Instrument(
            symbol="SOLD",
            name="Fully Sold",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        never = Instrument(
            symbol="NEVER",
            name="Never Held",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        split = Instrument(
            symbol="SPLIT",
            name="Split Across Accounts",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        partial = Instrument(
            symbol="PARTIAL",
            name="Partially Sold",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        softdel = Instrument(
            symbol="SOFTDEL",
            name="Sell Soft-Deleted",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )

        session.add_all(
            [account_a, account_b, held, sold, never, split, partial, softdel]
        )
        await session.flush()

        # HELD — single buy in account A.
        session.add(
            Transaction(
                account_id=account_a.id,
                instrument_id=held.id,
                date=date(2026, 1, 1),
                quantity=Decimal("10"),
                **EUR_BUY_KW,
            )
        )

        # SOLD — buy 10 + sell -10, both live.
        session.add(
            Transaction(
                account_id=account_a.id,
                instrument_id=sold.id,
                date=date(2026, 1, 1),
                quantity=Decimal("10"),
                **EUR_BUY_KW,
            )
        )
        session.add(
            Transaction(
                account_id=account_a.id,
                instrument_id=sold.id,
                txn_type="sell",
                date=date(2026, 2, 1),
                quantity=Decimal("-10"),
                unit_price=Decimal("1"),
                price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("0"),
            )
        )

        # NEVER — no transactions.

        # SPLIT — buy 5 in A, buy 3 in B (net 8 across the instrument).
        session.add(
            Transaction(
                account_id=account_a.id,
                instrument_id=split.id,
                date=date(2026, 1, 1),
                quantity=Decimal("5"),
                **EUR_BUY_KW,
            )
        )
        session.add(
            Transaction(
                account_id=account_b.id,
                instrument_id=split.id,
                date=date(2026, 1, 1),
                quantity=Decimal("3"),
                **EUR_BUY_KW,
            )
        )

        # PARTIAL — buy 10 + sell -3 (net 7).
        session.add(
            Transaction(
                account_id=account_a.id,
                instrument_id=partial.id,
                date=date(2026, 1, 1),
                quantity=Decimal("10"),
                **EUR_BUY_KW,
            )
        )
        session.add(
            Transaction(
                account_id=account_a.id,
                instrument_id=partial.id,
                txn_type="sell",
                date=date(2026, 2, 1),
                quantity=Decimal("-3"),
                unit_price=Decimal("1"),
                price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("0"),
            )
        )

        # SOFTDEL — buy 10 (live) + sell -10 (soft-deleted). Live SUM = 10 → held.
        session.add(
            Transaction(
                account_id=account_a.id,
                instrument_id=softdel.id,
                date=date(2026, 1, 1),
                quantity=Decimal("10"),
                **EUR_BUY_KW,
            )
        )
        session.add(
            Transaction(
                account_id=account_a.id,
                instrument_id=softdel.id,
                txn_type="sell",
                date=date(2026, 2, 1),
                quantity=Decimal("-10"),
                unit_price=Decimal("1"),
                price_currency="EUR",
                fx_rate_to_eur=Decimal("1"),
                cost_basis_eur=Decimal("0"),
                deleted_at=datetime(2026, 2, 2, 12, 0, 0),
            )
        )

        await session.commit()

        return {
            "HELD": held.id,
            "SOLD": sold.id,
            "NEVER": never.id,
            "SPLIT": split.id,
            "PARTIAL": partial.id,
            "SOFTDEL": softdel.id,
        }


@pytest.mark.asyncio
async def test_list_instruments_default_unchanged(client_with_maker):
    """No `held` query string → returns ALL seeded instruments regardless of
    holdings. Pins the backwards-compatible default."""
    client, maker = client_with_maker
    ids = await _seed_held_universe(maker)

    resp = await client.get("/api/instruments")
    assert resp.status_code == 200
    body = resp.json()
    returned_ids = {row["id"] for row in body}

    # Every seeded instrument is present, including never-held and fully-sold.
    for key in ("HELD", "SOLD", "NEVER", "SPLIT", "PARTIAL", "SOFTDEL"):
        assert ids[key] in returned_ids, f"default branch dropped {key}"


@pytest.mark.asyncio
async def test_list_instruments_held_false_equivalent_to_default(client_with_maker):
    """held=false explicit → identical response (length + ordering) to no param."""
    client, maker = client_with_maker
    await _seed_held_universe(maker)

    resp_default = await client.get("/api/instruments")
    resp_false = await client.get("/api/instruments?held=false")
    assert resp_default.status_code == 200
    assert resp_false.status_code == 200
    assert resp_default.json() == resp_false.json()


@pytest.mark.asyncio
async def test_list_instruments_held_true_excludes_never_held(client_with_maker):
    """An instrument with zero transactions must not appear in held=true."""
    client, maker = client_with_maker
    ids = await _seed_held_universe(maker)

    resp = await client.get("/api/instruments?held=true")
    assert resp.status_code == 200
    returned_ids = {row["id"] for row in resp.json()}

    assert ids["NEVER"] not in returned_ids


@pytest.mark.asyncio
async def test_list_instruments_held_true_excludes_fully_sold(client_with_maker):
    """SUM(quantity) == 0 → not held. Also covers the soft-delete case (a
    cancelling sell that is soft-deleted does not reduce the live SUM, so the
    instrument IS held). And the partially-sold case (net positive) IS held."""
    client, maker = client_with_maker
    ids = await _seed_held_universe(maker)

    resp = await client.get("/api/instruments?held=true")
    assert resp.status_code == 200
    returned_ids = {row["id"] for row in resp.json()}

    # Fully sold (live buy + live sell, net 0) → excluded.
    assert ids["SOLD"] not in returned_ids
    # Soft-deleted sell does not contribute → live SUM == 10 → included.
    assert ids["SOFTDEL"] in returned_ids
    # Partial sell, net positive → included.
    assert ids["PARTIAL"] in returned_ids


@pytest.mark.asyncio
async def test_list_instruments_held_true_includes_currently_held(client_with_maker):
    """Single-account held position appears; multi-account split appears
    exactly once (no row duplication from the JOIN/IN subquery)."""
    client, maker = client_with_maker
    ids = await _seed_held_universe(maker)

    resp = await client.get("/api/instruments?held=true")
    assert resp.status_code == 200
    body = resp.json()
    returned_ids = [row["id"] for row in body]

    # Currently held (single buy) → included.
    assert ids["HELD"] in returned_ids
    # Split across two accounts → included exactly once.
    assert ids["SPLIT"] in returned_ids
    assert returned_ids.count(ids["SPLIT"]) == 1, (
        "SPLIT appeared more than once — IN-subquery should dedupe"
    )


# ---------------------------------------------------------------------------
# Cross-field (instrument_type, price_source) rejection
# tests + display_decimals persistence/bounds tests.
# ---------------------------------------------------------------------------


def _detail_text(payload: dict) -> str:
    """Flatten Pydantic 422 detail into a single string for substring asserts."""
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return " ".join(str(item) for item in detail)
    return str(detail)


@pytest.mark.asyncio
async def test_create_instrument_rejects_stock_with_coingecko(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "coingecko",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    text = _detail_text(body)
    assert "price_source" in text or "instrument_type" in text


@pytest.mark.asyncio
async def test_create_instrument_rejects_crypto_with_finnhub(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "BTC",
            "name": "Bitcoin",
            "instrument_type": "crypto",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    text = _detail_text(body)
    assert "price_source" in text or "instrument_type" in text


@pytest.mark.asyncio
async def test_create_instrument_rejects_cash_with_manual(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "EUR",
            "name": "Euro cash",
            "instrument_type": "cash",
            "base_currency": "EUR",
            "price_source": "manual",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    text = _detail_text(body)
    assert "price_source" in text or "instrument_type" in text


@pytest.mark.asyncio
async def test_create_instrument_rejects_metal_with_finnhub(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "XAU",
            "name": "Gold",
            "instrument_type": "metal",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    text = _detail_text(body)
    assert "price_source" in text or "instrument_type" in text


@pytest.mark.asyncio
async def test_create_instrument_accepts_fund_with_ft(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "VWRL",
            "name": "Vanguard FTSE All-World",
            "instrument_type": "fund",
            "base_currency": "EUR",
            "price_source": "ft",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["instrument_type"] == "fund"
    assert body["price_source"] == "ft"


@pytest.mark.asyncio
async def test_create_instrument_persists_display_decimals(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
            "display_decimals": 3,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["display_decimals"] == 3


@pytest.mark.asyncio
async def test_create_instrument_rejects_display_decimals_out_of_range(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
            "display_decimals": 99,
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    text = _detail_text(body)
    assert "display_decimals" in text


@pytest.mark.asyncio
async def test_create_instrument_default_display_decimals_is_null(client):
    """Existing behaviour: omitting display_decimals leaves the column NULL.
    Frontend then falls back to DEFAULT_DECIMALS_BY_TYPE for the type."""
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "MSFT",
            "name": "Microsoft",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["display_decimals"] is None


# -----------------------------------------------------------------------
# risk_level round-trips through POST / GET / PUT
# -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_instrument_response_includes_risk_level_default(client):
    # POST without risk_level -> response should carry the "Medium" default
    # (the ORM-level default kicks in even when the schema also defaults).
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
        },
    )
    assert resp.status_code == 201
    inst_id = resp.json()["id"]
    assert resp.json()["risk_level"] == "Medium"

    # And GET /{id} returns it too.
    get_resp = await client.get(f"/api/instruments/{inst_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["risk_level"] == "Medium"

    # And GET (list) returns it on every row.
    list_resp = await client.get("/api/instruments")
    assert list_resp.status_code == 200
    for row in list_resp.json():
        assert "risk_level" in row


@pytest.mark.asyncio
async def test_put_instrument_updates_risk_level(client):
    # Create with default risk_level.
    create = await client.post(
        "/api/instruments",
        json={
            "symbol": "VWCE",
            "name": "Vanguard FTSE All-World",
            "instrument_type": "etf",
            "base_currency": "EUR",
            "price_source": "finnhub",
        },
    )
    assert create.status_code == 201
    inst_id = create.json()["id"]
    assert create.json()["risk_level"] == "Medium"

    # PUT changing risk_level to "Low" — body must include the other
    # required fields because PUT uses InstrumentCreate (not a partial).
    put = await client.put(
        f"/api/instruments/{inst_id}",
        json={
            "symbol": "VWCE",
            "name": "Vanguard FTSE All-World",
            "instrument_type": "etf",
            "base_currency": "EUR",
            "price_source": "finnhub",
            "risk_level": "Low",
        },
    )
    assert put.status_code == 200, put.text
    assert put.json()["risk_level"] == "Low"

    # GET reflects the new value.
    get_resp = await client.get(f"/api/instruments/{inst_id}")
    assert get_resp.json()["risk_level"] == "Low"


@pytest.mark.asyncio
async def test_create_instrument_rejects_invalid_risk_level(client):
    resp = await client.post(
        "/api/instruments",
        json={
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "instrument_type": "stock",
            "base_currency": "USD",
            "price_source": "finnhub",
            "risk_level": "garbage",
        },
    )
    assert resp.status_code == 422
    text = _detail_text(resp.json())
    assert "risk_level" in text


# ---------------------------------------------------------------------------
# GET /backfill-preview + POST /backfill-all tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_preview_summary_counts_and_earliest_date(
    client_with_maker, monkeypatch
):
    """Preview endpoint returns eligible_count, synthetic_count, earliest first-txn
    date, and estimated_api_calls (= eligible_count). Instruments without any
    transactions count toward neither bucket."""
    client, maker = client_with_maker

    async with maker() as session:
        account = Account(name="Acct", account_type="broker", currency="EUR")
        aapl = Instrument(
            symbol="AAPL",
            name="Apple Inc.",
            instrument_type="stock",
            base_currency="USD",
            price_source="finnhub",
        )
        btc = Instrument(
            symbol="BTC",
            name="Bitcoin",
            instrument_type="crypto",
            base_currency="USD",
            price_source="coingecko",
        )
        mmeur = Instrument(
            symbol="MMEUR",
            name="Money Market EUR",
            instrument_type="cash",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, aapl, btc, mmeur])
        await session.flush()

        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=aapl.id,
                date=date(2025, 6, 1),
                quantity=Decimal("1"),
                **EUR_BUY_KW,
            )
        )
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=btc.id,
                date=date(2025, 3, 15),
                quantity=Decimal("1"),
                **EUR_BUY_KW,
            )
        )
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=mmeur.id,
                date=date(2024, 12, 1),
                quantity=Decimal("1"),
                **EUR_BUY_KW,
            )
        )
        await session.commit()

    resp = await client.get("/api/instruments/backfill-preview")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "eligible_count": 2,
        "synthetic_count": 1,
        "earliest_first_txn_date": "2025-03-15",
        "estimated_api_calls": 2,
    }


@pytest.mark.asyncio
async def test_backfill_all_per_instrument_breakdown(
    client_with_maker, monkeypatch
):
    """Bulk endpoint returns a per-instrument breakdown ordered by symbol ASC.
    Synthetic-fund instruments surface verbatim with `manual_history_required`.
    Instruments with zero transactions surface as `no_transactions`."""
    from app.routers import instruments as instruments_router
    from app.services.backfill import BackfillResult

    client, maker = client_with_maker

    async with maker() as session:
        account = Account(name="Acct", account_type="broker", currency="EUR")
        aapl = Instrument(
            symbol="AAPL",
            name="Apple Inc.",
            instrument_type="stock",
            base_currency="USD",
            price_source="finnhub",
        )
        mmeur = Instrument(
            symbol="MMEUR",
            name="Money Market EUR",
            instrument_type="cash",
            base_currency="EUR",
            price_source="manual",
        )
        orphan = Instrument(
            symbol="ORPHAN",
            name="Orphan Stock",
            instrument_type="stock",
            base_currency="USD",
            price_source="finnhub",
        )
        session.add_all([account, aapl, mmeur, orphan])
        await session.flush()
        aapl_id, mm_id, orph_id = aapl.id, mmeur.id, orphan.id

        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=aapl.id,
                date=date(2025, 6, 1),
                quantity=Decimal("1"),
                **EUR_BUY_KW,
            )
        )
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=mmeur.id,
                date=date(2024, 12, 1),
                quantity=Decimal("1"),
                **EUR_BUY_KW,
            )
        )
        await session.commit()

    async def fake_backfill_instrument_history(
        session, http_client, instrument, start, end
    ):
        # Mirror the service's behaviour for manual sources so the router
        # still reflects synthetic instruments as manual_history_required
        # without our test fake having to reproduce the gate.
        if instrument.price_source in {"ft", "manual"}:
            return BackfillResult(
                instrument_id=instrument.id,
                status="manual_history_required",
                inserted_prices=0,
                skipped_existing=0,
                start=start,
                end=end,
            )
        return BackfillResult(
            instrument_id=instrument.id,
            status="ok",
            inserted_prices=3,
            skipped_existing=0,
            start=start,
            end=end,
        )

    async def fake_backfill_fx_history(session, http_client, start, end):
        return 0

    monkeypatch.setattr(
        instruments_router,
        "backfill_instrument_history",
        fake_backfill_instrument_history,
    )
    monkeypatch.setattr(
        instruments_router, "backfill_fx_history", fake_backfill_fx_history
    )

    resp = await client.post("/api/instruments/backfill-all")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_inserted_prices"] == 3
    assert body["total_inserted_fx_rates"] == 0
    assert body["rate_limited_count"] == 0

    items = body["items"]
    # Sort order: by symbol ASC.
    assert [i["symbol"] for i in items] == ["AAPL", "MMEUR", "ORPHAN"]
    by_symbol = {i["symbol"]: i for i in items}
    assert by_symbol["AAPL"] == {
        "instrument_id": aapl_id,
        "symbol": "AAPL",
        "status": "ok",
        "inserted_prices": 3,
        "skipped_existing": 0,
    }
    assert by_symbol["MMEUR"] == {
        "instrument_id": mm_id,
        "symbol": "MMEUR",
        "status": "manual_history_required",
        "inserted_prices": 0,
        "skipped_existing": 0,
    }
    assert by_symbol["ORPHAN"] == {
        "instrument_id": orph_id,
        "symbol": "ORPHAN",
        "status": "no_transactions",
        "inserted_prices": 0,
        "skipped_existing": 0,
    }


@pytest.mark.asyncio
async def test_backfill_all_rate_limit_isolation(
    client_with_maker, monkeypatch
):
    """A rate-limit on one instrument inside the bulk loop must not roll back
    the commits of earlier successful instruments. The endpoint itself still
    returns 200 — `rate_limited_count` exposes the per-instrument failure."""
    from sqlalchemy import select

    from app.routers import instruments as instruments_router
    from app.services.backfill import BackfillResult
    from app.services.pricing.errors import PriceProviderRateLimited

    client, maker = client_with_maker

    async with maker() as session:
        account = Account(name="Acct", account_type="broker", currency="EUR")
        aapl = Instrument(
            symbol="AAPL",
            name="Apple Inc.",
            instrument_type="stock",
            base_currency="USD",
            price_source="finnhub",
        )
        msft = Instrument(
            symbol="MSFT",
            name="Microsoft",
            instrument_type="stock",
            base_currency="USD",
            price_source="finnhub",
        )
        session.add_all([account, aapl, msft])
        await session.flush()
        msft_id = msft.id

        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=aapl.id,
                date=date(2025, 6, 1),
                quantity=Decimal("1"),
                **EUR_BUY_KW,
            )
        )
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=msft.id,
                date=date(2025, 6, 1),
                quantity=Decimal("1"),
                **EUR_BUY_KW,
            )
        )
        await session.commit()

    async def fake_backfill_instrument_history(
        session, http_client, instrument, start, end
    ):
        if instrument.symbol == "AAPL":
            raise PriceProviderRateLimited("twelve_data 429")
        # MSFT — stage a real PriceQuote so the assertion that MSFT's commit
        # survived AAPL's rollback can read the row back.
        session.add(
            PriceQuote(
                instrument_id=instrument.id,
                date=start,
                price=Decimal("100"),
                currency="USD",
                source="twelve_data",
            )
        )
        return BackfillResult(
            instrument_id=instrument.id,
            status="ok",
            inserted_prices=5,
            skipped_existing=0,
            start=start,
            end=end,
        )

    async def fake_backfill_fx_history(session, http_client, start, end):
        return 0

    monkeypatch.setattr(
        instruments_router,
        "backfill_instrument_history",
        fake_backfill_instrument_history,
    )
    monkeypatch.setattr(
        instruments_router, "backfill_fx_history", fake_backfill_fx_history
    )

    resp = await client.post("/api/instruments/backfill-all")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["total_inserted_prices"] == 5
    assert body["rate_limited_count"] == 1

    by_symbol = {i["symbol"]: i for i in body["items"]}
    assert by_symbol["AAPL"]["status"] == "rate_limited"
    assert by_symbol["AAPL"]["inserted_prices"] == 0
    assert by_symbol["MSFT"]["status"] == "ok"
    assert by_symbol["MSFT"]["inserted_prices"] == 5

    # Proof that MSFT's per-instrument commit landed despite AAPL raising
    # later in the loop ordering. (Bulk loop iterates by symbol ASC, so
    # AAPL is processed first — its rollback must NOT touch MSFT's commit.)
    async with maker() as session:
        msft_quotes = (
            await session.execute(
                select(PriceQuote).where(PriceQuote.instrument_id == msft_id)
            )
        ).scalars().all()
        assert len(msft_quotes) == 1, (
            "MSFT PriceQuote should have survived the AAPL rate-limit rollback"
        )


# ---------------------------------------------------------------------------
# DELETE /api/instruments/{id}: block-vs-cascade safety
#
# Classification of the five instrument_id FKs:
#   - transaction              => BLOCK  (409, "delete those transactions first")
#   - price_quote              => CASCADE (instrument-owned cache)
#   - apy_config               => CASCADE (instrument-owned config)
#   - holding_tag (HoldingTag) => CASCADE (instrument-owned attachment)
#   - concentration_mute       => CASCADE (instrument-owned attachment)
# A childless instrument still deletes (204). An unclassified FK violation must
# surface as a clean 409, never a 500.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_instrument_with_only_price_quotes_succeeds(client_with_maker):
    """Test A: an instrument with ONLY price_quote children (no transactions)
    deletes with 204 and its cached quotes are cascade-removed."""
    from sqlalchemy import select

    c, maker = client_with_maker
    async with maker() as session:
        inst = Instrument(
            symbol="ONLYQ",
            name="Only Quotes",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add(inst)
        await session.flush()
        inst_id = inst.id
        session.add(
            PriceQuote(
                instrument_id=inst_id,
                date=date(2026, 1, 1),
                price=Decimal("100"),
                currency="EUR",
                source="manual",
            )
        )
        await session.commit()

    resp = await c.delete(f"/api/instruments/{inst_id}")
    assert resp.status_code == 204, resp.text

    # Instrument is gone.
    get_resp = await c.get(f"/api/instruments/{inst_id}")
    assert get_resp.status_code == 404

    # No price_quote rows remain for that instrument.
    async with maker() as session:
        rows = (
            await session.execute(
                select(PriceQuote).where(PriceQuote.instrument_id == inst_id)
            )
        ).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_delete_instrument_referenced_by_transaction_returns_409(
    client_with_maker,
):
    """Test B: an instrument referenced by a non-deleted transaction returns a
    clean 409 (NOT 500) and the instrument is preserved."""
    c, maker = client_with_maker
    async with maker() as session:
        account = Account(name="Acct", account_type="broker", currency="EUR")
        inst = Instrument(
            symbol="HELDTX",
            name="Held With Txn",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, inst])
        await session.flush()
        inst_id = inst.id
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=inst_id,
                date=date(2026, 1, 1),
                quantity=Decimal("10"),
                **EUR_BUY_KW,
            )
        )
        await session.commit()

    resp = await c.delete(f"/api/instruments/{inst_id}")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, str) and detail.strip()
    assert "transaction" in detail.lower()

    # Instrument still exists.
    get_resp = await c.get(f"/api/instruments/{inst_id}")
    assert get_resp.status_code == 200


@pytest.mark.asyncio
async def test_delete_instrument_no_children_succeeds(client):
    """Test C: a bare instrument with no children deletes with 204."""
    create = await client.post(
        "/api/instruments",
        json={
            "symbol": "BARE",
            "name": "Bare Instrument",
            "instrument_type": "stock",
            "base_currency": "EUR",
            "price_source": "manual",
        },
    )
    assert create.status_code == 201
    inst_id = create.json()["id"]

    resp = await client.delete(f"/api/instruments/{inst_id}")
    assert resp.status_code == 204, resp.text

    get_resp = await client.get(f"/api/instruments/{inst_id}")
    assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_instrument_cascades_apy_and_mute_and_tags(client_with_maker):
    """Test D: an instrument with apy_config + concentration_mute + holding_tag
    children (and no transactions) deletes with 204 and each child table is
    cleared for that instrument_id."""
    from sqlalchemy import select

    from app.models import ApyConfig, ConcentrationMute, HoldingTag, Tag

    c, maker = client_with_maker
    async with maker() as session:
        account = Account(name="Acct", account_type="broker", currency="EUR")
        inst = Instrument(
            symbol="MANYCH",
            name="Many Children",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        tag = Tag(name="growth")
        session.add_all([account, inst, tag])
        await session.flush()
        inst_id = inst.id
        session.add_all(
            [
                ApyConfig(
                    account_id=account.id,
                    instrument_id=inst_id,
                    apy_rate=Decimal("0.0237"),
                    effective_from=date(2026, 1, 1),
                ),
                ConcentrationMute(instrument_id=inst_id),
                HoldingTag(
                    account_id=account.id,
                    instrument_id=inst_id,
                    tag_id=tag.id,
                ),
            ]
        )
        await session.commit()

    resp = await c.delete(f"/api/instruments/{inst_id}")
    assert resp.status_code == 204, resp.text

    async with maker() as session:
        apy_rows = (
            await session.execute(
                select(ApyConfig).where(ApyConfig.instrument_id == inst_id)
            )
        ).scalars().all()
        mute_rows = (
            await session.execute(
                select(ConcentrationMute).where(
                    ConcentrationMute.instrument_id == inst_id
                )
            )
        ).scalars().all()
        tag_rows = (
            await session.execute(
                select(HoldingTag).where(HoldingTag.instrument_id == inst_id)
            )
        ).scalars().all()
        assert apy_rows == []
        assert mute_rows == []
        assert tag_rows == []


@pytest.mark.asyncio
async def test_delete_instrument_blocked_even_when_transaction_soft_deleted(
    client_with_maker,
):
    """Edge: a soft-deleted-only transaction still BLOCKS. The FK is physical, so
    a referencing row is a referencing row regardless of soft-delete state."""
    c, maker = client_with_maker
    async with maker() as session:
        account = Account(name="Acct", account_type="broker", currency="EUR")
        inst = Instrument(
            symbol="SOFTTX",
            name="Soft-Deleted Txn Only",
            instrument_type="stock",
            base_currency="EUR",
            price_source="manual",
        )
        session.add_all([account, inst])
        await session.flush()
        inst_id = inst.id
        session.add(
            Transaction(
                account_id=account.id,
                instrument_id=inst_id,
                date=date(2026, 1, 1),
                quantity=Decimal("10"),
                deleted_at=datetime(2026, 2, 2, 12, 0, 0),
                **EUR_BUY_KW,
            )
        )
        await session.commit()

    resp = await c.delete(f"/api/instruments/{inst_id}")
    assert resp.status_code == 409, resp.text

    get_resp = await c.get(f"/api/instruments/{inst_id}")
    assert get_resp.status_code == 200
