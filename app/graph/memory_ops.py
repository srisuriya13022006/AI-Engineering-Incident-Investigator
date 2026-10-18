from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import RedisError

from app.graph.connection import get_graph_client, FalkorDBConnectionError
from app.memory.models import (
    ALLOWLISTED_RELATIONSHIPS,
    StructuredIngestPayload,
    StructuredMemory,
    StructuredEntity,
    StructuredGoal,
    StructuredDecision,
    StructuredExperience,
    StructuredRelationship,
    IngestResponse,
    generate_deterministic_id,
    generate_memory_fingerprint,
)

logger = logging.getLogger("mai.graph.memory_ops")


def ensure_user(
    user_id: str,
    user_name: Optional[str] = None,
    graph_name: Optional[str] = None,
) -> None:
    """Ensure a User node exists idempotently."""
    now_iso = datetime.now(timezone.utc).isoformat()
    name = user_name or user_id

    query = """
    MERGE (u:User {id: $user_id})
    ON CREATE SET u.name = $name, u.created_at = $timestamp
    ON MATCH SET u.name = coalesce($name, u.name), u.updated_at = $timestamp
    """
    params = {"user_id": user_id, "name": name, "timestamp": now_iso}

    try:
        graph = get_graph_client(graph_name=graph_name)
        graph.query(query, params=params)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database failure ensuring user '{user_id}': {exc}") from exc


def ingest_memory_payload(
    user_id: str,
    user_name: Optional[str] = None,
    payload: StructuredIngestPayload = None,
    extraction_mode: str = "structured",
    graph_name: Optional[str] = None,
) -> IngestResponse:
    """
    Idempotently stores memories, entities, goals, decisions, experiences,
    and allowlisted relationships into FalkorDB under the designated user.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    ensure_user(user_id=user_id, user_name=user_name, graph_name=graph_name)

    graph = get_graph_client(graph_name=graph_name)

    stored_memories = 0
    deduped_memories = 0
    memory_ids: List[str] = []
    id_mapping: Dict[str, str] = {}  # Map original/temporary IDs to graph IDs
    warnings: List[str] = []

    # 1. Store Memories
    for mem in payload.memories:
        fingerprint = generate_memory_fingerprint(
            user_id=user_id,
            summary=mem.summary,
            memory_type=mem.memory_type,
        )
        mem_id = mem.id or generate_deterministic_id("mem", f"{user_id}_{mem.summary}")
        if mem.id:
            id_mapping[mem.id] = mem_id

        # Store temporal metadata
        recorded_at = mem.recorded_at or now_iso
        valid_at = mem.valid_at or ""
        status = mem.status or "current"

        # Check for existing memory with the same fingerprint for this user
        check_query = """
        MATCH (u:User {id: $user_id})-[:HAS_MEMORY]->(m:Memory {fingerprint: $fingerprint})
        RETURN m.id AS existing_id
        """
        try:
            check_res = graph.query(check_query, params={"user_id": user_id, "fingerprint": fingerprint})
            if check_res.result_set:
                existing_id = str(check_res.result_set[0][0])
                deduped_memories += 1
                memory_ids.append(existing_id)
                if mem.id:
                    id_mapping[mem.id] = existing_id
                # Update timestamp on deduplicated memory
                graph.query(
                    "MATCH (m:Memory {id: $mem_id}) SET m.updated_at = $timestamp",
                    params={"mem_id": existing_id, "timestamp": now_iso},
                )
                continue
        except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
            raise FalkorDBConnectionError(f"Database error during memory check: {exc}") from exc

        # Create new memory node linked to User with temporal metadata
        create_query = """
        MATCH (u:User {id: $user_id})
        MERGE (m:Memory {id: $mem_id})
        ON CREATE SET 
            m.user_id = $user_id,
            m.text = $text,
            m.summary = $summary,
            m.memory_type = $memory_type,
            m.confidence = $confidence,
            m.provenance = $provenance,
            m.fingerprint = $fingerprint,
            m.created_at = $timestamp,
            m.recorded_at = $recorded_at,
            m.valid_at = $valid_at,
            m.status = $status
        ON MATCH SET
            m.updated_at = $timestamp
        MERGE (u)-[r:HAS_MEMORY]->(m)
        ON CREATE SET r.created_at = $timestamp
        """
        params = {
            "user_id": user_id,
            "mem_id": mem_id,
            "text": mem.text,
            "summary": mem.summary,
            "memory_type": mem.memory_type,
            "confidence": float(mem.confidence),
            "provenance": mem.provenance,
            "fingerprint": fingerprint,
            "timestamp": mem.created_at or now_iso,
            "recorded_at": recorded_at,
            "valid_at": valid_at,
            "status": status,
        }

        try:
            graph.query(create_query, params=params)
            stored_memories += 1
            memory_ids.append(mem_id)
        except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
            raise FalkorDBConnectionError(f"Failed to ingest memory '{mem_id}': {exc}") from exc

    # 2. Store Entities
    entities_created = 0
    entities_reused = 0
    for ent in payload.entities:
        ent_id = ent.id or generate_deterministic_id("ent", ent.name)
        if ent.id:
            id_mapping[ent.id] = ent_id

        ent_query = """
        MERGE (e:Entity {id: $ent_id})
        ON CREATE SET 
            e.name = $name,
            e.entity_type = $entity_type,
            e.description = $description,
            e.created_at = $timestamp
        ON MATCH SET 
            e.description = coalesce($description, e.description),
            e.updated_at = $timestamp
        """
        params = {
            "ent_id": ent_id,
            "name": ent.name,
            "entity_type": ent.entity_type,
            "description": ent.description or "",
            "timestamp": now_iso,
        }
        try:
            res = graph.query(ent_query, params=params)
            if res.nodes_created > 0:
                entities_created += 1
            else:
                entities_reused += 1
        except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
            raise FalkorDBConnectionError(f"Failed to ingest entity '{ent_id}': {exc}") from exc

    # 3. Store Goals
    goals_created = 0
    goals_reused = 0
    for goal in payload.goals:
        goal_id = goal.id or generate_deterministic_id("goal", goal.title)
        if goal.id:
            id_mapping[goal.id] = goal_id

        recorded_at = goal.recorded_at or now_iso
        valid_at = goal.valid_at or ""
        status = goal.status or "current"

        goal_query = """
        MATCH (u:User {id: $user_id})
        MERGE (g:Goal {id: $goal_id})
        ON CREATE SET 
            g.user_id = $user_id,
            g.title = $title,
            g.priority = $priority,
            g.created_at = $timestamp,
            g.recorded_at = $recorded_at,
            g.valid_at = $valid_at,
            g.status = $status
        ON MATCH SET 
            g.priority = $priority,
            g.updated_at = $timestamp
        MERGE (u)-[r:HAS_GOAL]->(g)
        ON CREATE SET r.created_at = $timestamp
        """
        params = {
            "user_id": user_id,
            "goal_id": goal_id,
            "title": goal.title,
            "priority": goal.priority,
            "timestamp": now_iso,
            "recorded_at": recorded_at,
            "valid_at": valid_at,
            "status": status,
        }
        try:
            res = graph.query(goal_query, params=params)
            if res.nodes_created > 0:
                goals_created += 1
            else:
                goals_reused += 1
        except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
            raise FalkorDBConnectionError(f"Failed to ingest goal '{goal_id}': {exc}") from exc

    # 4. Store Decisions
    decisions_created = 0
    decisions_reused = 0
    for dec in payload.decisions:
        dec_id = dec.id or generate_deterministic_id("dec", dec.action)
        if dec.id:
            id_mapping[dec.id] = dec_id

        recorded_at = dec.recorded_at or now_iso
        valid_at = dec.valid_at or ""
        status = dec.status or "current"

        dec_query = """
        MERGE (d:Decision {id: $dec_id})
        ON CREATE SET 
            d.user_id = $user_id,
            d.action = $action,
            d.rationale = $rationale,
            d.created_at = $timestamp,
            d.recorded_at = $recorded_at,
            d.valid_at = $valid_at,
            d.status = $status
        ON MATCH SET 
            d.rationale = $rationale,
            d.updated_at = $timestamp
        """
        params = {
            "user_id": user_id,
            "dec_id": dec_id,
            "action": dec.action,
            "rationale": dec.rationale,
            "timestamp": now_iso,
            "recorded_at": recorded_at,
            "valid_at": valid_at,
            "status": status,
        }
        try:
            res = graph.query(dec_query, params=params)
            if res.nodes_created > 0:
                decisions_created += 1
            else:
                decisions_reused += 1
        except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
            raise FalkorDBConnectionError(f"Failed to ingest decision '{dec_id}': {exc}") from exc

    # 5. Store Experiences
    experiences_created = 0
    experiences_reused = 0
    for exp in payload.experiences:
        exp_id = exp.id or generate_deterministic_id("exp", exp.content)
        if exp.id:
            id_mapping[exp.id] = exp_id

        recorded_at = exp.recorded_at or now_iso
        valid_at = exp.valid_at or ""
        status = exp.status or "current"

        exp_query = """
        MERGE (e:Experience {id: $exp_id})
        ON CREATE SET 
            e.user_id = $user_id,
            e.content = $content,
            e.outcome = $outcome,
            e.confidence = $confidence,
            e.created_at = $timestamp,
            e.recorded_at = $recorded_at,
            e.valid_at = $valid_at,
            e.status = $status
        ON MATCH SET 
            e.outcome = $outcome,
            e.confidence = $confidence,
            e.updated_at = $timestamp
        """
        params = {
            "user_id": user_id,
            "exp_id": exp_id,
            "content": exp.content,
            "outcome": exp.outcome,
            "confidence": float(exp.confidence),
            "timestamp": now_iso,
            "recorded_at": recorded_at,
            "valid_at": valid_at,
            "status": status,
        }
        try:
            res = graph.query(exp_query, params=params)
            if res.nodes_created > 0:
                experiences_created += 1
            else:
                experiences_reused += 1
        except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
            raise FalkorDBConnectionError(f"Failed to ingest experience '{exp_id}': {exc}") from exc

    # Relationships counters
    relationships_created = 0
    relationships_reused = 0

    # 5B. Automated Conflict & Temporal Linking
    from app.memory.conflict import evaluate_conflict_between_claims

    # Load existing goals and memories for this user
    existing_goals = []
    existing_mems = []
    try:
        eg_res = graph.query(
            "MATCH (u:User {id: $user_id})-[:HAS_GOAL]->(g:Goal) RETURN g.id, g.title, g.status, g.created_at",
            params={"user_id": user_id},
        )
        for row in eg_res.result_set:
            existing_goals.append({"id": str(row[0]), "title": str(row[1]), "status": str(row[2] or "current"), "created_at": str(row[3] or "")})
    except Exception as exc:
        logger.warning(f"Could not load existing goals for conflict check: {exc}")

    try:
        em_res = graph.query(
            "MATCH (u:User {id: $user_id})-[:HAS_MEMORY]->(m:Memory) RETURN m.id, m.summary, m.text, m.status, m.created_at",
            params={"user_id": user_id},
        )
        for row in em_res.result_set:
            existing_mems.append({"id": str(row[0]), "summary": str(row[1]), "text": str(row[2]), "status": str(row[3] or "current"), "created_at": str(row[4] or "")})
    except Exception as exc:
        logger.warning(f"Could not load existing memories for conflict check: {exc}")

    # Check incoming goals against existing goals
    for new_g in payload.goals:
        new_g_id = id_mapping.get(new_g.id, new_g.id or generate_deterministic_id("goal", new_g.title))
        for old_g in existing_goals:
            if old_g["id"] == new_g_id:
                continue
            conflict = evaluate_conflict_between_claims(
                older_claim=old_g["title"],
                newer_claim=new_g.title,
                older_type="goal",
                newer_type="goal",
                older_id=old_g["id"],
                newer_id=new_g_id,
                older_ts=old_g["created_at"],
                newer_ts=now_iso,
            )
            if conflict:
                if conflict.conflict_type == "superseded":
                    graph.query("MATCH (g:Goal {id: $id}) SET g.status = 'superseded'", params={"id": old_g["id"]})
                    sup_q = """
                    MATCH (g_new:Goal {id: $new_id}), (g_old:Goal {id: $old_id})
                    MERGE (g_new)-[r:SUPERSEDES]->(g_old)
                    ON CREATE SET r.reason = $reason, r.certainty = $certainty, r.created_at = $timestamp
                    """
                    s_res = graph.query(sup_q, params={
                        "new_id": new_g_id,
                        "old_id": old_g["id"],
                        "reason": conflict.reason,
                        "certainty": conflict.classification_certainty,
                        "timestamp": now_iso,
                    })
                    if s_res.relationships_created > 0:
                        relationships_created += 1
                elif conflict.conflict_type == "contradiction":
                    graph.query("MATCH (g:Goal {id: $id}) SET g.status = 'disputed'", params={"id": old_g["id"]})
                    graph.query("MATCH (g:Goal {id: $id}) SET g.status = 'disputed'", params={"id": new_g_id})
                    con_q = """
                    MATCH (g_new:Goal {id: $new_id}), (g_old:Goal {id: $old_id})
                    MERGE (g_new)-[r:CONTRADICTS]->(g_old)
                    ON CREATE SET r.reason = $reason, r.certainty = $certainty, r.created_at = $timestamp
                    """
                    c_res = graph.query(con_q, params={
                        "new_id": new_g_id,
                        "old_id": old_g["id"],
                        "reason": conflict.reason,
                        "certainty": conflict.classification_certainty,
                        "timestamp": now_iso,
                    })
                    if c_res.relationships_created > 0:
                        relationships_created += 1

    # Check incoming memories against existing memories
    for new_m in payload.memories:
        new_m_id = id_mapping.get(new_m.id, new_m.id or generate_deterministic_id("mem", new_m.summary))
        for old_m in existing_mems:
            if old_m["id"] == new_m_id:
                continue
            conflict = evaluate_conflict_between_claims(
                older_claim=old_m["text"] or old_m["summary"],
                newer_claim=new_m.text or new_m.summary,
                older_type="memory",
                newer_type="memory",
                older_id=old_m["id"],
                newer_id=new_m_id,
                older_ts=old_m["created_at"],
                newer_ts=now_iso,
            )
            if conflict:
                if conflict.conflict_type == "superseded":
                    graph.query("MATCH (m:Memory {id: $id}) SET m.status = 'superseded'", params={"id": old_m["id"]})
                    sup_q = """
                    MATCH (m_new:Memory {id: $new_id}), (m_old:Memory {id: $old_id})
                    MERGE (m_new)-[r:SUPERSEDES]->(m_old)
                    ON CREATE SET r.reason = $reason, r.certainty = $certainty, r.created_at = $timestamp
                    """
                    s_res = graph.query(sup_q, params={
                        "new_id": new_m_id,
                        "old_id": old_m["id"],
                        "reason": conflict.reason,
                        "certainty": conflict.classification_certainty,
                        "timestamp": now_iso,
                    })
                    if s_res.relationships_created > 0:
                        relationships_created += 1
                elif conflict.conflict_type == "contradiction":
                    graph.query("MATCH (m:Memory {id: $id}) SET m.status = 'disputed'", params={"id": old_m["id"]})
                    graph.query("MATCH (m:Memory {id: $id}) SET m.status = 'disputed'", params={"id": new_m_id})
                    con_q = """
                    MATCH (m_new:Memory {id: $new_id}), (m_old:Memory {id: $old_id})
                    MERGE (m_new)-[r:CONTRADICTS]->(m_old)
                    ON CREATE SET r.reason = $reason, r.certainty = $certainty, r.created_at = $timestamp
                    """
                    c_res = graph.query(con_q, params={
                        "new_id": new_m_id,
                        "old_id": old_m["id"],
                        "reason": conflict.reason,
                        "certainty": conflict.classification_certainty,
                        "timestamp": now_iso,
                    })
                    if c_res.relationships_created > 0:
                        relationships_created += 1

    # Check incoming experiences against goals for target comparison
    for new_e in payload.experiences:
        new_e_id = id_mapping.get(new_e.id, new_e.id or generate_deterministic_id("exp", new_e.content))
        for g in (payload.goals + [StructuredGoal(id=og["id"], title=og["title"]) for og in existing_goals]):
            g_id = id_mapping.get(g.id, g.id or generate_deterministic_id("goal", g.title))
            conflict = evaluate_conflict_between_claims(
                older_claim=g.title,
                newer_claim=f"{new_e.content} outcome: {new_e.outcome}",
                older_type="goal",
                newer_type="experience",
                older_id=g_id,
                newer_id=new_e_id,
                newer_ts=now_iso,
            )
            if conflict and conflict.conflict_type == "target_mismatch":
                mismatch_q = """
                MATCH (e:Experience {id: $exp_id}), (g:Goal {id: $goal_id})
                MERGE (e)-[r:INFORMED_BY]->(g)
                ON CREATE SET r.meets_target = false, r.reason = $reason, r.created_at = $timestamp
                """
                mm_res = graph.query(mismatch_q, params={
                    "exp_id": new_e_id,
                    "goal_id": g_id,
                    "reason": conflict.reason,
                    "timestamp": now_iso,
                })
                if mm_res.relationships_created > 0:
                    relationships_created += 1

    # 6. Store Relationships
    for rel in payload.relationships:
        source_id = id_mapping.get(rel.source_id, rel.source_id)
        target_id = id_mapping.get(rel.target_id, rel.target_id)
        rel_type = rel.relation.upper().strip()

        if rel_type not in ALLOWLISTED_RELATIONSHIPS:
            warnings.append(f"Ignored disallowed relationship type: {rel.relation}")
            continue

        # Safely insert allowlisted relationship
        # Cypher relationship types are validated tokens from ALLOWLISTED_RELATIONSHIPS
        rel_cypher = f"""
        MATCH (a {{id: $source_id}}), (b {{id: $target_id}})
        MERGE (a)-[r:{rel_type}]->(b)
        ON CREATE SET r.created_at = $timestamp
        """
        params = {
            "source_id": source_id,
            "target_id": target_id,
            "timestamp": now_iso,
        }
        try:
            res = graph.query(rel_cypher, params=params)
            if res.relationships_created > 0:
                relationships_created += 1
            else:
                relationships_reused += 1

            # Update statuses if relevant
            if rel_type == "SUPERSEDES":
                graph.query("MATCH (b {id: $target_id}) SET b.status = 'superseded'", params={"target_id": target_id})
            elif rel_type == "CONTRADICTS":
                graph.query("MATCH (a {id: $source_id}), (b {id: $target_id}) SET a.status = 'disputed', b.status = 'disputed'", params={"source_id": source_id, "target_id": target_id})
        except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
            warnings.append(f"Could not link {source_id} -[{rel_type}]-> {target_id}: {exc}")

    return IngestResponse(
        success=True,
        status="ingested",
        user_id=user_id,
        extraction_mode="conversation" if extraction_mode == "conversation" else "structured",
        extracted_data=payload if extraction_mode == "conversation" else None,
        memory_ids=memory_ids,
        memories_stored=stored_memories,
        memories_deduplicated=deduped_memories,
        entities_created=entities_created,
        entities_reused=entities_reused,
        entities_processed=entities_created + entities_reused,
        goals_created=goals_created,
        goals_reused=goals_reused,
        goals_processed=goals_created + goals_reused,
        decisions_created=decisions_created,
        decisions_reused=decisions_reused,
        decisions_processed=decisions_created + decisions_reused,
        experiences_created=experiences_created,
        experiences_reused=experiences_reused,
        experiences_processed=experiences_created + experiences_reused,
        relationships_created=relationships_created,
        relationships_reused=relationships_reused,
        relationships_processed=relationships_created + relationships_reused,
        warnings=warnings,
    )
