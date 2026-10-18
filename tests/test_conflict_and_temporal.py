import uuid
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.graph.connection import check_connection
from app.graph.memory_ops import ingest_memory_payload
from app.memory.conflict import (
    evaluate_conflict_between_claims,
    parse_latency_threshold,
    parse_benchmark_value,
    extract_technology_name,
)
from app.memory.models import (
    StructuredIngestPayload,
    StructuredMemory,
    StructuredGoal,
    StructuredExperience,
    StructuredRelationship,
    QueryRequest,
)
from app.memory.retrieval import execute_memory_query

client = TestClient(app)
is_db_available, _ = check_connection()
requires_falkordb = pytest.mark.skipif(
    not is_db_available,
    reason="Requires active FalkorDB instance.",
)


# ============================================================================
# 1. Deterministic Unit Tests: Conflict Engine Classification
# ============================================================================

def test_conflict_engine_historical_claim_followed_by_update():
    """Verify that an explicit update/migration claim is classified as 'superseded' with deterministic certainty."""
    # Latency goal update
    res_lat = evaluate_conflict_between_claims(
        older_claim="Our assistant's target response latency is below 30 ms.",
        newer_claim="We changed the target response latency to below 20 ms.",
        older_type="goal",
        newer_type="goal",
    )
    assert res_lat is not None
    assert res_lat.conflict_type == "superseded"
    assert res_lat.classification_certainty == "deterministic"
    assert "updated from below 30 ms to below 20 ms" in res_lat.reason

    # Database migration update
    res_db = evaluate_conflict_between_claims(
        older_claim="The project uses PostgreSQL for all graph data.",
        newer_claim="We migrated the project to FalkorDB for sparse matrix traversals.",
    )
    assert res_db is not None
    assert res_db.conflict_type == "superseded"
    assert res_db.classification_certainty == "deterministic"
    assert "migrated" in res_db.reason.lower() or "transitioned" in res_db.reason.lower()


def test_conflict_engine_genuine_conflicting_claim():
    """Verify that mutually exclusive claims without migration context are classified as 'contradiction'."""
    res = evaluate_conflict_between_claims(
        older_claim="The primary database is PostgreSQL.",
        newer_claim="The primary database is FalkorDB.",
    )
    assert res is not None
    assert res.conflict_type == "contradiction"
    assert res.classification_certainty == "deterministic"
    assert "mutually exclusive" in res.reason.lower()


def test_conflict_engine_compatible_pair_of_claims():
    """Verify that claims covering different subjects or attributes are classified as compatible (or None)."""
    res = evaluate_conflict_between_claims(
        older_claim="We use Python 3.11 for our API services.",
        newer_claim="We use FalkorDB for our memory graph engine.",
    )
    assert res is None or res.conflict_type == "compatible"


def test_conflict_engine_ambiguous_case_remains_uncertain():
    """Verify that speculative or conditional claims are explicitly classified with certainty='uncertain'."""
    res = evaluate_conflict_between_claims(
        older_claim="We might consider Redis for temporary caching.",
        newer_claim="We might consider FalkorDB for graph memory.",
    )
    assert res is not None
    assert res.conflict_type == "uncertain"
    assert res.classification_certainty == "uncertain"
    assert "proposals or conditional phrasing" in res.reason.lower()


def test_conflict_engine_target_vs_benchmark_evaluation():
    """Verify comparison between latency target and measured benchmark."""
    # Benchmark fails target: 24ms > 20ms
    mismatch = evaluate_conflict_between_claims(
        older_claim="Target response latency below 20 ms",
        newer_claim="Last benchmark measured 24 ms",
        older_type="goal",
        newer_type="experience",
    )
    assert mismatch is not None
    assert mismatch.conflict_type == "target_mismatch"
    assert mismatch.classification_certainty == "deterministic"
    assert "does not meet" in mismatch.reason.lower()
    assert "single synthetic measurement" in mismatch.reason.lower()

    # Benchmark meets target: 16ms <= 20ms
    success = evaluate_conflict_between_claims(
        older_claim="Target response latency below 20 ms",
        newer_claim="Last benchmark measured 16 ms",
        older_type="goal",
        newer_type="experience",
    )
    assert success is not None
    assert success.conflict_type == "compatible"
    assert "satisfies" in success.reason.lower()


# ============================================================================
# 2. Integration Tests with Live FalkorDB: Evidence Preservation & Retrieval
# ============================================================================

@requires_falkordb
def test_evidence_preservation_and_superseding_retrieval():
    """
    Verify that ingesting a superseding claim:
    1. Preserves BOTH older and newer nodes in FalkorDB (nothing overwritten).
    2. Updates status of older node to 'superseded'.
    3. Links the nodes via [:SUPERSEDES].
    4. Retrieves both sides in QueryResponse with temporal metadata.
    """
    user_id = f"user_temporal_{uuid.uuid4().hex[:8]}"
    t1 = "2026-01-01T10:00:00Z"
    t2 = "2026-02-01T15:00:00Z"
    g_old_id = f"g_old_{user_id}"
    m_old_id = f"m_old_{user_id}"
    g_new_id = f"g_new_{user_id}"
    m_new_id = f"m_new_{user_id}"

    # Step 1: Ingest older goal
    p1 = StructuredIngestPayload(
        goals=[StructuredGoal(id=g_old_id, title="Target response latency below 30 ms", priority="high", recorded_at=t1)],
        memories=[StructuredMemory(id=m_old_id, text="Target response latency is below 30 ms.", summary="Target latency below 30 ms", recorded_at=t1)],
        relationships=[StructuredRelationship(source_id=m_old_id, relation="HAS_GOAL", target_id=g_old_id)],
    )
    r1 = ingest_memory_payload(user_id=user_id, payload=p1)
    assert r1.success is True
    assert r1.goals_created == 1

    # Step 2: Ingest newer superseding goal
    p2 = StructuredIngestPayload(
        goals=[StructuredGoal(id=g_new_id, title="We changed the target response latency to below 20 ms", priority="high", recorded_at=t2)],
        memories=[StructuredMemory(id=m_new_id, text="We changed the target response latency to below 20 ms.", summary="Target response latency changed to below 20 ms", recorded_at=t2)],
        relationships=[StructuredRelationship(source_id=m_new_id, relation="HAS_GOAL", target_id=g_new_id)],
    )
    r2 = ingest_memory_payload(user_id=user_id, payload=p2)
    assert r2.success is True

    # Step 3: Query retrieval
    q_req = QueryRequest(user_id=user_id, query="target response latency", max_depth=3)
    q_res = execute_memory_query(q_req)

    assert q_res.found_evidence is True
    # Both goals preserved!
    goal_ids = {g.id for g in q_res.goals}
    assert g_old_id in goal_ids
    assert g_new_id in goal_ids

    # Check status values
    old_g = next(g for g in q_res.goals if g.id == g_old_id)
    new_g = next(g for g in q_res.goals if g.id == g_new_id)
    assert old_g.status == "superseded"
    assert new_g.status == "current"

    # Check that SUPERSEDES path was discovered
    assert any("SUPERSEDES" in p for p in q_res.evidence_paths)
    assert q_res.has_conflicts is True
    assert any(c.conflict_type == "superseded" for c in q_res.conflicts)


@requires_falkordb
def test_contradiction_evidence_preservation():
    """Verify that contradictory statements are marked 'disputed' and both are preserved."""
    user_id = f"user_contra_test_{uuid.uuid4().hex[:8]}"

    p = StructuredIngestPayload(
        memories=[
            StructuredMemory(id="m_pg", text="The primary database is PostgreSQL.", summary="Primary database is PostgreSQL", recorded_at="2026-01-01T00:00:00Z"),
            StructuredMemory(id="m_falkor", text="The primary database is FalkorDB.", summary="Primary database is FalkorDB", recorded_at="2026-01-02T00:00:00Z"),
        ],
        relationships=[
            StructuredRelationship(source_id="m_falkor", relation="CONTRADICTS", target_id="m_pg")
        ],
    )
    r = ingest_memory_payload(user_id=user_id, payload=p)
    assert r.success is True

    q = QueryRequest(user_id=user_id, query="primary database PostgreSQL FalkorDB", max_depth=2)
    res = execute_memory_query(q)

    assert res.found_evidence is True
    assert res.has_conflicts is True
    m_ids = {m.id for m in res.memories}
    assert "m_pg" in m_ids and "m_falkor" in m_ids
    # Both sides preserved and conflict reported
    assert any("CONTRADICTS" in p for p in res.evidence_paths)


@requires_falkordb
def test_user_isolation_in_conflicts():
    """Verify that conflicts and updates in User A's graph never affect User B's graph."""
    user_a = f"user_a_iso_{uuid.uuid4().hex[:8]}"
    user_b = f"user_b_iso_{uuid.uuid4().hex[:8]}"

    # User A has 30ms goal
    p_a = StructuredIngestPayload(
        goals=[StructuredGoal(id="g_a", title="Target response latency below 30 ms")],
    )
    ingest_memory_payload(user_id=user_a, payload=p_a)

    # User B ingests 20ms goal
    p_b = StructuredIngestPayload(
        goals=[StructuredGoal(id="g_b", title="We changed the target response latency to below 20 ms")],
    )
    ingest_memory_payload(user_id=user_b, payload=p_b)

    # Query User A: User A's goal is NOT superseded because User B's update is isolated!
    q_a = execute_memory_query(QueryRequest(user_id=user_a, query="latency"))
    assert len(q_a.goals) == 1
    assert q_a.goals[0].id == "g_a"
    assert q_a.goals[0].status == "current"  # Still current for User A!

    # Query User B: Contains only User B's goal
    q_b = execute_memory_query(QueryRequest(user_id=user_b, query="latency"))
    assert len(q_b.goals) == 1
    assert q_b.goals[0].id == "g_b"


@requires_falkordb
def test_missing_timestamps_handled_gracefully():
    """Verify that when timestamps are omitted, the system handles it safely using defaults without error."""
    user_id = f"user_no_ts_{uuid.uuid4().hex[:8]}"

    p = StructuredIngestPayload(
        memories=[StructuredMemory(id="m_no_ts", text="Simple memory statement.", summary="Simple memory", created_at=None, recorded_at=None, valid_at=None)],
        goals=[StructuredGoal(id="g_no_ts", title="Goal without timestamp", created_at=None, recorded_at=None)],
    )
    res = ingest_memory_payload(user_id=user_id, payload=p)
    assert res.success is True

    q_res = execute_memory_query(QueryRequest(user_id=user_id, query="Simple memory"))
    assert q_res.found_evidence is True
    assert q_res.memories[0].id == "m_no_ts"
    assert q_res.memories[0].recorded_at is not None  # Automatically populated with UTC now


# ============================================================================
# 3. Milestone 6 Synthetic 3-Step Conflict Scenario Verification
# ============================================================================

@requires_falkordb
def test_milestone_6_synthetic_conflict_scenario_endpoint():
    """
    Verify the 3-step synthetic scenario via POST /demo/conflict_scenario:
    1. Target latency below 30 ms
    2. Changed target latency to below 20 ms
    3. Last benchmark measured 24 ms
    """
    test_user = f"user_test_scenario_{uuid.uuid4().hex[:8]}"
    resp = client.post(f"/demo/conflict_scenario?user_id={test_user}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["user_id"] == test_user
    assert len(data["steps"]) == 3
    assert data["preserved_evidence_count"] >= 3  # All 3 preserved!

    query_res = data["query_result"]
    assert query_res["found_evidence"] is True
    assert query_res["has_conflicts"] is True

    # Check conflicts list
    conflicts = query_res["conflicts"]
    assert len(conflicts) >= 2

    # Check superseding goal identified
    sup_conflict = next((c for c in conflicts if c["conflict_type"] == "superseded"), None)
    assert sup_conflict is not None
    assert "updated" in sup_conflict["reason"].lower()

    # Check target mismatch identified (24 ms > 20 ms)
    mismatch_conflict = next((c for c in conflicts if c["conflict_type"] == "target_mismatch"), None)
    assert mismatch_conflict is not None
    assert "does not meet" in mismatch_conflict["reason"].lower()
    assert "single synthetic measurement" in mismatch_conflict["reason"].lower()

    # Check disclaimer in response
    assert "Synthetic Scenario Data" in data["disclaimer"]
    assert "not an actual system benchmark" in data["disclaimer"].lower()
