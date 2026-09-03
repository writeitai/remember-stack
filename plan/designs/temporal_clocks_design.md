# Temporal Clocks — world-time flows from the claim's window (Design)

**Status:** binding under D107

**Date:** 2026-09-03 (fourth revision the same day, after three independent
Codex design reviews; §11 records what each round withdrew)

**Analysis:** `plan/analysis/time_handling_audit.md` (twenty-one findings at
`02b79904`, each cited by file and function)

**Builds on:** D41 (claims carry an immutable, source-asserted validity
interval; a fact's window is the adjudicator's single recorded verdict, never
a reduction over claim columns, never reopened by a late retrospective),
D3/D4 (supersession over verdicts; the cheap-first cascade), D43
(observations; the no-cap rule for fixed-period figures), D88/D90
(continuous-ingest processing order and the late-arrival re-split), D106
(temporal compatibility in observation adjudication), D24 (append-only,
reversible review verdicts — amended here), D7/D12 (rebuild, generations,
idempotency), D55 (retraction), D49 (envelopes).

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
summaries, filters, timelines, aggregates — reason on the *source's own
date* instead, or on no date at all. The audit lists twenty-one instances;
D106 fixed one. This design closes the class without creating a second
validity authority.

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
  leaves a fact's windows `NULL` with basis `unknown`. Nothing is ever filled
  with `now()`, with the said-on date, or with belief-time; an undated fact
  never caps another and is never capped; it coexists, recorded.

## 3. Two kinds of fact, two windows

Every fact (relation or observation) carries a **temporal kind** and two
windows with different authority:

| `temporal_kind` | seeded from claim kind | what the fact says | identity |
| --- | --- | --- | --- |
| `state` | `effective_period`, `proposition_validity` | something held over a span ("was CEO 2015–2018") | key + **verdict window** (disjoint slices per key; the relations `EXCLUDE`) |
| `occurrence` | `event_time`, `measurement_period` | something happened, or a figure for a span ("won the final on 2022-11-05"; "FY2023 revenue was $5M") | key + an **adjudicated** occurrence identity (§4.2) — windows may overlap |
| `undated` | `NULL` | the source tied nothing to a date | key (+ statement wording for observations) |

| Window | Columns | Authority | Changes when |
| --- | --- | --- | --- |
| **verdict window** | `valid_from` / `valid_until` (existing), `valid_from_basis` / `valid_until_basis` (new, `NOT NULL DEFAULT 'unknown'`: `world_time`, `verdict`, `source_removed`, `unknown`), `seed_claim_id` (new) | the adjudicator's single recorded verdict (D3/D41/D43): over which span the fact is believed to have held | seeded **once** at insert from the seed claim (§4.1); afterwards only by a recorded verdict: a supersede cap (§4.4), a `temporal_window` review verdict (§4.3), a D55 retraction — never automatically |
| **occurrence window** | `occurs_from` / `occurs_until` / `occurs_precision` (new, nullable) | **non-authoritative** derived metadata: the union of the canonical D41 windows of the fact's current-testimony evidence — the aggregate D106 already computes at block time | recomputed whenever evidence attaches or is withdrawn (the recount path); a database check forbids it from writing the verdict window |

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
first claim for it in D90 processing order in a fresh ingest — recorded in
`seed_claim_id` and in the fact's `add` adjudication row (its
`triggering_claim_id`, which both fact planes already write). Windows are
the seed claim's **canonical bounds** (§5):

| seed claim kind | `temporal_kind` | `valid_from` | `valid_until` | bases | `occurs_*` |
| --- | --- | --- | --- | --- | --- |
| `effective_period`, `proposition_validity` | `state` | canonical start | canonical end (bounded span → a closed historical slice; open → `NULL`) | `world_time` / `world_time`, or `unknown` for an open end | same as the verdict window |
| `event_time`, `measurement_period` | `occurrence` | canonical start (believed from then on) | `NULL` — never capped (D43, extended to events) | `world_time` / `unknown` | the claim's canonical window |
| `NULL` | `undated` | `NULL` | `NULL` | `unknown` / `unknown` | `NULL` |

The re-occurrence floor (a reopened state slice starts no earlier than the
prior closed slice's end) applies **only** when the seed's start is provably
later than that end — a chronological successor; an older spell discovered
late is inserted as its own closed slice (§4.2).

### 4.2 Matching: two candidate sets, then the ladder

All matching for one key runs under the per-key advisory lock the
observation path already takes, so identity decisions are serialised and no
database exclusion is needed to keep them consistent. After the entity/key
block (unchanged), an incoming claim is compared against **two candidate
sets**:

- **same-kind candidates** — live facts of the same key and the same
  `temporal_kind`, eligible for evidence attachment (`evidence`), for
  `contradict`, or for `new`;
- **state-ending candidates** — for an incoming `occurrence` claim only: the
  open `state` slices of the same key (or, for observations, the same
  entity), eligible **only** for `supersede` or `contradict` (§4.4), never
  for evidence. This is the cross-kind comparison D106's dated resignation
  needs to end an "is CEO" state.

Within the same-kind set, by kind:

**States.** The incoming canonical window overlapping a live `state` slice's
verdict window → evidence attaches; the verdict is untouched; `occurs_*`
recomputes. No overlap → a **new slice** (a distinct spell; disjoint ranges
satisfy the relations `EXCLUDE`, which applies `WHERE temporal_kind =
'state'`). An overlap with a *different value* of the same property is the
supersede/contradict question the ladder answers (§4.4).

**Occurrences.** Overlap of canonical windows is the *candidate filter*, not
identity: the D4/D106 ladder decides, with both clocks shown (§7.2),
`evidence` (the same occurrence re-mentioned; `occurs_*` widens),
`contradict` (the same occurrence, disputed detail; both stand, grouped), or
`new` (a different occurrence that merely overlaps — two visits on one day,
a day-precision and an instant-precision claim about different things).
Disjoint windows are always `new`. There is **no** occurrence exclusion
constraint: overlapping occurrence rows of one key are legal and expected,
a union expansion that comes to overlap a neighbouring occurrence never
merges rows, and the advisory lock plus the recorded verdict are what
prevent duplicates. Acceptance covers same-key recurring events, coarse and
fine precision overlap judged `new`, and a union expansion bridging two
existing occurrences.

**Undated claims.** Observations follow D106's mixed rule — an undated
statement never attaches to a dated occurrence and a dated event never
attaches to an undated statement; identical wording collapses onto an
existing `undated` row of the same key, otherwise a new `undated` row.
Relations: the triple *is* the content, so an undated claim attaches to the
single open `state` slice if exactly one exists, else to the single `undated`
row for the key (creating it once); it never attaches to an `occurrence` and
never creates a second unbounded slice.

**Relations schema.** The existing GiST `EXCLUDE` on `(subject, predicate,
object) && tstzrange(valid_from, valid_until)` becomes partial on
`temporal_kind = 'state'`; `undated` relations get a unique key on the
triple; occurrence relations have no constraint (adjudicated identity under
the lock, as observations always have).

### 4.3 Revising a verdict: a `temporal_window` review verdict (amends D24)

No automatic path changes a verdict window after seeding. A discrepancy —
attached evidence whose occurrence start precedes a `state` slice's verdict
start, or dated evidence attaching to a slice with an unknown window — is
surfaced in the envelope (`occurs_from < valid_from`; bases `unknown` beside
a dated `occurrence`) and raised as a review item of a new kind,
`temporal_window`, in the D24 queue. Its verdict record is append-only,
reversible, and provenance-stamped like every D24 action:

| field | meaning |
| --- | --- |
| `target_fact_id`, `temporal_kind` | the fact whose verdict window is revised (stable identity) |
| `seed_claim_id` | the seed as recorded at the time of the verdict |
| `old_valid_from`/`old_valid_until` with bases; `new_valid_from`/`new_valid_until` with bases | canonical bounds before and after; a new endpoint's basis is `verdict` |
| `rationale`, `reviewer`, `decided_at` | provenance |
| `reverses_verdict_id` | set on a reversal; a reversal restores the prior bounds and bases |
| `invariants` | checked before apply: the start may move earlier, never later; no endpoint moves past a neighbouring slice's bound; a closed end is never reopened (D41); a `state`'s end, when set, is later than its start |

Verdicts apply in `decided_at` order after caps and retractions on the
same fact, are idempotent by `verdict_id`, and are replayed in that order on
rebuild (D7). Anything the invariants refuse stays a review item.

### 4.4 Closing: temporal succession, separate from processing order

D90's deterministic **processing** order — `(asserted_at NULLS LAST,
claim_id, statement)` — is unchanged; it makes replay total and reproducible
and decides nothing about the world.

**Temporal succession** is the rule for capping a `state` slice. A supersede
verdict caps the predecessor at a **world-time instant supplied by the
successor**:

- a successor `state`'s verdict start (basis `world_time` or `verdict`); or
- an **ending occurrence** from the state-ending candidate set (§4.2) that
  the ladder judged `supersede`, at the occurrence's canonical start — so a
  dated resignation ends an "is CEO" state whose own start may be unknown.

**Chronological guard:** the boundary must be later than the predecessor's
known verdict start; a boundary at or before a known start is not a cap —
the ladder's outcome is recorded and the pair routes to `contradict`
(both stand, grouped) when the values conflict, otherwise coexists, and in
either case a `temporal_window` review item is raised. The predecessor's own
start basis is otherwise irrelevant. When the successor supplies no
world-time instant (an undated successor), there is no succession: the pair
**coexists** and the adjudication row records `reason = "no world-time
boundary -> coexist"`. The cap's basis is `verdict`. Relation supersession
and observation supersession share this one rule. `now()` is never a
boundary.

**Retraction (D55).** The withdrawing document version's source time is a
*source action*: when known, it caps the state with basis `source_removed`.
When it is unknown, the world-time end stays `unknown` and only the belief
interval closes — `invalidated_at` set from the **persisted reconciliation
event's timestamp**, never from the database clock — so a rebuild replays the
same instant and no world-time endpoint is ever labelled with a basis the
source did not supply.

**Residual, measured rather than assumed:** undated, differently worded
restatements of a changing state coexist as `undated` rows instead of
capping. Identical wording collapses (§4.2), so growth comes only from
distinct undated wordings; the count of `undated` rows per key is a reported
metric and the lever is extraction coverage of the D41 kinds (§6).

### 4.5 The D90 late-arrival re-split uses the world clock

D90 §5.5.3 re-materialises evidence attached to a capped observation when
that evidence lies *after* the cap. Under this design the cap `T` is a
world-time instant, so eligibility compares the attached **state** claim's
canonical occurrence start to `T` — not its `asserted_at`, which stays the
total work order only. Undated attached evidence is never re-split (it
coexists on the slice it attached to). The staggered acceptance case gains a
variant in which said-on order and is-about order are deliberately reversed
and the final slices must follow the world. The D90 design's §5.5.3 is
rewritten to this rule.

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

**Where it runs.** The function ships as one immutable SQL function in the
`memory_v1` query space (`memory_v1.canonical_bounds`) with a companion
`claims_canonical` view exposing `canon_start`/`canon_end` beside the raw
columns, so saved examples, open SQL, the catalog metadata and the
open-query prose use the same canonicalisation as the engine; the Python
side calls the same definition. **Fact windows are stored canonical** —
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

### 7.1 The current-fact predicate and expiry

"Current" is one predicate evaluated at an explicit instant `E`:
`invalidated_at IS NULL AND (valid_until IS NULL OR valid_until > E)`.
Occurrences and undated facts are always current (open verdict). A `state`
with a finite **future** end is current until it passes. **Every** current
fact read uses this predicate with `E` fixed per evaluation — the consumers
that today test `valid_until IS NULL` (entity profiles, source-removal
eligibility, K routing, `facts_current`) *and* the ones that today test
only `invalidated_at` (the `aggregate` count/group queries and the
predicate-absence query, which would otherwise keep counting an expired
relation and block a true absence answer; D49 requires fact-grain answers to
be validity-filtered).

Cached artifacts (profiles, K pages) carry the earliest future
`valid_until` among their inputs as an explicit **expiry instant** in their
input hash, and a durable, indexed `fact_expiry_schedule` (keyed by the
artifact, the endpoint instant, and the generation; D12 idempotent) queues
regeneration when that instant passes, with the boundary instant as the
evaluation time `E`; restart catches up from the schedule. The read-time
predicate remains the correctness backstop, so a late sweep can delay a
refresh but never serve an expired fact as current.

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
occurrence window, and `—` for `unknown`; "Observation history" sorts by
`occurs_from`. The consumption skill states the three clocks, the two fact
kinds, and defines `claims_as_of` over source world-time (D41), not system
time.

## 8. Generations and protocol

- Generations: the extractor (kinds, header), the normaliser (kind and
  seeding), both adjudicators (matching by kind, succession, re-split), the
  `adjudicate_observations` flush component, P1 labels and selection, K page
  generation, and the query space (`canonical_bounds`, `claims_canonical`).
- Versions: the assured operations of §7.3, the surface and query-space
  manifests, OpenAPI/SDK, and the LoCoMo protocol. **Any package whose
  observable result semantics change rolls the protocol**, including ones
  whose JSON shape does not (canonical comparison changes `claims_as_of`
  results; labels, P1 selection and profile ranking change answer inputs);
  packages released together roll it once. Scores across a roll are
  directional.

## 9. Cutover: in-place conversion that preserves identity and history

A store never mixes pre- and post-D107 fact semantics, and conversion never
replaces a fact row — fact ids are provenance handles referenced by
evidence, review payloads and K citations, and D55 keeps historical facts
with no current support, which a replay from current testimony would erase.

1. **stop** intake and **drain** in-flight units (the D106 contract lets
   pre-roll observation units complete under their own generation);
2. **migrate** — add the new columns (`temporal_kind`, the two bases,
   `occurs_from`/`occurs_until`/`occurs_precision`, `seed_claim_id`), make
   the relations `EXCLUDE` partial on `state`, add the `undated` unique key,
   the `fact_expiry_schedule` table, the `temporal_window` review kind, and
   the `memory_v1` canonicalisation function and view;
3. **convert in place**, per fact, idempotently and in batches: `seed_claim_id`
   := the `triggering_claim_id` of the fact's recorded `add` adjudication
   (present on both planes, so the seed is the claim that actually created
   the row — history-preserving, not a re-pick); `temporal_kind` and the
   verdict window := from that seed claim's kind and canonical bounds by
   §4.1, keeping any existing cap end (an existing `valid_until` set by a
   recorded supersede or retraction keeps basis `verdict` / `source_removed`,
   and a cap that the chronological guard would refuse is flagged for
   review rather than dropped); `occurs_*` := the union over current
   evidence; bases as derived. Conversion runs shadow-first (computed into
   staging columns, validated against the invariants of §4.3, then swapped in
   one transaction per batch) and resumes after a crash from the last
   validated batch;
4. **readiness** reports the fact-layer generation; consumers refuse to
   serve a store whose fact generation predates D107;
5. **rollback** is restore of the pre-migration backup, as for any
   generation roll.

Conversion does not re-adjudicate identity decisions made under the old
rules: two occurrences a pre-D106 adjudicator merged stay one row until an
operator chooses a full rebuild of a fresh store (D7), which is a re-ingest,
not a migration. Only §6's added kinds benefit from re-extraction.

## 10. Alternatives considered

- **Seed `valid_from` from the said-on date when the window is unknown**
  (revision 1). Withdrawn: provenance is not validity (Rule 2).
- **Seed a measurement's or event's `valid_until` from its claim**
  (revision 1). Withdrawn: D43 never caps a fixed-period figure; the period
  belongs to the occurrence window.
- **Match every fact by verdict-window overlap** (revision 2). Withdrawn: an
  occurrence's open verdict window overlaps every later occurrence.
- **An occurrence exclusion constraint on `occurs_*`** (revision 3).
  Withdrawn: two distinct occurrences can legitimately overlap after
  canonicalisation, and a union expansion could make a legal row illegal;
  occurrence identity is adjudicated under the per-key lock.
- **Automatic `extend_start` to the earliest evidenced start** (revision 2).
  Withdrawn: `min()` over claim columns is the reduction D41 forbids;
  revisions are `temporal_window` review verdicts.
- **A replay-based rebuild from current testimony** (revision 3).
  Withdrawn: it discards D55 history, replaces fact ids that other tables
  reference, and can re-pick seeds; conversion is in place from the recorded
  `add` adjudication.
- **Universal half-open claim storage** (revision 1) and **"+ one unit"
  effective ends with raw starts** (revisions 2–3). Withdrawn: the first
  empties `instant`; the second over-expands normalised ends and leaves
  non-aligned starts; both ends are now truncated to the unit, in SQL and
  Python alike.
- **One coalesced key for processing order and succession** (revision 1).
  Withdrawn: D90 needs a total processing order; succession needs world-time
  bounds.
- **Succession only between two dated states** (revision 2). Withdrawn: it
  made D106's dated-resignation case impossible; the state-ending candidate
  set restores it.
- **Closing a retracted state on belief-time and labelling the world-time
  end `source_removed`** (revision 3). Withdrawn: belief-time is not
  world-time; the end stays `unknown` and only the belief interval closes,
  from the persisted reconciliation instant.
- **Cap at the successor's said-on date as an upper bound** (revision 1).
  Withdrawn: a retrospective's publication date proves nothing about when
  the change happened.
- **Bake the resolved date into the observation statement** (revision 1).
  Withdrawn: identity would depend on arrival order (D43); labels are
  derived.
- **Twenty-one local patches.** The pattern recurs in every new consumer.
- **Keep `now()` as the undated cap.** Breaks rebuild stability (D7).

## 11. Non-goals (scope boundaries)

- Recurrence ("every Q4"), anchor-relative time ("as of the merger"), and
  part-of-day expressions ("this morning") remain outside the single-interval
  precision model (D41).
- Belief-time semantics are unchanged.
- No new authority: the claim window stays immutable evidence; the verdict
  window stays the one adjudicated home; the occurrence window is derived
  metadata and is documented as such wherever it is shown.

## References

Decisions: D107 (this design), D41, D3, D4, D43, D88, D90, D106, D24, D7,
D12, D55, D49. Analysis: `plan/analysis/time_handling_audit.md`. Sequencing:
`plan/plans/temporal_clocks.md`. Affected designs carry a D107 amendment
banner: `e2_e3_claims_relations_design.md`, `observations_design.md`,
`registries_design.md`, `retrieval_design.md`, `k_layers_design.md`,
`locomo_benchmark_design.md`, `e3_entity_obs_flush_fanout_design.md`.
