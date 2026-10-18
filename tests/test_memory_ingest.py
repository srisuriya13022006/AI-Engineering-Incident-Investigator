import uuid
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.main import app
from app.graph.connection import check_connection, FalkorDBConnectionError
from app.memory.models import (
    StructuredIngestPayload,
    StructuredMemory,
    StructuredEntity,
    StructuredGoal,
    StructuredDecision,
    StructuredExperience,
    StructuredRelationship,
)
from app.memory.extraction import (
    MockExtractionProvider,
    OpenAIExtractionProvider,
    ProviderNotConfiguredError,
    InvalidExtractionOutputError,
)

client = TestClient(app)

is_db_available, _ = check_connection()
requires_falkordb = pytest.mark.skipif(
    not is_db_available,
    reason="Requires active FalkorDB instance (e.g. running via `docker compose up -d`).",
)


# ============================================================================
# 1. Valid Structured Ingestion (Mode A)
# ============================================================================

@requires_falkordb
def test_valid_structured_ingestion():
    """Verify Mode A structured memory ingestion without an LLM API key."""
    user_id = f"user_alice_{uuid.uuid4().hex[:8]}"
    payload = {
        "user_id": user_id,
        "user_name": "Alice",
        "structured_data": {
            "memories": [
                {
                    "text": "Alice prefers writing Python over JavaScript for backend APIs.",
                    "summary": "Prefers Python for backend APIs",
                    "memory_type": "preference",
                    "confidence": 0.95,
                    "provenance": "test:user_input",
                }
            ],
            "entities": [
                {
                    "name": "Python",
                    "entity_type": "technology",
                    "description": "High-level programming language",
                }
            ],
            "relationships": [
                {
                    "source_id": "mem_alice_01",
                    "relation": "MENTIONS",
                    "target_id": "ent_python",
                }
            ],
        },
    }

    response = client.post("/memory/ingest", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["user_id"] == user_id
    assert data["extraction_mode"] == "structured"
    assert data["memories_stored"] >= 1
    assert data["entities_processed"] >= 1


# ============================================================================
# 2. Invalid Structured Input Validation
# ============================================================================

def test_invalid_structured_input_empty_payload():
    """Verify malformed/empty payload is rejected with 422 Unprocessable Entity."""
    # Missing both conversation_text and structured_data
    response = client.post("/memory/ingest", json={"user_id": "user_test"})
    assert response.status_code == 422

    # Empty user_id
    response2 = client.post("/memory/ingest", json={"user_id": "   ", "conversation_text": "hello"})
    assert response2.status_code == 422

    # Disallowed relationship type
    invalid_rel_payload = {
        "user_id": "user_test",
        "structured_data": {
            "memories": [{"text": "Sample text", "summary": "Sample summary"}],
            "relationships": [
                {"source_id": "a", "relation": "MALICIOUS_DROP_DATABASE", "target_id": "b"}
            ],
        },
    }
    response3 = client.post("/memory/ingest", json=invalid_rel_payload)
    assert response3.status_code == 422
    assert "not permitted" in response3.text


# ============================================================================
# 3. Conversation Extraction using Mocked Provider (Mode B)
# ============================================================================

@requires_falkordb
def test_conversation_extraction_with_mock_provider():
    """Verify Mode B conversation ingestion using MockExtractionProvider."""
    user_id = f"user_bob_{uuid.uuid4().hex[:8]}"
    with patch("app.main.get_extraction_provider", return_value=MockExtractionProvider()):
        payload = {
            "user_id": user_id,
            "conversation_text": "We need to optimize graph traversal latency using FalkorDB.",
        }
        response = client.post("/memory/ingest", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["extraction_mode"] == "conversation"
        assert data["memories_stored"] >= 1
        assert data["entities_processed"] >= 1
        assert data["goals_processed"] >= 1


# ============================================================================
# 4. Missing Provider Configuration
# ============================================================================

def test_missing_provider_configuration():
    """Verify that requesting Mode B without configured provider returns clear 400 error."""
    unconfigured_provider = OpenAIExtractionProvider(api_key=None)
    with patch("app.main.get_extraction_provider", return_value=unconfigured_provider):
        payload = {
            "user_id": "user_test",
            "conversation_text": "Extract this without API key",
        }
        response = client.post("/memory/ingest", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert "ProviderNotConfigured" in str(body)


# ============================================================================
# 5. Malformed LLM JSON Handling
# ============================================================================

def test_malformed_llm_json_handling():
    """Verify graceful handling when LLM returns unparseable JSON."""
    mock_provider = MagicMock()
    mock_provider.extract.side_effect = InvalidExtractionOutputError("LLM returned non-JSON text: 'Sorry, I cannot do that'")
    with patch("app.main.get_extraction_provider", return_value=mock_provider):
        payload = {
            "user_id": "user_test",
            "conversation_text": "Some text",
        }
        response = client.post("/memory/ingest", json=payload)
        assert response.status_code == 422
        body = response.json()
        assert "InvalidExtractionOutput" in str(body)


# ============================================================================
# 6. Duplicate Ingestion & Idempotency
# ============================================================================

@requires_falkordb
def test_duplicate_ingestion_and_idempotency():
    """Verify that ingesting identical memories deduplicates via fingerprint without adding duplicate nodes."""
    user_id = f"user_idempotent_{uuid.uuid4().hex[:8]}"
    payload = {
        "user_id": user_id,
        "structured_data": {
            "memories": [
                {
                    "text": "User is building an AI assistant called Mai.",
                    "summary": "Building AI assistant Mai",
                    "memory_type": "fact",
                    "confidence": 1.0,
                }
            ]
        },
    }

    # First ingestion -> stored
    res1 = client.post("/memory/ingest", json=payload)
    assert res1.status_code == 200
    assert res1.json()["memories_stored"] == 1
    assert res1.json()["memories_deduplicated"] == 0

    # Second identical ingestion -> deduplicated
    res2 = client.post("/memory/ingest", json=payload)
    assert res2.status_code == 200
    assert res2.json()["memories_stored"] == 0
    assert res2.json()["memories_deduplicated"] == 1


# ============================================================================
# 7. Contradictory Facts Preservation
# ============================================================================

@requires_falkordb
def test_contradictory_facts_preserved_separately():
    """Verify contradictory facts are preserved as separate timestamped evidence with CONTRADICTS relation."""
    user_id = f"user_contra_{uuid.uuid4().hex[:8]}"
    payload = {
        "user_id": user_id,
        "structured_data": {
            "memories": [
                {
                    "id": f"mem_v1_{uuid.uuid4().hex[:6]}",
                    "text": "User strictly prefers light mode for code editors.",
                    "summary": "Prefers light mode",
                    "memory_type": "preference",
                    "created_at": "2026-01-01T10:00:00Z",
                },
                {
                    "id": f"mem_v2_{uuid.uuid4().hex[:6]}",
                    "text": "User updated preference to dark mode for all IDEs.",
                    "summary": "Switched preference to dark mode",
                    "memory_type": "preference",
                    "created_at": "2026-06-01T10:00:00Z",
                },
            ],
            "relationships": [
                {
                    "source_id": "mem_v2_theme",
                    "relation": "CONTRADICTS",
                    "target_id": "mem_v1_theme",
                }
            ],
        },
    }

    res = client.post("/memory/ingest", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["memories_stored"] == 2  # Both separate memories preserved!

    # Query theme to verify both pieces of evidence appear with contradiction context
    query_resp = client.post(
        "/memory/query",
        json={"user_id": user_id, "query": "mode preference"},
    )
    assert query_resp.status_code == 200
    q_data = query_resp.json()
    assert q_data["found_evidence"] is True
    assert len(q_data["memories"]) >= 2
    summaries = {m["summary"] for m in q_data["memories"]}
    assert "Prefers light mode" in summaries
    assert "Switched preference to dark mode" in summaries
