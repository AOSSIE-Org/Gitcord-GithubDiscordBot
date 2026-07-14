from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghdcbot.adapters.github.identity import VerificationMatch
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.bot import (
    IdentityVerificationView,
    VERIFICATION_CODE_REMOVAL_NOTE,
    build_identity_verification_embed,
    github_profile_settings_url,
)
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    IdentityMapping,
    RoleMappingConfig,
    RuntimeConfig,
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


def test_duplicate_pending_claim_for_same_github_user_is_rejected(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))

    first_claim = svc.create_claim("d1", "shubham_5080")

    with pytest.raises(ValueError, match="active pending claim"):
        svc.create_claim("d2", "shubham_5080")

    original_row = storage.get_identity_link("d1", "shubham_5080")
    duplicate_row = storage.get_identity_link("d2", "shubham_5080")
    assert original_row is not None
    assert original_row["verification_code"] == first_claim.verification_code
    assert duplicate_row is None


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
    svc.create_claim("d1", "octocat")
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


def test_unlink_succeeds_after_cooldown(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.create_claim("d1", "octocat")
    svc.verify_claim("d1", "octocat")
    # cooldown_hours=0 so unlink is allowed immediately
    svc.unlink("d1", cooldown_hours=0)
    row = storage.get_identity_link("d1", "octocat")
    assert row is not None
    assert row["verified"] == 0
    assert row["unlinked_at"] is not None
    assert len(storage.list_verified_identity_mappings()) == 0


def test_unlink_fails_if_no_verified_identity(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    with pytest.raises(ValueError, match="No verified identity link found"):
        svc.unlink("d1", cooldown_hours=0)


def test_unlink_fails_inside_cooldown_window(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.create_claim("d1", "octocat")
    svc.verify_claim("d1", "octocat")
    with pytest.raises(ValueError, match="Identity was verified recently"):
        svc.unlink("d1", cooldown_hours=24)
    row = storage.get_identity_link("d1", "octocat")
    assert row is not None
    assert row["verified"] == 1


def test_unlinked_identity_not_used_in_planning(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    storage.create_identity_claim("d1", "alice", "A" * 10, datetime.now(timezone.utc) + timedelta(minutes=10))
    storage.create_identity_claim("d2", "bob", "B" * 10, datetime.now(timezone.utc) + timedelta(minutes=10))
    storage.mark_identity_verified("d2", "bob")
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.unlink("d2", cooldown_hours=0)
    # After unlink, no verified mappings; engine falls back to config
    class _GitHubStub:
        def list_contributions(self, since):  # noqa: ANN001, ARG002
            return []

        def list_open_issues(self):
            return [{"repo": "r", "number": 1}]

        def list_open_pull_requests(self):
            return []

        def assign_issue(self, repo: str, issue_number: int, assignee: str) -> None:  # noqa: ARG002
            pass

        def request_review(self, repo: str, pr_number: int, reviewer: str) -> None:  # noqa: ARG002
            pass

        def close(self) -> None:
            pass

    class _DiscordStub:
        def list_member_roles(self):
            return {"d1": [], "d2": []}

        def add_role(self, discord_user_id: str, role_name: str) -> None:  # noqa: ARG002
            pass

        def remove_role(self, discord_user_id: str, role_name: str) -> None:  # noqa: ARG002
            pass

        def close(self) -> None:
            pass

    config = BotConfig(
        runtime=RuntimeConfig(
            mode="dry-run",
            log_level="INFO",
            data_dir=str(tmp_path),
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(org="x", token="t", api_base="https://api.github.com", user_fallback=False),
        discord=DiscordConfig(guild_id="1", token="t"),
        assignments=AssignmentConfig(issue_assignees=["Contributor"], review_roles=[]),
        identity_mappings=[IdentityMapping(github_user="alice", discord_user_id="d1")],
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
    # Bob was unlinked; only config (alice) can be used
    assert "\"assignee\": \"bob\"" not in payload


def test_audit_event_identity_unlinked_written(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.create_claim("d1", "octocat")
    svc.verify_claim("d1", "octocat")
    svc.unlink("d1", cooldown_hours=0)
    audit_path = Path(tmp_path) / "audit_events.jsonl"
    assert audit_path.exists()
    lines = [line.strip() for line in audit_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    unlink_events = [line for line in lines if '"event_type":"identity_unlinked"' in line or '"event_type": "identity_unlinked"' in line]
    assert len(unlink_events) == 1
    import json
    ev = json.loads(unlink_events[0])
    assert ev.get("event_type") == "identity_unlinked"
    assert ev.get("context", {}).get("github_user") == "octocat"
    assert "unlinked_at" in ev.get("context", {})


def test_relink_works_after_unlink(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.create_claim("d1", "octocat")
    svc.verify_claim("d1", "octocat")
    svc.unlink("d1", cooldown_hours=0)
    assert len(storage.list_verified_identity_mappings()) == 0
    # Relink: create new claim and verify
    claim = svc.create_claim("d1", "octocat")
    assert claim.verification_code
    ok, location = svc.verify_claim("d1", "octocat")
    assert ok is True
    assert location == "bio"
    mappings = storage.list_verified_identity_mappings()
    assert len(mappings) == 1
    assert mappings[0].github_user == "octocat" and mappings[0].discord_user_id == "d1"
    status = storage.get_identity_status("d1")
    assert status["status"] == "verified"
    assert status["github_user"] == "octocat"


# --- Identity status (read-only) ---


def test_identity_status_not_linked_when_no_row(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    status = storage.get_identity_status("d1")
    assert status["status"] == "not_linked"
    assert status["github_user"] is None
    assert status["verified_at"] is None


def test_identity_status_verified_when_verified_row_exists(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    storage.create_identity_claim(
        "d1", "alice", "A" * 10, datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    storage.mark_identity_verified("d1", "alice")
    status = storage.get_identity_status("d1")
    assert status["status"] == "verified"
    assert status["github_user"] == "alice"
    assert status["verified_at"] is not None


def test_identity_status_pending_when_unverified_not_expired(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    storage.create_identity_claim(
        "d1", "bob", "B" * 10, datetime.now(timezone.utc) + timedelta(hours=1)
    )
    status = storage.get_identity_status("d1")
    assert status["status"] == "pending"
    assert status["github_user"] == "bob"
    assert status["verified_at"] is None


def test_identity_status_pending_when_unverified_expired(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    storage.create_identity_claim(
        "d1", "bob", "B" * 10, datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    status = storage.get_identity_status("d1")
    assert status["status"] == "pending"
    assert status["github_user"] == "bob"
    assert status["verified_at"] is None


def test_identity_status_not_linked_after_unlink(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.create_claim("d1", "octocat")
    svc.verify_claim("d1", "octocat")
    svc.unlink("d1", cooldown_hours=0)
    status = storage.get_identity_status("d1")
    assert status["status"] == "not_linked"
    assert status["github_user"] is None


def test_unlinked_identity_hidden_from_active_link_list(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.create_claim("d1", "octocat")
    svc.verify_claim("d1", "octocat")
    svc.unlink("d1", cooldown_hours=0)

    links = storage.get_identity_links_for_discord_user("d1")

    assert links == []


def test_cli_identity_status_output_contains_correct_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
runtime:
  mode: dry-run
  log_level: INFO
  data_dir: "{tmp_path}"
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  org: x
  token: t
  api_base: https://api.github.com
  user_fallback: false
discord:
  guild_id: "1"
  token: t
scoring:
  period_days: 30
  weights: {{}}
role_mappings:
  - discord_role: Contributor
    min_score: 1
assignments:
  issue_assignees: []
  review_roles: []
"""
    )
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    storage.create_identity_claim(
        "123456", "alice", "A" * 10, datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    storage.mark_identity_verified("123456", "alice")

    from ghdcbot.cli import main
    import sys
    original_argv = sys.argv
    try:
        sys.argv = ["ghdcbot", "--config", str(config_path), "identity", "status", "--discord-user-id", "123456"]
        main()
    finally:
        sys.argv = original_argv

    out, _ = capsys.readouterr()
    assert "Discord user: 123456" in out
    assert "GitHub user: alice" in out
    assert "Status: Verified" in out
    assert "Verified at:" in out


def test_cli_identity_status_not_linked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
runtime:
  mode: dry-run
  log_level: INFO
  data_dir: "{tmp_path}"
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  org: x
  token: t
  api_base: https://api.github.com
  user_fallback: false
discord:
  guild_id: "1"
  token: t
scoring:
  period_days: 30
  weights: {{}}
role_mappings:
  - discord_role: Contributor
    min_score: 1
assignments:
  issue_assignees: []
  review_roles: []
"""
    )
    from ghdcbot.cli import main
    import sys
    original_argv = sys.argv
    try:
        sys.argv = ["ghdcbot", "--config", str(config_path), "identity", "status", "--discord-user-id", "999"]
        main()
    finally:
        sys.argv = original_argv

    out, _ = capsys.readouterr()
    assert "Discord user: 999" in out
    assert "Status: Not linked" in out
    assert "Verified at: -" in out


def test_discord_profile_supports_optional_member_lookup() -> None:
    """Discord /profile may show another member via optional contributor param."""
    import inspect
    from ghdcbot.bot import run_bot as run_bot_fn
    source = inspect.getsource(run_bot_fn)
    assert 'name="profile"' in source
    assert "contributor: discord.Member | None = None" in source
    assert "Social Profiles" in source


def test_discord_profile_shows_verification_status() -> None:
    """/profile folds in verification status (identity group command removed)."""
    import inspect
    from ghdcbot.bot import run_bot as run_bot_fn
    source = inspect.getsource(run_bot_fn)
    assert "get_identity_status" in source
    assert "**Verification:**" in source
    assert 'name="identity"' not in source


# --- Stale identity expiry (soft) ---


def test_identity_status_not_stale_when_fresh(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    storage.create_identity_claim(
        "d1", "alice", "A" * 10, datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    storage.mark_identity_verified("d1", "alice")
    status = storage.get_identity_status("d1", max_age_days=30)
    assert status["status"] == "verified"
    assert status["is_stale"] is False


def test_identity_status_stale_when_old(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    # Create verified identity 31 days ago
    old_time = datetime.now(timezone.utc) - timedelta(days=31)
    storage.create_identity_claim(
        "d1", "alice", "A" * 10, datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    storage.mark_identity_verified("d1", "alice")
    # Manually update verified_at to be old
    with storage._connect() as conn:
        conn.execute(
            "UPDATE identity_links SET verified_at = ? WHERE discord_user_id = ? AND github_user = ?",
            (old_time.isoformat(), "d1", "alice"),
        )
    status = storage.get_identity_status("d1", max_age_days=30)
    assert status["status"] == "verified_stale"
    assert status["is_stale"] is True


def test_identity_status_not_stale_when_max_age_not_set(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    old_time = datetime.now(timezone.utc) - timedelta(days=100)
    storage.create_identity_claim(
        "d1", "alice", "A" * 10, datetime.now(timezone.utc) + timedelta(minutes=10)
    )
    storage.mark_identity_verified("d1", "alice")
    with storage._connect() as conn:
        conn.execute(
            "UPDATE identity_links SET verified_at = ? WHERE discord_user_id = ? AND github_user = ?",
            (old_time.isoformat(), "d1", "alice"),
        )
    status = storage.get_identity_status("d1", max_age_days=None)
    assert status["status"] == "verified"
    assert status["is_stale"] is False


def test_create_claim_allows_refresh_when_stale(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.create_claim("d1", "alice")
    svc.verify_claim("d1", "alice")
    # Make it stale
    old_time = datetime.now(timezone.utc) - timedelta(days=31)
    with storage._connect() as conn:
        conn.execute(
            "UPDATE identity_links SET verified_at = ? WHERE discord_user_id = ? AND github_user = ?",
            (old_time.isoformat(), "d1", "alice"),
        )
    # Should allow creating new claim when stale
    claim = svc.create_claim("d1", "alice", max_age_days=30)
    assert claim.verification_code
    # Verify the new claim refreshes verified_at
    ok, location = svc.verify_claim("d1", "alice")
    assert ok is True
    row = storage.get_identity_link("d1", "alice")
    assert row is not None
    assert row["verified"] == 1
    verified_at = _parse_utc(row["verified_at"])
    # Should be recent (within last minute)
    age = (datetime.now(timezone.utc) - verified_at).total_seconds()
    assert age < 60


def test_create_claim_rejects_refresh_when_not_stale(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    svc.create_claim("d1", "alice")
    svc.verify_claim("d1", "alice")
    # Should reject creating new claim when not stale
    with pytest.raises(ValueError, match="already verified"):
        svc.create_claim("d1", "alice", max_age_days=30)


class _FakeInteractionResponse:
    def __init__(self) -> None:
        self.edits: list[dict] = []
        self.messages: list[dict] = []
        self.deferred = False
        self._done = False

    def is_done(self) -> bool:
        return self._done

    async def defer(self) -> None:
        self.deferred = True
        self._done = True

    async def edit_message(self, **kwargs) -> None:  # noqa: ANN003
        self.edits.append(kwargs)
        self._done = True

    async def send_message(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})
        self._done = True


class _FakeFollowup:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, content: str, *, ephemeral: bool = False) -> None:
        self.messages.append({"content": content, "ephemeral": ephemeral})


class _FakeButtonInteraction:
    def __init__(self, discord_user_id: str) -> None:
        self.user = SimpleNamespace(id=discord_user_id)
        self.response = _FakeInteractionResponse()
        self.followup = _FakeFollowup()
        self.original_edits: list[dict] = []

    async def edit_original_response(self, **kwargs) -> None:  # noqa: ANN003
        self.original_edits.append(kwargs)


def _view_children_disabled(view: IdentityVerificationView) -> bool:
    return all(bool(getattr(item, "disabled", False)) for item in view.children)


def test_github_profile_settings_url_from_api_base() -> None:
    assert (
        github_profile_settings_url("https://api.github.com")
        == "https://github.com/settings/profile"
    )
    assert (
        github_profile_settings_url("https://ghes.example.com/api/v3")
        == "https://ghes.example.com/settings/profile"
    )


def test_identity_verification_embed_includes_github_profile_settings_link(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    claim = svc.create_claim("d1", "octocat")
    profile_settings_url = github_profile_settings_url("https://api.github.com")

    embed = build_identity_verification_embed(claim, profile_settings_url=profile_settings_url)
    description = embed.to_dict()["description"]

    assert profile_settings_url in description
    assert "Copy the verification code below" in description
    assert "Paste the code into your GitHub bio" in description


def test_identity_verification_view_includes_github_profile_button() -> None:
    profile_settings_url = github_profile_settings_url("https://api.github.com")
    view = IdentityVerificationView(
        service=SimpleNamespace(),
        storage=SimpleNamespace(),
        discord_user_id="d1",
        github_user="octocat",
        profile_settings_url=profile_settings_url,
    )
    link_buttons = [
        item
        for item in view.children
        if getattr(item, "label", None) == "Open GitHub Profile"
        and getattr(item, "url", None) == profile_settings_url
    ]
    assert len(link_buttons) == 1


def test_identity_verify_button_marks_claim_verified(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(True, "bio"))
    claim = svc.create_claim("d1", "octocat")
    profile_settings_url = github_profile_settings_url("https://api.github.com")
    embed = build_identity_verification_embed(claim, profile_settings_url=profile_settings_url)
    view = IdentityVerificationView(
        service=svc,
        storage=storage,
        discord_user_id="d1",
        github_user="octocat",
        profile_settings_url=profile_settings_url,
    )
    interaction = _FakeButtonInteraction("d1")

    asyncio.run(view.verify_identity(interaction))

    row = storage.get_identity_link("d1", "octocat")
    assert row is not None
    assert row["verified"] == 1
    assert row["verification_code"] is None
    assert interaction.response.deferred is True
    assert _view_children_disabled(view)
    assert interaction.original_edits[-1]["content"] == (
        "✅ Successfully verified GitHub account\n\n"
        "GitHub: octocat\n\n"
        f"Status: Verified\n\n"
        f"{VERIFICATION_CODE_REMOVAL_NOTE}"
    )
    assert interaction.original_edits[-1]["embed"] is None
    assert interaction.original_edits[-1]["view"] is view
    assert embed.to_dict()["fields"][0]["value"] == "octocat"
    assert _view_children_disabled(view)


def test_identity_verify_button_reports_expired_claim(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(
        storage=storage,
        github_identity=_GitHubIdentityAlways(True, "bio"),
        ttl_minutes=-1,
    )
    svc.create_claim("d1", "octocat")
    view = IdentityVerificationView(
        service=svc,
        storage=storage,
        discord_user_id="d1",
        github_user="octocat",
        profile_settings_url=github_profile_settings_url("https://api.github.com"),
    )
    interaction = _FakeButtonInteraction("d1")

    asyncio.run(view.verify_identity(interaction))

    row = storage.get_identity_link("d1", "octocat")
    assert row is not None
    assert row["verified"] == 0
    assert interaction.response.deferred is True
    assert interaction.original_edits[-1]["content"] == (
        "❌ Verification expired.\n\n"
        "Run /link again to generate a new verification code."
    )
    assert _view_children_disabled(view)


def test_identity_verify_button_reports_code_not_found(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    svc = IdentityLinkService(storage=storage, github_identity=_GitHubIdentityAlways(False))
    svc.create_claim("d1", "octocat")
    view = IdentityVerificationView(
        service=svc,
        storage=storage,
        discord_user_id="d1",
        github_user="octocat",
        profile_settings_url=github_profile_settings_url("https://api.github.com"),
    )
    interaction = _FakeButtonInteraction("d1")

    asyncio.run(view.verify_identity(interaction))

    row = storage.get_identity_link("d1", "octocat")
    assert row is not None
    assert row["verified"] == 0
    assert interaction.response.deferred is True
    assert interaction.followup.messages[0]["content"] == (
        "❌ Verification code not found.\n\n"
        "Please ensure the code is present in your GitHub bio or public gist and try again."
    )
    assert interaction.followup.messages[0]["ephemeral"] is True
    assert not _view_children_disabled(view)


def test_identity_verify_button_defers_before_slow_github_check(tmp_path: Path) -> None:
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()

    class _SlowGitHubIdentity:
        def search_verification_code(self, github_user: str, code: str):  # noqa: ARG002
            import time

            time.sleep(0.05)
            from ghdcbot.adapters.github.identity import VerificationMatch

            return VerificationMatch(found=True, location="bio")

    svc = IdentityLinkService(storage=storage, github_identity=_SlowGitHubIdentity())
    svc.create_claim("d1", "octocat")
    view = IdentityVerificationView(
        service=svc,
        storage=storage,
        discord_user_id="d1",
        github_user="octocat",
        profile_settings_url=github_profile_settings_url("https://api.github.com"),
    )
    interaction = _FakeButtonInteraction("d1")

    asyncio.run(view.verify_identity(interaction))

    assert interaction.response.deferred is True
    assert interaction.original_edits
    assert "Successfully verified" in interaction.original_edits[-1]["content"]


def test_identity_verify_cancel_button_disables_view() -> None:
    svc = SimpleNamespace()
    storage = SimpleNamespace()
    view = IdentityVerificationView(
        service=svc,
        storage=storage,
        discord_user_id="d1",
        github_user="octocat",
        profile_settings_url=github_profile_settings_url("https://api.github.com"),
    )
    interaction = _FakeButtonInteraction("d1")

    asyncio.run(view.cancel_verification(interaction))

    assert interaction.response.edits[0]["content"] == "Verification cancelled."
    assert interaction.response.edits[0]["embed"] is None
    assert interaction.response.edits[0]["view"] is view
    assert _view_children_disabled(view)


def _parse_utc(value: str) -> datetime:
    """Helper to parse UTC timestamp."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
