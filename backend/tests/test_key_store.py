"""Key store unit tests (Nyquist scaffold for the whole store layer).

Covers: resolver cache miss/hit, write-invalidate with no restart, clear,
boot reload, masked status (last-4 only, short values fully dotted), and the
off-allowlist guarantee (KEY_STORE_KEYS disjoint from SETTING_KEYS_ALLOWLIST).
"""
from __future__ import annotations

import pytest

from app.services import key_store
from app.services.settings import SETTING_KEYS_ALLOWLIST


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts from an empty resolver cache."""
    key_store._CACHE.clear()
    yield
    key_store._CACHE.clear()


@pytest.mark.asyncio
async def test_empty_cache_resolver_miss():
    assert key_store.get_api_key("finnhub") is None


@pytest.mark.asyncio
async def test_set_key_is_live_without_reload(db_session):
    await key_store.set_key(db_session, "finnhub", "ABCDEF1234")
    await db_session.commit()
    # No load_key_cache() call — the write itself updated the cache (no restart).
    assert key_store.get_api_key("finnhub") == "ABCDEF1234"


@pytest.mark.asyncio
async def test_clear_key_removes_row_and_cache(db_session):
    await key_store.set_key(db_session, "finnhub", "ABCDEF1234")
    await db_session.commit()
    await key_store.clear_key(db_session, "finnhub")
    await db_session.commit()
    assert key_store.get_api_key("finnhub") is None
    # Row is gone: a fresh reload sees nothing.
    key_store._CACHE.clear()
    await key_store.load_key_cache(db_session)
    assert key_store.get_api_key("finnhub") is None


@pytest.mark.asyncio
async def test_load_key_cache_repopulates_from_db(db_session):
    await key_store.set_key(db_session, "coingecko", "CGSECRET99")
    await db_session.commit()
    # Simulate a fresh process: wipe the in-memory cache, then boot-load.
    key_store._CACHE.clear()
    assert key_store.get_api_key("coingecko") is None
    await key_store.load_key_cache(db_session)
    assert key_store.get_api_key("coingecko") == "CGSECRET99"


@pytest.mark.asyncio
async def test_set_key_trims_whitespace(db_session):
    await key_store.set_key(db_session, "finnhub", "  ABCDEF1234  ")
    await db_session.commit()
    assert key_store.get_api_key("finnhub") == "ABCDEF1234"


@pytest.mark.asyncio
async def test_get_key_status_order_and_configured(db_session):
    await key_store.set_key(db_session, "finnhub", "ABCDEF1234")
    await db_session.commit()
    status = key_store.get_key_status()
    assert [s["id"] for s in status] == [
        "finnhub",
        "coingecko",
        "alpha_vantage",
        "twelve_data",
        "github",
    ]
    by_id = {s["id"]: s for s in status}
    assert by_id["finnhub"]["configured"] is True
    assert by_id["coingecko"]["configured"] is False


@pytest.mark.asyncio
async def test_get_key_status_masks_long_value(db_session):
    await key_store.set_key(db_session, "finnhub", "ABCDEF1234")
    await db_session.commit()
    entry = next(s for s in key_store.get_key_status() if s["id"] == "finnhub")
    assert entry["masked"] == "••••" + "1234"
    assert entry["masked"].endswith("1234")
    assert entry["masked"].startswith("••••")


@pytest.mark.asyncio
async def test_get_key_status_short_value_fully_dotted(db_session):
    await key_store.set_key(db_session, "finnhub", "AB")
    await db_session.commit()
    entry = next(s for s in key_store.get_key_status() if s["id"] == "finnhub")
    assert entry["masked"] == "••"  # length 2, no plaintext
    assert "A" not in entry["masked"] and "B" not in entry["masked"]


@pytest.mark.asyncio
async def test_get_key_status_unconfigured_has_no_mask(db_session):
    entry = next(s for s in key_store.get_key_status() if s["id"] == "coingecko")
    assert entry["configured"] is False
    assert entry.get("masked") is None


@pytest.mark.asyncio
async def test_get_key_status_never_returns_raw_value(db_session):
    await key_store.set_key(db_session, "finnhub", "ABCDEF1234")
    await db_session.commit()
    for entry in key_store.get_key_status():
        assert "ABCDEF1234" not in repr(entry)


def test_key_store_keys_off_allowlist():
    assert set(key_store.KEY_STORE_KEYS).isdisjoint(set(SETTING_KEYS_ALLOWLIST))
