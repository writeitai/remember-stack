# Implementation review — D90 entity-grain observation flush fan-out

**Agent:** claude-opus
**Date:** 2026-08-12
**Target:** branch `feat/d90-entity-obs-flush-fanout`, PR #265, commit `ab16d241`
**Design:** [e3_entity_obs_flush_fanout_design.md](../../plan/designs/e3_entity_obs_flush_fanout_design.md) (D90)
**Scope reviewed:** migration `p9_10_0031`, `work_ledger` fan-out +
`complete_entity_obs_flush`, `AdjudicateObservationsHandler` entity path,
`fact_catalog` load helpers, `EntityObsFlushBarrier`, tests

---

## Verdict

**REQUEST_CHANGES.**

The ledger-side skeleton is close to the design: `unit_id` really is the
`target_id` (D12 collision avoided), the barrier reuses the exact
`d88-normalize-barrier` advisory-lock family, membership and version state are
durable, and the empty path goes through `obs_flush_version_state` rather than a
`document_version` processing row. Those are the parts the dual design review
fought hardest over, and they landed.

But the apply semantics — the thing D90 exists to get right — were traded away
for an implementation convenience, and two read surfaces that the design names as
binding (§5.8 readiness, §5.8 lifecycle) were not touched at all. As it stands
this branch loses observations across versions, reports every document as
not-ready forever, finalizes connector cycles before flush completes, and is red
on `make lint` and on a pre-existing test.

---

## Summary

The change reads as two efforts of very different maturity.

The **spine** work is careful. `_enqueue_entity_obs_flush_fanout` materializes
membership + version state + one processing row per staging entity inside the
claim-barrier transaction; `complete_entity_obs_flush` takes the representation
barrier lock, marks the unit succeeded, and only then evaluates an anti-join over
`obs_flush_entity_units`, so a missing or non-succeeded unit blocks. The legacy
exclusion check runs before any insert. `EntityObsFlushBarrier` carries exactly
the coordinates §5.4 asks for. I could not construct a false-barrier-open against
`_entity_obs_flush_barrier_ready` — it counts expected from membership and
matched from `processing_state` at the fan-out component version with
`status='succeeded'`, both inside the same locked transaction as `_COMPLETE`.

The **handler** work is not. `_handle_entity_unit` does the expensive, correct
thing — it loads entity-global unapplied staging in the §5.5 total order — and
then throws the result away, filtering back down to this unit's own
`(version_id, normalizer_version)` slice and applying only that. The code comment
at `e3.py:779-786` says why: `clear_staging` is version-scoped, so applying rows
the unit does not own would leave them staged. That is a real problem with a
known fix (retire each staging row by its own PK, which §5.6 explicitly
anticipates: "staging PK already includes `statement`"), and instead the binding
rule was inverted. §8 lists "per-unit apply" as **Rejected**; this is that.
§5.5.3's late-arrival re-split, the sequential twin of the same bug, is absent
entirely.

Beyond apply order, the change moves `adjudicate_observations` off
`target_kind=document_version` without following that move into the three places
that read version-level work: readiness, connector-cycle finalization, and hard
forget. §5.8 specifies all three. None were updated, and the resulting breakage
is not subtle — readiness is unconditionally false for every document on this
branch.

Test coverage is the weakest part. Five new tests, all `inspect.getsource`
substring assertions, several using `or` across alternatives so they pass on a
partial implementation. Nothing executes the fan-out, the handler, or the
barrier. The two acceptance cases the design calls out by name (§5.5.1
co-present, §5.5.3 staggered) are exactly the two the implementation gets wrong,
and neither is tested. One existing test was weakened rather than extended.

---

## Blockers

### B1 — Per-unit apply, not entity-global merge-apply (silent cross-version loss)

`src/rememberstack/workers/e3.py:765-806`

`load_unapplied_obs_staging_for_entity` returns the entity-global unapplied set
in `(asserted_at NULLS LAST, claim_id, statement)` order — correct. Lines 787-796
then rebuild `unit_assertions` filtered to `row["version_id"] == version_id and
row["normalizer_version"] == normalizer_version`, and line 801 passes only that
slice to `add_observations`. Rows belonging to other versions' units are read,
sorted, and discarded.

This is the alternative §8 lists as **Rejected (Codex r3 evidence-collapse
case)**, and it fails §11's "No silent cross-version observation loss (dual-review
B1 closed)".

Failure scenario — the §5.5.1 example, verbatim:

- Entity `E`. V1 unit `A` staging: `t1: role=A`, `t3: role=A`. V2 unit `B`
  staging: `t2: role=B`. `t1 < t2 < t3`. Both units `pending`.
- Worker claims `A`. Global load returns `[t1:A, t2:B, t3:A]`. Filter keeps
  `[t1:A, t3:A]`. Apply opens `A` at `t1`; `t3` is the same statement so it
  collapses as evidence on the open `A` row.
- Worker claims `B`. Filter keeps `[t2:B]`. Apply supersedes open `A` at `t2`.
- Final: `A[t1,t2), B[t2,∞)`. Required: `A[t1,t2), B[t2,t3), A[t3,∞)`.

The `A`-at-`t3` observation is gone, with no error, no DLQ, and both units
`succeeded`. Window re-cap cannot recreate it after the evidence collapse (§5.5.1).

Note this fires on *any* multi-version co-presence for a shared entity, which is
the normal steady state under continuous ingestion (§4 row 2) — not an edge case.

**Direction:** apply all of `staged_rows` in the returned order, and retire each
staging row individually in the same durable write as its apply, keyed by its own
`(deployment_id, version_id, normalizer_version, subject_entity_id, claim_id,
statement)` PK. `add_observations` currently accepts a single version-scoped
`clear_staging` dict; it needs a per-assertion retire instead. Grouping the
global set by version and calling `clear_staging` once per group is **not**
equivalent — it fixes retirement but not interleaving, and interleaving is the
whole point.

### B2 — §5.5.3 late-arrival re-split is not implemented

No re-split logic exists anywhere in
`src/rememberstack/spine/observation_adjudication.py` or the D90 handler path.
§5.3 step 4 makes it a required step of the entity handler; §5.5.3 marks it
**binding**; §10 carves it out of "out of scope" specifically to keep it in.

Failure scenario — the §5.5.3 staggered case:

- Unit `A` `{t1:A, t3:A}` is the only unit; it applies fully and succeeds. Open
  `A` row carries `t3` as evidence.
- Unit `B` `{t2:B}` materializes later (new version, same entity — §4 row 2).
- Apply caps open `A` at `t2`. The `t3` evidence claim on the now-capped row is
  never re-materialized as a subsequent open slice.
- Final: `A[t1,t2), B[t2,∞)`. Required: `A[t1,t2), B[t2,t3), A[t3,∞)`.

Fixing B1 does not fix this: entity-global merge only sees *unapplied* staging,
and `A`'s rows are already applied and deleted. §5.5.3 requires that when an apply
caps observation `O` at boundary `T`, evidence claims already attached to `O` with
`asserted_at > T` are re-materialized as open slices after the cap.

### B3 — Readiness reports `adjudicate_observations` as `missing` for every version, permanently

`src/rememberstack/spine/readiness.py:228-237`, `src/rememberstack/profiles/selfhost.py:899`

`_VERSION_WORK` selects `WHERE target_kind = 'document_version'`.
`_expected_components()` maps `ADJUDICATE_OBSERVATIONS → OBS_FLUSH_VERSION`,
which is now `:entity-fanout-1`. §5.7 forbids ever writing a `document_version`
processing row at that component version. So
`by_key.get((version_id, ADJUDICATE_OBSERVATIONS, OBS_FLUSH_VERSION))` is always
`None`, `status` is always `"missing"`, `VersionPipelineReadiness.ready` is always
`False`, and `PipelineReadinessReport.ready` is always `False` — for every
document, forever, on this branch.

D84 and D88 each hit this same wall and each added a derived-status block
(`_EXTRACT_CHUNK_STATUS`, `_NORMALIZE_CLAIM_STATUS`) that replaces the
version-level lookup. D90 needs the equivalent join over
`obs_flush_entity_units` → `processing_state` by `unit_id`, plus
`obs_flush_version_state.fanout_status IN ('empty_complete','barrier_complete')`
as the empty/complete signal. §5.8 row 1 specifies exactly this. It is absent.

The zero-chunk path is a second, independent route to the same false-not-ready:
`e1.py:631` and `e2.py:1067` now enqueue at `OBS_FLUSH_LEGACY_VERSION`, which
also never matches the expected `OBS_FLUSH_VERSION` key.

### B4 — Connector-cycle finalization no longer waits for observation flush

`src/rememberstack/spine/lifecycle.py:1024` (`_SELECT_READY_CYCLES`)

The query has three `NOT EXISTS` guards: version-level `document_version` work,
D84 chunk-grain extract, D88 claim-grain normalize. Observation flush used to be
covered by the first guard, because it was a `document_version` row. It no longer
is, and no D90 guard was added.

Failure scenario: a sync cycle's documents finish normalize; obs flush units are
`pending` (or `dead_letter`). `_SELECT_READY_CYCLES` finds no blocking row and
the cycle finalizes, running reconcile and the K-plane cascade against a graph
whose observations have not been written. §5.8 row 2 requires waiting on unit rows
for the version, DLQ included. The D88 block immediately above is the template.

### B5 — Branch is red: failing test plus lint and format failures

Test — `src/tests/workers/test_chunk_level_extract.py:148`:

```
assert job.component_version == OBS_FLUSH_VERSION
E  AssertionError: assert 'e3-obs-flush-2026.08a:claim-fanout-1'
                       == 'e3-obs-flush-2026.08a:claim-fanout-1:entity-fanout-1'
```

`_extract_follow_up` now enqueues `OBS_FLUSH_LEGACY_VERSION`; the test still
asserts `OBS_FLUSH_VERSION`. It was not updated. (Run:
`1 failed, 21 passed`.) Note that deciding *how* to update it is not mechanical —
see B3, where the same mismatch breaks readiness for real.

Lint — `ruff check src/` fails `I001` at `src/rememberstack/workers/e1.py:631`,
and `ruff format --check src/` reports the same file would be reformatted. The
line is 90 chars:

```python
from rememberstack.workers.e3 import OBS_FLUSH_LEGACY_VERSION as OBS_FLUSH_VERSION
```

`make check` stops at `lint`.

### B6 — Hard forget does not scrub the two new tables

`src/rememberstack/spine/forget.py`

The forget statement list deletes from 30+ tables including
`normalize_observation_staging` (line 1251) and nulls `processing_state` payloads
(line ~1442). Neither `obs_flush_entity_units` nor `obs_flush_version_state`
appears. Both survive hard forget carrying `doc_id`, `version_id`, `content_hash`,
and `subject_entity_id` for the forgotten document. The residue verification
`UNION ALL` chain (line ~1550+) also omits both tables, so the existing
hard-forget residue test cannot see the gap.

§5.8 row 3 is explicit: "Scrub **units and processing rows for forgotten
versions** via membership `version_id` / `doc_id`" — and equally explicit about
the trap to avoid: do *not* kill units merely because `target_id` matches a
canonical entity id in a forgotten document's entity set. Scope the scrub by
`version_id`/`doc_id` only.

Secondary effect: forget deletes the staging rows but leaves the units and their
`pending` processing rows. With B4 fixed, that version's cycle would then never
finalize; today the barrier for that version simply never opens.

Given CLAUDE.md Rule 3 ("deletion ... always fully here"), an incomplete hard
forget is a blocker rather than a follow-up.

---

## Nits

1. **`obs_flush_component_version` is hardcoded, not read from the claimed row.**
   `e3.py:825` passes the module constant `OBS_FLUSH_VERSION`. The barrier then
   counts succeeded rows at that literal. `work.component_version` is the
   authoritative generation for the row actually claimed; use it. Today they
   agree, so this is latent — but it means an ops replay at any other generation
   silently counts zero and stalls the version.

2. **`content_hash=work.content_hash` bypasses membership.** `e3.py:823`.
   §5.2 is explicit that the handler and barrier "**must** load coordinates from
   membership / `obs_flush_version_state` by `unit_id` / version". `unit`
   already carries `content_hash` and it is loaded (`fact_catalog.py:713`) and
   then ignored. Same value today; drift-prone by construction.

3. **`doc_id` silently dropped on type mismatch.** `e3.py:826`:
   `doc_id=doc_id if isinstance(doc_id, UUID) else None`. If the driver ever
   returns `str`, `doc_id` vanishes from the supersession payload with no error.
   Prefer `UUID(str(doc_id)) if doc_id is not None else None`.

4. **Both staging queries inner-JOIN `claims`.**
   `work_ledger.py:_SELECT_STAGING_ENTITIES_FOR_FANOUT` and
   `fact_catalog.py:_SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY`. A staging row
   whose claim is absent is dropped from *both* membership and the apply set: it
   is never applied, never deleted, and never blocks the barrier — silent loss
   with a `succeeded` version. `LEFT JOIN` (NULL `asserted_at` already sorts
   last under `NULLS LAST`) or raise non-retryable; do not drop.

5. **`_SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY` LEFT JOIN omits
   `component_version`.** `fact_catalog.py:728-735` matches any
   `processing_state` row for the unit at `stage='adjudicate_observations'`. If a
   unit ever carries rows at two generations, the join multiplies and each
   staging row is applied twice. Pin `p.component_version`.

6. **Insert-then-reselect per unit.** `work_ledger.py:989` +
   `work_ledger.py:1004`: `INSERT ... ON CONFLICT DO NOTHING` followed by a
   separate `SELECT unit_id`. At BEAM's ~2k staging entities (§2) that is ~4k
   statements inside the claim-barrier transaction while holding the
   `d88-normalize-barrier` advisory lock — the one lock that serializes barrier
   fires for the representation. Use `ON CONFLICT ... DO UPDATE SET
   subject_entity_id = EXCLUDED.subject_entity_id RETURNING unit_id`, or a single
   set-based `INSERT ... SELECT ... RETURNING`. `uuid4()` per row could equally be
   `gen_random_uuid()` server-side.

7. **`ix_procstate_entity_obs_flush` does not serve the barrier query.**
   Migration line 93 indexes `(deployment_id, stage, target_kind,
   component_version, status)`, but `_COUNT_OBS_FLUSH_UNITS_SUCCEEDED` joins on
   `p.target_id = u.unit_id`, which the existing `UNIQUE (deployment_id,
   target_kind, target_id, stage, component_version)`
   (`p0_02_0002_infrastructure_registries.py:94`) already covers. The new index is
   useful for the §7 queue-depth scan — say so in the migration comment, or drop
   it rather than leave it reading as barrier support.

8. **No `REFERENCES deployments(deployment_id)` on either new table.**
   `EXPECTED_CONSTRAINT_COUNTS` keeps `f` at 128, confirming zero FKs were added.
   `processing_state` (`... deployment_id uuid NOT NULL REFERENCES deployments`)
   and the p9_06 registry tables both carry the tenancy FK. Two new
   tenant-scoped tables without one is a convention break.

9. **New indexes not registered in `EXPECTED_INDEXES`.**
   `catalog_contract.py:172`. The head check filters by `indexname = ANY(:names)`,
   so unregistered indexes pass silently — which is precisely why the omission
   matters: the contract exists to pin head shape.
   `ix_obs_flush_units_version`, `ix_obs_flush_units_entity`, and
   `ix_procstate_entity_obs_flush` belong in the tuple. (The table additions and
   constraint-count bump `{c: 53→54, p: 67→69, u: 34→35}` are correct — 2 PKs,
   1 UNIQUE, 1 CHECK.)

10. **Legacy detection by substring.** `_COUNT_LEGACY_OBS_FLUSH` uses
    `component_version NOT LIKE '%entity-fanout%'`. §5.7's generation boundary
    deserves an explicit comparison against `OBS_FLUSH_VERSION` (or a declared
    legacy set), not string sniffing that a future `:entity-fanout-2` or an
    unrelated version string could confuse.

11. **Two silent `return []` branches.** `work_ledger.py:891` (legacy
    non-terminal) and `work_ledger.py:902` (already materialized) both return
    empty with no log and no durable marker. §5.2.2 says the already-materialized
    case must still "ensure barrier evaluation can still fire". More concretely:
    if fan-out is skipped because a legacy row was non-terminal, and ops later
    dead-letters and removes that legacy row, nothing re-triggers fan-out and the
    version stalls with no signal anywhere. At minimum log it.

12. **Legacy path lost its residual-staging safety net, and the comment
    explaining why is wrong.** `e3.py:875-876` removes
    `clear_staged_observations` from the *legacy* handler with the comment "would
    wipe peer entity progress under mixed cutover". The clear is scoped to
    `(deployment_id, version_id, normalizer_version)`, and §5.7 forbids legacy and
    fan-out from both running for the same version — so by construction there are
    no peer entity units for that version to wipe. §5.7's constraint ("Entity-path
    **must not** invoke version-wide staging clear") binds the entity path only.
    Either restore it on the legacy path or state the real reason.

13. **Cost key does not match §7.** `e3.py:803` uses
    `observation_flush:{version_id}:{entity_id}`; §7 specifies
    `observation_flush:{unit_id|entity_id}:{index}`. Ops cannot resolve a cost row
    back to a unit, which is the stated purpose.

14. **Design deviation recorded as a code comment.** `e3.py:779-786` is an
    eight-line comment that describes the entity-global apply, then explains why
    the code does not do it. Per CLAUDE.md Rules 1-2, a deviation from a binding
    design belongs in the design doc or `decisions.md`, not buried in a handler.
    If B1 is fixed as suggested the comment goes away entirely.

---

## Test gaps

The new file `src/tests/workers/test_e3_entity_obs_flush_fanout.py` contains five
tests, all `inspect.getsource` substring assertions. None constructs a unit, runs
the handler, or exercises the barrier. None would fail if the fan-out SQL were
wrong, if the barrier opened on 2/3 units, or if apply order were reversed —
which is to say none of them test D90.

Several are weaker than they look. `test_enqueue_entity_fanout_source_pins_membership:28`
asserts `"entity-fanout" in source or "obs_flush_component_version" in source`;
the second disjunct is the name of a function parameter, so the assertion is
unconditionally true. Line 22 and lines 25-27 use the same `or` pattern.

One existing test was **weakened rather than extended** —
`test_e3_claim_normalize_fanout.py:196`:

```python
- assert "clear_staging=" in handler_source
+ assert "clear_staging=" in entity_source or "clear_staging=" in legacy_source
```

That `or` now passes if the entity path drops staging retirement entirely, which
is the exact regression the test was written to catch. It should assert both.

### Missing, against §9's own table

| §9 case | Status |
| --- | --- |
| Claim barrier, 3 staging entities → 3 units + 3 processing rows | missing |
| Two versions, same subject entity → 2 distinct `unit_id`s | **missing** — this is dual-review B1, the entire reason `target_id` is `unit_id` rather than `subject_entity_id`, and nothing verifies the `ON CONFLICT` does not collapse them |
| V2 after V1 succeeded for same entity → V2 gets its own unit + apply | missing |
| Same entity, two pending units → single apply stream, global order | missing |
| Unit A `{t1:A,t3:A}` + unit B `{t2:B}` co-present → `A[t1,t2), B[t2,t3), A[t3,∞)` | **missing — would fail today (B1)** |
| Unit A completes alone, then B `{t2:B}` → same slices via §5.5.3 | **missing — would fail today (B2)** |
| Supersession payload reconstruction fields present from membership/state | missing |
| Zero-chunk empty path → no `document_version` row at fan-out version | missing |
| Empty staging → `empty_complete` + supersession + `embed_claim` | missing |
| 2/3 units succeeded → no supersession | **missing** — `_entity_obs_flush_barrier_ready` has no test at all, so the anti-join has never been executed |
| 3/3 succeeded → supersession + `embed_claim` exactly once | missing (including idempotence under concurrent last-unit completion) |
| Unit DLQ → no supersession | missing |
| Within-entity multi-statement same claim → `statement` tie-break stable | missing |
| Forget doc A entities → version B's units for shared entity still runnable | missing — and this is the §5.8 trap the design warns about by name |
| No version-wide staging clear on entity path → peer staging intact | missing (source-substring only) |
| Legacy non-terminal blocks fan-out → no unit insert | missing |
| Partial unit retry → no double evidence corruption | missing |

Zero of seventeen have executable coverage.

### Additional gaps beyond §9

- **Readiness** (B3): no test asserts the obs stage reports succeeded via
  units / `obs_flush_version_state`. A single test over `PipelineReadinessCatalog`
  with a fully-flushed version would have caught B3 before review.
- **Lifecycle** (B4): no test asserts a cycle stays unfinalized while units are
  `pending` / `dead_letter`. The D88 equivalent presumably exists — mirror it.
- **Hard forget** (B6): the residue assertion chain does not cover
  `obs_flush_entity_units` or `obs_flush_version_state`, so the tables' absence
  from the delete list is invisible.
- **Migration**: `test_migrations.py` adds only the revision id and the head
  bump. Nothing asserts the
  `(deployment_id, version_id, normalizer_version, subject_entity_id)` unique key
  or the `fanout_status` CHECK — both are load-bearing (the unique key is what
  makes fan-out idempotent).
- **Ordering under `NULLS LAST`**: §5.5 row 4 specifies undated assertions sort
  last; no test covers undated/tied assertions split across units.

---

## What I checked and found correct

Recording these so a re-review does not re-derive them:

- `target_id = unit_id` (not `subject_entity_id`) — D12 identity collision from
  design §1.2 / dual-review B1 is genuinely avoided. `unit_id` is a fresh `uuid4`
  per `(deployment, version, normalizer, entity)`.
- `complete_entity_obs_flush` acquires `_ADVISORY_LOCK_NORMALIZE_BARRIER`
  (`work_ledger.py:444`) — the same key family as `complete_claim_normalize`
  (`work_ledger.py:364`), keyed on `representation_id`. §5.4.1 satisfied with a
  shared namespace, so the "two keys, fixed global order" caveat does not apply.
- `_entity_obs_flush_barrier_ready` — expected from membership, matched from
  `processing_state` at the fan-out component version with `status='succeeded'`,
  both inside the locked transaction that also runs `_COMPLETE`. Missing rows and
  `pending`/`running`/`failed`/`dead_letter` all block, per §5.4.3. I could not
  construct a false open.
- Empty path uses `obs_flush_version_state.empty_complete` and never a
  `document_version` processing row at the fan-out component version (§5.1, §5.7).
- Supersession follow-up targets `document_version` / `version_id`, never
  `unit_id`, and omits `relation_ids` only with all four reconstruction fields
  present — which `AdjudicateSupersessionHandler` (`e3.py:958-985`) does accept
  and reconstruct from. §5.4.4 satisfied.
- `add_observations` takes `pg_advisory_xact_lock` on
  `{deployment}:obs:{entity}` for the whole batch transaction
  (`observation_adjudication.py:144-146`), so the §5.6 "single xact for the whole
  unit" shape holds and no other writer interleaves inside an apply.
- Forget's `UPDATE processing_state SET payload = NULL` treats unit payloads
  correctly by accident-and-design: payload is cache-only (§5.2) and the handler
  loads from membership, so nulling it does not break replay.
- `EXPECTED_CONSTRAINT_COUNTS` bump is arithmetically right.
