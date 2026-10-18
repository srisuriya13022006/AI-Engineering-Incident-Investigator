"""
Service orchestrator for GitHub repository data ingestion and normalization.

Transforms raw GitHub API payloads into normalized Pydantic nodes, detects
explicit issue references, and coordinates idempotent storage in FalkorDB.
"""

from datetime import datetime, timezone
import logging
import re
import time
from typing import Any, Dict, List, Optional, Set

from app.github.client import (
    GitHubClient,
    GitHubClientError,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
)
from app.github.models import (
    GitHubRepoNode,
    GitHubIssueNode,
    PullRequestNode,
    CommitNode,
    WorkflowRunNode,
    GitHubImportRequest,
    GitHubImportResponse,
    GitHubImportSummary,
)
from app.github.operations import ingest_github_repository_data

logger = logging.getLogger("incident.github.service")

# Regex to detect issue references like 'fixes #12', 'closes #45', '#123'
ISSUE_REF_REGEX = re.compile(r"(?:(?:fixes|closed|closes|resolved|resolves|issue|issues)\s*#?|#)(\d+)", re.IGNORECASE)


def parse_repo_identifier(raw: str) -> tuple[str, str]:
    """Parse 'owner/repo' or GitHub URL into (owner, repo)."""
    cleaned = raw.strip()
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        parts = cleaned.rstrip("/").split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
    if "/" in cleaned:
        parts = cleaned.split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            return parts[0].strip(), parts[1].strip()
    raise ValueError(f"Invalid repository identifier '{raw}'. Expected 'owner/repo' format.")


def extract_referenced_issues(text: Optional[str]) -> List[int]:
    """Extract integer issue numbers referenced in markdown text."""
    if not text:
        return []
    matches = ISSUE_REF_REGEX.findall(text)
    refs: Set[int] = set()
    for m in matches:
        try:
            refs.add(int(m))
        except ValueError:
            pass
    return sorted(list(refs))


def import_repository(
    req: GitHubImportRequest,
    client: Optional[GitHubClient] = None,
    graph_name: Optional[str] = None,
) -> GitHubImportResponse:
    """
    Fetches engineering records from GitHub and stores them in FalkorDB
    under user_id. Idempotent and tenant-isolated.
    """
    start_time = time.perf_counter()
    user_id = req.user_id.strip()

    try:
        owner, repo_name = parse_repo_identifier(req.repo)
    except ValueError as val_err:
        return GitHubImportResponse(
            success=False,
            user_id=user_id,
            repo=req.repo,
            error_message=str(val_err),
        )

    # Initialize client if not provided
    gh = client or GitHubClient(token=req.token)

    try:
        raw_repo = gh.get_repo(owner, repo_name)
    except GitHubClientError as exc:
        logger.error("GitHub repository fetch failed: %s", exc)
        return GitHubImportResponse(
            success=False,
            user_id=user_id,
            repo=req.repo,
            error_message=str(exc),
        )

    # 1. Normalize Repository
    repo_id = f"gh_repo_{owner}_{repo_name}".lower()
    now_iso = datetime.now(timezone.utc).isoformat()
    repo_node = GitHubRepoNode(
        id=repo_id,
        owner=owner,
        name=repo_name,
        full_name=raw_repo.get("full_name", f"{owner}/{repo_name}"),
        description=raw_repo.get("description"),
        html_url=raw_repo.get("html_url", f"https://github.com/{owner}/{repo_name}"),
        default_branch=raw_repo.get("default_branch", "main"),
        open_issues_count=raw_repo.get("open_issues_count", 0),
        user_id=user_id,
        imported_at=now_iso,
    )

    # 2. Fetch and Normalize Issues
    raw_issues = []
    if req.issue_limit > 0:
        try:
            raw_issues = gh.get_issues(owner, repo_name, limit=req.issue_limit)
        except GitHubClientError as e:
            logger.warning("Error fetching issues for %s/%s: %s", owner, repo_name, e)

    issue_nodes: List[GitHubIssueNode] = []
    for item in raw_issues:
        num = item.get("number")
        if not num:
            continue
        labels = [l["name"] for l in item.get("labels", []) if isinstance(l, dict) and "name" in l]
        author = item.get("user", {}).get("login") if isinstance(item.get("user"), dict) else None
        issue_nodes.append(
            GitHubIssueNode(
                id=f"gh_issue_{owner}_{repo_name}_{num}".lower(),
                repo_id=repo_id,
                number=num,
                title=item.get("title", ""),
                body=item.get("body"),
                state="closed" if item.get("state") == "closed" else "open",
                labels=labels,
                html_url=item.get("html_url", f"{repo_node.html_url}/issues/{num}"),
                author=author,
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                closed_at=item.get("closed_at"),
            )
        )

    # 3. Fetch and Normalize Pull Requests and their PR-branch Commits
    raw_prs = []
    if req.pr_limit > 0:
        try:
            raw_prs = gh.get_pull_requests(owner, repo_name, limit=req.pr_limit)
        except GitHubClientError as e:
            logger.warning("Error fetching PRs for %s/%s: %s", owner, repo_name, e)

    commit_map: Dict[str, CommitNode] = {}
    pr_nodes: List[PullRequestNode] = []
    total_pr_commits = 0

    for item in raw_prs:
        num = item.get("number")
        if not num:
            continue
        body = item.get("body") or ""
        ref_issues = extract_referenced_issues(body)
        head_sha = item.get("head", {}).get("sha") if isinstance(item.get("head"), dict) else None
        base_ref = item.get("base", {}).get("ref") if isinstance(item.get("base"), dict) else None
        author = item.get("user", {}).get("login") if isinstance(item.get("user"), dict) else None

        # Fetch commits belonging to this PR branch (including unmerged PR branches)
        pr_commit_shas: List[str] = []
        if req.commit_limit > 0:
            try:
                raw_pr_commits = gh.get_pull_request_commits(owner, repo_name, num, limit=req.commit_limit)
                for c_item in raw_pr_commits:
                    c_sha = c_item.get("sha")
                    if not c_sha:
                        continue
                    pr_commit_shas.append(c_sha)
                    if c_sha not in commit_map:
                        com_obj = c_item.get("commit", {})
                        msg = com_obj.get("message", "")
                        c_author = com_obj.get("author", {}).get("name") if isinstance(com_obj.get("author"), dict) else None
                        c_ts = com_obj.get("author", {}).get("date") if isinstance(com_obj.get("author"), dict) else None
                        commit_map[c_sha] = CommitNode(
                            id=f"gh_commit_{owner}_{repo_name}_{c_sha[:10]}".lower(),
                            repo_id=repo_id,
                            sha=c_sha,
                            message=msg,
                            author=c_author,
                            html_url=c_item.get("html_url", f"{repo_node.html_url}/commit/{c_sha}"),
                            timestamp=c_ts,
                        )
                total_pr_commits += len(pr_commit_shas)
            except GitHubClientError as e:
                logger.warning("Error fetching commits for PR #%s in %s/%s: %s", num, owner, repo_name, e)

        pr_nodes.append(
            PullRequestNode(
                id=f"gh_pr_{owner}_{repo_name}_{num}".lower(),
                repo_id=repo_id,
                number=num,
                title=item.get("title", ""),
                body=body,
                state="closed" if item.get("state") == "closed" else "open",
                merged=bool(item.get("merged_at")),
                html_url=item.get("html_url", f"{repo_node.html_url}/pull/{num}"),
                head_sha=head_sha,
                base_ref=base_ref,
                author=author,
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                closed_at=item.get("closed_at"),
                merged_at=item.get("merged_at"),
                referenced_issue_numbers=ref_issues,
                commit_shas=pr_commit_shas,
            )
        )

    # 4. Fetch and Normalize Default-Branch Commits
    raw_commits = []
    if req.commit_limit > 0:
        try:
            raw_commits = gh.get_commits(owner, repo_name, limit=req.commit_limit)
        except GitHubClientError as e:
            logger.warning("Error fetching commits for %s/%s: %s", owner, repo_name, e)

    for item in raw_commits:
        sha = item.get("sha")
        if not sha or sha in commit_map:
            continue
        com_obj = item.get("commit", {})
        msg = com_obj.get("message", "")
        author = com_obj.get("author", {}).get("name") if isinstance(com_obj.get("author"), dict) else None
        ts = com_obj.get("author", {}).get("date") if isinstance(com_obj.get("author"), dict) else None

        commit_map[sha] = CommitNode(
            id=f"gh_commit_{owner}_{repo_name}_{sha[:10]}".lower(),
            repo_id=repo_id,
            sha=sha,
            message=msg,
            author=author,
            html_url=item.get("html_url", f"{repo_node.html_url}/commit/{sha}"),
            timestamp=ts,
        )

    commit_nodes: List[CommitNode] = list(commit_map.values())

    # 5. Fetch and Normalize Workflow Runs
    raw_runs = []
    if req.workflow_limit > 0:
        try:
            raw_runs = gh.get_workflow_runs(owner, repo_name, limit=req.workflow_limit)
        except GitHubClientError as e:
            logger.warning("Error fetching workflow runs for %s/%s: %s", owner, repo_name, e)

    wf_nodes: List[WorkflowRunNode] = []
    for item in raw_runs:
        run_id = item.get("id")
        if not run_id:
            continue

        # Extract explicit pull request metadata from GitHub Actions run
        raw_pull_requests = item.get("pull_requests", [])
        pr_numbers: List[int] = []
        if isinstance(raw_pull_requests, list):
            for pr_entry in raw_pull_requests:
                if isinstance(pr_entry, dict) and pr_entry.get("number"):
                    pr_numbers.append(int(pr_entry["number"]))

        wf_head_sha = item.get("head_sha")
        if wf_head_sha and not any(c.sha == wf_head_sha or c.sha.startswith(wf_head_sha) or wf_head_sha.startswith(c.sha) for c in commit_nodes):
            logger.info(
                "Workflow run #%s references head commit %s not present in imported commits; preserving run without phantom commit",
                run_id,
                wf_head_sha,
            )

        wf_nodes.append(
            WorkflowRunNode(
                id=f"gh_wf_{owner}_{repo_name}_{run_id}".lower(),
                repo_id=repo_id,
                run_id=run_id,
                name=item.get("name", "Workflow"),
                event=item.get("event", "push"),
                status=item.get("status", "completed"),
                conclusion=item.get("conclusion"),
                html_url=item.get("html_url", f"{repo_node.html_url}/actions/runs/{run_id}"),
                head_sha=wf_head_sha,
                pull_request_numbers=pr_numbers,
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
            )
        )

    # 6. Ingest into FalkorDB
    counts = ingest_github_repository_data(
        user_id=user_id,
        repo_node=repo_node,
        issues=issue_nodes,
        prs=pr_nodes,
        commits=commit_nodes,
        workflow_runs=wf_nodes,
        graph_name=graph_name,
    )

    elapsed_ms = round((time.perf_counter() - start_time) * 1000, 2)
    summary = GitHubImportSummary(
        repo_id=repo_id,
        full_name=repo_node.full_name,
        imported_issues=counts["issues"],
        imported_prs=counts["prs"],
        imported_commits=counts["commits"],
        imported_workflow_runs=counts["workflow_runs"],
        explicit_references_count=counts["references"],
        pr_commits_count=total_pr_commits,
        duration_ms=elapsed_ms,
    )

    return GitHubImportResponse(
        success=True,
        user_id=user_id,
        repo=req.repo,
        summary=summary,
    )


def seed_demo_github_scenario(
    user_id: str = "user_default",
    graph_name: Optional[str] = None,
) -> GitHubImportResponse:
    """
    Seeds a realistic synthetic GitHub engineering repository into FalkorDB
    for judge demonstration.
    
    Contains:
    - Repository 'gateway-corp/gateway-api'
    - Issues regarding tail timeout and connection pool sizing
    - Pull Request #42 bumping connection pool size from 10 to 50
    - Linked Commit 9f4b12c8a1
    - Failed Workflow Run #1092 demonstrating CI load test failure
    """
    owner = "gateway-corp"
    repo_name = "gateway-api"
    repo_id = f"gh_repo_{owner}_{repo_name}"

    repo_node = GitHubRepoNode(
        id=repo_id,
        owner=owner,
        name=repo_name,
        full_name=f"{owner}/{repo_name}",
        description="High-throughput API Gateway routing microservices with connection pooling.",
        html_url=f"https://github.com/{owner}/{repo_name}",
        default_branch="main",
        open_issues_count=2,
        user_id=user_id,
        imported_at="2026-10-15T12:00:00Z",
    )

    issues = [
        GitHubIssueNode(
            id=f"gh_issue_{owner}_{repo_name}_38",
            repo_id=repo_id,
            number=38,
            title="Intermittent tail timeouts and connection pool starvation during peak traffic",
            body="Gateway latency spikes to >100ms when database connection pool reaches limit of 10.",
            state="closed",
            labels=["bug", "performance", "gateway"],
            html_url=f"https://github.com/{owner}/{repo_name}/issues/38",
            author="alex-engineer",
            created_at="2026-10-10T08:30:00Z",
            closed_at="2026-10-12T16:00:00Z",
        ),
        GitHubIssueNode(
            id=f"gh_issue_{owner}_{repo_name}_41",
            repo_id=repo_id,
            number=41,
            title="Downstream auth proxy connection pool sizing and token cache saturation",
            body="Investigation into whether downstream auth service latency is propagating into gateway timeouts.",
            state="open",
            labels=["investigation", "auth", "infra"],
            html_url=f"https://github.com/{owner}/{repo_name}/issues/41",
            author="sre-lead",
            created_at="2026-10-13T10:00:00Z",
        ),
    ]

    prs = [
        PullRequestNode(
            id=f"gh_pr_{owner}_{repo_name}_42",
            repo_id=repo_id,
            number=42,
            title="feat(gateway): Increase client connection pool from 10 to 50",
            body="Increase connection pool size to mitigate pool starvation. Closes #38.",
            state="closed",
            merged=True,
            html_url=f"https://github.com/{owner}/{repo_name}/pull/42",
            head_sha="9f4b12c8a1",
            base_ref="main",
            author="alex-engineer",
            created_at="2026-10-11T14:00:00Z",
            merged_at="2026-10-12T15:30:00Z",
            referenced_issue_numbers=[38],
            commit_shas=["9f4b12c8a1e8d7c6b5a4"],
        )
    ]

    commits = [
        CommitNode(
            id=f"gh_commit_{owner}_{repo_name}_9f4b12c8a1",
            repo_id=repo_id,
            sha="9f4b12c8a1e8d7c6b5a4",
            message="Increase connection pool default to 50 connections",
            author="alex-engineer",
            html_url=f"https://github.com/{owner}/{repo_name}/commit/9f4b12c8a1",
            timestamp="2026-10-11T13:45:00Z",
        ),
        CommitNode(
            id=f"gh_commit_{owner}_{repo_name}_c3d2e1f0a5",
            repo_id=repo_id,
            sha="c3d2e1f0a5b4c3d2e1f0",
            message="Add distributed tracing tags for downstream auth token verification",
            author="sre-lead",
            html_url=f"https://github.com/{owner}/{repo_name}/commit/c3d2e1f0a5",
            timestamp="2026-10-13T11:20:00Z",
        ),
    ]

    workflow_runs = [
        WorkflowRunNode(
            id=f"gh_wf_{owner}_{repo_name}_1092",
            repo_id=repo_id,
            run_id=1092,
            name="Gateway Load & Stress Benchmark",
            event="pull_request",
            status="completed",
            conclusion="failure",
            html_url=f"https://github.com/{owner}/{repo_name}/actions/runs/1092",
            head_sha="9f4b12c8a1",
            pull_request_numbers=[42],
            created_at="2026-10-12T16:15:00Z",
            updated_at="2026-10-12T16:25:00Z",
        )
    ]

    counts = ingest_github_repository_data(
        user_id=user_id,
        repo_node=repo_node,
        issues=issues,
        prs=prs,
        commits=commits,
        workflow_runs=workflow_runs,
        graph_name=graph_name,
    )

    summary = GitHubImportSummary(
        repo_id=repo_id,
        full_name=repo_node.full_name,
        imported_issues=counts["issues"],
        imported_prs=counts["prs"],
        imported_commits=counts["commits"],
        imported_workflow_runs=counts["workflow_runs"],
        explicit_references_count=counts["references"],
        duration_ms=12.5,
    )

    return GitHubImportResponse(
        success=True,
        user_id=user_id,
        repo=f"{owner}/{repo_name}",
        summary=summary,
    )
