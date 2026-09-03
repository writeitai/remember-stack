# The Observation Layer — Non-Graph Facts with Temporal Validity (Design)

> **Binding D107 amendment (2026-09-03).** An observation's `valid_from` is
> seeded from the triggering claim's D41 window (basis `world_time`), from
> the said-on date only when the window is unknown (basis `said_on`), and
> otherwise stays `NULL` (basis `unknown`). Evidence widens the start earlier
> to its is-about start, never to a said-on time. A cap uses the successor's
> is-about start, else its said-on date, else no cap — never `now()`. The
> staging order and predecessor/successor orientation use the shared
> comparator of `temporal_clocks_design.md` §4.4; the D106 rung is unchanged.

> **Binding D98 amendment (2026-08-27).** Relations are the only fact kind
> mapped as live graph edges over PostgreSQL views. Observations remain
> PostgreSQL facts and P1 search targets but are not graph elements. There is no
> P2 projection/generation; pre-D98 “projects to P2” wording means only this
> live relation-edge mapping.

How the system records and time-travels facts that are **about one entity but not links between two
entities** — a headcount, a balance, a fiscal revenue, a founding date, a status — *without* forcing
them into the typed graph and *without* a governed attribute vocabulary. Binding design for decision
**D43**. It sits beside the **relations** layer (D2–D5/D18, unchanged) and builds on D3 (supersession
over verdicts, never claims), D4 (the cheap-first blocking cascade), D6 (validity has one home), D94
(P1 is a private PostgreSQL projection), D18 (the graph holds only entities), and D41 (claims carry an immutable
asserted-validity interval).

> **Reading this cold (CLAUDE.md Rule 1).** You need no prior context. A **claim** is an atomic,
> immutable, natural-language assertion *as a source made it* (the evidence record — never edited). A
> **relation** is a believed `(subject_entity → predicate → object_entity)` fact — the kind a graph can
> hold as an edge. An **observation** (this doc) is a believed fact whose object is a **value or
> statement about a single entity** ("Acme's headcount is 600"), *not* a link to another entity — so it
> is **not** a graph edge. **Bi-temporal** means two clocks: *valid-time* (when the fact held in the
> world — `valid_from`/`valid_until`) and *transaction-time* (when we believed it —
> `ingested_at`/`invalidated_at`). **Supersession** = a newer belief closing an older one's validity
> window. **Blocking** = cheaply narrowing to the handful of existing rows a new fact might conflict
> with, before spending any expensive comparison.

## 1. Two canonical layers, split by what a fact *is*

A document yields claims; normalization (E3) turns claims into the facts the system *believes*. Those
facts come in two shapes, and D43 keeps them in **two separate canonical tables** rather than one:

- **Relations** — both ends are entities: `(Alice) —works_for→ (Acme)`. Typed (governed predicate),
  because a graph needs typed edges. This is the **only** layer that projects to the graph (P2).
- **Observations** — the object is a *value or statement* about one entity: `(Acme) · headcount · 600`,
  `(Acme) · fiscal_revenue · $5M (FY2023)`. A value like `600` or `$5M` is not an entity and by D18 can
  never be a graph node — so observations are **not** graph edges and never enter P2.

Both carry the same bi-temporal windows and the same supersession machinery *in spirit*, so a changing
headcount time-travels exactly as a changing employment does. They differ in two ways that matter:

| | Relations | Observations |
|---|---|---|
| object | another **entity** | a **value/statement** about the subject |
| typed by | a **governed predicate** (registry) | **nothing** — no attribute vocabulary |
| supersession slot | `(subject, predicate, object)` exact key | the **resolved entity** (+ semantic narrowing) |
| projects to graph (P2)? | **yes** (D18) | **no** |
| projects to search (P1)? | yes (fact-label) | yes (observation label + optional value) |

**Why two tables, not one merged "facts" table.** A relation and an observation can never describe the
*same* belief (entity-object vs value-object are disjoint), so the D6 "one belief home" rule — which
exists to stop the *same* fact living in two places and drifting — is not at stake. Two simple,
disjoint layers beat one polymorphic table whose every row must carry both an entity-object arm and a
literal-object arm. (A unified typed table *was* designed and rejected — see §4.)

### Running example

- **Doc B** (a 2024 filing): *"Acme's headcount was 500 at year-end 2023; revenue was \$5M for FY2023."*
- **Doc C** (a 2025 report): *"Acme now employs around 600 people."*
- **Doc D** (a different 2024 source): *"Acme's FY2023 revenue was \$7M."*

E3 writes three observations on the entity **Acme**: headcount-500 (valid from 2023-12-31),
revenue-\$5M (FY2023), revenue-\$7M (FY2023); then Doc C adds headcount-600 (2025). We want:
*"headcount mid-2024?"* → **500**; *"headcount now?"* → **600**; *"FY2023 revenue?"* → **both \$5M and
\$7M, surfaced as a conflict** (never one silently chosen). That is exactly what the layer delivers.

## 2. What an observation is

An observation is a believed value/statement about **one resolved entity**, with bi-temporal validity
and evidence. The row is deliberately lean (full DDL: `postgres_schema_design.md` §9.A):

- `subject_entity_id` — the **anchor**, always a resolved canonical entity. This is the supersession
  blocking key.
- `statement` — the canonical natural-language form of the observed fact ("Acme's headcount is 600",
  "Acme's FY2023 revenue was \$5M"). This holds **everything** the value carries — the number, the unit,
  and any reporting period ("FY2023") — and is embedded in the P1 fact projection for retrieval.
  There is deliberately **no structured `value` column, no period column, no fingerprint**: the value and
  the period are matched *semantically* by the adjudicator, exactly the way the *property* is (see §3).
  This keeps the layer fully consistent with its own untyped premise — nothing about a fact is given a
  typed slot. (If cross-entity numeric range queries — "all entities with revenue > \$5M" — ever become a
  real requirement, an optional structured `value` is a clean, additive change at that point; it is left
  out now because the core memory use is entity-anchored recall, not cross-entity range scans.)
- the **bi-temporal** quartet (`valid_from`/`valid_until` = world-validity, `ingested_at`/`invalidated_at`
  = belief-time), a generated `status` mirror of `invalidated_at` (one validity home, D6),
  `evidence_count` (distinct current-testimony lineages supporting — D54) / `contradict_count`
  (distinct current-testimony lineages contradicting — note this is distinct from *conflicting
  observations*, which are linked by `contradiction_group`),
  `confidence`, and `contradiction_group` (set when two observations conflict and both must stand).

There is **no governed attribute key, no `value_domain`, no `unit_dimension`, no `cardinality`, no
structured value/period column, and no typed exclusion constraint.** The only DB guards are FK-to-entity
and basic temporal sanity. Everything about *which observations are the same thing* and *whether a new
one supersedes* is decided by the
adjudicator (§3), not the schema. (`status` reuses the existing `relation_status` enum — `active` /
`invalidated` — intentionally, as a common fact-status type shared with relations.)

## 3. Supersession by blocking + adjudication (the heart)

This reuses the exact pattern relations use for supersession (D4) — *block cheaply, then adjudicate the
small residue* — with one change: **block on the resolved entity, not on a typed predicate.**

When a claim asserts a value/property about entity *E*:

1. **Block (exact, exhaustive).** Fetch *E*'s **live** observations: `WHERE subject_entity_id = E AND
   invalidated_at IS NULL`. Indexed; most entities have few observations, so this is cheap and — this is
   the key property — **exhaustive for that entity**. Nothing about *E* can be missed.
2. **Narrow (only for hubs).** If *E* has many observations, rank them by **embedding similarity** to the
   new statement over open observation **statements** (versioned write-path vector cache; ordering
   only — not P1 membership) to choose *which to compare first*. This is an ordering optimization,
   not a membership filter: the entity block already makes **all** of *E*'s live observations
   *available* (an exact key — no clustering can hide one). Crucially, because `supersede` requires a
   **positive** match (step 3), a prior that top-k ranking happens to skip yields at worst a
   *duplicate coexisting observation* to reconcile later — **never** a wrong supersede. (Contrast pure
   clustering, where a mis-clustered prior is invisible and silently duplicated.) P1 fact
   vectors remain a separate retrieval projection and are not required at write time.
3. **Adjudicate (cheap-first cascade, D4).** Every outcome below first requires a **positive match on the
   same thing** — the adjudicator reading both `statement`s and judging *same property* (and, for a
   period figure, *same reporting period* and *compatible value*) **semantically**, exactly the way it
   judges "same property". There is no typed period/value column to lean on; "FY2023" vs "fiscal 2023"
   is an LLM equivalence call, just like "headcount" vs "staff count". Then:
   - **evidence** — same property, same value, overlapping validity → don't insert a row; add evidence to
     the existing observation and bump `evidence_count`. Collapse is **adjudicated (best-effort)**.
   - **supersede** — same property, an **effective state** (headcount/balance/status), value *changed*
     over time → **cap** the prior observation's `valid_until` at the new `valid_from` and insert the new
     one. The prior stays *true for its window* (`invalidated_at` stays NULL). (Headcount 500 → 600.)
   - **contradict / coexist** — same property **and** same reporting period, *incompatible* value → both
     stand, with a shared `contradiction_group`; the recipe surfaces both, the system never picks. (FY2023
     *revenue* \$5M vs \$7M conflict; FY2023 *revenue* vs FY2023 *profit* do **not** — different property;
     FY2023 vs Q1-2023 do **not** — different period.)
   - **new** — a different property, period, or thing → insert independently, no interaction.

   *(The D55 `retract` path — a living document withdrawing a fact's sole support — routes
   through this same state-vs-measurement judgment: a withdrawn **state** observation gets its
   `valid_until` capped; a withdrawn **measurement** gets `invalidated_at` instead, because the
   no-cap rule below holds even under retraction — the figure stays true of its period; what
   ends is the belief. Recorded as `retracted_source_removal`.)*

   **The no-cap rule (D43).** A `supersede` (capping `valid_until`) applies **only** to a *changing
   effective state*. A **measurement / fixed-period figure** ("FY2023 revenue") is **never** capped on
   valid-time — it does not stop being true at period-end; its window stays open, and a conflicting
   same-period figure goes to *contradict/coexist*, never *supersede*. The adjudicator decides
   state-vs-measurement from the `statement` (semantic), not from a typed column. This is the rule that
   replaces the dropped `about_period` columns: rather than recording the period structurally, the
   system simply never caps a period figure and lets same-period conflicts coexist.

   **Temporal compatibility — the deterministic rung (D106).** Before any model call, the
   adjudicator reads what the claim already knows about *when*: its D41 `claim_valid_kind` and
   resolved window. A claim that describes a **datable event** (`event_time` — a win, a visit, a
   purchase; the extractor resolved "last Saturday" to a calendar day) is compared to the
   candidate's own event window, which is the span of the event-time claims supporting it.
   A resolved start with no end is a D41 *open* interval and stays unbounded (it overlaps every later
   window). Then:
   - **both dated events, disjoint windows → `evidence` and `supersede` are forbidden.** Two things
     that happened on different days are different occurrences — *unless* two sources are disagreeing
     about the date of one named occurrence ("the Valorant final on Friday" vs "…on Saturday"), which
     only the date-aware model can tell. So the pair is still judged, but may only `contradict` (both
     stand, grouped) or stay `new`; an `evidence` or `supersede` verdict is coerced to `new` and
     recorded with its reason. This holds even when the wording is byte-identical — "won a tournament
     last week" said in January and again in October is two wins, not one re-asserted — and identical
     text is then kept apart *without* a verdict (the exact-statement shortcut obeys the same rule).
   - **one dated event, one undated statement → `evidence` is forbidden.** The pair may still be
     judged for supersede/contradict (a dated resignation can end a "is CEO" state), but a verdict
     of `evidence` is coerced to `new` and recorded with its reason; identical text is again kept
     apart without a verdict. A specific dated event is never a re-assertion of a vaguer statement
     ("has been winning a few tournaments"), and a summary never re-asserts a specific event — both
     stand.
   - **both undated, or both dated with overlapping windows → the ladder as before.** A re-mention
     of the *same* event (same resolved date, or a year-level and a day-level window for one
     occurrence) still collapses as evidence; headcounts, revenues and stances are untouched.
   The verdict prompt shows, for each statement, when the source *said it* and what world-time it *is
   about* (the resolved window of any D41 kind, not only events), and defines both, so the model
   judges with the timelines in front of it rather than two bare strings. Within one batch a block row
   that absorbs evidence widens its in-memory windows, so a later overlapping claim is not split off
   as a different occurrence. Every adjudication record carries the coercions that preceded it. The
   rung is the "deterministic value/period compare" step the cascade below always named; it is what
   keeps recurring same-shaped events countable — the failure it closes is recorded in
   `plan/analysis/locomo_conv42_recurring_event_adjudication.md`.
4. **Fail safe — a binding adjudicator contract (not just a hope).** This is the honest core of the
   untyped design: "never silently resolve" is **policy enforced in E3 + eval**, not a schema invariant.
   The binding rules:
   - A **`supersede`** (capping a prior `valid_until`) is permitted **only** against a *positively
     matched* prior whose adjudicator margin clears an explicit threshold, **and every cap writes an
     `observation_adjudications` reason row.** No silent caps.
   - Below threshold, or on any *incomplete* comparison, the outcome **must** be `coexist`/`new` —
     never `supersede`. Coexisting is always safe (both surfaced); a wrong-but-confident supersede is
     the one forbidden outcome, and these rules make the *failure mode* a duplicate, not an overwrite.
   - A **contradiction precision/recall eval gate** on the golden set is an **acceptance criterion** for
     shipping the adjudicator (this is the E2/E3 eval harness flagged as unowned in `questions.md` — it
     becomes load-bearing here).
   That contract — not the DDL — is how "contradictions are surfaced, never silently resolved"
   (requirements_v3) is honored without a typed schema gate. The design does not pretend the DB
   guarantees it.

### Worked examples

**Supersede — a changing headcount.**

| step | what happens |
|---|---|
| Doc B → "headcount 500 (year-end 2023)" | block on Acme: none match → insert **O1** (`statement`="Acme headcount 500", `valid_from`=2023-12-31 normalized from "year-end 2023", open). Headcount is a *changing state*. |
| Doc C → "≈600" (2025) | block on Acme finds O1; positive same-property match, an effective state, later value → **cap O1** (`valid_until` ≈ 2025-01, normalized from the report date), insert **O2** ("…600", open). An `observation_adjudications` row records `outcome=supersede` with its reason. |
| query *"headcount mid-2024?"* | `subject=Acme ∧ semantic≈headcount ∧ valid_from ≤ t < valid_until ∧ invalidated_at IS NULL` → **O1 = 500**. |

(`valid_from`/`valid_until` are normalized from the source's temporal language — "year-end 2023", "as
of", "now" → the report date — via the claim's D41 asserted validity, not from ingestion time.)

**Both-stand — conflicting FY2023 revenue.** Doc B "FY2023 revenue \$5M" and Doc D "FY2023 revenue \$7M"
each become an observation whose `statement` carries the value *and* the period. The adjudicator reads
both and judges same-entity + **same property (revenue)** + **same period (FY2023)** + incompatible value
→ and, by the **no-cap rule**, a fixed-period figure is never superseded → **contradiction**: both kept
with open windows and a shared `contradiction_group`; the recipe returns both. No typed
period/`measurement_period`/`cardinality` column was needed — the period match is the same kind of
semantic judgment as the property match. Correctly **not** a conflict: FY2023 *revenue* vs FY2023
*profit* (different property), or FY2023 vs Q1-2023 revenue (different period) — both coexist.

**No recall hole.** Suppose Acme has 40 observations. A new headcount claim blocks on Acme (all 40 found,
exact), then semantic-ranks to compare against the existing *headcount* one first. Even if the semantic
rank were poor, the prior headcount is still *in the candidate set* (the entity block is exhaustive) — at
worst the adjudicator spends a bit more. Contrast a pure semantic-cluster approach, where a mis-clustered
prior headcount would be **invisible** and silently duplicated. Anchoring to the resolved entity is what
buys this.

**Distinct events — seven tournament wins (D106).** A conversation reports, months apart, "I won
my first tournament last week", "won my second tournament last week", "won another regional
tournament", "won my fourth tournament on Friday", "won an international tournament yesterday",
"won a really big tournament last week", "won the Valorant final last Saturday". Every claim is a
dated event (`event_time`, day precision, all seven windows disjoint). Without the temporal rung a
small model reading only the two strings judged October's "won a really big tournament last week"
to be `evidence` for January's "won his first tournament last week" — both say "last week" — and
folded the international and Valorant wins into "has been winning a few tournaments"; the entity
ended with four win facts and a count question answered "at least five" against seven. With the
rung: each new win is still judged against the similar earlier wins and the undated summary, but
with both timelines in the prompt, and whatever the model answers, an `evidence` or `supersede`
verdict against a disjoint-window win — or an `evidence` verdict against the summary — is coerced to
`new` and recorded → **seven observations, one summary**. A second mention of the *same* win (same
resolved day) still collapses as evidence onto it, and two sources disagreeing about *which* day one
named final was won may `contradict`, so both stand grouped.

### Supersession appends — an observation is a time-slice, never an in-place edit

An observation is **a time-slice of belief, not a mutable current-state record.** Supersession does two
things, *neither of which touches the old `statement`*: it (1) **caps** the prior observation's
`valid_until` (to the successor's `valid_from`) and (2) **inserts a new row** for the new value. The old
row persists, `statement` intact:

| | id | statement | valid_from | valid_until | invalidated_at | status |
|---|---|---|---|---|---|---|
| after Doc B | O1 | "Acme headcount 500" | 2023-12-31 | NULL | NULL | active |
| after Doc C supersedes | O1 | "Acme headcount 500" *(unchanged)* | 2023-12-31 | **2025-01 (capped)** | NULL | active |
|  | O2 | "Acme headcount 600" | 2025-01 | NULL | NULL | active |

- **The "current state" is a query, not a row.** The live value is `valid_until IS NULL` (or covering
  `now`) `AND invalidated_at IS NULL` (→ O2 = 600); the history is the *set* of rows (→ "headcount in
  2024?" = O1 = 500). Overwriting O1's statement would destroy that history — the whole reason
  supersession appends rather than rewrites (same as relations, D3, and the immutable-claims principle).
- **`status='active'` ≠ "current value".** `status` only mirrors `invalidated_at` — *"do we still
  believe this was true?"*. O1 stays `active` after being superseded, because we still believe Acme had
  500 *then* (it *ended*, it wasn't *wrong*). "Is it the value now?" is the valid-time question; "was it
  ever retracted as a mistake?" is `status`/`invalidated_at`.
- **Never changes:** `statement` and the identity it carries. **Can change:** `valid_until` (the cap),
  `invalidated_at` (only on learning it was *wrong*, not merely ended), the cached
  `evidence_count`/`contradict_count`/`confidence`, `contradiction_group`. A *correction* to a value is a
  **new** observation (or invalidate + new), not an edit. (By the no-cap rule, a fixed-period figure is
  never even capped — a conflicting figure becomes a second coexisting row.)

### The add-observation worker, cheapest-first

Governing principle (from D4): **write-side LLM cost scales with *ambiguity*, not volume** — most writes
resolve with zero LLM calls; only the ambiguous residue escalates. The worker arrives with upstream work
already done in the E2/E3 extraction call — **don't redo it**: the **subject entity resolved** (the ER
T0–T4 cascade), the `statement`, the **asserted validity** (D41), the routing decision (single-entity
value → observation), and often the claim's embedding (E2 embeds claims for P1).

1. **Block (indexed, no LLM).** Fetch the entity's live observations via the `ix_observations_block`
   partial index (`subject_entity_id` + `invalidated_at IS NULL`). One lookup; few rows for most
   entities — the exact, exhaustive candidate set.
2. **Novelty gate (cheap signals, no LLM — most volume exits here).**
   - **zero candidates** → **new**: insert + link evidence.
   - **exact / near-exact** match (same property + value + overlapping window, via precomputed-embedding
     or lexical compare) → **evidence**: add an `observation_evidence` row, bump `evidence_count`, **no
     new observation**. *(The "200 docs say the same thing" corpus-redundancy case — the biggest saver.)*
   - **clear novelty** (similarity below a low, golden-tuned threshold) → **new**: insert + link.
   - For a **hub entity**, the same vector step top-k ranks *which* candidates to compare (cheap math); a
     skipped far candidate costs at most a duplicate row, never a wrong supersede.
3. **Adjudicate the residue only (cheap → frontier).** Only similar-but-not-identical candidates escalate
   the D4 cascade: the deterministic temporal-compatibility compare (§3 step 3, D106 — it bounds which
   verdicts a pair may take, and decides identical text alone) → small model → frontier LLM for the
   survivors. The
   adjudicator decides same-property (+ same-period for a figure) and the outcome under the no-cap rule
   (state → supersede; measurement → contradict/coexist; same value → evidence; else new), and **fails
   safe to coexist** below the supersede margin.
4. **Write (one transaction).** Apply the outcome — an evidence row; or cap + new observation +
   `observation_adjudications` reason (supersede); or new observation + shared `contradiction_group` +
   adjudication (contradict); or new (new). The `(observation_id, claim_id)` PK makes retries no-ops.
5. **Label + embed (batched/deferred).** Reuse `statement` as the label where possible (no extra call);
   batch the embeddings that *future* blocking will compare against.

**Where each write lands (the cost ladder):**

| case | frequency | cost |
|---|---|---|
| entity has no live observations (first mention) | high | **0 LLM** — insert |
| exact / near-exact re-assertion (corpus redundancy) | **highest** | **0 LLM** — evidence-collapse |
| clearly novel | high | **0 LLM** — insert |
| similar-but-not-identical | low | deterministic → small model |
| genuinely ambiguous | lowest | frontier LLM |

**Cost levers:** the block is one indexed lookup, not a scan; the two no-LLM exits (first-mention and
exact-duplicate) absorb the bulk of volume; precomputed embeddings drive the novelty gate and hub
narrowing; upstream work (entity, embedding, asserted validity) is reused; claims are **batched per
entity** (a doc naming Acme 5× → one block fetch + batched adjudication); and the
`observation_adjudications` log makes decisions **replayable on rebuild** instead of re-calling the LLM
(D7). It is deliberately the *same* cascade and thresholds as relation supersession (D4) — blocked on the
resolved entity instead of `(subject, predicate)` — so it's one engine shared across both layers.

## 4. Why no typing (the honest rationale)

A fuller design — a **unified, typed `facts` table** with a governed attribute vocabulary
(`value_domain`, `unit_dimension`, `cardinality`) and DB-enforced, schema-gated literal supersession —
was designed and **rejected** (preserved in a closed PR, not on main). The reasons, plainly:

- **The attribute space is not enumerable.** Trying to register every property a source might assert
  about an entity fragments exactly like free-text predicates would, and forces extra LLM typing calls
  whose output is brittle.
- **The typing existed only to make literal supersession *schema-enforced*.** If supersession is
  *adjudicated* (as relations always have been), none of `value_domain`/`unit_dimension`/`cardinality`
  is needed — the adjudicator decides value-equality and supersede-vs-coexist at comparison time, on the
  same LLM call it already makes.
- **Merging graph and non-graph facts under one polymorphic table** is a heavy mental model (an
  entity-object arm XOR a literal-object arm, multiple exclusion constraints, registry-locking triggers)
  for no truth-level benefit, since the two are disjoint (§1).

What we give up by going untyped is real and named in §7: value-equality and supersede-vs-coexist become
**adjudicator judgments + an eval gate**, not schema invariants. For a *memory* system (not a financial
ledger), dexterity and a simple mental model are worth that trade — and the fail-safe-to-coexist default
keeps the one hard guarantee intact.

## 5. Retrieval — through the projections

Observations are **storage**; queries go through projections (D9), exactly like everything else:

- **P1 / PostgreSQL** — each observation's `statement`/label is embedded for **semantic search**:
  *"what has Acme's headcount been over time?"* resolves Acme, pulls its observations, orders by
  `valid_from`.
- **P2 / graph** — observations are **never** projected (a value is not a node — D18). Only relations
  reach the graph.
- **as-of** — the same bi-temporal filter as relations: valid-time (`valid_from ≤ t < valid_until`) for
  "what was true at T", transaction-time (`ingested_at ≤ t < COALESCE(invalidated_at, ∞)`) for "what did
  we believe at T". A **period** query ("FY2023 revenue?") is a *semantic* lookup over `statement`
  (Acme + revenue + FY2023), not a structured-column filter — and because a period figure is never
  capped, both conflicting `contradiction_group` members come back (both surfaced).

The cost of untyped storage shows up here: a query for a *specific* property or value is **semantic**
(over `statement`/label), not an exact typed-key or numeric-column lookup. That is what P1 is built
for. The one thing it does *not* do cheaply is a **cross-entity numeric range scan** ("all entities with
revenue > \$5M") — there is no structured `value` to range over. That is the explicit price of dropping
the typed columns; if such scans become a real requirement, an optional structured `value` is a clean
additive change at that point.

## 6. Schema essentials

Full DDL in `postgres_schema_design.md` §9.A. Three tables, mirroring the relations trio but leaner:

- **`observations`** — the row in §2; entity-anchored, bi-temporal, generated `status`,
  `contradiction_group`, the `statement` + label/embedding refs; FK to `entities` + temporal CHECKs;
  **no structured value/period columns, no typed EXCLUDE, no attribute FK.**
- **`observation_evidence`** — many-to-many join to claims (`HASH(observation_id)`, PK
  `(observation_id, claim_id)` = evidence-once); where corpus redundancy collapses into `evidence_count`.
- **`observation_adjudications`** — the append-only "why" transcript (supersede / contradict / merge),
  by cascade rung, so the non-deterministic adjudication is replayable on rebuild (D7).

## 7. Consequences, residuals, and what to measure

**Invariants preserved:** claims immutable (D2/D3); the graph holds only entities (D18 — observations
never project); validity has one home per fact (D6 — `status` is generated; relations and observations
are disjoint so they cannot drift); asserted-validity feeds but never overrides the believed window
(D41).

**Residuals — measure before locking (thresholds are starting points, not constants):**
1. **Supersede-vs-coexist is an adjudicator judgment, not a schema gate.** A confident-but-wrong
   `supersede` could silently resolve a conflict that should both-stand — the design does **not** pretend
   the DB prevents this. The safeguard is the **binding adjudicator contract of §3.4** (supersede only on
   a positively-matched prior above an explicit margin, with a persisted reason; else coexist) plus a
   contradiction **precision/recall eval gate** that is an **acceptance criterion** for shipping the
   adjudicator (the E2/E3 eval harness — `questions.md` flags it as unowned; this design makes it
   load-bearing and gives it an owner). This is the single biggest correctness risk and the price of
   dropping the typed gate; it fails toward *duplicate coexisting rows*, never silent overwrite.
2. **Adjudication cost.** Each value-claim does an entity block (cheap, indexed) + occasional semantic
   narrowing + an adjudication call — comparable to relation supersession. Load-test the per-write cost
   and the hub-entity tail (entities with many observations) at corpus scale.
3. **Specific-property and same-period matching are both semantic.** With no typed property/period
   column, "is this the same property?" and "is this the same reporting period?" are LLM judgments
   (e.g. "fiscal 2023" ≡ "FY2023"). Measure this on the contradiction golden set; if same-period recall
   proves weak, the additive fallback is an optional canonical period field — not a governed vocabulary.
   No cross-entity numeric range scan is supported (no structured `value`); see §5.
4. **Observation volume vs. relations.** Size `observations`/`observation_evidence` against full
   extraction; decide partitioning by the same logic as `relation_evidence` (`HASH` by id).

**Out of scope (documented non-goals, not deferrals):** observations do **not** carry a governed
attribute vocabulary or schema-enforced supersession; they never enter the graph. **Attributed
stance is IN scope (D59, resolving the former qualitative-belief question):** "Bob opposes the
pricing change" is an ordinary observation anchored on Bob — an effective state (a changed mind
supersedes; "what did Bob think in March?" is an as-of query; conflicting same-time reports
coexist via `contradiction_group`), on this design's machinery unchanged. *Unattributed*
opinion remains dropped at E2 Selection (D31/D34/D59) and becomes neither a relation nor an
observation.

## References

Decision: `decisions.md` D43 (and D2, D3, D4, D5, D6, D8, D18, D41). Schema:
`postgres_schema_design.md` §9.A (`observations`/`observation_evidence`/`observation_adjudications`).
Normalization: `e2_e3_claims_relations_design.md` §5. Explainer: `plan/analysis/concepts.md`. Open
items (the E2/E3 eval harness): `questions.md`. Attributed stance: D59.
