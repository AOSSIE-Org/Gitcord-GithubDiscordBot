"""Tests for GitHub snapshot writing."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    IdentityMapping,
    PermissionConfig,
    RoleMappingConfig,
    RuntimeConfig,
    ScoringConfig,
    SnapshotConfig,
)
from ghdcbot.core.modes import RunMode
from ghdcbot.core.models import ContributionEvent, ContributionSummary, Score
from ghdcbot.engine.snapshots import (
    SCHEMA_VERSION,
    _collect_snapshot_data,
    _parse_repo_path,
    write_snapshots_to_github,
)


class MockStorage:
    """Mock storage for testing."""

    def __init__(self) -> None:
        self.notifications: list[dict] = []
        self.contributions: list[ContributionEvent] = []

    def list_recent_notifications(self, limit: int = 1000) -> list[dict]:
        return self.notifications[:limit]

    def list_pending_issue_requests(self) -> list[dict]:
        return []

    def list_contributions(self, since: datetime) -> list[ContributionEvent]:
        return [e for e in self.contributions if e.created_at >= since]


class MockGitHubWriter:
    """Mock GitHub writer for testing."""
    
    def __init__(self) -> None:
        self.files_written: list[tuple[str, str, str, str]] = []  # (owner, repo, path, content)
    
    def write_file(self, owner: str, repo: str, file_path: str, content: str, commit_message: str, branch: str | None = None) -> bool:
        self.files_written.append((owner, repo, file_path, content))
        return True


def test_parse_repo_path() -> None:
    """Test parsing repo path."""
    owner, repo = _parse_repo_path("org/repo")
    assert owner == "org"
    assert repo == "repo"
    
    owner, repo = _parse_repo_path("owner/repo-name")
    assert owner == "owner"
    assert repo == "repo-name"


def test_parse_repo_path_invalid() -> None:
    """Test invalid repo path format."""
    with pytest.raises(ValueError, match="Invalid repo_path format"):
        _parse_repo_path("invalid")


def test_collect_snapshot_data() -> None:
    """Test snapshot data collection."""
    storage = MockStorage()
    config = BotConfig(
        runtime=RuntimeConfig(
            mode=RunMode.DRY_RUN,
            log_level="INFO",
            data_dir="/tmp/test",
            github_adapter="test",
            discord_adapter="test",
            storage_adapter="test",
        ),
        github=GitHubConfig(org="test-org", token="test", api_base="https://api.github.com", permissions=PermissionConfig()),
        discord=DiscordConfig(guild_id="123", token="test", permissions=PermissionConfig()),
        scoring=ScoringConfig(period_days=30, weights={}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        assignments=AssignmentConfig(),
    )
    
    identity_mappings = [
        IdentityMapping(discord_user_id="123", github_user="alice"),
        IdentityMapping(discord_user_id="456", github_user="bob"),
    ]
    
    scores = [
        Score(
            github_user="alice",
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 31, tzinfo=UTC),
            points=100,
        ),
    ]
    
    member_roles = {
        "123": ["Contributor"],
        "456": ["Maintainer"],
    }
    
    period_start = datetime(2024, 1, 1, tzinfo=UTC)
    period_end = datetime(2024, 1, 31, tzinfo=UTC)
    run_id = "test-run-123"
    generated_at = datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC)
    
    snapshots = _collect_snapshot_data(
        storage=storage,
        config=config,
        identity_mappings=identity_mappings,
        scores=scores,
        member_roles=member_roles,
        period_start=period_start,
        period_end=period_end,
        contribution_summaries=None,
        run_id=run_id,
        generated_at=generated_at,
    )
    
    # Check all snapshot files are present
    assert "meta.json" in snapshots
    assert "identities.json" in snapshots
    assert "scores.json" in snapshots
    assert "contributors.json" in snapshots
    assert "roles.json" in snapshots
    assert "issue_requests.json" in snapshots
    assert "notifications.json" in snapshots
    
    # Check meta.json structure
    meta = snapshots["meta.json"]
    assert meta["schema_version"] == SCHEMA_VERSION
    assert meta["org"] == "test-org"
    assert meta["run_id"] == run_id
    assert "generated_at" in meta
    
    # Check identities.json structure
    identities = snapshots["identities.json"]
    assert identities["schema_version"] == SCHEMA_VERSION
    assert len(identities["data"]) == 2
    assert identities["data"][0]["discord_user_id"] == "123"
    assert identities["data"][0]["github_user"] == "alice"
    
    # Check scores.json structure
    scores_snapshot = snapshots["scores.json"]
    assert scores_snapshot["schema_version"] == SCHEMA_VERSION
    assert len(scores_snapshot["data"]) == 1
    assert scores_snapshot["data"][0]["github_user"] == "alice"
    assert scores_snapshot["data"][0]["points"] == 100
    
    # Check roles.json structure
    roles = snapshots["roles.json"]
    assert roles["schema_version"] == SCHEMA_VERSION
    assert len(roles["data"]) == 2
    assert roles["data"][0]["discord_user_id"] == "123"
    assert roles["data"][0]["roles"] == ["Contributor"]


def test_collect_snapshot_data_with_contributors() -> None:
    """Test snapshot data collection with contribution summaries."""
    storage = MockStorage()
    config = BotConfig(
        runtime=RuntimeConfig(
            mode=RunMode.DRY_RUN,
            log_level="INFO",
            data_dir="/tmp/test",
            github_adapter="test",
            discord_adapter="test",
            storage_adapter="test",
        ),
        github=GitHubConfig(org="test-org", token="test", api_base="https://api.github.com", permissions=PermissionConfig()),
        discord=DiscordConfig(guild_id="123", token="test", permissions=PermissionConfig()),
        scoring=ScoringConfig(period_days=30, weights={}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        assignments=AssignmentConfig(),
    )
    
    contribution_summaries = [
        ContributionSummary(
            github_user="alice",
            issues_opened=5,
            prs_opened=3,
            prs_reviewed=2,
            comments=10,
            total_score=50,
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 31, tzinfo=UTC),
        ),
    ]
    
    snapshots = _collect_snapshot_data(
        storage=storage,
        config=config,
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
        contribution_summaries=contribution_summaries,
        run_id="test-run",
        generated_at=datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC),
    )
    
    contributors = snapshots["contributors.json"]
    assert len(contributors["data"]) == 1
    assert contributors["data"][0]["github_user"] == "alice"
    assert contributors["data"][0]["issues_opened"] == 5
    assert contributors["data"][0]["total_score"] == 50


def test_write_snapshots_disabled() -> None:
    """Test that snapshots are skipped when disabled."""
    storage = MockStorage()
    config = BotConfig(
        runtime=RuntimeConfig(
            mode=RunMode.DRY_RUN,
            log_level="INFO",
            data_dir="/tmp/test",
            github_adapter="test",
            discord_adapter="test",
            storage_adapter="test",
        ),
        github=GitHubConfig(org="test-org", token="test", api_base="https://api.github.com", permissions=PermissionConfig()),
        discord=DiscordConfig(guild_id="123", token="test", permissions=PermissionConfig()),
        scoring=ScoringConfig(period_days=30, weights={}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        assignments=AssignmentConfig(),
        snapshots=SnapshotConfig(enabled=False, repo_path="org/repo"),
    )
    github_writer = MockGitHubWriter()
    
    write_snapshots_to_github(
        storage=storage,
        config=config,
        github_writer=github_writer,
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
    )
    
    assert len(github_writer.files_written) == 0


def test_write_snapshots_enabled() -> None:
    """Test that snapshots are written when enabled."""
    storage = MockStorage()
    config = BotConfig(
        runtime=RuntimeConfig(
            mode=RunMode.DRY_RUN,
            log_level="INFO",
            data_dir="/tmp/test",
            github_adapter="test",
            discord_adapter="test",
            storage_adapter="test",
        ),
        github=GitHubConfig(org="test-org", token="test", api_base="https://api.github.com", permissions=PermissionConfig()),
        discord=DiscordConfig(guild_id="123", token="test", permissions=PermissionConfig()),
        scoring=ScoringConfig(period_days=30, weights={}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        assignments=AssignmentConfig(),
        snapshots=SnapshotConfig(enabled=True, repo_path="org/repo"),
    )
    github_writer = MockGitHubWriter()
    
    write_snapshots_to_github(
        storage=storage,
        config=config,
        github_writer=github_writer,
        identity_mappings=[
            IdentityMapping(discord_user_id="123", github_user="alice"),
        ],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
    )
    
    # Should have written snapshot files
    assert len(github_writer.files_written) > 0
    
    # Check that files are written to correct repo
    owner, repo, path, content = github_writer.files_written[0]
    assert owner == "org"
    assert repo == "repo"
    assert path.startswith("snapshots/")
    
    # Check that content is valid JSON
    import json
    parsed = json.loads(content)
    assert "schema_version" in parsed
    assert parsed["org"] == "test-org"


def test_write_snapshots_handles_errors() -> None:
    """Test that snapshot writing errors don't propagate."""
    storage = MockStorage()
    config = BotConfig(
        runtime=RuntimeConfig(
            mode=RunMode.DRY_RUN,
            log_level="INFO",
            data_dir="/tmp/test",
            github_adapter="test",
            discord_adapter="test",
            storage_adapter="test",
        ),
        github=GitHubConfig(org="test-org", token="test", api_base="https://api.github.com", permissions=PermissionConfig()),
        discord=DiscordConfig(guild_id="123", token="test", permissions=PermissionConfig()),
        scoring=ScoringConfig(period_days=30, weights={}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        assignments=AssignmentConfig(),
        snapshots=SnapshotConfig(enabled=True, repo_path="invalid"),  # Invalid format
    )
    github_writer = MockGitHubWriter()
    
    # Should not raise, just log warning
    write_snapshots_to_github(
        storage=storage,
        config=config,
        github_writer=github_writer,
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
    )
    
    # Should not have written files due to error
    assert len(github_writer.files_written) == 0


def _make_config(*, snapshots: "SnapshotConfig | None" = None) -> "BotConfig":
    return BotConfig(
        runtime=RuntimeConfig(
            mode=RunMode.DRY_RUN,
            log_level="INFO",
            data_dir="/tmp/test",
            github_adapter="test",
            discord_adapter="test",
            storage_adapter="test",
        ),
        github=GitHubConfig(org="test-org", token="test", api_base="https://api.github.com", permissions=PermissionConfig()),
        discord=DiscordConfig(guild_id="123", token="test", permissions=PermissionConfig()),
        scoring=ScoringConfig(period_days=30, weights={}),
        role_mappings=[RoleMappingConfig(discord_role="Contributor", min_score=10)],
        assignments=AssignmentConfig(),
        snapshots=snapshots,
    )


def test_raw_events_excluded_when_false() -> None:
    """events.json is not written when include_raw_events is False."""
    storage = MockStorage()
    storage.contributions = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="org/repo",
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
            payload={"pr_number": 1},
        )
    ]
    snapshots = _collect_snapshot_data(
        storage=storage,
        config=_make_config(),
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
        contribution_summaries=None,
        run_id="test-run",
        generated_at=datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC),
        include_raw_events=False,
    )
    assert "events.json" not in snapshots


def test_raw_events_included_when_enabled() -> None:
    """events.json is written with correct structure when include_raw_events=True."""
    storage = MockStorage()
    storage.contributions = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="org/repo",
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
            payload={"pr_number": 42},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="issue_opened",
            repo="org/repo",
            created_at=datetime(2024, 1, 20, tzinfo=UTC),
            payload={"issue_number": 7},
        ),
    ]
    snapshots = _collect_snapshot_data(
        storage=storage,
        config=_make_config(),
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
        contribution_summaries=None,
        run_id="test-run",
        generated_at=datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC),
        include_raw_events=True,
    )
    assert "events.json" in snapshots
    events_snapshot = snapshots["events.json"]
    assert events_snapshot["schema_version"] == SCHEMA_VERSION
    assert events_snapshot["org"] == "test-org"
    assert len(events_snapshot["data"]) == 2
    assert events_snapshot["data"][0]["github_user"] == "alice"
    assert events_snapshot["data"][0]["event_type"] == "pr_merged"
    assert events_snapshot["data"][0]["payload"] == {"pr_number": 42}
    assert events_snapshot["data"][1]["github_user"] == "bob"


def test_raw_events_respects_period_start() -> None:
    """Only events at or after period_start are included in events.json."""
    storage = MockStorage()
    period_start = datetime(2024, 1, 10, tzinfo=UTC)
    storage.contributions = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="org/repo",
            created_at=datetime(2024, 1, 5, tzinfo=UTC),  # before period_start
            payload={},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="org/repo",
            created_at=datetime(2024, 1, 15, tzinfo=UTC),  # within period
            payload={},
        ),
    ]
    snapshots = _collect_snapshot_data(
        storage=storage,
        config=_make_config(),
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=period_start,
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
        contribution_summaries=None,
        run_id="test-run",
        generated_at=datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC),
        include_raw_events=True,
    )
    events_data = snapshots["events.json"]["data"]
    assert len(events_data) == 1
    assert events_data[0]["github_user"] == "bob"


def test_raw_events_respects_period_end() -> None:
    """Only events at or before period_end are included in events.json."""
    storage = MockStorage()
    period_end = datetime(2024, 1, 31, tzinfo=UTC)
    storage.contributions = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="org/repo",
            created_at=datetime(2024, 1, 15, tzinfo=UTC),  # within period
            payload={},
        ),
        ContributionEvent(
            github_user="bob",
            event_type="pr_merged",
            repo="org/repo",
            created_at=datetime(2024, 2, 5, tzinfo=UTC),  # after period_end
            payload={},
        ),
    ]
    snapshots = _collect_snapshot_data(
        storage=storage,
        config=_make_config(),
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=period_end,
        contribution_summaries=None,
        run_id="test-run",
        generated_at=datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC),
        include_raw_events=True,
    )
    events_data = snapshots["events.json"]["data"]
    assert len(events_data) == 1
    assert events_data[0]["github_user"] == "alice"


def test_write_snapshots_raw_events_via_config() -> None:
    """include_raw_events=True in SnapshotConfig results in events.json being written."""
    storage = MockStorage()
    storage.contributions = [
        ContributionEvent(
            github_user="alice",
            event_type="pr_merged",
            repo="org/repo",
            created_at=datetime(2024, 1, 15, tzinfo=UTC),
            payload={"pr_number": 1},
        )
    ]
    config = _make_config(snapshots=SnapshotConfig(enabled=True, repo_path="org/repo", include_raw_events=True))
    github_writer = MockGitHubWriter()

    write_snapshots_to_github(
        storage=storage,
        config=config,
        github_writer=github_writer,
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
    )

    written_paths = [path for _, _, path, _ in github_writer.files_written]
    assert any("events.json" in p for p in written_paths)


def test_raw_events_graceful_when_storage_missing_method() -> None:
    """events.json is omitted gracefully if storage lacks list_contributions."""
    class MinimalStorage:
        def list_recent_notifications(self, _limit: int = 1000) -> list[dict]:
            return []
        def list_pending_issue_requests(self) -> list[dict]:
            return []

    storage = MinimalStorage()
    snapshots = _collect_snapshot_data(
        storage=storage,
        config=_make_config(),
        identity_mappings=[],
        scores=[],
        member_roles={},
        period_start=datetime(2024, 1, 1, tzinfo=UTC),
        period_end=datetime(2024, 1, 31, tzinfo=UTC),
        contribution_summaries=None,
        run_id="test-run",
        generated_at=datetime(2024, 1, 31, 12, 0, 0, tzinfo=UTC),
        include_raw_events=True,
    )
    assert "events.json" not in snapshots
