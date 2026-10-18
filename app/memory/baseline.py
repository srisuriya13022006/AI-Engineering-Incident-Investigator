import logging
import time
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import RedisError

from app.graph.connection import get_graph_client, FalkorDBConnectionError
from app.memory.retrieval import extract_keywords

logger = logging.getLogger("mai.memory.baseline")


class BaselineRecord(BaseModel):
    """A record retrieved purely by direct keyword matching without graph traversal."""
    id: str
    record_type: str  # "Memory", "Goal", "Decision", "Experience", "Entity"
    title_or_summary: str
    content: str
    matched_keyword: str


class BaselineResult(BaseModel):
    """Result of searching the dataset with keyword matching only (no graph traversal)."""
    user_id: str
    query: str
    keywords: List[str]
    total_retrieved: int
    records: List[BaselineRecord] = Field(default_factory=list)
    found_goal: bool = False
    found_decision: bool = False
    found_experience: bool = False
    evidence_path_available: bool = False
    execution_time_ms: float = 0.0


def keyword_baseline_search(
    user_id: str,
    query: str,
    max_results: int = 10,
    graph_name: Optional[str] = None,
) -> BaselineResult:
    """
    Executes a fair keyword search baseline over the user's records in FalkorDB
    WITHOUT following any graph edges/relationships.

    Demonstrates the limitation of traditional text matching:
    Keyword search can only find records containing the exact words,
    and cannot retrieve the connected causal chain (Goal -> Decision -> Experience).
    """
    start_time = time.perf_counter()
    keywords = extract_keywords(query)
    user_id = user_id.strip()

    if not keywords:
        return BaselineResult(
            user_id=user_id,
            query=query,
            keywords=[],
            total_retrieved=0,
            execution_time_ms=0.0,
        )

    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database error during baseline search: {exc}") from exc

    records_map: Dict[str, BaselineRecord] = {}

    for kw in keywords[:5]:
        kw_lower = kw.lower()

        # 1. Search Memories
        mem_cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_MEMORY]->(m:Memory)
        WHERE toLower(m.summary) CONTAINS $kw OR toLower(m.text) CONTAINS $kw
        RETURN m.id AS id, m.summary AS summary, m.text AS text
        LIMIT $limit
        """
        try:
            res = graph.ro_query(mem_cypher, params={"user_id": user_id, "kw": kw_lower, "limit": max_results})
            for row in res.result_set:
                m_id, summary, text = row
                if m_id not in records_map:
                    records_map[str(m_id)] = BaselineRecord(
                        id=str(m_id),
                        record_type="Memory",
                        title_or_summary=str(summary),
                        content=str(text),
                        matched_keyword=kw,
                    )
        except Exception as exc:
            logger.warning(f"Baseline error querying memories: {exc}")

        # 2. Search Goals
        goal_cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_GOAL]->(g:Goal)
        WHERE toLower(g.title) CONTAINS $kw
        RETURN g.id AS id, g.title AS title
        LIMIT $limit
        """
        try:
            res = graph.ro_query(goal_cypher, params={"user_id": user_id, "kw": kw_lower, "limit": max_results})
            for row in res.result_set:
                g_id, title = row
                if g_id not in records_map:
                    records_map[str(g_id)] = BaselineRecord(
                        id=str(g_id),
                        record_type="Goal",
                        title_or_summary=str(title),
                        content=str(title),
                        matched_keyword=kw,
                    )
        except Exception as exc:
            logger.warning(f"Baseline error querying goals: {exc}")

        # 3. Search Decisions (isolated, user-scoped)
        dec_cypher = """
        MATCH (d:Decision {user_id: $user_id})
        WHERE toLower(d.action) CONTAINS $kw OR toLower(d.rationale) CONTAINS $kw
        RETURN d.id AS id, d.action AS action, d.rationale AS rationale
        LIMIT $limit
        """
        try:
            res = graph.ro_query(dec_cypher, params={"user_id": user_id, "kw": kw_lower, "limit": max_results})
            for row in res.result_set:
                d_id, action, rationale = row
                if d_id not in records_map:
                    records_map[str(d_id)] = BaselineRecord(
                        id=str(d_id),
                        record_type="Decision",
                        title_or_summary=str(action),
                        content=str(rationale),
                        matched_keyword=kw,
                    )
        except Exception as exc:
            logger.warning(f"Baseline error querying decisions: {exc}")

        # 4. Search Experiences (isolated, user-scoped)
        exp_cypher = """
        MATCH (e:Experience {user_id: $user_id})
        WHERE toLower(e.content) CONTAINS $kw OR toLower(e.outcome) CONTAINS $kw
        RETURN e.id AS id, e.content AS content, e.outcome AS outcome
        LIMIT $limit
        """
        try:
            res = graph.ro_query(exp_cypher, params={"user_id": user_id, "kw": kw_lower, "limit": max_results})
            for row in res.result_set:
                e_id, content, outcome = row
                if e_id not in records_map:
                    records_map[str(e_id)] = BaselineRecord(
                        id=str(e_id),
                        record_type="Experience",
                        title_or_summary=str(content),
                        content=str(outcome),
                        matched_keyword=kw,
                    )
        except Exception as exc:
            logger.warning(f"Baseline error querying experiences: {exc}")

    elapsed_ms = (time.perf_counter() - start_time) * 1000.0
    records_list = list(records_map.values())[:max_results]

    found_goal = any(r.record_type == "Goal" for r in records_list)
    found_decision = any(r.record_type == "Decision" for r in records_list)
    found_experience = any(r.record_type == "Experience" for r in records_list)

    return BaselineResult(
        user_id=user_id,
        query=query,
        keywords=keywords,
        total_retrieved=len(records_list),
        records=records_list,
        found_goal=found_goal,
        found_decision=found_decision,
        found_experience=found_experience,
        evidence_path_available=False,  # Keyword search has no graph edge awareness
        execution_time_ms=round(elapsed_ms, 2),
    )


class EvaluationComparison(BaseModel):
    """Honest side-by-side evaluation between Graph Retrieval and Keyword Baseline."""
    query: str
    expected_record_ids: List[str]
    graph_retrieved_ids: List[str]
    baseline_retrieved_ids: List[str]
    graph_precision: float
    graph_recall: float
    baseline_precision: float
    baseline_recall: float
    graph_found_decision: bool
    baseline_found_decision: bool
    graph_found_experience: bool
    baseline_found_experience: bool
    graph_has_evidence_path: bool
    baseline_has_evidence_path: bool
    graph_latency_ms: float
    baseline_latency_ms: float
    summary: str


def evaluate_graph_vs_baseline(
    user_id: str,
    query: str,
    expected_relevant_ids: Set[str],
    graph_name: Optional[str] = None,
) -> EvaluationComparison:
    """
    Compares Graph Traversal retrieval against Keyword Baseline on the same data.
    Calculates precision, recall, and causal path availability.
    """
    from app.memory.models import QueryRequest
    from app.memory.retrieval import execute_memory_query

    # 1. Run Graph Retrieval
    t0 = time.perf_counter()
    req = QueryRequest(user_id=user_id, query=query, max_results=10, max_depth=3)
    graph_resp = execute_memory_query(req, graph_name=graph_name)
    graph_time_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    graph_ids = set()
    for m in graph_resp.memories:
        graph_ids.add(m.id)
    for g in graph_resp.goals:
        graph_ids.add(g.id)
    for d in graph_resp.decisions:
        graph_ids.add(d.id)
    for e in graph_resp.experiences:
        graph_ids.add(e.id)
    for ent in graph_resp.entities:
        graph_ids.add(ent.id)

    # 2. Run Keyword Baseline
    baseline_resp = keyword_baseline_search(user_id=user_id, query=query, max_results=10, graph_name=graph_name)
    baseline_ids = {r.id for r in baseline_resp.records}

    # 3. Calculate metrics
    # Graph metrics
    graph_true_positives = len(graph_ids.intersection(expected_relevant_ids))
    graph_precision = round(graph_true_positives / len(graph_ids), 3) if graph_ids else 0.0
    graph_recall = round(graph_true_positives / len(expected_relevant_ids), 3) if expected_relevant_ids else 0.0

    # Baseline metrics
    base_true_positives = len(baseline_ids.intersection(expected_relevant_ids))
    base_precision = round(base_true_positives / len(baseline_ids), 3) if baseline_ids else 0.0
    base_recall = round(base_true_positives / len(expected_relevant_ids), 3) if expected_relevant_ids else 0.0

    has_graph_path = len(graph_resp.evidence_paths) > 0
    graph_found_dec = len(graph_resp.decisions) > 0
    graph_found_exp = len(graph_resp.experiences) > 0

    if graph_recall > base_recall:
        summary_text = (
            f"Graph retrieval achieved higher recall ({graph_recall:.1%} vs {base_recall:.1%}) "
            f"by following causal edges from Goal -> Decision -> Experience. "
            f"Keyword matching failed to connect records whose wording did not repeat the query."
        )
    elif graph_recall == base_recall and has_graph_path:
        summary_text = (
            f"Both methods found expected records, but Graph Retrieval additionally provided "
            f"{len(graph_resp.evidence_paths)} causal evidence paths explaining why the records connect."
        )
    else:
        summary_text = (
            f"Graph recall: {graph_recall:.1%}, Baseline recall: {base_recall:.1%}. "
            f"Direct keyword matches were sufficient for this query."
        )

    return EvaluationComparison(
        query=query,
        expected_record_ids=sorted(list(expected_relevant_ids)),
        graph_retrieved_ids=sorted(list(graph_ids)),
        baseline_retrieved_ids=sorted(list(baseline_ids)),
        graph_precision=graph_precision,
        graph_recall=graph_recall,
        baseline_precision=base_precision,
        baseline_recall=base_recall,
        graph_found_decision=graph_found_dec,
        baseline_found_decision=baseline_resp.found_decision,
        graph_found_experience=graph_found_exp,
        baseline_found_experience=baseline_resp.found_experience,
        graph_has_evidence_path=has_graph_path,
        baseline_has_evidence_path=False,
        graph_latency_ms=graph_time_ms,
        baseline_latency_ms=baseline_resp.execution_time_ms,
        summary=summary_text,
    )
