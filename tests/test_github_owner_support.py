"""Personal-account (user) and organization owner support.

All GitHub traffic is mocked with ``httpx.MockTransport``; nothing touches the network,
Discord, or any role mutation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import ValidationError

from ghdcbot.adapters.github import rest as rest_module
from ghdcbot.adapters.github.rest import GitHubRestAdapter
from ghdcbot.adapters.storage.sqlite import SqliteStorage
from ghdcbot.config import loader
from ghdcbot.config.models import (
    AssignmentConfig,
    BotConfig,
    DiscordConfig,
    GitHubConfig,
    IdentityMapping,
    MergeRoleRuleConfig,
    MergeRoleRulesConfig,
    RepoFilterConfig,
    RuntimeConfig,
)
from ghdcbot.core.github_owner import (
    GitHubOwner,
    OwnerResolutionError,
    normalize_owner_type,
    owner_from_users_response,
)
from ghdcbot.core.models import Score
from ghdcbot.engine.orchestrator import Orchestrator
from ghdcbot.engine.planning import count_merged_prs_per_user, plan_discord_roles
from ghdcbot.engine.pr_status import get_repo_suggestions

OWNER = "MystifiedSky"
API = "https://api.github.com"
PRIOR = datetime(2026, 11, 1, tzinfo=UTC)


def _repo(name: str, owner: str = OWNER, *, private: bool = False, archived: bool = False) -> dict:
    return {
        "id": abs(hash((owner, name))) % 10**8,
        "name": name,
        "full_name": f"{owner}/{name}",
        "owner": {"login": owner},
        "private": private,
        "archived": archived,
    }


def _config(owner: str = OWNER, owner_type: str = "auto", repos=None, **extra) -> BotConfig:
    return BotConfig(
        runtime=RuntimeConfig(
            mode="dry-run",
            data_dir="/tmp",
            github_adapter="ghdcbot.adapters.github.rest:GitHubRestAdapter",
            discord_adapter="ghdcbot.adapters.discord.api:DiscordApiAdapter",
            storage_adapter="ghdcbot.adapters.storage.sqlite:SqliteStorage",
        ),
        github=GitHubConfig(
            owner=owner, owner_type=owner_type, token="t", api_base=API, repos=repos, **extra
        ),
        discord=DiscordConfig(guild_id="1", token="t"),
        assignments=AssignmentConfig(),
        identity_mappings=[],
    )


class Recorder:
    """Route-table handler recording every request path."""

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.paths: list[str] = []
        self.queries: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        self.queries.append(str(request.url.params))
        route = self.routes.get(path)
        if route is None:
            return httpx.Response(200, json=[])
        if callable(route):
            return route(request)
        return route

    def called(self, prefix: str) -> bool:
        return any(p.startswith(prefix) for p in self.paths)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(rest_module.time, "sleep", lambda _s: None)


def make_adapter(
    handler,
    monkeypatch,
    *,
    owner: str = OWNER,
    owner_type: str = "auto",
    repos: RepoFilterConfig | None = None,
    token="t",
    **cfg_extra,
) -> GitHubRestAdapter:
    monkeypatch.setattr(loader, "_ACTIVE_CONFIG", _config(owner, owner_type, repos, **cfg_extra))
    adapter = GitHubRestAdapter(token=token, org=owner, api_base=API, owner_type=owner_type)
    adapter._client = httpx.Client(base_url=API, transport=httpx.MockTransport(handler))
    return adapter


USER_PROFILE = httpx.Response(200, json={"login": OWNER, "type": "User"})
ME = httpx.Response(200, json={"login": OWNER})


# --- 4. owner type detection -------------------------------------------------------------


def test_auto_detects_personal_account(monkeypatch) -> None:
    rec = Recorder({f"/users/{OWNER}": USER_PROFILE})
    adapter = make_adapter(rec, monkeypatch)
    owner = adapter.resolve_owner()
    assert owner == GitHubOwner(OWNER, "user")
    assert owner.search_qualifier == f"user:{OWNER}"


def test_auto_detects_organization_and_canonical_login(monkeypatch) -> None:
    rec = Recorder({"/users/aossie-org": httpx.Response(200, json={"login": "AOSSIE-Org", "type": "Organization"})})
    adapter = make_adapter(rec, monkeypatch, owner="aossie-org")
    owner = adapter.resolve_owner()
    assert owner == GitHubOwner("AOSSIE-Org", "organization")
    assert owner.search_qualifier == "org:AOSSIE-Org"
    assert owner.owns_login("aossie-org")


def test_explicit_owner_types_make_no_detection_call(monkeypatch) -> None:
    for kind in ("user", "organization"):
        rec = Recorder({})
        adapter = make_adapter(rec, monkeypatch, owner_type=kind)
        assert adapter.resolve_owner().type == kind
        assert rec.paths == []


def test_owner_type_normalisation_and_validation() -> None:
    assert normalize_owner_type("Org") == "organization"
    assert normalize_owner_type("personal") == "user"
    assert normalize_owner_type(None) == "auto"
    with pytest.raises(ValueError):
        normalize_owner_type("team")


# --- 5. invalid / inaccessible accounts -------------------------------------------------


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (404, "was not found"),
        (401, "rejected the configured credentials"),
        (403, "refused the lookup"),
        (429, "refused the lookup"),
        (None, "Could not reach GitHub"),
    ],
)
def test_owner_errors_are_distinct(status, needle) -> None:
    with pytest.raises(OwnerResolutionError) as exc:
        owner_from_users_response("ghost", status, None)
    assert needle in str(exc.value)
    if status != 404:
        assert "was not found" not in str(exc.value)


def test_nonexistent_account_blocks_ingestion_and_keeps_cursor_safe(monkeypatch, caplog) -> None:
    rec = Recorder({f"/users/{OWNER}": httpx.Response(404, json={"message": "Not Found"})})
    adapter = make_adapter(rec, monkeypatch)
    caplog.set_level(logging.ERROR)
    assert list(adapter.list_contributions(PRIOR)) == []
    assert adapter.sync_repo_listing_incomplete is True
    assert "was not found" in (adapter.last_owner_error or "")
    assert not rec.called("/orgs/")  # never falls back to another owner


def test_bad_token_is_not_reported_as_missing_account(monkeypatch) -> None:
    rec = Recorder({f"/users/{OWNER}": httpx.Response(401, json={"message": "Bad credentials"})})
    adapter = make_adapter(rec, monkeypatch)
    assert adapter.resolve_owner() is None
    assert "not found" not in adapter.last_owner_error
    assert "credentials" in adapter.last_owner_error


# --- 1/2. personal account discovery -------------------------------------------------------


def test_personal_account_public_repository_with_other_token_owner(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": httpx.Response(401, json={}),
            f"/users/{OWNER}/repos": httpx.Response(200, json=[_repo("GitCraft")]),
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    repos = list(adapter._list_repos())
    assert [r["full_name"] for r in repos] == [f"{OWNER}/GitCraft"]
    assert not rec.called("/orgs/")
    assert adapter.sync_repo_listing_incomplete is False


def test_personal_account_private_repo_via_authenticated_owner(monkeypatch) -> None:
    def user_repos(request: httpx.Request) -> httpx.Response:
        assert request.url.params["affiliation"] == "owner"
        assert request.url.params["visibility"] == "all"
        return httpx.Response(
            200, json=[_repo("GitCraft"), _repo("secret", private=True), _repo("other", "someone")]
        )

    rec = Recorder({f"/users/{OWNER}": USER_PROFILE, "/user": ME, "/user/repos": user_repos})
    adapter = make_adapter(rec, monkeypatch)
    names = [r["name"] for r in adapter._list_repos()]
    assert names == ["GitCraft", "secret"]  # other owner's repo never ingested


def test_token_of_another_user_adds_shared_private_repos_only_for_this_owner(monkeypatch) -> None:
    def user_repos(request: httpx.Request) -> httpx.Response:
        assert request.url.params["affiliation"] == "collaborator"
        return httpx.Response(
            200, json=[_repo("shared-private", private=True), _repo("x", "unrelated")]
        )

    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": httpx.Response(200, json={"login": "bot-account"}),
            f"/users/{OWNER}/repos": httpx.Response(200, json=[_repo("GitCraft")]),
            "/user/repos": user_repos,
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    assert sorted(r["name"] for r in adapter._list_repos()) == ["GitCraft", "shared-private"]


def test_github_app_token_lists_installation_repos(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": httpx.Response(403, json={}),
            f"/users/{OWNER}/repos": httpx.Response(200, json=[_repo("GitCraft")]),
            "/installation/repositories": httpx.Response(
                200, json={"repositories": [_repo("GitCraft"), _repo("hidden", private=True)]}
            ),
        }
    )
    adapter = make_adapter(rec, monkeypatch, token=lambda: "app-token")
    assert sorted(r["name"] for r in adapter._list_repos()) == ["GitCraft", "hidden"]


# --- 7. allow/deny lists ----------------------------------------------------------------------


def test_allowlist_uses_direct_repo_checks_not_listing(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            f"/repos/{OWNER}/gitcraft": httpx.Response(200, json=_repo("GitCraft", private=True)),
        }
    )
    adapter = make_adapter(
        rec, monkeypatch, repos=RepoFilterConfig(mode="allow", names=["gitcraft"])
    )
    repos = list(adapter._list_repos())
    assert [r["name"] for r in repos] == ["GitCraft"]  # case-insensitive, canonical casing kept
    assert not rec.called("/user/repos") and not rec.called(f"/users/{OWNER}/repos")


def test_allowlist_missing_repo_is_skipped_but_forbidden_marks_incomplete(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            f"/repos/{OWNER}/gone": httpx.Response(404, json={"message": "Not Found"}),
            f"/repos/{OWNER}/GitCraft": httpx.Response(200, json=_repo("GitCraft")),
        }
    )
    adapter = make_adapter(
        rec, monkeypatch, repos=RepoFilterConfig(mode="allow", names=["gone", "GitCraft"])
    )
    assert [r["name"] for r in adapter._list_repos()] == ["GitCraft"]
    assert adapter.sync_repo_listing_incomplete is False  # 404 = absent, not a failed read

    rec2 = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            f"/repos/{OWNER}/locked": httpx.Response(403, json={"message": "Forbidden"}),
        }
    )
    adapter2 = make_adapter(
        rec2, monkeypatch, repos=RepoFilterConfig(mode="allow", names=["locked"])
    )
    assert list(adapter2._list_repos()) == []
    assert adapter2._repo_listing_incomplete is True


def test_allowlist_repo_owned_by_someone_else_is_rejected(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            f"/repos/{OWNER}/moved": httpx.Response(200, json=_repo("moved", "new-owner")),
        }
    )
    adapter = make_adapter(
        rec, monkeypatch, repos=RepoFilterConfig(mode="allow", names=["moved"])
    )
    assert list(adapter._list_repos()) == []


def test_renamed_repository_redirect_is_followed(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            f"/repos/{OWNER}/OldName": httpx.Response(
                301, headers={"Location": f"{API}/repositories/42"}
            ),
            "/repositories/42": httpx.Response(200, json=_repo("NewName")),
        }
    )
    adapter = make_adapter(
        rec, monkeypatch, repos=RepoFilterConfig(mode="allow", names=["OldName"])
    )
    assert [r["name"] for r in adapter._list_repos()] == ["NewName"]


def test_denylist_applies_case_insensitively_to_user_listing(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": ME,
            "/user/repos": httpx.Response(200, json=[_repo("GitCraft"), _repo("Scratch")]),
        }
    )
    adapter = make_adapter(
        rec, monkeypatch, repos=RepoFilterConfig(mode="deny", names=["scratch"])
    )
    assert [r["name"] for r in adapter._list_repos()] == ["GitCraft"]


# --- 8/14. pagination, rate limiting, partial failures ---------------------------------------


def test_user_repo_listing_paginates(monkeypatch) -> None:
    def user_repos(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        if page == "1":
            return httpx.Response(
                200,
                json=[_repo("r1")],
                headers={"Link": f'<{API}/user/repos?page=2>; rel="next"'},
            )
        return httpx.Response(200, json=[_repo("r2")])

    rec = Recorder({f"/users/{OWNER}": USER_PROFILE, "/user": ME, "/user/repos": user_repos})
    adapter = make_adapter(rec, monkeypatch)
    assert [r["name"] for r in adapter._list_repos()] == ["r1", "r2"]
    assert adapter.sync_repo_listing_incomplete is False


def test_failed_second_page_keeps_first_page_but_flags_incomplete(monkeypatch) -> None:
    def user_repos(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page", "1") == "1":
            return httpx.Response(
                200,
                json=[_repo("r1")],
                headers={"Link": f'<{API}/user/repos?page=2>; rel="next"'},
            )
        return httpx.Response(403, json={"message": "nope"})

    rec = Recorder({f"/users/{OWNER}": USER_PROFILE, "/user": ME, "/user/repos": user_repos})
    adapter = make_adapter(rec, monkeypatch)
    adapter._sync_cached_repos = None
    list(adapter.list_contributions(PRIOR))
    assert adapter.sync_repo_listing_incomplete is True


def test_rate_limited_discovery_is_incomplete_not_empty_success(monkeypatch) -> None:
    exhausted = httpx.Response(
        429, json={"message": "rate limit"}, headers={"Retry-After": "1"}
    )
    rec = Recorder({f"/users/{OWNER}": USER_PROFILE, "/user": ME, "/user/repos": exhausted})
    adapter = make_adapter(rec, monkeypatch)
    adapter._sync_cached_repos = None
    assert list(adapter.list_contributions(PRIOR)) == []
    assert adapter.sync_repo_listing_incomplete is True


# --- 9/10. ingestion: PRs and issues -----------------------------------------------------------


def _pull(number: int, author: str, merged: bool = True) -> dict:
    stamp = "2026-11-10T00:00:00Z"
    return {
        "number": number,
        "created_at": stamp,
        "updated_at": stamp,
        "merged_at": stamp if merged else None,
        "merged_by": {"login": OWNER} if merged else None,
        "user": {"login": author},
        "title": f"PR {number}",
        "body": "",
        "state": "closed" if merged else "open",
        "html_url": f"https://github.com/{OWNER}/GitCraft/pull/{number}",
        "head": {"sha": f"sha{number}"},
        "base": {"ref": "main"},
    }


def _issue(number: int, author: str) -> dict:
    stamp = "2026-11-10T00:00:00Z"
    return {
        "number": number,
        "created_at": stamp,
        "updated_at": stamp,
        "user": {"login": author},
        "title": "issue",
        "state": "open",
        "labels": [],
        "html_url": "https://example.com",
        "assignees": [],
    }


def _ingest(monkeypatch, pulls, issues=()) -> list:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": ME,
            "/user/repos": httpx.Response(200, json=[_repo("GitCraft")]),
            f"/repos/{OWNER}/GitCraft/pulls": httpx.Response(200, json=list(pulls)),
            f"/repos/{OWNER}/GitCraft/issues": httpx.Response(200, json=list(issues)),
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    return list(adapter.list_contributions(PRIOR))


def test_personal_repo_activity_is_imported_with_correct_authors(monkeypatch) -> None:
    events = _ingest(
        monkeypatch,
        [_pull(1, "alice"), _pull(2, "bob", merged=False)],
        [_issue(7, "carol")],
    )
    merged = [e for e in events if e.event_type == "pr_merged"]
    assert [(e.github_user, e.repo) for e in merged] == [("alice", "GitCraft")]
    assert merged[0].payload["merged_by"] == OWNER
    opened = {(e.github_user, e.payload["pr_number"]) for e in events if e.event_type == "pr_opened"}
    assert opened == {("alice", 1), ("bob", 2)}
    issues = [e for e in events if e.event_type == "issue_opened"]
    assert [(e.github_user, e.payload["issue_number"]) for e in issues] == [("carol", 7)]


# --- 12. rewards: 1 / 5 / 15 merged PRs -> roles, no duplicates -------------------------------


def test_merged_pr_counts_drive_configured_roles_without_double_counting(
    monkeypatch, tmp_path
) -> None:
    pulls, number = [], 0
    for author, count in (("bob", 1), ("alice", 5), ("carol", 15)):
        for _ in range(count):
            number += 1
            pulls.append(_pull(number, author))
    storage = SqliteStorage(str(tmp_path))
    storage.init_schema()
    for _ in range(2):  # re-ingesting the same history must not inflate counts
        storage.record_contributions(_ingest(monkeypatch, pulls))

    end = datetime.now(UTC) + timedelta(days=1)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    identities = [
        IdentityMapping(github_user="bob", discord_user_id="1"),
        IdentityMapping(github_user="alice", discord_user_id="2"),
        IdentityMapping(github_user="carol", discord_user_id="3"),
    ]
    # Stored counts must stay 1 / 5 / 15 after ingesting the same history twice.
    assert count_merged_prs_per_user(storage, identities, start, end) == {
        "bob": 1,
        "alice": 5,
        "carol": 15,
    }
    rules = MergeRoleRulesConfig(
        enabled=True,
        rules=[
            MergeRoleRuleConfig(discord_role="Contributor", min_merged_prs=1),
            MergeRoleRuleConfig(discord_role="Active Contributor", min_merged_prs=5),
            MergeRoleRuleConfig(discord_role="Core Contributor", min_merged_prs=15),
        ],
    )
    plans = plan_discord_roles(
        member_roles={},
        scores=[Score(github_user=u, period_start=start, period_end=end, points=0) for u in ("bob", "alice", "carol")],
        identity_mappings=identities,
        role_mappings=[],
        storage=storage,
        period_start=start,
        period_end=end,
        merge_role_rules=rules,
    )
    granted: dict[str, set[str]] = {}
    for plan in plans:
        if plan.action == "add":
            granted.setdefault(plan.discord_user_id, set()).add(plan.role)
    assert "Core Contributor" not in granted.get("2", set())
    assert "Active Contributor" in granted["2"]
    assert "Core Contributor" in granted["3"]
    assert "Active Contributor" not in granted["1"] and "Contributor" in granted["1"]
    assert len([p for p in plans if p.discord_user_id == "1"]) == len(granted["1"])  # no dup plans


# --- 4/9. search scoping --------------------------------------------------------------------------


def _search_item(owner: str, repo: str, number: int, *, merged: bool = False) -> dict:
    return {
        "number": number,
        "title": "t",
        "state": "closed" if merged else "open",
        "user": {"login": "alice"},
        "html_url": f"https://github.com/{owner}/{repo}/pull/{number}",
        "repository_url": f"{API}/repos/{owner}/{repo}",
        "created_at": "2026-11-01T00:00:00Z",
        "updated_at": "2026-11-02T00:00:00Z",
        "pull_request": {"merged_at": "2026-11-02T00:00:00Z" if merged else None},
    }


def test_personal_account_search_uses_user_qualifier_and_drops_other_owners(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/search/issues": httpx.Response(
                200,
                json={
                    "items": [
                        _search_item(OWNER, "GitCraft", 1, merged=True),
                        _search_item("someone", "GitCraft", 2),
                    ]
                },
            ),
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    prs = adapter.list_pull_requests_for_author("alice")
    assert [(p["repo"], p["number"], p["status"]) for p in prs] == [("GitCraft", 1, "merged")]
    assert f"user:{OWNER}" in rec.queries[-1].replace("%3A", ":")
    assert "org:" not in rec.queries[-1].replace("%3A", ":")


def test_personal_account_allowlist_uses_repo_scoped_search(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/search/issues": httpx.Response(
                200, json={"items": [_search_item(OWNER, "GitCraft", 3)]}
            ),
        }
    )
    adapter = make_adapter(
        rec, monkeypatch, repos=RepoFilterConfig(mode="allow", names=["GitCraft"])
    )
    prs = adapter.list_open_pull_requests_for_author("alice")
    assert [p["number"] for p in prs] == [3]
    decoded = rec.queries[-1].replace("%3A", ":").replace("%2F", "/")
    assert f"repo:{OWNER}/GitCraft" in decoded and "user:" not in decoded and "org:" not in decoded


def test_organization_search_still_uses_org_qualifier(monkeypatch) -> None:
    rec = Recorder({"/search/issues": httpx.Response(200, json={"items": []})})
    adapter = make_adapter(rec, monkeypatch, owner="AOSSIE-Org", owner_type="organization")
    adapter.list_open_pull_requests_for_author("alice")
    assert "org:AOSSIE-Org" in rec.queries[-1].replace("%3A", ":")


# --- 13. org-only features degrade gracefully ---------------------------------------------------------


def _permission(level: str) -> httpx.Response:
    return httpx.Response(200, json={"permission": level})


def test_write_access_for_personal_repos_never_queries_org_membership(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            f"/repos/{OWNER}/GitCraft/collaborators/reader/permission": _permission("read"),
            f"/repos/{OWNER}/GitCraft/collaborators/writer/permission": _permission("write"),
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    assert adapter.has_write_access(OWNER, "GitCraft", "writer") is True
    assert adapter.has_write_access(OWNER, "GitCraft", "reader") is False
    assert not rec.called("/orgs/")


def test_write_access_for_organizations_still_honours_membership(monkeypatch) -> None:
    rec = Recorder(
        {
            "/repos/Org/r/collaborators/member/permission": _permission("read"),
            "/orgs/Org/members/member": httpx.Response(204),
        }
    )
    adapter = make_adapter(rec, monkeypatch, owner="Org", owner_type="organization")
    assert adapter.has_write_access("Org", "r", "member") is True


# --- 11. Discord repo autocomplete -----------------------------------------------------------------------


def test_repo_autocomplete_lists_personal_repositories(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": ME,
            "/user/repos": httpx.Response(200, json=[_repo("GitCraft"), _repo("Dotfiles")]),
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    config = loader.get_active_config()
    suggestions = asyncio.run(get_repo_suggestions(config, adapter, "git"))
    assert suggestions == ["GitCraft"]


def test_pr_channel_repo_listing_ignores_allowlist_and_archived(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": ME,
            "/user/repos": httpx.Response(
                200, json=[_repo("GitCraft"), _repo("old", archived=True), _repo("new-one")]
            ),
        }
    )
    adapter = make_adapter(
        rec, monkeypatch, repos=RepoFilterConfig(mode="allow", names=["GitCraft"])
    )
    assert adapter.list_all_org_repo_names() == ["GitCraft", "new-one"]


def test_pr_channel_repo_listing_raises_clear_error_for_unknown_owner(monkeypatch) -> None:
    rec = Recorder({f"/users/{OWNER}": httpx.Response(404, json={})})
    adapter = make_adapter(rec, monkeypatch)
    with pytest.raises(RuntimeError, match="was not found"):
        adapter.list_all_org_repo_names()


# --- 3/16. organizations and legacy configuration -----------------------------------------------------------


def test_organization_discovery_keeps_org_endpoint(monkeypatch) -> None:
    rec = Recorder(
        {
            "/users/AOSSIE-Org": httpx.Response(200, json={"login": "AOSSIE-Org", "type": "Organization"}),
            "/orgs/AOSSIE-Org/repos": httpx.Response(200, json=[_repo("Agora", "AOSSIE-Org")]),
        }
    )
    adapter = make_adapter(rec, monkeypatch, owner="AOSSIE-Org")
    assert [r["name"] for r in adapter._list_repos()] == ["Agora"]
    assert not rec.called("/user/repos")


def test_directly_constructed_adapter_keeps_legacy_org_behaviour() -> None:
    adapter = GitHubRestAdapter(token="t", org="Org", api_base=API)
    assert adapter.resolve_owner() == GitHubOwner("Org", "organization")  # no API call needed


def test_org_404_logs_hint_about_personal_accounts(monkeypatch, caplog) -> None:
    rec = Recorder({"/orgs/MystifiedSky/repos": httpx.Response(404, json={})})
    adapter = make_adapter(rec, monkeypatch, owner_type="organization")
    caplog.set_level(logging.ERROR)
    assert list(adapter._list_repos()) == []
    assert adapter.sync_repo_listing_incomplete is True
    assert any("personal account" in r.getMessage() for r in caplog.records)


def test_legacy_org_key_populates_owner_and_defaults_to_auto() -> None:
    cfg = GitHubConfig.model_validate({"org": "AOSSIE-Org", "token": "t"})
    assert (cfg.owner, cfg.org, cfg.owner_type) == ("AOSSIE-Org", "AOSSIE-Org", "auto")


def test_owner_key_also_exposes_org_for_existing_call_sites() -> None:
    cfg = GitHubConfig.model_validate({"owner": OWNER, "owner_type": "user"})
    assert cfg.org == OWNER and cfg.owner_type == "user"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"owner": "a", "org": "b"},
        {"owner": "a/b"},
        {"owner": "a", "owner_type": "team"},
    ],
)
def test_invalid_owner_configuration_is_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        GitHubConfig.model_validate(payload)


def test_owner_and_org_may_both_be_set_when_equal() -> None:
    cfg = GitHubConfig.model_validate({"owner": "Sky", "org": "sky"})
    assert cfg.owner == "Sky"


def test_example_personal_account_yaml_loads(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("DISCORD_TOKEN", "d")
    path = tmp_path / "c.yaml"
    path.write_text(
        f"""
runtime:
  mode: dry-run
  data_dir: {tmp_path}
  github_adapter: ghdcbot.adapters.github.rest:GitHubRestAdapter
  discord_adapter: ghdcbot.adapters.discord.api:DiscordApiAdapter
  storage_adapter: ghdcbot.adapters.storage.sqlite:SqliteStorage
github:
  owner: MystifiedSky
  owner_type: user
  token: ${{GITHUB_TOKEN}}
  repos:
    mode: allow
    names: [GitCraft]
discord:
  guild_id: "1"
  token: ${{DISCORD_TOKEN}}
assignments:
  review_roles: []
  issue_assignees: []
identity_mappings: []
""",
        encoding="utf-8",
    )
    config = loader.load_config(str(path))
    assert (config.github.owner, config.github.owner_type) == (OWNER, "user")
    assert config.github.repos.names == ["GitCraft"]


# --- 15. cursor is not advanced after incomplete discovery ------------------------------------------------


def _cursor_run(monkeypatch, tmp_path, *, second_page_status: int):
    """Page 1 of /user/repos has GitCraft (with fresh activity); page 2 returns the status."""
    fresh = (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def user_repos(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page", "1") == "1":
            return httpx.Response(
                200,
                json=[_repo("GitCraft")],
                headers={"Link": f'<{API}/user/repos?page=2>; rel="next"'},
            )
        return httpx.Response(second_page_status, json=[] if second_page_status == 200 else {})

    issue = {**_issue(7, "carol"), "created_at": fresh, "updated_at": fresh}
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": ME,
            "/user/repos": user_repos,
            f"/repos/{OWNER}/GitCraft/issues": httpx.Response(200, json=[issue]),
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    storage = SqliteStorage(data_dir=str(tmp_path))
    storage.init_schema()
    config = _config()
    config.runtime.enable_discord_role_updates = False
    orchestrator = Orchestrator(
        github_reader=adapter,
        github_writer=adapter,
        discord_reader=SimpleNamespace(list_member_roles=dict, close=lambda: None),
        discord_writer=MagicMock(),
        storage=storage,
        config=config,
    )
    prior = datetime.now(UTC) - timedelta(days=2)
    storage.set_cursor("github", prior)
    orchestrator.run_once()
    stored = [
        e for e in storage.list_contributions(prior) if e.event_type == "issue_opened"
    ]
    return storage, prior, stored


def test_cursor_held_but_activity_stored_when_discovery_fails_midway(
    monkeypatch, tmp_path
) -> None:
    storage, prior, stored = _cursor_run(monkeypatch, tmp_path, second_page_status=403)
    assert [(e.github_user, e.repo) for e in stored] == [("carol", "GitCraft")]
    assert storage.get_cursor("github") == prior  # unread repos on page 2 stay re-readable


def test_cursor_advances_when_discovery_completes(monkeypatch, tmp_path) -> None:
    storage, prior, stored = _cursor_run(monkeypatch, tmp_path, second_page_status=200)
    assert len(stored) == 1
    assert storage.get_cursor("github") > prior


# --- discovery completeness (review follow-ups) -------------------------------------------------------


def test_installation_listing_failure_of_any_kind_marks_incomplete(monkeypatch) -> None:
    for status in (403, 500, 404):
        rec = Recorder(
            {
                f"/users/{OWNER}": USER_PROFILE,
                "/user": httpx.Response(403, json={}),
                f"/users/{OWNER}/repos": httpx.Response(200, json=[_repo("GitCraft")]),
                "/installation/repositories": httpx.Response(status, json={}),
            }
        )
        adapter = make_adapter(rec, monkeypatch, token=lambda: "app-token")
        adapter._sync_cached_repos = None
        assert [r["name"] for r in adapter._list_repos()] == ["GitCraft"]
        assert adapter._repo_listing_incomplete is True, status


def test_identity_probe_failure_is_not_cached_and_holds_the_cursor(monkeypatch) -> None:
    state = {"status": 500}

    def me(_request: httpx.Request) -> httpx.Response:
        if state["status"] == 200:
            return ME
        return httpx.Response(state["status"], json={})

    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": me,
            f"/users/{OWNER}/repos": httpx.Response(200, json=[_repo("GitCraft")]),
            "/user/repos": httpx.Response(200, json=[_repo("GitCraft"), _repo("secret")]),
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    assert [r["name"] for r in adapter._list_repos()] == ["GitCraft"]  # public still returned
    assert adapter._repo_listing_incomplete is True
    assert adapter._authenticated_login_known is False

    state["status"] = 200  # same adapter recovers: private repo appears, nothing was cached
    adapter._repo_listing_incomplete = False
    assert [r["name"] for r in adapter._list_repos()] == ["GitCraft", "secret"]
    assert adapter._repo_listing_incomplete is False


def test_definitive_non_user_credentials_are_cached_without_flagging(monkeypatch) -> None:
    rec = Recorder(
        {
            f"/users/{OWNER}": USER_PROFILE,
            "/user": httpx.Response(403, json={}),
            f"/users/{OWNER}/repos": httpx.Response(200, json=[_repo("GitCraft")]),
        }
    )
    adapter = make_adapter(rec, monkeypatch)
    list(adapter._list_repos())
    list(adapter._list_repos())
    assert adapter._repo_listing_incomplete is False
    assert rec.paths.count("/user") == 1


def test_multi_repo_search_results_are_globally_ordered_by_update(monkeypatch) -> None:
    def search(request: httpx.Request) -> httpx.Response:
        query = request.url.params["q"]
        repo, stamp = (
            ("A", "2026-11-01T00:00:00Z") if "/A" in query else ("B", "2026-11-09T00:00:00Z")
        )
        item = _search_item(OWNER, repo, 1)
        item["updated_at"] = stamp
        return httpx.Response(200, json={"items": [item]})

    rec = Recorder({f"/users/{OWNER}": USER_PROFILE, "/search/issues": search})
    adapter = make_adapter(
        rec, monkeypatch, repos=RepoFilterConfig(mode="allow", names=["A", "B"])
    )
    prs = adapter.list_pull_requests_for_author("alice")
    assert [p["repo"] for p in prs] == ["B", "A"]  # newest first across both searches
