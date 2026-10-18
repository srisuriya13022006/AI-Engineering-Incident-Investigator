import uuid
import pytest
from app.graph.connection import check_connection
from app.graph.memory_ops import ingest_memory_payload
from app.memory.models import (
    StructuredIngestPayload,
    StructuredMemory,
    StructuredEntity,
    StructuredGoal,
    StructuredDecision,
    StructuredExperience,
    StructuredRelationship,
    QueryRequest,
)
from app.memory.retrieval import execute_memory_query
from app.memory.baseline import keyword_baseline_search, evaluate_graph_vs_baseline

is_db_available, _ = check_connection()
requires_falkordb = pytest.mark.skipif(
    not is_db_available,
    reason="Requires active FalkorDB instance (e.g. running via `docker compose up -d`).",
)


@requires_falkordb
def test_evaluation_graph_vs_keyword_baseline():
    """
    Automated evaluation verifying that Graph Traversal outperforms flat keyword
    matching on multi-hop causal reasoning queries.
    """
    user_id = f"user_eval_{uuid.uuid4().hex[:8]}"
    goal_id = f"goal_scale_{uuid.uuid4().hex[:6]}"
    dec_id = f"dec_opt_{uuid.uuid4().hex[:6]}"
    exp_id = f"exp_perf_{uuid.uuid4().hex[:6]}"

    payload = StructuredIngestPayload(
        goals=[
            StructuredGoal(
                id=goal_id,
                title="Scale memory graph traversal to 50k nodes",
                priority="high",
            )
        ],
        decisions=[
            StructuredDecision(
                id=dec_id,
                action="Deploy FalkorDB memory pipeline with GraphBLAS indices",
                rationale="Leverages sparse matrix algebra for constant-time hop lookups",
            )
        ],
        experiences=[
            StructuredExperience(
                id=exp_id,
                content="Sparse linear algebra benchmark on 50,000 synthetic episodic nodes",
                outcome="Achieved 12ms 3-hop traversal latency across cluster",
                confidence=0.99,
            )
        ],
        relationships=[
            StructuredRelationship(source_id=goal_id, relation="MOTIVATES", target_id=dec_id),
            StructuredRelationship(source_id=dec_id, relation="INFORMED_BY", target_id=exp_id),
        ],
    )

    ingest_res = ingest_memory_payload(user_id=user_id, payload=payload)
    assert ingest_res.success is True

    # Query uses goal vocabulary ('scale', 'nodes'), NOT experience vocabulary ('sparse', 'algebra')
    query = "What decisions were taken to scale the memory graph nodes?"
    expected_ids = {goal_id, dec_id, exp_id}

    comparison = evaluate_graph_vs_baseline(
        user_id=user_id,
        query=query,
        expected_relevant_ids=expected_ids,
    )

    # 1. Graph retrieval must find all 3 connected records (Recall = 100%)
    assert comparison.graph_recall == 1.0
    assert comparison.graph_found_decision is True
    assert comparison.graph_found_experience is True
    assert comparison.graph_has_evidence_path is True

    # 2. Keyword baseline fails to find the experience because it doesn't contain 'scale' or 'graph'
    assert comparison.graph_recall > comparison.baseline_recall
    assert comparison.baseline_found_experience is False
    assert comparison.baseline_has_evidence_path is False


@requires_falkordb
def test_evidence_paths_and_provenance_integrity():
    """Verify evidence paths and provenance directly map to existing graph entities."""
    user_id = f"user_prov_{uuid.uuid4().hex[:8]}"
    mem_id = f"mem_prov_{uuid.uuid4().hex[:6]}"
    ent_id = f"ent_prov_{uuid.uuid4().hex[:6]}"

    payload = StructuredIngestPayload(
        memories=[
            StructuredMemory(
                id=mem_id,
                text="Documenting provenance audit test.",
                summary="Provenance audit test",
                provenance="audit_log:2026",
            )
        ],
        entities=[
            StructuredEntity(
                id=ent_id,
                name="AuditModule",
                entity_type="component",
            )
        ],
        relationships=[
            StructuredRelationship(source_id=mem_id, relation="MENTIONS", target_id=ent_id)
        ],
    )

    ingest_memory_payload(user_id=user_id, payload=payload)

    req = QueryRequest(user_id=user_id, query="Provenance audit test", max_depth=2)
    resp = execute_memory_query(req)

    assert resp.found_evidence is True
    assert any("audit_log:2026" in p for p in resp.provenance)
    assert any("MENTIONS" in path for path in resp.evidence_paths)


def test_ui_and_static_serving():
    """Verify that the web interface index.html and static CSS/JS assets are served correctly."""
    from fastapi.testclient import TestClient
    from app.main import app

    tc = TestClient(app)
    # Check root UI
    resp_root = tc.get("/")
    assert resp_root.status_code == 200
    assert "Mai Graph Hacks" in resp_root.text
    assert "text/html" in resp_root.headers.get("content-type", "")

    # Check /ui alias
    resp_ui = tc.get("/ui")
    assert resp_ui.status_code == 200

    # Check static CSS
    resp_css = tc.get("/static/style.css")
    assert resp_css.status_code == 200
    assert "text/css" in resp_css.headers.get("content-type", "")

    # Check static JS
    resp_js = tc.get("/static/app.js")
    assert resp_js.status_code == 200
    assert "javascript" in resp_js.headers.get("content-type", "")


@requires_falkordb
def test_compare_endpoint_live():
    """Verify POST /memory/compare returns valid evaluation metrics comparing graph to baseline."""
    from fastapi.testclient import TestClient
    from app.main import app

    tc = TestClient(app)
    user_id = f"user_cmp_{uuid.uuid4().hex[:8]}"

    # Ingest a scenario
    payload = StructuredIngestPayload(
        goals=[StructuredGoal(id="g1", title="Latency target 20ms", priority="high")],
        decisions=[StructuredDecision(id="d1", action="Adopt FalkorDB", rationale="Matrix traversal")],
        experiences=[StructuredExperience(id="e1", content="Benchmark done", outcome="Success", confidence=1.0)],
        relationships=[
            StructuredRelationship(source_id="g1", relation="MOTIVATES", target_id="d1"),
            StructuredRelationship(source_id="d1", relation="INFORMED_BY", target_id="e1"),
        ],
    )
    ingest_memory_payload(user_id=user_id, payload=payload)

    compare_req = {
        "user_id": user_id,
        "query": "Which decision supported latency target 20ms and what experience informed it?",
        "expected_ids": ["g1", "d1", "e1"],
    }
    resp = tc.post("/memory/compare", json=compare_req)
    assert resp.status_code == 200
    data = resp.json()
    assert data["graph_recall"] == 1.0
    assert data["graph_found_decision"] is True
    assert data["graph_found_experience"] is True
    assert data["graph_has_evidence_path"] is True
    assert "summary" in data


@requires_falkordb
def test_ingest_response_accurate_breakdown_counts():
    """Verify that IngestResponse accurately distinguishes new vs reused elements on repeated ingestion."""
    user_id = f"user_cnt_{uuid.uuid4().hex[:8]}"
    goal_id = f"g_cnt_{uuid.uuid4().hex[:6]}"
    dec_id = f"d_cnt_{uuid.uuid4().hex[:6]}"
    exp_id = f"e_cnt_{uuid.uuid4().hex[:6]}"

    payload = StructuredIngestPayload(
        memories=[
            StructuredMemory(
                text="Auditing breakdown counts test.",
                summary=f"Breakdown counts test {user_id}",
                memory_type="observation",
            )
        ],
        goals=[StructuredGoal(id=goal_id, title="Test Goal Count", priority="medium")],
        decisions=[StructuredDecision(id=dec_id, action="Count Action", rationale="Count Rationale")],
        experiences=[StructuredExperience(id=exp_id, content="Count Exp", outcome="Exp Outcome", confidence=0.9)],
        relationships=[
            StructuredRelationship(source_id=goal_id, relation="MOTIVATES", target_id=dec_id),
            StructuredRelationship(source_id=dec_id, relation="INFORMED_BY", target_id=exp_id),
        ],
    )

    # First ingestion: elements must be reported as created
    res1 = ingest_memory_payload(user_id=user_id, payload=payload)
    assert res1.memories_stored == 1
    assert res1.memories_deduplicated == 0
    assert res1.goals_created == 1
    assert res1.goals_reused == 0
    assert res1.decisions_created == 1
    assert res1.decisions_reused == 0
    assert res1.experiences_created == 1
    assert res1.experiences_reused == 0
    assert res1.relationships_created >= 2
    assert res1.relationships_reused == 0

    # Second ingestion: elements must be reported as deduplicated / reused, 0 new created
    res2 = ingest_memory_payload(user_id=user_id, payload=payload)
    assert res2.memories_stored == 0
    assert res2.memories_deduplicated == 1
    assert res2.goals_created == 0
    assert res2.goals_reused == 1
    assert res2.decisions_created == 0
    assert res2.decisions_reused == 1
    assert res2.experiences_created == 0
    assert res2.experiences_reused == 1
    assert res2.relationships_created == 0
    assert res2.relationships_reused >= 2


@requires_falkordb
def test_edge_direction_and_type_verification_against_falkordb():
    """Verify that returned evidence paths preserve actual stored relationship types and directions, without phantom paths."""
    user_id = f"user_edge_{uuid.uuid4().hex[:8]}"
    goal_id = f"g_edge_{uuid.uuid4().hex[:6]}"
    dec_id = f"d_edge_{uuid.uuid4().hex[:6]}"
    exp_id = f"e_edge_{uuid.uuid4().hex[:6]}"
    disc_goal_id = f"g_disc_{uuid.uuid4().hex[:6]}"

    payload = StructuredIngestPayload(
        goals=[
            StructuredGoal(id=goal_id, title="Verified Path Latency Goal", priority="high"),
            StructuredGoal(id=disc_goal_id, title="Quantum Telemetry Standalone Target", priority="low"),
        ],
        decisions=[
            StructuredDecision(id=dec_id, action="Specific Verified Decision", rationale="Direct verification")
        ],
        experiences=[
            StructuredExperience(id=exp_id, content="Empirical Verified Experience", outcome="Observed 100% path accuracy", confidence=1.0)
        ],
        relationships=[
            StructuredRelationship(source_id=goal_id, relation="MOTIVATES", target_id=dec_id),
            StructuredRelationship(source_id=dec_id, relation="INFORMED_BY", target_id=exp_id),
        ],
    )
    ingest_memory_payload(user_id=user_id, payload=payload)

    # 1. Query for the connected goal:
    req_conn = QueryRequest(user_id=user_id, query="Verified Path Latency Goal", max_depth=3)
    resp_conn = execute_memory_query(req_conn)
    assert resp_conn.found_evidence is True
    # The actual relationship stored in FalkorDB was MOTIVATES (not MOTIVATED_BY)
    assert any("-[MOTIVATES]->" in path for path in resp_conn.evidence_paths)
    assert any("-[INFORMED_BY]->" in path for path in resp_conn.evidence_paths)

    # 2. Query for the disconnected goal:
    req_disc = QueryRequest(user_id=user_id, query="Quantum Telemetry Standalone", max_depth=3)
    resp_disc = execute_memory_query(req_disc)
    assert resp_disc.found_evidence is True
    assert len(resp_disc.goals) == 1
    assert resp_disc.goals[0].id == disc_goal_id
    assert len(resp_disc.decisions) == 0
    assert len(resp_disc.experiences) == 0
    # Confirm no phantom MOTIVATES or INFORMED_BY edges were fabricated for the disconnected goal
    assert len(resp_disc.evidence_paths) == 1
    assert "MOTIVATES" not in resp_disc.evidence_paths[0]
    assert "INFORMED_BY" not in resp_disc.evidence_paths[0]
    assert f"Goal('Quantum Telemetry Standalone Target')" in resp_disc.evidence_paths[0]
