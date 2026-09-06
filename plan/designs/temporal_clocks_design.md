# Temporal Clocks — world-time flows from the claim's window (Design)

**Status:** binding under D107, amended by D110

> **D110 amendment (2026-09-07).**
> [Temporal write and lifecycle design](temporal_write_and_lifecycle_design.md)
> closes the four §12 contracts: assertion-grain ordered relation writes,
> autonomous guarded corrections under D108, checked cache freshness and D74
> sanitized replay checkpoints. It also adds the `erased` endpoint basis and
> qualifies continuous-ingest seed ordering. Its explicit amendments take
> precedence over the original D107 revision history below.

**Date:** 2026-09-03 (sixth revision the same day, after five independent
Codex design reviews; §10 records the original withdrawn alternatives;
D110 now resolves the adjacent contracts in §12)

**Analysis:** `plan/analysis/time_handling_audit.md` (twenty-two findings at
`02b79904`, each cited by file and function)

**Adjacent contracts:** D110 now supplies the binding relation admission,
locked autonomous application, cache freshness and hard-forget contracts
listed in §12. Sequencing alone never establishes those contracts.

**Builds on:** D41 (claims carry an immutable, source-asserted validity
interval; a fact's window is the adjudicator's single recorded verdict, never
a reduction over claim columns, never reopened by a late retrospective),
D3/D4 (supersession over verdicts; the cheap-first cascade), D43
(observations; the no-cap rule for fixed-period figures), D88/D90
(continuous-ingest processing order and the late-arrival re-split), D106
(temporal compatibility in observation adjudication), D24 (append-only,
reversible review verdicts — amended here), D7/D12 (rebuild, generations,
idempotency), D55 (retraction), D49 (envelopes and fact identity).

> **Reading this cold.** RememberStack extracts **claims** (what a source
> said, immutable) and adjudicates them into **facts** — relations between
> two entities, observations about one entity — each with a validity window:
> the span over which the system believes the fact held. The extractor
> already resolves *when* a statement is about ("last week" said on
> 2022-10-06 → 2022-09-29) and stores it on the claim as its **D41 window**.
> This design says where that resolved time must flow, which clock each
> stage reasons on, how facts about *things that happened* differ from facts
> about *states that held*, and what happens when a time is unknown.

## 1. The problem, in one sentence

The engine resolves world-time once, at extraction, and then most later
stages — fact windows, supersession, ordering, deduplication, prompts,
summaries, filters, timelines, aggregates, retraction — reason on the
*source's own date* instead, or on no date at all. The audit lists
twenty-two instances; D106 fixed one. This design closes the class without
creating a second validity authority.

## 2. The three clocks and the two rules

| Clock | Where it lives | Meaning | Role |
| --- | --- | --- | --- |
| **said-on** | `claims.asserted_at` | when the source said it | provenance, shown beside every statement a model or reader judges; the total *processing* order (D90); never a window boundary |
| **is-about** | `claims.claim_valid_kind` / `claim_valid_from` / `claim_valid_until` / `claim_valid_precision` (D41) | the world-time the statement refers to, resolved from the wording against the said-on date | the source of every fact window, every succession decision, every dated label, every temporal prompt line |
| **believed** | `ingested_at` / `invalidated_at` | when the system held the belief | belief-time only; never a substitute for world-time |

`claim_valid_kind` says what sort of interval the is-about window is:
`event_time` (something happened), `effective_period` (a state held over a
span), `measurement_period` (a figure for a reporting span),
`proposition_validity` (the proposition held over a span). `NULL` means the
source tied nothing to a date. `claim_valid_precision` says how coarse the
resolved bounds are; a start with `claim_valid_until IS NULL` is **open**;
`instant` is a point (`until = from`, enforced by the schema).

- **Rule 1 — world-time comes from the is-about window.** Fact windows,
  temporal succession, dated labels, dedupe keys, and every temporal prompt
  read the is-about window. The said-on clock is displayed as provenance and
  orders processing; it is never a window boundary.
- **Rule 2 — a missing time stays missing.** An unknown is-about window
  leaves a fact's window endpoints `NULL` with basis `unknown`. Nothing is
  ever filled with `now()`, with the said-on date, or with belief-time. A
  fact whose bounds are unknown may still be *ended* by a successor that
  supplies a world-time instant (a dated resignation ends "is CEO" even when
  nobody said when the tenure began — D106); what an undated fact can never
  do is *supply* a boundary to another.

## 3. Two kinds of fact, two windows

Every fact (relation or observation) carries a **temporal kind** — its
*shape*, which is independent of whether its bounds are known — and two
windows with different authority:

| `temporal_kind` | seeded from claim kind | what the fact says | identity |
| --- | --- | --- | --- |
| `state` | `effective_period`, `proposition_validity` | something held over a span ("was CEO 2015–2018"; "is CEO" with no start given) | key + **verdict window** (disjoint slices per key; the relations `EXCLUDE`) — bounds may be unknown |
| `occurrence` | `event_time`, `measurement_period` | something happened, or a figure for a span ("won the final on 2022-11-05"; "FY2023 revenue was $5M") | key + an **adjudicated** occurrence identity (§4.2) — windows may overlap |
| `unknown` | `NULL` kind, and the ladder cannot tell shape from wording | the source tied nothing to a date and the statement does not say whether it is a state or an occurrence | key (+ statement wording for observations) |

Shape and datedness are different questions. A claim with no D41 window is
first classified by the normaliser's existing shape judgement (a state or
an occurrence, from the wording, the same call that routes a claim to a
relation or an observation); only when that judgement is itself unsure is
the fact's kind `unknown`. A `state` with unknown bounds is still a state:
it can be superseded and ended, and it is what most undated "X is Y"
testimony becomes.

| Window | Columns | Authority | Changes when |
| --- | --- | --- | --- |
| **verdict window** | `valid_from` / `valid_until` (existing), `valid_from_basis` / `valid_until_basis` (new, `NOT NULL DEFAULT 'unknown'`: `world_time`, `verdict`, `source_removed`, `legacy`, `unknown`, `erased`), `seed_claim_id` (new, nullable — `NULL` only for legacy facts whose creator is unrecoverable and after hard forget scrubs it) | the adjudicator's single recorded verdict (D3/D41/D43): over which span the fact is believed to have held | seeded **once** at insert from the seed claim (§4.1); afterwards only by a recorded verdict: a supersede cap (§4.4), an autonomous temporal correction or compensation (§4.3), a D74 sanitized checkpoint (D110 §6), a D55 retraction (§4.4), or a migration verdict (§9) — never automatically from evidence |
| **occurrence window** | `occurs_from` / `occurs_until` / `occurs_precision` (new, nullable) | **non-authoritative** derived metadata: the union of the canonical D41 windows of the fact's **attached** evidence — current or withdrawn testimony alike, so a D55 withdrawal never erases a historical fact's world-time; hard forget recomputes from surviving evidence | recomputed whenever evidence attaches or is forgotten; the typed guarded write transaction forbids this recompute from writing the verdict window |

Why two kinds and two windows: a measurement is *about* FY2023 but is
*believed* from the day it was reported onward and is never capped (D43's
no-cap rule); a past event is about one day but stays a believed fact
forever — and a later occurrence of the same key is a **different fact**,
not corroboration (D106); a state is about the same span it is believed to
have held and is what supersession caps. Occurrences therefore have an open
verdict window `[occurs_from, ∞)` that is *not* their identity; states have a
verdict window that *is*. D41's boundary holds: the verdict is single-valued
and recorded; the occurrence window is evidence-derived and cannot mutate
it.

## 4. Fact windows: seeding, matching, revising, closing

### 4.1 Seeding at insert (once, from the seed claim)

The **seed claim** is the claim whose processing created the fact row — the
first applied assertion for it in the closed admission order specified by D110 §3 — recorded
atomically with the row in `seed_claim_id` **and** as the
`triggering_claim_id` of the fact's `add` adjudication on both planes (today
only observation `add` rows carry one; relation `add` rows gain it). Windows
are the seed claim's **canonical bounds** (§5):

| seed claim kind | `temporal_kind` | `valid_from` | `valid_until` | bases | `occurs_*` |
| --- | --- | --- | --- | --- | --- |
| `effective_period`, `proposition_validity` | `state` | canonical start | canonical end (bounded span → a closed historical slice; open → `NULL`) | `world_time` / `world_time`, or `unknown` for an open end | same as the verdict window |
| `event_time`, `measurement_period` | `occurrence` | canonical start (believed from then on) | `NULL` — never capped (D43, extended to events) | `world_time` / `unknown` | the claim's canonical window |
| `NULL`, shape judged `state` | `state` | `NULL` | `NULL` | `unknown` / `unknown` | `NULL` |
| `NULL`, shape judged `occurrence` | `occurrence` | `NULL` | `NULL` | `unknown` / `unknown` | `NULL` |
| `NULL`, shape unsure | `unknown` | `NULL` | `NULL` | `unknown` / `unknown` | `NULL` |

The re-occurrence floor (a reopened state slice starts no earlier than the
prior closed slice's end) applies **only** when the seed's start is provably
later than that end — a chronological successor; an older spell discovered
late is inserted as its own closed slice (§4.2). The schema requires a
`state` with two known endpoints to be non-empty (`valid_until >
valid_from`); the existing checks that allow `valid_until = valid_from` are
replaced, because in half-open form that is an empty fact that the
`EXCLUDE` cannot see.

### 4.2 Matching: nomination as today, verdicts bounded by temporal relation

All matching participates in D110 §2's shared block/revision protocol.
Occurrence identity decisions serialize under the block lock and need no
occurrence exclusion. The state exclusion remains an additional invariant.

**Candidate nomination is unchanged in mechanism**: the entity/key block,
then the similarity-ranked residue (D43 §3) — never an overlap filter, so a
same-occurrence date dispute between two disjoint dated claims still reaches
the ladder, as D106 requires. What changes is the **two candidate sets** and
the **verdicts a temporal relation permits**:

- **same-kind candidates** — live facts of the same key and the same
  `temporal_kind`; eligible for `evidence`, `supersede` (states only),
  `contradict`, or `new`;
- **state-ending candidates** — for an incoming `occurrence` claim only: the
  non-invalidated `state` slices of the same key (for observations, the same
  entity) whose known interval does not end before the occurrence's
  canonical start — bounds unknown, open-ended, *or* finite-ended (a 2027
  resignation must reach "CEO 2025–2030", and a dated resignation must reach
  "is CEO" with no start given) — eligible **only** for `supersede` or
  `contradict` (§4.4), never for evidence. This is the cross-kind comparison
  D106's dated resignation needs.

Within the same-kind set, the temporal relation of the two canonical
windows bounds the verdict, as D106 does today (`supersede` rows apply to
states; occurrences are never superseded):

| relation | permitted verdicts |
| --- | --- |
| both dated, **disjoint** | `new`, or `contradict` when the ladder finds one occurrence with a disputed date (D106); for states, `supersede` only when the incoming start is later than the existing start (a later disjoint spell that ends the earlier one at the incoming start) — never `evidence` |
| both dated, **overlapping** | `evidence` (the same occurrence or state re-mentioned; `occurs_*` widens), `supersede` (a state's value changed; cap at the incoming start under the guard), `contradict`, `new` (a different occurrence that merely overlaps: two visits on one day; a day-precision and an instant-precision claim about different things) |
| **mixed** (one dated, one undated) | `supersede` (only when the *incoming* side is dated — it supplies the boundary), `contradict`, `new` — never `evidence` |
| both undated | `evidence`, `contradict`, `new` — never `supersede` (no boundary exists) |

Consequences by kind:

**States.** An overlapping same-property claim with the same value attaches
as evidence; a disjoint one seeds a new slice (disjoint ranges satisfy the
relations `EXCLUDE`, which applies `WHERE temporal_kind = 'state'`); an
overlapping different value is the supersede/contradict question (§4.4).

**Occurrences.** Identity is the ladder's verdict, not the window: there is
**no** occurrence exclusion constraint; overlapping occurrence rows of one
key are legal, a union expansion that comes to overlap a neighbouring
occurrence never merges rows, and D110's block lock plus the recorded verdict
prevent duplicate application. Acceptance covers same-key recurring events, coarse and
fine precision overlap judged `new`, a disjoint pair judged `contradict`, and
a union expansion bridging two existing occurrences.

**Undated claims.** A claim without a D41 window takes its shape from the
normaliser's judgement (§3) and then follows its kind's rules with unknown
bounds. Observations of kind `unknown`: identical wording collapses onto an
existing `unknown` row of the same key, otherwise a new row. Relations follow normalized shape. Only a state-shaped undated claim may use
an eligible unknown-bounds state slice; an occurrence-shaped claim follows
occurrence identity adjudication and is never forced into that state shortcut.
The exact application and idempotency contract is D110 §3.

**The relation write path becomes staged.** Today `upsert_relation` finds a
live triple and attaches evidence before the relation ladder runs, which
would collapse two same-triple occurrences before any verdict. Under this
design a normalised relation claim is **held unattached** (staged, as
observation claims already are) until, under the key's block lock, candidate
nomination and the verdict complete; `new` inserts the fact, its `add`
adjudication (with `triggering_claim_id`) and the evidence link in one
transaction; `evidence` attaches in one transaction; every identity verdict
is idempotent on `(assertion_id, adjudicator generation)` so a retry
or a concurrent D88 normaliser replays the recorded verdict rather than
deciding again. The relation ladder's "same object after redirects → exact
no-op" short-circuit applies only to compatible state-shaped assertions; missing dates do not
make an occurrence a state. D110 §3 defines the complete staging/receipt path.

**Relations schema.** The existing GiST `EXCLUDE` on `(subject, predicate,
object) && tstzrange(valid_from, valid_until)` becomes partial on
`temporal_kind = 'state'`, retaining the existing non-invalidated and
no-contradiction predicates. D110 further excludes intervals with an `erased`
endpoint basis and requires explicit uncertain-membership disclosure. Ordinary
unknown-bounds states remain unbounded ranges under it. Occurrences have no
interval exclusion.

### 4.3 Revising a verdict: autonomous recorded correction (D110/D108)

D110 §4 replaces the original human `temporal_window` queue/verdict mechanism.
New eligible evidence can trigger an explicit correction adjudication choosing
canonical endpoints backed by claim IDs. It never directly reduces evidence
minima/maxima into a verdict window. A completed uncertain result preserves the
existing endpoints and remains visible as uncertainty.

D110 §2 defines the common locks, complete revision revalidation and ordered
operation history. Its §4 defines ordinary monotonic corrections and the narrow
compensating exception that restores only components still owned by the reversed
operation, preserving intervening caps and belief-time invalidation. Existing
plane adjudication logs retain narrative authority; typed temporal operations
record application. No new human queue or second narrative verdict table exists.
Replay follows committed dependencies, not all caps followed by wall-clock
review order. D74 erasure uses D110 §6's independently verified sanitized roots.

### 4.4 Closing: temporal succession, separate from processing order

D90's deterministic **processing** order — `(asserted_at NULLS LAST,
claim_id, statement)` for observations — decides nothing about the world.
D110 qualifies this as ordering within a closed admitted set and exact replay
of recorded history; unseen future inputs cannot determine today's immutable
seed. Its assertion-level relation order and common operation sequence govern
relation application and interleaved corrections.

**Temporal succession** is the rule for capping a `state` slice. A supersede
verdict caps the predecessor at a **world-time instant supplied by the
successor**:

- a successor `state`'s verdict start (basis `world_time` or `verdict`); or
- an **ending occurrence** from the state-ending candidate set (§4.2) that
  the ladder judged `supersede`, at the occurrence's canonical start — so a
  dated resignation ends an "is CEO" state whose own start may be unknown,
  and a 2027 resignation shortens "CEO 2025–2030" to end in 2027.

**Chronological guard, applied to every cap source including D55**, stated
mechanically over the predecessor's verdict window:
`(valid_from IS NULL OR T > valid_from) AND (valid_until IS NULL OR T <
valid_until)`. A boundary that fails it is not a cap — the ladder's outcome
is recorded, the pair routes to `contradict` (both stand, grouped) when the
values conflict and coexists otherwise, and a temporal discrepancy
is recorded. When the successor supplies no world-time instant (an undated
successor), there is no succession: the pair **coexists** and the
adjudication row records `reason = "no world-time boundary -> coexist"`.
The cap's basis is `verdict`. Relation supersession and observation
supersession share this one rule. `now()` is never a boundary.

**Retraction (D55) by temporal kind, fail-closed.** A withdrawn `state`
whose withdrawing document version has a known source time is capped there
under the guard (basis `source_removed`). When that time is unknown, **or
the guard refuses it**, preserve the existing world-time end and basis
(including a finite earlier cap); an already unknown end stays unknown.
The belief interval closes regardless — `invalidated_at` set from the
**persisted reconciliation event's timestamp**, never from the database
clock, so a rebuild replays the same instant and a sole-support removal can
never leave a zombie fact that later activates; a refused boundary is
recorded as a disputed world-time endpoint for autonomous consideration. A withdrawn
`occurrence` or `unknown`-kind fact is never capped: it closes on belief-time
the same way. This is the per-shape judgement D55 asks for and
`close_observations` could not make while shape was semantic;
`temporal_kind` makes it mechanical.

**Residual, measured rather than assumed:** undated, differently worded
restatements of a changing state coexist as unknown-bounds rows instead of
capping. Identical wording collapses (§4.2), so growth comes only from
distinct undated wordings; the count of unknown-bounds rows per key is a
reported metric and the lever is extraction coverage of the D41 kinds (§6).

### 4.5 The D90 late-arrival re-split uses the world clock

D90 §5.5.3 re-materialises evidence attached to a capped observation when
that evidence lies *after* the cap. Under this design the cap `T` is a
world-time instant, so eligibility compares the attached **state** claim's
canonical occurrence start to `T` — not its `asserted_at`, which stays the
total work order only. Undated attached evidence is never re-split (it
coexists on the slice it attached to). The staggered acceptance case gains a
variant in which said-on order and is-about order are deliberately reversed
and the final slices must follow the world. The D90 design's §5.5.3 is
rewritten to this rule, including its implementation note.

### 4.6 Statements stay canonical; dated labels are derived

The observation `statement` remains the canonical source-faithful wording
(D43; unchanged on collapse and supersession). The human-facing **label** —
`obs_label`, `FactResult.label`, the profile line, the K fact-sheet cell — is
derived deterministically from `statement` plus `occurs_*` and
`occurs_precision` ("Nate won a video game tournament last week — on
2022-09-29"), refreshed whenever the occurrence window recomputes. Nothing
source-specific enters identity; claims of different precision that collapse
yield one label from the union window at the coarser precision.

## 5. Canonical bounds: precision honoured once, consistently

Claim storage is unchanged (inclusive bounds; `instant` as `until = from`;
`open` as a `NULL` end; the schema `CHECK`s as they are). One function,
`canonical_bounds(from, until, precision) → [start, end)`, converts a stored
claim window to a **half-open** interval, aligning **both** ends to the
precision unit in UTC (D41 bounds are timezone-aware; the calendar is the
proleptic Gregorian calendar PostgreSQL's `date_trunc` uses):

| `claim_valid_precision` | canonical `[start, end)` |
| --- | --- |
| `day` / `month` / `quarter` / `year` | `[date_trunc(unit, from), date_trunc(unit, until) + unit)` — a year stored 2022-01-01…2022-12-31 becomes `[2022-01-01, 2023-01-01)`; a day whose stored start is noon on 2023-05-07 still becomes `[05-07 00:00, 05-08 00:00)` |
| `instant` | `[t, t + 1 µs)` — a non-empty point |
| `open` | `[date_trunc(day, from), NULL)` when the source gave a date, `[from, NULL)` when it gave an instant; unbounded end |
| `unknown` | no interval; never matches a window predicate and is disclosed as excluded |

The overlap predicate is `a.start < b.end AND b.start < a.end` with `NULL`
ends as +∞. A caller's inclusive request `claims_as_of(from, to)` converts
to `[from, to + 1 µs)`, so `from == to` is a point-in-time query, not an
empty one.

**Where it runs.** The engine's own SQL calls two IMMUTABLE public-schema
functions, `claim_canonical_start(from, precision)` and
`claim_canonical_end(from, until, precision)`, backed by an expression index
so the as-of scan stays indexed; the query space exposes the same
canonicalisation as `memory_v1.canonical_bounds` with a companion
`claims_canonical` view exposing `canon_start`/`canon_end` beside the raw
columns, so saved examples, open SQL, the catalog metadata and the
open-query prose use it too; the Python side (`core/temporal.py`) mirrors
the same table and a test pins the twins equal. **Fact windows are stored canonical** —
`valid_from`/`valid_until` and `occurs_from`/`occurs_until` are written from
`canonical_bounds` at seeding, so every fact predicate the engine already
has (`valid_until > :as_of`, `tstzrange(valid_from, valid_until)` in the
`EXCLUDE`, the P1 `FactTime` selectors) is correct without a precision
column; `occurs_precision` is kept for labels only. **Claim comparisons
canonicalise at query time** — `claims_as_of`, D106's `_windows_disjoint`,
§4.2 matching, the §7 dedupe key and filters. Acceptance covers adjacent
boundaries, equality queries, non-aligned stored inputs, and existing claim
rows.

## 6. Extraction: vocabulary and anchor

The extractor prompt defines and exemplifies **all four kinds** and `open`
("has been CEO since 2019" → `proposition_validity`, `open`; "FY2023 revenue
was $5M" → `measurement_period`, `year`; "worked at Acme from 2015 to 2018"
→ `effective_period`, `year`), and the structured-output model describes
each field. The document header shown to the extractor carries the **full
source timestamp**, so offset expressions ("three hours ago") resolve to
instants and two same-day sources are distinct anchors. Expressions the
precision enum cannot represent ("this morning" has no part-of-day
precision) remain unresolved by design — a documented expressivity boundary,
not a defect of the anchor.

## 7. Every temporal judgement sees both clocks; every surface names them

### 7.1 The current-fact predicate and activation/expiry

"Current" is one predicate — the interval containment `memory_v1.facts_current`
and `fact_context` already use — evaluated at an explicit instant `E`:

```
ingested_at <= E AND invalidated_at IS NULL
AND (valid_from IS NULL OR valid_from <= E)
AND (valid_until IS NULL OR valid_until > E)
AND valid_from_basis <> 'erased' AND valid_until_basis <> 'erased'
```

A fact whose verdict start lies in the future is not yet current; a `state`
with a finite future end is current until it passes; occurrences and undated
facts are current from their start (or always, when undated). **Every**
current fact read uses this predicate with `E` fixed per evaluation — the
consumers that today test `valid_until IS NULL` (entity profiles,
source-removal eligibility, K routing) *and* the ones that today test only
`invalidated_at` (the `aggregate` count/group queries and the
predicate-absence query, which would otherwise keep counting an expired
relation and block a true absence answer; D49 requires fact-grain answers to
be validity-filtered).

D110 §6 distinguishes membership uncertainty caused by an erased boundary
from ordinary unknown source timing. Counts and absence disclose uncertain
rows instead of presenting their exclusion as complete knowledge.

D110 §5 owns cache correctness: complete pre-top-k/future candidate and routing
dependencies, checked revision/deadline certificates, event identities and
scheduling through the existing work ledger, current-E downtime catch-up, and
publication revalidation. A timer alone cannot certify already-rendered text.
Readers omit stale generated text/vectors and fall back to current facts.

### 7.2 Prompts

The two-clock block D106 introduced —

```
EXISTING: …
  said on: <date or unknown>
  is about: <canonical window and kind, or "no specific time given">
NEW: …
  said on: …
  is about: …
```

with its definitions — is the contract for **every** prompt that compares,
merges, ranks, or summarises facts: relation supersession (its evidence
laterals select the D41 columns), the T4 identity candidate list (each
salient fact with its occurrence window and kind), the K prose writer's
claim bundle (each claim with said-on and is-about), and the benchmark
answer agent, which additionally names the envelope fields (`asserted_at`,
`claim_valid_*`, `validity` with bases, `occurrence`, `temporal_kind`).

### 7.3 Envelopes and versions (D49, additive fields; rolled versions)

- `EvidenceResult` gains `grouped_members` — one `(claim_id, asserted_at,
  claim_valid_kind, claim_valid_from, claim_valid_until,
  claim_valid_precision)` record per grouped claim — so grouping never hides
  a member's times.
- The fact-grain `Validity` gains `valid_from_basis`, `valid_until_basis`,
  `temporal_kind`, and `occurrence` (`from`/`until`/`precision`); the same
  fields are added to `GraphEdge`'s flat validity, the K-layer fact model,
  and the `memory_v1` fact views.
- Because every envelope-returning operation derives its result schema from
  the shared `Envelope` schema, all of them roll: `resolve_entity@2`,
  `testimony_context@2`, `fact_context@3`, `answer_context@3`; the surface
  manifest hash, the query-space manifest, the generated OpenAPI/SDK
  artifacts, and the benchmark protocol roll with them.
- The open-query confirmation surface (`query_sandbox/nomination.py`) returns
  the complete D41 tuple for claim rows and bases/occurrence/kind for fact
  rows; the shipped `claims_as_of` saved example counts `unknown` precision
  by precision alone (its current bound-based count can never be non-zero
  under D41's checks) and reads through `claims_canonical`.

### 7.4 Dedupe, filters, timeline, profiles

- **Deduplication** keys on `(normalised text, claim_valid_kind,
  canonical start, canonical end, claim_valid_precision)` when the window is
  known and on `(normalised text, asserted_at)` when it is not.
- **Filters.** P1 claim search gains `valid_from` / `valid_until` (is-about,
  canonical overlap) beside the said-on `asserted_from` / `asserted_to`; P1
  fact search's `FactTime` selector gains an `occurs` mode over `occurs_*`.
- **Timeline.** `aggregate(form="timeline")` buckets facts by `occurs_from`,
  then `valid_from`, and emits an explicit `undated` bucket; it never
  substitutes `ingested_at`.
- **Profiles.** Salient facts rank by evidence, then by occurrence recency;
  never by `updated_at`; each carries its occurrence window and kind into the
  T4 prompt.

### 7.5 Consumer surfaces

The K fact sheet prints `valid from` / `valid until` from the verdict window
only where the basis is `world_time` or `verdict`, prints `about` from the
occurrence window, `legacy` rows under a `legacy` heading, and `—` for
`unknown`; "Observation history" sorts by `occurs_from`. The consumption
skill states the three clocks, the two fact kinds, and defines
`claims_as_of` over source world-time (D41), not system time.

## 8. Generations and protocol

- Generations: the extractor (kinds, header), the normaliser (kind, seeding,
  the staged relation write), both adjudicators (matching by kind,
  succession, re-split, D55 by kind), the `adjudicate_observations` flush
  component, P1 labels and selection, K page generation, and the query space
  (`canonical_bounds`, `claims_canonical`).
- Versions: the assured operations of §7.3, the surface and query-space
  manifests, OpenAPI/SDK, and the LoCoMo protocol. **Any package whose
  observable result semantics change rolls the protocol**, including ones
  whose JSON shape does not; packages released together roll it once. Scores
  across a roll are directional.

## 9. Cutover: in-place conversion with recorded migration verdicts

A store never mixes pre- and post-D107 fact semantics, and conversion never
replaces a fact row — fact ids are provenance handles referenced by
evidence, review payloads and K citations, and D55 keeps historical facts
with no current support, which a replay from current testimony would erase.
Conversion is a **recorded migration adjudication** per fact (outcome
`migrate`, with the legacy and converted bounds in its features), so what it
did is auditable like any verdict.

1. **stop** intake and **drain** in-flight units (the D106 contract lets
   pre-roll observation units complete under their own generation);
2. **migrate** — add the new columns (`temporal_kind`, the two bases,
   `occurs_from`/`occurs_until`/`occurs_precision`, `seed_claim_id`),
   the non-empty `state` check, the partial `EXCLUDE`, the
   D110 operation/admission/cache/checkpoint stores and `erased` basis,
   the `adjudication_outcome` value `migrate`
   and `adjudication_method` value `migration`, and the `memory_v1`
   canonicalisation function and view (`relation_adjudications.triggering_claim_id`
   already exists and is populated from now on; `postgres_schema_design.md`
   receives the matching D107 amendment with the full DDL);
3. **convert in place**, per fact, idempotently and in batches, shadow-first
   (computed into staging columns, validated against §4.1 and §4.3
   invariants, swapped in one transaction per batch, resumable from the last
   validated batch):
   - **seed** — an observation's seed is the `triggering_claim_id` of its
     recorded `add` adjudication (present today except where hard forget
     scrubbed it); a **relation's creator was never recorded**, so its seed
     is *not* recovered: `seed_claim_id` stays `NULL`, the verdict window's
     bases become `legacy` for retained known bounds (unknown remains unknown),
     and the fact's `temporal_kind` is derived from
     the kinds of its attached evidence when they agree (all state, all
     occurrence, all undated) and set to `unknown` otherwise — every such
     choice recorded in the migration adjudication; the same `legacy` path
     applies to any observation whose seed was scrubbed;
   - **windows** — with a recovered seed, `temporal_kind` and the verdict
     window follow §4.1 from that seed's canonical bounds; without one, the
     legacy `valid_from` is kept with basis `legacy`; `occurs_*` is the union
     over attached evidence in every case;
   - **legacy caps are not kept as they are**, because they are the said-on
     and `now()` boundaries this decision removes: a legacy supersede cap
     whose successor fact is recorded (the adjudication's related fact) is
     recomputed at that successor's canonical world-time start when it has
     one, basis `verdict`; a legacy cap on a row converted to `occurrence`
     is removed (no-cap rule) with the cap moved to the belief interval only
     if the row was withdrawn; a D55 fallback cap (the withdrawing version's
     time unknown) becomes belief-time invalidation at the persisted
     reconciliation instant with `valid_until` set to `NULL`, basis
     `unknown`; a cap that
     cannot be recomputed (successor unknown or undated) is set to `NULL`,
     basis `unknown`, with a `legacy_unknown_boundary` diagnostic, and a cap the
     chronological guard would refuse is likewise recorded as uncertainty;
4. **readiness** reports the fact-layer generation and the count of open
   `legacy_unknown_boundary` items; consumers refuse to serve a store whose
   fact generation predates D107. D110 distinguishes completed explicit
   legacy/unknown uncertainty from an incomplete conversion or replay conflict;
   only the latter blocks promotion. No human queue acceptance is required;
5. **rollback** is restore of the pre-migration backup, as for any
   generation roll; all accepted forget manifests must be honored before
   restored serving resumes (D110 §6).

Conversion does not re-adjudicate identity decisions made under the old
rules: two occurrences a pre-D106 adjudicator merged stay one row until an
operator chooses a fresh rebuild (D7), which is a re-ingest, not a migration.
Only §6's added kinds benefit from re-extraction.

## 10. Alternatives considered

- **Seed `valid_from` from the said-on date when the window is unknown**
  (revision 1). Withdrawn: provenance is not validity (Rule 2).
- **Seed a measurement's or event's `valid_until` from its claim**
  (revision 1). Withdrawn: D43 never caps a fixed-period figure.
- **Match every fact by verdict-window overlap** (revision 2) and **an
  occurrence exclusion constraint** (revision 3). Withdrawn: an occurrence's
  open verdict window overlaps every later occurrence; two distinct
  occurrences can legitimately overlap.
- **Overlap as the occurrence candidate filter** (revision 4). Withdrawn: it
  removed D106's disjoint-date `contradict` path; nomination stays as today
  and the temporal relation bounds the verdict.
- **Automatic `extend_start` to the earliest evidenced start** (revision 2).
  Withdrawn: `min()` over claim columns is the reduction D41 forbids.
- **A replay-based rebuild from current testimony** (revision 3) and
  **exact seed recovery for every legacy fact** (revision 4). Withdrawn:
  replay erases D55 history and re-picks seeds; relation creators were never
  recorded, so legacy relations convert under an explicit `legacy` basis
  with migration verdicts.
- **Keeping legacy cap boundaries through conversion** (revision 4).
  Withdrawn: they are the said-on/`now()` values D107 removes; recoverable
  caps are recomputed, the rest become belief-time closes or review items.
- **A current predicate without a lower bound** (revision 4). Withdrawn: it
  made a 2030 fact current in 2026 and regressed `facts_current`; the full
  containment predicate is used and activation is scheduled too.
- **State-ending candidates limited to open states** (revision 4).
  Withdrawn: an ending event could not shorten a finite-ended state.
- **Universal half-open claim storage** (revision 1) and **"+ one unit" ends
  with raw starts** (revisions 2–3). Withdrawn: both ends are truncated to
  the unit, in SQL and Python alike.
- **One coalesced key for processing order and succession** (revision 1).
  Withdrawn: D90 needs a total processing order.
- **Succession only between two dated states** (revision 2). Withdrawn: it
  lost D106's ending-event case.
- **Closing a retracted state on belief-time labelled `source_removed`**
  (revision 3). Withdrawn: belief-time is not world-time.
- **An `undated` temporal kind that conflates shape with datedness**
  (revisions 3–5). Withdrawn: it made "is CEO" with no start uncappable and
  so lost D106's ending-event case again; shape and datedness are separate,
  and only an undated *successor* is barred from supplying a boundary.
- **Cap at the successor's said-on date** (revision 1); **bake the resolved
  date into the observation statement** (revision 1); **twenty-two local
  patches**; **keep `now()` as the undated cap**. Withdrawn for the reasons
  above.

## 11. Non-goals (scope boundaries)

- Recurrence ("every Q4"), anchor-relative time ("as of the merger"), and
  part-of-day expressions ("this morning") remain outside the single-interval
  precision model (D41).
- Belief-time semantics are unchanged.
- No new authority: the claim window stays immutable evidence; the verdict
  window stays the one adjudicated home; the occurrence window is derived
  metadata and is documented as such wherever it is shown.

## 12. Adjacent contracts resolved by D110

The original D107 left four consequential contracts open. They are now
specified in `temporal_write_and_lifecycle_design.md`, supported by independent
analyses and its complete PostgreSQL schema appendix:

| Spike | Binding answer |
| --- | --- |
| #365 relation ordering | D110 §§2–3: complete normalization answers, assertion-grain idempotency, closed admission, ordered guarded apply and honest continuous-ingest replay guarantee |
| #366 correction application | D110 §§2, 4: autonomous grounded correction, complete revalidation, component-owned compensation and committed dependency replay |
| #367 cached artifacts | D110 §5: complete future-candidate/routing dependencies, certified text/vector reads, one existing scheduler and late-work fallback |
| #368 hard forget | D110 §6: complete new-field inventory, independent-support classification, erased endpoint basis and sanitized block checkpoint/replay |

T.1 implementation requires this amendment on main, the full schema and the
acceptance evidence in D110 §8. Design acceptance does not itself mean code,
conversion or release is complete. Tracking remains program #364 and
`plan/plans/temporal_clocks.md`.

## References

Decisions: D107 (this design), D41, D3, D4, D43, D88, D90, D106, D24, D7,
D12, D55, D49, D74. Analysis: `plan/analysis/time_handling_audit.md`. Sequencing:
`plan/plans/temporal_clocks.md`. Affected designs carry a D107 amendment
banner: `e2_e3_claims_relations_design.md`, `observations_design.md`,
`registries_design.md`, `retrieval_design.md`, `k_layers_design.md`,
`locomo_benchmark_design.md`, `e3_entity_obs_flush_fanout_design.md`.
