"""Keys router integration tests for the HTTP-boundary Nyquist layer.

Proves the blocking test-then-persist flow, that no plaintext ever crosses
back, and the demo write-lock at the /api/keys surface. The `key_test`
dispatch is monkeypatched so no test reaches a real provider host (the hermetic
guard would otherwise block these calls).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import config as cfg_module
from app.core.database import Base, attach_sqlite_pragmas, get_db
from app.main import app
from app.services import key_store, key_test
from tests.conftest import seed_admin_password

# A configured key value used to prove it NEVER appears in any response body.
FINNHUB_KEY = "FINNHUB-SECRET-1234"


async def _passing(client, candidate_key):  # noqa: ANN001
    return None


async def _failing(client, candidate_key):  # noqa: ANN001
    raise ValueError("finnhub: invalid API key")


@pytest.fixture(autouse=True)
def _reset_cache():
    """Every test starts from an empty resolver cache (module-global state)."""
    key_store._CACHE.clear()
    yield
    key_store._CACHE.clear()


@pytest_asyncio.fixture
async def authed(monkeypatch):
    original_password = cfg_module.settings.app_password
    original_demo = cfg_module.settings.demo_mode
    cfg_module.settings.app_password = "test-password-123"
    cfg_module.settings.demo_mode = False

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
    cfg_module.settings.demo_mode = original_demo


@pytest.mark.asyncio
async def test_requires_session_cookie():
    """The surface is session-gated — no cookie -> 401 (not exempt)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/keys")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_get_lists_five_unconfigured_providers(authed):
    resp = await authed.get("/api/keys")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["demo"] is False
    assert len(body["providers"]) == 5
    assert [p["id"] for p in body["providers"]] == [
        "finnhub",
        "coingecko",
        "alpha_vantage",
        "twelve_data",
        "github",
    ]
    for p in body["providers"]:
        assert p["configured"] is False
        assert p["masked"] is None
        # No raw-value field is ever serialized.
        assert "value" not in p


@pytest.mark.asyncio
async def test_put_passing_test_persists_masked(authed, monkeypatch):
    monkeypatch.setitem(key_test.TEST_DISPATCH, "finnhub", _passing)

    put = await authed.put("/api/keys/finnhub", json={"value": FINNHUB_KEY})
    assert put.status_code == 204, put.text
    assert FINNHUB_KEY not in put.text  # 204 has no body, but assert anyway

    get = await authed.get("/api/keys")
    body = get.json()
    finnhub = next(p for p in body["providers"] if p["id"] == "finnhub")
    assert finnhub["configured"] is True
    assert finnhub["masked"] == "••••1234"
    # The raw key never crosses back to the client.
    assert FINNHUB_KEY not in get.text


@pytest.mark.asyncio
async def test_put_failing_test_blocks_save(authed, monkeypatch):
    monkeypatch.setitem(key_test.TEST_DISPATCH, "finnhub", _failing)

    put = await authed.put("/api/keys/finnhub", json={"value": FINNHUB_KEY})
    assert put.status_code == 422, put.text
    assert FINNHUB_KEY not in put.text  # sanitized detail, no key echo

    get = await authed.get("/api/keys")
    finnhub = next(p for p in get.json()["providers"] if p["id"] == "finnhub")
    assert finnhub["configured"] is False  # nothing persisted


@pytest.mark.asyncio
async def test_put_empty_github_clears_without_test(authed, monkeypatch):
    called = {"hit": False}

    async def _tracking(client, candidate_key):  # noqa: ANN001
        called["hit"] = True

    monkeypatch.setitem(key_test.TEST_DISPATCH, "github", _tracking)

    put = await authed.put("/api/keys/github", json={"value": "   "})
    assert put.status_code == 204, put.text
    assert called["hit"] is False  # optional empty path skips the test entirely

    get = await authed.get("/api/keys")
    github = next(p for p in get.json()["providers"] if p["id"] == "github")
    assert github["configured"] is False


@pytest.mark.asyncio
async def test_put_empty_required_provider_is_422(authed):
    put = await authed.put("/api/keys/finnhub", json={"value": ""})
    assert put.status_code == 422, put.text


@pytest.mark.asyncio
async def test_unknown_provider_is_404(authed, monkeypatch):
    monkeypatch.setitem(key_test.TEST_DISPATCH, "finnhub", _passing)
    put = await authed.put("/api/keys/bogus", json={"value": "x"})
    assert put.status_code == 404


@pytest.mark.asyncio
async def test_post_test_success_and_failure(authed, monkeypatch):
    monkeypatch.setitem(key_test.TEST_DISPATCH, "finnhub", _passing)
    ok = await authed.post("/api/keys/finnhub/test", json={"value": FINNHUB_KEY})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True}
    # The standalone test must NOT persist anything.
    get = await authed.get("/api/keys")
    finnhub = next(p for p in get.json()["providers"] if p["id"] == "finnhub")
    assert finnhub["configured"] is False

    monkeypatch.setitem(key_test.TEST_DISPATCH, "finnhub", _failing)
    bad = await authed.post("/api/keys/finnhub/test", json={"value": FINNHUB_KEY})
    assert bad.status_code == 422
    assert FINNHUB_KEY not in bad.text


@pytest.mark.asyncio
async def test_delete_clears_a_configured_key(authed, monkeypatch):
    monkeypatch.setitem(key_test.TEST_DISPATCH, "finnhub", _passing)
    await authed.put("/api/keys/finnhub", json={"value": FINNHUB_KEY})

    delete = await authed.delete("/api/keys/finnhub")
    assert delete.status_code == 204
    get = await authed.get("/api/keys")
    finnhub = next(p for p in get.json()["providers"] if p["id"] == "finnhub")
    assert finnhub["configured"] is False


@pytest.mark.asyncio
async def test_demo_mode_locks_writes_and_masks(authed, monkeypatch):
    # Configure a key first (demo off), then flip demo on.
    monkeypatch.setitem(key_test.TEST_DISPATCH, "finnhub", _passing)
    await authed.put("/api/keys/finnhub", json={"value": FINNHUB_KEY})

    monkeypatch.setattr(cfg_module.settings, "demo_mode", True)

    put = await authed.put("/api/keys/finnhub", json={"value": FINNHUB_KEY})
    assert put.status_code == 403
    post = await authed.post("/api/keys/finnhub/test", json={"value": FINNHUB_KEY})
    assert post.status_code == 403
    delete = await authed.delete("/api/keys/finnhub")
    assert delete.status_code == 403

    get = await authed.get("/api/keys")
    body = get.json()
    assert body["demo"] is True
    finnhub = next(p for p in body["providers"] if p["id"] == "finnhub")
    # Still configured, but the mask is suppressed so no value is revealed.
    assert finnhub["configured"] is True
    assert finnhub["masked"] is None
    assert FINNHUB_KEY not in get.text


@pytest.mark.asyncio
async def test_settings_response_carries_no_key_value(authed, monkeypatch):
    """GET /api/settings must not expose a key (allowlist unchanged)."""
    monkeypatch.setitem(key_test.TEST_DISPATCH, "finnhub", _passing)
    await authed.put("/api/keys/finnhub", json={"value": FINNHUB_KEY})

    settings_resp = await authed.get("/api/settings")
    assert settings_resp.status_code == 200
    assert FINNHUB_KEY not in settings_resp.text
    assert "finnhub_api_key" not in settings_resp.json()["settings"]
