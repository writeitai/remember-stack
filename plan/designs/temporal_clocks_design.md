# Temporal Clocks — world-time flows from the claim's window (Design)

**Status:** binding under D107

**Date:** 2026-09-03 (revised the same day after an independent Codex design
review; the first revision's said-on seeding, mechanical widening, universal
half-open ends, and merged ordering key were withdrawn — see §9)

**Analysis:** `plan/analysis/time_handling_audit.md` (eighteen findings at
`02b79904`, each cited by file and function)

**Builds on:** D41 (claims carry an immutable, source-asserted validity
interval; a fact's window is the adjudicator's single recorded verdict, never
a reduction over claim columns), D3/D4 (supersession over verdicts; the
cheap-first cascade), D43 (observations; the no-cap rule for fixed-period
figures), D88/D90 (continuous-ingest processing order and the re-split),
D106 (temporal compatibility in observation adjudication), D7/D12 (rebuild
and generations), D55 (retraction), D49 (envelopes).

> **Reading this cold.** RememberStack extracts **claims** (what a source
> said, immutable) and adjudicates them into **facts** — relations between
> two entities, observations about one entity — each with a validity window:
> the span over which the system believes the fact held. The extractor
> already resolves *when* a statement is about ("last week" said on
> 2022-10-06 → 2022-09-29) and stores it on the claim as its **D41 window**.
> This design says where that resolved time must flow, which clock each
> stage reasons on, and what happens when a time is unknown.

## 1. The problem, in one sentence

The engine resolves world-time once, at extraction, and then most later
stages — fact windows, supersession, ordering, deduplication, prompts,
summaries, filters, timelines — reason on the *source's own date* instead.
The audit lists eighteen instances; D106 fixed one. This design closes the
class without creating a second validity authority.

## 2. The three clocks and the two rules

| Clock | Where it lives | Meaning | Role |
| --- | --- | --- | --- |
| **said-on** | `claims.asserted_at` | when the source said it | provenance, shown beside every statement a model or reader judges; never written into a fact window |
| **is-about** | `claims.claim_valid_kind` / `claim_valid_from` / `claim_valid_until` / `claim_valid_precision` (D41) | the world-time the statement refers to, resolved from the wording against the said-on date | the source of every fact window, every temporal succession decision, every dated label, and every temporal prompt line |
| **believed** | `ingested_at` / `invalidated_at` | when the system held the belief | belief-time only; never a substitute for world-time |

`claim_valid_kind` says what sort of interval the is-about window is:
`event_time` (something happened), `effective_period` (a state held over a
span), `measurement_period` (a figure for a reporting span),
`proposition_validity` (the proposition held over a span). `NULL` means the
source tied nothing to a date. A resolved start with `claim_valid_until IS
NULL` is an **open** interval; `instant` is a point (`until = from`, enforced
by the schema).

- **Rule 1 — world-time comes from the is-about window.** Fact windows,
  temporal succession, dated labels, dedupe keys, and every temporal prompt
  read the is-about window. The said-on clock is displayed as provenance and
  is never a window boundary.
- **Rule 2 — a missing time stays missing.** An unknown is-about window
  leaves a fact window `NULL` with basis `unknown`. Nothing is ever filled
  with `now()` or with the said-on date, and a fact whose timing is unknown
  never caps another fact and is never capped by one; it coexists, recorded.

## 3. Two windows on every fact

The audit's confusions come from asking one column to be two things. A fact
carries two windows with different authority:

| Window | Columns | Authority | Changes when |
| --- | --- | --- | --- |
| **verdict window** | `valid_from` / `valid_until` (existing), plus `valid_from_basis` / `valid_until_basis` (new, `NOT NULL DEFAULT 'unknown'`; values `world_time`, `verdict`, `source_removed`, `unknown`) | the adjudicator's single recorded verdict (D3/D41/D43): "over which span do we believe this fact held" | seeded **once** at insert from the triggering claim (§4.1); afterwards only by a recorded adjudication verdict: a supersede cap (§4.4), an `extend_start` verdict (§4.3), a D55 retraction |
| **occurrence window** | `occurs_from` / `occurs_until` / `occurs_precision` (new, nullable) | **non-authoritative** derived metadata: the union of the D41 windows of the fact's current-testimony evidence — the same aggregate D106 already computes at block time | recomputed whenever evidence attaches or is withdrawn (the existing recount path); it never writes to the verdict window |

Why two: a measurement ("FY2023 revenue was $5M") is *about* FY2023 but is
*believed* from the day it was reported onward and is never capped (D43's
no-cap rule); a past event ("won the final on 2022-11-05") is about one day
but stays a believed fact forever; a state ("was CEO 2015–2018") is about the
same span it is believed to have held. Only the last kind has one window. The
occurrence window is what "when did it happen / what period is this figure
for" queries, dated labels, timelines and is-about filters read; the verdict
window is what `valid_at` / `facts_as_of` and supersession read. D41's
boundary is kept exactly: the verdict is single-valued and recorded; the
occurrence window is evidence-derived and cannot mutate it.

## 4. Fact windows: seeding, matching, extending, closing

### 4.1 Seeding at insert (once)

| Triggering claim (`claim_valid_kind`) | `valid_from` | `valid_until` | bases |
| --- | --- | --- | --- |
| `effective_period`, `proposition_validity` (a state held over a span) | `claim_valid_from` | `claim_valid_until` — a bounded span becomes a **closed historical slice**; open → `NULL` | `world_time` / `world_time` (or `unknown` for an open end) |
| `event_time` (something happened) | `claim_valid_from` (the fact "it happened" holds from then on) | `NULL` — a past event is never capped; it can only be contradicted | `world_time` / `unknown` |
| `measurement_period` (a figure for a span) | `claim_valid_from` (the period start; believed from then on) | `NULL` — D43's no-cap rule | `world_time` / `unknown` |
| no is-about window | `NULL` | `NULL` | `unknown` / `unknown` |

The occurrence window is set from the same claim at the same time. The
previous re-occurrence floor (a reopened spell starts no earlier than the
prior closed spell's end) applies **only** when the incoming occurrence start
is provably later than that end — a proven chronological successor; an older
spell discovered late is inserted as its own historical slice (§4.2).

### 4.2 Matching evidence to bounded slices

A fact's identity is its key plus its verdict window (relations enforce this
with a GiST `EXCLUDE` on `(subject, predicate, object) && tstzrange(valid_from,
valid_until)`; observations have no constraint and rely on the D106 rung).
Evidence-target selection is therefore interval-aware, not open-only:

1. **Overlapping** — the incoming claim's occurrence window overlaps a live
   slice's verdict window → the claim attaches as evidence; the slice's
   verdict is untouched; its occurrence window is recomputed.
2. **Disjoint** — no live slice's verdict window overlaps → a **new slice**
   (a distinct historical spell), which the `EXCLUDE` permits.
3. **Unknown timing** on the incoming claim → attach to the single open slice
   if exactly one exists; otherwise attach to the most recent slice and
   record `attachment = "unknown_timing"` on the evidence row. An undated
   claim never creates a second unbounded slice (which the `EXCLUDE` would
   reject for relations and which would be a silent duplicate for
   observations).
4. **Unknown timing on the existing slice** (its verdict window is
   `NULL`/`NULL`) and a dated incoming claim → attach; the slice stays
   undated (no mechanical seeding after the fact). A later explicit verdict
   may date it (§4.3).

Overlap uses the precision-aware predicate of §5.

### 4.3 Revising a verdict: explicit, recorded, deterministic

A verdict window changes only through a recorded adjudication row with a
rationale, exactly as a cap does today:

- **`extend_start`** — when attached evidence for a *state* fact
  (`effective_period` / `proposition_validity`) carries an occurrence start
  earlier than the verdict start, the adjudicator records `extend_start`
  (rationale: the claim id and its window) and moves `valid_from` earlier —
  never later, never earlier than a capped predecessor's `valid_until`, and
  never touching `valid_until`. The rule is deterministic over the evidence
  set (the earliest evidenced start wins), so the outcome does not depend on
  arrival order (D88's independence) or on rebuild (D7), and it is a verdict
  with a transcript row, not a reduction over claim columns (D41). It does
  not apply to events or measurements, whose verdict start is the occurrence
  start of the seeding claim and whose occurrence window already widens.
- **`date_undated`** — a slice with an unknown verdict window may be dated by
  an explicit verdict when dated evidence attaches and the slice is the only
  one for its key; recorded the same way.
- No verdict ever reopens a closed end (D41's retrospective guard); a
  retrospective that contradicts a closed end is a `contradict` verdict and
  both stand.

### 4.4 Closing: temporal succession, separate from processing order

D90's deterministic **processing** order — `(asserted_at NULLS LAST,
claim_id, statement)` for observation staging and the re-split — is
unchanged; it exists so replay is total and reproducible, and it decides
nothing about the world.

**Temporal succession** is a separate relation used only for capping:
*B succeeds A* iff both are state facts with verdict starts of basis
`world_time` and `B.valid_from > A.valid_from`. A supersede verdict caps A
at `B.valid_from` (basis `verdict`). When either side's start is not
`world_time`, there is no succession: the pair **coexists** and the
adjudication row records `reason = "no world-time boundary -> coexist"`.
Relation supersession and observation supersession share this one definition
(today they disagree about undated testimony; the audit's 4.11). `now()` is
never a boundary.

D55's retraction boundary — the withdrawing document version's source time —
is a *source action*, not a temporal inference, and keeps its own basis
`source_removed`; when that time is unknown, a retracted state is closed on
belief-time (`invalidated_at`) rather than capped at `now()`.

**Residual, measured rather than assumed:** undated restatements of a
changing state coexist as duplicates. Identical wording still collapses
(§4.2 step 3 attaches it), so growth comes only from *differently worded,
undated* restatements; the lever is extraction coverage of the D41 kinds
(§6), and the count of `unknown`-basis slices per key is a reported metric.

### 4.5 Statements stay canonical; dated labels are derived

The observation `statement` remains the canonical source-faithful wording
(D43; unchanged on collapse and supersession). The human-facing **label** —
`obs_label`, `FactResult.label`, the profile line, the K fact-sheet cell — is
derived deterministically from `statement` plus the occurrence window and
precision ("Nate won a video game tournament last week — on 2022-09-29"),
and is refreshed whenever the occurrence window is recomputed. Nothing
source-specific is baked into identity, and two claims of different
precision that collapse yield one label from the union window at the coarser
precision.

## 5. Precision without changing storage

Claim windows stay stored as they are (closed intervals; `instant` as a
point; `open` as a `NULL` end; the schema `CHECK`s unchanged). What changes is
**comparison**: every overlap or containment test derives an *effective
exclusive end* from the stored end and the precision —

| `claim_valid_precision` | effective end |
| --- | --- |
| `day` / `month` / `quarter` / `year` | `claim_valid_until + one unit` (a day-precision 2023-05-07 covers `[05-07 00:00, 05-08 00:00)`) |
| `instant` | the point itself (closed; `from = until`) |
| `open` | +∞ |
| `unknown` | no window (never matches a window predicate; disclosed as excluded) |

— and the predicate is `a.from < b.effective_end AND b.from < a.effective_end`,
with points compared inclusively. This one function is used by
`claims_as_of`, D106's `_windows_disjoint`, §4.2 matching, the §7 dedupe key,
and the is-about filters, so a day-precision claim is found by an intraday
query and two same-day events from different wordings overlap. No
re-extraction, no migration, and no ordering between this and §4.

Extraction itself improves in vocabulary, not shape: the prompt defines and
exemplifies **all four kinds** and `open` ("has been CEO since 2019" →
`proposition_validity`, `open`; "FY2023 revenue was $5M" →
`measurement_period`, `year`; "worked at Acme from 2015 to 2018" →
`effective_period`, `year`), the structured-output model describes each
field, and the document header shown to the extractor carries the **full
source timestamp** so intraday expressions resolve and same-day sources are
distinct anchors.

## 6. Every temporal judgement sees both clocks

The two-clock block D106 introduced for the observation verdict —

```
EXISTING: …
  said on: <date or unknown>
  is about: <resolved window and kind, or "no specific time given">
NEW: …
  said on: …
  is about: …
```

with its definitions — is the contract for **every** prompt that compares,
merges, ranks, or summarises facts: relation supersession (its evidence
laterals select the D41 columns), the T4 identity candidate list (each
salient fact with its occurrence window), the K prose writer's claim bundle
(each claim with said-on and is-about), and the benchmark answer agent, which
additionally names the envelope fields (`asserted_at`, `claim_valid_*`,
`validity.valid_from/valid_until` with bases, `occurrence`).

## 7. Retrieval, envelopes, filters, consumers

- **Deduplication.** Grouping identical testimony keys on `(normalised
  text, claim_valid_kind, claim_valid_from, claim_valid_until,
  claim_valid_precision)` when the window is known and on `(normalised
  text, asserted_at)` when it is not, and every grouped row carries each
  member's `asserted_at` and window, so no temporal distinction is lost
  before a reader sees it.
- **Envelopes (D49, additive).** The fact-grain `Validity` gains
  `valid_from_basis`, `valid_until_basis`, and `occurrence`
  (`from`/`until`/`precision`); the same fields are added to `GraphEdge`'s
  flat validity, to the K-layer fact model, and to the `memory_v1` fact
  views. The affected assured operations roll their versions
  (`fact_context@3`, `answer_context@3`; `testimony_context` is unchanged in
  shape), the surface manifest hash and generated OpenAPI/SDK artifacts roll
  with them.
- **Filters.** P1 claim search gains `valid_from` / `valid_until` (is-about,
  §5 overlap) beside the said-on `asserted_from` / `asserted_to`; P1 fact
  search's existing `FactTime` selector gains an `occurs` mode over the
  occurrence window beside its verdict-window modes.
- **Timeline.** `aggregate(form="timeline")` buckets facts by occurrence
  start, then verdict start, and emits an explicit `undated` bucket; it never
  substitutes `ingested_at`.
- **Profiles.** Salient facts rank by evidence, then by occurrence recency;
  never by `updated_at`; each carries its occurrence window into the T4
  prompt.
- **Consumers.** The K fact sheet prints `valid from` / `valid until` from
  the verdict window only where the basis is `world_time` or `verdict`,
  prints `about` from the occurrence window, and `—` for `unknown`;
  "Observation history" sorts by occurrence start. The consumption skill
  states the three clocks and defines `claims_as_of` over source world-time
  (D41), not system time.

## 8. Generations, rebuild, protocol

- Schema: one migration adds the four basis/occurrence columns to
  `relations` and `observations`; the schema head rolls.
- Generations: the extractor (kinds, header), the normaliser (seeding), both
  adjudicators (succession, `extend_start`, labels), the
  `adjudicate_observations` flush component (with the D106
  stop-drain-rebuild contract), P1 labels, K page generation.
- Rebuild (D7): existing stores are re-seeded from the claim rows they hold
  (§4.1 needs only `claim_valid_*`, already present); only §5's added kinds
  benefit from re-extraction.
- Protocol: each landed work package that changes ingestion provenance or
  the assured surface rolls the LoCoMo protocol; packages released together
  roll it once. Scores across a roll are directional.

## 9. Alternatives considered (including the withdrawn first revision)

- **Seed `valid_from` from the said-on date when the window is unknown**
  (first revision). Withdrawn: it writes provenance into a world-time column
  and contradicts Rule 2; a source's modification date is not evidence that
  a state began then.
- **Seed `valid_until` from a measurement's or event's claim end.** Withdrawn:
  D43 says a fixed-period figure is never capped and a finite end would drop
  it from current facts and profiles; the period belongs to the occurrence
  window, which is why there are two.
- **Widen the verdict start mechanically on evidence.** Withdrawn: a
  reduction over claim columns (D41). Replaced by the recorded,
  deterministic `extend_start` verdict.
- **Store all precisions half-open.** Withdrawn: `instant` requires
  `until = from` and would become empty; comparison-time effective ends
  achieve the same result with no migration or re-extraction.
- **One coalesced ordering key for processing and succession.** Withdrawn:
  D90 needs a total processing order; succession needs world-time bounds;
  they are different questions.
- **Cap at the successor's said-on date as an upper bound.** Withdrawn: a
  retrospective's publication date proves nothing about when the described
  change happened.
- **Bake the resolved date into the observation statement.** Withdrawn:
  makes identity depend on which claim arrived first (D43); labels are
  derived instead.
- **Seventeen local patches.** The pattern recurs in every new consumer; one
  contract with one overlap function, one succession rule, and one prompt
  block is smaller and stays true.
- **Keep `now()` as the undated cap.** Breaks rebuild stability (D7).

## 10. Non-goals (scope boundaries)

- Recurrence ("every Q4") and anchor-relative time ("as of the merger")
  remain outside the single-interval model (D41).
- Belief-time semantics are unchanged.
- No new authority: the claim window stays immutable evidence; the verdict
  window stays the one adjudicated home; the occurrence window is derived
  metadata and is documented as such wherever it is shown.

## References

Decisions: D107 (this design), D41, D3, D4, D43, D88, D90, D106, D7, D12,
D55, D49. Analysis: `plan/analysis/time_handling_audit.md`. Sequencing:
`plan/plans/temporal_clocks.md`. Affected designs carry a D107 amendment
banner: `e2_e3_claims_relations_design.md`, `observations_design.md`,
`registries_design.md`, `retrieval_design.md`, `k_layers_design.md`,
`locomo_benchmark_design.md`.
