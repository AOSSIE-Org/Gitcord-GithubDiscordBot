#!/usr/bin/env python3
"""Debug script: check why repo-contributor role might not be assigned.
Run from repo root: python scripts/debug_repo_contributor_roles.py --config config/shubh-olrd.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure src is on path
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ghdcbot.config.loader import load_config
from ghdcbot.plugins.registry import build_adapter


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug repo-contributor role assignment")
    parser.add_argument("--config", required=True, help="Path to config YAML (e.g. config/shubh-olrd.yaml)")
    args = parser.parse_args()
    config = load_config(args.config)
    storage = build_adapter(
        config.runtime.storage_adapter,
        data_dir=config.runtime.data_dir,
    )
    storage.init_schema()

    repo_contributor_roles = getattr(config, "repo_contributor_roles", None) or {}
    if not repo_contributor_roles:
        print("No repo_contributor_roles in config. Add e.g. castro: \"Contributor-castro\"")
        return
    print("Config repo_contributor_roles:", repo_contributor_roles)
    print()

    # 1) Any pr_merged events for the configured repos?
    from datetime import datetime, timezone
    from ghdcbot.engine.planning import REPO_CONTRIBUTOR_EPOCH
    contributions = storage.list_contributions(REPO_CONTRIBUTOR_EPOCH)
    pr_merged_by_repo: dict[str, list[str]] = {}
    for e in contributions:
        if e.event_type == "pr_merged":
            pr_merged_by_repo.setdefault(e.repo, []).append(e.github_user)
    for repo in repo_contributor_roles:
        users = pr_merged_by_repo.get(repo, [])
        print(f"  Repo '{repo}' (-> role {repo_contributor_roles[repo]}): {len(users)} user(s) with merged PR: {users or 'none'}")
    if not any(pr_merged_by_repo.get(r) for r in repo_contributor_roles):
        print("\n  -> No pr_merged events found for configured repos. Ensure:")
        print("     - You ran run-once or /sync AFTER merging a PR in that repo.")
        print("     - The merge was within the last 30 days (or cursor covers that time).")
        print("     - GitHub org in config matches the repo owner (e.g. shubham-orld).")
    print()

    # 2) Verified identity mappings (used for role assignment)
    verified = list(storage.list_verified_identity_mappings()) if hasattr(storage, "list_verified_identity_mappings") else []
    print(f"Verified Discord <-> GitHub links: {len(verified)}")
    for m in verified:
        print(f"  Discord {m.discord_user_id} <-> GitHub {m.github_user}")
    if not verified:
        print("  -> No verified links. Either:")
        print("     - Use /link and /verify-link in Discord, OR")
        print("     - Set identity_mappings in config with real github_user and discord_user_id.")
    print()

    # 3) Who would get the role? (use same resolution as orchestrator: verified first, then config)
    from ghdcbot.engine.orchestrator import _resolve_identity_mappings
    identity_list = _resolve_identity_mappings(storage, getattr(config, "identity_mappings", []) or [])
    if not identity_list:
        print("No identity mappings (verified or config). Role cannot be assigned.")
        return
    from ghdcbot.engine.planning import repos_with_merged_pr_per_user
    repos_per_user = repos_with_merged_pr_per_user(storage, identity_list)
    for mapping in identity_list:
        gh = mapping.github_user
        dc = mapping.discord_user_id
        repos = repos_per_user.get(gh, set())
        roles_they_get = [repo_contributor_roles[r] for r in repo_contributor_roles if r in repos]
        print(f"  GitHub {gh} (Discord {dc}): repos with merged PR = {repos}; would get roles = {roles_they_get or 'none'}")


if __name__ == "__main__":
    main()
