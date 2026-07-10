"""Tests for the DB-stored admin password and the first-run /api/setup API.

Covers:
- setup_state service helpers (is_setup_complete / get_admin_password_hash /
  claim_admin_password / pre_seed_admin_password_from_env) over user_setting.
- DB-backed check_password (auth.py reads admin_password_hash; DB is the store).
- GET /api/setup/status + POST /api/setup/claim with first-claim-wins (409),
  session-cookie issuance, and short-password rejection (422).

Design: the admin password lives ONLY in the DB. APP_PASSWORD is a
boot-time pre-seed that materializes into the DB row, never a runtime
fallback inside check_password.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.auth import check_password
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.core.security import verify_password
from app.main import app
from app.services.setup_state import (
    claim_admin_password,
    get_admin_password_hash,
    is_setup_complete,
    pre_seed_admin_password_from_env,
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


# --- setup_state service ----------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_session_not_complete(db_session):
    assert await is_setup_complete(db_session) is False


@pytest.mark.asyncio
async def test_claim_writes_hash_and_marks_complete(db_session):
    won = await claim_admin_password(db_session, "hunter2hunter2")
    await db_session.commit()

    assert won is True  # the first claim wins (atomic gate)
    assert await is_setup_complete(db_session) is True
    stored = await get_admin_password_hash(db_session)
    assert stored is not None
    # Never plaintext — bcrypt hash.
    assert stored != "hunter2hunter2"
    assert stored.startswith("$2")
    assert verify_password("hunter2hunter2", stored) is True


@pytest.mark.asyncio
async def test_second_claim_loses_and_preserves_winner_hash(db_session):
    """A second claim against an already-claimed instance returns False
    (the loser) and never overwrites the winner's password hash."""
    won_first = await claim_admin_password(db_session, "winnerpassword")
    await db_session.commit()
    assert won_first is True
    winner_hash = await get_admin_password_hash(db_session)

    won_second = await claim_admin_password(db_session, "attackerpassword")
    await db_session.commit()
    assert won_second is False  # lost the atomic claim

    # The winner's hash is intact; the attacker's password never took effect.
    assert await get_admin_password_hash(db_session) == winner_hash
    assert verify_password("winnerpassword", winner_hash) is True
    assert verify_password("attackerpassword", winner_hash) is False


@pytest.mark.asyncio
async def test_pre_seed_sets_hash_then_is_noop_when_complete(db_session):
    await pre_seed_admin_password_from_env(db_session, "seedpw12")
    await db_session.commit()
    assert await is_setup_complete(db_session) is True
    first_hash = await get_admin_password_hash(db_session)

    # Calling again must NOT overwrite the already-claimed hash.
    await pre_seed_admin_password_from_env(db_session, "different-password")
    await db_session.commit()
    assert await get_admin_password_hash(db_session) == first_hash


@pytest.mark.asyncio
async def test_pre_seed_noop_when_app_password_none(db_session):
    await pre_seed_admin_password_from_env(db_session, None)
    await db_session.commit()
    assert await is_setup_complete(db_session) is False


@pytest.mark.asyncio
async def test_pre_seed_rejects_sub_8_char_password_on_unclaimed_db(db_session):
    """The pre-seed enforces the same 8-char floor as the interactive setup."""
    with pytest.raises(RuntimeError, match="shorter than 8 characters"):
        await pre_seed_admin_password_from_env(db_session, "short7x")
    await db_session.rollback()
    assert await is_setup_complete(db_session) is False


@pytest.mark.asyncio
async def test_pre_seed_accepts_exactly_8_char_password(db_session):
    """An 8-char password is the floor, not the cutoff, it must succeed."""
    await pre_seed_admin_password_from_env(db_session, "eightchr")
    await db_session.commit()
    assert await is_setup_complete(db_session) is True
    assert await get_admin_password_hash(db_session) is not None


@pytest.mark.asyncio
async def test_pre_seed_short_password_is_noop_on_already_claimed_db(db_session):
    """An already-claimed instance returns before the length check, a short
    env value lingering after a proper in-app password change cannot brick it."""
    await pre_seed_admin_password_from_env(db_session, "firstclaimpw")
    await db_session.commit()
    first_hash = await get_admin_password_hash(db_session)

    # No RuntimeError even though "short" is under the floor: the early
    # is_setup_complete return short-circuits before the length check.
    await pre_seed_admin_password_from_env(db_session, "short")
    await db_session.commit()
    assert await get_admin_password_hash(db_session) == first_hash


@pytest.mark.asyncio
async def test_check_password_reads_db(db_session):
    await claim_admin_password(db_session, "correctpassword")
    await db_session.commit()
    assert await check_password(db_session, "correctpassword") is True
    assert await check_password(db_session, "wrongpassword") is False


@pytest.mark.asyncio
async def test_check_password_false_when_unconfigured(db_session):
    """No admin_password_hash row → check_password returns False, never raises."""
    assert await check_password(db_session, "anything") is False


# --- /api/setup router ------------------------------------------------------


@pytest_asyncio.fixture
async def client():
    """HTTP client wired to a fresh in-memory DB via get_db override.

    No password is pre-seeded — the instance starts unclaimed so the claim
    flow can be exercised end to end.
    """
    original_password = cfg_module.settings.app_password
    cfg_module.settings.app_password = None

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    attach_sqlite_pragmas(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def override_db():
        async with maker() as s:
            yield s

    app.dependency_overrides[get_db] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password


@pytest.mark.asyncio
async def test_status_unclaimed(client):
    resp = await client.get("/api/setup/status")
    assert resp.status_code == 200
    assert resp.json() == {"claimed": False}


@pytest.mark.asyncio
async def test_claim_then_status_claimed(client):
    claim = await client.post(
        "/api/setup/claim", json={"password": "hunter2hunter2"}
    )
    assert claim.status_code == 200
    assert "session" in claim.cookies
    assert "HttpOnly" in claim.headers.get("set-cookie", "")

    status = await client.get("/api/setup/status")
    assert status.json() == {"claimed": True}


@pytest.mark.asyncio
async def test_second_claim_is_409(client):
    first = await client.post("/api/setup/claim", json={"password": "hunter2hunter2"})
    assert first.status_code == 200
    second = await client.post("/api/setup/claim", json={"password": "anotherpassword"})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_short_password_is_422(client):
    resp = await client.post("/api/setup/claim", json={"password": "short"})
    assert resp.status_code == 422
