"""Semver comparison for the update-check prompt.

The prompt must fire only for a strictly newer release, never a downgrade. Right
after a release the running version can lead the cached latest release (the daily
GitHub check has not refreshed yet), so a plain `latest != current` wrongly flags
the older cached release as an update.

Stdlib only, no third-party version parser, so the runtime image needs no extra
dependency (mirrors the frontend isNewerVersion helper).
"""
from __future__ import annotations

import re

_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _parse(version: str) -> tuple[int, ...] | None:
    bare = version[1:] if version[:1] in ("v", "V") else version
    if not _VERSION_RE.match(bare):
        return None
    return tuple(int(part) for part in bare.split("."))


def is_newer_release(latest: str | None, current: str) -> bool:
    """True only when `latest` is a strictly newer release than `current`.

    Handles an optional leading "v" on either side. Unparseable versions
    (a dev build, a malformed tag) are treated as not-newer so the prompt stays
    off rather than firing on garbage.
    """
    if latest is None:
        return False
    a = _parse(latest)
    b = _parse(current)
    if a is None or b is None:
        return False
    length = max(len(a), len(b))
    a_padded = a + (0,) * (length - len(a))
    b_padded = b + (0,) * (length - len(b))
    return a_padded > b_padded
