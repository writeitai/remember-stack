# Agent retrieval surface — binding design

*2026-08-02. Binding once accepted. Extends `retrieval_design.md` (which
owns nomination/fusion/hydration internals) with the complete agent-facing
tool surface. Rationale and evidence: `plan/analysis/
agent_retrieval_surface_analysis.md`. Goal: an AI agent using only the
public recipe catalog can reach everything the system knows, within the
call budget minimal-effort agents actually spend.*

## 1. Principles (binding)

1. **Complete coverage:** every data spine (claims, chunks, documents,
   entities, mentions, relations, observations, valid-time, graph,
   changes, pages) is reachable through at least one question-shaped tool.
   The coverage matrix in the analysis must contain no gap without a
   recorded decision.
2. **One-call ergonomics for common intents.** Measured agent behavior is
   ~2 calls/question; each common intent (recall, current-state, entity
   browse, time window, connection between entities, read-around-a-hit)
   gets a single-call tool. Composition happens inside recipes, not inside
   the agent's plan.
3. **Typed grains never blur.** Evidence (claims), source passages
   (chunks), current facts (relations/observations), documents, and graph
   structure travel in their own envelope fields. Nothing is flattened.
4. **D48 everywhere.** All content-bearing output passes
   nominate-then-confirm; projections never leak unconfirmed text.
5. **Derived facts ship with their evidence.** Any tool returning
   observations/relations includes their supporting claims (stance =
   supports, hydrated). The distilled layer is the product; the testimony
   is the accuracy anchor; agents get both, typed. (Alternative — facts
   standing alone — rejected for accuracy: derivation stages have observed
   error modes; an unbacked derived fact is unverifiable by the consumer.)
6. **Tool descriptions are written in question language** — they state
   *when to reach for the tool* ("use when a question links two people…"),
   because descriptions are the only routing a minimal-effort agent reads.
   Descriptions never mention any benchmark.
7. **Honest negatives and bounds.** Every tool: bounded k/hops/windows,
   typed `negative` on empty ("no connecting path found"), truncation
   surfaced, `dropped_by_hydration` counted, refill semantics per §4.
8. **Benchmark honesty.** No dataset-specific logic in the surface. The
   benchmark consumes the same catalog as every other agent.

## 2. The target catalog (delta over `0ef5454`)

### 2.1 Entity and time spines (Batch B)

| Tool | Signature | Returns | Description intent |
| --- | --- | --- | --- |
| `documents_about` | `(entity: str, k≤50)` | `sources[]`: doc_id, title, source_kind, mention_count, first/last mention timestamps; recency- or mention-ranked | "Which ingested documents talk about X?" — corpus browse per entity. Backed by mentions + resolution (Postgres authoritative). |
| `claims_about` | `(entity: str, query?: str, k≤50)` | evidence grain claims (valid-time included), entity-filtered via mentions join, optionally semantically ranked by `query` | "What did/does X say/do…" with precision instead of embedding luck. |
| `claims_between` | `(from_iso, to_iso, query?: str, k≤50)` | evidence-grain claims whose `[valid_from, valid_until]` intersects the window; claims lacking valid-time are excluded and the count of exclusions surfaced | "What happened in/around <time>" — exposes the D41/#158 stamps. Rejects reversed/unbounded windows. |
| `chunk_neighbors` | `(chunk_id, radius≤2)` | `chunks[]` — the D48-confirmed previous/next chunks in the current representation's order | "Read the surrounding passage of this hit." Document-edge truncation is explicit, not silent. |

### 2.2 Current-state context (Batch C)

`current_context(query, k≤30)` — the facts-grain sibling of
`question_context`: semantic nomination over the P1 facts channel
(observations + relations; the adapter's `search_facts`, exposed at last),
Postgres-confirmed, each fact hydrated **with its supporting claims**
(principle 5) and its supersession status. Returns `facts[]` + backing
`evidence[]`, typed separately. Description intent: "what is true *now*
about the things the question mentions." Lexical (BM25) nomination over
fact text is included only if fact text is already indexed in P1;
otherwise semantic-only ships first and lexical is a recorded follow-up —
this design does not mandate new index machinery.

### 2.3 Connection context (Batch D)

`multi_hop_context(query, entity_a, entity_b?, k≤30, hops≤2)` — one-call
composed chain: resolve entities → `graph_path` (both) or bounded
`graph_neighborhood` (one) → hydrate each returned edge's supporting claims →
run `question_context(query)` → `combine_evidence`, with `paths`/`edges`
kept in their typed fields. Tenets: an edge is a lead, never an answer
(edges without confirming claims are dropped); bounded hops and fan-out;
typed negative when no path exists. Requires two small internal additions:
a top-entity selector step (extract the best resolved candidate for the
next op) and batched edge-evidence hydration. Optional v2 (separate
decision): entity-free variant via semantic entity nomination.

### 2.4 Recall mechanics inside recipes (Batch E)

- **Refill:** when D48 confirmation drops nominated candidates below the
  declared `k`, the recipe re-nominates (bounded: one refill round,
  `candidate_k` cap unchanged) before returning. Surfaced via
  `dropped_by_hydration` as today.
- **Near-duplicate diversity:** candidate lists are deduplicated by
  content similarity (normalized text equality first; embedding-similarity
  threshold as a recorded follow-up), not only by id, before the final cut
  — measured slot-burn case: five near-identical claims in one k=10
  window.

### 2.5 Explicitly deferred (recorded decisions, not silent gaps)

- **Session/transcript fetch** — wants its own design (privacy grain +
  size); the chunk_neighbors radius covers the near-term need.
- **Absence/exhaustive queries** ("did X ever…") — different cost model;
  separate design.
- **Entity-free multi_hop v2** — after v1 proves out on cat-1 replay.

## 3. Benchmark protocol impact (v9)

- New recipes roll the tool catalog hash → protocol identity bump to
  `full-v9`/`full-v9-strong` following the established renaming pattern;
  fingerprints re-locked; prompt tool list flows from the registry.
- **Answer length cap: flag-gated, default OFF** (operator decision
  2026-08-02, recorded with dissent noted in the benchmarks corpus):
  `answer_word_cap: int | None = None` on the protocol dataclass,
  fingerprinted; when set, the prompt sentence and runner guard activate;
  when `None`, neither exists. The qualitative instruction ("shortest
  phrase that fully names the requested entities/values, no explanations")
  remains — it is an accuracy device, not a length cap.
- Scores under v9 are not comparable to v8 or earlier (standing rule).

## 4. Validation plan (before any full run)

1. Recipe-level: linter + registry tests; live-PG behavior tests for
   mentions joins, window intersection (incl. null valid-time exclusion),
   neighbor ordering at document edges, facts confirmation + evidence
   backing, refill bound, dedup.
2. Cheap targeted replays against the preserved v8 stores (answer+judge
   only): cat-1 slice (282 q) for `multi_hop_context`; cat-2 slice for
   `claims_between`; adversarial spot-set for `current_context` accuracy
   (derived facts must never contradict their shipped evidence).
3. Ship gate per batch: the targeted slice moves double digits or the tool
   is redesigned, not shipped by default prompt mention.

## 5. Sequencing

Batch B (entity/time recipes + v9 flag) → C (`current_context`) → D
(`multi_hop_context`) → E (refill/diversity). Each batch: Codex
implements, Grok + Claude review, own PR, own catalog/fingerprint roll if
it touches the surface.
