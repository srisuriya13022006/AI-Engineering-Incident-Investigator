import uuid
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from app.main import app
from app.graph.connection import check_connection, FalkorDBConnectionError

client = TestClient(app)

is_db_available, _ = check_connection()
requires_falkordb = pytest.mark.skipif(
    not is_db_available,
    reason="Requires active FalkorDB instance (e.g. running via `docker compose up -d`).",
)


# ============================================================================
# 8. Multi-Hop Graph Retrieval & Relationship Awareness
# ============================================================================

@requires_falkordb
def test_multihop_graph_retrieval_relationship_awareness():
    """
    Key hackathon demonstration:
    Ingest: Goal -> MOTIVATED_BY -> Decision -> INFORMED_BY -> Experience.
    Query for the Goal's keyword ('throughput').
    Retrieval traverses the graph and returns the Decision and Experience,
    even though the Experience does not contain the word 'throughput'!
    """
    user_id = f"user_multihop_{uuid.uuid4().hex[:8]}"
    goal_id = f"goal_throughput_{uuid.uuid4().hex[:6]}"
    dec_id = f"dec_batching_{uuid.uuid4().hex[:6]}"
    exp_id = f"exp_run_{uuid.uuid4().hex[:6]}"

    ingest_payload = {
        "user_id": user_id,
        "structured_data": {
            "goals": [
                {
                    "id": goal_id,
                    "title": "Scale Agent Event Throughput to 10k QPS",
                    "priority": "high",
                }
            ],
            "decisions": [
                {
                    "id": dec_id,
                    "action": "Implement Redis pipeline batching for Cypher mutations",
                    "rationale": "Amortizes TCP round trips during high concurrency load",
                }
            ],
            "experiences": [
                {
                    "id": exp_id,
                    "content": "Pipelined 500 nodes in single transaction buffer",
                    "outcome": "Observed 12,400 QPS sustained write performance",
                    "confidence": 0.99,
                }
            ],
            "relationships": [
                {
                    "source_id": goal_id,
                    "relation": "MOTIVATED_BY",
                    "target_id": dec_id,
                },
                {
                    "source_id": dec_id,
                    "relation": "INFORMED_BY",
                    "target_id": exp_id,
                },
            ],
        },
    }

    ingest_res = client.post("/memory/ingest", json=ingest_payload)
    assert ingest_res.status_code == 200

    # Query using only keyword 'throughput'
    query_payload = {
        "user_id": user_id,
        "query": "What decisions were made regarding throughput?",
        "max_depth": 3,
    }

    query_res = client.post("/memory/query", json=query_payload)
    assert query_res.status_code == 200
    data = query_res.json()

    assert data["success"] is True
    assert data["found_evidence"] is True
    assert len(data["goals"]) >= 1
    assert data["goals"][0]["title"] == "Scale Agent Event Throughput to 10k QPS"

    # Crucial check: Decision and Experience were retrieved via graph hops!
    assert len(data["decisions"]) >= 1
    assert data["decisions"][0]["action"] == "Implement Redis pipeline batching for Cypher mutations"

    assert len(data["experiences"]) >= 1
    assert "12,400 QPS" in data["experiences"][0]["outcome"]

    # Verify relationship paths are populated
    assert len(data["evidence_paths"]) >= 1
    assert any("MOTIVATED_BY" in p for p in data["evidence_paths"])
    assert any("INFORMED_BY" in p for p in data["evidence_paths"])


# ============================================================================
# 9. No-Evidence Retrieval
# ============================================================================

@requires_falkordb
def test_no_evidence_retrieval():
    """Verify system returns explicit no-evidence status when no supporting facts exist."""
    user_id = f"user_empty_{uuid.uuid4().hex[:8]}"
    payload = {
        "user_id": user_id,
        "query": "Quantum teleportation flux capacitor physics",
    }
    response = client.post("/memory/query", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["found_evidence"] is False
    assert "No supporting evidence" in data["context_summary"]
    assert len(data["memories"]) == 0
    assert len(data["goals"]) == 0


# ============================================================================
# 10. Strict User Isolation
# ============================================================================

@requires_falkordb
def test_strict_user_data_isolation():
    """Verify User A's private memories cannot be retrieved by User B."""
    user_a = f"user_alice_{uuid.uuid4().hex[:8]}"
    user_b = f"user_bob_{uuid.uuid4().hex[:8]}"

    # 1. Ingest secret memory for User A
    user_a_payload = {
        "user_id": user_a,
        "structured_data": {
            "memories": [
                {
                    "text": "Alice secret password hash is argon2_xyz987654321.",
                    "summary": "Secret hash argon2_xyz987654321",
                    "memory_type": "fact",
                }
            ]
        },
    }
    res_a = client.post("/memory/ingest", json=user_a_payload)
    assert res_a.status_code == 200

    # 2. Ingest benign memory for User B
    user_b_payload = {
        "user_id": user_b,
        "structured_data": {
            "memories": [
                {
                    "text": "Bob likes reading graph theory research papers.",
                    "summary": "Likes reading graph theory papers",
                    "memory_type": "preference",
                }
            ]
        },
    }
    res_b = client.post("/memory/ingest", json=user_b_payload)
    assert res_b.status_code == 200

    # 3. User B searches for Alice's secret keyword 'argon2'
    query_b = {
        "user_id": user_b,
        "query": "argon2 password hash",
    }
    res_query_b = client.post("/memory/query", json=query_b)
    assert res_query_b.status_code == 200
    data_b = res_query_b.json()
    # User B must find ZERO evidence!
    assert data_b["found_evidence"] is False
    assert len(data_b["memories"]) == 0
    assert "argon2" not in data_b["context_summary"]
    assert "xyz987654321" not in data_b["context_summary"]

    # 4. User A searches for their own secret -> finds it!
    query_a = {
        "user_id": user_a,
        "query": "argon2 password hash",
    }
    res_query_a = client.post("/memory/query", json=query_a)
    assert res_query_a.status_code == 200
    data_a = res_query_a.json()
    assert data_a["found_evidence"] is True
    assert len(data_a["memories"]) >= 1
    assert "argon2_xyz987654321" in data_a["memories"][0]["summary"]


# ============================================================================
# 11. FalkorDB Unavailable Handling
# ============================================================================

def test_endpoints_handle_falkordb_offline():
    """Verify HTTP 503 is returned cleanly when FalkorDB is unreachable."""
    with patch("app.main.ingest_memory_payload", side_effect=FalkorDBConnectionError("Connection timeout")):
        payload = {
            "user_id": "user_test",
            "structured_data": {
                "memories": [{"text": "Hello", "summary": "Hello"}]
            },
        }
        res_ingest = client.post("/memory/ingest", json=payload)
        assert res_ingest.status_code == 503

    with patch("app.main.execute_memory_query", side_effect=FalkorDBConnectionError("Connection timeout")):
        res_query = client.post("/memory/query", json={"user_id": "user_test", "query": "hello"})
        assert res_query.status_code == 503
