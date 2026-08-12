# Implementation re-review (r4) — D90 entity-grain observation flush fan-out

**Agent:** claude-opus
**Date:** 2026-08-12
**Target:** branch `feat/d90-entity-obs-flush-fanout`, PR #265
**Commit reviewed:** `ceefb459` (review was requested at `1fa5fb8b`; the branch
advanced mid-review — see "Moving target" below)
**Design:** [e3_entity_obs_flush_fanout_design.md](../../plan/designs/e3_entity_obs_flush_fanout_design.md) (D90)
**Prior implementation reviews:**
[claude-opus r1](REVIEW_claude-opus_e3_entity_obs_flush_fanout_impl_2026-08-12.md),
[codex-sol r1](REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_2026-08-12.md),
[codex-sol r2](REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_r2_2026-08-12.md),
[claude-opus r3](REVIEW_claude-opus_e3_entity_obs_flush_fanout_impl_r3_2026-08-12.md),
[codex-sol r3](REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_r3_2026-08-12.md),
[codex-sol r4](REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_r4_2026-08-12.md)

---

## Verdict

**REQUEST_CHANGES.**

All five r3 blockers this HEAD set out to absorb are genuinely closed, and I
confirmed each by **executing** it against real PostgreSQL rather than reading
the diff. Codex r3 B1's tied-timestamp counterexample now produces the exact
total-order result. My r3 B1's withdrawn-testimony repro no longer reproduces.
Codex r3 B2's forged-coordinate barrier is refused. And D90 finally has two
executable acceptance tests, one of which I verified is load-bearing by mutation.
That is real progress and I would have signed it off.

The merge gate is held by **one defect introduced by this round's own lifecycle
fix**: `_SELECT_READY_CYCLES` is no longer an executable query. The generation
pin added at `lifecycle.py:1085` — `LIKE '%:entity-fanout-%'` — sits inside a
SQLAlchemy `text()` construct, where `:entity` is parsed as a **bind parameter**.
The query now demands a parameter named `entity` that no caller supplies, so
**every** call to `cycles_ready_to_finalize()` raises, and D22/D54 connector-cycle
finalization — absence-based closure and the tombstone cascade — cannot run at
all. Three tests in `src/tests/workers/test_lifecycle_reconciliation.py`, a
registered CI integration path, fail at this HEAD. The intended generation pin
also never reaches PostgreSQL. Full detail, executed proof and a verified
one-character fix in B1.

This slipped past two r4 reviewers (codex r4 signed it `APPROVE_WITH_NITS`)
because the only test covering the clause is a `str()` substring assertion —
and `str()` on a `text()` construct **strips the escape**, so that assertion
passes identically whether the query works or not. This is precisely the failure
mode r1–r3 kept naming, arriving in the one clause nobody executed.

Everything else I found is nit-grade, including one D90-introduced data artifact
(N1) that I judge below the merge bar but worth a decision.

### Moving target

The branch advanced from `1fa5fb8b` to `ceefb459` while I was reviewing.
`ceefb459` fixes the `ruff format` failure that codex r4 raised as N1 (I had
independently hit it — it is now **closed**, 367 files formatted) and lands the
codex r4 artifact. It touches **no implementation file**
(`git diff 1fa5fb8b..ceefb459 --stat -- src/rememberstack/` is empty), so every
executed result below applies to both commits. I re-confirmed B1 at `ceefb459`.

---

## What I executed

Real PostgreSQL 16 in a dedicated container at structural head `p9_10_0031`
(`downgrade base` → `upgrade head`), repo fixtures only.

```text
uv run ruff check src/ benchmarks/                All checks passed
uv run ruff format --check src/ benchmarks/       367 already formatted  (fixed by ceefb459)
uv run pyright src/ benchmarks/ --pythonversion 3.13   0 errors, 0 warnings
uv run pytest -q src/tests/workers                123 passed, 90 skipped
python3 .github/ci/check_test_inventory.py        unit=66 integration=53 discovered=119
pytest src/tests/spine/test_observation_adjudication.py
       src/tests/spine/test_pipeline_readiness.py 23 passed        (was 21 at r3; +2 new)
pytest src/tests/workers/test_lifecycle_reconciliation.py
                                                  3 FAILED, 9 passed   ← B1
```

### Prior blockers — each re-run, each closed

| r3 blocker | r4 result | Executed evidence |
| --- | --- | --- |
| **codex B1** — re-split lost the `(claim_id, statement)` tie-break | **Closed** | Codex's own counterexample, fixed UUIDs: `A@t1`, `A@t2` claim `ffff…` collapsed as evidence, then `B@t2` claim `0000…02`. Result exactly `A[t1,t2), B[t2,t2), A[t2,∞)` — the zero-width middle slice and the A-open tail the total order requires. |
| **claude B1** — withdrawn testimony re-opened as a new current fact | **Closed** | My r3 repro re-run: `A{t1,t3}` applied alone, `t3`'s claim flipped to `is_current_testimony=false`, then `B{t2}`. Result `A[t1,t2)`, `B[t2,∞)` — B stays open, no zero-evidence resurrected slice, no cap on live testimony. `_SELECT_EVIDENCE_FOR_OBS` now filters `c.is_current_testimony` (`observation_adjudication.py:1206`). |
| **codex B2** — completion could forge `barrier_complete` | **Closed** | One membership unit + running D90 row, **no** version-state row, completion called with forged representation/chunker/extractor/hash: `obs_flush_version_state` stays empty, zero sibling `document_version` rows, zero follow-ups. The guard at `work_ledger.py:467-478` holds. (Residual: N3.) |
| **codex B4** — missing processing row looked terminal | **Intent landed, query broken** | The `LEFT JOIN` + `(w.status IS NULL OR w.status <> 'succeeded')` shape is right, but the clause it lives in cannot execute — **B1**. |
| **claude B2** — zero executable D90 coverage | **Partially closed** | Two real PostgreSQL tests now exist. I mutation-tested them: neutering `_resplit_later_evidence` **fails** `test_d90_staggered_late_arrival_resplit_shapes`, so it is genuinely load-bearing. See N2 for what is still uncovered. |

---

## Blocker

### B1 — `_SELECT_READY_CYCLES` cannot execute; connector-cycle finalization is dead

`src/rememberstack/spine/lifecycle.py:1085` (introduced by `d858a427`)

```sql
AND w.component_version LIKE '%:entity-fanout-%'
```

This literal is inside `text(...)`. SQLAlchemy scans `text()` bodies for `:name`
bind parameters and finds `:entity`. The consequences are all executed, not
inferred:

```text
sorted(lifecycle._SELECT_READY_CYCLES._bindparams.keys())
  at 0bb37204 (r3):  ['deployment_id']
  at ceefb459 (r4):  ['deployment_id', 'entity']      ← regression

lifecycle.py:543 executes it with {"deployment_id": deployment_id} only:
  sqlalchemy.exc.InvalidRequestError: A value is required for bind parameter 'entity'

rendered SQL reaching PostgreSQL:
  ... LIKE '%%%(entity)s-fanout-%%'      ← the generation pin is gone too
```

**Blast radius.** `cycles_ready_to_finalize()` (`lifecycle.py:528-548`) is called
from `ReconcileWorker.finalize_ready_cycles` (`reconcile.py:312`). That is the
D22/D54 driver for absence-based closure: `_close_lineage_zero_support` for every
lineage of a healthy cycle, plus the deployment-wide tombstone cascade that
follows it. With this raise, **no cycle is ever finalized and no removed-source
fact is ever withdrawn** — the memory keeps serving facts whose source documents
have disappeared. The reconcile stage throws on every pass.

**The branch is red.** `src/tests/workers/test_lifecycle_reconciliation.py` is a
registered CI integration path (`.github/ci/integration-paths.txt:48`) and fails
at this HEAD:

```text
FAILED test_intra_cycle_move_is_a_support_swap_never_a_retract
FAILED test_cycle_finalization_closes_a_genuinely_removed_fact
FAILED test_finalization_never_closes_a_flagged_fact
3 failed, 9 passed
  sqlalchemy.exc.StatementError: (InvalidRequestError)
  A value is required for bind parameter 'entity'
```

**Why three reviews missed it.** The only test covering the new clause is
`test_cycle_wait_blocks_on_missing_entity_obs_flush_units`
(`test_e3_claim_normalize_fanout.py:245-252`), which asserts substrings of
`str(lifecycle._SELECT_READY_CYCLES)`. `str()` on a `text()` construct renders
the *unescaped* body, so `assert "entity-fanout" in sql` passes for the broken
query **and** for the fixed one — I verified both. A source-inspection test
cannot see this class of defect; that is the whole argument r1–r3 have been
making about D90's coverage, now with a concrete casualty.

**Verified fix.** Escape the colon so SQLAlchemy leaves it alone:

```sql
AND w.component_version LIKE '%\:entity-fanout-%'
```

Executed after applying it:

```text
binds: ['deployment_id']                                    ← restored
pytest src/tests/workers/test_lifecycle_reconciliation.py   12 passed
PostgreSQL LIKE semantics (en_US.utf8):
  'e3-obs-flush-2026.08a:claim-fanout-1:entity-fanout-1' LIKE '%\:entity-fanout-%'  → t
  'e3-obs-flush-2026.08a:claim-fanout-1'                 LIKE '%\:entity-fanout-%'  → f
pytest src/tests/workers/test_e3_claim_normalize_fanout.py  15 passed  (substring test unaffected)
```

I reverted the patch; the worktree is clean at `ceefb459`.

Two things must land with it. First, an **executable** cycle-finalization proof
covering the D90 clause — the existing substring test demonstrably cannot fail
on this. Second, prefer the exact declared generation over `LIKE` (codex r4 N3
asks the same): `_COUNT_OBS_FLUSH_UNITS_SUCCEEDED` already pins
`p.component_version = :obs_flush_version`, and a bound parameter cannot be
mis-parsed the way an inline literal just was.

---

## Nits

New at this HEAD unless marked carried.

### N1 — The re-split leaves a zero-evidence orphan observation for undated pairs

`observation_adjudication.py:887-959`

Two **undated** assertions for one entity, split across units, now leave three
observation rows where two are warranted, one of them with `evidence_count = 0`.
Executed (`A` = "hc 500", claim `ffffffff-…`, sorts last; `B` = "hc 600", claim
`00000001-…`, sorts first; both `asserted_at IS NULL`):

```text
at ceefb459                                        pre-D90 baseline (re-split neutered)
("hc 500", NULL, OPEN,   evidence 1)               ("hc 500", NULL, now(), evidence 1)
("hc 500", NULL, now(),  evidence 0)   ← orphan    ("hc 600", NULL, OPEN,  evidence 1)
("hc 600", NULL, now(),  evidence 1)
```

I proved it is D90-introduced by mutation: neutering `_resplit_later_evidence`
produces the two-row shape. The mechanism is that `B` caps `A`, then the
re-split correctly judges `A`'s founding claim later in the total order and
detaches it — but `A` had only that one evidence row, so the capped row is left
as an empty husk while the re-applied claim opens a fresh slice.

The **ordering outcome is an improvement**: `A` ends open, which is what
`(asserted_at NULLS LAST, claim_id, statement)` demands and what the pre-D90
shape got wrong. The residue is the new part. Its cost is real but bounded: the
husk is capped, so current-fact reads are unaffected; but it duplicates "hc 500"
across the undated window for as-of reads, and reconcile's
`_SELECT_ZERO_OBSERVATIONS` (`lifecycle.py:906-920`) will route it to a spurious
`support_withdrawn` review. The dated equivalent is clean — I checked unit
`A{t3}` alone then `B{t2}` and got exactly `B[t2,t3), A[t3,∞)` with zero
husks — so this is specific to the undated/tied tail.

Either delete a capped observation whose evidence set is fully drained by the
re-split, or state in the function docstring that the husk is intended and let
reconcile's zero-evidence sweep retire it. Under CLAUDE.md Rule 1 the choice
belongs in the doc either way, since a future reader will otherwise read the
empty row as corruption. Note §5.5 row 4 defers undated *boundary* semantics to
existing D43 rules, which is why I do not treat this as a blocker — but §5.5.1
still requires the acceptance test (N2).

### N2 — The total-order tie-break — this round's headline fix — has zero tests

Mutation-proven: I replaced the entire `(claim_id, statement)` tail of
`_is_later_in_total_order` (`observation_adjudication.py:1061-1089`) with
`return False`, reverting it to r3 behaviour, and **every test still passed** —
135 across `src/tests/workers` and `src/tests/spine/test_observation_adjudication.py`.
The defect codex r3 B1 raised, that `d858a427` exists to fix, is entirely
unguarded against reintroduction.

Design §5.5.1 closes with a binding instruction: *"Acceptance tests must include
this case **and undated/tied assertions split across units**."* Both halves of
that second clause are missing. The fixed-UUID tied case I ran (and that codex
r4 ran) is a two-line addition to `test_observation_adjudication.py` on top of
the helpers already there — it, plus an undated pair, would pin both the fix and
N1's chosen behaviour.

The §9 scoreboard is otherwise still mostly open; codex r4 N4 lists the same
gaps and I agree with that list. The two committed tests both drive
`ObservationAdjudicator` directly — neither materializes membership units nor
calls `flush_entity_global_staging`, so the fan-out, barrier, readiness, DLQ and
forget paths remain covered only by source inspection. Given B1, that is now a
demonstrated risk rather than a stylistic complaint.

The three weakened existing assertions from r3 nit B2 are also unchanged
(`test_e3_claim_normalize_fanout.py:199-205` satisfied purely by the legacy
branch; `test_enqueue_entity_fanout_source_pins_membership:28` unconditionally
true; the negative source assertions in
`test_entity_handler_applies_global_stream_and_row_clear`).

### N3 — Completion validates the state row's *status* but still writes forged coordinates

`work_ledger.py:467-491` (carried, codex r3 B2 / codex r4 N2 — narrowed, not closed)

The new guard reads only `fanout_status` and then upserts every coordinate from
the handler object. `_UPSERT_OBS_FLUSH_VERSION_STATE` (`work_ledger.py:1753-1774`)
does `ON CONFLICT DO UPDATE SET … representation_id = EXCLUDED.representation_id,
chunker_version = …, extractor_version = …, content_hash = …`, so a caller that
clears the barrier check can still overwrite the authoritative durable
coordinates. It also still locks the *caller's* `representation_id`, so a wrong
value does not serialize against claim fan-out. Select the stored row and derive
the lock, the upsert and both follow-up payloads from it.

Related, same function: completion is still not bound to the claimed unit. I
completed version V1's processing row while passing V2's barrier coordinates; it
succeeded V1's row without complaint. No false barrier resulted — the
`_COUNT_OBS_FLUSH_UNITS_SUCCEEDED` anti-join correctly refused, since V2's own
unit was still `running` — so the practical exploit is blocked, but by the
counting barrier rather than by binding. Resolving `processing_id → D90 row →
unit → materialized state` inside the transaction would make the durable
expected set the authority §5.2 says it is.

### N4 — The new guard fails closed **silently**, stalling the version

`work_ledger.py:475-478`

Both refusal paths `return tuple(outcomes)` with no log, no raise, and no durable
marker — while the unit's processing row has *already* been marked `succeeded` by
`_COMPLETE` at line 447. In my B2 probe the unit ended `succeeded` with no state
row, no siblings and no error: the version is now permanently un-completable with
nothing anywhere recording why. Refusing the forge is right; doing it silently
after committing the success is not. Detect the missing/incompatible state
*before* `_COMPLETE` so the unit stays retryable, or at minimum `log()` it.
(This is the same shape as carried r3 nit 5, the two silent `return []` early
exits in `_enqueue_entity_obs_flush_fanout` at `work_ledger.py:1013-1014`,
`:1024-1025` — both still present.)

### N5 — The statement tie-break compares the wrong operand

`observation_adjudication.py:928`

`_is_later_in_total_order` is called with `left_statement=capped_statement` — the
*observation's* text, not the evidence claim's staged statement. The staging
order key is `s.statement`, the claim's own. The two coincide for an exact-match
collapse and diverge when the collapse was a semantic `evidence` verdict
("staff count: 100" onto "headcount is 100"). It only decides anything at the
third tie-break level, i.e. same `asserted_at` **and** same `claim_id` — one
claim asserting two statements — which is exactly §9's "within-entity
multi-statement same claim, `statement` tie-break" row, still untested.

It is at least self-consistent: the re-split re-materializes with
`capped_statement` (carried r3 nit 13, still undocumented), so the comparison
matches what will actually be written. Worth one line in the docstring saying so.

Separately, this comparison is Python code-point ordering against PostgreSQL's
`ORDER BY s.statement` under the database collation. On this deployment
(`en_US.utf8`) I could not construct a divergent pair — the two agreed on every
case I tried — so this is a portability caveat to note, not a demonstrated bug.

### N6 — The lifecycle clause is not pinned to the normalizer generation

`lifecycle.py:1076-1087`

The membership join pins `deployment_id` and `version_id` but not
`u.normalizer_version`. Units from a superseded normalizer generation therefore
block cycle finalization on equal terms with current ones — and because a
dead-lettered or purged unit is non-terminal under `w.status <> 'succeeded'`, a
stale generation whose processing rows were cleaned up stalls the cycle with no
operator escape. Pin the generation the way `_COUNT_OBS_FLUSH_UNITS` does.

### Carried from r3, all still open

Re-confirmed by inspection at `ceefb459`; none were touched this round.

| r3 nit | Status |
| --- | --- |
| 1 — `obs_flush_component_version=OBS_FLUSH_VERSION` module constant, not `work.component_version` (`e3.py:795`) | open |
| 2 — `_SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY` `LEFT JOIN` does not pin `p.component_version` (`observation_adjudication.py:1180-1194`) | open |
| 4 — both staging queries inner-`JOIN claims`; a staging row with no claim is silently never applied and never deleted | open |
| 5 — two silent `return []` early exits in `_enqueue_entity_obs_flush_fanout` | open (see N4) |
| 6 — `ix_obs_flush_units_version` / `ix_obs_flush_units_entity` / `ix_procstate_entity_obs_flush` absent from `EXPECTED_INDEXES` | open (grep confirms zero hits) |
| 7 — neither new table carries `REFERENCES deployments(deployment_id)` | open |
| 8 — `_COUNT_LEGACY_OBS_FLUSH` detects legacy via `NOT LIKE '%entity-fanout%'` rather than `OBS_FLUSH_LEGACY_VERSION` (`work_ledger.py:1793`) | open |
| 9 — dead `FactCatalog.load_unapplied_obs_staging_for_entity` + duplicated SQL (`fact_catalog.py:514`) | open, still zero callers |
| 10 — `OBS_FLUSH_LEGACY_VERSION` unreachable, docstring does not say so | open |
| 11 — re-split cost key `"d90_late_arrival_resplit"` unqualified by entity/unit (`observation_adjudication.py:956`) | open |
| 12 — whole entity-global drain is one transaction with LLM calls inside | open (§5.6 permits it; note idle-in-transaction limits in the PR) |
| 13 — re-materialized slices carry the capped statement, undocumented | open (see N5) |
| 14 — hard-forget residue verifier does not cover the two new tables | open |

Codex r3 B3 (D56 sibling `doc_id`/`content_hash` taken from the origin claim and
the primary completion rather than the target version) and B5 (mixed-image
rollout: `_CLAIM_SELECT` is capability-blind) are also unchanged. I agree with
codex r4 that both are nits rather than blockers on the accepted path — the
ordinary worker builds the barrier from membership — and note B3 will matter more
once N3 makes the stored row authoritative.

---

## What I checked and found correct

Recording these so r5 does not re-derive them.

- **`ruff format` is clean at `ceefb459`** — codex r4 N1 is closed.
  `ruff check`, `pyright` (0/0/0) and the CI test inventory all pass.
- The tied-timestamp and withdrawn-testimony fixes are correct **as executed**,
  not merely as written; both r3 repros are dead.
- `_is_later_in_total_order`'s UUID comparison is sound: Python compares
  `UUID.int`, i.e. big-endian byte order, which is what PostgreSQL's `uuid_cmp`
  memcmp produces. NULLS-LAST handling (undated later than dated, two undated
  falling through to `claim_id`) matches the staging `ORDER BY`.
- `test_d90_staggered_late_arrival_resplit_shapes` is load-bearing — it fails
  when `_resplit_later_evidence` is neutered. (Its co-present sibling is not: it
  passes with the re-split disabled, which is expected, since an already-ordered
  batch never needs the repair.)
- The forged-coordinate barrier is genuinely refused: no state row, no siblings,
  no follow-ups.
- The r3-verified items I did not re-run and have no reason to doubt: entity
  lock before the staging snapshot, staging retirement by true PK, DLQ exclusion
  from the drain, no version-wide staging clear on the entity path, the derived
  readiness aggregate (`test_pipeline_readiness` green), E1/E2 `EmptyObsFlushComplete`
  routing, `target_id = unit_id`, the shared `_ADVISORY_LOCK_NORMALIZE_BARRIER`
  key family, and migration `p9_10_0031` at 69 tables.
- **Docs obligation (CLAUDE.md / D66): not triggered.** D90 changes no CLI, API,
  MCP, config, mount or connector surface; the stage name is unchanged and
  `remember ops replay` stays generation-agnostic. No `website/src/app/docs/**`
  page describes work grains or `target_kind`, and `/docs/project-status` makes
  no claim this branch falsifies. D90 is recorded at `decisions.md:3576`.

---

## Summary

One line of SQL stands between this branch and a merge. The D90 apply engine is
in good shape — the history-loss family that dominated r1–r3 is closed under
execution, from four different angles, and the branch finally has a test that
would notice if the staggered case regressed. What it does not yet have is a test
that would notice the clause it just broke, which is why B1 is a blocker rather
than a nit: the fix is trivial, the coverage that should have caught it is the
part that keeps being deferred.

Fix `lifecycle.py:1085`, add an executable cycle-finalization proof and the
tied/undated acceptance cases §5.5.1 already requires, and this is an approve.
