"""
GitHub REST API client for secure repository record fetching.

Uses httpx with strict timeouts, proper User-Agent and Accept headers,
and comprehensive error handling for rate limits, auth errors, and missing repos.
Tokens are read from settings or per-request parameters and are NEVER logged or stored.
"""

import logging
from typing import Any, Dict, List, Optional
import httpx

from app.config import settings

logger = logging.getLogger("incident.github.client")


class GitHubClientError(Exception):
    """Base exception for GitHub client failures."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class GitHubAuthError(GitHubClientError):
    """Raised when GitHub token is invalid or lacks required permissions."""
    pass


class GitHubNotFoundError(GitHubClientError):
    """Raised when repository or resource is not found (404)."""
    pass


class GitHubRateLimitError(GitHubClientError):
    """Raised when GitHub API rate limit is exceeded (403)."""
    pass


class GitHubClient:
    """Client for interacting with the GitHub REST API v3."""

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        # Resolve token: explicit param -> config settings -> None
        resolved_token = (token or settings.github_token or "").strip()
        self._token: Optional[str] = resolved_token if resolved_token else None
        self.base_url = (base_url or settings.github_api_base_url or "https://api.github.com").rstrip("/")
        self.timeout = timeout or settings.github_timeout_sec or 15.0

    @property
    def has_token(self) -> bool:
        """Indicate whether an authentication token is configured."""
        return bool(self._token)

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Engineering-Incident-Investigator/1.0",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        headers = self._get_headers()

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.request(method, url, headers=headers, params=params)
        except httpx.TimeoutException as exc:
            logger.error("Timeout connecting to GitHub API at %s", endpoint)
            raise GitHubClientError(f"GitHub API request timed out: {exc}") from exc
        except httpx.RequestError as exc:
            logger.error("Network error communicating with GitHub API: %s", exc)
            raise GitHubClientError(f"GitHub network connection error: {exc}") from exc

        if resp.status_code == 401:
            raise GitHubAuthError("GitHub authentication failed: invalid or expired token.", status_code=401)
        elif resp.status_code == 403:
            msg = resp.json().get("message", "Forbidden or rate limit exceeded") if resp.headers.get("content-type", "").startswith("application/json") else "Forbidden"
            if "rate limit" in msg.lower():
                raise GitHubRateLimitError(f"GitHub API rate limit exceeded: {msg}", status_code=403)
            raise GitHubClientError(f"GitHub API access forbidden: {msg}", status_code=403)
        elif resp.status_code == 404:
            raise GitHubNotFoundError(f"GitHub repository or resource not found at '{endpoint}'. Check owner/repo name.", status_code=404)
        elif resp.status_code >= 400:
            err_body = resp.text[:200]
            raise GitHubClientError(f"GitHub API returned error {resp.status_code}: {err_body}", status_code=resp.status_code)

        return resp.json()

    def get_repo(self, owner: str, repo: str) -> Dict[str, Any]:
        """Fetch repository metadata."""
        return self._request("GET", f"repos/{owner}/{repo}")

    def get_issues(self, owner: str, repo: str, limit: int = 30) -> List[Dict[str, Any]]:
        """
        Fetch repository issues (state=all).
        Note: GitHub REST API includes pull requests in /issues. We filter out PRs here.
        """
        raw_items = self._request(
            "GET",
            f"repos/{owner}/{repo}/issues",
            params={"state": "all", "per_page": min(limit * 2, 100), "sort": "updated", "direction": "desc"},
        )
        if not isinstance(raw_items, list):
            return []

        # Filter out pull requests which GitHub includes in the issues endpoint
        issues_only = [item for item in raw_items if "pull_request" not in item]
        return issues_only[:limit]

    def get_pull_requests(self, owner: str, repo: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Fetch pull requests (state=all)."""
        raw_items = self._request(
            "GET",
            f"repos/{owner}/{repo}/pulls",
            params={"state": "all", "per_page": min(limit, 100), "sort": "updated", "direction": "desc"},
        )
        if not isinstance(raw_items, list):
            return []
        return raw_items[:limit]

    def get_pull_request_commits(self, owner: str, repo: str, pull_number: int, limit: int = 30) -> List[Dict[str, Any]]:
        """
        Fetch commits belonging to a specific pull request (including unmerged branches).
        Uses GitHub endpoint: GET /repos/{owner}/{repo}/pulls/{pull_number}/commits
        """
        raw_items = self._request(
            "GET",
            f"repos/{owner}/{repo}/pulls/{pull_number}/commits",
            params={"per_page": min(limit, 100)},
        )
        if not isinstance(raw_items, list):
            return []
        return raw_items[:limit]

    def get_commits(self, owner: str, repo: str, limit: int = 30) -> List[Dict[str, Any]]:
        """Fetch recent commit records."""
        raw_items = self._request(
            "GET",
            f"repos/{owner}/{repo}/commits",
            params={"per_page": min(limit, 100)},
        )
        if not isinstance(raw_items, list):
            return []
        return raw_items[:limit]

    def get_workflow_runs(self, owner: str, repo: str, limit: int = 15) -> List[Dict[str, Any]]:
        """Fetch recent GitHub Actions workflow runs."""
        data = self._request(
            "GET",
            f"repos/{owner}/{repo}/actions/runs",
            params={"per_page": min(limit, 50)},
        )
        if isinstance(data, dict) and "workflow_runs" in data:
            return data["workflow_runs"][:limit]
        return []
