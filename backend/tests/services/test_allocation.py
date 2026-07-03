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
from app.models import Account, HoldingTag, Instrument, PriceQuote, Tag, Transaction
from app.services.allocation import get_allocation_slices
from tests.conftest import seed_admin_password


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


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
        yield client

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _holding(
    session: AsyncSession,
    *,
    account_name: str | None = None,
    is_banked: bool = True,
    symbol: str = "FLOW",
    instrument_type: str = "stock",
    risk_level: str = "Medium",
    quantity: str = "1",
    quote_price: str = "100",
    deleted: bool = False,
) -> tuple[Account, Instrument]:
    account = Account(
        name=account_name or f"{symbol} Account",
        account_type="broker",
        currency="EUR",
        is_banked=is_banked,
    )
    instrument = Instrument(
        symbol=symbol,
        name=f"{symbol} Holding",
        instrument_type=instrument_type,
        risk_level=risk_level,
        base_currency="EUR",
        price_source="manual",
    )
    session.add_all([account, instrument])
    await session.flush()
    session.add(
        Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type="buy",
            date=date.today(),
            quantity=Decimal(quantity),
            unit_price=Decimal(quote_price),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal(quantity) * Decimal(quote_price),
            deleted_at=datetime.utcnow() if deleted else None,
        )
    )
    session.add(
        PriceQuote(
            instrument_id=instrument.id,
            date=date.today(),
            price=Decimal(quote_price),
            currency="EUR",
            source="manual",
        )
    )
    await session.flush()
    return account, instrument


async def _tag_holding(
    session: AsyncSession, account: Account, instrument: Instrument, tag_name: str
) -> None:
    tag = Tag(name=tag_name, color="#00aa00")
    session.add(tag)
    await session.flush()
    session.add(
        HoldingTag(account_id=account.id, instrument_id=instrument.id, tag_id=tag.id)
    )
    await session.flush()


def _slice_map(response):
    return {item.label: item for item in response.slices}


@pytest.mark.asyncio
async def test_allocation_groups_open_holdings_by_type(session):
    await _holding(session, symbol="BTC", instrument_type="crypto", quote_price="100")
    await _holding(session, symbol="MSFT", instrument_type="stock", quote_price="300")

    response = await get_allocation_slices(session, dimension="type", display_currency="EUR")

    slices = _slice_map(response)
    assert set(slices) == {"crypto", "stock"}
    assert slices["crypto"].value == Decimal("100.000000000000000000")
    assert slices["stock"].percent == Decimal("0.75")


@pytest.mark.asyncio
async def test_allocation_groups_by_risk_default_medium(session):
    await _holding(session, symbol="BTC", instrument_type="crypto", risk_level="High")
    await _holding(session, symbol="CASH", instrument_type="cash")

    response = await get_allocation_slices(session, dimension="risk", display_currency="EUR")

    assert set(_slice_map(response)) == {"High", "Medium"}


@pytest.mark.asyncio
async def test_allocation_groups_by_account_name(session):
    await _holding(session, account_name="Revolut", symbol="BTC")
    await _holding(session, account_name="XTB", symbol="MSFT")

    response = await get_allocation_slices(session, dimension="account", display_currency="EUR")

    assert set(_slice_map(response)) == {"Revolut", "XTB"}


@pytest.mark.asyncio
async def test_allocation_groups_by_banked_label(session):
    await _holding(session, account_name="Bank", is_banked=True, symbol="CASH")
    await _holding(session, account_name="Wallet", is_banked=False, symbol="BTC")

    response = await get_allocation_slices(session, dimension="banked", display_currency="EUR")

    assert set(_slice_map(response)) == {"Banked", "Non-banked"}


@pytest.mark.asyncio
async def test_allocation_tag_filter_only_includes_tagged_holdings(session):
    tagged_account, tagged_instrument = await _holding(
        session, symbol="BTC", instrument_type="crypto", quote_price="100"
    )
    await _holding(session, symbol="ETH", instrument_type="crypto", quote_price="200")
    await _tag_holding(session, tagged_account, tagged_instrument, "growth")

    response = await get_allocation_slices(
        session, dimension="type", display_currency="EUR", tag_filter="growth"
    )

    assert len(response.slices) == 1
    assert response.slices[0].label == "crypto"
    assert response.slices[0].value == Decimal("100.000000000000000000")


@pytest.mark.asyncio
async def test_allocation_excludes_soft_deleted_transactions(session):
    await _holding(session, symbol="LIVE", instrument_type="stock", quote_price="100")
    await _holding(
        session, symbol="DEAD", instrument_type="crypto", quote_price="200", deleted=True
    )

    response = await get_allocation_slices(session, dimension="type", display_currency="EUR")

    assert set(_slice_map(response)) == {"stock"}


@pytest.mark.asyncio
async def test_allocation_empty_portfolio_returns_empty_response(session):
    response = await get_allocation_slices(session, dimension="type", display_currency="EUR")

    assert response.total == Decimal("0")
    assert response.slices == []


@pytest.mark.asyncio
async def test_allocation_router_invalid_dimension_returns_422(authed_client):
    response = await authed_client.get("/api/allocation?dimension=bogus")

    assert response.status_code == 422
