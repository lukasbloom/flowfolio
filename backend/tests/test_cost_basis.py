"""Pin tests for the FIFO helpers in app.services.cost_basis.

These exist so a future refactor of the FIFO math (or its move out of
``contributions.py``) is provably safe: the test fails
fast if the open-lot consumption rule is changed by accident.

The test uses lightweight stand-ins for ``Transaction`` / ``LotAlloc`` rather
than spinning up an in-memory SQLite session — ``_cost_basis_at`` is a pure
function over plain attributes, and the public parity test in
``tests/services/test_contributions.py`` already exercises the DB path
end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.cost_basis import _cost_basis_at


@dataclass
class _Buy:
    id: str
    date: date
    quantity: Decimal
    cost_basis_eur: Decimal | None


@dataclass
class _Sell:
    date: date


@dataclass
class _Alloc:
    buy_txn_id: str
    quantity: Decimal


def test_cost_basis_at_no_sells_returns_full_buy_basis() -> None:
    """Two buy lots, no sells — basis is the sum of both lots' EUR cost."""
    buys = [
        _Buy("b1", date(2026, 1, 1), Decimal("10"), Decimal("100")),
        _Buy("b2", date(2026, 2, 1), Decimal("5"), Decimal("75")),
    ]
    result = _cost_basis_at(buys, allocations=[], on_date=date(2026, 3, 1))
    assert result == Decimal("175")


def test_cost_basis_at_excludes_buys_after_on_date() -> None:
    """A buy that hasn't settled yet must not contribute."""
    buys = [
        _Buy("b1", date(2026, 1, 1), Decimal("10"), Decimal("100")),
        _Buy("b2", date(2026, 6, 1), Decimal("5"), Decimal("75")),
    ]
    result = _cost_basis_at(buys, allocations=[], on_date=date(2026, 3, 1))
    assert result == Decimal("100")


def test_cost_basis_at_partial_fifo_consumption_is_proportional() -> None:
    """Selling 4 of 10 units consumes 4/10 of the lot's EUR cost."""
    buys = [_Buy("b1", date(2026, 1, 1), Decimal("10"), Decimal("100"))]
    sell = _Sell(date(2026, 2, 1))
    allocations = [(_Alloc("b1", Decimal("4")), object(), sell)]
    result = _cost_basis_at(buys, allocations=allocations, on_date=date(2026, 3, 1))
    # Open qty = 10 - 4 = 6 → remaining basis = 100 * 6 / 10 = 60
    assert result == Decimal("60")


def test_cost_basis_at_ignores_sells_after_on_date() -> None:
    """A sell that hasn't settled yet must not reduce the open lot."""
    buys = [_Buy("b1", date(2026, 1, 1), Decimal("10"), Decimal("100"))]
    sell = _Sell(date(2026, 5, 1))
    allocations = [(_Alloc("b1", Decimal("4")), object(), sell)]
    # on_date is BEFORE the sell — full basis still open.
    result = _cost_basis_at(buys, allocations=allocations, on_date=date(2026, 3, 1))
    assert result == Decimal("100")


def test_cost_basis_at_fully_consumed_lot_drops_out() -> None:
    """A buy lot whose entire quantity has been allocated returns 0 contribution."""
    buys = [_Buy("b1", date(2026, 1, 1), Decimal("10"), Decimal("100"))]
    sell = _Sell(date(2026, 2, 1))
    allocations = [(_Alloc("b1", Decimal("10")), object(), sell)]
    result = _cost_basis_at(buys, allocations=allocations, on_date=date(2026, 3, 1))
    assert result == Decimal("0")


def test_cost_basis_at_skips_null_cost_basis() -> None:
    """Buy lots without a stamped cost_basis_eur (legacy rows) are skipped."""
    buys = [
        _Buy("b1", date(2026, 1, 1), Decimal("10"), Decimal("100")),
        _Buy("b2", date(2026, 1, 2), Decimal("5"), None),
    ]
    result = _cost_basis_at(buys, allocations=[], on_date=date(2026, 3, 1))
    assert result == Decimal("100")
