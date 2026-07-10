#!/usr/bin/env python3
"""Fail CI if ruff or mypy error counts exceed lint-baseline.json.

Also fails on improvement, so a lowered count must be locked into the
baseline file in the same PR. Run from backend/: `uv run python scripts/lint_ratchet.py`.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent.parent / "lint-baseline.json"


def count_ruff_errors() -> int:
    result = subprocess.run(
        ["ruff", "check", "app", "tests", "--output-format", "json"],
        capture_output=True,
        text=True,
    )
    violations = json.loads(result.stdout)
    return len(violations)


def count_mypy_errors() -> int:
    result = subprocess.run(
        ["mypy", "app"],
        capture_output=True,
        text=True,
    )
    match = re.search(r"Found (\d+) errors?", result.stdout)
    if match:
        return int(match.group(1))
    return 0


def main() -> int:
    baseline = json.loads(BASELINE_PATH.read_text())
    current = {
        "ruff": count_ruff_errors(),
        "mypy": count_mypy_errors(),
    }

    failed = False
    for tool, current_count in current.items():
        baseline_count = baseline[tool]
        if current_count > baseline_count:
            print(
                f"RATCHET FAIL: {tool} went from {baseline_count} to "
                f"{current_count} (+{current_count - baseline_count} new errors)"
            )
            failed = True
        elif current_count < baseline_count:
            print(
                f"RATCHET: {tool} improved {baseline_count} -> {current_count}. "
                f"Lower the number in lint-baseline.json in this PR."
            )
            failed = True
        else:
            print(f"OK: {tool} at {current_count} (baseline {baseline_count})")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
