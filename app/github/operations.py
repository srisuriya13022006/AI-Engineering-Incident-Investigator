"""
FalkorDB graph database operations for GitHub Engineering Memory Integration.

Provides parameterized, user-scoped Cypher queries to idempotently store
repositories, issues, pull requests, commits, and workflow runs, and retrieve
connected code evidence during incident investigations.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import RedisError

from app.graph.connection import get_graph_client, FalkorDBConnectionError
from app.github.models import (
    GitHubRepoNode,
    GitHubIssueNode,
    PullRequestNode,
    CommitNode,
    WorkflowRunNode,
    GitHubRecordEvidence,
    GitHubRelationshipTrace,
    GitHubMissingRelationship,
    GitHubRepositoryDetails,
)

logger = logging.getLogger("incident.github.operations")


def ingest_github_repository_data(
    user_id: str,
    repo_node: GitHubRepoNode,
    issues: List[GitHubIssueNode],
    prs: List[PullRequestNode],
    commits: List[CommitNode],
    workflow_runs: List[WorkflowRunNode],
    graph_name: Optional[str] = None,
) -> Dict[str, int]:
    """
    Idempotently stores repository and associated GitHub engineering records
    into FalkorDB, scoped to user_id.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    counts = {
        "issues": 0,
        "prs": 0,
        "commits": 0,
        "workflow_runs": 0,
        "references": 0,
    }

    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database failure connecting to FalkorDB for GitHub import: {exc}") from exc

    # 1. Upsert User and Repository
    repo_cypher = """
    MERGE (u:User {id: $user_id})
    ON CREATE SET u.created_at = $now_iso

    MERGE (repo:Repository {id: $repo_id})
    ON CREATE SET
        repo.owner = $owner,
        repo.name = $name,
        repo.full_name = $full_name,
        repo.description = $description,
        repo.html_url = $html_url,
        repo.default_branch = $default_branch,
        repo.open_issues_count = $open_issues_count,
        repo.user_id = $user_id,
        repo.imported_at = $now_iso
    ON MATCH SET
        repo.description = $description,
        repo.open_issues_count = $open_issues_count,
        repo.updated_at = $now_iso

    MERGE (u)-[r:HAS_REPOSITORY]->(repo)
    ON CREATE SET r.created_at = $now_iso
    """
    graph.query(
        repo_cypher,
        params={
            "user_id": user_id,
            "repo_id": repo_node.id,
            "owner": repo_node.owner,
            "name": repo_node.name,
            "full_name": repo_node.full_name,
            "description": repo_node.description or "",
            "html_url": repo_node.html_url,
            "default_branch": repo_node.default_branch,
            "open_issues_count": repo_node.open_issues_count,
            "now_iso": now_iso,
        },
    )

    # 2. Ingest Issues & link (repo)-[:CONTAINS_ISSUE]->(issue)
    for issue in issues:
        issue_cypher = """
        MATCH (repo:Repository {id: $repo_id})
        MERGE (iss:GitHubIssue {id: $issue_id})
        ON CREATE SET
            iss.repo_id = $repo_id,
            iss.number = $number,
            iss.title = $title,
            iss.body = $body,
            iss.state = $state,
            iss.labels = $labels,
            iss.html_url = $html_url,
            iss.author = $author,
            iss.created_at = $created_at,
            iss.updated_at = $updated_at,
            iss.user_id = $user_id
        ON MATCH SET
            iss.title = $title,
            iss.body = $body,
            iss.state = $state,
            iss.labels = $labels,
            iss.updated_at = $updated_at

        MERGE (repo)-[r:CONTAINS_ISSUE]->(iss)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(
            issue_cypher,
            params={
                "repo_id": repo_node.id,
                "issue_id": issue.id,
                "number": issue.number,
                "title": issue.title,
                "body": issue.body or "",
                "state": issue.state,
                "labels": ",".join(issue.labels),
                "html_url": issue.html_url,
                "author": issue.author or "",
                "created_at": issue.created_at or now_iso,
                "updated_at": issue.updated_at or now_iso,
                "user_id": user_id,
                "now_iso": now_iso,
            },
        )
        counts["issues"] += 1

    # 3. Ingest Pull Requests & link (repo)-[:HAS_PULL_REQUEST]->(pr)
    for pr in prs:
        pr_cypher = """
        MATCH (repo:Repository {id: $repo_id})
        MERGE (pr:PullRequest {id: $pr_id})
        ON CREATE SET
            pr.repo_id = $repo_id,
            pr.number = $number,
            pr.title = $title,
            pr.body = $body,
            pr.state = $state,
            pr.merged = $merged,
            pr.html_url = $html_url,
            pr.head_sha = $head_sha,
            pr.base_ref = $base_ref,
            pr.author = $author,
            pr.created_at = $created_at,
            pr.updated_at = $updated_at,
            pr.user_id = $user_id
        ON MATCH SET
            pr.title = $title,
            pr.body = $body,
            pr.state = $state,
            pr.merged = $merged,
            pr.updated_at = $updated_at

        MERGE (repo)-[r:HAS_PULL_REQUEST]->(pr)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(
            pr_cypher,
            params={
                "repo_id": repo_node.id,
                "pr_id": pr.id,
                "number": pr.number,
                "title": pr.title,
                "body": pr.body or "",
                "state": pr.state,
                "merged": pr.merged,
                "html_url": pr.html_url,
                "head_sha": pr.head_sha or "",
                "base_ref": pr.base_ref or "",
                "author": pr.author or "",
                "created_at": pr.created_at or now_iso,
                "updated_at": pr.updated_at or now_iso,
                "user_id": user_id,
                "now_iso": now_iso,
            },
        )
        counts["prs"] += 1

        # Explicit issue references from PR body: (pr)-[:REFERENCES_ISSUE]->(issue)
        for ref_num in pr.referenced_issue_numbers:
            ref_cypher = """
            MATCH (pr:PullRequest {id: $pr_id})
            MATCH (iss:GitHubIssue)
            WHERE iss.repo_id = $repo_id AND iss.number = $ref_num
            MERGE (pr)-[r:REFERENCES_ISSUE]->(iss)
            ON CREATE SET r.created_at = $now_iso
            """
            graph.query(
                ref_cypher,
                params={
                    "pr_id": pr.id,
                    "repo_id": repo_node.id,
                    "ref_num": ref_num,
                    "now_iso": now_iso,
                },
            )
            counts["references"] += 1

    # 4. Ingest Commits & link (repo)-[:CONTAINS_COMMIT]->(commit)
    for commit in commits:
        commit_cypher = """
        MATCH (repo:Repository {id: $repo_id})
        MERGE (com:Commit {id: $commit_id})
        ON CREATE SET
            com.repo_id = $repo_id,
            com.sha = $sha,
            com.message = $message,
            com.author = $author,
            com.html_url = $html_url,
            com.timestamp = $ts,
            com.user_id = $user_id
        ON MATCH SET
            com.message = $message,
            com.timestamp = $ts

        MERGE (repo)-[r:CONTAINS_COMMIT]->(com)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(
            commit_cypher,
            params={
                "repo_id": repo_node.id,
                "commit_id": commit.id,
                "sha": commit.sha,
                "message": commit.message,
                "author": commit.author or "",
                "html_url": commit.html_url,
                "ts": commit.timestamp or now_iso,
                "user_id": user_id,
                "now_iso": now_iso,
            },
        )
        counts["commits"] += 1

    # 4b. Link PullRequests to their Commits via CONTAINS_COMMIT
    for pr in prs:
        all_pr_shas = set(pr.commit_shas)
        if pr.head_sha:
            all_pr_shas.add(pr.head_sha)

        for c_sha in all_pr_shas:
            link_pr_commit = """
            MATCH (pr:PullRequest {id: $pr_id})
            MATCH (com:Commit {repo_id: $repo_id})
            WHERE com.sha = $sha OR com.sha STARTS WITH $sha OR $sha STARTS WITH com.sha
            MERGE (pr)-[r:CONTAINS_COMMIT]->(com)
            ON CREATE SET r.created_at = $now_iso
            """
            graph.query(
                link_pr_commit,
                params={
                    "pr_id": pr.id,
                    "repo_id": repo_node.id,
                    "sha": c_sha,
                    "now_iso": now_iso,
                },
            )

    # 5. Ingest Workflow Runs & link (repo)-[:HAS_WORKFLOW_RUN]->(wf)
    for wf in workflow_runs:
        wf_cypher = """
        MATCH (repo:Repository {id: $repo_id})
        MERGE (run:WorkflowRun {id: $wf_id})
        ON CREATE SET
            run.repo_id = $repo_id,
            run.run_id = $run_id,
            run.name = $name,
            run.event = $event,
            run.status = $status,
            run.conclusion = $conclusion,
            run.html_url = $html_url,
            run.head_sha = $head_sha,
            run.created_at = $created_at,
            run.updated_at = $updated_at,
            run.user_id = $user_id
        ON MATCH SET
            run.status = $status,
            run.conclusion = $conclusion,
            run.updated_at = $updated_at

        MERGE (repo)-[r:HAS_WORKFLOW_RUN]->(run)
        ON CREATE SET r.created_at = $now_iso
        """
        graph.query(
            wf_cypher,
            params={
                "repo_id": repo_node.id,
                "wf_id": wf.id,
                "run_id": wf.run_id,
                "name": wf.name,
                "event": wf.event,
                "status": wf.status,
                "conclusion": wf.conclusion or "",
                "html_url": wf.html_url,
                "head_sha": wf.head_sha or "",
                "created_at": wf.created_at or now_iso,
                "updated_at": wf.updated_at or now_iso,
                "user_id": user_id,
                "now_iso": now_iso,
            },
        )
        counts["workflow_runs"] += 1

        # Link WorkflowRun to Commit via TRIGGERED_BY if commit exists
        if wf.head_sha:
            link_wf_commit = """
            MATCH (run:WorkflowRun {id: $wf_id})
            MATCH (com:Commit {repo_id: $repo_id})
            WHERE com.sha = $sha OR com.sha STARTS WITH $sha OR $sha STARTS WITH com.sha
            MERGE (run)-[r:TRIGGERED_BY]->(com)
            ON CREATE SET r.created_at = $now_iso
            """
            graph.query(
                link_wf_commit,
                params={
                    "wf_id": wf.id,
                    "repo_id": repo_node.id,
                    "sha": wf.head_sha,
                    "now_iso": now_iso,
                },
            )

        # Link WorkflowRun to PullRequest via TESTS_PULL_REQUEST only when supported by GitHub metadata
        if wf.event == "pull_request":
            for pr_num in wf.pull_request_numbers:
                link_wf_pr_num = """
                MATCH (run:WorkflowRun {id: $wf_id})
                MATCH (pr:PullRequest)
                WHERE pr.repo_id = $repo_id AND pr.number = $pr_num
                MERGE (run)-[r:TESTS_PULL_REQUEST]->(pr)
                ON CREATE SET r.created_at = $now_iso
                """
                graph.query(
                    link_wf_pr_num,
                    params={
                        "wf_id": wf.id,
                        "repo_id": repo_node.id,
                        "pr_num": pr_num,
                        "now_iso": now_iso,
                    },
                )

            if wf.head_sha:
                link_wf_pr_sha = """
                MATCH (run:WorkflowRun {id: $wf_id})
                MATCH (pr:PullRequest)
                WHERE pr.repo_id = $repo_id AND (pr.head_sha = $head_sha OR pr.head_sha STARTS WITH $head_sha OR $head_sha STARTS WITH pr.head_sha)
                MERGE (run)-[r:TESTS_PULL_REQUEST]->(pr)
                ON CREATE SET r.created_at = $now_iso
                """
                graph.query(
                    link_wf_pr_sha,
                    params={
                        "wf_id": wf.id,
                        "repo_id": repo_node.id,
                        "head_sha": wf.head_sha,
                    },
                )
    try:
        # Schedule background save to persist graph to disk volume
        graph.execute_command("BGSAVE")
    except Exception:
        pass

    return counts


# Common operational and engineering words that must not by themselves establish a connection
GENERIC_INCIDENT_WORDS: Set[str] = {
    "api", "service", "system", "app", "application", "server", "servers",
    "traffic", "peak", "during", "latency", "timeout", "timeouts",
    "connection", "connections", "pool", "pooling", "slow", "high", "low",
    "error", "errors", "failed", "failure", "fail", "alert", "alerts",
    "request", "requests", "response", "responses", "issue", "issues",
    "bug", "bugs", "automated", "checks", "check", "run", "runs",
    "workflow", "workflows", "action", "actions", "job", "jobs", "test", "tests",
    "testing", "log", "logs", "metrics", "metric", "rate", "latest", "code", "branch",
}


def query_github_evidence_for_incident(
    user_id: str,
    keywords: List[str],
    component: Optional[str] = None,
    target_repo: Optional[str] = None,
    symptoms_text: Optional[str] = None,
    graph_name: Optional[str] = None,
) -> Tuple[List[GitHubRecordEvidence], List[str], Set[str]]:
    """
    Retrieves connected GitHub issues, PRs, commits, and workflow runs
    scoped strictly to user_id that match incident symptoms, component, or target repository.

    Follows multi-hop causal graph relationships:
    - (pr)-[:REFERENCES_ISSUE]->(issue)
    - (pr)-[:CONTAINS_COMMIT]->(commit)
    - (run)-[:TRIGGERED_BY]->(commit)
    - (run)-[:TESTS_PULL_REQUEST]->(pr)
    - (repo)-[:DEPENDS_ON]->(upstream_repo)

    Ranks evidence by relevance, enforces repository and component constraints,
    and prevents irrelevant cross-repository contamination from generic tokens like 'api' or 'timeout'.
    """
    records: List[GitHubRecordEvidence] = []
    evidence_paths: List[str] = []
    provenance_traces: Set[str] = set()

    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError):
        logger.warning("FalkorDB offline or unreachable during GitHub evidence query")
        return records, evidence_paths, provenance_traces

    comp_clean = component.strip().lower() if component else ""
    raw_kw_list = [k.lower() for k in keywords if len(k) > 1] if keywords else []
    domain_kws = [k for k in raw_kw_list if k not in GENERIC_INCIDENT_WORDS and len(k) > 2]

    # Detect explicit identifiers in symptoms text
    sym_lower = (symptoms_text or "").lower()
    issue_nums = [int(n) for n in re.findall(r"#(\d+)", sym_lower)]
    commit_shas = [s for s in re.findall(r"\b([0-9a-f]{7,40})\b", sym_lower)]
    workflow_ids = [s for s in re.findall(r"\b(\d{7,15})\b", sym_lower)]
    is_ci_failure_query = (
        any(w in sym_lower for w in ["workflow", "checks", "ci", "automated"])
        and any(w in sym_lower for w in ["fail", "failed", "failure", "broken"])
    )

    # 1. Resolve target repository constraint
    effective_target_repo = target_repo.strip() if target_repo else None
    if not effective_target_repo and symptoms_text:
        match_repo = re.search(r"\b([a-zA-Z0-9_\-\.]+/[a-zA-Z0-9_\-\.]+)\b", symptoms_text)
        if match_repo:
            candidate_slug = match_repo.group(1).lower()
            chk_cypher = """
            MATCH (u:User {id: $user_id})-[:HAS_REPOSITORY]->(repo:Repository)
            WHERE toLower(repo.full_name) = $slug
            RETURN repo.full_name LIMIT 1
            """
            chk_res = graph.ro_query(chk_cypher, params={"user_id": user_id, "slug": candidate_slug})
            if chk_res.result_set:
                effective_target_repo = chk_res.result_set[0][0]

    # 2. Identify candidate repositories matching component, domain keywords, or explicit scope
    candidate_repos: Set[str] = set()
    if effective_target_repo:
        candidate_repos.add(effective_target_repo)
    else:
        all_repos_cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_REPOSITORY]->(repo:Repository)
        RETURN repo.full_name AS full_name, toLower(repo.name) AS name, toLower(repo.description) AS desc
        """
        all_repos_res = graph.ro_query(all_repos_cypher, params={"user_id": user_id})
        # Step 2a: If component is explicitly specified, prioritize repositories matching the component
        if comp_clean:
            for r_full, r_name, _ in all_repos_res.result_set:
                if comp_clean in r_name or comp_clean in r_full.lower():
                    candidate_repos.add(r_full)

        # Step 2b: If no component matched (or no component provided), resolve candidates via domain keywords, issues, or CI runs
        if not candidate_repos:
            for r_full, r_name, r_desc in all_repos_res.result_set:
                r_desc_val = r_desc or ""
                # Match domain keywords in repository name or description
                if domain_kws and any(dk in r_name or dk in r_full.lower() or dk in r_desc_val for dk in domain_kws):
                    candidate_repos.add(r_full)
                    continue
                # Match domain keywords or issue numbers in repository issues
                if domain_kws or issue_nums:
                    repo_iss_chk = """
                    MATCH (repo:Repository {full_name: $r_full})-[:CONTAINS_ISSUE]->(iss:GitHubIssue)
                    WHERE ($has_nums AND iss.number IN $issue_nums)
                       OR ($has_kws AND any(dk IN $domain_kws WHERE toLower(iss.title) CONTAINS dk OR toLower(iss.body) CONTAINS dk))
                    RETURN iss.id LIMIT 1
                    """
                    iss_chk_res = graph.ro_query(
                        repo_iss_chk,
                        params={
                            "r_full": r_full,
                            "has_nums": bool(issue_nums),
                            "issue_nums": issue_nums or [0],
                            "has_kws": bool(domain_kws),
                            "domain_kws": domain_kws or [""],
                        },
                    )
                    if iss_chk_res.result_set:
                        candidate_repos.add(r_full)
                        continue
                # Match failed workflows if this is a CI failure query
                if is_ci_failure_query:
                    wf_chk = """
                    MATCH (repo:Repository {full_name: $r_full})-[:HAS_WORKFLOW_RUN]->(wf:WorkflowRun)
                    WHERE wf.conclusion IN ['failure', 'timed_out', 'cancelled']
                    RETURN wf.id LIMIT 1
                    """
                    wf_res = graph.ro_query(wf_chk, params={"r_full": r_full})
                    if wf_res.result_set:
                        candidate_repos.add(r_full)
                        continue

        # If this is specifically a query for the "latest" or "recent" failing CI run, focus on the repository with the latest failure
        if is_ci_failure_query and any(w in sym_lower for w in ["latest", "recent"]) and candidate_repos:
            latest_wf_cypher = """
            MATCH (u:User {id: $user_id})-[:HAS_REPOSITORY]->(repo:Repository)-[:HAS_WORKFLOW_RUN]->(wf:WorkflowRun)
            WHERE repo.full_name IN $candidate_repos
              AND wf.conclusion IN ['failure', 'timed_out', 'cancelled']
            RETURN repo.full_name AS repo_name, wf.created_at AS created_at
            ORDER BY wf.created_at DESC LIMIT 1
            """
            lwf_res = graph.ro_query(latest_wf_cypher, params={"user_id": user_id, "candidate_repos": list(candidate_repos)})
            if lwf_res.result_set:
                candidate_repos = {lwf_res.result_set[0][0]}

    if not candidate_repos:
        return records, evidence_paths, provenance_traces

    candidate_list = list(candidate_repos)

    seen_prs: Set[str] = set()
    seen_issues: Set[str] = set()
    seen_commits: Set[str] = set()
    seen_wf: Set[str] = set()
    seen_paths: Set[str] = set()
    record_map: Dict[str, GitHubRecordEvidence] = {}

    def link_edge(src_id, tgt_id, rel_type):
        if src_id and tgt_id and str(src_id) in record_map:
            edge_entry = {"target": str(tgt_id), "relationship": str(rel_type)}
            if edge_entry not in record_map[str(src_id)].connected_edges:
                record_map[str(src_id)].connected_edges.append(edge_entry)

    def add_path(p: str):
        if p and p not in seen_paths:
            seen_paths.add(p)
            evidence_paths.append(p)

    def add_pr_record(p_id, p_num, p_title, p_state, p_merged, p_url, p_ts, r_name, category="direct", path=None):
        if not p_id or str(p_id) in seen_prs:
            return
        seen_prs.add(str(p_id))
        st_desc = "merged" if p_merged else str(p_state)
        rec = GitHubRecordEvidence(
            record_type="pull_request",
            id=str(p_id),
            title_or_summary=f"PR #{p_num}: {p_title}",
            state_or_status=st_desc,
            html_url=str(p_url),
            timestamp=str(p_ts) if p_ts else None,
            relevance_reason=f"Pull request in {r_name} modifies code related to incident symptoms or component.",
            is_confirmed_fix=False,
            notes="Candidate code change in history; verify against telemetry before treating as confirmed root cause.",
            repository=r_name,
            evidence_category=category,
            relationship_path=path,
        )
        records.append(rec)
        record_map[str(p_id)] = rec
        provenance_traces.add(f"github_pr:{p_id}")

    def add_issue_record(i_id, i_num, i_title, i_state, i_url, i_ts, r_name, ref_by_pr=None, category="direct", path=None):
        if not i_id or str(i_id) in seen_issues:
            return
        seen_issues.add(str(i_id))
        reason = (
            f"Historical incident issue in {r_name} referenced by PR #{ref_by_pr}."
            if ref_by_pr
            else f"Historical incident issue in {r_name} reports matching symptoms or component."
        )
        rec = GitHubRecordEvidence(
            record_type="issue",
            id=str(i_id),
            title_or_summary=f"Issue #{i_num}: {i_title}",
            state_or_status=str(i_state),
            html_url=str(i_url),
            timestamp=str(i_ts) if i_ts else None,
            relevance_reason=reason,
            is_confirmed_fix=False,
            repository=r_name,
            evidence_category=category,
            relationship_path=path,
        )
        records.append(rec)
        record_map[str(i_id)] = rec
        provenance_traces.add(f"github_issue:{i_id}")

    def add_commit_record(c_id, c_sha, c_msg, c_author, c_url, c_ts, r_name, p_num=None, category="related", path=None):
        if not c_id or not c_sha or str(c_id) in seen_commits:
            return
        seen_commits.add(str(c_id))
        reason = f"Commit on PR #{p_num} branch modifying {r_name}." if p_num else f"Commit in {r_name}."
        rec = GitHubRecordEvidence(
            record_type="commit",
            id=str(c_id),
            title_or_summary=f"Commit {c_sha[:8]}: {c_msg}",
            state_or_status="committed",
            html_url=str(c_url),
            timestamp=str(c_ts) if c_ts else None,
            relevance_reason=reason,
            is_confirmed_fix=False,
            notes=f"Author: {c_author or 'unknown'} | Full SHA: {c_sha}",
            repository=r_name,
            evidence_category=category,
            relationship_path=path,
        )
        records.append(rec)
        record_map[str(c_id)] = rec
        provenance_traces.add(f"github_commit:{c_id}")

    def add_wf_record(w_id, w_run_id, w_name, w_event, w_status, w_conc, w_url, w_head, w_ts, r_name, p_num=None, c_sha=None, c_msg=None, category="related", path=None):
        if not w_id or str(w_id) in seen_wf:
            return
        seen_wf.add(str(w_id))
        conc = str(w_conc or w_status or "unknown")
        note_parts = []
        if p_num:
            note_parts.append(f"Tests PR #{p_num}")
        if c_sha:
            note_parts.append(f"Triggered by commit {c_sha[:8]}: {c_msg or ''}")
        elif w_head:
            note_parts.append(f"Head SHA: {w_head[:8]}")

        if conc.lower() in ("failure", "timed_out", "cancelled"):
            note_parts.append(f"Signal to investigate: CI conclusion '{conc}' on PR branch warrants inspection (diagnostic test signal, not proof of production root cause)")

        rec = GitHubRecordEvidence(
            record_type="workflow_run",
            id=str(w_id),
            title_or_summary=f"Workflow Run '{w_name}' (#{w_run_id})",
            state_or_status=conc,
            html_url=str(w_url),
            timestamp=str(w_ts) if w_ts else None,
            relevance_reason=f"Workflow execution '{w_name}' ({w_event or 'ci'}) in {r_name} concluded with '{conc}'.",
            is_confirmed_fix=False,
            notes=" | ".join(note_parts),
            repository=r_name,
            evidence_category=category,
            relationship_path=path,
        )
        records.append(rec)
        record_map[str(w_id)] = rec
        provenance_traces.add(f"github_wf:{w_id}")

    # 3. Query matching Pull Requests and traverse connected Issues, Commits, and Workflow Runs
    match_kws = domain_kws if domain_kws else raw_kw_list[:4]
    pr_cypher = """
    MATCH (u:User {id: $user_id})-[:HAS_REPOSITORY]->(repo:Repository)-[:HAS_PULL_REQUEST]->(pr:PullRequest)
    WHERE repo.full_name IN $candidate_repos
      AND (
          $is_ci
          OR ($has_pr_nums AND pr.number IN $pr_nums)
          OR ($comp <> "" AND (toLower(pr.title) CONTAINS $comp OR toLower(pr.body) CONTAINS $comp))
          OR ($has_kws AND any(kw IN $keywords WHERE toLower(pr.title) CONTAINS kw OR toLower(pr.body) CONTAINS kw))
          OR ($match_all)
      )
    OPTIONAL MATCH (pr)-[:REFERENCES_ISSUE]->(iss:GitHubIssue)
    OPTIONAL MATCH (pr)-[:CONTAINS_COMMIT]->(com:Commit)
    OPTIONAL MATCH (wf:WorkflowRun)-[:TESTS_PULL_REQUEST]->(pr)
    OPTIONAL MATCH (wf_trig:WorkflowRun)-[:TRIGGERED_BY]->(com)
    RETURN
        repo.full_name AS repo_name,
        pr.id AS pr_id,
        pr.number AS pr_num,
        pr.title AS pr_title,
        pr.state AS pr_state,
        pr.merged AS pr_merged,
        pr.html_url AS pr_url,
        pr.created_at AS pr_ts,
        iss.id AS iss_id,
        iss.number AS iss_num,
        iss.title AS iss_title,
        iss.state AS iss_state,
        iss.html_url AS iss_url,
        iss.created_at AS iss_ts,
        com.id AS com_id,
        com.sha AS com_sha,
        com.message AS com_msg,
        com.author AS com_author,
        com.html_url AS com_url,
        com.timestamp AS com_ts,
        wf.id AS wf_id,
        wf.run_id AS wf_run_id,
        wf.name AS wf_name,
        wf.event AS wf_event,
        wf.status AS wf_status,
        wf.conclusion AS wf_conclusion,
        wf.html_url AS wf_url,
        wf.head_sha AS wf_head_sha,
        wf.created_at AS wf_ts,
        wf_trig.id AS wft_id,
        wf_trig.run_id AS wft_run_id,
        wf_trig.name AS wft_name,
        wf_trig.event AS wft_event,
        wf_trig.status AS wft_status,
        wf_trig.conclusion AS wft_conclusion,
        wf_trig.html_url AS wft_url,
        wf_trig.head_sha AS wft_head_sha,
        wf_trig.created_at AS wft_ts
    LIMIT 30
    """
    res_pr = graph.ro_query(
        pr_cypher,
        params={
            "user_id": user_id,
            "candidate_repos": candidate_list,
            "is_ci": is_ci_failure_query,
            "has_pr_nums": bool(issue_nums),
            "pr_nums": issue_nums or [0],
            "comp": comp_clean,
            "has_kws": bool(match_kws),
            "keywords": match_kws or [""],
            "match_all": bool(effective_target_repo and not match_kws and not comp_clean),
        },
    )

    for row in res_pr.result_set:
        (
            repo_name, pr_id, pr_num, pr_title, pr_state, pr_merged, pr_url, pr_ts,
            iss_id, iss_num, iss_title, iss_state, iss_url, iss_ts,
            com_id, com_sha, com_msg, com_author, com_url, com_ts,
            wf_id, wf_run_id, wf_name, wf_event, wf_status, wf_conclusion, wf_url, wf_head_sha, wf_ts,
            wft_id, wft_run_id, wft_name, wft_event, wft_status, wft_conclusion, wft_url, wft_head_sha, wft_ts,
        ) = row

        status_desc = "merged" if pr_merged else str(pr_state)
        pr_path = f"Repository('{repo_name}') -[HAS_PULL_REQUEST]-> PullRequest(#{pr_num} '{pr_title}' [{status_desc}])"
        add_pr_record(pr_id, pr_num, pr_title, pr_state, pr_merged, pr_url, pr_ts, repo_name, category="direct", path=pr_path)

        if iss_id:
            ref_path = f"{pr_path} -[REFERENCES_ISSUE]-> GitHubIssue(#{iss_num} '{iss_title}')"
            add_issue_record(iss_id, iss_num, iss_title, iss_state, iss_url, iss_ts, repo_name, ref_by_pr=pr_num, category="related", path=ref_path)
            link_edge(pr_id, iss_id, "REFERENCES_ISSUE")
            add_path(ref_path)
        else:
            add_path(pr_path)

        if com_id and com_sha:
            com_path = f"PullRequest(#{pr_num}) -[CONTAINS_COMMIT]-> Commit('{com_sha[:8]}')"
            add_commit_record(com_id, com_sha, com_msg, com_author, com_url, com_ts, repo_name, p_num=pr_num, category="related", path=com_path)
            link_edge(pr_id, com_id, "CONTAINS_COMMIT")
            add_path(com_path)

        if wf_id:
            wf_path = f"WorkflowRun('{wf_name}' [{wf_conclusion}]) -[TESTS_PULL_REQUEST]-> PullRequest(#{pr_num})"
            add_wf_record(wf_id, wf_run_id, wf_name, wf_event, wf_status, wf_conclusion, wf_url, wf_head_sha, wf_ts, repo_name, p_num=pr_num, c_sha=com_sha, c_msg=com_msg, category="related", path=wf_path)
            link_edge(wf_id, pr_id, "TESTS_PULL_REQUEST")
            add_path(wf_path)

        if wft_id and wft_id != wf_id:
            wft_path = f"WorkflowRun('{wft_name}' [{wft_conclusion}]) -[TRIGGERED_BY]-> Commit('{com_sha[:8]}')"
            add_wf_record(wft_id, wft_run_id, wft_name, wft_event, wft_status, wft_conclusion, wft_url, wft_head_sha, wft_ts, repo_name, p_num=pr_num, c_sha=com_sha, c_msg=com_msg, category="related", path=wft_path)
            if com_id:
                link_edge(wft_id, com_id, "TRIGGERED_BY")
            add_path(wft_path)

        if iss_id and com_sha and wft_id:
            add_path(f"GitHubIssue(#{iss_num}) <-[REFERENCES_ISSUE]- PullRequest(#{pr_num}) -[CONTAINS_COMMIT]-> Commit('{com_sha[:8]}') <-[TRIGGERED_BY]- WorkflowRun('{wft_name}' [{wft_conclusion}])")
        elif iss_id and wf_id:
            add_path(f"WorkflowRun('{wf_name}' [{wf_conclusion}]) -[TESTS_PULL_REQUEST]-> PullRequest(#{pr_num}) -[REFERENCES_ISSUE]-> GitHubIssue(#{iss_num})")

    # 4. Query matching Issues and traverse referencing PRs, Commits, and Workflow Runs
    issue_cypher = """
    MATCH (u:User {id: $user_id})-[:HAS_REPOSITORY]->(repo:Repository)-[:CONTAINS_ISSUE]->(iss:GitHubIssue)
    WHERE repo.full_name IN $candidate_repos
      AND NOT $is_ci
      AND (
          ($has_nums AND iss.number IN $issue_nums)
          OR ($comp <> "" AND (toLower(iss.title) CONTAINS $comp OR toLower(iss.body) CONTAINS $comp))
          OR ($has_kws AND any(kw IN $keywords WHERE toLower(iss.title) CONTAINS kw OR toLower(iss.body) CONTAINS kw))
          OR ($match_all)
      )
    OPTIONAL MATCH (iss)<-[:REFERENCES_ISSUE]-(pr:PullRequest)
    OPTIONAL MATCH (pr)-[:CONTAINS_COMMIT]->(com:Commit)
    OPTIONAL MATCH (wf:WorkflowRun)-[:TESTS_PULL_REQUEST]->(pr)
    OPTIONAL MATCH (wf_trig:WorkflowRun)-[:TRIGGERED_BY]->(com)
    RETURN
        repo.full_name AS repo_name,
        iss.id AS iss_id,
        iss.number AS iss_num,
        iss.title AS iss_title,
        iss.state AS iss_state,
        iss.html_url AS iss_url,
        iss.created_at AS iss_ts,
        pr.id AS pr_id,
        pr.number AS pr_num,
        pr.title AS pr_title,
        pr.state AS pr_state,
        pr.merged AS pr_merged,
        pr.html_url AS pr_url,
        pr.created_at AS pr_ts,
        com.id AS com_id,
        com.sha AS com_sha,
        com.message AS com_msg,
        com.author AS com_author,
        com.html_url AS com_url,
        com.timestamp AS com_ts,
        wf.id AS wf_id,
        wf.run_id AS wf_run_id,
        wf.name AS wf_name,
        wf.event AS wf_event,
        wf.status AS wf_status,
        wf.conclusion AS wf_conclusion,
        wf.html_url AS wf_url,
        wf.head_sha AS wf_head_sha,
        wf.created_at AS wf_ts,
        wf_trig.id AS wft_id,
        wf_trig.run_id AS wft_run_id,
        wf_trig.name AS wft_name,
        wf_trig.event AS wft_event,
        wf_trig.status AS wft_status,
        wf_trig.conclusion AS wft_conclusion,
        wf_trig.html_url AS wft_url,
        wf_trig.head_sha AS wft_head_sha,
        wf_trig.created_at AS wft_ts
    LIMIT 30
    """
    res_issue = graph.ro_query(
        issue_cypher,
        params={
            "user_id": user_id,
            "candidate_repos": candidate_list,
            "is_ci": is_ci_failure_query,
            "has_nums": bool(issue_nums),
            "issue_nums": issue_nums or [0],
            "comp": comp_clean,
            "has_kws": bool(match_kws),
            "keywords": match_kws or [""],
            "match_all": bool(effective_target_repo and not match_kws and not comp_clean),
        },
    )

    for row in res_issue.result_set:
        (
            repo_name, iss_id, iss_num, iss_title, iss_state, iss_url, iss_ts,
            pr_id, pr_num, pr_title, pr_state, pr_merged, pr_url, pr_ts,
            com_id, com_sha, com_msg, com_author, com_url, com_ts,
            wf_id, wf_run_id, wf_name, wf_event, wf_status, wf_conclusion, wf_url, wf_head_sha, wf_ts,
            wft_id, wft_run_id, wft_name, wft_event, wft_status, wft_conclusion, wft_url, wft_head_sha, wft_ts,
        ) = row

        iss_path = f"Repository('{repo_name}') -[CONTAINS_ISSUE]-> GitHubIssue(#{iss_num} '{iss_title}' [{iss_state}])"
        add_issue_record(iss_id, iss_num, iss_title, iss_state, iss_url, iss_ts, repo_name, category="direct", path=iss_path)
        add_path(iss_path)

        if pr_id:
            status_desc = "merged" if pr_merged else str(pr_state)
            ref_path = f"Repository('{repo_name}') -[HAS_PULL_REQUEST]-> PullRequest(#{pr_num} '{pr_title}' [{status_desc}]) -[REFERENCES_ISSUE]-> GitHubIssue(#{iss_num} '{iss_title}')"
            add_pr_record(pr_id, pr_num, pr_title, pr_state, pr_merged, pr_url, pr_ts, repo_name, category="related", path=ref_path)
            link_edge(pr_id, iss_id, "REFERENCES_ISSUE")
            add_path(ref_path)

            if com_id and com_sha:
                com_path = f"PullRequest(#{pr_num}) -[CONTAINS_COMMIT]-> Commit('{com_sha[:8]}')"
                add_commit_record(com_id, com_sha, com_msg, com_author, com_url, com_ts, repo_name, p_num=pr_num, category="related", path=com_path)
                link_edge(pr_id, com_id, "CONTAINS_COMMIT")
                add_path(com_path)

            if wf_id:
                wf_path = f"WorkflowRun('{wf_name}' [{wf_conclusion}]) -[TESTS_PULL_REQUEST]-> PullRequest(#{pr_num})"
                add_wf_record(wf_id, wf_run_id, wf_name, wf_event, wf_status, wf_conclusion, wf_url, wf_head_sha, wf_ts, repo_name, p_num=pr_num, c_sha=com_sha, c_msg=com_msg, category="related", path=wf_path)
                link_edge(wf_id, pr_id, "TESTS_PULL_REQUEST")
                add_path(wf_path)

            if wft_id and wft_id != wf_id:
                wft_path = f"WorkflowRun('{wft_name}' [{wft_conclusion}]) -[TRIGGERED_BY]-> Commit('{com_sha[:8]}')"
                add_wf_record(wft_id, wft_run_id, wft_name, wft_event, wft_status, wft_conclusion, wft_url, wft_head_sha, wft_ts, repo_name, p_num=pr_num, c_sha=com_sha, c_msg=com_msg, category="related", path=wft_path)
                if com_id:
                    link_edge(wft_id, com_id, "TRIGGERED_BY")
                add_path(wft_path)

            if com_sha and wft_id:
                add_path(f"GitHubIssue(#{iss_num}) <-[REFERENCES_ISSUE]- PullRequest(#{pr_num}) -[CONTAINS_COMMIT]-> Commit('{com_sha[:8]}') <-[TRIGGERED_BY]- WorkflowRun('{wft_name}' [{wft_conclusion}])")
            elif wf_id:
                add_path(f"WorkflowRun('{wf_name}' [{wf_conclusion}]) -[TESTS_PULL_REQUEST]-> PullRequest(#{pr_num}) -[REFERENCES_ISSUE]-> GitHubIssue(#{iss_num})")

    # 5. Query standalone Workflow Runs (failures matching candidate repos or query identifiers)
    wf_cypher = """
    MATCH (u:User {id: $user_id})-[:HAS_REPOSITORY]->(repo:Repository)-[:HAS_WORKFLOW_RUN]->(run:WorkflowRun)
    WHERE repo.full_name IN $candidate_repos
      AND run.conclusion IN ['failure', 'timed_out', 'cancelled']
      AND (
          $is_ci
          OR ($has_wf_ids AND (run.run_id IN $wf_ids OR run.id IN $wf_ids))
          OR ($has_kws AND any(kw IN $keywords WHERE toLower(run.name) CONTAINS kw))
          OR ($comp <> "" AND toLower(run.name) CONTAINS $comp)
      )
    OPTIONAL MATCH (run)-[:TRIGGERED_BY]->(com:Commit)
    OPTIONAL MATCH (run)-[:TESTS_PULL_REQUEST]->(pr:PullRequest)
    RETURN
        repo.full_name AS repo_name,
        run.id AS run_id,
        run.run_id AS raw_run_id,
        run.name AS run_name,
        run.event AS run_event,
        run.status AS run_status,
        run.conclusion AS run_conclusion,
        run.html_url AS run_url,
        run.created_at AS run_ts,
        run.head_sha AS run_head_sha,
        com.id AS com_id,
        com.sha AS commit_sha,
        com.message AS commit_msg,
        com.author AS com_author,
        com.html_url AS com_url,
        com.timestamp AS com_ts,
        pr.id AS pr_id,
        pr.number AS pr_num,
        pr.title AS pr_title,
        pr.html_url AS pr_url
    LIMIT 10
    """
    res_wf = graph.ro_query(
        wf_cypher,
        params={
            "user_id": user_id,
            "candidate_repos": candidate_list,
            "is_ci": is_ci_failure_query,
            "has_wf_ids": bool(workflow_ids),
            "wf_ids": workflow_ids or ["0"],
            "has_kws": bool(match_kws),
            "keywords": match_kws or [""],
            "comp": comp_clean,
        },
    )

    for row in res_wf.result_set:
        (
            repo_name, run_id, raw_run_id, run_name, run_event, run_status, run_conc, run_url, run_ts, run_head_sha,
            com_id, com_sha, com_msg, com_author, com_url, com_ts,
            pr_id, pr_num, pr_title, pr_url,
        ) = row

        wf_path = f"Repository('{repo_name}') -[HAS_WORKFLOW_RUN]-> WorkflowRun('{run_name}' [{run_conc}])"
        add_wf_record(run_id, raw_run_id, run_name, run_event, run_status, run_conc, run_url, run_head_sha, run_ts, repo_name, p_num=pr_num, c_sha=com_sha, c_msg=com_msg, category="direct", path=wf_path)
        if pr_num and com_sha:
            full_wfp = f"Repository('{repo_name}') -[HAS_WORKFLOW_RUN]-> WorkflowRun('{run_name}' [{run_conc}]) -[TESTS_PULL_REQUEST]-> PullRequest(#{pr_num}) -[CONTAINS_COMMIT]-> Commit('{com_sha[:8]}')"
            add_path(full_wfp)
            if pr_id:
                add_pr_record(pr_id, pr_num, pr_title, "open", False, pr_url, None, repo_name, category="related", path=full_wfp)
                link_edge(run_id, pr_id, "TESTS_PULL_REQUEST")
            if com_id:
                add_commit_record(com_id, com_sha, com_msg, com_author, com_url, com_ts, repo_name, p_num=pr_num, category="related", path=full_wfp)
                if pr_id:
                    link_edge(pr_id, com_id, "CONTAINS_COMMIT")
                link_edge(run_id, com_id, "TRIGGERED_BY")
        elif pr_num:
            add_path(f"Repository('{repo_name}') -[HAS_WORKFLOW_RUN]-> WorkflowRun('{run_name}' [{run_conc}]) -[TESTS_PULL_REQUEST]-> PullRequest(#{pr_num})")
            if pr_id:
                add_pr_record(pr_id, pr_num, pr_title, "open", False, pr_url, None, repo_name, category="related", path=wf_path)
                link_edge(run_id, pr_id, "TESTS_PULL_REQUEST")
        elif com_sha:
            add_path(f"Repository('{repo_name}') -[HAS_WORKFLOW_RUN]-> WorkflowRun('{run_name}' [{run_conc}]) -[TRIGGERED_BY]-> Commit('{com_sha[:8]}')")
            if com_id:
                add_commit_record(com_id, com_sha, com_msg, com_author, com_url, com_ts, repo_name, p_num=None, category="related", path=wf_path)
                link_edge(run_id, com_id, "TRIGGERED_BY")
        else:
            add_path(wf_path)

    # 6. Check for Legitimate Shared Dependencies / Cross-Repository Relationships (Test G)
    if not is_ci_failure_query:
        cross_repo_cypher = """
        MATCH (u:User {id: $user_id})-[:HAS_REPOSITORY]->(r1:Repository)
        WHERE r1.full_name IN $candidate_repos
        MATCH (r1)-[rel:DEPENDS_ON|CROSS_REPO_REF]->(r2:Repository)
        OPTIONAL MATCH (r2)-[:CONTAINS_ISSUE]->(ext_iss:GitHubIssue)
        OPTIONAL MATCH (r2)-[:HAS_PULL_REQUEST]->(ext_pr:PullRequest)
        RETURN
            r1.full_name AS from_repo,
            type(rel) AS rel_type,
            r2.full_name AS to_repo,
            ext_iss.id AS iss_id,
            ext_iss.number AS iss_num,
            ext_iss.title AS iss_title,
            ext_iss.state AS iss_state,
            ext_iss.html_url AS iss_url,
            ext_iss.created_at AS iss_ts,
            ext_pr.id AS pr_id,
            ext_pr.number AS pr_num,
            ext_pr.title AS pr_title,
            ext_pr.state AS pr_state,
            ext_pr.merged AS pr_merged,
            ext_pr.html_url AS pr_url,
            ext_pr.created_at AS pr_ts
        LIMIT 10
        """
        cross_res = graph.ro_query(cross_repo_cypher, params={"user_id": user_id, "candidate_repos": candidate_list})
        for row in cross_res.result_set:
            (
                from_repo, rel_type, to_repo,
                x_iss_id, x_iss_num, x_iss_title, x_iss_state, x_iss_url, x_iss_ts,
                x_pr_id, x_pr_num, x_pr_title, x_pr_state, x_pr_merged, x_pr_url, x_pr_ts,
            ) = row

            cross_path = f"Repository('{from_repo}') -[{rel_type}]-> Repository('{to_repo}')"
            add_path(cross_path)

            if x_iss_id:
                iss_reason = f"Cross-repository dependency: {from_repo} has verified '{rel_type}' link to {to_repo}."
                add_issue_record(x_iss_id, x_iss_num, x_iss_title, x_iss_state, x_iss_url, x_iss_ts, to_repo, category="related", path=cross_path)
                # Override reason for clear explanation
                for rec in records:
                    if rec.id == str(x_iss_id):
                        rec.relevance_reason = iss_reason

            if x_pr_id:
                pr_reason = f"Cross-repository dependency: {from_repo} has verified '{rel_type}' link to {to_repo}."
                add_pr_record(x_pr_id, x_pr_num, x_pr_title, x_pr_state, x_pr_merged, x_pr_url, x_pr_ts, to_repo, category="related", path=cross_path)
                for rec in records:
                    if rec.id == str(x_pr_id):
                        rec.relevance_reason = pr_reason

    return records, evidence_paths, provenance_traces


def get_user_repositories_summary(
    user_id: str,
    graph_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Lists repositories and counts of imported records for the given user."""
    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError):
        return []

    cypher = """
    MATCH (u:User {id: $user_id})-[:HAS_REPOSITORY]->(repo:Repository)
    OPTIONAL MATCH (repo)-[:CONTAINS_ISSUE]->(iss:GitHubIssue)
    OPTIONAL MATCH (repo)-[:HAS_PULL_REQUEST]->(pr:PullRequest)
    OPTIONAL MATCH (repo)-[:CONTAINS_COMMIT]->(com:Commit)
    OPTIONAL MATCH (repo)-[:HAS_WORKFLOW_RUN]->(wf:WorkflowRun)
    RETURN
        repo.id AS id,
        repo.full_name AS full_name,
        repo.description AS description,
        repo.html_url AS html_url,
        repo.default_branch AS default_branch,
        repo.imported_at AS imported_at,
        count(DISTINCT iss) AS issue_count,
        count(DISTINCT pr) AS pr_count,
        count(DISTINCT com) AS commit_count,
        count(DISTINCT wf) AS wf_count
    """
    res = graph.ro_query(cypher, params={"user_id": user_id})
    repos = []
    for row in res.result_set:
        rid, full_name, desc, url, branch, imp_at, iss_c, pr_c, com_c, wf_c = row
        repos.append({
            "repo_id": rid,
            "full_name": full_name,
            "description": desc,
            "html_url": url,
            "default_branch": branch,
            "imported_at": imp_at,
            "issue_count": iss_c,
            "pr_count": pr_c,
            "commit_count": com_c,
            "workflow_run_count": wf_c,
        })
    return repos


def get_repository_details(
    user_id: str,
    repo_identifier: str,
    graph_name: Optional[str] = None,
) -> Optional[GitHubRepositoryDetails]:
    """
    Retrieves full in-app repository details, imported records, and verified/missing
    relationship paths for a specific tenant-owned repository.
    Strictly isolated to user_id. Returns None if repository not found or not owned.
    """
    try:
        graph = get_graph_client(graph_name=graph_name)
    except (RedisConnectionError, RedisTimeoutError, RedisError, FalkorDBConnectionError) as exc:
        raise FalkorDBConnectionError(f"Database connection error retrieving repository details: {exc}") from exc

    # 1. Fetch Repository Metadata ensuring user tenant isolation
    cypher_repo = """
    MATCH (u:User)-[:HAS_REPOSITORY]->(repo:Repository)
    WHERE u.id = $user_id AND (repo.full_name = $repo_name OR repo.id = $repo_name OR toLower(repo.full_name) = toLower($repo_name))
    RETURN
        repo.id AS repo_id,
        repo.owner AS owner,
        repo.name AS name,
        repo.full_name AS full_name,
        repo.description AS description,
        repo.html_url AS html_url,
        repo.default_branch AS default_branch,
        repo.open_issues_count AS open_issues_count,
        repo.imported_at AS imported_at
    LIMIT 1
    """
    res_repo = graph.ro_query(cypher_repo, params={"user_id": user_id, "repo_name": repo_identifier})
    if not res_repo.result_set:
        return None

    row = res_repo.result_set[0]
    repo_id, owner, name, full_name, description, html_url, default_branch, open_issues_count, imported_at = row

    # 2. Fetch Issues
    cypher_issues = """
    MATCH (repo:Repository)-[:CONTAINS_ISSUE]->(iss:GitHubIssue)
    WHERE repo.id = $repo_id
    RETURN
        iss.id AS id,
        iss.number AS number,
        iss.title AS title,
        iss.body AS body,
        iss.state AS state,
        iss.html_url AS html_url,
        iss.author AS author,
        iss.created_at AS created_at,
        iss.updated_at AS updated_at,
        iss.closed_at AS closed_at
    ORDER BY iss.number ASC
    """
    res_issues = graph.ro_query(cypher_issues, params={"repo_id": repo_id})

    # PR -> Issue references
    cypher_pr_issues = """
    MATCH (pr:PullRequest)-[:REFERENCES_ISSUE]->(iss:GitHubIssue)
    WHERE pr.repo_id = $repo_id
    RETURN pr.number AS pr_num, pr.title AS pr_title, iss.number AS iss_num
    """
    res_pr_issues = graph.ro_query(cypher_pr_issues, params={"repo_id": repo_id})
    issue_to_prs: Dict[int, List[int]] = {}
    pr_to_issues: Dict[int, List[int]] = {}
    for r in res_pr_issues.result_set:
        p_num, p_title, i_num = r
        issue_to_prs.setdefault(i_num, []).append(p_num)
        pr_to_issues.setdefault(p_num, []).append(i_num)

    # 3. Fetch Pull Requests
    cypher_prs = """
    MATCH (repo:Repository)-[:HAS_PULL_REQUEST]->(pr:PullRequest)
    WHERE repo.id = $repo_id
    RETURN
        pr.id AS id,
        pr.number AS number,
        pr.title AS title,
        pr.body AS body,
        pr.state AS state,
        pr.merged AS merged,
        pr.html_url AS html_url,
        pr.head_sha AS head_sha,
        pr.base_ref AS base_ref,
        pr.author AS author,
        pr.created_at AS created_at,
        pr.updated_at AS updated_at,
        pr.closed_at AS closed_at,
        pr.merged_at AS merged_at
    ORDER BY pr.number ASC
    """
    res_prs = graph.ro_query(cypher_prs, params={"repo_id": repo_id})

    # PR -> Commit relationships
    cypher_pr_commits = """
    MATCH (pr:PullRequest)-[:CONTAINS_COMMIT]->(com:Commit)
    WHERE pr.repo_id = $repo_id
    RETURN pr.number AS pr_num, com.sha AS com_sha
    """
    res_pr_commits = graph.ro_query(cypher_pr_commits, params={"repo_id": repo_id})
    pr_to_commits: Dict[int, List[str]] = {}
    commit_to_prs: Dict[str, List[int]] = {}
    for r in res_pr_commits.result_set:
        p_num, c_sha = r
        pr_to_commits.setdefault(p_num, []).append(c_sha)
        commit_to_prs.setdefault(c_sha, []).append(p_num)

    # Workflow -> PR relationships
    cypher_wf_prs = """
    MATCH (wf:WorkflowRun)-[:TESTS_PULL_REQUEST]->(pr:PullRequest)
    WHERE wf.repo_id = $repo_id
    RETURN wf.run_id AS wf_run_id, wf.name AS wf_name, wf.conclusion AS wf_conclusion, pr.number AS pr_num
    """
    res_wf_prs = graph.ro_query(cypher_wf_prs, params={"repo_id": repo_id})
    pr_to_wfs: Dict[int, List[int]] = {}
    wf_to_prs: Dict[int, List[int]] = {}
    wf_pr_links: List[Tuple[int, str, str, int]] = []
    for r in res_wf_prs.result_set:
        w_id, w_name, w_conc, p_num = r
        pr_to_wfs.setdefault(p_num, []).append(w_id)
        wf_to_prs.setdefault(w_id, []).append(p_num)
        wf_pr_links.append((w_id, str(w_name), str(w_conc), p_num))

    # 4. Fetch Commits
    cypher_commits = """
    MATCH (repo:Repository)-[:CONTAINS_COMMIT]->(com:Commit)
    WHERE repo.id = $repo_id
    RETURN
        com.id AS id,
        com.sha AS sha,
        com.message AS message,
        com.author AS author,
        com.html_url AS html_url,
        com.timestamp AS timestamp
    ORDER BY com.timestamp DESC
    """
    res_commits = graph.ro_query(cypher_commits, params={"repo_id": repo_id})

    # Workflow -> Commit triggers
    cypher_wf_commits = """
    MATCH (wf:WorkflowRun)-[:TRIGGERED_BY]->(com:Commit)
    WHERE wf.repo_id = $repo_id
    RETURN wf.run_id AS wf_run_id, wf.name AS wf_name, wf.conclusion AS wf_conclusion, com.sha AS com_sha
    """
    res_wf_commits = graph.ro_query(cypher_wf_commits, params={"repo_id": repo_id})
    commit_to_wfs: Dict[str, List[int]] = {}
    wf_to_commits: Dict[int, List[str]] = {}
    wf_com_links: List[Tuple[int, str, str, str]] = []
    for r in res_wf_commits.result_set:
        w_id, w_name, w_conc, c_sha = r
        commit_to_wfs.setdefault(c_sha, []).append(w_id)
        wf_to_commits.setdefault(w_id, []).append(c_sha)
        wf_com_links.append((w_id, str(w_name), str(w_conc), c_sha))

    # 5. Fetch Workflow Runs
    cypher_wfs = """
    MATCH (repo:Repository)-[:HAS_WORKFLOW_RUN]->(wf:WorkflowRun)
    WHERE repo.id = $repo_id
    RETURN
        wf.id AS id,
        wf.run_id AS run_id,
        wf.name AS name,
        wf.event AS event,
        wf.status AS status,
        wf.conclusion AS conclusion,
        wf.html_url AS html_url,
        wf.head_sha AS head_sha,
        wf.created_at AS created_at,
        wf.updated_at AS updated_at
    ORDER BY wf.created_at DESC
    """
    res_wfs = graph.ro_query(cypher_wfs, params={"repo_id": repo_id})

    # Build issues list
    issues_list = []
    for row_iss in res_issues.result_set:
        i_id, i_num, i_title, i_body, i_state, i_url, i_author, i_created, i_updated, i_closed = row_iss
        issues_list.append({
            "id": i_id,
            "number": i_num,
            "title": i_title,
            "body": i_body,
            "state": i_state,
            "html_url": i_url,
            "author": i_author,
            "created_at": i_created,
            "updated_at": i_updated,
            "closed_at": i_closed,
            "referencing_pr_numbers": issue_to_prs.get(i_num, []),
        })

    # Build PRs list
    prs_list = []
    for row_pr in res_prs.result_set:
        p_id, p_num, p_title, p_body, p_state, p_merged, p_url, p_head_sha, p_base_ref, p_author, p_created, p_updated, p_closed, p_merged_at = row_pr
        prs_list.append({
            "id": p_id,
            "number": p_num,
            "title": p_title,
            "body": p_body,
            "state": "merged" if p_merged else p_state,
            "merged": bool(p_merged),
            "html_url": p_url,
            "head_sha": p_head_sha,
            "base_ref": p_base_ref,
            "author": p_author,
            "created_at": p_created,
            "updated_at": p_updated,
            "closed_at": p_closed,
            "merged_at": p_merged_at,
            "referenced_issue_numbers": pr_to_issues.get(p_num, []),
            "commit_shas": pr_to_commits.get(p_num, []),
            "testing_workflow_run_ids": pr_to_wfs.get(p_num, []),
        })

    # Build Commits list
    commits_list = []
    for row_com in res_commits.result_set:
        c_id, c_sha, c_msg, c_author, c_url, c_ts = row_com
        commits_list.append({
            "id": c_id,
            "sha": c_sha,
            "message": c_msg,
            "author": c_author,
            "html_url": c_url,
            "timestamp": c_ts,
            "containing_pr_numbers": commit_to_prs.get(c_sha, []),
            "triggering_workflow_run_ids": commit_to_wfs.get(c_sha, []),
        })

    # Build Workflow Runs list
    wfs_list = []
    for row_wf in res_wfs.result_set:
        w_id, w_run_id, w_name, w_event, w_status, w_conc, w_url, w_head_sha, w_created, w_updated = row_wf
        wfs_list.append({
            "id": w_id,
            "run_id": w_run_id,
            "name": w_name,
            "event": w_event,
            "status": w_status,
            "conclusion": w_conc,
            "html_url": w_url,
            "head_sha": w_head_sha,
            "created_at": w_created,
            "updated_at": w_updated,
            "tested_pr_numbers": wf_to_prs.get(w_run_id, []),
            "triggered_commit_shas": wf_to_commits.get(w_run_id, []),
        })

    # 6. Verified Relationships
    verified_relationships: List[GitHubRelationshipTrace] = []
    seen_traces: Set[str] = set()

    for r in res_pr_issues.result_set:
        p_num, p_title, i_num = r
        key = f"pr_{p_num}->REFERENCES_ISSUE->iss_{i_num}"
        if key not in seen_traces:
            seen_traces.add(key)
            verified_relationships.append(
                GitHubRelationshipTrace(
                    source_type="pull_request",
                    source_id=f"pr_{p_num}",
                    source_identifier=f"PR #{p_num}",
                    relationship="REFERENCES_ISSUE",
                    target_type="issue",
                    target_id=f"issue_{i_num}",
                    target_identifier=f"Issue #{i_num}",
                    path_string=f"PullRequest(#{p_num}) -[REFERENCES_ISSUE]-> GitHubIssue(#{i_num})",
                )
            )

    for r in res_pr_commits.result_set:
        p_num, c_sha = r
        key = f"pr_{p_num}->CONTAINS_COMMIT->{c_sha}"
        if key not in seen_traces:
            seen_traces.add(key)
            verified_relationships.append(
                GitHubRelationshipTrace(
                    source_type="pull_request",
                    source_id=f"pr_{p_num}",
                    source_identifier=f"PR #{p_num}",
                    relationship="CONTAINS_COMMIT",
                    target_type="commit",
                    target_id=c_sha,
                    target_identifier=f"Commit {c_sha[:8]}",
                    path_string=f"PullRequest(#{p_num}) -[CONTAINS_COMMIT]-> Commit('{c_sha[:8]}')",
                )
            )

    for w_id, w_name, w_conc, p_num in wf_pr_links:
        key = f"wf_{w_id}->TESTS_PULL_REQUEST->pr_{p_num}"
        if key not in seen_traces:
            seen_traces.add(key)
            verified_relationships.append(
                GitHubRelationshipTrace(
                    source_type="workflow_run",
                    source_id=str(w_id),
                    source_identifier=f"WorkflowRun #{w_id}",
                    relationship="TESTS_PULL_REQUEST",
                    target_type="pull_request",
                    target_id=f"pr_{p_num}",
                    target_identifier=f"PR #{p_num}",
                    path_string=f"WorkflowRun('{w_name}' [{w_conc}]) -[TESTS_PULL_REQUEST]-> PullRequest(#{p_num})",
                )
            )

    for w_id, w_name, w_conc, c_sha in wf_com_links:
        key = f"wf_{w_id}->TRIGGERED_BY->{c_sha}"
        if key not in seen_traces:
            seen_traces.add(key)
            verified_relationships.append(
                GitHubRelationshipTrace(
                    source_type="workflow_run",
                    source_id=str(w_id),
                    source_identifier=f"WorkflowRun #{w_id}",
                    relationship="TRIGGERED_BY",
                    target_type="commit",
                    target_id=c_sha,
                    target_identifier=f"Commit {c_sha[:8]}",
                    path_string=f"WorkflowRun('{w_name}' [{w_conc}]) -[TRIGGERED_BY]-> Commit('{c_sha[:8]}')",
                )
            )

    # 7. Multi-hop causal paths
    multi_hop_paths: List[str] = []
    seen_multihop: Set[str] = set()

    for w_id, w_name, w_conc, p_num in wf_pr_links:
        for i_num in pr_to_issues.get(p_num, []):
            path = f"WorkflowRun('{w_name}' [{w_conc}]) -[TESTS_PULL_REQUEST]-> PullRequest(#{p_num}) -[REFERENCES_ISSUE]-> GitHubIssue(#{i_num})"
            if path not in seen_multihop:
                seen_multihop.add(path)
                multi_hop_paths.append(path)

    for w_id, w_name, w_conc, c_sha in wf_com_links:
        for p_num in commit_to_prs.get(c_sha, []):
            for i_num in pr_to_issues.get(p_num, []):
                path = f"GitHubIssue(#{i_num}) <-[REFERENCES_ISSUE]- PullRequest(#{p_num}) -[CONTAINS_COMMIT]-> Commit('{c_sha[:8]}') <-[TRIGGERED_BY]- WorkflowRun('{w_name}' [{w_conc}])"
                if path not in seen_multihop:
                    seen_multihop.add(path)
                    multi_hop_paths.append(path)

    # 8. Missing / Unlinked Relationships
    missing_relationships: List[GitHubMissingRelationship] = []

    for iss in issues_list:
        if not iss["referencing_pr_numbers"]:
            missing_relationships.append(
                GitHubMissingRelationship(
                    record_type="issue",
                    identifier=f"Issue #{iss['number']}",
                    missing_relationship="REFERENCES_ISSUE",
                    description=f"Issue #{iss['number']} ('{iss['title']}') has no referencing pull requests recorded in the graph.",
                )
            )

    for pr in prs_list:
        if not pr["referenced_issue_numbers"]:
            missing_relationships.append(
                GitHubMissingRelationship(
                    record_type="pull_request",
                    identifier=f"PR #{pr['number']}",
                    missing_relationship="REFERENCES_ISSUE",
                    description=f"Pull Request #{pr['number']} ('{pr['title']}') does not reference any known issue.",
                )
            )
        if not pr["commit_shas"]:
            missing_relationships.append(
                GitHubMissingRelationship(
                    record_type="pull_request",
                    identifier=f"PR #{pr['number']}",
                    missing_relationship="CONTAINS_COMMIT",
                    description=f"Pull Request #{pr['number']} has no branch commits linked in the graph.",
                )
            )
        if not pr["testing_workflow_run_ids"]:
            missing_relationships.append(
                GitHubMissingRelationship(
                    record_type="pull_request",
                    identifier=f"PR #{pr['number']}",
                    missing_relationship="TESTS_PULL_REQUEST",
                    description=f"Pull Request #{pr['number']} has no associated CI test workflow runs recorded.",
                )
            )

    for com in commits_list:
        if not com["containing_pr_numbers"]:
            msg_snippet = com["message"].splitlines()[0][:45] if com["message"] else "no message"
            missing_relationships.append(
                GitHubMissingRelationship(
                    record_type="commit",
                    identifier=f"Commit {com['sha'][:8]}",
                    missing_relationship="CONTAINS_COMMIT",
                    description=f"Commit {com['sha'][:8]} ('{msg_snippet}') is on the default branch and not part of an imported PR.",
                )
            )
        if not com["triggering_workflow_run_ids"]:
            missing_relationships.append(
                GitHubMissingRelationship(
                    record_type="commit",
                    identifier=f"Commit {com['sha'][:8]}",
                    missing_relationship="TRIGGERED_BY",
                    description=f"Commit {com['sha'][:8]} was not the trigger head for any imported workflow run.",
                )
            )

    for wf in wfs_list:
        if not wf["tested_pr_numbers"]:
            missing_relationships.append(
                GitHubMissingRelationship(
                    record_type="workflow_run",
                    identifier=f"Workflow Run #{wf['run_id']}",
                    missing_relationship="TESTS_PULL_REQUEST",
                    description=f"Workflow Run #{wf['run_id']} ('{wf['name']}') was triggered by a '{wf['event']}' event and is not linked to a pull request.",
                )
            )

    return GitHubRepositoryDetails(
        repo_id=str(repo_id),
        owner=str(owner),
        name=str(name),
        full_name=str(full_name),
        description=str(description) if description else None,
        html_url=str(html_url),
        default_branch=str(default_branch or "main"),
        open_issues_count=int(open_issues_count or 0),
        imported_at=str(imported_at) if imported_at else None,
        counts={
            "issues": len(issues_list),
            "pull_requests": len(prs_list),
            "commits": len(commits_list),
            "workflow_runs": len(wfs_list),
        },
        issues=issues_list,
        pull_requests=prs_list,
        commits=commits_list,
        workflow_runs=wfs_list,
        verified_relationships=verified_relationships,
        multi_hop_paths=multi_hop_paths,
        missing_relationships=missing_relationships,
    )

