"""Tests for /issue command engine helpers (issue_list)."""

from __future__ import annotations

from unittest.mock import MagicMock

from ghdcbot.engine.issue_list import (
    clamp_issue_limit,
    filter_open_issues,
    format_issue_description,
    format_issue_list_messages,
    format_single_issue_entry,
    resolve_repo_for_issue,
)


def test_clamp_issue_limit():
    assert clamp_issue_limit(None) == 10
    assert clamp_issue_limit(0) == 1
    assert clamp_issue_limit(-5) == 1
    assert clamp_issue_limit(25) == 25
    assert clamp_issue_limit(50) == 50
    assert clamp_issue_limit(100) == 50


def test_filter_open_issues_excludes_prs_and_closed():
    items = [
        {"number": 1, "title": "Issue 1", "state": "open"},
        {"number": 2, "title": "PR 2", "state": "open", "pull_request": {"url": "https://..."}},
        {"number": 3, "title": "Issue 3", "state": "closed"},
        {"number": 4, "title": "Issue 4", "state": "open"},
        {"number": 5, "title": "Issue 5", "state": "open"},
    ]
    filtered = filter_open_issues(items, limit=2)
    assert len(filtered) == 2
    assert [i["number"] for i in filtered] == [1, 4]


def test_format_issue_description():
    assert format_issue_description(None) == "> _No description provided._"
    assert format_issue_description("") == "> _No description provided._"
    assert format_issue_description("   \n\n  ") == "> _No description provided._"

    body_with_html_comment = "<!-- template header -->This is the actual issue content."
    assert format_issue_description(body_with_html_comment) == "> This is the actual issue content."

    long_body = "A" * 300
    formatted = format_issue_description(long_body, max_length=50)
    assert len(formatted) <= 55
    assert formatted.endswith("...")
    assert formatted.startswith("> ")


def test_format_single_issue_entry_with_author_and_labels():
    issue = {
        "number": 42,
        "title": "Add dark mode toggle",
        "html_url": "https://github.com/org/repo/issues/42",
        "state": "open",
        "user": {"login": "octocat"},
        "comments": 3,
        "labels": [{"name": "enhancement"}, {"name": "ui"}],
        "body": "Please add dark mode.",
    }
    lines = format_single_issue_entry(issue, org="org", repo="repo", storage=None)
    assert len(lines) == 3
    assert lines[0] == "• [#42](<https://github.com/org/repo/issues/42>) — **Add dark mode toggle**"
    assert "Status: `Open 🟢`" in lines[1]
    assert "Opened by: octocat" in lines[1]
    assert "💬 3 comments" in lines[1]
    assert "🏷️ enhancement, ui" in lines[1]
    assert lines[2] == "  > Please add dark mode."


def test_format_single_issue_entry_with_discord_link():
    mapping = MagicMock()
    mapping.github_user = "alice"
    mapping.discord_user_id = "123456789"
    storage = MagicMock()
    storage.list_verified_identity_mappings.return_value = [mapping]

    issue = {
        "number": 10,
        "title": "Fix bug",
        "html_url": "https://github.com/org/repo/issues/10",
        "state": "open",
        "user": {"login": "alice"},
        "comments": 1,
        "labels": [],
        "body": None,
    }
    lines = format_single_issue_entry(issue, org="org", repo="repo", storage=storage)
    assert "Opened by: <@123456789> (alice)" in lines[1]
    assert "💬 1 comment" in lines[1]
    assert "🏷️" not in lines[1]
    assert lines[2] == "  > _No description provided._"


def test_format_issue_list_messages_chunking():
    issues = [
        {
            "number": i,
            "title": f"Issue title {i} " + "X" * 100,
            "html_url": f"https://github.com/org/repo/issues/{i}",
            "state": "open",
            "user": {"login": f"user{i}"},
            "comments": i,
            "labels": [{"name": "bug"}],
            "body": "Body content " * 20,
        }
        for i in range(1, 20)
    ]
    messages = format_issue_list_messages(issues, org="org", repo="repo", limit=20)
    assert len(messages) > 1
    for msg in messages:
        assert len(msg) <= 1900


def test_resolve_repo_for_issue():
    config = MagicMock()
    config.github.repos.mode = "allow"
    config.github.repos.names = ["Knowledge-Agent"]
    config.discord.pr_open_channels = {"Knowledge-Agent": "11223344"}
    config.repo_contributor_roles = {}

    # Explicit repo allowed
    repo, err = resolve_repo_for_issue(config, repo="Knowledge-Agent")
    assert repo == "Knowledge-Agent"
    assert err is None

    # Explicit repo not allowed
    repo, err = resolve_repo_for_issue(config, repo="Unknown-Repo")
    assert repo is None
    assert "not allowed" in err

    # Auto-detect via channel ID
    repo, err = resolve_repo_for_issue(config, channel_id="11223344")
    assert repo == "Knowledge-Agent"
    assert err is None

    # Auto-detect via channel name
    repo, err = resolve_repo_for_issue(config, channel_name="knowledge-agent")
    assert repo == "Knowledge-Agent"
    assert err is None

    # Auto-detect via single repo
    repo, err = resolve_repo_for_issue(config)
    assert repo == "Knowledge-Agent"
    assert err is None


def test_github_rest_adapter_list_repo_open_issues():
    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    adapter = GitHubRestAdapter("fake-token", "fake-org", "https://api.github.com")
    fake_items = [
        {"number": 1, "title": "Real issue 1", "state": "open"},
        {"number": 2, "title": "Pull request 2", "state": "open", "pull_request": {"url": "..."}},
        {"number": 3, "title": "Real issue 3", "state": "open"},
    ]

    adapter._paginate = MagicMock(return_value=[fake_items])
    issues = adapter.list_repo_open_issues("fake-org", "fake-repo", per_page=10)

    assert len(issues) == 2
    assert [i["number"] for i in issues] == [1, 3]

