"""Cross-language integration: real request_update() + real scripts/updater.sh.

The UAT found a BLOCKER the unit tests missed because each side mocked
the other: the backend's reset pre-seeds status.json with this run's
request_id BEFORE the updater sees the request, and the updater's old dedup
treated request.json.request_id == status.json.request_id as "already processed"
and no-opped forever. This test runs BOTH real components against a shared temp
channel with a hermetic fake `docker` shim (no real docker, no network) and
asserts the updater actually PROCESSES the request — it fails on the old
status.json-keyed dedup and passes on the processed_id-marker fix.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core import config as cfg_module
from app.services import update_apply

# repo_root/backend/tests/this_file -> parents[2] == repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
UPDATER_SH = REPO_ROOT / "scripts" / "updater.sh"

# Hermetic fake `docker`: records calls and returns canned output for the happy
# path (always healthy), so the real updater walks preparing->...->success
# without touching a real engine. Mirrors scripts/test_updater.sh's shim.
_FAKE_DOCKER = """#!/usr/bin/env sh
echo "docker $*" >> "${DOCKER_CALLS}"
case "${1}" in
  compose)
    shift
    while [ "${1:-}" = "-f" ]; do shift 2; done
    case "${1:-}" in
      ps) echo "fakeappcid0001" ;;
      *)  : ;;
    esac
    ;;
  inspect) echo "sha256:oldimageid0001" ;;
  exec)
    shift
    while [ "${1:-}" = "-e" ]; do shift 2; done
    shift
    case "$*" in
      *sqlite3*alembic_version*) echo "head_rev_0001" ;;
      *curl*healthcheck*) exit 0 ;;
      *) exit 0 ;;
    esac
    ;;
  *) : ;;
esac
exit 0
"""


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh unavailable")
def test_real_request_update_then_real_updater_processes(tmp_path, monkeypatch):
    channel = tmp_path / "update"
    channel.mkdir()
    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(channel))

    # 1) Real backend call: writes request.json AND pre-seeds status.json
    #    with THIS run's request_id — the exact collision that wedged the updater.
    request_id = update_apply.request_update("v9.9.9")

    request = json.loads((channel / "request.json").read_text())
    status = json.loads((channel / "status.json").read_text())
    assert request["request_id"] == request_id
    assert status["request_id"] == request_id  # collision condition
    assert status["state"] == "preparing"

    # 2) Hermetic fake docker on PATH.
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    docker = fakebin / "docker"
    docker.write_text(_FAKE_DOCKER)
    docker.chmod(0o755)

    # 3) Run the REAL updater (oneshot) against the same channel.
    env = {
        **os.environ,
        "PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_CALLS": str(tmp_path / "docker-calls.log"),
        "UPDATER_ONESHOT": "1",
        "UPDATE_CHANNEL_DIR": str(channel),
        "COMPOSE_FILE": str(tmp_path / "compose.yml"),
        "UPDATER_LOG_FILE": str(tmp_path / "updater.log"),
        "HEALTHCHECK_TIMEOUT": "4",
        "HEALTHCHECK_INTERVAL": "1",
    }
    result = subprocess.run(
        ["sh", str(UPDATER_SH)],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr

    # 4) The updater must have PROCESSED the request, not no-opped on the
    #    pre-seeded status. Old (status.json-keyed) dedup leaves it at 'preparing'.
    #    strict=False: the updater's log_tail can carry a raw control char when the
    #    script runs under macOS BSD sed on the host (alpine BusyBox in production
    #    collapses it cleanly); we only care about the structured state here.
    final = json.loads((channel / "status.json").read_text(), strict=False)
    assert final["state"] == "success", (
        f"updater wedged at '{final['state']}' — the backend pre-seed collided "
        f"with the updater dedup.\nupdater stdout:\n{result.stdout}"
    )
    # And it claimed the request in its OWN marker (not status.json).
    assert (channel / "processed_id").read_text().strip() == request_id


@pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh unavailable")
def test_real_updater_reattaches_same_request_id(tmp_path, monkeypatch):
    """A second updater poll for the SAME request_id is a no-op."""
    channel = tmp_path / "update"
    channel.mkdir()
    monkeypatch.setattr(cfg_module.settings, "update_channel_dir", str(channel))
    request_id = update_apply.request_update("v9.9.9")

    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    docker = fakebin / "docker"
    docker.write_text(_FAKE_DOCKER)
    docker.chmod(0o755)

    calls = tmp_path / "docker-calls.log"
    env = {
        **os.environ,
        "PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls),
        "UPDATER_ONESHOT": "1",
        "UPDATE_CHANNEL_DIR": str(channel),
        "COMPOSE_FILE": str(tmp_path / "compose.yml"),
        "UPDATER_LOG_FILE": str(tmp_path / "updater.log"),
        "HEALTHCHECK_TIMEOUT": "4",
        "HEALTHCHECK_INTERVAL": "1",
    }

    def run_once():
        return subprocess.run(
            ["sh", str(UPDATER_SH)], env=env, capture_output=True, text=True, timeout=30
        )

    assert run_once().returncode == 0  # first pass processes + claims
    calls.write_text("")  # truncate: only the SECOND pass should be recorded
    assert run_once().returncode == 0  # second pass: same request_id
    assert calls.read_text() == "", "re-running the same request_id was not a no-op"
