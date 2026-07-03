from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer


def decimal_to_str(d: Decimal) -> str:
    """Serialise a Decimal as a plain (non-exponential) string.

    Pydantic's default ``str(Decimal)`` preserves the internal exponent,
    so very small values render as e.g. ``"7E-7"`` in JSON. ``format(d, "f")``
    expands the exponent so the wire value matches what users typed.
    """
    if not d.is_finite():
        return str(d)
    return format(d, "f")


_FX_QUANT = Decimal("0.0001")


def fx_rate_to_str(d: Decimal) -> str:
    """Serialise an FX rate as a 4dp banker's-rounded string with trailing zeros stripped.

    The server is the single source of truth for the FX wire string.
    Quantization uses ROUND_HALF_EVEN (matches Python decimal default + IEEE 754 +
    the implicit rounding mode used throughout services/perf.py). After quantize,
    ``format(d, "f")`` preserves the trailing zeros from the stored exponent
    (Decimal("1.1760") → "1.1760"), so we strip explicitly:

    - "1.1760" → "1.176"
    - "1.0000" → "1"     (orphan dot also stripped)
    - "1.1762" → "1.1762" (no trailing zeros, unchanged)
    - Decimal("1.17625") → "1.1762" (banker's rounding: digit before .5 is even (2),
      half-rounds-to-even keeps the value at the even neighbor)
    - Non-finite (NaN/Infinity) values fall through to str(d).
    """
    if not d.is_finite():
        return str(d)
    quantized = d.quantize(_FX_QUANT, rounding=ROUND_HALF_EVEN)
    s = format(quantized, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


FxRateStr = Annotated[Decimal, PlainSerializer(fx_rate_to_str, return_type=str, when_used="json")]

# Plain-string Decimal serialization as a field-level annotated type. Replaces
# the deprecated per-model encoder ConfigDict mechanism (removed in Pydantic v3).
# Apply to every Decimal money field on a schema:
# ``amount: DecimalStr``, ``amount: DecimalStr | None``, ``list[DecimalStr]``, etc.
# ``when_used="json"`` keeps Python-side ``.model_dump()`` returning real Decimals
# (validators/services operate on Decimal); only the JSON wire form is stringified —
# byte-identical to the previous per-model encoder output.
DecimalStr = Annotated[Decimal, PlainSerializer(decimal_to_str, return_type=str, when_used="json")]


class DecimalModel(BaseModel):
    """Base for request/response schemas that carry Decimal money fields.

    Historically this base injected a per-model Decimal encoder via
    ``ConfigDict`` (deprecated in Pydantic v2, removed in v3). Decimal money
    fields now declare the field-level ``DecimalStr`` annotated type instead,
    so this base no longer carries any serialization config. It is retained so
    the ~25 subclass declarations don't all need editing and so a future common
    config has a single home.
    """


class DecimalORMModel(DecimalModel):
    """DecimalModel + ``from_attributes=True`` for ORM-backed response schemas."""

    model_config = ConfigDict(from_attributes=True)
