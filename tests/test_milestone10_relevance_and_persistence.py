"""
Milestone 10 Verification Test Suite:
- Repository persistence & idempotent import
- Precision relevance filtering (Tests A through G)
- Cross-repository isolation vs legitimate shared dependencies
- Structured graph visualization nodes and edges
"""

import pytest
from unittest.mock import MagicMock

from app.incident.models import IncidentQueryRequest
from app.incident.investigator import investigate_incident
from app.incident.operations import query_incident_graph
from app.github.models import GitHubImportRequest
from app.github.client import GitHubClient
from app.github.service import import_repository, seed_demo_github_scenario
from app.github.operations import (
    get_user_repositories_summary,
    get_repository_details,
    query_github_evidence_for_incident,
)
from app.graph.connection import get_graph_client, check_connection


@pytest.fixture
def dual_repo_tenant():
    """
    Sets up a tenant graph containing BOTH:
    1. 'srisuriya13022006/engineering-incident-lab' (payment service, issue #1, PR #2, commit, failed workflow)
    2. 'gateway-corp/gateway-api' (API gateway, tail latency issues #38, #41, PR #42, connection pooling)
    """
    is_available, _ = check_connection()
    if not is_available:
        pytest.skip("Requires active FalkorDB instance.")

    user_id = "user_m10_dual_repo_eval"
    owner = "srisuriya13022006"
    repo = "engineering-incident-lab"
    repo_full = f"{owner}/{repo}"
    head_sha = "780c02200fba7c4fef915101e0daedfa901caf36"
    wf_run_id = 38904009974

    # 0. Clean up any existing DEPENDS_ON links for isolation
    graph = get_graph_client()
    graph.query("""
    MATCH (r1:Repository)-[rel:DEPENDS_ON|CROSS_REPO_REF]->(r2:Repository)
    DELETE rel
    """)

    # 1. Seed gateway scenario (Gateway incidents + Gateway repo)
    seed_demo_github_scenario(user_id=user_id)

    # Ingest incident history for gateway-api so that Test B and graph tests have incidents, fix attempts, and outcomes
    from app.incident.models import IncidentIngestPayload, IncidentNode, InvestigationNode, FixAttemptNode, OutcomeNode
    from app.incident.operations import ingest_incident_payload

    now_iso = "2026-10-14T08:00:00Z"
    inc_payload = IncidentIngestPayload(
        incident=IncidentNode(
            id="inc_gateway_tail_latency",
            title="Gateway API p99 latency degradation",
            symptoms="API response latency p99 degradation above 120ms during peak load",
            affected_component="gateway-api",
            severity="high",
            timestamp=now_iso,
            user_id=user_id,
        ),
        investigations=[
            InvestigationNode(
                id="inv_pool_exhaustion",
                incident_id="inc_gateway_tail_latency",
                hypothesis="Database connection pool exhaustion under load",
                evidence_notes="Observed queued socket connections in gateway metrics",
                investigator="SRE Team",
                timestamp=now_iso,
                user_id=user_id,
            )
        ],
        fix_attempts=[
            FixAttemptNode(
                id="fix_pool_increase_50",
                investigation_id="inv_pool_exhaustion",
                description="Increase upstream HTTP connection pool from 10 to 50",
                status="executed",
                timestamp=now_iso,
                user_id=user_id,
            )
        ],
        outcomes=[
            OutcomeNode(
                id="out_pool_increase_fail",
                fix_attempt_id="fix_pool_increase_50",
                metric="p99_latency_ms",
                before_value="120ms",
                after_value="118ms",
                evaluation="unsuccessful",
                notes="Latency remained high at 118ms; connection pool was not the bottleneck",
                timestamp=now_iso,
                user_id=user_id,
            )
        ],
        affected_entities=["gateway-api"],
    )
    ingest_incident_payload(user_id=user_id, payload=inc_payload)

    # 2. Import engineering-incident-lab (Payment incident)
    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": repo_full,
        "description": "Engineering Incident Lab payment service repository",
        "html_url": f"https://github.com/{repo_full}",
        "default_branch": "main",
        "open_issues_count": 1,
    }
    mock_client.get_issues.return_value = [
        {
            "number": 1,
            "title": "[INCIDENT] Payment API requests time out under peak traffic",
            "body": "P99 latency degraded to 4200ms during billing spike. Stripe integration timed out.",
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
            "body": "Fixes #1 by increasing max connection pool size for billing.",
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

    import_res = import_repository(req=GitHubImportRequest(user_id=user_id, repo=repo_full), client=mock_client)
    assert import_res.success is True
    return user_id, repo_full


def test_part1_repository_persistence_and_listing_restoration(dual_repo_tenant):
    """
    Verifies that after importing repositories, get_user_repositories_summary
    and get_repository_details restore full metadata directly from FalkorDB.
    """
    user_id, lab_repo = dual_repo_tenant

    repos = get_user_repositories_summary(user_id=user_id)
    assert len(repos) == 2
    repo_names = {r["full_name"] for r in repos}
    assert lab_repo in repo_names
    assert "gateway-corp/gateway-api" in repo_names

    # Check details restoration
    details = get_repository_details(user_id=user_id, repo_identifier=lab_repo)
    assert details is not None
    assert details.full_name == lab_repo
    assert details.counts["issues"] == 1
    assert details.counts["pull_requests"] == 1
    assert len(details.issues) == 1
    assert len(details.pull_requests) == 1


def test_part1_idempotent_repeated_imports(dual_repo_tenant):
    """
    Verifies that importing the same repository repeatedly does not produce duplicate nodes.
    """
    user_id, lab_repo = dual_repo_tenant

    # Re-import same repository
    mock_client = MagicMock(spec=GitHubClient)
    mock_client.get_repo.return_value = {
        "full_name": lab_repo,
        "description": "Updated description",
        "html_url": f"https://github.com/{lab_repo}",
        "default_branch": "main",
        "open_issues_count": 1,
    }
    mock_client.get_issues.return_value = []
    mock_client.get_pull_requests.return_value = []
    mock_client.get_pull_request_commits.return_value = []
    mock_client.get_commits.return_value = []
    mock_client.get_workflow_runs.return_value = []

    res = import_repository(req=GitHubImportRequest(user_id=user_id, repo=lab_repo), client=mock_client)
    assert res.success is True

    # Total repositories count must still be 2 (no duplicate repo node)
    repos = get_user_repositories_summary(user_id=user_id)
    assert len(repos) == 2


def test_part2_test_a_payment_api_timeouts(dual_repo_tenant):
    """
    Test A — Payment API timeouts
    Query: 'Payment API timeouts during peak traffic'
    Expected:
    - Finds payment incident issue (Issue #1).
    - Linked PR #2, commit, and workflow.
    - Does NOT show unrelated gateway records as direct evidence solely because gateway is imported.
    """
    user_id, lab_repo = dual_repo_tenant

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Payment API timeouts during peak traffic",
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is True

    # Assert relevant inclusion
    rec_types = [r.get("record_type") for r in res.retrieved_github_records]
    assert "issue" in rec_types
    assert "pull_request" in rec_types

    # Every direct record must belong to payment/engineering-incident-lab
    for rec in res.retrieved_github_records:
        assert rec.get("repository") == lab_repo
        assert "gateway" not in rec.get("repository", "").lower()

    # Verify no gateway evidence paths
    for path in res.evidence_paths:
        assert "gateway-corp" not in path


def test_part2_test_b_gateway_latency(dual_repo_tenant):
    """
    Test B — Gateway latency
    Query: 'Tail timeout alerts and slow gateway response during peak traffic'
    Expected:
    - Finds gateway incident history (inc_gateway_tail_latency).
    - Retrieves failed intervention ('Increase upstream HTTP connection pool') and measured outcome ('unsuccessful').
    - Includes associated GitHub records only when connection supported (gateway-api).
    - Does NOT show payment records.
    """
    user_id, _ = dual_repo_tenant

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Tail timeout alerts and slow gateway response during peak traffic",
        component="gateway-api",
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is True
    assert len(res.unsuccessful_fixes_to_avoid) > 0
    assert any("Increase upstream HTTP connection pool" in fix for fix in res.unsuccessful_fixes_to_avoid)

    # All retrieved github records must belong to gateway-corp/gateway-api
    for rec in res.retrieved_github_records:
        assert rec.get("repository") == "gateway-corp/gateway-api"
        assert "engineering-incident-lab" not in rec.get("repository", "")


def test_part2_test_c_ci_workflow_failure(dual_repo_tenant):
    """
    Test C — CI workflow failure
    Query: 'The latest pull request failed its automated workflow checks'
    Expected:
    - Finds relevant failed workflow.
    - Traces to tested PR and commit.
    - Does NOT attach unrelated issues or workflows from gateway.
    """
    user_id, lab_repo = dual_repo_tenant

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="The latest pull request failed its automated workflow checks",
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is True

    # Must find the failed workflow run
    wf_records = [r for r in res.retrieved_github_records if r.get("record_type") == "workflow_run"]
    assert len(wf_records) > 0
    assert any(r.get("state_or_status") == "failure" for r in wf_records)

    # Must connect to PR #2
    pr_records = [r for r in res.retrieved_github_records if r.get("record_type") == "pull_request"]
    assert len(pr_records) > 0

    # Must not attach unrelated gateway issues as direct causes
    for rec in res.retrieved_github_records:
        assert "gateway-corp" not in rec.get("repository", "")


def test_part2_test_d_unrelated_component_abstention(dual_repo_tenant):
    """
    Test D — Unrelated component
    Query: 'Quantum navigation subsystem instability'
    Expected:
    - Does NOT return payment or gateway records as direct evidence.
    - Returns honest no-evidence state.
    """
    user_id, _ = dual_repo_tenant

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Quantum navigation subsystem instability",
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is False
    assert len(res.retrieved_github_records) == 0
    assert len(res.matched_incidents) == 0
    assert "No prior incidents, investigations, fix attempts, or connected GitHub records" in res.insufficient_evidence_reason


def test_part2_test_e_explicit_repository_scope(dual_repo_tenant):
    """
    Test E — Explicit repository scope
    Query: 'Payment API timeouts in srisuriya13022006/engineering-incident-lab'
    Expected:
    - Scopes direct evidence strictly to srisuriya13022006/engineering-incident-lab.
    - Excludes gateway records.
    """
    user_id, lab_repo = dual_repo_tenant

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Payment API timeouts in srisuriya13022006/engineering-incident-lab",
        repository=lab_repo,
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is True
    assert len(res.retrieved_github_records) > 0
    for rec in res.retrieved_github_records:
        assert rec.get("repository") == lab_repo


def test_part2_test_f_tenant_isolation(dual_repo_tenant):
    """
    Test F — Tenant isolation
    Expected:
    - Foreign tenant cannot retrieve records from dual_repo_tenant.
    """
    _, lab_repo = dual_repo_tenant
    foreign_user = "user_m10_isolated_stranger"

    # Foreign tenant queries payment timeouts
    req = IncidentQueryRequest(
        user_id=foreign_user,
        symptoms="Payment API timeouts during peak traffic",
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is False
    assert len(res.retrieved_github_records) == 0

    # Foreign tenant repos summary
    repos = get_user_repositories_summary(user_id=foreign_user)
    assert len(repos) == 0


def test_part2_test_g_legitimate_shared_dependency(dual_repo_tenant):
    """
    Test G — Legitimate shared dependency
    Expected:
    - Cross-repository evidence is allowed when a real dependency (DEPENDS_ON) links repositories.
    - Result explains why external record is relevant.
    """
    user_id, lab_repo = dual_repo_tenant

    # Explicitly add a DEPENDS_ON edge in FalkorDB from lab_repo to gateway-api
    graph = get_graph_client()
    dep_cypher = """
    MATCH (r1:Repository {full_name: $lab_repo})
    MATCH (r2:Repository {full_name: 'gateway-corp/gateway-api'})
    MERGE (r1)-[r:DEPENDS_ON]->(r2)
    """
    graph.query(dep_cypher, params={"lab_repo": lab_repo})

    try:
        # Query with lab_repo as target
        records, paths, _ = query_github_evidence_for_incident(
            user_id=user_id,
            keywords=["payment", "timeouts"],
            target_repo=lab_repo,
        )

        # Should find lab_repo direct records AND gateway-api related dependency records
        repos_found = {r.repository for r in records}
        assert lab_repo in repos_found
        assert "gateway-corp/gateway-api" in repos_found

        # Check that external records are marked as 'related' with clear justification
        gateway_records = [r for r in records if r.repository == "gateway-corp/gateway-api"]
        assert len(gateway_records) > 0
        for gr in gateway_records:
            assert gr.evidence_category == "related"
            assert "Cross-repository dependency" in gr.relevance_reason

        # Verify dependency path is recorded
        assert any("DEPENDS_ON" in p for p in paths)
    finally:
        graph.query("""
        MATCH (r1:Repository {full_name: $lab_repo})-[r:DEPENDS_ON]->(r2:Repository {full_name: 'gateway-corp/gateway-api'})
        DELETE r
        """, params={"lab_repo": lab_repo})


def test_part3_interactive_graph_visualization_structure(dual_repo_tenant):
    """
    Verifies that investigate_incident populates structured graph_nodes and graph_edges
    with distinct entity types (incident, investigation, fix_attempt, outcome, next_action, repository, pull_request, issue, commit, workflow_run).
    """
    user_id, lab_repo = dual_repo_tenant

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Tail timeout alerts and slow gateway response during peak traffic",
        component="gateway-api",
    )
    res = investigate_incident(req=req)

    assert res.success is True
    assert res.found_evidence is True
    assert len(res.graph_nodes) > 0
    assert len(res.graph_edges) > 0

    node_types = {n["type"] for n in res.graph_nodes}
    assert "incident" in node_types
    assert "investigation" in node_types
    assert "fix_attempt" in node_types
    assert "outcome" in node_types
    assert "next_action" in node_types
    assert "repository" in node_types

    edge_types = {e["relationship"] for e in res.graph_edges}
    assert "INVESTIGATED_BY" in edge_types
    assert "LED_TO" in edge_types
    assert "RESULTED_IN" in edge_types
    assert "RECOMMENDS" in edge_types

    # Check investigation summary object
    summary = res.investigation_summary
    assert summary is not None
    assert "affected_component" in summary
    assert summary["affected_component"] == "gateway-api"
    assert "most_relevant_finding" in summary
    assert "previous_intervention_outcome" in summary
    assert "recommended_investigation" in summary
    assert "confidence_level" in summary
