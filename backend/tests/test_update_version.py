"""Semver comparison for the update-check prompt.

The prompt must fire only for a strictly newer release, never a downgrade. Right
after a release the running version can lead the cached latest release (the daily
GitHub check has not refreshed yet), so a plain `latest != current` wrongly flags
the older cached release as an update.
"""
from __future__ import annotations

from app.services.update_version import is_newer_release


def test_newer_release_is_actionable():
    assert is_newer_release("v1.3.0", "v1.2.0") is True


def test_same_version_is_not_actionable():
    assert is_newer_release("v1.3.0", "v1.3.0") is False


def test_older_cached_release_is_not_actionable():
    # The reported bug: running v1.3.0 with the cached latest still at v1.2.5
    # (daily check not refreshed) must NOT show the older release as an update.
    assert is_newer_release("v1.2.5", "v1.3.0") is False


def test_none_latest_is_not_actionable():
    assert is_newer_release(None, "v1.3.0") is False


def test_dev_current_is_not_actionable():
    # A dev build is not a comparable version, treat as not-newer rather than raise.
    assert is_newer_release("v1.3.0", "dev") is False


def test_unparseable_latest_is_not_actionable():
    assert is_newer_release("not-a-version", "v1.3.0") is False


def test_v_prefix_is_optional_on_either_side():
    assert is_newer_release("1.3.0", "v1.2.0") is True
    assert is_newer_release("v1.3.0", "1.2.0") is True


def test_patch_and_minor_ordering():
    assert is_newer_release("v1.2.10", "v1.2.9") is True
    assert is_newer_release("v1.10.0", "v1.9.0") is True
