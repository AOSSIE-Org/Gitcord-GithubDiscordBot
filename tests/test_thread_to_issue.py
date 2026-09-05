"""Tests for ghdcbot.engine.thread_to_issue — the /thread engine logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from ghdcbot.engine.thread_to_issue import (
    collect_thread_messages,
    extract_code_blocks,
    extract_environment_info,
    format_issue_body,
    generate_issue_title,
    resolve_authors,
    strip_template_frontmatter,
)

# ---------------------------------------------------------------------------
# Helpers: fake Discord objects for testing collect_thread_messages
# ---------------------------------------------------------------------------

@dataclass
class _FakeAttachment:
    url: str
    filename: str


@dataclass
class _FakeAuthor:
    id: int
    name: str
    display_name: str
    bot: bool = False


@dataclass
class _FakeMessage:
    author: _FakeAuthor
    content: str
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
    attachments: list[_FakeAttachment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# extract_code_blocks
# ---------------------------------------------------------------------------

class TestExtractCodeBlocks:
    def test_single_block(self) -> None:
        content = "Here is code:\n```python\nprint('hello')\n```\nDone."
        blocks = extract_code_blocks(content)
        assert len(blocks) == 1
        assert blocks[0] == "print('hello')"

    def test_multiple_blocks(self) -> None:
        content = "```\nfoo()\n```\ntext\n```js\nconsole.log('bar')\n```"
        blocks = extract_code_blocks(content)
        assert len(blocks) == 2
        assert "foo()" in blocks[0]
        assert "console.log" in blocks[1]

    def test_empty_content(self) -> None:
        assert extract_code_blocks("") == []
        assert extract_code_blocks(None) == []

    def test_no_code_blocks(self) -> None:
        assert extract_code_blocks("Just plain text") == []


# ---------------------------------------------------------------------------
# extract_environment_info
# ---------------------------------------------------------------------------

class TestExtractEnvironmentInfo:
    def test_detects_os_and_python(self) -> None:
        messages = [
            {"content": "OS: Ubuntu 22.04\nPython: 3.11.5"},
        ]
        result = extract_environment_info(messages)
        assert "Ubuntu 22.04" in result
        assert "3.11.5" in result

    def test_detects_node_version(self) -> None:
        messages = [
            {"content": "node version: 18.17.0"},
        ]
        result = extract_environment_info(messages)
        assert "18.17.0" in result

    def test_empty_messages(self) -> None:
        assert extract_environment_info([]) == ""
        assert extract_environment_info([{"content": ""}]) == ""

    def test_no_env_info(self) -> None:
        messages = [{"content": "I have a bug where the button doesn't work"}]
        assert extract_environment_info(messages) == ""


# ---------------------------------------------------------------------------
# collect_thread_messages
# ---------------------------------------------------------------------------

class TestCollectThreadMessages:
    def test_basic_collection(self) -> None:
        msgs = [
            _FakeMessage(
                author=_FakeAuthor(id=123, name="alice", display_name="Alice"),
                content="Hello world",
            ),
            _FakeMessage(
                author=_FakeAuthor(id=456, name="bob", display_name="Bob"),
                content="Hi there",
            ),
        ]
        result = collect_thread_messages(msgs)
        # Reversed (oldest first)
        assert len(result) == 2
        assert result[0]["author_name"] == "Bob"
        assert result[1]["author_name"] == "Alice"

    def test_skips_bot_messages(self) -> None:
        msgs = [
            _FakeMessage(
                author=_FakeAuthor(id=999, name="Gitcord", display_name="Gitcord", bot=True),
                content="Bot message",
            ),
            _FakeMessage(
                author=_FakeAuthor(id=123, name="alice", display_name="Alice"),
                content="Human message",
            ),
        ]
        result = collect_thread_messages(msgs)
        assert len(result) == 1
        assert result[0]["content"] == "Human message"

    def test_extracts_attachments(self) -> None:
        msgs = [
            _FakeMessage(
                author=_FakeAuthor(id=123, name="alice", display_name="Alice"),
                content="See attachment",
                attachments=[_FakeAttachment(url="https://cdn.example.com/log.txt", filename="log.txt")],
            ),
        ]
        result = collect_thread_messages(msgs)
        assert len(result[0]["attachments"]) == 1
        assert result[0]["attachments"][0]["filename"] == "log.txt"

    def test_extracts_code_blocks(self) -> None:
        msgs = [
            _FakeMessage(
                author=_FakeAuthor(id=123, name="alice", display_name="Alice"),
                content="```python\nraise ValueError\n```",
            ),
        ]
        result = collect_thread_messages(msgs)
        assert len(result[0]["code_blocks"]) == 1
        assert "ValueError" in result[0]["code_blocks"][0]


# ---------------------------------------------------------------------------
# generate_issue_title
# ---------------------------------------------------------------------------

class TestGenerateIssueTitle:
    def test_takes_first_substantive_message(self) -> None:
        messages = [
            {"content": ""},
            {"content": "Button click causes crash on profile page"},
        ]
        title = generate_issue_title(messages)
        assert title == "Button click causes crash on profile page"

    def test_truncates_long_title(self) -> None:
        long_msg = "A" * 100
        messages = [{"content": long_msg}]
        title = generate_issue_title(messages)
        assert len(title) <= 80
        assert title.endswith("...")

    def test_skips_code_only_messages(self) -> None:
        messages = [
            {"content": "```python\nprint('test')\n```"},
            {"content": "The bot crashes here"},
        ]
        title = generate_issue_title(messages)
        assert title == "The bot crashes here"

    def test_fallback_title(self) -> None:
        messages = [{"content": ""}]
        title = generate_issue_title(messages)
        assert title == "Issue from Discord discussion"

    def test_does_not_fabricate_feature_title_from_keywords(self) -> None:
        messages = [
            {"content": "At this point we should check if the workflow is running."},
        ]
        title = generate_issue_title(messages)
        assert title == "At this point we should check if the workflow is running."
        assert not title.startswith("Feature:")


# ---------------------------------------------------------------------------
# format_issue_body (no template)
# ---------------------------------------------------------------------------

class TestFormatIssueBodyNoTemplate:
    def test_produces_transcript_section(self) -> None:
        messages = [
            {
                "author_name": "Alice",
                "github_user": "@alice-gh",
                "content": "Something is broken",
                "timestamp": "2026-01-15 12:00 UTC",
                "attachments": [],
                "code_blocks": [],
            },
        ]
        body = format_issue_body(messages)
        assert "📜 Source Transcript" not in body
        assert "@alice-gh" in body
        assert "Something is broken" in body
        assert "Gitcord" in body  # footer


    def test_includes_code_snippets(self) -> None:
        messages = [
            {
                "author_name": "Bob",
                "github_user": "@bob-gh",
                "content": "```python\nraise ValueError\n```",
                "timestamp": "",
                "attachments": [],
                "code_blocks": ["raise ValueError"],
            },
        ]
        body = format_issue_body(messages)
        assert "### 💻 Code Snippets" in body
        assert "raise ValueError" in body

    def test_includes_environment(self) -> None:
        messages = [
            {
                "author_name": "Alice",
                "content": "OS: Ubuntu 22.04",
                "timestamp": "",
                "attachments": [],
                "code_blocks": [],
            },
        ]
        body = format_issue_body(messages)
        assert "### ⚙️ Environment" in body
        assert "Ubuntu 22.04" in body


# ---------------------------------------------------------------------------
# format_issue_body (with template)
# ---------------------------------------------------------------------------

class TestFormatIssueBodyWithTemplate:
    def test_fills_description_section(self) -> None:
        template = "### Description\n\n<!-- Describe the bug -->\n\n### Steps to Reproduce\n\n1. ...\n"
        messages = [
            {
                "author_name": "Alice",
                "github_user": "@alice-gh",
                "content": "The login button is broken",
                "timestamp": "2026-01-15 12:00 UTC",
                "attachments": [],
                "code_blocks": [],
            },
        ]
        body = format_issue_body(messages, template_body=template)
        assert "### Description" in body
        assert "@alice-gh" in body
        assert "Gitcord" in body

    def test_fill_template_preserves_backslashes_and_regex_escapes(self) -> None:
        template = (
            "### Description\n\n"
            "### Steps to Reproduce\n\n"
            "### Environment\n\n"
            "### Error Logs\n\n"
        )
        messages = [
            {
                "author_name": "Alice",
                "github_user": "@alice-gh",
                "content": r"Issue with regex \1 and path C:\Users\name\test and group \g<0>",
                "timestamp": "2026-01-15 12:00 UTC",
                "attachments": [],
                "code_blocks": [],
            },
            {
                "author_name": "Bob",
                "github_user": "@bob-gh",
                "content": r"OS: C:\Windows\System32\os.dll",
                "timestamp": "2026-01-15 12:01 UTC",
                "attachments": [],
                "code_blocks": [],
            },
            {
                "author_name": "Bob",
                "github_user": "@bob-gh",
                "content": "Traceback (most recent call last):\n  File \"C:\\test\\app.py\", line 10\n    re.sub(r'\\1', x)",
                "timestamp": "2026-01-15 12:02 UTC",
                "attachments": [],
                "code_blocks": [],
            },
        ]
        body = format_issue_body(messages, template_body=template)
        assert r"\1" in body
        assert r"\g<0>" in body
        assert "### Environment" in body
        assert "### Error Logs" in body



# ---------------------------------------------------------------------------
# resolve_authors
# ---------------------------------------------------------------------------

class TestResolveAuthors:
    def test_verified_user(self) -> None:
        """Verified user gets @github_handle."""
        class FakeStorage:
            pass

        # Monkey-patch resolve_discord_to_github for this test
        original = None
        try:
            import ghdcbot.engine.issue_assignment as ia_mod
            original = ia_mod.resolve_discord_to_github
            ia_mod.resolve_discord_to_github = lambda storage, discord_id: "alice-gh" if discord_id == "123" else None
            
            messages = [
                {"author_name": "Alice", "author_id": "123", "content": "test"},
            ]
            result = resolve_authors(messages, FakeStorage())
            assert result[0]["github_user"] == "@alice-gh"
        finally:
            if original is not None:
                ia_mod.resolve_discord_to_github = original

    def test_unverified_user(self) -> None:
        """Unverified user gets Discord: @display_name fallback."""
        class FakeStorage:
            pass

        import ghdcbot.engine.issue_assignment as ia_mod
        original = ia_mod.resolve_discord_to_github
        try:
            ia_mod.resolve_discord_to_github = lambda storage, discord_id: None
            
            messages = [
                {"author_name": "Bob", "author_id": "999", "content": "test"},
            ]
            result = resolve_authors(messages, FakeStorage())
            assert result[0]["github_user"] == "Discord: @Bob"
        finally:
            ia_mod.resolve_discord_to_github = original


# ---------------------------------------------------------------------------
# strip_template_frontmatter
# ---------------------------------------------------------------------------

class TestStripTemplateFrontmatter:
    def test_strips_yaml_frontmatter(self) -> None:
        raw = "---\nname: Bug Report\nabout: File a bug\n---\n### Description\n\nDescribe the bug."
        result = strip_template_frontmatter(raw)
        assert result.startswith("### Description")
        assert "name: Bug Report" not in result

    def test_no_frontmatter(self) -> None:
        raw = "### Description\n\nDescribe the bug."
        result = strip_template_frontmatter(raw)
        assert result == raw

    def test_incomplete_frontmatter(self) -> None:
        raw = "---\nname: Bug Report\nThis has no closing delimiter"
        result = strip_template_frontmatter(raw)
        # Should return as-is since there's no closing ---
        assert result == raw

    def test_delimiter_within_yaml_value_does_not_prematurely_strip(self) -> None:
        raw = (
            "---\n"
            "name: 'Bug Report --- Important'\n"
            "about: 'File a bug report --- please attach logs'\n"
            "title: ''\n"
            "---\n"
            "### Description\n\n"
            "Describe the bug."
        )
        result = strip_template_frontmatter(raw)
        assert result.startswith("### Description")
        assert "Bug Report --- Important" not in result
        assert "about:" not in result

    def test_crlf_yaml_frontmatter_stripped(self) -> None:
        raw = "---\r\nname: Bug Report\r\nabout: File a bug\r\n---\r\n### Description\r\n\r\nDescribe the bug."
        result = strip_template_frontmatter(raw)
        assert result.startswith("### Description")
        assert "name: Bug Report" not in result

    def test_non_frontmatter_dash_preserved(self) -> None:
        raw = "---not-frontmatter\n### Description"
        result = strip_template_frontmatter(raw)
        assert result == raw


# ---------------------------------------------------------------------------
# summarize_thread_messages
# ---------------------------------------------------------------------------

class TestSummarizeThreadMessages:
    def test_summarizes_empty_list(self) -> None:
        from ghdcbot.engine.thread_to_issue import summarize_thread_messages
        assert summarize_thread_messages([]) == "No messages captured to summarize."

    def test_summarizes_conversation(self) -> None:
        from ghdcbot.engine.thread_to_issue import summarize_thread_messages
        messages = [
            {"author_name": "Alice", "content": "I have an issue where the app crashes when logging in"},
            {"author_name": "Bob", "content": "Can you share the error log?"},
            {"author_name": "Alice", "content": "Sure, it says Exception: DB connection failed"},
        ]
        summary = summarize_thread_messages(messages)
        assert "Participants" in summary
        assert "@Alice" in summary
        assert "@Bob" in summary
        assert "Thread Transcript" in summary
        assert "crashes when logging in" in summary
        assert "DB connection failed" in summary

    def test_formats_discussion_transcript(self) -> None:
        from ghdcbot.engine.thread_to_issue import summarize_thread_messages
        messages = [
            {"author_name": "Alice", "content": "Hey, does anyone know how to deploy?"},
            {"author_name": "Bob", "content": "Yes, use docker compose up"},
        ]
        summary = summarize_thread_messages(messages)
        assert "Thread Transcript" in summary
        assert "does anyone know how to deploy?" in summary
        assert "Yes, use docker compose up" in summary
        assert "Participants" in summary

    def test_transcript_cleans_mentions_and_deterministic_sections_only(self) -> None:
        from ghdcbot.engine.thread_to_issue import summarize_thread_messages
        messages = [
            {"author_name": "Prithvijit", "content": "i will make issues for all of them soon."},
            {"author_name": "Poorvith", "content": "Okay, if you open issues, please assign some of them to me too. I will also be working on additional features within the same workflow: Creating issues and Creating PRs. All will be in chunks, so you can review them carefully."},
            {"author_name": "Prithvijit", "content": "yeah, if you want to propose any other features which can really help Knowledge in future, you can"},
            {"author_name": "Poorvith", "content": "I'll start on it soon. I've already tested a few new features and tracked bugs while using the knowledge agent, so I pushed changes to the local repo and opened a PR. Feel free to review it and flag any problems. I'll also tackle the multi-repository intelligence and point system, and I'll create an issue or PR if needed."},
            {"author_name": "Prithvijit", "content": "<@1533304803537584279> Basically this Point system will work internally and will not be showing to any users, i want Knowledge to adapt to the user's understanding so we need to be very careful how the Point system works because there are many types of developers in the world"},
        ]
        summary = summarize_thread_messages(messages)
        # Verify no fabricated synthesis sections
        assert "Proposed Features & Architecture" not in summary
        assert "Internal Developer Adaptation Point System" not in summary
        assert "Action Items & Planned Work" not in summary
        # Verify deterministic sections are present
        assert "Participants" in summary
        assert "@Prithvijit" in summary
        assert "@Poorvith" in summary
        assert "Thread Transcript" in summary
        assert "<@1533304803537584279>" not in summary


class TestGetIssueTemplate:
    def test_get_issue_template_markdown_success(self, monkeypatch) -> None:
        import base64

        import httpx

        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
        raw_content = "---\nname: Bug Report\n---\n### Description\nDescribe the bug"
        encoded = base64.b64encode(raw_content.encode("utf-8")).decode("utf-8")

        def mock_request(method: str, path: str, params: dict) -> httpx.Response:
            assert path == "/repos/AOSSIE/Gitcord/contents/.github/ISSUE_TEMPLATE/bug_report.md"
            return httpx.Response(200, json={"content": encoded})

        monkeypatch.setattr(adapter, "_request", mock_request)
        result = adapter.get_issue_template("AOSSIE", "Gitcord", "bug_report")
        assert result == raw_content

    def test_get_issue_template_markdown_with_extension(self, monkeypatch) -> None:
        import base64

        import httpx

        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
        raw_content = "### Feature\nDescribe feature"
        encoded = base64.b64encode(raw_content.encode("utf-8")).decode("utf-8")

        def mock_request(method: str, path: str, params: dict) -> httpx.Response:
            assert path == "/repos/AOSSIE/Gitcord/contents/.github/ISSUE_TEMPLATE/feature_request.md"
            return httpx.Response(200, json={"content": encoded})

        monkeypatch.setattr(adapter, "_request", mock_request)
        result = adapter.get_issue_template("AOSSIE", "Gitcord", "feature_request.md")
        assert result == raw_content

    def test_get_issue_template_ignores_yaml_and_returns_none_when_md_missing(self, monkeypatch) -> None:
        import httpx

        from ghdcbot.adapters.github.rest import GitHubRestAdapter

        adapter = GitHubRestAdapter(token="t", org="AOSSIE", api_base="https://api.github.com")
        requested_paths: list[str] = []

        def mock_request(method: str, path: str, params: dict) -> httpx.Response:
            requested_paths.append(path)
            return httpx.Response(404)

        monkeypatch.setattr(adapter, "_request", mock_request)
        result = adapter.get_issue_template("AOSSIE", "Gitcord", "form_template")
        assert result is None
        assert requested_paths == [
            "/repos/AOSSIE/Gitcord/contents/.github/ISSUE_TEMPLATE/form_template.md"
        ]
        assert not any(".yml" in p or ".yaml" in p for p in requested_paths)


# ---------------------------------------------------------------------------
# Helpers & Tests for ThreadApproveView and ThreadEditTitleModal
# ---------------------------------------------------------------------------

class _FakeInteractionResponseForView:
    def __init__(self) -> None:
        self._done = False
        self.sent_messages: list[str] = []
        self.last_modal: Any = None
        self.last_edited: dict[str, Any] = {}

    def is_done(self) -> bool:
        return self._done

    async def defer(self, *, ephemeral: bool = False) -> None:
        self._done = True

    async def send_message(self, content: str, *, ephemeral: bool = False, **kwargs) -> None:
        self._done = True
        self.sent_messages.append(content)

    async def edit_message(self, **kwargs) -> None:
        self._done = True
        self.last_edited = kwargs

    async def send_modal(self, modal: Any) -> None:
        self.last_modal = modal


class _FakeFollowupForView:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []

    async def send(self, content: str, *, ephemeral: bool = False, **kwargs) -> None:
        self.sent_messages.append(content)


class _FakeInteractionForView:
    def __init__(self, user_id: int = 123) -> None:
        from unittest.mock import AsyncMock, MagicMock
        self.user = MagicMock()
        self.user.id = user_id
        self.response = _FakeInteractionResponseForView()
        self.followup = _FakeFollowupForView()
        self.message = MagicMock()
        self.message.edit = AsyncMock()


class TestThreadApproveView:
    def test_build_preview_content(self) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView

        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Bug in profile command",
            issue_body="Here is the description of the problem.",
            config=MagicMock(),
            github_adapter=MagicMock(),
            author_id=123,
        )
        preview = view.build_preview_content()
        assert "AOSSIE/Gitcord" in preview
        assert "Bug in profile command" in preview
        assert "Here is the description of the problem." in preview
        assert "Approve & Create Issue" in preview
        assert len(preview) < 2000

    def test_build_preview_content_truncates_long_body(self) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView

        long_body = "x" * 3000
        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="A very long issue",
            issue_body=long_body,
            config=MagicMock(),
            github_adapter=MagicMock(),
            author_id=123,
        )
        preview = view.build_preview_content()
        assert "(truncated for preview)" in preview
        assert len(preview) < 2000

    @pytest.mark.asyncio
    async def test_approve_permission_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView

        monkeypatch.setattr("ghdcbot.bot.slash_command_allowed", lambda interaction, config, cmd: False)

        mock_adapter = MagicMock()
        mock_config = MagicMock()
        mock_config.discord.command_permissions = {"thread": MagicMock()}
        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Bug",
            issue_body="Desc",
            config=mock_config,
            github_adapter=mock_adapter,
            author_id=123,
        )
        interaction = _FakeInteractionForView(user_id=123)
        await view.approve_creation(interaction)

        assert len(interaction.response.sent_messages) == 1
        assert "Permission denied" in interaction.response.sent_messages[0]
        mock_adapter.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_approve_allowed_for_everyone_by_default(self) -> None:
        """When thread is not restricted in command_permissions, any guild member who drafted the issue can approve."""
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView
        from ghdcbot.core.modes import RunMode

        mock_config = MagicMock()
        mock_config.discord.command_permissions = {}
        mock_config.runtime.mode = RunMode.DRY_RUN
        mock_config.github.permissions.write = True
        mock_adapter = MagicMock()

        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Bug",
            issue_body="Desc",
            config=mock_config,
            github_adapter=mock_adapter,
            author_id=123,
        )
        interaction = _FakeInteractionForView(user_id=123)

        await view.approve_creation(interaction)
        assert any("[DRY RUN]" in msg for msg in interaction.followup.sent_messages)

    @pytest.mark.asyncio
    async def test_approve_other_user_denied(self) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView

        mock_adapter = MagicMock()
        mock_config = MagicMock()
        mock_config.discord.command_permissions = {}
        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Bug",
            issue_body="Desc",
            config=mock_config,
            github_adapter=mock_adapter,
            author_id=123,
        )
        interaction = _FakeInteractionForView(user_id=999)
        await view.approve_creation(interaction)

        assert any("Only the member who initiated this issue preview can approve it" in msg for msg in interaction.response.sent_messages)
        mock_adapter.create_issue.assert_not_called()

    @pytest.mark.asyncio
    async def test_approve_dry_run_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView
        from ghdcbot.core.modes import RunMode

        monkeypatch.setattr("ghdcbot.bot.slash_command_allowed", lambda interaction, config, cmd: True)

        mock_config = MagicMock()
        mock_config.runtime.mode = RunMode.DRY_RUN
        mock_config.github.permissions.write = True
        mock_adapter = MagicMock()

        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Bug",
            issue_body="Desc",
            config=mock_config,
            github_adapter=mock_adapter,
            author_id=123,
        )
        interaction = _FakeInteractionForView()

        await view.approve_creation(interaction)

        mock_adapter.create_issue.assert_not_called()
        assert any("[DRY RUN]" in msg for msg in interaction.followup.sent_messages)

    @pytest.mark.asyncio
    async def test_approve_creates_issue_successfully(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView
        from ghdcbot.core.modes import RunMode

        monkeypatch.setattr("ghdcbot.bot.slash_command_allowed", lambda interaction, config, cmd: True)

        mock_config = MagicMock()
        mock_config.runtime.mode = RunMode.ACTIVE
        mock_config.github.permissions.write = True
        mock_adapter = MagicMock()
        mock_adapter.create_issue.return_value = {
            "html_url": "https://github.com/AOSSIE/Gitcord/issues/42",
            "number": 42,
        }

        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Bug Title",
            issue_body="Bug Description",
            config=mock_config,
            github_adapter=mock_adapter,
            author_id=123,
        )
        interaction = _FakeInteractionForView()

        await view.approve_creation(interaction)

        mock_adapter.create_issue.assert_called_once_with(
            owner="AOSSIE",
            repo="Gitcord",
            title="Bug Title",
            body="Bug Description",
        )
        assert any("Successfully created issue" in msg for msg in interaction.followup.sent_messages)
        assert any("https://github.com/AOSSIE/Gitcord/issues/42" in msg for msg in interaction.followup.sent_messages)

    @pytest.mark.asyncio
    async def test_approve_handles_creation_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView
        from ghdcbot.core.modes import RunMode

        monkeypatch.setattr("ghdcbot.bot.slash_command_allowed", lambda interaction, config, cmd: True)

        mock_config = MagicMock()
        mock_config.runtime.mode = RunMode.ACTIVE
        mock_config.github.permissions.write = True
        mock_adapter = MagicMock()
        mock_adapter.create_issue.return_value = None
        mock_adapter.last_error = "Repository not found"

        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Bug Title",
            issue_body="Bug Description",
            config=mock_config,
            github_adapter=mock_adapter,
            author_id=123,
        )
        interaction = _FakeInteractionForView()

        await view.approve_creation(interaction)
        assert any("Failed to create issue: Repository not found" in msg for msg in interaction.followup.sent_messages)

    @pytest.mark.asyncio
    async def test_cancel_button(self) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView

        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Bug",
            issue_body="Desc",
            config=MagicMock(),
            github_adapter=MagicMock(),
            author_id=123,
        )
        interaction = _FakeInteractionForView(user_id=123)

        await view.cancel_creation(interaction)
        assert "cancelled by <@123>" in interaction.response.last_edited.get("content", "")
        assert all(item.disabled for item in view.children)


class TestThreadEditTitleModal:
    @pytest.mark.asyncio
    async def test_modal_submits_new_title(self) -> None:
        from unittest.mock import MagicMock

        from ghdcbot.bot import ThreadApproveView, ThreadEditTitleModal

        view = ThreadApproveView(
            owner="AOSSIE",
            repo="Gitcord",
            issue_title="Old Title",
            issue_body="Desc",
            config=MagicMock(),
            github_adapter=MagicMock(),
            author_id=123,
        )
        modal = ThreadEditTitleModal(current_title="Old Title", view=view)
        modal.title_input._value = "New Updated Title"

        interaction = _FakeInteractionForView()

        await modal.on_submit(interaction)
        assert view.issue_title == "New Updated Title"
        assert "New Updated Title" in interaction.response.last_edited.get("content", "")


class TestDeterministicSummarizer:
    def test_extract_overview_skips_greetings_and_fillers(self) -> None:
        from ghdcbot.engine.thread_to_issue import _extract_overview

        messages = [
            {"content": "Hey everyone!"},
            {"content": "good morning"},
            {"content": "The bot crashes on Linux when running /profile because bio is null."},
            {"content": "Can someone help?"},
        ]
        overview = _extract_overview(messages)
        assert "The bot crashes on Linux when running /profile because bio is null." in overview
        assert "Hey everyone" not in overview

    def test_extract_participant_key_points_filters_noise(self) -> None:
        from ghdcbot.engine.thread_to_issue import _extract_participant_key_points

        messages = [
            {"author_name": "Alice", "content": "I noticed that the Docker build fails during pip install on Windows."},
            {"author_name": "Bob", "content": "ok"},
            {"author_name": "Bob", "content": "thanks"},
            {"author_name": "Bob", "content": "We need to ensure libpq-dev is installed before building the image."},
            {"author_name": "Alice", "content": "sounds good"},
        ]
        points = _extract_participant_key_points(messages)
        author_map = dict(points)
        assert "@Alice" in author_map
        assert "@Bob" in author_map
        assert any("Docker build fails" in p for p in author_map["@Alice"])
        assert any("libpq-dev is installed" in p for p in author_map["@Bob"])
        assert not any(p in ("ok", "thanks", "sounds good") for p in author_map["@Bob"])

    def test_extract_identified_errors_detects_exceptions_and_traces(self) -> None:
        from ghdcbot.engine.thread_to_issue import _extract_identified_errors

        messages = [
            {"content": "Running test failed with exit code 1"},
            {"content": "Exception: Connection to Redis database timed out", "code_blocks": []},
            {"content": "Traceback (most recent call last):\n  File 'main.py', line 22\nKeyError: 'user_id'"},
        ]
        errors = _extract_identified_errors(messages)
        assert len(errors) >= 2
        assert any("Connection to Redis database timed out" in e for e in errors)
        assert any("exit code 1" in e for e in errors)

    def test_extract_action_items_identifies_commitments(self) -> None:
        from ghdcbot.engine.thread_to_issue import _extract_action_items

        messages = [
            {"author_name": "Charlie", "content": "I will submit a PR to add the missing null check this afternoon."},
            {"author_name": "Dave", "content": "Please review PR #49 when you have time."},
            {"author_name": "Dave", "content": "Cool, thanks!"},
        ]
        actions = _extract_action_items(messages)
        assert len(actions) == 2
        assert actions[0][0] == "@Charlie"
        assert "I will submit a PR" in actions[0][1]
        assert actions[1][0] == "@Dave"
        assert "Please review PR #49" in actions[1][1]

    def test_extract_references_finds_urls_and_prs(self) -> None:
        from ghdcbot.engine.thread_to_issue import _extract_references

        messages = [
            {"content": "Check out https://github.com/AOSSIE-Org/Gitcord/pull/49 for details."},
            {"content": "This relates to issue #12 and PR #45."},
        ]
        refs = _extract_references(messages)
        assert "https://github.com/AOSSIE-Org/Gitcord/pull/49" in refs
        assert "#12" in refs
        assert "#45" in refs

    def test_summarize_thread_messages_full_structure(self) -> None:
        from ghdcbot.engine.thread_to_issue import summarize_thread_messages

        messages = [
            {"author_name": "Alice", "content": "The application crashes when running /profile on Linux."},
            {"author_name": "Bob", "content": "Exception: Database connection refused on port 5432."},
            {"author_name": "Alice", "content": "I will create a patch to fix the PostgreSQL port config."},
            {"author_name": "Bob", "content": "Refer to https://github.com/AOSSIE-Org/Gitcord/pull/10 for reference."},
        ]
        summary = summarize_thread_messages(messages)
        assert "### 📋 Discussion Summary" in summary
        assert "**Overview:** The application crashes when running /profile on Linux." in summary
        assert "**Key Discussion Points:**" in summary
        assert "@Alice" in summary
        assert "@Bob" in summary
        assert "**Identified Errors & Defects:**" in summary
        assert "Database connection refused" in summary
        assert "**Action Items & Next Steps:**" in summary
        assert "I will create a patch" in summary
        assert "**Referenced Links & Issues:**" in summary
        assert "https://github.com/AOSSIE-Org/Gitcord/pull/10" in summary
        assert "**👥 Participants (2):** @Alice, @Bob" in summary
        assert "### 💬 Thread Transcript" in summary


class TestThreadCommandIntegration:
    def test_starter_message_inclusion_in_thread(self) -> None:
        from unittest.mock import MagicMock

        import discord

        from ghdcbot.engine.thread_to_issue import collect_thread_messages

        thread_channel = MagicMock(spec=discord.Thread)
        thread_channel.id = 1001

        starter_author = MagicMock()
        starter_author.id = 111
        starter_author.display_name = "StarterUser"
        starter_author.name = "StarterUser"
        starter_author.bot = False

        starter_msg = MagicMock()
        starter_msg.id = 1001
        starter_msg.author = starter_author
        starter_msg.content = "Initial bug report that started the thread"
        starter_msg.created_at = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
        starter_msg.attachments = []

        thread_channel.starter_message = starter_msg

        reply_author = MagicMock()
        reply_author.id = 222
        reply_author.display_name = "ReplyUser"
        reply_author.name = "ReplyUser"
        reply_author.bot = False

        reply_msg = MagicMock()
        reply_msg.id = 1002
        reply_msg.author = reply_author
        reply_msg.content = "I will fix this bug."
        reply_msg.created_at = datetime(2026, 1, 1, 10, 5, tzinfo=UTC)
        reply_msg.attachments = []

        raw_messages = [reply_msg]

        if starter_msg.id not in {m.id for m in raw_messages}:
            raw_messages.append(starter_msg)

        collected = collect_thread_messages(raw_messages)
        assert len(collected) == 2
        assert collected[0]["content"] == "Initial bug report that started the thread"
        assert collected[1]["content"] == "I will fix this bug."


