"""
Mai Graph Hacks — Milestone 8 Incident Investigator & Judge Demo Script

Executes an end-to-end synthetic scenario demonstrating:
1. Health and database verification
2. Structured memory ingestion (Goal -> Decision -> Experience -> Entity)
3. Idempotent re-ingestion and deduplication
4. Multi-hop relationship-aware graph retrieval with wording that does NOT match the experience
5. Provenance and evidence paths inspection
6. Fair keyword baseline comparison (precision, recall, causal paths)
7. Multi-iteration latency measurements (median, min, max)
8. Zero-evidence query handling
9. Cross-user isolation verification
10. Milestone 6 temporal memory & conflict resolution
11. Milestone 8 Engineering Incident Investigator (prior failed fixes, constraints, tasks, outcomes)
"""

import sys
import os
import time
import statistics
from typing import List, Set

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.graph.connection import check_connection
from app.graph.memory_ops import ingest_memory_payload
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
from app.memory.baseline import keyword_baseline_search, evaluate_graph_vs_baseline
from app.incident import (
    IncidentNode,
    InvestigationNode,
    FixAttemptNode,
    OutcomeNode,
    NextActionNode,
    IncidentIngestPayload,
    IncidentQueryRequest,
    UpdateTaskStatusRequest,
    RecordOutcomeRequest,
    ingest_incident_payload,
    investigate_incident,
    update_next_action_status,
    record_outcome_for_fix,
)
from app.github import (
    seed_demo_github_scenario,
    get_user_repositories_summary,
    query_github_evidence_for_incident,
)

# Terminal formatting
BOLD = "\033[1m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RED = "\033[91m"
RESET = "\033[0m"


def print_step(title: str):
    print(f"\n{BOLD}{CYAN}=== {title} ==={RESET}")


def main():
    print(f"{BOLD}{GREEN}===================================================================={RESET}")
    print(f"{BOLD}{GREEN}      Mai Graph Hacks -- Relationship-Aware Memory Demo             {RESET}")
    print(f"{BOLD}{GREEN}          Powered by FalkorDB Graph Database Engine                {RESET}")
    print(f"{BOLD}{GREEN}===================================================================={RESET}")

    total_checks = 0
    passed_checks = 0

    def assert_check(condition: bool, description: str):
        nonlocal total_checks, passed_checks
        total_checks += 1
        if condition:
            passed_checks += 1
            print(f"  {GREEN}[PASS]{RESET} {description}")
        else:
            print(f"  {RED}[FAIL]{RESET} {description}")
            print(f"\n{RED}Critical assertion failed: {description}{RESET}")
            sys.exit(1)

    # ------------------------------------------------------------------------
    # Step 1: Health & Database Probe
    # ------------------------------------------------------------------------
    print_step("Step 1: Probing FalkorDB Connectivity")
    is_connected, error_msg = check_connection()
    assert_check(is_connected is True, f"FalkorDB engine connected and responsive at 127.0.0.1:6379 (Error: {error_msg})")

    # ------------------------------------------------------------------------
    # Step 2: Define Deterministic Synthetic Scenario (Alex Rivera)
    # ------------------------------------------------------------------------
    print_step("Step 2: Preparing Synthetic Developer Memory Chain")
    user_alex = "user_alex_rivera_demo"
    user_jordan = "user_jordan_rival_demo"

    # Safely clean up only the dedicated synthetic demo user to ensure fresh demo execution
    # (Leaves all other users and existing database data completely intact)
    try:
        graph = check_connection()[0]
        if graph:
            from app.graph.connection import get_graph_client
            g_client = get_graph_client()
            cleanup_cypher = """
            MATCH (u:User {id: $user_id})
            OPTIONAL MATCH (u)-[:HAS_MEMORY]->(m:Memory)
            OPTIONAL MATCH (u)-[:HAS_GOAL]->(g:Goal)
            OPTIONAL MATCH (g)-[:MOTIVATES|MOTIVATED_BY]-(d:Decision)
            OPTIONAL MATCH (d)-[:INFORMED_BY]-(e:Experience)
            DETACH DELETE u, m, g, d, e
            """
            g_client.query(cleanup_cypher, params={"user_id": user_alex})
            g_client.query(cleanup_cypher, params={"user_id": user_jordan})
    except Exception as exc:
        print(f"  Note: Pre-run demo user check: {exc}")

    goal_id = "goal_alex_reduce_latency"
    dec_id = "dec_alex_falkordb_cypher"
    exp_id = "exp_alex_benchmark_graphblas"
    ent_id = "ent_falkordb_engine"
    mem_id = "mem_alex_architecture_note"

    # Notice: The experience text talks about 'GraphBLAS matrix traversal execution',
    # while the later query will ask about 'improving response latency for AI assistant'.
    scenario_payload = StructuredIngestPayload(
        memories=[
            StructuredMemory(
                id=mem_id,
                text="Fictional dev Alex Rivera recorded: Graph memory enables connected reasoning across episodic traces.",
                summary="Graph memory enables connected reasoning across episodic traces",
                memory_type="observation",
                confidence=1.0,
                provenance="dev_log:2026_q1",
            )
        ],
        entities=[
            StructuredEntity(
                id=ent_id,
                name="FalkorDB",
                entity_type="technology",
                description="Graph database leveraging GraphBLAS matrix algorithms",
            )
        ],
        goals=[
            StructuredGoal(
                id=goal_id,
                title="Reduce AI assistant reasoning response latency under 30ms",
                priority="critical",
            )
        ],
        decisions=[
            StructuredDecision(
                id=dec_id,
                action="Adopt FalkorDB multi-hop Cypher traversal with read-only execution",
                rationale="Causal connections avoid combinatorial vector similarity joins",
            )
        ],
        experiences=[
            StructuredExperience(
                id=exp_id,
                content="[Synthetic Illustrative Scenario] Dev team evaluation: GraphBLAS matrix traversal benchmark concept across episodic hops",
                outcome="[Illustrative Target] Target 14ms median traversal latency under path determinism (Synthetic scenario data — not an actual system benchmark measurement)",
                confidence=0.98,
            )
        ],
        relationships=[
            StructuredRelationship(source_id=mem_id, relation="MENTIONS", target_id=ent_id),
            StructuredRelationship(source_id=mem_id, relation="HAS_GOAL", target_id=goal_id),
            StructuredRelationship(source_id=goal_id, relation="MOTIVATES", target_id=dec_id),
            StructuredRelationship(source_id=dec_id, relation="INFORMED_BY", target_id=exp_id),
        ],
    )

    # ------------------------------------------------------------------------
    # Step 3: Ingest Scenario into FalkorDB
    # ------------------------------------------------------------------------
    print_step("Step 3: Ingesting Scenario into FalkorDB (Mode A)")
    ingest_res1 = ingest_memory_payload(user_id=user_alex, user_name="Alex Rivera", payload=scenario_payload)
    print(f"  Ingestion Status:        {ingest_res1.status}")
    print(f"  New Memories Stored:     {ingest_res1.memories_stored}")
    print(f"  Memories Deduplicated:   {ingest_res1.memories_deduplicated}")
    print(f"  Goals Created / Reused:  {ingest_res1.goals_created} / {ingest_res1.goals_reused}")
    print(f"  Decisions Created / Re:  {ingest_res1.decisions_created} / {ingest_res1.decisions_reused}")
    print(f"  Experiences Created/Re:  {ingest_res1.experiences_created} / {ingest_res1.experiences_reused}")
    print(f"  Entities Created / Re:   {ingest_res1.entities_created} / {ingest_res1.entities_reused}")
    print(f"  Relations Created / Re:  {ingest_res1.relationships_created} / {ingest_res1.relationships_reused}")
    assert_check(ingest_res1.success is True, "Scenario ingested successfully")
    assert_check(ingest_res1.memories_stored >= 1, "At least 1 memory stored")

    # ------------------------------------------------------------------------
    # Step 4: Verify Idempotency on Repeated Ingestion
    # ------------------------------------------------------------------------
    print_step("Step 4: Re-ingesting to Verify Idempotency & Deduplication")
    ingest_res2 = ingest_memory_payload(user_id=user_alex, user_name="Alex Rivera", payload=scenario_payload)
    print(f"  Memories Deduplicated:   {ingest_res2.memories_deduplicated}")
    print(f"  New Memories Stored:     {ingest_res2.memories_stored}")
    print(f"  Existing Goals Reused:   {ingest_res2.goals_reused}")
    print(f"  Existing Decisions Re:   {ingest_res2.decisions_reused}")
    print(f"  Existing Relations Re:   {ingest_res2.relationships_reused}")
    assert_check(ingest_res2.memories_deduplicated >= 1, "Identical memory detected and deduplicated via SHA-256 fingerprint")
    assert_check(ingest_res2.memories_stored == 0, "Zero redundant memory nodes created")
    assert_check(ingest_res2.goals_reused >= 1, "Existing goal node reused")
    assert_check(ingest_res2.relationships_reused >= 1, "Existing relationships reused")

    # ------------------------------------------------------------------------
    # Step 5: Multi-Hop Retrieval (Bridging the Semantic Gap)
    # ------------------------------------------------------------------------
    print_step("Step 5: Executing Multi-Hop Contextual Retrieval")
    # Query uses vocabulary from the Goal ('latency', 'reasoning', 'assistant'),
    # NOT the vocabulary from the Experience ('GraphBLAS', 'matrix', '10,000 hops').
    test_query = "What decisions were taken to reduce reasoning response latency for the assistant?"
    print(f"  Target Query: \"{test_query}\"")

    req = QueryRequest(user_id=user_alex, query=test_query, max_depth=3)
    query_resp = execute_memory_query(req)

    print(f"\n  Found Evidence: {query_resp.found_evidence}")
    print(f"  Context Summary: {query_resp.context_summary}")
    print("\n  Retrieved Goals:")
    for g in query_resp.goals:
        print(f"    * [{g.id}] {g.title} (Priority: {g.priority})")
    print("  Retrieved Decisions (Connected via Goal -> Decision):")
    for d in query_resp.decisions:
        print(f"    * [{d.id}] Action: {d.action}")
        print(f"      Rationale: {d.rationale}")
    print("  Retrieved Supporting Experiences (Connected via Decision -> Experience):")
    for e in query_resp.experiences:
        print(f"    * [{e.id}] Content: {e.content}")
        print(f"      Outcome: {e.outcome} (Confidence: {e.confidence})")
    print("  Evidence Paths Traversed:")
    for p in query_resp.evidence_paths:
        print(f"    -> {p}")
    print("  Provenance Records:")
    for prov in query_resp.provenance:
        print(f"    [Trace] {prov}")

    assert_check(query_resp.found_evidence is True, "Supporting evidence found in memory graph")
    assert_check(any(g.id == goal_id for g in query_resp.goals), "Target Goal retrieved")
    assert_check(any(d.id == dec_id for d in query_resp.decisions), "Connected Decision retrieved via graph traversal")
    assert_check(any(e.id == exp_id for e in query_resp.experiences), "Supporting Experience retrieved across multiple graph hops")
    assert_check(len(query_resp.evidence_paths) >= 1, "Causal evidence path returned")
    assert_check(len(query_resp.provenance) >= 1, "Provenance traces provided")

    # ------------------------------------------------------------------------
    # Step 6: Fair Keyword-Retrieval Baseline Comparison
    # ------------------------------------------------------------------------
    print_step("Step 6: Comparing Graph Retrieval vs. Fair Keyword Baseline")
    expected_ids: Set[str] = {goal_id, dec_id, exp_id}
    comparison = evaluate_graph_vs_baseline(
        user_id=user_alex,
        query=test_query,
        expected_relevant_ids=expected_ids,
    )

    print(f"  Query: \"{comparison.query}\"")
    print(f"  Expected Relevant Entities: {comparison.expected_record_ids}")
    print(f"\n  --- Graph Traversal Retrieval ---")
    print(f"  Retrieved IDs:        {comparison.graph_retrieved_ids}")
    print(f"  Precision:            {comparison.graph_precision:.1%}")
    print(f"  Recall:               {comparison.graph_recall:.1%}")
    print(f"  Found Decision:       {comparison.graph_found_decision}")
    print(f"  Found Experience:     {comparison.graph_found_experience}")
    print(f"  Has Evidence Path:    {comparison.graph_has_evidence_path}")
    print(f"  Latency:              {comparison.graph_latency_ms:.2f} ms")

    print(f"\n  --- Flat Keyword Baseline ---")
    print(f"  Retrieved IDs:        {comparison.baseline_retrieved_ids}")
    print(f"  Precision:            {comparison.baseline_precision:.1%}")
    print(f"  Recall:               {comparison.baseline_recall:.1%}")
    print(f"  Found Decision:       {comparison.baseline_found_decision}")
    print(f"  Found Experience:     {comparison.baseline_found_experience}")
    print(f"  Has Evidence Path:    {comparison.baseline_has_evidence_path}")
    print(f"  Latency:              {comparison.baseline_latency_ms:.2f} ms")

    print(f"\n  Evaluation Insight: {comparison.summary}")
    assert_check(comparison.graph_recall > comparison.baseline_recall, "Graph retrieval achieved strictly higher recall than keyword baseline by traversing causal edges")
    assert_check(comparison.graph_has_evidence_path is True, "Graph retrieval provides full relationship path")
    assert_check(comparison.baseline_has_evidence_path is False, "Keyword baseline cannot provide relational reasoning path")

    # ------------------------------------------------------------------------
    # Step 7: Performance & Latency Measurements (Multiple Iterations)
    # ------------------------------------------------------------------------
    print_step("Step 7: Honest Latency Benchmarks (Sample Size N=20)")
    sample_count = 20
    graph_latencies: List[float] = []
    baseline_latencies: List[float] = []

    for _ in range(sample_count):
        t0 = time.perf_counter()
        execute_memory_query(req)
        graph_latencies.append((time.perf_counter() - t0) * 1000.0)

        t1 = time.perf_counter()
        keyword_baseline_search(user_id=user_alex, query=test_query)
        baseline_latencies.append((time.perf_counter() - t1) * 1000.0)

    g_median = statistics.median(graph_latencies)
    g_min = min(graph_latencies)
    g_max = max(graph_latencies)

    b_median = statistics.median(baseline_latencies)
    b_min = min(baseline_latencies)
    b_max = max(baseline_latencies)

    print(f"  Graph Retrieval Latency (N={sample_count}):")
    print(f"    Median: {g_median:.2f} ms | Min: {g_min:.2f} ms | Max: {g_max:.2f} ms")
    print(f"  Keyword Baseline Latency (N={sample_count}):")
    print(f"    Median: {b_median:.2f} ms | Min: {b_min:.2f} ms | Max: {b_max:.2f} ms")
    assert_check(g_median < 250.0, f"Local graph retrieval median latency is within acceptable interactive bounds ({g_median:.2f} ms)")

    # ------------------------------------------------------------------------
    # Step 8: Demonstrate Zero-Evidence Query (No Hallucination)
    # ------------------------------------------------------------------------
    print_step("Step 8: Testing No-Evidence Query (Hallucination Prevention)")
    no_ev_query = "Cryogenic superconductor quantum teleportation"
    no_ev_req = QueryRequest(user_id=user_alex, query=no_ev_query)
    no_ev_resp = execute_memory_query(no_ev_req)

    print(f"  Query: \"{no_ev_query}\"")
    print(f"  Found Evidence: {no_ev_resp.found_evidence}")
    print(f"  Summary: {no_ev_resp.context_summary}")
    assert_check(no_ev_resp.found_evidence is False, "Explicit found_evidence=False returned when no graph facts match")
    assert_check(len(no_ev_resp.memories) == 0 and len(no_ev_resp.goals) == 0, "No phantom entities fabricated")

    # ------------------------------------------------------------------------
    # Step 9: User Isolation Verification
    # ------------------------------------------------------------------------
    print_step("Step 9: Verifying Cross-User Memory Isolation")
    # Jordan searches for Alex's private goal using the exact goal keywords
    rival_req = QueryRequest(user_id=user_jordan, query="reasoning response latency")
    rival_resp = execute_memory_query(rival_req)

    print(f"  User '{user_jordan}' querying for Alex's goal...")
    print(f"  Found Evidence for Rival: {rival_resp.found_evidence}")
    print(f"  Goals Retrieved: {len(rival_resp.goals)}")
    print(f"  Decisions Retrieved: {len(rival_resp.decisions)}")
    assert_check(rival_resp.found_evidence is False, "Second user cannot retrieve first user's private memory graph")
    assert_check(len(rival_resp.goals) == 0, "Zero goals leaked across user boundary")
    assert_check(len(rival_resp.decisions) == 0, "Zero decisions leaked across user boundary")

    # ------------------------------------------------------------------------
    # Step 10: Milestone 6 Temporal Conflict & Evidence Preservation Demo
    # ------------------------------------------------------------------------
    print_step("Step 10: Milestone 6 Temporal Memory & Conflict Resolution Demo")
    user_temporal = "user_temporal_demo_runner"

    # Ingest Statement 1: Target latency below 30 ms
    t1 = "2026-03-01T10:00:00Z"
    t2 = "2026-03-02T14:30:00Z"
    t3 = "2026-03-03T09:15:00Z"

    p1 = StructuredIngestPayload(
        memories=[StructuredMemory(id="m_lat_30ms", text="Our assistant's target response latency is below 30 ms.", summary="Target response latency below 30 ms", memory_type="goal_context", recorded_at=t1, provenance="dev_log:2026_q1")],
        goals=[StructuredGoal(id="g_lat_30ms", title="Target response latency below 30 ms", priority="high", recorded_at=t1)],
        relationships=[StructuredRelationship(source_id="m_lat_30ms", relation="HAS_GOAL", target_id="g_lat_30ms")],
    )
    r1 = ingest_memory_payload(user_id=user_temporal, payload=p1)
    assert_check(r1.success is True, "Statement 1 ingested: Target latency < 30 ms")

    # Ingest Statement 2: Changed target latency to below 20 ms
    p2 = StructuredIngestPayload(
        memories=[StructuredMemory(id="m_lat_20ms", text="We changed the target response latency to below 20 ms.", summary="Target response latency changed to below 20 ms", memory_type="goal_context", recorded_at=t2, provenance="dev_log:2026_q1_update")],
        goals=[StructuredGoal(id="g_lat_20ms", title="Target response latency below 20 ms", priority="high", recorded_at=t2)],
        relationships=[StructuredRelationship(source_id="m_lat_20ms", relation="HAS_GOAL", target_id="g_lat_20ms")],
    )
    r2 = ingest_memory_payload(user_id=user_temporal, payload=p2)
    assert_check(r2.success is True, "Statement 2 ingested: Changed target latency to < 20 ms")

    # Ingest Statement 3: Empirical benchmark 24 ms
    p3 = StructuredIngestPayload(
        memories=[StructuredMemory(id="m_bench_24ms", text="Our last benchmark measured 24 ms.", summary="Last benchmark measured 24 ms", memory_type="observation", recorded_at=t3, provenance="benchmark_run:2026_03_03")],
        experiences=[StructuredExperience(id="e_bench_24ms", content="Last benchmark execution on assistant queries", outcome="Measured 24 ms response latency", confidence=0.95, recorded_at=t3)],
        relationships=[StructuredRelationship(source_id="m_bench_24ms", relation="INFORMED_BY", target_id="e_bench_24ms")],
    )
    r3 = ingest_memory_payload(user_id=user_temporal, payload=p3)
    assert_check(r3.success is True, "Statement 3 ingested: Last benchmark measured 24 ms")

    # Query contextual retrieval across the 3 statements
    temporal_q = QueryRequest(user_id=user_temporal, query="What is the target response latency and benchmark?", max_depth=3)
    temporal_resp = execute_memory_query(temporal_q)

    assert_check(temporal_resp.found_evidence is True, "Evidence retrieved for temporal conflict query")
    assert_check(len(temporal_resp.memories) >= 3, "All three statements preserved as separate source evidence in FalkorDB (no deletions)")
    assert_check(temporal_resp.has_conflicts is True, "System detected active conflicts/updates in retrieved context")

    conflicts = temporal_resp.conflicts
    has_superseded = any(c.conflict_type == "superseded" for c in conflicts)
    assert_check(has_superseded is True, "Target change correctly identified as SUPERSEDES (goal update) rather than contradiction")

    has_mismatch = any(c.conflict_type == "target_mismatch" for c in conflicts)
    assert_check(has_mismatch is True, "Benchmark (24 ms) correctly identified as failing active 20 ms target")

    # Print Evidence Grounded Summary
    print(f"\n  {CYAN}Evidence-Grounded Retrieval Summary:{RESET}")
    print(f"  {temporal_resp.context_summary}\n")

    # ------------------------------------------------------------------------
    # Step 11: Milestone 8 Engineering Incident Investigator Demo
    # ------------------------------------------------------------------------
    print_step("Step 11: Milestone 8 Engineering Incident Investigator Demo")
    user_incident = "user_incident_cli_runner"
    t_inc_1 = "2026-03-10T08:00:00Z"
    t_inc_2 = "2026-03-10T09:30:00Z"
    t_inc_3 = "2026-03-10T10:15:00Z"

    # Ingest goals (active < 20ms supersedes < 30ms)
    goal_payload = StructuredIngestPayload(
        memories=[
            StructuredMemory(id="m_g_prior", text="Target response latency below 30 ms", summary="Target response latency below 30 ms", memory_type="goal_context", status="superseded", recorded_at=t_inc_1),
            StructuredMemory(id="m_g_active", text="Target response latency below 20 ms", summary="Target response latency below 20 ms", memory_type="goal_context", status="current", recorded_at=t_inc_2),
        ],
        goals=[
            StructuredGoal(id="g_inc_30ms", title="API response latency below 30 ms", priority="high", status="superseded", recorded_at=t_inc_1),
            StructuredGoal(id="g_inc_20ms", title="API response latency below 20 ms", priority="critical", status="current", recorded_at=t_inc_2),
        ],
        relationships=[
            StructuredRelationship(source_id="g_inc_20ms", relation="SUPERSEDES", target_id="g_inc_30ms"),
        ],
    )
    ingest_memory_payload(user_id=user_incident, payload=goal_payload)

    # Ingest prior incident with failed fix attempt
    inc_payload = IncidentIngestPayload(
        incident=IncidentNode(
            id="inc_gateway_latency_cli",
            title="Gateway API p99 latency degradation",
            symptoms="API response latency p99 degradation above 120ms during peak load",
            affected_component="gateway-api",
            severity="high",
            status="investigating",
            timestamp=t_inc_2,
            user_id=user_incident,
        ),
        investigations=[
            InvestigationNode(
                id="inv_pool_exhaustion_cli",
                incident_id="inc_gateway_latency_cli",
                hypothesis="Database connection pool exhaustion under load",
                evidence_notes="Observed queued socket connections in gateway metrics",
                investigator="SRE Team",
                timestamp=t_inc_2,
                user_id=user_incident,
            )
        ],
        fix_attempts=[
            FixAttemptNode(
                id="fix_pool_increase_cli",
                investigation_id="inv_pool_exhaustion_cli",
                description="Increased pool size from 10 to 50 connections",
                status="executed",
                timestamp=t_inc_2,
                user_id=user_incident,
            )
        ],
        outcomes=[
            OutcomeNode(
                id="out_pool_increase_fail_cli",
                fix_attempt_id="fix_pool_increase_cli",
                metric="p99_latency_ms",
                before_value="120ms",
                after_value="118ms",
                evaluation="unsuccessful",
                notes="Latency remained high at 118ms; connection pool was not the bottleneck",
                timestamp=t_inc_3,
                user_id=user_incident,
            )
        ],
        related_goals=["g_inc_20ms"],
        affected_entities=["gateway-api"],
    )
    inc_res = ingest_incident_payload(user_id=user_incident, payload=inc_payload)
    assert_check(inc_res["success"] is True, "Incident history ingested: Gateway p99 latency degradation with failed pool resize")

    # Query with DIFFERENT symptom wording:
    diff_query = "Tail timeout alerts and slow gateway response during peak traffic"
    print(f"  Target Query (Different Wording): \"{diff_query}\"")
    q_req = IncidentQueryRequest(
        user_id=user_incident,
        symptoms=diff_query,
        component="gateway-api",
        current_metric_value="115ms",
    )
    inv_res = investigate_incident(req=q_req)

    assert_check(inv_res.found_evidence is True, "FalkorDB retrieved prior investigation despite different wording")
    assert_check(len(inv_res.unsuccessful_fixes_to_avoid) >= 1, "Engine flagged connection pool resize as UNSUCCESSFUL")
    assert_check("Do NOT re-attempt" in inv_res.recommendation, "Grounded recommendation actively cautions against repeating failed fix")
    assert_check(inv_res.recommended_next_action is not None, "Persisted NextAction task created in FalkorDB with status 'pending'")

    action_id = inv_res.recommended_next_action.id
    # Update task status: pending -> in_progress
    upd_ok = update_next_action_status(
        user_id=user_incident,
        action_id=action_id,
        status="in_progress",
        resolution_notes="Investigating query execution plans and index coverage.",
    )
    assert_check(upd_ok is True, f"NextAction task '{action_id}' updated to 'in_progress'")

    # Record later measured outcome (closing feedback loop: latency 118ms -> 18ms, successful)
    out_rec = record_outcome_for_fix(
        user_id=user_incident,
        req=RecordOutcomeRequest(
            user_id=user_incident,
            action_id=action_id,
            metric="p99_latency_ms",
            before_value="118ms",
            after_value="18ms",
            evaluation="successful",
            notes="Added missing composite index on tokens table; latency resolved under 20ms target.",
        ),
    )
    assert_check(out_rec["success"] is True, "Later outcome recorded in FalkorDB (118ms -> 18ms, successful)")

    # Test explicit no-evidence abstention
    no_ev_res = investigate_incident(
        IncidentQueryRequest(
            user_id=user_incident,
            symptoms="Quantum optical phase decoherence in superconducting qubit array",
            component="quantum-array",
        )
    )
    assert_check(no_ev_res.found_evidence is False, "Explicit no-evidence abstention returned when no prior investigations exist")
    assert_check(no_ev_res.recommendation is None, "Zero phantom recommendations fabricated on no evidence")

    # Test cross-tenant isolation
    rival_inc_res = investigate_incident(
        IncidentQueryRequest(
            user_id="user_jordan_rival_demo",
            symptoms="Gateway API p99 latency degradation",
            component="gateway-api",
        )
    )
    assert_check(rival_inc_res.found_evidence is False, "Cross-tenant isolation: Tenant B cannot access Tenant A's incident history")

    # ------------------------------------------------------------------------
    # Step 12: Milestone 9 GitHub Engineering Memory Integration Demo
    # ------------------------------------------------------------------------
    print_step("Step 12: Milestone 9 GitHub Engineering Memory Integration Demo")
    user_gh = "user_github_demo_runner"

    # Seed synthetic repository history with issues, PRs, commits, workflows
    gh_res = seed_demo_github_scenario(user_id=user_gh)
    assert_check(gh_res.success is True, "GitHub repository 'gateway-corp/gateway-api' imported into FalkorDB")
    assert_check(gh_res.summary.imported_issues >= 2, "Issues imported with labels, timestamps, and authors")
    assert_check(gh_res.summary.imported_prs >= 1, "Pull requests imported with merge state and head SHA")
    assert_check(gh_res.summary.explicit_references_count >= 1, "Explicit cross-reference detected: PR #42 -> Issue #38")
    assert_check(gh_res.summary.imported_workflow_runs >= 1, "Workflow run failure captured and connected to commit")

    # Query incident investigation connecting prior failed fix and GitHub PR
    gh_inc_res = investigate_incident(
        IncidentQueryRequest(
            user_id=user_gh,
            symptoms="Tail timeout alerts and slow gateway response during peak traffic",
            component="gateway-api",
        )
    )
    assert_check(gh_inc_res.found_evidence is True, "FalkorDB retrieved connected GitHub engineering records")
    assert_check(len(gh_inc_res.retrieved_github_records) >= 2, "Both PR and Workflow failure records surfaced in investigation")

    pr_recs = [r for r in gh_inc_res.retrieved_github_records if r.get("record_type") == "pull_request"]
    assert_check(len(pr_recs) >= 1 and "https://github.com/gateway-corp/gateway-api/pull/42" in pr_recs[0]["html_url"], "Canonical GitHub PR source URL preserved")

    wf_recs = [r for r in gh_inc_res.retrieved_github_records if r.get("record_type") == "workflow_run"]
    assert_check(len(wf_recs) >= 1 and wf_recs[0]["state_or_status"] == "failure", "CI workflow load test failure correctly surfaced")

    assert_check("candidate interventions to inspect" in gh_inc_res.uncertainty_notes, "Epistemic distinction preserved: PR marked as candidate change, not proven root cause")

    # Cross-tenant isolation verification
    rival_repos = get_user_repositories_summary(user_id="user_jordan_rival_demo")
    assert_check(len(rival_repos) == 0, "Tenant isolation: Rival tenant cannot access imported GitHub repositories")

    # ------------------------------------------------------------------------
    # Final Pass/Fail Summary
    # ------------------------------------------------------------------------
    print_step("Demo Summary")
    print(f"  Total Checks Executed: {total_checks}")
    print(f"  Checks Passed:         {GREEN}{passed_checks}{RESET}")
    print(f"  Checks Failed:         {RED}0{RESET}")
    print(f"\n{BOLD}{GREEN}[OK] ALL MILESTONE 9 VERIFICATION CRITERIA PASSED SUCCESSFULLY!{RESET}\n")


if __name__ == "__main__":
    main()
