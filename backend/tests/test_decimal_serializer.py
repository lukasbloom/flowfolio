"""Tests for the shared Decimal->str serializer used by all response schemas.

Pydantic's default ``str(Decimal)`` preserves the internal exponent so
small fees and FX rates serialize as ``"7E-7"`` / ``"1E-8"`` and surface
that exponent verbatim in the audit modal and edit form. ``decimal_to_str``
calls ``format(d, "f")`` so the wire value matches what users typed.
"""
from __future__ import annotations

from decimal import Decimal

from app.schemas._serializers import decimal_to_str
from app.schemas.transaction import TransactionCreate


def test_decimal_to_str_zero() -> None:
    assert decimal_to_str(Decimal("0")) == "0"


def test_decimal_to_str_normal() -> None:
    assert decimal_to_str(Decimal("12345.6789")) == "12345.6789"


def test_decimal_to_str_small_exponent_8() -> None:
    assert decimal_to_str(Decimal("7E-8")) == "0.00000007"


def test_decimal_to_str_small_exponent_7() -> None:
    assert decimal_to_str(Decimal("1E-7")) == "0.0000001"


def test_decimal_to_str_negative() -> None:
    assert decimal_to_str(Decimal("-0.0000007")) == "-0.0000007"


def test_decimal_to_str_non_finite_passthrough() -> None:
    # Non-finite Decimals (NaN, Infinity) should fall through to ``str``
    # rather than raise from ``format(d, "f")``.
    assert decimal_to_str(Decimal("NaN")) == "NaN"


def test_pydantic_response_serializes_small_decimal_as_plain_string() -> None:
    """End-to-end: a response schema with a Decimal field set to a small
    value must serialize as ``"0.0000007"``, not ``"7E-7"``.

    TransactionCreate is the schema closest to the bug surface (fee_eur in
    the audit modal); we use ``model_dump_json`` to exercise the configured
    ``json_encoders`` mapping.
    """
    txn = TransactionCreate(
        account_id="acc-1",
        instrument_id="inst-1",
        txn_type="buy",
        date="2026-01-01",
        quantity=Decimal("1"),
        unit_price=Decimal("100"),
        price_currency="EUR",
        fx_rate_to_eur=Decimal("1"),
        fee_eur=Decimal("0.0000007"),
    )
    payload = txn.model_dump_json()
    assert "0.0000007" in payload
    assert "7E-7" not in payload
    assert "7e-7" not in payload
