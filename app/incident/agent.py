"""
Agentic incident investigation reasoning engine.

Implements a bounded, multi-turn LLM agent loop backed by FalkorDB tools.
The agent observes incident symptoms, autonomously selects and executes tools
to gather empirical graph evidence, enforces max tool-call limits, tracks provenance,
and synthesizes a grounded investigation without hallucination.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from app.config import settings
from app.incident.models import (
    IncidentNode,
    PriorInvestigationSummary,
)
from app.incident.operations import query_incident_graph
from app.github.operations import query_github_evidence_for_incident, get_repository_details
from app.graph.connection import FalkorDBConnectionError

logger = logging.getLogger("incident.agent")


AGENT_SYSTEM_PROMPT = (
    "You are Mai's Engineering Incident Investigator Agent. "
    "Your mission is to investigate observed incident symptoms and formulate a grounded, evidence-backed diagnostic recommendation. "
    "\n\nStrict Execution Rules:\n"
    "1. Use tools to query prior incident telemetry, hypotheses, fix attempts, and connected repository artifacts (PRs, issues, commits, workflow runs) in FalkorDB.\n"
    "2. You have a strict budget of at most 3 tool calls. Call only the tools required to investigate the symptoms.\n"
    "3. NEVER fabricate incidents, pull requests, commits, workflow runs, root causes, or fix outcomes. Only state facts directly returned by tools.\n"
    "4. If a previous intervention was evaluated as 'UNSUCCESSFUL', explicitly warn against repeating it.\n"
    "5. If tools return no matching evidence, explicitly state that evidence is insufficient and abstain from speculating.\n"
    "6. Formulate a clear, actionable, evidence-justified next step for the engineering team."
)


@dataclass
class AgentEvidenceContext:
    """Accumulates verified graph evidence retrieved across autonomous tool calls."""
    incidents: List[IncidentNode] = field(default_factory=list)
    investigations: List[PriorInvestigationSummary] = field(default_factory=list)
    github_records: List[Dict[str, Any]] = field(default_factory=list)
    candidate_records: List[Dict[str, Any]] = field(default_factory=list)
    goals: List[Dict[str, Any]] = field(default_factory=list)
    evidence_paths: List[str] = field(default_factory=list)
    provenance: Set[str] = field(default_factory=set)

    def merge_incident_graph_data(self, data: Dict[str, Any]):
        for inc in data.get("incidents", []):
            if not any(existing.id == inc.id for existing in self.incidents):
                self.incidents.append(inc)
        for inv in data.get("investigations", []):
            if not any(existing.investigation_id == inv.investigation_id for existing in self.investigations):
                self.investigations.append(inv)
        for g in data.get("goals", []):
            if not any(existing.get("id") == g.get("id") for existing in self.goals):
                self.goals.append(g)
        for p in data.get("evidence_paths", []):
            if p not in self.evidence_paths:
                self.evidence_paths.append(p)
        for prov in data.get("provenance", []):
            self.provenance.add(prov)

    def merge_github_records(
        self,
        records: List[Any],
        paths: List[str],
        provenance_traces: Set[str],
    ):
        for r in records:
            r_dict = r.model_dump() if hasattr(r, "model_dump") else (r if isinstance(r, dict) else r.__dict__)
            cat = r_dict.get("evidence_category", "related")
            r_id = r_dict.get("id")
            if cat in ("direct", "related"):
                if not any(existing.get("id") == r_id for existing in self.github_records):
                    self.github_records.append(r_dict)
            else:
                if not any(existing.get("id") == r_id for existing in self.candidate_records):
                    self.candidate_records.append(r_dict)
        for p in paths:
            if p not in self.evidence_paths:
                self.evidence_paths.append(p)
        for prov in provenance_traces:
            self.provenance.add(prov)


def _sanitize_turn_request(turn: int, contents: List[Any], tools: Optional[List[Any]]) -> Dict[str, Any]:
    """
    Produces a safe, sanitized summary of request structure and tools for turn logging.
    STRICTLY excludes all API keys, authorization tokens, raw secrets, or credentials.
    """
    sanitized_contents = []
    for item in contents:
        role = getattr(item, "role", "unknown")
        parts = getattr(item, "parts", []) or []
        part_summaries = []
        for p in parts:
            if hasattr(p, "text") and p.text:
                part_summaries.append({"type": "text", "length": len(p.text)})
            elif hasattr(p, "function_call") and p.function_call:
                fc = p.function_call
                arg_keys = list(fc.args.keys()) if getattr(fc, "args", None) else []
                part_summaries.append({
                    "type": "function_call",
                    "name": getattr(fc, "name", "unknown"),
                    "arg_keys": arg_keys,
                })
            elif hasattr(p, "function_response") and p.function_response:
                fr = p.function_response
                part_summaries.append({
                    "type": "function_response",
                    "name": getattr(fr, "name", "unknown"),
                    "response_keys": list(fr.response.keys()) if getattr(fr, "response", None) else [],
                })
            else:
                part_summaries.append({"type": type(p).__name__})
        sanitized_contents.append({"role": role, "parts": part_summaries})

    tool_names = []
    if tools:
        for t in tools:
            if callable(t):
                tool_names.append(getattr(t, "__name__", str(t)))
            elif hasattr(t, "function_declarations"):
                for fd in t.function_declarations:
                    tool_names.append(getattr(fd, "name", "unnamed"))
            else:
                tool_names.append(str(t))

    return {
        "turn": turn,
        "contents_count": len(contents),
        "contents": sanitized_contents,
        "available_tools": tool_names,
    }


def _is_transient_error(exc: Exception) -> bool:
    """Returns True if exception is a transient server or network error suitable for bounded retry."""
    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code in (500, 502, 503, 504):
        return True
    exc_type = type(exc).__name__
    exc_msg = str(exc)
    if any(t in exc_type for t in ("ServerError", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError")):
        return True
    if any(code in exc_msg for code in ("503", "500", "502", "504", "high demand", "temporary", "Internal error")):
        return True
    return False


def _call_model_with_retry(
    client: Any,
    model: str,
    contents: List[Any],
    config: Any,
    max_retries: int = 2,
    initial_backoff: float = 1.0,
) -> Any:
    """
    Executes generate_content with bounded exponential backoff retry ONLY for transient server errors.
    Strictly avoids indefinite retries.
    """
    for attempt in range(max_retries + 1):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            if _is_transient_error(exc) and attempt < max_retries:
                backoff = initial_backoff * (2 ** attempt)
                logger.warning(
                    f"Transient server error from Gemini model endpoint (attempt {attempt + 1}/{max_retries + 1}): {exc}. "
                    f"Backing off for {backoff:.1f}s before retry..."
                )
                time.sleep(backoff)
                continue
            raise


class IncidentInvestigationAgent:
    """
    Autonomous bounded agent that investigates incidents by iteratively selecting
    and executing tools against FalkorDB.
    """

    def __init__(
        self,
        user_id: str,
        target_component: Optional[str] = None,
        target_repository: Optional[str] = None,
        max_tool_calls: int = 3,
        client: Optional[Any] = None,
        model: Optional[str] = None,
    ):
        # Strict tenant binding (prevent cross-user leakage)
        self.user_id = user_id.strip()
        self.target_component = target_component.strip() if target_component else None
        self.target_repository = target_repository.strip() if target_repository else None
        self.max_tool_calls = max_tool_calls

        self.tool_call_count = 0
        self.execution_trace: List[Dict[str, Any]] = []
        self.context = AgentEvidenceContext()
        self.db_error: Optional[Exception] = None

        self._client = client
        self.model = model or getattr(settings, "gemini_model", "gemini-3.8-flash")

    # =========================================================================
    # Callable Bounded Tools (exposed to LLM)
    # =========================================================================

    def search_incident_history(self, symptoms: str, component: str = "") -> str:
        """
        Search prior incident investigations, hypotheses, fix attempts, and empirical outcomes in FalkorDB.
        
        Args:
            symptoms: Observed error messages, latency degradation, or failure alerts.
            component: Optional subsystem or service name to narrow the query.
        """
        self.tool_call_count += 1
        comp_val = (component or self.target_component or "").strip()
        now_iso = datetime.now(timezone.utc).isoformat()

        if self.tool_call_count > self.max_tool_calls:
            msg = "Tool call limit reached (max 3 calls). You must now formulate your final response from retrieved evidence."
            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "search_incident_history",
                "arguments": {"symptoms": symptoms, "component": comp_val},
                "result_summary": "Rejected: max tool call budget reached.",
                "timestamp": now_iso,
            })
            return msg

        try:
            graph_data = query_incident_graph(
                user_id=self.user_id,
                symptoms=symptoms,
                component=comp_val or None,
                repository=self.target_repository,
            )
            self.context.merge_incident_graph_data(graph_data)

            incidents = graph_data.get("incidents", [])
            investigations = graph_data.get("investigations", [])

            if not incidents and not investigations:
                summary = f"No historical incidents or investigations found for '{symptoms}' under component '{comp_val or 'any'}'."
            else:
                inc_titles = [f"'{inc.title}' ({inc.affected_component})" for inc in incidents]
                fix_notes = []
                for inv in investigations:
                    if inv.fix_description:
                        eval_note = f" Outcome: {inv.outcome_evaluation} ({inv.outcome_metric}: {inv.before_value} -> {inv.after_value})" if inv.outcome_metric else f" Evaluation: {inv.outcome_evaluation}"
                        fix_notes.append(f"Fix '{inv.fix_description}' [{inv.fix_status}]{eval_note}")
                summary = (
                    f"Retrieved {len(incidents)} incident(s): {', '.join(inc_titles)}. "
                    f"Investigations: {len(investigations)} hop(s). "
                    f"{'Past fixes: ' + '; '.join(fix_notes) if fix_notes else 'No prior fix attempts recorded.'}"
                )

            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "search_incident_history",
                "arguments": {"symptoms": symptoms, "component": comp_val},
                "result_summary": summary,
                "timestamp": now_iso,
            })
            return summary
        except FalkorDBConnectionError as exc:
            self.db_error = exc
            raise
        except Exception as exc:
            err = f"Error querying incident history: {exc}"
            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "search_incident_history",
                "arguments": {"symptoms": symptoms, "component": comp_val},
                "result_summary": err,
                "timestamp": now_iso,
            })
            return err

    def retrieve_github_evidence(self, symptoms: str, component: str = "", repository: str = "") -> str:
        """
        Retrieve and trace relevant GitHub issues, pull requests, commits, and workflow runs in FalkorDB.
        
        Args:
            symptoms: Incident error text or workflow failure symptoms.
            component: Subsystem name.
            repository: Optional repository full name (e.g. 'owner/repo').
        """
        self.tool_call_count += 1
        comp_val = (component or self.target_component or "").strip()
        repo_val = (repository or self.target_repository or "").strip()
        now_iso = datetime.now(timezone.utc).isoformat()

        if self.tool_call_count > self.max_tool_calls:
            msg = "Tool call limit reached (max 3 calls). You must now formulate your final response from retrieved evidence."
            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "retrieve_github_evidence",
                "arguments": {"symptoms": symptoms, "component": comp_val, "repository": repo_val},
                "result_summary": "Rejected: max tool call budget reached.",
                "timestamp": now_iso,
            })
            return msg

        try:
            kw_list = [w.strip() for w in re.split(r"[\s,;]+", symptoms) if len(w.strip()) > 2]
            records, paths, provs = query_github_evidence_for_incident(
                user_id=self.user_id,
                keywords=kw_list,
                component=comp_val or None,
                target_repo=repo_val or None,
                symptoms_text=symptoms,
            )
            self.context.merge_github_records(records, paths, provs)

            if not records:
                summary = f"No GitHub issues, pull requests, or workflow runs matched for repository '{repo_val or 'any'}'."
            else:
                prs = [r.title_or_summary for r in records if r.record_type == "pull_request"]
                issues = [r.title_or_summary for r in records if r.record_type == "issue"]
                wfs = [f"{r.title_or_summary} ({r.state_or_status})" for r in records if r.record_type == "workflow_run"]
                commits = [r.title_or_summary for r in records if r.record_type == "commit"]

                parts = []
                if prs: parts.append(f"PRs: {', '.join(prs)}")
                if issues: parts.append(f"Issues: {', '.join(issues)}")
                if wfs: parts.append(f"Workflows: {', '.join(wfs)}")
                if commits: parts.append(f"Commits: {', '.join(commits)}")
                summary = f"Retrieved {len(records)} GitHub artifact(s): {'; '.join(parts)}. Causal paths: {len(paths)}."

            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "retrieve_github_evidence",
                "arguments": {"symptoms": symptoms, "component": comp_val, "repository": repo_val},
                "result_summary": summary,
                "timestamp": now_iso,
            })
            return summary
        except FalkorDBConnectionError as exc:
            self.db_error = exc
            raise
        except Exception as exc:
            err = f"Error retrieving GitHub evidence: {exc}"
            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "retrieve_github_evidence",
                "arguments": {"symptoms": symptoms, "component": comp_val, "repository": repo_val},
                "result_summary": err,
                "timestamp": now_iso,
            })
            return err

    def inspect_repository_details(self, repository: str) -> str:
        """
        Inspect details, branches, and cross-repository dependencies for a specific repository.
        
        Args:
            repository: Full repository name (e.g. 'owner/repo').
        """
        self.tool_call_count += 1
        now_iso = datetime.now(timezone.utc).isoformat()
        repo_val = repository.strip()

        if self.tool_call_count > self.max_tool_calls:
            msg = "Tool call limit reached (max 3 calls). You must now formulate your final response from retrieved evidence."
            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "inspect_repository_details",
                "arguments": {"repository": repo_val},
                "result_summary": "Rejected: max tool call budget reached.",
                "timestamp": now_iso,
            })
            return msg

        try:
            details = get_repository_details(user_id=self.user_id, repo_identifier=repo_val)
            if not details:
                summary = f"Repository '{repo_val}' is not imported into FalkorDB for user '{self.user_id}'."
            else:
                summary = (
                    f"Repository '{details.full_name}' (branch: {details.default_branch}): "
                    f"{details.issues_count} issues, {details.pull_requests_count} PRs, "
                    f"{details.commits_count} commits, {details.workflow_runs_count} workflow runs. "
                    f"Dependencies: {', '.join(details.downstream_dependencies) if details.downstream_dependencies else 'None'}."
                )

            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "inspect_repository_details",
                "arguments": {"repository": repo_val},
                "result_summary": summary,
                "timestamp": now_iso,
            })
            return summary
        except FalkorDBConnectionError as exc:
            self.db_error = exc
            raise
        except Exception as exc:
            err = f"Error inspecting repository: {exc}"
            self.execution_trace.append({
                "step": self.tool_call_count,
                "tool": "inspect_repository_details",
                "arguments": {"repository": repo_val},
                "result_summary": err,
                "timestamp": now_iso,
            })
            return err

    # =========================================================================
    # Agent Execution Loop
    # =========================================================================

    def run(
        self,
        symptoms: str,
        mock_runner: Optional[Callable[["IncidentInvestigationAgent", str], str]] = None,
    ) -> Tuple[AgentEvidenceContext, List[Dict[str, Any]], str, bool]:
        """
        Runs the autonomous bounded agent loop.
        
        Returns:
            (context, execution_trace, deliberation_text, success)
        """
        prompt = (
            f"Observed Incident Symptoms: {symptoms}\n"
            f"Component: {self.target_component or 'unspecified'}\n"
            f"Repository: {self.target_repository or 'unspecified'}\n"
            f"\nPlease investigate these symptoms systematically using your tools to inspect prior incident telemetry and repository records in FalkorDB."
        )

        # 1. Mock runner path (for deterministic automated tests without external API calls)
        if mock_runner is not None:
            deliberation = mock_runner(self, prompt)
            return self.context, self.execution_trace, deliberation, True

        # 2. Check for configured Gemini API Key
        raw_key = getattr(settings, "gemini_api_key", None)
        if raw_key and raw_key.strip().lower() in ("your_gemini_api_key_here", "your_actual_key_here", ""):
            raw_key = None

        if not raw_key and self._client is None:
            logger.info("Gemini API key is not configured; cannot initialize autonomous LLM agent.")
            return self.context, [], "", False

        # 3. Live Google GenAI Client
        try:
            if self._client is not None:
                client = self._client
            else:
                from google import genai
                client = genai.Client(api_key=raw_key.strip())

            from google.genai import types

            tool_map = {
                "search_incident_history": self.search_incident_history,
                "retrieve_github_evidence": self.retrieve_github_evidence,
                "inspect_repository_details": self.inspect_repository_details,
            }
            tools = list(tool_map.values())

            contents: List[Any] = [
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=prompt)]
                )
            ]

            deliberation = ""
            for turn in range(1, self.max_tool_calls + 2):
                config = types.GenerateContentConfig(
                    system_instruction=AGENT_SYSTEM_PROMPT,
                    tools=tools,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                    temperature=0.0,
                )

                sanitized_info = _sanitize_turn_request(turn, contents, tools)
                logger.info(f"Dispatching sanitized turn {turn} request: {json.dumps(sanitized_info)}")

                try:
                    response = _call_model_with_retry(
                        client=client,
                        model=self.model,
                        contents=contents,
                        config=config,
                        max_retries=2,
                        initial_backoff=1.0,
                    )
                except Exception as exc:
                    # If Turn 2+ fails with a backend error after sending tool responses,
                    # activate the compatible agent path with empirical findings as observation context.
                    if turn > 1 and len(contents) >= 3 and getattr(contents[-1], "role", None) == "tool":
                        logger.warning(
                            f"Model endpoint encountered error on turn {turn} tool response ({exc}). "
                            "Activating compatible observation path grounded in real tool findings."
                        )
                        last_tool_content = contents.pop()
                        obs_lines = []
                        for p in getattr(last_tool_content, "parts", []):
                            fr = getattr(p, "function_response", None)
                            if fr:
                                res_val = fr.response.get("result", "") if isinstance(fr.response, dict) else str(fr.response)
                                obs_lines.append(f"- Tool '{fr.name}' returned: {res_val}")
                        obs_prompt = (
                            "Empirical Graph Evidence retrieved by autonomous tool execution:\n"
                            + "\n".join(obs_lines)
                            + "\n\nPlease synthesize your final grounded incident investigation report, "
                            "identifying hypotheses, prior fix attempts and outcomes, and recommended next actions."
                        )
                        contents.append(
                            types.Content(
                                role="user",
                                parts=[types.Part.from_text(text=obs_prompt)]
                            )
                        )
                        config_compat = types.GenerateContentConfig(
                            system_instruction=AGENT_SYSTEM_PROMPT,
                            tools=tools,
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                            temperature=0.0,
                        )
                        response = _call_model_with_retry(
                            client=client,
                            model=self.model,
                            contents=contents,
                            config=config_compat,
                            max_retries=2,
                            initial_backoff=1.0,
                        )
                    else:
                        raise

                if self.db_error:
                    raise self.db_error

                # Check if model requested tools
                fcs = getattr(response, "function_calls", None) or []
                if fcs and self.tool_call_count < self.max_tool_calls:
                    # Append model's candidate content to keep proper conversation turn sequence
                    contents.append(response.candidates[0].content)
                    resp_parts = []
                    for fc in fcs:
                        fn = tool_map.get(fc.name)
                        if fn:
                            args = fc.args or {}
                            tool_res = fn(**args)
                        else:
                            tool_res = f"Unknown tool '{fc.name}'"
                        resp_parts.append(
                            types.Part.from_function_response(
                                name=fc.name,
                                response={"result": str(tool_res)}
                            )
                        )
                    contents.append(
                        types.Content(
                            role="tool",
                            parts=resp_parts
                        )
                    )
                    # Next turn will send tool responses back to model
                    continue
                elif fcs and self.tool_call_count >= self.max_tool_calls and not getattr(response, "text", None):
                    # Tool budget reached, but model emitted function call without text
                    contents.append(response.candidates[0].content)
                    resp_parts = [
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"result": "Tool call limit reached (max 3 calls). Formulate your final grounded recommendation from retrieved evidence."}
                        )
                        for fc in fcs
                    ]
                    contents.append(types.Content(role="tool", parts=resp_parts))
                    final_resp = _call_model_with_retry(
                        client=client,
                        model=self.model,
                        contents=contents,
                        config=config,
                        max_retries=2,
                        initial_backoff=1.0,
                    )
                    deliberation = final_resp.text or "Investigation completed from retrieved evidence."
                    break
                else:
                    # Model provided text deliberation or completed
                    deliberation = response.text or "Investigation completed from retrieved evidence."
                    break

            if not deliberation and response and response.text:
                deliberation = response.text

            return self.context, self.execution_trace, deliberation or "Investigation completed.", True

        except FalkorDBConnectionError:
            raise
        except Exception as exc:
            if self.db_error:
                raise self.db_error
            logger.warning(f"Autonomous incident agent encountered execution failure: {exc}")
            self.execution_trace.append({
                "step": self.tool_call_count + 1,
                "tool": "agent_error",
                "arguments": {},
                "result_summary": str(exc),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return self.context, self.execution_trace, "", False
