from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator, Sequence

import httpx

from ghdcbot.config.loader import get_active_config
from ghdcbot.config.models import RepoFilterConfig
from ghdcbot.core.models import ContributionEvent


@dataclass(frozen=True)
class RateLimitStatus:
    remaining: int | None
    reset_at: datetime | None


class GitHubRestAdapter:
    def __init__(self, token: str, org: str, api_base: str) -> None:
        self._logger = logging.getLogger(self.__class__.__name__)
        self._org = org
        self._last_repo_count: int | None = None
        self._client = httpx.Client(
            base_url=api_base,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "GitHubRestAdapter":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def list_contributions(self, since: datetime) -> Iterable[ContributionEvent]:
        self._logger.info(
            "Starting GitHub ingestion",
            extra={"org": self._org, "since": since.isoformat()},
        )
        for repo in self._list_repos():
            yield from self._ingest_repo(repo, since)

    def list_open_issues(self) -> Iterable[dict]:
        for repo in self._list_repos():
            yield from self._list_repo_open_issues(repo)

    def list_open_pull_requests(self) -> Iterable[dict]:
        for repo in self._list_repos():
            yield from self._list_repo_open_prs(repo)

    def assign_issue(self, repo: str, issue_number: int, assignee: str) -> None:
        self._logger.info(
            "GitHub issue assignment stub",
            extra={"repo": repo, "issue_number": issue_number, "assignee": assignee},
        )

    def request_review(self, repo: str, pr_number: int, reviewer: str) -> None:
        self._logger.info(
            "GitHub review request stub",
            extra={"repo": repo, "pr_number": pr_number, "reviewer": reviewer},
        )

    def _ingest_repo(self, repo: dict, since: datetime) -> Iterable[ContributionEvent]:
        repo_name = repo["name"]
        owner = repo["owner"]["login"]
        full_name = repo["full_name"]
        self._logger.info("Ingesting repository", extra={"repo": full_name})

        issue_events, issue_numbers = self._collect_issue_events(owner, repo_name, since)
        pr_events, pr_numbers, pr_opened_count = self._collect_pull_request_events(
            owner, repo_name, since
        )
        issue_comment_events = list(
            self._ingest_issue_comments(owner, repo_name, issue_numbers, since)
        )
        pr_comment_events = list(
            self._ingest_pr_comments(owner, repo_name, pr_numbers, since)
        )
        # Emit helpful_comment events for non-author comments
        helpful_comment_events = list(
            self._ingest_helpful_comments(owner, repo_name, issue_numbers, pr_numbers, since)
        )
        self._logger.info(
            "Ingestion results",
            extra={
                "repo": full_name,
                "issue_events": len(issue_events),
                "pr_events": len(pr_events),
                "comment_events": len(issue_comment_events) + len(pr_comment_events),
            },
        )
        if pr_opened_count:
            self._logger.info(
                "Emitted pr_opened event",
                extra={"repo": full_name, "count": pr_opened_count},
            )
        yield from issue_events
        yield from issue_comment_events
        yield from pr_events
        yield from pr_comment_events
        yield from helpful_comment_events

    def _list_repos(self) -> Sequence[dict]:
        repos, status = self._list_repos_from_path(f"/orgs/{self._org}/repos")
        if status == 200 and not repos:
            self._logger.info("Organization has no repositories yet", extra={"org": self._org})
        user_fallback = _load_user_fallback()
        if user_fallback and (status in {401, 403} or (status == 200 and not repos)):
            self._logger.info("Falling back to user repositories (not an org member)")
            repos, _ = self._list_repos_from_path("/user/repos")

        self._last_repo_count = len(repos)
        if not repos:
            self._logger.warning("No repositories discovered", extra={"org": self._org})

        filtered = _apply_repo_filter(repos, _load_repo_filter(), self._logger)
        if not filtered:
            self._logger.warning(
                "All repositories filtered out; skipping ingestion",
                extra={"org": self._org},
            )
        return filtered

    def _collect_issue_events(
        self, owner: str, repo: str, since: datetime
    ) -> tuple[list[ContributionEvent], list[int]]:
        issue_events: list[ContributionEvent] = []
        issue_numbers: list[int] = []
        params = {"state": "all", "since": since.isoformat(), "per_page": 100}
        for page in self._paginate(f"/repos/{owner}/{repo}/issues", params=params):
            for issue in page:
                if "pull_request" in issue:
                    continue
                issue_numbers.append(issue["number"])
                issue_events.extend(self._issue_events(owner, repo, issue, since))
        return issue_events, issue_numbers

    def _collect_pull_request_events(
        self, owner: str, repo: str, since: datetime
    ) -> tuple[list[ContributionEvent], list[int], int]:
        pr_events: list[ContributionEvent] = []
        pr_numbers: list[int] = []
        pr_opened_count = 0
        params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}
        for page in self._paginate(f"/repos/{owner}/{repo}/pulls", params=params):
            for pr in page:
                updated_at = _parse_iso8601(pr.get("updated_at"))
                if updated_at and updated_at < since:
                    return pr_events, pr_numbers, pr_opened_count
                pr_numbers.append(pr["number"])
                created_at = _parse_iso8601(pr.get("created_at"))
                if created_at and created_at >= since:
                    pr_author = pr.get("user") or {}
                    pr_author_login = pr_author.get("login") or "<deleted>"
                    pr_events.append(
                        ContributionEvent(
                            github_user=pr_author_login,
                            event_type="pr_opened",
                            repo=repo,
                            created_at=created_at,
                            payload={
                                "pr_number": pr["number"],
                                "title": pr.get("title"),
                                "created_at": pr.get("created_at"),
                            },
                        )
                    )
                    pr_opened_count += 1
                merged_at = _parse_iso8601(pr.get("merged_at"))
                if merged_at and merged_at >= since:
                    author = (pr.get("user") or {}).get("login") or "<deleted>"
                    # Extract linked issue numbers and fetch difficulty labels
                    pr_body = pr.get("body") or ""
                    linked_issue_numbers = _extract_linked_issue_numbers(pr_body)
                    difficulty_labels = []
                    if linked_issue_numbers:
                        difficulty_labels = self._fetch_issue_difficulty_labels(
                            owner, repo, linked_issue_numbers
                        )
                    # Check CI status
                    ci_failed = _check_pr_ci_status(pr, owner, repo, self._client)
                    payload = {
                        "pr_number": pr["number"],
                        "title": pr.get("title"),
                        "merged_at": pr.get("merged_at"),
                    }
                    if difficulty_labels:
                        payload["difficulty_labels"] = difficulty_labels
                    if ci_failed:
                        payload["ci_failed"] = True
                    pr_events.append(
                        ContributionEvent(
                            github_user=author,
                            event_type="pr_merged",
                            repo=repo,
                            created_at=merged_at,
                            payload=payload,
                        )
                    )
                    # Emit pr_merged_with_failed_ci if CI failed
                    if ci_failed:
                        pr_events.append(
                            ContributionEvent(
                                github_user=author,
                                event_type="pr_merged_with_failed_ci",
                                repo=repo,
                                created_at=merged_at,
                                payload={
                                    "pr_number": pr["number"],
                                    "merged_at": pr.get("merged_at"),
                                },
                            )
                        )
                # Check if this PR reverts another PR (whether merged or not)
                reverted_pr_number = _detect_reverted_pr(pr, owner, repo, self._client)
                if reverted_pr_number:
                    # Fetch the reverted PR to check if it was merged
                    try:
                        revert_response = self._client.get(
                            f"/repos/{owner}/{repo}/pulls/{reverted_pr_number}",
                            headers={"Accept": "application/vnd.github+json"},
                        )
                        if revert_response.status_code == 200:
                            reverted_pr = revert_response.json()
                            reverted_merged_at = _parse_iso8601(reverted_pr.get("merged_at"))
                            if reverted_merged_at and reverted_merged_at >= since:
                                reverted_author = (reverted_pr.get("user") or {}).get("login") or "<deleted>"
                                # Emit pr_reverted event for the original author
                                pr_events.append(
                                    ContributionEvent(
                                        github_user=reverted_author,
                                        event_type="pr_reverted",
                                        repo=repo,
                                        created_at=reverted_merged_at,  # Use original merge time
                                        payload={
                                            "pr_number": reverted_pr_number,
                                            "reverted_by_pr": pr["number"],
                                            "reverted_at": pr.get("created_at"),
                                        },
                                    )
                                )
                    except Exception as exc:  # noqa: BLE001
                        # Network errors, etc. - skip revert detection but log for debugging
                        self._logger.debug(
                            "Failed to fetch reverted PR for revert detection",
                            exc_info=True,
                            extra={
                                "owner": owner,
                                "repo": repo,
                                "reverted_pr_number": reverted_pr_number,
                                "reverting_pr_number": pr["number"],
                                "error": str(exc),
                            },
                        )
                pr_events.extend(self._pull_request_reviews(owner, repo, pr["number"], since))
        return pr_events, pr_numbers, pr_opened_count

    def _fetch_issue_difficulty_labels(
        self, owner: str, repo: str, issue_numbers: list[int]
    ) -> list[str]:
        """Fetch labels from linked issues and return difficulty labels only.
        
        Returns list of difficulty label names (case-normalized) found in any linked issue.
        If an issue doesn't exist or API call fails, it's silently skipped.
        """
        difficulty_labels = []
        for issue_number in issue_numbers:
            try:
                response = self._client.get(
                    f"/repos/{owner}/{repo}/issues/{issue_number}",
                    headers={"Accept": "application/vnd.github+json"},
                )
                if response.status_code == 200:
                    issue = response.json()
                    labels = issue.get("labels", [])
                    for label in labels:
                        label_name = label.get("name", "").lower() if isinstance(label, dict) else str(label).lower()
                        if label_name:
                            difficulty_labels.append(label_name)
                elif response.status_code == 404:
                    # Issue doesn't exist or is a PR (which is fine, skip it)
                    continue
                # Other errors: log but don't fail
                elif response.status_code not in (200, 404):
                    self._logger.debug(
                        "Failed to fetch issue labels",
                        extra={
                            "repo": f"{owner}/{repo}",
                            "issue_number": issue_number,
                            "status": response.status_code,
                        },
                    )
            except Exception:  # noqa: BLE001
                # Network errors, etc. - skip this issue
                self._logger.debug(
                    "Error fetching issue labels",
                    extra={"repo": f"{owner}/{repo}", "issue_number": issue_number},
                    exc_info=True,
                )
        return difficulty_labels

    def _pull_request_reviews(
        self, owner: str, repo: str, pr_number: int, since: datetime
    ) -> Iterable[ContributionEvent]:
        params = {"per_page": 100}
        for page in self._paginate(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews", params=params
        ):
            for review in page:
                submitted_at = _parse_iso8601(review.get("submitted_at"))
                if not submitted_at or submitted_at < since:
                    continue
                user = review.get("user")
                if not user:
                    continue
                reviewer = user.get("login")
                if not reviewer:
                    continue
                yield ContributionEvent(
                    github_user=reviewer,
                    event_type="pr_reviewed",
                    repo=repo,
                    created_at=submitted_at,
                    payload={
                        "pr_number": pr_number,
                        "review_id": review.get("id"),
                        "state": review.get("state"),
                        "submitted_at": review.get("submitted_at"),
                    },
                )

    def _ingest_issue_comments(
        self, owner: str, repo: str, issue_numbers: Sequence[int], since: datetime
    ) -> Iterable[ContributionEvent]:
        if not issue_numbers:
            return []
        self._logger.info(
            "Ingesting issue comments",
            extra={"repo": f"{owner}/{repo}", "issues": len(issue_numbers)},
        )
        comment_events: list[ContributionEvent] = []
        for issue_number in issue_numbers:
            params = {"per_page": 100}
            for page in self._paginate(
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments", params=params
            ):
                for comment in page:
                    created_at = _parse_iso8601(comment.get("created_at"))
                    if not created_at or created_at < since:
                        continue
                    user = comment.get("user") or {}
                    if _is_bot_user(user):
                        continue
                    login = user.get("login")
                    if not login:
                        continue
                    comment_events.append(
                        ContributionEvent(
                            github_user=login,
                            event_type="comment",
                            repo=repo,
                            created_at=created_at,
                            payload={
                                "issue_number": issue_number,
                                "comment_id": comment.get("id"),
                                "url": comment.get("html_url"),
                            },
                        )
                    )
        if comment_events:
            self._logger.info(
                "Emitted comment events",
                extra={"repo": f"{owner}/{repo}", "count": len(comment_events), "source": "issue"},
            )
        return comment_events

    def _ingest_pr_comments(
        self, owner: str, repo: str, pr_numbers: Sequence[int], since: datetime
    ) -> Iterable[ContributionEvent]:
        if not pr_numbers:
            return []
        self._logger.info(
            "Ingesting PR comments",
            extra={"repo": f"{owner}/{repo}", "prs": len(pr_numbers)},
        )
        comment_events: list[ContributionEvent] = []
        seen: set[tuple[str, int]] = set()
        for pr_number in pr_numbers:
            params = {"per_page": 100}
            paths = [
                f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            ]
            for path in paths:
                for page in self._paginate(path, params=params):
                    for comment in page:
                        comment_id = comment.get("id")
                        if comment_id is None:
                            continue
                        key = (repo, int(comment_id))
                        if key in seen:
                            continue
                        created_at = _parse_iso8601(comment.get("created_at"))
                        if not created_at or created_at < since:
                            continue
                        user = comment.get("user") or {}
                        if _is_bot_user(user):
                            continue
                        login = user.get("login")
                        if not login:
                            continue
                        seen.add(key)
                        comment_events.append(
                            ContributionEvent(
                                github_user=login,
                                event_type="comment",
                                repo=repo,
                                created_at=created_at,
                                payload={
                                    "issue_number": pr_number,
                                    "comment_id": comment_id,
                                    "url": comment.get("html_url"),
                                },
                            )
                        )
        if comment_events:
            self._logger.info(
                "Emitted comment events",
                extra={"repo": f"{owner}/{repo}", "count": len(comment_events), "source": "pr"},
            )
        return comment_events

    def _ingest_helpful_comments(
        self,
        owner: str,
        repo: str,
        issue_numbers: Sequence[int],
        pr_numbers: Sequence[int],
        since: datetime,
    ) -> Iterable[ContributionEvent]:
        """Emit helpful_comment events for non-author comments on issues and PRs.
        
        A comment is "helpful" if:
        - It's on an issue/PR
        - The commenter is not the issue/PR author
        - It's not a bot comment
        
        Bonus is capped per PR/issue (max 5 helpful comments count for bonus).
        """
        helpful_events: list[ContributionEvent] = []
        # Track authors for issues and PRs
        issue_authors: dict[int, str] = {}
        pr_authors: dict[int, str] = {}
        
        # Fetch issue authors
        for issue_number in issue_numbers:
            try:
                response = self._client.get(
                    f"/repos/{owner}/{repo}/issues/{issue_number}",
                    headers={"Accept": "application/vnd.github+json"},
                )
                if response.status_code == 200:
                    issue = response.json()
                    author = (issue.get("user") or {}).get("login")
                    if author:
                        issue_authors[issue_number] = author
            except Exception:  # noqa: BLE001
                pass
        
        # Fetch PR authors
        for pr_number in pr_numbers:
            try:
                response = self._client.get(
                    f"/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers={"Accept": "application/vnd.github+json"},
                )
                if response.status_code == 200:
                    pr = response.json()
                    author = (pr.get("user") or {}).get("login")
                    if author:
                        pr_authors[pr_number] = author
            except Exception:  # noqa: BLE001
                pass
        
        # Process issue comments
        for issue_number in issue_numbers:
            params = {"per_page": 100}
            helpful_count = 0
            for page in self._paginate(
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments", params=params
            ):
                for comment in page:
                    created_at = _parse_iso8601(comment.get("created_at"))
                    if not created_at or created_at < since:
                        continue
                    user = comment.get("user") or {}
                    if _is_bot_user(user):
                        continue
                    commenter = user.get("login")
                    if not commenter:
                        continue
                    author = issue_authors.get(issue_number)
                    if author and commenter != author and helpful_count < 5:
                        helpful_events.append(
                            ContributionEvent(
                                github_user=commenter,
                                event_type="helpful_comment",
                                repo=repo,
                                created_at=created_at,
                                payload={
                                    "issue_number": issue_number,
                                    "comment_id": comment.get("id"),
                                    "target_type": "issue",
                                },
                            )
                        )
                        helpful_count += 1
        
        # Process PR comments
        for pr_number in pr_numbers:
            params = {"per_page": 100}
            helpful_count = 0
            paths = [
                f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
                f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
            ]
            for path in paths:
                for page in self._paginate(path, params=params):
                    for comment in page:
                        created_at = _parse_iso8601(comment.get("created_at"))
                        if not created_at or created_at < since:
                            continue
                        user = comment.get("user") or {}
                        if _is_bot_user(user):
                            continue
                        commenter = user.get("login")
                        if not commenter:
                            continue
                        author = pr_authors.get(pr_number)
                        if author and commenter != author and helpful_count < 5:
                            helpful_events.append(
                                ContributionEvent(
                                    github_user=commenter,
                                    event_type="helpful_comment",
                                    repo=repo,
                                    created_at=created_at,
                                    payload={
                                        "pr_number": pr_number,
                                        "comment_id": comment.get("id"),
                                        "target_type": "pull_request",
                                    },
                                )
                            )
                            helpful_count += 1
        
        return helpful_events

    def _issue_events(
        self, owner: str, repo: str, issue: dict, since: datetime
    ) -> Iterable[ContributionEvent]:
        issue_user = issue.get("user") or {}
        issue_author = issue_user.get("login") or "unknown"
        created_at = _parse_iso8601(issue.get("created_at"))
        if created_at and created_at >= since:
            yield ContributionEvent(
                github_user=issue_author,
                event_type="issue_opened",
                repo=repo,
                created_at=created_at,
                payload=_issue_payload(issue),
            )
        closed_at = _parse_iso8601(issue.get("closed_at"))
        if closed_at and closed_at >= since:
            closer = (issue.get("closed_by") or {}).get("login") or issue_author
            yield ContributionEvent(
                github_user=closer,
                event_type="issue_closed",
                repo=repo,
                created_at=closed_at,
                payload=_issue_payload(issue),
            )
        yield from self._issue_assignment_events(owner, repo, issue, since)

    def _issue_assignment_events(
        self, owner: str, repo: str, issue: dict, since: datetime
    ) -> Iterable[ContributionEvent]:
        """Fetch issue timeline events for 'assigned' and emit ContributionEvent for each."""
        issue_number = issue.get("number")
        if not issue_number:
            return
        try:
            params = {"per_page": 100}
            for page in self._paginate(
                f"/repos/{owner}/{repo}/issues/{issue_number}/timeline", params=params
            ):
                for event in page:
                    if event.get("event") != "assigned":
                        continue
                    created_at = _parse_iso8601(event.get("created_at"))
                    if not created_at or created_at < since:
                        continue
                    assignee = event.get("assignee")
                    if not assignee or not isinstance(assignee, dict):
                        continue
                    assignee_login = assignee.get("login")
                    if not assignee_login:
                        continue
                    yield ContributionEvent(
                        github_user=assignee_login,
                        event_type="issue_assigned",
                        repo=repo,
                        created_at=created_at,
                        payload=_issue_payload(issue),
                    )
        except Exception:
            # If timeline call fails, omit assignment events to avoid wrong timestamps
            pass

    def _list_repo_open_issues(self, repo: dict) -> Iterable[dict]:
        owner = repo["owner"]["login"]
        repo_name = repo["name"]
        params = {"state": "open", "per_page": 100}
        for page in self._paginate(f"/repos/{owner}/{repo_name}/issues", params=params):
            for issue in page:
                if "pull_request" in issue:
                    continue
                yield {"repo": repo["name"], "number": issue["number"]}

    def _list_repo_open_prs(self, repo: dict) -> Iterable[dict]:
        owner = repo["owner"]["login"]
        repo_name = repo["name"]
        params = {"state": "open", "per_page": 100}
        for page in self._paginate(f"/repos/{owner}/{repo_name}/pulls", params=params):
            for pr in page:
                yield {"repo": repo["name"], "number": pr["number"]}

    def _paginate(self, path: str, params: dict) -> Iterator[list]:
        page = 1
        while True:
            response = self._request("GET", path, params={**params, "page": page})
            if response is None:
                return
            if response.status_code != 200:
                self._logger.warning(
                    "GitHub request failed",
                    extra={"path": path, "status_code": response.status_code},
                )
                return
            data = response.json()
            if not isinstance(data, list) or not data:
                return
            yield data
            if not _has_next_page(response.headers.get("Link")):
                return
            page += 1

    def _list_repos_from_path(self, path: str) -> tuple[list[dict], int | None]:
        repos: list[dict] = []
        params = {"per_page": 100, "page": 1}
        response = self._request_with_status("GET", path, params=params)
        if response is None:
            return [], None
        if response.status_code != 200:
            return [], response.status_code
        data = response.json()
        if isinstance(data, list):
            repos.extend(data)
        if _has_next_page(response.headers.get("Link")):
            for page in self._paginate_from_page(path, {"per_page": 100}, start_page=2):
                repos.extend(page)
        return repos, response.status_code

    def _paginate_from_page(self, path: str, params: dict, start_page: int) -> Iterator[list]:
        page = start_page
        while True:
            response = self._request("GET", path, params={**params, "page": page})
            if response is None:
                return
            if response.status_code != 200:
                self._logger.warning(
                    "GitHub request failed",
                    extra={"path": path, "status_code": response.status_code},
                )
                return
            data = response.json()
            if not isinstance(data, list) or not data:
                return
            yield data
            if not _has_next_page(response.headers.get("Link")):
                return
            page += 1

    def _request(self, method: str, path: str, params: dict) -> httpx.Response | None:
        try:
            response = self._client.request(method, path, params=params)
        except httpx.HTTPError as exc:
            self._logger.warning("GitHub request failed", extra={"path": path, "error": str(exc)})
            return None

        rate_limit = _parse_rate_limit(response.headers)
        if rate_limit.remaining is not None and rate_limit.remaining <= 1:
            self._logger.warning(
                "GitHub rate limit nearly exhausted",
                extra={
                    "path": path,
                    "remaining": rate_limit.remaining,
                    "reset_at": rate_limit.reset_at.isoformat()
                    if rate_limit.reset_at
                    else None,
                },
            )

        if response.status_code == 403:
            self._log_permission_issue(path, response)
            return None
        if response.status_code == 404:
            self._log_not_found(path, response)
            return None

        return response

    def _request_with_status(self, method: str, path: str, params: dict) -> httpx.Response | None:
        try:
            response = self._client.request(method, path, params=params)
        except httpx.HTTPError as exc:
            self._logger.warning("GitHub request failed", extra={"path": path, "error": str(exc)})
            return None

        rate_limit = _parse_rate_limit(response.headers)
        if rate_limit.remaining is not None and rate_limit.remaining <= 1:
            self._logger.warning(
                "GitHub rate limit nearly exhausted",
                extra={
                    "path": path,
                    "remaining": rate_limit.remaining,
                    "reset_at": rate_limit.reset_at.isoformat()
                    if rate_limit.reset_at
                    else None,
                },
            )

        if response.status_code in {401, 403}:
            self._log_permission_issue(path, response)
        if response.status_code == 404:
            self._log_not_found(path, response)
        return response

    def _log_permission_issue(self, path: str, response: httpx.Response) -> None:
        self._logger.warning(
            "GitHub permission or visibility issue",
            extra={
                "path": path,
                "status_code": response.status_code,
                "response_message": response.text[:200],
            },
        )

    def _log_not_found(self, path: str, response: httpx.Response) -> None:
        self._logger.warning(
            "GitHub resource not found",
            extra={
                "path": path,
                "status_code": response.status_code,
                "response_message": response.text[:200],
            },
        )


def _parse_rate_limit(headers: dict) -> RateLimitStatus:
    remaining = headers.get("X-RateLimit-Remaining")
    reset = headers.get("X-RateLimit-Reset")
    remaining_val = int(remaining) if remaining and remaining.isdigit() else None
    reset_at = (
        datetime.fromtimestamp(int(reset), tz=timezone.utc) if reset and reset.isdigit() else None
    )
    return RateLimitStatus(remaining=remaining_val, reset_at=reset_at)


def _has_next_page(link_header: str | None) -> bool:
    if not link_header:
        return False
    return 'rel="next"' in link_header


def _parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_bot_user(user: dict) -> bool:
    user_type = (user.get("type") or "").lower()
    login = (user.get("login") or "").lower()
    return user_type == "bot" or login.endswith("[bot]")


def _extract_linked_issue_numbers(pr_body: str) -> list[int]:
    """Extract issue numbers from PR body that are closed/fixed/resolved.
    
    Matches patterns like: closes #123, fixes #456, resolves #789, or just #123
    Returns list of unique issue numbers (integers).
    """
    if not pr_body:
        return []
    # Match: closes/fixes/resolves #123 or just #123
    patterns = [
        r"(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+#(\d+)",
        r"#(\d+)",
    ]
    issue_numbers = set()
    for pattern in patterns:
        matches = re.finditer(pattern, pr_body, re.IGNORECASE)
        for match in matches:
            try:
                issue_numbers.add(int(match.group(1)))
            except (ValueError, IndexError):
                continue
    return sorted(list(issue_numbers))


def _detect_reverted_pr(pr: dict, owner: str, repo: str, client: httpx.Client) -> int | None:
    """Detect if a PR reverts another PR.
    
    Checks PR title/body and commit messages for revert patterns.
    Returns the PR number being reverted, or None if not a revert.
    """
    pr_title = (pr.get("title") or "").lower()
    pr_body = (pr.get("body") or "").lower()
    combined_text = f"{pr_title} {pr_body}"
    
    # Match: revert #123, reverts #123, rollback #123, etc.
    revert_patterns = [
        r"(?:revert|reverts|reverted|rollback|rollbacks|rollbacked)\s+#(\d+)",
    ]
    for pattern in revert_patterns:
        matches = re.finditer(pattern, combined_text, re.IGNORECASE)
        for match in matches:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue
    
    # Also check commit messages (if PR has commits)
    pr_number = pr.get("number")
    if pr_number:
        try:
            # Get commits for this PR
            response = client.get(
                f"/repos/{owner}/{repo}/pulls/{pr_number}/commits",
                headers={"Accept": "application/vnd.github+json"},
            )
            if response.status_code == 200:
                commits = response.json()
                for commit in commits:
                    commit_msg = (commit.get("commit", {}).get("message") or "").lower()
                    for pattern in revert_patterns:
                        matches = re.finditer(pattern, commit_msg, re.IGNORECASE)
                        for match in matches:
                            try:
                                return int(match.group(1))
                            except (ValueError, IndexError):
                                continue
        except Exception:  # noqa: BLE001
            # Network errors, etc. - skip commit check
            pass
    return None


def _check_pr_ci_status(pr: dict, owner: str, repo: str, client: httpx.Client) -> bool:
    """Check if PR was merged with failing CI status.
    
    Returns True if merged_at exists and CI checks failed at merge time.
    Uses GitHub Checks API to check status.
    """
    merged_at = pr.get("merged_at")
    merge_sha = pr.get("merge_commit_sha")
    if not merged_at or not merge_sha:
        return False
    
    try:
        # Check check runs for the merge commit
        response = client.get(
            f"/repos/{owner}/{repo}/commits/{merge_sha}/check-runs",
            headers={"Accept": "application/vnd.github+json"},
        )
        if response.status_code == 200:
            data = response.json()
            check_runs = data.get("check_runs", [])
            # If any check run failed, CI failed
            for run in check_runs:
                conclusion = run.get("conclusion", "").lower()
                if conclusion == "failure":
                    return True
        # Also check status API (legacy status checks)
        status_response = client.get(
            f"/repos/{owner}/{repo}/commits/{merge_sha}/status",
            headers={"Accept": "application/vnd.github+json"},
        )
        if status_response.status_code == 200:
            status_data = status_response.json()
            state = status_data.get("state", "").lower()
            if state == "failure":
                return True
    except Exception:  # noqa: BLE001
        # Network errors, etc. - assume CI passed (fail closed)
        pass
    return False


def _issue_payload(issue: dict) -> dict:
    return {
        "issue_number": issue.get("number"),
        "title": issue.get("title"),
        "state": issue.get("state"),
        "labels": [label.get("name") for label in issue.get("labels") or []],
    }


def _load_repo_filter() -> RepoFilterConfig | None:
    config = get_active_config()
    if not config:
        return None
    return config.github.repos


def _load_user_fallback() -> bool:
    config = get_active_config()
    if not config:
        return False
    return config.github.user_fallback


def _apply_repo_filter(
    repos: Sequence[dict],
    repo_filter: RepoFilterConfig | None,
    logger: logging.Logger,
) -> list[dict]:
    """Filter repos according to config. Returns a new list, never mutates input."""
    if repo_filter is None:
        logger.info(
            "Repo filter disabled; ingesting all repositories",
            extra={"mode": "all", "before": len(repos), "after": len(repos)},
        )
        return list(repos)

    if not repos:
        logger.info("No repositories available to apply repo filter", extra={"mode": repo_filter.mode})
        return []

    names = {name.strip() for name in repo_filter.names}
    repo_names = {repo["name"] for repo in repos}
    if repo_filter.mode == "allow":
        allowed = [repo for repo in repos if repo["name"] in names]
        skipped = sorted(repo_names - names)
    else:
        allowed = [repo for repo in repos if repo["name"] not in names]
        skipped = sorted(repo_names & names)

    logger.info(
        "Applied repo filter",
        extra={
            "mode": repo_filter.mode,
            "before": len(repos),
            "after": len(allowed),
        },
    )
    if not allowed:
        logger.warning(
            "All repositories filtered out",
            extra={"mode": repo_filter.mode, "requested": sorted(names)},
        )
    if skipped:
        logger.debug("Skipped repositories", extra={"repos": skipped})
    return allowed
