from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from ghdcbot.core.models import ContributionEvent


SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
DELIVERY_HEADER = "X-GitHub-Delivery"


class WebhookError(ValueError):
    pass


@dataclass(frozen=True)
class GitHubDelivery:
    event_name: str
    delivery_id: str
    events: list[ContributionEvent]


@dataclass(frozen=True)
class DeliveryIngestResult:
    delivery_id: str
    event_name: str
    stored: int
    duplicate: bool = False


def verify_github_signature(body: bytes, signature_header: str | None, secret: str) -> None:
    # GitHub signs the raw request body, not the parsed JSON.
    if not secret:
        raise WebhookError("GitHub webhook secret is not configured")
    if not signature_header:
        raise WebhookError("Missing X-Hub-Signature-256 header")
    if not signature_header.startswith("sha256="):
        raise WebhookError("Unsupported GitHub webhook signature format")

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise WebhookError("Invalid GitHub webhook signature")


def parse_github_delivery(
    headers: Mapping[str, str],
    body: bytes,
    payload: Mapping[str, Any],
    secret: str,
) -> GitHubDelivery:
    signature = _get_header(headers, SIGNATURE_HEADER)
    event_name = _get_header(headers, EVENT_HEADER)
    delivery_id = _get_header(headers, DELIVERY_HEADER)

    if not event_name:
        raise WebhookError("Missing X-GitHub-Event header")
    if not delivery_id:
        raise WebhookError("Missing X-GitHub-Delivery header")

    verify_github_signature(body, signature, secret)
    events = map_github_webhook_to_contributions(event_name, payload)
    return GitHubDelivery(event_name=event_name, delivery_id=delivery_id, events=events)


def ingest_github_delivery(storage: Any, delivery: GitHubDelivery) -> DeliveryIngestResult:
    record_delivery = getattr(storage, "record_webhook_delivery", None)
    if not callable(record_delivery):
        raise WebhookError("Storage adapter does not support webhook delivery dedupe")

    if not record_delivery(delivery.delivery_id, delivery.event_name):
        return DeliveryIngestResult(
            delivery_id=delivery.delivery_id,
            event_name=delivery.event_name,
            stored=0,
            duplicate=True,
        )

    stored = storage.record_contributions(delivery.events) if delivery.events else 0
    return DeliveryIngestResult(
        delivery_id=delivery.delivery_id,
        event_name=delivery.event_name,
        stored=stored,
    )


def map_github_webhook_to_contributions(
    event_name: str,
    payload: Mapping[str, Any],
) -> list[ContributionEvent]:
    action = payload.get("action")
    repository = payload.get("repository") or {}
    repo = _repo_name(repository)
    if not repo:
        return []

    if event_name == "pull_request":
        pull_request = payload.get("pull_request") or {}
        if action == "opened":
            return [_pull_request_event("pr_opened", repo, pull_request)]
        if action == "closed" and pull_request.get("merged") is True:
            event = _pull_request_event("pr_merged", repo, pull_request, time_field="merged_at")
            labels = _label_names(pull_request.get("labels") or [])
            if labels:
                event.payload["difficulty_labels"] = labels
            return [event]
        return []

    if event_name == "pull_request_review" and action == "submitted":
        review = payload.get("review") or {}
        pull_request = payload.get("pull_request") or {}
        return [_pull_request_review_event(repo, pull_request, review)]

    if event_name == "issues" and action == "opened":
        issue = payload.get("issue") or {}
        return [_issue_event("issue_opened", repo, issue)]

    if event_name == "issue_comment" and action == "created":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        return [_comment_event(repo, issue, comment)]

    return []


def _pull_request_event(
    event_type: str,
    repo: str,
    pull_request: Mapping[str, Any],
    *,
    time_field: str = "created_at",
) -> ContributionEvent:
    user = pull_request.get("user") or {}
    pr_number = int(pull_request.get("number") or 0)
    return ContributionEvent(
        github_user=str(user.get("login") or ""),
        event_type=event_type,
        repo=repo,
        created_at=_parse_github_time(pull_request.get(time_field) or pull_request.get("created_at")),
        payload={
            "pr_number": pr_number,
            "title": pull_request.get("title"),
            "url": pull_request.get("html_url"),
            "merged": pull_request.get("merged") is True,
        },
    )


def _pull_request_review_event(
    repo: str,
    pull_request: Mapping[str, Any],
    review: Mapping[str, Any],
) -> ContributionEvent:
    reviewer = review.get("user") or {}
    author = pull_request.get("user") or {}
    return ContributionEvent(
        github_user=str(reviewer.get("login") or ""),
        event_type="pr_reviewed",
        repo=repo,
        created_at=_parse_github_time(review.get("submitted_at")),
        payload={
            "pr_number": int(pull_request.get("number") or 0),
            "review_id": review.get("id"),
            "state": review.get("state"),
            "url": review.get("html_url"),
            "pr_author": author.get("login"),
        },
    )


def _issue_event(event_type: str, repo: str, issue: Mapping[str, Any]) -> ContributionEvent:
    user = issue.get("user") or {}
    return ContributionEvent(
        github_user=str(user.get("login") or ""),
        event_type=event_type,
        repo=repo,
        created_at=_parse_github_time(issue.get("created_at")),
        payload={
            "issue_number": int(issue.get("number") or 0),
            "title": issue.get("title"),
            "url": issue.get("html_url"),
        },
    )


def _comment_event(
    repo: str,
    issue: Mapping[str, Any],
    comment: Mapping[str, Any],
) -> ContributionEvent:
    user = comment.get("user") or {}
    target_type = "pull_request" if issue.get("pull_request") else "issue"
    return ContributionEvent(
        github_user=str(user.get("login") or ""),
        event_type="comment",
        repo=repo,
        created_at=_parse_github_time(comment.get("created_at")),
        payload={
            "target_type": target_type,
            "target_number": int(issue.get("number") or 0),
            "comment_id": comment.get("id"),
            "url": comment.get("html_url"),
        },
    )


def _repo_name(repository: Mapping[str, Any]) -> str:
    full_name = repository.get("full_name")
    if full_name:
        return str(full_name)
    owner = repository.get("owner") or {}
    owner_login = owner.get("login")
    name = repository.get("name")
    if owner_login and name:
        return f"{owner_login}/{name}"
    if name:
        return str(name)
    return ""


def _label_names(labels: list[Any]) -> list[str]:
    names = []
    for label in labels:
        if isinstance(label, Mapping) and label.get("name"):
            names.append(str(label["name"]))
    return names


def _parse_github_time(value: Any) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None
