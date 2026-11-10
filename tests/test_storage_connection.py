from __future__ import annotations

import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage


def test_connect_closes_connection_on_clean_exit(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    conn_ref: sqlite3.Connection | None = None
    with storage._connect() as conn:
        conn_ref = conn
        conn.execute("CREATE TABLE test (id INT)")

    assert conn_ref is not None
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        conn_ref.execute("SELECT 1")


def test_connect_closes_connection_on_exception(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    conn_ref: sqlite3.Connection | None = None
    with pytest.raises(RuntimeError, match="simulated error"), storage._connect() as conn:
        conn_ref = conn
        raise RuntimeError("simulated error")

    assert conn_ref is not None
    with pytest.raises(sqlite3.ProgrammingError, match="Cannot operate on a closed database"):
        conn_ref.execute("SELECT 1")


def test_connect_commits_on_clean_exit(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    with storage._connect() as conn:
        conn.execute("CREATE TABLE test (val TEXT)")

    with storage._connect() as conn:
        conn.execute("INSERT INTO test VALUES ('saved')")

    with storage._connect() as conn:
        row = conn.execute("SELECT val FROM test").fetchone()
        assert row is not None
        assert row[0] == "saved"


def test_connect_rolls_back_on_exception(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    with storage._connect() as conn:
        conn.execute("CREATE TABLE test (val TEXT)")

    with pytest.raises(RuntimeError, match="boom"), storage._connect() as conn:
        conn.execute("INSERT INTO test VALUES ('failed')")
        raise RuntimeError("boom")

    with storage._connect() as conn:
        rows = conn.execute("SELECT * FROM test").fetchall()
        assert len(rows) == 0


def test_storage_does_not_leak_file_handles_in_temp_dir() -> None:
    """Verify tempfile.TemporaryDirectory teardown does not fail due to open SQLite file handles."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = SqliteStorage(tmp_dir)
        storage.init_schema()
        storage.create_identity_claim(
            "d1", "alice", "code123", datetime.now(UTC) + timedelta(minutes=10)
        )
        storage.get_identity_link("d1", "alice")


def test_connect_sets_busy_timeout(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    with storage._connect() as conn:
        row = conn.execute("PRAGMA busy_timeout;").fetchone()
        assert row is not None
        assert row[0] == 30000


def test_init_schema_enables_wal_mode_on_disk_db(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    with storage._connect() as conn:
        row = conn.execute("PRAGMA journal_mode;").fetchone()
        assert row is not None
        assert str(row[0]).lower() == "wal"


def test_wal_concurrent_reader_not_blocked_by_active_writer(tmp_path: Path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()

    with storage._connect() as conn1:
        conn1.execute("INSERT INTO identity_links (discord_user_id, github_user, created_at) VALUES ('u1', 'alice', '2026-01-01')")

    # While a writer has an open transaction, a reader should still be able to read in WAL mode
    with storage._connect() as writer_conn:
        writer_conn.execute("INSERT INTO identity_links (discord_user_id, github_user, created_at) VALUES ('u2', 'bob', '2026-01-02')")

        with storage._connect() as reader_conn:
            # WAL allows non-blocking read of the last committed snapshot
            row = reader_conn.execute("SELECT count(*) FROM identity_links").fetchone()
            assert row is not None
            assert row[0] == 1

