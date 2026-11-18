"""Cross-process lock shared by every ``run-once`` entry point.

``scripts/sync-loop.sh`` (the Compose ``scheduler`` profile and the Fly.io supervisor) wraps each
scheduled run in ``flock -n`` on ``$GITCORD_SYNC_LOCK_FILE`` (default ``/data/run-once.lock``).
The mentor ``/sync`` slash command takes the *same* lock through :func:`run_once_lock`, so a manual
sync and a scheduled sync cannot run at the same time against one data volume.

Both sides use ``flock(2)`` on the same file, so Python's ``fcntl.flock`` and the util-linux
``flock`` binary exclude each other.

The lock is best effort by design: if it cannot be set up (read-only volume, a filesystem without
``flock`` support, or a platform without ``fcntl`` such as Windows) the sync still runs, with a
warning, exactly as it did before the lock existed.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:  # fcntl is POSIX-only; on Windows (local development) locking is skipped.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX platforms
    fcntl = None  # type: ignore[assignment]

LOCK_FILE_ENV = "GITCORD_SYNC_LOCK_FILE"
DEFAULT_LOCK_NAME = "run-once.lock"

logger = logging.getLogger("ghdcbot.sync_lock")


class SyncAlreadyRunningError(RuntimeError):
    """Another ``run-once`` (scheduled or manual) already holds the sync lock."""


def resolve_lock_path(data_dir: str | os.PathLike[str]) -> Path:
    """Lock file path, matching ``scripts/sync-loop.sh``.

    ``GITCORD_SYNC_LOCK_FILE`` wins; otherwise ``<data_dir>/run-once.lock`` (``/data`` in Docker).
    """
    override = os.environ.get(LOCK_FILE_ENV, "").strip()
    if override:
        return Path(override)
    return Path(data_dir) / DEFAULT_LOCK_NAME


@contextmanager
def run_once_lock(data_dir: str | os.PathLike[str]) -> Iterator[None]:
    """Hold the exclusive run-once lock for the duration of the ``with`` block.

    Raises :class:`SyncAlreadyRunningError` immediately (never blocks) if another process holds it.
    The lock is released when the block exits, including on error.
    """
    if fcntl is None:
        logger.debug("flock unavailable on this platform; running sync without a lock")
        yield
        return

    path = resolve_lock_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    except OSError as exc:
        logger.warning("Cannot open sync lock %s (%s); running sync without a lock", path, exc)
        yield
        return

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SyncAlreadyRunningError(f"another run-once holds {path}") from exc
        except OSError as exc:
            logger.warning("Cannot lock %s (%s); running sync without a lock", path, exc)
        yield
    finally:
        os.close(fd)  # closing the descriptor releases the flock
