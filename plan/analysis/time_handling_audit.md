# Time-handling audit: where the engine reads the wrong clock

**Date:** 2026-09-03

**Status:** analysis (non-binding). The decision it motivated is D107; the
binding contract is `plan/designs/temporal_clocks_design.md`.

**Revision audited:** `02b79904` (the merge of D106). Line numbers below are
for that revision; function and statement names are the stable anchors.
Findings 4.2, 4.5, 4.6, 4.12, 4.13, 4.14 and 4.15 were narrowed after four
independent Codex reviews verified each against the code; 4.18–4.22 were
added from those reviews.

## 1. Question

D106 fixed one defect: the observation adjudicator compared two statements
as bare strings and merged "won a tournament last week" said in January with
the same words said in October, although both claims carried resolved D41
windows nine months apart. That defect had a shape — a stage that *has* the
resolved world-time available and reasons on the source's date instead. This
audit asks whether the same shape occurs anywhere else, stage by stage.

## 2. The clocks, so the findings read cold

The engine records three distinct clocks, and the defects are all
confusions between the first two:

| Clock | Column(s) | Meaning | Plain example |
| --- | --- | --- | --- |
| **said-on** | `claims.asserted_at` (from the document's `source_modified_at`) | when the SOURCE said it | the chat session was on 2022-10-06 |
| **is-about** | `claims.claim_valid_kind` / `claim_valid_from` / `claim_valid_until` / `claim_valid_precision` (D41) | the world-time the statement refers to, resolved by the extractor from the wording against the said-on date | "last week" in that session → 2022-09-29 |
| **believed** | `ingested_at` / `invalidated_at` | when the system held the belief | ingested 2026-09-01 |

`claim_valid_kind` says what sort of interval the is-about window is:
`event_time` (a datable event), `measurement_period` (a figure for a span),
`effective_period` (a state that held over a span), `proposition_validity`
(the proposition was true over a span), or `NULL` when the source tied nothing
to a date. A resolved start with `claim_valid_until IS NULL` is an *open*
interval (unbounded end), not a point.

The fact layer — `relations.valid_from/valid_until` and
`observations.valid_from/valid_until` — is meant to hold the adjudicated
world-time of each fact (D3, D43): the window a caller filters on with
`valid_at` / `facts_as_of`.

## 3. Patterns

Each finding is tagged with the pattern it instantiates:

- **P1 ignored** — resolved timing is available on the row and not read.
- **P2 conflated** — the said-on clock is used where the is-about clock is meant.
- **P3 precision lost** — an interval is stored or compared as a point, or a
  timestamp is truncated before use.
- **P4 blind model** — a model is asked a time-dependent question without
  the dates it would need.
- **P5 wrong sort key** — ordering, deduplication, or supersession uses the
  wrong clock.
- **P6 silent degrade** — a missing time is replaced by a value that changes
  the meaning (typically `now()`).

## 4. Findings, ordered by likely impact on answers

### 4.1 Retrieval dedupes testimony by text only — the D106 defect, one layer up (P1, P5)

`src/rememberstack/surfaces/query_engine.py` — `_group_claim_evidence`
(`:3388-3405`), invoked from `_testimony_context_retrieval` with
`group_exact_text=True` (`:2230`), which serves both `testimony_context` and
`answer_context` (`surfaces/operation_executor.py:75-117`).

```python
grouped.setdefault(_normalize_hybrid_text(value=record.claim_text), []).append(record)
...
members[0].model_copy(update={"corroboration_count": len({m.doc_id for m in members}), ...})
```

The group key is the normalised claim text. `asserted_at` and the four
`claim_valid_*` fields are on every `EvidenceResult`
(`model/envelope.py:294-298`) and are not consulted. Two claims with the same
words about different dates become one evidence row carrying only the
top-ranked member's dates, with `corroboration_count = 2`. The second date is
absent from the envelope, not merely unlabeled. The conv-42 counting question
escaped this only because the seven win claims were worded differently.

### 4.2 Relations' `valid_from` is never seeded (P1, P6)

`src/rememberstack/spine/fact_catalog.py:78-89`, `:523-533`,
`_LATEST_CLOSED_UNTIL` (`:867-877`). A new relation's `valid_from` is the
`max(valid_until)` of a previously *closed* spell of the same
`(subject, predicate, object)` — `NULL` for every first occurrence — and is
never written anywhere else: not from `claim_valid_from`, not from
`asserted_at`. D41's consequences say the opposite ("a claim 'Alice joined in
March 2024' can seed `works_for.valid_from`"), and the docs promise
`valid_at` answers "when was this true in the world"
(`website/src/app/docs/concepts/page.mdx:80`). `lookup_relations(valid_at=…)`
does filter both ends (`valid_from IS NULL OR valid_from <= :as_of` and
`valid_until IS NULL OR valid_until > :as_of`, `query_engine.py:3696-3697`),
so a *capped* spell is excluded after its end — but with `valid_from` always
`NULL`, no fact can be excluded *before its true beginning*: "who did Alice
work for in 2010?" returns her 2024 employer alongside any earlier one. The
start half of the as-of axis is a no-op on the relation side.

### 4.3 Relation supersession judges "the same period" without seeing any period (P2, P4)

`src/rememberstack/spine/supersession.py` — `_ADJUDICATION_PROMPT`
(`:37-51`), formatted at `:231-238`:

```
EXISTING: {existing_label}
  evidence: {existing_evidence!r} (asserted {existing_asserted})
NEW: {new_label}
  evidence: {new_evidence!r} (asserted {new_asserted})
...
- contradict: the sources describe the SAME period incompatibly
```

Only the said-on dates are shown. `_LOAD_RELATION` (`:436-443`) and
`_BLOCK_CANDIDATES` (`:459-466`) select `c.claim_text, c.asserted_at` and not
the `claim_valid_*` columns on the same row. A 2024 memoir ("Alice worked for
Acme from 2015 to 2018") beside a 2023 email ("Alice works for Zeta") makes
the memoir look newer; `works_for Zeta` is superseded by a historical spell.

### 4.4 Supersession boundaries are the speaking date, and undated ones become `now()` (P2, P6)

`supersession.py:272-282` and `_CLOSE_WINDOW` (`:503-511`);
`observation_adjudication.py` `_CAP_WINDOW` (`:1539-1546`);
`lifecycle.py` `_CAP_RELATION` (`:929-936`, the D55 retraction path):

```sql
SET valid_until = coalesce(:boundary_asserted, now())
```

The cap is the *successor's said-on date* even when its is-about start is
known, and an undated successor caps the predecessor at the wall-clock
instant of ingestion. Consequences: `facts_as_of(valid_at='2026-09-02')`
reports Alice still at Acme the day before an undated CV was ingested, and
the same ingest replayed on another day yields a different history — the
value is not rebuild-stable (D7).

### 4.5 Normalisation and label construction do not propagate the resolved window, so the fact statement keeps "last week" forever (P1, P6)

`src/rememberstack/workers/e3.py:98-120` (normaliser prompt: `CLAIM
(attributed=…): {claim_text}`; no valid-time fields at `:436-441`). E2's prompt
correctly *forbids* writing the resolved date into `claim_text`
(`e2.py:176-179`), so the only place the date exists is the claim row. The
observation `statement` (and `obs_label`) is minted from `claim_text`
(`fact_catalog.py:571-581`, `observation_adjudication.py:1527-1537`), then
embedded by P1 (`workers/p1.py:214, 231-236`), returned as `FactResult.label`
(`query_engine.py:3709`), listed in entity profiles
(`profile_refresher.py:687`), and rendered into K pages. Every fact-grain
consumer reads "Nate won a video game tournament last week" with no anchor.

### 4.6 The answer agent is never told which clock is which (P4, P2)

`benchmarks/locomo/protocol.py:135-137` — the prompt says "Use timestamps to
resolve relative dates" and nothing else about time. The envelopes carry
`asserted_at`, `claim_valid_*`, and `validity.valid_from` (fact grain) side
by side, and `fact_context` does include representative evidence rows with
`asserted_at` and claim windows (`query_engine.py:778-867`); but the direct
relation result's own `Validity` is all-`NULL` for a first-occurrence
relation (4.2), and `_reader_trace_record` (`:344-347`) drops `None` fields,
so on that row the only surviving timestamp is `ingested_at` — the
benchmark's own run clock — while for an observation `validity.valid_from` is
the session date under a name that reads as world-time. The reader is never
told which is which. Contrast `observation_adjudication.py:63-72`, which
spells the distinction out for the verdict model.

### 4.7 K fact sheets label the said-on date "valid since" and sort history by it (P2, P5)

`src/rememberstack/core/knowledge_fact_sheet.py:55-63`, `:72-99`: the
"Current relations" column `valid since` is `fact.valid_from` (always empty
for relations, 4.2) and the observation table's `valid from` is the session
date; "Observation history" is sorted by that same value, so a back-dated
retrospective reads in the wrong order.

### 4.8 The K prose writer's claims carry no dates at all (P4)

`src/rememberstack/spine/knowledge.py:4636-4658` (`_SELECT_WRITER_CLAIMS`)
and `model/knowledge.py:531-543` (`KnowledgeWriterClaim`): `claim_text`,
`source_span`, `document_title`, `source_kind` — no `asserted_at`, no
`claim_valid_*`. The writer is asked to compile durable prose from "last
week".

### 4.9 `aggregate(form="timeline")` buckets by ingest year (P2, P6)

`query_engine.py:4285-4307`: `coalesce(valid_from, ingested_at)` for both fact
kinds. With 4.2, the relation half is entirely ingest time; the observation
half is said-on time. A 2015–2020 archive imported today is one bar labelled
2026, under a docstring that says "an entity's facts by year" (`:2087-2088`).

### 4.10 Observation batch order — who supersedes whom — follows the said-on clock (P5)

`fact_catalog.py:652`, `:725`; `observation_adjudication.py:1614`, `:1630`:
`ORDER BY c.asserted_at NULLS LAST, …`. The order fixes predecessor/successor
(`_is_strictly_earlier(asserted_at, existing_from)`, `:664-665`) and the cap
boundary (`:718-724`). "I moved to Berlin in 2019" said in 2023 caps "I live
in Prague" said in 2022 — the reverse of the world. D106 made the verdict
window-aware; not the ordering.

### 4.11 The two adjudicators disagree about undated testimony (P5, P6)

`supersession.py:545-562` (`_is_source_successor`: "dated testimony is later
than undated" → undated is the *predecessor*) versus
`observation_adjudication.py:1453-1481` (`_is_later_in_total_order`:
`asserted_at NULLS LAST` → undated is the *successor*). One document pair
yields contradictory histories on the two fact planes, and neither is
replay-stable under reordering.

### 4.12 P1 offers a said-on filter and no is-about filter (P1, P2)

`adapters/postgres_p1.py:1371-1400`: *claim* search accepts `asserted_from`
/ `asserted_to` only — the D41 partial index on
`(claim_valid_from, claim_valid_until)` is unused by the search path, so
"Nate's claims about October" returns what was *spoken* in October. *Fact*
search is not time-blind: `_fact_time` (`:1449-1492`) offers as-of and window
selectors over the fact's `valid_from`/`valid_until` — which, per 4.2 and
4.17, hold the wrong clock, so the selector is correct over incorrect
inputs.

### 4.13 Day/month/year precision is stored as a zero-width interval (P3)

`workers/e2.py:181-186` (prompt: "the resolved date as both ISO ends"),
`_parse_iso_timestamp` (`:660-667`, date-only → midnight UTC);
`_CLAIMS_AS_OF_CANDIDATES` (`query_engine.py:3585-3600`) intersects closed
intervals. A day-precision May-7 claim is `[May 7 00:00, May 7 00:00]`, so
"what held during May 7, 09:00–23:00" returns nothing; a year-precision 2022
claim ends at 2022-12-31 00:00. D106's `_windows_disjoint` compares with
strict `<`, so two *equal* points do overlap; but a day-precision claim and an
instant-precision claim about the same day miss each other, and two
day-precision claims about adjacent days touch at a point neither covers.
(Two genuinely instant-precision events at different times of one day *are*
disjoint, and correctly so.)

### 4.14 E2 teaches only `event_time` (P4)

`workers/e2.py:167-209`: all three worked examples are `event_time`;
`measurement_period`, `effective_period`, `proposition_validity` are never
named; `open` gets one structural mention and no example; `CandidateClaim`
(`model/claims.py:158-168`) has no field descriptions. The kinds are exposed
structurally, so the model *can* emit `proposition_validity` / `open` for
"Alice has been CEO since 2019", but nothing teaches it their semantics; how
often it does is unmeasured. Whatever it emits instead, D106's rung fires on
`about_kind == "event_time"` only, so mislabelled kinds silently change which
pairs the rung governs.

### 4.15 The document header shown to E2 drops the time of day (P3)

`workers/e2.py:899-906`: `date {modified.date().isoformat()}` while
`asserted_at` keeps the full timestamp (`:599`). LoCoMo sessions carry wall
times; offset expressions ("three hours ago") cannot resolve to an instant,
and two same-day sessions are the same anchor. (Part-of-day expressions such
as "this morning" have no precision in the D41 enum and are an expressivity
boundary, not an anchor defect.)

### 4.16 Entity profiles and the T4 candidate list carry no dates and rank by row-touch time (P4, P5)

`spine/profile_refresher.py:838, 840, 877, 889, 677-695`;
`spine/resolver.py:1158-1175`: salient facts are bare strings ordered by
`evidence_count DESC, updated_at DESC` — `updated_at` is bumped by every
recount and label write (`fact_catalog.py:556, 616`). Two people named Nate
five years apart present to T4 with no temporal separation; a fact recounted
today outranks a heavily evidenced older one. The `valid_until IS NULL` gate
combined with 4.4 means one undated supersession removes a fact from every
profile and from T3 embeddings.

### 4.17 `_pull_valid_from_earlier` widens the stored window with the speaking date (P2, P1)

`observation_adjudication.py:917-962`: on evidence collapse the persisted
`valid_from` moves to the earliest `asserted_at`, while `_absorb_timing`
(`:1357-1383`) simultaneously widens the correct is-about window in memory and
discards it — nothing persists it (`_INSERT_OBSERVATION:1527-1537` stores no
window). Two sessions re-asserting one event leave the row's world-window at
`[earliest session, ∞)` although the event window `[2023-09-29, 2023-09-29]`
was computed one function away.

### 4.18 The consumption skill defines `claims_as_of` as a system-time query (P2)

`src/rememberstack/core/consumption_skill.py:191-194`: "`claims_as_of` means
what sources asserted as of a past **system** time" — D41 defines it over the
claims' *world*-time window, and the implementation filters
`claim_valid_from/until` (5. below). The skill's "Time and media" section
(`:202-210`) teaches fact validity versus ingestion but not the said-on /
is-about distinction. Under D60 the skill is part of the complete agent-facing
surface, so agents are taught the wrong clock in the one place meant to teach
clocks.

### 4.19 The open-query confirmation surface truncates the clocks (P1, P4)

`src/rememberstack/surfaces/query_sandbox/nomination.py:323-327`
(`_CONFIRM_SQL["claims"]`) confirms claim search rows with `asserted_at`,
`claim_valid_from`, `claim_valid_until` — and omits `claim_valid_precision`
and `claim_valid_kind`; the fact-row confirmation carries no verdict or
occurrence time at all. An agent on this public path cannot tell a point from
a coarse period, an open interval, or an unknown, even after is-about filters
exist.

### 4.20 Aggregates and predicate absence never evaluate `valid_until` (P1)

`src/rememberstack/surfaces/query_engine.py` — `_AGG_COUNT` (`:4246`) and
its group variants, and `_AGG_PREDICATE_ABSENCE` (`:4335`) select "live"
relations by `invalidated_at IS NULL` alone. Today every relation is
open-ended (4.2), so the omission is invisible; once a state can carry a
finite end, an expired relation keeps counting and can block a true absence
answer, against D49's rule that current fact-grain answers are
validity-filtered.

### 4.21 The shipped `claims_as_of` example cannot count what it excludes (P3)

The saved `claims_as_of` example reports its `unknown`-precision exclusion
by combining `precision = 'unknown'` with a non-null bound comparison; D41's
`CHECK` makes an unknown-precision claim's bounds `NULL`, so the count is
permanently zero. (Reported by the third independent review; the fix rides
the query-space canonicalisation of design §5.)

### 4.22 D55's observation close is shape-blind (P6)

`src/rememberstack/spine/lifecycle.py:438-468` (`close_observations`)
invalidates every withdrawn observation, by its own docstring because
"observations are untyped (state vs measurement is semantic)" and
`invalidated_at` is the exit safe for both shapes. D55 and
`evidence_lifecycle_design.md:153-165` ask for a per-shape judgement — a
withdrawn effective state caps its world-time, a withdrawn measurement keeps
its window and closes belief only. Without a temporal kind the code cannot
make that judgement, so a withdrawn state observation is recorded as *never
believed* rather than *ended*.

## 5. Checked and sound

Absence of a finding above is a result, not an omission:

- **E2's D41 parse/validate gate** (`workers/e2.py:607-713`) — rejects naive
  datetimes rather than inventing a zone, mirrors every DB CHECK, refuses to
  promote a date-only input to `instant`, degrades the temporal fields to
  unknown without dropping the claim; `asserted_at` comes from
  `source_modified_at or published_at` (`:599`); the header reaches every
  chunk's bundle (`:731, :759`).
- **D106's rung** (`observation_adjudication.py:1304-1354, 1398-1424,
  1503-1512`) — `None` ends are unbounded, incomparable values fail safe to
  "overlap", one open supporting claim keeps the aggregate open; its prompt
  (`:55-98`) is the model the other prompts should follow.
- **`claims_as_of`** (`query_engine.py:522-587`, `_CLAIMS_AS_OF_CANDIDATES`)
  — filters on the is-about columns, refuses `unknown` precision, orders by
  `claim_valid_from`, discloses `excluded_unstamped`; only 4.13's boundary
  semantics apply.
- **The evidence-grain envelope** (`model/envelope.py:280-302`) — carries all
  five D41 fields beside `asserted_at`, and the `EvidenceResult` hydration
  queries select them (`query_engine.py:3881-3933`, `:3995-3996`). D41's
  promise that evidence payloads surface `claim_valid_from/until` is kept
  there; the open-query confirmation path is the exception (4.19). The
  fact-grain `Validity` (`:171-179`) is where the conflation lives.
- **Belief-time plumbing** (`query_engine.py:3463-3483`; migration
  `p9_03_0024_facts_as_of.py:92-95`) — the transaction-time axis is separate
  and sound.
- **Query-space catalog typing** (`spine/query_space/catalog.py:371-434,
  447-509, 869-916`) — claim views expose all five D41 columns to open SQL.
- **D55 retraction for relations** (`spine/lifecycle.py:412-437`) — caps at
  the withdrawing version's source time; only the `now()` fallback (4.4) is
  wrong. (The observation side is not sound — 4.22.)
- **Adjudication transcripts** (`supersession.py:298-307`;
  `observation_adjudication.py:553-571, 741-751`) — every cap boundary and
  every D106 coercion is recorded, so all of the above is diagnosable from
  the audit tables without a rerun.

## 6. Why this is one decision, not twenty-two fixes

Every finding is the same confusion: the engine resolves world-time once, at
extraction, and then reasons on the source's date. The fix that closes all of
them is a single contract — world-time flows from the claim's is-about window
into fact windows, temporal succession, prompts, dedupe keys, and consumer
surfaces; said-on time is provenance, never validity; a missing time stays
missing rather than becoming `now()`. That contract is D107 and
`plan/designs/temporal_clocks_design.md`; the sequencing is
`plan/plans/temporal_clocks.md`.
