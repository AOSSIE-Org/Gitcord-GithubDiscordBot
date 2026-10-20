from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time

import pytest

from ghdcbot.engine import sync_lock
from ghdcbot.engine.sync_lock import (
    LOCK_FILE_ENV,
    SyncAlreadyRunningError,
    resolve_lock_path,
    run_once_lock,
)

pytestmark = pytest.mark.skipif(sync_lock.fcntl is None, reason="flock is POSIX-only")

needs_flock_binary = pytest.mark.skipif(
    shutil.which("flock") is None, reason="util-linux flock binary not installed"
)


@pytest.fixture(autouse=True)
def _no_lock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOCK_FILE_ENV, raising=False)


def test_default_lock_path_is_in_data_dir(tmp_path):
    assert resolve_lock_path(tmp_path) == tmp_path / "run-once.lock"


def test_env_override_matches_sync_loop_script(tmp_path, monkeypatch):
    custom = tmp_path / "elsewhere" / "custom.lock"
    monkeypatch.setenv(LOCK_FILE_ENV, str(custom))
    assert resolve_lock_path(tmp_path) == custom


def test_second_holder_is_rejected_and_lock_is_released_after(tmp_path):
    with run_once_lock(tmp_path):
        with pytest.raises(SyncAlreadyRunningError):
            with run_once_lock(tmp_path):
                pytest.fail("a second run-once must not enter while the first holds the lock")
    with run_once_lock(tmp_path):  # released on exit, so it can be taken again
        pass


def test_lock_is_released_when_the_body_raises(tmp_path):
    with pytest.raises(ValueError):
        with run_once_lock(tmp_path):
            raise ValueError("sync blew up")
    with run_once_lock(tmp_path):
        pass


def test_missing_data_dir_is_created(tmp_path):
    data_dir = tmp_path / "not-yet"
    with run_once_lock(data_dir):
        assert (data_dir / "run-once.lock").exists()


def test_unwritable_lock_location_does_not_block_the_sync(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("not a directory")
    ran = False
    with run_once_lock(blocker):  # <file>/run-once.lock cannot be created
        ran = True
    assert ran


@needs_flock_binary
def test_python_lock_excludes_the_shell_scheduler(tmp_path):
    """scripts/sync-loop.sh uses `flock -n`; a running /sync must make it skip."""
    lock = tmp_path / "run-once.lock"
    with run_once_lock(tmp_path):
        assert subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 1
    assert subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0


@needs_flock_binary
def test_running_scheduler_blocks_manual_sync(tmp_path):
    """A scheduled run holding the lock via `flock` must make /sync back off."""
    lock = tmp_path / "run-once.lock"
    ready = tmp_path / "holder-has-lock"
    # The holder creates `ready` only after flock(1) has acquired the lock, so there is no race.
    # flock(1) forks its command, so the whole process group is stopped during cleanup.
    holder = subprocess.Popen(
        ["flock", str(lock), "sh", "-c", 'touch "$1"; sleep 30', "sh", str(ready)],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists(), "scheduler stand-in never acquired the lock"
        with pytest.raises(SyncAlreadyRunningError):
            with run_once_lock(tmp_path):
                pytest.fail("/sync must not run while the scheduler holds the lock")
    finally:
        os.killpg(holder.pid, signal.SIGTERM)
        holder.wait(timeout=5)

    # Once the scheduler run ends the lock frees up (allow a moment for the child to exit).
    deadline = time.monotonic() + 5
    while True:
        try:
            with run_once_lock(tmp_path):
                break
        except SyncAlreadyRunningError:
            assert time.monotonic() < deadline, "lock was never released"
            time.sleep(0.05)
