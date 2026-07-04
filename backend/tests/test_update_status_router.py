"""GET /api/update-status + PUT /api/update/dismiss tests.

Mirrors the ASGITransport + AsyncClient auth fixture from test_version_endpoint.py.
The status endpoint reads the DB cache only (no outbound GitHub call); the dismiss
endpoint persists dismissed_version off the settings allowlist.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.services.update_store import set_cached_release
from tests.conftest import seed_admin_password


async def _seed_release(maker, *, version, notes_url="https://github.com/x/y/releases"):
    async with maker() as session:
        await set_cached_release(
            session, version=version, notes_url=notes_url, published_at="2026-06-25T00:00:00Z"
        )
        await session.commit()


@pytest_asyncio.fixture
async def authed():
    original_password = cfg_module.settings.app_password
    original_version = cfg_module.settings.app_version
    cfg_module.settings.app_password = "test-password-123"
    cfg_module.settings.app_version = "v1.2.0"

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
    cfg_module.settings.app_version = original_version


@pytest.mark.asyncio
async def test_status_reports_available_update(authed):
    client, maker = authed
    await _seed_release(maker, version="v1.3.0")
    resp = await client.get("/api/update-status")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["current_version"] == "v1.2.0"
    assert body["latest_version"] == "v1.3.0"
    assert body["update_available"] is True
    assert body["dismissed"] is False
    assert body["check_failed"] is False
    assert body["release_notes_url"] == "https://github.com/x/y/releases"


@pytest.mark.asyncio
async def test_no_update_when_current_is_latest(authed):
    client, maker = authed
    await _seed_release(maker, version="v1.2.0")
    body = (await client.get("/api/update-status")).json()
    assert body["update_available"] is False


@pytest.mark.asyncio
async def test_dev_build_reports_is_dev_and_suppresses_update(authed):
    """A source-mounted dev build (app_version == 'dev') can't self-update, and
    'dev' isn't comparable to a release (dev is usually AHEAD of the latest tag).
    So no update prompt, and is_dev flags the state for the Settings note."""
    client, maker = authed
    cfg_module.settings.app_version = "dev"
    await _seed_release(maker, version="v1.3.0")
    body = (await client.get("/api/update-status")).json()
    assert body["current_version"] == "dev"
    assert body["latest_version"] == "v1.3.0"
    assert body["is_dev"] is True
    assert body["update_available"] is False


@pytest.mark.asyncio
async def test_release_build_is_not_dev(authed):
    client, maker = authed
    await _seed_release(maker, version="v1.3.0")
    body = (await client.get("/api/update-status")).json()
    assert body["is_dev"] is False
    assert body["update_available"] is True


@pytest.mark.asyncio
async def test_dismiss_hides_then_newer_reappears(authed):
    client, maker = authed
    await _seed_release(maker, version="v1.3.0")

    dismiss = await client.put("/api/update/dismiss", json={"version": "v1.3.0"})
    assert dismiss.status_code == 204, dismiss.text

    body = (await client.get("/api/update-status")).json()
    assert body["dismissed"] is True
    assert body["update_available"] is False

    # A newer release ships → the dismissal no longer applies.
    await _seed_release(maker, version="v1.4.0")
    body2 = (await client.get("/api/update-status")).json()
    assert body2["latest_version"] == "v1.4.0"
    assert body2["dismissed"] is False
    assert body2["update_available"] is True


@pytest.mark.asyncio
async def test_status_makes_no_outbound_call(authed, monkeypatch):
    client, maker = authed
    await _seed_release(maker, version="v1.3.0")

    # Any attempt to construct an httpx client during the request should blow up;
    # the status endpoint must serve purely from cache.
    import app.services.update_check as update_check_mod

    def _boom(*args, **kwargs):
        raise AssertionError("update-status must not make an outbound call")

    monkeypatch.setattr(update_check_mod.httpx, "AsyncClient", _boom)
    resp = await client.get("/api/update-status")
    assert resp.status_code == 200
    assert resp.json()["update_available"] is True


@pytest.mark.asyncio
async def test_check_now_refreshes_cache(authed, monkeypatch):
    """POST /api/update/check forces an immediate GitHub re-check, bypassing the
    once-per-UTC-day cron cadence, and updates the cached latest release."""
    import app.services.update_check as update_check_mod
    from tests.test_update_check import _VALID_RELEASE, _fake_client_factory

    client, _maker = authed
    monkeypatch.setattr(
        update_check_mod.httpx, "AsyncClient", _fake_client_factory(_VALID_RELEASE)
    )
    resp = await client.post("/api/update/check")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["latest_version"] == "v1.3.0"

    # The cached status the banner/settings read now reflects the fresh result.
    status = (await client.get("/api/update-status")).json()
    assert status["latest_version"] == "v1.3.0"
    assert status["update_available"] is True


@pytest.mark.asyncio
async def test_check_now_soft_fails_without_raising(authed, monkeypatch):
    """A GitHub hiccup must not 500 the endpoint — it reports status=failed."""
    import app.services.update_check as update_check_mod
    from tests.test_update_check import _fake_client_factory

    client, _maker = authed
    monkeypatch.setattr(
        update_check_mod.httpx,
        "AsyncClient",
        _fake_client_factory({}, get_raises=RuntimeError("boom")),
    )
    resp = await client.post("/api/update/check")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_check_now_unauthenticated_401():
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
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post("/api/update/check")
            assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_status_unauthenticated_401():
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
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get("/api/update-status")
            assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
