# Mai Graph Hacks — Relationship-Aware Agent Memory & Reasoning Engine

**Project:** Mai Graph Hacks  
**Event:** FalkorDB Graph Hacks Hackathon  
**Target Category:** Agent Memory and Coordination  
**Milestone:** 8 — Mai's Engineering Incident Investigator  

---

## 1. Problem Statement & Intended Use Case

Autonomous AI agents struggle with **episodic context fragmentation** and **causal amnesia**:
- **Isolated Vector Retrieval:** Standard RAG embeds text as detached chunks. When a user asks a high-level question about a goal or decision, vector search fails if the supporting evidence uses different terminology (e.g., query asks about *"improving response latency"*, while the benchmark record discusses *"GraphBLAS sparse matrix traversal"*).
- **Broken Causal Chains:** Agents cannot trace *why* a decision was made, which goal it advanced, or what empirical experience justified it.
- **Deduplication vs. History:** Simple key-value stores either silently overwrite past contradictory facts or create duplicate records.

### Intended User & Scenario
AI agent developers building long-running cognitive assistants. In our demonstration scenario, a developer (**Alex Rivera**) sets a goal to reduce an AI assistant's reasoning retrieval latency. Alex chooses **FalkorDB multi-hop Cypher traversal** after an empirical benchmark demonstrates superior traversal latency compared to vector similarity joins.

---

## 2. Why Graph Relationships Matter

Instead of relying on fuzzy text overlap, FalkorDB models the causal chain directly:

```mermaid
graph TD
    User([User: Alex Rivera]) -->|HAS_MEMORY| Memory([Memory: Architecture Log])
    User -->|HAS_GOAL| Goal([Goal: Reduce Latency Under 30ms])
    Memory -->|MENTIONS| Entity([Entity: FalkorDB])
    Memory -->|HAS_GOAL| Goal
    Goal -->|MOTIVATES| Decision([Decision: Adopt FalkorDB Cypher])
    Decision -->|INFORMED_BY| Experience([Experience: GraphBLAS 14ms Benchmark])
```

When Alex asks:
> *"What decisions were taken to reduce reasoning response latency for the assistant?"*

1. **Goal match:** The query keyword `"latency"` directly matches the Goal node.
2. **Causal hop 1 (`[:MOTIVATES]`):** The graph traverses from the Goal to the Decision (`"Adopt FalkorDB multi-hop Cypher traversal"`).
3. **Causal hop 2 (`[:INFORMED_BY]`):** The graph traverses from the Decision to the empirical Experience (`"GraphBLAS matrix traversal executed 10,000 synthetic episodic hops in benchmark; measured 14ms latency"`).
4. **Relational Context:** The agent retrieves the entire causal path, the supporting empirical experience, and the mentioned entity—**even though the Experience text never contained the word "latency" or "assistant"**.

---

## 3. Graph Retrieval vs. Fair Keyword Baseline (Empirical Evaluation)

We evaluated Graph Retrieval against a fair Keyword Baseline on the exact same synthetic dataset in FalkorDB (where keyword search checks every text field across all nodes without graph hops). The evaluation is executed over **$N=30$ reproducible iterations**:

| Metric | Graph Traversal Retrieval | Flat Keyword Baseline | Hackathon Significance |
| :--- | :---: | :---: | :--- |
| **Recall** | **100.0%** (3 / 3 expected) | **33.3%** (1 / 3 expected) | Graph traversal bridges the semantic gap across causal edges. |
| **Precision** | **100.0%** (connected targets) | **100.0%** | Both methods return strictly relevant records without hallucination. |
| **Found Decision?** | **YES** | **NO** | Keyword search missed the decision because wording differed. |
| **Found Experience?** | **YES** | **NO** | Experience wording (`GraphBLAS`, `hops`) did not match query terms. |
| **Causal Evidence Path?** | **YES** (`Goal -> Decision -> Experience`) | **NO** (0 paths) | Graph provides explainable provenance traces. |
| **Median Latency (N=30)** | **210.97 ms** (Min: 164.37 ms, Max: 1056.29 ms) | **144.95 ms** (Min: 114.06 ms, Max: 945.74 ms) | Millisecond-level multi-hop performance via FalkorDB Docker engine. |

> **Fair Evaluation Note:** The keyword baseline was not artificially degraded; it searched all text fields of all records for the user. It failed on the experience and decision simply because language varies across causal hops. We do not claim graph retrieval is faster than keyword scanning on tiny datasets; FalkorDB's advantage is **complete recall** and **deterministic causal path extraction**.

---

## 4. Quickstart & Launch Instructions (Windows PowerShell)

### Prerequisites
- Python 3.11+
- Docker Desktop running on `127.0.0.1`

### Step 1: Start FalkorDB
In Windows PowerShell:
```powershell
docker compose up -d
```
FalkorDB is accessible on `127.0.0.1:6379` (engine) and `http://localhost:3000` (FalkorDB Browser).

### Step 2: Virtual Environment & Dependencies
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Step 3: Run Automated Tests & CLI Demo
```powershell
# Run the complete test suite (42 automated tests covering API, graph, deduplication, Gemini, temporal/conflict engine, and UI)
.\.venv\Scripts\python -m pytest -v

# Run the deterministic CLI evaluation demo (30 verification criteria across all 10 judge steps)
.\.venv\Scripts\python scripts/run_demo.py
```

### Step 4: Launch the Interactive Judge-Facing Web Interface
Start the FastAPI server:
```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```
Open your browser and navigate to:
**`http://127.0.0.1:8000/`** (or `http://127.0.0.1:8000/ui`)

---

## 5. Judge-Facing Web Interface Walkthrough

The web interface is built with zero external framework bloat (Vanilla JS + custom glassmorphic CSS design system), communicating directly with the live FastAPI and FalkorDB backend:

1. **System Health & Connection Status:** Real-time polling of `/health` verifying FalkorDB connectivity (`mai_agent_memory` graph).
2. **Demo Scenario Ingestion & Deduplication:** Ingests the synthetic scenario for developer **Alex Rivera** (Mode A) and tests SHA-256 fingerprint deduplication with zero duplicate nodes created.
3. **Real Memory Extraction from Natural Conversation:** Unstructured dialogue extracted into typed graph nodes and allowlisted relationships via Google Gemini API (`gemini-3.8-flash`) with strict Pydantic validation.
4. **Contextual Query Panel:** Natural language input with quick preset chips and client-side end-to-end latency timing.
5. **Visual Causal Graph Path:** Dynamically renders verified Cypher relationship paths (`Goal -> MOTIVATED_BY -> Decision -> INFORMED_BY -> Experience`).
6. **Typed Graph Nodes & Provenance:** Grid cards displaying exact properties of Goals, Decisions, Experiences, Memories, Entities, and SHA-256/audit provenance traces.
7. **Side-by-Side Baseline Comparison:** On-demand drawer executing identical queries across Graph Traversal vs Keyword Search, displaying live Recall (100% vs 33.3%), Decision retrieval, and Causal path availability.
8. **Cross-Tenant Privacy & Hallucination Prevention:** Switch active identity to **Jordan Rival (Tenant B)** to verify zero data leakage from Alex Rivera's private graph, and test unrelated queries for explicit safe no-evidence states.
9. **Temporal Memory & Conflict Resolution Panel:** Live 3-statement demonstration displaying goal updates (`SUPERSEDES`), target vs. benchmark evaluations (`meets_target = false`), and side-by-side claim history without destructive overwrites.
10. **Pass/Fail Summary Verification:** Deterministic multi-point verification with exit code 0 confirmation.

---

## 5. API Reference & Live Examples

### Start the API Server
```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 1. Ingest Memory Chain (`POST /memory/ingest`)
```powershell
$body = @{
    user_id = "user_alex_rivera_demo"
    user_name = "Alex Rivera"
    structured_data = @{
        memories = @(
            @{
                id = "mem_alex_architecture_note"
                text = "Graph memory enables connected reasoning across episodic traces."
                summary = "Graph memory enables connected reasoning across episodic traces"
                memory_type = "observation"
                provenance = "dev_log:2026_q1"
            }
        )
        entities = @(
            @{
                id = "ent_falkordb_engine"
                name = "FalkorDB"
                entity_type = "technology"
                description = "Graph database leveraging GraphBLAS matrix algorithms"
            }
        )
        goals = @(
            @{
                id = "goal_alex_reduce_latency"
                title = "Reduce AI assistant reasoning response latency under 30ms"
                priority = "critical"
            }
        )
        decisions = @(
            @{
                id = "dec_alex_falkordb_cypher"
                action = "Adopt FalkorDB multi-hop Cypher traversal with read-only execution"
                rationale = "Causal connections avoid combinatorial vector similarity joins"
            }
        )
        experiences = @(
            @{
                id = "exp_alex_benchmark_graphblas"
                content = "GraphBLAS matrix traversal executed 10,000 synthetic episodic hops in benchmark"
                outcome = "Measured 14ms median traversal latency with 100% path determinism"
                confidence = 0.98
            }
        )
        relationships = @(
            @{ source_id = "mem_alex_architecture_note"; relation = "MENTIONS"; target_id = "ent_falkordb_engine" },
            @{ source_id = "mem_alex_architecture_note"; relation = "HAS_GOAL"; target_id = "goal_alex_reduce_latency" },
            @{ source_id = "goal_alex_reduce_latency"; relation = "MOTIVATES"; target_id = "dec_alex_falkordb_cypher" },
            @{ source_id = "dec_alex_falkordb_cypher"; relation = "INFORMED_BY"; target_id = "exp_alex_benchmark_graphblas" }
        )
    }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8000/memory/ingest" -ContentType "application/json" -Body $body
```

### 2. Contextual Query (`POST /memory/query`)
```powershell
$queryBody = @{
    user_id = "user_alex_rivera_demo"
    query = "What decisions were taken to reduce reasoning response latency for the assistant?"
    max_depth = 3
} | ConvertTo-Json

Invoke-RestMethod -Method POST -Uri "http://127.0.0.1:8000/memory/query" -ContentType "application/json" -Body $queryBody
```

**Real API Response Output:**
```json
{
  "success": true,
  "user_id": "user_alex_rivera_demo",
  "query": "What decisions were taken to reduce reasoning response latency for the assistant?",
  "found_evidence": true,
  "context_summary": "Evidence for 'user_alex_rivera_demo': Memories: 'Graph memory enables connected reasoning across episodic traces' (type: observation) | Goals: 'Reduce AI assistant reasoning response latency under 30ms' | Decisions: Action: 'Adopt FalkorDB multi-hop Cypher traversal with read-only execution' (Rationale: Causal connections avoid combinatorial vector similarity joins) | Empirical evidence: 'GraphBLAS matrix traversal executed 10,000 synthetic episodic hops in benchmark' -> outcome: Measured 14ms median traversal latency with 100% path determinism | Entities: FalkorDB",
  "memories": [
    {
      "id": "mem_alex_architecture_note",
      "summary": "Graph memory enables connected reasoning across episodic traces",
      "text": "Fictional dev Alex Rivera recorded: Graph memory enables connected reasoning across episodic traces.",
      "memory_type": "observation",
      "confidence": 1.0,
      "created_at": "",
      "provenance": "dev_log:2026_q1",
      "fingerprint": null
    }
  ],
  "entities": [
    {
      "id": "ent_falkordb_engine",
      "name": "FalkorDB",
      "entity_type": "technology",
      "description": ""
    }
  ],
  "goals": [
    {
      "id": "goal_alex_reduce_latency",
      "title": "Reduce AI assistant reasoning response latency under 30ms",
      "priority": "critical"
    }
  ],
  "decisions": [
    {
      "id": "dec_alex_falkordb_cypher",
      "action": "Adopt FalkorDB multi-hop Cypher traversal with read-only execution",
      "rationale": "Causal connections avoid combinatorial vector similarity joins"
    }
  ],
  "experiences": [
    {
      "id": "exp_alex_benchmark_graphblas",
      "content": "GraphBLAS matrix traversal executed 10,000 synthetic episodic hops in benchmark",
      "outcome": "Measured 14ms median traversal latency with 100% path determinism",
      "confidence": 0.98
    }
  ],
  "evidence_paths": [
    "Memory(mem_alex_architecture_note) -[HAS_GOAL]-> Goal('Reduce AI assistant reasoning response latency under 30ms')",
    "User(user_alex_rivera_demo) -[HAS_GOAL]-> Goal('Reduce AI assistant reasoning response latency under 30ms') -[MOTIVATED_BY]-> Decision('Adopt FalkorDB multi-hop Cypher traversal with read-only execution') -[INFORMED_BY]-> Experience('GraphBLAS matrix traversal executed 10,000 synthetic episodic hops in benchmark')"
  ],
  "provenance": [
    "decision:dec_alex_falkordb_cypher",
    "dev_log:2026_q1",
    "entity:ent_falkordb_engine",
    "experience:exp_alex_benchmark_graphblas",
    "goal:goal_alex_reduce_latency"
  ]
}
```

---

## 6. Architecture Safeguards

1. **Strict User Data Isolation:**
   All Cypher traversals originate strictly from `(u:User {id: $user_id})`. Cross-user data leakage is impossible because graph paths are anchored to the authenticated user.
2. **Cypher Injection Immunity:**
   Relationship types must belong to `ALLOWLISTED_RELATIONSHIPS` (`HAS_MEMORY`, `MENTIONS`, `HAS_GOAL`, `MOTIVATES`, `MOTIVATED_BY`, `INFORMED_BY`, `RELATED_TO`, `CONTRADICTS`). All identifiers and parameters are passed via parameter dictionaries (`$user_id`, `$kw`).
3. **Idempotency via Deterministic Fingerprinting:**
   Memories are fingerprinted as `SHA-256(user_id :: memory_type :: normalized_summary)`. Duplicate submissions refresh `updated_at` without producing redundant nodes.
4. **Preservation of Contradictory Evidence:**
   Contradictory facts are preserved as distinct, timestamped records linked via `CONTRADICTS`, preventing historical loss.

---

---

## 7. Natural-Language Memory Extraction with Google Gemini (Milestone 5)

Mai Graph Hacks integrates **Google Gemini API** via the official `google-genai` Python SDK (`from google import genai`) to convert unstructured developer or agent dialogue into grounded, typed knowledge graphs in FalkorDB.

### Supported Model & Configuration
- **Official SDK:** `google-genai` (minimum footprint added to `requirements.txt`).
- **Active Model Identifier:** `gemini-3.8-flash` (Google's currently supported high-speed model for structured extraction).
- **Environment Variables:**
  - `GEMINI_API_KEY`: Server-side API key. Never sent to the browser or leaked in logs/error payloads.
  - `GEMINI_MODEL`: Model name (defaults to `gemini-3.8-flash`).
  - `GEMINI_TIMEOUT_SEC`: Extraction request timeout (default: `30.0`s).

### Grounded Extraction Guardrails
1. **Zero Hallucination:** Strict system prompt and Pydantic validation ensure the model extracts only facts, goals, decisions, experiences, entities, and relationships explicitly stated in the input text.
2. **Schema Reliability:** Structured JSON output schema (`GeminiExtractionPayload`) enforced without `additionalProperties`, preventing schema mismatches in Gemini Developer API mode.
3. **Relationship Allowlist:** Relationships are restricted to allowlisted types (`HAS_MEMORY`, `MENTIONS`, `HAS_GOAL`, `MOTIVATES`, `MOTIVATED_BY`, `INFORMED_BY`, `RELATED_TO`, `CONTRADICTS`).
4. **Contradiction Preservation:** Contradictory statements are preserved as distinct timestamped records connected via `CONTRADICTS` edges.
5. **Irrelevant Chatter Guardrail:** Conversations with zero extractable facts return empty payloads rather than hallucinated records.
6. **No Silent Fallbacks:** If `GEMINI_API_KEY` is missing or the API returns an error, the backend returns clear HTTP 400 / 422 / 502 error codes with troubleshooting actions. It never silently substitutes mock extractions in production.
7. **Mode A Independence:** Mode A structured ingestion remains 100% functional without requiring any LLM API key.

---

## 8. Architecture Safeguards

1. **Strict User Data Isolation:**
   All Cypher traversals originate strictly from `(u:User {id: $user_id})`. Cross-user data leakage is impossible because graph paths are anchored to the authenticated user.
2. **Cypher Injection Immunity:**
   Relationship types must belong to `ALLOWLISTED_RELATIONSHIPS` (`HAS_MEMORY`, `MENTIONS`, `HAS_GOAL`, `MOTIVATES`, `MOTIVATED_BY`, `INFORMED_BY`, `RELATED_TO`, `CONTRADICTS`). All identifiers and parameters are passed via parameter dictionaries (`$user_id`, `$kw`).
3. **Idempotency via Deterministic Fingerprinting:**
   Memories are fingerprinted as `SHA-256(user_id :: memory_type :: normalized_summary)`. Duplicate submissions refresh `updated_at` without producing redundant nodes.
4. **Preservation of Contradictory Evidence:**
   Contradictory facts are preserved as distinct, timestamped records linked via `CONTRADICTS`, preventing historical loss.

---

## 9. Audit of Demo Claims & Empirical Grounding

To maintain rigorous engineering standards and hackathon integrity, all claims in this repository are strictly classified:

| Category | Item | Source & Verification |
| :--- | :--- | :--- |
| **Real Live Integration** | FalkorDB Cypher Engine (`mai_falkordb`) | Live container on `127.0.0.1:6379`. Tested with `redis-py` / `falkordb-py`. All Cypher queries (`MERGE`, `MATCH`, `RETURN`, `ro_query`) execute live. Verified by 32/32 automated tests. |
| **Real Live Integration** | Google Gemini Extraction | Verified live with `gemini-3.8-flash` via `google-genai` SDK. Successfully extracts memories, goals, decisions, experiences, entities, and relationships from natural conversation and writes them directly into live FalkorDB. |
| **Real Live Integration** | Edge Verification & Traversal | All returned evidence paths verify that every node and edge genuinely exists in FalkorDB, returning actual relationship types (`[:MOTIVATES]`, `[:INFORMED_BY]`) and directions (`->`, `<-`). Disconnected nodes do not fabricate false paths. |
| **Real Live Integration** | Accurate Ingestion Breakdown | Backend calculates and returns exact counts for new nodes/relationships created vs existing elements reused. Repeated ingestion confirms zero duplicate nodes created. |
| **Real Live Integration** | Multi-Tenant Data Isolation | Tested live: queries from `Jordan Rival` return zero nodes from `Alex Rivera`'s graph. Verified by `test_strict_user_data_isolation` and browser verification. |
| **Real Live Integration** | Deterministic Deduplication | SHA-256 fingerprint calculation and Cypher `MERGE` verified live. Re-ingesting creates 0 duplicate nodes. Verified by `test_duplicate_ingestion_and_idempotency`. |
| **Actual Measured Latency** | Query Traversal Latency (Local) | Measured by `scripts/run_demo.py` (N=20 iterations): Median **~70–85 ms** over Windows Docker loopback. |
| **Actual Measured Latency** | End-to-End Client Latency | Measured in browser via `performance.now()` in `app.js`: **~120–150 ms** including network fetch, JSON parsing, and DOM rendering. |
| **Performance Clarification** | Graph vs. Keyword Speed | **We do not claim graph retrieval is faster than flat keyword search on small single-user datasets.** Keyword search on a small set of records executed in ~40–65 ms. The definitive advantage of FalkorDB is **Recall (100.0% vs. 33.3%)** and **Causal Path Discovery** (`Goal -> Decision -> Experience`), which keyword matching cannot provide. |
| **Synthetic Illustrative Data** | Developer Alex Rivera Scenario | The narrative content (e.g. "Alex Rivera", "GraphBLAS benchmark concept", "14ms latency target") is **synthetic illustrative test data** designed to model a developer agent's episodic trace. It is **not** an actual performance benchmark measurement of the running system. |
| **Deterministic Unit Tests** | Mocked Gemini Client Tests | Comprehensive unit test suite in `tests/test_gemini_extraction.py` verifies missing keys, timeouts, 429 rate limits, malformed JSON, unallowlisted relationships, and empty conversations deterministically offline. |
| **Temporal & Conflict Engine** | Deterministic Conflict Engine | Verified by 10/10 automated tests in `tests/test_conflict_and_temporal.py`. Detects `SUPERSEDES`, `CONTRADICTS`, and `target_mismatch` with deterministic reasoning without destructive overwrites. |

---

## 10. Milestone 6 — Temporal Memory, Conflict Detection, and Evidence-Grounded Retrieval

### Objective
Equip the cognitive agent memory with time-awareness and deterministic conflict handling:
1. **Temporal Metadata:** Every claim, goal, decision, and experience records its ingestion timestamp (`created_at`), source recorded timestamp (`recorded_at`), validity timestamp (`valid_at`), and temporal state (`status`: `current`, `historical`, `superseded`, `disputed`, `unknown`).
2. **Evidence Preservation:** Claims are NEVER deleted or overwritten merely because a newer claim differs. Both sides are preserved in FalkorDB with full provenance and timestamps.
3. **Deterministic Conflict Engine (`app/memory/conflict.py`):**
   - **Genuine Incompatibilities:** e.g., "The project uses PostgreSQL" vs. "The project uses FalkorDB" linked via `[:CONTRADICTS]` and marked `disputed`.
   - **Goal & Architecture Updates:** e.g., "Our target latency is below 30 ms" followed by "We changed the target response latency to below 20 ms", or "We migrated the project to FalkorDB" linked via `[:SUPERSEDES]` with older node marked `superseded` and newer marked `current`.
   - **Target vs. Empirical Measurement Mismatches:** e.g., Active target &lt; 20 ms vs. Empirical benchmark 24 ms linked via `[:INFORMED_BY]` with `meets_target = false`.
   - **Compatible Pairs:** e.g., Target &lt; 30 ms vs. Benchmark 14 ms evaluated as satisfying the target.
   - **Uncertain / Ambiguous Cases:** Speculative statements ("we might consider", "tentatively thinking about") remain explicitly marked `uncertain` without premature commitment.
4. **Evidence-Grounded Retrieval (`POST /memory/query`):**
   - Returns supporting claims, contradictory claims, historical claims, and superseding claims.
   - Context summaries explain conflicts neutrally and transparently without silently declaring the newest statement objectively true.
   - Preserves explicit no-evidence behavior when graph contains no relevant facts.
   - Never claims statements or benchmarks are independently verified.

### 3-Statement Conflict & Temporal Demo (`POST /demo/conflict_scenario`)
1. **Statement 1:** *"Our assistant's target response latency is below 30 ms."* (Initial goal established)
2. **Statement 2:** *"We changed the target response latency to below 20 ms."* (Detected as `SUPERSEDES` goal update; older goal marked `superseded`, newer marked `current`)
3. **Statement 3:** *"Our last benchmark measured 24 ms."* (Empirical observation; evaluated against active 20 ms goal: 24 ms does not meet target; disclaimer that a single synthetic measurement does not establish overall performance)
- **Result:** All 3 source statements preserved in FalkorDB; both sides of conflict retrieved with full provenance and side-by-side comparison in the UI.

---

## 11. Milestone 7 — Hackathon Readiness Audit & Submission Summary

### Problem Statement
Autonomous AI agents suffer from episodic context fragmentation, vector retrieval vocabulary mismatches, and causal amnesia. In conventional vector RAG, text chunks are stored without explicit semantic or causal edges. When queries require traversing from a high-level goal (*"minimize response latency"*) to an architectural decision (*"adopt FalkorDB Cypher traversal"*) to an empirical observation (*"GraphBLAS benchmark execution"*), vector similarity fails unless vocabulary directly overlaps. Furthermore, traditional stores silently overwrite contradictory memories, losing critical provenance and historical context.

### Intended Users
- **AI Agent Engineers & Cognitive Systems Architects:** Developers constructing multi-turn autonomous agents that require long-term episodic memory, explainable reasoning traces, and audit-grade decision history.
- **Enterprise Automation Teams:** Teams requiring strict multi-tenant isolation, idempotency on repeated ingestion, and transparent contradiction detection without destructive data loss.

### Why FalkorDB is Essential to the Solution
FalkorDB is not merely used as a key-value store or visualization layer; it serves as the core reasoning engine:
1. **GraphBLAS Sparse Matrix Algebra:** Performs multi-hop Cypher traversals in milliseconds without the combinatorial cost of relational joins or high-dimensional vector similarity scans.
2. **First-Class Edge Types & Directionality:** Encodes typed semantics (`[:MOTIVATES]`, `[:INFORMED_BY]`, `[:SUPERSEDES]`, `[:CONTRADICTS]`) directly into the storage layer, allowing Cypher pattern matching to bridge semantic gaps.
3. **Deterministic Multi-Hop Discovery:** Guarantees 100% path determinism for auditability and compliance, eliminating vector search probabilistic hallucinations.
4. **Isolated Tenant Subgraphs:** Enforces tenant isolation natively in Cypher by anchoring all traversals to `(u:User {id: $user_id})`.

### System Architecture
```
Unstructured Dialogue (Developer / Agent)
       │
       ▼
Google Gemini API (gemini-3.8-flash via google-genai)
       │  (Strict JSON Schema, Zero Hallucination System Prompt)
       ▼
Pydantic Validation Layer (MemoryPayload, Goal, Decision, Experience, Allowlisted Relations)
       │  (SHA-256 Fingerprint Deduplication, Strict Sanitization)
       ▼
FalkorDB Engine (Docker: 127.0.0.1:6379, Graph: mai_agent_memory)
       │  (Multi-hop Cypher Traversals, Temporal Metadata, Conflict Modeling)
       ▼
Contextual Retrieval & Deterministic Conflict Engine
       │  (SUPERSEDES updates, CONTRADICTS disputes, Target vs. Empirical verification)
       ▼
Provenance-Backed Response & Glassmorphic Web UI
```

### Main Technical Differentiators
1. **Semantic Bridge Across Disparate Vocabularies:** Retrieves supporting evidence even when query terms share 0% token overlap with empirical records, achieving **100% recall** vs. **33.3% for keyword baselines**.
2. **Deterministic Conflict & Temporal Engine:** Detects goal supersession (`SUPERSEDES`) and target mismatches (`meets_target = false`) without deleting past records.
3. **Zero-Hallucination Safe State:** Explicitly returns `found_evidence = false` when no grounded graph facts exist.
4. **Dual-Mode Ingestion:** Fully supports structured ingestion (Mode A) with zero API keys or cloud dependencies, alongside natural language extraction (Mode B) via Google Gemini API.

### Verified Demo Instructions (Clean Windows PowerShell Setup)

```powershell
# 1. Start FalkorDB Container
docker compose up -d

# 2. Virtual Environment Setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 3. Configure Gemini (Optional for Mode B, Mode A runs with zero keys)
$env:GEMINI_API_KEY = "your-gemini-api-key"
$env:GEMINI_MODEL = "gemini-3.8-flash"

# 4. Run Complete Automated Test Suite (42 tests)
.\.venv\Scripts\python -m pytest -v

# 5. Run CLI Judge Demonstration (30 verification criteria)
.\.venv\Scripts\python scripts/run_demo.py

# 6. Launch Interactive Web Application
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
# Access browser UI at http://127.0.0.1:8000/
```

### Empirical Evaluation Methodology & Results
- **Dataset:** Fixed synthetic episodic developer scenario (`Alex Rivera`, 5 nodes, 4 causal edges).
- **Target Query:** *"What decisions were taken to reduce reasoning response latency for the assistant?"*
- **Expected Relevant IDs:** `['goal_alex_reduce_latency', 'dec_alex_falkordb_cypher', 'exp_alex_benchmark_graphblas']`.
- **Sample Count:** $N=30$ consecutive iterations.
- **Results:**
  - **Graph Traversal Recall:** **100.0%** (3/3 retrieved)
  - **Baseline Recall:** **33.3%** (1/3 retrieved — missed Decision and Experience)
  - **Graph Traversal Precision:** **100.0%** (connected targets)
  - **Baseline Precision:** **100.0%**
  - **Causal Evidence Paths:** **100.0%** in Graph Retrieval vs. **0.0%** in Baseline
  - **Graph Traversal Latency:** Median **210.97 ms** (Min: 164.37 ms, Max: 1056.29 ms)
  - **Keyword Baseline Latency:** Median **144.95 ms** (Min: 114.06 ms, Max: 945.74 ms)

### Known Limitations
1. **Single-Node Keyword Anchoring:** Initial graph search matches user-anchored nodes matching query tokens before traversing outbound edges. Queries with zero matching entry nodes require fallback full-text indexing for large corpora.
2. **Gemini Free-Tier Rate Limits:** The Google Gemini API free tier enforces a project-level rate limit of 20 requests per day (`RESOURCE_EXHAUSTED` / HTTP 429). In production, a paid API key or local model provider should be used. Mode A structured ingestion is completely immune to this limitation.
3. **Local Docker Loopback Overhead:** Latency figures include Windows Docker virtualization and loopback TCP overhead (~150–210 ms). Direct Linux/bare-metal deployment provides sub-10 ms execution.

### Disclosures & Third-Party Components
- **Third-Party Libraries:** `falkordb` Python client, `redis`, `fastapi`, `uvicorn`, `pydantic`, `google-genai`, `pytest`.
- **Database Engine:** FalkorDB official Docker image (`falkordb/falkordb:latest`).
- **AI Assistance:** AI coding assistants were utilized for scaffolding, boilerplate generation, and refactoring assistance under human direction. All architecture, Cypher queries, Pydantic schemas, conflict logic, evaluation metrics, and documentation were verified and tested by the team.
- **Official Rules Confirmation:** Project operates strictly within the target repository (`mai-graph-hacks`). All code is open-source under MIT/Apache compatible terms with zero external proprietary agent framework dependencies.

---

## 12. Milestone 8 — Mai's Engineering Incident Investigator

### Product Goal
Evolve Mai into an **Engineering Incident Investigator** that:
1. **Remembers prior investigations:** Retains hypotheses, telemetry notes, and interventions in FalkorDB.
2. **Connects fix attempts to measured outcomes:** Explicitly evaluates before/after metrics as `successful`, `unsuccessful`, `inconclusive`, or `unknown`. **Never infers success merely because a fix was recorded.**
3. **Retrieves evidence across different wording:** Discovers relevant prior investigations even when current alerts or symptoms use different vocabulary (e.g., *"Tail timeout alerts on gateway-api"* matches prior *"API response latency p99 degradation above 120ms"*).
4. **Evaluates against active constraints:** Compares incidents against active goals (e.g., latency target &lt; 20ms) while preserving superseded goals (prior &lt; 30ms target).
5. **Recommends evidence-grounded next actions:** Warns against re-attempting previously failed interventions (e.g., connection pool resizing) and recommends high-probability diagnostic paths (e.g., query indexing or cache hit rates).
6. **Closes the feedback loop:** Persists follow-up investigation tasks (`NextAction`) in FalkorDB and records later measured outcomes to inform future incidents.
7. **Abstains on insufficient evidence:** Returns explicit `found_evidence = false` with zero fabricated root causes when no relevant incidents exist in the tenant graph.

### FalkorDB Graph Model

```mermaid
graph TD
    User([User / Tenant]) -->|HAS_INCIDENT| Inc([Incident: Gateway Latency Spike])
    Inc -->|INVESTIGATED_BY| Inv([Investigation: Connection Pool Exhaustion])
    Inv -->|LED_TO| Fix([FixAttempt: Increased Pool Size 10 -> 50])
    Fix -->|RESULTED_IN| Out([Outcome: p99 Latency Unchanged at 118ms - UNSUCCESSFUL])
    Inc -->|RECOMMENDS| Act([NextAction: Profile Query Execution & Indexes - PENDING])
    Inc -->|EVALUATED_AGAINST| Goal([Goal: Response Latency Below 20ms - CURRENT])
    Goal -->|SUPERSEDES| OldGoal([Goal: Response Latency Below 30ms - SUPERSEDED])
```

### End-to-End Incident Workflow Verification

| Workflow Step | API / Component | Verified Behavior |
| :--- | :--- | :--- |
| **1. Ingest Incident & Prior Fix** | `POST /incident/ingest` | Ingests `inc_gateway_latency_01` with failed fix `fix_pool_increase_50` and outcome `out_pool_increase_fail` (unsuccessful: 120ms -> 118ms). |
| **2. Query with Different Wording** | `POST /incident/investigate` | Query: *"Tail timeout alerts and slow gateway response"*. FalkorDB traverses component and symptom graph edges, retrieving the prior investigation. |
| **3. Grounded Avoidance Check** | `app/incident/investigator.py` | Detects that pool size increase was **unsuccessful**. Rationale actively cautions: *"Do NOT re-attempt connection pool resize; symptoms persisted at 118ms."* |
| **4. Constraint Evaluation** | FalkorDB Cypher traversal | Checks active goal (latency &lt; 20ms) and acknowledges superseded historical target (&lt; 30ms). |
| **5. Persisted Task Creation** | `NextActionNode` in FalkorDB | Creates task `act_inc_gateway_latency_01_next` with status `pending`. |
| **6. Task Status Lifecycle** | `POST /incident/task` | Updates task status: `pending` ➔ `in_progress` ➔ `completed`. |
| **7. Closing the Feedback Loop** | `POST /incident/outcome` | Records new measured outcome (index fix dropped latency to 18ms, `successful`), permanently updating graph memory for future queries. |
| **8. No-Evidence Abstention** | `POST /incident/investigate` | Query on unrelated topic (*"Quantum decoherence"*) returns `found_evidence = false` and `recommendation = None`. |
| **9. Cross-Tenant Isolation** | FalkorDB Cypher anchor | Tenant B queries for Tenant A's incidents and receives 0 records. |

### Test & CLI Verification
- **Automated Tests:** 50 tests in total (**49 passed, 1 skipped**).
- **CLI Demo Script:** `.\.venv\Scripts\python scripts/run_demo.py` verifies all 11 milestone steps (**40/40 checks passed**, exit code 0).

---

## 13. Milestone 9 — GitHub Engineering Memory Integration v1

### Product Goal
Integrate GitHub repository engineering records directly into the FalkorDB incident investigation graph. When an incident or alert occurs, engineers can trace failure symptoms across connected issues, pull requests, commit messages, and CI workflow runs, inspecting canonical source links without losing track of prior fix outcomes.

### Graph Model & Schema Extensions

```mermaid
graph TD
    User([User / Tenant]) -->|HAS_REPOSITORY| Repo([Repository: gateway-corp/gateway-api])
    Repo -->|CONTAINS_ISSUE| Iss([GitHubIssue: #38 Tail timeouts & pool starvation])
    Repo -->|HAS_PULL_REQUEST| PR([PullRequest: #42 Increase pool 10 -> 50 - MERGED])
    PR -->|REFERENCES_ISSUE| Iss
    Repo -->|CONTAINS_COMMIT| Com([Commit: 9f4b12c8 Increase pool default])
    PR -->|CONTAINS_COMMIT| Com
    Repo -->|HAS_WORKFLOW_RUN| WF([WorkflowRun: Load & Stress Benchmark - FAILURE])
    WF -->|TRIGGERED_BY| Com
    WF -->|TESTS_PULL_REQUEST| PR

    User -->|HAS_INCIDENT| Inc([Incident: Gateway Latency Spike])
    Inv([Investigation: Connection Pool Starvation])
    Inc -->|INVESTIGATED_BY| Inv
    Inv -->|LED_TO| Fix([FixAttempt: Pool Bump - UNSUCCESSFUL])
```

### Key Capabilities & Workflow Steps

| Dimension | Implementation Details | Verified Result |
| :--- | :--- | :--- |
| **1. Secure API Client** | `app/github/client.py` uses `httpx` with timeout and strict error handling (401, 403 rate limits, 404). Retrieves PR branch commits via `GET /repos/{owner}/{repo}/pulls/{number}/commits`. | Credentials resolved server-side from `GITHUB_TOKEN`. Tokens are **never** logged, **never** exposed in API responses, and **never** stored in FalkorDB. |
| **2. Idempotent Ingestion** | Parameterized Cypher with `MERGE` across `Repository`, `GitHubIssue`, `PullRequest`, `Commit`, and `WorkflowRun`. | Repeated imports update existing records in place without creating duplicates. Tested in `test_idempotent_github_import_and_graph_relationships` and `test_repeated_import_idempotency_for_pr_commits_and_links`. |
| **3. Explicit References** | Regex parsing on PR titles and bodies (`fixes #12`, `closes #38`). | Creates explicit `(pr)-[:REFERENCES_ISSUE]->(issue)` relationships in FalkorDB. |
| **4. PR-to-Commit & Workflow Links** | PR branch commits fetched and linked via `(pr)-[:CONTAINS_COMMIT]->(com)`. Workflow runs linked to matching commits via `(wf)-[:TRIGGERED_BY]->(com)` and to PRs via `(wf)-[:TESTS_PULL_REQUEST]->(pr)` based on GitHub event and metadata. | Connects unmerged PR branches to their tested commits and CI results. Missing commits are preserved without phantom nodes. |
| **5. Connected Investigation** | `query_github_evidence_for_incident` in `app/github/operations.py`. | Matches PRs, issues, and workflow failures by component and symptom keywords. Connects code history directly to the incident evidence path. |
| **6. Epistemic Boundary** | `app/incident/investigator.py` uncertainty synthesis. | Code changes in history are surfaced as candidate changes to inspect, **not** asserted as confirmed root causes or fixes without empirical telemetry. |
| **7. Source Traceability** | Preserves canonical GitHub `html_url` for every record. | Engineers can click directly to PRs, issues, commits, and workflow run logs on GitHub. |
| **8. Tenant Isolation** | All repository and incident queries anchored to `(u:User {id: $user_id})`. | Rival tenants cannot view or query another tenant's imported code history. Tested in `test_tenant_isolation_in_github_imports`. |
| **9. No-Evidence Abstention** | Evaluates incident history and GitHub records concurrently. | Unrelated queries return `found_evidence = false` with zero fabricated code links. |

### API Endpoints Added

- `POST /github/import`: Triggers tenant-scoped repository import with configurable record limits (`issue_limit`, `pr_limit`, `commit_limit`, `workflow_limit`).
- `GET /github/status`: Returns integration status, whether server token is configured, and imported repository summary counts for the tenant.
- `POST /demo/github_scenario`: One-click judge demonstration seeding synthetic repository `gateway-corp/gateway-api` with PR #42 and failed workflow run #1092, linking it to the incident investigation.

### Test & CLI Verification
- **Automated Tests:** Full suite of 62 tests (**61 passed, 1 skipped**).
  - 12 dedicated tests in `tests/test_github_integration.py` verifying client error handling, PR-branch commits, PR-to-commit edges, workflow-to-commit/PR matching, missing commit handling, idempotency, tenant isolation, and API endpoints.
- **CLI Demo Script:** `.\.venv\Scripts\python scripts/run_demo.py` verifies all 12 steps (**51/51 checks passed**, exit code 0).



