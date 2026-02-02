from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghdcbot.adapters.github.identity import VerificationMatch
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    IdentityMapping,
    RoleMappingConfig,
    RuntimeConfig,
    ScoringConfig,
)
from ghdcbot.engine.identity_linking import IdentityLinkService
from ghdcbot.engine.orchestrator import Orchestrator


class _GitHubIdentityAlways:
    def __init__(self, found: bool, location: str | None = None) -> None:
        self._found = found
        self._location = location

    def search_verification_code(self, github_user: str, code: str) -> VerificationMatch:  # noqa: ARG002
        return VerificationMatch(found=self._found, location=self._location)


def test_verification_code_generated_and_stored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("secrets.choice", lambda alphabet: "Z")
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    claim = svc.create_claim("d1", "octocat")

    row = storage.get_identity_link("d1", "octocat")
    assert row is not None
    assert row["verified"] == 0
    assert row["verification_code"] == "Z" * 10
    assert claim.verification_code == "Z" * 10
    assert row["expires_at"].endswith("+00:00")


def test_impersonation_attempt_fails_when_github_user_already_verified(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))

    claim = svc.create_claim("d1", "octocat")
    assert claim.verification_code
    storage.mark_identity_verified("d1", "octocat")

    with pytest.raises(ValueError):
        svc.create_claim("d2", "octocat")


def test_create_claim_rejects_already_verified_same_pair(tmp_path: Path) -> None:
    """Verified (discord_user_id, github_user) must not be overwritten by create_claim."""
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))

    svc.create_claim("d1", "octocat")
    svc.verify_claim("d1", "octocat")

    with pytest.raises(ValueError, match="already verified"):
        svc.create_claim("d1", "octocat")

    row = storage.get_identity_link("d1", "octocat")
    assert row is not None
    assert row["verified"] == 1


def test_verify_marks_mapping_verified_and_clears_code(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    claim = svc.create_claim("d1", "octocat")
    ok, location = svc.verify_claim("d1", "octocat")
    assert ok is True
    assert location == "bio"

    row = storage.get_identity_link("d1", "octocat")
    assert row is not None
    assert row["verified"] == 1
    assert row["verification_code"] is None
    assert row["expires_at"] is None
    assert row["verified_at"] is not None


def test_verified_mappings_used_unverified_ignored_in_planning(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    # Unverified mapping should not be used
    storage.create_identity_claim(
        discord_user_id="d1",
        github_user="alice",
        verification_code="A" * 10,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    # Verified mapping should be used
    storage.create_identity_claim(
        discord_user_id="d2",
        github_user="bob",
        verification_code="B" * 10,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    storage.mark_identity_verified("d2", "bob")

    class _GitHubStub:
        def list_contributions(self, since):  # noqa: ANN001, ARG002
            return []

        def list_open_issues(self):
            return [{"repo": "r", "number": 1}]

        def list_open_pull_requests(self):
            return []

        def assign_issue(self, repo: str, issue_number: int, assignee: str) -> None:  # noqa: ARG002
            raise AssertionError("should not write in dry-run")

        def request_review(self, repo: str, pr_number: int, reviewer: str) -> None:  # noqa: ARG002
            raise AssertionError("should not write in dry-run")

        def close(self) -> None:
            return None

    class _DiscordStub:
        def list_member_roles(self):
            return {"d1": ["Contributor"], "d2": ["Contributor"]}

        def add_role(self, discord_user_id: str, role_name: str) -> None:  # noqa: ARG002
            raise AssertionError("should not write in dry-run")

        def remove_role(self, discord_user_id: str, role_name: str) -> None:  # noqa: ARG002
            raise AssertionError("should not write in dry-run")

        def close(self) -> None:
            return None

    config = BotConfig(
        runtime=RuntimeConfig(
            mode="dry-run",
            log_level="INFO",
            data_dir=str(tmp_path),
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            org="x",
            token="t",
            api_base="https://api.github.com",
            user_fallback=False,
        ),
        discord=DiscordConfig(guild_id="1", token="t"),
        scoring=ScoringConfig(period_days=30, weights={"issue_opened": 1}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=1)],
        assignments=AssignmentConfig(issue_assignees=["Contributor"], review_roles=[]),
        # Config mappings should be ignored when storage has verified mappings.
        identity_mappings=[
            IdentityMapping(github_user="alice", discord_user_id="d1"),
        ],
    )

    orch = Orchestrator(
        github_reader=_GitHubStub(),
        github_writer=_GitHubStub(),
        discord_reader=_DiscordStub(),
        discord_writer=_DiscordStub(),
        storage=storage,
        config=config,
    )

    orch.run_once()

    audit_path = Path(config.runtime.data_dir) / "reports" / "audit.json"
    payload = audit_path.read_text(encoding="utf-8")
    assert "\"github_assignment_plans\"" in payload
    # Only bob (verified) should be used for assignment eligibility.
    assert "\"assignee\": \"bob\"" in payload
    assert "\"assignee\": \"alice\"" not in payload

