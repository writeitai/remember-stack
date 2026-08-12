# Design review (round 3) — D90 entity-grain observation flush fan-out

**Reviewer / AGENT:** `claude-opus`
**Date:** 2026-08-12
**Branch / PR:** `design/d89-entity-obs-flush-fanout` (#262) @ `08b0d8fd`
**Under review:**
`plan/designs/e3_entity_obs_flush_fanout_design.md`,
`plan/analysis/e3_entity_obs_flush_fanout_analysis.md`,
`decisions.md` §D90
**Prior rounds:**
r1 [claude-opus](REVIEW_claude-opus_e3_entity_obs_flush_fanout_design_2026-08-12.md) (B1–B8),
r1 [codex-sol](REVIEW_codex-sol_e3_entity_obs_flush_fanout_design_2026-08-12.md) (B1–B5),
r2 [claude-opus](REVIEW_claude-opus_e3_entity_obs_flush_fanout_design_r2_2026-08-12.md) (R1, R2),
r2 [codex-sol](REVIEW_codex-sol_e3_entity_obs_flush_fanout_design_r2_2026-08-12.md) (B1),
r3 [grok](REVIEW_grok_e3_entity_obs_flush_fanout_design_r3_2026-08-12.md) (B1, H1–H3)
**Cross-checked against code:** `workers/e3.py`, `spine/work_ledger.py`,
`spine/observation_adjudication.py`, `spine/readiness.py`, `spine/lifecycle.py`,
`spine/fact_catalog.py`, `model/processing.py`, `profiles/selfhost.py`

## Verdict

**APPROVE_WITH_NITS.** All sixteen blocking findings across both r1 reviews and
all three r2 blockers are closed in `08b0d8fd`. Nothing remaining causes a wrong
D43 outcome, a false barrier, silent data loss, or an unimplementable contract.
The two r2 blockers I raised (R1 membership coordinates, R2 `document_version`
row at the fan-out generation) are closed with the exact shape asked for, and
Codex r2 B1 (reverse-arrival multi-version windows) is closed as a binding
co-requisite in §5.5.1 rather than waved off as pre-existing.

I **differ from grok r3** on severity: its B1 (follow-up lease identity) is real
and should be written down, but it is a nit, not a blocker — see N1 for the code
evidence that the design already routes an implementer to the correct shape.

## Summary

The design has converged. The load-bearing choice — a durable version-scoped
flush unit (`obs_flush_entity_units`, `target_id = unit_id`) instead of a bare
canonical `subject_entity_id` — is unchanged since r1 and remains correct: D12
work identity has no version column (`(deployment_id, target_kind, target_id,
stage, component_version)`), canonical entities are deployment-global, and
flushing entity E for V1 is not "done" for V2. §1.2 and analysis §7 both explain
that in plain language for a cold reader, which is the Rule 1 bar.

What r2 changed, and what I verified this round:

- **Membership now carries every coordinate the hand-off needs.** §5.1 adds
  `representation_id`, `chunker_version`, `extractor_version`. That closes the
  silent no-op I found in r2: `AdjudicateSupersessionHandler`'s reconstruction
  arm fires only when `version_id`, `representation_id`, `normalizer_version`
  **and** `chunker_version` are all present (`e3.py:869-886`), and its
  `RECONCILE` chain returns a bare `HandlerOutcome()` without
  `representation_id` (`e3.py:903-907`). §5.4 also now derives the barrier
  advisory-lock key from `obs_flush_version_state`, not a live
  `current_representation_id` lookup — which removes the two-completers-take-
  different-keys missed-fire race.
- **One empty/fan-out state artifact, and it cannot alias a work row.** §5.1
  bans a `document_version` processing row at the fan-out component version
  outright, and §5.7 routes all four shortcut enqueue sites to
  `obs_flush_version_state`. I re-enumerated those sites: `work_ledger.py:713-733`
  (empty extract), `:770-791` (all-claims-already-succeeded hop),
  `e1.py:631-651` and `e2.py:1067-1086` (no chunks). The design's list of four is
  exactly right — the fifth `ADJUDICATE_OBSERVATIONS` producer
  (`work_ledger.py:405-428`, the claim-barrier fire) is the one §5.2 replaces
  with fan-out, so it is correctly absent from the list.
- **Reverse arrival is now a bound post-condition, not an accepted defect.**
  Codex's `t3, t1, t2` case reproduces exactly as it described: capped slices are
  excluded from `open_candidates` (`observation_adjudication.py:250-256`), so
  the third arrival never sees O1 and both `[t1,t3)` and `[t2,t3)` survive
  (`:443-497`); `_pull_valid_from_earlier` does not repair it, since it only
  pulls `valid_from` back on equivalent-evidence collapse and refuses when a
  later cap boundary exists (`:673-718`). §5.5.1 now requires consistency with
  the **full** ordered durable set after each such apply, and §9 carries the
  acceptance case. Crucially, this is implementable rather than aspirational:
  every cap already writes a durable decision row with
  `related_observation_id` and `outcome='supersede'` into
  `observation_adjudications` (`:806-833`, `:988`), so an entity-local recompute
  can reconstruct the changing-state chain from stored outcomes without
  re-running the ladder.
- **Same-entity cross-version scheduling** is now single-flight with a claim
  order `(min_asserted_at NULLS LAST, version_id, unit_id)` (§1.7, §5.5), with
  `min_asserted_at` materialized into membership at fan-out. Correctness no
  longer *depends* on that order — §5.5.1 carries it — which is the right
  layering.

The decision is sound, the contracts are implementable, and the test plan (§9)
covers the cases that actually fail: two versions of one entity, `t3/t1/t2`,
supersession payload reconstruction, zero-chunk empty path, DLQ blocking, and
"no version-wide staging clear on the entity path".

## Prior findings — closed / open

| Round | Finding | Status | Evidence in `08b0d8fd` |
| --- | --- | --- | --- |
| r1 Claude B1 / Codex B1 | Bare entity ledger identity collides across versions | **Closed** | §1.1–1.2, §5.1 unique key, §8 rejection row; analysis §7 |
| r1 Claude B2 / Codex B4 | No durable version↔job join; missing child undetectable | **Closed** | §5.2.5 atomic set insert, §5.4.3 anti-join over membership, §5.8 readiness |
| r1 Claude B3 / Codex B4 | Forget strands an unrelated version | **Closed** | §5.8 forbids scrubbing by bare canonical entity id; scoped to membership `version_id`/`doc_id` |
| r1 Claude B4 / Codex B5 | Legacy coexistence, version-wide staging clear | **Closed** | §5.3 (no version-wide clear), §5.2.1 + §5.7 two-way mutual exclusion, mixed-image gate |
| r1 Claude B5 / Codex B3 | Stale LLM verdict / TOCTOU apply | **Closed** | §5.6 binds the invariant, rejects unlock-for-LLM-without-revalidate by name |
| r1 Claude B6 / Codex B2 (order) | Ordering key not total | **Closed** | `(asserted_at NULLS LAST, claim_id, statement)` in §1.5, §5.3.2, §5.5, D90 |
| r1 Claude B7 | Supersession/embed topology ambiguous | **Closed** | §1.8, §5.2.4, §5.4.4 — siblings on both empty and non-empty paths |
| r1 Claude B8 | Rule 2 "v1 / phase" framing | **Closed** in D90 scope | No phasing language in the design or D90; D88's own doc still has it (nit N8) |
| r2 Claude R1 | Membership omits `representation_id` / `chunker_version` → supersession no-ops, `RECONCILE` never chains, barrier lock key unstable | **Closed** | §5.1 columns (incl. `extractor_version`), §5.4.1 lock key from `obs_flush_version_state`, §5.4.4 names the reconstruction fields |
| r2 Claude R2 | `document_version` row at fan-out version both required and undefined; four enqueue sites produce it | **Closed** | §1.8/§1.9, §5.1 ("**Never** use a `processing_state` row … at the fan-out component version"), §5.7 call-site rule |
| r2 Codex B1 | `t3,t1,t2` leaves overlapping windows — wrong D43 outcome | **Closed** | §5.5.1 binding post-condition + §8 rejection row + §9 acceptance case |
| r2 Claude N4 (re-fire), N5 (rollback), N6 (index), N7 (staging visibility proof), N9 (cost key) | — | **Open, still nits** | see N4–N7 below |
| r2 Claude N1 / Codex N4 / grok H3 | Exact component-version literal | **Open** | §1.1 still says "with suffix `:entity-fanout-1`" (nit N3) |
| r2 Claude N2 / Codex N1 | Off-by-one section cross-references | **Closed** | §1.5/§1.10 → §5.6/§5.7 now resolve correctly |
| r2 Codex N3 | Pin one empty-state representation | **Closed** | `obs_flush_version_state` is the only permitted marker |
| r2 Claude N10 / Codex N5 | Stale analysis | **Open** | analysis still names `ObservationFlushHandler`, chains embed after supersession, uses the two-key order (nit N9) |
| grok r3 B1 | Follow-up lease identity | **Open, downgraded to nit** | see N1 |
| grok r3 H1–H2 | Session-lock connection ownership; single-flight mechanism | **Open, nits** | see N2, N5 |

## Remaining blockers

**None.**

I considered and rejected two candidates:

- **grok r3 B1 as a blocker.** Its failure mode is real — I confirmed that a
  supersession/embed row carrying `target_id = unit_id` would be invisible to
  readiness (`readiness.py:230-235` filters `target_kind='document_version' AND
  target_id IN :version_ids`, and the per-version lookup at `:159-166` keys on
  `(version_id, stage, component_version)`) and to connector-cycle finalization
  (`lifecycle.py:1032-1040` joins `w.target_id = v.version_id`). But the design
  does not lead an implementer there: §5.3.5 routes completion through
  `complete_entity_obs_flush`, and §5.4.1 defines that function as the analogue
  of `complete_claim_normalize` — which is *ledger-side* and already builds its
  downstream `EnqueueWork` from explicit barrier coordinates with
  `target_kind=DOCUMENT_VERSION, target_id=version_id`
  (`work_ledger.py:405-428`), never by copying a completing row's `target_id`.
  The `target_id=work.target_id` hazard grok cites lives in the *handler* path
  (`e3.py:798-828`), which §5.4 replaces. So: worth one clause (N1), not a
  redesign and not a gap that an implementer following the doc falls into.
- **§5.5.1's "open windows" phrasing.** Read at its narrowest it would permit
  the very overlap it exists to forbid. But the same paragraph names the exact
  forbidden intervals (`[t1,t3)` and `[t2,t3)`), so the binding content is
  present and unambiguous. Nit N2 — but the one I would fix first, because §9's
  acceptance case inherits the same loose noun and a test written against it
  literally would pass while the bug ships.

## Nits

Ordered by what I would fix first. N1–N3 are worth folding into the merge
commit; the rest can ride the implementation PR.

- **N1 — write the follow-up lease triple (grok r3 B1, downgraded).** §5.4.4
  says "preserve today's topology" but never states
  `target_kind = document_version`, `target_id = version_id` (from membership /
  `obs_flush_version_state`). D90 is the first design in which the completing
  row's `target_id` is *not* a version id, so the one thing every prior stage
  could leave implicit is now the one thing worth writing. It also underwrites a
  property §5.4.4 already asserts: "enqueue **once** (idempotent)" is provided
  by the D12 work identity, and that only holds if `target_id` is the
  version — the *last* completing unit varies across replays, so a unit-scoped
  `target_id` would let a re-fire enqueue a second supersession row for the same
  version. Add the triple to §5.2.4 (empty) and §5.4.4 (barrier), plus grok's §9
  case ("supersession + embed rows exist with `target_id = version_id`").
- **N2 — §5.5.1 post-condition should say *validity* windows, not *open*
  windows.** In the bound example, `[t1,t3)` and `[t2,t3)` are both **capped**
  (`valid_until = t3`), so a literal reading of "leave the entity's open windows
  consistent" is satisfied by the defective state. State the post-condition as
  Codex did: for each changing-state chain, slices are non-overlapping and each
  is capped at its immediate successor's `valid_from` — with §9 asserting
  exactly `[t1,t2)`, `[t2,t3)`, `[t3,∞)`. One sentence; it is the difference
  between a test that catches the bug and one that cannot.
- **N3 — the literal component-version string (third time raised).** §1.1 still
  says `OBS_FLUSH_VERSION` "with suffix `:entity-fanout-1`", and the current
  constant is `"e3-obs-flush-2026.08a:claim-fanout-1"` (`e3.py:64`), so "append"
  and "replace" yield different literals. Either works — §5.7's mutual exclusion
  keys on "pre-fanout vs fan-out", not on the string's shape — but write the
  literal (I'd replace: `e3-obs-flush-2026.08a:entity-fanout-1`) in both the
  design and D90 so cutover dispatch has one referent.
- **N4 — make §5.2.2 an idempotent set *extension*, which also gives you the
  re-fire mechanism (repeat of r2 N4).** "Do not re-insert units" is absolute,
  and two things need the softer rule. (a) *Re-fire*: if every unit already
  succeeded, no completion event remains to fire the barrier, so a claim-barrier
  re-fire does nothing — the claim path solved this by evaluating readiness
  inline (`work_ledger.py:770-791`); say the re-fire path evaluates the
  membership anti-join inline and enqueues the siblings when the set is already
  complete. (b) *Late staging*: if ops replays an already-succeeded claim job
  after fan-out and normalize yields an entity not in the pinned set, that
  entity's staging has no unit and never flushes, with no failing job anywhere.
  Both close with the same wording — insert units for any distinct staging
  entity lacking membership (the unique key makes it `ON CONFLICT DO NOTHING`)
  rather than skipping insertion wholesale. Not a blocker: the legacy
  version-serial path loses the same rows the same way, so this is not a D90
  regression — but D90 makes manual recovery harder, because there is no
  operator gesture that creates a missing unit.
- **N5 — pin the single-flight mechanism (grok H2).** §5.5's "at most one
  `running` per `subject_entity_id`" has no bound enforcement point; claim SQL
  filters on deployment/stage/lane only (`work_ledger.py:999-1012`). Correctness
  is carried by the entity lock plus §5.5.1, so this is ops, not truth — but
  name one mechanism (handler defer/requeue is enough). Without it a second unit
  blocks inside `pg_advisory_xact_lock` (`observation_adjudication.py:884`)
  holding a worker slot and a pooled connection for the duration of a hub apply.
- **N6 — session locks need a dedicated connection (grok H1).** §5.6's preferred
  scale path is a session advisory lock across short per-assertion
  transactions. Say it uses a dedicated connection not returned to the pool
  while held, with unlock in `finally` — the precedent already exists as
  `FactCatalog.label_lock` (`fact_catalog.py:174-192`, session-scoped lock on
  `engine.connect()` held across commits). Otherwise pool reuse either leaks the
  lock or unlocks from the wrong session.
- **N7 — readiness derived arm and the legacy-drain artifact.** §5.8 binds the
  membership join but not the key it must emit: readiness matches on
  `(version_id, stage, expected_component_version)` (`readiness.py:159-166`), so
  the derived arm must report the fan-out component version, exactly as
  `_NORMALIZE_CLAIM_STATUS` does for D88. Worth one sentence in §5.7 that
  versions drained under the legacy generation will read `missing` for the obs
  stage once the constant is bumped — the same accepted artifact as the D84 and
  D88 bumps, but a cold reader will otherwise read it as a regression. Also
  unstated: whether forget scrubs `obs_flush_version_state` rows alongside unit
  rows (§5.8 mentions only units).
- **N8 — carry-overs I would not block on.** Rollback direction is still unbound
  (r2 N5: an old image claiming by stage+lane would read `target_id = unit_id`
  as a version id — mirror D88 §5.7's "roll all workers together"). The
  membership-by-version index must be index-served since the anti-join runs once
  per unit completion under the shared barrier lock (r2 N6). The one-sentence
  proof that the fan-out `DISTINCT` cannot miss staging rows — each claim job
  commits staging in its own transaction *before* the ledger completes it
  (`workers/base.py:292-307`), so a barrier requiring all claim rows `succeeded`
  necessarily observes all staging — is still worth stating (r2 N7). §7's cost
  key `observation_flush:{unit_id|entity_id}:{index}` still shifts across
  attempts under per-assertion commits; a `claim_id`/`statement`-derived key
  attributes retries stably (r2 N9). `e3_claim_level_normalize_fanout_design.md`
  still carries "v1" framing in its own §5.6 — a Rule 2 sweep for whenever D88
  is next touched, not this PR's obligation.
- **N9 — refresh the non-binding analysis.** It still names
  `ObservationFlushHandler` (§1.1; the class is `AdjudicateObservationsHandler`,
  `e3.py:715`), still chains embed *after* supersession rather than as a sibling
  (§4), and still states the two-key order `(asserted_at, claim_id)` (§5). It is
  explicitly non-binding, so Rule 2 does not bite the "Chosen v1 / Reject v1"
  cells in §2 — but the analysis is the doc a cold reader reaches first, and it
  currently contradicts the design on three checkable facts.
- **N10 — D90's "Design review" paragraph** records only the first-round dual
  REQUEST_CHANGES. The Consequences paragraph correctly absorbs r2 (membership
  coordinates, `obs_flush_version_state`, reverse-arrival recompute,
  single-flight), so this is bookkeeping: extend the sentence to name the r2 and
  r3 rounds.

## Checklist

Same twelve items as r1/r2, for comparability.

| # | Item | r1 | r2 | r3 | Note |
| --- | --- | --- | --- | --- | --- |
| 1 | Expected entity set pin vs live staging | Concern | Pass | **Pass** | Membership materialized in the claim-barrier TX is the anti-join's set; staging drain cannot erase it. See N4 for late-arriving staging and N8 for the visibility proof. |
| 2 | Empty staging path | Fail | Fail | **Pass** | One marker (`obs_flush_version_state`), both siblings enqueued, no `document_version` row at the fan-out generation, all four shortcut sites routed. |
| 3 | DLQ / missing unit blocks downstream | Concern | Pass | **Pass** | "Membership row without a succeeded processing row at the fan-out version" is the blocking predicate; *missing* is observable. |
| 4 | Within-entity order + undated | Fail | Pass | **Pass** | `(asserted_at NULLS LAST, claim_id, statement)`, matching `_SELECT_OBS_STAGING_ORDERED` (`fact_catalog.py:650-659`). |
| 5 | Cross-entity independence | Pass | Pass | **Pass** | D43 writes and the candidate block are keyed by `subject_entity_id`. |
| 6 | Continuous multi-version ingest + locks | Fail | Pass | **Pass** | Was Codex's standing objection. §5.5.1 makes windows schedule-independent, and the durable supersede edges in `observation_adjudications` make the recompute implementable. N2 for the wording. |
| 7 | Barrier lock ordering vs `complete_claim_normalize` | Concern | Concern | **Pass** | Shared representation-scoped family, key sourced from `obs_flush_version_state` rather than a live representation lookup — the r2 missed-fire race is gone. |
| 8 | Idempotent re-run after partial unit | Concern | Pass | **Pass** | Per-assertion staging delete under the retained unit lock; evidence-PK idempotency; no-op-success arm states its premise. |
| 9 | Legacy version-serial cutover | Fail | Concern | **Pass** | Both mutual-exclusion directions hold now that no `document_version` row can exist at the fan-out generation — the r2 evasion path is closed. N3 (literal), N7 (readiness artifact), N8 (rollback) remain. |
| 10 | LLM / transaction protocol | Fail | Pass | **Pass** | Invariant bound, TOCTOU shape rejected by name, revalidate-or-abort required. N6 for connection ownership. |
| 11 | Readiness / lifecycle / forget | Fail | Concern | **Pass** | All three derive through version-scoped membership including unit DLQ and empty success. N1 (follow-up identity), N7 (derived-arm key, version_state scrub). |
| 12 | Overclaiming vs under-specifying | Concern | Concern | **Pass** | §6, §10 and §11 are honest about what fan-out does *not* fix (the largest hub still bounds the critical path), and the reverse-arrival limitation is no longer described as acceptable — it is bound as a co-requisite. |

## Final recommendation

**Merge the design: yes.**

Merge `08b0d8fd` with N1–N3 folded in as a small doc commit — the follow-up
lease triple in §5.2.4/§5.4.4, "validity windows" in §5.5.1 and §9, and the
literal component-version string in the design and D90. None of the three
changes the decision, and none is worth another review round; a re-read of the
diff is enough.

Two things the implementation PR owes beyond the fan-out itself:

1. **§5.5.1 ships with D90, not after it.** It is a co-requisite, not a
   follow-on — without it, parallel units under continuous multi-version ingest
   produce overlapping validity windows that no barrier catches. The §9
   `t3, t1, t2` case is the acceptance gate.
2. **The cutover is exclusive, in both directions and in both roll directions.**
   Stop-drain-restart or a capability gate before any producer emits unit rows,
   and drain unit rows before rolling back (N8).

The core decision — version-scoped entity units, serial ordered apply under the
entity lock, strict membership anti-join before sibling supersession and
`embed_claim` — has survived three rounds and two reviewers without amendment.
It should be built as written.
