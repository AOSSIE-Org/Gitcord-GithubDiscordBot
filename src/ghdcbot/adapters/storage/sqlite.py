from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from ghdcbot.core.models import ContributionEvent, Score


class SqliteStorage:
    def __init__(self, data_dir: str) -> None:
        self._db_path = Path(data_dir) / "state.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS contributions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    github_user TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scores (
                    github_user TEXT NOT NULL,
                    period_start TEXT NOT NULL,
                    period_end TEXT NOT NULL,
                    points INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (github_user, period_start, period_end)
                );
                CREATE TABLE IF NOT EXISTS cursors (
                    source TEXT PRIMARY KEY,
                    cursor TEXT NOT NULL
                );
                """
            )

    def record_contributions(self, events: Iterable[ContributionEvent]) -> int:
        stored = 0
        with self._connect() as conn:
            for event in events:
                created_at = _ensure_utc(event.created_at)
                conn.execute(
                    """
                    INSERT INTO contributions (github_user, event_type, repo, created_at, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        event.github_user,
                        event.event_type,
                        event.repo,
                        created_at.isoformat(),
                        json.dumps(event.payload, separators=(",", ":")),
                    ),
                )
                stored += 1
        return stored

    def list_contributions(self, since: datetime) -> Sequence[ContributionEvent]:
        since_utc = _ensure_utc(since)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT github_user, event_type, repo, created_at, payload_json
                FROM contributions
                WHERE created_at >= ?
                ORDER BY created_at ASC
                """,
                (since_utc.isoformat(),),
            ).fetchall()
        return [
            ContributionEvent(
                github_user=row["github_user"],
                event_type=row["event_type"],
                repo=row["repo"],
                created_at=_parse_utc(row["created_at"]),
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def upsert_scores(self, scores: Sequence[Score]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO scores (github_user, period_start, period_end, points, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(github_user, period_start, period_end)
                DO UPDATE SET points = excluded.points, updated_at = excluded.updated_at
                """,
                [
                    (
                        score.github_user,
                        _ensure_utc(score.period_start).isoformat(),
                        _ensure_utc(score.period_end).isoformat(),
                        score.points,
                        now,
                    )
                    for score in scores
                ],
            )

    def get_scores(self) -> Sequence[Score]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT github_user, period_start, period_end, points
                FROM scores
                ORDER BY points DESC
                """
            ).fetchall()
        return [
            Score(
                github_user=row["github_user"],
                period_start=_parse_utc(row["period_start"]),
                period_end=_parse_utc(row["period_end"]),
                points=row["points"],
            )
            for row in rows
        ]

    def get_cursor(self, source: str) -> datetime | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cursor FROM cursors WHERE source = ?", (source,)
            ).fetchone()
        if not row:
            return None
        return _parse_utc(row["cursor"])

    def set_cursor(self, source: str, cursor: datetime) -> None:
        cursor_utc = _ensure_utc(cursor)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cursors (source, cursor)
                VALUES (?, ?)
                ON CONFLICT(source) DO UPDATE SET cursor = excluded.cursor
                """,
                (source, cursor_utc.isoformat()),
            )


def _ensure_utc(value: datetime) -> datetime:
    """Normalize timestamps to UTC with tzinfo for safe SQLite ordering."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
