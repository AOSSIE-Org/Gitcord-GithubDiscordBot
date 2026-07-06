#!/usr/bin/env python3
"""Undo Gitcord sync auto-assignments using a fixed TSV audit list.

TSV columns: repo<TAB>issue_number<TAB>github_username

Only removes the listed assignee from each issue. Does not touch other assignees.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

ORG = "AOSSIE-Org"
API = "https://api.github.com"


def load_rows(path: Path) -> list[tuple[str, int, str]]:
    rows: list[tuple[str, int, str]] = []
    for i, line in enumerate(path.read_text().splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise ValueError(f"{path}:{i}: expected 3 tab-separated columns, got {parts!r}")
        repo, num_s, user = parts
        rows.append((repo.strip(), int(num_s.strip()), user.strip()))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Rollback Gitcord auto issue assignments")
    parser.add_argument("--list", type=Path, required=True, help="TSV: repo, issue_number, assignee")
    parser.add_argument("--execute", action="store_true", help="Apply changes (default: dry-run)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between API calls")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("GITHUB_TOKEN is not set", file=sys.stderr)
        return 1

    rows = load_rows(args.list)
    print(f"Loaded {len(rows)} rollback rows from {args.list}")
    if not args.execute:
        print("DRY RUN — pass --execute to unassign")
        for repo, num, user in rows[:5]:
            print(f"  would unassign {user} from {ORG}/{repo}#{num}")
        if len(rows) > 5:
            print(f"  ... and {len(rows) - 5} more")
        return 0

    ok = 0
    skipped = 0
    failed: list[str] = []

    with httpx.Client(
        base_url=API,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        timeout=30.0,
    ) as client:
        for i, (repo, num, user) in enumerate(rows, start=1):
            path = f"/repos/{ORG}/{repo}/issues/{num}/assignees"
            label = f"{ORG}/{repo}#{num} ({user})"
            try:
                resp = client.request("DELETE", path, json={"assignees": [user]})
            except httpx.HTTPError as exc:
                failed.append(f"{label}: request error {exc}")
                print(f"[{i}/{len(rows)}] FAIL {label}: {exc}")
                time.sleep(args.delay)
                continue

            if resp.status_code in {200, 201}:
                assignees = [
                    str(a.get("login", "")).lower()
                    for a in resp.json().get("assignees", [])
                    if isinstance(a, dict) and a.get("login")
                ]
                if user.lower() in assignees:
                    failed.append(f"{label}: still assigned after DELETE")
                    print(f"[{i}/{len(rows)}] FAIL {label}: assignee still present")
                else:
                    ok += 1
                    print(f"[{i}/{len(rows)}] OK   {label}")
            elif resp.status_code == 404:
                skipped += 1
                print(f"[{i}/{len(rows)}] SKIP {label}: not found")
            elif resp.status_code == 403:
                failed.append(f"{label}: forbidden (403)")
                print(f"[{i}/{len(rows)}] FAIL {label}: 403 forbidden")
            else:
                body = (resp.text or "")[:200]
                failed.append(f"{label}: HTTP {resp.status_code} {body}")
                print(f"[{i}/{len(rows)}] FAIL {label}: HTTP {resp.status_code} {body}")

            time.sleep(args.delay)

    print("---")
    print(f"Done: ok={ok} skipped={skipped} failed={len(failed)} total={len(rows)}")
    if failed:
        print("Failures:")
        for item in failed:
            print(f"  - {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
