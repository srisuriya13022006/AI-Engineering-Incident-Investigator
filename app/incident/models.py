"""
Pydantic data models for Mai's Engineering Incident Investigator.

Models incidents, investigations, fix attempts, measured outcomes,
and recommended next actions with strict typing and validation.
"""

from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


IncidentSeverity = Literal["low", "medium", "high", "critical"]
FixStatus = Literal["proposed", "executed", "rolled_back"]
OutcomeEvaluation = Literal["successful", "unsuccessful", "inconclusive", "unknown"]
ActionStatus = Literal["pending", "in_progress", "completed", "abandoned"]


def generate_incident_id(prefix: str, content: str) -> str:
    """Generate deterministic ID from content string."""
    h = hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"


class IncidentNode(BaseModel):
    """An observed engineering incident or system degradation."""
    id: str
    title: str = Field(..., description="Short summary title of the incident")
    symptoms: str = Field(..., description="Observed error symptoms, log patterns, or alerts")
    affected_component: str = Field(..., description="Name of the service or subsystem affected")
    severity: IncidentSeverity = Field(default="high")
    timestamp: Optional[str] = None
    status: Literal["active", "resolved", "investigating"] = Field(default="investigating")
    user_id: Optional[str] = None

    @field_validator("id", "title", "symptoms", "affected_component")
    @classmethod
    def not_empty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


class InvestigationNode(BaseModel):
    """An analytical investigation into an incident with a specific hypothesis."""
    id: str
    incident_id: Optional[str] = None
    hypothesis: str = Field(..., description="Root cause hypothesis being investigated")
    evidence_notes: Optional[str] = Field(default=None, description="Observational notes or telemetry backing the hypothesis")
    timestamp: Optional[str] = None
    investigator: Optional[str] = None
    user_id: Optional[str] = None

    @field_validator("id", "hypothesis")
    @classmethod
    def not_empty_inv(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


class FixAttemptNode(BaseModel):
    """An intervention proposed or executed to resolve an incident."""
    id: str
    investigation_id: Optional[str] = None
    description: str = Field(..., description="Exact change or mitigation attempted")
    status: FixStatus = Field(default="executed", description="State of the intervention")
    timestamp: Optional[str] = None
    user_id: Optional[str] = None

    @field_validator("id", "description")
    @classmethod
    def not_empty_fix(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


class OutcomeNode(BaseModel):
    """An empirical before/after measurement validating a fix attempt."""
    id: str
    fix_attempt_id: Optional[str] = None
    metric: str = Field(..., description="Metric key measured (e.g. p99_latency_ms, error_rate)")
    before_value: Optional[str] = Field(default=None, description="Metric value prior to intervention")
    after_value: Optional[str] = Field(default=None, description="Metric value observed after intervention")
    evaluation: OutcomeEvaluation = Field(
        default="unknown",
        description="Whether the intervention was successful, unsuccessful, inconclusive, or unknown"
    )
    notes: Optional[str] = Field(default=None, description="Contextual observation notes")
    timestamp: Optional[str] = None
    user_id: Optional[str] = None

    @field_validator("id", "metric")
    @classmethod
    def not_empty_out(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


class NextActionNode(BaseModel):
    """A recommended follow-up investigation or task backed by graph evidence."""
    id: str
    incident_id: Optional[str] = None
    action: str = Field(..., description="Recommended action or investigation to execute")
    rationale: str = Field(..., description="Grounded explanation citing prior outcomes and constraints")
    status: ActionStatus = Field(default="pending", description="Task lifecycle status")
    evidence_references: List[str] = Field(default_factory=list, description="IDs of nodes supporting this action")
    timestamp: Optional[str] = None
    user_id: Optional[str] = None

    @field_validator("id", "action", "rationale")
    @classmethod
    def not_empty_act(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


class IncidentIngestPayload(BaseModel):
    """Structured payload for ingesting an incident with its historical chain."""
    incident: IncidentNode
    investigations: List[InvestigationNode] = Field(default_factory=list)
    fix_attempts: List[FixAttemptNode] = Field(default_factory=list)
    outcomes: List[OutcomeNode] = Field(default_factory=list)
    next_actions: List[NextActionNode] = Field(default_factory=list)
    related_goals: List[str] = Field(default_factory=list, description="IDs of Goal nodes applicable to this incident")
    affected_entities: List[str] = Field(default_factory=list, description="IDs or names of Entity nodes affected")


class IncidentIngestRequest(BaseModel):
    """API request for ingesting incident history."""
    user_id: str = Field(..., description="Tenant / user identifier for strict isolation")
    payload: IncidentIngestPayload

    @field_validator("user_id")
    @classmethod
    def valid_uid(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("user_id cannot be empty.")
        return s


class PriorInvestigationSummary(BaseModel):
    """Summary of a past investigation hop retrieved from FalkorDB."""
    incident_id: str
    incident_title: str
    symptoms: str
    component: str
    investigation_id: str
    hypothesis: str
    evidence_notes: Optional[str] = None
    fix_attempt_id: Optional[str] = None
    fix_description: Optional[str] = None
    fix_status: Optional[str] = None
    outcome_id: Optional[str] = None
    outcome_evaluation: Optional[str] = None
    outcome_metric: Optional[str] = None
    before_value: Optional[str] = None
    after_value: Optional[str] = None
    outcome_notes: Optional[str] = None


class IncidentQueryRequest(BaseModel):
    """API request for querying prior incident history and obtaining evidence-grounded next investigations."""
    user_id: str = Field(..., description="Target tenant / user identifier")
    symptoms: str = Field(..., description="Current observed incident symptoms or error messages")
    component: Optional[str] = Field(default=None, description="Optional affected component or service")
    current_metric_value: Optional[str] = Field(default=None, description="Current measured metric value (e.g. 115ms)")
    repository: Optional[str] = Field(default=None, description="Optional target repository constraint (e.g. 'owner/repo')")
    prefer_agent: bool = Field(default=True, description="Whether to invoke the autonomous LLM agent loop")

    @field_validator("user_id", "symptoms")
    @classmethod
    def not_empty_q(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


class IncidentInvestigationResponse(BaseModel):
    """Result of an incident investigation query with grounded recommendations."""
    success: bool
    user_id: str
    query_symptoms: str
    query_component: Optional[str] = None
    query_repository: Optional[str] = None
    found_evidence: bool
    insufficient_evidence_reason: Optional[str] = None
    matched_incidents: List[IncidentNode] = Field(default_factory=list)
    prior_investigations: List[PriorInvestigationSummary] = Field(default_factory=list)
    applicable_goals: List[Dict[str, Any]] = Field(default_factory=list)
    unsuccessful_fixes_to_avoid: List[str] = Field(default_factory=list)
    untested_fixes: List[str] = Field(default_factory=list)
    recommendation: Optional[str] = None
    recommendation_rationale: Optional[str] = None
    recommended_next_action: Optional[NextActionNode] = None
    uncertainty_notes: Optional[str] = None
    evidence_paths: List[str] = Field(default_factory=list)
    provenance: List[str] = Field(default_factory=list)
    retrieved_github_records: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Connected GitHub engineering records (issues, PRs, commits, workflow runs) retrieved from graph"
    )
    candidate_records: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Secondary or weak candidate records kept separate from direct evidence"
    )
    investigation_summary: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Concise structured engineering summary of the incident investigation"
    )
    graph_nodes: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Interactive graph visualization nodes"
    )
    graph_edges: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Interactive graph visualization edges"
    )
    investigation_mode: Literal["agentic", "deterministic_fallback"] = Field(
        default="deterministic_fallback",
        description="Whether the investigation was conducted by an autonomous LLM agent or deterministic fallback pipeline"
    )
    agent_execution_trace: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Step-by-step trace of agent tool selection and execution"
    )
    agent_deliberation: Optional[str] = Field(
        default=None,
        description="LLM agent synthesis and reasoning"
    )


class UpdateTaskStatusRequest(BaseModel):
    """Request to update a persisted NextAction task status."""
    user_id: str
    action_id: str
    status: ActionStatus
    resolution_notes: Optional[str] = None


class RecordOutcomeRequest(BaseModel):
    """Request to record a measured outcome for a fix attempt or task."""
    user_id: str
    fix_attempt_id: Optional[str] = None
    action_id: Optional[str] = None
    metric: str
    before_value: Optional[str] = None
    after_value: Optional[str] = None
    evaluation: OutcomeEvaluation = "unknown"
    notes: Optional[str] = None
