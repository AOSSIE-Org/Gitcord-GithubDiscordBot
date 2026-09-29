"""Contributions are stored once even when the event at the sync cursor is re-fetched."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.core.models import ContributionEvent

T0 = datetime(2026, 9, 29, 3, 25, 34, tzinfo=timezone.utc)
EPOCH = T0 - timedelta(days=365)


def _event(user: str = "alice", kind: str = "pr_reviewed", at: datetime = T0, **payload) -> ContributionEvent:
    return ContributionEvent(
        github_user=user,
        event_type=kind,
        repo="Gluon-EVM",
        created_at=at,
        payload={"pr_number": 40, **payload},
    )


def _row_count(tmp_path) -> int:
    with sqlite3.connect(tmp_path / "state.db") as conn:
        return conn.execute("SELECT COUNT(*) FROM contributions").fetchone()[0]


def test_refetched_events_are_not_stored_again(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    batch = [_event(), _event(kind="comment")]

    assert storage.record_contributions(batch) == 2
    assert storage.record_contributions(batch) == 0
    assert storage.record_contributions([_event(), _event(user="bob")]) == 1

    assert _row_count(tmp_path) == 3
    assert len(storage.list_contributions(EPOCH)) == 3


def test_same_event_with_different_payload_is_kept(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()

    assert storage.record_contributions([_event(state="open")]) == 1
    assert storage.record_contributions([_event(state="closed")]) == 1
    assert len(storage.list_contributions(EPOCH)) == 2


def _legacy_db_with_duplicates(tmp_path) -> None:
    """Pre-fix schema (no dedupe_key) holding repeated rows, as on the live volumes."""
    rows = []
    for _ in range(17):
        rows.append(("alice", "pr_reviewed", "Gluon-EVM", T0.isoformat(), json.dumps({"pr_number": 40})))
        rows.append(("alice", "comment", "Gluon-EVM", T0.isoformat(), json.dumps({"pr_number": 40})))
    rows.append(("bob", "pr_merged", "Other", (T0 - timedelta(days=1)).isoformat(), json.dumps({"pr_number": 7})))
    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.execute(
            """
            CREATE TABLE contributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                github_user TEXT NOT NULL,
                event_type TEXT NOT NULL,
                repo TEXT NOT NULL,
                created_at TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            "INSERT INTO contributions (github_user, event_type, repo, created_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def test_migration_removes_duplicates_without_changing_reads(tmp_path) -> None:
    _legacy_db_with_duplicates(tmp_path)
    storage = SqliteStorage(str(tmp_path))
    period_end = T0 + timedelta(days=1)

    with sqlite3.connect(tmp_path / "state.db") as conn:
        conn.row_factory = sqlite3.Row
        before_rows = conn.execute(
            "SELECT DISTINCT github_user, event_type, repo, created_at, payload_json "
            "FROM contributions ORDER BY created_at, event_type"
        ).fetchall()
    before = [tuple(r) for r in before_rows]
    assert _row_count(tmp_path) == 35

    storage.init_schema()
    storage.init_schema()

    assert _row_count(tmp_path) == 3
    after = [
        (e.github_user, e.event_type, e.repo, e.created_at.isoformat(), json.dumps(e.payload))
        for e in storage.list_contributions(EPOCH)
    ]
    assert sorted(after) == sorted(before)

    summaries = {s.github_user: s for s in storage.list_contribution_summaries(EPOCH, period_end)}
    assert summaries["alice"].prs_reviewed == 1
    assert summaries["alice"].comments == 1
    assert summaries["bob"].prs_opened == 1

    with sqlite3.connect(tmp_path / "state.db") as conn:
        kept_ids = [r[0] for r in conn.execute("SELECT id FROM contributions ORDER BY id")]
        assert kept_ids == [1, 2, 35]
        indexes = {r[1] for r in conn.execute("PRAGMA index_list('contributions')")}
        assert "idx_contributions_dedupe_key" in indexes


def test_rows_written_without_key_are_keyed_on_next_init(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    storage.record_contributions([_event()])

    stored = storage.list_contributions(EPOCH)[0]
    with sqlite3.connect(tmp_path / "state.db") as conn:
        for _ in range(2):
            conn.execute(
                "INSERT INTO contributions (github_user, event_type, repo, created_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    stored.github_user,
                    stored.event_type,
                    stored.repo,
                    stored.created_at.isoformat(),
                    json.dumps(stored.payload, separators=(",", ":")),
                ),
            )
    assert _row_count(tmp_path) == 3

    storage.init_schema()

    assert _row_count(tmp_path) == 1
    assert storage.record_contributions([_event()]) == 0
