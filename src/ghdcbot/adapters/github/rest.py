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

    def assign_issue(self, owner: str, repo: str, issue_number: int, assignee: str) -> bool:
        """Assign a GitHub issue to a user.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue number
            assignee: GitHub username to assign
        
        Returns:
            True if assignment succeeded, False otherwise.
        """
        try:
            response = self._client.post(
                f"/repos/{owner}/{repo}/issues/{issue_number}/assignees",
                json={"assignees": [assignee]},
            )
        except httpx.HTTPError as exc:
            self._logger.warning(
                "GitHub request failed",
                extra={"path": f"/repos/{owner}/{repo}/issues/{issue_number}/assignees", "error": str(exc)},
            )
            return False
        
        rate_limit = _parse_rate_limit(response.headers)
        if rate_limit.remaining is not None and rate_limit.remaining <= 1:
            self._logger.warning(
                "GitHub rate limit nearly exhausted",
                extra={
                    "path": f"/repos/{owner}/{repo}/issues/{issue_number}/assignees",
                    "remaining": rate_limit.remaining,
                    "reset_at": rate_limit.reset_at.isoformat() if rate_limit.reset_at else None,
                },
            )
        
        if response.status_code in {200, 201}:
            self._logger.info(
                "Issue assigned successfully",
                extra={"owner": owner, "repo": repo, "issue_number": issue_number, "assignee": assignee},
            )
            return True
        else:
            error_body = ""
            try:
                error_body = (response.text or "")[:300]
            except Exception:
                pass
            self._logger.warning(
                "Issue assignment failed",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "issue_number": issue_number,
                    "assignee": assignee,
                    "status_code": response.status_code,
                    "error_response": error_body,
                },
            )
            return False

    def unassign_issue(self, owner: str, repo: str, issue_number: int, assignee: str) -> bool:
        """Unassign a GitHub issue from a user.
        
        Args:
            owner: Repository owner
            repo: Repository name
            issue_number: Issue number
            assignee: GitHub username to unassign
        
        Returns:
            True if unassignment succeeded, False otherwise.
        """
        try:
            response = self._client.delete(
                f"/repos/{owner}/{repo}/issues/{issue_number}/assignees",
                json={"assignees": [assignee]},
            )
        except httpx.HTTPError as exc:
            self._logger.warning(
                "GitHub request failed",
                extra={"path": f"/repos/{owner}/{repo}/issues/{issue_number}/assignees", "error": str(exc)},
            )
            return False
        
        rate_limit = _parse_rate_limit(response.headers)
        if rate_limit.remaining is not None and rate_limit.remaining <= 1:
            self._logger.warning(
                "GitHub rate limit nearly exhausted",
                extra={
                    "path": f"/repos/{owner}/{repo}/issues/{issue_number}/assignees",
                    "remaining": rate_limit.remaining,
                    "reset_at": rate_limit.reset_at.isoformat() if rate_limit.reset_at else None,
                },
            )
        
        if response.status_code in {200, 201}:
            self._logger.info(
                "Issue unassigned successfully",
                extra={"owner": owner, "repo": repo, "issue_number": issue_number, "assignee": assignee},
            )
            return True
        else:
            self._logger.warning(
                "Issue unassignment failed",
                extra={
                    "owner": owner,
                    "repo": repo,
                    "issue_number": issue_number,
                    "assignee": assignee,
                    "status_code": response.status_code,
                },
            )
            return False

    def request_review(self, repo: str, pr_number: int, reviewer: str) -> None:
        self._logger.info(
            "GitHub review request stub",
            extra={"repo": repo, "pr_number": pr_number, "reviewer": reviewer},
        )

    def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict | None:
        """Fetch a single pull request by number.
        
        Returns PR dict or None if not found/accessible.
        """
        response = self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}", params={})
        if response and response.status_code == 200:
            return response.json()
        return None

    def get_pull_request_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Fetch reviews for a pull request.
        
        Returns list of review dicts (empty list on error).
        """
        reviews = []
        for page in self._paginate(f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews", params={"per_page": 100}):
            reviews.extend(page)
        return reviews

    def get_pull_request_check_runs(self, owner: str, repo: str, head_sha: str) -> list[dict]:
        """Fetch check runs for a commit (used for CI status).
        
        Returns list of check run dicts (empty list on error).
        Note: Requires 'checks:read' permission for private repos.
        """
        check_runs = []
        # Use check-runs endpoint (requires checks:read scope)
        response = self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
            params={"per_page": 100},
        )
        if response and response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and "check_runs" in data:
                check_runs = data["check_runs"]
        return check_runs

    def get_issue(self, owner: str, repo: str, issue_number: int) -> dict | None:
        """Fetch a single issue by number.
        
        Returns issue dict or None if not found/accessible.
        Note: GitHub API uses /issues/{number} for both issues and PRs.
        """
        response = self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}", params={})
        if response and response.status_code == 200:
            return response.json()
        return None

    def write_file(
        self, owner: str, repo: str, file_path: str, content: str, commit_message: str, branch: str | None = None
    ) -> bool:
        """Write a file to GitHub repo using Contents API.
        
        Creates or updates a file in the repository. Uses the default branch if branch is not specified.
        
        Args:
            owner: Repository owner
            repo: Repository name
            file_path: Path to file within repo (e.g., "snapshots/2024-01-01/meta.json")
            content: File content (will be base64 encoded)
            commit_message: Commit message
            branch: Branch name (default: main or master)
        
        Returns:
            True if successful, False otherwise.
        """
        import base64
        
        try:
            # Get default branch if not specified
            if not branch:
                repo_info = self._request("GET", f"/repos/{owner}/{repo}", params={})
                if repo_info and repo_info.status_code == 200:
                    branch = repo_info.json().get("default_branch", "main")
                else:
                    branch = "main"
            
            # Check if file exists to get SHA for update
            file_sha = None
            try:
                file_response = self._request(
                    "GET",
                    f"/repos/{owner}/{repo}/contents/{file_path}",
                    params={"ref": branch},
                )
                if file_response and file_response.status_code == 200:
                    file_sha = file_response.json().get("sha")
            except Exception:
                # File doesn't exist yet, will create new
                pass
            
            # Prepare content (base64 encode)
            content_bytes = content.encode("utf-8")
            content_b64 = base64.b64encode(content_bytes).decode("ascii")
            
            # Create/update file
            payload = {
                "message": commit_message,
                "content": content_b64,
                "branch": branch,
            }
            if file_sha:
                payload["sha"] = file_sha
            
            # Use _client directly for PUT with JSON body
            try:
                response = self._client.put(
                    f"/repos/{owner}/{repo}/contents/{file_path}",
                    json=payload,
                )
            except httpx.HTTPError as exc:
                self._logger.warning(
                    "GitHub request failed",
                    extra={"path": f"/repos/{owner}/{repo}/contents/{file_path}", "error": str(exc)},
                )
                return False
            
            if response and response.status_code in {200, 201}:
                self._logger.info(
                    "File written to GitHub",
                    extra={"owner": owner, "repo": repo, "file_path": file_path, "branch": branch},
                )
                return True
            else:
                error_body = ""
                try:
                    error_body = (response.text or "")[:300] if response else ""
                except Exception:
                    pass
                self._logger.warning(
                    "Failed to write file to GitHub",
                    extra={
                        "owner": owner,
                        "repo": repo,
                        "file_path": file_path,
                        "status_code": response.status_code if response else None,
                        "error": error_body,
                    },
                )
                return False
        except Exception as exc:
            self._logger.warning(
                "Exception writing file to GitHub",
                exc_info=True,
                extra={"owner": owner, "repo": repo, "file_path": file_path, "error": str(exc)},
            )
            return False

    def _ingest_repo(self, repo: dict, since: datetime) -> Iterable[ContributionEvent]:
        repo_name = repo["name"]
        owner = repo["owner"]["login"]
        full_name = repo["full_name"]
        self._logger.info("Ingesting repository", extra={"repo": full_name})

        issue_events, issue_numbers, issue_authors = self._collect_issue_events(
            owner, repo_name, since
        )
        pr_events, pr_numbers, pr_opened_count, pr_authors = self._collect_pull_request_events(
            owner, repo_name, since
        )
        # Ingest comments (returns both events and raw comments for reuse)
        issue_comment_events, issue_comments_by_number = self._ingest_issue_comments(
            owner, repo_name, issue_numbers, since
        )
        pr_comment_events, pr_comments_by_number = self._ingest_pr_comments(
            owner, repo_name, pr_numbers, since
        )
        # Emit helpful_comment events for non-author comments (authors from collectors, pre-fetched comments)
        helpful_comment_events = list(
            self._ingest_helpful_comments(
                owner, repo_name, issue_comments_by_number, pr_comments_by_number, since,
                issue_authors=issue_authors, pr_authors=pr_authors,
            )
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
    ) -> tuple[list[ContributionEvent], list[int], dict[int, str]]:
        issue_events: list[ContributionEvent] = []
        issue_numbers: list[int] = []
        issue_authors: dict[int, str] = {}
        params = {"state": "all", "since": since.isoformat(), "per_page": 100}
        for page in self._paginate(f"/repos/{owner}/{repo}/issues", params=params):
            for issue in page:
                if "pull_request" in issue:
                    continue
                num = issue["number"]
                issue_numbers.append(num)
                author = (issue.get("user") or {}).get("login")
                if author:
                    issue_authors[num] = author
                issue_events.extend(self._issue_events(owner, repo, issue, since))
        return issue_events, issue_numbers, issue_authors

    def _collect_pull_request_events(
        self, owner: str, repo: str, since: datetime
    ) -> tuple[list[ContributionEvent], list[int], int, dict[int, str]]:
        pr_events: list[ContributionEvent] = []
        pr_numbers: list[int] = []
        pr_authors: dict[int, str] = {}
        pr_opened_count = 0
        params = {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}
        for page in self._paginate(f"/repos/{owner}/{repo}/pulls", params=params):
            for pr in page:
                updated_at = _parse_iso8601(pr.get("updated_at"))
                if updated_at and updated_at < since:
                    return pr_events, pr_numbers, pr_opened_count, pr_authors
                num = pr["number"]
                pr_numbers.append(num)
                author = (pr.get("user") or {}).get("login")
                if author:
                    pr_authors[num] = author
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
                pr_author_for_reviews = pr_authors.get(pr["number"])
                pr_events.extend(
                    self._pull_request_reviews(owner, repo, pr["number"], since, pr_author=pr_author_for_reviews)
                )
        return pr_events, pr_numbers, pr_opened_count, pr_authors

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
        self, owner: str, repo: str, pr_number: int, since: datetime, pr_author: str | None = None
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
                payload = {
                    "pr_number": pr_number,
                    "review_id": review.get("id"),
                    "state": review.get("state"),
                    "submitted_at": review.get("submitted_at"),
                }
                if pr_author:
                    payload["pr_author"] = pr_author
                yield ContributionEvent(
                    github_user=reviewer,
                    event_type="pr_reviewed",
                    repo=repo,
                    created_at=submitted_at,
                    payload=payload,
                )

    def _ingest_issue_comments(
        self, owner: str, repo: str, issue_numbers: Sequence[int], since: datetime
    ) -> tuple[list[ContributionEvent], dict[int, list[dict]]]:
        """Ingest issue comments and return both events and raw comments for reuse.
        
        Returns:
            Tuple of (comment_events, comments_by_number) where comments_by_number
            maps issue_number -> list of comment dicts.
        """
        if not issue_numbers:
            return [], {}
        self._logger.info(
            "Ingesting issue comments",
            extra={"repo": f"{owner}/{repo}", "issues": len(issue_numbers)},
        )
        comment_events: list[ContributionEvent] = []
        comments_by_number: dict[int, list[dict]] = {}
        for issue_number in issue_numbers:
            params = {"per_page": 100}
            comments_list: list[dict] = []
            for page in self._paginate(
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments", params=params
            ):
                comments_list.extend(page)
            comments_by_number[issue_number] = comments_list
            
            for comment in comments_list:
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
        return comment_events, comments_by_number

    def _ingest_pr_comments(
        self, owner: str, repo: str, pr_numbers: Sequence[int], since: datetime
    ) -> tuple[list[ContributionEvent], dict[int, list[dict]]]:
        """Ingest PR comments and return both events and raw comments for reuse.
        
        Returns:
            Tuple of (comment_events, comments_by_number) where comments_by_number
            maps pr_number -> list of comment dicts.
        """
        if not pr_numbers:
            return [], {}
        self._logger.info(
            "Ingesting PR comments",
            extra={"repo": f"{owner}/{repo}", "prs": len(pr_numbers)},
        )
        comment_events: list[ContributionEvent] = []
        comments_by_number: dict[int, list[dict]] = {}
        seen: set[tuple[str, int]] = set()
        for pr_number in pr_numbers:
            params = {"per_page": 100}
            comments_list: list[dict] = []
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
                        if key not in seen:
                            seen.add(key)
                            comments_list.append(comment)
            comments_by_number[pr_number] = comments_list
            
            for comment in comments_list:
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
                            "issue_number": pr_number,
                            "comment_id": comment.get("id"),
                            "url": comment.get("html_url"),
                        },
                    )
                )
        if comment_events:
            self._logger.info(
                "Emitted comment events",
                extra={"repo": f"{owner}/{repo}", "count": len(comment_events), "source": "pr"},
            )
        return comment_events, comments_by_number

    def _ingest_helpful_comments(
        self,
        owner: str,
        repo: str,
        issue_comments_by_number: dict[int, Iterable[dict]],
        pr_comments_by_number: dict[int, Iterable[dict]],
        since: datetime,
        *,
        issue_authors: dict[int, str] | None = None,
        pr_authors: dict[int, str] | None = None,
    ) -> Iterable[ContributionEvent]:
        """Emit helpful_comment events for non-author comments on issues and PRs.
        
        A comment is "helpful" if:
        - It's on an issue/PR
        - The commenter is not the issue/PR author
        - It's not a bot comment
        
        Bonus is capped per PR/issue (max 5 helpful comments count for bonus).
        
        issue_authors and pr_authors should be precomputed by callers (e.g. from
        _collect_issue_events / _collect_pull_request_events) to avoid N+1 API calls.
        
        issue_comments_by_number and pr_comments_by_number should contain pre-fetched
        comment iterables to avoid duplicate API pagination.
        """
        helpful_events: list[ContributionEvent] = []
        issue_authors = issue_authors or {}
        pr_authors = pr_authors or {}

        # Process issue comments
        for issue_number, comments in issue_comments_by_number.items():
            helpful_count = 0
            for comment in comments:
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
        for pr_number, comments in pr_comments_by_number.items():
            helpful_count = 0
            for comment in comments:
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
                    payload = _issue_payload(issue)
                    # Include assigned_by (actor) if available in timeline event
                    actor = event.get("actor")
                    if actor and isinstance(actor, dict):
                        assigned_by = actor.get("login")
                        if assigned_by:
                            payload["assigned_by"] = assigned_by
                    yield ContributionEvent(
                        github_user=assignee_login,
                        event_type="issue_assigned",
                        repo=repo,
                        created_at=created_at,
                        payload=payload,
                    )
        except Exception as e:
            # If timeline call fails, omit assignment events to avoid wrong timestamps
            self._logger.debug(
                "Failed to fetch issue timeline for assignment events",
                exc_info=True,
                extra={
                    "owner": owner,
                    "repo": repo,
                    "issue_number": issue_number,
                    "error": str(e),
                },
            )

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
    """Extract issue numbers from PR body that are explicitly closed/fixed/resolved.
    
    Only matches closing-keyword patterns: closes #123, fixes #456, resolves #789.
    Does not match bare #number references to avoid unrelated issue lookups.
    Returns list of unique issue numbers (integers).
    """
    if not pr_body:
        return []
    # Only match explicit closing keywords + #number (not bare #number)
    pattern = r"(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+#(\d+)"
    issue_numbers = set()
    for match in re.finditer(pattern, pr_body, re.IGNORECASE):
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
        except Exception as e:  # noqa: BLE001
            # Network errors, etc. - skip commit check
            import logging
            logger = logging.getLogger("GitHubRestAdapter")
            logger.debug(
                "Failed to check commits for revert detection",
                exc_info=True,
                extra={
                    "owner": owner,
                    "repo": repo,
                    "pr_number": pr_number,
                    "error": str(e),
                },
            )
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
    except Exception as e:  # noqa: BLE001
        # Network errors, etc. - assume CI passed (fail closed)
        import logging
        logger = logging.getLogger("GitHubRestAdapter")
        logger.debug(
            "Failed to check CI status for PR",
            exc_info=True,
            extra={
                "owner": owner,
                "repo": repo,
                "pr_number": pr.get("number"),
                "merge_sha": merge_sha,
                "error": str(e),
            },
        )
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
