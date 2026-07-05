"""ForwardCursor: an amortized-O(1) "last item at-or-before" over a sorted list.

Replaces the per-day linear scans (`[x for x in items if key(x) <= as_of][-1]`)
in the net-worth / cost-basis day replays. The replay advances its `as_of`
(the calendar day) monotonically and the underlying lists (price quotes, fx
dates, priced transactions) are sorted ascending, so a single forward-only index
reproduces the scan's result while touching each element at most once overall.
"""
from __future__ import annotations

from typing import Callable, Generic, Sequence, TypeVar

T = TypeVar("T")
K = TypeVar("K")


class ForwardCursor(Generic[T]):
    """Return the last item with ``key(item) <= as_of`` over an ascending-sorted
    ``items``. ``at`` must be called with a non-decreasing sequence of ``as_of``
    values (the replay's monotonic day); the index only ever moves forward.
    """

    __slots__ = ("_items", "_key", "_i")

    def __init__(self, items: Sequence[T], key: Callable[[T], K]) -> None:
        self._items = items
        self._key = key
        self._i = -1  # index of the last item with key <= the last as_of; -1 = none

    def at(self, as_of: K) -> T | None:
        items, key = self._items, self._key
        n = len(items)
        i = self._i
        while i + 1 < n and key(items[i + 1]) <= as_of:
            i += 1
        self._i = i
        return items[i] if i >= 0 else None
