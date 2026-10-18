"""
Verification script for GitHub Engineering Memory Integration on live repository.
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.graph.connection import get_graph_client
from app.github.operations import query_github_evidence_for_incident

g = get_graph_client()
repo_id = "gh_repo_srisuriya13022006_engineering-incident-lab"

print("=== 1. PR #2 COMMITS (CONTAINS_COMMIT) ===")
res_pr_com = g.ro_query("""
MATCH (pr:PullRequest)-[:CONTAINS_COMMIT]->(com:Commit)
WHERE pr.repo_id = $repo_id AND pr.number = 2
RETURN pr.number, com.sha, com.message, com.author
ORDER BY com.sha
""", params={"repo_id": repo_id})
for row in res_pr_com.result_set:
    print("PR #2 Commit:", row)

print("\n=== 2. WORKFLOW RUN #38904009974 (TRIGGERED_BY & TESTS_PULL_REQUEST) ===")
res_wf = g.ro_query("""
MATCH (wf:WorkflowRun)
WHERE wf.repo_id = $repo_id AND wf.run_id = 38904009974
OPTIONAL MATCH (wf)-[:TRIGGERED_BY]->(com:Commit)
OPTIONAL MATCH (wf)-[:TESTS_PULL_REQUEST]->(pr:PullRequest)
RETURN wf.run_id, wf.conclusion, wf.event, com.sha, pr.number
""", params={"repo_id": repo_id})
for row in res_wf.result_set:
    print("Workflow Run #38904009974:", row)

print("\n=== 3. COMPLETE END-TO-END TRACE (ISSUE -> PR -> COMMIT <- WORKFLOW RUN) ===")
res_trace = g.ro_query("""
MATCH (iss:GitHubIssue)<-[:REFERENCES_ISSUE]-(pr:PullRequest)-[:CONTAINS_COMMIT]->(com:Commit)<-[:TRIGGERED_BY]-(wf:WorkflowRun)
WHERE iss.repo_id = $repo_id AND iss.number = 1
RETURN iss.number, iss.title, pr.number, pr.title, com.sha, com.message, wf.run_id, wf.name, wf.conclusion
""", params={"repo_id": repo_id})
for row in res_trace.result_set:
    print("End-to-End Trace:", row)

print("\n=== 4. DIRECT TESTS_PULL_REQUEST TRACE ===")
res_direct = g.ro_query("""
MATCH (wf:WorkflowRun)-[:TESTS_PULL_REQUEST]->(pr:PullRequest)-[:REFERENCES_ISSUE]->(iss:GitHubIssue)
WHERE pr.repo_id = $repo_id
RETURN wf.run_id, wf.conclusion, pr.number, iss.number
""", params={"repo_id": repo_id})
for row in res_direct.result_set:
    print("Direct WF-to-PR-to-Issue:", row)

print("\n=== 5. RETRIEVED EVIDENCE FOR INCIDENT INVESTIGATION ===")
records, paths, traces = query_github_evidence_for_incident(
    user_id="user_alex_rivera_demo",
    keywords=["payment", "timeout", "concurrency"],
    component="payment-api",
)
print(f"Retrieved {len(records)} records, {len(paths)} paths:")
for p in paths:
    print("  Path:", p)
for r in records:
    print(f"  [{r.record_type.upper()}] {r.title_or_summary} ({r.state_or_status}): {r.notes or r.relevance_reason}")
