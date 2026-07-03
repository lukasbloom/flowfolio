from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.models import Account, ConcentrationMute, Instrument, PriceQuote, Transaction, UserSetting
from app.services.concentration import (
    add_mute,
    get_concentration_offenders,
    remove_mute,
)
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
        yield client, maker

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


async def _account(session: AsyncSession, name: str) -> Account:
    account = Account(name=name, account_type="broker", currency="EUR")
    session.add(account)
    await session.flush()
    return account


async def _instrument(
    session: AsyncSession,
    symbol: str,
    *,
    instrument_type: str = "stock",
) -> Instrument:
    instrument = Instrument(
        symbol=symbol,
        name=f"{symbol} Holding",
        instrument_type=instrument_type,
        risk_level="Medium",
        base_currency="EUR",
        price_source="manual",
    )
    session.add(instrument)
    await session.flush()
    return instrument


async def _holding(
    session: AsyncSession,
    account: Account,
    instrument: Instrument,
    *,
    quantity: str,
    price: str,
) -> None:
    session.add(
        Transaction(
            account_id=account.id,
            instrument_id=instrument.id,
            txn_type="buy",
            date=date.today(),
            quantity=Decimal(quantity),
            unit_price=Decimal(price),
            price_currency="EUR",
            fx_rate_to_eur=Decimal("1"),
            cost_basis_eur=Decimal(quantity) * Decimal(price),
        )
    )
    existing_quote = await session.scalar(
        select(PriceQuote).where(
            PriceQuote.instrument_id == instrument.id,
            PriceQuote.date == date.today(),
            PriceQuote.source == "manual",
        )
    )
    if existing_quote is None:
        session.add(
            PriceQuote(
                instrument_id=instrument.id,
                date=date.today(),
                price=Decimal(price),
                currency="EUR",
                source="manual",
            )
        )
    await session.flush()


async def _seed_threshold(session: AsyncSession, value: str = "0.25") -> None:
    session.add(UserSetting(key="concentration_threshold", value=value))
    await session.flush()


def _offender_map(response):
    return {item.instrument_symbol: item for item in response.offenders}


@pytest.mark.asyncio
async def test_concentration_empty_portfolio_returns_default_threshold(session):
    response = await get_concentration_offenders(session)

    assert response.threshold == Decimal("0.25")
    assert response.offenders == []


@pytest.mark.asyncio
async def test_concentration_single_holding_at_full_net_worth_is_offender(session):
    account = await _account(session, "Revolut")
    btc = await _instrument(session, "BTC")
    await _holding(session, account, btc, quantity="1", price="100")

    response = await get_concentration_offenders(session)

    assert response.offenders[0].instrument_symbol == "BTC"
    assert response.offenders[0].percent == Decimal("1")


@pytest.mark.asyncio
async def test_concentration_only_above_threshold_offends(session):
    await _seed_threshold(session, "0.25")
    account = await _account(session, "Revolut")
    btc = await _instrument(session, "BTC")
    eth = await _instrument(session, "ETH")
    cash = await _instrument(session, "EURC", instrument_type="cash")
    stable = await _instrument(session, "USDC", instrument_type="stablecoin")
    await _holding(session, account, btc, quantity="30", price="1")
    await _holding(session, account, eth, quantity="20", price="1")
    await _holding(session, account, cash, quantity="25", price="1")
    await _holding(session, account, stable, quantity="25", price="1")

    response = await get_concentration_offenders(session)

    assert set(_offender_map(response)) == {"BTC"}


@pytest.mark.asyncio
async def test_concentration_aggregates_same_instrument_across_accounts(session):
    await _seed_threshold(session, "0.25")
    revolut = await _account(session, "Revolut")
    xtb = await _account(session, "XTB")
    bank = await _account(session, "Bank")
    btc = await _instrument(session, "BTC")
    cash = await _instrument(session, "EURC", instrument_type="cash")
    stable = await _instrument(session, "USDC", instrument_type="stablecoin")
    fund = await _instrument(session, "FUND", instrument_type="fund")
    await _holding(session, revolut, btc, quantity="15", price="1")
    await _holding(session, xtb, btc, quantity="15", price="1")
    await _holding(session, bank, cash, quantity="25", price="1")
    await _holding(session, bank, stable, quantity="25", price="1")
    await _holding(session, bank, fund, quantity="20", price="1")

    response = await get_concentration_offenders(session)

    assert len(response.offenders) == 1
    assert response.offenders[0].instrument_symbol == "BTC"
    assert response.offenders[0].percent == Decimal("0.3")


@pytest.mark.asyncio
async def test_concentration_excludes_muted_instrument(session):
    account = await _account(session, "Revolut")
    btc = await _instrument(session, "BTC")
    await _holding(session, account, btc, quantity="1", price="100")
    await add_mute(session, btc.id)

    response = await get_concentration_offenders(session)

    assert response.offenders == []


@pytest.mark.asyncio
async def test_concentration_denominator_includes_cash_and_stablecoins(session):
    await _seed_threshold(session, "0.25")
    account = await _account(session, "Revolut")
    btc = await _instrument(session, "BTC", instrument_type="crypto")
    stable = await _instrument(session, "USDC", instrument_type="stablecoin")
    cash = await _instrument(session, "EURC", instrument_type="cash")
    await _holding(session, account, btc, quantity="30", price="1")
    await _holding(session, account, stable, quantity="30", price="1")
    await _holding(session, account, cash, quantity="40", price="1")

    response = await get_concentration_offenders(session)

    assert _offender_map(response)["BTC"].percent == Decimal("0.3")


def test_concentration_signature_has_no_tag_filter_parameter():
    signature = inspect.signature(get_concentration_offenders)

    assert "tag_filter" not in signature.parameters


@pytest.mark.asyncio
async def test_concentration_mute_post_is_idempotent(authed_client):
    client, maker = authed_client
    async with maker() as session:
        instrument = await _instrument(session, "BTC")
        await session.commit()
        instrument_id = instrument.id

    first = await client.post(f"/api/concentration/mute/{instrument_id}")
    second = await client.post(f"/api/concentration/mute/{instrument_id}")

    assert first.status_code == 204
    assert second.status_code == 204
    async with maker() as session:
        count = await session.scalar(select(func.count()).select_from(ConcentrationMute))
    assert count == 1


@pytest.mark.asyncio
async def test_concentration_delete_mute_hard_deletes_and_404s_when_missing(session):
    instrument = await _instrument(session, "BTC")
    await add_mute(session, instrument.id)

    deleted = await remove_mute(session, instrument.id)
    deleted_again = await remove_mute(session, instrument.id)

    assert deleted is True
    assert deleted_again is False
    count = await session.scalar(select(func.count()).select_from(ConcentrationMute))
    assert count == 0
