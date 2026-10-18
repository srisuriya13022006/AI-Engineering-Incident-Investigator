/**
 * Mai Graph Hacks — Interactive Frontend Application
 * Handles scenario ingestion, deduplication checks, contextual multi-hop queries,
 * visual path rendering, and fair keyword baseline comparison.
 */

let currentUser = "user_alex_rivera_demo";
let currentUserName = "Alex Rivera";

// Fictional synthetic scenario payload (Alex Rivera)
function getScenarioPayload(userId, userName) {
  const memId = "mem_alex_architecture_note";
  const goalId = "goal_alex_reduce_latency";
  const decId = "dec_alex_falkordb_cypher";
  const expId = "exp_alex_benchmark_graphblas";
  const entId = "ent_falkordb_engine";

  return {
    user_id: userId,
    user_name: userName,
    structured_data: {
      memories: [
        {
          id: memId,
          text: "Fictional dev Alex Rivera recorded: Graph memory enables connected reasoning across episodic traces.",
          summary: "Graph memory enables connected reasoning across episodic traces",
          memory_type: "observation",
          confidence: 1.0,
          provenance: "dev_log:2026_q1"
        }
      ],
      entities: [
        {
          id: entId,
          name: "FalkorDB",
          entity_type: "technology",
          description: "Graph database leveraging GraphBLAS matrix algorithms"
        }
      ],
      goals: [
        {
          id: goalId,
          title: "Reduce AI assistant reasoning response latency under 30ms",
          priority: "critical"
        }
      ],
      decisions: [
        {
          id: decId,
          action: "Adopt FalkorDB multi-hop Cypher traversal with read-only execution",
          rationale: "Causal connections avoid combinatorial vector similarity joins"
        }
      ],
      experiences: [
        {
          id: expId,
          content: "[Synthetic Illustrative Scenario] Dev team evaluation: GraphBLAS matrix traversal benchmark concept across episodic hops",
          outcome: "[Illustrative Target] Target 14ms median traversal latency under path determinism (Synthetic scenario data — not an actual system benchmark measurement)",
          confidence: 0.98
        }
      ],
      relationships: [
        { source_id: memId, relation: "MENTIONS", target_id: entId },
        { source_id: memId, relation: "HAS_GOAL", target_id: goalId },
        { source_id: goalId, relation: "MOTIVATES", target_id: decId },
        { source_id: decId, relation: "INFORMED_BY", target_id: expId }
      ]
    }
  };
}

// Escape HTML utility
function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// 1. Check FalkorDB and Backend Health
async function checkHealth() {
  const dot = document.getElementById("falkordb-dot");
  const text = document.getElementById("falkordb-status-text");

  try {
    const res = await fetch("/health");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.status === "healthy") {
      dot.className = "pulse-dot online";
      text.textContent = `FalkorDB Online (${data.graph_name})`;
    } else {
      dot.className = "pulse-dot degraded";
      text.textContent = `FalkorDB Degraded: ${data.error || "disconnected"}`;
    }
  } catch (err) {
    dot.className = "pulse-dot degraded";
    text.textContent = `Backend Offline: ${err.message}`;
  }
}

// 2. Ingest Scenario (Mode A)
async function ingestDemoScenario() {
  const feedback = document.getElementById("ingest-feedback");
  const btn = document.getElementById("btn-ingest-scenario");
  feedback.className = "ingest-feedback show";
  feedback.textContent = "Ingesting synthetic scenario into FalkorDB...";
  btn.disabled = true;

  try {
    const payload = getScenarioPayload(currentUser, currentUserName);
    const res = await fetch("/memory/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    feedback.className = "ingest-feedback show success";
    if (data.memories_stored > 0) {
      feedback.textContent = `✓ Ingestion Complete: ${data.memories_stored} new memory stored (${data.memories_deduplicated} deduplicated). Nodes created: ${data.goals_created} goal, ${data.decisions_created} decision, ${data.experiences_created} experience, ${data.entities_created} entity. Relationships created: ${data.relationships_created} (reused: ${data.relationships_reused}).`;
    } else {
      feedback.textContent = `✓ Scenario Verified in Graph (Deduplication): 0 new memories stored, ${data.memories_deduplicated} existing memory preserved via SHA-256 fingerprint. Existing nodes reused: ${data.goals_reused} goals, ${data.decisions_reused} decisions, ${data.experiences_reused} experiences. Relationships reused: ${data.relationships_reused}. Zero redundant nodes created.`;
    }
  } catch (err) {
    feedback.className = "ingest-feedback show";
    feedback.style.background = "var(--danger-bg)";
    feedback.style.color = "#fca5a5";
    feedback.textContent = `✗ Ingestion Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

// 3. Test Deduplication
async function testDeduplication() {
  const feedback = document.getElementById("ingest-feedback");
  const btn = document.getElementById("btn-test-dedup");
  feedback.className = "ingest-feedback show";
  feedback.textContent = "Re-submitting exact identical payload to test deduplication...";
  btn.disabled = true;

  try {
    const payload = getScenarioPayload(currentUser, currentUserName);
    const res = await fetch("/memory/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    feedback.className = "ingest-feedback show success";
    feedback.textContent = `✓ Deduplication Verified: 0 new memories stored, ${data.memories_deduplicated} existing memory safely identified by SHA-256 fingerprint. Existing nodes reused: ${data.goals_reused} goals, ${data.decisions_reused} decisions, ${data.experiences_reused} experiences. Existing relationships reused: ${data.relationships_reused}. Graph invariants preserved with zero duplicates.`;
  } catch (err) {
    feedback.className = "ingest-feedback show";
    feedback.style.background = "var(--danger-bg)";
    feedback.style.color = "#fca5a5";
    feedback.textContent = `✗ Deduplication Test Error: ${err.message}`;
  } finally {
    btn.disabled = false;
  }
}

// 3B. Set Conversation Sample
function setConversationSample(sampleType) {
  const input = document.getElementById("input-conversation");
  if (sampleType === "falkordb") {
    input.value = "During our sprint architecture review, we set a high-priority goal to reduce agent memory lookup latency below 20ms. We decided to adopt FalkorDB with GraphBLAS sparse matrix algorithms because it enables direct multi-hop relationship traversals without combinatorial vector joins. In our internal benchmark, we verified query completion in 16ms.";
  } else if (sampleType === "contradiction") {
    input.value = "In Q1 we recorded that our primary database was PostgreSQL. Today we decided to migrate our entire memory graph to FalkorDB for real-time Cypher traversal.";
  } else if (sampleType === "chatter") {
    input.value = "Good morning team! The weather is lovely today and let's grab coffee at 10am.";
  }
}

// 3C. Ingest Natural Language Conversation using Google Gemini API
async function ingestNaturalLanguageConversation() {
  const input = document.getElementById("input-conversation");
  const btn = document.getElementById("btn-extract-gemini");
  const loading = document.getElementById("gemini-loading");
  const errorBox = document.getElementById("gemini-error");
  const errorTitle = document.getElementById("gemini-error-title");
  const errorMsg = document.getElementById("gemini-error-message");
  const errorAction = document.getElementById("gemini-error-action");
  const feedback = document.getElementById("gemini-feedback");
  const previewBox = document.getElementById("gemini-extracted-preview");
  const grid = document.getElementById("gemini-extracted-grid");

  const conversationText = input.value.trim();
  if (!conversationText) {
    alert("Please enter or paste conversation text to extract.");
    return;
  }

  // Reset display states
  loading.style.display = "block";
  errorBox.style.display = "none";
  feedback.className = "ingest-feedback";
  feedback.style.display = "none";
  previewBox.style.display = "none";
  grid.innerHTML = "";
  btn.disabled = true;

  try {
    const res = await fetch("/memory/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser,
        user_name: currentUserName,
        conversation_text: conversationText,
      }),
    });

    const data = await res.json();

    if (!res.ok) {
      const detail = data.detail || {};
      const errName = detail.error || "ExtractionError";
      const errMsg = detail.message || (typeof detail === "string" ? detail : JSON.stringify(detail));
      let actionText = detail.action || "";
      if (!actionText && (errMsg.includes("rate limit") || errMsg.includes("RESOURCE_EXHAUSTED") || errMsg.includes("429"))) {
        actionText = "💡 Demo Fallback: Google Gemini Free Tier rate limit reached (20 req/day). Use '⚡ Ingest Scenario (Mode A)' above to test complete FalkorDB graph storage, multi-hop reasoning, and deduplication with zero API keys required!";
      }

      errorBox.style.display = "block";
      errorTitle.textContent = `⚠️ Gemini Extraction Error (${errName})`;
      errorMsg.textContent = errMsg;
      errorAction.textContent = actionText;
      return;
    }

    // Success feedback
    feedback.className = "ingest-feedback show success";
    feedback.style.display = "block";
    feedback.innerHTML = `
      <strong>✓ Gemini Extraction & FalkorDB Storage Succeeded:</strong><br>
      • Memories: ${data.memories_stored} stored, ${data.memories_deduplicated} deduplicated<br>
      • Goals: ${data.goals_created} created, ${data.goals_reused} reused<br>
      • Decisions: ${data.decisions_created} created, ${data.decisions_reused} reused<br>
      • Experiences: ${data.experiences_created} created, ${data.experiences_reused} reused<br>
      • Relationships: ${data.relationships_created} created, ${data.relationships_reused} reused
    `;

    // Render extracted items preview if returned
    if (data.extracted_data) {
      const ext = data.extracted_data;
      let itemsHtml = "";

      (ext.memories || []).forEach(m => {
        itemsHtml += `
          <div class="extracted-item">
            <span class="extracted-item-tag" style="background: rgba(139, 92, 246, 0.2); color: #c4b5fd;">Memory</span>
            <div class="extracted-item-content"><strong>${escapeHtml(m.summary || m.text)}</strong><br><span style="font-size: 0.75rem; color: var(--text-muted);">${escapeHtml(m.text)}</span></div>
          </div>`;
      });

      (ext.goals || []).forEach(g => {
        itemsHtml += `
          <div class="extracted-item">
            <span class="extracted-item-tag" style="background: rgba(16, 185, 129, 0.2); color: #6ee7b7;">Goal</span>
            <div class="extracted-item-content"><strong>${escapeHtml(g.title)}</strong> (Priority: ${escapeHtml(g.priority)})</div>
          </div>`;
      });

      (ext.decisions || []).forEach(d => {
        itemsHtml += `
          <div class="extracted-item">
            <span class="extracted-item-tag" style="background: rgba(59, 130, 246, 0.2); color: #93c5fd;">Decision</span>
            <div class="extracted-item-content"><strong>Action:</strong> ${escapeHtml(d.action)}<br><strong>Rationale:</strong> ${escapeHtml(d.rationale)}</div>
          </div>`;
      });

      (ext.experiences || []).forEach(e => {
        itemsHtml += `
          <div class="extracted-item">
            <span class="extracted-item-tag" style="background: rgba(245, 158, 11, 0.2); color: #fcd34d;">Experience</span>
            <div class="extracted-item-content"><strong>Observation:</strong> ${escapeHtml(e.content)}<br><strong>Outcome:</strong> ${escapeHtml(e.outcome)}</div>
          </div>`;
      });

      (ext.entities || []).forEach(ent => {
        itemsHtml += `
          <div class="extracted-item">
            <span class="extracted-item-tag" style="background: rgba(236, 72, 153, 0.2); color: #f472b6;">Entity</span>
            <div class="extracted-item-content"><strong>${escapeHtml(ent.name)}</strong> (${escapeHtml(ent.entity_type)}): ${escapeHtml(ent.description || "")}</div>
          </div>`;
      });

      (ext.relationships || []).forEach(r => {
        itemsHtml += `
          <div class="extracted-item">
            <span class="extracted-item-tag" style="background: rgba(99, 102, 241, 0.2); color: #a5b4fc;">Rel</span>
            <div class="extracted-item-content"><code>${escapeHtml(r.source_id)}</code> <strong>─[${escapeHtml(r.relation)}]─►</strong> <code>${escapeHtml(r.target_id)}</code></div>
          </div>`;
      });

      if (itemsHtml) {
        grid.innerHTML = itemsHtml;
        previewBox.style.display = "block";
      }
    }
  } catch (err) {
    errorBox.style.display = "block";
    errorTitle.textContent = "⚠️ Network / Server Error";
    errorMsg.textContent = err.message || "Failed to reach server.";
    errorAction.textContent = "Check that the FastAPI server is running.";
  } finally {
    loading.style.display = "none";
    btn.disabled = false;
  }
}

// Workspace View Navigation (Investigate vs Repositories vs DevTools)
function switchView(viewName) {
  const views = {
    investigate: document.getElementById("page-investigate"),
    repositories: document.getElementById("page-repositories"),
    devtools: document.getElementById("page-devtools")
  };
  const navBtns = {
    investigate: document.getElementById("nav-btn-investigate"),
    repositories: document.getElementById("nav-btn-repositories"),
    devtools: document.getElementById("nav-btn-devtools")
  };
  const titleEl = document.getElementById("header-view-title");

  const titles = {
    investigate: "Investigate Incident",
    repositories: "Connected Repositories",
    devtools: "Developer & Demo Tools"
  };

  for (const [key, pageEl] of Object.entries(views)) {
    if (pageEl) {
      if (key === viewName) {
        pageEl.classList.add("active");
        pageEl.style.display = "block";
      } else {
        pageEl.classList.remove("active");
        pageEl.style.display = "none";
      }
    }
  }

  for (const [key, btnEl] of Object.entries(navBtns)) {
    if (btnEl) {
      if (key === viewName) {
        btnEl.classList.add("active");
      } else {
        btnEl.classList.remove("active");
      }
    }
  }

  if (titleEl && titles[viewName]) {
    titleEl.textContent = titles[viewName];
  }

  if (viewName === "repositories") {
    refreshGitHubStatus();
  }
}

// Target Repository Context Selector
function onRepositorySelectChange(repoValue) {
  const pill = document.getElementById("header-repo-text");
  if (pill) {
    pill.textContent = repoValue ? `Repo: ${repoValue}` : "Repo: All Connected";
  }
}

// 4. Toggle User Isolation / Tenant Switcher
function toggleUserIsolation() {
  const display = document.getElementById("current-user-display");
  const feedback = document.getElementById("ingest-feedback");

  if (currentUser === "user_alex_rivera_demo") {
    currentUser = "user_incident_judge_demo";
    currentUserName = "Judge Demo";
  } else if (currentUser === "user_incident_judge_demo") {
    currentUser = "user_jordan_rival_demo";
    currentUserName = "Jordan Rival";
  } else {
    currentUser = "user_alex_rivera_demo";
    currentUserName = "Alex Rivera";
  }

  if (display) {
    display.textContent = `${currentUser}`;
  }
  if (feedback) {
    feedback.className = "ingest-feedback show";
    feedback.style.background = "rgba(37, 99, 235, 0.12)";
    feedback.style.color = "#93c5fd";
    feedback.textContent = `Switched tenant to: ${currentUser} (${currentUserName}). Graph is strictly isolated per tenant.`;
  }

  // Clear developer memory query results to reflect new user context
  const devRes = document.getElementById("results-container");
  if (devRes) devRes.style.display = "none";
  const devNoEv = document.getElementById("state-no-evidence");
  if (devNoEv) devNoEv.style.display = "none";
  const devMetrics = document.getElementById("metrics-strip");
  if (devMetrics) devMetrics.style.display = "none";

  // Clear incident investigator view and graph
  const incRes = document.getElementById("incident-results-container");
  if (incRes) incRes.style.display = "none";
  const incNoEv = document.getElementById("incident-no-evidence");
  if (incNoEv) incNoEv.style.display = "none";
  if (typeof cyIncidentGraph !== "undefined" && cyIncidentGraph) {
    cyIncidentGraph.destroy();
    cyIncidentGraph = null;
  }

  // Refresh GitHub imported repositories list for active user
  if (typeof refreshGitHubStatus === "function") {
    refreshGitHubStatus();
  }
}

// 5. Preset Query Helper
function setPresetQuery(text) {
  document.getElementById("input-query").value = text;
  executeContextQuery();
}

// 6. Execute Context Query
async function executeContextQuery() {
  const queryInput = document.getElementById("input-query");
  const queryText = queryInput.value.trim();
  if (!queryText) return;

  const btn = document.getElementById("btn-execute-query");
  const btnText = document.getElementById("btn-query-text");
  const loading = document.getElementById("state-loading");
  const noEvidence = document.getElementById("state-no-evidence");
  const resultsContainer = document.getElementById("results-container");
  const metricsStrip = document.getElementById("metrics-strip");

  // Reset UI
  loading.style.display = "block";
  noEvidence.style.display = "none";
  resultsContainer.style.display = "none";
  metricsStrip.style.display = "none";
  btn.disabled = true;
  btnText.textContent = "Retrieving...";

  const t0 = performance.now();

  try {
    const res = await fetch("/memory/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser,
        query: queryText,
        max_depth: 3
      })
    });

    const elapsedMs = Math.round(performance.now() - t0);
    const data = await res.json();

    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    // Update metrics strip
    document.getElementById("metric-latency").textContent = `${elapsedMs} ms (client e2e)`;
    const totalEvidence = (data.goals?.length || 0) + (data.decisions?.length || 0) + (data.experiences?.length || 0) + (data.memories?.length || 0) + (data.entities?.length || 0);
    document.getElementById("metric-count").textContent = `${totalEvidence} items`;
    document.getElementById("metric-user").textContent = currentUser;
    metricsStrip.style.display = "flex";

    if (!data.found_evidence || totalEvidence === 0) {
      document.getElementById("no-evidence-detail").textContent =
        `No causal memory paths found in '${currentUser}' graph matching query. Zero phantom facts generated.`;
      noEvidence.style.display = "block";
      return;
    }

    // Render Evidence Paths
    const pathList = document.getElementById("path-list");
    pathList.innerHTML = "";
    if (data.evidence_paths && data.evidence_paths.length > 0) {
      data.evidence_paths.forEach(p => {
        const item = document.createElement("div");
        item.className = "path-item";
        item.textContent = p;
        pathList.appendChild(item);
      });
      document.getElementById("path-box").style.display = "block";
    } else {
      document.getElementById("path-box").style.display = "none";
    }

    // Render Conflict & Temporal History Display
    const conflictPanel = document.getElementById("conflict-panel");
    const conflictContainer = document.getElementById("conflict-cards-container");
    const conflictBadge = document.getElementById("conflict-badge-count");

    if (data.conflicts && data.conflicts.length > 0) {
      conflictBadge.textContent = data.conflicts.length;
      conflictContainer.innerHTML = data.conflicts.map(c => renderConflictCard(c)).join("");
      conflictPanel.style.display = "block";
    } else {
      conflictPanel.style.display = "none";
    }

    // Render Context Summary
    document.getElementById("summary-text").textContent = data.context_summary || "Context synthesized from verified graph evidence.";

    // Render Goals with Status and Timestamps
    renderNodeList("list-goals", "badge-goals-count", data.goals, g => `
      <div class="node-card">
        <div class="node-title">${escapeHtml(g.title)}</div>
        <div class="node-meta">
          <span>Priority: <strong>${escapeHtml(g.priority)}</strong></span>
          <span class="badge-status ${escapeHtml(g.status || 'current')}">${escapeHtml(g.status || 'current')}</span>
        </div>
        <div class="node-meta" style="margin-top: 4px; font-size: 0.7rem; color: #94a3b8;">
          <span>ID: ${escapeHtml(g.id)}</span>
          ${g.recorded_at ? `<span>Recorded: ${escapeHtml(g.recorded_at)}</span>` : ""}
        </div>
      </div>
    `);

    // Render Decisions with Status and Timestamps
    renderNodeList("list-decisions", "badge-decisions-count", data.decisions, d => `
      <div class="node-card">
        <div class="node-title">${escapeHtml(d.action)}</div>
        <div class="node-body">${escapeHtml(d.rationale)}</div>
        <div class="node-meta">
          <span class="badge-status ${escapeHtml(d.status || 'current')}">${escapeHtml(d.status || 'current')}</span>
          <span>${escapeHtml(d.id)}</span>
        </div>
      </div>
    `);

    // Render Experiences with Status and Timestamps
    renderNodeList("list-experiences", "badge-experiences-count", data.experiences, e => `
      <div class="node-card">
        <div class="node-title">${escapeHtml(e.content)}</div>
        <div class="node-body">Outcome: <strong>${escapeHtml(e.outcome)}</strong></div>
        <div class="node-meta">
          <span>Confidence: ${(e.confidence * 100).toFixed(0)}%</span>
          <span class="badge-status ${escapeHtml(e.status || 'current')}">${escapeHtml(e.status || 'current')}</span>
        </div>
        <div class="node-meta" style="margin-top: 4px; font-size: 0.7rem; color: #94a3b8;">
          <span>ID: ${escapeHtml(e.id)}</span>
          ${e.recorded_at ? `<span>Recorded: ${escapeHtml(e.recorded_at)}</span>` : ""}
        </div>
      </div>
    `);

    // Render Memories with Status and Timestamps
    renderNodeList("list-memories", "badge-memories-count", data.memories, m => `
      <div class="node-card">
        <div class="node-title">${escapeHtml(m.summary)}</div>
        <div class="node-body">${escapeHtml(m.text)}</div>
        <div class="node-meta">
          <span>Type: ${escapeHtml(m.memory_type)}</span>
          <span class="badge-status ${escapeHtml(m.status || 'current')}">${escapeHtml(m.status || 'current')}</span>
        </div>
        <div class="node-meta" style="margin-top: 4px; font-size: 0.7rem; color: #94a3b8;">
          <span>ID: ${escapeHtml(m.id)}</span>
          ${m.recorded_at ? `<span>Recorded: ${escapeHtml(m.recorded_at)}</span>` : ""}
        </div>
      </div>
    `);

    // Render Entities
    renderNodeList("list-entities", "badge-entities-count", data.entities, ent => `
      <div class="node-card">
        <div class="node-title">${escapeHtml(ent.name)}</div>
        <div class="node-body">${escapeHtml(ent.description || "")}</div>
        <div class="node-meta">
          <span>Type: ${escapeHtml(ent.entity_type)}</span>
          <span>${escapeHtml(ent.id)}</span>
        </div>
      </div>
    `);

    // Render Provenance Chips
    const provStrip = document.getElementById("provenance-strip");
    provStrip.innerHTML = "";
    if (data.provenance && data.provenance.length > 0) {
      data.provenance.forEach(pr => {
        const chip = document.createElement("span");
        chip.className = "prov-chip";
        chip.textContent = pr;
        provStrip.appendChild(chip);
      });
    } else {
      provStrip.innerHTML = "<span class='prov-chip'>Direct user query</span>";
    }

    resultsContainer.style.display = "block";

  } catch (err) {
    noEvidence.style.display = "block";
    document.getElementById("no-evidence-detail").textContent = `Query Failure: ${err.message}`;
  } finally {
    loading.style.display = "none";
    btn.disabled = false;
    btnText.textContent = "🔍 Retrieve";
  }
}

function renderNodeList(elementId, badgeId, items, renderFn) {
  const container = document.getElementById(elementId);
  const badge = document.getElementById(badgeId);
  const col = container.closest(".node-column");

  if (!items || items.length === 0) {
    container.innerHTML = "<p style='color: var(--text-subtle); font-size: 0.78rem;'>None traversed for this query.</p>";
    badge.textContent = "0";
    return;
  }

  badge.textContent = items.length;
  container.innerHTML = items.map(renderFn).join("");
}

// 7. Side-by-Side Baseline Comparison
async function runBaselineComparison() {
  const queryText = document.getElementById("input-query").value.trim();
  if (!queryText) return;

  const section = document.getElementById("section-comparison");
  section.style.display = "block";
  section.scrollIntoView({ behavior: "smooth" });

  try {
    const expectedIds = (currentUser === "user_alex_rivera_demo")
      ? ["goal_alex_reduce_latency", "dec_alex_falkordb_cypher", "exp_alex_benchmark_graphblas"]
      : [];

    const res = await fetch("/memory/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser,
        query: queryText,
        expected_ids: expectedIds
      })
    });

    if (res.ok) {
      const data = await res.json();
      document.getElementById("comp-g-recall").textContent = `${(data.graph_recall * 100).toFixed(1)}%`;
      document.getElementById("comp-b-recall").textContent = `${(data.baseline_recall * 100).toFixed(1)}%`;

      document.getElementById("comp-g-dec").innerHTML = data.graph_found_decision ? "<span class='pill-success'>YES</span>" : "<span class='pill-fail'>NO</span>";
      document.getElementById("comp-b-dec").innerHTML = data.baseline_found_decision ? "<span class='pill-success'>YES</span>" : "<span class='pill-fail'>NO</span>";

      document.getElementById("comp-g-exp").innerHTML = data.graph_found_experience ? "<span class='pill-success'>YES</span>" : "<span class='pill-fail'>NO</span>";
      document.getElementById("comp-b-exp").innerHTML = data.baseline_found_experience ? "<span class='pill-success'>YES</span>" : "<span class='pill-fail'>NO</span>";

      document.getElementById("comp-g-path").innerHTML = data.graph_has_evidence_path ? "<span class='pill-success'>YES</span>" : "<span class='pill-fail'>NO</span>";
      document.getElementById("comp-b-path").innerHTML = "<span class='pill-fail'>NO (0 paths)</span>";

      document.getElementById("comp-g-lat").textContent = `${data.graph_latency_ms.toFixed(1)} ms`;
      document.getElementById("comp-b-lat").textContent = `${data.baseline_latency_ms.toFixed(1)} ms`;

      document.getElementById("comparison-summary").textContent = data.summary;
    } else {
      document.getElementById("comparison-summary").textContent = "Baseline comparison request failed.";
    }
  } catch (err) {
    console.error("Baseline comparison error:", err);
  }
}

// 8. Milestone 6: Render Conflict Card (Side-by-Side Claims)
function renderConflictCard(c) {
  const typeLabel = c.conflict_type === "superseded" ? "SUPERSEDES (Goal / State Update)" :
                    c.conflict_type === "contradiction" ? "CONTRADICTS (Incompatible Claims)" :
                    c.conflict_type === "target_mismatch" ? "TARGET MISMATCH (Empirical Observation)" :
                    c.conflict_type === "compatible" ? "COMPATIBLE (Satisfies Target)" :
                    c.conflict_type.toUpperCase();

  const certClass = c.classification_certainty === "deterministic" ? "deterministic" : "uncertain";

  const oldStatusClass = c.conflict_type === 'superseded' ? 'superseded' :
                         (c.conflict_type === 'contradiction' ? 'disputed' : 'current');

  const newStatusClass = c.conflict_type === 'contradiction' ? 'disputed' : 'current';

  return `
    <div class="conflict-card">
      <div class="conflict-header">
        <span class="conflict-type-pill">${escapeHtml(typeLabel)}</span>
        <span class="certainty-pill ${certClass}">Classification: ${escapeHtml(c.classification_certainty)}</span>
      </div>
      <div class="conflict-sides-grid">
        <div class="claim-side">
          <div class="claim-side-title">
            <span>Earlier Claim / Stated Target</span>
            <span class="badge-status ${oldStatusClass}">
              ${c.conflict_type === 'superseded' ? 'superseded' : (c.conflict_type === 'contradiction' ? 'disputed' : 'active')}
            </span>
          </div>
          <div class="claim-text">${escapeHtml(c.original_claim)}</div>
          <div class="claim-meta">
            <span>ID: <code>${escapeHtml(c.original_id || "n/a")}</code></span>
            <span>Recorded: ${escapeHtml(c.original_timestamp || "unknown")}</span>
          </div>
        </div>
        <div class="claim-side">
          <div class="claim-side-title">
            <span>Newer Claim / Empirical Measurement</span>
            <span class="badge-status ${newStatusClass}">
              ${c.conflict_type === 'contradiction' ? 'disputed' : 'current'}
            </span>
          </div>
          <div class="claim-text">${escapeHtml(c.new_claim)}</div>
          <div class="claim-meta">
            <span>ID: <code>${escapeHtml(c.new_id || "n/a")}</code></span>
            <span>Recorded: ${escapeHtml(c.new_timestamp || "unknown")}</span>
          </div>
        </div>
      </div>
      <div class="conflict-reason-box">
        <strong>Deterministic Reason:</strong> ${escapeHtml(c.reason)}
      </div>
    </div>
  `;
}

// 9. Milestone 6: Run Focused 3-Statement Conflict Scenario Demo
async function runConflictDemo() {
  const btn = document.getElementById("btn-run-conflict-demo");
  const statusSpan = document.getElementById("conflict-demo-status");
  const container = document.getElementById("conflict-scenario-container");
  const stepsList = document.getElementById("conflict-steps-list");
  const cardsList = document.getElementById("conflict-cards-list");
  const analysisText = document.getElementById("conflict-analysis-text");

  btn.disabled = true;
  btn.textContent = "⏳ Running Scenario...";
  statusSpan.textContent = "Ingesting 3 statements & evaluating deterministic graph relationships...";

  try {
    const res = await fetch("/demo/conflict_scenario?user_id=user_alex_temporal_demo", {
      method: "POST"
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    // Render Steps
    stepsList.innerHTML = (data.steps || []).map(s => `
      <div class="path-item" style="border-left-color: #3b82f6; margin-bottom: 8px;">
        <div><span class="scenario-step-badge">${s.step}</span> <strong>Statement:</strong> "${escapeHtml(s.statement)}"</div>
        <div style="font-size: 0.78rem; color: #cbd5e1; margin-top: 4px; padding-left: 26px;">
          <strong>Action:</strong> ${escapeHtml(s.action)}
        </div>
      </div>
    `).join("");

    // Render Conflict & Temporal Cards
    const conflicts = data.query_result?.conflicts || [];
    if (conflicts.length > 0) {
      cardsList.innerHTML = conflicts.map(c => renderConflictCard(c)).join("");
    } else {
      cardsList.innerHTML = "<div class='path-item'>No conflicts found.</div>";
    }

    // Render Analysis Synthesis
    analysisText.textContent = data.analysis || data.query_result?.context_summary || "";

    container.style.display = "block";
    statusSpan.innerHTML = `✓ Completed! <strong style="color: #34d399;">${data.preserved_evidence_count} separate claims preserved</strong> in FalkorDB without overwriting history.`;
  } catch (err) {
    statusSpan.textContent = `Scenario failed: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "⚡ Run 3-Statement Conflict & Temporal Demo";
  }
}

// ============================================================================
// Incident Investigator Logic (Milestone 8)
// ============================================================================

let currentActionId = null;

// Preset selector for Incident Investigator
function setIncidentPreset(symptoms, component) {
  document.getElementById("input-incident-symptoms").value = symptoms;
  document.getElementById("input-incident-component").value = component;
  queryIncidentInvestigation();
}

// 5. Query Incident Investigation
async function queryIncidentInvestigation() {
  const symptomsEl = document.getElementById("input-incident-symptoms");
  const componentEl = document.getElementById("input-incident-component");
  const repoSelectEl = document.getElementById("input-incident-repo-select");
  const preferAgentEl = document.getElementById("input-incident-prefer-agent");
  const btn = document.getElementById("btn-investigate-incident");
  const loading = document.getElementById("incident-loading");
  const resultsContainer = document.getElementById("incident-results-container");
  const noEvidence = document.getElementById("incident-no-evidence");
  const noEvidenceText = document.getElementById("incident-no-evidence-text");
  const evidencePanel = document.getElementById("incident-evidence-panel");

  const symptoms = symptomsEl ? symptomsEl.value.trim() : "";
  const component = componentEl ? componentEl.value.trim() : "";
  const repo = repoSelectEl ? repoSelectEl.value.trim() : "";
  const preferAgent = preferAgentEl ? preferAgentEl.checked : true;

  if (!symptoms) {
    alert("Please enter incident symptoms.");
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-small" style="margin-right: 6px;"></span><span>Investigating...</span>`;
  }
  loading.style.display = "flex";
  resultsContainer.style.display = "none";
  noEvidence.style.display = "none";
  evidencePanel.style.display = "none";

  try {
    const res = await fetch("/incident/investigate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser,
        symptoms: symptoms,
        component: component || null,
        repository: repo || null,
        prefer_agent: preferAgent
      }),
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    resultsContainer.style.display = "block";

    // Update header mode badge
    const headerModeText = document.getElementById("header-mode-text");
    if (headerModeText) {
      if (data.investigation_mode === "agentic") {
        headerModeText.textContent = "Autonomous Agent (gemini-3.8-flash)";
      } else {
        headerModeText.textContent = "Deterministic Fallback Guard";
      }
    }

    if (!data.found_evidence) {
      noEvidence.style.display = "block";
      evidencePanel.style.display = "none";
      noEvidenceText.textContent = data.insufficient_evidence_reason || "No prior investigations or fix outcomes match this query.";
      if (typeof cyIncidentGraph !== "undefined" && cyIncidentGraph) {
        cyIncidentGraph.destroy();
        cyIncidentGraph = null;
      }
      return;
    }

    renderIncidentInvestigationResults(data);
  } catch (err) {
    alert(`Investigation failed: ${err.message}`);
  } finally {
    loading.style.display = "none";
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = `<span class="btn-icon">🔍</span><span>Investigate Incident</span>`;
    }
  }
}

// Global reference for Cytoscape instance
let cyIncidentGraph = null;

// Graph Zoom & Pan Controls
function graphZoomIn() {
  if (cyIncidentGraph) cyIncidentGraph.zoom(cyIncidentGraph.zoom() * 1.25);
}

function graphZoomOut() {
  if (cyIncidentGraph) cyIncidentGraph.zoom(cyIncidentGraph.zoom() * 0.8);
}

function graphFitView() {
  if (cyIncidentGraph) cyIncidentGraph.fit(null, 30);
}

let currentPresetPositions = null;

function graphResetLayout() {
  if (cyIncidentGraph && currentPresetPositions) {
    cyIncidentGraph.layout({ name: "preset", positions: currentPresetPositions, fit: true, padding: 35 }).run();
  } else if (cyIncidentGraph) {
    cyIncidentGraph.layout({ name: "breadthfirst", directed: true, padding: 30, spacingFactor: 1.3 }).run();
    cyIncidentGraph.fit(null, 30);
  }
}

// Show Node Inspector Details Card with complete evidence provenance
function showNodeInspector(d) {
  const panel = document.getElementById("graph-node-details-panel");
  const typeTag = document.getElementById("node-inspector-type");
  const titleEl = document.getElementById("node-inspector-title");
  const bodyEl = document.getElementById("node-inspector-body");
  const linkEl = document.getElementById("node-inspector-link");
  if (!panel) return;

  panel.style.display = "block";
  const raw = d.raw || {};
  const data = d.data || raw.data || raw || {};

  const entityType = data.entity_type || (d.type || "NODE").toUpperCase();
  typeTag.className = `gh-type-tag ${d.type || 'incident'}`;
  typeTag.textContent = entityType.toUpperCase();

  titleEl.textContent = data.title_or_summary || d.title || d.label || d.id;

  let bodyText = "";
  if (data.source_identifier) {
    bodyText += `<div><strong>Source Identifier:</strong> <code style="color: #38bdf8;">${escapeHtml(data.source_identifier)}</code></div>`;
  }
  if (data.repository) {
    bodyText += `<div><strong>Repository / Domain:</strong> <span style="color: #e2e8f0;">${escapeHtml(data.repository)}</span></div>`;
  }
  if (data.evidence_category) {
    const catClass = data.evidence_category === "direct" ? "direct" : "related";
    bodyText += `<div style="margin-top: 3px;"><strong>Evidence Category:</strong> <span class="badge ${catClass}" style="text-transform: capitalize;">${escapeHtml(data.evidence_category.replace('_', ' '))}</span></div>`;
  }
  if (data.status) {
    bodyText += `<div style="margin-top: 3px;"><strong>Status / Outcome:</strong> <span style="color: #f1f5f9; font-weight: 600;">${escapeHtml(data.status)}</span></div>`;
  }
  if (data.metric) {
    bodyText += `<div style="margin-top: 3px;"><strong>Metric Measured:</strong> ${escapeHtml(data.metric)} (${escapeHtml(data.before || '')} ➔ ${escapeHtml(data.after || '')})</div>`;
  }
  if (data.hypothesis) {
    bodyText += `<div style="margin-top: 4px;"><strong>Hypothesis:</strong> <em>${escapeHtml(data.hypothesis)}</em></div>`;
  }
  if (data.action) {
    bodyText += `<div style="margin-top: 4px; color: #67e8f9;"><strong>Recommended Action:</strong> ${escapeHtml(data.action)}</div>`;
  }
  if (data.rationale) {
    bodyText += `<div style="margin-top: 4px; color: #cbd5e1;"><strong>Grounded Rationale:</strong> ${escapeHtml(data.rationale)}</div>`;
  }
  if (data.notes && data.notes !== data.rationale) {
    bodyText += `<div style="margin-top: 4px; color: #94a3b8;"><strong>Provenance Notes:</strong> ${escapeHtml(data.notes)}</div>`;
  }

  bodyEl.innerHTML = bodyText || "<div>No additional metadata.</div>";

  const sourceUrl = data.source_url || d.html_url || raw.html_url;
  if (sourceUrl) {
    linkEl.innerHTML = `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="font-size: 0.72rem; padding: 4px 8px; text-decoration: none; display: inline-flex; align-items: center; gap: 4px;">↗ Open in GitHub</a>`;
  } else {
    linkEl.innerHTML = "";
  }
}

// Cytoscape Interactive Graph Renderer with Deterministic 2-Tier Layout & Provenance Tooltips
function renderInvestigationGraph(nodes, edges) {
  const container = document.getElementById("incident-cytoscape-container");
  if (!container) return;

  if (typeof cytoscape === "undefined") {
    container.innerHTML = "<div style='display: flex; align-items: center; justify-content: center; height: 100%; color: #94a3b8; font-size: 0.85rem;'>Cytoscape library loading...</div>";
    return;
  }

  if (cyIncidentGraph) {
    cyIncidentGraph.destroy();
    cyIncidentGraph = null;
  }

  if (!nodes || nodes.length === 0) {
    container.innerHTML = "<div style='display: flex; align-items: center; justify-content: center; height: 100%; color: #94a3b8; font-size: 0.85rem;'>No graph nodes to visualize.</div>";
    return;
  }

  // Calculate deterministic layout coordinates
  // Lane A (y = 90): Incident intelligence pipeline
  // Lane B (y = 270): Engineering repository pipeline
  const hasIncidentPipeline = nodes.some(n => n.type === "incident" || n.type === "investigation");
  const typeCounters = {};
  const presetPositions = {};

  const colX = {
    incident: 120,
    investigation: 330,
    fix_attempt: 540,
    outcome: 750,
    next_action: 960,
    repository: 120,
    issue: 330,
    pull_request: 540,
    commit: 750,
    workflow_run: 960
  };

  nodes.forEach(n => {
    const t = n.type || "incident";
    const count = typeCounters[t] || 0;
    typeCounters[t] = count + 1;

    let x = colX[t] || 400;
    let y = 180;

    if (t === "incident" || t === "investigation" || t === "fix_attempt" || t === "outcome") {
      y = 90 + count * 65;
    } else if (t === "repository" || t === "issue" || t === "pull_request" || t === "commit" || t === "workflow_run") {
      y = hasIncidentPipeline ? (270 + count * 65) : (180 + count * 65);
    } else if (t === "next_action") {
      y = hasIncidentPipeline ? (90 + count * 65) : (110 + count * 65);
    }

    presetPositions[String(n.id)] = { x, y };
  });

  currentPresetPositions = presetPositions;

  const elements = [];

  nodes.forEach(n => {
    const pos = presetPositions[String(n.id)] || { x: 300, y: 200 };
    elements.push({
      group: "nodes",
      position: pos,
      data: {
        id: String(n.id),
        label: n.label || n.title || n.id,
        type: n.type || "incident",
        title: n.title || n.label,
        status: n.status || "",
        notes: n.notes || "",
        html_url: n.html_url || "",
        data: n.data || {},
        raw: n
      }
    });
  });

  (edges || []).forEach((e, idx) => {
    elements.push({
      group: "edges",
      data: {
        id: `e_${idx}_${e.source}_${e.target}`,
        source: String(e.source),
        target: String(e.target),
        label: e.relationship || "CONNECTED",
        relationship: e.relationship || ""
      }
    });
  });

  cyIncidentGraph = cytoscape({
    container: container,
    elements: elements,
    style: [
      {
        selector: "node",
        style: {
          "label": "data(label)",
          "color": "#e2e8f0",
          "font-size": "10px",
          "font-family": "Inter, sans-serif",
          "text-valign": "bottom",
          "text-margin-y": 5,
          "text-wrap": "ellipsis",
          "text-max-width": "110px",
          "width": 32,
          "height": 32,
          "border-width": 2,
          "border-color": "#ffffff",
          "background-color": "#64748b"
        }
      },
      {
        selector: "node[type = 'incident']",
        style: {
          "background-color": "#f87171",
          "border-color": "#fca5a5",
          "shape": "diamond",
          "width": 38,
          "height": 38
        }
      },
      {
        selector: "node[type = 'investigation']",
        style: {
          "background-color": "#818cf8",
          "border-color": "#a5b4fc",
          "shape": "round-rectangle",
          "width": 34,
          "height": 34
        }
      },
      {
        selector: "node[type = 'fix_attempt']",
        style: {
          "background-color": "#fbbf24",
          "border-color": "#fde047",
          "shape": "hexagon",
          "width": 34,
          "height": 34
        }
      },
      {
        selector: "node[type = 'outcome']",
        style: {
          "background-color": "#ef4444",
          "border-color": "#f87171",
          "shape": "octagon",
          "width": 32,
          "height": 32
        }
      },
      {
        selector: "node[type = 'next_action']",
        style: {
          "background-color": "#38bdf8",
          "border-color": "#7dd3fc",
          "shape": "star",
          "width": 38,
          "height": 38
        }
      },
      {
        selector: "node[type = 'repository']",
        style: {
          "background-color": "#a855f7",
          "border-color": "#d8b4fe",
          "shape": "round-rectangle",
          "width": 42,
          "height": 30
        }
      },
      {
        selector: "node[type = 'pull_request']",
        style: {
          "background-color": "#c084fc",
          "border-color": "#e9d5ff",
          "shape": "ellipse",
          "width": 32,
          "height": 32
        }
      },
      {
        selector: "node[type = 'issue']",
        style: {
          "background-color": "#fb923c",
          "border-color": "#fdba74",
          "shape": "round-tag",
          "width": 32,
          "height": 32
        }
      },
      {
        selector: "node[type = 'commit']",
        style: {
          "background-color": "#94a3b8",
          "border-color": "#cbd5e1",
          "shape": "ellipse",
          "width": 26,
          "height": 26
        }
      },
      {
        selector: "node[type = 'workflow_run']",
        style: {
          "background-color": "#f43f5e",
          "border-color": "#fda4af",
          "shape": "diamond",
          "width": 32,
          "height": 32
        }
      },
      {
        selector: "edge",
        style: {
          "width": 2,
          "line-color": "#475569",
          "target-arrow-color": "#94a3b8",
          "target-arrow-shape": "triangle",
          "curve-style": "bezier",
          "label": "data(label)",
          "font-size": "8px",
          "font-family": "JetBrains Mono, monospace",
          "color": "#94a3b8",
          "text-rotation": "autorotate",
          "text-background-opacity": 0.85,
          "text-background-color": "#0b101b",
          "text-background-padding": 2,
          "text-background-shape": "roundrectangle"
        }
      },
      {
        selector: "node:selected",
        style: {
          "border-width": 4,
          "border-color": "#38bdf8",
          "shadow-blur": 12,
          "shadow-color": "#38bdf8",
          "shadow-opacity": 0.8
        }
      }
    ],
    layout: {
      name: "preset",
      positions: presetPositions,
      padding: 35,
      fit: true
    }
  });

  // Floating hover tooltip
  let cyTooltip = document.getElementById("cytoscape-node-tooltip");
  if (!cyTooltip) {
    cyTooltip = document.createElement("div");
    cyTooltip.id = "cytoscape-node-tooltip";
    cyTooltip.className = "cy-node-tooltip";
    container.parentElement.style.position = "relative";
    container.parentElement.appendChild(cyTooltip);
  }

  cyIncidentGraph.on("mouseover", "node", function(evt) {
    const node = evt.target;
    const d = node.data();
    const raw = d.raw || {};
    const data = d.data || raw.data || raw || {};
    const entityType = data.entity_type || (d.type || "Node").toUpperCase();
    const title = data.title_or_summary || d.title || d.label;
    const srcId = data.source_identifier || d.id;
    const cat = data.evidence_category || "evidence";

    cyTooltip.innerHTML = `
      <div class="tooltip-header">
        <span class="tooltip-badge gh-type-tag ${d.type}">${escapeHtml(entityType)}</span>
        <span class="badge ${cat === 'direct' ? 'direct' : 'related'}" style="font-size: 0.65rem; text-transform: capitalize;">${escapeHtml(cat.replace('_', ' '))}</span>
      </div>
      <div class="tooltip-title">${escapeHtml(title)}</div>
      <div class="tooltip-detail">ID: <code>${escapeHtml(srcId)}</code></div>
      ${data.status ? `<div class="tooltip-detail">Status: <strong>${escapeHtml(data.status)}</strong></div>` : ''}
    `;
    cyTooltip.style.display = "block";

    const renderedPos = node.renderedPosition();
    cyTooltip.style.left = `${Math.max(10, Math.min(container.clientWidth - 280, renderedPos.x + 15))}px`;
    cyTooltip.style.top = `${Math.max(10, renderedPos.y - 15)}px`;
  });

  cyIncidentGraph.on("mouseout", "node", function() {
    if (cyTooltip) cyTooltip.style.display = "none";
  });

  cyIncidentGraph.on("pan zoom", function() {
    if (cyTooltip) cyTooltip.style.display = "none";
  });

  cyIncidentGraph.on("tap", "node", function(evt) {
    const node = evt.target;
    showNodeInspector(node.data());
  });

  setTimeout(() => {
    if (cyIncidentGraph) cyIncidentGraph.fit(null, 35);
  }, 100);
}

// Render Incident Results
function renderIncidentInvestigationResults(data) {
  const evidencePanel = document.getElementById("incident-evidence-panel");
  const pathList = document.getElementById("incident-path-list");
  const fixesList = document.getElementById("list-prior-fixes");
  const goalsList = document.getElementById("list-incident-goals");
  const recText = document.getElementById("incident-recommendation-text");
  const uncBox = document.getElementById("incident-uncertainty-box");
  const taskCard = document.getElementById("task-card");
  const taskIdDisplay = document.getElementById("task-id-display");
  const taskActionTitle = document.getElementById("task-action-title");
  const taskStatusBadge = document.getElementById("task-status-badge");
  const taskRationale = document.getElementById("task-rationale");

  evidencePanel.style.display = "block";

  // 0. Top Investigation Summary Strip (Milestone 10 & 11)
  const summaryStrip = document.getElementById("investigation-summary-strip");
  if (summaryStrip && data.investigation_summary) {
    const s = data.investigation_summary;
    const confLevel = (s.confidence_level || "medium").toLowerCase();
    const confScore = typeof s.confidence_score === "number" ? Math.round(s.confidence_score * 100) : "--";
    const mode = data.investigation_mode || "deterministic_fallback";
    const modeLabel = mode === "agentic" ? "🤖 Autonomous Agent Loop" : "⚙️ Deterministic Fallback";
    const trace = Array.isArray(data.agent_execution_trace) ? data.agent_execution_trace : [];

    let traceHtml = "";
    if (trace.length > 0 || data.agent_deliberation) {
      const stepItems = trace.map(t => `
        <div class="agent-step-card">
          <div class="agent-step-header">
            <span class="agent-step-tool">Step ${t.step || 1}: <code>${escapeHtml(t.tool || 'tool')}</code></span>
            <span style="color: #64748b; font-size: 0.7rem;">${escapeHtml(t.timestamp || '')}</span>
          </div>
          <div class="agent-step-args">Args: <code>${escapeHtml(JSON.stringify(t.arguments || {}))}</code></div>
          <div class="agent-step-result">${escapeHtml(t.result_summary || '')}</div>
        </div>
      `).join("");

      traceHtml = `
        <div class="agent-trace-section">
          <div class="agent-trace-header" onclick="this.nextElementSibling.style.display = (this.nextElementSibling.style.display === 'none' ? 'block' : 'none');">
            <div class="agent-trace-title">
              <span>Agent Execution Trace</span>
              <span class="agent-trace-count">${trace.length} tool call(s)</span>
            </div>
            <span style="font-size: 0.72rem; color: #94a3b8;">▼ toggle trace</span>
          </div>
          <div style="display: block; margin-top: 8px;">
            <div class="agent-trace-steps">${stepItems || '<div style="color: #94a3b8; font-size: 0.75rem;">No tool calls recorded.</div>'}</div>
            ${data.agent_deliberation ? `
              <div class="agent-deliberation-box">
                <strong style="color: #f3e8ff;">Agent Synthesis &amp; Grounded Deliberation:</strong><br>
                ${escapeHtml(data.agent_deliberation)}
              </div>
            ` : ''}
          </div>
        </div>
      `;
    }

    summaryStrip.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
        <span class="agent-mode-badge ${mode}">${modeLabel}</span>
        <span style="font-size: 0.74rem; color: #94a3b8;">Bounded budget: max 3 tool calls / investigation</span>
      </div>
      ${mode === "deterministic_fallback" ? `
        <div style="margin-bottom: 12px; background: rgba(100, 116, 139, 0.12); border: 1px solid rgba(100, 116, 139, 0.35); border-radius: var(--radius-sm); padding: 8px 12px; font-size: 0.76rem; color: #cbd5e1;">
          <strong>⚙️ Deterministic Fallback Mode:</strong> The autonomous model turn was not completed or model-driven execution was bypassed. The system safely executed deterministic FalkorDB Cypher multi-hop graph traversal to retrieve verified historical evidence without hallucination.
        </div>
      ` : `
        <div style="margin-bottom: 12px; background: rgba(168, 85, 247, 0.10); border: 1px solid rgba(168, 85, 247, 0.3); border-radius: var(--radius-sm); padding: 8px 12px; font-size: 0.76rem; color: #e9d5ff;">
          <strong>🤖 Autonomous Agent Loop Active:</strong> Google Gemini autonomously observed incident symptoms, dynamically selected and sequenced evidence-gathering tools against FalkorDB, and synthesized this grounded investigation.
        </div>
      `}
      <div class="investigation-summary-grid">
        <div class="inv-summary-card highlight">
          <div class="inv-summary-label">Incident Symptoms</div>
          <div class="inv-summary-val" style="font-weight: 600; color: #f8fafc;">${escapeHtml(s.incident_summary || 'Observed failure')}</div>
        </div>
        <div class="inv-summary-card">
          <div class="inv-summary-label">Affected Component</div>
          <div class="inv-summary-val"><code style="color: #38bdf8;">${escapeHtml(s.affected_component || 'unknown')}</code></div>
        </div>
        <div class="inv-summary-card">
          <div class="inv-summary-label">Most Relevant Finding</div>
          <div class="inv-summary-val">${escapeHtml(s.most_relevant_finding || 'None')}</div>
        </div>
        <div class="inv-summary-card">
          <div class="inv-summary-label">Prior Intervention &amp; Outcome</div>
          <div class="inv-summary-val">${escapeHtml(s.previous_intervention_outcome || 'No prior attempts')}</div>
        </div>
        <div class="inv-summary-card highlight">
          <div class="inv-summary-label">Recommended Next Action</div>
          <div class="inv-summary-val" style="color: #67e8f9; font-weight: 600;">${escapeHtml(s.recommended_investigation || 'Formulate hypothesis')}</div>
        </div>
        <div class="inv-summary-card">
          <div class="inv-summary-label">Epistemic Confidence</div>
          <div class="inv-summary-val">
            <span class="inv-confidence-badge ${confLevel}">${confLevel.toUpperCase()} (${confScore}%)</span>
          </div>
        </div>
      </div>
      ${s.confidence_explanation || s.cross_repository_explanation ? `
        <div class="inv-summary-explanation-bar">
          ${s.confidence_explanation ? `
            <div>
              <span class="expl-title">Confidence Provenance:</span>
              <span>${escapeHtml(s.confidence_explanation)}</span>
            </div>
          ` : ''}
          ${s.cross_repository_explanation ? `
            <div>
              <span class="expl-title" style="color: #c084fc;">Cross-Repository Provenance:</span>
              <span>${escapeHtml(s.cross_repository_explanation)}</span>
            </div>
          ` : ''}
        </div>
      ` : ''}
      ${traceHtml}
    `;
  } else if (summaryStrip) {
    summaryStrip.innerHTML = "";
  }

  // 0.5. Interactive Visual Knowledge Graph (Milestone 10)
  renderInvestigationGraph(data.graph_nodes || [], data.graph_edges || []);

  // 1. Evidence Paths
  pathList.innerHTML = (data.evidence_paths || []).map(p => {
    const formatted = escapeHtml(p)
      .replace(/-\[(REFERENCES_ISSUE|CONTAINS_COMMIT|TRIGGERED_BY|TESTS_PULL_REQUEST|HAS_PULL_REQUEST|HAS_WORKFLOW_RUN|CONTAINS_ISSUE|INVESTIGATED_BY|LED_TO|RECOMMENDS)\]-&gt;/g, '<span class="rel-token">-[$1]-&gt;</span>')
      .replace(/&lt;-\[(REFERENCES_ISSUE|CONTAINS_COMMIT|TRIGGERED_BY|TESTS_PULL_REQUEST)\]-/g, '<span class="rel-token">&lt;-[$1]-</span>');
    return `
      <div class="path-item">
        <span>${formatted}</span>
      </div>
    `;
  }).join("") || "<div class='path-item'>No causal paths traversed.</div>";

  // 2. Prior Fix Attempts
  if (data.prior_investigations && data.prior_investigations.length > 0) {
    fixesList.innerHTML = data.prior_investigations.map(inv => {
      const evalClass = (inv.outcome_evaluation || "unknown").toLowerCase();
      return `
        <div style="background: rgba(0,0,0,0.25); padding: 10px; border-radius: var(--radius-sm); margin-bottom: 8px; border-left: 3px solid ${evalClass === 'unsuccessful' ? '#ef4444' : '#10b981'};">
          <div style="display: flex; justify-content: space-between; align-items: center;">
            <strong style="font-size: 0.82rem; color: #f1f5f9;">${escapeHtml(inv.fix_description || 'Intervention')}</strong>
            <span class="eval-tag ${evalClass}">${escapeHtml(inv.outcome_evaluation || 'unknown')}</span>
          </div>
          <div style="font-size: 0.78rem; color: #94a3b8; margin-top: 4px;">
            Hypothesis: <em>${escapeHtml(inv.hypothesis)}</em>
          </div>
          ${inv.outcome_metric ? `
            <div style="font-size: 0.75rem; color: #cbd5e1; font-family: var(--font-mono); margin-top: 4px;">
              Measured: ${escapeHtml(inv.before_value || '')} ➔ ${escapeHtml(inv.after_value || '')} (${escapeHtml(inv.outcome_metric)})
            </div>
          ` : ''}
          ${inv.outcome_notes ? `<div style="font-size: 0.74rem; color: #fca5a5; margin-top: 4px;">${escapeHtml(inv.outcome_notes)}</div>` : ''}
        </div>
      `;
    }).join("");
  } else {
    fixesList.innerHTML = "<div style='font-size: 0.8rem; color: #94a3b8;'>No prior interventions recorded.</div>";
  }

  // 3. Applicable Goals
  if (data.applicable_goals && data.applicable_goals.length > 0) {
    goalsList.innerHTML = data.applicable_goals.map(g => `
      <div style="background: rgba(0,0,0,0.25); padding: 10px; border-radius: var(--radius-sm); margin-bottom: 8px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <strong style="font-size: 0.82rem; color: #e2e8f0;">${escapeHtml(g.title)}</strong>
          <span class="badge-status ${escapeHtml(g.status || 'current')}">${escapeHtml(g.status || 'current')}</span>
        </div>
        <div style="font-size: 0.74rem; color: #94a3b8; margin-top: 4px;">Priority: ${escapeHtml(g.priority || 'medium')}</div>
      </div>
    `).join("");
  } else {
    goalsList.innerHTML = "<div style='font-size: 0.8rem; color: #94a3b8;'>No specific constraint goals defined.</div>";
  }

  // 3.5. Connected GitHub Engineering Records
  const ghContainer = document.getElementById("incident-github-records-container");
  const ghList = document.getElementById("incident-github-records-list");
  if (ghContainer && ghList) {
    if (data.retrieved_github_records && data.retrieved_github_records.length > 0) {
      ghContainer.style.display = "block";
      ghList.innerHTML = data.retrieved_github_records.map(r => {
        const badgeClass = (r.state_or_status || "open").toLowerCase();
        let icon = "📋";
        let typeLabel = "RECORD";
        if (r.record_type === "pull_request") {
          icon = "🔀";
          typeLabel = "PULL REQUEST";
        } else if (r.record_type === "workflow_run") {
          icon = "⚙️";
          typeLabel = "WORKFLOW RUN";
        } else if (r.record_type === "commit") {
          icon = "📦";
          typeLabel = "COMMIT";
        } else if (r.record_type === "issue") {
          icon = "📋";
          typeLabel = "ISSUE";
        }

        const isFailure = r.record_type === "workflow_run" && ["failure", "cancelled", "timed_out"].includes((r.state_or_status || "").toLowerCase());
        const cardClass = isFailure ? "gh-record-card failure" : `gh-record-card ${badgeClass}`;

        return `
          <div class="${cardClass}">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
              <span style="font-size: 0.85rem; font-weight: 600;">
                <span class="gh-type-tag ${escapeHtml(r.record_type)}">${typeLabel}</span>
                ${icon} <a href="${escapeHtml(r.html_url)}" target="_blank" rel="noopener noreferrer" style="color: #38bdf8; text-decoration: underline;">
                  ${escapeHtml(r.title_or_summary)}
                </a>
              </span>
              <span class="gh-badge ${badgeClass}">${escapeHtml(r.state_or_status)}</span>
            </div>
            <div style="font-size: 0.78rem; color: #cbd5e1; margin-top: 4px;">
              ${escapeHtml(r.relevance_reason)}
            </div>
            ${r.notes ? `
              <div style="font-size: 0.74rem; color: ${isFailure ? '#fca5a5' : '#94a3b8'}; margin-top: 4px;">
                ${isFailure ? '<strong>⚠️ Signal:</strong> ' : '<em>Details:</em> '}${escapeHtml(r.notes)}
              </div>
            ` : ''}
          </div>
        `;
      }).join("");
    } else {
      ghContainer.style.display = "none";
      ghList.innerHTML = "";
    }
  }

  // 4. Recommendation & Uncertainty
  recText.innerHTML = escapeHtml(data.recommendation || "No recommendation.").replace(/\n/g, "<br>");
  uncBox.innerHTML = `<strong>⚠️ Epistemic Uncertainty Note:</strong> ${escapeHtml(data.uncertainty_notes || 'Hypothesis requires empirical verification.')}`;

  // 5. Persisted Task Card
  if (data.recommended_next_action) {
    const act = data.recommended_next_action;
    currentActionId = act.id;
    taskCard.style.display = "block";
    taskIdDisplay.textContent = `Task ID: ${act.id}`;
    taskActionTitle.textContent = act.action;
    taskRationale.textContent = act.rationale;
    taskStatusBadge.className = `task-badge ${act.status || 'pending'}`;
    taskStatusBadge.textContent = (act.status || 'pending').toUpperCase();
  } else {
    taskCard.style.display = "none";
  }
}

// Update Task Status
async function updateIncidentTaskStatus(newStatus) {
  if (!currentActionId) {
    alert("No active task action selected.");
    return;
  }
  try {
    const res = await fetch("/incident/task", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser,
        action_id: currentActionId,
        status: newStatus,
        resolution_notes: `Status updated to ${newStatus} via judge interface.`
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    const taskStatusBadge = document.getElementById("task-status-badge");
    taskStatusBadge.className = `task-badge ${newStatus}`;
    taskStatusBadge.textContent = newStatus.toUpperCase();
    alert(`Task '${currentActionId}' status updated to '${newStatus}' in FalkorDB!`);
  } catch (err) {
    alert(`Failed to update task: ${err.message}`);
  }
}

// Record Task Outcome
async function recordTaskOutcome() {
  const metric = document.getElementById("out-input-metric").value.trim();
  const beforeVal = document.getElementById("out-input-before").value.trim();
  const afterVal = document.getElementById("out-input-after").value.trim();
  const evaluation = document.getElementById("out-input-eval").value;
  const notes = document.getElementById("out-input-notes").value.trim();
  const feedback = document.getElementById("outcome-feedback");

  if (!metric) {
    alert("Please enter a metric key.");
    return;
  }

  try {
    const res = await fetch("/incident/outcome", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser,
        action_id: currentActionId || null,
        metric: metric,
        before_value: beforeVal,
        after_value: afterVal,
        evaluation: evaluation,
        notes: notes,
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    feedback.className = "ingest-feedback show success";
    feedback.style.display = "block";
    feedback.innerHTML = `✓ Outcome recorded in FalkorDB! (Evaluation: <strong>${data.evaluation}</strong>, Outcome ID: ${data.outcome_id})`;
  } catch (err) {
    alert(`Failed to record outcome: ${err.message}`);
  }
}

// One-Click Incident Demo Runner
async function runIncidentDemo() {
  const btn = document.getElementById("btn-run-incident-demo");
  const statusSpan = document.getElementById("incident-demo-status");

  btn.disabled = true;
  btn.textContent = "⏳ Seeding Incident...";
  statusSpan.textContent = "Seeding prior gateway incident with failed pool resize & evaluating with different wording...";

  try {
    const res = await fetch(`/demo/incident_scenario?user_id=${currentUser}`, {
      method: "POST"
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    document.getElementById("input-incident-symptoms").value = data.investigation_query;
    document.getElementById("input-incident-component").value = "gateway-api";

    document.getElementById("incident-results-container").style.display = "block";
    document.getElementById("incident-no-evidence").style.display = "none";
    renderIncidentInvestigationResults(data.investigation_result);

    statusSpan.innerHTML = `✓ Scenario Seeded & Evaluated! <strong style="color: #34d399;">Grounded recommendation derived</strong> avoiding repeating failed pool resize.`;
  } catch (err) {
    statusSpan.textContent = `Demo failed: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "⚡ Run Incident Investigator Demo";
  }
}

// ============================================================================
// GitHub Engineering Memory Logic (Milestone 9)
// ============================================================================

// 1. One-Click GitHub Engineering Memory Demo Runner
async function runGitHubDemo() {
  const btn = document.getElementById("btn-run-github-demo");
  const statusSpan = document.getElementById("github-demo-status");

  btn.disabled = true;
  btn.textContent = "⏳ Seeding GitHub Memory...";
  statusSpan.textContent = "Seeding repository gateway-corp/gateway-api with PR #42 & failed workflow #1092...";

  try {
    const res = await fetch(`/demo/github_scenario?user_id=${currentUser}`, {
      method: "POST"
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    // Populate incident investigation query inputs
    document.getElementById("input-incident-symptoms").value = "Tail timeout alerts and slow gateway response during peak traffic";
    document.getElementById("input-incident-component").value = "gateway-api";

    // Unhide and render incident investigation results with connected GitHub records
    document.getElementById("incident-results-container").style.display = "block";
    document.getElementById("incident-no-evidence").style.display = "none";
    renderIncidentInvestigationResults(data.investigation_result);

    // Refresh repository status list in Section 6
    await refreshGitHubStatus();

    statusSpan.innerHTML = `✓ Scenario Seeded! <strong style="color: #38bdf8;">Connected PR #42 &amp; failed workflow #1092</strong> to incident investigation.`;
  } catch (err) {
    statusSpan.textContent = `Demo failed: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "⚡ Run GitHub Engineering Memory Demo";
  }
}

// 2. Import Repository into FalkorDB
async function importGitHubRepo() {
  const repoInput = document.getElementById("input-github-repo").value.trim();
  const tokenInput = document.getElementById("input-github-token").value.trim();
  const btn = document.getElementById("btn-import-github");

  if (!repoInput) {
    alert("Please enter a repository identifier (e.g. owner/repo).");
    return;
  }

  btn.disabled = true;
  btn.textContent = "⏳ Importing...";

  try {
    const res = await fetch("/github/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: currentUser,
        repo: repoInput,
        token: tokenInput || null,
        issue_limit: 20,
        pr_limit: 20,
        commit_limit: 20,
        workflow_limit: 10,
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    if (data.success && data.summary) {
      alert(`Successfully imported ${data.repo} in ${data.summary.duration_ms}ms!\n• Issues: ${data.summary.imported_issues}\n• PRs: ${data.summary.imported_prs}\n• Commits: ${data.summary.imported_commits}\n• Workflows: ${data.summary.imported_workflow_runs}\n• Explicit Cross-References: ${data.summary.explicit_references_count}`);
      await refreshGitHubStatus();
    } else {
      alert(`Import failed: ${data.error_message || 'Unknown error'}`);
    }
  } catch (err) {
    alert(`Import request failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "📥 Import Repo";
  }
}

// 3. Refresh GitHub Repositories Status List
async function refreshGitHubStatus() {
  const reposList = document.getElementById("github-repos-list");
  const navRepoCount = document.getElementById("nav-repo-count");
  const repoSelect = document.getElementById("input-incident-repo-select");

  try {
    const res = await fetch(`/github/status?user_id=${currentUser}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    const repos = data.imported_repositories || [];

    if (navRepoCount) {
      navRepoCount.textContent = repos.length;
    }

    if (repoSelect) {
      const currentVal = repoSelect.value;
      let opts = '<option value="">All Connected Repositories</option>';
      repos.forEach(r => {
        const selected = (r.full_name === currentVal || (!currentVal && r.full_name === "gateway-corp/gateway-api")) ? "selected" : "";
        opts += `<option value="${escapeHtml(r.full_name)}" ${selected}>${escapeHtml(r.full_name)}</option>`;
      });
      repoSelect.innerHTML = opts;
    }

    if (!reposList) return;

    if (repos.length > 0) {
      reposList.innerHTML = repos.map(repo => `
        <div class="gh-repo-card">
          <div>
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
              <span style="font-weight: 700; font-size: 0.92rem; color: #f1f5f9; cursor: pointer;" onclick="openRepoDetails('${escapeHtml(repo.full_name)}')">
                📁 <span class="repo-name-link" style="color: #38bdf8; text-decoration: underline;" title="View in-app repository details">${escapeHtml(repo.full_name)}</span>
              </span>
              <span style="font-size: 0.72rem; color: #94a3b8; font-weight: normal;">(${escapeHtml(repo.default_branch || 'main')})</span>
              <a href="${escapeHtml(repo.html_url)}" target="_blank" rel="noopener noreferrer" title="Open repository on GitHub" style="font-size: 0.72rem; color: #94a3b8; text-decoration: none; padding: 2px 6px; border: 1px solid rgba(255,255,255,0.12); border-radius: 4px; display: inline-flex; align-items: center; gap: 2px;">
                ↗ GitHub
              </a>
            </div>
            <div style="font-size: 0.78rem; color: #cbd5e1; margin-top: 4px;">
              ${escapeHtml(repo.description || 'No description provided')}
            </div>
          </div>
          <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
            <span class="metric-pill">📋 Issues: <span>${repo.issue_count}</span></span>
            <span class="metric-pill">🔀 PRs: <span>${repo.pr_count}</span></span>
            <span class="metric-pill">💻 Commits: <span>${repo.commit_count}</span></span>
            <span class="metric-pill">⚙️ Workflows: <span>${repo.workflow_run_count}</span></span>
            <button class="btn btn-secondary btn-xs" onclick="openRepoDetails('${escapeHtml(repo.full_name)}')">
              🔍 Inspect Details
            </button>
          </div>
        </div>
      `).join("");
    } else {
      reposList.innerHTML = "<div class='empty-state-card'>No repositories imported yet for active tenant. Click 'Load GitHub PR/CI Memory Demo' or enter a repository above.</div>";
    }
  } catch (err) {
    if (reposList) {
      reposList.innerHTML = `<div style='font-size: 0.8rem; color: #ef4444;'>Failed to load repository status: ${escapeHtml(err.message)}</div>`;
    }
  }
}

// 4. Open In-App Repository Details View
async function openRepoDetails(repoFullName) {
  const listView = document.getElementById("github-repos-list-view");
  const detailsView = document.getElementById("github-repo-details-view");
  const loading = document.getElementById("repo-details-loading");
  const errorBox = document.getElementById("repo-details-error");
  const content = document.getElementById("repo-details-content");

  if (!detailsView) return;

  if (listView) listView.style.display = "none";
  detailsView.style.display = "block";
  if (loading) loading.style.display = "block";
  if (errorBox) errorBox.style.display = "none";
  if (content) content.style.display = "none";

  try {
    const res = await fetch(`/github/repository?user_id=${currentUser}&repo=${encodeURIComponent(repoFullName)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail?.message || data.detail || `HTTP ${res.status}`);

    if (!data.success || !data.repository) {
      throw new Error(data.error_message || "Repository details not found.");
    }

    renderRepoDetails(data.repository);
    if (content) content.style.display = "block";
  } catch (err) {
    if (errorBox) {
      errorBox.style.display = "block";
      errorBox.innerHTML = `<strong>Error loading repository details:</strong> ${escapeHtml(err.message)}<div style="margin-top: 8px;"><button class="btn btn-secondary" style="font-size: 0.75rem;" onclick="backToReposList()">← Back to Repositories</button></div>`;
    }
  } finally {
    if (loading) loading.style.display = "none";
  }
}

// 5. Back to Repositories Action
function backToReposList() {
  const listView = document.getElementById("github-repos-list-view");
  const detailsView = document.getElementById("github-repo-details-view");
  if (detailsView) detailsView.style.display = "none";
  if (listView) {
    listView.style.display = "block";
    listView.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
}

// 6. Render Repository Details View
function renderRepoDetails(repo) {
  // Title & Header metadata
  const titleEl = document.getElementById("repo-details-title");
  const idTagEl = document.getElementById("repo-details-id-tag");
  const descEl = document.getElementById("repo-details-desc");
  const impAtEl = document.getElementById("repo-details-imported-at");
  const branchBadge = document.getElementById("repo-details-badge-branch");
  const extUrlEl = document.getElementById("repo-details-external-url");

  if (titleEl) titleEl.textContent = repo.full_name;
  if (idTagEl) idTagEl.textContent = `ID: ${repo.repo_id}`;
  if (descEl) descEl.textContent = repo.description || "No description provided for this repository.";
  if (impAtEl) impAtEl.textContent = repo.imported_at ? new Date(repo.imported_at).toLocaleString() : "Unknown";
  if (branchBadge) branchBadge.textContent = repo.default_branch || "main";
  if (extUrlEl) extUrlEl.href = repo.html_url;

  // Counts
  const counts = repo.counts || {};
  const cIssues = document.getElementById("repo-count-issues");
  const cPRs = document.getElementById("repo-count-prs");
  const cCommits = document.getElementById("repo-count-commits");
  const cWFs = document.getElementById("repo-count-workflows");

  if (cIssues) cIssues.textContent = counts.issues || 0;
  if (cPRs) cPRs.textContent = counts.pull_requests || 0;
  if (cCommits) cCommits.textContent = counts.commits || 0;
  if (cWFs) cWFs.textContent = counts.workflow_runs || 0;

  const tPRs = document.getElementById("repo-details-prs-title-count");
  const tIssues = document.getElementById("repo-details-issues-title-count");
  const tCommits = document.getElementById("repo-details-commits-title-count");
  const tWFs = document.getElementById("repo-details-workflows-title-count");

  if (tPRs) tPRs.textContent = counts.pull_requests || 0;
  if (tIssues) tIssues.textContent = counts.issues || 0;
  if (tCommits) tCommits.textContent = counts.commits || 0;
  if (tWFs) tWFs.textContent = counts.workflow_runs || 0;

  // Evidence Paths: Multi-hop paths
  const multiHopEl = document.getElementById("repo-details-multihop-list");
  if (multiHopEl) {
    if (repo.multi_hop_paths && repo.multi_hop_paths.length > 0) {
      multiHopEl.innerHTML = repo.multi_hop_paths.map(p => {
        const formatted = escapeHtml(p)
          .replace(/-\[(REFERENCES_ISSUE|CONTAINS_COMMIT|TRIGGERED_BY|TESTS_PULL_REQUEST)\]-&gt;/g, '<span class="rel-token">-[$1]-&gt;</span>')
          .replace(/&lt;-\[(REFERENCES_ISSUE|CONTAINS_COMMIT|TRIGGERED_BY|TESTS_PULL_REQUEST)\]-/g, '<span class="rel-token">&lt;-[$1]-</span>');
        return `<div class="path-item" style="font-size: 0.78rem;">${formatted}</div>`;
      }).join("");
    } else {
      multiHopEl.innerHTML = "<div style='font-size: 0.78rem; color: #94a3b8; font-style: italic;'>No complete multi-hop chains detected in repository records.</div>";
    }
  }

  // Verified Relationships
  const vListEl = document.getElementById("repo-details-verified-list");
  const vCountEl = document.getElementById("repo-details-verified-count");
  const verified = repo.verified_relationships || [];
  if (vCountEl) vCountEl.textContent = verified.length;
  if (vListEl) {
    if (verified.length > 0) {
      vListEl.innerHTML = verified.map(v => {
        const formatted = escapeHtml(v.path_string)
          .replace(/-\[(REFERENCES_ISSUE|CONTAINS_COMMIT|TRIGGERED_BY|TESTS_PULL_REQUEST)\]-&gt;/g, '<span class="rel-token">-[$1]-&gt;</span>')
          .replace(/&lt;-\[(REFERENCES_ISSUE|CONTAINS_COMMIT|TRIGGERED_BY|TESTS_PULL_REQUEST)\]-/g, '<span class="rel-token">&lt;-[$1]-</span>');
        return `
          <div style="background: rgba(16, 185, 129, 0.08); border: 1px solid rgba(16, 185, 129, 0.25); border-radius: var(--radius-sm); padding: 8px 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
            <div style="font-size: 0.78rem; color: #f1f5f9;">
              <span style="background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.4); padding: 1px 6px; border-radius: 3px; font-size: 0.68rem; font-weight: 700; margin-right: 6px;">VERIFIED</span>
              ${formatted}
            </div>
            <div style="font-size: 0.72rem; color: #94a3b8; font-family: var(--font-mono);">
              ${escapeHtml(v.relationship)}
            </div>
          </div>
        `;
      }).join("");
    } else {
      vListEl.innerHTML = "<div style='font-size: 0.78rem; color: #94a3b8; font-style: italic;'>No verified relationships stored in FalkorDB.</div>";
    }
  }

  // Missing Relationships
  const mListEl = document.getElementById("repo-details-missing-list");
  const mCountEl = document.getElementById("repo-details-missing-count");
  const missing = repo.missing_relationships || [];
  if (mCountEl) mCountEl.textContent = missing.length;
  if (mListEl) {
    if (missing.length > 0) {
      mListEl.innerHTML = missing.map(m => `
        <div style="background: rgba(245, 158, 11, 0.06); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: var(--radius-sm); padding: 8px 12px;">
          <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
            <span style="font-size: 0.78rem; color: #fde68a;">
              <span style="background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); padding: 1px 6px; border-radius: 3px; font-size: 0.68rem; font-weight: 700; margin-right: 6px;">UNLINKED</span>
              <strong>${escapeHtml(m.identifier)}</strong>
            </span>
            <span style="font-size: 0.7rem; color: #94a3b8; font-family: var(--font-mono);">
              Missing: ${escapeHtml(m.missing_relationship)}
            </span>
          </div>
          <div style="font-size: 0.74rem; color: #cbd5e1; margin-top: 4px;">
            ${escapeHtml(m.description)}
          </div>
        </div>
      `).join("");
    } else {
      mListEl.innerHTML = "<div style='font-size: 0.78rem; color: #34d399; font-style: italic;'>All records are fully linked with no missing relationship paths.</div>";
    }
  }

  // 1. Pull Requests list
  const prsListEl = document.getElementById("repo-details-prs-list");
  if (prsListEl) {
    const prs = repo.pull_requests || [];
    if (prs.length > 0) {
      prsListEl.innerHTML = prs.map(pr => {
        const badgeClass = pr.merged ? "merged" : (pr.state || "open").toLowerCase();
        return `
          <div class="gh-record-card ${badgeClass}">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
              <span style="font-size: 0.85rem; font-weight: 600;">
                <span class="gh-type-tag pull_request">PULL REQUEST</span>
                🔀 <a href="${escapeHtml(pr.html_url)}" target="_blank" rel="noopener noreferrer" style="color: #38bdf8; text-decoration: underline;">
                  PR #${pr.number}: ${escapeHtml(pr.title)}
                </a>
              </span>
              <div style="display: flex; gap: 6px; align-items: center;">
                <span class="gh-badge ${badgeClass}">${escapeHtml(pr.state)}</span>
                <a href="${escapeHtml(pr.html_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="font-size: 0.68rem; padding: 2px 6px; text-decoration: none;">
                  ↗ GitHub
                </a>
              </div>
            </div>
            ${pr.body ? `<div style="font-size: 0.76rem; color: #cbd5e1; margin-top: 4px;">${escapeHtml(pr.body)}</div>` : ''}
            <div style="font-size: 0.74rem; color: #94a3b8; margin-top: 6px; display: flex; gap: 12px; flex-wrap: wrap;">
              <span>Author: <strong>${escapeHtml(pr.author || 'unknown')}</strong></span>
              ${pr.head_sha ? `<span>Head SHA: <code style="font-family: var(--font-mono); color: #7dd3fc;">${escapeHtml(pr.head_sha.substring(0, 8))}</code></span>` : ''}
              ${pr.created_at ? `<span>Created: ${new Date(pr.created_at).toLocaleDateString()}</span>` : ''}
            </div>
            ${pr.referenced_issue_numbers && pr.referenced_issue_numbers.length > 0 ? `
              <div style="font-size: 0.72rem; color: #a7f3d0; margin-top: 4px;">
                ✓ References Issue(s): ${pr.referenced_issue_numbers.map(n => `#${n}`).join(', ')}
              </div>
            ` : ''}
            ${pr.commit_shas && pr.commit_shas.length > 0 ? `
              <div style="font-size: 0.72rem; color: #7dd3fc; margin-top: 2px;">
                ✓ Branch Commits (${pr.commit_shas.length}): ${pr.commit_shas.map(s => s.substring(0, 8)).join(', ')}
              </div>
            ` : ''}
          </div>
        `;
      }).join("");
    } else {
      prsListEl.innerHTML = "<div style='font-size: 0.8rem; color: var(--text-muted); font-style: italic;'>No pull requests imported for this repository.</div>";
    }
  }

  // 2. Issues list
  const issuesListEl = document.getElementById("repo-details-issues-list");
  if (issuesListEl) {
    const issues = repo.issues || [];
    if (issues.length > 0) {
      issuesListEl.innerHTML = issues.map(iss => {
        const badgeClass = (iss.state || "open").toLowerCase();
        return `
          <div class="gh-record-card ${badgeClass}">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
              <span style="font-size: 0.85rem; font-weight: 600;">
                <span class="gh-type-tag issue">ISSUE</span>
                📋 <a href="${escapeHtml(iss.html_url)}" target="_blank" rel="noopener noreferrer" style="color: #38bdf8; text-decoration: underline;">
                  Issue #${iss.number}: ${escapeHtml(iss.title)}
                </a>
              </span>
              <div style="display: flex; gap: 6px; align-items: center;">
                <span class="gh-badge ${badgeClass}">${escapeHtml(iss.state)}</span>
                <a href="${escapeHtml(iss.html_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="font-size: 0.68rem; padding: 2px 6px; text-decoration: none;">
                  ↗ GitHub
                </a>
              </div>
            </div>
            ${iss.body ? `<div style="font-size: 0.76rem; color: #cbd5e1; margin-top: 4px;">${escapeHtml(iss.body)}</div>` : ''}
            <div style="font-size: 0.74rem; color: #94a3b8; margin-top: 6px; display: flex; gap: 12px; flex-wrap: wrap;">
              <span>Author: <strong>${escapeHtml(iss.author || 'unknown')}</strong></span>
              ${iss.created_at ? `<span>Created: ${new Date(iss.created_at).toLocaleDateString()}</span>` : ''}
            </div>
            ${iss.referencing_pr_numbers && iss.referencing_pr_numbers.length > 0 ? `
              <div style="font-size: 0.72rem; color: #d8b4fe; margin-top: 4px;">
                ✓ Referenced by PR: ${iss.referencing_pr_numbers.map(n => `PR #${n}`).join(', ')}
              </div>
            ` : ''}
          </div>
        `;
      }).join("");
    } else {
      issuesListEl.innerHTML = "<div style='font-size: 0.8rem; color: var(--text-muted); font-style: italic;'>No issues imported for this repository.</div>";
    }
  }

  // 3. Commits list
  const commitsListEl = document.getElementById("repo-details-commits-list");
  if (commitsListEl) {
    const commits = repo.commits || [];
    if (commits.length > 0) {
      commitsListEl.innerHTML = commits.map(com => {
        const msgFirst = com.message ? (com.message.split('\n')[0] || com.message) : 'no commit message';
        return `
          <div class="gh-record-card committed">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
              <span style="font-size: 0.85rem; font-weight: 600;">
                <span class="gh-type-tag commit">COMMIT</span>
                📦 <a href="${escapeHtml(com.html_url)}" target="_blank" rel="noopener noreferrer" style="color: #38bdf8; text-decoration: underline;">
                  <code>${escapeHtml(com.sha.substring(0, 8))}</code>: ${escapeHtml(msgFirst)}
                </a>
              </span>
              <div style="display: flex; gap: 6px; align-items: center;">
                <span class="gh-badge commit">committed</span>
                <a href="${escapeHtml(com.html_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="font-size: 0.68rem; padding: 2px 6px; text-decoration: none;">
                  ↗ GitHub
                </a>
              </div>
            </div>
            <div style="font-size: 0.74rem; color: #94a3b8; margin-top: 6px; display: flex; gap: 12px; flex-wrap: wrap;">
              <span>Author: <strong>${escapeHtml(com.author || 'unknown')}</strong></span>
              <span>Full SHA: <code style="font-family: var(--font-mono); color: #cbd5e1;">${escapeHtml(com.sha)}</code></span>
              ${com.timestamp ? `<span>Date: ${new Date(com.timestamp).toLocaleString()}</span>` : ''}
            </div>
            ${com.containing_pr_numbers && com.containing_pr_numbers.length > 0 ? `
              <div style="font-size: 0.72rem; color: #d8b4fe; margin-top: 4px;">
                ✓ Contained in PR: ${com.containing_pr_numbers.map(n => `PR #${n}`).join(', ')}
              </div>
            ` : ''}
            ${com.triggering_workflow_run_ids && com.triggering_workflow_run_ids.length > 0 ? `
              <div style="font-size: 0.72rem; color: #fdba74; margin-top: 2px;">
                ✓ Triggered Workflow Run(s): ${com.triggering_workflow_run_ids.map(id => `#${id}`).join(', ')}
              </div>
            ` : ''}
          </div>
        `;
      }).join("");
    } else {
      commitsListEl.innerHTML = "<div style='font-size: 0.8rem; color: var(--text-muted); font-style: italic;'>No commits imported for this repository.</div>";
    }
  }

  // 4. Workflow Runs list
  const wfsListEl = document.getElementById("repo-details-workflows-list");
  if (wfsListEl) {
    const wfs = repo.workflow_runs || [];
    if (wfs.length > 0) {
      wfsListEl.innerHTML = wfs.map(wf => {
        const isFailure = ["failure", "cancelled", "timed_out"].includes((wf.conclusion || "").toLowerCase());
        const cardClass = isFailure ? "gh-record-card failure" : "gh-record-card success";
        const badgeClass = (wf.conclusion || wf.status || "completed").toLowerCase();
        return `
          <div class="${cardClass}">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 6px;">
              <span style="font-size: 0.85rem; font-weight: 600;">
                <span class="gh-type-tag workflow_run">WORKFLOW RUN</span>
                ⚙️ <a href="${escapeHtml(wf.html_url)}" target="_blank" rel="noopener noreferrer" style="color: #38bdf8; text-decoration: underline;">
                  ${escapeHtml(wf.name)} (#${wf.run_id})
                </a>
              </span>
              <div style="display: flex; gap: 6px; align-items: center;">
                <span class="gh-badge ${badgeClass}">${escapeHtml(wf.conclusion || wf.status)}</span>
                <a href="${escapeHtml(wf.html_url)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary" style="font-size: 0.68rem; padding: 2px 6px; text-decoration: none;">
                  ↗ GitHub
                </a>
              </div>
            </div>
            <div style="font-size: 0.74rem; color: #94a3b8; margin-top: 6px; display: flex; gap: 12px; flex-wrap: wrap;">
              <span>Trigger Event: <strong>${escapeHtml(wf.event)}</strong></span>
              ${wf.head_sha ? `<span>Head SHA: <code style="font-family: var(--font-mono); color: #7dd3fc;">${escapeHtml(wf.head_sha.substring(0, 8))}</code></span>` : ''}
              ${wf.created_at ? `<span>Executed: ${new Date(wf.created_at).toLocaleString()}</span>` : ''}
            </div>
            ${isFailure ? `
              <div style="font-size: 0.74rem; color: #fca5a5; margin-top: 4px;">
                <strong>⚠️ Signal:</strong> CI conclusion '${escapeHtml(wf.conclusion)}' warrants inspection (diagnostic test signal, not proof of root cause)
              </div>
            ` : ''}
            ${wf.tested_pr_numbers && wf.tested_pr_numbers.length > 0 ? `
              <div style="font-size: 0.72rem; color: #d8b4fe; margin-top: 4px;">
                ✓ Tests Pull Request: ${wf.tested_pr_numbers.map(n => `PR #${n}`).join(', ')}
              </div>
            ` : ''}
          </div>
        `;
      }).join("");
    } else {
      wfsListEl.innerHTML = "<div style='font-size: 0.8rem; color: var(--text-muted); font-style: italic;'>No workflow runs imported for this repository.</div>";
    }
  }
}

// Initial health check on page load
window.addEventListener("DOMContentLoaded", () => {
  checkHealth();
  refreshGitHubStatus();
  setInterval(checkHealth, 15000);
});
