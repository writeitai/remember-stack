# Temporal Clocks — world-time flows from the claim's window (Design)

**Status:** binding under D107

**Date:** 2026-09-03 (third revision the same day, after two independent
Codex design reviews; §10 records what each round withdrew)

**Analysis:** `plan/analysis/time_handling_audit.md` (nineteen findings at
`02b79904`, each cited by file and function)

**Builds on:** D41 (claims carry an immutable, source-asserted validity
interval; a fact's window is the adjudicator's single recorded verdict, never
a reduction over claim columns, never reopened by a late retrospective),
D3/D4 (supersession over verdicts; the cheap-first cascade), D43
(observations; the no-cap rule for fixed-period figures), D88/D90
(continuous-ingest processing order and the late-arrival re-split), D106
(temporal compatibility in observation adjudication), D24 (review verdicts),
D7/D12 (rebuild and generations), D55 (retraction), D49 (envelopes).

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
summaries, filters, timelines — reason on the *source's own date* instead.
The audit lists nineteen instances; D106 fixed one. This design closes the
class without creating a second validity authority.

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
  with `now()` or with the said-on date; an undated fact never caps another
  and is never capped; it coexists, recorded.

## 3. Two kinds of fact, two windows

The audit's confusions come from asking one column to be several things.
Every fact (relation or observation) carries a **temporal kind** and two
windows with different authority:

| `temporal_kind` | seeded from claim kind | what the fact says | identity |
| --- | --- | --- | --- |
| `state` | `effective_period`, `proposition_validity` | something held over a span ("was CEO 2015–2018") | key + **verdict window** overlap |
| `occurrence` | `event_time`, `measurement_period` | something happened, or a figure for a span ("won the final on 2022-11-05"; "FY2023 revenue was $5M") | key + **occurrence window** overlap |
| `undated` | `NULL` | the source tied nothing to a date | key (+ statement wording for observations) |

| Window | Columns | Authority | Changes when |
| --- | --- | --- | --- |
| **verdict window** | `valid_from` / `valid_until` (existing), `valid_from_basis` / `valid_until_basis` (new, `NOT NULL DEFAULT 'unknown'`: `world_time`, `verdict`, `source_removed`, `unknown`), `seed_claim_id` (new) | the adjudicator's single recorded verdict (D3/D41/D43): over which span the fact is believed to have held | seeded **once** at insert from the seed claim (§4.1); afterwards only by a recorded verdict: a supersede cap (§4.4), a review verdict (§4.3), a D55 retraction — never automatically |
| **occurrence window** | `occurs_from` / `occurs_until` / `occurs_precision` (new, nullable) | **non-authoritative** derived metadata: the union of the canonical D41 windows of the fact's current-testimony evidence — the aggregate D106 already computes at block time | recomputed whenever evidence attaches or is withdrawn (the recount path); it never writes to the verdict window |

Why two kinds and two windows: a measurement is *about* FY2023 but is
*believed* from the day it was reported onward and is never capped (D43's
no-cap rule); a past event is about one day but stays a believed fact
forever — and a second, later occurrence of the same key is a **different
fact**, not corroboration (D106); a state is about the same span it is
believed to have held and is what supersession caps. Occurrences therefore
have an open verdict window `[occurs_from, ∞)` that is *not* their identity;
states have a verdict window that *is*. D41's boundary holds: the verdict is
single-valued and recorded; the occurrence window is evidence-derived and
cannot mutate it.

## 4. Fact windows: seeding, matching, revising, closing

### 4.1 Seeding at insert (once, from the seed claim)

The **seed claim** is the first claim for the fact in D90 processing order —
exactly the claim that creates the row in a fresh ingest — and is recorded
in `seed_claim_id`, so a rebuild (§9) reproduces the same seed. Windows are
the claim's **canonical bounds** (§5):

| seed claim kind | `temporal_kind` | `valid_from` | `valid_until` | bases | `occurs_*` |
| --- | --- | --- | --- | --- | --- |
| `effective_period`, `proposition_validity` | `state` | canonical start | canonical end (bounded span → a closed historical slice; open → `NULL`) | `world_time` / `world_time`, or `unknown` for an open end | same as the verdict window |
| `event_time`, `measurement_period` | `occurrence` | canonical start (believed from then on) | `NULL` — never capped (D43, extended to events) | `world_time` / `unknown` | the claim's canonical window |
| `NULL` | `undated` | `NULL` | `NULL` | `unknown` / `unknown` | `NULL` |

The re-occurrence floor (a reopened state slice starts no earlier than the
prior closed slice's end) applies **only** when the seed's start is provably
later than that end — a chronological successor; an older spell discovered
late is inserted as its own closed slice (§4.2).

### 4.2 Matching evidence to facts, by kind

Matching runs in this order: the entity/key block (unchanged), then
temporal-kind identity (this section), then the D4/D106 ladder for the
verdict. Overlap is the canonical half-open predicate of §5.

**State claims** (`effective_period` / `proposition_validity`):

1. the claim's canonical window overlaps a live `state` slice's verdict
   window → attach as evidence; the verdict is untouched; `occurs_*`
   recomputes;
2. no overlap → a **new closed or open slice** (a distinct spell; the
   relations `EXCLUDE` on state slices permits disjoint ranges);
3. an overlap with a *different value* of the same property is the
   supersede/contradict question the ladder answers (§4.4), not a matching
   question.

**Occurrence claims** (`event_time` / `measurement_period`):

1. the claim's canonical window overlaps a live `occurrence` fact's
   `occurs_*` window → the ladder decides `evidence` (same occurrence
   re-mentioned; `occurs_*` widens), `contradict` (same occurrence, disputed
   detail), or `new`;
2. no overlap → a **new occurrence fact**. Two visits, two wins, two
   quarterly figures are two rows; this is D106's rule applied to relations
   as well as observations.

**Undated claims** (`NULL` kind):

- observations: D106's mixed rule — an undated statement never attaches to
  a dated occurrence and a dated event never attaches to an undated
  statement; identical wording collapses onto an existing `undated` row of
  the same key, otherwise a new `undated` row;
- relations: the triple *is* the content, so an undated claim attaches to
  the single open `state` slice if exactly one exists, else to the single
  `undated` row for the key (creating it once); it never attaches to an
  `occurrence` and never creates a second unbounded slice.

**Relations schema.** The existing GiST `EXCLUDE` on
`(subject, predicate, object) && tstzrange(valid_from, valid_until)` applies
`WHERE temporal_kind = 'state'` only; occurrence relations get an `EXCLUDE`
on `(subject, predicate, object) && tstzrange(occurs_from, occurs_until)`,
and `undated` relations a unique key on the triple. Observations keep no
constraint (D43) and rely on this matching plus the D106 rung.

### 4.3 Revising a verdict: review only

No automatic path changes a verdict window after seeding. When attached
evidence's occurrence start precedes a `state` slice's verdict start (or a
slice with an unknown window gains dated evidence), the discrepancy is
visible in the envelope (`occurs_from < valid_from`, or bases `unknown`
beside a dated `occurs_*`) and is raised as a **review item** (D24). A
reviewer's verdict may move the start earlier or date the slice, recorded
with rationale and basis `verdict`, and may never reopen a closed end
(D41's retrospective guard). Rebuild replays review verdicts as it replays
caps (D7).

### 4.4 Closing: temporal succession, separate from processing order

D90's deterministic **processing** order — `(asserted_at NULLS LAST,
claim_id, statement)` — is unchanged; it makes replay total and reproducible
and decides nothing about the world.

**Temporal succession** is the rule for capping a `state` slice. A supersede
verdict caps the predecessor at a **world-time instant supplied by the
successor**:

- a successor `state`'s verdict start (basis `world_time` or `verdict`); or
- an **ending occurrence** the ladder matched as superseding (D106's dated
  resignation ending an "is CEO" state, which may itself have an unknown
  start), at the occurrence's canonical start.

The predecessor's own start basis is irrelevant. When the successor supplies
no world-time instant (an undated successor), there is no succession: the
pair **coexists** and the adjudication row records `reason = "no world-time
boundary -> coexist"`. The cap's basis is `verdict`. Relation supersession
and observation supersession share this one rule (today they disagree about
undated testimony). `now()` is never a boundary.

D55's retraction boundary — the withdrawing document version's source time —
is a *source action*, not a temporal inference; it keeps basis
`source_removed`, and when that time is unknown a retracted state closes on
belief-time (`invalidated_at`) rather than at `now()`.

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
and the final slices must follow the world. This amends the D90 design
(`e3_entity_obs_flush_fanout_design.md` §5.5.3).

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
`canonical_bounds(from, until, precision) → [start, end_exclusive)`,
converts a stored claim window to a **half-open** interval:

| `claim_valid_precision` | canonical `[start, end)` |
| --- | --- |
| `day` / `month` / `quarter` / `year` | `[from, date_trunc(unit, until) + unit)` — a year stored as 2022-01-01…2022-12-31 becomes `[2022-01-01, 2023-01-01)`; a day 2023-05-07 becomes `[05-07 00:00, 05-08 00:00)` |
| `instant` | `[t, t + 1 µs)` — a non-empty point |
| `open` | `[from, NULL)` (unbounded) |
| `unknown` | no interval; never matches a window predicate and is disclosed as excluded |

**Fact windows are stored canonical.** `valid_from`/`valid_until` and
`occurs_from`/`occurs_until` are written from `canonical_bounds` at seeding,
so every fact predicate the engine already has — `valid_until > :as_of`,
`tstzrange(valid_from, valid_until)` in the `EXCLUDE`, the P1 `FactTime`
selectors — is correct without a precision column; `occurs_precision` is kept
for labels only. **Claim comparisons canonicalise at query time** —
`claims_as_of`, D106's `_windows_disjoint`, §4.2 matching, the §7 dedupe key
and filters all call the same function — so a day-precision claim is found
by an intraday query and two same-day events from different wordings
overlap. The overlap predicate is `a.start < b.end AND b.start < a.end` with
`NULL` ends as +∞; no second predicate for points is needed because an
instant is a non-empty interval.

## 6. Extraction: vocabulary and anchor

The extractor prompt defines and exemplifies **all four kinds** and `open`
("has been CEO since 2019" → `proposition_validity`, `open`; "FY2023 revenue
was $5M" → `measurement_period`, `year`; "worked at Acme from 2015 to 2018"
→ `effective_period`, `year`), and the structured-output model describes
each field. The document header shown to the extractor carries the **full
source timestamp**, so offset expressions ("three hours ago", "an hour
before this was written") resolve to instants and two same-day sources are
distinct anchors. Expressions the precision enum cannot represent ("this
morning" has no part-of-day precision) remain unresolved by design — a
documented expressivity boundary, not a defect of the anchor.

## 7. Every temporal judgement sees both clocks; every surface names them

### 7.1 The current-fact predicate

"Current" is one predicate evaluated at an explicit instant `E`:
`invalidated_at IS NULL AND (valid_until IS NULL OR valid_until > E)`.
Occurrences and undated facts are always current (open verdict). A `state`
with a finite **future** end is current until it passes. Every consumer that
today tests `valid_until IS NULL` — entity profiles, source-removal
eligibility, K routing, `facts_current` — uses this predicate with `E` fixed
per evaluation, and cached artifacts (profiles, K pages) include the earliest
future `valid_until` among their inputs in their input hash and are queued
for refresh by an **expiry sweep** when that instant passes, so the hash
cannot go stale silently as wall time moves (the concern the profile code
records today).

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
  rows.

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
  `adjudicate_observations` flush component, P1 labels, K page generation.
- Versions: the assured operations of §7.3, the surface and query-space
  manifests, OpenAPI/SDK, and the LoCoMo protocol — each landed work package
  that changes ingestion provenance or the assured surface rolls it;
  packages released together roll it once. Scores across a roll are
  directional.

## 9. Cutover: stop, drain, migrate, rebuild, then serve

A store never mixes pre- and post-D107 fact semantics:

1. **stop** intake and **drain** in-flight units (the D106 contract lets
   pre-roll observation units complete under their own generation);
2. **migrate** — add the new columns (`temporal_kind`, the two bases,
   `occurs_from`/`occurs_until`/`occurs_precision`, `seed_claim_id`), the
   state-only `EXCLUDE`, the occurrence `EXCLUDE`, the undated unique key;
3. **rebuild the fact layer** by replaying the current-testimony claims in
   D90 processing order through the new normaliser and adjudicators (D7):
   the first claim per fact seeds it, exactly as a fresh ingest would, so
   the seed is reproducible; existing verdict rows are replaced, not
   reinterpreted; recorded review verdicts and D55 retractions replay after
   the caps;
4. **readiness** reports the fact-layer generation; consumers refuse to
   serve a store whose fact generation predates D107;
5. **rollback** is restore of the pre-migration backup, as for any
   generation roll.

Only §6's added kinds benefit from re-extraction; steps 1–4 need none.

## 10. Alternatives considered, including what the two reviews withdrew

- **Seed `valid_from` from the said-on date when the window is unknown**
  (revision 1). Withdrawn: provenance is not validity (Rule 2).
- **Seed a measurement's or event's `valid_until` from its claim**
  (revision 1). Withdrawn: D43 never caps a fixed-period figure; the period
  belongs to the occurrence window.
- **Match every fact by verdict-window overlap** (revision 2). Withdrawn: an
  occurrence's open verdict window overlaps every later occurrence, which
  recreates the D106 collapse and collides with the relations `EXCLUDE`;
  occurrences are matched by `occurs_*` and have their own exclusion.
- **Automatic `extend_start` to the earliest evidenced start** (revision 2).
  Withdrawn: `min()` over claim columns is the reduction D41 forbids, and it
  would let a late retrospective move an adjudicated window. Discrepancies
  are surfaced for review instead.
- **Universal half-open claim storage** (revision 1) and **comparison-time
  effective ends by "+ one unit"** (revision 2). Withdrawn: the first empties
  `instant`; the second over-expands normalised year/quarter/month ends and
  leaves fact predicates and the `EXCLUDE` reading raw bounds. Replaced by
  `canonical_bounds` applied at fact seeding and at claim comparison.
- **One coalesced key for processing order and succession** (revision 1).
  Withdrawn: D90 needs a total processing order; succession needs world-time
  bounds.
- **Succession only between two dated states** (revision 2). Withdrawn: it
  made D106's dated-resignation-ends-undated-state case impossible; any
  world-time instant supplied by the successor may cap.
- **Cap at the successor's said-on date as an upper bound** (revision 1).
  Withdrawn: a retrospective's publication date proves nothing about when
  the change happened.
- **Bake the resolved date into the observation statement** (revision 1).
  Withdrawn: identity would depend on arrival order (D43); labels are
  derived.
- **Nineteen local patches.** The pattern recurs in every new consumer.
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
