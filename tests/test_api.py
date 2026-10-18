import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from app.main import app
from app.graph.connection import FalkorDBConnectionError
from app.graph.schema import EvidencePathResponse, SeedResponse, EvidenceChain, NodeData, RelationshipData

client = TestClient(app)


def test_health_endpoint_contract():
    """Verify GET /health returns HTTP 200 with required structure regardless of DB state."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "database" in data
    assert "graph_name" in data
    assert data["status"] in ("healthy", "degraded")


def test_api_handles_falkordb_offline_gracefully():
    """Verify that when FalkorDB is offline, endpoints return 503 instead of crashing."""
    with patch("app.main.seed_demo_memory", side_effect=FalkorDBConnectionError("Connection refused")):
        seed_resp = client.post("/demo/seed")
        assert seed_resp.status_code == 503
        error_payload = seed_resp.json()
        assert "detail" in error_payload or "message" in error_payload

    with patch("app.main.query_evidence_path", side_effect=FalkorDBConnectionError("Connection refused")):
        path_resp = client.get("/demo/path")
        assert path_resp.status_code == 503


def test_seed_and_query_api_with_mock():
    """Verify endpoint serialization and contract using mocked graph operations."""
    mock_seed = SeedResponse(
        success=True,
        message="Seeded successfully",
        user_id="user_test",
        goal_id="goal_test",
        decision_id="dec_test",
        experience_id="exp_test",
        nodes_created=4,
        relationships_created=3,
        properties_set=14,
    )

    mock_chain = EvidenceChain(
        user_id="user_test",
        user_name="Alice",
        goal_id="goal_test",
        goal_title="Test Goal",
        goal_priority="high",
        decision_id="dec_test",
        decision_action="Test Action",
        decision_rationale="Test Rationale",
        experience_id="exp_test",
        experience_content="Test Content",
        experience_outcome="Test Outcome",
        experience_confidence=0.98,
        path_summary="User(Alice) -> Goal -> Decision -> Experience",
    )

    mock_path = EvidencePathResponse(
        success=True,
        count=1,
        chains=[mock_chain],
        nodes=[
            NodeData(label="User", id="user_test", properties={"name": "Alice"}),
            NodeData(label="Goal", id="goal_test", properties={"title": "Test Goal"}),
            NodeData(label="Decision", id="dec_test", properties={"action": "Test Action"}),
            NodeData(label="Experience", id="exp_test", properties={"content": "Test Content"}),
        ],
        relationships=[
            RelationshipData(source_id="user_test", source_label="User", relation="HAS_GOAL", target_id="goal_test", target_label="Goal"),
            RelationshipData(source_id="goal_test", source_label="Goal", relation="MOTIVATED_BY", target_id="dec_test", target_label="Decision"),
            RelationshipData(source_id="dec_test", source_label="Decision", relation="INFORMED_BY", target_id="exp_test", target_label="Experience"),
        ],
    )

    with patch("app.main.seed_demo_memory", return_value=mock_seed):
        seed_resp = client.post("/demo/seed")
        assert seed_resp.status_code == 200
        assert seed_resp.json()["user_id"] == "user_test"

    with patch("app.main.query_evidence_path", return_value=mock_path):
        path_resp = client.get("/demo/path?goal_id=goal_test")
        assert path_resp.status_code == 200
        body = path_resp.json()
        assert body["count"] == 1
        assert len(body["nodes"]) == 4
        assert len(body["relationships"]) == 3
        assert body["chains"][0]["goal_id"] == "goal_test"
