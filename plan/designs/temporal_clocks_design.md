# Temporal Clocks — world-time flows from the claim's window (Design)

**Status:** binding under D107

**Date:** 2026-09-03

**Analysis:** `plan/analysis/time_handling_audit.md` (seventeen findings at
`02b79904`, each cited by file and function)

**Builds on:** D41 (claims carry an immutable, source-asserted validity
interval), D3/D4 (supersession over verdicts, the cheap-first cascade), D43
(observations), D88/D90 (continuous ingest ordering), D106 (temporal
compatibility in observation adjudication), D7/D12 (rebuild and generations).

> **Reading this cold.** RememberStack extracts **claims** (what a source said,
> immutable) and adjudicates them into **facts** (relations between two
> entities, and observations about one entity) that carry a validity window:
> the span of world-time over which the fact held. The extractor already
> resolves *when* a statement is about — "last week" said on 2022-10-06
> becomes the calendar day 2022-09-29 — and stores it on the claim as its
> **D41 window**. This design says where that resolved time must flow, which
> clock every stage reasons on, and what happens when a time is unknown.

## 1. The problem, in one sentence

The engine resolves world-time once, at extraction, and then almost every
later stage — fact windows, supersession, ordering, deduplication, prompts,
summaries, filters, timelines — reasons on the *source's own date* instead.
The audit lists seventeen instances; D106 fixed one. This design closes the
class.

## 2. The three clocks

| Clock | Where it lives | Meaning | Role under this design |
| --- | --- | --- | --- |
| **said-on** | `claims.asserted_at` | when the source said it | provenance; an *upper bound* on when a described change had happened; never validity by itself |
| **is-about** | `claims.claim_valid_kind`, `claim_valid_from`, `claim_valid_until`, `claim_valid_precision` (D41) | the world-time the statement refers to, resolved from the wording against the said-on date | the source of every fact window, ordering decision, and temporal prompt line |
| **believed** | `ingested_at`, `invalidated_at` | when the system held the belief | belief-time only; never a substitute for world-time |

`claim_valid_kind` is the *sort* of interval: `event_time` (something
happened — a win, a visit, a resignation), `effective_period` (a state held —
"was CEO"), `measurement_period` (a figure for a span — "FY2023 revenue"),
`proposition_validity` (the proposition held — "has lived in Prague since
2019"). `NULL` means the source tied nothing to a date. A resolved start with
`claim_valid_until IS NULL` is an **open** interval: its end is unbounded,
never the same instant as its start.

Two rules follow everything below:

- **Rule 1 — world-time comes from the is-about window.** A fact's validity,
  the order in which facts supersede one another, whether two statements are
  the same fact, what a model is shown, and what a summary prints all read the
  is-about window first.
- **Rule 2 — a missing time stays missing.** When the is-about window is
  unknown, the said-on date may serve only where it is logically an upper
  bound (a change described by a source had happened by the time the source
  said so), and it is recorded as such. Nothing is ever filled with `now()`,
  and an undated statement never *wins* an ordering.

## 3. Extraction (E2): emit every kind, keep precision honest

- The extractor prompt defines and exemplifies **all four kinds** and the
  **open** precision, one worked example each, in the same style as the
  existing `event_time` examples: "has been CEO since 2019" →
  `proposition_validity`, `open`, from 2019-01-01, until null; "FY2023 revenue
  was $5M" → `measurement_period`, `year`, 2023; "worked at Acme from 2015 to
  2018" → `effective_period`, `year`, 2015–2018. The structured-output model
  carries a field description for each.
- The document header shown to the extractor carries the **full source
  timestamp**, not the date alone, so intraday expressions can resolve and two
  same-day sources are distinguishable anchors.
- **Precision-derived ends are half-open.** A day-precision claim about
  2023-05-07 is stored as `[2023-05-07T00:00Z, 2023-05-08T00:00Z)`; a
  year-precision claim about 2022 as `[2022-01-01, 2023-01-01)`. Comparisons
  everywhere use `from < other.until AND other.from < until` (half-open
  overlap), with a `NULL` until as +∞. `claim_valid_precision` remains the
  honesty marker; the stored ends are what the precision means, not a point.
  The existing DB checks still hold (`unknown` ⇒ both ends `NULL`).

## 4. Fact windows: seeding, widening, closing

### 4.1 Seeding at insert

When E3 creates a relation or an observation from a claim, the fact's window
is seeded from that claim's is-about window **by kind**:

| Triggering claim | `valid_from` | `valid_until` | `validity_basis` |
| --- | --- | --- | --- |
| `effective_period`, `proposition_validity` (a state or proposition held over a span) | `claim_valid_from` | `claim_valid_until` (NULL when open) | `world_time` |
| `event_time` (something happened) | `claim_valid_from` | `claim_valid_until` (the event's own span; NULL when open) | `world_time` |
| `measurement_period` (a figure for a span) | `claim_valid_from` | `claim_valid_until` | `world_time` |
| no is-about window, said-on known | `asserted_at` | NULL | `said_on` |
| neither | NULL | NULL | `unknown` |

`validity_basis` is a new nullable column on `relations` and `observations`
(`world_time` / `said_on` / `unknown`) so a reader can tell a window the
source dated from one the engine inferred. This is not a second validity
authority (D41 §"why this is not…"): the window is still one value per fact,
recorded by the adjudicator, and only the adjudicator changes it. The claim
window *seeds* it, exactly as D41 anticipated.

The previous re-occurrence rule — a relation reopened after an earlier closed
spell starts no earlier than that spell's end — remains as a monotonic floor
on the seeded start.

### 4.2 Widening on evidence

When a claim collapses as evidence onto an existing fact, the fact's start may
move **earlier** to the evidence's is-about start (or said-on start under
basis `said_on`), never later, and never past a neighbouring capped slice.
This replaces the current pull to the earliest *said-on* time. The end is
never changed by evidence.

### 4.3 Closing (supersession, retraction)

A supersede verdict caps the predecessor at the **successor's world-time
start**, chosen in this order:

1. the successor's is-about start (`world_time`);
2. else the successor's said-on date — by the time the source said the new
   state held, the old one had ended, so the said-on date is an honest upper
   bound (`said_on`);
3. else **no cap**: the pair coexists and the adjudication row records
   `reason = "successor undated -> coexist"`. This is D43's fail-safe applied
   to time: a duplicate, never an invented instant.

`now()` is never a boundary. The same order applies to the D55 retraction
cap on relations (basis: the withdrawing version's source time, else no cap).

### 4.4 One ordering comparator

Predecessor/successor orientation — in relation supersession, in the
observation staging order, and in the D90 re-split — uses one shared key:

```
(is-about start, else said-on start, NULLS LAST; claim_id)
```

with **undated never winning**: when one side is undated and the other is not,
the undated side is neither predecessor nor successor for capping purposes —
it coexists, recorded. The two adjudicators stop disagreeing about undated
testimony because they share the comparator.

### 4.5 The observation statement carries its date

E3's normaliser receives the claim's is-about window with the claim. When the
window is known, the observation `statement` must carry the absolute date in
its own words ("Nate won a video game tournament on 2022-09-29 (said 'last
week')"), because that statement is what P1 embeds, profiles list, K pages
print, and readers see. The claim text itself stays source-faithful (D32); the
date joins the *fact's* wording, not the *claim's*.

## 5. Adjudication and retrieval

### 5.1 Every temporal judgement sees both clocks

The two-clock block D106 introduced for the observation verdict —

```
EXISTING: …
  said on: <date or unknown>
  is about: <resolved window, kind, or "no specific time given">
NEW: …
  said on: …
  is about: …
```

with its definitions — is the contract for **every** prompt that compares,
merges, ranks, or summarises facts: relation supersession, the T4 identity
candidate list (each salient fact with its window), the K prose writer's
claim bundle (each claim with said-on and is-about), and the benchmark answer
agent (which additionally names the envelope fields: `asserted_at`,
`claim_valid_*`, `validity.valid_from/valid_until`, `validity_basis`).

### 5.2 Deduplication keys include the window

Grouping identical testimony for a response (`testimony_context`,
`answer_context`) keys on `(normalised text, claim_valid_kind,
claim_valid_from, claim_valid_until)`; two claims with the same words about
different windows are two rows. When the window is unknown on both sides the
key falls back to text alone, as today.

### 5.3 The fact-grain envelope names the clocks

`Validity` gains `validity_basis` beside `valid_from` / `valid_until`, and the
evidence grain keeps all five D41 fields (already the case). A reader can
therefore always tell an inferred window from a source-dated one.

### 5.4 Filters and timelines use world-time

- P1 claim search accepts `valid_from` / `valid_until` filters (half-open
  overlap on the D41 columns, using the existing partial index) beside the
  said-on `asserted_from` / `asserted_to`; fact search accepts the same on the
  fact window.
- `aggregate(form="timeline")` buckets facts by `valid_from` and emits an
  explicit `undated` bucket; it never substitutes `ingested_at`.
- Entity profiles rank salient facts by evidence then by the fact window's
  recency, never by `updated_at`; each salient fact carries its window.

## 6. Consumer surfaces label honestly

The K fact sheet's columns are `valid from` / `valid until` **only** for
`world_time` rows; rows with basis `said_on` print under `said on` and rows
with basis `unknown` print `—`. "Observation history" sorts by the fact
window under the same rule. Until the fact layer is re-seeded, existing sheets
must not print a said-on date under a world-time heading.

## 7. Generations, rebuild, protocol

- The extractor generation rolls (half-open ends, full-timestamp header, all
  kinds); the normaliser and both adjudicator generations roll; the
  `adjudicate_observations` flush component rolls again with the D106
  stop-drain-rebuild contract.
- The schema adds `validity_basis` on `relations` and `observations` (a
  migration; head rolls).
- Existing stores are rebuilt (D7): fact windows are re-seeded from claim
  rows, which already hold the D41 fields — no re-extraction is needed for
  4.1–4.4, only for §3's half-open ends and additional kinds.
- The LoCoMo protocol rolls once for the whole change (ingestion provenance
  and the answer prompt both change); scores before and after are
  directional.

## 8. Alternatives considered

- **Patch each finding where it sits.** Seventeen local fixes would leave the
  next stage free to read the wrong clock again; the audit shows the pattern
  recurs wherever a new consumer is written. One contract with one comparator
  and one prompt block is smaller and stays true.
- **Make the claim window the fact window (a view, no seeding).** Rejected by
  D41's own boundary: the fact window must be the adjudicator's single,
  recorded, monotonic verdict, not a reduction over many-valued claim columns.
  Seeding-then-adjudicating keeps that property.
- **Keep `now()` as the undated cap.** It makes histories depend on ingest
  wall-clock and breaks rebuild stability (D7); coexisting with a recorded
  reason is the D43 fail-safe and costs at most a duplicate.
- **A typed period/value column on facts.** D43 §4 rejected typed value
  schemas; the window plus `validity_basis` is the minimum that makes as-of
  queries true, and it is already the schema's shape.
- **Stripping relative wording from claim text.** Violates D32's
  source-faithfulness; the absolute date belongs on the *fact's* statement
  (§4.5), where it is the engine's wording.

## 9. Non-goals (scope boundaries)

- Recurrence ("every Q4") and anchor-relative time ("as of the merger")
  remain outside the single-interval model, as D41 records.
- Belief-time (`ingested_at`/`invalidated_at`) semantics are unchanged.
- No new time authority: the claim window stays immutable evidence; the fact
  window stays the one adjudicated home.

## References

Decisions: D107 (this design), D41, D3, D4, D43, D88, D90, D106, D7, D12,
D55. Analysis: `plan/analysis/time_handling_audit.md`. Sequencing:
`plan/plans/temporal_clocks.md`. Affected designs carry a D107 amendment
banner: `e2_e3_claims_relations_design.md`, `observations_design.md`,
`registries_design.md`, `retrieval_design.md`, `k_layers_design.md`,
`locomo_benchmark_design.md`.
