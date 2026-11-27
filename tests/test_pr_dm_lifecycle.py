"""Tests for the live PR DM lifecycle card and post-merge cleanup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    NotificationConfig,
    PermissionConfig,
    RuntimeConfig,
)
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.core.models import ContributionEvent
from ghdcbot.engine.notifications import (
    _PR_DM_TIMELINE_RENDER_LIMIT,
    build_pr_dm_lifecycle_card,
    cleanup_expired_merged_pr_dms,
    send_notification_for_event,
)
from ghdcbot.engine.orchestrator import Orchestrator


class LiveDMWriter:
    """Minimal Discord writer that records card creates/edits/deletes."""

    def __init__(self, *, fail_edits: bool = False) -> None:
        self.fail_edits = fail_edits
        self.created: list[tuple[str, str, list[dict] | None]] = []
        self.edited: list[tuple[str, str, str, list[dict] | None]] = []
        self.plain_dms: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, str]] = []
        self._next_id = 100

    def create_dm_message(
        self, discord_user_id: str, content: str = "", *, embeds: list[dict] | None = None
    ) -> tuple[str, str]:
        self.created.append((discord_user_id, content, embeds))
        self._next_id += 1
        return ("dm-channel", f"msg-{self._next_id}")

    def edit_message(
        self,
        channel_id: str,
        message_id: str,
        content: str,
        *,
        embeds: list[dict] | None = None,
    ) -> bool:
        if self.fail_edits:
            return False
        self.edited.append((channel_id, message_id, content, embeds))
        return True

    def send_dm(self, discord_user_id: str, content: str) -> bool:
        self.plain_dms.append((discord_user_id, content))
        return True

    def delete_message(self, channel_id: str, message_id: str) -> bool:
        self.deleted.append((channel_id, message_id))
        return True


def _active_policy() -> MutationPolicy:
    return MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=True,
        discord_write_allowed=True,
    )


def _storage_with_verified_user(tmp_path) -> SqliteStorage:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    storage.create_identity_claim(
        "discord-1",
        "alice",
        "code",
        datetime.now(UTC) + timedelta(minutes=10),
    )
    storage.mark_identity_verified("discord-1", "alice")
    return storage


def _review_event(state: str, when: datetime) -> ContributionEvent:
    return ContributionEvent(
        github_user="maint",
        event_type="pr_reviewed",
        repo="Gitcord",
        created_at=when,
        payload={
            "pr_number": 7,
            "title": "Fix [bug]",
            "state": state,
            "pr_author": "alice",
        },
    )


def _merged_event(when: datetime) -> ContributionEvent:
    return ContributionEvent(
        github_user="alice",
        event_type="pr_merged",
        repo="Gitcord",
        created_at=when,
        payload={
            "pr_number": 7,
            "title": "Fix [bug]",
            "pr_author": "alice",
            "merged_by": "maint",
            "base_branch": "main",
            "merged_at": when.isoformat(),
        },
    )


def test_pr_dm_card_shows_status_breadcrumbs_and_timeline() -> None:
    entries = [
        {
            "status": "changes_requested",
            "badge": "⚠️",
            "label": "Changes Requested",
            "actor": "maint",
            "detail": "",
            "timestamp": 1_700_000_000,
        },
        {
            "status": "approved",
            "badge": "✅",
            "label": "PR Approved",
            "actor": "maint",
            "detail": "",
            "timestamp": 1_700_000_100,
        },
    ]

    content, embeds = build_pr_dm_lifecycle_card(
        repo="Gitcord",
        pr_number=7,
        pr_title="Fix [bug]",
        github_org="AOSSIE",
        status="approved",
        timeline_entries=entries,
        author_github="alice",
    )

    assert content == ""
    embed = embeds[0]
    assert "✅ Approved" in embed["title"]
    assert "PR #7" in embed["title"]
    assert "Fix [bug]" in embed["title"]  # embed titles are not markdown links
    assert "**[✅ Approved]**" in embed["description"]
    assert "⚠️ **Changes Requested**" in embed["description"]
    assert "✅ **PR Approved**" in embed["description"]
    assert embed["color"] == 0x2DA44E


def test_pr_dm_card_limits_rendered_timeline() -> None:
    entries = [
        {
            "badge": "⚪",
            "label": f"Update {index}",
            "actor": "maint",
            "timestamp": 1_700_000_000 + index,
        }
        for index in range(_PR_DM_TIMELINE_RENDER_LIMIT + 4)
    ]

    _, embeds = build_pr_dm_lifecycle_card(
        repo="Gitcord",
        pr_number=7,
        pr_title="Fix bug",
        github_org="AOSSIE",
        status="in_review",
        timeline_entries=entries,
    )

    description = embeds[0]["description"]
    assert "… 4 earlier updates" in description
    assert "Update 4" in description  # oldest rendered (only 4 hidden)
    assert "Update 3" not in description


def test_pr_dm_created_then_edited_in_place(tmp_path) -> None:
    storage = _storage_with_verified_user(tmp_path)
    writer = LiveDMWriter()
    config = NotificationConfig(enabled=True, pr_review_result=True, pr_merged=True)
    policy = _active_policy()

    changes_at = datetime.now(UTC) - timedelta(minutes=30)
    assert send_notification_for_event(
        _review_event("CHANGES_REQUESTED", changes_at),
        storage,
        writer,
        policy,
        config,
        "AOSSIE",
    )
    assert len(writer.created) == 1
    assert writer.plain_dms == []
    record = storage.get_pr_dm_message("Gitcord", 7, "discord-1")
    assert record is not None
    assert record["status"] == "changes_requested"
    assert record["message_id"] == "msg-101"

    approved_at = changes_at + timedelta(minutes=10)
    assert send_notification_for_event(
        _review_event("APPROVED", approved_at),
        storage,
        writer,
        policy,
        config,
        "AOSSIE",
    )
    assert len(writer.created) == 1
    assert len(writer.edited) == 1
    channel_id, message_id, content, embeds = writer.edited[0]
    assert (channel_id, message_id) == ("dm-channel", "msg-101")
    assert content == ""
    assert "✅ Approved" in embeds[0]["title"]
    assert "Changes Requested" in embeds[0]["description"]

    merged_at = approved_at + timedelta(minutes=5)
    assert send_notification_for_event(
        _merged_event(merged_at),
        storage,
        writer,
        policy,
        config,
        "AOSSIE",
    )
    assert len(writer.created) == 1
    assert len(writer.edited) == 2
    merged_embed = writer.edited[-1][3][0]
    assert "🟣 Merged" in merged_embed["title"]
    assert "🟣 **PR Merged** by `maint` (main)" in merged_embed["description"]
    record = storage.get_pr_dm_message("Gitcord", 7, "discord-1")
    assert record is not None
    assert record["status"] == "merged"
    assert record["merged_at"] is not None


def test_pr_dm_edit_failure_posts_a_new_card(tmp_path) -> None:
    storage = _storage_with_verified_user(tmp_path)
    writer = LiveDMWriter(fail_edits=True)
    config = NotificationConfig(enabled=True, pr_review_result=True)
    policy = _active_policy()
    when = datetime.now(UTC) - timedelta(minutes=5)

    send_notification_for_event(
        _review_event("CHANGES_REQUESTED", when),
        storage,
        writer,
        policy,
        config,
        "AOSSIE",
    )
    send_notification_for_event(
        _review_event("APPROVED", when + timedelta(minutes=1)),
        storage,
        writer,
        policy,
        config,
        "AOSSIE",
    )

    assert len(writer.created) == 2
    record = storage.get_pr_dm_message("Gitcord", 7, "discord-1")
    assert record is not None
    assert record["message_id"] == "msg-102"
    assert record["status"] == "approved"


def test_cleanup_deletes_expired_merged_dm(tmp_path) -> None:
    storage = _storage_with_verified_user(tmp_path)
    writer = LiveDMWriter()
    policy = _active_policy()
    merged_at = datetime.now(UTC) - timedelta(days=2)
    config = NotificationConfig(enabled=True, pr_merged=True)

    send_notification_for_event(
        _merged_event(merged_at),
        storage,
        writer,
        policy,
        config,
        "AOSSIE",
    )
    assert storage.get_pr_dm_message("Gitcord", 7, "discord-1") is not None

    cleaned = cleanup_expired_merged_pr_dms(
        storage, writer, policy, older_than_seconds=86400
    )

    assert cleaned == 1
    assert writer.deleted == [("dm-channel", "msg-101")]
    assert storage.get_pr_dm_message("Gitcord", 7, "discord-1") is None


def test_cleanup_keeps_recent_merged_dm(tmp_path) -> None:
    storage = _storage_with_verified_user(tmp_path)
    writer = LiveDMWriter()
    policy = _active_policy()
    config = NotificationConfig(enabled=True, pr_merged=True)

    send_notification_for_event(
        _merged_event(datetime.now(UTC)),
        storage,
        writer,
        policy,
        config,
        "AOSSIE",
    )

    assert cleanup_expired_merged_pr_dms(storage, writer, policy, 86400) == 0
    assert writer.deleted == []
    assert storage.get_pr_dm_message("Gitcord", 7, "discord-1") is not None


def test_cleanup_dry_run_keeps_message_and_row(tmp_path) -> None:
    storage = _storage_with_verified_user(tmp_path)
    writer = LiveDMWriter()
    dry_run = MutationPolicy(
        mode=RunMode.DRY_RUN,
        github_write_allowed=True,
        discord_write_allowed=True,
    )
    merged_at = datetime.now(UTC) - timedelta(days=2)
    config = NotificationConfig(enabled=True, pr_merged=True)

    send_notification_for_event(
        _merged_event(merged_at),
        storage,
        writer,
        _active_policy(),
        config,
        "AOSSIE",
    )

    assert cleanup_expired_merged_pr_dms(storage, writer, dry_run, 86400) == 0
    assert writer.deleted == []
    assert storage.get_pr_dm_message("Gitcord", 7, "discord-1") is not None
    audit = storage.list_audit_events()
    assert any(
        event["event_type"] == "pr_dm_cleanup"
        and event["context"]["dry_run"] is True
        for event in audit
    )


def test_cleanup_flag_defaults_off_and_accepts_uppercase_alias() -> None:
    assert NotificationConfig().enable_merged_dm_cleanup is False
    assert NotificationConfig().merged_dm_cleanup_hours == 24

    config = NotificationConfig.model_validate(
        {
            "ENABLE_MERGED_DM_CLEANUP": "yes",
            "MERGED_DM_CLEANUP_HOURS": 48,
        }
    )
    assert config.enable_merged_dm_cleanup is True
    assert config.merged_dm_cleanup_hours == 48


def test_cleanup_hours_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        NotificationConfig(merged_dm_cleanup_hours=0)


def test_cleanup_flag_can_be_enabled_from_env(monkeypatch) -> None:
    monkeypatch.setenv("ENABLE_MERGED_DM_CLEANUP", "true")
    monkeypatch.setenv("MERGED_DM_CLEANUP_HOURS", "12")

    config = NotificationConfig()

    assert config.enable_merged_dm_cleanup is True
    assert config.merged_dm_cleanup_hours == 12


def test_storage_pr_dm_record_upsert_and_list(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    storage.save_pr_dm_message(
        repo="Gitcord",
        pr_number=7,
        discord_user_id="discord-1",
        message_id="msg-1",
        channel_id="dm-channel",
        status="approved",
        pr_title="Fix bug",
        author_github="alice",
        timeline_json='[{"label": "PR Approved"}]',
    )
    storage.save_pr_dm_message(
        repo="Gitcord",
        pr_number=7,
        discord_user_id="discord-1",
        message_id="msg-1",
        channel_id="dm-channel",
        status="merged",
        pr_title=None,
        author_github=None,
        timeline_json='[{"label": "PR Approved"}, {"label": "PR Merged"}]',
        merged_at="2020-01-01T00:00:00Z",
    )

    record = storage.get_pr_dm_message("Gitcord", 7, "discord-1")
    assert record is not None
    assert record["status"] == "merged"
    assert record["pr_title"] == "Fix bug"  # COALESCE keeps earlier title
    assert len(storage.list_pr_dm_messages("discord-1")) == 1
    assert len(storage.list_expired_merged_pr_dm_messages(86400)) == 1

    assert storage.delete_pr_dm_message("Gitcord", 7, "discord-1") is True
    assert storage.get_pr_dm_message("Gitcord", 7, "discord-1") is None


class _EmptyGitHubReader:
    sync_repos_processed = 0
    sync_request_count = 0

    def peek_repos_for_sync(self) -> int:
        return 0

    def list_contributions(self, since: datetime) -> list:
        return []

    def list_open_issues(self) -> list:
        return []

    def list_open_pull_requests(self) -> list:
        return []

    def close(self) -> None:
        return None


def _bot_config(tmp_path, *, enable_cleanup: bool) -> BotConfig:
    return BotConfig(
        runtime=RuntimeConfig(
            mode="active",
            data_dir=str(tmp_path),
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
        ),
        github=GitHubConfig(org="AOSSIE", token="fake"),
        discord=DiscordConfig(
            guild_id="1",
            token="fake",
            permissions=PermissionConfig(write=True),
            notifications=NotificationConfig(
                enabled=True,
                enable_merged_dm_cleanup=enable_cleanup,
                merged_dm_cleanup_hours=24,
            ),
        ),
        assignments=AssignmentConfig(),
    )


def test_orchestrator_runs_cleanup_only_when_flag_enabled(tmp_path, monkeypatch) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    calls: list[dict] = []

    def fake_cleanup(*args, **kwargs) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(
        "ghdcbot.engine.orchestrator.cleanup_expired_merged_pr_dms", fake_cleanup
    )
    monkeypatch.setattr(
        "ghdcbot.engine.orchestrator._send_notifications_for_new_events",
        lambda *args, **kwargs: None,
    )

    def run(config: BotConfig) -> None:
        monkeypatch.setattr("ghdcbot.config.loader._ACTIVE_CONFIG", config)
        Orchestrator(
            github_reader=_EmptyGitHubReader(),
            github_writer=MagicMock(),
            discord_reader=SimpleNamespace(
                list_member_roles=lambda: {}, close=lambda: None
            ),
            discord_writer=MagicMock(),
            storage=storage,
            config=config,
        ).run_once()

    run(_bot_config(tmp_path, enable_cleanup=False))
    assert calls == []

    run(_bot_config(tmp_path, enable_cleanup=True))
    assert calls == [{"older_than_seconds": 24 * 3600}]
