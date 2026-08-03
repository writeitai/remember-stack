# Agent retrieval surface — binding design

*2026-08-02, revision 2 (addresses the Grok-4.5 and Codex REQUEST-CHANGES
reviews of revision 1; both review transcripts are linked from the PR).
Binding once accepted. Extends `retrieval_design.md` (which owns
nomination/fusion/hydration internals) with the complete agent-facing tool
surface. Rationale and evidence: `plan/analysis/
agent_retrieval_surface_analysis.md`. Goal: an AI agent using only the
public recipe catalog can reach everything the system knows, within the
call budget minimal-effort agents actually spend.*

## 1. Principles (binding)

1. **Complete coverage:** every data spine (claims, chunks, documents,
   media segments, entities, mentions, relations, observations,
   valid-time, graph incl. citations, changes, pages) is reachable through
   at least one question-shaped tool, or carries a recorded deferral in
   §6. The coverage matrix in the analysis must contain no silent gap.
2. **One-call ergonomics for common intents.** Measured agent behavior is
   ~2 calls/question; each common intent (recall, current-state, entity
   browse, time window, connection, read-around-a-hit) gets a single-call
   tool. Composition happens inside compound engine operations, not inside
   the agent's plan and not by bending the recipe-chain linter.
3. **Typed grains never blur.** Evidence (claims), source passages
   (chunks), current facts (relations/observations), documents, and graph
   structure travel in their own envelope fields; multi-grain answers use
   the composite-envelope `parts[]` contract (D49) rather than flattening.
4. **D48 everywhere.** All content-bearing output passes
   nominate-then-confirm; projections never leak unconfirmed text.
5. **Derived facts ship with their evidence — both stances.** Tools
   *introduced by this design* that return observations/relations include
   their evidence claims: up to `evidence_per_fact` (default 3, max 5)
   supporting claims chosen source-diverse by existing ranking, plus up to
   the same cap of `stance='contradicts'` claims when they exist, with the
   exact totals stated so the agent knows what was elided. Evidence
   hydration filters current testimony and deleted lineages (this
   tightens the existing relation-evidence SQL as part of Batch C).
   Existing lookup tools (`relation_current`, `observation_current`,
   graph tools) keep their current shapes; `explain` remains the per-fact
   deep-dive. Rationale: derivation stages have observed error modes; an
   unbacked derived fact is unverifiable by the consumer. D54 remains
   binding: facts whose support was withdrawn stay visible with their
   support flag — nothing in this design silently drops them.
6. **Tool descriptions are written in grain-honest question language.**
   Evidence tools say "what sources asserted/reported…", fact tools say
   "what currently holds…" (D41 routing discipline); each description
   states *when* to reach for the tool. Descriptions never mention any
   benchmark.
7. **Honest negatives, bounds, and continuations.** Every list-returning
   tool: bounded k (JSON-schema bounds per §3), typed `negative` on empty
   (including a disambiguation negative for unresolved/ambiguous entity
   parameters), `truncation` populated whenever a bound elided results,
   `dropped_by_hydration` counted, worst-case envelope size stated in the
   recipe's design note.
8. **Benchmark honesty.** No dataset-specific logic in the surface. The
   benchmark consumes the same catalog as every other agent.
9. **Entity-parameter resolution policy (uniform).** Tools taking
   `entity: str` resolve via the current resolve ladder. Today that
   ladder implements exact-normalized alias matching only (T0); this
   design inherits that limit and records the T1–T3 upgrade as a separate
   design (§6). If resolution is ambiguous (multiple candidates above
   floor) the tool returns the ranked candidates and a disambiguation
   negative instead of guessing; if resolution fails, a typed negative.
   Deterministic: same store, same string → same outcome.

## 2. Naming alignment with the existing corpus

`retrieval_design.md` already names aspirational recipes. This design
binds the mapping instead of renaming silently:

| Corpus name | This design | Disposition |
| --- | --- | --- |
| `claims_as_of` | `claims_as_of` (window form) | **Adopted — the time-window tool below uses the taught name.** |
| `relation_near_entity` | `claims_about` + `current_context` | Superseded by these two (recorded here). |
| `relation_hybrid_rrf` | `current_context` | Superseded; semantic facts nomination lands inside `current_context`. |
| `brief`, `contradictions` | deferred | §6, each with trigger. |

## 3. The target catalog (delta over `0ef5454`)

Parameter schemas use the established JSON-schema style
(`type`/`required`/`default`/bounds; `timestamp` and `uuid` types as in
existing recipes). All new recipes carry full D50 descriptor fields
(name, description, parameters, chain-or-op, output_grain, answer_intent,
version).

### 3.1 Entity and time spines (Batch B)

**`documents_about`** — `entity: string (required)`, `k: integer
[1..50] default 20`. Returns `sources[]` (SourceRecord extended with
optional `mention_count`, `first_mentioned_at`, `last_mentioned_at` —
optional fields keep envelope compatibility), ordered by mention count
then recency. Backed by resolution_decisions + mentions with Postgres
authoritative; the join path and its indexes are bound in Batch B's
implementation note and measured before merge (hub entities are the
worst case; the k-bound and mention-count ordering keep the scan
bounded). Description intent: "which ingested documents mention X."
Boundary stated in the description: documents with no *resolved* mention
of X are not listed (they remain reachable via text search).

**`claims_about`** — `entity: string (required)`, `query: string
(optional)`, `k: integer [1..50] default 20`. Evidence grain; claims
whose chunks carry a resolved mention of the entity (resolution →
mentions → chunk claims join), hydrated through the existing confirmation
path, valid-time included. With `query`: the entity-filtered candidate
set is ranked by semantic similarity (embed once, rank the bounded set —
never global-nominate-then-filter, which caps recall). Description:
"what sources asserted X said/did/was — verbatim testimony about a
person or thing."

**`claims_as_of`** — `from: timestamp (required)`, `to: timestamp
(required, ≥ from)`, `query: string (optional)`, `k: integer [1..50]
default 20`. Evidence grain; claims whose validity interval intersects
[from, to]. Interval semantics: `valid_kind='open'` intervals
participate (null `valid_until` = still open, not unknown); only claims
with `claim_valid_precision='unknown'` (no stamp) are excluded, and the
envelope surfaces `excluded_unstamped` as an exact count so agents know
what the window could not see. Storage decision (binding): a partial
Postgres index on `(deployment_id, claim_valid_from, claim_valid_until)
WHERE claim_valid_kind IS NOT NULL` — stamped claims are a minority
(~33% measured), the partial index is small, and PG-side interval
filtering followed by bounded semantic ranking avoids the recall ceiling
of global-semantic-then-filter. P1 valid-time scalars remain a recorded
alternative if the PG path measures poorly (adoption trigger: p95 window
query > 250 ms on the benchmark-scale store). Description: "what sources
asserted happened within a time window."

**`chunk_neighbors`** — `chunk_id: uuid (required)`, `radius: integer
[1..2] default 1`. Returns `chunks[]`: the D48-confirmed neighbors in
the current representation's section order, document edges surfaced via
`truncation`-style explicitness rather than silence. Description: "read
the source passage surrounding a hit."

### 3.2 Current-state context (Batch C)

**`current_context`** — `query: string (required)`, `k: integer [1..30]
default 15`, `evidence_per_fact: integer [0..5] default 3`. Implemented
as a **compound engine operation** (like the S5 hydrate chain — one
`query_engine` method, one recipe step; the chain linter is not bent):
semantic nomination over the P1 facts channel (`search_facts`,
observations + relations — exposed at last) → Postgres confirmation
(current, non-deleted, supersession status included) → per-fact evidence
hydration per principle 5 (supporting + contradicting, capped,
source-diverse, exact totals). Output: **composite envelope** — one part
carrying `facts[]` (current-fact grain, support flags per D54), one part
carrying the backing `evidence[]` (evidence grain). Lexical nomination
over fact text ships only if fact text is already indexed in P1;
otherwise semantic-only first, lexical recorded in §6. Description:
"what currently holds about the things the question mentions — with the
testimony behind it."

### 3.3 Connection context (Batch D)

**`multi_hop_context`** — `query: string (required)`, `entity_a: string
(required)`, `entity_b: string (optional)`, `k: integer [1..30] default
15`, `hops: integer [1..2] default 2`. Also a **compound engine
operation** (revision 1 sketched it as a recipe chain; both reviews
correctly found that incompatible with executor dataflow and
`combine_evidence` type guards). Inside the operation: resolve both
entities per principle 9 → `graph_path` (two entities) or bounded
`graph_neighborhood` (one) → hydrate each surviving edge's evidence per
principle 5 (both stances, capped) → run the question-context retrieval →
assemble a **composite envelope**: part 1 = claims+chunks context,
part 2 = `paths`/`edges` with their backing evidence and D54 support
flags. Edge policy (revised per D54): edges with withdrawn or no current
supporting evidence are **kept and flagged**, never silently dropped;
the tool description instructs that edges are structure — quotable
answers come from the evidence parts. Bounded fan-out: top-N edges by
existing ranking, N fixed by `k`. Typed negative when no path exists.
Entity-free v2 stays deferred (§6).

### 3.4 Recall mechanics inside existing hybrids (Batch E)

- **Refill from the candidate tail (deterministic).** Hybrid recipes
  already fetch `candidate_k` nominations and cut to `k` before
  hydration; when confirmation drops results below `k`, hydration
  consumes the *unused tail of the already-fetched pool* (no second
  index read, no changed-state exposure) until `k` is met or the pool is
  exhausted. `dropped_by_hydration` keeps the honest count. No
  re-nomination round exists (revision 1's wording implied one; it
  returns identical pools and is withdrawn).
- **Near-duplicate grouping (deterministic v1).** Before the final cut,
  candidates are grouped by exact normalized-text equality (normalizer
  version pinned in the recipe version string); the highest-ranked
  member represents the group and carries `corroboration_count` plus the
  grouped claim ids, so source diversity is preserved, not erased.
  Embedding-similarity grouping is deferred (§6) — it is
  non-deterministic across index states.
- Both mechanics change recipe behavior → recipe version bumps → tool
  catalog hash rolls → benchmark protocol identity rolls (accepted).

## 4. Benchmark protocol impact (v9)

- New recipes roll the tool catalog hash → `full-v9`/`full-v9-strong`
  per the established renaming pattern; fingerprints re-locked.
- **Answer length cap: flag-gated, default OFF** (operator decision
  2026-08-02; dissent recorded in the benchmarks corpus). Binding
  mechanism, because the field alone does nothing: `answer_word_cap:
  int | None = None` on `LoCoMoProtocol` **and** threaded through
  `RunConfiguration`, the fingerprint input, prompt rendering (the cap
  sentence becomes a template parameter — the current hardcoded "at most
  twenty words" sentence is removed from the module template), the
  `_answer_one` guard (skipped when `None`), persisted protocol
  literals, and the locked tests. The qualitative instruction ("the
  shortest phrase that fully names the requested entities/values, no
  explanations or reasoning") remains unconditionally.
- Scores under v9 are not comparable to v8 or earlier (standing rule).

## 5. Validation plan (per batch, before any full run)

1. Recipe/linter/registry tests plus live-PG behavior tests: mentions
   joins (incl. hub-entity bound), window intersection (open intervals
   in, unknown-precision out, exact `excluded_unstamped`), neighbor
   ordering at document edges, facts confirmation + two-stance evidence
   caps + lineage filters, refill tail consumption, dedup grouping
   determinism, entity-ambiguity negatives.
2. Cheap targeted replays against the preserved v8 stores (answer+judge
   only): cat-1 slice for `multi_hop_context`; cat-2 slice for
   `claims_as_of`; a current-state + adversarial spot-set for
   `current_context` (a returned fact must never contradict its own
   shipped evidence without the contradiction being visible).
3. Ship gate: the targeted slice moves double digits, or the tool is
   redesigned rather than shipped into the default prompt.

## 6. Recorded deferrals (each with its trigger)

| Deferred | Why | Adoption trigger |
| --- | --- | --- |
| Media-segment retrieval recipe (D65 spine) | binding elsewhere; needs its own surface design | first media-bearing corpus in production use |
| `citation_path` recipe (primitive exists, unexposed) | document-citation questions unmeasured | first corpus with DOC_CROSSREF density |
| K pages by predicate/community/doc-source + page bodies | primitive supports it; entity key covers current need | agent demand or product surface for browsing pages |
| Semantic/profile entity discovery (name unknown) | needs entity-vector nomination design | after T-ladder upgrade below |
| Resolve ladder T1–T3 (fuzzy/semantic aliasing) | separate design; current tools inherit T0 | recall failures attributable to aliasing in production traces |
| Negative-testimony polarity ("X does not…") | relations lack polarity; boundary recorded — such content is reachable only as claims/observations today | polarity modeling design |
| Absence/exhaustive queries ("did X ever…") | `typed_absence`/`scan` primitives exist engine-side; agent surface needs its own cost design | dedicated design |
| Session/transcript fetch | privacy grain + size budget design needed | dedicated design |
| Pagination/continuation beyond truncation, envelope size budgets | truncation contract covers v1 bounds; full continuation tokens need executor support | first agent consumer hitting bounds in practice |
| Lexical facts nomination; embedding-similarity dedup; entity-free multi-hop v2; `brief`/`contradictions` recipes | see sections above | per-item notes above |

## 7. Sequencing (non-binding rollout note)

Implementation order B → C → D → E, one PR per batch, each through
Codex-implement + Grok/Claude review, each rolling catalog/fingerprints
as needed. Sequencing lives here as a note; the binding content of this
document is §§1–6.
