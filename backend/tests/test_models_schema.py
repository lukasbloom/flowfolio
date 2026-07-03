"""
Schema validation tests for ORM models.

These tests guard the immovable first-principles invariants:
- All monetary columns use DecimalText canonical-TEXT storage (never FLOAT/REAL;
  plan 006 — SQLite binds Numeric/Decimal as REAL and corrupts >15-digit values)
- Transaction has fx_rate_to_eur and quantity stored as DecimalText
- LotAlloc.sell_txn_id has ON DELETE CASCADE
"""

from app.core.db_types import DecimalText


def test_all_models_import():
    from app.models import (
        Account,
        ApyConfig,
        HoldingTag,
        Instrument,
        LotAlloc,
        Tag,
        Transaction,
    )
    assert Account.__tablename__ == "account"
    assert Instrument.__tablename__ == "instrument"
    assert Transaction.__tablename__ == "transaction"
    assert LotAlloc.__tablename__ == "lot_alloc"
    assert Tag.__tablename__ == "tag"
    assert HoldingTag.__tablename__ == "holding_tag"
    assert ApyConfig.__tablename__ == "apy_config"


def test_transaction_fx_rate_to_eur_is_decimal_text():
    from app.models import Transaction

    col = Transaction.__table__.c.fx_rate_to_eur
    assert isinstance(col.type, DecimalText), "fx_rate_to_eur must be DecimalText (exact)"


def test_transaction_quantity_is_decimal_text():
    from app.models import Transaction

    col = Transaction.__table__.c.quantity
    assert isinstance(col.type, DecimalText)


def test_lot_alloc_sell_txn_cascade():
    from app.models import LotAlloc

    fks = list(LotAlloc.__table__.c.sell_txn_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].ondelete == "CASCADE"


# Reconciliation schema

def test_reconciliation_model_imports_and_tablename():
    from app.models import Reconciliation

    assert Reconciliation.__tablename__ == "reconciliation"


def test_reconciliation_columns_present():
    from app.models import Reconciliation

    cols = {c.name for c in Reconciliation.__table__.columns}
    assert {"id", "account_id", "snapshot_date", "created_at", "notes", "holdings_snapshot"} <= cols


def test_reconciliation_account_id_fk():
    from app.models import Reconciliation

    fks = list(Reconciliation.__table__.c.account_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "account"


def test_reconciliation_holdings_snapshot_is_json():
    from sqlalchemy import JSON

    from app.models import Reconciliation

    col = Reconciliation.__table__.c.holdings_snapshot
    assert isinstance(col.type, JSON)
    assert col.nullable is False


def test_reconciliation_index_account_date():
    from app.models import Reconciliation

    idx_names = {idx.name for idx in Reconciliation.__table__.indexes}
    assert "idx_reconciliation_account_date" in idx_names


def test_transaction_has_reconciliation_id_fk():
    from app.models import Transaction

    col = Transaction.__table__.c.reconciliation_id
    assert col.nullable is True
    fks = list(col.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "reconciliation"


def test_transaction_reconciliation_index():
    from app.models import Transaction

    idx_names = {idx.name for idx in Transaction.__table__.indexes}
    assert "idx_txn_reconciliation" in idx_names
