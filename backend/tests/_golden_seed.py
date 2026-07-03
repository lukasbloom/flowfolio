"""Reusable in-memory seeder for the golden-portfolio dataset.

Materializes ``scripts/fixtures/golden_portfolio.py`` (ACCOUNTS / INSTRUMENTS /
FX_ANCHORS / PRICE_ANCHORS / TRANSACTIONS) into a fresh in-memory AsyncSession,
mirroring ``scripts/seed-golden.py::_seed`` but without the alembic / file /
httpx machinery. Used by ``test_dashboard_golden_equivalence.py`` to snapshot
the six dashboard read-path services' output as the byte-identity baseline.

Equivalence notes vs. ``seed-golden.py::_seed``:
- FX rows are inserted first (from ``FX_ANCHORS``); transaction ``fx_rate_to_eur``
  is then resolved from an in-memory ``{date: rate}`` map built from the same
  anchors. ``FX_ANCHORS`` is daily-dense over the full fixture range, so every
  USD-txn date has an exact-date entry — this is precisely the cache-hit path
  ``get_or_fetch_fx_rate`` takes in the real seeder (no walk-back ever fires).
- ``cost_basis_eur`` is computed via the production ``compute_cost_basis`` —
  byte-identical to the seeder.
- FIFO matching for sells/spends uses the production
  ``app.services.fifo.match_lots_for_sell`` (mirrors ``conftest.make_transaction``).
  The seeder's ``_match_lots_for_sell_deterministic`` only differs in the
  LotAlloc id / created_at it assigns — the matching algorithm, matched
  quantities, and ``realized_gain_eur`` values are identical, and analytics
  never read LotAlloc.id, so the production matcher yields equivalent rows.
- ``created_at`` defaults to ``func.now()`` per row here (vs. the seeder's frozen
  FIXTURE_EPOCH). FIFO's ``(date asc, created_at asc)`` tiebreak still resolves
  to insertion order because we insert in date-sorted order — same relative
  ordering the frozen-epoch seeder gets from its date-sorted insert loop.
"""
from __future__ import annotations

import sys
from datetime import date as date_t
from decimal import Decimal
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

# Repo root = backend/tests/../../  (mirror test_golden_portfolio_fixture.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.models import (  # noqa: E402
    Account,
    FxRate,
    Instrument,
    PriceQuote,
    Transaction,
)
from app.services.cost_basis import compute_cost_basis  # noqa: E402
from app.services.fifo import match_lots_for_sell  # noqa: E402
from scripts.fixtures.golden_portfolio import (  # noqa: E402
    ACCOUNTS,
    FIXTURE_FROZEN_NOW,
    FX_ANCHORS,
    INSTRUMENTS,
    PRICE_ANCHORS,
    TRANSACTIONS,
    account_id_for,
    instrument_id_for,
)


async def seed_golden(session: AsyncSession) -> dict[str, dict[str, str]]:
    """Seed the golden portfolio into ``session``.

    Returns id-lookup maps the equivalence tests may need:
        {"accounts": {name: id}, "instruments": {symbol: id}}
    """
    # ---- 1. Accounts ----------------------------------------------------
    # Use the fixture's DETERMINISTIC uuid5 id helpers so Account/Instrument
    # ids are byte-stable across runs — random uuid4 ids would make the JSON
    # snapshot (which embeds account_id/instrument_id) non-reproducible and
    # also perturb the GROUP-BY holding order the services iterate.
    account_id_by_name: dict[str, str] = {}
    for a in ACCOUNTS:
        acct = Account(
            id=account_id_for(a["name"]),
            name=a["name"],
            account_type=a["account_type"],
        )
        session.add(acct)
        await session.flush()
        account_id_by_name[a["name"]] = acct.id

    # ---- 2. Instruments -------------------------------------------------
    instrument_id_by_symbol: dict[str, str] = {}
    for i in INSTRUMENTS:
        inst = Instrument(
            id=instrument_id_for(i["symbol"]),
            symbol=i["symbol"],
            name=i["name"],
            instrument_type=i["instrument_type"],
            base_currency=i["base_currency"],
            price_source=i["price_source"],
        )
        session.add(inst)
        await session.flush()
        instrument_id_by_symbol[i["symbol"]] = inst.id

    # ---- 3. FX rates (EUR/USD daily-dense) ------------------------------
    # Insert first so transaction FX auto-lock below is a pure dict lookup
    # (the cache-hit path of the real seeder's get_or_fetch_fx_rate).
    fx_rate_by_date: dict[date_t, Decimal] = {}
    for fx in FX_ANCHORS:
        session.add(
            FxRate(
                base_currency=fx["base"],
                quote_currency=fx["quote"],
                date=fx["date"],
                rate=fx["rate"],
                source="manual",
                fetched_at=FIXTURE_FROZEN_NOW,
            )
        )
        if fx["base"] == "EUR" and fx["quote"] == "USD":
            fx_rate_by_date[fx["date"]] = fx["rate"]
    await session.flush()

    # ---- 4. Price quotes (manual anchors, daily-dense) ------------------
    for p in PRICE_ANCHORS:
        session.add(
            PriceQuote(
                instrument_id=instrument_id_by_symbol[p["symbol"]],
                date=p["date"],
                price=p["price"],
                currency=p["currency"],
                source="manual",
                # Pin fetched_at so perf's current_price_fetched_at is stable
                # across runs (matches the seeder's FIXTURE_FROZEN_NOW).
                fetched_at=FIXTURE_FROZEN_NOW,
            )
        )
    await session.flush()

    # ---- 5. Transactions (date-sorted so FIFO sees lots in order) -------
    def _date_of(t: dict) -> date_t:
        return t["trade"]["date"] if "trade" in t else t["date"]

    sorted_txns = sorted(TRANSACTIONS, key=_date_of)

    def _resolve_fx(price_currency: str | None, on_date: date_t) -> Decimal | None:
        if price_currency == "EUR":
            return Decimal("1")
        if price_currency == "USD":
            return fx_rate_by_date[on_date]
        return None

    for t in sorted_txns:
        if "trade" in t:
            trade = t["trade"]
            trade_date = trade["date"]
            sold = trade["sold"]
            recv = trade["received"]

            sell_txn = Transaction(
                account_id=account_id_by_name[sold["account"]],
                instrument_id=instrument_id_by_symbol[sold["symbol"]],
                txn_type="sell",
                date=trade_date,
                quantity=-abs(sold["quantity"]),
                unit_price=sold["unit_price"],
                price_currency=sold["price_currency"],
                fx_rate_to_eur=sold.get("fx_rate_to_eur"),
                fee_eur=sold.get("fee_eur") or Decimal("0"),
                notes=trade.get("notes"),
            )
            buy_txn = Transaction(
                account_id=account_id_by_name[recv["account"]],
                instrument_id=instrument_id_by_symbol[recv["symbol"]],
                txn_type="buy",
                date=trade_date,
                quantity=abs(recv["quantity"]),
                unit_price=recv["unit_price"],
                price_currency=recv["price_currency"],
                fx_rate_to_eur=recv.get("fx_rate_to_eur"),
                fee_eur=recv.get("fee_eur") or Decimal("0"),
                notes=trade.get("notes"),
            )
            for txn, leg in ((sell_txn, sold), (buy_txn, recv)):
                if txn.fx_rate_to_eur is None:
                    txn.fx_rate_to_eur = _resolve_fx(leg["price_currency"], trade_date)
            sell_txn.cost_basis_eur = compute_cost_basis(sell_txn)
            buy_txn.cost_basis_eur = compute_cost_basis(buy_txn)
            session.add(sell_txn)
            session.add(buy_txn)
            await session.flush()
            await match_lots_for_sell(session, sell_txn)
            await session.flush()
        else:
            signed_qty = -t["quantity"] if t["txn_type"] == "spend" else t["quantity"]
            txn = Transaction(
                account_id=account_id_by_name[t["account"]],
                instrument_id=instrument_id_by_symbol[t["symbol"]],
                txn_type=t["txn_type"],
                date=t["date"],
                quantity=signed_qty,
                unit_price=t.get("unit_price"),
                price_currency=t.get("price_currency"),
                fx_rate_to_eur=t.get("fx_rate_to_eur"),
                fee_eur=t.get("fee_eur") or Decimal("0"),
                notes=t.get("notes"),
                source=t.get("source", "manual"),
            )
            if txn.fx_rate_to_eur is None:
                if t.get("price_currency") in ("EUR", "USD"):
                    txn.fx_rate_to_eur = _resolve_fx(t["price_currency"], t["date"])
                elif t["txn_type"] == "yield" and t.get("price_currency") is None:
                    # Mirror the seeder: yield rows with no price_currency get a
                    # placeholder EUR/USD rate from the same daily anchor map.
                    txn.fx_rate_to_eur = fx_rate_by_date[t["date"]]
            txn.cost_basis_eur = compute_cost_basis(txn)
            session.add(txn)
            await session.flush()
            if t["txn_type"] == "spend":
                await match_lots_for_sell(session, txn)
                await session.flush()

    await session.commit()

    return {
        "accounts": account_id_by_name,
        "instruments": instrument_id_by_symbol,
    }
