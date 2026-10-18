import json
import uuid
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.graph.connection import check_connection
from app.memory.extraction import (
    GeminiExtractionProvider,
    ProviderNotConfiguredError,
    InvalidExtractionOutputError,
    ExtractionError,
    get_extraction_provider,
)
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

client = TestClient(app)
is_db_available, _ = check_connection()
requires_falkordb = pytest.mark.skipif(
    not is_db_available,
    reason="Requires active FalkorDB instance.",
)


# ============================================================================
# 1. Missing API Key Handling
# ============================================================================

def test_gemini_missing_api_key_raises_error():
    """Verify missing or placeholder GEMINI_API_KEY raises ProviderNotConfiguredError."""
    with patch.object(settings, "gemini_api_key", ""):
        # Test with None when env is empty
        provider_none = GeminiExtractionProvider(api_key=None)
        with pytest.raises(ProviderNotConfiguredError) as exc_info:
            provider_none.extract("Some conversation", user_id="test_user")
        assert "Gemini API key is not configured" in str(exc_info.value)

        # Test with empty string explicitly
        provider_empty = GeminiExtractionProvider(api_key="")
        with pytest.raises(ProviderNotConfiguredError):
            provider_empty.extract("Some conversation", user_id="test_user")

        # Test with placeholder
        provider_ph = GeminiExtractionProvider(api_key="your_actual_key_here")
        with pytest.raises(ProviderNotConfiguredError):
            provider_ph.extract("Some conversation", user_id="test_user")

        # Test API endpoint response
        with patch("app.main.get_extraction_provider", return_value=provider_none):
            resp = client.post("/memory/ingest", json={"user_id": "u1", "conversation_text": "hello"})
            assert resp.status_code == 400
            data = resp.json()
            assert "ProviderNotConfigured" in str(data)
            assert "GEMINI_API_KEY" in str(data)


# ============================================================================
# 2. Empty or Whitespace Conversation
# ============================================================================

def test_gemini_empty_conversation_rejected():
    """Verify empty or whitespace-only conversation is rejected with InvalidExtractionOutputError."""
    provider = GeminiExtractionProvider(api_key="test_dummy_key_12345")
    with pytest.raises(InvalidExtractionOutputError) as exc_info:
        provider.extract("", user_id="test_user")
    assert "empty conversation text" in str(exc_info.value).lower()

    with pytest.raises(InvalidExtractionOutputError):
        provider.extract("   \n\t  ", user_id="test_user")


# ============================================================================
# 3. Valid Structured Extraction with Mocked Client
# ============================================================================

def test_gemini_valid_structured_extraction():
    """Verify that a valid Gemini structured response correctly parses into StructuredIngestPayload."""
    mock_payload = {
        "memories": [
            {
                "id": "mem_1",
                "text": "The developer chose FalkorDB for GraphBLAS matrix acceleration.",
                "summary": "Chose FalkorDB for GraphBLAS acceleration",
                "memory_type": "decision_context",
                "confidence": 1.0,
            }
        ],
        "entities": [
            {
                "id": "ent_1",
                "name": "FalkorDB",
                "entity_type": "technology",
                "description": "Graph database",
            }
        ],
        "goals": [
            {
                "id": "g_1",
                "title": "Achieve sub-30ms reasoning retrieval",
                "priority": "high",
            }
        ],
        "decisions": [
            {
                "id": "d_1",
                "action": "Adopt Cypher graph queries",
                "rationale": "Direct relationship traversal avoids vector joins",
            }
        ],
        "experiences": [
            {
                "id": "e_1",
                "content": "Benchmarked episodic trace queries",
                "outcome": "Observed deterministic 15ms traversal",
                "confidence": 0.95,
            }
        ],
        "relationships": [
            {"source_id": "mem_1", "relation": "MENTIONS", "target_id": "ent_1"},
            {"source_id": "g_1", "relation": "MOTIVATES", "target_id": "d_1"},
            {"source_id": "d_1", "relation": "INFORMED_BY", "target_id": "e_1"},
        ],
    }

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(mock_payload)
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(
        api_key="test_dummy_key_12345",
        model="gemini-2.5-flash",
        client=mock_client,
    )

    result = provider.extract("Sample text", user_id="alex")

    assert isinstance(result, StructuredIngestPayload)
    assert len(result.memories) == 1
    assert result.memories[0].summary == "Chose FalkorDB for GraphBLAS acceleration"
    assert len(result.goals) == 1
    assert result.goals[0].title == "Achieve sub-30ms reasoning retrieval"
    assert len(result.decisions) == 1
    assert result.decisions[0].action == "Adopt Cypher graph queries"
    assert len(result.experiences) == 1
    assert len(result.relationships) == 3
    assert result.relationships[1].relation == "MOTIVATES"


# ============================================================================
# 4. Irrelevant Conversation with Empty Facts Handled Safely
# ============================================================================

def test_gemini_irrelevant_conversation_empty_facts():
    """Verify that when Gemini extracts zero factual items (e.g. conversational chatter), it raises InvalidExtractionOutputError."""
    empty_payload = {
        "memories": [],
        "entities": [],
        "goals": [],
        "decisions": [],
        "experiences": [],
        "relationships": [],
    }

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(empty_payload)
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(api_key="test_key", client=mock_client)
    with pytest.raises(InvalidExtractionOutputError) as exc:
        provider.extract("Hello, how are you today?", user_id="alex")
    assert "No extractable memories" in str(exc.value)


# ============================================================================
# 5. Contradictory Facts Preserved Separately
# ============================================================================

@requires_falkordb
def test_gemini_contradictory_facts_preserved():
    """Verify that contradictory facts returned by Gemini are preserved as separate nodes linked via CONTRADICTS."""
    user_id = f"user_contra_{uuid.uuid4().hex[:8]}"
    mem_1 = f"mem_v1_{uuid.uuid4().hex[:6]}"
    mem_2 = f"mem_v2_{uuid.uuid4().hex[:6]}"

    contra_payload = {
        "memories": [
            {
                "id": mem_1,
                "text": "The primary database is PostgreSQL.",
                "summary": "Primary database is PostgreSQL",
                "memory_type": "fact",
                "created_at": "2026-01-01T10:00:00Z",
            },
            {
                "id": mem_2,
                "text": "The primary database was switched to FalkorDB.",
                "summary": "Primary database switched to FalkorDB",
                "memory_type": "fact",
                "created_at": "2026-02-01T10:00:00Z",
            },
        ],
        "relationships": [
            {
                "source_id": mem_2,
                "relation": "CONTRADICTS",
                "target_id": mem_1,
            }
        ],
    }

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(contra_payload)
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(api_key="test_key", client=mock_client)
    with patch("app.main.get_extraction_provider", return_value=provider):
        req = {
            "user_id": user_id,
            "conversation_text": "We initially used PostgreSQL, but later switched to FalkorDB.",
        }
        resp = client.post("/memory/ingest", json=req)
        assert resp.status_code == 200
        data = resp.json()
        assert data["memories_stored"] == 2
        assert data["relationships_created"] >= 1

    # Query contradiction links
    q_req = QueryRequest(user_id=user_id, query="PostgreSQL FalkorDB primary database", max_depth=2)
    q_resp = execute_memory_query(q_req)
    assert q_resp.found_evidence is True
    assert any("CONTRADICTS" in p for p in q_resp.evidence_paths)


# ============================================================================
# 6. Malformed JSON Handling
# ============================================================================

def test_gemini_malformed_json():
    """Verify malformed JSON from Gemini raises InvalidExtractionOutputError and returns HTTP 422."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "{invalid json string... not closed"
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(api_key="test_key", client=mock_client)
    with pytest.raises(InvalidExtractionOutputError) as exc:
        provider.extract("Some conversation", user_id="alex")
    assert "invalid JSON" in str(exc.value)

    with patch("app.main.get_extraction_provider", return_value=provider):
        resp = client.post("/memory/ingest", json={"user_id": "u1", "conversation_text": "text"})
        assert resp.status_code == 422


# ============================================================================
# 7. Invalid Relationship Filtering
# ============================================================================

def test_gemini_invalid_relationship_filtering():
    """Verify that unallowlisted relationship types returned by Gemini are safely filtered out."""
    payload_with_bad_rel = {
        "memories": [
            {"id": "m1", "text": "Valid memory statement", "summary": "Valid summary"}
        ],
        "entities": [
            {"id": "e1", "name": "TestEntity", "entity_type": "concept"}
        ],
        "relationships": [
            {"source_id": "m1", "relation": "MENTIONS", "target_id": "e1"},
            {"source_id": "m1", "relation": "ARBITRARY_INJECTION_TYPE", "target_id": "e1"},
        ],
    }

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(payload_with_bad_rel)
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(api_key="test_key", client=mock_client)
    res = provider.extract("Valid input", user_id="alex")
    assert len(res.relationships) == 1
    assert res.relationships[0].relation == "MENTIONS"


# ============================================================================
# 8. Timeout and API Error Handling
# ============================================================================

def test_gemini_timeout_and_api_failure():
    """Verify that network timeout and API errors raise ExtractionError and return HTTP 502."""
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = TimeoutError("Deadline exceeded connecting to api.google.com")

    provider = GeminiExtractionProvider(api_key="test_key", client=mock_client)
    with pytest.raises(ExtractionError) as exc:
        provider.extract("Some text", user_id="alex")
    assert "timed out" in str(exc.value).lower() or "deadline" in str(exc.value).lower()

    with patch("app.main.get_extraction_provider", return_value=provider):
        resp = client.post("/memory/ingest", json={"user_id": "u1", "conversation_text": "text"})
        assert resp.status_code == 502


# ============================================================================
# 9. End-to-End Extraction, Deduplication, and User Isolation
# ============================================================================

@requires_falkordb
def test_gemini_extraction_storage_dedup_and_isolation():
    """Verify full pipeline: Gemini extraction -> FalkorDB storage -> Deduplication -> Cross-tenant isolation."""
    user_alice = f"user_gemini_alice_{uuid.uuid4().hex[:8]}"
    user_bob = f"user_gemini_bob_{uuid.uuid4().hex[:8]}"

    g_id = f"goal_{uuid.uuid4().hex[:6]}"
    d_id = f"dec_{uuid.uuid4().hex[:6]}"
    e_id = f"exp_{uuid.uuid4().hex[:6]}"

    mock_gemini_output = {
        "memories": [
            {
                "text": "Alice decided to implement FalkorDB GraphBLAS matrix traversals.",
                "summary": "Alice adopted FalkorDB GraphBLAS matrix traversals",
                "memory_type": "observation",
            }
        ],
        "goals": [{"id": g_id, "title": "Reduce Graph Traversal Latency Under 20ms", "priority": "high"}],
        "decisions": [{"id": d_id, "action": "Deploy FalkorDB Cypher Engine", "rationale": "Constant time sparse lookups"}],
        "experiences": [{"id": e_id, "content": "Benchmark test on 5000 items", "outcome": "Observed 12ms latency", "confidence": 1.0}],
        "relationships": [
            {"source_id": g_id, "relation": "MOTIVATES", "target_id": d_id},
            {"source_id": d_id, "relation": "INFORMED_BY", "target_id": e_id},
        ],
    }

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps(mock_gemini_output)
    mock_client.models.generate_content.return_value = mock_response

    provider = GeminiExtractionProvider(api_key="test_key", client=mock_client)

    # 1. First Ingestion: Stored
    with patch("app.main.get_extraction_provider", return_value=provider):
        req1 = {"user_id": user_alice, "conversation_text": "We decided to implement FalkorDB."}
        resp1 = client.post("/memory/ingest", json=req1)
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["memories_stored"] == 1
        assert data1["memories_deduplicated"] == 0
        assert data1["goals_created"] == 1
        assert data1["decisions_created"] == 1

        # 2. Second Ingestion (exact same conversation): Deduplicated
        resp2 = client.post("/memory/ingest", json=req1)
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["memories_stored"] == 0
        assert data2["memories_deduplicated"] == 1
        assert data2["goals_reused"] == 1

    # 3. Retrieval for Alice: Verified connected causal path
    q_alice = QueryRequest(user_id=user_alice, query="Reduce Graph Traversal Latency", max_depth=3)
    resp_alice = execute_memory_query(q_alice)
    assert resp_alice.found_evidence is True
    assert len(resp_alice.goals) == 1
    assert len(resp_alice.decisions) == 1
    assert any("-[MOTIVATES]->" in p for p in resp_alice.evidence_paths)

    # 4. Retrieval for Bob (User Isolation): Zero leakage
    q_bob = QueryRequest(user_id=user_bob, query="Reduce Graph Traversal Latency", max_depth=3)
    resp_bob = execute_memory_query(q_bob)
    assert resp_bob.found_evidence is False
    assert len(resp_bob.goals) == 0
    assert len(resp_bob.decisions) == 0


# ============================================================================
# 10. Live Smoke Test (Only runs if a genuine GEMINI_API_KEY is supplied)
# ============================================================================

@requires_falkordb
def test_smoke_real_gemini_live_if_configured():
    """
    Live smoke test against the actual Google Gemini API.
    Only executed if GEMINI_API_KEY is non-empty and non-placeholder.
    """
    key = settings.gemini_api_key
    if not key or key.strip().lower() in ("your_actual_key_here", "your_gemini_api_key_here", ""):
        pytest.skip("No real GEMINI_API_KEY configured in .env. Skipping live smoke test.")

    user_id = f"user_real_gemini_{uuid.uuid4().hex[:8]}"
    provider = GeminiExtractionProvider(api_key=key, model=settings.gemini_model)

    synthetic_conversation = (
        "During our architectural review, we set a goal to improve agent response speed. "
        "We decided to adopt FalkorDB because its sparse matrix GraphBLAS traversal avoids vector joins. "
        "In our preliminary test, we observed 18ms query response time."
    )

    try:
        extracted_payload = provider.extract(synthetic_conversation, user_id=user_id)
    except ExtractionError as exc:
        if "503" in str(exc) or "UNAVAILABLE" in str(exc) or "rate limit" in str(exc).lower():
            pytest.skip(f"Google Gemini API temporarily unavailable during smoke test: {exc}")
        raise
    assert isinstance(extracted_payload, StructuredIngestPayload)
    assert len(extracted_payload.goals) >= 1 or len(extracted_payload.decisions) >= 1

    # Ingest and query from FalkorDB
    from app.graph.memory_ops import ingest_memory_payload
    ingest_res = ingest_memory_payload(user_id=user_id, payload=extracted_payload)
    assert ingest_res.success is True

    q = QueryRequest(user_id=user_id, query="improve agent response speed", max_depth=2)
    q_res = execute_memory_query(q)
    assert q_res.found_evidence is True
