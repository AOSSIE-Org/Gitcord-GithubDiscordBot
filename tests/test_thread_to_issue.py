"""Tests for ghdcbot.engine.thread_to_issue — the /thread engine logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    created_at: datetime = field(default_factory=lambda: datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc))
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
        import ghdcbot.engine.thread_to_issue as module
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
        assert "Summary" in summary
        assert "Identified" in summary
        assert "crashes when logging in" in summary
        assert "DB connection failed" in summary

    def test_synthesizes_discussion_topics(self) -> None:
        from ghdcbot.engine.thread_to_issue import summarize_thread_messages
        messages = [
            {"author_name": "Alice", "content": "Hey, does anyone know how to deploy?"},
            {"author_name": "Bob", "content": "Yes, use docker compose up"},
        ]
        summary = summarize_thread_messages(messages)
        assert "Discussion Summary" in summary
        assert "deploy" in summary
        assert "docker compose up" in summary
        assert "Participants" in summary

    def test_synthesizes_features_from_discussion(self) -> None:
        from ghdcbot.engine.thread_to_issue import summarize_thread_messages
        messages = [
            {"author_name": "Prithvijit", "content": "i will make issues for all of them soon."},
            {"author_name": "Poorvith", "content": "Okay, if you open issues, please assign some of them to me too. I will also be working on additional features within the same workflow: Creating issues and Creating PRs. All will be in chunks, so you can review them carefully."},
            {"author_name": "Prithvijit", "content": "yeah, if you want to propose any other features which can really help Knowledge in future, you can"},
            {"author_name": "Poorvith", "content": "I'll start on it soon. I've already tested a few new features and tracked bugs while using the knowledge agent, so I pushed changes to the local repo and opened a PR. Feel free to review it and flag any problems. I'll also tackle the multi-repository intelligence and point system, and I'll create an issue or PR if needed."},
            {"author_name": "Prithvijit", "content": "<@1533304803537584279> Basically this Point system will work internally and will not be showing to any users, i want Knowledge to adapt to the user's understanding so we need to be very careful how the Point system works because there are many types of developers in the world"},
        ]
        summary = summarize_thread_messages(messages)
        assert "Proposed Features" in summary
        assert "Point System" in summary
        assert "Multi-Repository Intelligence" in summary
        assert "Action Items" in summary
        # Ensure raw mention tokens and conversational chatter are not copied blindly
        assert "<@1533304803537584279>" not in summary
        assert "i will make issues for all of them soon" not in summary.lower()


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




