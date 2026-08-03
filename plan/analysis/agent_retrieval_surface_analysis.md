# Agent retrieval surface — gap analysis

*2026-08-02. Analysis, non-binding. Evidence base: the completed v5 and v8
full LoCoMo publication runs (517/1540 → 1100/1540), their per-question
answer traces, the extraction decision ledgers of all twenty preserved
per-conversation stores, and the tool catalog as of `0ef5454`.*

## The question being decided

RememberStack's consumers are AI agents calling public recipe tools. The
question: **can an agent reach everything the system knows, through tools it
will actually use?** "Fullest potential" decomposes into two testable
properties:

1. **Coverage** — every data spine the engine maintains is reachable through
   at least one question-shaped tool.
2. **Ergonomics** — the common question intents are satisfiable within the
   call budget agents actually spend (measured: **2.2 calls per question**
   at reasoning-effort `none` in the v8 run — one retrieval, one answer).
   A capability that requires a 3-call plan effectively does not exist for
   a minimal-effort agent.

## What the two benchmark generations proved

- v5→v8's entire +583-question gain came from retrieval-surface work
  (hybrid claims+chunks nomination, `question_context`) plus answer-stage
  mechanics — the *stored memory barely changed*. Surface, not store, was
  the binding constraint.
- The agent used exactly one retrieval tool per question in >95% of traces,
  whichever tool the prompt named first. Conclusion: **the surface must
  make the right thing the one-call thing**; descriptions and composition
  do the routing, because minimal-effort planning will not.
- Remaining measured weaknesses: multi-hop 47.9% (graph tools never
  called), open-domain 32.3% (partly policy), residual Unknowns ~11 per
  conversation (reach), and judge-visible paraphrase losses.

## Coverage matrix (spine × access path), as of `0ef5454`

| Spine | Free-text semantic | Hybrid lexical (BM25+RRF) | By entity | By time | By structure/adjacency | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| Claims (testimony + valid-time) | ✓ `claims_verbatim` | ✓ `claims_hybrid_rrf` | **✗** | **✗** | **✗** (no neighbor/session read) | valid-time stamps exist since #158/#179 but are un-queryable |
| Source chunks (passages) | ✓ `chunks_hybrid_rrf` | ✓ | **✗** | ✗ | **✗** (no prev/next, no whole-doc) | gold answers measurably sit adjacent to hits (conv-26 qa/0083 class) |
| Combined question context | ✓ `question_context` (claims+chunks) | ✓ | ✗ | ✗ | ✗ | the one-call default; facts grain absent from it |
| Source documents | ✗ | ✗ | **✗** | ✗ | ✗ | no "documents about X" listing at all; MENTIONED_IN edges exist in P2, mentions table in PG |
| Entities (identity) | ~ `resolve_entity` (name only) | — | ✓ | ✓ `identity_as_of` | ✓ timeline | |
| Relations (typed current facts) | **✗** (P1 `search_facts` exists, unexposed) | ✗ | ✓ `relation_current` (must know subject) | ~ | ✓ `explain` (evidence) | the system's flagship grain is lookup-only |
| Observations (current attributes) | **✗** (same unexposed channel) | ✗ | ✓ `observation_current` | ✗ | — | |
| Graph (P2) | — | — | ✓ `graph_neighborhood`, `graph_path` | — | ✓ | 2-call minimum (resolve first) → never used by minimal-effort agents (0 calls in 3,076 v8 questions) |
| Change feed | — | — | ✓ `changed_since` | ✓ | — | |
| Compiled pages (K) | — | — | ✓ `pages_about` | — | ✓ | |
| Sessions/transcripts | ✗ | ✗ | ✗ | ✗ | **✗** (no session fetch) | transcript exists for identity only |
| Media segments (D65) | **✗** | ✗ | ✗ | ✗ | ✗ | binding retrieval design requires the spine; no recipe exposes it |
| Document citations (DOC_CROSSREF) | — | — | — | — | **✗** | `citation_path` primitive exists engine-side, absent from catalog |
| Mention records themselves | — | — | **✗** | ✗ | — | mentions used as internal joins only; no mention-transcript tool |

Reading the matrix: **the structured spines the system was built for —
entities, time, mentions, facts — are precisely the columns with the most
✗.** Free-text similarity got two generations of investment; the
"index-richer-than-the-query-surface" imbalance is the central finding.

## Ranked gaps, each with its evidence

1. **Time-windowed claim retrieval.** The #158 chain put honest valid-time
   on 33% of claims; no tool filters on it. Cat-2 sits at 72.9% with the
   needed data already stored. ("What happened in August 2023" retrieves by
   the word "August" appearing, or not at all.)
2. **Entity-anchored retrieval** (`claims_about`, `documents_about`). All
   claim search is similarity-only; the mentions/resolution spine is
   unused at query time. "What did John say about Tim's books" depends on
   embedding luck. Document listing per entity — the natural human/agent
   browse affordance — does not exist in any form.
3. **Facts-grain question retrieval.** No semantic path from a question to
   observations/relations, though the P1 facts channel exists in the
   adapter. Current-state questions — the product's raison d'être — go
   through testimony reconstruction instead of the distilled layer.
   Direction held by the operator (2026-08-02): add the facts path
   *without demoting claims*; derived facts must carry their supporting
   evidence (claims stay the accuracy anchor; observations/relations pass
   through extra derivation stages whose error modes we have observed).
4. **Multi-hop composition.** Graph tools exist and are never called: the
   2-call entry cost plus engine-language descriptions price them out of a
   2.2-call budget. Cat-1 47.9% vs 91%+ reported by systems that hand the
   answerer everything. Needs a one-call composed path whose every edge
   ships with hydrated supporting claims (an edge is a lead, never an
   answer).
5. **Adjacency reads.** No prev/next-chunk, no whole-session fetch. The
   measured qa/0083 class (answer in the neighboring sentence) recurs.
6. **Recall mechanics inside recipes**: no refill after D48 hydration
   drops (declared k under-delivered); dedup is exact-id only, so
   near-duplicate claims burn candidate slots (five copies of one fact
   observed occupying half a k=10 window).
7. **Absence questions** ("did X ever…"): `typed_absence` and `scan`
   primitives exist engine-side but no agent-facing recipe exposes them —
   the earlier claim "no affordance exists" was too broad (Codex review).
   Honest agents still cannot distinguish "No" from "Unknown" through the
   catalog. Deferred with its own cost design.
8. **Entity resolution is T0-only** (exact normalized alias match) while
   the binding retrieval design promises a T0–T3 ladder — every
   `entity: str` tool inherits this recall limit until the ladder design
   lands (recorded deferral in the design).
9. **Negative testimony has no retrieval path**: relations carry no
   polarity; "X does not work at Acme" survives only as claims — a
   recorded boundary, not a silent gap.
10. **Additional misses recorded by review**: fact as-of queries,
    object-side relation lookup, contradictions-stance retrieval,
    document-by-id fetch, pagination/envelope size budgets, K pages by
    non-entity keys, semantic entity discovery. All now carried in the
    design's deferral table or its catalog.

## Non-goals confirmed by this analysis

- No benchmark-specific logic anywhere in the surface (the v5→v8 gain was
  achieved without any; the score's credibility depends on it).
- No relaxation of D48 (nominate-then-confirm) or typed grains: every gap
  fix must return confirmed, typed, provenance-bearing records.
