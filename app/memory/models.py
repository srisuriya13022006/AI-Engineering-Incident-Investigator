from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, List, Literal, Optional, Set
from pydantic import BaseModel, Field, field_validator, model_validator


# Strict allowlist of permitted relationship types to prevent Cypher injection
ALLOWLISTED_RELATIONSHIPS: Set[str] = {
    "HAS_MEMORY",
    "MENTIONS",
    "HAS_GOAL",
    "MOTIVATES",
    "MOTIVATED_BY",
    "INFORMED_BY",
    "RELATED_TO",
    "CONTRADICTS",
    "SUPERSEDES",
    "SUPPORTED_BY",
    # Incident Investigator relationship types
    "HAS_INCIDENT",
    "INVESTIGATED_BY",
    "LED_TO",
    "RESULTED_IN",
    "RECOMMENDS",
    "AFFECTS",
    "EVALUATED_AGAINST",
    "SIMILAR_TO",
    "RESOLVED_BY",
    # GitHub Engineering Memory relationship types (Milestone 9)
    "HAS_REPOSITORY",
    "CONTAINS_ISSUE",
    "HAS_PULL_REQUEST",
    "CONTAINS_COMMIT",
    "HAS_WORKFLOW_RUN",
    "REFERENCES_ISSUE",
    "RESOLVES_ISSUE",
    "ASSOCIATED_WITH_INCIDENT",
    "MODIFIES_COMPONENT",
    "TRIGGERED_BY",
    "TESTS_PULL_REQUEST",
    "DEPENDS_ON",
    "CROSS_REPO_REF",
}

# Permitted memory types
MEMORY_TYPES = Literal["fact", "preference", "episodic", "observation", "goal_context", "decision_context"]

# Permitted temporal and claim statuses
CLAIM_STATUS = Literal["current", "historical", "superseded", "disputed", "unknown"]


def generate_deterministic_id(prefix: str, content: str) -> str:
    """Generate a stable, deterministic ID from a content string."""
    h = hashlib.sha256(content.strip().lower().encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{h}"


def generate_memory_fingerprint(user_id: str, summary: str, memory_type: str) -> str:
    """Generate a deterministic fingerprint for duplicate memory detection per user."""
    raw = f"{user_id.strip()}::{memory_type.strip()}::{summary.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class StructuredMemory(BaseModel):
    """Normalized memory unit representing a fact, preference, or observation."""
    id: Optional[str] = None
    text: str = Field(..., description="Original raw text or statement representing the memory")
    summary: str = Field(..., description="Normalized factual summary")
    memory_type: MEMORY_TYPES = Field(default="fact", description="Type classification of memory")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    provenance: str = Field(default="structured_input", description="Source or message provenance")
    created_at: Optional[str] = None
    recorded_at: Optional[str] = None
    valid_at: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    status: CLAIM_STATUS = Field(default="current", description="Temporal / epistemological claim status")
    source_memory_id: Optional[str] = None
    user_id: Optional[str] = None

    @field_validator("text", "summary")
    @classmethod
    def not_empty(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field must not be empty or whitespace only.")
        return s


class StructuredEntity(BaseModel):
    """Named entity mentioned in a memory or conversation."""
    id: Optional[str] = None
    name: str = Field(..., description="Unique name or title of the entity")
    entity_type: str = Field(default="concept", description="Entity category: technology, person, location, concept, etc.")
    description: Optional[str] = Field(default=None, description="Brief contextual description")

    @field_validator("name")
    @classmethod
    def not_empty_name(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Entity name must not be empty.")
        return s


class StructuredGoal(BaseModel):
    """Agent or user goal."""
    id: Optional[str] = None
    title: str = Field(..., description="Goal title or objective statement")
    priority: Literal["low", "medium", "high", "critical"] = Field(default="medium")
    recorded_at: Optional[str] = None
    valid_at: Optional[str] = None
    status: CLAIM_STATUS = Field(default="current", description="Status of goal: current or superseded")
    source_memory_id: Optional[str] = None

    @field_validator("title")
    @classmethod
    def not_empty_title(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Goal title must not be empty.")
        return s


class StructuredDecision(BaseModel):
    """Decision made towards a goal."""
    id: Optional[str] = None
    action: str = Field(..., description="The action chosen or taken")
    rationale: str = Field(..., description="Reasoning or motivation for the action")
    recorded_at: Optional[str] = None
    valid_at: Optional[str] = None
    status: CLAIM_STATUS = Field(default="current", description="Status of decision")
    source_memory_id: Optional[str] = None

    @field_validator("action", "rationale")
    @classmethod
    def not_empty_fields(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Decision action and rationale must not be empty.")
        return s


class StructuredExperience(BaseModel):
    """Empirical experience or observation supporting a decision."""
    id: Optional[str] = None
    content: str = Field(..., description="Observed event or empirical experiment")
    outcome: str = Field(..., description="Result or outcome of the experience")
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    recorded_at: Optional[str] = None
    valid_at: Optional[str] = None
    status: CLAIM_STATUS = Field(default="current", description="Status of experience")
    source_memory_id: Optional[str] = None

    @field_validator("content", "outcome")
    @classmethod
    def not_empty_exp(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Experience content and outcome must not be empty.")
        return s


class StructuredRelationship(BaseModel):
    """Validated directed relationship between two entities or memories."""
    source_id: str
    relation: str
    target_id: str
    properties: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("relation")
    @classmethod
    def validate_relation(cls, v: str) -> str:
        upper_v = v.strip().upper()
        if upper_v not in ALLOWLISTED_RELATIONSHIPS:
            allowed = ", ".join(sorted(ALLOWLISTED_RELATIONSHIPS))
            raise ValueError(
                f"Relationship '{v}' is not permitted. Must be one of allowlist: [{allowed}]"
            )
        return upper_v

    @field_validator("source_id", "target_id")
    @classmethod
    def not_empty_ids(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("source_id and target_id must not be empty.")
        return s


class StructuredIngestPayload(BaseModel):
    """Complete structured memory ingestion payload (Mode A)."""
    memories: List[StructuredMemory] = Field(default_factory=list)
    entities: List[StructuredEntity] = Field(default_factory=list)
    goals: List[StructuredGoal] = Field(default_factory=list)
    decisions: List[StructuredDecision] = Field(default_factory=list)
    experiences: List[StructuredExperience] = Field(default_factory=list)
    relationships: List[StructuredRelationship] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_has_content(self):
        total_items = (
            len(self.memories)
            + len(self.entities)
            + len(self.goals)
            + len(self.decisions)
            + len(self.experiences)
        )
        if total_items == 0:
            raise ValueError("Payload must contain at least one memory, entity, goal, decision, or experience.")
        return self


class IngestRequest(BaseModel):
    """API request for memory ingestion (supports Mode A structured and Mode B conversation)."""
    user_id: str = Field(..., description="User ID owning the memories for strict isolation")
    user_name: Optional[str] = Field(default=None, description="Optional display name for the user")
    conversation_text: Optional[str] = Field(
        default=None,
        description="Mode B: Raw conversation or message to extract memories from using configured LLM provider",
    )
    structured_data: Optional[StructuredIngestPayload] = Field(
        default=None,
        description="Mode A: Pre-extracted structured facts, entities, and relationships",
    )

    @field_validator("user_id")
    @classmethod
    def validate_user_id(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("user_id cannot be empty.")
        return s

    @model_validator(mode="after")
    def validate_mode_exclusivity(self):
        if not self.conversation_text and not self.structured_data:
            raise ValueError("Either 'structured_data' (Mode A) or 'conversation_text' (Mode B) must be provided.")
        return self


class IngestResponse(BaseModel):
    """Response returned after memory ingestion, detailing created vs reused elements."""
    success: bool
    status: str
    user_id: str
    extraction_mode: Literal["structured", "conversation"]
    memory_ids: List[str] = Field(default_factory=list)
    memories_stored: int = 0
    memories_deduplicated: int = 0
    # Detailed entity counts
    entities_created: int = 0
    entities_reused: int = 0
    entities_processed: int = 0
    # Detailed goal counts
    goals_created: int = 0
    goals_reused: int = 0
    goals_processed: int = 0
    # Detailed decision counts
    decisions_created: int = 0
    decisions_reused: int = 0
    decisions_processed: int = 0
    # Detailed experience counts
    experiences_created: int = 0
    experiences_reused: int = 0
    experiences_processed: int = 0
    # Detailed relationship counts
    relationships_created: int = 0
    relationships_reused: int = 0
    relationships_processed: int = 0
    extracted_data: Optional[StructuredIngestPayload] = None
    warnings: List[str] = Field(default_factory=list)
    errors: Optional[List[str]] = None


# --- Query Models ---

class QueryRequest(BaseModel):
    """API request for relationship-aware memory retrieval."""
    user_id: str = Field(..., description="Target user ID to enforce strict data isolation")
    query: str = Field(..., description="Natural language query or retrieval keywords")
    max_results: int = Field(default=5, ge=1, le=50, description="Maximum number of memory items to return")
    max_depth: int = Field(default=2, ge=1, le=3, description="Bounded multi-hop graph traversal depth (1-3)")

    @field_validator("user_id", "query")
    @classmethod
    def not_empty_query(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("Field must not be empty.")
        return s


class RetrievedMemory(BaseModel):
    """Memory retrieved from graph with provenance."""
    id: str
    summary: str
    text: str
    memory_type: str
    confidence: float
    created_at: str
    provenance: str
    fingerprint: Optional[str] = None
    recorded_at: Optional[str] = None
    valid_at: Optional[str] = None
    status: Optional[str] = "current"


class RetrievedEntity(BaseModel):
    id: str
    name: str
    entity_type: str
    description: Optional[str] = None


class RetrievedGoal(BaseModel):
    id: str
    title: str
    priority: str
    status: Optional[str] = "current"
    recorded_at: Optional[str] = None
    valid_at: Optional[str] = None


class RetrievedDecision(BaseModel):
    id: str
    action: str
    rationale: str
    status: Optional[str] = "current"
    recorded_at: Optional[str] = None
    valid_at: Optional[str] = None


class RetrievedExperience(BaseModel):
    id: str
    content: str
    outcome: str
    confidence: float
    status: Optional[str] = "current"
    recorded_at: Optional[str] = None
    valid_at: Optional[str] = None


class ConflictDetail(BaseModel):
    """Detailed record of an identified conflict, superseding update, or mismatch."""
    subject: str = Field(..., description="Subject or property evaluated for conflict")
    conflict_type: Literal["contradiction", "superseded", "target_mismatch", "compatible", "uncertain"] = Field(
        ..., description="Classification of the conflict or temporal update"
    )
    original_claim: str = Field(..., description="The original or earlier claim")
    original_id: Optional[str] = None
    original_timestamp: Optional[str] = None
    new_claim: str = Field(..., description="The newer or updating claim")
    new_id: Optional[str] = None
    new_timestamp: Optional[str] = None
    reason: str = Field(..., description="Explicit rationale for the classification")
    classification_certainty: Literal["deterministic", "uncertain"] = Field(
        default="deterministic", description="Whether classification is deterministic or uncertain"
    )
    source_memory_ids: List[str] = Field(default_factory=list)


class QueryResponse(BaseModel):
    """API response for relationship-aware memory query."""
    success: bool
    user_id: str
    query: str
    found_evidence: bool
    context_summary: str
    memories: List[RetrievedMemory] = Field(default_factory=list)
    entities: List[RetrievedEntity] = Field(default_factory=list)
    goals: List[RetrievedGoal] = Field(default_factory=list)
    decisions: List[RetrievedDecision] = Field(default_factory=list)
    experiences: List[RetrievedExperience] = Field(default_factory=list)
    evidence_paths: List[str] = Field(default_factory=list)
    provenance: List[str] = Field(default_factory=list)
    conflicts: List[ConflictDetail] = Field(default_factory=list)
    has_conflicts: bool = False
    historical_claims: List[RetrievedMemory] = Field(default_factory=list)
    superseding_claims: List[RetrievedMemory] = Field(default_factory=list)
