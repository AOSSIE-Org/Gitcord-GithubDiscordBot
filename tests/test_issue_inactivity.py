"""Tests for inactive issue check-in, reminder, and escalation lifecycle."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import IdentityMapping, NotificationConfig
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.inactivity import (
    build_checkin_message,
    build_mentor_alert_message,
    build_unassign_comment,
    build_unassign_dm_message,
    has_contributor_activity,
    run_issue_inactivity_lifecycle,
)


class MockStorage:
    """Mock storage for inactivity tests."""

    def __init__(self) -> None:
        self.verified_mappings: list[IdentityMapping] = []
        self.notifications_sent: set[str] = set()
        self.audit_events: list[dict] = []
        self.tracking: dict[tuple[str, int, str], dict] = {}

    def list_verified_identity_mappings(self) -> list[IdentityMapping]:
        return self.verified_mappings

    def was_notification_sent(self, dedupe_key: str) -> bool:
        return dedupe_key in self.notifications_sent

    def mark_notification_sent(self, dedupe_key: str, event: object, discord_user_id: str, channel_id: str | None, github_user: str) -> None:
        self.notifications_sent.add(dedupe_key)

    def append_audit_event(self, event: dict) -> None:
        self.audit_events.append(event)

    def track_issue_assignment(
        self,
        repo: str,
        issue_number: int,
        github_user: str,
        assigned_at: datetime,
        last_activity_at: datetime | None = None,
    ) -> None:
        key = (repo, issue_number, github_user)
        self.tracking[key] = {
            "repo": repo,
            "issue_number": issue_number,
            "github_user": github_user,
            "assigned_at": assigned_at.isoformat(),
            "last_activity_at": (last_activity_at or assigned_at).isoformat(),
            "status": "assigned",
            "reminder_sent_at": None,
            "escalated_at": None,
        }

    def update_issue_inactivity_activity(
        self,
        repo: str,
        issue_number: int,
        github_user: str,
        activity_at: datetime,
    ) -> None:
        key = (repo, issue_number, github_user)
        if key in self.tracking:
            self.tracking[key]["last_activity_at"] = activity_at.isoformat()
            self.tracking[key]["status"] = "assigned"
            self.tracking[key]["reminder_sent_at"] = None

    def record_issue_inactivity_reminder(
        self,
        repo: str,
        issue_number: int,
        github_user: str,
        reminder_sent_at: datetime,
    ) -> None:
        key = (repo, issue_number, github_user)
        if key in self.tracking:
            self.tracking[key]["reminder_sent_at"] = reminder_sent_at.isoformat()
            self.tracking[key]["status"] = "reminded"

    def record_issue_inactivity_escalation(
        self,
        repo: str,
        issue_number: int,
        github_user: str,
        escalated_at: datetime,
        status: str = "unassigned",
    ) -> None:
        key = (repo, issue_number, github_user)
        if key in self.tracking:
            self.tracking[key]["escalated_at"] = escalated_at.isoformat()
            self.tracking[key]["status"] = status

    def get_issue_inactivity_record(
        self,
        repo: str,
        issue_number: int,
        github_user: str,
    ) -> dict | None:
        return self.tracking.get((repo, issue_number, github_user))


class TestInactivityConfigValidation:
    """Test configuration validation for inactivity settings."""

    def test_default_config(self) -> None:
        cfg = NotificationConfig()
        assert cfg.issue_inactivity_reminders is False
        assert cfg.issue_inactivity_days == 7
        assert cfg.issue_inactivity_escalate_days == 7
        assert cfg.issue_inactivity_auto_unassign is True
        assert cfg.issue_inactivity_comment_on_unassign is True
        assert cfg.issue_inactivity_alert_channel_id is None

    def test_custom_valid_config(self) -> None:
        cfg = NotificationConfig(
            issue_inactivity_reminders=True,
            issue_inactivity_days=5,
            issue_inactivity_escalate_days=10,
            issue_inactivity_alert_channel_id="123456",
        )
        assert cfg.issue_inactivity_reminders is True
        assert cfg.issue_inactivity_days == 5
        assert cfg.issue_inactivity_escalate_days == 10
        assert cfg.issue_inactivity_alert_channel_id == "123456"

    def test_invalid_days_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="inactivity"):
            NotificationConfig(issue_inactivity_days=0)

        with pytest.raises(ValueError, match="inactivity"):
            NotificationConfig(issue_inactivity_escalate_days=-1)


class TestMessageFormatting:
    """Test generation of user-facing messages and comments."""

    def test_build_checkin_message(self) -> None:
        msg = build_checkin_message(
            github_org="AOSSIE-Org",
            repo="Gitcord",
            issue_number=42,
            issue_title="Refactor REST adapter error handling",
            github_user="alex",
            discord_user_id="987654321",
            days=7,
        )
        assert "**Gitcord Check-in: Issue #42**" in msg
        assert "**Repository:** AOSSIE-Org/Gitcord" in msg
        assert "**Issue:** Refactor REST adapter error handling" in msg
        assert "> Hey <@987654321>, you were assigned to this issue 7 days ago. Are you still actively working on it?" in msg

    def test_build_unassign_comment(self) -> None:
        comment = build_unassign_comment("alex", 14)
        assert "@alex has been unassigned from this issue due to 14 days of inactivity" in comment
        assert "This issue is now open for other contributors to claim." in comment

    def test_build_unassign_dm_message(self) -> None:
        dm = build_unassign_dm_message(
            github_org="AOSSIE-Org",
            repo="Gitcord",
            issue_number=42,
            issue_title="Refactor REST adapter error handling",
            github_user="alex",
            discord_user_id="987654321",
            total_days=14,
        )
        assert "**Gitcord Update: Issue #42**" in dm
        assert "> Hey <@987654321>, you were unassigned from this issue due to 14 days of inactivity" in dm

    def test_build_mentor_alert_message(self) -> None:
        alert = build_mentor_alert_message(
            github_org="AOSSIE-Org",
            repo="Gitcord",
            issue_number=42,
            issue_title="Refactor REST adapter error handling",
            github_user="alex",
            discord_user_id="987654321",
            total_days=14,
            action="unassigned",
        )
        assert "⚠️ **Inactive Issue Unassigned**" in alert
        assert "Issue **#42**" in alert
        assert "<@987654321> (`alex`)" in alert


class TestActivityDetection:
    """Test detection of contributor activity on GitHub."""

    def test_no_activity_returns_false(self) -> None:
        reader = MagicMock()
        reader.get_issue_comments.return_value = []
        reader.list_pull_requests_for_author.return_value = []
        now = datetime.now(UTC)
        since = now - timedelta(days=5)

        has_act, act_time = has_contributor_activity(
            github_reader=reader,
            owner="AOSSIE-Org",
            repo="Gitcord",
            issue_number=42,
            github_user="alex",
            since=since,
        )
        assert has_act is False
        assert act_time is None

    def test_recent_comment_by_assignee_counts_as_activity(self) -> None:
        reader = MagicMock()
        now = datetime.now(UTC)
        since = now - timedelta(days=5)
        comment_time = now - timedelta(days=2)
        reader.get_issue_comments.return_value = [
            {
                "user": {"login": "alex"},
                "created_at": comment_time.isoformat(),
                "body": "Working on a fix for this!",
            }
        ]
        reader.list_pull_requests_for_author.return_value = []

        has_act, act_time = has_contributor_activity(
            github_reader=reader,
            owner="AOSSIE-Org",
            repo="Gitcord",
            issue_number=42,
            github_user="alex",
            since=since,
        )
        assert has_act is True
        assert act_time == comment_time

    def test_other_user_comment_does_not_count(self) -> None:
        reader = MagicMock()
        now = datetime.now(UTC)
        since = now - timedelta(days=5)
        reader.get_issue_comments.return_value = [
            {
                "user": {"login": "mentor_bob"},
                "created_at": (now - timedelta(days=2)).isoformat(),
                "body": "Any updates?",
            }
        ]
        reader.list_pull_requests_for_author.return_value = []

        has_act, act_time = has_contributor_activity(
            github_reader=reader,
            owner="AOSSIE-Org",
            repo="Gitcord",
            issue_number=42,
            github_user="alex",
            since=since,
        )
        assert has_act is False
        assert act_time is None

    def test_recent_pr_by_assignee_counts_as_activity(self) -> None:
        reader = MagicMock()
        reader.get_issue_comments.return_value = []
        now = datetime.now(UTC)
        since = now - timedelta(days=5)
        pr_time = now - timedelta(days=1)
        reader.list_pull_requests_for_author.return_value = [
            {
                "number": 105,
                "title": "Fix #42 rest adapter handling",
                "created_at": pr_time.isoformat(),
            }
        ]

        has_act, act_time = has_contributor_activity(
            github_reader=reader,
            owner="AOSSIE-Org",
            repo="Gitcord",
            issue_number=42,
            github_user="alex",
            since=since,
        )
        assert has_act is True
        assert act_time == pr_time


class TestInactivityLifecycleExecution:
    """Test full 2-stage lifecycle execution."""

    @pytest.fixture
    def setup_env(self) -> tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]:
        github_reader = MagicMock()
        github_writer = MagicMock()
        discord_writer = MagicMock()
        storage = MockStorage()
        storage.verified_mappings = [
            IdentityMapping(discord_user_id="11223344", github_user="alex"),
        ]
        policy = MutationPolicy(
            mode=RunMode.ACTIVE,
            github_write_allowed=True,
            discord_write_allowed=True,
        )
        config = NotificationConfig(
            issue_inactivity_reminders=True,
            issue_inactivity_days=7,
            issue_inactivity_escalate_days=7,
            issue_inactivity_auto_unassign=True,
            issue_inactivity_alert_channel_id="999999",
        )
        return github_reader, github_writer, discord_writer, storage, policy, config

    def test_no_reminder_when_inactivity_under_7_days(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)
        assigned_at = now - timedelta(days=3)

        github_reader.list_open_issues.return_value = [
            {
                "repo": "Gitcord",
                "number": 42,
                "title": "Refactor error handling",
                "assignees": [{"login": "alex"}],
                "created_at": assigned_at.isoformat(),
            }
        ]
        github_reader.get_issue_comments.return_value = []
        github_reader.list_pull_requests_for_author.return_value = []

        run_issue_inactivity_lifecycle(
            github_reader=github_reader,
            github_writer=github_writer,
            discord_writer=discord_writer,
            storage=storage,
            policy=policy,
            config=config,
            github_org="AOSSIE-Org",
            now=now,
        )

        discord_writer.send_dm.assert_not_called()
        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec is not None
        assert rec["status"] == "assigned"
        assert rec["reminder_sent_at"] is None

    def test_stage_1_sends_checkin_dm_at_7_days(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)
        assigned_at = now - timedelta(days=7, hours=1)

        github_reader.list_open_issues.return_value = [
            {
                "repo": "Gitcord",
                "number": 42,
                "title": "Refactor error handling",
                "assignees": [{"login": "alex"}],
                "created_at": assigned_at.isoformat(),
            }
        ]
        github_reader.get_issue_comments.return_value = []
        github_reader.list_pull_requests_for_author.return_value = []
        discord_writer.send_dm.return_value = True

        run_issue_inactivity_lifecycle(
            github_reader=github_reader,
            github_writer=github_writer,
            discord_writer=discord_writer,
            storage=storage,
            policy=policy,
            config=config,
            github_org="AOSSIE-Org",
            now=now,
        )

        discord_writer.send_dm.assert_called_once()
        args = discord_writer.send_dm.call_args[0]
        assert args[0] == "11223344"
        assert "Gitcord Check-in: Issue #42" in args[1]
        assert "Hey <@11223344>, you were assigned to this issue 7 days ago." in args[1]

        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec is not None
        assert rec["status"] == "reminded"
        assert rec["reminder_sent_at"] is not None

    def test_stage_2_escalates_and_unassigns_at_14_days(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)
        assigned_at = now - timedelta(days=15)
        reminded_at = now - timedelta(days=7, hours=2)

        # Pre-seed reminder in storage
        storage.track_issue_assignment("Gitcord", 42, "alex", assigned_at)
        storage.record_issue_inactivity_reminder("Gitcord", 42, "alex", reminded_at)

        github_reader.list_open_issues.return_value = [
            {
                "repo": "Gitcord",
                "number": 42,
                "title": "Refactor error handling",
                "assignees": [{"login": "alex"}],
                "created_at": assigned_at.isoformat(),
            }
        ]
        github_reader.get_issue_comments.return_value = []
        github_reader.list_pull_requests_for_author.return_value = []
        discord_writer.send_dm.return_value = True
        discord_writer.send_message.return_value = True

        run_issue_inactivity_lifecycle(
            github_reader=github_reader,
            github_writer=github_writer,
            discord_writer=discord_writer,
            storage=storage,
            policy=policy,
            config=config,
            github_org="AOSSIE-Org",
            now=now,
        )

        # Unassigned on GitHub
        github_writer.unassign_issue.assert_called_once_with("AOSSIE-Org", "Gitcord", 42, "alex")

        # Comment on issue
        github_writer.create_issue_comment.assert_called_once()
        comment_body = github_writer.create_issue_comment.call_args[0][3]
        assert "@alex has been unassigned from this issue due to 14 days of inactivity" in comment_body

        # DM to contributor
        discord_writer.send_dm.assert_called_once()
        dm_args = discord_writer.send_dm.call_args[0]
        assert dm_args[0] == "11223344"
        assert "Gitcord Update: Issue #42" in dm_args[1]

        # Mentor alert channel message
        discord_writer.send_message.assert_called_once()
        alert_args = discord_writer.send_message.call_args[0]
        assert alert_args[0] == "999999"
        assert "⚠️ **Inactive Issue Unassigned**" in alert_args[1]

        # Storage updated
        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec is not None
        assert rec["status"] == "unassigned"
        assert rec["escalated_at"] is not None

    def test_dry_run_mode_does_not_mutate(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, _, config = setup_env
        now = datetime.now(UTC)
        assigned_at = now - timedelta(days=15)
        reminded_at = now - timedelta(days=8)

        dry_run_policy = MutationPolicy(
            mode=RunMode.DRY_RUN,
            github_write_allowed=False,
            discord_write_allowed=False,
        )

        storage.track_issue_assignment("Gitcord", 42, "alex", assigned_at)
        storage.record_issue_inactivity_reminder("Gitcord", 42, "alex", reminded_at)

        github_reader.list_open_issues.return_value = [
            {
                "repo": "Gitcord",
                "number": 42,
                "title": "Refactor error handling",
                "assignees": [{"login": "alex"}],
                "created_at": assigned_at.isoformat(),
            }
        ]
        github_reader.get_issue_comments.return_value = []
        github_reader.list_pull_requests_for_author.return_value = []

        run_issue_inactivity_lifecycle(
            github_reader=github_reader,
            github_writer=github_writer,
            discord_writer=discord_writer,
            storage=storage,
            policy=dry_run_policy,
            config=config,
            github_org="AOSSIE-Org",
            now=now,
        )

        github_writer.unassign_issue.assert_not_called()
        github_writer.create_issue_comment.assert_not_called()
        discord_writer.send_dm.assert_not_called()
        discord_writer.send_message.assert_not_called()

        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec is not None
        assert rec["status"] == "escalated"

    def test_minute_based_inactivity_lifecycle(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, _ = setup_env
        config = NotificationConfig(
            issue_inactivity_reminders=True,
            issue_inactivity_minutes=1,
            issue_inactivity_escalate_minutes=1,
            issue_inactivity_auto_unassign=True,
        )
        now = datetime.now(UTC)
        assigned_at = now - timedelta(seconds=65)

        github_reader.list_open_issues.return_value = [
            {
                "repo": "Gitcord",
                "number": 42,
                "title": "Refactor error handling",
                "assignees": [{"login": "alex"}],
                "created_at": assigned_at.isoformat(),
            }
        ]
        github_reader.get_issue_comments.return_value = []
        github_reader.list_pull_requests_for_author.return_value = []
        discord_writer.send_dm.return_value = True

        run_issue_inactivity_lifecycle(
            github_reader=github_reader,
            github_writer=github_writer,
            discord_writer=discord_writer,
            storage=storage,
            policy=policy,
            config=config,
            github_org="AOSSIE-Org",
            now=now,
        )

        discord_writer.send_dm.assert_called_once()
        args = discord_writer.send_dm.call_args[0]
        assert "1 minute ago" in args[1]


class TestSqliteStorageInactivityMethods:
    """Test SQLite storage methods for issue inactivity tracking."""

    @pytest.fixture
    def storage(self, tmp_path) -> SqliteStorage:
        st = SqliteStorage(str(tmp_path))
        st.init_schema()
        return st

    def test_track_and_get_record(self, storage: SqliteStorage) -> None:
        now = datetime.now(UTC)
        storage.track_issue_assignment("Gitcord", 42, "alex", now)
        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec is not None
        assert rec["repo"] == "Gitcord"
        assert rec["issue_number"] == 42
        assert rec["github_user"] == "alex"
        assert rec["status"] == "assigned"

    def test_update_activity_resets_reminder(self, storage: SqliteStorage) -> None:
        now = datetime.now(UTC)
        storage.track_issue_assignment("Gitcord", 42, "alex", now - timedelta(days=10))
        storage.record_issue_inactivity_reminder("Gitcord", 42, "alex", now - timedelta(days=2))

        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec["status"] == "reminded"
        assert rec["reminder_sent_at"] is not None

        # Contributor makes progress
        storage.update_issue_inactivity_activity("Gitcord", 42, "alex", now - timedelta(hours=1))
        rec_updated = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec_updated["status"] == "assigned"
        assert rec_updated["reminder_sent_at"] is None

    def test_list_active_trackers_and_close(self, storage: SqliteStorage) -> None:
        now = datetime.now(UTC)
        storage.track_issue_assignment("Gitcord", 42, "alex", now)
        storage.track_issue_assignment("Gitcord", 43, "bob", now)

        active = storage.list_active_issue_inactivity_trackers()
        assert len(active) == 2

        storage.close_issue_inactivity_tracking("Gitcord", 42, "alex")
        active_after = storage.list_active_issue_inactivity_trackers()
        assert len(active_after) == 1
        assert active_after[0]["github_user"] == "bob"
