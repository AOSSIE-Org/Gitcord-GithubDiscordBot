from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from ghdcbot.config.models import IdentityMapping
from ghdcbot.core.models import ContributionEvent, ContributionSummary, Score


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
                CREATE TABLE IF NOT EXISTS identity_links (
                    discord_user_id TEXT NOT NULL,
                    github_user TEXT NOT NULL,
                    verified INTEGER NOT NULL DEFAULT 0,
                    verification_code TEXT,
                    expires_at TEXT,
                    created_at TEXT NOT NULL,
                    verified_at TEXT,
                    PRIMARY KEY (discord_user_id, github_user)
                );
                CREATE INDEX IF NOT EXISTS idx_identity_links_github_user
                    ON identity_links (github_user);
                CREATE INDEX IF NOT EXISTS idx_identity_links_verified
                    ON identity_links (verified);
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
                SELECT DISTINCT github_user, event_type, repo, created_at, payload_json
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

    def list_contribution_summaries(
        self,
        period_start: datetime,
        period_end: datetime,
        weights: dict[str, int],
    ) -> Sequence[ContributionSummary]:
        start_utc = _ensure_utc(period_start)
        end_utc = _ensure_utc(period_end)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT github_user, event_type, repo, created_at, payload_json
                FROM contributions
                WHERE created_at >= ? AND created_at <= ?
                ORDER BY github_user ASC, created_at ASC
                """,
                (start_utc.isoformat(), end_utc.isoformat()),
            ).fetchall()

        summaries: dict[str, dict[str, int]] = {}
        for row in rows:
            user = row["github_user"]
            event_type = row["event_type"]
            bucket = summaries.setdefault(
                user,
                {
                    "issues_opened": 0,
                    "prs_opened": 0,
                    "prs_reviewed": 0,
                    "comments": 0,
                    "total_score": 0,
                },
            )
            if event_type == "issue_opened":
                bucket["issues_opened"] += 1
            elif event_type in {"pr_opened", "pr_merged"}:
                bucket["prs_opened"] += 1
            elif event_type == "pr_reviewed":
                bucket["prs_reviewed"] += 1
            elif event_type == "comment":
                bucket["comments"] += 1
            bucket["total_score"] += weights.get(event_type, 0)

        return [
            ContributionSummary(
                github_user=user,
                issues_opened=counts["issues_opened"],
                prs_opened=counts["prs_opened"],
                prs_reviewed=counts["prs_reviewed"],
                comments=counts["comments"],
                total_score=counts["total_score"],
                period_start=start_utc,
                period_end=end_utc,
            )
            for user, counts in sorted(summaries.items(), key=lambda item: item[0])
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

    def create_identity_claim(
        self,
        discord_user_id: str,
        github_user: str,
        verification_code: str,
        expires_at: datetime,
    ) -> None:
        """Create or refresh an identity claim for (discord_user_id, github_user).

        Impersonation protection:
        - If github_user is already verified for a different discord_user_id, reject.
        - If an unexpired claim exists for github_user under a different discord_user_id, reject.
        - If an expired claim exists for github_user under a different discord_user_id, replace it.
        """
        now = datetime.now(timezone.utc)
        expires_utc = _ensure_utc(expires_at)
        with self._connect() as conn:
            # Verified github_user owned by someone else -> reject
            row = conn.execute(
                """
                SELECT discord_user_id
                FROM identity_links
                WHERE github_user = ? AND verified = 1
                """,
                (github_user,),
            ).fetchone()
            if row and row["discord_user_id"] != discord_user_id:
                raise ValueError("github_user is already verified for another Discord user")

            # Pending claims for same github_user by other users: check all, reject if any active
            rows = conn.execute(
                """
                SELECT discord_user_id, expires_at
                FROM identity_links
                WHERE github_user = ? AND verified = 0 AND discord_user_id != ?
                """,
                (github_user, discord_user_id),
            ).fetchall()
            for row in rows:
                existing_expires = row["expires_at"]
                if existing_expires:
                    existing_dt = _parse_utc(existing_expires)
                    if existing_dt > now:
                        raise ValueError("github_user has an active pending claim by another Discord user")
            # No other user has an active claim; delete only expired pending rows for this github_user
            conn.execute(
                """
                DELETE FROM identity_links
                WHERE github_user = ? AND verified = 0 AND (expires_at IS NULL OR expires_at <= ?)
                """,
                (github_user, now.isoformat()),
            )

            # Enforce one verified mapping per discord user (prevent accidental multi-link)
            row = conn.execute(
                """
                SELECT github_user, verified
                FROM identity_links
                WHERE discord_user_id = ? AND github_user = ?
                """,
                (discord_user_id, github_user),
            ).fetchone()
            if row and int(row["verified"] or 0) == 1:
                raise ValueError("This Discord user and GitHub user are already verified; cannot create a new claim")

            row = conn.execute(
                """
                SELECT github_user
                FROM identity_links
                WHERE discord_user_id = ? AND verified = 1
                """,
                (discord_user_id,),
            ).fetchone()
            if row and row["github_user"] != github_user:
                raise ValueError("discord_user_id is already verified for another GitHub user")

            conn.execute(
                """
                INSERT INTO identity_links (
                    discord_user_id, github_user, verified, verification_code, expires_at, created_at, verified_at
                )
                VALUES (?, ?, 0, ?, ?, ?, NULL)
                ON CONFLICT(discord_user_id, github_user)
                DO UPDATE SET
                    verified = 0,
                    verification_code = excluded.verification_code,
                    expires_at = excluded.expires_at,
                    created_at = excluded.created_at,
                    verified_at = NULL
                """,
                (
                    discord_user_id,
                    github_user,
                    verification_code,
                    expires_utc.isoformat(),
                    now.isoformat(),
                ),
            )

    def get_identity_link(self, discord_user_id: str, github_user: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT discord_user_id, github_user, verified, verification_code, expires_at, created_at, verified_at
                FROM identity_links
                WHERE discord_user_id = ? AND github_user = ?
                """,
                (discord_user_id, github_user),
            ).fetchone()
        return dict(row) if row else None

    def mark_identity_verified(self, discord_user_id: str, github_user: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE identity_links
                SET verified = 1,
                    verified_at = ?,
                    verification_code = NULL,
                    expires_at = NULL
                WHERE discord_user_id = ? AND github_user = ?
                """,
                (now, discord_user_id, github_user),
            )

    def list_verified_identity_mappings(self) -> list[IdentityMapping]:
        """Return verified identity mappings for engine usage."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT discord_user_id, github_user
                FROM identity_links
                WHERE verified = 1
                ORDER BY discord_user_id ASC
                """
            ).fetchall()
        return [
            IdentityMapping(github_user=row["github_user"], discord_user_id=row["discord_user_id"])
            for row in rows
        ]


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
