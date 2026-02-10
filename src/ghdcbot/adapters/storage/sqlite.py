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
            # Additive: unlinked_at for unlink flow (preserve history, no row delete).
            try:
                conn.execute("ALTER TABLE identity_links ADD COLUMN unlinked_at TEXT")
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
            # Issue requests: contributor requests for assignment, mentor reviews
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS issue_requests (
                    request_id TEXT PRIMARY KEY,
                    discord_user_id TEXT NOT NULL,
                    github_user TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    issue_number INTEGER NOT NULL,
                    issue_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                );
                CREATE INDEX IF NOT EXISTS idx_issue_requests_status ON issue_requests (status);
                CREATE INDEX IF NOT EXISTS idx_issue_requests_created ON issue_requests (created_at);
                CREATE TABLE IF NOT EXISTS notifications_sent (
                    dedupe_key TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    github_user TEXT NOT NULL,
                    discord_user_id TEXT NOT NULL,
                    repo TEXT NOT NULL,
                    target TEXT NOT NULL,
                    channel_id TEXT,
                    sent_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_notifications_sent_github_user ON notifications_sent (github_user);
                CREATE INDEX IF NOT EXISTS idx_notifications_sent_discord_user ON notifications_sent (discord_user_id);
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
        difficulty_weights: dict[str, int] | None = None,
    ) -> Sequence[ContributionSummary]:
        start_utc = _ensure_utc(period_start)
        end_utc = _ensure_utc(period_end)
        # Normalize difficulty weights keys to lowercase for case-insensitive matching
        normalized_difficulty_weights = None
        if difficulty_weights:
            normalized_difficulty_weights = {
                k.lower(): v for k, v in difficulty_weights.items()
            }
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
            # Scoring: merge-only to prevent spam and align incentives with mentor-approved contributions.
            # Only pr_merged events contribute to scores. All other events remain visible in reports
            # but do not affect scores.
            if event_type == "pr_merged":
                # Check if this is a merged PR with difficulty labels
                if normalized_difficulty_weights:
                    payload = json.loads(row["payload_json"])
                    difficulty_labels = payload.get("difficulty_labels", [])
                    if difficulty_labels:
                        # Find matching difficulty labels (case-insensitive)
                        matching_weights = []
                        for label in difficulty_labels:
                            label_lower = label.lower() if isinstance(label, str) else str(label).lower()
                            if label_lower in normalized_difficulty_weights:
                                matching_weights.append(normalized_difficulty_weights[label_lower])
                        if matching_weights:
                            # Use max weight if multiple labels exist
                            bucket["total_score"] += max(matching_weights)
                            continue
                # Fallback to weight-based scoring for merged PRs
                bucket["total_score"] += weights.get("pr_merged", 0)
            # All other event types are ignored for scoring (but remain in counts/reports)

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
        *,
        max_age_days: int | None = None,
    ) -> None:
        """Create or refresh an identity claim for (discord_user_id, github_user).

        Impersonation protection:
        - If github_user is already verified for a different discord_user_id, reject.
        - If an unexpired claim exists for github_user under a different discord_user_id, reject.
        - If an expired claim exists for github_user under a different discord_user_id, replace it.

        Stale refresh:
        - If already verified for same pair and stale (per max_age_days), allow creating new claim to refresh.
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
            # Exception: allow refresh if verified identity is stale
            row = conn.execute(
                """
                SELECT github_user, verified, verified_at
                FROM identity_links
                WHERE discord_user_id = ? AND github_user = ?
                """,
                (discord_user_id, github_user),
            ).fetchone()
            if row and int(row["verified"] or 0) == 1:
                # Check if stale
                is_stale = False
                if max_age_days is not None and max_age_days > 0:
                    verified_at_raw = row["verified_at"]
                    if verified_at_raw:
                        verified_at = _parse_utc(verified_at_raw)
                        age_days = (now - verified_at).days
                        if age_days >= max_age_days:
                            is_stale = True
                if not is_stale:
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
        self.init_schema()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT discord_user_id, github_user, verified, verification_code,
                       expires_at, created_at, verified_at, unlinked_at
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

    def unlink_identity(
        self, discord_user_id: str, cooldown_hours: int
    ) -> dict | None:
        """Unlink the verified identity for this Discord user (set verified=0, unlinked_at=now).
        Rows are never deleted. Returns unlink info for audit, or None if no verified link.
        Raises ValueError if inside cooldown window.
        Read-check-write is done in a single connection to avoid TOCTOU races.
        """
        from datetime import timedelta

        self.init_schema()
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT discord_user_id, github_user, verified_at
                FROM identity_links
                WHERE discord_user_id = ? AND verified = 1
                """,
                (discord_user_id,),
            ).fetchone()
            if not row:
                return None
            verified_at_raw = row["verified_at"]
            if not verified_at_raw:
                return None
            verified_at = _parse_utc(verified_at_raw)
            cooldown = timedelta(hours=cooldown_hours)
            if now - verified_at < cooldown:
                remaining = (verified_at + cooldown) - now
                total_seconds = int(remaining.total_seconds())
                hours, rem = divmod(total_seconds, 3600)
                minutes, _ = divmod(rem, 60)
                if hours > 0:
                    remaining_str = f"{hours}h {minutes}m"
                else:
                    remaining_str = f"{minutes}m"
                raise ValueError(
                    f"Identity was verified recently. You can unlink after {remaining_str}."
                )
            github_user = row["github_user"]
            conn.execute(
                """
                UPDATE identity_links
                SET verified = 0, unlinked_at = ?
                WHERE discord_user_id = ? AND github_user = ?
                """,
                (now_iso, discord_user_id, github_user),
            )
        return {
            "discord_user_id": discord_user_id,
            "github_user": github_user,
            "verified_at": verified_at_raw,
            "unlinked_at": now_iso,
        }

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

    def get_identity_links_for_discord_user(self, discord_user_id: str) -> list[dict]:
        """Return all identity link rows for a Discord user (verified and pending).
        Optional method; not part of the Storage protocol. Used for /verify and /status.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT discord_user_id, github_user, verified, verification_code,
                       expires_at, created_at, verified_at, unlinked_at
                FROM identity_links
                WHERE discord_user_id = ?
                ORDER BY verified DESC, created_at DESC
                """,
                (discord_user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_identity_status(self, discord_user_id: str, max_age_days: int | None = None) -> dict:
        """Read-only: return current identity status for a Discord user.
        Returns dict with github_user, status ('verified'|'verified_stale'|'pending'|'not_linked'),
        verified_at (UTC ISO or None), is_stale (bool).
        Does not modify data, auto-verify, or clean expired claims.
        
        Args:
            discord_user_id: Discord user ID to check
            max_age_days: Optional max age in days for verified identities. If set and verified_at
                         is older than this, status will be 'verified_stale' and is_stale=True.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT discord_user_id, github_user, verified, verified_at
                FROM identity_links
                WHERE discord_user_id = ? AND (unlinked_at IS NULL)
                ORDER BY verified DESC, created_at DESC
                LIMIT 1
                """,
                (discord_user_id,),
            ).fetchone()
        if not row:
            return {"github_user": None, "status": "not_linked", "verified_at": None, "is_stale": False}
        if int(row["verified"] or 0) == 1:
            verified_at_raw = row["verified_at"]
            is_stale = False
            status = "verified"
            if verified_at_raw and max_age_days is not None and max_age_days > 0:
                verified_at = _parse_utc(verified_at_raw)
                age_days = (datetime.now(timezone.utc) - verified_at).days
                if age_days >= max_age_days:
                    is_stale = True
                    status = "verified_stale"
            return {
                "github_user": row["github_user"],
                "status": status,
                "verified_at": verified_at_raw,
                "is_stale": is_stale,
            }
        return {
            "github_user": row["github_user"],
            "status": "pending",
            "verified_at": None,
            "is_stale": False,
        }

    def insert_issue_request(
        self,
        request_id: str,
        discord_user_id: str,
        github_user: str,
        owner: str,
        repo: str,
        issue_number: int,
        issue_url: str,
    ) -> None:
        """Store a new issue assignment request. Status is pending."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO issue_requests (
                    request_id, discord_user_id, github_user, owner, repo,
                    issue_number, issue_url, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (request_id, discord_user_id, github_user, owner, repo, issue_number, issue_url, now),
            )

    def list_pending_issue_requests(self) -> list[dict]:
        """Return all issue requests with status pending, ordered by created_at ascending."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id, discord_user_id, github_user, owner, repo,
                       issue_number, issue_url, created_at, status
                FROM issue_requests
                WHERE status = 'pending'
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_issue_request(self, request_id: str) -> dict | None:
        """Return a single issue request by request_id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT request_id, discord_user_id, github_user, owner, repo, issue_number, issue_url, created_at, status FROM issue_requests WHERE request_id = ?",
                (request_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_issue_request_status(self, request_id: str, status: str) -> None:
        """Update request status to approved, rejected, or cancelled."""
        if status not in ("pending", "approved", "rejected", "cancelled"):
            raise ValueError(f"Invalid status: {status}")
        with self._connect() as conn:
            conn.execute("UPDATE issue_requests SET status = ? WHERE request_id = ?", (status, request_id))

    def append_audit_event(self, event: dict) -> None:
        """Append a single audit event (append-only) to data_dir/audit_events.jsonl.
        Optional method; not part of the Storage protocol.
        """
        path = self._db_path.parent / "audit_events.jsonl"
        payload = dict(event)
        if "timestamp" not in payload:
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        path.open("a", encoding="utf-8").write(line)

    def list_audit_events(self) -> list[dict]:
        """Read-only: return all audit events from audit_events.jsonl.
        Returns empty list if file doesn't exist. Does not modify data.
        Optional method; not part of the Storage protocol.
        """
        path = self._db_path.parent / "audit_events.jsonl"
        events = []
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        return events

    def was_notification_sent(self, dedupe_key: str) -> bool:
        """Check if notification was already sent (deduplication)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM notifications_sent WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
        return row is not None

    def mark_notification_sent(
        self,
        dedupe_key: str,
        event: Any,
        discord_user_id: str,
        channel_id: str | None,
        target_github_user: str | None = None,
    ) -> None:
        """Mark notification as sent (deduplication tracking)."""
        now = datetime.now(timezone.utc).isoformat()
        target = str(event.payload.get("issue_number") or event.payload.get("pr_number") or "")
        # Use target_github_user if provided (for pr_reviewed), else event.github_user
        github_user = target_github_user or event.github_user
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO notifications_sent
                (dedupe_key, event_type, github_user, discord_user_id, repo, target, channel_id, sent_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dedupe_key,
                    event.event_type,
                    github_user,
                    discord_user_id,
                    event.repo,
                    target,
                    channel_id,
                    now,
                ),
            )

    def list_recent_notifications(self, limit: int = 1000) -> list[dict]:
        """List recent notifications (for snapshot export).
        Returns list of notification dicts, ordered by sent_at DESC.
        Optional method; not part of the Storage protocol.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT dedupe_key, event_type, github_user, discord_user_id, repo, target, channel_id, sent_at
                FROM notifications_sent
                ORDER BY sent_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


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
