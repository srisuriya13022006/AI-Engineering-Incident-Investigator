import abc
import json
import logging
from typing import Optional
import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.memory.models import (
    StructuredIngestPayload,
    StructuredMemory,
    StructuredEntity,
    StructuredGoal,
    StructuredDecision,
    StructuredExperience,
    StructuredRelationship,
)

logger = logging.getLogger("mai.memory.extraction")


class ExtractionError(Exception):
    """Base exception for extraction failures."""
    pass


class ProviderNotConfiguredError(ExtractionError):
    """Raised when Mode B is requested but no LLM provider/key is configured."""
    pass


class InvalidExtractionOutputError(ExtractionError):
    """Raised when provider returns unparseable or invalid schema JSON."""
    pass


class ExtractionProvider(abc.ABC):
    """Abstract interface for memory extraction providers."""

    @abc.abstractmethod
    def extract(self, conversation_text: str, user_id: str) -> StructuredIngestPayload:
        """Extract structured memory entities, facts, and relationships from text."""
        pass


class MockExtractionProvider(ExtractionProvider):
    """
    Deterministic provider for testing and offline environments.
    Extracts goals, entities, and memories from predictable pattern keywords without calling external APIs.
    """

    def extract(self, conversation_text: str, user_id: str) -> StructuredIngestPayload:
        if not conversation_text or not conversation_text.strip():
            raise InvalidExtractionOutputError("Cannot extract from empty conversation text.")

        text = conversation_text.strip()
        lines = [line.strip("- *").strip() for line in text.split("\n") if line.strip()]

        memories = []
        entities = []
        goals = []
        decisions = []
        experiences = []
        relationships = []

        mem_id = f"mem_mock_{abs(hash(text)) % 100000}"
        memories.append(
            StructuredMemory(
                id=mem_id,
                text=text,
                summary=lines[0] if lines else text[:100],
                memory_type="observation",
                provenance="conversation:mock",
            )
        )

        # Keyword heuristics for simple deterministic mock extraction
        if "falkordb" in text.lower():
            ent_id = "ent_falkordb"
            entities.append(
                StructuredEntity(
                    id=ent_id,
                    name="FalkorDB",
                    entity_type="technology",
                    description="Graph database engine",
                )
            )
            relationships.append(
                StructuredRelationship(
                    source_id=mem_id,
                    relation="MENTIONS",
                    target_id=ent_id,
                )
            )

        if "python" in text.lower():
            ent_id = "ent_python"
            entities.append(
                StructuredEntity(
                    id=ent_id,
                    name="Python",
                    entity_type="technology",
                    description="Programming language",
                )
            )
            relationships.append(
                StructuredRelationship(
                    source_id=mem_id,
                    relation="MENTIONS",
                    target_id=ent_id,
                )
            )

        if "goal" in text.lower() or "latency" in text.lower():
            goal_id = "goal_extracted_01"
            goals.append(
                StructuredGoal(
                    id=goal_id,
                    title="Optimize reasoning retrieval latency",
                    priority="high",
                )
            )
            relationships.append(
                StructuredRelationship(
                    source_id=mem_id,
                    relation="HAS_GOAL",
                    target_id=goal_id,
                )
            )

        return StructuredIngestPayload(
            memories=memories,
            entities=entities,
            goals=goals,
            decisions=decisions,
            experiences=experiences,
            relationships=relationships,
        )


class OpenAIExtractionProvider(ExtractionProvider):
    """
    Extracts structured memory representations using an OpenAI-compatible JSON completion endpoint.
    Strictly enforces JSON schema output and validates with Pydantic.
    """

    SYSTEM_PROMPT = """You are a strict, factual memory extraction system for an autonomous agent.
Analyze the provided conversation and extract:
1. memories: key facts, preferences, or observations strictly stated by the user.
2. entities: named tools, technologies, concepts, or people mentioned.
3. goals: any explicit user or agent goals.
4. decisions: any decisions taken or planned.
5. experiences: empirical observations, benchmarks, or outcomes.
6. relationships: directed connections using only these allowlisted types:
   ["HAS_MEMORY", "MENTIONS", "HAS_GOAL", "MOTIVATED_BY", "INFORMED_BY", "RELATED_TO", "CONTRADICTS"]

RULES:
- Do NOT invent or hallucinate facts not present in the input.
- Return ONLY valid JSON adhering strictly to the JSON schema.
- No commentary or preamble.
"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_sec: Optional[float] = None,
    ):
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model or "gpt-4o-mini"
        self.base_url = (base_url or settings.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout_sec = timeout_sec or settings.llm_timeout_sec

    def extract(self, conversation_text: str, user_id: str) -> StructuredIngestPayload:
        if not self.api_key:
            raise ProviderNotConfiguredError(
                "LLM provider is not configured: 'LLM_API_KEY' is missing in settings. "
                "Provide 'LLM_API_KEY' in .env or use Mode A (structured_data) ingestion."
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        user_content = (
            f"User ID: {user_id}\n"
            f"Input Conversation:\n\"\"\"\n{conversation_text}\n\"\"\"\n\n"
            f"Output JSON with schema:\n"
            f'{{"memories": [{{"summary": "...", "text": "...", "memory_type": "fact|preference|observation", "confidence": 1.0}}], '
            f'"entities": [{{"name": "...", "entity_type": "...", "description": "..."}}], '
            f'"goals": [{{"title": "...", "priority": "medium|high"}}], '
            f'"decisions": [{{"action": "...", "rationale": "..."}}], '
            f'"experiences": [{{"content": "...", "outcome": "...", "confidence": 0.9}}], '
            f'"relationships": [{{"source_id": "...", "relation": "MENTIONS|HAS_GOAL|...", "target_id": "..."}}]}}'
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

        try:
            with httpx.Client(timeout=self.timeout_sec) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                raw_json_str = data["choices"][0]["message"]["content"]
        except httpx.TimeoutException as exc:
            raise ExtractionError(f"LLM extraction request timed out after {self.timeout_sec}s: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise ExtractionError(f"LLM extraction API error ({exc.response.status_code}): {exc.response.text}") from exc
        except Exception as exc:
            raise ExtractionError(f"Failed to communicate with LLM provider: {exc}") from exc

        try:
            parsed = json.loads(raw_json_str)
        except json.JSONDecodeError as exc:
            raise InvalidExtractionOutputError(f"LLM returned invalid JSON: {exc}") from exc

        try:
            return StructuredIngestPayload(**parsed)
        except ValidationError as exc:
            raise InvalidExtractionOutputError(f"Extracted JSON does not match required schema: {exc}") from exc


class GeminiRelationship(BaseModel):
    source_id: str
    relation: str
    target_id: str


class GeminiExtractionPayload(BaseModel):
    memories: list[StructuredMemory] = Field(default_factory=list)
    entities: list[StructuredEntity] = Field(default_factory=list)
    goals: list[StructuredGoal] = Field(default_factory=list)
    decisions: list[StructuredDecision] = Field(default_factory=list)
    experiences: list[StructuredExperience] = Field(default_factory=list)
    relationships: list[GeminiRelationship] = Field(default_factory=list)


class GeminiExtractionProvider(ExtractionProvider):
    """
    Google Gemini memory extraction provider using the official `google-genai` Python SDK.
    Extracts grounded facts, goals, decisions, experiences, entities, and relationships
    into a validated StructuredIngestPayload without hallucination.
    """
    DEFAULT_MODEL = "gemini-3.8-flash"

    SYSTEM_PROMPT = (
        "You are a strict, grounded knowledge-graph memory extraction system for AI cognitive agents. "
        "Analyze the provided user conversation and extract ONLY factual information explicitly stated. "
        "Strict Extraction Rules:\n"
        "1. DO NOT invent missing facts, numbers, benchmark results, dates, or relationships.\n"
        "2. Extract memories (observations, facts, preferences stated by the user).\n"
        "3. Extract goals explicitly targeted by the user.\n"
        "4. Extract decisions made and explicitly stated rationales.\n"
        "5. Extract empirical experiences and their observed outcomes.\n"
        "6. Extract key named entities (technologies, projects, components).\n"
        "7. Extract supported relationships connecting the extracted nodes. The 'relation' field MUST strictly be one of: "
        "HAS_MEMORY, MENTIONS, HAS_GOAL, MOTIVATES, MOTIVATED_BY, INFORMED_BY, RELATED_TO, CONTRADICTS.\n"
        "8. If the conversation contains contradictory statements, preserve both as separate items linked with a CONTRADICTS relationship.\n"
        "9. If the conversation is empty, irrelevant conversational chatter, or contains no extractable facts, return empty lists for all fields.\n"
        "10. Output MUST strictly conform to the required JSON schema."
    )

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        timeout_sec: float = 30.0,
        client: Optional[object] = None,
    ):
        raw_key = api_key if api_key is not None else settings.gemini_api_key
        # Guard against placeholder values in .env
        if raw_key and raw_key.strip().lower() in (
            "your_actual_key_here",
            "your_gemini_api_key_here",
            "your_selected_supported_model",
            "",
        ):
            raw_key = None
        self.api_key = raw_key.strip() if raw_key else None

        raw_model = model if model is not None else settings.gemini_model
        if raw_model and raw_model.strip().lower() in ("your_selected_supported_model", ""):
            raw_model = self.DEFAULT_MODEL
        self.model = raw_model or self.DEFAULT_MODEL
        self.timeout_sec = timeout_sec
        self._client = client

    def extract(self, conversation_text: str, user_id: str) -> StructuredIngestPayload:
        if not self.api_key:
            raise ProviderNotConfiguredError(
                "Gemini API key is not configured. Set GEMINI_API_KEY in .env or provide 'structured_data' directly via Mode A."
            )

        if not conversation_text or not conversation_text.strip():
            raise InvalidExtractionOutputError("Cannot extract memories from empty conversation text.")

        text_content = conversation_text.strip()

        # Build or use injected Google GenAI client
        if self._client is not None:
            client = self._client
        else:
            try:
                from google import genai
                client = genai.Client(api_key=self.api_key)
            except Exception as exc:
                raise ExtractionError(f"Failed to initialize Google Gemini client: {exc}") from exc

        user_prompt = (
            f"User ID: {user_id}\n"
            f"Conversation to extract memory graph from:\n"
            f"\"\"\"\n{text_content}\n\"\"\"\n"
        )

        import time
        max_attempts = 1
        response = None

        for attempt in range(max_attempts):
            try:
                from google.genai import types

                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=GeminiExtractionPayload,
                    system_instruction=self.SYSTEM_PROMPT,
                    temperature=0.0,
                )

                response = client.models.generate_content(
                    model=self.model,
                    contents=user_prompt,
                    config=config,
                )
                break
            except Exception as exc:
                err_type = type(exc).__name__
                err_msg = str(exc)
                # Check for transient capacity / demand spikes
                is_transient = (
                    "503" in err_msg
                    or "UNAVAILABLE" in err_msg
                    or "429" in err_msg
                    or "RESOURCE_EXHAUSTED" in err_msg
                )
                if is_transient and attempt < max_attempts - 1:
                    logger.warning(
                        f"Gemini API transient error ({err_type}). Retrying attempt {attempt + 2}/{max_attempts}..."
                    )
                    time.sleep(2 * (attempt + 1))
                    continue

                if "401" in err_msg or "API_KEY_INVALID" in err_msg or "PERMISSION_DENIED" in err_msg:
                    raise ExtractionError(
                        f"Gemini API authentication failed: invalid or unauthorized GEMINI_API_KEY ({err_type})."
                    ) from exc
                elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                    raise ExtractionError(
                        f"Gemini API rate limit exceeded ({err_type}). Please retry shortly."
                    ) from exc
                elif "DeadlineExceeded" in err_type or "Timeout" in err_type or "timed out" in err_msg.lower():
                    raise ExtractionError(
                        f"Gemini API request timed out after {self.timeout_sec}s: {err_msg}"
                    ) from exc
                else:
                    raise ExtractionError(
                        f"Gemini API call failed ({err_type}): {err_msg}"
                    ) from exc

        if not response or not getattr(response, "text", None):
            raise InvalidExtractionOutputError("Gemini returned an empty or null extraction response.")

        raw_json_str = response.text.strip()
        if raw_json_str.startswith("```"):
            lines = raw_json_str.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            raw_json_str = "\n".join(lines).strip()

        try:
            parsed = json.loads(raw_json_str)
        except json.JSONDecodeError as exc:
            raise InvalidExtractionOutputError(f"Gemini returned invalid JSON: {exc}") from exc

        if not isinstance(parsed, dict):
            raise InvalidExtractionOutputError("Gemini returned a JSON value that is not an object.")

        # Check if any extractable entities/memories were found
        total_items = (
            len(parsed.get("memories", []))
            + len(parsed.get("entities", []))
            + len(parsed.get("goals", []))
            + len(parsed.get("decisions", []))
            + len(parsed.get("experiences", []))
        )
        if total_items == 0:
            raise InvalidExtractionOutputError(
                "No extractable memories, goals, decisions, experiences, or entities found in the conversation."
            )

        # Validate relationship relation allowlist and map common aliases
        from app.memory.models import ALLOWLISTED_RELATIONSHIPS
        valid_rels = []
        for rel in parsed.get("relationships", []):
            if isinstance(rel, dict):
                r_type = str(rel.get("relation", "")).upper().strip()
                if r_type == "TARGETS_GOAL":
                    r_type = "HAS_GOAL"
                elif r_type in ("UTILIZES_ENTITY", "RECORDS_DECISION", "USES"):
                    r_type = "RELATED_TO"

                if r_type in ALLOWLISTED_RELATIONSHIPS:
                    rel["relation"] = r_type
                    valid_rels.append(rel)
                else:
                    logger.warning(f"Filtered out unallowed relationship type from Gemini extraction: {r_type}")
        parsed["relationships"] = valid_rels

        try:
            return StructuredIngestPayload(**parsed)
        except ValidationError as exc:
            raise InvalidExtractionOutputError(f"Gemini extracted JSON does not match required schema: {exc}") from exc


def get_extraction_provider() -> ExtractionProvider:
    """Factory to retrieve the active extraction provider based on settings."""
    provider_name = (settings.llm_provider or "").lower().strip()

    if provider_name == "mock":
        return MockExtractionProvider()
    elif provider_name in ("openai", "groq", "ollama", "custom"):
        return OpenAIExtractionProvider()
    else:
        # Default provider is Google Gemini API (Milestone 5)
        return GeminiExtractionProvider()
