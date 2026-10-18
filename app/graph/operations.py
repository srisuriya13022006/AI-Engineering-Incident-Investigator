import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import RedisError

from app.graph.connection import get_graph_client, FalkorDBConnectionError
from app.graph.schema import (
    EvidenceChain,
    EvidencePathResponse,
    NodeData,
    RelationshipData,
    SeedResponse,
)

logger = logging.getLogger("mai.graph.operations")

# Default synthetic demo fixtures for deterministic verification
DEFAULT_DEMO_USER = {
    "user_id": "user_mai_01",
    "user_name": "Suriya",
}
DEFAULT_DEMO_GOAL = {
    "goal_id": "goal_optimize_query_latency",
    "goal_title": "Reduce Graph Reasoning Retrieval Latency Under 50ms",
    "goal_priority": "high",
}
DEFAULT_DEMO_DECISION = {
    "decision_id": "dec_use_falkordb_indexed_paths",
    "decision_action": "Migrate from ad-hoc vector lookups to FalkorDB multi-hop Cypher traversal",
    "decision_rationale": "Graph query benchmarks demonstrate O(k) hop traversals outperforming iterative vector similarity joins",
}
DEFAULT_DEMO_EXPERIENCE = {
    "experience_id": "exp_benchmark_traversal_2026",
    "experience_content": "Evaluated 3-hop connected retrieval on 10,000 synthetic episodic memories in FalkorDB",
    "experience_outcome": "Observed 18ms median query latency and 100% path determinism",
    "experience_confidence": 0.95,
}


def seed_demo_memory(
    graph_name: Optional[str] = None,
    user_payload: Optional[Dict[str, Any]] = None,
    goal_payload: Optional[Dict[str, Any]] = None,
    decision_payload: Optional[Dict[str, Any]] = None,
    experience_payload: Optional[Dict[str, Any]] = None,
) -> SeedResponse:
    """
    Idempotently creates or updates a User -> Goal -> Decision -> Experience
    evidence path using parameterized Cypher MERGE statements.
    """
    user = {**DEFAULT_DEMO_USER, **(user_payload or {})}
    goal = {**DEFAULT_DEMO_GOAL, **(goal_payload or {})}
    decision = {**DEFAULT_DEMO_DECISION, **(decision_payload or {})}
    experience = {**DEFAULT_DEMO_EXPERIENCE, **(experience_payload or {})}

    now_iso = datetime.now(timezone.utc).isoformat()

    cypher_query = """
    MERGE (u:User {id: $user_id})
    ON CREATE SET u.name = $user_name, u.created_at = $timestamp
    ON MATCH SET u.name = $user_name, u.updated_at = $timestamp

    MERGE (g:Goal {id: $goal_id})
    ON CREATE SET g.title = $goal_title, g.priority = $goal_priority, g.created_at = $timestamp
    ON MATCH SET g.title = $goal_title, g.priority = $goal_priority, g.updated_at = $timestamp

    MERGE (d:Decision {id: $decision_id})
    ON CREATE SET d.action = $decision_action, d.rationale = $decision_rationale, d.created_at = $timestamp
    ON MATCH SET d.action = $decision_action, d.rationale = $decision_rationale, d.updated_at = $timestamp

    MERGE (e:Experience {id: $experience_id})
    ON CREATE SET e.content = $experience_content, e.outcome = $experience_outcome, e.confidence = $experience_confidence, e.created_at = $timestamp
    ON MATCH SET e.content = $experience_content, e.outcome = $experience_outcome, e.confidence = $experience_confidence, e.updated_at = $timestamp

    MERGE (u)-[r_ug:HAS_GOAL]->(g)
    ON CREATE SET r_ug.created_at = $timestamp

    MERGE (g)-[r_gd:MOTIVATED_BY]->(d)
    ON CREATE SET r_gd.created_at = $timestamp

    MERGE (d)-[r_de:INFORMED_BY]->(e)
    ON CREATE SET r_de.created_at = $timestamp

    RETURN u.id AS user_id, g.id AS goal_id, d.id AS decision_id, e.id AS experience_id
    """

    params = {
        "user_id": user["user_id"],
        "user_name": user["user_name"],
        "goal_id": goal["goal_id"],
        "goal_title": goal["goal_title"],
        "goal_priority": goal["goal_priority"],
        "decision_id": decision["decision_id"],
        "decision_action": decision["decision_action"],
        "decision_rationale": decision["decision_rationale"],
        "experience_id": experience["experience_id"],
        "experience_content": experience["experience_content"],
        "experience_outcome": experience["experience_outcome"],
        "experience_confidence": float(experience["experience_confidence"]),
        "timestamp": now_iso,
    }

    try:
        graph = get_graph_client(graph_name=graph_name)
        result = graph.query(cypher_query, params=params)

        return SeedResponse(
            success=True,
            message="Synthetic agent memory path seeded idempotently.",
            user_id=user["user_id"],
            goal_id=goal["goal_id"],
            decision_id=decision["decision_id"],
            experience_id=experience["experience_id"],
            nodes_created=result.nodes_created,
            relationships_created=result.relationships_created,
            properties_set=result.properties_set,
        )
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        logger.error(f"FalkorDB query failed during seed: {exc}")
        raise FalkorDBConnectionError(f"Database error during seeding: {exc}") from exc


def query_evidence_path(
    goal_id: Optional[str] = None,
    user_id: Optional[str] = None,
    graph_name: Optional[str] = None,
) -> EvidencePathResponse:
    """
    Executes a multi-hop Cypher query traversing:
    (User)-[:HAS_GOAL]->(Goal)-[:MOTIVATED_BY]->(Decision)-[:INFORMED_BY]->(Experience)
    Returns structured nodes, relationships, and evidence paths.
    """
    # Parameterized multi-hop query
    # If goal_id is provided, filters precisely to that goal; otherwise retrieves all memory chains
    cypher_query = """
    MATCH (u:User)-[r_ug:HAS_GOAL]->(g:Goal)-[r_gd:MOTIVATED_BY]->(d:Decision)-[r_de:INFORMED_BY]->(e:Experience)
    WHERE ($goal_id IS NULL OR g.id = $goal_id)
      AND ($user_id IS NULL OR u.id = $user_id)
    RETURN 
        u.id AS user_id,
        u.name AS user_name,
        g.id AS goal_id,
        g.title AS goal_title,
        g.priority AS goal_priority,
        d.id AS decision_id,
        d.action AS decision_action,
        d.rationale AS decision_rationale,
        e.id AS experience_id,
        e.content AS experience_content,
        e.outcome AS experience_outcome,
        e.confidence AS experience_confidence
    """

    params = {
        "goal_id": goal_id if goal_id is not None else None,
        "user_id": user_id if user_id is not None else None,
    }

    try:
        graph = get_graph_client(graph_name=graph_name)
        # Using read-only query ro_query for optimal read execution plan
        result = graph.ro_query(cypher_query, params=params)

        chains: List[EvidenceChain] = []
        unique_nodes: Dict[str, NodeData] = {}
        unique_rels: Dict[str, RelationshipData] = {}

        for row in result.result_set:
            (
                u_id,
                u_name,
                g_id,
                g_title,
                g_priority,
                d_id,
                d_action,
                d_rationale,
                e_id,
                e_content,
                e_outcome,
                e_conf,
            ) = row

            summary = (
                f"User({u_name}) HAS_GOAL -> Goal('{g_title}') "
                f"MOTIVATED_BY -> Decision('{d_action}') "
                f"INFORMED_BY -> Experience('{e_content}' | confidence={e_conf})"
            )

            chain = EvidenceChain(
                user_id=str(u_id),
                user_name=str(u_name),
                goal_id=str(g_id),
                goal_title=str(g_title),
                goal_priority=str(g_priority),
                decision_id=str(d_id),
                decision_action=str(d_action),
                decision_rationale=str(d_rationale),
                experience_id=str(e_id),
                experience_content=str(e_content),
                experience_outcome=str(e_outcome),
                experience_confidence=float(e_conf or 0.0),
                path_summary=summary,
            )
            chains.append(chain)

            # Collect unique structured nodes
            unique_nodes[f"User:{u_id}"] = NodeData(label="User", id=str(u_id), properties={"name": u_name})
            unique_nodes[f"Goal:{g_id}"] = NodeData(
                label="Goal", id=str(g_id), properties={"title": g_title, "priority": g_priority}
            )
            unique_nodes[f"Decision:{d_id}"] = NodeData(
                label="Decision", id=str(d_id), properties={"action": d_action, "rationale": d_rationale}
            )
            unique_nodes[f"Experience:{e_id}"] = NodeData(
                label="Experience",
                id=str(e_id),
                properties={"content": e_content, "outcome": e_outcome, "confidence": e_conf},
            )

            # Collect unique relationships
            unique_rels[f"{u_id}->HAS_GOAL->{g_id}"] = RelationshipData(
                source_id=str(u_id),
                source_label="User",
                relation="HAS_GOAL",
                target_id=str(g_id),
                target_label="Goal",
            )
            unique_rels[f"{g_id}->MOTIVATED_BY->{d_id}"] = RelationshipData(
                source_id=str(g_id),
                source_label="Goal",
                relation="MOTIVATED_BY",
                target_id=str(d_id),
                target_label="Decision",
            )
            unique_rels[f"{d_id}->INFORMED_BY->{e_id}"] = RelationshipData(
                source_id=str(d_id),
                source_label="Decision",
                relation="INFORMED_BY",
                target_id=str(e_id),
                target_label="Experience",
            )

        return EvidencePathResponse(
            success=True,
            count=len(chains),
            chains=chains,
            nodes=list(unique_nodes.values()),
            relationships=list(unique_rels.values()),
        )

    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        logger.error(f"FalkorDB query failed during path query: {exc}")
        raise FalkorDBConnectionError(f"Database error during evidence path query: {exc}") from exc


def get_graph_stats(graph_name: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve node and relationship count stats from the graph."""
    cypher_query = """
    MATCH (n)
    OPTIONAL MATCH (n)-[r]->()
    RETURN count(DISTINCT n) AS node_count, count(r) AS rel_count
    """
    try:
        graph = get_graph_client(graph_name=graph_name)
        result = graph.ro_query(cypher_query)
        if result.result_set:
            node_cnt, rel_cnt = result.result_set[0]
            return {"node_count": int(node_cnt), "relationship_count": int(rel_cnt)}
        return {"node_count": 0, "relationship_count": 0}
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Failed to query graph stats: {exc}") from exc
