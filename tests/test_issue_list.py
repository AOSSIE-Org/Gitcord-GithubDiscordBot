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

    fake_items = [
        {"number": 1, "title": "Real issue 1", "state": "open"},
        {"number": 2, "title": "Pull request 2", "state": "open", "pull_request": {"url": "..."}},
        {"number": 3, "title": "Real issue 3", "state": "open"},
    ]

    with GitHubRestAdapter("fake-token", "fake-org", "https://api.github.com") as adapter:
        adapter._paginate = MagicMock(return_value=[fake_items])
        issues = adapter.list_repo_open_issues("fake-org", "fake-repo", limit=10)

        assert len(issues) == 2
        assert [i["number"] for i in issues] == [1, 3]
        adapter._paginate.assert_called_once_with(
            "/repos/fake-org/fake-repo/issues",
            params={"state": "open", "sort": "created", "direction": "desc", "per_page": 100},
        )

        # Verify per_page specifies GitHub API page size
        adapter._paginate.reset_mock()
        adapter._paginate.return_value = [fake_items]
        adapter.list_repo_open_issues("fake-org", "fake-repo", limit=10, per_page=25)
        adapter._paginate.assert_called_once_with(
            "/repos/fake-org/fake-repo/issues",
            params={"state": "open", "sort": "created", "direction": "desc", "per_page": 25},
        )

        # Verify per_page > 100 is clamped and does not exceed limit
        adapter._paginate.reset_mock()
        adapter._paginate.return_value = [fake_items]
        limited = adapter.list_repo_open_issues("fake-org", "fake-repo", limit=1, per_page=200)
        assert len(limited) == 1
        assert limited[0]["number"] == 1
        adapter._paginate.assert_called_once_with(
            "/repos/fake-org/fake-repo/issues",
            params={"state": "open", "sort": "created", "direction": "desc", "per_page": 100},
        )

        # Verify non-positive limit returns empty list without making API calls
        adapter._paginate.reset_mock()
        assert adapter.list_repo_open_issues("fake-org", "fake-repo", limit=0) == []
        adapter._paginate.assert_not_called()

        # Verify mock returning None or yielding None propagates None
        adapter._paginate = MagicMock(return_value=None)
        assert adapter.list_repo_open_issues("fake-org", "fake-repo") is None
        adapter._paginate = MagicMock(return_value=[None])
        assert adapter.list_repo_open_issues("fake-org", "fake-repo") is None

    assert adapter._client.is_closed

    # Verify request failure (None from _request) propagates None
    with GitHubRestAdapter("fake-token", "fake-org", "https://api.github.com") as real_paginate_adapter:
        real_paginate_adapter._request = MagicMock(return_value=None)
        assert real_paginate_adapter.list_repo_open_issues("fake-org", "nonexistent-repo") is None

        # Verify non-200 response (e.g. 500 server error) propagates None
        error_response = MagicMock(status_code=500)
        real_paginate_adapter._request = MagicMock(return_value=error_response)
        assert real_paginate_adapter.list_repo_open_issues("fake-org", "server-error-repo") is None

        # Verify 200 OK with empty issues list returns empty list [] (not None)
        ok_empty_response = MagicMock(status_code=200)
        ok_empty_response.json.return_value = []
        ok_empty_response.headers = {}
        real_paginate_adapter._request = MagicMock(return_value=ok_empty_response)
        assert real_paginate_adapter.list_repo_open_issues("fake-org", "empty-repo") == []

    assert real_paginate_adapter._client.is_closed


def test_issue_handling_fetch_error_vs_empty():
    import asyncio
    from unittest.mock import AsyncMock

    async def _handle(raw_issues: list[dict] | None) -> list[dict]:
        followup = AsyncMock()
        if raw_issues is None:
            await followup.send(
                "❌ Error fetching issues. Please try again later.",
                ephemeral=True,
            )
        else:
            issues = filter_open_issues(raw_issues, limit=10)
            messages = format_issue_list_messages(
                issues=issues,
                org="fake-org",
                repo="fake-repo",
                limit=10,
            )
            for message in messages:
                await followup.send(
                    message,
                    ephemeral=True,
                    suppress_embeds=True,
                )
        return followup.send.call_args_list

    # Error case (raw_issues is None)
    calls_err = asyncio.run(_handle(None))
    assert len(calls_err) == 1
    assert "❌ Error fetching issues. Please try again later." in calls_err[0].args[0]

    # Empty case (raw_issues is [])
    calls_empty = asyncio.run(_handle([]))
    assert len(calls_empty) == 1
    assert "No open issues found in **fake-repo**." in calls_empty[0].args[0]



