#!/usr/bin/env python
"""Seed the golden snapshot DB at tests/fixtures/golden.sqlite.

Run inside the api container (or against the api venv from the repo root) so
`from app...` and `from scripts.fixtures...` work:

    # In Docker (requires compose.test.yml):
    docker compose -f compose.yml -f compose.test.yml run --rm api python scripts/seed-golden.py

    # Locally (from repo root, with backend venv active):
    PYTHONPATH=./backend python scripts/seed-golden.py

The frontend npm script wraps this:

    cd frontend && npm run test:e2e:regen-db

First action is `alembic upgrade head` against an empty SQLite scratch file.
Auto-accrual yield rows go through the validated TransactionCreate surface.
Trade pairs go through app.services.trades.create_linked_trade.

Path resolution:
- In Docker (SEED_DOCKER=true or running as root with /data mounted): uses /data/ for scratch
- Locally: uses /tmp/ for scratch
- Output path: SEED_OUTPUT_PATH env var override, else auto-detected as
  tests/fixtures/golden.sqlite relative to the repo root (two levels above this script).
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

# Resolve repo root: this script lives at <repo_root>/scripts/seed-golden.py
_REPO_ROOT = Path(__file__).parent.parent.resolve()

# Scratch path: use /data/ if inside the Docker container, else /tmp/
_IN_DOCKER = Path("/data").exists() and os.getuid() == 0
SCRATCH_PATH = Path(os.environ.get(
    "SEED_SCRATCH_PATH",
    "/data/golden-build.sqlite" if _IN_DOCKER else "/tmp/golden-build.sqlite",
))

# Final output: env override or default to <repo_root>/tests/fixtures/golden.sqlite
FINAL_OUTPUT = Path(os.environ.get(
    "SEED_OUTPUT_PATH",
    str(_REPO_ROOT / "tests" / "fixtures" / "golden.sqlite"),
))


def _run_alembic_against(scratch: Path) -> None:
    """Run `alembic upgrade head` as a subprocess so DATABASE_URL override is hermetic."""
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{scratch}"}
    # When running locally, alembic.ini is in backend/
    alembic_cwd = "/app" if Path("/app/alembic.ini").exists() else str(_REPO_ROOT / "backend")
    # Invoke alembic as a module via the current interpreter. The runtime image
    # copies the alembic package but not its console script, and alembic is not on
    # PATH, so a bare `alembic` exe lookup fails there. `python -m alembic` works
    # anywhere the package is importable, both in Docker and in a local venv.
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=alembic_cwd,
        env=env,
        check=True,
    )


async def _seed(scratch: Path) -> dict:
    """Insert fixtures via the canonical validated surfaces. Returns counts."""
    # Critical: set DATABASE_URL BEFORE importing app modules so settings + engine
    # construction picks up the scratch path.
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{scratch}"
    # Disable hermetic guard for the seed process — FX rows seeded first means
    # no live fetch is attempted, but the guard would otherwise refuse the
    # cache-miss safety net.
    os.environ["FLOWFOLIO_NETWORK_HERMETIC"] = "false"
    # Minimal required settings for app imports
    os.environ.setdefault("SECRET_KEY", "seed-secret-key-not-for-production")

    import httpx
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from scripts.fixtures.golden_portfolio import (
        ACCOUNTS, APY_CONFIGS, FX_ANCHORS, INSTRUMENTS,
        PRICE_ANCHORS, TRANSACTIONS, FIXTURE_EPOCH, FIXTURE_FROZEN_NOW,
        account_id_for, apy_config_id_for, fx_rate_id_for,
        instrument_id_for, lot_alloc_id_for, price_quote_id_for,
        trade_pair_id_for, transaction_id_for,
    )

    from app.models.account import Account
    from app.models.apy_config import ApyConfig
    from app.models.fx_rate import FxRate
    from app.models.instrument import Instrument
    from app.models.lot_alloc import LotAlloc
    from app.models.price_quote import PriceQuote
    from app.models.transaction import Transaction
    from app.schemas.account import AccountCreate
    from app.schemas.instrument import InstrumentCreate
    from app.schemas.transaction import TransactionCreate
    from app.services.cost_basis import compute_cost_basis
    from app.services.fx import get_or_fetch_fx_rate
    # NOTE: match_lots_for_sell + create_linked_trade are intentionally NOT imported
    # — the seeder uses inline-replicate paths (_match_lots_for_sell_deterministic +
    # the trade-branch body below) so every row carries a deterministic id + frozen
    # timestamp from construction.

    async def _match_lots_for_sell_deterministic(
        session: AsyncSession,
        sell_txn: Transaction,
    ) -> list[LotAlloc]:
        """Inline replicate of app.services.fifo.match_lots_for_sell with deterministic
        ids + frozen created_at. The body MUST stay byte-equivalent in BEHAVIOR to the
        production service — only the LotAlloc(...) constructor is altered. If the
        production service's matching algorithm changes, this copy must be updated
        in lockstep (covered by the dump-diff diagnostic in Task 5)."""
        from sqlalchemy import func as sa_func  # avoid shadowing app.services.fx.func
        sell_qty = abs(sell_txn.quantity)

        stmt = (
            select(Transaction)
            .where(
                Transaction.account_id == sell_txn.account_id,
                Transaction.instrument_id == sell_txn.instrument_id,
                Transaction.txn_type.in_(("buy", "adjustment")),
                Transaction.quantity > 0,
                Transaction.deleted_at.is_(None),
            )
            .order_by(Transaction.date.asc(), Transaction.created_at.asc())
        )
        buy_txns = (await session.execute(stmt)).scalars().all()

        buy_txn_ids = [b.id for b in buy_txns]
        if buy_txn_ids:
            alloc_stmt = (
                select(LotAlloc.buy_txn_id, sa_func.sum(LotAlloc.quantity).label("consumed"))
                .where(LotAlloc.buy_txn_id.in_(buy_txn_ids))
                .group_by(LotAlloc.buy_txn_id)
            )
            consumed_by_lot = {
                row.buy_txn_id: row.consumed
                for row in (await session.execute(alloc_stmt))
            }
        else:
            consumed_by_lot = {}

        remaining_to_match = sell_qty
        allocs: list[LotAlloc] = []

        for buy in buy_txns:
            if remaining_to_match <= Decimal("0"):
                break
            already_consumed = consumed_by_lot.get(buy.id, Decimal("0"))
            available = buy.quantity - already_consumed
            if available <= Decimal("0"):
                continue
            matched_qty = min(remaining_to_match, available)

            realized_gain_eur = None
            if (
                sell_txn.unit_price is not None
                and sell_txn.fx_rate_to_eur is not None
                and buy.unit_price is not None
                and buy.fx_rate_to_eur is not None
                and buy.fx_rate_to_eur != Decimal("0")
                and sell_txn.fx_rate_to_eur != Decimal("0")
            ):
                sell_price_eur = sell_txn.unit_price / sell_txn.fx_rate_to_eur
                buy_price_eur = buy.unit_price / buy.fx_rate_to_eur
                realized_gain_eur = (sell_price_eur - buy_price_eur) * matched_qty

            alloc = LotAlloc(
                id=lot_alloc_id_for(sell_txn.id, buy.id),
                sell_txn_id=sell_txn.id,
                buy_txn_id=buy.id,
                quantity=matched_qty,
                realized_gain_eur=realized_gain_eur,
                created_at=FIXTURE_EPOCH,
            )
            session.add(alloc)
            allocs.append(alloc)
            remaining_to_match -= matched_qty

        if remaining_to_match > Decimal("0"):
            raise ValueError(
                f"Sell quantity {sell_qty} exceeds available lots by {remaining_to_match}"
            )
        return allocs

    engine = create_async_engine(f"sqlite+aiosqlite:///{scratch}")
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    counts = {"accounts": 0, "instruments": 0, "prices": 0, "fx": 0, "apy": 0, "txns": 0}

    async with Session() as session, httpx.AsyncClient() as fx_client:
        # 0. Freeze the user_setting row that alembic migration 0003 inserted
        # with `INSERT INTO user_setting ...` — that row picks up CURRENT_TIMESTAMP
        # on its updated_at column when the migration runs, which would drift
        # every regen. The plan inventory missed this row (it's produced by a
        # migration, not by a model with server_default=func.now() that the
        # seeder constructs). Rule-2 / Rule-3 fix scoped to the seeder only —
        # production migration 0003 stays untouched.
        from sqlalchemy import update as sa_update
        from app.models.user_setting import UserSetting
        await session.execute(
            sa_update(UserSetting).values(updated_at=FIXTURE_EPOCH)
        )
        await session.flush()

        # 1. Accounts (Pydantic-validated, find-or-create by name)
        account_id_by_name: dict[str, str] = {}
        for a in ACCOUNTS:
            existing = (await session.execute(
                select(Account).where(Account.name == a["name"])
            )).scalar_one_or_none()
            if existing is None:
                payload = AccountCreate(**a)
                dumped = payload.model_dump()
                dumped.pop("id", None)
                dumped.pop("created_at", None)
                existing = Account(**dumped, id=account_id_for(a["name"]), created_at=FIXTURE_EPOCH)
                session.add(existing)
                await session.flush()
                counts["accounts"] += 1
            account_id_by_name[a["name"]] = existing.id

        # 2. Instruments (Pydantic-validated, find-or-create by symbol+type)
        instrument_id_by_symbol: dict[str, str] = {}
        for i in INSTRUMENTS:
            existing = (await session.execute(
                select(Instrument).where(
                    Instrument.symbol == i["symbol"],
                    Instrument.instrument_type == i["instrument_type"],
                )
            )).scalar_one_or_none()
            if existing is None:
                payload = InstrumentCreate(**i)
                # InstrumentCreate carries `id: Optional[str] = None`
                # (reserved-id guard), so model_dump() includes id=None.
                # Exclude it so our deterministic uuid5 below is the only id
                # value passed to the ORM — no collision with the kwarg.
                existing = Instrument(
                    **payload.model_dump(exclude={"id"}),
                    id=instrument_id_for(i["symbol"]),
                    created_at=FIXTURE_EPOCH,
                )
                session.add(existing)
                await session.flush()
                counts["instruments"] += 1
            instrument_id_by_symbol[i["symbol"]] = existing.id

        # 3. FX rates — direct ORM. INSERT FIRST so any subsequent
        #    get_or_fetch_fx_rate call cache-hits and never reaches the wire.
        #    FX_ANCHORS covers every unique USD-transaction date.
        for fx in FX_ANCHORS:
            existing_fx = (await session.execute(
                select(FxRate).where(
                    FxRate.date == fx["date"],
                    FxRate.base_currency == fx["base"],
                    FxRate.quote_currency == fx["quote"],
                )
            )).scalar_one_or_none()
            if existing_fx is None:
                session.add(FxRate(
                    id=fx_rate_id_for(fx["base"], fx["quote"], fx["date"]),
                    base_currency=fx["base"],
                    quote_currency=fx["quote"],
                    date=fx["date"],
                    rate=fx["rate"],
                    source="manual",
                    fetched_at=FIXTURE_FROZEN_NOW,
                ))
                counts["fx"] += 1

        # 4. Instrument price quotes — direct ORM (anchors only; PriceQuote model)
        for p in PRICE_ANCHORS:
            existing_price = (await session.execute(
                select(PriceQuote).where(
                    PriceQuote.instrument_id == instrument_id_by_symbol[p["symbol"]],
                    PriceQuote.date == p["date"],
                    PriceQuote.source == "manual",
                )
            )).scalar_one_or_none()
            if existing_price is None:
                inst_id = instrument_id_by_symbol[p["symbol"]]
                session.add(PriceQuote(
                    id=price_quote_id_for(inst_id, p["date"], "manual"),
                    instrument_id=inst_id,
                    date=p["date"],
                    price=p["price"],
                    currency=p["currency"],
                    source="manual",
                    fetched_at=FIXTURE_FROZEN_NOW,
                ))
                counts["prices"] += 1

        # 5. APY configs — direct ORM
        for cfg in APY_CONFIGS:
            existing_apy = (await session.execute(
                select(ApyConfig).where(
                    ApyConfig.account_id == account_id_by_name[cfg["account"]],
                    ApyConfig.instrument_id == instrument_id_by_symbol[cfg["symbol"]],
                    ApyConfig.effective_from == cfg["effective_from"],
                )
            )).scalar_one_or_none()
            if existing_apy is None:
                acc_id = account_id_by_name[cfg["account"]]
                inst_id = instrument_id_by_symbol[cfg["symbol"]]
                session.add(ApyConfig(
                    id=apy_config_id_for(acc_id, inst_id, cfg["effective_from"]),
                    account_id=acc_id,
                    instrument_id=inst_id,
                    apy_rate=cfg["apy_rate"],
                    effective_from=cfg["effective_from"],
                    created_at=FIXTURE_EPOCH,
                ))
                counts["apy"] += 1

        await session.flush()  # Ensure FX rows are flushed so get_or_fetch_fx_rate sees them

        # 6. Transactions — sort by date ascending so FIFO sees lots in order.
        def _date_of(t: dict) -> object:
            return t["trade"]["date"] if "trade" in t else t["date"]

        sorted_txns = sorted(TRANSACTIONS, key=_date_of)

        for idx, t in enumerate(sorted_txns):
            if "trade" in t:
                # INLINE REPLICATE of app/services/trades.py::create_linked_trade with
                # deterministic ids set at construction time. We DO NOT call the production
                # service here because it flushes uuid4 rows before returning, which would
                # force us into a post-flush UPDATE path that is unsafe under SQLite
                # PRAGMA foreign_keys=ON (LotAlloc.sell_txn_id UPDATE can race the
                # Transaction.id UPDATE). The body below mirrors trades.py:32-99 exactly
                # — if the production service's flow changes (new column, different FX
                # auto-lock, etc.), this copy must be updated in lockstep.
                trade = t["trade"]
                trade_date = trade["date"]
                sold_sym = trade["sold"]["symbol"]
                recv_sym = trade["received"]["symbol"]

                pair_id = trade_pair_id_for(idx, sold_sym, recv_sym, trade_date)

                sell_id = transaction_id_for(
                    "trade", idx, "sold", trade["sold"]["account"], sold_sym,
                    trade_date.isoformat(),
                    str(trade["sold"]["quantity"]), str(trade["sold"]["unit_price"]),
                )
                buy_id = transaction_id_for(
                    "trade", idx, "received", trade["received"]["account"], recv_sym,
                    trade_date.isoformat(),
                    str(trade["received"]["quantity"]), str(trade["received"]["unit_price"]),
                )

                sell_txn = Transaction(
                    id=sell_id,
                    account_id=account_id_by_name[trade["sold"]["account"]],
                    instrument_id=instrument_id_by_symbol[sold_sym],
                    txn_type="sell",
                    date=trade_date,
                    quantity=-abs(trade["sold"]["quantity"]),
                    unit_price=trade["sold"]["unit_price"],
                    price_currency=trade["sold"]["price_currency"],
                    fx_rate_to_eur=trade["sold"].get("fx_rate_to_eur"),
                    fee_eur=trade["sold"].get("fee_eur") or Decimal("0"),
                    notes=trade.get("notes"),
                    trade_pair_id=pair_id,
                    created_at=FIXTURE_EPOCH,
                    updated_at=FIXTURE_EPOCH,
                )
                buy_txn = Transaction(
                    id=buy_id,
                    account_id=account_id_by_name[trade["received"]["account"]],
                    instrument_id=instrument_id_by_symbol[recv_sym],
                    txn_type="buy",
                    date=trade_date,
                    quantity=abs(trade["received"]["quantity"]),
                    unit_price=trade["received"]["unit_price"],
                    price_currency=trade["received"]["price_currency"],
                    fx_rate_to_eur=trade["received"].get("fx_rate_to_eur"),
                    fee_eur=trade["received"].get("fee_eur") or Decimal("0"),
                    notes=trade.get("notes"),
                    trade_pair_id=pair_id,
                    created_at=FIXTURE_EPOCH,
                    updated_at=FIXTURE_EPOCH,
                )

                # FX auto-fetch per leg — mirrors trades.py:77-86 verbatim.
                for txn in (sell_txn, buy_txn):
                    if txn.price_currency == "USD" and txn.fx_rate_to_eur is None:
                        fx_row = await get_or_fetch_fx_rate(
                            session, fx_client, txn.date, base="EUR", quote="USD"
                        )
                        txn.fx_rate_to_eur = fx_row.rate
                    elif txn.price_currency == "EUR":
                        txn.fx_rate_to_eur = Decimal("1")

                # Cost basis locked at insert time (mirrors trades.py:89-90).
                sell_txn.cost_basis_eur = compute_cost_basis(sell_txn)
                buy_txn.cost_basis_eur = compute_cost_basis(buy_txn)

                session.add(sell_txn)
                session.add(buy_txn)
                await session.flush()  # populate ids in identity map before FIFO

                # FIFO on the sell leg only — deterministic version creates LotAlloc rows
                # with id=lot_alloc_id_for(sell_id, buy_id) + created_at=FIXTURE_EPOCH.
                await _match_lots_for_sell_deterministic(session, sell_txn)

                counts["txns"] += 2
            else:
                # Single-row buy / spend / yield — replicate routers/transactions.py::create_transaction
                # inline flow (no extracted service function exists per plan interfaces spec).
                tc = TransactionCreate(
                    account_id=account_id_by_name[t["account"]],
                    instrument_id=instrument_id_by_symbol[t["symbol"]],
                    txn_type=t["txn_type"],
                    date=t["date"],
                    quantity=t["quantity"],
                    unit_price=t.get("unit_price"),
                    price_currency=t.get("price_currency"),
                    fx_rate_to_eur=t.get("fx_rate_to_eur"),
                    fee_eur=t.get("fee_eur") or Decimal("0"),
                    notes=t.get("notes"),
                    source=t.get("source", "manual"),
                )
                # Sign convention: spend is negative; buy/yield are positive (sell already rejected).
                signed_qty = -tc.quantity if tc.txn_type == "spend" else tc.quantity
                txn = Transaction(
                    id=transaction_id_for(
                        "single", idx, t["account"], t["symbol"],
                        tc.date.isoformat(), tc.txn_type,
                        str(signed_qty), str(tc.unit_price) if tc.unit_price is not None else "none",
                    ),
                    account_id=tc.account_id,
                    instrument_id=tc.instrument_id,
                    txn_type=tc.txn_type,
                    date=tc.date,
                    quantity=signed_qty,
                    unit_price=tc.unit_price,
                    price_currency=tc.price_currency,
                    fx_rate_to_eur=tc.fx_rate_to_eur,
                    fee_eur=tc.fee_eur,
                    notes=tc.notes,
                    source=tc.source or "manual",
                    created_at=FIXTURE_EPOCH,
                    updated_at=FIXTURE_EPOCH,
                )
                # FX auto-lock (cache-hit because FX rows were inserted above).
                if tc.price_currency == "USD" and tc.fx_rate_to_eur is None:
                    fx_row = await get_or_fetch_fx_rate(
                        session, fx_client, tc.date, base="EUR", quote="USD"
                    )
                    txn.fx_rate_to_eur = fx_row.rate
                elif tc.price_currency == "EUR":
                    txn.fx_rate_to_eur = Decimal("1")
                elif tc.price_currency is None and tc.txn_type == "yield" and tc.fx_rate_to_eur is None:
                    # Yield rows with no price_currency — seed a placeholder FX from nearest anchor.
                    # get_or_fetch_fx_rate with USD/EUR is called here to stay consistent with
                    # how the accrual job fills fx_rate_to_eur on auto-accrual rows.
                    fx_row = await get_or_fetch_fx_rate(
                        session, fx_client, tc.date, base="EUR", quote="USD"
                    )
                    txn.fx_rate_to_eur = fx_row.rate
                txn.cost_basis_eur = compute_cost_basis(txn)
                session.add(txn)
                await session.flush()
                if tc.txn_type == "spend":
                    await _match_lots_for_sell_deterministic(session, txn)
                counts["txns"] += 1

        await session.commit()

    await engine.dispose()
    return counts


def main() -> int:
    if SCRATCH_PATH.exists():
        SCRATCH_PATH.unlink()
    SCRATCH_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"[seed-golden] alembic upgrade head against {SCRATCH_PATH}", flush=True)
    _run_alembic_against(SCRATCH_PATH)

    print(f"[seed-golden] inserting fixtures ...", flush=True)
    counts = asyncio.run(_seed(SCRATCH_PATH))

    FINAL_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    # SQLAlchemy's batch_alter_table recreate path emits FOREIGN KEY constraints
    # and CREATE INDEX statements in non-deterministic order (the iteration order
    # of internal sets isn't stable across Python runs). VACUUM doesn't fix this
    # because the CREATE-TABLE / CREATE-INDEX text is stored verbatim in
    # sqlite_master and copied across.
    #
    # Determinism fix: dump the whole DB to canonical text (sorting sqlite_master
    # entries to a stable order, AND sorting FOREIGN KEY lines inside each
    # CREATE TABLE), then materialize the FINAL_OUTPUT by replaying that text
    # into a fresh sqlite file. After the replay, VACUUM normalizes page layout.
    import sqlite3
    if FINAL_OUTPUT.exists():
        FINAL_OUTPUT.unlink()

    def _canonical_table_sql(sql: str) -> str:
        # Sort contiguous blocks of CONSTRAINT ... CHECK / FOREIGN KEY / CONSTRAINT ... FOREIGN KEY
        # lines inside the CREATE TABLE body so non-deterministic SQLAlchemy DDL
        # emission order doesn't produce different sqlite_master text between
        # regens. PRIMARY KEY / unique / column lines are NOT sorted (their
        # order is structurally meaningful).
        def _is_sortable_constraint(line: str) -> bool:
            s = line.strip().rstrip(",").rstrip()
            return s.startswith(("FOREIGN KEY", "CONSTRAINT "))

        lines = sql.split("\n")
        out: list[str] = []
        i = 0
        while i < len(lines):
            if _is_sortable_constraint(lines[i]):
                block_start = i
                while i < len(lines) and _is_sortable_constraint(lines[i]):
                    i += 1
                block = lines[block_start:i]
                last_had_comma = block[-1].rstrip().endswith(",")
                # Sort by stripped content (without trailing comma).
                stripped_block = [b.rstrip().rstrip(",") for b in block]
                content_for_sort = sorted(stripped_block, key=lambda s: s.strip())
                for j, b in enumerate(content_for_sort):
                    if j < len(content_for_sort) - 1 or last_had_comma:
                        out.append(b + ",")
                    else:
                        out.append(b)
                continue
            out.append(lines[i])
            i += 1
        return "\n".join(out)

    src = sqlite3.connect(str(SCRATCH_PATH))
    try:
        # 1. Snapshot sqlite_master in a canonical sorted order. Tables MUST be
        #    created before indexes/views/triggers reference them; SQLite's "type"
        #    enum sorts table < index < trigger < view alphabetically, which gives
        #    us the right dependency order naturally. Within each type, sort by name.
        master_rows = src.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY "
            "  CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 "
            "            WHEN 'trigger' THEN 2 WHEN 'view' THEN 3 ELSE 4 END, "
            "  name"
        ).fetchall()

        # 2. Snapshot all data rows in (table, rowid) order for deterministic INSERT order.
        #    Tables alembic creates are listed in the master snapshot; we iterate those.
        tables = [r for r in master_rows if r[0] == "table"]

        # 3. Build the destination DB
        dst = sqlite3.connect(str(FINAL_OUTPUT))
        try:
            dst.execute("PRAGMA foreign_keys = OFF;")
            # Recreate schema in canonical order
            for typ, name, tbl_name, sql in master_rows:
                if typ == "table":
                    dst.execute(_canonical_table_sql(sql))
                else:
                    dst.execute(sql)
            # Copy data table-by-table in canonical name order
            for typ, name, tbl_name, sql in tables:
                cols = src.execute(f'PRAGMA table_info("{name}")').fetchall()
                col_names = [c[1] for c in cols]
                col_list = ",".join(f'"{c}"' for c in col_names)
                placeholders = ",".join("?" * len(col_names))
                # ORDER BY rowid for deterministic row order (matches insertion order).
                rows = src.execute(f'SELECT {col_list} FROM "{name}" ORDER BY rowid').fetchall()
                if rows:
                    dst.executemany(
                        f'INSERT INTO "{name}" ({col_list}) VALUES ({placeholders})',
                        rows,
                    )
            # Re-enable foreign_keys and integrity-check (defensive).
            dst.execute("PRAGMA foreign_keys = ON;")
            dst.commit()
            # VACUUM to normalize page layout. This rewrites the file with no
            # free pages, no journal residue — strongest possible byte-stability.
            dst.execute("VACUUM;")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()
    print(f"[seed-golden] wrote {FINAL_OUTPUT}: {counts}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
