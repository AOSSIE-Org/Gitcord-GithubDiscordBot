"""Regression shim for issue #248 (scheduled-run-once.sh flock scope).

The real check lives in ``scripts/test-lockscope.sh`` because the regression
is a shell-level race on ``flock(1)``. This shim runs that harness under
pytest and skips on non-POSIX hosts (Windows CI, or any environment without
``flock(1)`` on PATH).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "scripts" / "test-lockscope.sh"


@pytest.mark.skipif(
    os.name != "posix" or shutil.which("flock") is None or shutil.which("bash") is None,
    reason="requires a POSIX shell with flock(1)",
)
def test_scheduled_run_once_holds_lock_across_both_commands() -> None:
    """Two concurrent invocations must produce one success + one skip."""
    assert HARNESS.exists(), f"missing harness: {HARNESS}"
    result = subprocess.run(
        ["bash", str(HARNESS)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"lockscope harness failed (rc={result.returncode}):\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )
