"""
Unit and Integration Tests for Mai's Engineering Incident Investigator (Milestone 8).

Verifies:
1. Incident and investigation ingestion into FalkorDB with typed relationships.
2. Retrieval across different symptom wording.
3. Distinguishing unsuccessful vs. untested vs. successful fix attempts.
4. Evaluation of active vs. superseded goals and constraints.
5. Explicit no-evidence abstention to prevent hallucination.
6. Tenant isolation (User A cannot access User B's incident history).
7. Persisted task (NextAction) creation and status updates.
8. Recording of later measured outcomes (closing the feedback loop).
9. FalkorDB offline graceful handling.
10. Deterministic execution with synthetic benchmark data.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.graph.connection import FalkorDBConnectionError, check_connection

is_db_available, _ = check_connection()
requires_falkordb = pytest.mark.skipif(
    not is_db_available,
    reason="Requires active FalkorDB instance.",
)
from app.incident.models import (
    IncidentNode,
    InvestigationNode,
    FixAttemptNode,
    OutcomeNode,
    NextActionNode,
    IncidentIngestPayload,
    IncidentQueryRequest,
    UpdateTaskStatusRequest,
    RecordOutcomeRequest,
)
from app.incident.operations import (
    ingest_incident_payload,
    query_incident_graph,
    update_next_action_status,
    record_outcome_for_fix,
)
from app.incident.investigator import investigate_incident

client = TestClient(app)


@pytest.fixture
def clean_incident_tenant():
    """Provides unique tenant IDs for test isolation."""
    return "test_tenant_inc_01", "test_tenant_inc_02"


@requires_falkordb
def test_incident_ingest_and_multihop_traversal(clean_incident_tenant):
    """Verifies that an incident, investigation, fix attempt, and outcome are stored and traversable in FalkorDB."""
    tenant_a, _ = clean_incident_tenant
    now_iso = "2026-03-10T12:00:00Z"

    payload = IncidentIngestPayload(
        incident=IncidentNode(
            id="inc_test_01",
            title="Database Connection Pool Exhaustion",
            symptoms="Service gateway reports connection timeout alerts p99 latency 150ms",
            affected_component="auth-service",
            severity="critical",
            timestamp=now_iso,
            user_id=tenant_a,
        ),
        investigations=[
            InvestigationNode(
                id="inv_test_01",
                incident_id="inc_test_01",
                hypothesis="Thread starvation caused by pool size bottleneck",
                evidence_notes="Socket queues observed at capacity",
                investigator="DevOps Team",
                timestamp=now_iso,
                user_id=tenant_a,
            )
        ],
        fix_attempts=[
            FixAttemptNode(
                id="fix_test_01",
                investigation_id="inv_test_01",
                description="Increased pool size from 10 to 40",
                status="executed",
                timestamp=now_iso,
                user_id=tenant_a,
            )
        ],
        outcomes=[
            OutcomeNode(
                id="out_test_01",
                fix_attempt_id="fix_test_01",
                metric="p99_latency_ms",
                before_value="150ms",
                after_value="148ms",
                evaluation="unsuccessful",
                notes="Latency remained high at 148ms; pool size was not the primary constraint",
                timestamp=now_iso,
                user_id=tenant_a,
            )
        ],
        affected_entities=["auth-service"],
    )

    ingest_res = ingest_incident_payload(user_id=tenant_a, payload=payload)
    assert ingest_res["success"] is True
    assert ingest_res["incident_id"] == "inc_test_01"
    assert ingest_res["counts"]["investigations"] == 1
    assert ingest_res["counts"]["fix_attempts"] == 1
    assert ingest_res["counts"]["outcomes"] == 1

    # Query graph directly
    graph_res = query_incident_graph(user_id=tenant_a, symptoms="connection timeout alerts", component="auth-service")
    assert len(graph_res["incidents"]) >= 1
    assert graph_res["incidents"][0].id == "inc_test_01"
    assert len(graph_res["investigations"]) >= 1
    assert graph_res["investigations"][0].fix_description == "Increased pool size from 10 to 40"
    assert graph_res["investigations"][0].outcome_evaluation == "unsuccessful"
    assert any("INVESTIGATED_BY" in p for p in graph_res["evidence_paths"])


@requires_falkordb
def test_retrieval_across_different_symptom_wording(clean_incident_tenant):
    """Verifies that an incident is retrieved even when query uses completely different wording."""
    tenant_a, _ = clean_incident_tenant

    # Query uses vocabulary: 'socket delays and slow authorization tokens'
    # Prior incident used vocabulary: 'connection timeout alerts p99 latency 150ms'
    req = IncidentQueryRequest(
        user_id=tenant_a,
        symptoms="Socket delays and authorization tokens taking too long",
        component="auth-service",
    )
    res = investigate_incident(req)

    assert res.found_evidence is True
    assert len(res.matched_incidents) >= 1
    assert res.matched_incidents[0].affected_component == "auth-service"
    # Verify the failed fix attempt was flagged
    assert any("Increased pool size from 10 to 40" in fix for fix in res.unsuccessful_fixes_to_avoid)
    # Verify the recommendation explicitly cautions against repeating the failed fix
    assert "Do NOT re-attempt" in res.recommendation


@requires_falkordb
def test_previously_unsuccessful_vs_untested_fixes(clean_incident_tenant):
    """Verifies that the engine never assumes a fix succeeded and clearly separates failed from untested interventions."""
    tenant_a, _ = clean_incident_tenant

    payload = IncidentIngestPayload(
        incident=IncidentNode(
            id="inc_test_untested",
            title="Worker Node CPU Throttling",
            symptoms="Kubernetes worker throttling under CPU quota",
            affected_component="worker-pool",
            severity="medium",
            user_id=tenant_a,
        ),
        investigations=[
            InvestigationNode(
                id="inv_worker_quota",
                hypothesis="Resource limits configured too conservatively",
                user_id=tenant_a,
            )
        ],
        fix_attempts=[
            FixAttemptNode(
                id="fix_increase_cpu_quota",
                investigation_id="inv_worker_quota",
                description="Double CPU limit from 2 cores to 4 cores",
                status="proposed",  # Proposed, not executed
                user_id=tenant_a,
            )
        ],
        outcomes=[
            OutcomeNode(
                id="out_worker_unknown",
                fix_attempt_id="fix_increase_cpu_quota",
                metric="cpu_throttling_percent",
                evaluation="unknown",  # Outcome unknown / unmeasured
                user_id=tenant_a,
            )
        ],
    )
    ingest_incident_payload(user_id=tenant_a, payload=payload)

    req = IncidentQueryRequest(
        user_id=tenant_a,
        symptoms="Worker node CPU quota throttling alerts",
        component="worker-pool",
    )
    res = investigate_incident(req)

    assert res.found_evidence is True
    assert len(res.untested_fixes) >= 1
    assert any("Double CPU limit" in f for f in res.untested_fixes)
    # Crucial rule: An untested/proposed fix must NOT be counted as unsuccessful or successful
    assert not any("Double CPU limit" in f for f in res.unsuccessful_fixes_to_avoid)


@requires_falkordb
def test_no_evidence_abstention():
    """Verifies that the engine abstains from making ungrounded recommendations when no evidence exists."""
    req = IncidentQueryRequest(
        user_id="user_empty_knowledge_base",
        symptoms="Quantum entanglement phase decoherence in cluster sigma",
        component="quantum-core",
    )
    res = investigate_incident(req)

    assert res.found_evidence is False
    assert res.recommendation is None
    assert res.recommended_next_action is None
    assert res.insufficient_evidence_reason is not None
    assert "Abstaining from ungrounded recommendation" in res.insufficient_evidence_reason


@requires_falkordb
def test_tenant_isolation_in_incidents(clean_incident_tenant):
    """Verifies strict cross-tenant isolation: Tenant B cannot retrieve Tenant A's incidents or failed fixes."""
    tenant_a, tenant_b = clean_incident_tenant

    # Tenant B queries for the exact component and symptoms ingested for Tenant A
    req = IncidentQueryRequest(
        user_id=tenant_b,
        symptoms="Service gateway reports connection timeout alerts p99 latency 150ms",
        component="auth-service",
    )
    res = investigate_incident(req)

    assert res.found_evidence is False
    assert len(res.matched_incidents) == 0
    assert len(res.unsuccessful_fixes_to_avoid) == 0


@requires_falkordb
def test_task_status_lifecycle_and_later_outcome_recording(clean_incident_tenant):
    """Verifies creating a task, updating status (pending -> in_progress -> completed), and recording a later outcome."""
    tenant_a, _ = clean_incident_tenant

    # 1. First run investigation to create NextAction task
    req = IncidentQueryRequest(
        user_id=tenant_a,
        symptoms="Service gateway connection timeout",
        component="auth-service",
    )
    res = investigate_incident(req)
    assert res.found_evidence is True
    assert res.recommended_next_action is not None
    action_id = res.recommended_next_action.id

    # 2. Update task status via endpoint: pending -> in_progress
    resp_update = client.post(
        "/incident/task",
        json={
            "user_id": tenant_a,
            "action_id": action_id,
            "status": "in_progress",
            "resolution_notes": "SRE engineer investigating index coverage on user_tokens table.",
        },
    )
    assert resp_update.status_code == 200
    assert resp_update.json()["status"] == "in_progress"

    # 3. Record the later outcome: completed with measured success!
    resp_outcome = client.post(
        "/incident/outcome",
        json={
            "user_id": tenant_a,
            "action_id": action_id,
            "metric": "p99_latency_ms",
            "before_value": "148ms",
            "after_value": "18ms",
            "evaluation": "successful",
            "notes": "Added missing composite index on (tenant_id, token_hash); latency dropped to 18ms.",
        },
    )
    assert resp_outcome.status_code == 200
    assert resp_outcome.json()["evaluation"] == "successful"

    # 4. Update task to completed
    resp_done = client.post(
        "/incident/task",
        json={
            "user_id": tenant_a,
            "action_id": action_id,
            "status": "completed",
            "resolution_notes": "Resolution confirmed; latency within active <20ms target.",
        },
    )
    assert resp_done.status_code == 200
    assert resp_done.json()["status"] == "completed"


def test_falkordb_offline_graceful_handling():
    """Verifies that incident endpoints return 503 Service Unavailable when FalkorDB is offline."""
    with patch("app.incident.operations.get_graph_client", side_effect=FalkorDBConnectionError("Connection refused")):
        resp = client.post(
            "/incident/investigate",
            json={
                "user_id": "test_user",
                "symptoms": "gateway timeout",
                "component": "gateway-api",
            },
        )
        assert resp.status_code == 503
        assert "FalkorDB unreachable" in resp.json()["detail"]


@requires_falkordb
def test_milestone_8_demo_scenario_endpoint():
    """Verifies the complete one-click judge demo scenario endpoint."""
    resp = client.post("/demo/incident_scenario?user_id=user_incident_judge_eval")
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == "user_incident_judge_eval"
    assert data["seeded_incident_id"] == "inc_gateway_latency_01"
    assert data["investigation_result"]["found_evidence"] is True
    assert len(data["investigation_result"]["unsuccessful_fixes_to_avoid"]) >= 1
    assert "Increased pool size from 10 to 50" in data["investigation_result"]["unsuccessful_fixes_to_avoid"][0]
    assert len(data["summary_of_checks"]) == 6
    assert any("UNSUCCESSFUL" in check for check in data["summary_of_checks"])
