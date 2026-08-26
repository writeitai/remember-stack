# Retrieval Design — the Query Machine

How agents get answers out of the memory: the primitives, assured operations, the response contract,
the consumption surfaces (API / CLI / MCP / mounted filesystems), and the rules that keep a
multi-store system honest. Binding design for decisions **D48–D51** as amended
by D94 (PostgreSQL-native P1), building on D9 (parallel channels + RRF, zero
LLM on the query path), D10/D44 (as-of mechanics),
D16 (scope views), D41 (`claims_as_of` and its bar), D43 (observation retrieval), D22 (the
retrieval eval). Driven by the scenario battery `plan/analysis/retrieval_scenarios.md`
(S1–S63) — every design element below cites the scenarios that forced it — and the
implementation analysis
[`retrieval_default_path.md`](../analysis/retrieval_default_path.md). Numbers are starting
points to measure, not committed constants (CLAUDE.md).

> **Amended 2026-08-26 (D97).** Ordinary questions resolve to ids, load observations **and**
> relations, hop `neighborhood` with an **empty** predicate list, and match fact text.
> Predicates are optional filters. Details:
> [`entity_identity_and_retrieval_design.md`](entity_identity_and_retrieval_design.md) §7.

> **Reading this cold (CLAUDE.md Rule 1).** The memory has three planes: **E** (evidence —
> immutable claims, adjudicated relations/observations, all anchored on canonical entities with
> bi-temporal validity), **K** (compiled + authored knowledge pages in git), and **P**
> (projections: PostgreSQL-native P1 search state, P2 LadybugDB graph
> snapshot, P3 corpus filesystem).
> Two **grains** matter everywhere here: the **evidence grain** (claims — *what sources
> asserted*, immutable, possibly stale or contradictory) and the **fact grain**
> (relations/observations — *what the system holds or held true*, with adjudicated validity
> windows). A third, the **compiled grain**, is K pages (LLM-written syntheses with recorded
> citations and freshness). "**Hydration**" = resolving the bare IDs that search/traversal
> return into full, provenance-bearing records from Postgres, the source of truth. The primary
> consumers are **agentic coding harnesses** (Claude Code, Codex, OpenCode) — callers that can
> read files, compose API calls, and reason; the design leans on that hard.
>
> Search vocabulary used throughout: **FTS/BM25** — classic keyword search (full-text search;
> BM25 is its standard relevance-scoring formula) — finds *exact words*, which vectors miss;
> **semantic search** — matching by embedding similarity — finds *meaning*, which keywords
> miss; **RRF** (reciprocal rank fusion) — merging several ranked lists by rewarding items
> that rank high on *any* list (score ≈ Σ 1/(k+rank)), the standard way to combine
> keyword+vector+structured channels without tuning score scales against each other;
> **cross-encoder** — a small neural model that re-scores a query/result *pair* jointly —
> more accurate than embedding distance, too slow for more than a final top-k re-sort (hence
> optional and flagged); **continuation** — an opaque cursor a caller passes back to fetch
> the next page of a truncated result; **MCP** — the Model Context Protocol, the standard by
> which agent harnesses discover and call external tools.

## 1. The central stance: the agent is the query planner

D9 forbids LLM calls on the query path — so the intelligence must live in the **caller**, and
the callers are agents. The consequence is a design that does not try to be smart; it tries to
be **composable, self-describing, and honest**:

- **Composable** — a small set of typed, orthogonal primitives (§3) that agents chain; assured
  operations (§4) are frozen compositions, not new capabilities.
- **Self-describing** — the system teaches its consumers: MCP tool descriptions render from
  the assured-operation registry; a shipped **consumption skill** (§8) teaches the memory model itself.
- **Honest** — every response carries a machine-readable account of its own limitations:
  grain, freshness, contradictions, truncation, and a typed taxonomy of "no" (§5, §6).

**Non-goal (stated, not deferred):** a natural-language→query planner on the query path. An
agent that wants NL planning does it in its own head — that is what it is. (An off-path
"query advisor" could be built *as a consumer* of this API; it is not part of the system.)

## 2. The correctness rule: projections propose, the spine disposes (D48)

Every fast entry channel is a projection with **lag**: P1 embeddings are
written asynchronously into private PostgreSQL search tables, the graph is an
hours-old snapshot (P2, D7), and K is debounced. The design resolves the
mixed-freshness problem (`questions.md` #23) with one invariant — **scoped precisely to the
query engine**:

> **Stale projections may nominate candidates; only authoritative PostgreSQL
> rows confirm truth.** P1 nomination and confirmation share one PostgreSQL
> statement/MVCC snapshot. P2 nominations pass through by-ID hydration against
> the live spine. In both cases validity, invalidation and contradiction state
> come from invariant-bearing authority views, never the projection row.

**Where the invariant does NOT apply — and what covers those surfaces instead.** Two
consumption paths never pass through hydration, and the design says so rather than
overclaiming:

- **Mounted reads** (P3 / artifacts / raw / the K checkout, §7) are snapshot reads by
  construction — an agent `cat`-ing a file gets the file as of the snapshot/compile. The
  covering mechanisms: every mounted surface carries **visible freshness metadata** (P3
  snapshot version + per-page freshness/flags in `_index.md`; K page provenance footers; E0
  artifacts are immutable-per-`content_hash`, so staleness does not apply), and the
  consumption skill (§8) teaches the covering *motion*: mounts are for orientation and
  reading; **anything load-bearing at the fact grain is verified against the spine**
  (a `lookup`) before acting on it.
- **K prose is never "confirmed" by hydration even via the API** — a compiled page's cited
  IDs can be re-checked, which detects *staleness*, but detecting it cannot make a stale
  synthesis correct or excise the one superseded sentence. K answers are therefore always
  **compiled grain** (§6) with `compiled_at` + staleness + open-flag state — never presented
  as live-confirmed fact. Current-fact questions route through relations/observations;
  the K page is the orientation layer above them.

Consequences, spelled out (S42, S43):

- On the query engine, staleness can only cost **recall** (a fact too new to be indexed is
  not found), never **correctness** (a superseded fact cannot pass the
  same-statement P1 authority join or P2 hydration). Recall lag is bounded by projection cadence and
  *reported* (§5 freshness stamps); correctness is live, always.
- P1 authority rejection is ordinary filtering within the ranked statement and
  is reported through candidate/eligible counters. The caller-visible
  **nominate-then-drop** hydration artifact remains for P2 and deep source-body
  hydration; the envelope reports those drops honestly.
- **Compound results revalidate as units, not rows (S17, S21).** A graph *path* is only
  meaningful whole: if hydration invalidates one edge of a nominated path, the **path is
  dropped as a unit** (and counted in `dropped_by_hydration`) — never returned with a hole.
  The engine does not silently recompute an alternative live path (that would hide snapshot
  staleness); the honest answer is the drop plus the P2 snapshot timestamp, and the caller
  re-queries if freshness matters. The same unit rule applies to any future compound shape.
- P1 entry and authority filtering now run together in PostgreSQL; P2 expansion
  remains on the API node's local snapshot. This removes P1's cross-store
  confirmation round trip while preserving progressive hydration to evidence
  and source bytes (overall_design §7).

## 3. Primitives — the typed, zero-LLM operations

Each primitive is grain-typed (§6), accepts the two temporal parameters (§below), and returns
the envelope (§5). None calls an LLM; none has side effects (S-battery invariant: **reads
never trigger anything** — all K/E triggering originates from writes).

| Primitive | Signature (essentials) | What it is | Scenarios |
|---|---|---|---|
| `resolve` | text, type?, context_entities? → ranked entity candidates | query-time entity resolution over the registry's **non-LLM tiers T0–T3** (T0 canonical-alias exact, T1 trigram, T2 phonetic, T3 embedding similarity — embedding a query string is not an LLM call and the semantic channel does it anyway; **no T4 adjudication on the hot path**, D17). Inflected names (S50) ride the stored canonical aliases + T1/T2. Ambiguity → ranked candidates, never a silent guess (S51). Returns **current** identities, following `merged_into` survivor chains with the redirect disclosed (S60); *pre-merge* identity reconstruction is the transcript-based `identity_as_of` recipe (S61), never automatic | S1, S50, S51, S60 |
| `lookup` | relations(s?, p?, o?) / observations(entity, property?) / claims(doc?, entity?) / entity(id) / document(id) | scalar reads on the spine and its indexes; observation property matching is semantic-over-statement (D43) | S1–S4, S26 |
| `search` | channel × target × query, filters, k — channels: semantic \| bm25 \| fts; targets: chunks \| claims \| relations \| observations \| k_pages \| **media_segments** (D65) | PostgreSQL-native P1 entry: pgvector semantic indexes, pg_textsearch BM25 indexes, and authority joins in one statement (D94). `media_segments` is the **cross-modal** target — a logical target over per-modality PostgreSQL indexes: one row per standalone image / video keyframe-or-shot / bounded audio segment, embedded by CLIP-class models that map pixels (or audio) and *text queries* into a shared vector space — so "the photo with the small red connector" matches pixels the description never mentioned (access is not discovery: an agent can open any file it *found*, never one it didn't retrieve). Rows carry modality + embedding family/version + representation + immutable source locator, hydrate to representation passage + preview + raw deep link, and RRF-fuse with the text channels (rank fusion only — different embedding families are never compared by raw distance); embedders are port config (D63), capability advertised **per query→target modality pair** — **any unconfigured pair reports as a typed `boundary`** (§5), never a silent gap | S6, S46, S52, S62 |
| `graph` | neighborhood(entity, hops, predicates?) / path(a, b, max_hops) | P2 snapshot traversal; as-of via inline path predicates (D44) | S17–S22 |
| `fuse` | result_sets → RRF-merged set | reciprocal-rank fusion of parallel channels (D9), exposed as an operator so *agent-composed* channel sets fuse the same way recipes do | S46 |
| `rerank` | candidates × signal — graph_distance(focal), evidence_count, cross_encoder (flagged) | the D9 rerankers as explicit, inspectable stages | S46, S48 |
| `hydrate` | ids, depth: record \| evidence \| sources \| bytes, locator? | the §2 confirmation hop + progressive deepening: record → evidence rows + claims → documents → GCS handles. At `depth=bytes` an optional **source locator** (D65) scopes the fetch to a time interval / region, returning a seekable, codec-aware segment (§7 — unmounted parity for media) | S5, S59, all |
| `transcript` | relation \| observation \| entity \| k_page → its decision history (recent-first bound; see amendment below) | adjudications, resolution decisions, compile provenance — the audit trail as a first-class query ("why do we believe…") | S8, S32, S35 |
| `delta` | since T, scope?, kinds? → changed evidence / pages | the change feed as a query (new / capped / invalidated / recompiled) | S13, S14, S30 |
| `pages_about` | entity \| key → K pages (+ freshness/flags) | **the K routing index read backwards**: the rule-key inverted index built for write-side routing doubles as the reader's discovery index — which pages exist about X, mechanically | S31, S45 |
| `aggregate` | enumerated forms: count / group-by-predicate / group-by-object / timeline(entity) / delta-top-entities(since T) / typed-absence(type, predicate) | see §9 — enumerated, not general | S12, S26–S28, S30, S40 |
| `scan` | filter → stream | the batch surface (§9): full exports for compilers, auditors, external analytics — same zero-LLM reads, streaming contract, separate resource pool from interactive | S53 |

**D94 P1 amendment (2026-08-14; implementation pending).** The `search` rows
above remain independent nomination channels, not two names for one lookup.
For claims and chunks, `semantic` embeds the query once and searches a pgvector
HNSW index; `bm25` searches a pg_textsearch BM25 index over the same canonical
text and stable ID. RRF combines ranks, never raw vector/BM25 scores.

Both channels join the invariant-bearing authority view inside the ranked
PostgreSQL statement. Projection lag can omit a new row, but an invalidated,
wrong-lineage or wrong-generation row cannot leave the statement. Ordinary DML
maintains both indexes and autovacuum cleans dead rows; index creation belongs
to migrations/backfill and evidence-driven reindexing, not reads or a P1
maintenance ticker. The analyzer/text configuration is versioned rather than
silently assuming English.

A chunk result still preserves D80 separation: the P1 text is normalized body
only, an optional deterministic `location_header` is returned separately, and
source offsets plus document/version/representation handles remain explicit.
Deeper byte hydration verifies the current coordinate and reproducible hashes.
The result is evidence grain with its own typed `chunks` payload: a chunk is
source text, never an atomic claim or adjudicated fact.

**Amendment (D80/D94):** passage vectors are built under the embedding-input
policy (`e1_embedding_input_policy.md`). Admitted filters join normalized
authority such as `document_versions.source_shape` and
`document_sections.role`; message-level refs (`channel_ref`, …) participate only
when a recipe declares operators and the typed authority exists. Search validates
one configured current attestation; it carries no copied generation/filter scalars.

**Claim-channel filters (D80 decision):** claim rows **do not** carry message filter copies.
Recipes that need channel/author/time on claim hits **join** claim → origin chunk (or document
location facts). Do not invent per-claim scalar inheritance in the first implementation.

`combine_evidence` is the side-effect-free composition operator for separately typed evidence
sets. It combines confirmed claim and chunk envelopes without cross-fusing their unlabeled UUIDs
or pretending the two shapes share one relevance scale. Like `fuse`, it adds no retrieval
capability; an agent can run and combine the same primitive results itself.

**Amendment (2026-07-27, issue #156).** `transcript` (and the `identity_as_of`
recipe that is one fixed chain over it) is **recent-first bounded** by a
measurable default (`DEFAULT_TRANSCRIPT_LIMIT`, starting point 40 rows), with
the envelope's existing truncation marker set when the cap applies. Long
entity resolution logs were returning hundreds of rows and blowing agent
reader contexts; S18 already forbids silent caps, so the bound is disclosed
rather than unbounded-by-default. Callers that need a larger window pass an
explicit `limit` — on the Python surface and as the recipe's optional `limit`
parameter (recipe v3), so a truncated history is recoverable through the
public surface too. This is the same cap-with-signal pattern as `delta`, not
a new mechanism.

**Temporal parameters — composed, not special-cased (S15/S16).** Every primitive that touches
validity accepts `valid_at` (world time: "what held at T") and `believed_at` (system time:
"what did we believe at T"; caps `ingested_at`/`invalidated_at` across *all* channels at once —
S43). One composition rule makes multi-step temporal questions free: **every envelope exposes
its timestamps in parameter-ready form**, so "who worked at Acme when the contract was
signed?" is two ordinary calls — resolve the signing's event time from the first result, feed
it as `valid_at` to the second. No special machinery, and none should ever be added; if a
temporal question cannot be composed this way, that is a design finding, not a recipe request.

Two honest limits on `believed_at`, stated rather than discovered (S43, S61):

- **Per-channel transaction-time horizons.** Postgres holds full belief history. Under D69 the
  *hot* P2 relation view is unbounded by invalidation age: it keeps every relation whose
  survivor-redirected endpoints remain emitted active nodes, so P2 reports a `null` (unbounded)
  retention-age horizon. Endpoint retirement/forgetting remains a structural projection boundary,
  not an age horizon. P1 carries live-filtered copies and may have a real channel horizon. The
  envelope exposes each channel's **`believed_at` horizon**; whenever one is finite, a query before
  it gets a typed `boundary` naming the fallback rather than a silent truncation.
- **Identity is resolved in the current regime by default.** `resolve`/`hydrate` follow
  today's aliases and merge redirects even under a past `believed_at`; reconstructing the
  *identity boundary* as it stood at T (pre-merge, pre-un-merge) is the explicit
  `identity_as_of` recipe over `resolution_decisions`/`merge_events` (D21 transcripts). The
  envelope states which identity regime answered, so an audit-date query can never silently
  mix today's identities with yesterday's beliefs (S61).

## 4. Assured operations — frozen plans as registry data (D50, D87)

The public intent catalog contains exactly `resolve_entity`,
`testimony_context`, `fact_context`, and `answer_context`. The former
`question_context` and `current_context` names and the older named recipe
catalog are superseded by `open_query_space_design.md` §§2–3; reusable query
patterns live as non-tool `examples.*` saved queries. There is no compatibility
alias layer.

An **assured operation** is a named, versioned zero-LLM execution plan.
`resolve_entity`, `testimony_context`, and `fact_context` are compositions of
primitives with fixed fusion/rerank settings; `answer_context` is the sole
`operation_bundle` plan and references the two canonical child descriptors.
Operations are **closed platform registry rows, not independent
invariant implementations** — the same move as predicates (D5), ontology
(D15), and K routing rules (D45). An operation row carries, concretely: `name`,
`description`, typed `parameters`, a discriminated **`execution_plan`**:
`primitive_chain` with ordered §3 operations and fixed settings, or the sole
`operation_bundle` value whose ordered children are exactly
`testimony_context` and `fact_context`;
**`result_contract`** (`envelope` | `context_bundle_v1`), nullable-only-for-the-bundle
**`output_grain`**, **`answer_intent`** (`identity` | `testimony` | `facts` |
`combined_context`), plus `version` and MCP-rendering metadata. The database closes these fields
to the four canonical tuples; the bundle has no false single-grain declaration. (Full DDL joins
the control-plane tables in
`postgres_schema_design.md`; the fields above are the contract.) Three payoffs:

1. **The linter can enforce semantics — mechanically, on the enums.** The rule is a
   constraint, not a prose judgment: `answer_intent = facts` requires
   `output_grain = fact` and a chain built only over validity-filtered
   relation/observation primitives. The bundle plan is accepted only for
   `answer_context` with those exact two children. `testimony_context` declares `testimony` over
   `evidence`, so the D41 bar ("claims never answer *is it true now*") is violated only by a
   registration the constraint rejects. A *name/description* smell-check (a recipe named like
   a fact query but declared evidence-grain) is an advisory lint for humans, not the
   enforcement mechanism.
2. **The eval harness measures per operation.** Recall@k per operation per scenario class
   (D22's retrieval half); operation versions make regressions attributable.
3. **MCP tools render from the closed registry** — the assured tool list is the four
   platform-owned rows (name/description/parameters), exactly as extraction prompts render
   from the ontology registry. Customer-authored behavior belongs in the saved-query registry
   and never becomes a top-level intent tool merely by inserting a row.

Assured operations never add base capability — anything they do is composed from §3 and the
invariant-compiled `memory_v1` authorities. That is a testable property: the eval harness
replays each operation through those authorities and checks membership equivalence.

The ordinary high-recall evidence entry is **`testimony_context`**. Its frozen chain independently
fuses cheap semantic + lexical claim-ID nominations, independently fuses cheap semantic +
lexical chunk-ID nominations, hydrates each fused list exactly once through D48, then
returns the two payloads under one evidence envelope. This order avoids
hydrating candidate-depth rows or counting one stale candidate more than once. Candidate depth
is larger than returned depth; both bounds are declared in the operation schema and measured rather
than hidden. Optional confirmed `entity_ids` narrow the eligible set inside the
same PostgreSQL ranked statement, through normalized associations, before
candidate depth is applied; globally nominating and then filtering is forbidden. Multi-anchor coverage sorts before
relevance and before the candidate cut. The operation contains no fact or entity-candidate channel.

`fact_context` is the fact-grain counterpart: semantic facts nomination followed by
PostgreSQL confirmation under its current, valid-at, overlap, or history world-time mode. It
applies entity and time eligibility before its internal candidate depth by
joining the normalized fact authority in the ranked PostgreSQL statement; the current-only public
`semantic_facts` SRF is not the historical implementation. It returns facts with their
supporting and contradicting evidence associations. `answer_context`
does not add a retrieval path; it returns the complete testimony and fact responses as separate
members of `ContextBundle/v1`. The exact contracts and bounds live in
`open_query_space_design.md` §3.1.

## 5. The response envelope — the contract is the answer's self-account (D49)

Most systems return rows. This one returns rows **plus machine-readable claims about the
answer itself** — because the caller is an agent that must *reason about* the answer:

```
{
  grain:        fact | evidence | compiled | composite,  // §6; one operation's cohesive result
  results: [ { …typed record…,
        validity: {valid_from, valid_until, ingested_at, invalidated_at},
        evidence_count, confidence,
        contradiction: {group_id, co_members[≤cap], returned, total, continuation} | null,
        provenance: {hydrate_handle, depth_available,
                     // D65 — on EVIDENCE-GRAIN items only (a claim has one derivation;
                     // a fact aggregates many — its evidence hydrates to per-claim records):
                     source_locators[]?, derivation: {kind, evidence_mode}? } } ],
  temporal_scope: {mode, evaluated_at, believed_at,     // exact applied scope; closed per operation
                   identity_regime: current | as_of,
                   ...mode_specific_fields},            // at, from/to, or valid_at as declared
  freshness: {                                          // per contributing source (S42)
      pg: live_ts, p1: {max_write_lag, believed_at_horizon},
      p2: {snapshot_ts, believed_at_horizon},           // horizons: §3 — beyond them, `boundary`
      k:  {compiled_at, stale: bool, open_flags: n} },  // ← k_layers spike 9 lands here
  truncation:   {truncated: bool, returned, estimated_total, continuation} | null,  // S18/S49
  dropped_by_hydration: n,                             // §2 nominate-then-drop honesty (paths drop as units)
  negative:     null | {kind, explanation, workaround}  // §below
}
```

`temporal_scope` is required and discriminated by `mode`; an operation exposes
only the variants it actually supports. In particular, `fact_context` returns
exactly one of `current`, `at`, `overlap`, or `history`, with the mode-specific
fields and invariants defined in `open_query_space_design.md` §3.1. This makes
the applied world-time selection distinguishable on the wire instead of
encoding history and current as the same pair of null timestamps.

An evidence-grain envelope may carry both `evidence[]` (atomic claims) and `chunks[]` (confirmed
source-text fallback). They are distinct fields because they have different semantics and
provenance shapes even though both are testimony. Every chunk record discloses its generated
context prefix separately from its source body and carries the immutable source coordinate
needed to audit it. A consumer may compact their rendering, but the raw envelope never erases
the distinction.

Single-grain answers are the common case. A `composite` envelope is one operation's cohesive
typed result, such as a fact hydrated with its explicitly associated evidence; it is not a
container for independent complete responses. D87 removes `Envelope.parts` and `EnvelopePart`.
The sole side-by-side testimony/fact form is `ContextBundle/v1`:

```text
ContextBundleV1 {
  contract:  literal("ContextBundle/v1")
  testimony: Envelope  // evidence grain; complete testimony_context result
  facts:     Envelope  // fact grain; complete fact_context result
}
```

The wrapper forbids extra fields, never flattens the children, and returns no partial bundle on a
child execution failure. A typed negative is a completed child response and stays inside that
child. `open_query_space_design.md` §3.1 owns the exact failure and equivalence contract.

Three rules that are contract, not garnish:

- **Contradiction co-members are never silently absent (S23).** Returning one side of a live
  `contradiction_group` with no indication of the others is a **contract violation**, not a
  ranking choice — "contradictions are surfaced, never silently resolved" applied to the read
  path. The bounded form: co-members return **inline up to a guaranteed cap** (typical groups
  are 2–3 sides; both FY2023 revenue figures come back together, with per-side evidence
  handles); beyond the cap the contradiction block still *always* carries `group_id`,
  `returned`, `total`, and a `continuation` — bounded like every hub answer, one-sided never.
- **No silent caps (S18, S49).** Hub neighborhoods and big result sets return ranked pages
  with explicit truncation markers and continuations — never a silent top-k posing as
  everything, never a timeout.
- **The K freshness block is the reader-facing flag surface** (resolves `k_layers_design.md`
  §11 spike 9): any answer that consumed a K page carries its `compiled_at`, staleness, and
  open-flag count, and P3's generated `_index.md` shows the same per page — an agent can see
  "this to-be page has 3 unresolved evidence-change flags" *before* planning against it (S34).
  (The third candidate surface — a per-page status sidecar file — is dropped: two surfaces
  cover both consumption modes, and a sidecar would be a second mutable state to keep honest.)

Two media additions to the provenance block (D65, `media_design.md` §4–§5) — both attach at
the **evidence grain**: a *claim* has one derivation record and one locator set; a *fact*
aggregates many claims with possibly different modes, models, and locators, so a fact-grain
result never carries a single flattened `derivation` — its evidence hydrates to per-claim
provenance records, association intact:

- **Source locators** — for evidence derived from media (or paginated paper), the provenance
  block carries the typed locator(s): the exact page/bbox, image region, or time interval of
  the raw original the evidence traces to, rendered as deep links (`original.mp3#t=873` — a
  display rendering; mounted consumers seek locally with the structured locator, unmounted
  consumers pass it to `hydrate depth=bytes`; `media_design.md` §8). Locators are pinned to
  the document version + representation and precision-honest; the agent lands on the moment,
  not on a 90-minute file (S59, S63).
- **Derivation disclosure** — evidence extracted from a media representation carries its
  `derivation_kind` (asr | acoustic_events | vlm_description | ocr | shot_notes | …) and
  **`evidence_mode`**:
  `source_expression` (a fallible rendering of speech/symbols present in the source — a
  transcript sentence, OCR'd text), `model_observation` (the model's account of what the
  source *shows* — "the image shows a red valve"), or `model_interpretation` (the model's
  reading *into* the source — "the speaker sounds hesitant"). Inherited deterministically
  from the converter's mode-homogeneous labeled ranges (a claim spanning modes takes the
  most-mediated one), cached on the claim's occurrence record, never judged per claim at
  read time; disclosure, never a verdict. An agent reading "Alice looked hesitant" sees
  `model_interpretation (vlm)` and weighs it.

**The negative taxonomy — typed "no"s (S29, S39, S55).** Each demands a different agent
reaction, so they are distinct envelope kinds, fixed now (retrofitting a taxonomy onto a
deployed API breaks consumers):

| kind | meaning | agent's correct move |
|---|---|---|
| `unknown_entity` | nothing resolves | widen resolution, check spelling, try search |
| `known_empty` | entity exists; no matching facts | trust the absence (within freshness) |
| `boundary` | a stated capability limit (e.g. S29 cross-entity numeric range scans, the D43 price) | named limitation + workaround in the envelope; re-plan |
| (forgotten) | hard-deleted content — **indistinguishable from never-existed** (S55), so it is *not* a kind: it surfaces as `unknown_entity`/`known_empty` | — |

(There is deliberately no `denied` kind: content-level authorization is a library non-goal —
§9. If a future deployment-side layer adds one, its wire behavior must be
indistinguishable-from-empty, which is why the taxonomy is safe to freeze without it.)

## 6. The grain type-system (D49)

The fact/evidence split (`concepts.md`; requirements §Retrieval) becomes a **type
discipline** rather than documentation:

- Every primitive and assured operation that returns an `Envelope` **declares its grain**; every
  envelope **carries it**. `answer_context` instead declares `ContextBundle/v1` explicitly.
- Source chunks are **evidence grain**, returned through a distinct typed payload. They say what
  the current source text contains; they are not atomic claims and never answer current-fact
  questions without extraction/adjudication.
- "Current-fact" answers may be assembled **only** from validity-filtered
  relations/observations. Claims answer *what sources asserted* — a claim's asserted-validity
  interval (D41) is testimony, never verdict, and the registry linter bars any composition
  that would let it pose as one (S4, S11).
- Mixed answers are **explicitly two-part**, never blended: S47 ("everything Alice *said*
  about pricing, plus what we *believe*") routes through `answer_context`, whose
  `ContextBundle/v1` carries the complete evidence and fact envelopes as separately named
  members.
- **`testimony_context` returns current testimony** (D54): claims superseded by a newer
  extraction generation, or left behind by a living document's current version, are excluded.
  It has no mode flag; historical source-version testimony remains available through
  `claims_visible_history`, direct audit primitives, and `examples.claims_as_of`. Fact-grain
  answers carry a `support: current | withdrawn` marker where a fact's
  current-testimony support has dropped to zero (flagged, not vanished — D54).
- The compiled grain carries its own honesty device: the K freshness block (§5) — a compiled
  answer is *pre-paid synthesis with a timestamp*, and says so.

## 7. Surfaces — filesystem-first for harnesses (D51)

Four consumption surfaces, one precedence rule, full parity.

**The mounts (read-only, four):** P3 corpus filesystem (navigate first), E0 artifacts
(Markdown + structure + derived media), E0 raw originals (off the navigation path — explicit
pointers only; whole-file media for multimodal ingestion; audit-logged; storage class routed
by mime — `e0_files_design.md` §2/§5), and the K repo (a read-only checkout: compiled +
authored pages, `_index.md`/`llms.txt` orientation). Markdown is what navigation promotes;
originals are reachable deliberately (S56, S59).

**API / CLI / MCP:** the primitives of §3, the four closed assured operations of §4, and the
open-query/saved-query infrastructure in `open_query_space_design.md`. MCP renders only the four
platform-owned assured descriptors as intent tools; reusable patterns remain discoverable
`examples.*` saved queries rather than becoming tools. CLI mirrors the API 1:1 (agents shell out);
the API is the one place authorization is enforced for query-engine reads (§9). The clean target
uses `GET /operations`, `POST /operations/{name}`, SDK
`list_operations`/`run_operation`, and CLI `remember operations list|run`; recipe-era transport
names are removed rather than aliased.

**The precedence rule (S56/S57).** Everything *readable* is available through both mounts and
API/CLI — some environments cannot mount, so parity is a hard requirement. When mounts are
available, agents are instructed (by the skill, §8) to **prefer the filesystem for everything
a filesystem can do** — navigate, read, grep — because harnesses are exceptionally good at
filesystem work, it costs the serving stack nothing, and it needs no network round-trips. The
API/CLI is reserved for what has **no filesystem equivalent**: semantic search, graph
traversal, temporal as-of, hydration, transcripts, deltas. When mounts are unavailable, the
API/CLI carries everything, including artifact/media byte fetches by handle (S57) — and for
time-coded media, a **locator-aware serving operation** (D65): `hydrate depth=bytes` accepts
a source locator and returns a seekable, codec-aware segment for the referenced interval or
region, so an unmounted agent inspects ten seconds of a 2 GB recording without downloading it
(S59 parity; a naive byte-range is a false promise for arbitrary video codecs). Clip
extraction is a serving operation, never a new stored artifact.

**Progressive disclosure as a query strategy.** The skill teaches one default motion: **orient
on K** (cheap, pre-paid synthesis — `brief`, `pages_about`, or just reading the mounted repo)
→ **verify on the spine** (fact-grain lookups for anything load-bearing, which also
refreshes past K's compile timestamp) → **audit on evidence** (hydrate to claims/sources when
stakes demand). Three coordinated maps of one territory: `llms.txt` (orientation), P3 (the
corpus as files), `pages_about` (the routing index read backwards).

## 8. The consumption skill — a shipped, versioned deliverable (D51)

The system ships **agent-facing instructions** that teach a cold harness the memory. The
curriculum, explicitly:

- **The planes, and the terminology ladder** (claim → relation/observation → *fact* → core
  belief — `concepts.md` §0).
- **The first rule of asking: questions about what is true go to the fact layer.** Relations
  and observations are the system's current, adjudicated holdings — validity-filtered,
  supersession-honoring. Claims are *testimony*: records of what sources said, possibly
  stale, superseded, or contradicted; they answer "who said what, when" and never "is it true
  now" (the D41 bar — enforced by the assured-operation registry, but the skill states it as the
  agent's default, not just a guardrail it will bounce off).
- **Testimony currency** (D54): even within the evidence grain, default claim search returns
  *current* testimony only — claims superseded by a newer extraction generation or left
  behind by a living document's current version are history, reachable through
  `claims_visible_history`, direct audit queries, or `examples.claims_as_of`;
  `testimony_context` has no history flag.
- **The `support: withdrawn` marker**: a fact carrying it has lost all current support (its
  case is in review) — read it as "standing but shaky": fine to report with the caveat, not
  fine to build plans on without checking the transcript.
- **Media: the three kinds of time, named apart** (D65). `start_ms` in a source locator is
  *where in the file* the evidence occurs (minute 14 of the recording); `valid_from`/D41
  asserted validity is *when the fact held in the world*; `ingested_at`/`believed_at` is
  *when the system learned it*. They are different axes — a claim spoken at minute 14 of a
  2023 recording about a 2019 event has all three, all different — and calling any two of
  them "the timestamp" produces wrong as-of queries.
- **Media: derivation disclosure and the drop to raw** (D65). Evidence from media carries its
  `evidence_mode` — `source_expression` (rendered speech/text: a transcript sentence),
  `model_observation` (what the model saw: "the image shows a red valve"),
  `model_interpretation` (what the model read into it: "the speaker sounds hesitant") — read
  it before weighing the fact. Every media-derived answer carries source locators as deep
  links; when the derivation isn't enough (tone matters, the detail is visual), follow the
  locator to the raw original — mounted (off-path, via the explicit pointer) or served by
  interval — and look/listen yourself. The transcript is the map, not the territory.
- **Validity and the two time axes; contradiction semantics** (expect co-members; never pick
  silently); **the envelope and the negative taxonomy**; **the mount layout and the
  precedence rule**; **the orient→verify→audit motion** (orient on K pages, verify
load-bearing facts on the spine, audit down to claims and sources when stakes demand). It is **versioned with the system and partially rendered per deployment** (scopes,
mounts, and enabled operations or saved queries differ) — the same registry-renders-the-prompt move as D15, aimed
at consumers instead of extractors. Its acceptance test is scenario **S58**: a harness that
has never seen the system, given only the skill, must orient via K, keep grains straight, and
respect the boundaries. The skill is not documentation *about* the system; it is part *of*
the system, and the eval harness runs S58 against every skill revision.

## 9. The trust model, aggregation, and the batch surface

**Trust model — content-level authorization is a library non-goal (S54).** The library serves
**one trust domain per deployment**: every agent that can reach a deployment's API or mounts
is trusted with everything in that deployment. This is not a gap but the deployment model's
own logic (registries §1) carried to its conclusion — deployments are already fully
independent instances precisely so that data with different trust boundaries never co-resolves.
The rules:

- **Isolation is achieved by deployment separation**, never by content filtering inside one
  deployment: data that must not be visible to a deployment's agents belongs in a *different
  deployment* (own Postgres, registries, projections, buckets, K repo).
- **Perimeter security is deployment infrastructure**, outside the library: who can reach the
  API/CLI/mounts at all is IAM / network / key management (`questions.md` #8, ops slice); the
  raw-mount audit logging (D51) stands, as an audit — not authorization — mechanism.
- **Why not in-library authz:** scope-level filtering would have to hold consistently across
  *every* channel (PostgreSQL P1, graph, K pages, P3, raw) — mounts cannot query-time-filter,
  so it degenerates to per-scope filtered projections of everything, i.e. a deployment inside
  a deployment. The deployment boundary *is* that mechanism, already designed. (D16's
  filtered-snapshot arm remains available as a *scope-view/performance* tool; it is no longer
  carried as an access-control mechanism.)
- S54 accordingly moves from "must be enforced" to a **documented boundary**: the scenario's
  correct outcome is "the deployment's perimeter admitted this agent; everything inside is
  answerable."

**Aggregation (S26–S30, S40).** Enumerated forms only — counts, group-by-predicate/object,
entity timelines, **delta-top-entities** (evidence gained since T, grouped by entity — S30,
bounded by the delta feed), and **typed-absence** (entities of type X with no relation of
predicate P — S40, an anti-join over the typed entity enumeration, answerable *because* the
ontology types entities) — each a bounded SQL shape with a predictable cost envelope. General ad-hoc
aggregation is **not** an interactive capability (an unbounded GROUP BY over 10⁸ rows is a
denial-of-service against the spine); the escape hatch is the batch surface. Cross-entity
numeric range scans over observation *values* remain a stated `boundary` (S29) — the D43
price, revisited only if a structured value column is ever added.

**The batch surface (S53).** `scan` streams filtered exports (relations of a scope, claims of
a doc-set, the delta feed) under a separate resource pool and no interactive latency promise.
Consumers: K writers, external analytics, auditors, migration tooling. Zero LLM, same grain
labels, same trust model (§9).

## 10. Performance envelope and topology

- **Interactive budget (starting point):** P95 ≤ ~300 ms for entry+expand+hydrate operations or saved queries at
  the 1M-doc target (the Zep/Graphiti reference point D9 cites), measured per plan in the
  eval harness. Batch: throughput-bound, no latency promise.
- **Locality:** API nodes hold the current P2 snapshot on local disk
  (hot-swapped per D7); PostgreSQL serves P1 semantic/BM25 entry, authority
  joins and registry lookups. LadybugDB is embedded in-process (D13) — no graph server.
- **Hot spots named:** hub-entity neighborhoods (ranked pagination, §5 truncation); PostgreSQL
  filtered ANN/BM25 search; multi-hop as-of path predicates
  (D44's known perf spike); `resolve` under trigram/phonetic load (registry indexes, D23).
- **Scaling shape:** query nodes are stateless-plus-replicas → horizontal; the spine scales
  reads via the by-ID discipline (no fuzzy scans on the hot path — D23's index philosophy).

## 11. Evaluation — the battery is the harness

The scenario battery (S1–S63) is the retrieval golden set's skeleton (D22): each scenario
class becomes labeled query/expected-result pairs; the harness measures recall@k and
precision per **operation or saved query × scenario class × corpus slice**, plus contract tests that are
non-negotiable CI: grain labels present and truthful; contradiction co-members always
returned (S23); truncation always marked (S18/S49);
forgotten ≡ never-existed (S55 — the *contract*; D74 designs the end-to-end active-store cascade
and restore non-resurrection behavior, and WP-7.5 activates the CI gate only when its executable
canary is green); operation-vs-primitive-plan
equivalence (§4); and S58 as the
skill's acceptance test. Rerank weights (graph distance, evidence count) are tuned on the
harness, never in production.

The D22 retrieval measurements separately report semantic claims, lexical claims, hybrid
claims, semantic chunks, lexical chunks, `testimony_context`, and each temporal mode of
`fact_context`. Exact source/dialog recall and
complete-evidence recall are recorded before any reader or judge result, so a planner or answer
failure cannot be mislabeled a retrieval miss.

## 12. Non-goals (scope boundaries, not deferrals)

- **No LLM calls on the query path** (D9) — including no NL→plan compiler (§1).
- **No prose generation at query time** — answers are structured envelopes; synthesis is
  either the K plane's compile-time work (S31–S33) or the calling agent's job.
- **No corpus-wide ad-hoc synthesis as a query** ("what's interesting in the corpus?") — that
  is K's compile-time job; the query surface serves what K precomputed.
- **No cross-entity numeric range scans over observation values** (S29) — the documented D43
  boundary, surfaced as a typed `boundary` negative.
- **No query-time writes or triggers** — reads are side-effect-free; all K/E triggering
  originates from writes (`k_layers_design.md` §5).

## 13. Measured spike results

The executable measurements and their machine-specific results are recorded in
[`retrieval_spike_battery.md`](../analysis/retrieval_spike_battery.md). Their
implementation consequences are:

1. **P1 filtered hybrid search:** D94 supersedes the historical Lance/IVFFlat
   spike result as implementation authority. Pgvector HNSW and pg_textsearch
   BM25 use one private `chunk_search` sidecar plus embeddings/indexes on natural
   PostgreSQL rows. Filters use normalized authority joins in the ranked
   statement. The prior Lance measurements remain historical only; D94 requires
   focused functional contract checks, not a parity or scale benchmark before
   the direct replacement.
2. **Hub pagination:** 500 neighbors fit below the battery's 64 KiB operational starting target
   at the 100,000-edge S49 hub; 1,000 did not. A snapshot-bound offset cursor walked all 100,000
   neighbors exactly once. The final machine-specific p95 was 310 ms, slightly above §10's
   300 ms starting target, so this is not a latency proof and WP-7.2 retains portable load
   testing. The
   10,000-row count probe limits only total-count metadata, never reachability; 64 KiB is neither
   a protocol limit nor an SLA.
3. **Rerank weights:** retain the conventional RRF `k=60`; the small hand-labelled grid does not
   distinguish it from the other tested k values. Use normalized graph-distance and
   evidence-count bonuses of `0.10` each — the smallest tested nonzero pair on the broad
   best-score plateau, including a canary with one misleading optional signal. D22 continues to
   monitor relevance as the golden set grows instead of pretending these defaults are universal.
4. **Multi-hop as-of path filtering:** closed by the WP-4.1 deployed-engine battery; inline
   predicates bind and filter during traversal, with no persistent as-of graph justified.
5. **Envelope overhead:** keep 25 contradiction co-members inline under the battery's 16 KiB
   operational starting target; group id, counts, and continuation preserve completeness beyond
   the cap. The target is neither a protocol limit nor an SLA.
6. **Skill + S58:** closed by WP-5.5's deployment-rendered skill and repeatable cold-agent
   acceptance harness.
7. **Historical remote hydration batching:** the Lance-era chunk confirmation
   battery used 256-ID batches. D94 removes that separate P1 confirmation hop;
   the measurement remains relevant only to P2/deep source hydration. A narrow
   indexed entity-id proxy was measured with eight concurrent clients; it is not the production
   claim join or a full hydration envelope, and the 25 ms network hop is an explicit model input,
   not a hosted-topology commitment. WP-7.2 keeps the bounded-round-trip invariant under portable
   injected-latency profiles; provider-specific tuning belongs outside the library (D60).
   A surface regression proves that the real confirmation path preserves order and honesty across
   batches. Backfill uses the separate batch resource class and is not governed by this cap.
8. **`resolve` context ranking:** current relation-adjacency count raises the planted S51
   top-1 result without hiding any ambiguous candidate. Accept at most eight de-duplicated focal
   ids and expose each candidate's hit count; the planted evidence does not justify a heavier
   ranker.

## References

Scenarios: `plan/analysis/retrieval_scenarios.md` (S1–S63, the coverage map, the boundary
list). Decisions: **D48–D51** (this design), D8, D9, D10, D13, D16, D22, D41, D43, D44
(`decisions.md`). Adjacent designs: `p2_graph_design.md` §6 (store roles, reranking),
`k_layers_design.md` §5 (reader-facing flags → §5 here; reads-never-trigger),
`e0_files_design.md` §2/§5 (mounts, media, raw), `postgres_schema_design.md` (spine indexes),
`observations_design.md` §5 (semantic property retrieval). Requirements: §Retrieval.
