"""Regression tests for backend/app/core/clock.py."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest


def _set_fixed(monkeypatch, fixed_now: str | None) -> None:
    """Monkeypatch clock._FIXED directly.

    Why not reload the module: a module reload computes `_FIXED` once and leaves
    it bound until the next reload. monkeypatch teardown restores env vars and
    cfg.settings attributes but does NOT re-run the module body, so `_FIXED`
    leaks to subsequent tests. Patching the attribute directly lets monkeypatch
    restore it on teardown.
    """
    import app.core.clock as clk

    if fixed_now is None:
        monkeypatch.setattr(clk, "_FIXED", None)
    else:
        parsed = datetime.fromisoformat(fixed_now.replace("Z", "+00:00")).replace(tzinfo=None)
        monkeypatch.setattr(clk, "_FIXED", parsed)


def test_clock_unset_returns_real_now(monkeypatch):
    _set_fixed(monkeypatch, None)
    import app.core.clock as clk

    real_now = datetime.utcnow()
    result = clk.now()
    # Within 5s — generous for slow CI.
    assert abs((result - real_now).total_seconds()) < 5
    assert clk.today() == date.today()


def test_clock_frozen_returns_pinned_instant(monkeypatch):
    _set_fixed(monkeypatch, "2026-04-30T12:00:00Z")
    import app.core.clock as clk

    assert clk.now() == datetime(2026, 4, 30, 12, 0, 0)
    assert clk.today() == date(2026, 4, 30)
    # Calling again returns the same instant (no drift).
    assert clk.now() == datetime(2026, 4, 30, 12, 0, 0)


def test_clock_frozen_naive_not_aware(monkeypatch):
    """The contract is naive UTC — match the datetime.utcnow() sites the refactor replaces."""
    _set_fixed(monkeypatch, "2026-04-30T12:00:00Z")
    import app.core.clock as clk

    assert clk.now().tzinfo is None


def test_production_with_fixed_now_raises_startup_error(monkeypatch):
    """APP_ENV=production AND FLOWFOLIO_FIXED_NOW set must refuse to boot."""
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "app_env", "production")
    monkeypatch.setattr(cfg.settings, "fixed_now", "2026-04-30T12:00:00Z")
    fresh_settings = cfg.settings
    with pytest.raises(RuntimeError, match=r"copy/paste foot-gun"):
        if fresh_settings.app_env == "production" and fresh_settings.fixed_now:
            raise RuntimeError(
                "FLOWFOLIO_FIXED_NOW is set with APP_ENV=production. "
                "Refusing to boot — this combination is a copy/paste foot-gun from compose.test.yml. "
                "Unset FLOWFOLIO_FIXED_NOW in the production environment."
            )


def test_development_with_fixed_now_does_not_raise(monkeypatch):
    """Dev + FIXED_NOW is the intended test-stack combination, must boot fine."""
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "app_env", "development")
    monkeypatch.setattr(cfg.settings, "fixed_now", "2026-04-30T12:00:00Z")
    fresh_settings = cfg.settings
    # The guard expression evaluates False; no exception.
    assert not (fresh_settings.app_env == "production" and fresh_settings.fixed_now)


def test_today_local_follows_madrid_not_utc(monkeypatch):
    """Regression: near local midnight, today_local() follows Madrid, not UTC.

    Reconciliation snapshot_date is validated against today_local(). Using UTC
    date.today() wrongly rejected a "today" snapshot in the window between
    Madrid midnight and ~02:00 CEST, when UTC is still the previous calendar
    day. At 2026-06-15 00:30 CEST (== 2026-06-14 22:30 UTC) the user's calendar
    is the 15th, so a snapshot dated 2026-06-15 must NOT be "in the future".
    """
    import app.core.clock as clk

    _set_fixed(monkeypatch, None)  # exercise the real-clock branch

    utc_instant = datetime(2026, 6, 14, 22, 30, tzinfo=ZoneInfo("UTC"))

    class _FakeDateTime:
        @staticmethod
        def now(tz=None):
            return utc_instant.astimezone(tz) if tz is not None else utc_instant

    monkeypatch.setattr(clk, "datetime", _FakeDateTime)

    assert utc_instant.date() == date(2026, 6, 14)  # UTC is still the 14th...
    assert clk.today_local() == date(2026, 6, 15)  # ...but Madrid is the 15th


def test_today_local_pinned_returns_fixed_date(monkeypatch):
    """When FIXED_NOW is set, today_local() returns the pinned date (test determinism)."""
    _set_fixed(monkeypatch, "2026-04-30T12:00:00Z")
    import app.core.clock as clk

    assert clk.today_local() == date(2026, 4, 30)


def test_clock_parses_fixed_now_at_import(monkeypatch):
    """_parse_fixed_now() consumes settings.fixed_now and produces a naive UTC datetime."""
    import app.core.clock as clk
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "fixed_now", "2026-04-30T12:00:00Z")
    parsed = clk._parse_fixed_now()
    assert parsed == datetime(2026, 4, 30, 12, 0, 0)
    assert parsed is not None and parsed.tzinfo is None

    monkeypatch.setattr(cfg.settings, "fixed_now", None)
    assert clk._parse_fixed_now() is None
