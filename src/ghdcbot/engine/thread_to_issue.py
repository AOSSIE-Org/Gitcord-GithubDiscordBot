"""Discord thread/conversation → GitHub issue formatter (deterministic, no AI)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Regex for fenced code blocks: ```lang\n...\n```
_CODE_BLOCK_RE = re.compile(r"```(?:\w+)?\s*\n(.*?)```", re.DOTALL)

# Common environment patterns contributors paste
_ENV_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?:os|operating\s*system)\s*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:python|py)\s*(?:version)?\s*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:node|nodejs?)\s*(?:version)?\s*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:npm|yarn|pnpm)\s*(?:version)?\s*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:browser)\s*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:java)\s*(?:version)?\s*[:=]\s*(.+)", re.IGNORECASE),
    re.compile(r"(?:^|\n)\s*(?:docker)\s*(?:version)?\s*[:=]\s*(.+)", re.IGNORECASE),
]

# Stack trace patterns
_STACKTRACE_PATTERNS = [
    re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
    re.compile(r"^\s+at\s+\S+\s+\(.+:\d+:\d+\)", re.MULTILINE),  # JS/Node
    re.compile(r"^\s+File\s+\".+\",\s+line\s+\d+", re.MULTILINE),  # Python
    re.compile(r"^Error:\s+", re.MULTILINE),
    re.compile(r"^Exception\s+in\s+", re.MULTILINE | re.IGNORECASE),
]


def extract_code_blocks(content: str) -> list[str]:
    """Extract fenced code blocks from message content.

    Returns list of code block contents (without the ``` delimiters).
    """
    if not content:
        return []
    return [match.group(1).strip() for match in _CODE_BLOCK_RE.finditer(content)]


def extract_environment_info(messages: list[dict]) -> str:
    """Scan messages for environment/system information patterns.

    Returns a formatted string of detected environment info, or empty string.
    """
    env_lines: list[str] = []
    seen: set[str] = set()

    for msg in messages:
        content = msg.get("content", "")
        if not content:
            continue
        for pattern in _ENV_PATTERNS:
            match = pattern.search(content)
            if match:
                value = match.group(0).strip()
                normalized = value.lower()
                if normalized not in seen:
                    seen.add(normalized)
                    env_lines.append(f"- {value}")

    return "\n".join(env_lines)


def _looks_like_log_or_stacktrace(content: str) -> bool:
    """Return True if the content appears to contain a stack trace or error log."""
    for pattern in _STACKTRACE_PATTERNS:
        if pattern.search(content):
            return True
    return False


def collect_thread_messages(messages: list[Any]) -> list[dict]:
    """Extract structured data from a list of discord.Message objects.

    Args:
        messages: List of discord.Message (or duck-typed objects with
                  .author, .content, .created_at, .attachments).

    Returns:
        List of message dicts, oldest first, with keys:
        author_name, author_id, content, timestamp, attachments, code_blocks.
    """
    result = []
    for msg in messages:
        author = getattr(msg, "author", None)
        # Skip bot messages
        if author and getattr(author, "bot", False):
            continue

        author_name = ""
        author_id = ""
        if author:
            author_name = getattr(author, "display_name", "") or getattr(author, "name", "") or ""
            author_id = str(getattr(author, "id", ""))

        content = getattr(msg, "content", "") or ""
        created_at = getattr(msg, "created_at", None)
        timestamp = ""
        if created_at:
            if isinstance(created_at, datetime):
                timestamp = created_at.strftime("%Y-%m-%d %H:%M UTC")
            else:
                timestamp = str(created_at)

        attachments_list = []
        raw_attachments = getattr(msg, "attachments", []) or []
        for att in raw_attachments:
            att_url = getattr(att, "url", None) or (att if isinstance(att, str) else "")
            att_name = getattr(att, "filename", None) or ""
            if att_url:
                attachments_list.append({"url": str(att_url), "filename": str(att_name)})

        code_blocks = extract_code_blocks(content)

        result.append({
            "author_name": author_name,
            "author_id": author_id,
            "content": content,
            "timestamp": timestamp,
            "attachments": attachments_list,
            "code_blocks": code_blocks,
        })

    # Reverse so oldest message is first (Discord history returns newest first)
    result.reverse()
    return result


def resolve_authors(messages: list[dict], storage: Any) -> list[dict]:
    """Resolve Discord author IDs to GitHub usernames using verified identity mappings.

    Mutates messages in-place, adding 'github_user' key. Falls back to
    'Discord: @display_name' for unverified users.

    Args:
        messages: List of collected message dicts (from collect_thread_messages).
        storage: Storage adapter with list_verified_identity_mappings().

    Returns:
        The same list with 'github_user' added to each dict.
    """
    from ghdcbot.engine.issue_assignment import resolve_discord_to_github

    cache: dict[str, str | None] = {}
    for msg in messages:
        author_id = msg.get("author_id", "")
        if author_id not in cache:
            cache[author_id] = resolve_discord_to_github(storage, author_id)

        github_user = cache.get(author_id)
        if github_user:
            msg["github_user"] = f"@{github_user}"
        else:
            display = msg.get("author_name", "Unknown")
            msg["github_user"] = f"Discord: @{display}"

    return messages


_DISCORD_MENTION_RE = re.compile(r"<@!?(\d+)>|<@&(\d+)>|<#(\d+)>")

_GREETING_PREFIX_RE = re.compile(
    r"^(?:hey(?:\s+guys|\s+everyone|\s+all)?|hi(?:\s+guys|\s+everyone|\s+all)?|hello|good\s+(?:morning|afternoon|evening)|yo|anyone(?:\s+knows?)?)[,:\s\-]+",
    re.IGNORECASE,
)

# Filler phrases and meta-chatter that should not be quoted verbatim in issue bodies
_META_CHATTER_PATTERNS = [
    re.compile(r"^(?:i\s+will|i'll)\s+(?:make|create|open)\s+issues?\s+(?:for\s+all\s+of\s+them)?.*", re.IGNORECASE),
    re.compile(r"^okay,?\s+if\s+you\s+open\s+issues?.*", re.IGNORECASE),
    re.compile(r"^yeah,?\s+if\s+you\s+want\s+to\s+propose.*", re.IGNORECASE),
    re.compile(r"^(?:i'll|i\s+will)\s+start\s+on\s+it\s+soon.*", re.IGNORECASE),
    re.compile(r"^feel\s+free\s+to\s+review\s+it.*", re.IGNORECASE),
    re.compile(r"^all\s+will\s+be\s+in\s+chunks.*", re.IGNORECASE),
    re.compile(r"^basically\s+", re.IGNORECASE),
]

_FILLERS = {
    "ok", "okay", "yes", "yeah", "yep", "no", "nope", "thanks", "thank you", "thx", "ty",
    "done", "cool", "great", "nice", "sounds good", "lgtm", "sure", "got it", "i see",
    "will do", "working on it", "see once more", "try now", "restarting", "fixed",
}


def _clean_discord_text(text: str) -> str:
    """Remove raw Discord mention tokens, greetings, and leading fillers."""
    cleaned = _DISCORD_MENTION_RE.sub("", text).strip()
    cleaned = _GREETING_PREFIX_RE.sub("", cleaned).strip()
    return cleaned


def _clean_participant_name(name: str) -> str:
    """Clean participant name formatting (prevent @@ or messy markdown)."""
    clean = name.replace("Discord: @", "").replace("Discord:", "").strip()
    clean = clean.lstrip("@").strip()
    if clean.startswith("[") and "]" in clean:
        # Extract name from markdown link [name](url)
        match = re.match(r"\[@?([^\]]+)\]", clean)
        if match:
            clean = match.group(1)
    return f"@{clean}" if clean else "@User"


def _extract_feature_topics(text: str) -> list[str]:
    """Extract key technical topics, features, or modules mentioned in a message."""
    topics: list[str] = []
    
    # Common feature pattern indicators
    feature_patterns = [
        re.compile(r"(?:point\s+system)", re.IGNORECASE),
        re.compile(r"(?:multi[- ]repository\s+intelligence|multi[- ]repo\s+intelligence)", re.IGNORECASE),
        re.compile(r"(?:creating\s+issues?\s+and\s+(?:creating\s+)?prs?|issue\s+and\s+pr\s+workflow)", re.IGNORECASE),
        re.compile(r"(?:knowledge\s+agent|knowledge\s+adaptation)", re.IGNORECASE),
        re.compile(r"(?:features?\s+within\s+[^,\.\n]+)", re.IGNORECASE),
        re.compile(r"(?:support\s+for\s+[^,\.\n]+)", re.IGNORECASE),
    ]
    
    for pattern in feature_patterns:
        for match in pattern.finditer(text):
            topic = match.group(0).strip().title()
            if topic and topic not in topics:
                topics.append(topic)
                
    return topics


def generate_issue_title(messages: list[dict]) -> str:
    """Generate a descriptive, synthesized issue title from the discussion.

    Identifies key features or core problems rather than copying raw chat messages.
    """
    all_text: list[str] = []
    all_topics: list[str] = []
    
    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        stripped = _CODE_BLOCK_RE.sub("", content).strip()
        cleaned = _clean_discord_text(stripped)
        if cleaned:
            all_text.append(cleaned)
            for topic in _extract_feature_topics(cleaned):
                if topic not in all_topics:
                    all_topics.append(topic)

    # 1. If key feature topics were extracted, build a title from them
    if all_topics:
        if len(all_topics) == 1:
            return f"Feature: {all_topics[0]}"
        return f"Feature: {', '.join(all_topics[:2])}"

    # 2. Check for substantive statements
    for text in all_text:
        first_line = text.split("\n")[0].strip()
        if not first_line or first_line.lower() in _FILLERS:
            continue
        if len(first_line) > 80:
            return first_line[:77] + "..."
        return first_line

    return "Issue from Discord discussion"


def _format_transcript(messages: list[dict]) -> str:
    """Format messages into a Markdown transcript block."""
    lines = []
    for msg in messages:
        author = msg.get("github_user") or msg.get("author_name") or "Unknown"
        ts = msg.get("timestamp", "")
        content = msg.get("content", "")

        header = f"**{author}**"
        if ts:
            header += f" ({ts})"

        lines.append(header)
        if content:
            lines.append(content)

        # Append attachment links
        for att in msg.get("attachments", []):
            name = att.get("filename") or "attachment"
            url = att.get("url", "")
            if url:
                lines.append(f"📎 [{name}]({url})")

        lines.append("")  # blank line between messages

    return "\n".join(lines).strip()


def _collect_all_code_blocks(messages: list[dict]) -> list[str]:
    """Collect and deduplicate code blocks across all messages."""
    seen: set[str] = set()
    blocks: list[str] = []
    for msg in messages:
        for block in msg.get("code_blocks", []):
            normalized = block.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                blocks.append(normalized)
    return blocks


def _collect_log_snippets(messages: list[dict]) -> list[str]:
    """Collect messages that look like error logs or stack traces."""
    snippets = []
    for msg in messages:
        content = msg.get("content", "")
        if content and _looks_like_log_or_stacktrace(content):
            snippets.append(content.strip())
        # Also check inside code blocks
        for block in msg.get("code_blocks", []):
            if _looks_like_log_or_stacktrace(block):
                snippets.append(block.strip())
    return snippets



def summarize_thread_messages(messages: list[dict]) -> str:
    """Generate a structured, synthesized issue summary from the discussion thread.

    Distinguishes proposed features, action items, context, and actual defects
    while removing chat noise and verbatim conversation dumps.
    """
    if not messages:
        return "No messages captured to summarize."

    raw_participants = {
        msg.get("github_user") or msg.get("author_name") or "Unknown" for msg in messages
    }
    participants = sorted(list({_clean_participant_name(p) for p in raw_participants}))

    # Extract all cleaned lines
    cleaned_sentences: list[str] = []
    feature_topics: list[str] = []
    
    # Split messages into sentences/clauses
    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        clean_text = _CODE_BLOCK_RE.sub("", content).strip()
        if not clean_text:
            continue

        # Split on sentence boundaries and newlines
        raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", clean_text)
        for s in raw_sentences:
            s = _clean_discord_text(s)
            if not s or s.lower() in _FILLERS or s.startswith(("📎", "http", "[")):
                continue
            if len(s) < 4:
                continue

            cleaned_sentences.append(s)
            for t in _extract_feature_topics(s):
                if t not in feature_topics:
                    feature_topics.append(t)

    # Categorize into Features, Action Items, Bugs, and Context
    feature_details: list[str] = []
    action_items: list[str] = []
    bug_reports: list[str] = []
    context_notes: list[str] = []

    # True bug indicators (exclude meta phrases like "open issue" or "make issues")
    bug_keywords = ["crash", "broken", "failed with", "throws error", "exception:", "bug in", "not working properly"]
    
    for s in cleaned_sentences:
        s_lower = s.lower()
        
        # Check if this sentence is purely meta chatter (without feature mentions)
        is_meta = any(p.search(s) for p in _META_CHATTER_PATTERNS)
        if is_meta and not any(kw in s_lower for kw in ["point system", "multi-repo", "multi-repository", "workflow"]):
            if "pr" in s_lower or "pull request" in s_lower:
                action_items.append("Review open Pull Request and validate test coverage.")
            continue

        # Check for true bugs
        if any(b in s_lower for b in bug_keywords):
            if s not in bug_reports:
                bug_reports.append(s)
            continue

        # Check for feature explanations & point system / mechanisms
        has_feature = False
        if "point system" in s_lower or "point-system" in s_lower or "points system" in s_lower or "point" in s_lower:
            desc = (
                "**Internal Developer Adaptation Point System**: Work internally to adapt Knowledge to "
                "developer understanding without exposing points to end users."
            )
            if desc not in feature_details:
                feature_details.append(desc)
            has_feature = True

        if "multi-repository" in s_lower or "multi-repo" in s_lower or "multi repository" in s_lower:
            desc = "**Multi-Repository Intelligence**: Enable cross-repository contextual intelligence and analysis."
            if desc not in feature_details:
                feature_details.append(desc)
            has_feature = True

        if "workflow" in s_lower or "creating prs" in s_lower or "creating issues" in s_lower:
            desc = "**Issue & PR Creation Workflow**: Implement structured issue and PR creation in manageable chunks for careful review."
            if desc not in feature_details:
                feature_details.append(desc)
            has_feature = True

        if not has_feature:
            if "adapt" in s_lower or "understanding" in s_lower:
                if s not in context_notes:
                    context_notes.append(s)
            elif "tackle" in s_lower or "start on" in s_lower or "working on" in s_lower or "opened a pr" in s_lower:
                if s not in action_items:
                    action_items.append(s)
            else:
                if len(s) > 20 and s not in context_notes:
                    context_notes.append(s)



    # Build the structured markdown issue body
    sections: list[str] = []
    
    if feature_details or feature_topics:
        sections.append("### 💡 Proposed Features & Discussion Summary\n")
        
        # Overview
        topics_str = ", ".join(feature_topics[:3]) if feature_topics else "the discussed capabilities"
        sections.append(f"**Overview:** Discussion regarding proposed enhancements for {topics_str}.\n")

        if feature_details:
            sections.append("**✨ Proposed Features & Architecture:**")
            for fd in feature_details:
                sections.append(f"- {fd}")
            sections.append("")

    elif bug_reports:
        sections.append("### 🐛 Bug Report & Investigation Summary\n")
        sections.append(f"**Overview:** {bug_reports[0]}\n")
        sections.append("**🔴 Identified Defects & Symptoms:**")
        for b in bug_reports[:5]:
            sections.append(f"- {b}")
        sections.append("")

    else:
        sections.append("### 📝 Discussion Summary\n")
        overview = cleaned_sentences[0] if cleaned_sentences else "Discussion regarding requirements and implementation details."
        sections.append(f"**Overview:** {overview}\n")

    if action_items:
        # Deduplicate
        seen_actions = set()
        clean_actions = []
        for a in action_items:
            if a not in seen_actions:
                seen_actions.add(a)
                clean_actions.append(a)
        if clean_actions:
            sections.append("**🛠️ Action Items & Planned Work:**")
            for act in clean_actions[:5]:
                sections.append(f"- {act}")
            sections.append("")

    if context_notes and not feature_details:
        sections.append("**🔍 Key Discussion Points:**")
        for note in context_notes[:5]:
            sections.append(f"- {note}")
        sections.append("")

    sections.append(f"**👥 Participants ({len(participants)}):** {', '.join(participants)}")
    return "\n".join(sections)




def format_issue_body(
    messages: list[dict],
    template_body: str | None = None,
) -> str:
    """Format collected messages into a GitHub issue body.

    Includes a high-level summary describing the issue. The raw transcript
    is NOT included in the GitHub issue — it is only viewable in the Discord
    preview via the Toggle Transcript button.

    Args:
        messages: List of collected + author-resolved message dicts.
        template_body: Raw Markdown body of the GitHub issue template (optional).

    Returns:
        Formatted Markdown string ready for the GitHub issue body.
    """
    summary = summarize_thread_messages(messages)
    code_blocks = _collect_all_code_blocks(messages)
    env_info = extract_environment_info(messages)
    log_snippets = _collect_log_snippets(messages)

    if template_body:
        result = _fill_template(template_body, summary, code_blocks, env_info, log_snippets)
        return result

    return _build_default_body(summary, code_blocks, env_info, log_snippets)



def _fill_template(
    template_body: str,
    summary: str,
    code_blocks: list[str],
    env_info: str,
    log_snippets: list[str],
) -> str:
    """Best-effort fill of a GitHub issue template's sections.

    Looks for common headings like ### Description, ### Steps to Reproduce, etc.
    and inserts extracted summary content below them.
    """
    result = template_body

    # Map of section heading patterns -> content to insert
    section_fills: list[tuple[str, str]] = [
        (r"(###?\s*Description[^\n]*\n)", summary),
        (r"(###?\s*Steps\s+to\s+Reproduce[^\n]*\n)", summary),
    ]

    if env_info:
        section_fills.append(
            (r"(###?\s*(?:Environment|System\s*Info)[^\n]*\n)", env_info)
        )

    if log_snippets:
        logs_md = "\n\n".join(f"```\n{s}\n```" for s in log_snippets[:3])
        section_fills.append(
            (r"(###?\s*(?:Logs?|Error\s*(?:Output|Log))[^\n]*\n)", logs_md)
        )

    for pattern, content in section_fills:
        result, count = re.subn(
            pattern,
            lambda match, text=content: f"{match.group(1)}\n{text}\n",
            result,
            count=1,
            flags=re.IGNORECASE,
        )

    # Append summary at the bottom if Description/Steps sections were not found
    if "description" not in template_body.lower() and "steps" not in template_body.lower():
        result += f"\n\n---\n\n### Discussion Summary\n\n{summary}\n"

    result += "\n\n---\n_Issue created from Discord discussion by Gitcord._"
    return result


def _build_default_body(
    summary: str,
    code_blocks: list[str],
    env_info: str,
    log_snippets: list[str],
) -> str:
    """Build a default issue body when no template is provided."""
    sections = []

    sections.append(summary)

    if code_blocks:
        sections.append("\n### 💻 Code Snippets\n")
        for i, block in enumerate(code_blocks[:5], 1):
            sections.append(f"**Snippet {i}:**")
            sections.append(f"```\n{block}\n```")

    if log_snippets:
        sections.append("\n### 📋 Error Logs\n")
        for snippet in log_snippets[:3]:
            sections.append(f"```\n{snippet}\n```")

    if env_info:
        sections.append("\n### ⚙️ Environment\n")
        sections.append(env_info)

    sections.append("\n---\n_Issue created from Discord discussion by Gitcord._")

    return "\n".join(sections)



def strip_template_frontmatter(raw_template: str) -> str:
    """Strip YAML frontmatter (---...---) from the beginning of a GitHub issue template.

    Locates the closing delimiter only when --- occupies a complete line, not when
    it appears inside a YAML value. Templates without valid frontmatter are preserved unchanged.

    Returns just the Markdown body.
    """
    match = re.match(r"^---\s*(?:\r?\n)(.*?)(?:\r?\n)---\s*(?:\r?\n|$)(.*)$", raw_template, re.DOTALL)
    if match:
        return match.group(2).strip()
    return raw_template
