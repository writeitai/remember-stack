# Implementation re-review: D90 entity-grain observation flush fan-out

**Agent:** `codex-sol`  
**Date:** 2026-08-12  
**PR:** #265  
**Branch:** `feat/d90-entity-obs-flush-fanout` @ `0bb37204`  
**Binding design:** `plan/designs/e3_entity_obs_flush_fanout_design.md`  
**Prior implementation reviews:**
`REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_2026-08-12.md`,
`REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_r2_2026-08-12.md`, and
`REVIEW_claude-opus_e3_entity_obs_flush_fanout_impl_2026-08-12.md`

## Verdict

**REQUEST_CHANGES**

The central D90 history-loss example is substantially repaired. At this HEAD,
the entity transaction lock is acquired before the global staging snapshot,
the complete ordered stream is applied and retired by staging PK in that same
transaction, and late evidence re-enters `_add_with_block` rather than being
blindly inserted. On real PostgreSQL I reproduced the binding staggered case by
fully draining A `{t1:A,t3:A}`, materializing B `{t2:B}` later, and draining B.
It now yields exactly `A[t1,t2), B[t2,t3), A[t3,inf)` with zero staging rows
left. The formerly invalid free-string adjudication method is also gone.

That closes the originally reported distinct-time case, but it does not close
the binding *total-order* rule. Re-split decides “later” from `asserted_at`
alone, explicitly treats equal timestamps and two undated assertions as not
ordered, and therefore still loses a reassertion when the decisive order comes
from `(claim_id, statement)`. A real-PostgreSQL tied-time variant failed with
`A[t1,t2), B[t2,inf)` instead of the total-order result ending in A.

Two other prior blockers remain independently merge-blocking. Entity completion
still trusts caller-supplied coordinates and can manufacture
`barrier_complete` without the required `materialized` version-state row; I
reproduced that on PostgreSQL with forged representation/chunker/extractor/hash
coordinates. Connector-cycle finalization now sees ordinary unit rows, but its
inner join treats a membership unit with a missing processing row as complete.
The D56 sibling path also stamps a target version's durable D90 coordinates
from the origin claim/primary completion rather than from that target version.

## HEAD-claim validation

| HEAD claim | R3 result | Evidence |
| --- | --- | --- |
| 1. Entity lock before load; apply; PK delete | **Closed** | `flush_entity_global_staging` takes `_LOCK_ENTITY`, then executes the entity-global staging select, applies the ordered assertions, and deletes each selected staging PK in one transaction (`observation_adjudication.py:168-225`). The handler calls only this locked operation (`e3.py:768-775`). |
| 2. Re-split re-enters `_add_with_block` | **Partial — blocker remains** | Re-entry correctly caps B in the distinct-time acceptance case (`observation_adjudication.py:885-946`). The “later” predicate uses only timestamps, however, so equal-time and two-undated ordering by claim/statement is lost (`observation_adjudication.py:917-921,1050-1065`). |
| 3. `EmptyObsFlushComplete`, `completed_at`, normalizer pin | **Closed for the reported paths** | E1/E2 return `EmptyObsFlushComplete`; worker completion writes `empty_complete` and sibling follow-ups; readiness uses `COALESCE(max(s.completed_at), max(p.finished_at))` and pins state/units to the active normalizer (`base.py:95-120,342-352`; `e1.py:632-660`; `e2.py:1067-1086`; `readiness.py:430-469`). |
| 4. Lifecycle wait; legacy `has_obs_flush_fanout` refusal | **Partial** | The reverse legacy guard is present (`e3.py:819-828`). Lifecycle waits on present pending/running/failed/DLQ rows, but a missing row falls through its inner join and a mixed-image capability/stop-drain gate is still absent (`lifecycle.py:1071-1087`; `work_ledger.py:1372-1385`). |
| 5. CI table count 69 | **Closed** | The migration assertion is 69 and the full real-PostgreSQL migration lifecycle passed (`test_migrations.py:438-441`). CI also pins Alembic head `p9_10_0031`. |

## Prior-blocker disposition

| Prior issue | R3 status | Evidence |
| --- | --- | --- |
| PostgreSQL `min(uuid)` fan-out failure | **Closed** | The selector uses ordered `array_agg(uuid)[1]` (`work_ledger.py:1697-1709`); the structural head and 69-table lifecycle pass on PostgreSQL. |
| Entity-global merge-apply | **Closed** | Lock-before-load global drain and per-row retirement are now one transaction (`observation_adjudication.py:168-225`). Two co-present workers serialize before taking their snapshots. |
| D43 late-arrival re-split | **Partial — blocker** | The named `t1<t2<t3` case passes, and re-split re-enters D43. Tied/undated assertions still violate the binding total key (B1). |
| Empty observation paths/readiness | **Closed for normal operation** | Direct-empty E1/E2 paths no longer enqueue a fan-out-generation document-version row; `completed_at` and normalizer generation are honored. Completion still needs the authority guard in B2. |
| Connector lifecycle | **Partial — blocker** | Ordinary unfinished/DLQ entity rows block, but missing expected processing rows do not (B4). |
| Hard forget | **Partial — non-blocking debt after the blockers below** | Membership/state deletion landed, but exact unit processing ids are not captured before membership deletion; D56 mis-stamped coordinates make generic payload/hash matching less reliable (B3/N1). |
| Legacy/fan-out exclusion | **Partial** | Fan-out refuses active legacy work and legacy now refuses materialized D90 state/units. The old stage-only worker claim route remains ungated during mixed-image rollout (B5). |
| Authoritative version-state completion | **Open — blocker** | Completion still updates the supplied processing id, counts caller-selected membership, and upserts from caller coordinates without loading/validating `materialized` state (B2). |
| Ruff/Pyright/test regression | **Closed** | Full Ruff lint/format, full Pyright, worker tests, and CI inventory check pass at this HEAD. |

## Remaining blockers

### B1 — Late re-split does not implement the binding total order for ties or undated claims

The global staging query correctly orders by
`(asserted_at NULLS LAST, claim_id, statement)`. The late-arrival repair loses
two-thirds of that key. `_resplit_later_evidence` receives only `boundary`
(the incoming assertion timestamp), and `_is_strictly_later` compares only the
two timestamps. Its comments explicitly keep equal timestamps and two undated
assertions on the capped row (`observation_adjudication.py:917-921,1050-1065`).

Real-PostgreSQL counterexample:

- `A@t1` is applied;
- `A@t2` with claim id `ffff...` collapses as evidence;
- later, `B@t2` with claim id `0002...` arrives;
- the binding total order is `A@t1, B@t2, A@t2` because B's claim id sorts
  first at the tied timestamp.

Expected history is `A[t1,t2), B[t2,t2), A[t2,inf)` (the zero-width middle
slice is permitted by the schema and is the deterministic total-order result).
Actual history is `A[t1,t2), B[t2,inf)`; A's tied reassertion stays only as
evidence on the capped row. Two undated claims have the analogous failure, and
same-claim statements need the final statement tie-break.

Pass the incoming assertion's complete order key into re-split and compare each
evidence reassertion by the same PostgreSQL ordering semantics, including
`NULLS LAST`, claim id, and statement. Add executable tied and undated staggered
tests; the current source-inspection assertion cannot detect this loss.

### B2 — Completion still does not require authoritative materialized state or bind the claimed unit

`complete_entity_obs_flush` takes the representation lock named by the caller,
marks any supplied running processing id succeeded, counts units using caller
version/generation coordinates, then upserts `barrier_complete` and enqueues
from the caller object (`work_ledger.py:425-518`). It never joins the processing
row to `obs_flush_entity_units`, never loads `obs_flush_version_state`, and never
requires that state to exist with `fanout_status='materialized'`.

The PostgreSQL probe was decisive:

1. create one membership unit and its running D90 processing row;
2. delete the version-state row;
3. call completion with forged representation, chunker, extractor, and hash;
4. completion succeeds, inserts `barrier_complete` with every forged value,
   and enqueues downstream work.

This is the exact false expected-set authority the durable version-state marker
was introduced to prevent. It also locks the forged representation rather than
the authoritative representation, so it need not serialize with claim fan-out.

Inside the completion transaction, resolve `processing_id -> exact D90 row ->
unit -> materialized version state`, validate deployment/target/stage/component
and the unit/state coordinate equality, acquire the lock from stored
`representation_id`, then complete and anti-join. Derive all follow-up fields
from membership/state. `complete_empty_obs_flush` should likewise fail closed
if an existing state is `materialized`, rather than unconditionally enqueueing
the empty follow-ups after merely skipping the state upsert.

### B3 — D56 sibling fan-out persists origin/primary coordinates for the target version

For the primary completion, `ClaimNormalizeBarrier.doc_id` is the target
version's document. For sibling versions discovered through D56 occurrence,
`_VERSIONS_WITH_CLAIM_OCCURRENCE` selects `cl.doc_id`, which is the claim's
origin document (`work_ledger.py:330-361,1407-1417`). The fan-out call then uses
that origin `doc_id` and reuses the primary completion's `content_hash` for every
sibling (`work_ledger.py:377-421`). `_SELECT_STAGING_ENTITIES_FOR_FANOUT`
reinforces the problem by choosing a staged origin doc through `array_agg`
(`work_ledger.py:1697-1709`).

The resulting membership/state for version B can therefore carry document and
content coordinates from version A. Entity completion copies those values into
supersession/embed processing rows. This violates §§5.1, 5.2, and 5.4's durable
target-version coordinate contract and makes hard-forget/operator attribution
unreliable for reused claims.

Load each candidate version's authoritative `doc_id` and `content_hash` from
the document-version/representation catalog. Pass them through the sibling
candidate and use that doc id for every unit; staging `doc_id` remains evidence
provenance and must not become unit ownership.

### B4 — Connector-cycle lifecycle treats a missing expected processing row as finished

The new lifecycle clause joins membership to `processing_state` and rejects
only rows whose joined status is pending/running/failed/dead-letter
(`lifecycle.py:1071-1087`). If a membership unit has no processing row, the
inner join returns nothing and the cycle may finalize. A row at an unrelated
component generation can similarly mask the intended D90 generation because
the join is not component-pinned.

D90's membership table is the durable expected set; a missing processing row is
explicitly non-terminal in §5.4. Make lifecycle ask whether any membership unit
lacks a `succeeded` processing row at the exact fan-out component version.
Pending, running, failed, DLQ, and missing must all keep the cycle open.

### B5 — Producer enablement is still unsafe for a mixed-image rollout

The reverse legacy guard closes same-version coexistence once capable code is
handling the row. It does not prevent an old stage-only worker from claiming a
new entity-unit row: `_CLAIM_SELECT` filters only deployment, stage, lane,
status, and due time (`work_ledger.py:1372-1385`). No capability flag or
stop/drain/restart release mechanism/documentation changed in this branch.

The binding design requires producer enablement only after all observation
workers understand entity units. Bind and test a capability transition, or make
the deployment contract explicitly stop, drain legacy work, replace all
workers, and only then start producers. The new reverse handler check cannot
protect a row already claimed by an old image.

## Nits and coverage debt

- Hard forget deletes `obs_flush_entity_units` before resolving their `unit_id`
  values and does not explicitly scrub/verify the corresponding processing rows
  through membership (`forget.py:1255-1273,1460-1487,1718-1742`). Capture exact
  unit ids first; do not depend on payload/hash substring matching, especially
  once B3 is corrected and shared entities/contents are considered.
- Both entity-global staging selectors join observation processing rows without
  pinning `component_version` (`observation_adjudication.py:1150-1169`;
  `fact_catalog.py:765-785`). A second generation can duplicate eligibility or
  let a non-DLQ row mask the D90 row's DLQ. Pin the literal D90 generation.
- The three migration indexes remain absent from `EXPECTED_INDEXES`, so schema
  verification proves the 69 tables but not the D90 index inventory
  (`catalog_contract.py:172-265`).
- Entity dispatch accepts every `target_kind=entity` observation job regardless
  of component version; the binding dispatch is entity **and** the exact fan-out
  version (`e3.py:738-747`).
- The D90 test module still consists of seven source/model-field inspections.
  There is no committed executable PostgreSQL fan-out, barrier, co-present,
  staggered, tied/undated, lifecycle, shared-entity forget, or concurrency
  proof (`test_e3_entity_obs_flush_fanout.py:13-81`). The custom PostgreSQL
  checks used for this review should become repository tests.

## Verification on `0bb37204`

```text
git rev-parse HEAD
0bb372041b35a18043795d8f2e5a7b7cc5b4d269

uv run ruff check src/ benchmarks/
All checks passed!

uv run ruff format --check src/ benchmarks/
367 files already formatted

uv run pyright src/ benchmarks/ --pythonversion 3.13
0 errors, 0 warnings, 0 informations

uv run pytest -q src/tests/workers
122 passed, 90 skipped

python3 .github/ci/check_test_inventory.py
test inventory OK: unit=66 integration=53 discovered=119

REMEMBERSTACK_DATABASE_URL=<isolated docker PostgreSQL> \
  uv run pytest -q src/tests/spine/test_migrations.py
6 passed in 186.24s

REMEMBERSTACK_DATABASE_URL=<isolated docker PostgreSQL> \
  uv run pytest -q src/tests/spine/test_observation_adjudication.py \
    src/tests/spine/test_pipeline_readiness.py
21 passed in 103.32s

custom real-PostgreSQL D90 staged staggered proof
A[t1,t2), B[t2,t3), A[t3,inf); remaining staging = 0  PASS

custom real-PostgreSQL tied staggered proof
expected total-order tail A[t2,inf); actual B[t2,inf)  FAIL (B1)

custom real-PostgreSQL missing-state completion probe
barrier_complete created with forged representation/chunker/extractor/hash  FAIL (B2)
```

The three mid-review defects named for `0bb37204` are fixed for the exact
distinct-time example, including valid enum provenance and lock-before-load.
Approval still requires the full total-order re-split, authoritative completion
and sibling coordinates, a missing-row-safe lifecycle barrier, and a bound
mixed-image cutover.
