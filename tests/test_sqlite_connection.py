"""SqliteStorage connections are closed after use so state.db is never left open."""

from __future__ import annotations

import sqlite3

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage


def _storage(tmp_path) -> SqliteStorage:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    return storage


def test_connection_is_closed_after_block(tmp_path) -> None:
    storage = _storage(tmp_path)
    with storage._connect() as conn:
        conn.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_connection_is_closed_and_rolled_back_on_error(tmp_path) -> None:
    storage = _storage(tmp_path)
    with pytest.raises(RuntimeError), storage._connect() as conn:
        conn.execute("CREATE TABLE scratch (x INTEGER)")
        conn.execute("INSERT INTO scratch VALUES (1)")
        raise RuntimeError("boom")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
    with storage._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM scratch").fetchone()[0] == 0


def test_writes_are_committed(tmp_path) -> None:
    storage = _storage(tmp_path)
    with storage._connect() as conn:
        conn.execute("CREATE TABLE scratch (x INTEGER)")
        conn.execute("INSERT INTO scratch VALUES (1)")
    with storage._connect() as conn:
        assert conn.execute("SELECT x FROM scratch").fetchone()[0] == 1


def test_db_file_can_be_removed_after_operations(tmp_path) -> None:
    """On Windows an open handle makes this unlink fail with PermissionError."""
    storage = _storage(tmp_path)
    storage.set_social_profile(
        discord_user_id="1",
        platform="x",
        profile_handle="user",
        display_value="https://x.com/user",
    )
    storage.get_all_social_profiles("1")
    (tmp_path / "state.db").unlink()
