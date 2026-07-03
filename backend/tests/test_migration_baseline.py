"""Schema-parity guard for a FRESH-migrated database.

This is the regression guard for the plan-006 class of bug: a migration that
stamps the latest revision but leaves a column in the wrong shape. The fast
suite builds its schema from ``Base.metadata.create_all`` and so never exercises
the migrations — it cannot catch a migration that produces a different schema
than the models intend. This test runs the real ``alembic upgrade head`` on a
throwaway on-disk DB (a subprocess, since alembic's env uses its own event loop)
and asserts the result is what the app requires.

Specifically: every money column must end up with TEXT/VARCHAR affinity
(DecimalText), never NUMERIC/REAL — SQLite stores Decimal in a NUMERIC column as
a float and silently corrupts money. (The original 0007 migration only converted
columns that already held data, so a fresh/empty database kept NUMERIC columns;
this test fails loudly on any such regression.)
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
from pathlib import Path

# (table, money columns) — every Decimal-bearing column in the schema.
MONEY_COLUMNS: list[tuple[str, list[str]]] = [
    ("transaction", ["quantity", "unit_price", "fx_rate_to_eur", "cost_basis_eur", "fee_eur"]),
    ("lot_alloc", ["quantity", "realized_gain_eur"]),
    ("price_quote", ["price"]),
    ("fx_rate", ["rate"]),
    ("apy_config", ["apy_rate"]),
]

_NUMERIC_AFFINITY = ("NUMERIC", "DECIMAL", "REAL", "FLOAT", "DOUBLE", "INT")


def _backend_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _build_fresh_db(tmp_path) -> Path:
    db_file = tmp_path / "fresh_migrated.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db_file}"}
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(_backend_dir()),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    return db_file


def test_fresh_migration_money_columns_are_text(tmp_path):
    """A freshly-migrated EMPTY database must store every money column as
    TEXT/VARCHAR — never a numeric affinity that SQLite would store as float."""
    db_file = _build_fresh_db(tmp_path)
    conn = sqlite3.connect(str(db_file))
    try:
        offenders: list[str] = []
        for table, cols in MONEY_COLUMNS:
            info = {r[1]: (r[2] or "").upper() for r in conn.execute(
                f'PRAGMA table_info("{table}")'
            ).fetchall()}
            for col in cols:
                decl = info.get(col)
                assert decl is not None, f"{table}.{col} missing from fresh schema"
                if any(a in decl for a in _NUMERIC_AFFINITY):
                    offenders.append(f"{table}.{col} -> {decl!r}")
        assert not offenders, (
            "money columns must have TEXT/VARCHAR affinity on a fresh build; "
            f"found numeric affinity: {offenders}"
        )
    finally:
        conn.close()


def test_fresh_migration_seeds_concentration_threshold(tmp_path):
    """The baseline must reproduce the default user_setting row that the
    original migration 0003 inserted (the app reads this default)."""
    db_file = _build_fresh_db(tmp_path)
    conn = sqlite3.connect(str(db_file))
    try:
        row = conn.execute(
            "SELECT value FROM user_setting WHERE key = 'concentration_threshold'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "0.25", (
        f"expected concentration_threshold='0.25' seed row; got {row!r}"
    )
