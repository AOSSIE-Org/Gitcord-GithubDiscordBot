from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.engine.webhooks import (
    WebhookError,
    ingest_github_delivery,
    map_github_webhook_to_contributions,
    parse_github_delivery,
    verify_github_signature,
)


SECRET = "a-long-random-webhook-secret"


def _signature(body: bytes) -> str:
    digest = hmac.new(SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_github_signature_accepts_valid_signature() -> None:
    body = b'{"action":"opened"}'

    verify_github_signature(body, _signature(body), SECRET)


def test_verify_github_signature_rejects_invalid_signature() -> None:
    with pytest.raises(WebhookError, match="Invalid GitHub webhook signature"):
        verify_github_signature(b"{}", "sha256=bad", SECRET)


def test_pull_request_closed_merged_maps_to_pr_merged() -> None:
    payload = {
        "action": "closed",
        "repository": {"full_name": "owner/repo"},
        "pull_request": {
            "number": 42,
            "title": "Add feature",
            "html_url": "https://github.com/owner/repo/pull/42",
            "merged": True,
            "created_at": "2024-01-01T00:00:00Z",
            "merged_at": "2024-01-02T03:04:05Z",
            "user": {"login": "alice"},
            "labels": [{"name": "good first issue"}],
        },
    }

    events = map_github_webhook_to_contributions("pull_request", payload)

    assert len(events) == 1
    event = events[0]
    assert event.github_user == "alice"
    assert event.event_type == "pr_merged"
    assert event.repo == "owner/repo"
    assert event.created_at == datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert event.payload["pr_number"] == 42
    assert event.payload["difficulty_labels"] == ["good first issue"]


def test_issue_comment_maps_to_comment_event() -> None:
    payload = {
        "action": "created",
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 7, "pull_request": {"url": "x"}},
        "comment": {
            "id": 99,
            "html_url": "https://github.com/owner/repo/pull/7#discussion",
            "created_at": "2024-01-02T00:00:00Z",
            "user": {"login": "bob"},
        },
    }

    events = map_github_webhook_to_contributions("issue_comment", payload)

    assert len(events) == 1
    assert events[0].event_type == "comment"
    assert events[0].github_user == "bob"
    assert events[0].payload["target_type"] == "pull_request"
    assert events[0].payload["target_number"] == 7
    assert events[0].payload["comment_id"] == 99


def test_parse_verified_webhook_requires_signature_and_delivery() -> None:
    body = b"{}"
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": _signature(body),
    }

    with pytest.raises(WebhookError, match="Missing X-GitHub-Delivery"):
        parse_github_delivery(headers, body, {}, SECRET)


def test_ingest_verified_webhook_dedupes_delivery(tmp_path) -> None:
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    payload = {
        "action": "opened",
        "repository": {"full_name": "owner/repo"},
        "issues": {},
        "pull_request": {
            "number": 1,
            "title": "PR",
            "html_url": "https://github.com/owner/repo/pull/1",
            "created_at": "2024-01-01T00:00:00Z",
            "user": {"login": "alice"},
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": "delivery-1",
        "X-Hub-Signature-256": _signature(body),
    }
    delivery = parse_github_delivery(headers, body, payload, SECRET)

    first = ingest_github_delivery(storage, delivery)
    second = ingest_github_delivery(storage, delivery)

    assert first.stored == 1
    assert first.duplicate is False
    assert second.stored == 0
    assert second.duplicate is True
    events = storage.list_contributions(datetime(2023, 1, 1, tzinfo=timezone.utc))
    assert len(events) == 1
    assert events[0].event_type == "pr_opened"
