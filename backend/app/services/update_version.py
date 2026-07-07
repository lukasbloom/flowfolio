"""Semver comparison for the update-check prompt.

The prompt must fire only for a strictly newer release, never a downgrade. Right
after a release the running version can lead the cached latest release (the daily
GitHub check has not refreshed yet), so a plain `latest != current` wrongly flags
the older cached release as an update.
"""
from __future__ import annotations

from packaging.version import InvalidVersion, Version


def is_newer_release(latest: str | None, current: str) -> bool:
    """True only when `latest` is a strictly newer release than `current`.

    Handles an optional leading "v" on either side. Unparseable versions
    (a dev build, a malformed tag) are treated as not-newer so the prompt stays
    off rather than firing on garbage.
    """
    if latest is None:
        return False
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False
