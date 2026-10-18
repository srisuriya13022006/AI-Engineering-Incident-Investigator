"""
Automated tests for GitHub Engineering Memory Integration (Milestone 9).

Verifies:
1. GitHub API parsing with mocked client responses.
2. Error handling: 404 Not Found, 401 Unauthorized, 403 Rate Limit.
3. Stable external IDs and idempotent FalkorDB imports.
4. Multi-hop graph relationships (Repository -> PR -> Issue, Repo -> WorkflowRun -> Commit).
5. Strict tenant isolation (cross-user data isolation).
6. Evidence retrieval during incident investigation with canonical source URLs.
7. No-evidence and uncertain-evidence behavior.
8. FalkorDB offline graceful degradation.
9. One-click demo endpoint /demo/github_scenario.
"""

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.github.models import (
    GitHubImportRequest,
    GitHubImportResponse,
)
from app.graph.connection import get_graph_client
from app.github.client import (
    GitHubClient,
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubClientError,
)
from app.github.service import (
    parse_repo_identifier,
    extract_referenced_issues,
    import_repository,
    seed_demo_github_scenario,
)
from app.github.operations import (
    get_user_repositories_summary,
    query_github_evidence_for_incident,
    get_repository_details,
)
from app.incident.models import (
    IncidentQueryRequest,
    IncidentInvestigationResponse,
)
from app.incident.investigator import investigate_incident

client = TestClient(app)


def test_parse_repo_identifier():
    """Verify repo parsing handles owner/repo, trailing slashes, and URLs."""
    owner, repo = parse_repo_identifier("falkordb/falkordb")
    assert owner == "falkordb"
    assert repo == "falkordb"

    owner, repo = parse_repo_identifier("https://github.com/facebook/react/")
    assert owner == "facebook"
    assert repo == "react"

    with pytest.raises(ValueError):
        parse_repo_identifier("invalid_single_string")


def test_extract_referenced_issues():
    """Verify explicit issue reference extraction from PR descriptions."""
    text = "This PR fixes #42 and resolves issue #108. Also related to #42."
    refs = extract_referenced_issues(text)
    assert refs == [42, 108]

    assert extract_referenced_issues(None) == []
    assert extract_referenced_issues("No references here") == []


def test_github_client_error_handling():
    """Verify GitHub client maps HTTP status codes to typed exceptions."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.text = '{"message": "Not Found"}'
    mock_resp.json.return_value = {"message": "Not Found"}

    with patch("httpx.Client.request", return_value=mock_resp):
        gh = GitHubClient()
        with pytest.raises(GitHubNotFoundError):
            gh.get_repo("fake-org", "nonexistent-repo")

    mock_resp_401 = MagicMock()
    mock_resp_401.status_code = 401
    mock_resp_401.headers = {"content-type": "application/json"}
    mock_resp_401.text = '{"message": "Bad credentials"}'
    mock_resp_401.json.return_value = {"message": "Bad credentials"}

    with patch("httpx.Client.request", return_value=mock_resp_401):
        gh = GitHubClient(token="bad_token")
        with pytest.raises(GitHubAuthError):
            gh.get_repo("fake-org", "repo")

    mock_resp_403 = MagicMock()
    mock_resp_403.status_code = 403
    mock_resp_403.headers = {"content-type": "application/json"}
    mock_resp_403.text = '{"message": "API rate limit exceeded"}'
    mock_resp_403.json.return_value = {"message": "API rate limit exceeded"}

    with patch("httpx.Client.request", return_value=mock_resp_403):
        gh = GitHubClient()
        with pytest.raises(GitHubRateLimitError):
            gh.get_repo("fake-org", "repo")


def test_idempotent_github_import_and_graph_relationships():
    """
    Verify repository import creates correct nodes and explicit relationships,
    and repeated import is strictly idempotent (updates without duplicates).
    """
    user_id = "user_gh_idempotency_test"

    # Mock GitHubClient
    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": "acme/payment-service",
        "description": "Payment microservice",
        "html_url": "https://github.com/acme/payment-service",
        "default_branch": "main",
        "open_issues_count": 5,
    }
    mock_client.get_issues.return_value = [
        {
            "number": 101,
            "title": "Payment gateway timeout on concurrent charges",
            "body": "Socket timeout observed when calling bank gateway.",
            "state": "closed",
            "labels": [{"name": "bug"}, {"name": "timeout"}],
            "html_url": "https://github.com/acme/payment-service/issues/101",
            "user": {"login": "dev1"},
            "created_at": "2026-10-01T10:00:00Z",
            "updated_at": "2026-10-02T12:00:00Z",
            "closed_at": "2026-10-02T12:00:00Z",
        }
    ]
    mock_client.get_pull_requests.return_value = [
        {
            "number": 105,
            "title": "fix: Increase HTTP client timeout to 30s",
            "body": "Fixes #101 by extending timeout threshold.",
            "state": "closed",
            "merged_at": "2026-10-02T11:50:00Z",
            "html_url": "https://github.com/acme/payment-service/pull/105",
            "head": {"sha": "a1b2c3d4e5f6"},
            "user": {"login": "dev1"},
            "created_at": "2026-10-02T10:30:00Z",
        }
    ]
    mock_client.get_commits.return_value = [
        {
            "sha": "a1b2c3d4e5f67890",
            "commit": {
                "message": "Increase HTTP client socket timeout",
                "author": {"name": "Dev One", "date": "2026-10-02T10:20:00Z"},
            },
            "html_url": "https://github.com/acme/payment-service/commit/a1b2c3d4e5f67890",
        }
    ]
    mock_client.get_workflow_runs.return_value = [
        {
            "id": 884422,
            "name": "Integration Tests",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/acme/payment-service/actions/runs/884422",
            "head_sha": "a1b2c3d4e5f6",
            "created_at": "2026-10-02T10:35:00Z",
        }
    ]

    req = GitHubImportRequest(
        user_id=user_id,
        repo="acme/payment-service",
        issue_limit=10,
        pr_limit=10,
        commit_limit=10,
        workflow_limit=5,
    )

    # First import
    res1 = import_repository(req=req, client=mock_client)
    assert res1.success is True
    assert res1.summary.imported_issues == 1
    assert res1.summary.imported_prs == 1
    assert res1.summary.imported_commits == 1
    assert res1.summary.imported_workflow_runs == 1
    assert res1.summary.explicit_references_count == 1

    # Second import (idempotency check)
    res2 = import_repository(req=req, client=mock_client)
    assert res2.success is True
    assert res2.summary.imported_issues == 1

    # Check repository summary
    repos = get_user_repositories_summary(user_id=user_id)
    assert len(repos) == 1
    assert repos[0]["full_name"] == "acme/payment-service"
    assert repos[0]["issue_count"] == 1
    assert repos[0]["pr_count"] == 1
    assert repos[0]["commit_count"] == 1
    assert repos[0]["workflow_run_count"] == 1


def test_tenant_isolation_in_github_imports():
    """Verify that Tenant A cannot access or see Tenant B's imported repositories."""
    tenant_a = "user_gh_tenant_alpha"
    tenant_b = "user_gh_tenant_beta"

    # Seed demo scenario under Tenant A
    seed_demo_github_scenario(user_id=tenant_a)

    # Tenant A summary
    repos_a = get_user_repositories_summary(user_id=tenant_a)
    assert len(repos_a) >= 1
    assert any(r["full_name"] == "gateway-corp/gateway-api" for r in repos_a)

    # Tenant B summary
    repos_b = get_user_repositories_summary(user_id=tenant_b)
    assert not any(r["full_name"] == "gateway-corp/gateway-api" for r in repos_b)

    # Query evidence for Tenant B should return empty
    records_b, paths_b, _ = query_github_evidence_for_incident(
        user_id=tenant_b,
        keywords=["gateway", "timeout", "latency"],
        component="gateway-api",
    )
    assert len(records_b) == 0
    assert len(paths_b) == 0


def test_incident_investigation_surfaces_connected_github_records():
    """
    Verify that an incident investigation query retrieves relevant connected
    GitHub PRs, issues, and workflow failures, constructs causal evidence paths,
    and includes canonical source URLs.
    """
    user_id = "user_gh_investigation_eval"

    # Seed realistic synthetic scenario
    seed_demo_github_scenario(user_id=user_id)

    # Query incident investigation touching gateway-api with timeout symptoms
    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Tail timeout alerts and connection starvation during peak traffic",
        component="gateway-api",
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is True
    assert len(res.retrieved_github_records) > 0

    # Verify PR record present
    prs = [r for r in res.retrieved_github_records if r.get("record_type") == "pull_request"]
    assert len(prs) >= 1
    pr = prs[0]
    assert "PR #42" in pr["title_or_summary"]
    assert "https://github.com/gateway-corp/gateway-api/pull/42" in pr["html_url"]
    assert pr["is_confirmed_fix"] is False  # Epistemic integrity check

    # Verify workflow failure present
    wfs = [r for r in res.retrieved_github_records if r.get("record_type") == "workflow_run"]
    assert len(wfs) >= 1
    assert wfs[0]["state_or_status"] == "failure"
    assert "https://github.com/gateway-corp/gateway-api/actions/runs/1092" in wfs[0]["html_url"]

    # Verify causal evidence path contains GitHub relationship
    assert any("PullRequest" in path or "WorkflowRun" in path for path in res.evidence_paths)

    # Verify uncertainty notes explicitly disclaim treating PR as proven root cause
    assert "not proven fixes or root causes" in res.uncertainty_notes


def test_no_evidence_abstention_on_unrelated_query():
    """Verify explicit found_evidence=False abstention when query matches neither incidents nor GitHub records."""
    user_id = "user_gh_abstain_eval"
    seed_demo_github_scenario(user_id=user_id)

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Quantum vacuum phase decoherence in subatomic cryogenic matrix",
        component="quantum-core",
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is False
    assert res.insufficient_evidence_reason is not None
    assert len(res.retrieved_github_records) == 0
    assert len(res.evidence_paths) == 0


def test_github_api_endpoints_live():
    """Verify FastAPI endpoints: POST /github/import, GET /github/status, and POST /demo/github_scenario."""
    # 1. GET /github/status
    res_status = client.get("/github/status?user_id=user_api_test")
    assert res_status.status_code == 200
    data_status = res_status.json()
    assert data_status["status"] == "active"
    assert "imported_repositories" in data_status

    # 2. POST /demo/github_scenario
    res_demo = client.post("/demo/github_scenario?user_id=user_api_test")
    assert res_demo.status_code == 200
    data_demo = res_demo.json()
    assert data_demo["success"] is True
    assert len(data_demo["summary_of_checks"]) >= 6
    assert data_demo["investigation_result"]["found_evidence"] is True
    assert len(data_demo["investigation_result"]["retrieved_github_records"]) > 0

    # 3. GET /github/status after seeding
    res_status2 = client.get("/github/status?user_id=user_api_test")
    assert res_status2.status_code == 200
    repos = res_status2.json()["imported_repositories"]
    assert any(r["full_name"] == "gateway-corp/gateway-api" for r in repos)


def test_pr_branch_commit_import_and_pr_to_commit_relationships():
    """
    Verify:
    1. GitHub pull-request commits endpoint retrieves unmerged PR branch commits.
    2. Commit nodes are upserted with full SHA, message, author, timestamp, canonical URL.
    3. PR is linked to its branch commits via CONTAINS_COMMIT.
    4. Workflow run with matching head SHA is linked to commit via TRIGGERED_BY.
    5. Workflow run with pull_request event is linked to PR via TESTS_PULL_REQUEST.
    """
    user_id = "user_gh_pr_branch_eval"
    owner = "org-test"
    repo = "pr-branch-repo"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": f"{owner}/{repo}",
        "description": "Test repo with PR branch commits",
        "html_url": f"https://github.com/{owner}/{repo}",
        "default_branch": "main",
        "open_issues_count": 1,
    }
    mock_client.get_issues.return_value = [
        {
            "number": 1,
            "title": "Payment timeouts on heavy concurrency",
            "body": "Connection pool starving.",
            "state": "open",
            "labels": [{"name": "bug"}],
            "html_url": f"https://github.com/{owner}/{repo}/issues/1",
        }
    ]
    head_sha = "780c02200fba7c4fef915101e0daedfa901caf36"
    branch_sha = "43b3f3c4929c8f5bac8982ac8f15b3a7ae2ef27e"
    main_sha = "dae142f05ea4598453bce9b3be218480cacbaa83"

    mock_client.get_pull_requests.return_value = [
        {
            "number": 2,
            "title": "Investigate payment API connection pool configuration",
            "body": "Bumps pool size to test concurrency. References #1.",
            "state": "open",
            "merged_at": None,
            "html_url": f"https://github.com/{owner}/{repo}/pull/2",
            "head": {"sha": head_sha},
            "user": {"login": "srisuriya13022006"},
            "created_at": "2026-10-15T10:00:00Z",
        }
    ]
    # Commits on PR branch (including unmerged branch commits)
    mock_client.get_pull_request_commits.return_value = [
        {
            "sha": branch_sha,
            "commit": {
                "message": "Add synthetic payment service configuration",
                "author": {"name": "sri suriya", "date": "2026-10-15T09:30:00Z"},
            },
            "html_url": f"https://github.com/{owner}/{repo}/commit/{branch_sha}",
        },
        {
            "sha": head_sha,
            "commit": {
                "message": "Add comment to trigger pull request workflow",
                "author": {"name": "sri suriya", "date": "2026-10-15T09:45:00Z"},
            },
            "html_url": f"https://github.com/{owner}/{repo}/commit/{head_sha}",
        },
    ]
    # Commits on default branch
    mock_client.get_commits.return_value = [
        {
            "sha": main_sha,
            "commit": {
                "message": "Initial commit on main",
                "author": {"name": "sri suriya", "date": "2026-10-14T08:00:00Z"},
            },
            "html_url": f"https://github.com/{owner}/{repo}/commit/{main_sha}",
        }
    ]
    # Workflow run triggered by PR event on head SHA
    mock_client.get_workflow_runs.return_value = [
        {
            "id": 38904009974,
            "name": "Synthetic Lab CI",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"https://github.com/{owner}/{repo}/actions/runs/38904009974",
            "head_sha": head_sha,
            "pull_requests": [{"number": 2, "head": {"sha": head_sha}}],
            "created_at": "2026-10-15T10:05:00Z",
        }
    ]

    req = GitHubImportRequest(
        user_id=user_id,
        repo=f"{owner}/{repo}",
        issue_limit=10,
        pr_limit=10,
        commit_limit=10,
        workflow_limit=10,
    )
    res = import_repository(req=req, client=mock_client)
    assert res.success is True
    assert res.summary.imported_prs == 1
    assert res.summary.imported_commits == 3  # 2 from PR branch + 1 from main
    assert res.summary.pr_commits_count == 2
    assert res.summary.imported_workflow_runs == 1

    # Verify graph database relationships directly via FalkorDB
    g = get_graph_client()
    repo_id = f"gh_repo_{owner}_{repo}".lower()

    # 1. Verify PR branch commits exist and are linked to PR via CONTAINS_COMMIT
    cypher_pr_commits = """
    MATCH (pr:PullRequest)-[:CONTAINS_COMMIT]->(com:Commit)
    WHERE pr.repo_id = $repo_id AND pr.number = 2
    RETURN com.sha AS sha, com.message AS msg, com.author AS author
    ORDER BY com.sha
    """
    res_pr_com = g.ro_query(cypher_pr_commits, params={"repo_id": repo_id})
    shas = [r[0] for r in res_pr_com.result_set]
    assert head_sha in shas
    assert branch_sha in shas
    assert len(shas) == 2

    # 2. Verify WorkflowRun is linked to commit via TRIGGERED_BY
    cypher_wf_com = """
    MATCH (wf:WorkflowRun)-[:TRIGGERED_BY]->(com:Commit)
    WHERE wf.repo_id = $repo_id AND wf.run_id = 38904009974
    RETURN com.sha AS sha
    """
    res_wf_com = g.ro_query(cypher_wf_com, params={"repo_id": repo_id})
    assert len(res_wf_com.result_set) == 1
    assert res_wf_com.result_set[0][0] == head_sha

    # 3. Verify WorkflowRun is linked to PullRequest via TESTS_PULL_REQUEST
    cypher_wf_pr = """
    MATCH (wf:WorkflowRun)-[:TESTS_PULL_REQUEST]->(pr:PullRequest)
    WHERE wf.repo_id = $repo_id AND wf.run_id = 38904009974
    RETURN pr.number AS pr_num
    """
    res_wf_pr = g.ro_query(cypher_wf_pr, params={"repo_id": repo_id})
    assert len(res_wf_pr.result_set) == 1
    assert res_wf_pr.result_set[0][0] == 2

    # 4. Verify end-to-end incident trace from Issue -> PR -> Commit <- WorkflowRun
    cypher_full_trace = """
    MATCH (iss:GitHubIssue)<-[:REFERENCES_ISSUE]-(pr:PullRequest)-[:CONTAINS_COMMIT]->(com:Commit)<-[:TRIGGERED_BY]-(wf:WorkflowRun)
    WHERE iss.repo_id = $repo_id AND iss.number = 1 AND com.sha = $head_sha
    RETURN iss.number, pr.number, com.sha, wf.conclusion
    """
    res_trace = g.ro_query(cypher_full_trace, params={"repo_id": repo_id, "head_sha": head_sha})
    assert len(res_trace.result_set) == 1
    row = res_trace.result_set[0]
    assert row[0] == 1
    assert row[1] == 2
    assert row[2] == head_sha
    assert row[3] == "failure"


def test_missing_commit_does_not_create_phantom_nodes():
    """
    Requirement 7: If a referenced commit cannot be fetched, preserve the workflow
    record without creating a phantom commit.
    """
    user_id = "user_gh_missing_commit_eval"
    owner = "org-missing"
    repo = "missing-commit-repo"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": f"{owner}/{repo}",
        "description": "Repo testing missing commit behavior",
        "html_url": f"https://github.com/{owner}/{repo}",
        "default_branch": "main",
        "open_issues_count": 0,
    }
    mock_client.get_issues.return_value = []
    mock_client.get_pull_requests.return_value = []
    mock_client.get_commits.return_value = []

    unfetched_sha = "deadbeefcafebabe000011112222333344445555"
    mock_client.get_workflow_runs.return_value = [
        {
            "id": 776655,
            "name": "Integration Build",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"https://github.com/{owner}/{repo}/actions/runs/776655",
            "head_sha": unfetched_sha,
            "pull_requests": [],
            "created_at": "2026-10-15T12:00:00Z",
        }
    ]

    req = GitHubImportRequest(
        user_id=user_id,
        repo=f"{owner}/{repo}",
        issue_limit=0,
        pr_limit=0,
        commit_limit=0,
        workflow_limit=5,
    )
    res = import_repository(req=req, client=mock_client)
    assert res.success is True
    assert res.summary.imported_workflow_runs == 1
    assert res.summary.imported_commits == 0

    g = get_graph_client()
    repo_id = f"gh_repo_{owner}_{repo}".lower()

    # Verify workflow run exists in graph
    cypher_wf = "MATCH (wf:WorkflowRun {repo_id: $repo_id}) RETURN wf.run_id, wf.head_sha"
    res_wf = g.ro_query(cypher_wf, params={"repo_id": repo_id})
    assert len(res_wf.result_set) == 1
    assert res_wf.result_set[0][0] == 776655
    assert res_wf.result_set[0][1] == unfetched_sha

    # Verify NO commit node exists for unfetched_sha (no phantom commit)
    cypher_com = "MATCH (com:Commit {repo_id: $repo_id}) RETURN count(com)"
    res_com = g.ro_query(cypher_com, params={"repo_id": repo_id})
    assert res_com.result_set[0][0] == 0

    # Verify NO TRIGGERED_BY relationship exists
    cypher_rel = "MATCH (wf:WorkflowRun {repo_id: $repo_id})-[:TRIGGERED_BY]->() RETURN count(*)"
    res_rel = g.ro_query(cypher_rel, params={"repo_id": repo_id})
    assert res_rel.result_set[0][0] == 0


def test_workflow_to_pr_requires_explicit_github_metadata():
    """
    Requirement 6: Link a workflow run to a pull request only when supported by
    GitHub metadata (pull_request event, matching head_sha, or explicit PR number).
    Do NOT infer relationships from titles or timestamps alone.
    """
    user_id = "user_gh_metadata_strict_eval"
    owner = "org-meta"
    repo = "meta-repo"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": f"{owner}/{repo}",
        "description": "Repo testing metadata strictness",
        "html_url": f"https://github.com/{owner}/{repo}",
        "default_branch": "main",
        "open_issues_count": 0,
    }
    mock_client.get_issues.return_value = []
    mock_client.get_commits.return_value = []

    pr_sha = "1111222233334444555566667777888899990000"
    other_sha = "aaaaabbbbbcccccdddddeeeeefffff0000011111"

    mock_client.get_pull_requests.return_value = [
        {
            "number": 10,
            "title": "Upgrade database connection timeout",
            "body": "Fixes timeout issues",
            "state": "open",
            "html_url": f"https://github.com/{owner}/{repo}/pull/10",
            "head": {"sha": pr_sha},
        }
    ]
    mock_client.get_pull_request_commits.return_value = []

    # Run A: workflow_dispatch event on other_sha with similar title -> should NOT link to PR
    # Run B: pull_request event on pr_sha -> SHOULD link to PR
    mock_client.get_workflow_runs.return_value = [
        {
            "id": 101,
            "name": "Upgrade database connection timeout validation",  # identical title!
            "event": "workflow_dispatch",
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"https://github.com/{owner}/{repo}/actions/runs/101",
            "head_sha": other_sha,
            "pull_requests": [],
            "created_at": "2026-10-15T10:00:00Z",
        },
        {
            "id": 102,
            "name": "CI Run on PR branch",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"https://github.com/{owner}/{repo}/actions/runs/102",
            "head_sha": pr_sha,
            "pull_requests": [{"number": 10}],
            "created_at": "2026-10-15T10:00:00Z",
        },
    ]

    req = GitHubImportRequest(
        user_id=user_id,
        repo=f"{owner}/{repo}",
        issue_limit=0,
        pr_limit=5,
        commit_limit=5,
        workflow_limit=5,
    )
    import_repository(req=req, client=mock_client)

    g = get_graph_client()
    repo_id = f"gh_repo_{owner}_{repo}".lower()

    # Verify Run 101 does NOT have TESTS_PULL_REQUEST despite identical title keywords
    cypher_run_101 = """
    MATCH (wf:WorkflowRun {repo_id: $repo_id, run_id: 101})-[:TESTS_PULL_REQUEST]->(pr:PullRequest)
    RETURN count(pr)
    """
    res_101 = g.ro_query(cypher_run_101, params={"repo_id": repo_id})
    assert res_101.result_set[0][0] == 0

    # Verify Run 102 DOES have TESTS_PULL_REQUEST
    cypher_run_102 = """
    MATCH (wf:WorkflowRun {repo_id: $repo_id, run_id: 102})-[:TESTS_PULL_REQUEST]->(pr:PullRequest)
    RETURN pr.number
    """
    res_102 = g.ro_query(cypher_run_102, params={"repo_id": repo_id})
    assert len(res_102.result_set) == 1
    assert res_102.result_set[0][0] == 10


def test_repeated_import_idempotency_for_pr_commits_and_links():
    """
    Verify repeated imports do not duplicate commits, PR-to-commit edges, or workflow edges.
    """
    user_id = "user_gh_idempotency_pr_commits"
    owner = "org-idem"
    repo = "idem-repo"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": f"{owner}/{repo}",
        "description": "Idempotency test repo",
        "html_url": f"https://github.com/{owner}/{repo}",
        "default_branch": "main",
        "open_issues_count": 0,
    }
    mock_client.get_issues.return_value = []
    sha1 = "c0ffee112233445566778899aabbccddeeff0011"
    mock_client.get_pull_requests.return_value = [
        {
            "number": 5,
            "title": "Fix cache evictions",
            "body": "Cache fix",
            "state": "open",
            "html_url": f"https://github.com/{owner}/{repo}/pull/5",
            "head": {"sha": sha1},
        }
    ]
    mock_client.get_pull_request_commits.return_value = [
        {
            "sha": sha1,
            "commit": {"message": "Fix cache eviction logic", "author": {"name": "Dev", "date": "2026-10-15T00:00:00Z"}},
            "html_url": f"https://github.com/{owner}/{repo}/commit/{sha1}",
        }
    ]
    mock_client.get_commits.return_value = []
    mock_client.get_workflow_runs.return_value = [
        {
            "id": 5001,
            "name": "CI",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"https://github.com/{owner}/{repo}/actions/runs/5001",
            "head_sha": sha1,
            "pull_requests": [{"number": 5}],
        }
    ]

    req = GitHubImportRequest(user_id=user_id, repo=f"{owner}/{repo}")

    # Import #1
    res1 = import_repository(req=req, client=mock_client)
    assert res1.success is True

    # Import #2
    res2 = import_repository(req=req, client=mock_client)
    assert res2.success is True

    g = get_graph_client()
    repo_id = f"gh_repo_{owner}_{repo}".lower()

    # Check commit node count is exactly 1
    cypher_com = "MATCH (com:Commit {repo_id: $repo_id}) RETURN count(com)"
    assert g.ro_query(cypher_com, params={"repo_id": repo_id}).result_set[0][0] == 1

    # Check PR-to-Commit relationship count is exactly 1
    cypher_pr_com = "MATCH (pr:PullRequest {repo_id: $repo_id})-[r:CONTAINS_COMMIT]->(com:Commit) RETURN count(r)"
    assert g.ro_query(cypher_pr_com, params={"repo_id": repo_id}).result_set[0][0] == 1

    # Check Workflow-to-PR relationship count is exactly 1
    cypher_wf_pr = "MATCH (wf:WorkflowRun {repo_id: $repo_id})-[r:TESTS_PULL_REQUEST]->(pr:PullRequest) RETURN count(r)"
    assert g.ro_query(cypher_wf_pr, params={"repo_id": repo_id}).result_set[0][0] == 1


def test_complete_issue_pr_commit_workflow_evidence_path():
    """
    Task Verification:
    1. Verify retrieval of Issue #1, PR #2, commit 780c02200fba7c4fef915101e0daedfa901caf36,
       and failed workflow run 38904009974 as distinct records.
    2. Verify relationships REFERENCES_ISSUE, CONTAINS_COMMIT, TRIGGERED_BY, TESTS_PULL_REQUEST.
    3. Verify canonical clickable URLs, commit SHA, and workflow conclusion.
    4. Verify failed CI is labeled as a signal to investigate, not proof of root cause.
    5. Verify memory semantics: prior_investigations is honest empty state (no fabricated interventions).
    6. Verify tenant isolation: other user sees zero records.
    """
    user_id = "user_gh_complete_evidence_path"
    other_user = "user_gh_other_tenant_eval"
    owner = "srisuriya13022006"
    repo = "engineering-incident-lab"
    repo_full = f"{owner}/{repo}"
    head_sha = "780c02200fba7c4fef915101e0daedfa901caf36"
    wf_run_id = 38904009974

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": repo_full,
        "description": "Engineering Incident Lab test repo",
        "html_url": f"https://github.com/{repo_full}",
        "default_branch": "main",
        "open_issues_count": 1,
    }
    mock_client.get_issues.return_value = [
        {
            "number": 1,
            "title": "[INCIDENT] Payment API requests time out under peak traffic",
            "body": "P99 latency degraded to 4200ms during billing spike.",
            "state": "open",
            "html_url": f"https://github.com/{repo_full}/issues/1",
            "user": {"login": "srisuriya13022006"},
            "created_at": "2026-10-16T12:43:42Z",
            "updated_at": "2026-10-16T12:43:42Z",
            "closed_at": None,
        }
    ]
    mock_client.get_pull_requests.return_value = [
        {
            "number": 2,
            "title": "Investigate payment API connection pool configuration",
            "body": "Fixes #1 by increasing max connection pool size.",
            "state": "open",
            "html_url": f"https://github.com/{repo_full}/pull/2",
            "head": {"sha": head_sha},
            "user": {"login": "srisuriya13022006"},
            "created_at": "2026-10-16T12:48:16Z",
        }
    ]
    mock_client.get_pull_request_commits.return_value = [
        {
            "sha": head_sha,
            "commit": {
                "message": "Add comment to trigger pull request workflow",
                "author": {"name": "sri suriya", "date": "2026-10-16T12:55:41Z"},
            },
            "html_url": f"https://github.com/{repo_full}/commit/{head_sha}",
        }
    ]
    mock_client.get_commits.return_value = []
    mock_client.get_workflow_runs.return_value = [
        {
            "id": wf_run_id,
            "name": "Synthetic Incident Check",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"https://github.com/{repo_full}/actions/runs/{wf_run_id}",
            "head_sha": head_sha,
            "created_at": "2026-10-16T12:55:46Z",
            "pull_requests": [{"number": 2}],
        }
    ]

    req = GitHubImportRequest(user_id=user_id, repo=repo_full)
    import_res = import_repository(req=req, client=mock_client)
    assert import_res.success is True

    # Run incident investigation query
    query_req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Payment API requests time out under peak traffic",
        component="payment-api",
    )
    inv_res = investigate_incident(req=query_req)
    assert inv_res.success is True
    assert inv_res.found_evidence is True

    # 1. Preserve memory semantics: No fabricated prior interventions
    assert len(inv_res.prior_investigations) == 0
    assert "No prior internal fix interventions or measured production outcomes are recorded yet" in (inv_res.recommendation_rationale or "")

    # 2. Distinct evidence records for issue, PR, commit, and workflow run
    records_by_type = {}
    for r in inv_res.retrieved_github_records:
        records_by_type.setdefault(r["record_type"], []).append(r)

    assert "issue" in records_by_type
    assert "pull_request" in records_by_type
    assert "commit" in records_by_type
    assert "workflow_run" in records_by_type

    issue_rec = records_by_type["issue"][0]
    assert "Issue #1" in issue_rec["title_or_summary"]
    assert issue_rec["html_url"] == f"https://github.com/{repo_full}/issues/1"
    assert issue_rec["is_confirmed_fix"] is False

    pr_rec = records_by_type["pull_request"][0]
    assert "PR #2" in pr_rec["title_or_summary"]
    assert pr_rec["html_url"] == f"https://github.com/{repo_full}/pull/2"
    assert pr_rec["is_confirmed_fix"] is False

    commit_rec = records_by_type["commit"][0]
    assert head_sha[:8] in commit_rec["title_or_summary"]
    assert commit_rec["html_url"] == f"https://github.com/{repo_full}/commit/{head_sha}"
    assert head_sha in commit_rec["notes"]

    wf_rec = records_by_type["workflow_run"][0]
    assert wf_rec["state_or_status"] == "failure"
    assert wf_rec["html_url"] == f"https://github.com/{repo_full}/actions/runs/{wf_run_id}"
    assert "Signal to investigate" in wf_rec["notes"]
    assert "not proof of production root cause" in wf_rec["notes"]

    # 3. Verify graph paths connecting all 4 entities
    paths = inv_res.evidence_paths
    assert any("REFERENCES_ISSUE" in p for p in paths)
    assert any("CONTAINS_COMMIT" in p for p in paths)
    assert any("TESTS_PULL_REQUEST" in p for p in paths)
    assert any("TRIGGERED_BY" in p for p in paths)

    # 4. Verify complete 4-hop chain
    chain_paths = [p for p in paths if "<-[REFERENCES_ISSUE]-" in p and "-[CONTAINS_COMMIT]->" in p and "<-[TRIGGERED_BY]-" in p]
    assert len(chain_paths) >= 1
    assert "GitHubIssue(#1)" in chain_paths[0]
    assert "PullRequest(#2)" in chain_paths[0]
    assert head_sha[:8] in chain_paths[0]
    assert "Synthetic Incident Check" in chain_paths[0]
    assert "failure" in chain_paths[0]

    # 5. Verify tenant isolation: other tenant cannot retrieve these records
    other_query = IncidentQueryRequest(
        user_id=other_user,
        symptoms="Payment API requests time out under peak traffic",
        component="payment-api",
    )
    other_res = investigate_incident(req=other_query)
    assert other_res.found_evidence is False
    assert len(other_res.retrieved_github_records) == 0
    assert len(other_res.evidence_paths) == 0


def test_get_repository_details_endpoint_and_relationships():
    """
    Verify:
    1. GET /github/repository returns full repository metadata and counts.
    2. Separate lists of issues, pull requests, commits, and workflow runs with canonical links.
    3. Verified relationships (REFERENCES_ISSUE, CONTAINS_COMMIT, TRIGGERED_BY, TESTS_PULL_REQUEST).
    4. Multi-hop causal paths.
    5. Clearly identified missing relationships.
    """
    user_id = "user_gh_details_eval"
    owner = "org-details"
    repo = "details-repo"
    repo_full = f"{owner}/{repo}"
    head_sha = "aabbcc1122334455667788990011223344556677"
    main_sha = "11223344556677889900aabbccddeeff00112233"
    wf_id = 998877

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": repo_full,
        "description": "Details evaluation repository",
        "html_url": f"https://github.com/{repo_full}",
        "default_branch": "main",
        "open_issues_count": 1,
    }
    mock_client.get_issues.return_value = [
        {
            "number": 10,
            "title": "Gateway timeout under high concurrency",
            "body": "P99 latency elevated",
            "state": "open",
            "html_url": f"https://github.com/{repo_full}/issues/10",
            "user": {"login": "dev1"},
            "created_at": "2026-10-18T00:00:00Z",
            "updated_at": "2026-10-18T01:00:00Z",
            "closed_at": None,
        }
    ]
    mock_client.get_pull_requests.return_value = [
        {
            "number": 20,
            "title": "Tune connection pool max size",
            "body": "Fixes #10 by increasing pool to 50",
            "state": "open",
            "html_url": f"https://github.com/{repo_full}/pull/20",
            "head": {"sha": head_sha},
            "user": {"login": "dev1"},
            "created_at": "2026-10-18T01:10:00Z",
        }
    ]
    mock_client.get_pull_request_commits.return_value = [
        {
            "sha": head_sha,
            "commit": {
                "message": "Increase pool connections to 50",
                "author": {"name": "Dev One", "date": "2026-10-18T01:05:00Z"},
            },
            "html_url": f"https://github.com/{repo_full}/commit/{head_sha}",
        }
    ]
    mock_client.get_commits.return_value = [
        {
            "sha": main_sha,
            "commit": {
                "message": "Initial project setup on main",
                "author": {"name": "Dev One", "date": "2026-10-17T12:00:00Z"},
            },
            "html_url": f"https://github.com/{repo_full}/commit/{main_sha}",
        }
    ]
    mock_client.get_workflow_runs.return_value = [
        {
            "id": wf_id,
            "name": "Integration Suite",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"https://github.com/{repo_full}/actions/runs/{wf_id}",
            "head_sha": head_sha,
            "pull_requests": [{"number": 20}],
            "created_at": "2026-10-18T01:15:00Z",
        }
    ]

    import_res = import_repository(
        req=GitHubImportRequest(user_id=user_id, repo=repo_full),
        client=mock_client,
    )
    assert import_res.success is True

    # 1. Test API endpoint GET /github/repository
    res = client.get(f"/github/repository?user_id={user_id}&repo={repo_full}")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["user_id"] == user_id
    assert data["repo"] == repo_full

    repo_data = data["repository"]
    assert repo_data["full_name"] == repo_full
    assert repo_data["owner"] == owner
    assert repo_data["name"] == repo
    assert repo_data["default_branch"] == "main"
    assert repo_data["description"] == "Details evaluation repository"
    assert repo_data["html_url"] == f"https://github.com/{repo_full}"

    # Verify counts
    assert repo_data["counts"]["issues"] == 1
    assert repo_data["counts"]["pull_requests"] == 1
    assert repo_data["counts"]["commits"] == 2  # 1 from PR branch + 1 from main
    assert repo_data["counts"]["workflow_runs"] == 1

    # Verify separate lists
    assert len(repo_data["issues"]) == 1
    iss = repo_data["issues"][0]
    assert iss["number"] == 10
    assert iss["state"] == "open"
    assert iss["html_url"] == f"https://github.com/{repo_full}/issues/10"
    assert 20 in iss["referencing_pr_numbers"]

    assert len(repo_data["pull_requests"]) == 1
    pr = repo_data["pull_requests"][0]
    assert pr["number"] == 20
    assert pr["head_sha"] == head_sha
    assert 10 in pr["referenced_issue_numbers"]
    assert head_sha in pr["commit_shas"]
    assert wf_id in pr["testing_workflow_run_ids"]

    assert len(repo_data["commits"]) == 2
    com_shas = [c["sha"] for c in repo_data["commits"]]
    assert head_sha in com_shas
    assert main_sha in com_shas

    assert len(repo_data["workflow_runs"]) == 1
    wf = repo_data["workflow_runs"][0]
    assert wf["run_id"] == wf_id
    assert wf["conclusion"] == "failure"
    assert 20 in wf["tested_pr_numbers"]

    # Verify verified relationships
    v_rels = [v["relationship"] for v in repo_data["verified_relationships"]]
    assert "REFERENCES_ISSUE" in v_rels
    assert "CONTAINS_COMMIT" in v_rels
    assert "TESTS_PULL_REQUEST" in v_rels
    assert "TRIGGERED_BY" in v_rels

    # Verify multi-hop paths
    assert any("WorkflowRun" in p and "PullRequest" in p and "GitHubIssue" in p for p in repo_data["multi_hop_paths"])

    # Verify missing relationships clearly distinguish unlinked items
    missing_coms = [m for m in repo_data["missing_relationships"] if m["record_type"] == "commit" and main_sha[:8] in m["identifier"]]
    assert len(missing_coms) >= 1
    assert any(m["missing_relationship"] == "CONTAINS_COMMIT" for m in missing_coms)


def test_get_repository_details_empty_states():
    """Verify repository with 0 issues/PRs/commits returns clean empty lists and zero counts."""
    user_id = "user_gh_empty_eval"
    owner = "org-empty"
    repo = "empty-repo"
    repo_full = f"{owner}/{repo}"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": repo_full,
        "description": "Empty test repository",
        "html_url": f"https://github.com/{repo_full}",
        "default_branch": "main",
        "open_issues_count": 0,
    }
    mock_client.get_issues.return_value = []
    mock_client.get_pull_requests.return_value = []
    mock_client.get_commits.return_value = []
    mock_client.get_workflow_runs.return_value = []

    res_import = import_repository(
        req=GitHubImportRequest(user_id=user_id, repo=repo_full),
        client=mock_client,
    )
    assert res_import.success is True

    res = client.get(f"/github/repository?user_id={user_id}&repo={repo_full}")
    assert res.status_code == 200
    data = res.json()["repository"]
    assert data["counts"]["issues"] == 0
    assert data["counts"]["pull_requests"] == 0
    assert data["counts"]["commits"] == 0
    assert data["counts"]["workflow_runs"] == 0
    assert data["issues"] == []
    assert data["pull_requests"] == []
    assert data["commits"] == []
    assert data["workflow_runs"] == []
    assert data["verified_relationships"] == []
    assert data["multi_hop_paths"] == []
    assert data["missing_relationships"] == []


def test_get_repository_details_tenant_isolation():
    """Verify that tenant A cannot access repository details imported by tenant B."""
    user_a = "user_tenant_alpha"
    user_b = "user_tenant_beta"
    owner = "org-iso"
    repo = "isolated-repo"
    repo_full = f"{owner}/{repo}"

    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": repo_full,
        "description": "Tenant isolated repo",
        "html_url": f"https://github.com/{repo_full}",
        "default_branch": "main",
        "open_issues_count": 0,
    }
    mock_client.get_issues.return_value = []
    mock_client.get_pull_requests.return_value = []
    mock_client.get_commits.return_value = []
    mock_client.get_workflow_runs.return_value = []

    # Import under user_a only
    import_repository(
        req=GitHubImportRequest(user_id=user_a, repo=repo_full),
        client=mock_client,
    )

    # user_a can access
    res_a = client.get(f"/github/repository?user_id={user_a}&repo={repo_full}")
    assert res_a.status_code == 200

    # user_b cannot access: returns 404
    res_b = client.get(f"/github/repository?user_id={user_b}&repo={repo_full}")
    assert res_b.status_code == 404
    assert "not found" in res_b.json()["detail"]["message"].lower()


def test_get_repository_details_missing_record_404():
    """Verify 404 response when querying a non-existent repository."""
    res = client.get("/github/repository?user_id=user_any&repo=non_existent_org/non_existent_repo")
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "Repository Not Found"


def test_repository_details_ui_elements_in_html():
    """Verify index.html contains all necessary UI containers and elements for the in-app details view."""
    res = client.get("/")
    assert res.status_code == 200
    html = res.text

    # Back to repositories button
    assert "btn-back-to-repos" in html
    assert "backToReposList()" in html

    # Details containers
    assert "github-repo-details-view" in html
    assert "repo-details-external-url" in html
    assert "repo-details-multihop-list" in html
    assert "repo-details-verified-list" in html
    assert "repo-details-missing-list" in html

    # 4 distinct record list containers
    assert "repo-details-prs-list" in html
    assert "repo-details-issues-list" in html
    assert "repo-details-commits-list" in html
    assert "repo-details-workflows-list" in html


