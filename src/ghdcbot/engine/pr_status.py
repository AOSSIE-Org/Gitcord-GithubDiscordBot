"""PR health dashboard: fetch and format PR status for maintainer triage.

Provides at-a-glance health assessment of open PRs including CI status,
CodeRabbit review comments, merge conflicts, and review state.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Default CodeRabbit bot logins (matches notifications.py pattern).
_DEFAULT_CODERABBIT_BOT_LOGINS = ["coderabbitai", "coderabbitai[bot]"]

# Maximum PRs per invocation to stay within Discord interaction timeout.
PR_STATUS_MAX_PRS = 25

# Maximum candidate repositories to probe when auto-detecting repo for a PR.
RESOLVE_REPO_MAX_CANDIDATES = 25

# Maximum concurrent repository probe requests.
RESOLVE_REPO_MAX_CONCURRENCY = 5


def is_repo_allowed(repo_filter: Any, repo_name: str) -> bool:
    """Check whether a repository is permitted by the configured RepoFilterConfig."""
    if not repo_filter:
        return True
    if isinstance(repo_filter, dict):
        names = repo_filter.get("names", [])
        mode = repo_filter.get("mode", "allow")
    else:
        names = getattr(repo_filter, "names", [])
        mode = getattr(repo_filter, "mode", "allow")
    filter_names = {name.strip().lower() for name in names}
    candidate = (repo_name or "").strip().lower()
    if mode == "allow":
        return candidate in filter_names
    if mode == "deny":
        return candidate not in filter_names
    if mode == "deny":
        return repo_name not in filter_names
    return True


def _cfg_get(target: Any, key: str, default: Any = None) -> Any:
    """Retrieve an attribute or dict key from target, returning default if absent or None."""
    if target is None:
        return default
    if isinstance(target, dict):
        val = target.get(key)
    else:
        val = getattr(target, key, None)
    return default if val is None else val


def get_configured_repo_names(config: Any) -> list[str]:
    """Extract repository names configured in Gitcord configuration.

    Collects repository names from:
    1. config.github.repos.names (when mode is 'allow')
    2. config.discord.pr_open_channels (keys)
    3. config.repo_contributor_roles (keys)

    Returns a deduplicated list of repository names preserving configuration order.
    """
    repo_names: list[str] = []
    seen: set[str] = set()

    def _add(name: Any) -> None:
        if isinstance(name, str):
            cleaned = name.strip()
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                repo_names.append(cleaned)

    if not config:
        return repo_names

    # 1. github.repos.names (allow mode)
    github_cfg = _cfg_get(config, "github")
    if github_cfg:
        repos_cfg = _cfg_get(github_cfg, "repos")
        if repos_cfg:
            if isinstance(repos_cfg, (list, tuple)):
                for r in repos_cfg:
                    _add(r)
            else:
                mode = _cfg_get(repos_cfg, "mode", "allow") or "allow"
                names = _cfg_get(repos_cfg, "names")

                if mode == "allow" and isinstance(names, (list, tuple)):
                    for r in names:
                        _add(r)

    # 2. discord.pr_open_channels
    discord_cfg = _cfg_get(config, "discord")
    if discord_cfg:
        pr_open_channels = _cfg_get(discord_cfg, "pr_open_channels")
        if isinstance(pr_open_channels, dict):
            for r in pr_open_channels:
                _add(r)

    # 3. repo_contributor_roles
    contributor_roles = _cfg_get(config, "repo_contributor_roles")
    if isinstance(contributor_roles, dict):
        for r in contributor_roles:
            _add(r)

    return repo_names


def filter_repo_suggestions(configured_repos: list[str], current: str) -> list[str]:
    """Filter configured repositories by prefix/substring match, up to 25 items."""
    curr = (current or "").strip().lower()
    if not curr:
        return configured_repos[:25]
    prefix_matches = [r for r in configured_repos if r.lower().startswith(curr)]
    substring_matches = [
        r for r in configured_repos if curr in r.lower() and not r.lower().startswith(curr)
    ]
    return (prefix_matches + substring_matches)[:25]


async def resolve_repo_for_pr(
    config: Any,
    github_adapter: Any,
    pr_number: int,
    repo: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve repository name for a PR status query.

    If ``repo`` is specified:
      - Validates against configured repo filters.
      - Returns (repo_name, None) or (None, error_message).

    If ``repo`` is omitted:
      - Reads configured repositories from Gitcord config.
      - If exactly one repo is configured, uses it directly (zero overhead).
      - If multiple repos are configured, scans them concurrently for ``pr_number``.
        - Exactly one match: uses that repo.
        - Multiple matches: asks user to disambiguate.
        - No matches: informs user which configured repos were checked.
      - If no repos are configured, prompts user to provide ``repo``.

    Returns:
      (repo_name, error_message)
    """
    repo_filter = None
    if config:
        github_cfg = _cfg_get(config, "github")
        if github_cfg:
            repo_filter = _cfg_get(github_cfg, "repos")

    # Case 1: User explicitly provided repo
    if repo and repo.strip():
        cleaned = repo.strip()
        if not is_repo_allowed(repo_filter, cleaned):
            return None, f"❌ Repository **{cleaned}** is not allowed by Gitcord configuration."
        return cleaned, None

    # Case 2: Auto-detect from Gitcord config
    configured_repos = get_configured_repo_names(config)
    if not configured_repos:
        return (
            None,
            "❌ Please specify `repo` (no allowed repositories found in Gitcord configuration).",
        )

    if len(configured_repos) == 1:
        return configured_repos[0], None

    # Multiple repos configured: check which one contains this PR number
    org = ""
    if config:
        github_cfg = _cfg_get(config, "github")
        if github_cfg:
            org = _cfg_get(github_cfg, "org", "") or ""

    candidates = configured_repos[:RESOLVE_REPO_MAX_CANDIDATES]
    semaphore = asyncio.Semaphore(RESOLVE_REPO_MAX_CONCURRENCY)

    async def _check_repo(candidate: str) -> bool:
        async with semaphore:
            try:
                get_pr = getattr(github_adapter, "get_pull_request", None)
                if not callable(get_pr):
                    return False
                pr = await asyncio.to_thread(get_pr, org, candidate, int(pr_number))
                return bool(pr)
            except Exception:
                return False

    results = await asyncio.gather(*[_check_repo(r) for r in candidates])
    matching = [r for r, ok in zip(candidates, results) if ok]

    if len(matching) == 1:
        return matching[0], None

    if len(matching) > 1:
        repo_list = ", ".join(f"`{r}`" for r in matching)
        return (
            None,
            (
                f"❌ Found PR **#{pr_number}** in multiple configured repositories: {repo_list}. "
                "Please specify which repository using `repo:<name>`."
            ),
        )

    repo_list = ", ".join(f"`{r}`" for r in candidates)
    return (
        None,
        (
            f"❌ PR **#{pr_number}** not found in configured repositories ({repo_list}). "
            "Please check the PR number or specify the repository with `repo:<name>`."
        ),
    )


@dataclass(frozen=True)
class PRHealthStatus:
    """Health assessment for a single pull request."""

    repo: str
    number: int
    title: str
    author: str
    html_url: str
    ci_status: str  # "passing" | "failing" | "pending" | "unknown"
    mergeable: bool | None  # True | False | None (unknown)
    review_state: str  # "approved" | "changes_requested" | "review_comment" | "awaiting_review"
    approved_count: int
    changes_requested_count: int
    has_coderabbit_comments: bool
    coderabbit_comment_count: int
    is_draft: bool
    state: str = "open"  # "open" | "merged" | "closed"

    @property
    def health_indicator(self) -> str:
        """Compute overall health: safe_to_merge | needs_attention | blocked | draft | merged | closed."""
        if self.state == "merged":
            return "merged"
        if self.state == "closed":
            return "closed"
        if self.is_draft:
            return "draft"
        # Blocked conditions: CI failing or merge conflicts
        if self.ci_status == "failing":
            return "blocked"
        if self.mergeable is False:
            return "blocked"
        if self.review_state == "changes_requested":
            return "blocked"
        # Safe to merge: CI passing/unknown, approved, mergeable, no CodeRabbit
        if (
            self.review_state == "approved"
            and self.ci_status in ("passing", "unknown")
            and self.mergeable is not False
            and not self.has_coderabbit_comments
        ):
            return "safe_to_merge"
        # Everything else needs attention
        return "needs_attention"


def fetch_pr_health(
    github_adapter: Any,
    owner: str,
    repo: str,
    pr_number: int,
    coderabbit_bot_logins: list[str] | None = None,
) -> PRHealthStatus | None:
    """Fetch and compute health status for a single PR.

    Returns None if the PR does not exist or is inaccessible.
    """
    pr = github_adapter.get_pull_request(owner, repo, pr_number)
    if not pr:
        return None

    pr_state = (pr.get("state") or "open").strip().lower()
    is_merged = bool(pr.get("merged") or pr.get("merged_at"))
    if is_merged or pr_state == "merged":
        state = "merged"
    elif pr_state == "closed":
        state = "closed"
    else:
        state = "open"

    user = pr.get("user") or {}

    # If merged or closed, return immediately without querying check runs, threads, reviews
    if state in ("merged", "closed"):
        return PRHealthStatus(
            repo=repo,
            number=pr_number,
            title=(pr.get("title") or "Untitled").strip(),
            author=user.get("login") or "unknown",
            html_url=pr.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{pr_number}",
            ci_status="unknown",
            mergeable=None,
            review_state="unknown",
            approved_count=0,
            changes_requested_count=0,
            has_coderabbit_comments=False,
            coderabbit_comment_count=0,
            is_draft=False,
            state=state,
        )

    # Reviews (separate human reviews and bot reviews)
    bot_logins = coderabbit_bot_logins or _DEFAULT_CODERABBIT_BOT_LOGINS
    bot_logins_lower = [x.strip().lower() for x in bot_logins if x]

    reviews = github_adapter.get_pull_request_reviews(owner, repo, pr_number)
    human_reviews = [
        r for r in reviews
        if ((r.get("user") or {}).get("login") or "").strip().lower() not in bot_logins_lower
    ]
    coderabbit_reviews = [
        r for r in reviews
        if ((r.get("user") or {}).get("login") or "").strip().lower() in bot_logins_lower
    ]

    approved_count = sum(
        1 for r in human_reviews if (r.get("state") or "").upper() == "APPROVED"
    )
    changes_requested_count = sum(
        1 for r in human_reviews if (r.get("state") or "").upper() == "CHANGES_REQUESTED"
    )
    comment_review_count = sum(
        1
        for r in human_reviews
        if (r.get("state") or "").upper() in ("COMMENT", "COMMENTED")
    )

    if changes_requested_count > 0:
        review_state = "changes_requested"
    elif approved_count > 0:
        review_state = "approved"
    elif comment_review_count > 0:
        review_state = "review_comment"
    else:
        review_state = "awaiting_review"

    # CI status from check runs
    head_sha = (pr.get("head") or {}).get("sha")
    ci_status = "unknown"
    if head_sha:
        check_runs = github_adapter.get_pull_request_check_runs(
            owner, repo, head_sha
        )
        ci_status = _compute_ci_status(check_runs)

    # Check if latest CodeRabbit review is APPROVED
    coderabbit_approved = False
    if coderabbit_reviews:
        # Latest review from CodeRabbit
        latest_cr_review = coderabbit_reviews[-1]
        if (latest_cr_review.get("state") or "").upper() == "APPROVED":
            coderabbit_approved = True

    # CodeRabbit inline review comments & unresolved review threads
    coderabbit_count = 0
    if not coderabbit_approved:
        # First check GraphQL review threads (returns list of threads if supported/available, None on error)
        get_threads = getattr(github_adapter, "get_pull_request_review_threads", None)
        raw_threads = None
        if callable(get_threads):
            try:
                raw_threads = get_threads(owner, repo, pr_number)
            except Exception as exc:
                logger.debug(
                    "Failed to fetch review threads for PR health",
                    extra={"repo": repo, "pr_number": pr_number, "error": str(exc)},
                )
                raw_threads = None
        if isinstance(raw_threads, list):
            for t in raw_threads:
                if isinstance(t, dict):
                    authors = t.get("authors") or []
                    if any(a in bot_logins_lower for a in authors):
                        if not t.get("is_resolved") and not t.get("is_outdated"):
                            coderabbit_count += 1
        else:
            # Fallback to REST review comments if GraphQL is unavailable or encountered an error
            get_comments = getattr(github_adapter, "get_pull_request_review_comments", None)
            if callable(get_comments):
                try:
                    comments = get_comments(owner, repo, pr_number)
                    if isinstance(comments, list):
                        coderabbit_count = sum(
                            1
                            for c in comments
                            if isinstance(c, dict)
                            and _is_active_coderabbit_comment(c, bot_logins_lower, head_sha)
                        )
                except Exception as exc:
                    logger.debug(
                        "Failed to fetch review comments for PR health",
                        extra={"repo": repo, "pr_number": pr_number, "error": str(exc)},
                    )

    user = pr.get("user") or {}
    return PRHealthStatus(
        repo=repo,
        number=pr_number,
        title=(pr.get("title") or "Untitled").strip(),
        author=user.get("login") or "unknown",
        html_url=pr.get("html_url") or f"https://github.com/{owner}/{repo}/pull/{pr_number}",
        ci_status=ci_status,
        mergeable=pr.get("mergeable"),
        review_state=review_state,
        approved_count=approved_count,
        changes_requested_count=changes_requested_count,
        has_coderabbit_comments=coderabbit_count > 0,
        coderabbit_comment_count=coderabbit_count,
        is_draft=bool(pr.get("draft", False)),
        state="open",
    )


def fetch_all_open_pr_health(
    github_adapter: Any,
    org: str,
    coderabbit_bot_logins: list[str] | None = None,
    max_prs: int = PR_STATUS_MAX_PRS,
    skip: int = 0,
) -> tuple[list[PRHealthStatus], int]:
    """Fetch health for all open PRs in the configured org.

    Returns (health_list, total_open_count).
    """
    all_open_prs = list(github_adapter.list_open_pull_requests())
    total = len(all_open_prs)

    # Apply pagination
    page_prs = all_open_prs[skip : skip + max_prs]

    results: list[PRHealthStatus] = []
    for pr in page_prs:
        repo = pr.get("repo")
        number = pr.get("number")
        if not repo or number is None:
            continue
        try:
            health = fetch_pr_health(
                github_adapter, org, repo, number, coderabbit_bot_logins
            )
            if health is not None:
                results.append(health)
        except Exception as exc:
            logger.warning(
                "Failed to fetch health for PR",
                extra={"repo": repo, "number": number, "error": str(exc)},
            )
    return results, total


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

_HEALTH_EMOJI = {
    "safe_to_merge": "🟢",
    "needs_attention": "🟡",
    "blocked": "🔴",
    "draft": "⬜",
}

_CI_EMOJI = {
    "passing": "✅",
    "failing": "❌",
    "pending": "⏳",
    "unknown": "❓",
}

_CI_LABEL = {
    "passing": "Passing",
    "failing": "Failing",
    "pending": "In Progress",
    "unknown": "Unknown",
}

_REVIEW_EMOJI = {
    "approved": "✅",
    "changes_requested": "🔄",
    "review_comment": "💬",
    "awaiting_review": "⏳",
}

_REVIEW_LABEL = {
    "approved": "Approved",
    "changes_requested": "Changes Requested",
    "review_comment": "Comment Review",
    "awaiting_review": "Awaiting Review",
}

_HEALTH_LABEL = {
    "safe_to_merge": "🟢 Safe to Merge",
    "needs_attention": "🟡 Needs Attention",
    "blocked": "🔴 Blocked",
    "draft": "⬜ Draft",
    "merged": "🟣 Merged",
    "closed": "🔴 Closed",
}

# Sort priority: blocked first (maintainer needs to know what's stuck), then
# needs_attention, then safe_to_merge, then drafts last.
_HEALTH_SORT_ORDER = {
    "blocked": 0,
    "needs_attention": 1,
    "safe_to_merge": 2,
    "draft": 3,
}


def format_single_pr_status(status: PRHealthStatus, org: str) -> str:
    """Format a detailed health report for a single PR."""
    lines: list[str] = []

    # Header
    lines.append(f"**{status.repo}#{status.number}** — {_truncate(status.title, 80)}")
    lines.append(f"👤 **Author:** {status.author}")
    lines.append("")

    # If merged or closed, just show that it's Merged or Closed and nothing on it
    if status.state == "merged":
        lines.append("🟣 **Status:** Merged")
        return "\n".join(lines)

    if status.state == "closed":
        lines.append("🔴 **Status:** Closed")
        return "\n".join(lines)

    # CI
    ci_emoji = _CI_EMOJI.get(status.ci_status, "❓")
    ci_label = _CI_LABEL.get(status.ci_status, "Unknown")
    lines.append(f"{ci_emoji} **CI:** {ci_label}")

    # CodeRabbit
    if status.has_coderabbit_comments:
        lines.append(
            f"🟡 **CodeRabbit:** {status.coderabbit_comment_count} suggestion"
            f"{'s' if status.coderabbit_comment_count != 1 else ''} pending"
        )
    else:
        lines.append("✅ **CodeRabbit:** No pending suggestions")

    # Merge conflicts
    if status.mergeable is False:
        lines.append("⚠️ **Merge Conflicts:** Yes")
    elif status.mergeable is True:
        lines.append("✅ **Mergeable:** Yes")
    else:
        lines.append("❓ **Mergeable:** Unknown")

    # Reviews
    review_emoji = _REVIEW_EMOJI.get(status.review_state, "❓")
    review_label = _REVIEW_LABEL.get(status.review_state, "Unknown")
    review_detail = review_label
    if status.approved_count > 0:
        review_detail += f" ({status.approved_count} approval{'s' if status.approved_count != 1 else ''})"
    if status.changes_requested_count > 0:
        review_detail += f" ({status.changes_requested_count} change request{'s' if status.changes_requested_count != 1 else ''})"
    lines.append(f"{review_emoji} **Review:** {review_detail}")

    # Draft
    if status.is_draft:
        lines.append("📝 **Draft:** Yes")

    lines.append("")
    lines.append("──────────")
    health_label = _HEALTH_LABEL.get(status.health_indicator, "Unknown")
    lines.append(f"📊 **Health:** {health_label}")

    return "\n".join(lines)


def format_all_pr_status(
    statuses: list[PRHealthStatus],
    org: str,
    skip: int = 0,
    total: int = 0,
) -> list[str]:
    """Format a triage-priority sorted summary for multiple PRs.

    Returns a list of message strings, each ≤2000 chars (Discord limit).
    """
    if not statuses:
        if total == 0:
            return ["📋 **PR Status Dashboard**\n\nNo open PRs found in configured repos."]
        return [
            f"📋 **PR Status Dashboard**\n\n"
            f"No PRs in range (skip={skip}, total={total}). "
            f"Try `/pr-status show_all:True` without skip."
        ]

    # Sort by triage priority
    sorted_statuses = sorted(
        statuses,
        key=lambda s: (_HEALTH_SORT_ORDER.get(s.health_indicator, 99), s.repo, s.number),
    )

    # Count by health
    counts = {"safe_to_merge": 0, "needs_attention": 0, "blocked": 0, "draft": 0}
    for s in sorted_statuses:
        counts[s.health_indicator] = counts.get(s.health_indicator, 0) + 1

    # Build header
    header_lines = [
        "📋 **PR Status Dashboard**",
        "",
        f"Showing {len(statuses)} of {total} open PR{'s' if total != 1 else ''}",
    ]
    summary_parts = []
    if counts["blocked"] > 0:
        summary_parts.append(f"🔴 {counts['blocked']} blocked")
    if counts["needs_attention"] > 0:
        summary_parts.append(f"🟡 {counts['needs_attention']} need attention")
    if counts["safe_to_merge"] > 0:
        summary_parts.append(f"🟢 {counts['safe_to_merge']} safe to merge")
    if counts["draft"] > 0:
        summary_parts.append(f"⬜ {counts['draft']} draft")
    if summary_parts:
        header_lines.append(" · ".join(summary_parts))
    header_lines.append("")

    header = "\n".join(header_lines)

    # Build PR lines
    pr_lines: list[str] = []
    for s in sorted_statuses:
        health_emoji = _HEALTH_EMOJI.get(s.health_indicator, "❓")
        detail_parts = []

        # CI
        ci_emoji = _CI_EMOJI.get(s.ci_status, "❓")
        detail_parts.append(f"CI {ci_emoji}")

        # Reviews
        if s.review_state == "approved":
            detail_parts.append("Reviews ✅")
        elif s.review_state == "changes_requested":
            detail_parts.append("Reviews: changes requested")
        elif s.review_state == "awaiting_review":
            detail_parts.append("Reviews: awaiting")
        else:
            detail_parts.append("Reviews: comment")

        # Mergeable
        if s.mergeable is False:
            detail_parts.append("Conflicts ⚠️")
        elif s.mergeable is True:
            detail_parts.append("Mergeable ✅")

        # CodeRabbit
        if s.has_coderabbit_comments:
            detail_parts.append(f"CodeRabbit: {s.coderabbit_comment_count} pending")

        # Draft
        if s.is_draft:
            detail_parts.append("Draft")

        detail = " · ".join(detail_parts)
        title = _truncate(s.title, 50)
        pr_lines.append(
            f"{health_emoji} **{s.repo}#{s.number}** — \"{title}\" ({detail})"
        )

    # Pagination footer
    footer_lines: list[str] = []
    shown_end = skip + len(statuses)
    if shown_end < total:
        footer_lines.append("")
        footer_lines.append(
            f"*…and {total - shown_end} more. "
            f"Use `/pr-status show_all:True skip:{shown_end}` to see next page.*"
        )

    # Split into ≤2000 char messages
    return _split_messages(header, pr_lines, footer_lines, max_length=2000)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_ci_status(check_runs: list[dict]) -> str:
    """Determine CI status from GitHub check runs (same logic as pr_context.py)."""
    if not check_runs:
        return "unknown"

    conclusions = [(cr.get("conclusion") or "").lower() for cr in check_runs]
    statuses = [(cr.get("status") or "").lower() for cr in check_runs]

    if any(c == "failure" for c in conclusions):
        return "failing"
    if any(c == "success" for c in conclusions) and not any(
        c == "failure" for c in conclusions
    ):
        return "passing"
    if any(s in ("in_progress", "queued") for s in statuses):
        return "pending"
    return "unknown"


def _is_coderabbit_bot(comment: dict, bot_logins_lower: list[str]) -> bool:
    """True if comment is from a configured CodeRabbit bot login."""
    user = comment.get("user") or {}
    login = (user.get("login") or "").strip().lower()
    return bool(login and login in bot_logins_lower)


def _is_active_coderabbit_comment(
    comment: dict,
    bot_logins_lower: list[str],
    head_sha: str | None = None,
) -> bool:
    """True if comment is from CodeRabbit and is still active/unresolved on the current PR HEAD.

    A CodeRabbit comment is considered resolved/inactive if:
    - It is not from CodeRabbit
    - It is marked outdated by GitHub (position is None while original_position was set)
    - It was on an older commit that has been superseded by a new HEAD commit
    - Its content indicates it was resolved (checked checkbox [x] or struck-through suggestion ~~)
    """
    if not _is_coderabbit_bot(comment, bot_logins_lower):
        return False

    # Outdated diff check: GitHub sets position to null when code changes supersede it
    if comment.get("position") is None and comment.get("original_position") is not None:
        return False

    # Superseded commit check: if head_sha is known and comment was made on an earlier commit
    comment_commit = comment.get("commit_id")
    if head_sha and comment_commit and comment_commit != head_sha:
        return False

    # Check if comment has explicit resolved flag or status
    if comment.get("resolved") is True or comment.get("is_resolved") is True:
        return False

    # Body check: if CodeRabbit marked the item resolved
    body = (comment.get("body") or "").strip()
    if "[x]" in body and "[ ]" not in body:
        # All checklist items were resolved
        return False

    return True


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if too long."""
    if len(text) <= max_len:
        return text
    return f"{text[: max_len - 3]}..."


def _split_messages(
    header: str,
    lines: list[str],
    footer_lines: list[str],
    max_length: int = 2000,
) -> list[str]:
    """Split content into messages each ≤max_length chars."""
    messages: list[str] = []
    current = header

    for line in lines:
        candidate = current + "\n" + line if current else line
        if len(candidate) > max_length and current:
            messages.append(current)
            current = line
        else:
            current = candidate

    # Append footer to last chunk
    footer = "\n".join(footer_lines) if footer_lines else ""
    if footer:
        candidate = current + "\n" + footer if current else footer
        if len(candidate) > max_length and current:
            messages.append(current)
            current = footer
        else:
            current = candidate

    if current:
        messages.append(current)

    return messages if messages else [header]
