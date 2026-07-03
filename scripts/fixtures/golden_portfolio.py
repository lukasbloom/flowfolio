"""Golden portfolio fixture — showcase-grade dataset that doubles as the E2E test fixture.

Replaces the prior minimal shape (10 instruments, ~15 txns, straight-line net-worth chart,
empty KpiStrip) with a ~30-month, ~18-instrument, ~150–180-transaction dataset that, when
loaded into the dev stack, looks like a real Spain-based retail investor's portfolio
journey: 12–15 active holdings across all 7 accounts, allocation pies with non-trivial
slices, a net-worth chart with a visible crypto-winter → bull → mild correction arc,
populated KpiStrip realized totals (including at least one losing trade), a closed-
positions table with 2 stories, and a 100+ row activity ledger that exercises the
TanStack virtualizer.

History span: 2023-11-01 → 2026-04-30 (~30 months, ending on the frozen instant).

Accounts (7):
    Revolut, XTB, MyInvestor, Bit2Me, Cold Wallet, Liquido, Revolut Earn.

Instruments (~18):
    US equities (6): AAPL, MSFT, GOOGL, NVDA, AMZN, TSLA — Finnhub.
    EU equities (2): ASML.AS, SAP.DE — Finnhub.
    ETFs (2):       VWCE, SXR8 — Finnhub, EUR.
    Funds (2):      MSCI-W (manual NAV, EUR), MM-EUR (synthetic EUR money-market fund,
                    manual NAV ~1.0 with small drift; held on Liquido as the "safe sleeve").
    Crypto (6):     BTC, ETH, SOL, XRP, TRX (now actually held), ADA (the loss-story
                    altcoin — bought near peak ~$0.55, declines steadily to ~$0.18 by
                    2026-04-30 to anchor a realized-loss trade pair).
    Stablecoin (1): USDC.

Snapshot-anchor preservation contract: the following rows MUST appear byte-identical
inside the bigger fixture so existing E2E filter-by-symbol selectors keep resolving
their targets:
    - AAPL buy qty 2 @ $190.50 on Revolut, 2025-08-15
    - SOL trade-pair sold-leg qty 2 @ $198.00 on 2026-03-20 (Bit2Me, → 396 USDC)
    - ETH trade-pair received-leg qty 0.135 @ $3705 on 2025-12-01 (Bit2Me, ← 500 USDC)
    - XRP buy 100 @ $0.5500 on 2025-06-15 + trade-pair close of all 100 @ $0.6200 on
      2025-10-20 (Bit2Me, → 62 USDC, fee €0.10 each side) — closed-position fixture
    - USDC spend "VPS rental" 2026-01-08 qty 50 @ $1.00 on Revolut
    - MSCI-W manual yield 0.10 on 2026-03-31 with notes "Distribution payment"
    - Revolut Earn auto-accrual yields whose notes contain "2.37%" (ETH) and "4.80%"
      (USDC) on at least one date — the row-yield-auto-accrual snapshot filters by
      ["ETH", "Revolut Earn", "2.37%"]
    - 2025-04-30 + 2026-04-30 quarterly price anchors for AAPL/MSFT/VWCE/MSCI-W/BTC/
      ETH/SOL/XRP/TRX/USDC (preserved verbatim from the prior fixture)
    - XRP 2025-10-20 close-date price anchor at $0.6200 (the close-date used by the
      compare-closed-row snapshot's price cell)
    - FX 2026-04-30 frozen-instant rate 1.1761

Trade pairs (10 total = 20 rows):
    Winners (4):    AAPL partial trim, NVDA partial take-profit, BTC partial take-
                    profit, SOL→USDC (preserved verbatim).
    Losers  (2):    TSLA swing trade closed underwater, ADA full close at $0.18.
    Rebalances (3): USDC→ETH (preserved verbatim), XRP→USDC (preserved verbatim,
                    closed position), new BTC→USDC partial.
    Redeploy (1):   USDC→SOL stablecoin redeploy.

FX_ANCHORS construction: a private `_fx_curve(d: date) -> Decimal` helper produces a
deterministic, smooth EUR→USD curve (baseline 1.08 + sinusoidal variance + linear drift
to ~1.17 by 2026-04). FX_ANCHORS is then built as a daily-dense list from 2023-11-01
through max(today, 2026-04-30, latest quarterly anchor) so the seeder always has an
exact-date FX row available regardless of which USD txn dates the transaction list
covers. The 2026-04-30 frozen-instant anchor at Decimal("1.1761") is forced verbatim
(overriding the curve) because other fixtures hard-code that exact rate.

PRICE_ANCHORS construction: `_PRICE_ANCHORS_QUARTERLY` is the editable source-of-truth
(~200 quarterly + special-date anchors per instrument). At module load, `_interpolate_daily`
linearly interpolates every day in [start, end) per (symbol, currency), so PRICE_ANCHORS
exposes ~15,000 daily-dense rows to the seeder. Interpolated values are quantized to the
START anchor's Decimal exponent. Preserved anchor dates round-trip Decimal-equal because
the offset==0 branch returns the start anchor's Decimal verbatim (no arithmetic).

Consumed by scripts/seed-golden.py.

Note on `TRANSACTIONS`: entries with key `"trade"` are dual-leg pairs routed through
`app.services.trades.create_linked_trade` (sell + buy sharing a trade_pair_id).
Entries WITHOUT a `"trade"` key are single-row inserts via
TransactionCreate → Transaction ORM (mirroring routers/transactions.py::create_transaction).
Direct `txn_type='sell'` single-row entries are FORBIDDEN by the Pydantic validator.
Every sell in the fixture MUST be the sold leg of a trade pair.
"""
from __future__ import annotations

import math
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

ACCOUNTS: list[dict] = [
    {"name": "Revolut",      "account_type": "broker"},
    {"name": "XTB",          "account_type": "broker"},
    {"name": "MyInvestor",   "account_type": "broker"},
    {"name": "Bit2Me",       "account_type": "broker"},
    {"name": "Cold Wallet",  "account_type": "wallet"},
    {"name": "Liquido",      "account_type": "broker"},
    {"name": "Revolut Earn", "account_type": "broker"},
]

# ---------------------------------------------------------------------------
# Deterministic Instrument.id derivation
#
# Project-specific UUID namespace for showcase-fixture instrument-id derivation.
# Generated once on 2026-05-27. Never regenerate. Rotating this literal silently
# invalidates every checked-in E2E snapshot baseline that embeds an instrument id.
# ---------------------------------------------------------------------------
FIXTURE_INSTRUMENT_NAMESPACE = uuid.UUID("7a2f67e5-173e-476f-b124-c9d517894790")


def instrument_id_for(symbol: str) -> str:
    """Deterministic Instrument.id for the showcase fixture.

    Derives a stable UUID v5 from the human-readable ticker so every regen of
    ``tests/fixtures/golden.sqlite`` produces byte-identical instrument ids,
    keeping E2E snapshot baselines that embed instrument links stable.

    ``symbol`` is used as-is (no normalization) — the instrument table already
    enforces symbol uniqueness via UniqueConstraint(symbol, instrument_type).
    """
    return str(uuid.uuid5(FIXTURE_INSTRUMENT_NAMESPACE, symbol))


# ---------------------------------------------------------------------------
# Deterministic id + timestamp derivation for the rest of the seeded models
# (closes full byte-determinism across the seeded models).
#
# Each namespace UUID was generated once on 2026-05-27 via uuid.uuid4() for the
# showcase fixture. NEVER REGENERATE — rotating any of these literals silently
# invalidates the next regen's golden.sqlite sha256 (the determinism contract).
#
# Convention for stable_key encoding: pipe-join str()-coerced components.
# Tuple components must be hashable strings — coerce Decimals via str(), dates
# via .isoformat(), and ints via str(). Never reorder a helper's args without
# invalidating the namespace.
# ---------------------------------------------------------------------------
FIXTURE_ACCOUNT_NAMESPACE     = uuid.UUID("d7a076f5-5d37-4479-ae75-43c542590ae6")
FIXTURE_FX_NAMESPACE          = uuid.UUID("50a955db-1f60-4e53-ad8f-806687994d22")
FIXTURE_PRICE_QUOTE_NAMESPACE = uuid.UUID("412e2640-4670-4fa7-adb4-3f67e84cfae8")
FIXTURE_APY_CONFIG_NAMESPACE  = uuid.UUID("3724c8ec-cb98-4176-b2fc-42758387c703")
FIXTURE_TRANSACTION_NAMESPACE = uuid.UUID("e73e1330-cea0-4c0b-8722-f9e50b1165c1")
FIXTURE_LOT_ALLOC_NAMESPACE   = uuid.UUID("292c338f-5a0c-456a-ab75-21be00b0b1e5")
FIXTURE_TRADE_PAIR_NAMESPACE  = uuid.UUID("c520a4a3-9e05-4328-ad28-dff9ad0f0c28")

# Single timezone-aware datetime used to freeze every server_default=func.now()
# AND onupdate=func.now() the seeder relies on (created_at / updated_at /
# fetched_at columns). Production ORM defaults remain CURRENT_TIMESTAMP via
# server_default=func.now() — only the seeder overrides them by passing an
# explicit value at construction time. The inline-replicate path in
# seed-golden.py emits INSERTs only, so onupdate never fires either.
FIXTURE_EPOCH = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

# The frozen "now" instant used for `fetched_at`-style timestamps in the fixture.
# `fetched_at` semantically means "when was this price/rate last refreshed from an
# external provider". When `now()` is frozen, `fetched_at` must equal that frozen
# instant — otherwise the UI's StaleBadge fires (every row appears stale because
# `now() - fetched_at` exceeds the staleness threshold) and the values are
# logically inconsistent (a quote dated 2025-08-22 can't have been fetched on
# 2024-01-01 — that's time-travel).
#
# Matches the clock-abstraction test-harness frozen instant. Three
# sources hold this literal in lockstep — if one moves, all three must move:
#   - backend/app/core/clock.py (production fixed-now parser, see _parse_fixed_now)
#   - backend/app/core/config.py:29 (FLOWFOLIO_FIXED_NOW env var docstring)
#   - frontend/tests/e2e/snapshots/*.snapshot.spec.ts (FIXED_INSTANT, 6 files)
# Only `fetched_at` columns use this value in the seeder; `created_at`-class
# columns stay at FIXTURE_EPOCH (= 2024-01-01) since that's just "when the
# fixture was authored" and any constant works.
FIXTURE_FROZEN_NOW = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


def _stable_uuid5(namespace: uuid.UUID, *parts: object) -> str:
    """str(uuid.uuid5(namespace, "|".join(str(p) for p in parts)))."""
    return str(uuid.uuid5(namespace, "|".join(str(p) for p in parts)))


def account_id_for(name: str) -> str:
    """Derive deterministic Account.id from the unique account name."""
    return _stable_uuid5(FIXTURE_ACCOUNT_NAMESPACE, name)


def fx_rate_id_for(base: str, quote: str, d: date) -> str:
    """Derive deterministic FxRate.id from (base, quote, date)."""
    return _stable_uuid5(FIXTURE_FX_NAMESPACE, base, quote, d.isoformat())


def price_quote_id_for(instrument_id: str, d: date, source: str) -> str:
    """Derive deterministic PriceQuote.id from (instrument_id, date, source).

    instrument_id must already be deterministic (use instrument_id_for(symbol)).
    """
    return _stable_uuid5(FIXTURE_PRICE_QUOTE_NAMESPACE, instrument_id, d.isoformat(), source)


def apy_config_id_for(account_id: str, instrument_id: str, effective_from: date) -> str:
    """Derive deterministic ApyConfig.id from (account_id, instrument_id, effective_from)."""
    return _stable_uuid5(
        FIXTURE_APY_CONFIG_NAMESPACE,
        account_id, instrument_id, effective_from.isoformat(),
    )


def transaction_id_for(*key_parts: object) -> str:
    """Derive deterministic Transaction.id from a free-form stable key.

    For single-row txns the seeder passes:
        ("single", idx, account_name, symbol, date.isoformat(), txn_type, str(signed_qty), str(unit_price or "none"))
    For trade legs the seeder passes:
        ("trade", idx, "sold"|"received", account_name, symbol, date.isoformat(), str(quantity), str(unit_price))
    See scripts/seed-golden.py for the canonical call sites — argument order
    is part of the namespace contract; do not reorder.
    """
    return _stable_uuid5(FIXTURE_TRANSACTION_NAMESPACE, *key_parts)


def lot_alloc_id_for(sell_txn_id: str, buy_txn_id: str) -> str:
    """Derive deterministic LotAlloc.id from the already-deterministic txn ids."""
    return _stable_uuid5(FIXTURE_LOT_ALLOC_NAMESPACE, sell_txn_id, buy_txn_id)


def trade_pair_id_for(idx: int, sold_symbol: str, received_symbol: str, trade_date: date) -> str:
    """Derive deterministic Transaction.trade_pair_id for both legs of a pair."""
    return _stable_uuid5(
        FIXTURE_TRADE_PAIR_NAMESPACE,
        idx, sold_symbol, received_symbol, trade_date.isoformat(),
    )


# ---------------------------------------------------------------------------
# Instruments (~18)
# ---------------------------------------------------------------------------

INSTRUMENTS: list[dict] = [
    # US equities
    {"symbol": "AAPL",   "name": "Apple Inc.",                         "instrument_type": "stock",      "base_currency": "USD", "price_source": "finnhub"},
    {"symbol": "MSFT",   "name": "Microsoft Corp.",                    "instrument_type": "stock",      "base_currency": "USD", "price_source": "finnhub"},
    {"symbol": "GOOGL",  "name": "Alphabet Inc. Class A",              "instrument_type": "stock",      "base_currency": "USD", "price_source": "finnhub"},
    {"symbol": "NVDA",   "name": "NVIDIA Corp.",                       "instrument_type": "stock",      "base_currency": "USD", "price_source": "finnhub"},
    {"symbol": "AMZN",   "name": "Amazon.com, Inc.",                   "instrument_type": "stock",      "base_currency": "USD", "price_source": "finnhub"},
    {"symbol": "TSLA",   "name": "Tesla, Inc.",                        "instrument_type": "stock",      "base_currency": "USD", "price_source": "finnhub"},
    # EU equities, manual NAV on free tier. Finnhub
    # free tier returns 403 for .AS / .DE; Twelve Data free tier rejects
    # the exchange-qualified form (XETR/AMS require a paid Grow/Venture
    # plan) and the bare ticker resolves to the US ADR in USD — wrong
    # currency and price for the EU-listed original. Daily-interpolated
    # quarterly anchors below cover the showcase window.
    {"symbol": "ASML.AS", "name": "ASML Holding N.V.",                 "instrument_type": "stock",      "base_currency": "EUR", "price_source": "manual"},
    {"symbol": "SAP.DE",  "name": "SAP SE",                            "instrument_type": "stock",      "base_currency": "EUR", "price_source": "manual"},
    # UCITS ETFs, manual NAV on free tier. VWCE/SXR8
    # are XETR-quoted and unavailable on Twelve Data free tier; Finnhub
    # returns 0 for them. Same daily-interpolated quarterly-anchor
    # strategy as the EU equities and the EUR funds below.
    {"symbol": "VWCE",   "name": "Vanguard FTSE All-World UCITS ETF",  "instrument_type": "etf",        "base_currency": "EUR", "price_source": "manual"},
    {"symbol": "SXR8",   "name": "iShares Core S&P 500 UCITS ETF",     "instrument_type": "etf",        "base_currency": "EUR", "price_source": "manual"},
    # Funds (manual NAV — no free API covers EUR ISIN-quoted open-end funds reliably)
    {"symbol": "MSCI-W", "name": "MSCI World Index Fund (Synthetic)",  "instrument_type": "fund",       "base_currency": "EUR", "price_source": "manual"},
    {"symbol": "MM-EUR", "name": "EUR Money Market Fund (Synthetic)",  "instrument_type": "fund",       "base_currency": "EUR", "price_source": "manual"},
    # Crypto (USD-priced via CoinGecko)
    {"symbol": "BTC",    "name": "Bitcoin",                            "instrument_type": "crypto",     "base_currency": "USD", "price_source": "coingecko"},
    {"symbol": "ETH",    "name": "Ethereum",                           "instrument_type": "crypto",     "base_currency": "USD", "price_source": "coingecko"},
    {"symbol": "SOL",    "name": "Solana",                             "instrument_type": "crypto",     "base_currency": "USD", "price_source": "coingecko"},
    {"symbol": "XRP",    "name": "XRP",                                "instrument_type": "crypto",     "base_currency": "USD", "price_source": "coingecko"},
    {"symbol": "TRX",    "name": "TRON",                               "instrument_type": "crypto",     "base_currency": "USD", "price_source": "coingecko"},
    {"symbol": "ADA",    "name": "Cardano",                            "instrument_type": "crypto",     "base_currency": "USD", "price_source": "coingecko"},
    # Stablecoin
    {"symbol": "USDC",   "name": "USD Coin",                           "instrument_type": "stablecoin", "base_currency": "USD", "price_source": "coingecko"},
]

# ---------------------------------------------------------------------------
# _interpolate_daily — expand a list of quarterly per-(symbol, currency)
# anchors into a daily-dense list via linear interpolation.
# ---------------------------------------------------------------------------


def _interpolate_daily(quarterly: list[dict]) -> list[dict]:
    """Expand quarterly per-instrument anchors into daily entries via linear interpolation.

    For each (symbol, currency), sort by date and walk consecutive anchor pairs,
    emitting every day in [start, end) (inclusive of start, exclusive of end) so
    the end anchor of pair N becomes the start anchor of pair N+1 — no duplicates.
    The final anchor of each (symbol, currency) is appended as-is.

    Precision: each interpolated value is quantized to the START anchor's Decimal
    exponent (e.g. Decimal("0.6200") → -4, Decimal("192.50") → -2,
    Decimal("0.00003200") → -8). Anchor dates round-trip Decimal-equal because
    the offset==0 branch returns the start anchor's Decimal verbatim (no
    arithmetic), guaranteeing the byte-identity contract for preserved anchors.
    """
    # Group by (symbol, currency)
    groups: dict[tuple[str, str], list[dict]] = {}
    for entry in quarterly:
        key = (entry["symbol"], entry["currency"])
        groups.setdefault(key, []).append(entry)

    out: list[dict] = []
    for (sym, ccy), entries in groups.items():
        entries = sorted(entries, key=lambda e: e["date"])
        for i in range(len(entries) - 1):
            start, end = entries[i], entries[i + 1]
            span_days = (end["date"] - start["date"]).days
            if span_days <= 0:
                continue  # defensive: duplicate or out-of-order dates
            start_exponent = start["price"].as_tuple().exponent
            # 10^exponent; e.g. -4 → Decimal("0.0001")
            quant = Decimal(1).scaleb(start_exponent)
            price_delta = end["price"] - start["price"]
            for offset in range(span_days):  # [start.date, end.date)
                d = start["date"] + timedelta(days=offset)
                if offset == 0:
                    price = start["price"]
                else:
                    frac = Decimal(offset) / Decimal(span_days)
                    price = (start["price"] + price_delta * frac).quantize(quant)
                out.append({"symbol": sym, "date": d, "price": price, "currency": ccy})
        # Append the final anchor as-is
        out.append(entries[-1])

    out.sort(key=lambda e: (e["symbol"], e["date"]))
    return out


# ---------------------------------------------------------------------------
# _PRICE_ANCHORS_QUARTERLY — QUARTERLY SOURCE — daily PRICE_ANCHORS is
# interpolated from this at module load. Preserved anchors here propagate
# byte-identically to the interpolated output (linear interpolation between
# consecutive anchors evaluated AT an anchor date yields the anchor value
# exactly — no rounding loss).
#
# Synthetic curve shapes:
#   - Crypto: winter (2023-11 lows) → 2024-25 bull → mild Q1-2026 correction.
#   - US tech: up-and-to-the-right with a Q1-2025 dip.
#   - EU equity: slow drift.
#   - ETFs: smooth uptrend.
#   - Funds: MSCI-W steady drift; MM-EUR ~1.000 → 1.045 (accumulated yield in NAV).
#   - USDC: flat 1.0000.
#
# PRESERVED VERBATIM (per snapshot-anchor contract):
#   * 2025-04-30 + 2026-04-30 entries for AAPL/MSFT/VWCE/MSCI-W/BTC/ETH/SOL/XRP/TRX/USDC
#   * XRP 2025-10-20 close-date anchor at $0.6200 (compare-closed-row snapshot)
# ---------------------------------------------------------------------------

_PRICE_ANCHORS_QUARTERLY: list[dict] = [
    # ========== AAPL (USD) — Q1-2025 dip, bullish recovery ==========
    {"symbol": "AAPL",   "date": date(2023, 12, 31), "price": Decimal("192.50"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2024, 3, 31),  "price": Decimal("171.20"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2024, 6, 30),  "price": Decimal("210.60"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2024, 9, 30),  "price": Decimal("233.00"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2024, 12, 31), "price": Decimal("250.40"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2025, 3, 31),  "price": Decimal("184.30"),  "currency": "USD"},  # Q1-25 dip
    {"symbol": "AAPL",   "date": date(2025, 4, 30),  "price": Decimal("175.00"),  "currency": "USD"},  # preserved
    {"symbol": "AAPL",   "date": date(2025, 6, 30),  "price": Decimal("196.80"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2025, 9, 30),  "price": Decimal("215.40"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2025, 12, 31), "price": Decimal("228.90"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2026, 3, 31),  "price": Decimal("215.60"),  "currency": "USD"},
    {"symbol": "AAPL",   "date": date(2026, 4, 30),  "price": Decimal("212.45"),  "currency": "USD"},  # preserved

    # ========== MSFT (USD) ==========
    {"symbol": "MSFT",   "date": date(2023, 12, 31), "price": Decimal("376.00"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2024, 3, 31),  "price": Decimal("420.70"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2024, 6, 30),  "price": Decimal("446.95"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2024, 9, 30),  "price": Decimal("430.20"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2024, 12, 31), "price": Decimal("412.50"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2025, 3, 31),  "price": Decimal("389.40"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2025, 4, 30),  "price": Decimal("405.00"),  "currency": "USD"},  # preserved
    {"symbol": "MSFT",   "date": date(2025, 6, 30),  "price": Decimal("428.30"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2025, 9, 30),  "price": Decimal("445.60"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2025, 12, 31), "price": Decimal("462.80"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2026, 3, 31),  "price": Decimal("470.10"),  "currency": "USD"},
    {"symbol": "MSFT",   "date": date(2026, 4, 30),  "price": Decimal("475.12"),  "currency": "USD"},  # preserved

    # ========== GOOGL (USD) ==========
    {"symbol": "GOOGL",  "date": date(2023, 12, 31), "price": Decimal("135.00"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2024, 3, 31),  "price": Decimal("152.40"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2024, 6, 30),  "price": Decimal("184.20"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2024, 9, 30),  "price": Decimal("166.80"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2024, 12, 31), "price": Decimal("189.50"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2025, 3, 31),  "price": Decimal("160.20"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2025, 6, 30),  "price": Decimal("178.40"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2025, 9, 30),  "price": Decimal("186.90"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2025, 12, 31), "price": Decimal("194.20"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2026, 3, 31),  "price": Decimal("198.50"),  "currency": "USD"},
    {"symbol": "GOOGL",  "date": date(2026, 4, 30),  "price": Decimal("195.30"),  "currency": "USD"},

    # ========== NVDA (USD) — post-split adjusted, dominant winner ==========
    {"symbol": "NVDA",   "date": date(2023, 12, 31), "price": Decimal("48.20"),   "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2024, 3, 31),  "price": Decimal("90.30"),   "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2024, 6, 30),  "price": Decimal("123.40"),  "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2024, 9, 30),  "price": Decimal("121.80"),  "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2024, 12, 31), "price": Decimal("134.60"),  "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2025, 3, 31),  "price": Decimal("108.20"),  "currency": "USD"},  # Q1-25 dip
    {"symbol": "NVDA",   "date": date(2025, 6, 30),  "price": Decimal("142.50"),  "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2025, 9, 30),  "price": Decimal("158.30"),  "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2025, 12, 31), "price": Decimal("170.40"),  "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2026, 3, 31),  "price": Decimal("162.70"),  "currency": "USD"},
    {"symbol": "NVDA",   "date": date(2026, 4, 30),  "price": Decimal("160.20"),  "currency": "USD"},

    # ========== AMZN (USD) ==========
    {"symbol": "AMZN",   "date": date(2023, 12, 31), "price": Decimal("151.90"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2024, 3, 31),  "price": Decimal("180.20"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2024, 6, 30),  "price": Decimal("193.30"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2024, 9, 30),  "price": Decimal("186.10"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2024, 12, 31), "price": Decimal("219.40"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2025, 3, 31),  "price": Decimal("190.50"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2025, 6, 30),  "price": Decimal("198.70"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2025, 9, 30),  "price": Decimal("205.20"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2025, 12, 31), "price": Decimal("214.80"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2026, 3, 31),  "price": Decimal("208.90"),  "currency": "USD"},
    {"symbol": "AMZN",   "date": date(2026, 4, 30),  "price": Decimal("210.30"),  "currency": "USD"},

    # ========== TSLA (USD) — loser story for stocks ==========
    {"symbol": "TSLA",   "date": date(2023, 12, 31), "price": Decimal("248.50"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2024, 3, 31),  "price": Decimal("175.80"),  "currency": "USD"},  # Q1-24 drawdown
    {"symbol": "TSLA",   "date": date(2024, 6, 30),  "price": Decimal("197.40"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2024, 9, 30),  "price": Decimal("261.60"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2024, 12, 31), "price": Decimal("403.80"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2025, 3, 31),  "price": Decimal("232.40"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2025, 6, 30),  "price": Decimal("203.10"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2025, 9, 30),  "price": Decimal("194.80"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2025, 12, 31), "price": Decimal("188.20"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2026, 3, 31),  "price": Decimal("181.50"),  "currency": "USD"},
    {"symbol": "TSLA",   "date": date(2026, 4, 30),  "price": Decimal("185.60"),  "currency": "USD"},

    # ========== ASML.AS (EUR) ==========
    {"symbol": "ASML.AS", "date": date(2023, 12, 31), "price": Decimal("680.40"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2024, 3, 31),  "price": Decimal("875.20"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2024, 6, 30),  "price": Decimal("946.80"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2024, 9, 30),  "price": Decimal("760.50"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2024, 12, 31), "price": Decimal("700.30"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2025, 3, 31),  "price": Decimal("672.10"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2025, 6, 30),  "price": Decimal("714.50"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2025, 9, 30),  "price": Decimal("742.80"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2025, 12, 31), "price": Decimal("768.40"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2026, 3, 31),  "price": Decimal("782.30"),  "currency": "EUR"},
    {"symbol": "ASML.AS", "date": date(2026, 4, 30),  "price": Decimal("778.60"),  "currency": "EUR"},

    # ========== SAP.DE (EUR) ==========
    {"symbol": "SAP.DE",  "date": date(2023, 12, 31), "price": Decimal("139.00"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2024, 3, 31),  "price": Decimal("171.60"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2024, 6, 30),  "price": Decimal("181.20"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2024, 9, 30),  "price": Decimal("213.10"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2024, 12, 31), "price": Decimal("232.40"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2025, 3, 31),  "price": Decimal("244.50"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2025, 6, 30),  "price": Decimal("236.80"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2025, 9, 30),  "price": Decimal("228.40"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2025, 12, 31), "price": Decimal("222.10"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2026, 3, 31),  "price": Decimal("218.60"),  "currency": "EUR"},
    {"symbol": "SAP.DE",  "date": date(2026, 4, 30),  "price": Decimal("220.80"),  "currency": "EUR"},

    # ========== VWCE (EUR) ==========
    {"symbol": "VWCE",   "date": date(2023, 12, 31), "price": Decimal("99.40"),   "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2024, 3, 31),  "price": Decimal("108.60"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2024, 6, 30),  "price": Decimal("113.20"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2024, 9, 30),  "price": Decimal("115.80"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2024, 12, 31), "price": Decimal("118.10"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2025, 3, 31),  "price": Decimal("114.50"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2025, 4, 30),  "price": Decimal("115.30"),  "currency": "EUR"},  # preserved
    {"symbol": "VWCE",   "date": date(2025, 6, 30),  "price": Decimal("120.20"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2025, 9, 30),  "price": Decimal("122.80"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2025, 12, 31), "price": Decimal("125.40"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2026, 3, 31),  "price": Decimal("127.10"),  "currency": "EUR"},
    {"symbol": "VWCE",   "date": date(2026, 4, 30),  "price": Decimal("128.40"),  "currency": "EUR"},  # preserved

    # ========== SXR8 (EUR) ==========
    {"symbol": "SXR8",   "date": date(2023, 12, 31), "price": Decimal("438.20"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2024, 3, 31),  "price": Decimal("485.10"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2024, 6, 30),  "price": Decimal("520.80"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2024, 9, 30),  "price": Decimal("536.40"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2024, 12, 31), "price": Decimal("554.70"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2025, 3, 31),  "price": Decimal("531.20"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2025, 6, 30),  "price": Decimal("568.40"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2025, 9, 30),  "price": Decimal("590.10"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2025, 12, 31), "price": Decimal("608.30"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2026, 3, 31),  "price": Decimal("615.80"),  "currency": "EUR"},
    {"symbol": "SXR8",   "date": date(2026, 4, 30),  "price": Decimal("621.40"),  "currency": "EUR"},

    # ========== MSCI-W (EUR, manual) ==========
    {"symbol": "MSCI-W", "date": date(2023, 12, 31), "price": Decimal("186.20"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2024, 3, 31),  "price": Decimal("194.50"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2024, 6, 30),  "price": Decimal("199.80"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2024, 9, 30),  "price": Decimal("204.30"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2024, 12, 31), "price": Decimal("208.90"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2025, 3, 31),  "price": Decimal("206.10"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2025, 4, 30),  "price": Decimal("210.00"),  "currency": "EUR"},  # preserved
    {"symbol": "MSCI-W", "date": date(2025, 6, 30),  "price": Decimal("215.40"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2025, 9, 30),  "price": Decimal("220.80"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2025, 12, 31), "price": Decimal("226.30"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2026, 3, 31),  "price": Decimal("232.60"),  "currency": "EUR"},
    {"symbol": "MSCI-W", "date": date(2026, 4, 30),  "price": Decimal("234.80"),  "currency": "EUR"},  # preserved

    # ========== MM-EUR (EUR, manual) — money-market fund, small linear accrual ==========
    {"symbol": "MM-EUR", "date": date(2023, 12, 31), "price": Decimal("1.0020"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2024, 3, 31),  "price": Decimal("1.0070"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2024, 6, 30),  "price": Decimal("1.0120"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2024, 9, 30),  "price": Decimal("1.0170"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2024, 12, 31), "price": Decimal("1.0220"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2025, 3, 31),  "price": Decimal("1.0270"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2025, 6, 30),  "price": Decimal("1.0320"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2025, 9, 30),  "price": Decimal("1.0360"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2025, 12, 31), "price": Decimal("1.0400"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2026, 3, 31),  "price": Decimal("1.0440"),  "currency": "EUR"},
    {"symbol": "MM-EUR", "date": date(2026, 4, 30),  "price": Decimal("1.0450"),  "currency": "EUR"},

    # ========== BTC (USD) — crypto winter trough → 2024-25 bull → Q1-26 dip ==========
    {"symbol": "BTC",    "date": date(2023, 12, 31), "price": Decimal("42200.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2024, 3, 31),  "price": Decimal("70800.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2024, 6, 30),  "price": Decimal("62700.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2024, 9, 30),  "price": Decimal("63300.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2024, 12, 31), "price": Decimal("93600.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2025, 3, 31),  "price": Decimal("84200.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2025, 4, 30),  "price": Decimal("62000.00"), "currency": "USD"},  # preserved
    {"symbol": "BTC",    "date": date(2025, 6, 30),  "price": Decimal("76500.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2025, 9, 30),  "price": Decimal("88200.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2025, 12, 31), "price": Decimal("98400.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2026, 3, 31),  "price": Decimal("82100.00"), "currency": "USD"},
    {"symbol": "BTC",    "date": date(2026, 4, 30),  "price": Decimal("78500.00"), "currency": "USD"},  # preserved

    # ========== ETH (USD) ==========
    {"symbol": "ETH",    "date": date(2023, 12, 31), "price": Decimal("2280.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2024, 3, 31),  "price": Decimal("3520.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2024, 6, 30),  "price": Decimal("3390.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2024, 9, 30),  "price": Decimal("2650.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2024, 12, 31), "price": Decimal("3340.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2025, 3, 31),  "price": Decimal("2820.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2025, 4, 30),  "price": Decimal("3100.00"),  "currency": "USD"},  # preserved
    {"symbol": "ETH",    "date": date(2025, 6, 30),  "price": Decimal("3480.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2025, 9, 30),  "price": Decimal("3920.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2025, 12, 31), "price": Decimal("4180.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2026, 3, 31),  "price": Decimal("3940.00"),  "currency": "USD"},
    {"symbol": "ETH",    "date": date(2026, 4, 30),  "price": Decimal("3850.00"),  "currency": "USD"},  # preserved

    # ========== SOL (USD) ==========
    {"symbol": "SOL",    "date": date(2023, 12, 31), "price": Decimal("101.40"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2024, 3, 31),  "price": Decimal("196.20"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2024, 6, 30),  "price": Decimal("147.80"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2024, 9, 30),  "price": Decimal("155.20"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2024, 12, 31), "price": Decimal("190.40"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2025, 3, 31),  "price": Decimal("125.60"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2025, 4, 30),  "price": Decimal("145.00"),  "currency": "USD"},  # preserved
    {"symbol": "SOL",    "date": date(2025, 6, 30),  "price": Decimal("170.30"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2025, 9, 30),  "price": Decimal("195.80"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2025, 12, 31), "price": Decimal("228.40"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2026, 3, 31),  "price": Decimal("210.20"),  "currency": "USD"},
    {"symbol": "SOL",    "date": date(2026, 4, 30),  "price": Decimal("215.00"),  "currency": "USD"},  # preserved

    # ========== XRP (USD) — winter trough → bull → 2025-10 trade ==========
    {"symbol": "XRP",    "date": date(2023, 12, 31), "price": Decimal("0.6150"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2024, 3, 31),  "price": Decimal("0.6280"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2024, 6, 30),  "price": Decimal("0.4910"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2024, 9, 30),  "price": Decimal("0.5840"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2024, 12, 31), "price": Decimal("2.1500"),  "currency": "USD"},  # ATH spike
    {"symbol": "XRP",    "date": date(2025, 3, 31),  "price": Decimal("0.5320"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2025, 4, 30),  "price": Decimal("0.5200"),  "currency": "USD"},  # preserved
    {"symbol": "XRP",    "date": date(2025, 6, 30),  "price": Decimal("0.5680"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2025, 9, 30),  "price": Decimal("0.6050"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2025, 10, 20), "price": Decimal("0.6200"),  "currency": "USD"},  # preserved (compare-closed-row snapshot)
    {"symbol": "XRP",    "date": date(2025, 12, 31), "price": Decimal("0.6480"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2026, 3, 31),  "price": Decimal("0.6310"),  "currency": "USD"},
    {"symbol": "XRP",    "date": date(2026, 4, 30),  "price": Decimal("0.6450"),  "currency": "USD"},  # preserved

    # ========== TRX (USD) ==========
    {"symbol": "TRX",    "date": date(2023, 12, 31), "price": Decimal("0.1070"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2024, 3, 31),  "price": Decimal("0.1280"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2024, 6, 30),  "price": Decimal("0.1230"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2024, 9, 30),  "price": Decimal("0.1580"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2024, 12, 31), "price": Decimal("0.2580"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2025, 3, 31),  "price": Decimal("0.2120"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2025, 4, 30),  "price": Decimal("0.1100"),  "currency": "USD"},  # preserved
    {"symbol": "TRX",    "date": date(2025, 6, 30),  "price": Decimal("0.1180"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2025, 9, 30),  "price": Decimal("0.1240"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2025, 12, 31), "price": Decimal("0.1300"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2026, 3, 31),  "price": Decimal("0.1290"),  "currency": "USD"},
    {"symbol": "TRX",    "date": date(2026, 4, 30),  "price": Decimal("0.1320"),  "currency": "USD"},  # preserved

    # ========== ADA (USD) — loser story: declines steadily from peak to ~$0.18 ==========
    {"symbol": "ADA",    "date": date(2023, 12, 31), "price": Decimal("0.5750"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2024, 3, 31),  "price": Decimal("0.6420"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2024, 6, 30),  "price": Decimal("0.3870"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2024, 9, 30),  "price": Decimal("0.3520"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2024, 12, 31), "price": Decimal("0.3120"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2025, 3, 31),  "price": Decimal("0.2810"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2025, 6, 30),  "price": Decimal("0.2540"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2025, 8, 22),  "price": Decimal("0.2350"),  "currency": "USD"},  # ADA close-trade date anchor
    {"symbol": "ADA",    "date": date(2025, 9, 30),  "price": Decimal("0.2240"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2025, 12, 31), "price": Decimal("0.2080"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2026, 3, 31),  "price": Decimal("0.1890"),  "currency": "USD"},
    {"symbol": "ADA",    "date": date(2026, 4, 30),  "price": Decimal("0.1820"),  "currency": "USD"},

    # ========== USDC (USD) — flat ==========
    {"symbol": "USDC",   "date": date(2023, 12, 31), "price": Decimal("1.0000"),  "currency": "USD"},
    {"symbol": "USDC",   "date": date(2024, 6, 30),  "price": Decimal("1.0000"),  "currency": "USD"},
    {"symbol": "USDC",   "date": date(2024, 12, 31), "price": Decimal("1.0000"),  "currency": "USD"},
    {"symbol": "USDC",   "date": date(2025, 4, 30),  "price": Decimal("1.0000"),  "currency": "USD"},  # preserved
    {"symbol": "USDC",   "date": date(2025, 12, 31), "price": Decimal("1.0000"),  "currency": "USD"},
    {"symbol": "USDC",   "date": date(2026, 4, 30),  "price": Decimal("1.0000"),  "currency": "USD"},  # preserved
]

# Daily-dense PRICE_ANCHORS, built at module load from the quarterly source.
# ~15,000 entries (17 instruments × ~900 days, varying per-instrument start dates).
PRICE_ANCHORS: list[dict] = _interpolate_daily(_PRICE_ANCHORS_QUARTERLY)

# ---------------------------------------------------------------------------
# APY configurations — Revolut Earn ETH (2.37%) + USDC (4.80%), effective 2024-11-01
# so ~18 months of monthly auto-accrual yield seeds cleanly.
# ---------------------------------------------------------------------------

APY_CONFIGS: list[dict] = [
    {"account": "Revolut Earn", "symbol": "ETH",  "apy_rate": Decimal("0.0237"), "effective_from": date(2024, 11, 1)},
    {"account": "Revolut Earn", "symbol": "USDC", "apy_rate": Decimal("0.0480"), "effective_from": date(2024, 11, 1)},
]


# ---------------------------------------------------------------------------
# Helpers used while constructing TRANSACTIONS
# ---------------------------------------------------------------------------

def _month_step(start: date, months_offset: int, day: int) -> date:
    """Return a date `months_offset` months after `start`, snapped to `day`.

    Used to generate monthly DCA cadences. Falls back to day=28 in February so
    we never hit the 30-Feb / 31-Apr trap. Pure stdlib — no dateutil dependency.
    """
    total = start.year * 12 + (start.month - 1) + months_offset
    y, m = divmod(total, 12)
    m += 1
    if m == 2 and day > 28:
        day = 28
    elif day == 31 and m in (4, 6, 9, 11):
        day = 30
    return date(y, m, day)


# ---------------------------------------------------------------------------
# TRANSACTIONS — ~150–180 rows in roughly chronological order.
#
# Two shapes:
#   - SINGLE-ROW (buy / spend / yield) — has top-level `txn_type` + `account` + `symbol`.
#   - DUAL-LEG TRADE — has top-level `"trade"` key containing sold/received leg dicts.
#     These route through app.services.trades.create_linked_trade.
#     Direct txn_type='sell' single-row entries are FORBIDDEN by the Pydantic
#     validator — every sell in the fixture MUST be the sold leg of a trade pair.
#
# PRESERVED VERBATIM (snapshot-anchor contract):
#   * AAPL Revolut 2025-08-15 qty 2 @ $190.50
#   * XRP Bit2Me 2025-06-15 qty 100 @ $0.5500
#   * XRP→USDC trade pair on 2025-10-20 (Bit2Me, 100 XRP @ $0.62 → 62 USDC, fee €0.10)
#   * USDC→ETH trade pair on 2025-12-01 (Bit2Me, 500 USDC → 0.135 ETH @ $3705, fee €0.75)
#   * SOL→USDC trade pair on 2026-03-20 (Bit2Me, 2 SOL @ $198.00 → 396 USDC, fee €0.50)
#   * USDC "VPS rental" spend on Revolut 2026-01-08 qty 50 @ $1.00
#   * MSCI-W manual yield on 2026-03-31 qty 0.10 notes "Distribution payment"
#   * Revolut Earn auto-accrual yields with notes containing "2.37%" / "4.80%"
# ---------------------------------------------------------------------------

TRANSACTIONS: list[dict] = []

# ===== EARLY HISTORY (2023-11 → 2024-04): crypto winter accumulation =====
TRANSACTIONS += [
    # Bit2Me — initial crypto positions during the winter
    {"account": "Bit2Me", "symbol": "BTC", "txn_type": "buy", "date": date(2023, 11, 8),  "quantity": Decimal("0.015"),  "unit_price": Decimal("36800.00"), "price_currency": "USD", "notes": "Winter accumulation"},
    {"account": "Bit2Me", "symbol": "ETH", "txn_type": "buy", "date": date(2023, 11, 20), "quantity": Decimal("0.40"),   "unit_price": Decimal("2050.00"),  "price_currency": "USD"},
    {"account": "Bit2Me", "symbol": "SOL", "txn_type": "buy", "date": date(2023, 12, 5),  "quantity": Decimal("8"),      "unit_price": Decimal("65.00"),    "price_currency": "USD"},
    {"account": "Bit2Me", "symbol": "TRX", "txn_type": "buy", "date": date(2023, 12, 18), "quantity": Decimal("3000"),   "unit_price": Decimal("0.1080"),   "price_currency": "USD"},
    # ADA — buy near peak so the eventual close-at-$0.18 lands underwater
    {"account": "Bit2Me", "symbol": "ADA", "txn_type": "buy", "date": date(2024, 3, 18),  "quantity": Decimal("1000"),   "unit_price": Decimal("0.5500"),   "price_currency": "USD", "notes": "DCA into ADA (would close at a loss in 2025)"},
    # Initial XTB stock positions
    {"account": "XTB", "symbol": "MSFT", "txn_type": "buy", "date": date(2024, 1, 12), "quantity": Decimal("3"), "unit_price": Decimal("385.20"), "price_currency": "USD"},
    {"account": "XTB", "symbol": "NVDA", "txn_type": "buy", "date": date(2024, 2, 7),  "quantity": Decimal("15"), "unit_price": Decimal("72.30"),  "price_currency": "USD", "notes": "Pre-AI-bull-run entry"},
    {"account": "XTB", "symbol": "ASML.AS", "txn_type": "buy", "date": date(2024, 2, 20), "quantity": Decimal("2"), "unit_price": Decimal("820.40"), "price_currency": "EUR"},
    {"account": "XTB", "symbol": "SAP.DE",  "txn_type": "buy", "date": date(2024, 3, 5),  "quantity": Decimal("8"), "unit_price": Decimal("168.50"), "price_currency": "EUR"},
    # Revolut stock toehold
    {"account": "Revolut", "symbol": "AAPL", "txn_type": "buy", "date": date(2024, 1, 25), "quantity": Decimal("5"), "unit_price": Decimal("194.20"), "price_currency": "USD"},
    {"account": "Revolut", "symbol": "TSLA", "txn_type": "buy", "date": date(2024, 2, 14), "quantity": Decimal("4"), "unit_price": Decimal("196.40"), "price_currency": "USD", "notes": "Tactical entry — bought during Q1-24 drawdown"},
    # Liquido — initial money-market position (the "safe sleeve")
    {"account": "Liquido", "symbol": "MM-EUR", "txn_type": "buy", "date": date(2024, 1, 8), "quantity": Decimal("5000"), "unit_price": Decimal("1.0030"), "price_currency": "EUR", "notes": "Cash sleeve seed"},
]

# ===== MID 2024: VWCE DCA pillar starts (2024-05 → 2026-04 = 24 months) =====
_VWCE_START = date(2024, 5, 5)
_VWCE_DCA_AMOUNTS = [
    Decimal("2.10"), Decimal("2.05"), Decimal("2.15"), Decimal("2.20"), Decimal("2.10"), Decimal("2.25"),
    Decimal("2.15"), Decimal("2.20"), Decimal("2.10"), Decimal("2.25"), Decimal("2.30"), Decimal("2.20"),
    Decimal("2.15"), Decimal("2.10"), Decimal("2.25"), Decimal("2.30"), Decimal("2.20"), Decimal("2.35"),
    Decimal("2.25"), Decimal("2.30"), Decimal("2.40"), Decimal("2.35"), Decimal("2.30"), Decimal("2.45"),
]
_VWCE_DCA_PRICES = [
    Decimal("110.40"), Decimal("112.80"), Decimal("113.60"), Decimal("115.20"), Decimal("115.80"), Decimal("117.10"),
    Decimal("117.90"), Decimal("118.40"), Decimal("118.10"), Decimal("116.80"), Decimal("114.50"), Decimal("113.20"),
    Decimal("115.30"), Decimal("117.40"), Decimal("119.20"), Decimal("120.10"), Decimal("121.40"), Decimal("122.80"),
    Decimal("123.60"), Decimal("125.10"), Decimal("125.80"), Decimal("126.40"), Decimal("127.20"), Decimal("128.10"),
]
for i, (qty, price) in enumerate(zip(_VWCE_DCA_AMOUNTS, _VWCE_DCA_PRICES)):
    TRANSACTIONS.append({
        "account":        "MyInvestor",
        "symbol":         "VWCE",
        "txn_type":       "buy",
        "date":           _month_step(_VWCE_START, i, 5),
        "quantity":       qty,
        "unit_price":     price,
        "price_currency": "EUR",
        "notes":          "Monthly VWCE DCA",
    })

# ===== 2024-Q3/Q4: more crypto buys, US stock continuation =====
TRANSACTIONS += [
    {"account": "Bit2Me",      "symbol": "BTC", "txn_type": "buy", "date": date(2024, 7, 12), "quantity": Decimal("0.008"), "unit_price": Decimal("61500.00"), "price_currency": "USD"},
    {"account": "Bit2Me",      "symbol": "ETH", "txn_type": "buy", "date": date(2024, 8, 20), "quantity": Decimal("0.35"),  "unit_price": Decimal("2580.00"),  "price_currency": "USD"},
    {"account": "Bit2Me",      "symbol": "SOL", "txn_type": "buy", "date": date(2024, 9, 8),  "quantity": Decimal("3"),     "unit_price": Decimal("148.20"),   "price_currency": "USD"},
    {"account": "Cold Wallet", "symbol": "BTC", "txn_type": "buy", "date": date(2024, 10, 12), "quantity": Decimal("0.01"), "unit_price": Decimal("66200.00"), "price_currency": "USD", "notes": "Transfer to cold storage"},
    {"account": "Cold Wallet", "symbol": "ETH", "txn_type": "buy", "date": date(2024, 11, 18), "quantity": Decimal("0.20"), "unit_price": Decimal("2880.00"),  "price_currency": "USD", "notes": "Transfer to cold storage"},
    # USDC stockpile on Bit2Me — earlier lot supports a 2025-08 USDC→SOL redeploy
    {"account": "Bit2Me", "symbol": "USDC", "txn_type": "buy", "date": date(2024, 10, 15), "quantity": Decimal("300.00"), "unit_price": Decimal("1.0000"), "price_currency": "USD"},
    # Revolut Earn — initial deposits that the auto-accrual job will yield against
    {"account": "Revolut Earn", "symbol": "ETH",  "txn_type": "buy", "date": date(2024, 11, 1), "quantity": Decimal("0.30"),    "unit_price": Decimal("2540.00"), "price_currency": "USD", "notes": "Earn deposit"},
    {"account": "Revolut Earn", "symbol": "USDC", "txn_type": "buy", "date": date(2024, 11, 1), "quantity": Decimal("600.00"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "Earn deposit"},
    # Additional XTB / Revolut stock buys
    {"account": "XTB",     "symbol": "NVDA",  "txn_type": "buy", "date": date(2024, 9, 25),  "quantity": Decimal("12"), "unit_price": Decimal("118.40"), "price_currency": "USD"},
    {"account": "XTB",     "symbol": "GOOGL", "txn_type": "buy", "date": date(2024, 10, 8),  "quantity": Decimal("6"),  "unit_price": Decimal("164.30"), "price_currency": "USD"},
    {"account": "Revolut", "symbol": "AMZN",  "txn_type": "buy", "date": date(2024, 11, 12), "quantity": Decimal("4"),  "unit_price": Decimal("198.10"), "price_currency": "USD"},
    {"account": "Revolut", "symbol": "AAPL",  "txn_type": "buy", "date": date(2024, 12, 5),  "quantity": Decimal("3"),  "unit_price": Decimal("242.80"), "price_currency": "USD"},
]

# ===== SXR8 DCA pillar — 18 months (2024-11 → 2026-04) =====
_SXR8_START = date(2024, 11, 12)
_SXR8_DCA_AMOUNTS = [
    Decimal("0.42"), Decimal("0.40"), Decimal("0.41"), Decimal("0.43"), Decimal("0.42"), Decimal("0.40"),
    Decimal("0.41"), Decimal("0.42"), Decimal("0.43"), Decimal("0.42"), Decimal("0.40"), Decimal("0.41"),
    Decimal("0.42"), Decimal("0.43"), Decimal("0.42"), Decimal("0.40"), Decimal("0.41"), Decimal("0.42"),
]
_SXR8_DCA_PRICES = [
    Decimal("542.10"), Decimal("552.30"), Decimal("549.80"), Decimal("541.20"), Decimal("531.20"), Decimal("548.60"),
    Decimal("558.20"), Decimal("568.40"), Decimal("578.10"), Decimal("584.50"), Decimal("590.10"), Decimal("596.40"),
    Decimal("602.20"), Decimal("608.30"), Decimal("611.50"), Decimal("613.80"), Decimal("615.80"), Decimal("619.20"),
]
for i, (qty, price) in enumerate(zip(_SXR8_DCA_AMOUNTS, _SXR8_DCA_PRICES)):
    TRANSACTIONS.append({
        "account":        "MyInvestor",
        "symbol":         "SXR8",
        "txn_type":       "buy",
        "date":           _month_step(_SXR8_START, i, 12),
        "quantity":       qty,
        "unit_price":     price,
        "price_currency": "EUR",
        "notes":          "Monthly SXR8 DCA",
    })

# ===== 2025: continued buys + preserved snapshot anchors =====
TRANSACTIONS += [
    # Preserved snapshot-anchor rows — DO NOT CHANGE
    {"account": "Revolut",    "symbol": "AAPL",   "txn_type": "buy", "date": date(2025, 5, 15),  "quantity": Decimal("3"),       "unit_price": Decimal("178.00"),   "price_currency": "USD"},
    {"account": "Revolut",    "symbol": "AAPL",   "txn_type": "buy", "date": date(2025, 8, 15),  "quantity": Decimal("2"),       "unit_price": Decimal("190.50"),   "price_currency": "USD"},
    {"account": "XTB",        "symbol": "MSFT",   "txn_type": "buy", "date": date(2025, 6, 10),  "quantity": Decimal("2"),       "unit_price": Decimal("412.00"),   "price_currency": "USD"},
    {"account": "MyInvestor", "symbol": "MSCI-W", "txn_type": "buy", "date": date(2025, 7, 1),   "quantity": Decimal("5"),       "unit_price": Decimal("215.00"),   "price_currency": "EUR"},
    {"account": "Bit2Me",     "symbol": "BTC",    "txn_type": "buy", "date": date(2025, 5, 20),  "quantity": Decimal("0.01"),    "unit_price": Decimal("63500.00"), "price_currency": "USD"},
    {"account": "Bit2Me",     "symbol": "ETH",    "txn_type": "buy", "date": date(2025, 6, 5),   "quantity": Decimal("0.5"),     "unit_price": Decimal("3150.00"),  "price_currency": "USD"},
    {"account": "Cold Wallet","symbol": "BTC",    "txn_type": "buy", "date": date(2025, 11, 12), "quantity": Decimal("0.005"),   "unit_price": Decimal("68000.00"), "price_currency": "USD"},
    # USDC stockpile on Bit2Me — parent lots for the trade pairs
    {"account": "Bit2Me",     "symbol": "USDC",   "txn_type": "buy", "date": date(2025, 7, 1),   "quantity": Decimal("1000.00"), "unit_price": Decimal("1.0000"),   "price_currency": "USD"},
    {"account": "Revolut",    "symbol": "USDC",   "txn_type": "buy", "date": date(2025, 12, 15), "quantity": Decimal("200.00"),  "unit_price": Decimal("1.0000"),   "price_currency": "USD"},
    # Pre-trade SOL lot for the SOL→USDC preserved trade pair
    {"account": "Bit2Me",     "symbol": "SOL",    "txn_type": "buy", "date": date(2025, 9, 10),  "quantity": Decimal("4"),       "unit_price": Decimal("160.00"),   "price_currency": "USD"},
    # XRP — pre-trade lot for the closed-position fixture
    {"account": "Bit2Me",     "symbol": "XRP",    "txn_type": "buy", "date": date(2025, 6, 15),  "quantity": Decimal("100"),     "unit_price": Decimal("0.5500"),   "price_currency": "USD"},

    # === Additional 2025 buys filling out the story ===
    # US stocks
    {"account": "Revolut", "symbol": "NVDA",  "txn_type": "buy", "date": date(2025, 2, 18),  "quantity": Decimal("8"),  "unit_price": Decimal("122.40"), "price_currency": "USD"},
    {"account": "Revolut", "symbol": "GOOGL", "txn_type": "buy", "date": date(2025, 3, 10),  "quantity": Decimal("5"),  "unit_price": Decimal("159.80"), "price_currency": "USD"},
    {"account": "XTB",     "symbol": "AMZN",  "txn_type": "buy", "date": date(2025, 4, 22),  "quantity": Decimal("3"),  "unit_price": Decimal("184.60"), "price_currency": "USD"},
    {"account": "Revolut", "symbol": "TSLA",  "txn_type": "buy", "date": date(2025, 1, 28),  "quantity": Decimal("3"),  "unit_price": Decimal("245.20"), "price_currency": "USD", "notes": "Swing add — would close underwater"},
    {"account": "XTB",     "symbol": "AAPL",  "txn_type": "buy", "date": date(2025, 6, 24),  "quantity": Decimal("4"),  "unit_price": Decimal("194.30"), "price_currency": "USD"},
    {"account": "XTB",     "symbol": "MSFT",  "txn_type": "buy", "date": date(2025, 9, 18),  "quantity": Decimal("2"),  "unit_price": Decimal("442.10"), "price_currency": "USD"},
    {"account": "XTB",     "symbol": "ASML.AS","txn_type": "buy", "date": date(2025, 7, 15),  "quantity": Decimal("1"),  "unit_price": Decimal("718.20"), "price_currency": "EUR"},
    {"account": "XTB",     "symbol": "SAP.DE", "txn_type": "buy", "date": date(2025, 8, 6),   "quantity": Decimal("4"),  "unit_price": Decimal("228.40"), "price_currency": "EUR"},
    # Crypto top-ups
    {"account": "Bit2Me", "symbol": "ETH", "txn_type": "buy", "date": date(2025, 8, 4),  "quantity": Decimal("0.25"), "unit_price": Decimal("3580.00"), "price_currency": "USD"},
    {"account": "Bit2Me", "symbol": "BTC", "txn_type": "buy", "date": date(2025, 10, 2), "quantity": Decimal("0.004"), "unit_price": Decimal("87400.00"), "price_currency": "USD"},
    {"account": "Bit2Me", "symbol": "TRX", "txn_type": "buy", "date": date(2025, 6, 28), "quantity": Decimal("2000"),  "unit_price": Decimal("0.1160"),   "price_currency": "USD"},
    # Cold-wallet self-custody transfers
    {"account": "Cold Wallet", "symbol": "SOL", "txn_type": "buy", "date": date(2025, 7, 8),  "quantity": Decimal("2"), "unit_price": Decimal("175.40"), "price_currency": "USD", "notes": "Self-custody transfer"},
    {"account": "Cold Wallet", "symbol": "ETH", "txn_type": "buy", "date": date(2025, 10, 5), "quantity": Decimal("0.15"), "unit_price": Decimal("3960.00"), "price_currency": "USD", "notes": "Self-custody transfer"},
    # Revolut Earn top-up
    {"account": "Revolut Earn", "symbol": "USDC", "txn_type": "buy", "date": date(2025, 5, 14), "quantity": Decimal("400.00"), "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "Earn top-up"},
]

# ===== Liquido MM-EUR cadence — monthly small buys + monthly manual yield (12+12 = 24 rows) =====
_MM_START = date(2024, 5, 8)  # First monthly add after the initial 2024-01 seed
for i in range(12):
    d = _month_step(_MM_START, i * 2, 8)  # bi-monthly buys to keep count tractable
    # Small monthly buys: 200–350 EUR
    qty = Decimal("200.00") + Decimal(str(i * 12))
    # NAV gently drifts from 1.005 → 1.040 over the period
    nav = (Decimal("1.005") + Decimal(str(i)) * Decimal("0.003")).quantize(Decimal("0.0001"))
    TRANSACTIONS.append({
        "account":        "Liquido",
        "symbol":         "MM-EUR",
        "txn_type":       "buy",
        "date":           d,
        "quantity":       qty,
        "unit_price":     nav,
        "price_currency": "EUR",
        "notes":          "MM-EUR top-up",
    })
# 12 monthly manual yield drips on MM-EUR (Liquido, no APY config — pure manual entry)
_MMYIELD_START = date(2025, 5, 28)
for i in range(12):
    d = _month_step(_MMYIELD_START, i, 28)
    # Small monthly yield: grows from 0.5 → 1.5 as the balance grows
    yqty = (Decimal("0.50") + Decimal(str(i)) * Decimal("0.08")).quantize(Decimal("0.01"))
    TRANSACTIONS.append({
        "account":  "Liquido",
        "symbol":   "MM-EUR",
        "txn_type": "yield",
        "date":     d,
        "quantity": yqty,
        # Manual yield in EUR — exercises the manual-yield-in-EUR seeder branch
        "unit_price":     Decimal("1.0300"),
        "price_currency": "EUR",
        "source":         "manual",
        "notes":          "MM-EUR yield accrual",
    })

# ===== Spends on Revolut USDC (~9 total) =====
# Preserved verbatim: the "VPS rental" spend on 2026-01-08
TRANSACTIONS += [
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2026, 1, 8),  "quantity": Decimal("50.00"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "VPS rental"},
    # Other monthly small spends (use USDC parent lot on Revolut bought 2025-12-15 qty 200)
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2026, 2, 12), "quantity": Decimal("12.00"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "Domain renewal"},
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2026, 3, 9),  "quantity": Decimal("18.50"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "SaaS subscription"},
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2026, 4, 6),  "quantity": Decimal("9.20"),   "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "Coffee subscription"},
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2025, 9, 22), "quantity": Decimal("22.00"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "Mobile top-up"},
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2025, 11, 5), "quantity": Decimal("14.50"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "VPS rental"},
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2025, 12, 28), "quantity": Decimal("28.00"), "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "Storage upgrade"},
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2026, 2, 28), "quantity": Decimal("16.30"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "Email service"},
    # One annual large spend — tax / hardware
    {"account": "Revolut", "symbol": "USDC", "txn_type": "spend", "date": date(2026, 3, 25), "quantity": Decimal("9.50"),   "unit_price": Decimal("1.0000"), "price_currency": "USD", "notes": "GitHub subscription"},
]

# ===== Trade pairs (10 pairs) =====
# Preserved verbatim first, then new pairs.
TRANSACTIONS += [
    # Trade pair 1: XRP→USDC closed-position fixture (PRESERVED VERBATIM)
    {"trade": {
        "sold":     {"account": "Bit2Me", "symbol": "XRP",  "quantity": Decimal("100"),    "unit_price": Decimal("0.6200"), "price_currency": "USD", "fee_eur": Decimal("0.10")},
        "received": {"account": "Bit2Me", "symbol": "USDC", "quantity": Decimal("62.00"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "fee_eur": Decimal("0.10")},
        "date":     date(2025, 10, 20),
        "notes":    "XRP->USDC realize (closed position fixture)",
    }},
    # Trade pair 2: USDC→ETH rebalance (PRESERVED VERBATIM)
    {"trade": {
        "sold":     {"account": "Bit2Me", "symbol": "USDC", "quantity": Decimal("500.00"), "unit_price": Decimal("1.0000"), "price_currency": "USD", "fee_eur": Decimal("0.75")},
        "received": {"account": "Bit2Me", "symbol": "ETH",  "quantity": Decimal("0.135"),  "unit_price": Decimal("3705.00"),"price_currency": "USD", "fee_eur": Decimal("0.75")},
        "date":     date(2025, 12, 1),
        "notes":    "USDC->ETH rebalance",
    }},
    # Trade pair 3: SOL→USDC realize (PRESERVED VERBATIM)
    {"trade": {
        "sold":     {"account": "Bit2Me", "symbol": "SOL",  "quantity": Decimal("2"),       "unit_price": Decimal("198.00"), "price_currency": "USD", "fee_eur": Decimal("0.50")},
        "received": {"account": "Bit2Me", "symbol": "USDC", "quantity": Decimal("396.00"),  "unit_price": Decimal("1.0000"), "price_currency": "USD", "fee_eur": Decimal("0.50")},
        "date":     date(2026, 3, 20),
        "notes":    "SOL->USDC realize",
    }},
    # Trade pair 4: AAPL partial trim (winner) — sell 1 AAPL @ $215 → 215 USDC on Revolut
    # The AAPL was bought at $194.20 (2024-01) and $178.00 (2025-05-15) — earliest lot is $194.20.
    # Realizing the $194.20 lot at $215 yields ~$20 gain per share.
    {"trade": {
        "sold":     {"account": "Revolut", "symbol": "AAPL", "quantity": Decimal("1"),     "unit_price": Decimal("215.00"), "price_currency": "USD", "fee_eur": Decimal("0.45")},
        "received": {"account": "Revolut", "symbol": "USDC", "quantity": Decimal("215.00"),"unit_price": Decimal("1.0000"), "price_currency": "USD", "fee_eur": Decimal("0.45")},
        "date":     date(2025, 9, 18),
        "notes":    "AAPL partial trim",
    }},
    # Trade pair 5: NVDA partial take-profit (winner) — sell 5 NVDA on XTB at $158
    # NVDA on XTB has lots at $72.30 (15 units) and $118.40 (12 units) — earliest is $72.30.
    # Realizing 5 of the $72.30 lot at $158 ≈ $430 USDC. Sent to Revolut USDC for convenience.
    {"trade": {
        "sold":     {"account": "XTB", "symbol": "NVDA", "quantity": Decimal("5"),     "unit_price": Decimal("158.00"), "price_currency": "USD", "fee_eur": Decimal("1.20")},
        "received": {"account": "XTB", "symbol": "USDC", "quantity": Decimal("790.00"),"unit_price": Decimal("1.0000"), "price_currency": "USD", "fee_eur": Decimal("1.20")},
        "date":     date(2025, 11, 14),
        "notes":    "NVDA partial take-profit",
    }},
    # Trade pair 6: BTC partial take-profit (winner) — Bit2Me, sell 0.003 BTC @ $96k
    # Bit2Me BTC lots: 0.015@$36800 (2023-11), 0.008@$61500 (2024-07), 0.01@$63500 (2025-05), 0.004@$87400 (2025-10)
    # Plenty of qty; FIFO consumes earliest (huge gain).
    {"trade": {
        "sold":     {"account": "Bit2Me", "symbol": "BTC",  "quantity": Decimal("0.003"), "unit_price": Decimal("96400.00"),"price_currency": "USD", "fee_eur": Decimal("1.10")},
        "received": {"account": "Bit2Me", "symbol": "USDC", "quantity": Decimal("289.20"),"unit_price": Decimal("1.0000"),  "price_currency": "USD", "fee_eur": Decimal("1.10")},
        "date":     date(2025, 12, 18),
        "notes":    "BTC partial take-profit",
    }},
    # Trade pair 7: TSLA swing trade closed underwater (loser) — Revolut, sell 3 @ $190
    # The 2025-01-28 buy of 3 TSLA @ $245.20 → swing-add lot; FIFO would actually consume the
    # earlier 2024-02-14 lot first (4 units @ $196.40, smaller loss). To make the loss honest
    # we sell ONLY 3 units — FIFO takes 3 of the $196.40 lot, realizing $190-$196.40 = -$6.40
    # per share. Modest documented loser, contrasts with the AAPL winner.
    {"trade": {
        "sold":     {"account": "Revolut", "symbol": "TSLA", "quantity": Decimal("3"),     "unit_price": Decimal("190.20"), "price_currency": "USD", "fee_eur": Decimal("0.70")},
        "received": {"account": "Revolut", "symbol": "USDC", "quantity": Decimal("570.60"),"unit_price": Decimal("1.0000"), "price_currency": "USD", "fee_eur": Decimal("0.70")},
        "date":     date(2025, 10, 8),
        "notes":    "TSLA swing closed underwater",
    }},
    # Trade pair 8: ADA full close at a loss (loser) — Bit2Me, sell ALL 1000 ADA @ $0.18
    # ADA was bought 2024-03 @ $0.55 (1000 units). Close at $0.18 realizes ~-$370 per net.
    # Net qty after this = 0 → closed position (loss story for crypto).
    {"trade": {
        "sold":     {"account": "Bit2Me", "symbol": "ADA",  "quantity": Decimal("1000"),   "unit_price": Decimal("0.1800"), "price_currency": "USD", "fee_eur": Decimal("0.40")},
        "received": {"account": "Bit2Me", "symbol": "USDC", "quantity": Decimal("180.00"), "unit_price": Decimal("1.0000"), "price_currency": "USD", "fee_eur": Decimal("0.40")},
        "date":     date(2025, 8, 22),
        "notes":    "ADA full close at a loss",
    }},
    # Trade pair 9: BTC→USDC partial rebalance — Bit2Me, sell 0.002 BTC @ $80k
    # Plenty of BTC qty remains after pair 6.
    {"trade": {
        "sold":     {"account": "Bit2Me", "symbol": "BTC",  "quantity": Decimal("0.002"), "unit_price": Decimal("80100.00"),"price_currency": "USD", "fee_eur": Decimal("0.95")},
        "received": {"account": "Bit2Me", "symbol": "USDC", "quantity": Decimal("160.20"),"unit_price": Decimal("1.0000"),  "price_currency": "USD", "fee_eur": Decimal("0.95")},
        "date":     date(2026, 2, 5),
        "notes":    "BTC->USDC rebalance",
    }},
    # Trade pair 10: USDC→SOL stablecoin redeploy — Bit2Me, sell 200 USDC → ~1.14 SOL @ $175
    # Uses the 2024-10-15 USDC lot (300 units @ $1.00).
    {"trade": {
        "sold":     {"account": "Bit2Me", "symbol": "USDC", "quantity": Decimal("200.00"), "unit_price": Decimal("1.0000"), "price_currency": "USD", "fee_eur": Decimal("0.45")},
        "received": {"account": "Bit2Me", "symbol": "SOL",  "quantity": Decimal("1.14"),   "unit_price": Decimal("175.40"), "price_currency": "USD", "fee_eur": Decimal("0.45")},
        "date":     date(2025, 1, 15),
        "notes":    "USDC->SOL stablecoin redeploy",
    }},
]

# ===== Auto-accrual yield drip on Revolut Earn — 18 months × 2 instruments = 36 rows =====
# These are source="accrual" with notes containing the APY rate so the row-yield-auto-accrual
# snapshot filter ["ETH", "Revolut Earn", "2.37%"] resolves a deterministic row.
_ACCRUAL_START = date(2024, 12, 25)  # First accrual a few weeks after the 2024-11-01 deposits
for i in range(18):
    d = _month_step(_ACCRUAL_START, i, 25)
    # ETH yield: small constant on top of the 0.30 ETH balance (~0.30 * 0.0237 / 12 ≈ 0.000593)
    # Scaled up slightly to make the numbers diff-able yet realistic.
    eth_qty = (Decimal("0.000590") + Decimal(str(i)) * Decimal("0.0000015")).quantize(Decimal("0.000001"))
    TRANSACTIONS.append({
        "account":  "Revolut Earn",
        "symbol":   "ETH",
        "txn_type": "yield",
        "date":     d,
        "quantity": eth_qty,
        "source":   "accrual",
        "notes":    "auto-accrual 2.37% APY",
    })
    # USDC yield: grows linearly as the running USDC balance grows
    usdc_qty = (Decimal("0.10") + Decimal(str(i)) * Decimal("0.018")).quantize(Decimal("0.01"))
    TRANSACTIONS.append({
        "account":  "Revolut Earn",
        "symbol":   "USDC",
        "txn_type": "yield",
        "date":     d,
        "quantity": usdc_qty,
        "source":   "accrual",
        "notes":    "auto-accrual 4.80% APY",
    })

# ===== MSCI-W quarterly manual distributions (3 rows) =====
TRANSACTIONS += [
    {"account": "MyInvestor", "symbol": "MSCI-W", "txn_type": "yield", "date": date(2025, 9, 30),  "quantity": Decimal("0.08"), "unit_price": Decimal("220.80"), "price_currency": "EUR", "source": "manual", "notes": "Distribution payment"},
    {"account": "MyInvestor", "symbol": "MSCI-W", "txn_type": "yield", "date": date(2025, 12, 31), "quantity": Decimal("0.09"), "unit_price": Decimal("226.30"), "price_currency": "EUR", "source": "manual", "notes": "Distribution payment"},
    # Preserved snapshot anchor: MSCI-W manual yield 0.10 on 2026-03-31 "Distribution payment"
    {"account": "MyInvestor", "symbol": "MSCI-W", "txn_type": "yield", "date": date(2026, 3, 31),  "quantity": Decimal("0.10"), "unit_price": Decimal("232.60"), "price_currency": "EUR", "source": "manual", "notes": "Distribution payment"},
]

# ---------------------------------------------------------------------------
# FX curve helper — produces a deterministic, smooth EUR→USD rate.
#
# Baseline 1.08 + linear drift to ~1.17 by 2026-04-30 + sinusoidal ±0.02 variance
# with a ~180-day period. Quantized to 4dp for diffability.
#
# All math via Decimal to keep monetary precision; math.sin output is converted
# from float via str() before Decimal coercion (zero-effort precision loss is
# acceptable for an FX synth curve quantized to 4dp).
# ---------------------------------------------------------------------------

def _fx_curve(d: date) -> Decimal:
    """EUR→USD synthetic rate for a given date.

    Curve = 1.08 baseline + (days-since-epoch * 0.0001) linear drift + 0.02 sinusoid
    with a 180-day period. Returns a Decimal quantized to 4 decimal places.
    """
    epoch = date(2023, 11, 1)
    days = (d - epoch).days
    drift = Decimal("1.08") + (Decimal(days) * Decimal("0.0001"))
    variance = Decimal(str(0.02 * math.sin(days * math.pi / 180.0)))
    return (drift + variance).quantize(Decimal("0.0001"))


# ---------------------------------------------------------------------------
# FX_ANCHORS — daily EUR→USD rates over the full fixture date range so the
# seeder always has an exact-date match regardless of which USD txn dates the
# transaction list covers.
#
# Range: 2023-11-01 (epoch) → max(today, 2026-04-30 frozen anchor, latest
# quarterly price anchor). The 2026-04-30 anchor is then forced verbatim to
# Decimal("1.1761") (other fixtures pin that exact rate).
# ---------------------------------------------------------------------------

_fx_start: date = date(2023, 11, 1)
_fx_end: date = max(
    date.today(),
    date(2026, 4, 30),
    max(e["date"] for e in _PRICE_ANCHORS_QUARTERLY),
)
_fx_span_days: int = (_fx_end - _fx_start).days

FX_ANCHORS: list[dict] = [
    {
        "base": "EUR",
        "quote": "USD",
        "date": _fx_start + timedelta(days=i),
        "rate": _fx_curve(_fx_start + timedelta(days=i)),
    }
    for i in range(_fx_span_days + 1)  # inclusive of _fx_end
]

# Override the 2026-04-30 anchor with the published frozen-instant rate (preserved verbatim).
FX_ANCHORS = [a for a in FX_ANCHORS if a["date"] != date(2026, 4, 30)] + [
    {"base": "EUR", "quote": "USD", "date": date(2026, 4, 30), "rate": Decimal("1.1761")}
]
FX_ANCHORS.sort(key=lambda r: r["date"])


__all__ = [
    "ACCOUNTS", "INSTRUMENTS", "FX_ANCHORS", "PRICE_ANCHORS", "APY_CONFIGS",
    "TRANSACTIONS",
    "FIXTURE_EPOCH",
    "FIXTURE_FROZEN_NOW",
    "FIXTURE_INSTRUMENT_NAMESPACE",
    "FIXTURE_ACCOUNT_NAMESPACE", "FIXTURE_FX_NAMESPACE",
    "FIXTURE_PRICE_QUOTE_NAMESPACE", "FIXTURE_APY_CONFIG_NAMESPACE",
    "FIXTURE_TRANSACTION_NAMESPACE", "FIXTURE_LOT_ALLOC_NAMESPACE",
    "FIXTURE_TRADE_PAIR_NAMESPACE",
    "instrument_id_for",
    "account_id_for", "fx_rate_id_for", "price_quote_id_for",
    "apy_config_id_for", "transaction_id_for", "lot_alloc_id_for",
    "trade_pair_id_for",
]
