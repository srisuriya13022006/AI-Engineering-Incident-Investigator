"""
Tests for the Bounded Agentic Incident Investigation System.

Verifies:
1. Autonomous tool selection, execution, and feedback loop.
2. Multi-turn deliberation and evidence aggregation into AgentEvidenceContext.
3. Strict enforcement of maximum 3 tool calls per investigation (bounded execution).
4. Strict tenant isolation (prevent cross-user retrieval).
5. Explicit abstention on insufficient/no evidence (never fabricate).
6. Provenance and execution trace logging for live demo inspection.
7. Distinction between agentic mode and deterministic fallback mode.
"""

import pytest
from unittest.mock import MagicMock, patch
from typing import Dict, Any, List

from app.incident.models import (
    IncidentNode,
    PriorInvestigationSummary,
    IncidentQueryRequest,
    IncidentInvestigationResponse,
)
from app.incident.agent import (
    IncidentInvestigationAgent,
    AgentEvidenceContext,
    AGENT_SYSTEM_PROMPT,
)
from app.incident.investigator import investigate_incident
from app.github.models import GitHubRecordEvidence


# ============================================================================
# 1. Tenant Isolation & Initialization
# ============================================================================

def test_agent_tenant_isolation():
    """Verify that the agent strictly binds user_id and does not permit cross-tenant leakage."""
    agent_a = IncidentInvestigationAgent(user_id="tenant_alpha")
    agent_b = IncidentInvestigationAgent(user_id="tenant_beta")

    assert agent_a.user_id == "tenant_alpha"
    assert agent_b.user_id == "tenant_beta"
    assert agent_a.max_tool_calls == 3

    # Mock query_incident_graph to verify user_id argument passed to FalkorDB
    with patch("app.incident.agent.query_incident_graph") as mock_query:
        mock_query.return_value = {
            "incidents": [],
            "investigations": [],
            "goals": [],
            "evidence_paths": [],
            "provenance": [],
        }

        agent_a.search_incident_history(symptoms="Database connection timeout", component="auth-service")

        # Must be called strictly with tenant_alpha
        mock_query.assert_called_once()
        _, kwargs = mock_query.call_args
        assert kwargs["user_id"] == "tenant_alpha"
        assert kwargs["symptoms"] == "Database connection timeout"
        assert kwargs["component"] == "auth-service"


# ============================================================================
# 2. Tool Selection, Execution & Result Feedback
# ============================================================================

def test_agent_tool_selection_and_execution():
    """Verify agent executes bounded tools against graph data and aggregates evidence."""
    agent = IncidentInvestigationAgent(
        user_id="tenant_alpha",
        target_component="payment-gateway",
        target_repository="mai/payments",
    )

    fake_incident = IncidentNode(
        id="inc_pay_01",
        title="Payment Gateway Timeout Spike",
        symptoms="504 Gateway Timeout on checkout",
        affected_component="payment-gateway",
        severity="critical",
        timestamp="2026-03-10T10:00:00Z",
        user_id="tenant_alpha",
    )
    fake_investigation = PriorInvestigationSummary(
        incident_id="inc_pay_01",
        incident_title="Payment Gateway Timeout Spike",
        symptoms="504 Gateway Timeout on checkout",
        component="payment-gateway",
        investigation_id="inv_pay_01",
        hypothesis="Downstream stripe client socket starvation",
        fix_attempt_id="fix_pay_01",
        fix_description="Bumped client pool max size to 80",
        fix_status="executed",
        outcome_id="out_pay_01",
        outcome_evaluation="unsuccessful",
        outcome_metric="p99_latency_ms",
        before_value="850ms",
        after_value="820ms",
    )

    with patch("app.incident.agent.query_incident_graph") as mock_inc_graph:
        mock_inc_graph.return_value = {
            "incidents": [fake_incident],
            "investigations": [fake_investigation],
            "goals": [{"id": "goal_1", "title": "P99 under 120ms", "status": "current"}],
            "evidence_paths": ["Incident(inc_pay_01) -> Investigation(inv_pay_01) -> FixAttempt(fix_pay_01)"],
            "provenance": ["incident:inc_pay_01", "fix_attempt:fix_pay_01"],
        }

        result_summary = agent.search_incident_history("504 Gateway Timeout", "payment-gateway")

        # Verify tool output reflects empirical findings
        assert "Retrieved 1 incident(s)" in result_summary
        assert "Payment Gateway Timeout Spike" in result_summary
        assert "unsuccessful" in result_summary.lower()

        # Verify evidence merged into agent context
        assert len(agent.context.incidents) == 1
        assert agent.context.incidents[0].id == "inc_pay_01"
        assert len(agent.context.investigations) == 1
        assert agent.context.investigations[0].outcome_evaluation == "unsuccessful"

        # Verify step recorded in execution trace
        assert len(agent.execution_trace) == 1
        trace_step = agent.execution_trace[0]
        assert trace_step["step"] == 1
        assert trace_step["tool"] == "search_incident_history"
        assert trace_step["arguments"]["symptoms"] == "504 Gateway Timeout"
        assert "Payment Gateway Timeout Spike" in trace_step["result_summary"]


def test_agent_github_evidence_tool():
    """Verify retrieve_github_evidence tool retrieves and formats GitHub records."""
    agent = IncidentInvestigationAgent(user_id="tenant_alpha")

    fake_gh_record = GitHubRecordEvidence(
        id="gh_pr_102",
        record_type="pull_request",
        title_or_summary="fix: optimize connection retry backoff",
        evidence_category="direct",
        state_or_status="merged",
        repository="mai/payments",
        html_url="https://github.com/mai/payments/pull/102",
        relevance_reason="Matches payment timeout retry changes",
    )

    with patch("app.incident.agent.query_github_evidence_for_incident") as mock_gh:
        mock_gh.return_value = (
            [fake_gh_record],
            ["Repository(mai/payments) -> PullRequest(gh_pr_102)"],
            {"github:pull_request:gh_pr_102"},
        )

        result_summary = agent.retrieve_github_evidence("connection retry", repository="mai/payments")

        assert "Retrieved 1 GitHub artifact(s)" in result_summary
        assert "fix: optimize connection retry backoff" in result_summary
        assert len(agent.context.github_records) == 1
        assert agent.execution_trace[0]["tool"] == "retrieve_github_evidence"


# ============================================================================
# 3. Bounded Execution & Stopping Condition (Strict Max 3 Tool Calls)
# ============================================================================

def test_agent_strict_maximum_three_tool_calls():
    """Verify that attempting more than 3 tool calls is rejected to prevent infinite loops."""
    agent = IncidentInvestigationAgent(user_id="tenant_alpha")

    with patch("app.incident.agent.query_incident_graph") as mock_query, \
         patch("app.incident.agent.query_github_evidence_for_incident") as mock_gh, \
         patch("app.incident.agent.get_repository_details") as mock_repo:

        mock_query.return_value = {"incidents": [], "investigations": [], "goals": [], "evidence_paths": [], "provenance": []}
        mock_gh.return_value = ([], [], set())
        mock_repo.return_value = None

        # 1st call: OK
        res1 = agent.search_incident_history("symptom 1")
        assert "limit reached" not in res1.lower()
        assert agent.tool_call_count == 1

        # 2nd call: OK
        res2 = agent.retrieve_github_evidence("symptom 2")
        assert "limit reached" not in res2.lower()
        assert agent.tool_call_count == 2

        # 3rd call: OK
        res3 = agent.inspect_repository_details("owner/repo")
        assert "limit reached" not in res3.lower()
        assert agent.tool_call_count == 3

        # 4th call: MUST BE REJECTED
        res4 = agent.search_incident_history("symptom 4")
        assert "Tool call limit reached (max 3 calls)" in res4
        assert agent.tool_call_count == 4
        assert agent.execution_trace[3]["result_summary"] == "Rejected: max tool call budget reached."


# ============================================================================
# 4. Multi-Turn Execution & Grounded Deliberation
# ============================================================================

def test_agent_multi_turn_execution_with_mock_runner():
    """Verify autonomous agent multi-turn interaction loop using mock_runner."""
    agent = IncidentInvestigationAgent(
        user_id="tenant_alpha",
        target_component="checkout-api",
    )

    fake_incident = IncidentNode(
        id="inc_co_1",
        title="Checkout Timeout Degradation",
        symptoms="HTTP 504 on POST /checkout",
        affected_component="checkout-api",
        severity="high",
        timestamp="2026-03-10T10:00:00Z",
        user_id="tenant_alpha",
    )

    def mock_agent_loop(runner_agent: IncidentInvestigationAgent, prompt: str) -> str:
        # Step 1: Agent decides to call search_incident_history
        hist_result = runner_agent.search_incident_history(
            symptoms="HTTP 504 on POST /checkout",
            component="checkout-api",
        )
        assert "Checkout Timeout" in hist_result

        # Step 2: Agent decides to inspect GitHub records
        gh_result = runner_agent.retrieve_github_evidence(
            symptoms="POST /checkout timeout",
            component="checkout-api",
        )

        # Agent decides to stop and formulate final grounded recommendation
        return (
            "Based on historical incident inc_co_1, checkout-api previously experienced 504 timeouts. "
            "Telemetry shows no verified fix has resolved this issue. "
            "Recommend profiling database connection pool saturation before applying code changes."
        )

    with patch("app.incident.agent.query_incident_graph") as mock_inc, \
         patch("app.incident.agent.query_github_evidence_for_incident") as mock_gh:

        mock_inc.return_value = {
            "incidents": [fake_incident],
            "investigations": [],
            "goals": [],
            "evidence_paths": ["Incident(inc_co_1)"],
            "provenance": ["incident:inc_co_1"],
        }
        mock_gh.return_value = ([], [], set())

        ctx, trace, deliberation, success = agent.run(
            symptoms="HTTP 504 on POST /checkout",
            mock_runner=mock_agent_loop,
        )

        assert success is True
        assert len(trace) == 2
        assert trace[0]["tool"] == "search_incident_history"
        assert trace[1]["tool"] == "retrieve_github_evidence"
        assert "checkout-api previously experienced" in deliberation
        assert len(ctx.incidents) == 1


# ============================================================================
# 5. Abstention on Insufficient Evidence
# ============================================================================

def test_agent_abstention_on_unrelated_query():
    """Verify that when no graph evidence exists, the agent abstains without hallucinating."""
    req = IncidentQueryRequest(
        user_id="tenant_alpha",
        symptoms="Coffee machine in kitchen is leaking water",
        component="breakroom",
    )

    def mock_runner_no_evidence(agent: IncidentInvestigationAgent, prompt: str) -> str:
        agent.search_incident_history("Coffee machine in kitchen is leaking water", "breakroom")
        return "No telemetry or graph evidence found for breakroom coffee machine. Abstaining from recommendation."

    with patch("app.incident.agent.query_incident_graph") as mock_inc:
        mock_inc.return_value = {
            "incidents": [],
            "investigations": [],
            "goals": [],
            "evidence_paths": [],
            "provenance": [],
        }

        resp = investigate_incident(req=req, mock_runner=mock_runner_no_evidence)

        # Must explicitly abstain
        assert resp.found_evidence is False
        assert "Abstaining from ungrounded recommendation" in resp.insufficient_evidence_reason
        assert resp.recommendation is None
        assert resp.recommended_next_action is None
        assert resp.investigation_mode == "agentic"
        assert len(resp.agent_execution_trace) == 1
        assert "No historical incidents" in resp.agent_execution_trace[0]["result_summary"]


# ============================================================================
# 6. Mode Distinction & Deterministic Fallback
# ============================================================================

def test_explicit_deterministic_fallback_when_agent_fails():
    """Verify that when the LLM agent fails, the system clearly falls back to deterministic retrieval."""
    req = IncidentQueryRequest(
        user_id="tenant_alpha",
        symptoms="Database connection timeout",
        component="auth-service",
        prefer_agent=True,
    )

    fake_incident = IncidentNode(
        id="inc_det_01",
        title="Auth Service DB Timeout",
        symptoms="Database connection timeout",
        affected_component="auth-service",
        severity="high",
        timestamp="2026-03-10T10:00:00Z",
        user_id="tenant_alpha",
    )

    # Force agent.run to fail (e.g. simulated network/LLM outage)
    with patch("app.incident.agent.IncidentInvestigationAgent.run", side_effect=RuntimeError("Gemini API connection error")), \
         patch("app.incident.investigator.query_incident_graph") as mock_fallback_query, \
         patch("app.incident.investigator.get_graph_client"):

        mock_fallback_query.return_value = {
            "incidents": [fake_incident],
            "investigations": [],
            "actions": [],
            "goals": [],
            "evidence_paths": ["Incident(inc_det_01)"],
            "provenance": ["incident:inc_det_01"],
            "github_records": [],
        }

        resp = investigate_incident(req=req)

        # Must identify as deterministic_fallback, not agentic
        assert resp.investigation_mode == "deterministic_fallback"
        assert resp.found_evidence is True
        assert len(resp.matched_incidents) == 1
        assert resp.matched_incidents[0].id == "inc_det_01"


def test_agentic_mode_success_in_response():
    """Verify that successful agent run produces investigation_mode == 'agentic' and valid trace."""
    req = IncidentQueryRequest(
        user_id="tenant_alpha",
        symptoms="Payment gateway timeout",
        component="payment-gateway",
        prefer_agent=True,
    )

    fake_incident = IncidentNode(
        id="inc_agent_01",
        title="Payment Latency Spike",
        symptoms="Payment gateway timeout",
        affected_component="payment-gateway",
        severity="high",
        timestamp="2026-03-10T10:00:00Z",
        user_id="tenant_alpha",
    )

    def mock_successful_agent(agent: IncidentInvestigationAgent, prompt: str) -> str:
        agent.search_incident_history("Payment gateway timeout", "payment-gateway")
        return "Agent synthesis: Payment gateway socket timeout identified in prior incident."

    with patch("app.incident.agent.query_incident_graph") as mock_query, \
         patch("app.incident.investigator.get_graph_client"):

        mock_query.return_value = {
            "incidents": [fake_incident],
            "investigations": [],
            "goals": [],
            "evidence_paths": ["Incident(inc_agent_01)"],
            "provenance": ["incident:inc_agent_01"],
        }

        resp = investigate_incident(req=req, mock_runner=mock_successful_agent)

        assert resp.investigation_mode == "agentic"
        assert resp.found_evidence is True
        assert len(resp.agent_execution_trace) == 1
        assert resp.agent_deliberation == "Agent synthesis: Payment gateway socket timeout identified in prior incident."
        assert resp.investigation_summary["affected_component"] == "payment-gateway"


# ============================================================================
# 7. Required Evaluation Scenarios: Payment, Gateway, CI Failure, Abstention
# ============================================================================

def test_scenario_payment_timeouts():
    """Scenario 1: Payment timeouts with prior unsuccessful intervention."""
    req = IncidentQueryRequest(
        user_id="tenant_payments",
        symptoms="Payment gateway 504 timeout on checkout charge",
        component="payment-gateway",
    )

    fake_incident = IncidentNode(
        id="inc_pay_99",
        title="Payment Service Checkout Timeout",
        symptoms="Payment gateway 504 timeout on checkout charge",
        affected_component="payment-gateway",
        severity="critical",
        timestamp="2026-03-10T10:00:00Z",
        user_id="tenant_payments",
    )
    fake_investigation = PriorInvestigationSummary(
        incident_id="inc_pay_99",
        incident_title="Payment Service Checkout Timeout",
        symptoms="Payment gateway 504 timeout on checkout charge",
        component="payment-gateway",
        investigation_id="inv_pay_99",
        hypothesis="Connection pool saturation to payment processor",
        fix_attempt_id="fix_pay_pool",
        fix_description="Increase connection pool size to 50",
        fix_status="executed",
        outcome_id="out_pay_pool",
        outcome_evaluation="unsuccessful",
        outcome_metric="p99_latency_ms",
        before_value="950ms",
        after_value="940ms",
    )

    def mock_agent_runner(agent: IncidentInvestigationAgent, prompt: str) -> str:
        agent.search_incident_history(
            symptoms="Payment gateway 504 timeout on checkout charge",
            component="payment-gateway",
        )
        return (
            "Payment service previously experienced checkout timeouts. "
            "A prior intervention to increase connection pool size to 50 was evaluated as UNSUCCESSFUL. "
            "Do NOT re-attempt increasing pool size. Recommend profiling processor socket timeouts."
        )

    with patch("app.incident.agent.query_incident_graph") as mock_query, \
         patch("app.incident.investigator.get_graph_client"):

        mock_query.return_value = {
            "incidents": [fake_incident],
            "investigations": [fake_investigation],
            "goals": [{"id": "g1", "title": "P99 < 150ms", "status": "current"}],
            "evidence_paths": ["Incident(inc_pay_99) -> FixAttempt(fix_pay_pool)"],
            "provenance": ["incident:inc_pay_99", "fix:fix_pay_pool"],
        }

        resp = investigate_incident(req=req, mock_runner=mock_agent_runner)

        assert resp.found_evidence is True
        assert resp.investigation_mode == "agentic"
        assert len(resp.unsuccessful_fixes_to_avoid) == 1
        assert "Increase connection pool size to 50" in resp.unsuccessful_fixes_to_avoid[0]
        assert "Do NOT re-attempt" in resp.recommendation
        assert resp.investigation_summary["affected_component"] == "payment-gateway"


def test_scenario_gateway_timeouts():
    """Scenario 2: Gateway API latency and timeouts."""
    req = IncidentQueryRequest(
        user_id="tenant_gateway",
        symptoms="Gateway latency spike and connection timeout",
        component="gateway-api",
    )

    fake_incident = IncidentNode(
        id="inc_gw_01",
        title="Gateway API Tail Latency",
        symptoms="Gateway latency spike and connection timeout",
        affected_component="gateway-api",
        severity="high",
        timestamp="2026-03-10T10:00:00Z",
        user_id="tenant_gateway",
    )

    def mock_agent_runner(agent: IncidentInvestigationAgent, prompt: str) -> str:
        agent.search_incident_history(
            symptoms="Gateway latency spike and connection timeout",
            component="gateway-api",
        )
        return "Gateway latency spike verified in graph history. Recommend profiling request execution trace."

    with patch("app.incident.agent.query_incident_graph") as mock_query, \
         patch("app.incident.investigator.get_graph_client"):

        mock_query.return_value = {
            "incidents": [fake_incident],
            "investigations": [],
            "goals": [],
            "evidence_paths": ["Incident(inc_gw_01)"],
            "provenance": ["incident:inc_gw_01"],
        }

        resp = investigate_incident(req=req, mock_runner=mock_agent_runner)

        assert resp.found_evidence is True
        assert resp.investigation_mode == "agentic"
        assert resp.investigation_summary["affected_component"] == "gateway-api"
        assert "gateway-api" in resp.recommended_next_action.action


def test_scenario_failed_ci_workflow():
    """Scenario 3: Failed CI workflows connected to repository history."""
    req = IncidentQueryRequest(
        user_id="tenant_ci",
        symptoms="CI workflow run failure on test suite",
        repository="mai/core-service",
    )

    fake_wf_record = GitHubRecordEvidence(
        id="gh_wf_991",
        record_type="workflow_run",
        title_or_summary="CI Integration Tests",
        state_or_status="failure",
        html_url="https://github.com/mai/core-service/actions/runs/991",
        repository="mai/core-service",
        evidence_category="direct",
        relevance_reason="Failing workflow run matches CI failure symptoms",
    )

    def mock_agent_runner(agent: IncidentInvestigationAgent, prompt: str) -> str:
        agent.retrieve_github_evidence(
            symptoms="CI workflow run failure on test suite",
            repository="mai/core-service",
        )
        return "Connected failing CI workflow run gh_wf_991 retrieved for mai/core-service."

    with patch("app.incident.agent.query_github_evidence_for_incident") as mock_gh, \
         patch("app.incident.investigator.get_graph_client"):

        mock_gh.return_value = (
            [fake_wf_record],
            ["Repository(mai/core-service) -> WorkflowRun(gh_wf_991)"],
            {"github:workflow_run:gh_wf_991"},
        )

        resp = investigate_incident(req=req, mock_runner=mock_agent_runner)

        assert resp.found_evidence is True
        assert resp.investigation_mode == "agentic"
        assert len(resp.retrieved_github_records) == 1
        assert resp.retrieved_github_records[0]["title_or_summary"] == "CI Integration Tests"
        assert "Connected Repository Evidence" in resp.recommendation_rationale


def test_scenario_unrelated_no_evidence_query():
    """Scenario 4: Completely unrelated query with no evidence (mandatory abstention)."""
    req = IncidentQueryRequest(
        user_id="tenant_any",
        symptoms="Office elevator motor overheated on 4th floor",
        component="facility-elevator",
    )

    def mock_agent_runner(agent: IncidentInvestigationAgent, prompt: str) -> str:
        agent.search_incident_history(
            symptoms="Office elevator motor overheated on 4th floor",
            component="facility-elevator",
        )
        return "No matching incidents or telemetry found in FalkorDB. Abstaining from recommendation."

    with patch("app.incident.agent.query_incident_graph") as mock_query:
        mock_query.return_value = {
            "incidents": [],
            "investigations": [],
            "goals": [],
            "evidence_paths": [],
            "provenance": [],
        }

        resp = investigate_incident(req=req, mock_runner=mock_agent_runner)

        assert resp.found_evidence is False
        assert resp.recommendation is None
        assert resp.recommended_next_action is None
        assert "Abstaining from ungrounded recommendation" in resp.insufficient_evidence_reason
        assert resp.investigation_mode == "agentic"
        assert len(resp.agent_execution_trace) == 1


# ============================================================================
# 8. Regression Tests: Protocol Formatting, Sanitized Logging, Bounded Retries & Fallback
# ============================================================================

def test_sanitize_turn_request_excludes_secrets():
    """Verify that sanitized turn request logging excludes all API keys and secrets."""
    from app.incident.agent import _sanitize_turn_request
    from google.genai import types

    secret_key = "AIzaSySecretApiKey12345"
    content_user = types.Content(
        role="user",
        parts=[types.Part.from_text(text=f"Check secret: {secret_key}")]
    )
    content_tool = types.Content(
        role="tool",
        parts=[
            types.Part.from_function_response(
                name="search_incident_history",
                response={"result": "Retrieved 1 incident"}
            )
        ]
    )

    sanitized = _sanitize_turn_request(turn=2, contents=[content_user, content_tool], tools=["dummy_tool"])

    sanitized_str = str(sanitized)
    assert secret_key not in sanitized_str
    assert sanitized["turn"] == 2
    assert sanitized["contents_count"] == 2
    assert sanitized["contents"][0]["role"] == "user"
    assert sanitized["contents"][1]["role"] == "tool"
    assert sanitized["contents"][1]["parts"][0]["name"] == "search_incident_history"
    assert "available_tools" in sanitized


def test_transient_error_bounded_retry_behavior():
    """Verify bounded retry with exponential backoff on 500/503 server errors."""
    from app.incident.agent import _call_model_with_retry, _is_transient_error

    # 1. Verify transient error classification
    class FakeServerError(Exception):
        status_code = 503
    class FakeClientError(Exception):
        status_code = 400

    assert _is_transient_error(FakeServerError("Unavailable")) is True
    assert _is_transient_error(RuntimeError("500 Internal error encountered")) is True
    assert _is_transient_error(FakeClientError("Bad Request")) is False

    # 2. Test retry recovery
    mock_client = MagicMock()
    fake_success = MagicMock(text="Model final output")
    mock_client.models.generate_content.side_effect = [
        FakeServerError("Temporary spike"),
        fake_success,
    ]

    with patch("time.sleep") as mock_sleep:
        resp = _call_model_with_retry(
            client=mock_client,
            model="gemma-4-31b-it",
            contents=[],
            config=None,
            max_retries=2,
            initial_backoff=0.1,
        )
        assert resp == fake_success
        assert mock_client.models.generate_content.call_count == 2
        mock_sleep.assert_called_once_with(0.1)

    # 3. Test non-transient error is NOT retried
    mock_client.reset_mock()
    mock_client.models.generate_content.side_effect = FakeClientError("Bad Request")
    with pytest.raises(FakeClientError):
        _call_model_with_retry(
            client=mock_client,
            model="gemma-4-31b-it",
            contents=[],
            config=None,
            max_retries=2,
        )
    assert mock_client.models.generate_content.call_count == 1

    # 4. Test retry limit exceeded (strict bound, no infinite loop)
    mock_client.reset_mock()
    mock_client.models.generate_content.side_effect = FakeServerError("Permanent outage")
    with patch("time.sleep"):
        with pytest.raises(FakeServerError):
            _call_model_with_retry(
                client=mock_client,
                model="gemma-4-31b-it",
                contents=[],
                config=None,
                max_retries=2,
                initial_backoff=0.01,
            )
    assert mock_client.models.generate_content.call_count == 3  # Initial + 2 retries


def test_agent_second_turn_function_response_protocol():
    """Verify that Turn 2 sends role='tool' and Part.from_function_response for every function call."""
    from google.genai import types

    agent = IncidentInvestigationAgent(
        user_id="tenant_alpha",
        target_component="gateway-api",
    )

    mock_client = MagicMock()
    # Turn 1: Model requests search_incident_history
    fc1 = types.FunctionCall(
        name="search_incident_history",
        args={"symptoms": "gateway timeout", "component": "gateway-api"}
    )
    turn1_candidate = types.Candidate(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=fc1)]
        )
    )
    turn1_resp = types.GenerateContentResponse(
        candidates=[turn1_candidate],
    )

    # Turn 2: Model returns deliberation text
    turn2_resp = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="Deliberation based on retrieved incident.")]
                )
            )
        ]
    )

    mock_client.models.generate_content.side_effect = [turn1_resp, turn2_resp]
    agent._client = mock_client

    with patch("app.incident.agent.query_incident_graph") as mock_query:
        mock_query.return_value = {
            "incidents": [
                IncidentNode(
                    id="inc_01",
                    title="Prior Gateway Timeout",
                    symptoms="gateway timeout",
                    affected_component="gateway-api",
                    user_id="tenant_alpha",
                )
            ],
            "investigations": [],
            "goals": [],
            "evidence_paths": [],
            "provenance": [],
        }

        ctx, trace, deliberation, success = agent.run("gateway timeout on gateway-api")

        assert success is True
        assert deliberation == "Deliberation based on retrieved incident."
        assert mock_client.models.generate_content.call_count == 2

        # Check call arguments of Turn 2:
        turn2_call = mock_client.models.generate_content.call_args_list[1]
        turn2_contents = turn2_call.kwargs["contents"]

        assert len(turn2_contents) == 3
        assert turn2_contents[0].role == "user"
        assert turn2_contents[1].role == "model"
        # Turn 2 MUST have role="tool"
        assert turn2_contents[2].role == "tool"
        tool_part = turn2_contents[2].parts[0]
        assert tool_part.function_response is not None
        assert tool_part.function_response.name == "search_incident_history"
        assert "Prior Gateway Timeout" in str(tool_part.function_response.response)


def test_second_turn_compatible_agent_fallback_path():
    """Verify that if Turn 2 role='tool' fails on server, compatible observation path is activated."""
    from google.genai import types

    agent = IncidentInvestigationAgent(
        user_id="tenant_alpha",
        target_component="gateway-api",
    )

    mock_client = MagicMock()
    # Turn 1: Model requests search_incident_history
    fc1 = types.FunctionCall(
        name="search_incident_history",
        args={"symptoms": "gateway timeout", "component": "gateway-api"}
    )
    turn1_candidate = types.Candidate(
        content=types.Content(
            role="model",
            parts=[types.Part(function_call=fc1)]
        )
    )
    turn1_resp = types.GenerateContentResponse(candidates=[turn1_candidate])

    # Turn 2: Fails with 500 Internal error
    class Server500Error(Exception):
        status_code = 500

    # Turn 2 fallback: Succeeds with grounded analysis
    fallback_resp = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="Grounded analysis from tool findings.")]
                )
            )
        ]
    )

    # We patch _call_model_with_retry to simulate:
    # 1st call: Turn 1 success
    # 2nd call: Turn 2 failure with Server500Error
    # 3rd call: Fallback success with fallback_resp
    call_counts = [0]
    def mock_call_with_retry(client, model, contents, config, **kwargs):
        call_counts[0] += 1
        if call_counts[0] == 1:
            return turn1_resp
        elif call_counts[0] == 2:
            raise Server500Error("500 INTERNAL: Internal error encountered.")
        else:
            return fallback_resp

    agent._client = mock_client

    with patch("app.incident.agent._call_model_with_retry", side_effect=mock_call_with_retry), \
         patch("app.incident.agent.query_incident_graph") as mock_query:

        mock_query.return_value = {
            "incidents": [
                IncidentNode(
                    id="inc_01",
                    title="Historical Gateway Latency Spike",
                    symptoms="gateway timeout",
                    affected_component="gateway-api",
                    user_id="tenant_alpha",
                )
            ],
            "investigations": [],
            "goals": [],
            "evidence_paths": [],
            "provenance": [],
        }

        ctx, trace, deliberation, success = agent.run("gateway timeout on gateway-api")

        assert success is True
        assert deliberation == "Grounded analysis from tool findings."
        assert len(trace) == 1
        assert trace[0]["tool"] == "search_incident_history"
        assert len(ctx.incidents) == 1

