import pytest
from app.graph.connection import check_connection, FalkorDBConnectionError
from app.graph.operations import (
    seed_demo_memory,
    query_evidence_path,
    get_graph_stats,
    DEFAULT_DEMO_USER,
    DEFAULT_DEMO_GOAL,
    DEFAULT_DEMO_DECISION,
    DEFAULT_DEMO_EXPERIENCE,
)
from app.graph.schema import EvidencePathResponse, SeedResponse

# Custom pytest marker requirement helper
is_db_available, _ = check_connection()
requires_falkordb = pytest.mark.skipif(
    not is_db_available,
    reason="Requires active FalkorDB instance (e.g. running via `docker compose up -d`).",
)


def test_schema_model_validation():
    """Verify data models correctly parse and validate graph objects."""
    seed_res = SeedResponse(
        success=True,
        message="OK",
        user_id="u1",
        goal_id="g1",
        decision_id="d1",
        experience_id="e1",
        nodes_created=4,
        relationships_created=3,
        properties_set=14,
    )
    assert seed_res.nodes_created == 4
    assert seed_res.relationships_created == 3


@requires_falkordb
def test_graph_seed_and_multihop_traversal():
    """
    Integration test against running FalkorDB:
    1. Idempotently seeds graph.
    2. Runs multi-hop query: User -> HAS_GOAL -> Goal -> MOTIVATED_BY -> Decision -> INFORMED_BY -> Experience.
    3. Verifies nodes, edges, properties, and data persistence across subsequent reads.
    """
    # 1. Seed demo data
    seed_result = seed_demo_memory()
    assert seed_result.success is True
    assert seed_result.user_id == DEFAULT_DEMO_USER["user_id"]
    assert seed_result.goal_id == DEFAULT_DEMO_GOAL["goal_id"]

    # 2. Query evidence path by goal_id
    path_result: EvidencePathResponse = query_evidence_path(goal_id=DEFAULT_DEMO_GOAL["goal_id"])
    assert path_result.success is True
    assert path_result.count >= 1

    chain = path_result.chains[0]
    assert chain.user_id == DEFAULT_DEMO_USER["user_id"]
    assert chain.goal_id == DEFAULT_DEMO_GOAL["goal_id"]
    assert chain.decision_id == DEFAULT_DEMO_DECISION["decision_id"]
    assert chain.experience_id == DEFAULT_DEMO_EXPERIENCE["experience_id"]
    assert chain.experience_confidence == DEFAULT_DEMO_EXPERIENCE["experience_confidence"]

    # Verify structured nodes & relationships
    node_labels = {n.label for n in path_result.nodes}
    assert {"User", "Goal", "Decision", "Experience"}.issubset(node_labels)

    rel_types = {r.relation for r in path_result.relationships}
    assert {"HAS_GOAL", "MOTIVATED_BY", "INFORMED_BY"}.issubset(rel_types)

    # 3. Verify persistence across subsequent read query
    stats = get_graph_stats()
    assert stats["node_count"] >= 4
    assert stats["relationship_count"] >= 3

    # 4. Verify idempotency: running seed again should not duplicate nodes
    second_seed = seed_demo_memory()
    assert second_seed.success is True
    assert second_seed.nodes_created == 0  # No new nodes created because of MERGE
