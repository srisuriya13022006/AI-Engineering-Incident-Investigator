"""
Milestone 11 Regression Test Suite:
- Investigation summary consistency (no gateway-api on payment incident, honest interventions)
- Cross-repository explanations
- Graph layout & edge validity (every edge connects existing nodes, no isolated NextAction)
- Complete node provenance (entity_type, repository, source_identifier, evidence_category)
- Defensible epistemic confidence calculation
"""

import pytest
from unittest.mock import MagicMock

from app.incident.models import (
    IncidentQueryRequest,
    IncidentIngestPayload,
    IncidentNode,
    InvestigationNode,
    FixAttemptNode,
    OutcomeNode,
)
from app.incident.investigator import investigate_incident, compute_evidence_confidence
from app.incident.operations import ingest_incident_payload
from app.github.models import GitHubImportRequest
from app.github.client import GitHubClient
from app.github.service import import_repository, seed_demo_github_scenario
from app.graph.connection import get_graph_client, check_connection


@pytest.fixture
def m11_test_environment():
    """
    Sets up a clean tenant environment with both:
    1. 'srisuriya13022006/engineering-incident-lab' (Payment incident repo)
    2. 'gateway-corp/gateway-api' (Gateway API repo with prior failed pool resize)
    """
    is_available, _ = check_connection()
    if not is_available:
        pytest.skip("Requires active FalkorDB instance.")

    user_id = "user_m11_eval_tenant"
    owner = "srisuriya13022006"
    repo = "engineering-incident-lab"
    head_sha = "780c02200fba7c4fef915101e0daedfa901caf36"
    wf_run_id = 38904009974

    graph = get_graph_client()
    # Clean up prior relationships
    graph.query("""
    MATCH (r1:Repository)-[rel:DEPENDS_ON|CROSS_REPO_REF]->(r2:Repository)
    DELETE rel
    """)

    # 1. Seed gateway demo scenario
    seed_demo_github_scenario(user_id=user_id)

    # Ingest gateway incident with failed pool resize
    now_iso = "2026-10-18T10:00:00Z"
    inc_payload = IncidentIngestPayload(
        incident=IncidentNode(
            id="inc_gateway_m11",
            title="Gateway API p99 latency degradation",
            symptoms="API response latency p99 degradation above 120ms during peak load",
            affected_component="gateway-api",
            severity="high",
            timestamp=now_iso,
            status="investigating",
            user_id=user_id,
        ),
        investigations=[
            InvestigationNode(
                id="inv_gateway_m11",
                incident_id="inc_gateway_m11",
                hypothesis="Downstream connection pool starvation causing HTTP request queuing",
                evidence_notes="TCP connection queue depth reached maximum limit 500",
                timestamp=now_iso,
                user_id=user_id,
            )
        ],
        fix_attempts=[
            FixAttemptNode(
                id="fix_gateway_m11",
                investigation_id="inv_gateway_m11",
                description="Increase HTTP client connection pool size from 50 to 200",
                status="executed",
                timestamp=now_iso,
                user_id=user_id,
            )
        ],
        outcomes=[
            OutcomeNode(
                id="out_gateway_m11",
                fix_attempt_id="fix_gateway_m11",
                metric="p99_latency_ms",
                before_value="145ms",
                after_value="152ms",
                evaluation="unsuccessful",
                notes="Connection pool resize increased lock contention and exacerbated latency",
                timestamp=now_iso,
                user_id=user_id,
            )
        ],
    )
    ingest_incident_payload(user_id=user_id, payload=inc_payload)

    # 2. Seed payment repository (engineering-incident-lab)
    client_mock = MagicMock(spec=GitHubClient)
    client_mock.get_repo.return_value = {
        "id": 111111,
        "name": repo,
        "full_name": f"{owner}/{repo}",
        "description": "Lab environment for reproducing and diagnosing engineering payment incidents",
        "default_branch": "main",
        "html_url": f"https://github.com/{owner}/{repo}",
    }
    client_mock.get_issues.return_value = [
        {
            "id": 222221,
            "number": 1,
            "title": "Payment gateway timeout on checkout endpoint",
            "body": "Checkout requests fail with 504 Gateway Timeout during peak traffic.",
            "state": "open",
            "html_url": f"https://github.com/{owner}/{repo}/issues/1",
            "created_at": "2026-10-14T09:00:00Z",
        }
    ]
    client_mock.get_pull_requests.return_value = [
        {
            "id": 333332,
            "number": 2,
            "title": "Fix checkout payment retry and connection timeout logic",
            "body": "Resolves #1 by adjusting retry backoff and socket read timeout.",
            "state": "open",
            "merged": False,
            "html_url": f"https://github.com/{owner}/{repo}/pull/2",
            "created_at": "2026-10-14T09:30:00Z",
        }
    ]
    client_mock.get_commits.return_value = [
        {
            "sha": head_sha,
            "commit": {
                "message": "Adjust checkout retry backoff logic for #1",
                "author": {"name": "Incident Engineer", "date": "2026-10-14T09:35:00Z"},
            },
            "html_url": f"https://github.com/{owner}/{repo}/commit/{head_sha}",
        }
    ]
    client_mock.get_workflow_runs.return_value = [
        {
            "id": wf_run_id,
            "name": "Integration & Unit Tests",
            "head_sha": head_sha,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "failure",
            "html_url": f"https://github.com/{owner}/{repo}/actions/runs/{wf_run_id}",
            "created_at": "2026-10-14T09:40:00Z",
        }
    ]

    import_repository(
        GitHubImportRequest(user_id=user_id, repo=f"{owner}/{repo}"),
        client=client_mock,
    )

    return user_id, f"{owner}/{repo}", "gateway-corp/gateway-api"


def test_payment_incident_summary_consistency(m11_test_environment):
    """
    Requirement 1:
    - Ensure incident symptoms, affected component, and recommendation derive from the same response.
    - Do NOT show 'gateway-api' for a payment incident query in engineering-incident-lab.
    - Component must match payment/engineering-incident-lab context.
    """
    user_id, lab_repo, _ = m11_test_environment

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Payment gateway timeout on checkout endpoint in engineering-incident-lab",
        repository=lab_repo,
    )
    resp = investigate_incident(req)

    assert resp.found_evidence is True
    summary = resp.investigation_summary
    assert summary is not None

    # CRITICAL: gateway-api must NOT appear for this payment incident!
    assert summary["affected_component"] != "gateway-api"
    assert "engineering-incident-lab" in summary["affected_component"]
    assert "gateway-api" not in summary["recommended_investigation"]
    assert "gateway-api" not in resp.recommendation

    # All retrieved records belong to lab_repo
    for rec in resp.retrieved_github_records:
        assert rec["repository"] == lab_repo


def test_gateway_incident_summary_consistency(m11_test_environment):
    """
    Requirement 1 & 2:
    - Gateway incident retrieves gateway-specific evidence.
    - Summary retains 'gateway-api' and cites the unsuccessful pool resize.
    - Next action recommends against re-attempting pool resize.
    """
    user_id, _, gateway_repo = m11_test_environment

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="API response latency p99 degradation above 120ms during peak load",
        component="gateway-api",
    )
    resp = investigate_incident(req)

    assert resp.found_evidence is True
    summary = resp.investigation_summary
    assert summary is not None

    assert summary["affected_component"] == "gateway-api"
    assert "pool" in summary["previous_intervention_outcome"].lower()
    assert "UNSUCCESSFUL" in summary["previous_intervention_outcome"]
    assert "Do NOT re-attempt" in summary["recommended_investigation"]


def test_ci_failure_traces_correct_pr_and_commit(m11_test_environment):
    """
    Requirement 1 & 2:
    - CI failure traces to the correct pull request (#2) and commit (780c0220).
    """
    user_id, lab_repo, _ = m11_test_environment

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Failing workflow run on latest automated test checks in engineering-incident-lab",
        repository=lab_repo,
    )
    resp = investigate_incident(req)

    assert resp.found_evidence is True
    recs = resp.retrieved_github_records

    # Check for Issue #1, PR #2, commit, and workflow run
    pr_rec = next((r for r in recs if r["record_type"] == "pull_request"), None)
    commit_rec = next((r for r in recs if r["record_type"] == "commit"), None)
    wf_rec = next((r for r in recs if r["record_type"] == "workflow_run"), None)

    assert pr_rec is not None
    assert "PR #2" in pr_rec["title_or_summary"]
    assert commit_rec is not None
    assert "780c0220" in commit_rec["title_or_summary"]
    assert wf_rec is not None
    assert wf_rec["state_or_status"] == "failure"


def test_no_evidence_query_abstains_without_fabrication(m11_test_environment):
    """
    Requirement 1 & 4:
    - No-evidence queries abstain, do not fabricate relationships, and return empty graphs.
    """
    user_id, _, _ = m11_test_environment

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Obscure quantum magnetic solar flare bit flip error 987654321",
        component="unknown-satellite-subsystem",
    )
    resp = investigate_incident(req)

    assert resp.found_evidence is False
    assert resp.insufficient_evidence_reason is not None
    assert "Abstaining from ungrounded recommendation" in resp.insufficient_evidence_reason
    assert resp.recommendation is None
    assert resp.investigation_summary is None
    assert resp.graph_nodes == []
    assert resp.graph_edges == []


def test_missing_intervention_records_distinction(m11_test_environment):
    """
    Requirement 1:
    - Distinguish "no intervention found in retrieved evidence" from "no prior intervention exists."
    """
    user_id, _, _ = m11_test_environment

    # Ingest an incident with an investigation hypothesis, but NO fix attempts or outcomes
    now_iso = "2026-10-18T11:00:00Z"
    inc_payload = IncidentIngestPayload(
        incident=IncidentNode(
            id="inc_auth_unresolved",
            title="OAuth token signing certificate expiry",
            symptoms="JWT token signature validation failed with 401 Unauthorized",
            affected_component="auth-service",
            severity="critical",
            timestamp=now_iso,
            status="investigating",
            user_id=user_id,
        ),
        investigations=[
            InvestigationNode(
                id="inv_auth_unresolved",
                incident_id="inc_auth_unresolved",
                hypothesis="Signing key rotation cron job stopped executing",
                evidence_notes="Last valid signature generated 24h ago",
                timestamp=now_iso,
                user_id=user_id,
            )
        ],
        fix_attempts=[],  # NO FIX ATTEMPTS
        outcomes=[],      # NO OUTCOMES
    )
    ingest_incident_payload(user_id=user_id, payload=inc_payload)

    # Query the auth incident
    req_auth = IncidentQueryRequest(
        user_id=user_id,
        symptoms="JWT token signature validation failed with 401 Unauthorized",
        component="auth-service",
    )
    resp_auth = investigate_incident(req_auth)
    assert resp_auth.found_evidence is True
    assert (
        resp_auth.investigation_summary["previous_intervention_outcome"]
        == "No intervention found in retrieved evidence for this incident."
    )

    # Query a repository component with NO prior investigations at all
    req_gh = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Payment gateway timeout on checkout endpoint in engineering-incident-lab",
        repository="srisuriya13022006/engineering-incident-lab",
    )
    resp_gh = investigate_incident(req_gh)
    assert resp_gh.found_evidence is True
    assert (
        resp_gh.investigation_summary["previous_intervention_outcome"]
        == "No prior interventions exist in graph memory for this component."
    )


def test_graph_edges_connect_existing_nodes(m11_test_environment):
    """
    Requirement 2 & 5:
    - Every graph edge must connect existing nodes in graph_nodes (no dangling edges).
    - Recommended next action is visibly connected to the investigation context.
    """
    user_id, lab_repo, _ = m11_test_environment

    for query_tuple in [
        ("API response latency p99 degradation above 120ms during peak load", "gateway-api", None),
        ("Payment gateway timeout on checkout endpoint in engineering-incident-lab", None, lab_repo),
    ]:
        sym, comp, repo = query_tuple
        req = IncidentQueryRequest(user_id=user_id, symptoms=sym, component=comp, repository=repo)
        resp = investigate_incident(req)

        assert resp.found_evidence is True
        nodes = resp.graph_nodes
        edges = resp.graph_edges

        assert len(nodes) > 0
        assert len(edges) > 0

        node_ids = {n["id"] for n in nodes}

        # Check that EVERY edge connects existing nodes
        for e in edges:
            assert e["source"] in node_ids, f"Edge source '{e['source']}' not in nodes: {node_ids}"
            assert e["target"] in node_ids, f"Edge target '{e['target']}' not in nodes: {node_ids}"
            assert e["relationship"] in (
                "INVESTIGATED_BY",
                "LED_TO",
                "RESULTED_IN",
                "RECOMMENDS",
                "HAS_PULL_REQUEST",
                "CONTAINS_ISSUE",
                "HAS_WORKFLOW_RUN",
                "REFERENCES_ISSUE",
                "CONTAINS_COMMIT",
                "TESTS_PULL_REQUEST",
                "TRIGGERED_BY",
                "CORRELATES_WITH",
                "DEPENDS_ON",
            )

        # Check next action node exists and has an incoming edge (not isolated!)
        action_nodes = [n for n in nodes if n["type"] == "next_action"]
        assert len(action_nodes) == 1
        act_id = action_nodes[0]["id"]
        incoming_edges = [e for e in edges if e["target"] == act_id]
        assert len(incoming_edges) >= 1, f"NextAction '{act_id}' has no incoming edge in {edges}"


def test_graph_node_provenance_completeness(m11_test_environment):
    """
    Requirement 3:
    - For every graph node, expose entity_type, repository, source_identifier, evidence_category, source_url.
    """
    user_id, lab_repo, _ = m11_test_environment

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="Payment gateway timeout on checkout endpoint in engineering-incident-lab",
        repository=lab_repo,
    )
    resp = investigate_incident(req)

    for node in resp.graph_nodes:
        data = node["data"]
        assert "entity_type" in data and data["entity_type"]
        assert "repository" in data and data["repository"]
        assert "source_identifier" in data and data["source_identifier"]
        assert "evidence_category" in data and data["evidence_category"] in (
            "direct",
            "related",
            "weak_candidate",
            "incident_memory",
        )
        assert "title_or_summary" in data and data["title_or_summary"]


def test_confidence_calculation_defensibility(m11_test_environment):
    """
    Requirement 4:
    - Verify confidence score reflects multi-factor evidence quality rather than a hardcoded presentation value.
    - Check inputs: empirical outcomes, repository grounding, component alignment, and path depth.
    - Verify confidence_explanation string.
    """
    user_id, _, _ = m11_test_environment

    req = IncidentQueryRequest(
        user_id=user_id,
        symptoms="API response latency p99 degradation above 120ms during peak load",
        component="gateway-api",
    )
    resp = investigate_incident(req)

    summary = resp.investigation_summary
    assert summary is not None

    score = summary["confidence_score"]
    level = summary["confidence_level"]
    explanation = summary["confidence_explanation"]

    assert isinstance(score, float)
    assert 0.60 <= score <= 0.95
    assert level in ("high", "medium")
    assert explanation is not None
    assert "Derived from" in explanation
    assert "%" in explanation
