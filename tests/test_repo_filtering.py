import logging

from ghdcbot.adapters.github.rest import _apply_repo_filter
from ghdcbot.config.models import RepoFilterConfig


def test_repo_filter_allow_mode(caplog) -> None:
    repos = [
        {"name": "repo-a"},
        {"name": "repo-b"},
        {"name": "repo-c"},
    ]
    caplog.set_level(logging.INFO)
    repo_filter = RepoFilterConfig(mode="allow", names=["repo-a", "repo-c"])

    filtered = _apply_repo_filter(repos, repo_filter, logging.getLogger("test"))

    assert [repo["name"] for repo in filtered] == ["repo-a", "repo-c"]


def test_repo_filter_deny_mode() -> None:
    repos = [
        {"name": "repo-a"},
        {"name": "repo-b"},
        {"name": "repo-c"},
    ]
    repo_filter = RepoFilterConfig(mode="deny", names=["repo-b"])

    filtered = _apply_repo_filter(repos, repo_filter, logging.getLogger("test"))

    assert [repo["name"] for repo in filtered] == ["repo-a", "repo-c"]


def test_repo_filter_empty_logs_warning(caplog) -> None:
    repos = [{"name": "repo-a"}]
    repo_filter = RepoFilterConfig(mode="allow", names=["missing"])

    caplog.set_level(logging.WARNING)
    filtered = _apply_repo_filter(repos, repo_filter, logging.getLogger("test"))

    assert filtered == []
    assert any("All repositories filtered out" in record.message for record in caplog.records)
