"""Self-update apply: write the request file + read the status file.

A commit-free, DB-free, socket-free file-I/O service. The app NEVER touches the
container engine (least-privilege): apply only drops `request.json` onto
the shared `update_channel` volume, and the socket-holding updater sidecar
acts on it. This module shells out to nothing and drives no container-engine
client by design — see the source assertion in tests/test_update_apply.py.

File contract:
  request.json (app -> updater): { request_id, target_version, requested_at }
  status.json  (updater -> app): { request_id, state, message, log_tail, updated_at }
  state ::= preparing | pulling | restarting | success | failed
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone

from app.core.config import settings

# Non-terminal updater states. While status.json reports one of these, a fresh
# apply re-attaches to the running request instead of writing a new one.
IN_FLIGHT_STATES = frozenset({"preparing", "pulling", "restarting"})

# A non-terminal status this old is treated as a crashed/stuck updater so a
# fresh request can break the in-flight lock instead of re-attaching to a dead run
# forever. Generously larger than any real pull+restart+healthcheck so it never
# fires mid-update (the updater freezes updated_at across a long `pull`).
STALE_AFTER_SECONDS = 15 * 60

# Lenient semver (optional leading v). target_version is informational only — the
# updater pulls the compose-pinned service, never an image ref built from this
# string, but we still refuse obvious garbage before writing it.
_SEMVER_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$")


def _request_path() -> str:
    return os.path.join(settings.update_channel_dir, "request.json")


def _status_path() -> str:
    return os.path.join(settings.update_channel_dir, "status.json")


def _write_preparing_status(request_id: str) -> None:
    """Reset status.json to a fresh `preparing` snapshot for THIS run.

    The updater only rewrites status.json on its next poll tick (a few seconds),
    so without this the status endpoint would keep serving a PRIOR run's terminal
    `failed`/`success` for the first seconds of a new update and the overlay would
    briefly show a false outcome. Writing a run-correlated `preparing` at request
    time closes that window. Atomic replace — a reader never sees a half-write.
    """
    payload = {
        "request_id": request_id,
        "state": "preparing",
        "message": "Preparing the update.",
        "log_tail": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _status_path()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def read_update_status() -> dict:
    """Read the updater's status.json, or an idle default when it is absent.

    Pure file read — no DB, no network. Returns a normalized dict so the status
    endpoint can surface live progress (state/message/log_tail). An absent or
    malformed file reads as the idle state (all fields None).
    """
    try:
        with open(_status_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    return {
        "request_id": data.get("request_id"),
        "state": data.get("state"),
        "message": data.get("message"),
        "log_tail": data.get("log_tail"),
        "updated_at": data.get("updated_at"),
    }


def _status_is_stale(status: dict) -> bool:
    """True when a non-terminal status is old enough to be a dead updater.

    Only an UNAMBIGUOUSLY old timestamp counts as stale: a missing/unparseable
    `updated_at` returns False so we keep re-attaching (the live updater always
    stamps it), avoiding a spurious second recreate.
    """
    updated_at = status.get("updated_at")
    if not updated_at:
        return False
    try:
        ts = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    return age > STALE_AFTER_SECONDS


def request_update(target_version: str) -> str:
    """Drop a locked, idempotent update request and return its request_id.

    If status.json reports a non-terminal state, re-attach: return the existing
    request_id WITHOUT writing a new request.json, so re-clicking never triggers a
    second recreate. Otherwise write request.json atomically with a fresh uuid and
    return it. Raises ValueError on a non-semver target_version.
    """
    if not target_version or not _SEMVER_RE.match(target_version.strip()):
        raise ValueError(
            f"target_version must be a semantic version, got {target_version!r}"
        )
    target_version = target_version.strip()

    status = read_update_status()
    if (
        status["state"] in IN_FLIGHT_STATES
        and status["request_id"]
        and not _status_is_stale(status)
    ):
        return status["request_id"]  # re-attach to the in-flight run (no rewrite)

    request_id = str(uuid.uuid4())
    payload = {
        "request_id": request_id,
        "target_version": target_version,
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(settings.update_channel_dir, exist_ok=True)
    # Reset the shared status BEFORE publishing the request so any concurrent
    # status poll already sees THIS run's `preparing`, never a prior terminal state.
    _write_preparing_status(request_id)
    path = _request_path()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)  # atomic publish — the updater never sees a half-write
    return request_id
