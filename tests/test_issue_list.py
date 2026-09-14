"""Tests for /issue command engine helpers (issue_list)."""

from __future__ import annotations

from unittest.mock import MagicMock

from ghdcbot.engine.issue_list import (
    clamp_issue_limit,
    filter_open_issues,
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
    assert len(lines) == 2
    assert lines[0] == "• [#42](<https://github.com/org/repo/issues/42>) — **Add dark mode toggle**"
    assert "Status: `Open 🟢`" in lines[1]
    assert "Opened by: octocat" in lines[1]
    assert "💬 3 comments" in lines[1]
    assert "🏷️ enhancement, ui" in lines[1]


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
    assert len(lines) == 2
    assert "Opened by: <@123456789> (alice)" in lines[1]
    assert "💬 1 comment" in lines[1]
    assert "🏷️" not in lines[1]


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

    # Multi-repo config
    config_multi = MagicMock()
    config_multi.github.repos.mode = "allow"
    config_multi.github.repos.names = ["Knowledge-Agent", "Devr.AI"]
    config_multi.discord.pr_open_channels = {"Knowledge-Agent": "11223344"}
    config_multi.repo_contributor_roles = {}

    # Multi-repo: explicit allowed repo chosen
    repo, err = resolve_repo_for_issue(config_multi, repo="Devr.AI")
    assert repo == "Devr.AI"
    assert err is None

    # Multi-repo: explicit repo with whitespace
    repo, err = resolve_repo_for_issue(config_multi, repo="  Devr.AI  ")
    assert repo == "Devr.AI"
    assert err is None

    # Multi-repo: cannot auto-detect, prompts to specify repo
    repo, err = resolve_repo_for_issue(config_multi, channel_id="99999999", channel_name="general")
    assert repo is None
    assert "specify repo" in err


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
            raise_on_error=True,
        )

        # Verify per_page specifies GitHub API page size
        adapter._paginate.reset_mock()
        adapter._paginate.return_value = [fake_items]
        adapter.list_repo_open_issues("fake-org", "fake-repo", limit=10, per_page=25)
        adapter._paginate.assert_called_once_with(
            "/repos/fake-org/fake-repo/issues",
            params={"state": "open", "sort": "created", "direction": "desc", "per_page": 25},
            raise_on_error=True,
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
            raise_on_error=True,
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

        # Verify GitHubPaginationError propagates as None
        from ghdcbot.adapters.github.rest import GitHubPaginationError

        adapter._paginate = MagicMock(side_effect=GitHubPaginationError("Request failed"))
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


def test_list_repo_open_issues_concurrency() -> None:
    """Concurrent list_repo_open_issues invocations do not corrupt each other's error state."""
    import concurrent.futures
    from typing import Any
    from unittest.mock import MagicMock

    from ghdcbot.adapters.github.rest import GitHubRestAdapter

    with GitHubRestAdapter("fake-token", "fake-org", "https://api.github.com") as adapter:
        def fake_request(method: str, path: str, params: dict | None = None) -> Any:
            if "fail-repo" in path:
                return MagicMock(status_code=500)
            if "success-repo" in path:
                res = MagicMock(status_code=200)
                res.json.return_value = [{"number": 1, "title": "Success issue", "state": "open"}]
                res.headers = {}
                return res
            return None

        adapter._request = MagicMock(side_effect=fake_request)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            fut_fail = executor.submit(adapter.list_repo_open_issues, "fake-org", "fail-repo")
            fut_success = executor.submit(adapter.list_repo_open_issues, "fake-org", "success-repo")

            assert fut_fail.result() is None
            success_issues = fut_success.result()
            assert success_issues is not None
            assert len(success_issues) == 1
            assert success_issues[0]["number"] == 1


def test_issue_handling_fetch_error_vs_empty() -> None:
    """Exercise real issue_cmd.callback with None (fetch error) and [] (empty list)."""
    import asyncio
    from typing import Any
    from unittest.mock import AsyncMock, MagicMock, patch

    import discord

    from ghdcbot.bot import run_bot
    from ghdcbot.config.models import (
        BotConfig,
        DiscordConfig,
        GitHubConfig,
        RepoFilterConfig,
        RuntimeConfig,
    )

    cfg = BotConfig(
        runtime=RuntimeConfig(
            data_dir="./data",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="test-org",
            repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent", "Devr.AI"]),
        ),
        discord=DiscordConfig(guild_id="123", token="fake"),
    )

    captured = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured.append(tree_self)
        orig_tree_init(tree_self, client)

    mock_gh = MagicMock()

    with (
        patch("ghdcbot.bot.load_config", return_value=cfg),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
        patch("ghdcbot.bot.build_adapter", return_value=mock_gh),
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

    tree = captured[0]
    issue_cmd = next(c for c in tree.get_commands(guild=discord.Object(id=123)) if c.name == "issue")

    # 1. Error case (list_repo_open_issues returns None)
    mock_gh.list_repo_open_issues.return_value = None
    mock_interaction = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()
    mock_interaction.channel_id = 99999
    mock_interaction.channel.name = "general"
    mock_interaction.user.id = 456

    asyncio.run(issue_cmd.callback(mock_interaction, repo="Devr.AI", limit=5))

    mock_interaction.followup.send.assert_called_once()
    err_text = mock_interaction.followup.send.call_args[0][0]
    assert "❌ Error fetching issues. Please try again later." in err_text

    # 2. Empty case (list_repo_open_issues returns [])
    mock_gh.list_repo_open_issues.return_value = []
    mock_interaction.followup.send.reset_mock()

    asyncio.run(issue_cmd.callback(mock_interaction, repo="Devr.AI", limit=5))

    mock_interaction.followup.send.assert_called_once()
    empty_text = mock_interaction.followup.send.call_args[0][0]
    assert "No open issues found in **Devr.AI**." in empty_text


def test_issue_repo_autocomplete_choice_creation() -> None:
    """Issue repo autocomplete choices filter and map correctly via get_issue_repo_choices."""
    from ghdcbot.bot import get_issue_repo_choices
    from ghdcbot.config.models import (
        BotConfig,
        DiscordConfig,
        GitHubConfig,
        RepoFilterConfig,
        RuntimeConfig,
    )

    cfg = BotConfig(
        runtime=RuntimeConfig(
            data_dir="./data",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="test-org",
            repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent", "Devr.AI"]),
        ),
        discord=DiscordConfig(guild_id="123", token="fake"),
    )

    choices = get_issue_repo_choices(cfg, "dev")
    assert len(choices) == 1
    assert choices[0].name == "Devr.AI"
    assert choices[0].value == "Devr.AI"

    # Multiple match
    all_choices = get_issue_repo_choices(cfg, "")
    assert len(all_choices) == 2
    assert [c.name for c in all_choices] == ["Knowledge-Agent", "Devr.AI"]


def test_issue_repo_autocomplete_integration() -> None:
    """In run_bot, /issue command has repo param with autocomplete connected to config."""
    from typing import Any
    from unittest.mock import patch

    import discord

    from ghdcbot.bot import run_bot
    from ghdcbot.config.models import (
        BotConfig,
        DiscordConfig,
        GitHubConfig,
        RepoFilterConfig,
        RuntimeConfig,
    )

    cfg = BotConfig(
        runtime=RuntimeConfig(
            data_dir="./data",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="test-org",
            repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent", "Devr.AI"]),
        ),
        discord=DiscordConfig(guild_id="123", token="fake"),
    )

    captured = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured.append(tree_self)
        orig_tree_init(tree_self, client)

    with (
        patch("ghdcbot.bot.load_config", return_value=cfg),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
        patch("ghdcbot.bot.build_adapter"),
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

    assert len(captured) == 1
    tree = captured[0]
    issue_cmds = [
        cmd
        for cmd in tree.get_commands(guild=discord.Object(id=123))
        if cmd.name == "issue"
    ]
    assert len(issue_cmds) == 1
    issue = issue_cmds[0]

    # Verify repo parameter exists and has autocomplete registered using public API
    repo_params = [p for p in issue.parameters if p.name == "repo"]
    assert len(repo_params) == 1
    assert repo_params[0].autocomplete is True


def test_issue_cmd_with_explicit_repo() -> None:
    """Calling /issue with an explicit repo queries that repo."""
    import asyncio
    from typing import Any
    from unittest.mock import AsyncMock, MagicMock, patch

    import discord

    from ghdcbot.bot import run_bot
    from ghdcbot.config.models import (
        BotConfig,
        DiscordConfig,
        GitHubConfig,
        RepoFilterConfig,
        RuntimeConfig,
    )

    cfg = BotConfig(
        runtime=RuntimeConfig(
            data_dir="./data",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="test-org",
            repos=RepoFilterConfig(mode="allow", names=["Knowledge-Agent", "Devr.AI"]),
        ),
        discord=DiscordConfig(guild_id="123", token="fake"),
    )

    captured = []
    orig_tree_init = discord.app_commands.CommandTree.__init__

    def mock_tree_init(tree_self: Any, client: Any) -> None:
        captured.append(tree_self)
        orig_tree_init(tree_self, client)

    mock_gh = MagicMock()
    mock_gh.list_repo_open_issues.return_value = [
        {"number": 101, "title": "Devr issue", "state": "open"}
    ]

    with (
        patch("ghdcbot.bot.load_config", return_value=cfg),
        patch("ghdcbot.bot.resolve_github_token", return_value="fake"),
        patch("ghdcbot.bot.build_adapter", return_value=mock_gh),
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

    tree = captured[0]
    issue_cmd = next(c for c in tree.get_commands(guild=discord.Object(id=123)) if c.name == "issue")

    mock_interaction = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.followup.send = AsyncMock()
    mock_interaction.channel_id = 99999
    mock_interaction.channel.name = "general"
    mock_interaction.user.id = 456

    asyncio.run(issue_cmd.callback(mock_interaction, repo="Devr.AI", limit=5))

    mock_gh.list_repo_open_issues.assert_called_once_with("test-org", "Devr.AI", 5)
    mock_interaction.followup.send.assert_called_once()
    sent_text = mock_interaction.followup.send.call_args[0][0]
    assert "Devr.AI" in sent_text
    assert "#101" in sent_text



