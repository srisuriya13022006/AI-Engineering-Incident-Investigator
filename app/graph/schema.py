from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class NodeData(BaseModel):
    """Represents a graph node with its label, ID, and properties."""
    label: str
    id: str
    properties: Dict[str, Any] = Field(default_factory=dict)


class RelationshipData(BaseModel):
    """Represents a directed relationship between two nodes."""
    source_id: str
    source_label: str
    relation: str
    target_id: str
    target_label: str
    properties: Dict[str, Any] = Field(default_factory=dict)


class EvidenceChain(BaseModel):
    """Structured path representing agent reasoning from goal to experience."""
    user_id: str
    user_name: str
    goal_id: str
    goal_title: str
    goal_priority: str
    decision_id: str
    decision_action: str
    decision_rationale: str
    experience_id: str
    experience_content: str
    experience_outcome: str
    experience_confidence: float
    path_summary: str


class EvidencePathResponse(BaseModel):
    """API response for evidence path queries."""
    success: bool
    count: int
    chains: List[EvidenceChain] = Field(default_factory=list)
    nodes: List[NodeData] = Field(default_factory=list)
    relationships: List[RelationshipData] = Field(default_factory=list)


class SeedResponse(BaseModel):
    """API response after idempotent graph seeding."""
    success: bool
    message: str
    user_id: str
    goal_id: str
    decision_id: str
    experience_id: str
    nodes_created: int
    relationships_created: int
    properties_set: int


class HealthResponse(BaseModel):
    """Application and FalkorDB connection status."""
    status: str
    database: str
    graph_name: str
    error: Optional[str] = None
