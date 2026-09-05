"""Discord thread/conversation → GitHub issue formatter (deterministic, no AI)."""

from __future__ import annotations

import logging
import re
from datetime import datetime
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
    r"^(?:hey(?:\s+guys|\s+everyone|\s+all)?|hi(?:\s+guys|\s+everyone|\s+all)?|hello(?:\s+guys|\s+everyone|\s+all)?|good\s+(?:morning|afternoon|evening)|yo|anyone(?:\s+knows?)?)[\s!.,:;\-]+",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(
    r"^(?:hey(?:\s+guys|\s+everyone|\s+all)?|hi(?:\s+guys|\s+everyone|\s+all)?|hello(?:\s+guys|\s+everyone|\s+all)?|good\s+(?:morning|afternoon|evening)|yo|welcome)[\s!.,:;\-]*$",
    re.IGNORECASE,
)

_FILLERS = {
    "ok", "okay", "yes", "yeah", "yep", "no", "nope", "thanks", "thank you", "thx", "ty",
    "done", "cool", "great", "nice", "sounds good", "lgtm", "sure", "got it", "i see",
    "will do", "working on it", "see once more", "try now", "restarting", "fixed",
    "np", "no problem", "k", "+1", "agree", "perfect",
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


def generate_issue_title(messages: list[dict]) -> str:
    """Generate an issue title from the first substantive message in the thread.

    Deterministic: uses the first non-filler sentence from cleaned message text.
    No keyword synthesis or invented architecture blurbs.
    """
    for msg in messages:
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        stripped = _CODE_BLOCK_RE.sub("", content).strip()
        cleaned = _clean_discord_text(stripped)
        if not cleaned:
            continue
        first_line = cleaned.split("\n")[0].strip()
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
            clean_content = _clean_discord_text(content)
            lines.append(clean_content if clean_content else content)

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



def _is_noise(text: str) -> bool:
    """Return True if text is empty, a filler/acknowledgement, command, greeting, or reaction."""
    clean = _clean_discord_text(text).strip()
    if not clean or len(clean) < 3:
        return True
    if clean.startswith(("/", "!", ".", "$", "?", "\\")):
        return True
    if _GREETING_RE.match(clean) or _GREETING_RE.match(text.strip()):
        return True
    lower = clean.lower().rstrip(".!?,:; ")
    if lower in _FILLERS or lower in ("everyone", "all", "guys", "hey everyone", "good morning"):
        return True
    return bool(re.fullmatch(r"[\s\W_]+", clean))


def _extract_overview(messages: list[dict]) -> str:
    """Extract opening problem statement or discussion topic from early messages."""
    for msg in messages:
        content = msg.get("content", "")
        if not content:
            continue
        no_code = _CODE_BLOCK_RE.sub("", content).strip()
        cleaned = _clean_discord_text(no_code)
        if _is_noise(cleaned):
            continue
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        substantive = [l for l in lines if not _is_noise(l)]
        if not substantive:
            continue
        candidate = " ".join(substantive[:2]).strip()
        if len(candidate) > 240:
            candidate = candidate[:237] + "..."
        return candidate
    return ""


def _extract_participant_key_points(messages: list[dict]) -> list[tuple[str, list[str]]]:
    """Extract substantive takeaway statements grouped by participant."""
    author_points: dict[str, list[str]] = {}
    seen_points: set[str] = set()

    for msg in messages:
        author = _clean_participant_name(msg.get("github_user") or msg.get("author_name") or "Unknown")
        content = msg.get("content", "")
        if not content:
            continue
        no_code = _CODE_BLOCK_RE.sub("", content).strip()
        cleaned = _clean_discord_text(no_code)
        if _is_noise(cleaned):
            continue

        sentences = re.split(r"(?<=[.!?])\s+|\n+", cleaned)
        for s in sentences:
            s_clean = s.strip().strip("•-* ")
            if len(s_clean) < 18 or _is_noise(s_clean):
                continue
            norm = re.sub(r"\s+", " ", s_clean.lower())
            if norm in seen_points:
                continue
            seen_points.add(norm)

            if len(s_clean) > 220:
                s_clean = s_clean[:217] + "..."

            author_points.setdefault(author, []).append(s_clean)

    result: list[tuple[str, list[str]]] = []
    for author, points in author_points.items():
        if points:
            result.append((author, points[:2]))
    return result


def _extract_identified_errors(messages: list[dict]) -> list[str]:
    """Scan messages and code snippets for explicit error messages or stacktraces."""
    errors: list[str] = []
    seen: set[str] = set()

    error_line_res = [
        re.compile(r"(?:^|\n)\s*((?:[A-Z]\w*(?:Error|Exception|Crash|Fault)|Error|Exception)\s*[:\-]\s*[^\n]+)", re.IGNORECASE),
        re.compile(r"(?:^|\n)\s*([^\n]*(?:failed\s+with|exit\s+code\s+\d+|status\s+code\s+[45]\d\d|HTTP\s+[45]\d\d)[^\n]*)", re.IGNORECASE),
        re.compile(r"(?:^|\n)\s*((?:ERROR|CRITICAL|FATAL)\s*[:\-]\s*[^\n]+)", re.IGNORECASE),
    ]

    def _check_text(text: str) -> None:
        for reg in error_line_res:
            for match in reg.finditer(text):
                err_str = match.group(1).strip()
                norm = err_str.lower()
                if norm not in seen and len(err_str) > 5:
                    seen.add(norm)
                    if len(err_str) > 120:
                        err_str = err_str[:117] + "..."
                    errors.append(err_str)

    for msg in messages:
        content = msg.get("content", "")
        if content:
            _check_text(content)
        for code in msg.get("code_blocks", []):
            _check_text(code)

    return errors[:4]


_ACTION_ITEM_PATTERNS = [
    re.compile(r"\b(?:i\s+will|i'll|i\s+am\s+going\s+to|we\s+will|we'll)\s+([^.!?\n]+)", re.IGNORECASE),
    re.compile(r"\b(?:we\s+(?:need\s+to|should|must))\s+([^.!?\n]+)", re.IGNORECASE),
    re.compile(r"\b(?:let's|lets)\s+([^.!?\n]+)", re.IGNORECASE),
    re.compile(r"\b(?:todo|action\s+item)s?\s*[:\-]\s*([^.!?\n]+)", re.IGNORECASE),
    re.compile(r"\b(?:please\s+(?:assign|check|review|open|fix|update|merge|create))\s+([^.!?\n]+)", re.IGNORECASE),
    re.compile(r"\b(?:working\s+on|pushed\s+changes|opened\s+a?\s*pr)\s*([^.!?\n]*)", re.IGNORECASE),
]


def _extract_action_items(messages: list[dict]) -> list[tuple[str, str]]:
    """Scan messages for committed action items and next steps."""
    action_items: list[tuple[str, str]] = []
    seen: set[str] = set()

    for msg in messages:
        author = _clean_participant_name(msg.get("github_user") or msg.get("author_name") or "Unknown")
        content = msg.get("content", "")
        if not content:
            continue
        no_code = _CODE_BLOCK_RE.sub("", content).strip()
        cleaned = _clean_discord_text(no_code)

        sentences = re.split(r"(?<=[.!?])\s+|\n+", cleaned)
        for s in sentences:
            s_clean = s.strip().strip("•-* ")
            if len(s_clean) < 12:
                continue
            for pattern in _ACTION_ITEM_PATTERNS:
                if pattern.search(s_clean):
                    norm = re.sub(r"\s+", " ", s_clean.lower())
                    if norm not in seen:
                        seen.add(norm)
                        if len(s_clean) > 200:
                            s_clean = s_clean[:197] + "..."
                        action_items.append((author, s_clean))
                    break
    return action_items[:6]


_URL_RE = re.compile(r"https?://[^\s\)]+")
_PR_ISSUE_RE = re.compile(r"\b(?:PR\s*#?|issue\s*#?|#)(\d+)\b", re.IGNORECASE)


def _extract_references(messages: list[dict]) -> list[str]:
    """Collect shared URLs and GitHub issue/PR mentions."""
    refs: list[str] = []
    seen: set[str] = set()

    for msg in messages:
        content = msg.get("content", "")
        if not content:
            continue
        for url in _URL_RE.findall(content):
            clean_url = url.rstrip(".,;!?>)")
            norm = clean_url.lower()
            if norm not in seen:
                seen.add(norm)
                refs.append(clean_url)
        for match in _PR_ISSUE_RE.finditer(content):
            num = match.group(1)
            ref_str = f"#{num}"
            if ref_str not in seen:
                seen.add(ref_str)
                refs.append(ref_str)

    return refs[:5]


def summarize_thread_messages(messages: list[dict]) -> str:
    """Build a structured, deterministic issue summary from thread messages.

    Emits only real data extracted from messages:
    - Overview of the discussion topic
    - Key discussion points grouped by participant
    - Identified errors and defects (if any)
    - Action items and next steps (if any)
    - Referenced links and issues (if any)
    - Participants list
    - Cleaned conversation transcript
    """
    if not messages:
        return "No messages captured to summarize."

    raw_participants = {
        msg.get("github_user") or msg.get("author_name") or "Unknown" for msg in messages
    }
    participants = sorted({_clean_participant_name(p) for p in raw_participants})

    overview = _extract_overview(messages)
    key_points = _extract_participant_key_points(messages)
    errors = _extract_identified_errors(messages)
    action_items = _extract_action_items(messages)
    references = _extract_references(messages)

    sections: list[str] = []

    summary_parts: list[str] = ["### 📋 Discussion Summary\n"]
    if overview:
        summary_parts.append(f"**Overview:** {overview}\n")

    if key_points:
        summary_parts.append("**Key Discussion Points:**")
        for author, points in key_points:
            joined = " ".join(points)
            summary_parts.append(f"- **{author}:** {joined}")
        summary_parts.append("")

    if errors:
        summary_parts.append("**Identified Errors & Defects:**")
        for err in errors:
            summary_parts.append(f"- `{err}`")
        summary_parts.append("")

    if action_items:
        summary_parts.append("**Action Items & Next Steps:**")
        for author, item in action_items:
            summary_parts.append(f"- **{author}:** {item}")
        summary_parts.append("")

    if references:
        summary_parts.append("**Referenced Links & Issues:**")
        for ref in references:
            summary_parts.append(f"- {ref}")
        summary_parts.append("")

    sections.append("\n".join(summary_parts).strip())
    sections.append(f"\n**👥 Participants ({len(participants)}):** {', '.join(participants)}\n")
    sections.append("### 💬 Thread Transcript\n")
    sections.append(_format_transcript(messages))

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
        result, _count = re.subn(
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
