"""Tests for audit export filtering and formatting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.engine.audit_export import (
    filter_audit_events,
    format_audit_csv,
    format_audit_markdown,
)


def test_filter_audit_events_by_user_github(tmp_path: Path) -> None:
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "456",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "bob"},
        },
    ]
    filtered = filter_audit_events(events, user="alice")
    assert len(filtered) == 1
    assert filtered[0]["context"]["github_user"] == "alice"


def test_filter_audit_events_by_user_discord(tmp_path: Path) -> None:
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "456",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "bob"},
        },
    ]
    filtered = filter_audit_events(events, user="123")
    assert len(filtered) == 1
    assert filtered[0]["actor_id"] == "123"


def test_filter_audit_events_by_event_type(tmp_path: Path) -> None:
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_claim_created",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {},
        },
    ]
    filtered = filter_audit_events(events, event_type="identity_verified")
    assert len(filtered) == 1
    assert filtered[0]["event_type"] == "identity_verified"


def test_filter_audit_events_by_time_range(tmp_path: Path) -> None:
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-15T00:00:00+00:00",
            "context": {},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-30T00:00:00+00:00",
            "context": {},
        },
    ]
    from_time = datetime(2026, 1, 10, tzinfo=timezone.utc)
    to_time = datetime(2026, 1, 20, tzinfo=timezone.utc)
    filtered = filter_audit_events(events, from_time=from_time, to_time=to_time)
    assert len(filtered) == 1
    assert filtered[0]["timestamp"] == "2026-01-15T00:00:00+00:00"


def test_filter_audit_events_combines_filters(tmp_path: Path) -> None:
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-15T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_claim_created",
            "timestamp": "2026-01-15T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "456",
            "event_type": "identity_verified",
            "timestamp": "2026-01-15T00:00:00+00:00",
            "context": {"github_user": "bob"},
        },
    ]
    filtered = filter_audit_events(events, user="alice", event_type="identity_verified")
    assert len(filtered) == 1
    assert filtered[0]["context"]["github_user"] == "alice"
    assert filtered[0]["event_type"] == "identity_verified"


def test_format_audit_csv_has_header(tmp_path: Path) -> None:
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
    ]
    csv_output = format_audit_csv(events)
    assert "ts,event_type,github_user,discord_user_id,repo,target,details" in csv_output
    assert "alice" in csv_output
    assert "123" in csv_output


def test_format_audit_csv_empty_events(tmp_path: Path) -> None:
    csv_output = format_audit_csv([])
    assert "ts,event_type,github_user,discord_user_id,repo,target,details" in csv_output
    lines = csv_output.strip().split("\n")
    assert len(lines) == 1  # Header only


def test_format_audit_markdown_groups_by_event_type(tmp_path: Path) -> None:
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_claim_created",
            "timestamp": "2026-01-02T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
    ]
    md_output = format_audit_markdown(events)
    assert "## identity_claim_created" in md_output
    assert "## identity_verified" in md_output
    assert "alice" in md_output


def test_format_audit_markdown_empty_events(tmp_path: Path) -> None:
    md_output = format_audit_markdown([])
    assert "No events found" in md_output


def test_storage_list_audit_events_reads_file(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    # Write some events directly
    import json
    audit_path = Path(tmp_path) / "audit_events.jsonl"
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
    ]
    with audit_path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    # Read via storage method
    read_events = storage.list_audit_events()
    assert len(read_events) == 1
    assert read_events[0]["event_type"] == "identity_verified"


def test_storage_list_audit_events_empty_when_no_file(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    events = storage.list_audit_events()
    assert events == []


def test_cli_export_audit_csv_no_filters(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ghdcbot.cli import main
    import sys
    import json
    # Create config and audit events
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
runtime:
  mode: dry-run
  log_level: INFO
  data_dir: "{tmp_path}"
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  org: x
  token: t
  api_base: https://api.github.com
  user_fallback: false
discord:
  guild_id: "1"
  token: t
scoring:
  period_days: 30
  weights: {{}}
role_mappings:
  - discord_role: Contributor
    min_score: 1
assignments:
  issue_assignees: []
  review_roles: []
"""
    )
    audit_path = Path(tmp_path) / "audit_events.jsonl"
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
    ]
    with audit_path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    original_argv = sys.argv
    try:
        sys.argv = ["ghdcbot", "--config", str(config_path), "export-audit", "--format", "csv"]
        main()
    finally:
        sys.argv = original_argv
    out, _ = capsys.readouterr()
    assert "ts,event_type,github_user" in out
    assert "alice" in out


def test_cli_export_audit_json_filtered_by_user(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ghdcbot.cli import main
    import sys
    import json
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
runtime:
  mode: dry-run
  log_level: INFO
  data_dir: "{tmp_path}"
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  org: x
  token: t
  api_base: https://api.github.com
  user_fallback: false
discord:
  guild_id: "1"
  token: t
scoring:
  period_days: 30
  weights: {{}}
role_mappings:
  - discord_role: Contributor
    min_score: 1
assignments:
  issue_assignees: []
  review_roles: []
"""
    )
    audit_path = Path(tmp_path) / "audit_events.jsonl"
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "456",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "bob"},
        },
    ]
    with audit_path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    original_argv = sys.argv
    try:
        sys.argv = ["ghdcbot", "--config", str(config_path), "export-audit", "--format", "json", "--user", "alice"]
        main()
    finally:
        sys.argv = original_argv
    out, _ = capsys.readouterr()
    import json as json_lib
    parsed = json_lib.loads(out)
    assert len(parsed) == 1
    assert parsed[0]["context"]["github_user"] == "alice"


def test_cli_export_audit_md_filtered_by_event_type(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ghdcbot.cli import main
    import sys
    import json
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
runtime:
  mode: dry-run
  log_level: INFO
  data_dir: "{tmp_path}"
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  org: x
  token: t
  api_base: https://api.github.com
  user_fallback: false
discord:
  guild_id: "1"
  token: t
scoring:
  period_days: 30
  weights: {{}}
role_mappings:
  - discord_role: Contributor
    min_score: 1
assignments:
  issue_assignees: []
  review_roles: []
"""
    )
    audit_path = Path(tmp_path) / "audit_events.jsonl"
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_claim_created",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
    ]
    with audit_path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    original_argv = sys.argv
    try:
        sys.argv = ["ghdcbot", "--config", str(config_path), "export-audit", "--format", "md", "--event-type", "identity_verified"]
        main()
    finally:
        sys.argv = original_argv
    out, _ = capsys.readouterr()
    assert "## identity_verified" in out
    assert "identity_claim_created" not in out


def test_cli_export_audit_date_range(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ghdcbot.cli import main
    import sys
    import json
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
runtime:
  mode: dry-run
  log_level: INFO
  data_dir: "{tmp_path}"
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  org: x
  token: t
  api_base: https://api.github.com
  user_fallback: false
discord:
  guild_id: "1"
  token: t
scoring:
  period_days: 30
  weights: {{}}
role_mappings:
  - discord_role: Contributor
    min_score: 1
assignments:
  issue_assignees: []
  review_roles: []
"""
    )
    audit_path = Path(tmp_path) / "audit_events.jsonl"
    events = [
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-15T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
        {
            "actor_type": "discord_user",
            "actor_id": "123",
            "event_type": "identity_verified",
            "timestamp": "2026-01-30T00:00:00+00:00",
            "context": {"github_user": "alice"},
        },
    ]
    with audit_path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    original_argv = sys.argv
    try:
        sys.argv = [
            "ghdcbot",
            "--config",
            str(config_path),
            "export-audit",
            "--format",
            "json",
            "--from",
            "2026-01-10T00:00:00+00:00",
            "--to",
            "2026-01-20T00:00:00+00:00",
        ]
        main()
    finally:
        sys.argv = original_argv
    out, _ = capsys.readouterr()
    import json as json_lib
    parsed = json_lib.loads(out)
    assert len(parsed) == 1
    assert parsed[0]["timestamp"] == "2026-01-15T00:00:00+00:00"


def test_cli_export_audit_invalid_date_format(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ghdcbot.cli import main
    import sys
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
runtime:
  mode: dry-run
  log_level: INFO
  data_dir: "{tmp_path}"
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  org: x
  token: t
  api_base: https://api.github.com
  user_fallback: false
discord:
  guild_id: "1"
  token: t
scoring:
  period_days: 30
  weights: {{}}
role_mappings:
  - discord_role: Contributor
    min_score: 1
assignments:
  issue_assignees: []
  review_roles: []
"""
    )
    original_argv = sys.argv
    try:
        sys.argv = ["ghdcbot", "--config", str(config_path), "export-audit", "--from", "invalid-date"]
        with pytest.raises(SystemExit):
            main()
    except SystemExit:
        pass
    finally:
        sys.argv = original_argv
    out, err = capsys.readouterr()
    assert "Invalid --from time format" in err or "Invalid --from time format" in out or "error" in err.lower()


def test_cli_export_audit_empty_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from ghdcbot.cli import main
    import sys
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
runtime:
  mode: dry-run
  log_level: INFO
  data_dir: "{tmp_path}"
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  org: x
  token: t
  api_base: https://api.github.com
  user_fallback: false
discord:
  guild_id: "1"
  token: t
scoring:
  period_days: 30
  weights: {{}}
role_mappings:
  - discord_role: Contributor
    min_score: 1
assignments:
  issue_assignees: []
  review_roles: []
"""
    )
    original_argv = sys.argv
    try:
        sys.argv = ["ghdcbot", "--config", str(config_path), "export-audit", "--format", "json", "--user", "nonexistent"]
        main()
    finally:
        sys.argv = original_argv
    out, _ = capsys.readouterr()
    import json as json_lib
    parsed = json_lib.loads(out)
    assert parsed == []
