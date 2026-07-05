import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base, attach_sqlite_pragmas
from app.services import setup_state as ss


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_totp_secret_roundtrip_and_clear(session):
    assert await ss.get_totp_secret(session) is None
    await ss.set_totp_secret(session, "SECRET123")
    assert await ss.get_totp_secret(session) == "SECRET123"
    await ss.set_totp_secret(session, None)
    assert await ss.get_totp_secret(session) is None


@pytest.mark.asyncio
async def test_totp_enabled_default_false(session):
    assert await ss.is_totp_enabled(session) is False
    await ss.set_totp_enabled(session, True)
    assert await ss.is_totp_enabled(session) is True
    await ss.set_totp_enabled(session, False)
    assert await ss.is_totp_enabled(session) is False


@pytest.mark.asyncio
async def test_token_epoch_default_zero_and_bump(session):
    assert await ss.get_token_epoch(session) == 0
    assert await ss.bump_token_epoch(session) == 1
    assert await ss.bump_token_epoch(session) == 2
    assert await ss.get_token_epoch(session) == 2
