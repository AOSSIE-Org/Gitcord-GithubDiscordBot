from __future__ import annotations

import asyncio
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.bot import MentorClaimReviewView, run_bot
from ghdcbot.config.models import BotConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy
from ghdcbot.engine.notifications import send_issue_opened_channel_notification


class FakeGitHubAdapter:
    def __init__(self) -> None:
        self.assigned_issues: list[dict[str, Any]] = []
        self.issue_pool: dict[tuple[str, int], dict[str, Any]] = {}

    def get_issue(self, owner: str, repo: str, issue_number: int) -> dict[str, Any] | None:
        key = (repo, issue_number)
        if key in self.issue_pool:
            return self.issue_pool[key]
        return {
            "number": issue_number,
            "title": f"Issue #{issue_number}",
            "html_url": f"https://github.com/{owner}/{repo}/issues/{issue_number}",
            "state": "open",
            "assignees": [],
            "labels": [{"name": "good-first-issue"}],
            "created_at": "2026-10-01T12:00:00Z",
        }

    def assign_issue(self, owner: str, repo: str, issue_number: int, assignee: str) -> bool:
        self.assigned_issues.append({
            "owner": owner,
            "repo": repo,
            "issue_number": issue_number,
            "assignee": assignee,
        })
        return True

    def list_org_repo_names(self) -> list[str]:
        return ["repo-a", "repo-b"]


def _build_test_config(*, repo_names: list[str] | None = None) -> BotConfig:
    repos_cfg = {"mode": "allow", "names": repo_names} if repo_names is not None else None
    return BotConfig.model_validate({
        "runtime": {
            "mode": "active",
            "log_level": "INFO",
            "data_dir": "/tmp",
            "github_adapter": "ghdcbot.adapters.github.rest:GitHubRestAdapter",
            "discord_adapter": "ghdcbot.adapters.discord.api:DiscordApiAdapter",
            "storage_adapter": "ghdcbot.adapters.storage.sqlite:SqliteStorage",
        },
        "github": {
            "org": "test-org",
            "token": "fake-token",
            "api_base": "https://api.github.com",
            "permissions": {"write": True},
            "repos": repos_cfg,
        },
        "discord": {
            "guild_id": "123",
            "token": "fake-discord-token",
            "unrestricted_slash_commands": True,
        },
        "assignments": {"review_roles": [], "issue_assignees": ["Mentor"]},
        "identity_mappings": [],
    })


def _setup_bot_and_get_commands(
    config: BotConfig,
    fake_github: FakeGitHubAdapter,
    storage: SqliteStorage,
) -> dict[str, Any]:
    captured_trees: list[Any] = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured_trees.append(tree_self)
        orig_tree_init(tree_self, client)

    def fake_build_adapter(adapter_name: str, **kwargs: Any) -> Any:
        if "github" in adapter_name.lower():
            return fake_github
        if "storage" in adapter_name.lower():
            return storage
        return MagicMock()

    with (
        patch("ghdcbot.bot.load_config", return_value=config),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake_token"),
        patch("ghdcbot.bot.build_adapter", side_effect=fake_build_adapter),
        patch("ghdcbot.bot.GitHubIdentityReader"),
        patch("ghdcbot.bot.IdentityLinkService"),
        patch("ghdcbot.bot.SocialProfileService"),
        patch("discord.app_commands.CommandTree.__init__", mock_tree_init),
        patch("discord.Client.run", side_effect=SystemExit(0)),
    ):
        try:
            run_bot("dummy.yaml")
        except SystemExit:
            pass

    guild_id = int(config.discord.guild_id)
    tree = captured_trees[0]
    return {c.name: c for c in tree.get_commands(guild=discord.Object(id=guild_id))}


def _make_mock_interaction(user_id: int = 123, user_name: str = "alice") -> MagicMock:
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.name = user_name
    interaction.user.roles = [SimpleNamespace(name="Contributor", id=1)]
    interaction.user.guild_permissions = SimpleNamespace(administrator=False)
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.edit_original_response = AsyncMock()
    interaction.client = MagicMock()
    interaction.client.fetch_user = AsyncMock(return_value=MagicMock(send=AsyncMock()))
    interaction.channel = MagicMock()
    interaction.channel.send = AsyncMock()
    return interaction


@pytest.fixture
def test_env():
    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SqliteStorage(tmpdir)
        storage.init_schema()
        fake_github = FakeGitHubAdapter()
        config = _build_test_config(repo_names=["repo-a", "repo-b"])
        yield storage, fake_github, config


def test_claim_issue_unlinked_user_rejected(test_env):
    storage, fake_github, config = test_env
    commands = _setup_bot_and_get_commands(config, fake_github, storage)
    claim_cmd = commands["claim-issue"]

    interaction = _make_mock_interaction(user_id=999)
    asyncio.run(claim_cmd.callback(interaction, repo="repo-a", issue_number=10))

    interaction.response.send_message.assert_called_once()
    msg = interaction.response.send_message.call_args[0][0]
    assert "verify your github account" in str(msg).lower()


def test_claim_issue_success_and_duplicate_rejection(test_env):
    storage, fake_github, config = test_env

    # Link user 123 to alice
    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO identity_links (discord_user_id, github_user, verified, created_at, verified_at, github_user_normalized)
            VALUES ('123', 'alice', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 'alice')
            """
        )

    commands = _setup_bot_and_get_commands(config, fake_github, storage)
    claim_cmd = commands["claim-issue"]

    interaction = _make_mock_interaction(user_id=123)
    asyncio.run(claim_cmd.callback(interaction, repo="repo-a", issue_number=10))

    # Should confirm to contributor
    interaction.followup.send.assert_called_once()
    msg = interaction.followup.send.call_args[0][0]
    assert "submitted" in str(msg).lower()

    # Request is saved in storage
    pending = storage.list_pending_issue_requests(repo="repo-a")
    assert len(pending) == 1
    assert pending[0]["github_user"] == "alice"
    assert pending[0]["issue_number"] == 10

    # Second claim for same issue by alice should be rejected as duplicate
    interaction2 = _make_mock_interaction(user_id=123)
    asyncio.run(claim_cmd.callback(interaction2, repo="repo-a", issue_number=10))
    interaction2.response.send_message.assert_called_once()
    msg2 = interaction2.response.send_message.call_args[0][0]
    assert "already have a pending claim" in str(msg2).lower()


def test_claim_issue_closed_or_already_assigned_rejected(test_env):
    storage, fake_github, config = test_env

    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO identity_links (discord_user_id, github_user, verified, created_at, verified_at, github_user_normalized)
            VALUES ('123', 'alice', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 'alice')
            """
        )

    # 1. Closed issue
    fake_github.issue_pool[("repo-a", 20)] = {
        "number": 20,
        "state": "closed",
        "assignees": [],
    }

    commands = _setup_bot_and_get_commands(config, fake_github, storage)
    claim_cmd = commands["claim-issue"]

    interaction1 = _make_mock_interaction(user_id=123)
    asyncio.run(claim_cmd.callback(interaction1, repo="repo-a", issue_number=20))
    msg1 = interaction1.followup.send.call_args[0][0]
    assert "closed" in str(msg1).lower()

    # 2. Already assigned issue
    fake_github.issue_pool[("repo-a", 21)] = {
        "number": 21,
        "state": "open",
        "assignees": [{"login": "bob"}],
    }
    interaction2 = _make_mock_interaction(user_id=123)
    asyncio.run(claim_cmd.callback(interaction2, repo="repo-a", issue_number=21))
    msg2 = interaction2.followup.send.call_args[0][0]
    assert "already assigned" in str(msg2).lower()


def test_mentor_claim_review_view_approve(test_env):
    storage, fake_github, config = test_env

    storage.insert_issue_request(
        request_id="req-test-1",
        discord_user_id="123",
        github_user="alice",
        owner="test-org",
        repo="repo-a",
        issue_number=42,
        issue_url="https://github.com/test-org/repo-a/issues/42",
    )

    view = MentorClaimReviewView(
        request_id="req-test-1",
        storage=storage,
        github_adapter=fake_github,
        config=config,
        owner="test-org",
        repo="repo-a",
        issue_number=42,
        github_user="alice",
        discord_user_id="123",
    )

    mentor_interaction = _make_mock_interaction(user_id=777)
    asyncio.run(view.approve(mentor_interaction))

    # Check GitHub assignment was performed
    assert len(fake_github.assigned_issues) == 1
    assert fake_github.assigned_issues[0]["assignee"] == "alice"
    assert fake_github.assigned_issues[0]["issue_number"] == 42

    # Check storage updated to approved
    claim = storage.get_issue_request("req-test-1")
    assert claim["status"] == "approved"
    assert claim["resolved_by_discord_id"] == "777"

    # Check response edited
    mentor_interaction.edit_original_response.assert_called_once()
    content = mentor_interaction.edit_original_response.call_args[1]["content"]
    assert "Claim Approved" in content


def test_mentor_claim_review_view_decline(test_env):
    storage, fake_github, config = test_env

    storage.insert_issue_request(
        request_id="req-test-2",
        discord_user_id="123",
        github_user="alice",
        owner="test-org",
        repo="repo-a",
        issue_number=43,
        issue_url="https://github.com/test-org/repo-a/issues/43",
    )

    view = MentorClaimReviewView(
        request_id="req-test-2",
        storage=storage,
        github_adapter=fake_github,
        config=config,
        owner="test-org",
        repo="repo-a",
        issue_number=43,
        github_user="alice",
        discord_user_id="123",
    )

    mentor_interaction = _make_mock_interaction(user_id=777)
    asyncio.run(view.decline(mentor_interaction))

    # No GitHub assignment
    assert len(fake_github.assigned_issues) == 0

    # Storage updated to declined
    claim = storage.get_issue_request("req-test-2")
    assert claim["status"] == "declined"
    assert claim["resolved_by_discord_id"] == "777"

    mentor_interaction.edit_original_response.assert_called_once()
    content = mentor_interaction.edit_original_response.call_args[1]["content"]
    assert "Claim Declined" in content


def test_issue_requests_mentor_command(test_env):
    storage, fake_github, config = test_env

    storage.insert_issue_request(
        request_id="req-test-3",
        discord_user_id="123",
        github_user="alice",
        owner="test-org",
        repo="repo-a",
        issue_number=50,
        issue_url="https://github.com/test-org/repo-a/issues/50",
    )

    commands = _setup_bot_and_get_commands(config, fake_github, storage)
    requests_cmd = commands["issue-requests"]

    mentor_interaction = _make_mock_interaction(user_id=777)
    asyncio.run(requests_cmd.callback(mentor_interaction, repo="repo-a"))

    # Succeeded and sent pending requests
    assert mentor_interaction.followup.send.call_count >= 1
    first_call_args = mentor_interaction.followup.send.call_args_list[0]
    content = first_call_args[1].get("content") or first_call_args[0][0]
    assert "Found" in content and "pending" in content


def test_issue_opened_channel_notification_attaches_claim_button():
    event = ContributionEvent(
        github_user="author-dev",
        event_type="issue_opened",
        repo="repo-a",
        created_at="2026-10-01T12:00:00Z",
        payload={
            "issue_number": 105,
            "title": "Add dark mode toggle",
            "created_at": "2026-10-01T12:00:00Z",
        },
    )

    mock_storage = MagicMock()
    mock_storage.claim_notification_sent.return_value = True
    mock_storage.list_verified_identity_mappings.return_value = []

    mock_writer = MagicMock()
    mock_writer.create_message.return_value = "msg-123"

    from ghdcbot.config.models import NotificationConfig
    cfg = NotificationConfig(enabled=True)
    cfg.issue_opened = True

    from ghdcbot.core.modes import RunMode
    policy = MutationPolicy(mode=RunMode.ACTIVE, github_write_allowed=False, discord_write_allowed=True)

    sent = send_issue_opened_channel_notification(
        event=event,
        storage=mock_storage,
        discord_writer=mock_writer,
        policy=policy,
        config=cfg,
        pr_open_channels={"repo-a": "chan-999"},
        github_org="test-org",
    )

    assert sent is True
    mock_writer.create_message.assert_called_once()
    kwargs = mock_writer.create_message.call_args[1]
    assert "components" in kwargs
    components = kwargs["components"]
    assert len(components) == 1
    buttons = components[0]["components"]
    claim_btn = next(b for b in buttons if "Claim Issue" in b["label"])
    assert claim_btn["custom_id"] == "claim_issue:repo-a:105"


def test_mentor_claim_review_view_race_condition_already_resolved(test_env):
    storage, fake_github, config = test_env

    storage.insert_issue_request(
        request_id="req-race-1",
        discord_user_id="123",
        github_user="alice",
        owner="test-org",
        repo="repo-a",
        issue_number=99,
        issue_url="https://github.com/test-org/repo-a/issues/99",
    )

    # First mentor approves
    storage.update_issue_request_status(
        "req-race-1",
        "approved",
        resolved_by_discord_id="mentor-1",
        expected_status="pending",
    )

    # Second mentor views and tries to decline
    view = MentorClaimReviewView(
        request_id="req-race-1",
        storage=storage,
        github_adapter=fake_github,
        config=config,
        owner="test-org",
        repo="repo-a",
        issue_number=99,
        github_user="alice",
        discord_user_id="123",
    )

    mentor2_interaction = _make_mock_interaction(user_id=888)
    asyncio.run(view.decline(mentor2_interaction))

    # Should report already resolved and NOT send a decline DM or overwrite status
    claim = storage.get_issue_request("req-race-1")
    assert claim["status"] == "approved"
    assert claim["resolved_by_discord_id"] == "mentor-1"

    content = mentor2_interaction.edit_original_response.call_args[1]["content"]
    assert "already resolved" in content.lower()


def test_claim_submission_integrity_error_handled(test_env):
    storage, fake_github, config = test_env

    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO identity_links (discord_user_id, github_user, verified, created_at, verified_at, github_user_normalized)
            VALUES ('123', 'alice', 1, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', 'alice')
            """
        )

    # Mock insert_issue_request to raise sqlite3.IntegrityError simulating concurrent insert
    import sqlite3

    commands = _setup_bot_and_get_commands(config, fake_github, storage)
    claim_cmd = commands["claim-issue"]

    with patch.object(storage, "insert_issue_request", side_effect=sqlite3.IntegrityError("UNIQUE constraint failed")):
        interaction = _make_mock_interaction(user_id=123)
        asyncio.run(claim_cmd.callback(interaction, repo="repo-a", issue_number=10))

        assert interaction.followup.send.called
        msg = interaction.followup.send.call_args[0][0]
        assert "already have a pending claim" in msg


def test_mentor_claim_review_in_flight_race_decline_blocked_by_reservation(test_env):
    storage, fake_github, config = test_env

    storage.insert_issue_request(
        request_id="req-inflight-1",
        discord_user_id="123",
        github_user="alice",
        owner="test-org",
        repo="repo-a",
        issue_number=88,
        issue_url="https://github.com/test-org/repo-a/issues/88",
    )

    view_a = MentorClaimReviewView(
        request_id="req-inflight-1",
        storage=storage,
        github_adapter=fake_github,
        config=config,
        owner="test-org",
        repo="repo-a",
        issue_number=88,
        github_user="alice",
        discord_user_id="123",
    )
    view_b = MentorClaimReviewView(
        request_id="req-inflight-1",
        storage=storage,
        github_adapter=fake_github,
        config=config,
        owner="test-org",
        repo="repo-a",
        issue_number=88,
        github_user="alice",
        discord_user_id="123",
    )

    interaction_a = _make_mock_interaction(user_id=101)
    interaction_b = _make_mock_interaction(user_id=202)

    # Coordinate: pause Mentor A inside assign_issue, run Mentor B's decline, then resume A
    import threading

    paused_in_assign = threading.Event()
    resume_assign = threading.Event()

    original_assign = fake_github.assign_issue

    def paused_assign(owner, repo, issue_number, assignee):
        paused_in_assign.set()
        resume_assign.wait(timeout=5.0)
        return original_assign(owner, repo, issue_number, assignee)

    fake_github.assign_issue = paused_assign

    async def run_race():
        task_a = asyncio.create_task(view_a.approve(interaction_a))
        while not paused_in_assign.is_set():
            await asyncio.sleep(0.01)

        # At this point, A holds 'approving' reservation
        assert storage.get_issue_request("req-inflight-1")["status"] == "approving"

        # B attempts to decline while A is approving
        await view_b.decline(interaction_b)

        # Resume A
        resume_assign.set()
        await task_a

    asyncio.run(run_race())

    # Verify outcomes
    # 1. B was rejected because A held reservation
    content_b = interaction_b.edit_original_response.call_args[1]["content"]
    assert "currently being approved" in content_b

    # 2. A completed approval
    claim = storage.get_issue_request("req-inflight-1")
    assert claim["status"] == "approved"
    assert claim["resolved_by_discord_id"] == "101"

    # 3. GitHub was assigned exactly once to alice
    assert len(fake_github.assigned_issues) == 1
    assert fake_github.assigned_issues[0]["assignee"] == "alice"


def test_mentor_claim_review_view_approve_github_failure_reverts_to_pending(test_env):
    storage, fake_github, config = test_env

    storage.insert_issue_request(
        request_id="req-fail-1",
        discord_user_id="123",
        github_user="alice",
        owner="test-org",
        repo="repo-a",
        issue_number=77,
        issue_url="https://github.com/test-org/repo-a/issues/77",
    )

    view = MentorClaimReviewView(
        request_id="req-fail-1",
        storage=storage,
        github_adapter=fake_github,
        config=config,
        owner="test-org",
        repo="repo-a",
        issue_number=77,
        github_user="alice",
        discord_user_id="123",
    )

    # Force GitHub assign to return False (permission denied / failure)
    fake_github.assign_issue = MagicMock(return_value=False)

    interaction = _make_mock_interaction(user_id=777)
    asyncio.run(view.approve(interaction))

    # Reservation safely rolled back to pending for retry
    claim = storage.get_issue_request("req-fail-1")
    assert claim["status"] == "pending"

    content = interaction.edit_original_response.call_args[1]["content"]
    assert "returned to pending" in content.lower()


def test_mentor_claim_review_view_approve_github_uncertain_outcome(test_env):
    storage, fake_github, config = test_env

    storage.insert_issue_request(
        request_id="req-uncertain-1",
        discord_user_id="123",
        github_user="alice",
        owner="test-org",
        repo="repo-a",
        issue_number=66,
        issue_url="https://github.com/test-org/repo-a/issues/66",
    )

    view = MentorClaimReviewView(
        request_id="req-uncertain-1",
        storage=storage,
        github_adapter=fake_github,
        config=config,
        owner="test-org",
        repo="repo-a",
        issue_number=66,
        github_user="alice",
        discord_user_id="123",
    )

    # Step 3 refetch succeeds, then assign raises error and Step 4 verification also raises error (network blackout)
    fake_github.get_issue = MagicMock(
        side_effect=[
            {"number": 66, "state": "open", "assignees": []},
            RuntimeError("DNS resolution failed"),
        ]
    )
    fake_github.assign_issue = MagicMock(side_effect=RuntimeError("Connection timeout"))

    interaction = _make_mock_interaction(user_id=777)
    asyncio.run(view.approve(interaction))

    # Uncertain outcome does NOT blindly reset to pending
    claim = storage.get_issue_request("req-uncertain-1")
    assert claim["status"] == "approving"

    content = interaction.edit_original_response.call_args[1]["content"]
    assert "uncertain" in content.lower()


def test_mentor_claim_review_view_approve_assignee_case_insensitive_match(test_env):
    storage, fake_github, config = test_env

    storage.insert_issue_request(
        request_id="req-case-1",
        discord_user_id="123",
        github_user="alice",
        owner="test-org",
        repo="repo-a",
        issue_number=55,
        issue_url="https://github.com/test-org/repo-a/issues/55",
    )

    view = MentorClaimReviewView(
        request_id="req-case-1",
        storage=storage,
        github_adapter=fake_github,
        config=config,
        owner="test-org",
        repo="repo-a",
        issue_number=55,
        github_user="alice",
        discord_user_id="123",
    )

    # GitHub issue already has assignee with differing casing 'Alice'
    fake_github.get_issue = MagicMock(
        return_value={"number": 55, "state": "open", "assignees": [{"login": "Alice"}]}
    )

    interaction = _make_mock_interaction(user_id=777)
    asyncio.run(view.approve(interaction))

    # Should not be rejected as 'Already assigned to someone else'
    claim = storage.get_issue_request("req-case-1")
    assert claim["status"] == "approved"
    content = interaction.edit_original_response.call_args[1]["content"]
    assert "Claim Approved" in content

