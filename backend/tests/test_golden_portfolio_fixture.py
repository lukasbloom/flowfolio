"""Contract tests for the golden-portfolio fixture.

Locks the byte-identity contract: PRICE_ANCHORS may be daily-interpolated, but
the manually-set preserved anchors must round-trip Decimal-equal so all
existing Playwright HTML baselines stay byte-identical.

The fixture module lives at ``<repo_root>/scripts/fixtures/golden_portfolio.py``.
Pytest is invoked from ``backend/`` (``cd backend && uv run python -m pytest``),
so we prepend the repo root to ``sys.path`` here to mirror the runtime invocation
``PYTHONPATH=./backend python scripts/seed-golden.py`` documented at the top of
``scripts/seed-golden.py``.
"""
from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Repo root = backend/tests/../../  (this file lives at backend/tests/)
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.fixtures.golden_portfolio import (  # noqa: E402
    FIXTURE_ACCOUNT_NAMESPACE,
    FIXTURE_FX_NAMESPACE,
    FIXTURE_INSTRUMENT_NAMESPACE,
    FIXTURE_TRANSACTION_NAMESPACE,
    FX_ANCHORS,
    PRICE_ANCHORS,
    account_id_for,
    fx_rate_id_for,
    instrument_id_for,
    transaction_id_for,
)

# 22 preserved (symbol, date, Decimal price, currency) tuples per the
# PRESERVED VERBATIM contract in scripts/fixtures/golden_portfolio.py.
# Values copied byte-for-byte from the source-of-truth list — do NOT reformat
# the Decimal literal; the byte-identity contract requires Decimal equality.
PRESERVED_ANCHORS = [
    # 2025-04-30 anchors
    ("AAPL",   date(2025, 4, 30), Decimal("175.00"),    "USD"),
    ("MSFT",   date(2025, 4, 30), Decimal("405.00"),    "USD"),
    ("VWCE",   date(2025, 4, 30), Decimal("115.30"),    "EUR"),
    ("MSCI-W", date(2025, 4, 30), Decimal("210.00"),    "EUR"),
    ("BTC",    date(2025, 4, 30), Decimal("62000.00"),  "USD"),
    ("ETH",    date(2025, 4, 30), Decimal("3100.00"),   "USD"),
    ("SOL",    date(2025, 4, 30), Decimal("145.00"),    "USD"),
    ("XRP",    date(2025, 4, 30), Decimal("0.5200"),    "USD"),
    ("TRX",    date(2025, 4, 30), Decimal("0.1100"),    "USD"),
    ("USDC",   date(2025, 4, 30), Decimal("1.0000"),    "USD"),
    # 2026-04-30 anchors
    ("AAPL",   date(2026, 4, 30), Decimal("212.45"),    "USD"),
    ("MSFT",   date(2026, 4, 30), Decimal("475.12"),    "USD"),
    ("VWCE",   date(2026, 4, 30), Decimal("128.40"),    "EUR"),
    ("MSCI-W", date(2026, 4, 30), Decimal("234.80"),    "EUR"),
    ("BTC",    date(2026, 4, 30), Decimal("78500.00"),  "USD"),
    ("ETH",    date(2026, 4, 30), Decimal("3850.00"),   "USD"),
    ("SOL",    date(2026, 4, 30), Decimal("215.00"),    "USD"),
    ("XRP",    date(2026, 4, 30), Decimal("0.6450"),    "USD"),
    ("TRX",    date(2026, 4, 30), Decimal("0.1320"),    "USD"),
    ("USDC",   date(2026, 4, 30), Decimal("1.0000"),    "USD"),
    # Special: XRP closed-position anchor used by compare-closed-row snapshot
    ("XRP",    date(2025, 10, 20), Decimal("0.6200"),   "USD"),
]


def test_preserved_anchors_byte_identical():
    """Every PRESERVED VERBATIM anchor must survive daily interpolation Decimal-equal."""
    for sym, d, expected_price, ccy in PRESERVED_ANCHORS:
        found = next(
            (e for e in PRICE_ANCHORS
             if e["symbol"] == sym and e["date"] == d and e["currency"] == ccy),
            None,
        )
        assert found is not None, f"missing preserved anchor: {sym} {d} {ccy}"
        assert found["price"] == expected_price, (
            f"preserved anchor drift: {sym} {d} expected {expected_price}, "
            f"got {found['price']}"
        )


def test_fx_2026_04_30_frozen():
    """The frozen-instant FX override must remain exactly Decimal('1.1761') and unique."""
    matches = [e for e in FX_ANCHORS if e["date"] == date(2026, 4, 30)]
    assert len(matches) == 1, (
        f"expected exactly one 2026-04-30 FX entry, got {len(matches)}"
    )
    e = matches[0]
    assert e["base"] == "EUR" and e["quote"] == "USD"
    assert e["rate"] == Decimal("1.1761")


def test_price_anchors_daily_coverage():
    """BTC entries must be daily-dense (no gap > 1 day) and span the quarterly range.

    BTC's first quarterly anchor is 2023-12-31 and last is 2026-04-30, so the
    expected count is ~853 daily entries (inclusive of both endpoints). The
    real contract is gap==1 between consecutive entries; the count threshold
    is just a sanity floor.
    """
    btc = sorted(
        [e for e in PRICE_ANCHORS if e["symbol"] == "BTC"],
        key=lambda e: e["date"],
    )
    assert len(btc) >= 850, f"BTC daily coverage thin: only {len(btc)} entries"
    for a, b in zip(btc, btc[1:]):
        gap = (b["date"] - a["date"]).days
        assert gap == 1, (
            f"BTC gap > 1 day between {a['date']} and {b['date']} (gap={gap})"
        )


def test_instrument_id_deterministic():
    """Lock the uuid5 namespace + algorithm so accidental rotation fails loudly.

    If this test ever fails, it means either FIXTURE_INSTRUMENT_NAMESPACE was
    rotated or the symbol-to-id mapping convention changed — either of which
    silently invalidates every checked-in E2E snapshot baseline that embeds
    an instrument id. Do NOT update the expected value unless you have
    consciously chosen to migrate every baseline file.
    """
    # Locked: NAMESPACE literal pinned to the value chosen on 2026-05-27.
    assert str(FIXTURE_INSTRUMENT_NAMESPACE) == "7a2f67e5-173e-476f-b124-c9d517894790"

    # Locked: AAPL → uuid5 mapping. Value computed once and committed;
    # never regenerated.
    expected_aapl = "0fbcc218-316e-5c3b-95d6-365073762652"
    assert instrument_id_for("AAPL") == expected_aapl

    # Determinism across calls
    assert instrument_id_for("AAPL") == instrument_id_for("AAPL")

    # Different symbols produce different ids
    assert instrument_id_for("AAPL") != instrument_id_for("MSFT")

    # Return type matches Instrument.id column type (String(36))
    aapl_id = instrument_id_for("AAPL")
    assert isinstance(aapl_id, str)
    assert len(aapl_id) == 36


def test_account_id_deterministic():
    """Single-key model — pins Account namespace + 'name'-only stable key.

    Locked value computed once and committed; NEVER regenerate.
    Rotating either the namespace or the expected uuid5 silently invalidates
    every checked-in golden.sqlite sha256.
    """
    assert str(FIXTURE_ACCOUNT_NAMESPACE) == "d7a076f5-5d37-4479-ae75-43c542590ae6"
    expected_revolut = "bac83d15-499a-5c7e-aaa8-40d76922d852"
    assert account_id_for("Revolut") == expected_revolut
    assert account_id_for("Revolut") == account_id_for("Revolut")
    assert account_id_for("Revolut") != account_id_for("XTB")
    assert len(account_id_for("Revolut")) == 36


def test_fx_rate_id_deterministic():
    """Composite-key model — pins FxRate namespace + (base, quote, date) order.

    Argument order is part of the namespace contract; reordering invalidates
    every committed sha256.
    """
    assert str(FIXTURE_FX_NAMESPACE) == "50a955db-1f60-4e53-ad8f-806687994d22"
    expected_fx = "f56b5324-0111-53a9-8a5a-11cc120a3d36"
    assert fx_rate_id_for("EUR", "USD", date(2026, 4, 30)) == expected_fx
    assert fx_rate_id_for("EUR", "USD", date(2026, 4, 30)) != fx_rate_id_for("USD", "EUR", date(2026, 4, 30))
    assert fx_rate_id_for("EUR", "USD", date(2026, 4, 30)) != fx_rate_id_for("EUR", "USD", date(2026, 4, 29))


def test_transaction_id_deterministic():
    """Free-form stable-key model — pins Transaction namespace + key encoding.

    The sample key matches the shape the seeder emits for single-row txns
    at idx=0 of sorted_txns. If the seeder's sort or the stable-key encoding
    ever changes, this test fails loudly.
    """
    assert str(FIXTURE_TRANSACTION_NAMESPACE) == "e73e1330-cea0-4c0b-8722-f9e50b1165c1"
    expected_sample = "d7d4d0e3-933f-5742-99d7-587bdf4e725e"
    assert transaction_id_for(
        "single", 0, "Revolut", "AAPL", "2024-01-25", "buy", "5", "194.20"
    ) == expected_sample
