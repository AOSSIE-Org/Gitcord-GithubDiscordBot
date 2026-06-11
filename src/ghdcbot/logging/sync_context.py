from __future__ import annotations

import contextvars
import logging
import secrets
import time
from collections import Counter
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from typing import Any

_sync_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("sync_id", default=None)


def generate_sync_id() -> str:
    now = datetime.now(timezone.utc)
    suffix = secrets.token_hex(2)
    return f"sync_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


def get_sync_id() -> str | None:
    return _sync_id_var.get()


def _contribution_event_type(event: Any) -> str:
    event_type = getattr(event, "event_type", None)
    if isinstance(event_type, str) and event_type:
        return event_type
    return "unknown"


class SyncContextFilter(logging.Filter):
    """Attach the active sync_id to every log record in the current context."""

    def filter(self, record: logging.LogRecord) -> bool:
        sync_id = _sync_id_var.get()
        if sync_id is not None:
            record.sync_id = sync_id
        return True


class SyncSession(AbstractContextManager["SyncSession"]):
    """Lifecycle logging for a single run-once or /sync execution."""

    _EVENT_SUMMARY_TYPES = (
        "issue_opened",
        "issue_closed",
        "pr_opened",
        "pr_merged",
        "pr_reviewed",
    )

    def __init__(self) -> None:
        self.sync_id = generate_sync_id()
        self._token: contextvars.Token[str | None] | None = None
        self._started_monotonic = time.monotonic()
        self._logger = logging.getLogger("Orchestrator")
        self.cursor_before: str | None = None
        self.cursor_after: str | None = None

    def __enter__(self) -> SyncSession:
        self._token = _sync_id_var.set(self.sync_id)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> bool:
        if self._token is not None:
            _sync_id_var.reset(self._token)
        return False

    def _duration_ms(self) -> int:
        return int((time.monotonic() - self._started_monotonic) * 1000)

    def log_started(self, cursor_before: datetime, repos_total: int) -> None:
        self.cursor_before = cursor_before.isoformat()
        self._logger.info(
            "GitHub sync started",
            extra={
                "event": "github_sync_started",
                "sync_id": self.sync_id,
                "cursor_before": self.cursor_before,
                "repos_total": repos_total,
            },
        )

    def log_completed(
        self,
        cursor_after: datetime,
        *,
        repos_processed: int,
        events_fetched: int,
        requests_total: int,
    ) -> None:
        self.cursor_after = cursor_after.isoformat()
        self.log_request_summary(requests_total)
        self._logger.info(
            "GitHub sync completed",
            extra={
                "event": "github_sync_completed",
                "sync_id": self.sync_id,
                "cursor_before": self.cursor_before,
                "cursor_after": self.cursor_after,
                "repos_processed": repos_processed,
                "events_fetched": events_fetched,
                "duration_ms": self._duration_ms(),
            },
        )

    def log_event_summary(self, contributions: list[Any]) -> None:
        counts = Counter(_contribution_event_type(event) for event in contributions)
        extra = self._event_summary_extra(dict(counts))
        unknown_count = counts.get("unknown", 0)
        if unknown_count:
            extra["unknown"] = unknown_count
        self._logger.info(
            "GitHub event summary",
            extra=extra,
        )

    def log_request_summary(self, requests_total: int) -> None:
        self._logger.info(
            "GitHub request summary",
            extra={
                "event": "github_request_summary",
                "sync_id": self.sync_id,
                "requests_total": requests_total,
            },
        )

    def log_failed(self, error: str) -> None:
        self._logger.error(
            "GitHub sync failed",
            extra={
                "event": "github_sync_failed",
                "sync_id": self.sync_id,
                "error": error,
                "duration_ms": self._duration_ms(),
            },
        )

    def _event_summary_extra(self, counts: dict[str, int]) -> dict[str, Any]:
        extra: dict[str, Any] = {
            "event": "github_event_summary",
            "sync_id": self.sync_id,
        }
        for event_type in self._EVENT_SUMMARY_TYPES:
            extra[event_type] = counts.get(event_type, 0)
        return extra
