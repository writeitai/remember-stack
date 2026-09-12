# Mutable fact windows: what to observe after the cutover

**Status:** non-binding operational analysis, 2026-09-11. Companion to
[D118](../designs/mutable_fact_windows_design.md) and its
[application contract](../designs/mutable_fact_application_contract.md).

The replacement runtime was reviewed and tested before any real corpus ran
through it. Several of its choices are sound on paper but depend on how the
model and the data actually behave. This page lists those points, what could go
wrong, the signal that would show it, and the adjustment we would consider. It
exists so that the first production corpora are watched deliberately rather than
discovered by complaint. Each item names the concrete place to look.

## 1. One model decision carries a lot

An ordinary fact decision names identity, an optional full window replacement,
window updates on other supplied facts, support moves between facts, contradiction
groups, a stance, a confidence and a rationale, all in one temperature-zero
structured answer. That breadth is what removes the separate correction and
dispute machinery, but it asks the model to do several jobs at once. The
confidence floor (0.75, a starting value) turns low-confidence answers into
conservative coexistence, which protects against destructive mistakes but can
also hide that the model is unsure most of the time.

- **Signal.** In `relation_adjudications` and `observation_adjudications`, the
  share of rows whose `features->'decision'` carries non-empty `updates` or
  `support_moves`, the share of `update` outcomes, the distribution of
  `confidence`, and how often the coexist fallback fired (rationale prefix
  "Confidence below").
- **Healthy.** Updates and support moves are rare and, when sampled, read as
  genuine corrections. Confidence clusters well above the floor.
- **Unhealthy.** Frequent support moves or window rewrites that a human would
  not make, or most answers sitting near the floor.
- **Adjustment.** Narrow the default answer (target, stance, own-window,
  contradictions) and require a second explicit pass for support moves and
  edits to other facts, or raise the floor. Either is a contract change.

## 2. A forgotten witness clears the whole window

`window_claim_ids` names every claim cited for the current window. Hard forget
of any one of them sets the window to unknown even when other cited claims
survive. This is deliberate: the engine must not keep dates it can no longer
show grounding for, and re-deriving a window during erasure would put a model
call inside the forget path. The cost is that one forgotten source can undate a
well-supported fact until the next ordinary adjudication on that entity happens
to revisit it.

- **Signal.** After each hard forget, the count of facts whose window went from
  known to unknown while `evidence_count` stayed above zero
  (`relations`/`observations` rows with `valid_precision='unknown'` and
  `evidence_count > 0` whose transcript shows a prior known window).
- **Healthy.** Rare, and those facts regain dates the next time evidence on the
  entity is processed.
- **Unhealthy.** Long-lived undated facts with surviving dated evidence.
- **Adjustment.** Enqueue an ordinary re-adjudication of affected facts after
  forget completes, using only surviving claims as inputs. That is ordinary
  work, not a new store, and it must still run after the purge has finished.

## 3. Precision is an enum, not a boolean

Precision distinguishes explicitly ongoing from end-unknown, which is essential,
and records the source's granularity for rendering, for the adjudicator's
reasoning and for endpoint alignment checks. If the granularity part turns out
to be unused, the column could shrink to an "open" flag and the rest of the
design survives.

- **Signal.** Whether labels, profiles and answers actually differ for
  month/quarter/year windows in a way consumers use, and whether the adjudicator
  cites precision in rationales when reconciling differently dated claims.
- **Adjustment.** Replace the enum with a boolean and render exact endpoints. Do
  this only with evidence; the cost of keeping it is one column.

## 4. Model cost per assertion

Every assertion with at least one candidate fact on its entity costs one
adjudication call. The empty-candidate case is decided without a call
(contract §8). Main's novelty gate also skipped the model for predicates marked
not change-prone; that shortcut no longer exists because identity is contextual
for every predicate.

- **Signal.** In `cost_ledger`, calls in tier `fact_adjudication` per ingested
  claim, and the share decided by `novelty_gate` versus `small_model` in the
  transcript `method` column.
- **Healthy.** Cost per claim comparable to the old normalize plus ladder
  budget once a corpus has settled.
- **Unhealthy.** Cost dominated by adjudication on entities where the answer is
  almost always "new fact".
- **Adjustment.** Measure before adding any deterministic gate. A gate that
  merges by text equality would reintroduce the collapse this design removes; a
  gate that only nominates fewer candidates is safe.

## 5. Candidate nomination is bounded

Nomination takes exact triple or statement matches first, then full-text
relevance, up to twenty facts. Truncation is disclosed to the model, but a fact
outside the window cannot be chosen as the identity. Busy entities (a company
with hundreds of relations) are where recall would suffer.

- **Signal.** Share of prepared inputs with `potentially_truncated` true, and
  sampled cases where a later human review merged facts the adjudicator had
  created separately.
- **Adjustment.** Raise the limit, add predicate-specific nomination, or add an
  embedding-ranked tier. All are nomination changes and need no contract change.

## 6. Undated facts in strict surfaces

Facts with unknown dates are "possible" temporal matches. The retrieval
operations return them flagged; the published `facts_current` SQL view and the
live graph exclude them, because a strict view must not assert that an undated
fact holds now. Conversational corpora are mostly undated, so consumers that read
the strict SQL view instead of the retrieval operations will see very little.

- **Signal.** Consumer questions of the form "why is X missing" answered by "it
  had no date", and the ratio of unknown-precision facts in a corpus.
- **Adjustment.** Publish an additional view that includes possible matches
  with an explicit `temporal_match` column. Do not loosen `facts_current`.

## 7. Normalizer date attribution

Each normalized output says whether the claim's world window applies to it. If
the normalizer says yes too eagerly, unrelated assertions inherit a date; if too
rarely, facts stay undated and fall into item 6.

- **Signal.** Share of outputs with `uses_claim_window` true per claim kind,
  and sampled mismatches between the claim text and the dated assertion.
- **Adjustment.** Prompt wording in the normalizer; no storage change.

Findings against any item go into the analysis corpus first, then into the
design or contract if they change a rule.
