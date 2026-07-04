"""POST /api/update/apply + the in-flight lock + status merge.

The apply endpoint drops request.json onto the shared volume and NEVER touches
the container engine. The in-flight lock makes a re-click during
a non-terminal run re-attach to the same request_id instead of recreating twice.
The status endpoint surfaces the updater's live progress from status.json.

The authed fixture mirrors test_update_status_router.py and additionally points
settings.update_channel_dir at a per-test tmp dir so file I/O is hermetic.
"""
from __future__ import annotations

import inspect
import json
import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.services import update_apply
from app.services.update_store import set_cached_release
from tests.conftest import seed_admin_password


def _write_status(channel_dir: str, **fields) -> None:
    with open(os.path.join(channel_dir, "status.json"), "w", encoding="utf-8") as fh:
        json.dump(fields, fh)


# --------------------------------------------------------------------------- #
# Pure-service unit tests (point update_channel_dir at a tmp dir)             #
# --------------------------------------------------------------------------- #


def test_request_update_writes_request_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(tmp_path))
    request_id = update_apply.request_update("v1.3.0")
    assert request_id
    written = json.loads((tmp_path / "request.json").read_text())
    assert written["request_id"] == request_id
    assert written["target_version"] == "v1.3.0"
    assert "requested_at" in written


def test_request_update_reattaches_while_in_flight(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(tmp_path))
    _write_status(str(tmp_path), request_id="run-abc", state="pulling")
    request_id = update_apply.request_update("v1.3.0")
    assert request_id == "run-abc"
    # No request.json written — a re-click must not trigger a second recreate.
    assert not (tmp_path / "request.json").exists()


def test_request_update_breaks_stale_in_flight_lock(tmp_path, monkeypatch):
    """A non-terminal status older than STALE_AFTER_SECONDS is a dead
    updater — a fresh request must break the lock instead of re-attaching."""
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(tmp_path))
    stale = (
        datetime.now(timezone.utc)
        - timedelta(seconds=update_apply.STALE_AFTER_SECONDS + 60)
    ).isoformat()
    _write_status(str(tmp_path), request_id="run-dead", state="pulling", updated_at=stale)
    request_id = update_apply.request_update("v1.3.0")
    assert request_id != "run-dead"
    assert (tmp_path / "request.json").exists()


def test_request_update_reattaches_to_fresh_in_flight(tmp_path, monkeypatch):
    """A recently-stamped non-terminal status is a live run, so re-attach."""
    from datetime import datetime, timezone

    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(tmp_path))
    fresh = datetime.now(timezone.utc).isoformat()
    _write_status(str(tmp_path), request_id="run-live", state="pulling", updated_at=fresh)
    request_id = update_apply.request_update("v1.3.0")
    assert request_id == "run-live"
    assert not (tmp_path / "request.json").exists()


def test_request_update_writes_again_after_terminal_state(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(tmp_path))
    _write_status(str(tmp_path), request_id="run-old", state="success")
    request_id = update_apply.request_update("v1.3.0")
    assert request_id != "run-old"
    assert (tmp_path / "request.json").exists()


def test_request_update_resets_status_to_preparing(tmp_path, monkeypatch):
    """A new run rewrites status.json to its own `preparing` so the overlay
    never briefly serves a PRIOR run's terminal failed/success."""
    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(tmp_path))
    _write_status(str(tmp_path), request_id="run-old", state="failed")
    request_id = update_apply.request_update("v1.3.0")
    status = update_apply.read_update_status()
    assert status["request_id"] == request_id
    assert status["state"] == "preparing"
    assert request_id != "run-old"


def test_request_update_rejects_non_semver(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(tmp_path))
    with pytest.raises(ValueError):
        update_apply.request_update("not-a-version")
    assert not (tmp_path / "request.json").exists()


def test_read_update_status_idle_default(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(tmp_path))
    status = update_apply.read_update_status()
    assert status == {
        "request_id": None,
        "state": None,
        "message": None,
        "log_tail": None,
        "updated_at": None,
    }


def test_update_apply_never_touches_the_container_engine():
    """Source assertion: the app process never shells out / drives the engine.

    Mirrors the plan's grep guard:
    `! grep -Eq 'import subprocess|create_subprocess|docker' update_apply.py`.
    """
    src = inspect.getsource(update_apply)
    assert "import subprocess" not in src
    assert "create_subprocess" not in src
    assert "docker" not in src  # lowercase engine name never appears


# --------------------------------------------------------------------------- #
# Router tests (full ASGI app + auth + tmp channel dir)                       #
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def authed(tmp_path):
    original_password = cfg_module.settings.app_password
    original_version = cfg_module.settings.app_version
    original_channel = cfg_module.settings.update_channel_dir
    cfg_module.settings.app_password = "test-password-123"
    cfg_module.settings.app_version = "v1.2.0"
    cfg_module.settings.update_channel_dir = str(tmp_path)

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
        yield c, maker, tmp_path

    app.dependency_overrides.clear()
    await engine.dispose()
    cfg_module.settings.app_password = original_password
    cfg_module.settings.app_version = original_version
    cfg_module.settings.update_channel_dir = original_channel


async def _seed_release(maker, *, version):
    async with maker() as session:
        await set_cached_release(
            session,
            version=version,
            notes_url="https://github.com/x/y/releases",
            published_at="2026-06-25T00:00:00Z",
        )
        await session.commit()


@pytest.mark.asyncio
async def test_apply_writes_request_and_returns_id(authed):
    client, maker, channel_dir = authed
    await _seed_release(maker, version="v1.3.0")

    resp = await client.post("/api/update/apply")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["request_id"]

    written = json.loads((channel_dir / "request.json").read_text())
    assert written["request_id"] == body["request_id"]
    assert written["target_version"] == "v1.3.0"


@pytest.mark.asyncio
async def test_second_apply_reattaches_during_run(authed):
    client, maker, channel_dir = authed
    await _seed_release(maker, version="v1.3.0")

    first = (await client.post("/api/update/apply")).json()
    # Simulate the updater picking the request up and entering a non-terminal state.
    _write_status(str(channel_dir), request_id=first["request_id"], state="pulling")
    before = (channel_dir / "request.json").read_text()

    second = await client.post("/api/update/apply")
    assert second.status_code == 200, second.text
    assert second.json()["request_id"] == first["request_id"]  # re-attach
    # request.json untouched — no second recreate.
    assert (channel_dir / "request.json").read_text() == before


@pytest.mark.asyncio
async def test_status_reflects_updater_progress(authed):
    client, maker, channel_dir = authed
    await _seed_release(maker, version="v1.3.0")
    _write_status(
        str(channel_dir),
        request_id="run-xyz",
        state="pulling",
        message="Downloading v1.3.0…",
        log_tail="pulling layer 1/5",
    )

    body = (await client.get("/api/update-status")).json()
    assert body["update_in_progress"] is True
    assert body["update_state"] == "pulling"
    assert body["update_message"] == "Downloading v1.3.0…"
    assert body["update_log_tail"] == "pulling layer 1/5"


@pytest.mark.asyncio
async def test_status_idle_when_no_run(authed):
    client, maker, _ = authed
    await _seed_release(maker, version="v1.3.0")
    body = (await client.get("/api/update-status")).json()
    assert body["update_in_progress"] is False
    assert body["update_state"] is None


@pytest.mark.asyncio
async def test_apply_409_when_no_cached_release(authed):
    client, _maker, _ = authed
    resp = await client.post("/api/update/apply")
    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_apply_409_when_target_equals_current(authed):
    """Never recreate the version already running."""
    client, maker, channel_dir = authed
    await _seed_release(maker, version="v1.2.0")  # == fixture app_version
    resp = await client.post("/api/update/apply")
    assert resp.status_code == 409, resp.text
    assert not (channel_dir / "request.json").exists()


@pytest.mark.asyncio
async def test_apply_409_on_dev_build(authed):
    """Self-update can't work on a source-mounted dev build — no image to pull.
    The apply endpoint refuses so a stray click or direct API call can't kick the
    updater against a dev stack (defense in depth, independent of UI hiding)."""
    client, maker, channel_dir = authed
    cfg_module.settings.app_version = "dev"
    await _seed_release(maker, version="v1.3.0")
    resp = await client.post("/api/update/apply")
    assert resp.status_code == 409, resp.text
    assert not (channel_dir / "request.json").exists()


@pytest.mark.asyncio
async def test_apply_403_in_demo_mode(authed, monkeypatch):
    """The self-update apply path is hard-blocked at the API layer in demo
    mode. forbid_in_demo returns 403 before the body, so no request.json is
    written even with an update available (defense in depth, not UI hiding)."""
    client, maker, channel_dir = authed
    await _seed_release(maker, version="v1.3.0")  # an update IS available
    monkeypatch.setattr(cfg_module.settings, "demo_mode", True)

    resp = await client.post("/api/update/apply")
    assert resp.status_code == 403, resp.text
    assert not (channel_dir / "request.json").exists()


@pytest.mark.asyncio
async def test_apply_unauthenticated_401(tmp_path):
    original_channel = cfg_module.settings.update_channel_dir
    cfg_module.settings.update_channel_dir = str(tmp_path)
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
            resp = await c.post("/api/update/apply")
            assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
        cfg_module.settings.update_channel_dir = original_channel
