# Clean Temporal Facts and Entity-First Retrieval Flow — Binding Design

**Status:** Accepted; binding technical design  
**Date:** 2026-09-21  
**Authority:** D127 (Engine), D73 (Cloud Offering)  
**Supersedes:** `[world time: ...]` formatting in `workers/p1.py` and claims-first retrieval guidance in `benchmarks/locomo/protocol.py`  
**Related Documents:** `plan/analysis/clean_temporal_facts_and_retrieval_flow_analysis.md`, `plan/designs/open_query_space_design.md`, `plan/designs/e2_e3_claims_relations_design.md`

---

## 1. Principles (Binding)

1. **Pure Content Invariant:**
   * Text columns in PostgreSQL (`observations.statement`, `observations.obs_label`, `relations.fact_label`) hold only the natural-language statement or predicate assertion without display brackets or suffixes.
   * `obs_label` is retained in PostgreSQL (mirroring `statement`) to preserve pipeline label-versioning machinery and avoid destructive database schema migrations.
   * Display-oriented metadata brackets (such as `[world time: ...]`) MUST NOT be written to PostgreSQL text columns or passed to vector embedding models (`P1FactRow`).
2. **Structured Temporal Fields:**
   * Temporal attributes (`valid_from`, `valid_until`, `valid_precision`) are stored in dedicated, typed columns and transmitted in typed structured objects (`Validity`, `FactResult`).
   * Field names across the API, Python SDK, and CLI strictly mirror database column names: `valid_from`, `valid_until`, `valid_precision`.
   * On source claims (`EvidenceResult`), `asserted_at` carries the source conversation/document timestamp. Facts (`FactResult.validity`) carry `valid_from` and `valid_until` (event validity); `ingested_at` is omitted from agent prompt serialization to prevent confusion between database transaction time and speech time.
3. **Retrieval Hierarchy (Entity $\rightarrow$ Fact $\rightarrow$ Source):**
   * Answering agents prioritize structured entity resolution (`resolve_entity`) and the adjudicated fact layer (`facts_context`) over raw transcript chunks.
   * Entity-anchored `facts_context` queries return the full active observation set for the target entity up to the configured limit, preventing top-K truncation across multi-session dialogues.
   * Raw testimony / source chunks (`claims_and_sources_context`) are accessed as a fallback only when the fact layer does not satisfy the inquiry or when verbatim quotes and dialogue tone are explicitly requested.
4. **Transparent Date Semantics:**
   * Ingestion resolves relative dates into `valid_from`/`valid_until` during claim extraction and normalization.
   * Answering agents are provided explicit, unambiguous definitions:
     * `valid_from` / `valid_until` denote real-world event validity ("When did X happen?").
     * `asserted_at` (on claims/evidence only) denotes conversation time, used by the agent as a fallback only when residual relative phrases (e.g. *"last Friday"*) remain in raw claim text.
     * `valid_precision` matches `ClaimValidPrecision`: `instant`, `day`, `month`, `quarter`, `year`, `open` (ongoing/open-ended state), and `unknown`.
5. **Simplicity Over Indirection:**
   * No fallback shims, dual-format parsers, or legacy bracket-stripping logic. If an observation has no recorded event date (`valid_precision="unknown"`), the system emits the clean prose with no temporal tag.

---

## 2. Ingestion & Embedding Specification

### 2.1 Worker P1 Fact Labeling (`rememberstack.workers.p1`)
In `P1Worker.run()`, the construction of relation and observation labels is modified:

1. **Relations:**
   ```python
   # Clean deterministic label:
   label = deterministic_fact_label(
       subject=relation.subject_name,
       predicate=relation.predicate,
       object_name=relation.object_name,
   )
   self._facts.record_fact_label(
       relation_id=relation.relation_id,
       label=label,
       label_version=label_generation,
       window=window,
   )
   ```
2. **Observations (`LabelFactsHandler`):**
   ```python
   # Clean observation label:
   label = observation.obs_label.strip()
   self._facts.record_observation_label(
       observation_id=observation.observation_id,
       statement=label,
       label=label,
       window=window,
   )
   ```
3. **Vector Embeddings (`P1FactRow`):**
   * `P1FactRow` embeds `label`, which now contains the pure semantic text without `[world time: ...]`.
   * Undated observations no longer share the `[world time: world date unknown]` suffix, restoring vector semantic purity.

### 2.2 Component Version Bump
* In `rememberstack.workers.p1`, bump `FACT_LABEL_VERSION`:
  ```python
  FACT_LABEL_VERSION: Final = "p1-fact-label-2026.09b:clean-temporal"
  ```
  The composed work-ledger and benchmark pin in `EXPECTED_INGEST_COMPONENT_VERSIONS["label_relation"]` combines the label generator with the embedder model:
  ```python
  "label_relation": "p1-fact-label-2026.09b:clean-temporal+qwen/qwen3-embedding-8b"
  ```
  This triggers clean re-labeling and re-embedding for updated stores while preserving deterministic pipeline fingerprints.

---

## 3. Data-Plane & Surface Contract

### 3.1 Fact Model & Envelope (`FactResult`)
`FactResult` in `src/rememberstack/model/envelope.py` remains typed:
```python
class FactResult(BaseModel):
    fact_id: UUID
    kind: str  # relation | observation
    label: str  # Clean prose statement without brackets
    evidence_count: int
    validity: Validity
    temporal_match: TemporalMatch
    contradiction_group: UUID | None = None
    contradiction: Contradiction | None = None
    support: FactSupport = FactSupport.CURRENT
```
Where `Validity` carries the typed temporal fields:
```python
class Validity(BaseModel):
    valid_from: UTCDateTime | None = None
    valid_until: UTCDateTime | None = None
    valid_precision: ClaimValidPrecision = ClaimValidPrecision.UNKNOWN
    ingested_at: UTCDateTime
    invalidated_at: UTCDateTime | None = None
```
Database column naming (`valid_from`, `valid_until`, `valid_precision`) is strictly preserved across the API, Python SDK, CLI, and TypeScript frontend models.

### 3.2 Canonical Prompt Serialization Format
When fact results are serialized into text context for LLM agents, dated facts receive a single canonical, deterministic suffix based on `valid_precision`:
* `instant`: `(valid: YYYY-MM-DDTHH:MM:SSZ)`
* `day`: `(valid: YYYY-MM-DD)`
* `month`: `(valid: YYYY-MM)`
* `quarter`: `(valid: YYYY QN)`
* `year`: `(valid: YYYY)`
* `open`: `(valid: since YYYY-MM-DD, ongoing)`
* `unknown`: *No suffix appended* (clean statement only).

> [!NOTE]
> Internal operator fact-sheet rendering (`knowledge_fact_sheet.py` markdown tables for human diagnostics) remains an internal operator inspection format and is an explicit non-goal for the LLM agent retrieval context.

---

## 4. Answering Agent Prompt & Retrieval Discipline

In `benchmarks/locomo/protocol.py`, `ANSWER_AGENT_PROMPT_TEMPLATE` is updated to bind the cognitive retrieval hierarchy and temporal rules:

### 4.1 Retrieval Protocol
1. **Step 1 — Entity Grounding:**
   * If the question asks about a specific named entity (person, place, pet, project), call `resolve_entity` to resolve the candidate.
2. **Step 2 — Fact Layer First:**
   * Query the adjudicated fact layer (`facts_context` with entity anchor or semantic query) for what the system holds true.
   * For person traits, allergies, hobbies, relationships, and history, query facts under `time.mode="history"`.
   * If facts directly satisfy the question, formulate the answer and terminate.
3. **Step 3 — Sources / Claims Fallback:**
   * Call `claims_and_sources_context` only when:
     * The fact layer does not contain the answer.
     * The inquiry explicitly demands verbatim quotes, tone analysis, or raw dialogue excerpts.

### 4.2 Date Interpretation Instructions
The answering agent prompt is explicitly taught:
* **`valid_from` / `valid_until` (Event Date):** The real-world date or date range when the event occurred or was valid. Answer "When did X happen?" using these bounds.
* **`open` Precision:** Represents an ongoing state that started at `valid_from` with no recorded end date.
* **`asserted_at` (Claims only):** Present on source claims (`EvidenceResult`). If raw claim text still contains an unresolved relative phrase (*"last Friday"*, *"yesterday"*), calculate the calendar date relative to `asserted_at`.
* **Absence of Date:** If no date is recorded, do not guess or state "unknown world time"; state the fact directly.
