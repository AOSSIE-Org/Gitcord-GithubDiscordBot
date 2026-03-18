"""GitHub-backed JSON snapshot writer for Gitcord state.

This module writes periodic snapshots of Gitcord state to GitHub repos as JSON files.
Snapshots are append-only, timestamped, and designed for consumption by Org Explorer.

Architecture:
- Snapshots are written AFTER run-once completes successfully
- Each snapshot is a timestamped directory containing JSON files
- Never overwrites previous snapshots
- Fails gracefully if GitHub write fails (does not block run-once)
- SQLite remains active; snapshots are additive audit output
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ghdcbot.config.models import BotConfig, IdentityMapping
from ghdcbot.core.models import ContributionEvent, ContributionSummary, Score

logger = logging.getLogger("Snapshots")

# Snapshot schema version (increment when breaking changes)
SCHEMA_VERSION = "1.0.0"


def write_snapshots_to_github(
    storage: Any,
    config: BotConfig,
    github_writer: Any,
    identity_mappings: list[IdentityMapping],
    scores: list[Score],
    member_roles: dict[str, list[str]],
    period_start: datetime,
    period_end: datetime,
    contribution_summaries: list[ContributionSummary] | None = None,
) -> None:
    """Write Gitcord state snapshots to GitHub repo as JSON files.
    
    This is ADDITIVE only - writes snapshots without affecting SQLite or existing behavior.
    Fails gracefully if GitHub write fails (logs error, does not raise).
    
    Args:
        storage: Storage adapter (for reading data)
        config: Bot configuration
        github_writer: GitHub writer adapter (for committing files)
        identity_mappings: Verified identity mappings
        scores: Current scores
        member_roles: Discord member roles mapping
        period_start: Scoring period start
        period_end: Scoring period end
        contribution_summaries: Optional contribution summaries for the period
    """
    snapshot_config = getattr(config, "snapshots", None)
    if not snapshot_config or not snapshot_config.enabled:
        return
    
    try:
        _write_snapshots(
            storage=storage,
            config=config,
            github_writer=github_writer,
            snapshot_config=snapshot_config,
            identity_mappings=identity_mappings,
            scores=scores,
            member_roles=member_roles,
            period_start=period_start,
            period_end=period_end,
            contribution_summaries=contribution_summaries,
        )
    except Exception as exc:
        # Never block run-once completion
        logger.warning(
            "Failed to write GitHub snapshots (non-blocking)",
            exc_info=True,
            extra={"error": str(exc), "org": config.github.org},
        )


def _write_snapshots(
    storage: Any,
    config: BotConfig,
    github_writer: Any,
    snapshot_config: Any,
    identity_mappings: list[IdentityMapping],
    scores: list[Score],
    member_roles: dict[str, list[str]],
    period_start: datetime,
    period_end: datetime,
    contribution_summaries: list[ContributionSummary] | None,
) -> None:
    """Internal snapshot writing logic."""
    now = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    
    # Build snapshot directory path: snapshots/YYYY-MM-DDTHH-MM-SS-runid/
    timestamp_str = now.strftime("%Y-%m-%dT%H-%M-%S")
    snapshot_dir = f"snapshots/{timestamp_str}-{run_id[:8]}"
    
    # Collect all snapshot data
    snapshot_data = _collect_snapshot_data(
        storage=storage,
        config=config,
        identity_mappings=identity_mappings,
        scores=scores,
        member_roles=member_roles,
        period_start=period_start,
        period_end=period_end,
        contribution_summaries=contribution_summaries,
        run_id=run_id,
        generated_at=now,
    )
    
    # Write each snapshot file to GitHub
    owner, repo = _parse_repo_path(snapshot_config.repo_path)
    commit_message = f"Gitcord snapshot: {timestamp_str} (run: {run_id[:8]})"
    branch = snapshot_config.branch
    
    files_written = 0
    for filename, content in snapshot_data.items():
        file_path = f"{snapshot_dir}/{filename}"
        if _write_file_to_github(github_writer, owner, repo, file_path, content, commit_message, branch=branch):
            files_written += 1
    
    if files_written > 0:
        logger.info(
            "GitHub snapshots written",
            extra={
                "org": config.github.org,
                "repo": f"{owner}/{repo}",
                "snapshot_dir": snapshot_dir,
                "files": files_written,
            },
        )
        # Audit log
        append_audit = getattr(storage, "append_audit_event", None)
        if callable(append_audit):
            append_audit({
                "event_type": "snapshot_written",
                "context": {
                    "org": config.github.org,
                    "repo": f"{owner}/{repo}",
                    "snapshot_dir": snapshot_dir,
                    "run_id": run_id,
                    "files_written": files_written,
                    "timestamp": now.isoformat(),
                },
            })


def _collect_snapshot_data(
    storage: Any,
    config: BotConfig,
    identity_mappings: list[IdentityMapping],
    scores: list[Score],
    member_roles: dict[str, list[str]],
    period_start: datetime,
    period_end: datetime,
    contribution_summaries: list[ContributionSummary] | None,
    run_id: str,
    generated_at: datetime,
) -> dict[str, dict[str, Any]]:
    """Collect all snapshot data into structured dictionaries."""
    org = config.github.org
    
    # Meta snapshot
    meta = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "org": org,
        "run_id": run_id,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }
    
    # Identities snapshot
    identities_data = []
    for mapping in identity_mappings:
        identities_data.append({
            "discord_user_id": mapping.discord_user_id,
            "github_user": mapping.github_user,
        })
    
    identities = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "org": org,
        "run_id": run_id,
        "data": identities_data,
    }
    
    # Scores snapshot
    scores_data = []
    for score in scores:
        scores_data.append({
            "github_user": score.github_user,
            "period_start": score.period_start.isoformat(),
            "period_end": score.period_end.isoformat(),
            "points": score.points,
        })
    
    scores_snapshot = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "org": org,
        "run_id": run_id,
        "data": scores_data,
    }
    
    # Contributors snapshot (from contribution summaries)
    contributors_data = []
    if contribution_summaries:
        for summary in contribution_summaries:
            contributors_data.append({
                "github_user": summary.github_user,
                "period_start": summary.period_start.isoformat(),
                "period_end": summary.period_end.isoformat(),
                "issues_opened": summary.issues_opened,
                "prs_opened": summary.prs_opened,
                "prs_reviewed": summary.prs_reviewed,
                "comments": summary.comments,
                "total_score": summary.total_score,
            })
    
    contributors = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "org": org,
        "run_id": run_id,
        "data": contributors_data,
    }
    
    # Roles snapshot (Discord member roles)
    roles_data = []
    for discord_user_id, roles in sorted(member_roles.items()):
        roles_data.append({
            "discord_user_id": discord_user_id,
            "roles": sorted(roles),
        })
    
    roles = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "org": org,
        "run_id": run_id,
        "data": roles_data,
    }
    
    # Issue requests snapshot
    issue_requests_data = []
    list_pending = getattr(storage, "list_pending_issue_requests", None)
    if callable(list_pending):
        pending_requests = list_pending()
        for req in pending_requests:
            issue_requests_data.append({
                "request_id": req.get("request_id"),
                "discord_user_id": req.get("discord_user_id"),
                "github_user": req.get("github_user"),
                "owner": req.get("owner"),
                "repo": req.get("repo"),
                "issue_number": req.get("issue_number"),
                "issue_url": req.get("issue_url"),
                "created_at": req.get("created_at"),
                "status": req.get("status"),
            })
    
    issue_requests = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "org": org,
        "run_id": run_id,
        "data": issue_requests_data,
    }
    
    # Notifications snapshot (recent sent notifications)
    notifications_data = []
    list_notifications = getattr(storage, "list_recent_notifications", None)
    if callable(list_notifications):
        recent_notifications = list_notifications(limit=1000)  # Last 1000 notifications
        for notif in recent_notifications:
            notifications_data.append({
                "dedupe_key": notif.get("dedupe_key"),
                "event_type": notif.get("event_type"),
                "github_user": notif.get("github_user"),
                "discord_user_id": notif.get("discord_user_id"),
                "repo": notif.get("repo"),
                "target": notif.get("target"),
                "channel_id": notif.get("channel_id"),
                "sent_at": notif.get("sent_at"),
            })
    
    notifications = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "org": org,
        "run_id": run_id,
        "data": notifications_data,
    }
    
    return {
        "meta.json": meta,
        "identities.json": identities,
        "scores.json": scores_snapshot,
        "contributors.json": contributors,
        "roles.json": roles,
        "issue_requests.json": issue_requests,
        "notifications.json": notifications,
    }


def _parse_repo_path(repo_path: str) -> tuple[str, str]:
    """Parse 'owner/repo' or 'owner/repo/path' into (owner, repo).
    
    For now, we only support owner/repo format. Path prefix is handled separately.
    """
    parts = repo_path.split("/", 2)
    if len(parts) < 2:
        raise ValueError(f"Invalid repo_path format: {repo_path}. Expected 'owner/repo'")
    return (parts[0], parts[1])


def _write_file_to_github(
    github_writer: Any,
    owner: str,
    repo: str,
    file_path: str,
    content: dict[str, Any],
    commit_message: str,
    branch: str | None = None,
) -> bool:
    """Write a JSON file to GitHub repo using GitHub API.
    
    Returns True if successful, False otherwise.
    """
    write_file = getattr(github_writer, "write_file", None)
    if not callable(write_file):
        logger.debug("GitHub writer does not support write_file method")
        return False
    
    try:
        json_content = json.dumps(content, indent=2, sort_keys=True, ensure_ascii=False)
        return write_file(owner, repo, file_path, json_content, commit_message, branch=branch)
    except Exception as exc:
        logger.warning(
            "Failed to write snapshot file to GitHub",
            exc_info=True,
            extra={"file_path": file_path, "error": str(exc)},
        )
        return False
