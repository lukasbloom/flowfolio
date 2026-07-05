"""ForwardCursor must equal the linear `[x for x in items if key(x) <= as_of][-1]`
scan it replaces, for any ascending-sorted list queried at non-decreasing as_of.
"""
from datetime import date, timedelta

from app.services.date_cursor import ForwardCursor


def _linear(items, key, as_of):
    eligible = [x for x in items if key(x) <= as_of]
    return eligible[-1] if eligible else None


def test_matches_linear_scan_over_monotonic_queries():
    dates = [date(2026, 1, d) for d in (1, 3, 3, 7, 10, 10, 20)]
    items = list(enumerate(dates))  # (idx, date) so equal dates are distinguishable
    key = lambda it: it[1]  # noqa: E731

    cursor = ForwardCursor(items, key)
    # Walk as_of forward one day at a time across (and past) the whole range.
    for offset in range(-2, 25):
        as_of = date(2026, 1, 1) + timedelta(days=offset)
        assert cursor.at(as_of) == _linear(items, key, as_of), as_of


def test_empty_list_returns_none():
    cursor = ForwardCursor([], key=lambda x: x)
    assert cursor.at(date(2026, 1, 1)) is None


def test_all_after_as_of_returns_none_then_catches_up():
    items = [date(2026, 6, 1), date(2026, 6, 2)]
    cursor = ForwardCursor(items, key=lambda d: d)
    assert cursor.at(date(2026, 1, 1)) is None  # before first
    assert cursor.at(date(2026, 6, 1)) == date(2026, 6, 1)  # inclusive boundary
    assert cursor.at(date(2026, 6, 30)) == date(2026, 6, 2)  # last eligible


def test_same_date_ties_return_last_occurrence():
    # eligible[-1] returns the LAST element with date <= as_of, i.e. the last of
    # a same-date run (mirrors quote_on_or_before's manual-last / max-fetched_at
    # tie-break which is baked into the list order).
    items = [("a", date(2026, 1, 1)), ("b", date(2026, 1, 1)), ("c", date(2026, 2, 1))]
    cursor = ForwardCursor(items, key=lambda it: it[1])
    assert cursor.at(date(2026, 1, 1)) == ("b", date(2026, 1, 1))
    assert cursor.at(date(2026, 1, 15)) == ("b", date(2026, 1, 1))
    assert cursor.at(date(2026, 2, 1)) == ("c", date(2026, 2, 1))
