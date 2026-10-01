"""Tests for inactive issue check-in, reminder, and escalation lifecycle."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import IdentityMapping, NotificationConfig
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.inactivity import (
    _lifecycle_lock,
    build_checkin_message,
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

    def mark_notification_sent(
        self,
        dedupe_key: str,
        event: object,
        discord_user_id: str,
        channel_id: str | None,
        github_user: str,
    ) -> None:
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
        existing = self.tracking.get(key)
        if existing and existing.get("status") in {"unassigned", "resolved"}:
            self.reset_issue_inactivity_tracking(
                repo=repo,
                issue_number=issue_number,
                github_user=github_user,
                assigned_at=assigned_at,
                last_activity_at=last_activity_at,
            )
            return

        if key not in self.tracking:
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

    def reset_issue_inactivity_tracking(
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

    def list_active_issue_inactivity_trackers(
        self,
        repo: str | None = None,
    ) -> list[dict]:
        results = [
            rec for rec in self.tracking.values()
            if rec.get("status") in {"assigned", "reminded"}
        ]
        if repo:
            results = [rec for rec in results if rec.get("repo") == repo]
        return results

    def close_issue_inactivity_tracking(
        self,
        repo: str,
        issue_number: int,
        github_user: str | None = None,
        status: str = "resolved",
    ) -> None:
        for (r, num, user), rec in self.tracking.items():
            if r == repo and num == issue_number and (github_user is None or user == github_user):
                rec["status"] = status


class TestInactivityConfigValidation:
    """Test configuration validation for inactivity settings."""

    def test_default_config(self) -> None:
        cfg = NotificationConfig()
        assert cfg.issue_inactivity_reminders is False
        assert cfg.issue_inactivity_days == 7
        assert cfg.issue_inactivity_escalate_days == 7
        assert cfg.issue_inactivity_auto_unassign is False
        assert cfg.issue_inactivity_comment_on_unassign is True

    def test_custom_valid_config(self) -> None:
        cfg = NotificationConfig(
            issue_inactivity_reminders=True,
            issue_inactivity_days=5,
            issue_inactivity_escalate_days=10,
            issue_inactivity_auto_unassign=True,
        )
        assert cfg.issue_inactivity_reminders is True
        assert cfg.issue_inactivity_days == 5
        assert cfg.issue_inactivity_escalate_days == 10
        assert cfg.issue_inactivity_auto_unassign is True

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
        assert "> Hey <@987654321>, you were assigned to this issue 7 days ago" in msg
        assert "**What counts as activity:**" in msg
        assert "• Commenting on the issue with a progress update" in msg
        assert "• Opening or linking a pull request referencing this issue" in msg

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

    def test_recent_pr_referencing_issue_counts_as_activity(self) -> None:
        reader = MagicMock()
        now = datetime.now(UTC)
        since = now - timedelta(days=5)
        pr_time = now - timedelta(days=1)
        reader.get_issue_comments.return_value = []
        reader.list_pull_requests_for_author.return_value = [
            {
                "number": 105,
                "title": "Fix bug (#42)",
                "body": "Closes #42",
                "created_at": pr_time.isoformat(),
                "updated_at": pr_time.isoformat(),
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

    def test_recent_pr_created_after_assignment_counts_as_activity(self) -> None:
        reader = MagicMock()
        now = datetime.now(UTC)
        since = now - timedelta(days=5)
        pr_time = now - timedelta(days=2)
        reader.get_issue_comments.return_value = []
        reader.list_pull_requests_for_author.return_value = [
            {
                "number": 106,
                "title": "Some progress work",
                "body": "WIP",
                "created_at": pr_time.isoformat(),
                "updated_at": pr_time.isoformat(),
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

    def test_comment_fetch_failure_returns_none(self) -> None:
        reader = MagicMock()
        reader.get_issue_comments.return_value = None
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
        assert has_act is None
        assert act_time is None

    def test_comment_exception_returns_none(self) -> None:
        reader = MagicMock()
        reader.get_issue_comments.side_effect = RuntimeError("rate limit exceeded")
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
        assert has_act is None
        assert act_time is None

    def test_pr_exception_returns_none(self) -> None:
        reader = MagicMock()
        reader.get_issue_comments.return_value = []
        reader.list_pull_requests_for_author.side_effect = RuntimeError("network error")
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
        assert has_act is None
        assert act_time is None


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

        storage.track_issue_assignment("Gitcord", 42, "alex", assigned_at)

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
        assert "What counts as activity:" in args[1]

        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec is not None
        assert rec["status"] == "reminded"
        assert rec["reminder_sent_at"] is not None

    def test_activity_resets_reminder_and_delays_stage_1(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)
        assigned_at = now - timedelta(days=10)
        recent_comment_time = now - timedelta(days=2)

        storage.track_issue_assignment("Gitcord", 42, "alex", assigned_at)

        github_reader.list_open_issues.return_value = [
            {
                "repo": "Gitcord",
                "number": 42,
                "title": "Refactor error handling",
                "assignees": [{"login": "alex"}],
                "created_at": assigned_at.isoformat(),
            }
        ]
        github_reader.get_issue_comments.return_value = [
            {
                "user": {"login": "alex"},
                "created_at": recent_comment_time.isoformat(),
                "body": "I am working on this!",
            }
        ]
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
        assert rec["last_activity_at"] == recent_comment_time.isoformat()
        assert rec["status"] == "assigned"

    def test_stage_2_escalates_and_unassigns_after_14_days(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)
        assigned_at = now - timedelta(days=15)
        reminded_at = now - timedelta(days=7, hours=2)

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
        github_writer.unassign_issue.return_value = True
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

        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec is not None
        assert rec["status"] == "reminded"
        assert rec["escalated_at"] is None
        assert len(storage.audit_events) == 1
        assert storage.audit_events[0]["action"] == "issue_inactivity_escalated_dry_run"

        # Repeated execution must not append duplicate audit events
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
        assert len(storage.audit_events) == 1

    def test_stage_1_dry_run_does_not_mark_sent_or_update_state(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, _, config = setup_env
        now = datetime.now(UTC)
        assigned_at = now - timedelta(days=8)

        dry_run_policy = MutationPolicy(
            mode=RunMode.DRY_RUN,
            github_write_allowed=False,
            discord_write_allowed=False,
        )

        storage.track_issue_assignment("Gitcord", 42, "alex", assigned_at)

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

        discord_writer.send_dm.assert_not_called()
        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec is not None
        assert rec["status"] == "assigned"
        assert rec["reminder_sent_at"] is None
        checkin_key = f"issue_inactivity_checkin:Gitcord:42:alex:{assigned_at.isoformat()}"
        assert not storage.was_notification_sent(checkin_key)

    def test_newly_discovered_issue_starts_tracking_without_dm(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)
        assigned_at = now - timedelta(days=10)

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
        assert rec["assigned_at"] == now.isoformat()

    def test_lifecycle_skips_when_lock_already_held(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        assert not _lifecycle_lock.locked()

        acquired = _lifecycle_lock.acquire(blocking=False)
        assert acquired is True
        try:
            run_issue_inactivity_lifecycle(
                github_reader=github_reader,
                github_writer=github_writer,
                discord_writer=discord_writer,
                storage=storage,
                policy=policy,
                config=config,
                github_org="AOSSIE-Org",
            )
            github_reader.list_open_issues.assert_not_called()
        finally:
            _lifecycle_lock.release()

        assert not _lifecycle_lock.locked()

    def test_lifecycle_releases_lock_on_exception(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        github_reader.list_open_issues.side_effect = RuntimeError("network failure")

        assert not _lifecycle_lock.locked()
        with pytest.raises(RuntimeError, match="network failure"):
            run_issue_inactivity_lifecycle(
                github_reader=github_reader,
                github_writer=github_writer,
                discord_writer=discord_writer,
                storage=storage,
                policy=policy,
                config=config,
                github_org="AOSSIE-Org",
            )
        assert not _lifecycle_lock.locked()

    def test_close_tracker_when_contributor_is_unassigned(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        """When contributor is no longer assigned on GitHub, active tracker is closed with 'unassigned'."""
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)

        # Pre-seed active tracking for alex on issue 42
        storage.track_issue_assignment("Gitcord", 42, "alex", now - timedelta(days=3))
        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec["status"] == "assigned"

        # GitHub returns issue 42, but assignee is now bob (alex was unassigned)
        github_reader.list_open_issues.return_value = [
            {
                "repo": "Gitcord",
                "number": 42,
                "title": "Refactor error handling",
                "assignees": [{"login": "bob"}],
                "created_at": (now - timedelta(days=3)).isoformat(),
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

        # Alex's tracker should be closed
        alex_rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert alex_rec["status"] == "unassigned"

        # Bob's tracker should be initialized
        bob_rec = storage.get_issue_inactivity_record("Gitcord", 42, "bob")
        assert bob_rec is not None
        assert bob_rec["status"] == "assigned"

    def test_close_tracker_when_issue_closes(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        """When issue closes on GitHub, active tracker is closed with 'resolved'."""
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)

        # Pre-seed active tracking for alex on issue 42
        storage.track_issue_assignment("Gitcord", 42, "alex", now - timedelta(days=3))
        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec["status"] == "assigned"

        # GitHub returns no open issues for Gitcord (issue 42 was closed)
        github_reader.list_open_issues.return_value = []
        github_reader.list_org_repo_names.return_value = ["Gitcord"]

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

        # Tracker should be marked resolved
        rec_after = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec_after["status"] == "resolved"

    def test_reassigned_contributor_resets_tracking_and_not_immediately_unassigned(
        self, setup_env: tuple[MagicMock, MagicMock, MagicMock, MockStorage, MutationPolicy, NotificationConfig]
    ) -> None:
        """A re-assigned contributor must have tracking reset fresh and not be immediately unassigned."""
        github_reader, github_writer, discord_writer, storage, policy, config = setup_env
        now = datetime.now(UTC)

        # Simulate alex was previously unassigned with old timestamps
        old_assigned = now - timedelta(days=20)
        old_reminded = now - timedelta(days=10)
        old_escalated = now - timedelta(days=3)
        storage.track_issue_assignment("Gitcord", 42, "alex", old_assigned)
        storage.record_issue_inactivity_reminder("Gitcord", 42, "alex", old_reminded)
        storage.record_issue_inactivity_escalation("Gitcord", 42, "alex", old_escalated, status="unassigned")

        rec = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec["status"] == "unassigned"
        assert rec["reminder_sent_at"] is not None

        # Maintainer re-assigns alex to issue 42 on GitHub
        github_reader.list_open_issues.return_value = [
            {
                "repo": "Gitcord",
                "number": 42,
                "title": "Refactor error handling",
                "assignees": [{"login": "alex"}],
                "created_at": old_assigned.isoformat(),
            }
        ]
        github_reader.get_issue_comments.return_value = []
        github_reader.list_pull_requests_for_author.return_value = []

        # Run sync on the day they are re-assigned
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

        # Must NOT be unassigned immediately or receive check-in DM immediately!
        github_writer.unassign_issue.assert_not_called()
        discord_writer.send_dm.assert_not_called()

        # Tracking must be reset to 'assigned' with clock starting at now
        rec_reassigned = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec_reassigned["status"] == "assigned"
        assert rec_reassigned["assigned_at"] == now.isoformat()
        assert rec_reassigned["last_activity_at"] == now.isoformat()
        assert rec_reassigned["reminder_sent_at"] is None
        assert rec_reassigned["escalated_at"] is None

        # 7 days later: Stage 1 reminder DM is sent
        day7 = now + timedelta(days=7, hours=1)
        discord_writer.send_dm.return_value = True

        run_issue_inactivity_lifecycle(
            github_reader=github_reader,
            github_writer=github_writer,
            discord_writer=discord_writer,
            storage=storage,
            policy=policy,
            config=config,
            github_org="AOSSIE-Org",
            now=day7,
        )

        discord_writer.send_dm.assert_called_once()
        rec_day7 = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec_day7["status"] == "reminded"
        assert rec_day7["reminder_sent_at"] is not None

        # 14 days later (7 days after DM): auto-unassign triggers
        day14 = day7 + timedelta(days=7, hours=1)
        github_writer.unassign_issue.return_value = True

        run_issue_inactivity_lifecycle(
            github_reader=github_reader,
            github_writer=github_writer,
            discord_writer=discord_writer,
            storage=storage,
            policy=policy,
            config=config,
            github_org="AOSSIE-Org",
            now=day14,
        )

        github_writer.unassign_issue.assert_called_once_with("AOSSIE-Org", "Gitcord", 42, "alex")
        rec_day14 = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec_day14["status"] == "unassigned"


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

    def test_sqlite_reassignment_resets_unassigned_record(self, storage: SqliteStorage) -> None:
        """When an unassigned contributor is tracked again, SQLite resets status and clears reminder."""
        old_time = datetime.now(UTC) - timedelta(days=20)
        storage.track_issue_assignment("Gitcord", 42, "alex", old_time)
        storage.record_issue_inactivity_reminder("Gitcord", 42, "alex", old_time + timedelta(days=7))
        storage.record_issue_inactivity_escalation("Gitcord", 42, "alex", old_time + timedelta(days=14), status="unassigned")

        rec_before = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec_before["status"] == "unassigned"
        assert rec_before["reminder_sent_at"] is not None
        assert rec_before["escalated_at"] is not None

        # Re-assign: track_issue_assignment should reset terminal status
        new_time = datetime.now(UTC)
        storage.track_issue_assignment("Gitcord", 42, "alex", new_time)

        rec_after = storage.get_issue_inactivity_record("Gitcord", 42, "alex")
        assert rec_after["status"] == "assigned"
        assert rec_after["reminder_sent_at"] is None
        assert rec_after["escalated_at"] is None
        assert rec_after["assigned_at"] == new_time.isoformat()
