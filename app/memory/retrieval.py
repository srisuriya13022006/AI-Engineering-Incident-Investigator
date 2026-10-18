import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import RedisError

from app.graph.connection import get_graph_client, FalkorDBConnectionError
from app.memory.models import (
    QueryRequest,
    QueryResponse,
    RetrievedMemory,
    RetrievedEntity,
    RetrievedGoal,
    RetrievedDecision,
    RetrievedExperience,
)

logger = logging.getLogger("mai.memory.retrieval")

# Common stop words to filter out
STOP_WORDS = {
    "a", "an", "the", "and", "or", "is", "are", "was", "were", "in", "on", "at",
    "to", "for", "of", "with", "by", "from", "about", "what", "which", "who",
    "how", "why", "when", "where", "my", "your", "his", "her", "their", "our",
    "do", "does", "did", "have", "has", "had", "can", "could", "should", "would",
}


def extract_keywords(query: str) -> List[str]:
    """Tokenize query and strip common stop words."""
    tokens = re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", query.lower())
    keywords = [t for t in tokens if t not in STOP_WORDS]
    return keywords if keywords else tokens


def execute_memory_query(
    request: QueryRequest,
    graph_name: Optional[str] = None,
) -> QueryResponse:
    """
    Executes relationship-aware multi-hop memory retrieval strictly bounded
    to the target user_id. Returns structured nodes, relationship paths,
    provenance, and an evidence-backed summary without fabricating data.
    """
    user_id = request.user_id.strip()
    query_str = request.query.strip()
    max_results = request.max_results
    max_depth = request.max_depth

    keywords = extract_keywords(query_str)
    if not keywords:
        return QueryResponse(
            success=True,
            user_id=user_id,
            query=query_str,
            found_evidence=False,
            context_summary=f"No query keywords provided to search memory for user '{user_id}'.",
        )

    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database unavailable during retrieval: {exc}") from exc

    # Containers for retrieved entities
    memories_map: Dict[str, RetrievedMemory] = {}
    entities_map: Dict[str, RetrievedEntity] = {}
    goals_map: Dict[str, RetrievedGoal] = {}
    decisions_map: Dict[str, RetrievedDecision] = {}
    experiences_map: Dict[str, RetrievedExperience] = {}
    evidence_paths: List[str] = []
    provenances: Set[str] = set()

    for kw in keywords[:5]:  # Limit to top 5 tokens for predictable latency
        kw_param = kw.lower()

        # --------------------------------------------------------------------
        # 1. Match direct memories owned by this user
        # --------------------------------------------------------------------
        mem_cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_MEMORY]->(m:Memory)
        WHERE toLower(m.summary) CONTAINS $kw OR toLower(m.text) CONTAINS $kw
        RETURN 
            m.id AS id, 
            m.summary AS summary, 
            m.text AS text, 
            m.memory_type AS memory_type, 
            m.confidence AS confidence, 
            m.created_at AS created_at, 
            m.provenance AS provenance,
            m.fingerprint AS fingerprint,
            m.recorded_at AS recorded_at,
            m.valid_at AS valid_at,
            m.status AS status
        LIMIT $limit
        """
        try:
            mem_res = graph.ro_query(mem_cypher, params={"user_id": user_id, "kw": kw_param, "limit": max_results})
            for row in mem_res.result_set:
                m_id, summary, text, m_type, conf, created_at, prov, fp, rec_at, val_at, status = row
                if m_id not in memories_map:
                    memories_map[str(m_id)] = RetrievedMemory(
                        id=str(m_id),
                        summary=str(summary),
                        text=str(text),
                        memory_type=str(m_type or "fact"),
                        confidence=float(conf or 1.0),
                        created_at=str(created_at or ""),
                        provenance=str(prov or f"memory:{m_id}"),
                        fingerprint=str(fp or ""),
                        recorded_at=str(rec_at or created_at or ""),
                        valid_at=str(val_at or "") if val_at else None,
                        status=str(status or "current"),
                    )
                    evidence_paths.append(f"User({user_id}) -[HAS_MEMORY]-> Memory({m_id}: '{summary}')")
                    provenances.add(str(prov) if prov else f"memory:{m_id}")
        except Exception as exc:
            logger.warning(f"Error querying direct memories for keyword '{kw}': {exc}")

        # --------------------------------------------------------------------
        # 2. Multi-Hop Goal Reasoning Traversal:
        # User -> Goal -> (MOTIVATES / MOTIVATED_BY) -> Decision -> INFORMED_BY -> Experience
        # Also traverses connected Memory and Entity nodes!
        # Returns actual edge types and directions from FalkorDB.
        # --------------------------------------------------------------------
        goal_multihop_cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_GOAL]->(g:Goal)
        WHERE toLower(g.title) CONTAINS $kw
        OPTIONAL MATCH (g)-[r_gd:MOTIVATES|MOTIVATED_BY]-(d:Decision)
        OPTIONAL MATCH (d)-[r_de:INFORMED_BY]-(e:Experience)
        OPTIONAL MATCH (u)-[:HAS_MEMORY]->(m_conn:Memory)-[r_mg:HAS_GOAL|RELATED_TO]->(g)
        OPTIONAL MATCH (g)-[r_ge:MENTIONS]->(ent_g:Entity)
        OPTIONAL MATCH (m_conn)-[r_me:MENTIONS]->(ent_m:Entity)
        RETURN 
            g.id AS goal_id,
            g.title AS goal_title,
            g.priority AS goal_priority,
            g.status AS goal_status,
            g.recorded_at AS goal_recorded_at,
            g.valid_at AS goal_valid_at,
            d.id AS decision_id,
            d.action AS decision_action,
            d.rationale AS decision_rationale,
            d.status AS decision_status,
            d.recorded_at AS decision_recorded_at,
            d.valid_at AS decision_valid_at,
            type(r_gd) AS r_gd_type,
            startNode(r_gd).id AS r_gd_src,
            e.id AS experience_id,
            e.content AS experience_content,
            e.outcome AS experience_outcome,
            e.confidence AS experience_confidence,
            e.status AS experience_status,
            e.recorded_at AS experience_recorded_at,
            e.valid_at AS experience_valid_at,
            type(r_de) AS r_de_type,
            startNode(r_de).id AS r_de_src,
            m_conn.id AS mem_id,
            m_conn.summary AS mem_summary,
            m_conn.text AS mem_text,
            m_conn.memory_type AS mem_type,
            m_conn.provenance AS mem_prov,
            m_conn.status AS mem_status,
            m_conn.recorded_at AS mem_recorded_at,
            type(r_mg) AS r_mg_type,
            startNode(r_mg).id AS r_mg_src,
            ent_g.id AS ent_g_id,
            ent_g.name AS ent_g_name,
            ent_g.entity_type AS ent_g_type,
            ent_m.id AS ent_m_id,
            ent_m.name AS ent_m_name,
            ent_m.entity_type AS ent_m_type
        LIMIT $limit
        """
        try:
            g_res = graph.ro_query(goal_multihop_cypher, params={"user_id": user_id, "kw": kw_param, "limit": max_results})
            for row in g_res.result_set:
                (
                    g_id, g_title, g_prio, g_status, g_rec, g_val,
                    d_id, d_action, d_rat, d_status, d_rec, d_val,
                    r_gd_type, r_gd_src,
                    e_id, e_content, e_outcome, e_conf, e_status, e_rec, e_val,
                    r_de_type, r_de_src,
                    m_id, m_sum, m_txt, m_type, m_prov, m_status, m_rec,
                    r_mg_type, r_mg_src,
                    eg_id, eg_name, eg_type,
                    em_id, em_name, em_type
                ) = row

                if g_id and str(g_id) not in goals_map:
                    goals_map[str(g_id)] = RetrievedGoal(
                        id=str(g_id),
                        title=str(g_title),
                        priority=str(g_prio or "medium"),
                        status=str(g_status or "current"),
                        recorded_at=str(g_rec or "") if g_rec else None,
                        valid_at=str(g_val or "") if g_val else None,
                    )
                    provenances.add(f"goal:{g_id}")

                if d_id and str(d_id) not in decisions_map:
                    decisions_map[str(d_id)] = RetrievedDecision(
                        id=str(d_id),
                        action=str(d_action),
                        rationale=str(d_rat or ""),
                        status=str(d_status or "current"),
                        recorded_at=str(d_rec or "") if d_rec else None,
                        valid_at=str(d_val or "") if d_val else None,
                    )
                    provenances.add(f"decision:{d_id}")

                if e_id and str(e_id) not in experiences_map:
                    experiences_map[str(e_id)] = RetrievedExperience(
                        id=str(e_id),
                        content=str(e_content),
                        outcome=str(e_outcome or ""),
                        confidence=float(e_conf or 1.0),
                        status=str(e_status or "current"),
                        recorded_at=str(e_rec or "") if e_rec else None,
                        valid_at=str(e_val or "") if e_val else None,
                    )
                    provenances.add(f"experience:{e_id}")

                # Connected memory
                if m_id and str(m_id) not in memories_map:
                    memories_map[str(m_id)] = RetrievedMemory(
                        id=str(m_id),
                        summary=str(m_sum or ""),
                        text=str(m_txt or ""),
                        memory_type=str(m_type or "observation"),
                        confidence=1.0,
                        created_at="",
                        provenance=str(m_prov or f"memory:{m_id}"),
                    )
                    provenances.add(str(m_prov) if m_prov else f"memory:{m_id}")
                    if r_mg_type:
                        mg_dir = f"-[{r_mg_type}]->" if r_mg_src == m_id else f"<-[{r_mg_type}]-"
                        evidence_paths.append(f"Memory({m_id}) {mg_dir} Goal('{g_title}')")

                # Connected entities
                if eg_id and str(eg_id) not in entities_map:
                    entities_map[str(eg_id)] = RetrievedEntity(id=str(eg_id), name=str(eg_name), entity_type=str(eg_type or "concept"))
                    evidence_paths.append(f"Goal('{g_title}') -[MENTIONS]-> Entity({eg_name})")
                    provenances.add(f"entity:{eg_id}")

                if em_id and str(em_id) not in entities_map:
                    entities_map[str(em_id)] = RetrievedEntity(id=str(em_id), name=str(em_name), entity_type=str(em_type or "concept"))
                    provenances.add(f"entity:{em_id}")

                # Build verified causal path trace using genuine edge types and directions
                if g_id and d_id and r_gd_type:
                    gd_dir = f"-[{r_gd_type}]->" if r_gd_src == g_id else f"<-[{r_gd_type}]-"
                    if e_id and r_de_type:
                        de_dir = f"-[{r_de_type}]->" if r_de_src == d_id else f"<-[{r_de_type}]-"
                        path_str = f"User({user_id}) -[HAS_GOAL]-> Goal('{g_title}') {gd_dir} Decision('{d_action}') {de_dir} Experience('{e_content}')"
                    else:
                        path_str = f"User({user_id}) -[HAS_GOAL]-> Goal('{g_title}') {gd_dir} Decision('{d_action}')"
                    if path_str not in evidence_paths:
                        evidence_paths.append(path_str)
                elif g_id:
                    path_str = f"User({user_id}) -[HAS_GOAL]-> Goal('{g_title}')"
                    if path_str not in evidence_paths:
                        evidence_paths.append(path_str)
        except Exception as exc:
            logger.warning(f"Error querying multi-hop goals for keyword '{kw}': {exc}")

        # --------------------------------------------------------------------
        # 3. Decision / Action Direct Matching Traversal:
        # User -> Goal -> Decision -> Experience
        # --------------------------------------------------------------------
        dec_match_cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_GOAL]->(g:Goal)-[r_gd:MOTIVATES|MOTIVATED_BY]-(d:Decision)
        WHERE toLower(d.action) CONTAINS $kw OR toLower(d.rationale) CONTAINS $kw
        OPTIONAL MATCH (d)-[r_de:INFORMED_BY]-(e:Experience)
        RETURN 
            g.id AS goal_id, g.title AS goal_title, g.priority AS goal_priority, g.status AS goal_status, g.recorded_at AS goal_recorded_at, g.valid_at AS goal_valid_at,
            d.id AS decision_id, d.action AS decision_action, d.rationale AS decision_rationale, d.status AS decision_status, d.recorded_at AS decision_recorded_at, d.valid_at AS decision_valid_at,
            type(r_gd) AS r_gd_type, startNode(r_gd).id AS r_gd_src,
            e.id AS experience_id, e.content AS experience_content, e.outcome AS experience_outcome, e.confidence AS experience_confidence, e.status AS experience_status, e.recorded_at AS experience_recorded_at, e.valid_at AS experience_valid_at,
            type(r_de) AS r_de_type, startNode(r_de).id AS r_de_src
        LIMIT $limit
        """
        try:
            d_res = graph.ro_query(dec_match_cypher, params={"user_id": user_id, "kw": kw_param, "limit": max_results})
            for row in d_res.result_set:
                (
                    g_id, g_title, g_prio, g_status, g_rec, g_val,
                    d_id, d_action, d_rat, d_status, d_rec, d_val,
                    r_gd_type, r_gd_src,
                    e_id, e_content, e_outcome, e_conf, e_status, e_rec, e_val,
                    r_de_type, r_de_src
                ) = row
                if g_id and str(g_id) not in goals_map:
                    goals_map[str(g_id)] = RetrievedGoal(
                        id=str(g_id),
                        title=str(g_title),
                        priority=str(g_prio or "medium"),
                        status=str(g_status or "current"),
                        recorded_at=str(g_rec or "") if g_rec else None,
                        valid_at=str(g_val or "") if g_val else None,
                    )
                    provenances.add(f"goal:{g_id}")
                if d_id and str(d_id) not in decisions_map:
                    decisions_map[str(d_id)] = RetrievedDecision(
                        id=str(d_id),
                        action=str(d_action),
                        rationale=str(d_rat or ""),
                        status=str(d_status or "current"),
                        recorded_at=str(d_rec or "") if d_rec else None,
                        valid_at=str(d_val or "") if d_val else None,
                    )
                    provenances.add(f"decision:{d_id}")
                if e_id and str(e_id) not in experiences_map:
                    experiences_map[str(e_id)] = RetrievedExperience(
                        id=str(e_id),
                        content=str(e_content),
                        outcome=str(e_outcome or ""),
                        confidence=float(e_conf or 1.0),
                        status=str(e_status or "current"),
                        recorded_at=str(e_rec or "") if e_rec else None,
                        valid_at=str(e_val or "") if e_val else None,
                    )
                    provenances.add(f"experience:{e_id}")
                if d_id and r_gd_type:
                    gd_dir = f"-[{r_gd_type}]->" if r_gd_src == g_id else f"<-[{r_gd_type}]-"
                    if e_id and r_de_type:
                        de_dir = f"-[{r_de_type}]->" if r_de_src == d_id else f"<-[{r_de_type}]-"
                        path_str = f"Goal('{g_title}') {gd_dir} Decision('{d_action}') {de_dir} Experience('{e_content}')"
                    else:
                        path_str = f"Goal('{g_title}') {gd_dir} Decision('{d_action}')"
                    if path_str not in evidence_paths:
                        evidence_paths.append(path_str)
        except Exception as exc:
            logger.warning(f"Error querying decision matches: {exc}")

        # --------------------------------------------------------------------
        # 3B. Experience Direct / Linked Traversal:
        # Match experiences belonging to user or linked to memories/goals
        # --------------------------------------------------------------------
        exp_search_cypher = """
        MATCH (e:Experience {user_id: $user_id})
        WHERE toLower(e.content) CONTAINS $kw OR toLower(e.outcome) CONTAINS $kw
        RETURN e.id, e.content, e.outcome, e.confidence, e.recorded_at, e.valid_at, e.status
        LIMIT $limit
        """
        try:
            exp_res = graph.ro_query(exp_search_cypher, params={"user_id": user_id, "kw": kw_param, "limit": max_results})
            for row in exp_res.result_set:
                e_id, e_content, e_outcome, e_conf, e_rec, e_val, e_status = row
                if e_id and str(e_id) not in experiences_map:
                    experiences_map[str(e_id)] = RetrievedExperience(
                        id=str(e_id),
                        content=str(e_content),
                        outcome=str(e_outcome or ""),
                        confidence=float(e_conf or 1.0),
                        recorded_at=str(e_rec or ""),
                        valid_at=str(e_val or "") if e_val else None,
                        status=str(e_status or "current"),
                    )
                    provenances.add(f"experience:{e_id}")
                    path_str = f"User({user_id}) -> Experience({e_id}: '{e_content}')"
                    if path_str not in evidence_paths:
                        evidence_paths.append(path_str)
        except Exception as exc:
            logger.warning(f"Error querying direct experience matches: {exc}")

        # --------------------------------------------------------------------
        # 4. Entity Mention Traversal (Hop 2/3)
        # --------------------------------------------------------------------
        if max_depth >= 2:
            ent_search_cypher = """
            MATCH (u:User {id: $user_id})-[:HAS_MEMORY]->(m:Memory)-[:MENTIONS]->(ent:Entity)
            WHERE toLower(ent.name) CONTAINS $kw
            RETURN m.id AS mem_id, m.summary AS mem_sum, ent.id AS ent_id, ent.name AS ent_name, ent.entity_type AS ent_type, ent.description AS ent_desc
            LIMIT $limit
            """
            try:
                ent_search_res = graph.ro_query(ent_search_cypher, params={"user_id": user_id, "kw": kw_param, "limit": max_results})
                for row in ent_search_res.result_set:
                    m_id, m_sum, ent_id, ent_name, ent_type, ent_desc = row
                    if ent_id and str(ent_id) not in entities_map:
                        entities_map[str(ent_id)] = RetrievedEntity(id=str(ent_id), name=str(ent_name), entity_type=str(ent_type or "concept"), description=str(ent_desc or ""))
                        provenances.add(f"entity:{ent_id}")
                        path_str = f"Memory({m_id}) -[MENTIONS]-> Entity({ent_name})"
                        if path_str not in evidence_paths:
                            evidence_paths.append(path_str)
            except Exception as exc:
                logger.warning(f"Error searching entity mentions: {exc}")

    # ------------------------------------------------------------------------
    # 5. Connected Entity and Contradiction Expansion from Retrieved Memories
    # ------------------------------------------------------------------------
    if memories_map and max_depth >= 2:
        mem_ids_list = list(memories_map.keys())[:10]
        ent_cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_MEMORY]->(m:Memory)-[r:MENTIONS]->(ent:Entity)
        WHERE m.id IN $mem_ids
        RETURN m.id AS mem_id, ent.id AS ent_id, ent.name AS ent_name, ent.entity_type AS ent_type, ent.description AS ent_desc
        LIMIT $limit
        """
        try:
            ent_res = graph.ro_query(ent_cypher, params={"user_id": user_id, "mem_ids": mem_ids_list, "limit": max_results})
            for row in ent_res.result_set:
                m_id, ent_id, ent_name, ent_type, ent_desc = row
                if ent_id not in entities_map:
                    entities_map[str(ent_id)] = RetrievedEntity(
                        id=str(ent_id),
                        name=str(ent_name),
                        entity_type=str(ent_type or "concept"),
                        description=str(ent_desc or ""),
                    )
                    provenances.add(f"entity:{ent_id}")
                    path_str = f"Memory({m_id}) -[MENTIONS]-> Entity({ent_name})"
                    if path_str not in evidence_paths:
                        evidence_paths.append(path_str)
        except Exception as exc:
            logger.warning(f"Error querying memory entities: {exc}")

        # --------------------------------------------------------------------
        # 5. Connected Entity and Conflict Expansion from Retrieved Memories
        # --------------------------------------------------------------------
    from app.memory.models import ConflictDetail
    from app.memory.conflict import evaluate_conflict_between_claims

    conflicts: List[ConflictDetail] = []
    historical_claims: List[RetrievedMemory] = []
    superseding_claims: List[RetrievedMemory] = []

    # Identify historical/superseded memories among retrieved
    for m in memories_map.values():
        if m.status in ("superseded", "historical"):
            historical_claims.append(m)

    mem_ids_list = list(memories_map.keys())[:20]

    # Query SUPERSEDES relations for Memories
    sup_mem_cypher = """
    MATCH (u:User {id: $user_id})-[:HAS_MEMORY]->(m1:Memory)-[r:SUPERSEDES]->(m2:Memory)
    WHERE m1.id IN $mem_ids OR m2.id IN $mem_ids
    RETURN m1.id AS m1_id, m1.summary AS m1_sum, m2.id AS m2_id, m2.summary AS m2_sum, r.reason AS reason, r.certainty AS certainty, r.created_at AS ts, m1.recorded_at AS m1_ts, m2.recorded_at AS m2_ts
    """
    try:
        sup_res = graph.ro_query(sup_mem_cypher, params={"user_id": user_id, "mem_ids": mem_ids_list})
        for row in sup_res.result_set:
            m1_id, m1_sum, m2_id, m2_sum, reason, cert, ts, m1_ts, m2_ts = row
            path_str = f"Memory({m1_id}: '{m1_sum}') -[SUPERSEDES]-> Memory({m2_id}: '{m2_sum}') [reason: {reason}]"
            if path_str not in evidence_paths:
                evidence_paths.append(path_str)
            conflicts.append(
                ConflictDetail(
                    subject="memory_update",
                    conflict_type="superseded",
                    original_claim=str(m2_sum),
                    original_id=str(m2_id),
                    original_timestamp=str(m2_ts or ts or ""),
                    new_claim=str(m1_sum),
                    new_id=str(m1_id),
                    new_timestamp=str(m1_ts or ts or ""),
                    reason=str(reason or "Newer memory supersedes earlier claim."),
                    classification_certainty=str(cert or "deterministic"),
                    source_memory_ids=[str(m2_id), str(m1_id)],
                )
            )
            if m1_id in memories_map and memories_map[m1_id] not in superseding_claims:
                superseding_claims.append(memories_map[m1_id])
    except Exception as exc:
        logger.warning(f"Error querying SUPERSEDES memories: {exc}")

    # Query Goal SUPERSEDES relations
    sup_goal_cypher = """
    MATCH (u:User {id: $user_id})-[:HAS_GOAL]->(g1:Goal)-[r:SUPERSEDES]->(g2:Goal)
    RETURN g1.id AS g1_id, g1.title AS g1_title, g2.id AS g2_id, g2.title AS g2_title, r.reason AS reason, r.certainty AS cert, r.created_at AS ts, g1.recorded_at AS g1_ts, g2.recorded_at AS g2_ts
    """
    try:
        sg_res = graph.ro_query(sup_goal_cypher, params={"user_id": user_id})
        for row in sg_res.result_set:
            g1_id, g1_title, g2_id, g2_title, reason, cert, ts, g1_ts, g2_ts = row
            path_str = f"Goal('{g1_title}') -[SUPERSEDES]-> Goal('{g2_title}') [reason: {reason}]"
            if path_str not in evidence_paths:
                evidence_paths.append(path_str)
            conflicts.append(
                ConflictDetail(
                    subject="target_response_latency",
                    conflict_type="superseded",
                    original_claim=str(g2_title),
                    original_id=str(g2_id),
                    original_timestamp=str(g2_ts or ts or ""),
                    new_claim=str(g1_title),
                    new_id=str(g1_id),
                    new_timestamp=str(g1_ts or ts or ""),
                    reason=str(reason or "Goal updated."),
                    classification_certainty=str(cert or "deterministic"),
                    source_memory_ids=[str(g2_id), str(g1_id)],
                )
            )
    except Exception as exc:
        logger.warning(f"Error querying Goal SUPERSEDES: {exc}")

    # Query CONTRADICTS links
    contra_cypher = """
    MATCH (u:User {id: $user_id})-[:HAS_MEMORY]->(m1:Memory)-[r:CONTRADICTS]->(m2:Memory)
    WHERE m1.id IN $mem_ids OR m2.id IN $mem_ids
    RETURN m1.id AS m1_id, m1.summary AS m1_sum, m2.id AS m2_id, m2.summary AS m2_sum, r.reason AS reason, r.created_at AS ts, m1.recorded_at AS m1_ts, m2.recorded_at AS m2_ts
    """
    try:
        contra_res = graph.ro_query(contra_cypher, params={"user_id": user_id, "mem_ids": mem_ids_list})
        for row in contra_res.result_set:
            m1_id, m1_sum, m2_id, m2_sum, reason, ts, m1_ts, m2_ts = row
            path_str = f"Memory({m1_id}: '{m1_sum}') -[CONTRADICTS]-> Memory({m2_id}: '{m2_sum}') [timestamp: {ts}]"
            if path_str not in evidence_paths:
                evidence_paths.append(path_str)
            conflicts.append(
                ConflictDetail(
                    subject="contradictory_memory",
                    conflict_type="contradiction",
                    original_claim=str(m2_sum),
                    original_id=str(m2_id),
                    original_timestamp=str(m2_ts or ts or ""),
                    new_claim=str(m1_sum),
                    new_id=str(m1_id),
                    new_timestamp=str(m1_ts or ts or ""),
                    reason=str(reason or "Contradictory factual claims preserved separately."),
                    classification_certainty="deterministic",
                    source_memory_ids=[str(m2_id), str(m1_id)],
                )
            )
    except Exception as exc:
        logger.warning(f"Error querying contradiction links: {exc}")

    # Dynamic target vs benchmark evaluation among retrieved goals and experiences/memories
    for g in goals_map.values():
        for exp in experiences_map.values():
            mismatch = evaluate_conflict_between_claims(
                older_claim=g.title,
                newer_claim=f"{exp.content} outcome: {exp.outcome}",
                older_type="goal",
                newer_type="experience",
                older_id=g.id,
                newer_id=exp.id,
                older_ts=g.recorded_at,
                newer_ts=exp.recorded_at,
            )
            if mismatch and mismatch.conflict_type in ("target_mismatch", "compatible"):
                if not any(c.subject == mismatch.subject and c.original_id == g.id and c.new_id == exp.id for c in conflicts):
                    conflicts.append(mismatch)

        for m in memories_map.values():
            mismatch = evaluate_conflict_between_claims(
                older_claim=g.title,
                newer_claim=m.text or m.summary,
                older_type="goal",
                newer_type="memory",
                older_id=g.id,
                newer_id=m.id,
                older_ts=g.recorded_at,
                newer_ts=m.recorded_at or m.created_at,
            )
            if mismatch and mismatch.conflict_type in ("target_mismatch", "compatible"):
                if not any(c.subject == mismatch.subject and c.original_id == g.id and c.new_id == m.id for c in conflicts):
                    conflicts.append(mismatch)

    has_conflicts = bool(conflicts)

    # Determine whether supporting evidence was found
    found_evidence = bool(memories_map or goals_map or decisions_map or experiences_map or entities_map)

    # Construct concise, evidence-grounded context summary without fabricating facts
    if not found_evidence:
        context_summary = f"No supporting evidence found in memory graph for user '{user_id}'."
    else:
        summary_parts = []
        if memories_map:
            mem_summaries = "; ".join([f"'{m.summary}' (status: {m.status}, recorded: {m.recorded_at or m.created_at})" for m in list(memories_map.values())[:3]])
            summary_parts.append(f"Memories: {mem_summaries}")
        if goals_map:
            goal_titles = ", ".join([f"'{g.title}' (status: {g.status})" for g in goals_map.values()])
            summary_parts.append(f"Goals: {goal_titles}")
        if decisions_map:
            decision_actions = "; ".join([f"Action: '{d.action}' (Rationale: {d.rationale})" for d in decisions_map.values()])
            summary_parts.append(f"Decisions: {decision_actions}")
        if experiences_map:
            exp_details = "; ".join([f"'{e.content}' -> outcome: {e.outcome}" for e in experiences_map.values()])
            summary_parts.append(f"Empirical evidence: {exp_details}")
        if entities_map:
            ent_names = ", ".join([e.name for e in entities_map.values()])
            summary_parts.append(f"Entities: {ent_names}")

        if conflicts:
            conflict_exps = " | ".join([f"[{c.conflict_type.upper()}] {c.reason}" for c in conflicts[:2]])
            summary_parts.append(f"Conflict & Temporal Context: {conflict_exps} (Evidence preserved; no claims silently overwritten).")

        context_summary = f"Evidence for '{user_id}': " + " | ".join(summary_parts)

    return QueryResponse(
        success=True,
        user_id=user_id,
        query=query_str,
        found_evidence=found_evidence,
        context_summary=context_summary,
        memories=list(memories_map.values())[:max_results],
        entities=list(entities_map.values())[:max_results],
        goals=list(goals_map.values())[:max_results],
        decisions=list(decisions_map.values())[:max_results],
        experiences=list(experiences_map.values())[:max_results],
        evidence_paths=evidence_paths[:max_results * 2],
        provenance=sorted(list(provenances)),
        conflicts=conflicts,
        has_conflicts=has_conflicts,
        historical_claims=historical_claims,
        superseding_claims=superseding_claims,
    )
