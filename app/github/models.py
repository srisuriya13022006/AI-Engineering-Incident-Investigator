"""
Pydantic data models for GitHub Engineering Memory Integration (Milestone 9).

Represents repositories, issues, pull requests, commits, and workflow runs
with stable external identifiers, canonical URLs, and timestamps.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class GitHubRepoNode(BaseModel):
    """Normalized GitHub repository node."""
    id: str = Field(..., description="Stable deterministic ID (e.g. gh_repo_owner_name)")
    owner: str = Field(..., description="Repository owner or organization")
    name: str = Field(..., description="Repository name")
    full_name: str = Field(..., description="Full repository path: owner/name")
    description: Optional[str] = Field(default=None, description="Repository description")
    html_url: str = Field(..., description="Canonical GitHub web URL")
    default_branch: str = Field(default="main", description="Default branch name")
    open_issues_count: int = Field(default=0, description="Open issues count")
    user_id: str = Field(..., description="Tenant / user isolation identifier")
    imported_at: Optional[str] = Field(default=None, description="ISO timestamp of import")


class GitHubIssueNode(BaseModel):
    """Normalized GitHub issue node."""
    id: str = Field(..., description="Stable deterministic ID (e.g. gh_issue_owner_name_123)")
    repo_id: str = Field(..., description="Parent repository ID")
    number: int = Field(..., description="GitHub issue number")
    title: str = Field(..., description="Issue title")
    body: Optional[str] = Field(default=None, description="Issue body markdown / text")
    state: Literal["open", "closed"] = Field(default="open", description="Issue status state")
    labels: List[str] = Field(default_factory=list, description="Labels associated with the issue")
    html_url: str = Field(..., description="Canonical GitHub issue URL")
    author: Optional[str] = Field(default=None, description="GitHub username of author")
    created_at: Optional[str] = Field(default=None, description="Creation ISO timestamp")
    updated_at: Optional[str] = Field(default=None, description="Last update ISO timestamp")
    closed_at: Optional[str] = Field(default=None, description="Closure ISO timestamp")


class PullRequestNode(BaseModel):
    """Normalized GitHub pull request node."""
    id: str = Field(..., description="Stable deterministic ID (e.g. gh_pr_owner_name_45)")
    repo_id: str = Field(..., description="Parent repository ID")
    number: int = Field(..., description="Pull request number")
    title: str = Field(..., description="PR title")
    body: Optional[str] = Field(default=None, description="PR body description")
    state: Literal["open", "closed"] = Field(default="open", description="PR state")
    merged: bool = Field(default=False, description="Whether the PR has been merged")
    html_url: str = Field(..., description="Canonical GitHub PR URL")
    head_sha: Optional[str] = Field(default=None, description="Commit SHA of PR head")
    base_ref: Optional[str] = Field(default=None, description="Target base branch")
    author: Optional[str] = Field(default=None, description="Author username")
    created_at: Optional[str] = Field(default=None, description="Creation ISO timestamp")
    updated_at: Optional[str] = Field(default=None, description="Update ISO timestamp")
    closed_at: Optional[str] = Field(default=None, description="Closure ISO timestamp")
    merged_at: Optional[str] = Field(default=None, description="Merge ISO timestamp")
    referenced_issue_numbers: List[int] = Field(
        default_factory=list,
        description="Explicit issue numbers parsed from PR body (e.g., 'fixes #12')"
    )
    commit_shas: List[str] = Field(
        default_factory=list,
        description="Commit SHAs belonging to this pull request branch"
    )


class CommitNode(BaseModel):
    """Normalized GitHub commit node."""
    id: str = Field(..., description="Stable deterministic ID (e.g. gh_commit_owner_name_sha10)")
    repo_id: str = Field(..., description="Parent repository ID")
    sha: str = Field(..., description="Full or short git commit SHA")
    message: str = Field(..., description="Commit message")
    author: Optional[str] = Field(default=None, description="Commit author name or handle")
    html_url: str = Field(..., description="Canonical commit URL on GitHub")
    timestamp: Optional[str] = Field(default=None, description="Commit ISO timestamp")


class WorkflowRunNode(BaseModel):
    """Normalized GitHub Actions workflow run summary."""
    id: str = Field(..., description="Stable deterministic ID (e.g. gh_wf_owner_name_runid)")
    repo_id: str = Field(..., description="Parent repository ID")
    run_id: int = Field(..., description="GitHub Actions run ID")
    name: str = Field(..., description="Workflow name (e.g., 'CI', 'Integration Tests')")
    event: str = Field(default="push", description="Trigger event")
    status: str = Field(default="completed", description="Execution status (e.g. completed, in_progress)")
    conclusion: Optional[str] = Field(
        default=None,
        description="Conclusion result: success, failure, cancelled, timed_out"
    )
    html_url: str = Field(..., description="Canonical GitHub workflow run URL")
    head_sha: Optional[str] = Field(default=None, description="Commit SHA associated with workflow")
    pull_request_numbers: List[int] = Field(
        default_factory=list,
        description="Associated PR numbers from GitHub metadata"
    )
    created_at: Optional[str] = Field(default=None, description="Run creation ISO timestamp")
    updated_at: Optional[str] = Field(default=None, description="Run finish / update ISO timestamp")


class GitHubImportRequest(BaseModel):
    """Request payload to initiate a repository import."""
    user_id: str = Field(default="user_default", description="Tenant / user isolation ID")
    repo: str = Field(..., description="Repository in 'owner/repo' format (e.g. 'falkordb/falkordb')")
    token: Optional[str] = Field(
        default=None,
        description="Optional personal access token for this request only. Never stored in graph."
    )
    issue_limit: int = Field(default=20, ge=0, le=50, description="Max issues to import")
    pr_limit: int = Field(default=20, ge=0, le=50, description="Max pull requests to import")
    commit_limit: int = Field(default=20, ge=0, le=50, description="Max commits to import")
    workflow_limit: int = Field(default=10, ge=0, le=20, description="Max workflow runs to import")


class GitHubImportSummary(BaseModel):
    """Detailed record counts from a repository import."""
    repo_id: str
    full_name: str
    imported_issues: int = 0
    imported_prs: int = 0
    imported_commits: int = 0
    imported_workflow_runs: int = 0
    explicit_references_count: int = 0
    pr_commits_count: int = 0
    duration_ms: float = 0.0


class GitHubImportResponse(BaseModel):
    """Response returned upon completing a repository import."""
    success: bool
    user_id: str
    repo: str
    summary: Optional[GitHubImportSummary] = None
    error_message: Optional[str] = None


class GitHubRecordEvidence(BaseModel):
    """A single retrieved GitHub record linked to an incident investigation."""
    record_type: Literal["issue", "pull_request", "commit", "workflow_run"]
    id: str
    title_or_summary: str
    state_or_status: str
    html_url: str
    timestamp: Optional[str] = None
    relevance_reason: str
    is_confirmed_fix: bool = False
    notes: Optional[str] = None
    repository: Optional[str] = None
    evidence_category: Literal["direct", "related", "weak_candidate"] = "direct"
    relationship_path: Optional[str] = None
    connected_edges: List[Dict[str, str]] = Field(default_factory=list)


class GitHubRelationshipTrace(BaseModel):
    """An explicit verified relationship edge between two engineering records."""
    source_type: Literal["pull_request", "workflow_run", "issue", "commit", "repository"]
    source_id: str
    source_identifier: str
    relationship: str
    target_type: Literal["pull_request", "workflow_run", "issue", "commit", "repository"]
    target_id: str
    target_identifier: str
    path_string: str


class GitHubMissingRelationship(BaseModel):
    """An explicit record of an unlinked or missing relationship for epistemic transparency."""
    record_type: Literal["issue", "pull_request", "commit", "workflow_run"]
    identifier: str
    missing_relationship: str
    description: str


class GitHubRepositoryDetails(BaseModel):
    """Complete in-app view of imported repository records and relationships."""
    repo_id: str
    owner: str
    name: str
    full_name: str
    description: Optional[str] = None
    html_url: str
    default_branch: str = "main"
    open_issues_count: int = 0
    imported_at: Optional[str] = None
    counts: Dict[str, int]
    issues: List[Dict[str, Any]] = Field(default_factory=list)
    pull_requests: List[Dict[str, Any]] = Field(default_factory=list)
    commits: List[Dict[str, Any]] = Field(default_factory=list)
    workflow_runs: List[Dict[str, Any]] = Field(default_factory=list)
    verified_relationships: List[GitHubRelationshipTrace] = Field(default_factory=list)
    multi_hop_paths: List[str] = Field(default_factory=list)
    missing_relationships: List[GitHubMissingRelationship] = Field(default_factory=list)


class GitHubRepositoryDetailsResponse(BaseModel):
    """API response for repository details endpoint."""
    success: bool
    user_id: str
    repo: str
    repository: Optional[GitHubRepositoryDetails] = None
    error_message: Optional[str] = None

