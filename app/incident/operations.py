"""
Graph database operations for Mai's Engineering Incident Investigator.

Implements parameterized, user-scoped Cypher queries for ingesting incidents,
traversing investigation chains (Incident -> Investigation -> FixAttempt -> Outcome),
evaluating goals, updating task statuses, and recording new outcomes.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import RedisError

from app.graph.connection import get_graph_client, FalkorDBConnectionError
from app.incident.models import (
    IncidentNode,
    InvestigationNode,
    FixAttemptNode,
    OutcomeNode,
    NextActionNode,
    IncidentIngestPayload,
    PriorInvestigationSummary,
    RecordOutcomeRequest,
)
from app.github.operations import query_github_evidence_for_incident

logger = logging.getLogger("incident.operations")

# Stop words to filter out during symptom keyword extraction
STOP_WORDS = {
    "a", "an", "the", "and", "or", "is", "are", "was", "were", "in", "on", "at",
    "to", "for", "of", "with", "by", "from", "about", "what", "which", "who",
    "how", "why", "when", "where", "my", "your", "his", "her", "their", "our",
    "do", "does", "did", "have", "has", "had", "can", "could", "should", "would",
    "we", "i", "it", "they", "this", "that", "these", "those", "reported", "observed",
}

GENERIC_INCIDENT_WORDS = {
    "api", "service", "system", "app", "application", "server", "servers",
    "traffic", "peak", "during", "latency", "timeout", "timeouts",
    "connection", "connections", "pool", "pooling", "slow", "high", "low",
    "error", "errors", "failed", "failure", "fail", "alert", "alerts",
    "request", "requests", "response", "responses", "issue", "issues",
    "bug", "bugs", "automated", "checks", "check", "run", "runs",
    "workflow", "workflows", "action", "actions", "job", "jobs", "test", "tests",
    "testing", "log", "logs", "metrics", "metric", "rate", "latest", "gateway",
}


def extract_incident_keywords(text: str) -> List[str]:
    """Extract relevant lowercase tokens from symptoms text."""
    tokens = re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", text.lower())
    keywords = [t for t in tokens if t not in STOP_WORDS]
    return keywords if keywords else tokens


def extract_domain_keywords(text: str) -> List[str]:
    """Extract non-generic domain tokens (e.g. payment, gateway, redis, quantum)."""
    tokens = re.findall(r"\b[a-zA-Z0-9_\-]{2,}\b", text.lower())
    return [t for t in tokens if t not in STOP_WORDS and t not in GENERIC_INCIDENT_WORDS]


def ingest_incident_payload(
    user_id: str,
    payload: IncidentIngestPayload,
    graph_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Idempotently stores an Incident with its connected Investigation,
    FixAttempt, Outcome, and NextAction nodes into FalkorDB under user_id.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    inc = payload.incident
    inc_id = inc.id.strip()
    inc_ts = inc.timestamp or now_iso

    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database failure connecting to FalkorDB: {exc}") from exc

    # 1. Ensure User and Incident exist
    inc_cypher = """
    MERGE (u:User {id: $user_id})
    ON CREATE SET u.created_at = $now_iso

    MERGE (inc:Incident {id: $inc_id})
    ON CREATE SET
        inc.title = $title,
        inc.symptoms = $symptoms,
        inc.affected_component = $component,
        inc.severity = $severity,
        inc.status = $status,
        inc.timestamp = $inc_ts,
        inc.user_id = $user_id
    ON MATCH SET
        inc.title = $title,
        inc.symptoms = $symptoms,
        inc.affected_component = $component,
        inc.severity = $severity,
        inc.status = $status,
        inc.updated_at = $now_iso

    MERGE (u)-[r_ui:HAS_INCIDENT]->(inc)
    ON CREATE SET r_ui.created_at = $now_iso
    """
    graph.query(
        inc_cypher,
        params={
            "user_id": user_id,
            "inc_id": inc_id,
            "title": inc.title,
            "symptoms": inc.symptoms,
            "component": inc.affected_component,
            "severity": inc.severity,
            "status": inc.status,
            "inc_ts": inc_ts,
            "now_iso": now_iso,
        },
    )

    created_counts = {
        "investigations": 0,
        "fix_attempts": 0,
        "outcomes": 0,
        "next_actions": 0,
    }

    # 2. Ingest Investigations & link (Incident)-[:INVESTIGATED_BY]->(Investigation)
    for inv in payload.investigations:
        inv_id = inv.id.strip()
        inv_ts = inv.timestamp or now_iso
        inv_cypher = """
        MATCH (inc:Incident {id: $inc_id})
        MERGE (inv:Investigation {id: $inv_id})
        ON CREATE SET
            inv.hypothesis = $hypothesis,
            inv.evidence_notes = $evidence_notes,
            inv.investigator = $investigator,
            inv.timestamp = $inv_ts,
            inv.user_id = $user_id
        ON MATCH SET
            inv.hypothesis = $hypothesis,
            inv.evidence_notes = $evidence_notes,
            inv.updated_at = $now_iso

        MERGE (inc)-[r:INVESTIGATED_BY]->(inv)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(
            inv_cypher,
            params={
                "inc_id": inc_id,
                "inv_id": inv_id,
                "hypothesis": inv.hypothesis,
                "evidence_notes": inv.evidence_notes or "",
                "investigator": inv.investigator or "",
                "inv_ts": inv_ts,
                "user_id": user_id,
                "now_iso": now_iso,
            },
        )
        created_counts["investigations"] += 1

    # 3. Ingest FixAttempts & link (Investigation)-[:LED_TO]->(FixAttempt)
    for fix in payload.fix_attempts:
        fix_id = fix.id.strip()
        fix_ts = fix.timestamp or now_iso
        inv_target_id = fix.investigation_id or (payload.investigations[0].id if payload.investigations else None)

        fix_cypher = """
        MERGE (fix:FixAttempt {id: $fix_id})
        ON CREATE SET
            fix.description = $description,
            fix.status = $status,
            fix.timestamp = $fix_ts,
            fix.user_id = $user_id
        ON MATCH SET
            fix.description = $description,
            fix.status = $status,
            fix.updated_at = $now_iso
        """
        graph.query(
            fix_cypher,
            params={
                "fix_id": fix_id,
                "description": fix.description,
                "status": fix.status,
                "fix_ts": fix_ts,
                "user_id": user_id,
                "now_iso": now_iso,
            },
        )

        if inv_target_id:
            link_cypher = """
            MATCH (inv:Investigation {id: $inv_id})
            MATCH (fix:FixAttempt {id: $fix_id})
            MERGE (inv)-[r:LED_TO]->(fix)
            ON CREATE SET r.created_at = $now_iso
            """
            graph.query(link_cypher, params={"inv_id": inv_target_id, "fix_id": fix_id, "now_iso": now_iso})

        created_counts["fix_attempts"] += 1

    # 4. Ingest Outcomes & link (FixAttempt)-[:RESULTED_IN]->(Outcome)
    for out in payload.outcomes:
        out_id = out.id.strip()
        out_ts = out.timestamp or now_iso
        fix_target_id = out.fix_attempt_id or (payload.fix_attempts[0].id if payload.fix_attempts else None)

        out_cypher = """
        MERGE (out:Outcome {id: $out_id})
        ON CREATE SET
            out.metric = $metric,
            out.before_value = $before_val,
            out.after_value = $after_val,
            out.evaluation = $evaluation,
            out.notes = $notes,
            out.timestamp = $out_ts,
            out.user_id = $user_id
        ON MATCH SET
            out.metric = $metric,
            out.before_value = $before_val,
            out.after_value = $after_val,
            out.evaluation = $evaluation,
            out.notes = $notes,
            out.updated_at = $now_iso
        """
        graph.query(
            out_cypher,
            params={
                "out_id": out_id,
                "metric": out.metric,
                "before_val": out.before_value or "",
                "after_val": out.after_value or "",
                "evaluation": out.evaluation,
                "notes": out.notes or "",
                "out_ts": out_ts,
                "user_id": user_id,
                "now_iso": now_iso,
            },
        )

        if fix_target_id:
            link_out = """
            MATCH (fix:FixAttempt {id: $fix_id})
            MATCH (out:Outcome {id: $out_id})
            MERGE (fix)-[r:RESULTED_IN]->(out)
            ON CREATE SET r.created_at = $now_iso
            """
            graph.query(link_out, params={"fix_id": fix_target_id, "out_id": out_id, "now_iso": now_iso})

        created_counts["outcomes"] += 1

    # 5. Ingest NextActions & link (Incident)-[:RECOMMENDS]->(NextAction)
    for act in payload.next_actions:
        act_id = act.id.strip()
        act_ts = act.timestamp or now_iso
        act_cypher = """
        MATCH (inc:Incident {id: $inc_id})
        MERGE (act:NextAction {id: $act_id})
        ON CREATE SET
            act.action = $action,
            act.rationale = $rationale,
            act.status = $status,
            act.timestamp = $act_ts,
            act.user_id = $user_id
        ON MATCH SET
            act.action = $action,
            act.rationale = $rationale,
            act.status = $status,
            act.updated_at = $now_iso

        MERGE (inc)-[r:RECOMMENDS]->(act)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(
            act_cypher,
            params={
                "inc_id": inc_id,
                "act_id": act_id,
                "action": act.action,
                "rationale": act.rationale,
                "status": act.status,
                "act_ts": act_ts,
                "user_id": user_id,
                "now_iso": now_iso,
            },
        )
        created_counts["next_actions"] += 1

    # 6. Link related Goals
    for g_id in payload.related_goals:
        goal_cypher = """
        MATCH (inc:Incident {id: $inc_id})
        MATCH (g:Goal {id: $g_id})
        MERGE (inc)-[r:EVALUATED_AGAINST]->(g)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(goal_cypher, params={"inc_id": inc_id, "g_id": g_id.strip(), "now_iso": now_iso})

    # 7. Link affected Entities
    for ent_name in payload.affected_entities:
        ent_id = f"ent_{ent_name.strip().lower().replace(' ', '_')}"
        ent_cypher = """
        MATCH (inc:Incident {id: $inc_id})
        MERGE (ent:Entity {id: $ent_id})
        ON CREATE SET ent.name = $ent_name, ent.entity_type = 'component'
        MERGE (inc)-[r:AFFECTS]->(ent)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(ent_cypher, params={"inc_id": inc_id, "ent_id": ent_id, "ent_name": ent_name, "now_iso": now_iso})

    return {
        "success": True,
        "incident_id": inc_id,
        "counts": created_counts,
    }


def query_incident_graph(
    user_id: str,
    symptoms: str,
    component: Optional[str] = None,
    repository: Optional[str] = None,
    graph_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Queries historical incidents, investigations, fix attempts, outcomes,
    and applicable goals strictly scoped to user_id in FalkorDB.
    Enforces domain and component relevance, preventing irrelevant cross-domain matching.
    """
    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database failure during incident query: {exc}") from exc

    keywords = extract_incident_keywords(symptoms)
    domain_kws = extract_domain_keywords(symptoms)
    comp_clean = component.strip().lower() if component else ""
    repo_clean = repository.strip().lower() if repository else ""

    is_ci_failure_query = (
        any(w in symptoms.lower() for w in ["workflow", "checks", "ci", "automated"])
        and any(w in symptoms.lower() for w in ["fail", "failed", "failure", "broken"])
    )

    incidents_map: Dict[str, IncidentNode] = {}
    investigations_list: List[PriorInvestigationSummary] = []
    actions_map: Dict[str, NextActionNode] = {}
    goals_map: Dict[str, Dict[str, Any]] = {}
    evidence_paths: List[str] = []
    provenance_traces: Set[str] = set()

    # Cypher query traversing full causal investigation path
    # Scoped strictly to (u:User {id: $user_id})-[:HAS_INCIDENT]->(inc:Incident)
    # Do not match production incidents on CI build checks queries if no incident domain words exist
    if not (is_ci_failure_query and not domain_kws and not comp_clean):
        cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_INCIDENT]->(inc:Incident)
        WHERE (
            ($comp <> "" AND toLower(inc.affected_component) = $comp)
            OR ($has_domain_kws AND any(dk IN $domain_kws WHERE toLower(inc.symptoms) CONTAINS dk OR toLower(inc.title) CONTAINS dk OR toLower(inc.affected_component) CONTAINS dk))
            OR ($allow_generic AND any(kw IN $keywords WHERE toLower(inc.symptoms) CONTAINS kw OR toLower(inc.title) CONTAINS kw))
        )
        AND ($repo_clean = "" OR $repo_clean CONTAINS toLower(inc.affected_component) OR toLower(inc.affected_component) CONTAINS $repo_clean)
        OPTIONAL MATCH (inc)-[r_ii:INVESTIGATED_BY]->(inv:Investigation)
        OPTIONAL MATCH (inv)-[r_if:LED_TO]->(fix:FixAttempt)
        OPTIONAL MATCH (fix)-[r_fo:RESULTED_IN]->(out:Outcome)
        OPTIONAL MATCH (inc)-[r_ia:RECOMMENDS]->(act:NextAction)
        OPTIONAL MATCH (inc)-[r_ig:EVALUATED_AGAINST]->(g:Goal)
        RETURN 
            inc.id AS inc_id,
            inc.title AS inc_title,
            inc.symptoms AS inc_symptoms,
            inc.affected_component AS inc_comp,
            inc.severity AS inc_sev,
            inc.status AS inc_status,
            inc.timestamp AS inc_ts,
            inv.id AS inv_id,
            inv.hypothesis AS inv_hypo,
            inv.evidence_notes AS inv_notes,
            inv.timestamp AS inv_ts,
            fix.id AS fix_id,
            fix.description AS fix_desc,
            fix.status AS fix_status,
            fix.timestamp AS fix_ts,
            out.id AS out_id,
            out.metric AS out_metric,
            out.before_value AS out_before,
            out.after_value AS out_after,
            out.evaluation AS out_eval,
            out.notes AS out_notes,
            act.id AS act_id,
            act.action AS act_action,
            act.rationale AS act_rationale,
            act.status AS act_status,
            g.id AS goal_id,
            g.title AS goal_title,
            g.priority AS goal_priority,
            g.status AS goal_status
        LIMIT 25
        """

        res = graph.ro_query(
            cypher,
            params={
                "user_id": user_id,
                "comp": comp_clean,
                "repo_clean": repo_clean,
                "has_domain_kws": bool(domain_kws),
                "domain_kws": domain_kws or [""],
                "allow_generic": bool(not domain_kws and not comp_clean),
                "keywords": keywords[:6],
            },
        )

        for row in res.result_set:
            (
                inc_id, inc_title, inc_sym, inc_comp_val, inc_sev, inc_stat, inc_ts,
                inv_id, inv_hypo, inv_notes, inv_ts,
                fix_id, fix_desc, fix_status, fix_ts,
                out_id, out_metric, out_before, out_after, out_eval, out_notes,
                act_id, act_action, act_rationale, act_status,
                g_id, g_title, g_prio, g_status
            ) = row

            if inc_id and str(inc_id) not in incidents_map:
                incidents_map[str(inc_id)] = IncidentNode(
                    id=str(inc_id),
                    title=str(inc_title or ""),
                    symptoms=str(inc_sym or ""),
                    affected_component=str(inc_comp_val or ""),
                    severity=str(inc_sev or "high"),
                    status=str(inc_stat or "investigating"),
                    timestamp=str(inc_ts or ""),
                    user_id=user_id,
                )
                provenance_traces.add(f"incident:{inc_id}")

            if inv_id:
                summary = PriorInvestigationSummary(
                    incident_id=str(inc_id),
                    incident_title=str(inc_title or ""),
                    symptoms=str(inc_sym or ""),
                    component=str(inc_comp_val or ""),
                    investigation_id=str(inv_id),
                    hypothesis=str(inv_hypo or ""),
                    evidence_notes=str(inv_notes or "") if inv_notes else None,
                    fix_attempt_id=str(fix_id) if fix_id else None,
                    fix_description=str(fix_desc or "") if fix_desc else None,
                    fix_status=str(fix_status or "executed") if fix_status else None,
                    outcome_id=str(out_id) if out_id else None,
                    outcome_evaluation=str(out_eval) if out_eval else "unknown",
                    outcome_metric=str(out_metric) if out_metric else None,
                    before_value=str(out_before) if out_before else None,
                    after_value=str(out_after) if out_after else None,
                    outcome_notes=str(out_notes) if out_notes else None,
                )
                investigations_list.append(summary)

                # Build explainable causal evidence path
                if fix_id:
                    eval_tag = f" -> Outcome('{out_eval}' on {out_metric}: {out_before} -> {out_after})" if out_id else ""
                    path_str = f"Incident({inc_id}) -[INVESTIGATED_BY]-> Investigation('{inv_hypo}') -[LED_TO]-> FixAttempt('{fix_desc}' [{fix_status}]){eval_tag}"
                else:
                    path_str = f"Incident({inc_id}) -[INVESTIGATED_BY]-> Investigation('{inv_hypo}')"

                if path_str not in evidence_paths:
                    evidence_paths.append(path_str)

                provenance_traces.add(f"investigation:{inv_id}")
                if fix_id:
                    provenance_traces.add(f"fix_attempt:{fix_id}")
                if out_id:
                    provenance_traces.add(f"outcome:{out_id}")

            if act_id and str(act_id) not in actions_map:
                actions_map[str(act_id)] = NextActionNode(
                    id=str(act_id),
                    incident_id=str(inc_id),
                    action=str(act_action or ""),
                    rationale=str(act_rationale or ""),
                    status=str(act_status or "pending"),
                    evidence_references=[str(inc_id)],
                    user_id=user_id,
                )
                evidence_paths.append(f"Incident({inc_id}) -[RECOMMENDS]-> NextAction('{act_action}' [{act_status}])")
                provenance_traces.add(f"next_action:{act_id}")

            if g_id and str(g_id) not in goals_map:
                goals_map[str(g_id)] = {
                    "id": str(g_id),
                    "title": str(g_title or ""),
                    "priority": str(g_prio or "medium"),
                    "status": str(g_status or "current"),
                }
                provenance_traces.add(f"goal:{g_id}")

    # Also retrieve active User goals to evaluate constraints
    user_goals_cypher = """
    MATCH (u:User {id: $user_id})-[:HAS_GOAL]->(g:Goal)
    RETURN g.id, g.title, g.priority, g.status
    LIMIT 10
    """
    ug_res = graph.ro_query(user_goals_cypher, params={"user_id": user_id})
    for g_id, g_title, g_prio, g_status in ug_res.result_set:
        if g_id and str(g_id) not in goals_map:
            goals_map[str(g_id)] = {
                "id": str(g_id),
                "title": str(g_title or ""),
                "priority": str(g_prio or "medium"),
                "status": str(g_status or "current"),
            }
            provenance_traces.add(f"goal:{g_id}")

    # 3. Retrieve connected GitHub repository evidence (issues, PRs, commits, workflows)
    matched_inc_comp = incidents_map[list(incidents_map.keys())[0]].affected_component if incidents_map else None
    gh_records, gh_paths, gh_traces = query_github_evidence_for_incident(
        user_id=user_id,
        keywords=keywords,
        component=component or matched_inc_comp,
        target_repo=repository,
        symptoms_text=symptoms,
        graph_name=graph_name,
    )
    for p in gh_paths:
        if p not in evidence_paths:
            evidence_paths.append(p)
    provenance_traces.update(gh_traces)

    return {
        "incidents": list(incidents_map.values()),
        "investigations": investigations_list,
        "actions": list(actions_map.values()),
        "goals": list(goals_map.values()),
        "evidence_paths": evidence_paths,
        "provenance": sorted(provenance_traces),
        "github_records": [r.model_dump() for r in gh_records],
    }


def update_next_action_status(
    user_id: str,
    action_id: str,
    status: str,
    resolution_notes: Optional[str] = None,
    graph_name: Optional[str] = None,
) -> bool:
    """Updates status and resolution notes of a NextAction task node."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database failure: {exc}") from exc

    cypher = """
    MATCH (act:NextAction {id: $act_id, user_id: $user_id})
    SET act.status = $status,
        act.resolution_notes = $notes,
        act.updated_at = $now_iso
    RETURN act.id
    """
    res = graph.query(
        cypher,
        params={
            "act_id": action_id.strip(),
            "user_id": user_id.strip(),
            "status": status,
            "notes": resolution_notes or "",
            "now_iso": now_iso,
        },
    )
    return len(res.result_set) > 0


def record_outcome_for_fix(
    user_id: str,
    req: RecordOutcomeRequest,
    graph_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Records a measured Outcome and connects it to a FixAttempt (or NextAction) in FalkorDB.
    Completes the feedback loop so future queries retrieve the empirical result.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database failure: {exc}") from exc

    out_id = f"out_{int(datetime.now().timestamp() * 1000)}"

    # If action_id is provided, convert/connect it to a FixAttempt or attach directly
    target_fix_id = req.fix_attempt_id
    if not target_fix_id and req.action_id:
        target_fix_id = f"fix_for_{req.action_id.strip()}"
        # Ensure a FixAttempt exists for this executed action
        ensure_fix = """
        MATCH (act:NextAction {id: $act_id, user_id: $user_id})
        MERGE (fix:FixAttempt {id: $fix_id})
        ON CREATE SET
            fix.description = act.action,
            fix.status = 'executed',
            fix.timestamp = $now_iso,
            fix.user_id = $user_id
        SET act.status = 'completed'
        """
        graph.query(
            ensure_fix,
            params={
                "act_id": req.action_id.strip(),
                "fix_id": target_fix_id,
                "user_id": user_id.strip(),
                "now_iso": now_iso,
            },
        )

    out_cypher = """
    MERGE (out:Outcome {id: $out_id})
    ON CREATE SET
        out.metric = $metric,
        out.before_value = $before_val,
        out.after_value = $after_val,
        out.evaluation = $evaluation,
        out.notes = $notes,
        out.timestamp = $now_iso,
        out.user_id = $user_id
    """
    graph.query(
        out_cypher,
        params={
            "out_id": out_id,
            "metric": req.metric,
            "before_val": req.before_value or "",
            "after_val": req.after_value or "",
            "evaluation": req.evaluation,
            "notes": req.notes or "",
            "user_id": user_id.strip(),
            "now_iso": now_iso,
        },
    )

    if target_fix_id:
        link_cypher = """
        MATCH (fix:FixAttempt {id: $fix_id})
        MATCH (out:Outcome {id: $out_id})
        MERGE (fix)-[r:RESULTED_IN]->(out)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(link_cypher, params={"fix_id": target_fix_id, "out_id": out_id, "now_iso": now_iso})

    return {
        "success": True,
        "outcome_id": out_id,
        "fix_attempt_id": target_fix_id,
        "evaluation": req.evaluation,
    }
