from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import NotificationConfig
from ghdcbot.core.models import ContributionEvent
from ghdcbot.core.modes import MutationPolicy, RunMode
from ghdcbot.engine.notifications import send_pr_opened_channel_notification


class _FakeStorage:
    def __init__(self) -> None:
        self.verified_mappings: list[dict] = []
        self.notifications_sent: set[str] = set()
        self.audit_events: list[dict] = []
        self.issue_authors: dict[tuple[str, int], str] = {}

    def list_verified_identity_mappings(self) -> list[dict]:
        return self.verified_mappings

    def claim_notification_sent(self, *args: object, **kwargs: object) -> bool:
        dedupe_key = str(args[0] if args else kwargs.get("dedupe_key", ""))
        if dedupe_key in self.notifications_sent:
            return False
        self.notifications_sent.add(dedupe_key)
        return True

    def release_notification_claim(self, dedupe_key: str) -> None:
        self.notifications_sent.discard(dedupe_key)

    def append_audit_event(self, event: dict) -> None:
        self.audit_events.append(event)

    def get_issue_author(self, repo: str, issue_number: int) -> str | None:
        return self.issue_authors.get((repo, int(issue_number)))


class _FakeDiscordWriter:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, list[dict] | None]] = []

    def create_message(
        self, channel_id: str, content: str, embeds: list[dict] | None = None
    ) -> str:
        self.sent.append((channel_id, content, embeds))
        return "msg-1"


def _active_policy() -> MutationPolicy:
    return MutationPolicy(
        mode=RunMode.ACTIVE,
        github_write_allowed=True,
        discord_write_allowed=True,
    )


def _pr_event(linked_issue_numbers: list[int] | None = None) -> ContributionEvent:
    payload: dict = {"pr_number": 42, "title": "Fix authentication bug"}
    if linked_issue_numbers is not None:
        payload["linked_issue_numbers"] = linked_issue_numbers
    return ContributionEvent(
        github_user="alice",
        event_type="pr_opened",
        repo="Gitcord-GithubDiscordBot",
        created_at=datetime(2026, 11, 25, 12, 0, tzinfo=UTC),
        payload=payload,
    )


def test_pr_opened_linked_issues_disabled_by_default() -> None:
    storage = _FakeStorage()
    storage.verified_mappings = [
        {"discord_user_id": "111", "github_user": "alice"},
        {"discord_user_id": "222", "github_user": "bob"},
    ]
    storage.issue_authors[("Gitcord-GithubDiscordBot", 15)] = "bob"
    writer = _FakeDiscordWriter()

    config = NotificationConfig(enabled=True, pr_opened=True)
    assert config.pr_opened_show_linked_issues is False

    sent = send_pr_opened_channel_notification(
        _pr_event([15]),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
    )
    assert sent is True
    desc = writer.sent[0][2][0]["description"]  # type: ignore[index]
    assert "Solves Issue" not in desc


def test_pr_opened_single_linked_issue_verified_author() -> None:
    storage = _FakeStorage()
    storage.verified_mappings = [
        {"discord_user_id": "111", "github_user": "alice"},
        {"discord_user_id": "222", "github_user": "bob"},
    ]
    storage.issue_authors[("Gitcord-GithubDiscordBot", 15)] = "bob"
    writer = _FakeDiscordWriter()
    config = NotificationConfig(
        enabled=True, pr_opened=True, pr_opened_show_linked_issues=True
    )

    sent = send_pr_opened_channel_notification(
        _pr_event([15]),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
    )
    assert sent is True
    desc = writer.sent[0][2][0]["description"]  # type: ignore[index]
    assert (
        "Solves Issue [#15](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues/15) "
        "(opened by @bob (<@222>))"
    ) in desc
    assert "Created by @alice (<@111>)" in desc


def test_pr_opened_single_linked_issue_unverified_author() -> None:
    storage = _FakeStorage()
    storage.verified_mappings = [{"discord_user_id": "111", "github_user": "alice"}]
    storage.issue_authors[("Gitcord-GithubDiscordBot", 15)] = "bob"
    writer = _FakeDiscordWriter()
    config = NotificationConfig(
        enabled=True, pr_opened=True, pr_opened_show_linked_issues=True
    )

    sent = send_pr_opened_channel_notification(
        _pr_event([15]),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
    )
    assert sent is True
    desc = writer.sent[0][2][0]["description"]  # type: ignore[index]
    assert (
        "Solves Issue [#15](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues/15) "
        "(opened by @bob)"
    ) in desc
    assert "<@222>" not in desc


def test_pr_opened_linked_issue_cache_miss_graceful_fallback() -> None:
    storage = _FakeStorage()
    storage.verified_mappings = [{"discord_user_id": "111", "github_user": "alice"}]
    writer = _FakeDiscordWriter()
    config = NotificationConfig(
        enabled=True, pr_opened=True, pr_opened_show_linked_issues=True
    )

    sent = send_pr_opened_channel_notification(
        _pr_event([15]),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
    )
    assert sent is True
    desc = writer.sent[0][2][0]["description"]  # type: ignore[index]
    assert (
        "Solves Issue [#15](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues/15)"
        in desc
    )
    assert "opened by" not in desc


def test_pr_opened_multiple_linked_issues_capped_at_two() -> None:
    storage = _FakeStorage()
    storage.verified_mappings = [{"discord_user_id": "111", "github_user": "alice"}]
    storage.issue_authors[("Gitcord-GithubDiscordBot", 1)] = "user1"
    storage.issue_authors[("Gitcord-GithubDiscordBot", 2)] = "user2"
    storage.issue_authors[("Gitcord-GithubDiscordBot", 3)] = "user3"
    writer = _FakeDiscordWriter()
    config = NotificationConfig(
        enabled=True, pr_opened=True, pr_opened_show_linked_issues=True
    )

    sent = send_pr_opened_channel_notification(
        _pr_event([1, 2, 3]),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
    )
    assert sent is True
    desc = writer.sent[0][2][0]["description"]  # type: ignore[index]
    assert "Solves Issues:" in desc
    assert "[#1](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues/1) (opened by @user1)" in desc
    assert "[#2](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues/2) (opened by @user2)" in desc
    assert "#3" not in desc


def test_pr_opened_without_linked_issues_unchanged() -> None:
    storage = _FakeStorage()
    storage.verified_mappings = [{"discord_user_id": "111", "github_user": "alice"}]
    writer = _FakeDiscordWriter()
    config = NotificationConfig(
        enabled=True, pr_opened=True, pr_opened_show_linked_issues=True
    )

    sent = send_pr_opened_channel_notification(
        _pr_event(None),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
    )
    assert sent is True
    desc = writer.sent[0][2][0]["description"]  # type: ignore[index]
    assert "Solves" not in desc


def test_sqlite_storage_get_issue_author(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()

    assert storage.get_issue_author("repo-a", 10) is None

    # Fallback to contributions (issue_opened)
    storage.record_contributions(
        [
            ContributionEvent(
                github_user="bob",
                event_type="issue_opened",
                repo="repo-a",
                created_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
                payload={"issue_number": 10, "title": "Bug"},
            )
        ]
    )
    assert storage.get_issue_author("repo-a", 10) == "bob"
    assert storage.get_issue_author("repo-a", 100) is None

    # Prefer issue_channel_announcements when present
    storage.save_issue_channel_announcement(
        repo="repo-a",
        issue_number=11,
        channel_id="chan-1",
        message_id="msg-11",
        issue_title="Feature",
        author_github="carol",
        status="open",
    )
    assert storage.get_issue_author("repo-a", 11) == "carol"


def test_rest_adapter_pr_opened_extracts_same_repo_linked_issues() -> None:
    adapter = GitHubRestAdapter(
        token="fake-token", org="AOSSIE-Org", api_base="https://api.github.com"
    )
    since = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)
    adapter._paginate = MagicMock(  # type: ignore[method-assign]
        return_value=[
            [
                {
                    "number": 42,
                    "title": "Fix bug",
                    "body": "Fixes #15\nCloses #22\nResolves org/other-repo#99\nSee #500",
                    "user": {"login": "alice"},
                    "created_at": "2026-11-25T10:00:00Z",
                    "updated_at": "2026-11-25T10:00:00Z",
                    "merged_at": None,
                }
            ]
        ]
    )
    adapter._client = MagicMock()
    adapter._client.get.return_value = MagicMock(status_code=404)

    pr_events, _, pr_opened_count, _ = adapter._collect_pull_request_events(
        "AOSSIE-Org", "Gitcord-GithubDiscordBot", since
    )
    assert pr_opened_count == 1
    assert len(pr_events) == 1
    assert pr_events[0].event_type == "pr_opened"
    assert pr_events[0].payload["linked_issue_numbers"] == [15, 22]


def test_pr_opened_linked_issue_github_reader_fallback_on_cache_miss() -> None:
    storage = _FakeStorage()
    storage.verified_mappings = [
        {"discord_user_id": "111", "github_user": "alice"},
        {"discord_user_id": "222", "github_user": "bob"},
    ]
    writer = _FakeDiscordWriter()
    config = NotificationConfig(
        enabled=True, pr_opened=True, pr_opened_show_linked_issues=True
    )
    github_reader = MagicMock()
    github_reader.get_issue.return_value = {"number": 5, "user": {"login": "bob"}}

    sent = send_pr_opened_channel_notification(
        _pr_event([5]),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
        github_reader=github_reader,
    )
    assert sent is True
    github_reader.get_issue.assert_called_once_with(
        "AOSSIE-Org", "Gitcord-GithubDiscordBot", 5
    )
    desc = writer.sent[0][2][0]["description"]  # type: ignore[index]
    assert (
        "Solves Issue [#5](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues/5) "
        "(opened by @bob (<@222>))"
    ) in desc


def test_pr_opened_linked_issue_skips_github_reader_on_cache_hit() -> None:
    storage = _FakeStorage()
    storage.issue_authors[("Gitcord-GithubDiscordBot", 5)] = "bob"
    writer = _FakeDiscordWriter()
    config = NotificationConfig(
        enabled=True, pr_opened=True, pr_opened_show_linked_issues=True
    )
    github_reader = MagicMock()

    sent = send_pr_opened_channel_notification(
        _pr_event([5]),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
        github_reader=github_reader,
    )
    assert sent is True
    github_reader.get_issue.assert_not_called()


def test_pr_opened_linked_issue_github_reader_error_graceful_fallback() -> None:
    storage = _FakeStorage()
    writer = _FakeDiscordWriter()
    config = NotificationConfig(
        enabled=True, pr_opened=True, pr_opened_show_linked_issues=True
    )
    github_reader = MagicMock()
    github_reader.get_issue.side_effect = RuntimeError("rate limit")

    sent = send_pr_opened_channel_notification(
        _pr_event([5]),
        storage,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        _active_policy(),
        config,
        {"Gitcord-GithubDiscordBot": "chan-1"},
        "AOSSIE-Org",
        github_reader=github_reader,
    )
    assert sent is True
    desc = writer.sent[0][2][0]["description"]  # type: ignore[index]
    assert (
        "Solves Issue [#5](https://github.com/AOSSIE-Org/Gitcord-GithubDiscordBot/issues/5)"
        in desc
    )
    assert "opened by" not in desc
