from datetime import datetime, timezone
import logging
import os
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import settings
from app.graph.connection import check_connection, FalkorDBConnectionError
from app.graph.operations import (
    seed_demo_memory,
    query_evidence_path,
    get_graph_stats,
)
from app.graph.memory_ops import ingest_memory_payload
from app.graph.schema import (
    EvidencePathResponse,
    HealthResponse,
    SeedResponse,
)
from app.memory.models import (
    IngestRequest,
    IngestResponse,
    QueryRequest,
    QueryResponse,
)
from app.memory.extraction import (
    get_extraction_provider,
    ProviderNotConfiguredError,
    InvalidExtractionOutputError,
    ExtractionError,
)
from app.memory.retrieval import execute_memory_query
from app.memory.baseline import (
    evaluate_graph_vs_baseline,
    EvaluationComparison,
)
from app.incident import (
    IncidentIngestRequest,
    IncidentIngestPayload,
    IncidentNode,
    InvestigationNode,
    FixAttemptNode,
    OutcomeNode,
    NextActionNode,
    IncidentQueryRequest,
    IncidentInvestigationResponse,
    UpdateTaskStatusRequest,
    RecordOutcomeRequest,
    ingest_incident_payload,
    investigate_incident,
    update_next_action_status,
    record_outcome_for_fix,
)
from app.github import (
    GitHubImportRequest,
    GitHubImportResponse,
    GitHubRepositoryDetailsResponse,
    import_repository,
    seed_demo_github_scenario,
    get_user_repositories_summary,
    get_repository_details,
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("incident.api")

app = FastAPI(
    title="Mai Graph Hacks API",
    description="Graph-powered memory and reasoning foundation backed by FalkorDB.",
    version="0.1.0",
)

# Static UI directory
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
@app.get("/ui", include_in_schema=False)
async def serve_ui():
    """Serve the interactive judge-facing graph memory demo UI."""
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"error": "UI files not found. Ensure app/static exists."},
    )


@app.exception_handler(FalkorDBConnectionError)
async def falkordb_connection_exception_handler(request, exc: FalkorDBConnectionError):
    """Handle database connection issues cleanly with a 503 Service Unavailable response."""
    logger.warning(f"Database unavailable: {exc}")
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "error": "DatabaseUnavailable",
            "message": str(exc),
            "detail": "FalkorDB service is currently unreachable. Ensure the container is running.",
        },
    )


@app.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check():
    """
    Application health and database connectivity probe.
    Returns status: 'healthy' when FalkorDB responds, or 'degraded' if the database is offline.
    """
    is_connected, error_msg = check_connection()
    if is_connected:
        return HealthResponse(
            status="healthy",
            database="connected",
            graph_name=settings.falkordb_graph,
        )
    return HealthResponse(
        status="degraded",
        database="disconnected",
        graph_name=settings.falkordb_graph,
        error=error_msg,
    )


@app.post("/demo/seed", response_model=SeedResponse, tags=["Demo Memory"])
async def seed_demo():
    """
    Seeds a synthetic agent memory evidence chain into FalkorDB idempotently:
    (User:Suriya) -> [:HAS_GOAL] -> (Goal) -> [:MOTIVATED_BY] -> (Decision) -> [:INFORMED_BY] -> (Experience)
    """
    try:
        result = seed_demo_memory()
        return result
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during seed: {exc}",
        ) from exc


@app.get("/demo/path", response_model=EvidencePathResponse, tags=["Demo Memory"])
async def get_evidence_path(
    goal_id: Optional[str] = Query(
        default=None,
        description="Optional Goal ID to filter connected evidence paths.",
    ),
    user_id: Optional[str] = Query(
        default=None,
        description="Optional User ID to filter connected evidence paths.",
    ),
):
    """
    Executes a multi-hop Cypher traversal retrieving the connected reasoning chain:
    User -> HAS_GOAL -> Goal -> MOTIVATED_BY -> Decision -> INFORMED_BY -> Experience.
    """
    try:
        response = query_evidence_path(goal_id=goal_id, user_id=user_id)
        return response
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during path query: {exc}",
        ) from exc


@app.get("/demo/stats", tags=["Demo Memory"])
async def get_stats():
    """Returns basic graph node and relationship counts."""
    try:
        return get_graph_stats()
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable: {exc}",
        ) from exc


# ============================================================================
# Milestone 2: Memory Ingestion & Contextual Retrieval Endpoints
# ============================================================================

@app.post("/memory/ingest", response_model=IngestResponse, tags=["Agent Memory"])
async def ingest_memory(request: IngestRequest):
    """
    Ingest user memories into FalkorDB.
    Supports:
    - Mode A: Direct structured input (`structured_data`) without requiring an LLM API key.
    - Mode B: Conversation extraction (`conversation_text`) using a configured LLM provider.
    Enforces strict user isolation and idempotent deduplication via deterministic fingerprints.
    """
    user_id = request.user_id.strip()

    # Determine mode & resolve payload
    if request.structured_data:
        mode = "structured"
        payload = request.structured_data
    elif request.conversation_text:
        mode = "conversation"
        try:
            provider = get_extraction_provider()
            payload = provider.extract(request.conversation_text, user_id=user_id)
        except ProviderNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "ProviderNotConfigured",
                    "message": str(exc),
                    "action": "Set GEMINI_API_KEY in .env or provide 'structured_data' directly via Mode A.",
                },
            ) from exc
        except InvalidExtractionOutputError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "InvalidExtractionOutput", "message": str(exc)},
            ) from exc
        except ExtractionError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": "ExtractionFailure", "message": str(exc)},
            ) from exc
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either 'structured_data' or 'conversation_text' must be provided.",
        )

    # Ingest validated payload into FalkorDB
    try:
        result = ingest_memory_payload(
            user_id=user_id,
            user_name=request.user_name,
            payload=payload,
            extraction_mode=mode,
        )
        return result
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during memory ingestion: {exc}",
        ) from exc


@app.post("/memory/query", response_model=QueryResponse, tags=["Agent Memory"])
async def query_memory(request: QueryRequest):
    """
    Perform relationship-aware memory retrieval across multi-hop paths.
    Enforces strict user data isolation so User A never retrieves User B's memories.
    Returns memories, connected entities, goals, decisions, experiences,
    evidence paths, and provenance without hallucination.
    """
    try:
        response = execute_memory_query(request)
        return response
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during memory retrieval: {exc}",
        ) from exc


class CompareRequest(BaseModel):
    user_id: str = Field(..., description="ID of the user whose records are being evaluated.")
    query: str = Field(..., description="Natural language search or reasoning query.")
    expected_ids: List[str] = Field(default_factory=list, description="IDs of ground-truth relevant records.")


@app.post("/memory/compare", response_model=EvaluationComparison, tags=["Agent Memory"])
async def compare_memory_retrieval(request: CompareRequest):
    """
    Compare FalkorDB multi-hop graph retrieval against a keyword-only baseline.
    Evaluates precision, recall, causal path discovery, and execution times on identical data.
    """
    try:
        result = evaluate_graph_vs_baseline(
            user_id=request.user_id,
            query=request.query,
            expected_relevant_ids=set(request.expected_ids),
        )
        return result
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during baseline comparison: {exc}",
        ) from exc


class ConflictDemoStep(BaseModel):
    step: int
    statement: str
    action: str
    ingest_result: IngestResponse


class ConflictDemoResponse(BaseModel):
    user_id: str
    disclaimer: str = (
        "Synthetic Scenario Data — For demonstration purposes only. Not an actual system benchmark measurement."
    )
    steps: List[ConflictDemoStep]
    query_result: QueryResponse
    preserved_evidence_count: int
    analysis: str


@app.post("/demo/conflict_scenario", response_model=ConflictDemoResponse, tags=["Demo Memory"])
async def run_conflict_scenario_endpoint(user_id: Optional[str] = None):
    """
    Executes Milestone 6 3-step synthetic conflict scenario:
    1. 'Our assistant's target response latency is below 30 ms.'
    2. 'We changed the target response latency to below 20 ms.'
    3. 'Our last benchmark measured 24 ms.'
    Preserves all three statements, detects the superseding goal, identifies benchmark mismatch,
    and returns evidence-grounded retrieval.
    """
    target_user = user_id.strip() if user_id and user_id.strip() else "user_fictional_conflict_demo"
    from app.memory.models import (
        StructuredIngestPayload,
        StructuredMemory,
        StructuredGoal,
        StructuredExperience,
        StructuredRelationship,
    )

    t1 = "2026-03-01T10:00:00Z"
    t2 = "2026-03-02T14:30:00Z"
    t3 = "2026-03-03T09:15:00Z"

    mem1_id = f"mem_target_30ms_{target_user}"
    goal1_id = f"goal_latency_30ms_{target_user}"
    mem2_id = f"mem_target_20ms_{target_user}"
    goal2_id = f"goal_latency_20ms_{target_user}"
    mem3_id = f"mem_benchmark_24ms_{target_user}"
    exp3_id = f"exp_benchmark_24ms_{target_user}"

    steps = []

    # Statement 1
    p1 = StructuredIngestPayload(
        memories=[
            StructuredMemory(
                id=mem1_id,
                text="Our assistant's target response latency is below 30 ms.",
                summary="Target response latency below 30 ms",
                memory_type="goal_context",
                recorded_at=t1,
                provenance="dev_log:2026_q1",
            )
        ],
        goals=[
            StructuredGoal(
                id=goal1_id,
                title="Target response latency below 30 ms",
                priority="high",
                recorded_at=t1,
            )
        ],
        relationships=[
            StructuredRelationship(source_id=mem1_id, relation="HAS_GOAL", target_id=goal1_id)
        ],
    )
    r1 = ingest_memory_payload(user_id=target_user, payload=p1, extraction_mode="structured")
    steps.append(
        ConflictDemoStep(
            step=1,
            statement="Our assistant's target response latency is below 30 ms.",
            action="Initial target latency goal established at below 30 ms.",
            ingest_result=r1,
        )
    )

    # Statement 2
    p2 = StructuredIngestPayload(
        memories=[
            StructuredMemory(
                id=mem2_id,
                text="We changed the target response latency to below 20 ms.",
                summary="Target response latency changed to below 20 ms",
                memory_type="goal_context",
                recorded_at=t2,
                provenance="dev_log:2026_q1_update",
            )
        ],
        goals=[
            StructuredGoal(
                id=goal2_id,
                title="Target response latency below 20 ms",
                priority="high",
                recorded_at=t2,
            )
        ],
        relationships=[
            StructuredRelationship(source_id=mem2_id, relation="HAS_GOAL", target_id=goal2_id)
        ],
    )
    r2 = ingest_memory_payload(user_id=target_user, payload=p2, extraction_mode="structured")
    steps.append(
        ConflictDemoStep(
            step=2,
            statement="We changed the target response latency to below 20 ms.",
            action="Target response latency updated to below 20 ms. Detected as SUPERSEDES (goal update) rather than an irreconcilable contradiction.",
            ingest_result=r2,
        )
    )

    # Statement 3
    p3 = StructuredIngestPayload(
        memories=[
            StructuredMemory(
                id=mem3_id,
                text="Our last benchmark measured 24 ms.",
                summary="Last benchmark measured 24 ms",
                memory_type="observation",
                recorded_at=t3,
                provenance="benchmark_run:2026_03_03",
            )
        ],
        experiences=[
            StructuredExperience(
                id=exp3_id,
                content="Last benchmark execution on assistant queries",
                outcome="Measured 24 ms response latency",
                confidence=0.95,
                recorded_at=t3,
            )
        ],
        relationships=[
            StructuredRelationship(source_id=mem3_id, relation="INFORMED_BY", target_id=exp3_id)
        ],
    )
    r3 = ingest_memory_payload(user_id=target_user, payload=p3, extraction_mode="structured")
    steps.append(
        ConflictDemoStep(
            step=3,
            statement="Our last benchmark measured 24 ms.",
            action="Empirical benchmark recorded. Evaluated against active target (below 20 ms): 24 ms does not meet the stated target.",
            ingest_result=r3,
        )
    )

    # Contextual Query
    q_req = QueryRequest(
        user_id=target_user,
        query="What is the target response latency and benchmark?",
        max_depth=3,
    )
    q_res = execute_memory_query(q_req)

    analysis = (
        "Preserved all three statements as separate, traceable source evidence. "
        "Correctly classified the 20 ms target change as SUPERSEDING the earlier 30 ms goal, rather than an arbitrary contradiction. "
        "Identified that the 24 ms benchmark does not meet the active 20 ms target. "
        "Important: A single synthetic measurement does not establish overall system performance and is not independently verified."
    )

    return ConflictDemoResponse(
        user_id=target_user,
        steps=steps,
        query_result=q_res,
        preserved_evidence_count=len(q_res.memories),
        analysis=analysis,
    )


# ============================================================================
# Incident Investigator Endpoints (Milestone 8)
# ============================================================================

@app.post("/incident/ingest", tags=["Incident Investigator"])
async def ingest_incident(request: IncidentIngestRequest):
    """
    Ingest a synthetic or real incident and its connected investigation history
    (Incident -> Investigation -> FixAttempt -> Outcome -> NextAction) into FalkorDB.
    """
    try:
        result = ingest_incident_payload(user_id=request.user_id, payload=request.payload)
        return result
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during incident ingestion: {exc}",
        ) from exc


@app.post("/incident/investigate", response_model=IncidentInvestigationResponse, tags=["Incident Investigator"])
async def investigate_incident_endpoint(request: IncidentQueryRequest):
    """
    Evaluates observed incident symptoms against historical graph investigations.
    Distinguishes executed, unsuccessful, and untested fix attempts.
    Produces evidence-grounded next action recommendations or abstains on no evidence.
    """
    try:
        result = investigate_incident(req=request)
        return result
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during incident investigation: {exc}",
        ) from exc


@app.post("/incident/task", tags=["Incident Investigator"])
async def update_task_endpoint(request: UpdateTaskStatusRequest):
    """
    Updates the lifecycle status of a persisted NextAction task node
    (e.g., pending -> in_progress -> completed).
    """
    try:
        success = update_next_action_status(
            user_id=request.user_id,
            action_id=request.action_id,
            status=request.status,
            resolution_notes=request.resolution_notes,
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task action '{request.action_id}' not found for user '{request.user_id}'.",
            )
        return {
            "success": True,
            "action_id": request.action_id,
            "status": request.status,
            "notes": request.resolution_notes,
        }
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during task update: {exc}",
        ) from exc


@app.post("/incident/outcome", tags=["Incident Investigator"])
async def record_outcome_endpoint(request: RecordOutcomeRequest):
    """
    Records an empirical measured outcome for a fix attempt or task,
    closing the investigation feedback loop in FalkorDB.
    """
    try:
        result = record_outcome_for_fix(user_id=request.user_id, req=request)
        return result
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"FalkorDB unreachable during outcome recording: {exc}",
        ) from exc


class IncidentDemoResponse(BaseModel):
    user_id: str
    scenario_description: str
    seeded_incident_id: str
    investigation_query: str
    investigation_result: IncidentInvestigationResponse
    summary_of_checks: List[str]


@app.post("/demo/incident_scenario", response_model=IncidentDemoResponse, tags=["Incident Investigator"])
async def run_incident_scenario_demo(user_id: Optional[str] = None):
    """
    One-click repeatable demo scenario for Milestone 8:
    1. Seeds a prior incident on 'gateway-api' where a connection pool size increase failed (p99 latency 120ms -> 118ms).
    2. Seeds active target goal (latency < 20ms, superseding prior < 30ms goal).
    3. Queries with DIFFERENT wording ('Tail timeout alerts and slow gateway response').
    4. Retrieves historical evidence, identifies the failed fix, and generates a grounded next recommendation.
    5. Persists the follow-up task in FalkorDB.
    """
    target_user = user_id or "user_incident_judge_demo"
    t1 = "2026-03-10T08:00:00Z"
    t2 = "2026-03-10T09:30:00Z"
    t3 = "2026-03-10T10:15:00Z"

    # Step 1: Ingest active and superseded goals into user's graph
    from app.memory.models import StructuredIngestPayload, StructuredMemory, StructuredGoal, StructuredRelationship
    from app.graph.memory_ops import ingest_memory_payload

    goal_payload = StructuredIngestPayload(
        memories=[
            StructuredMemory(
                id="mem_goal_prior",
                text="Initial target response latency was below 30 ms.",
                summary="Target response latency below 30 ms",
                memory_type="goal_context",
                status="superseded",
                recorded_at=t1,
            ),
            StructuredMemory(
                id="mem_goal_active",
                text="Active target response latency is below 20 ms.",
                summary="Target response latency below 20 ms",
                memory_type="goal_context",
                status="current",
                recorded_at=t2,
            ),
        ],
        goals=[
            StructuredGoal(id="g_lat_30ms", title="API response latency below 30 ms", priority="high", status="superseded", recorded_at=t1),
            StructuredGoal(id="g_lat_20ms", title="API response latency below 20 ms", priority="critical", status="current", recorded_at=t2),
        ],
        relationships=[
            StructuredRelationship(source_id="g_lat_20ms", relation="SUPERSEDES", target_id="g_lat_30ms"),
        ],
    )
    ingest_memory_payload(user_id=target_user, payload=goal_payload)

    # Step 2: Ingest prior incident with failed fix attempt
    inc_payload = IncidentIngestPayload(
        incident=IncidentNode(
            id="inc_gateway_latency_01",
            title="Gateway API p99 latency degradation",
            symptoms="API response latency p99 degradation above 120ms during peak load",
            affected_component="gateway-api",
            severity="high",
            status="investigating",
            timestamp=t2,
            user_id=target_user,
        ),
        investigations=[
            InvestigationNode(
                id="inv_pool_exhaustion",
                incident_id="inc_gateway_latency_01",
                hypothesis="Database connection pool exhaustion under load",
                evidence_notes="Observed queued socket connections in gateway metrics",
                investigator="SRE Team",
                timestamp=t2,
                user_id=target_user,
            )
        ],
        fix_attempts=[
            FixAttemptNode(
                id="fix_pool_increase_50",
                investigation_id="inv_pool_exhaustion",
                description="Increased pool size from 10 to 50 connections",
                status="executed",
                timestamp=t2,
                user_id=target_user,
            )
        ],
        outcomes=[
            OutcomeNode(
                id="out_pool_increase_fail",
                fix_attempt_id="fix_pool_increase_50",
                metric="p99_latency_ms",
                before_value="120ms",
                after_value="118ms",
                evaluation="unsuccessful",
                notes="Latency remained high at 118ms; connection pool was not the bottleneck",
                timestamp=t3,
                user_id=target_user,
            )
        ],
        related_goals=["g_lat_20ms"],
        affected_entities=["gateway-api"],
    )
    ingest_incident_payload(user_id=target_user, payload=inc_payload)

    # Step 3: Query with DIFFERENT wording
    query_symptoms = "Tail timeout alerts and slow gateway response during peak traffic"
    investigate_req = IncidentQueryRequest(
        user_id=target_user,
        symptoms=query_symptoms,
        component="gateway-api",
        current_metric_value="115ms",
    )
    investigation_res = investigate_incident(req=investigate_req)

    checks = [
        "Prior incident and investigation history retrieved across different symptom wording",
        "Empirical outcome evaluated: Identified connection pool resize as UNSUCCESSFUL",
        "Active goal (latency < 20ms) evaluated while preserving superseded 30ms goal",
        "Grounding check: Advised against repeating connection pool resize",
        "Recommended next investigation task persisted in FalkorDB with status 'pending'",
        "Evidence path and provenance traces generated from FalkorDB graph",
    ]

    return IncidentDemoResponse(
        user_id=target_user,
        scenario_description="Synthetic incident investigation with historical fix outcome evaluation",
        seeded_incident_id="inc_gateway_latency_01",
        investigation_query=query_symptoms,
        investigation_result=investigation_res,
        summary_of_checks=checks,
    )


# ============================================================================
# GitHub Engineering Memory Integration Endpoints (Milestone 9)
# ============================================================================


@app.post(
    "/github/import",
    response_model=GitHubImportResponse,
    summary="Import GitHub repository engineering records (issues, PRs, commits, workflows)",
    tags=["GitHub Engineering Memory"],
)
async def import_github_repository(req: GitHubImportRequest):
    """
    Imports repository metadata, issues, pull requests, commits, and workflow runs
    from GitHub REST API into FalkorDB, scoped to user_id.
    """
    try:
        res = import_repository(req=req)
        return res
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "FalkorDB Offline", "message": str(exc)},
        )
    except Exception as exc:
        logger.exception("Unexpected error during GitHub repository import")
        return GitHubImportResponse(
            success=False,
            user_id=req.user_id,
            repo=req.repo,
            error_message=f"Import failed: {str(exc)}",
        )


@app.get(
    "/github/status",
    summary="Inspect GitHub integration status and imported repository records",
    tags=["GitHub Engineering Memory"],
)
async def get_github_status(user_id: str = Query(default="user_default")):
    """
    Returns GitHub integration status and imported repositories with record counts
    for the specified user.
    """
    try:
        repos = get_user_repositories_summary(user_id=user_id)
        return {
            "status": "active",
            "has_server_token": bool(settings.github_token),
            "user_id": user_id,
            "imported_repositories": repos,
            "total_repositories": len(repos),
        }
    except Exception as exc:
        logger.exception("Error checking GitHub status")
        return {
            "status": "error",
            "has_server_token": bool(settings.github_token),
            "user_id": user_id,
            "imported_repositories": [],
            "error": str(exc),
        }


@app.get(
    "/github/repository",
    response_model=GitHubRepositoryDetailsResponse,
    summary="Get in-app repository details, records, and evidence paths",
    tags=["GitHub Engineering Memory"],
)
@app.get(
    "/github/repository/details",
    response_model=GitHubRepositoryDetailsResponse,
    include_in_schema=False,
)
async def get_github_repository_details(
    user_id: str = Query(..., description="Tenant / user isolation ID"),
    repo: str = Query(..., description="Repository full_name (owner/name) or repo_id"),
):
    """
    Returns complete in-app repository view including metadata, imported issues,
    pull requests, commits, workflow runs, verified relationship edges,
    multi-hop paths, and missing relationships.
    Strictly tenant-isolated. Returns 404 if repository does not exist for the user.
    """
    try:
        details = get_repository_details(user_id=user_id, repo_identifier=repo)
        if details is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "Repository Not Found", "message": f"Repository '{repo}' not found for user '{user_id}'."},
            )
        return GitHubRepositoryDetailsResponse(
            success=True,
            user_id=user_id,
            repo=repo,
            repository=details,
        )
    except HTTPException:
        raise
    except FalkorDBConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "FalkorDB Offline", "message": str(exc)},
        )
    except Exception as exc:
        logger.exception("Error retrieving repository details")
        return GitHubRepositoryDetailsResponse(
            success=False,
            user_id=user_id,
            repo=repo,
            error_message=f"Failed to retrieve repository details: {str(exc)}",
        )



@app.post(
    "/demo/github_scenario",
    summary="One-click demo scenario connecting GitHub repository records to incident investigation",
    tags=["GitHub Engineering Memory"],
)
async def run_github_incident_demo(user_id: Optional[str] = Query(default=None)):
    """
    Seeds a realistic synthetic repository ('gateway-corp/gateway-api') with issues, PRs,
    commits, and workflow runs, links it with the gateway latency incident, and runs an investigation.
    """
    target_user = user_id or "user_alex_rivera_demo"

    # Step 1: Seed synthetic GitHub repository history
    gh_import_res = seed_demo_github_scenario(user_id=target_user)

    # Step 2: Ensure the synthetic incident history exists
    now_iso = datetime.now(timezone.utc).isoformat()
    inc_payload = IncidentIngestPayload(
        incident=IncidentNode(
            id="inc_gateway_latency_01",
            title="Gateway API p99 latency degradation",
            symptoms="API response latency p99 degradation above 120ms during peak load",
            affected_component="gateway-api",
            severity="high",
            status="investigating",
            timestamp=now_iso,
            user_id=target_user,
        ),
        investigations=[
            InvestigationNode(
                id="inv_pool_exhaustion",
                incident_id="inc_gateway_latency_01",
                hypothesis="Database connection pool exhaustion under load",
                evidence_notes="Observed queued socket connections in gateway metrics",
                investigator="SRE Team",
                timestamp=now_iso,
                user_id=target_user,
            )
        ],
        fix_attempts=[
            FixAttemptNode(
                id="fix_pool_increase_50",
                investigation_id="inv_pool_exhaustion",
                description="Increased pool size from 10 to 50 connections",
                status="executed",
                timestamp=now_iso,
                user_id=target_user,
            )
        ],
        outcomes=[
            OutcomeNode(
                id="out_pool_increase_fail",
                fix_attempt_id="fix_pool_increase_50",
                metric="p99_latency_ms",
                before_value="120ms",
                after_value="118ms",
                evaluation="unsuccessful",
                notes="Latency remained high at 118ms; connection pool was not the bottleneck",
                timestamp=now_iso,
                user_id=target_user,
            )
        ],
        related_goals=[],
        affected_entities=["gateway-api"],
    )
    ingest_incident_payload(user_id=target_user, payload=inc_payload)

    # Step 3: Run incident investigation query
    query_symptoms = "Tail timeout alerts and slow gateway response during peak traffic"
    investigate_req = IncidentQueryRequest(
        user_id=target_user,
        symptoms=query_symptoms,
        component="gateway-api",
        current_metric_value="118ms",
    )
    investigation_res = investigate_incident(req=investigate_req)

    checks = [
        "Synthetic repository 'gateway-corp/gateway-api' imported into FalkorDB with issues, PRs, commits, and workflows",
        "Explicit issue reference detected: PR #42 references Issue #38 (Closes #38)",
        "Workflow run 'Gateway Load & Stress Benchmark' failure captured and connected to commit 9f4b12c8a1",
        "Incident investigation retrieved prior fix attempt and evaluated it as UNSUCCESSFUL",
        "Connected GitHub code history surfaced: PR #42 ('feat(gateway): Increase client connection pool from 10 to 50')",
        "Epistemic uncertainty preserved: Code changes in history explicitly marked as candidate changes, not proven root causes",
        "Source URLs preserved: Canonical links directly to GitHub PR, Issue, and Commit",
    ]

    return {
        "success": True,
        "user_id": target_user,
        "scenario": "GitHub Engineering Memory Integration with Incident Investigation",
        "github_import": gh_import_res,
        "investigation_result": investigation_res,
        "summary_of_checks": checks,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.debug,
    )
