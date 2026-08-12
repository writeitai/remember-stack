# Implementation re-review: D90 entity-grain observation flush fan-out (r4)

**Agent:** `codex-sol`
**Date:** 2026-08-12
**PR:** #265
**Branch:** `feat/d90-entity-obs-flush-fanout` @ `1fa5fb8b`
**Binding design:** `plan/designs/e3_entity_obs_flush_fanout_design.md`
**Prior reviews:** Codex r3 and Claude r3 at `0bb37204`

## Verdict

**APPROVE_WITH_NITS**

The r3 merge blockers in the D90 apply/barrier/lifecycle path are closed. The
late-evidence re-split now ignores withdrawn testimony and compares the complete
`(asserted_at NULLS LAST, claim_id, statement)` key; entity completion refuses
to manufacture `barrier_complete` unless durable state is already
`materialized` or `barrier_complete`; and connector-cycle finalization treats a
membership unit without a succeeded fan-out processing row as non-terminal.

I re-ran the committed distinct-time staggered and co-present cases on real
PostgreSQL 16. Both pass. I also ran a tied-timestamp staggered probe with fixed
UUIDs so the claim-id tie-break was decisive: `B@t2` used claim
`...0002`, the collapsed `A@t2` reassertion used `ffff...`, and the final
history was exactly `A[t1,t2), B[t2,t2), A[t2,inf)`. This closes Codex r3 B1,
not merely its distinct-time example.

The remaining issues are cleanup and coverage debt, not a reason to retain the
r3 `REQUEST_CHANGES` verdict. One mechanical nit does need fixing before merge:
the required Ruff format check is red for one test file.

## R3 finding disposition

| R3 issue | R4 result | Evidence |
| --- | --- | --- |
| Codex B1: re-split ignored claim/statement tie-breaks | **Closed** | `_resplit_later_evidence` passes both complete keys to `_is_later_in_total_order`; its NULL, timestamp, UUID, and statement comparisons match the staging order (`observation_adjudication.py:925-932,1061-1089`). Fixed-UUID PostgreSQL tied probe passed. |
| Claude B1: withdrawn testimony could reopen a slice | **Closed** | `_SELECT_EVIDENCE_FOR_OBS` now requires `c.is_current_testimony` before evidence can be detached and re-applied (`observation_adjudication.py:1197-1207`). |
| Codex B2: completion could invent authoritative state | **Closed for the merge gate; authority cleanup remains** | Follow-ups and the `barrier_complete` upsert now require an existing status in `materialized|barrier_complete` (`work_ledger.py:457-490`). A deleted/missing state can no longer create a completed barrier. See N2 for deriving all coordinates from the stored row. |
| Codex B3: sibling target coordinates | **Open nit** | D56 sibling discovery still selects `cl.doc_id`, and fan-out still reuses the completing parent barrier's `content_hash` (`work_ledger.py:405-421,1421-1431`). See N2. |
| Codex B4: missing processing row looked terminal | **Closed** | Lifecycle now `LEFT JOIN`s the expected membership set and blocks on `NULL` or any non-succeeded status (`lifecycle.py:1071-1087`). |
| Codex B5: mixed-image rollout gate | **Open operational nit** | Same-version mutual exclusion remains implemented, but the queue claim is still capability-blind. See N3. |
| Claude B2: zero executable D90 coverage | **Substantially improved; more coverage remains** | Executable PostgreSQL co-present and staggered history tests now exist (`test_observation_adjudication.py:192-306`). They pass, though they exercise the adjudicator directly rather than complete unit materialization and barrier flow. |

## Residual nits

### N1 — Required Ruff format check is red

`uv run ruff format --check src/ benchmarks/` reports that
`src/tests/workers/test_e3_claim_normalize_fanout.py` would be reformatted. The
only change is the wrapping of the `claim_wait` split at lines 235-237, but this
is a required CI job (`.github/workflows/ci.yml:61-62`). Run Ruff format before
merge.

### N2 — Finish making durable target coordinates authoritative

Two related coordinate issues remain:

- For D56 sibling versions, `_VERSIONS_WITH_CLAIM_OCCURRENCE` obtains `doc_id`
  from the origin claim rather than the target version, while
  `complete_claim_normalize` passes the primary completion's `content_hash` to
  every sibling fan-out (`work_ledger.py:405-421,1421-1431`). Load the target
  version's `doc_id` and content identity from its representation/version
  catalog before materializing membership.
- `complete_entity_obs_flush` now checks the stored status, which closes the r3
  false-barrier repro, but `_SELECT_OBS_FLUSH_VERSION_STATE` returns only that
  status and `_UPSERT_OBS_FLUSH_VERSION_STATE` can still overwrite all durable
  coordinates from the handler object (`work_ledger.py:467-490,1753-1782`). Load
  the stored representation/chunker/extractor/hash and derive the lock and
  follow-ups from it. A missing or incompatible state should be detected before
  `_COMPLETE`, rather than committing a succeeded unit with no retryable barrier
  fire. The empty completion path should likewise refuse an existing
  `materialized` state instead of enqueueing empty follow-ups
  (`work_ledger.py:566-629`).

The ordinary worker path currently constructs the barrier from membership, so
these are authority and recovery hardening rather than a reproduced wrong
history on the accepted path.

### N3 — Bind the mixed-image and exact-generation contract

The two-way legacy/fan-out handler checks protect capable workers, but
`_CLAIM_SELECT` remains generic and cannot prevent an old stage-only worker from
claiming an entity unit (`work_ledger.py:1386-1399`). Record and test the stated
all-workers-capable or stop/drain/restart deployment gate. Also replace broad
`LIKE '%:entity-fanout-%'` / `NOT LIKE` generation tests with the declared
component versions where practical; lifecycle is conservatively correct now,
but mixed generations can otherwise block or duplicate work unexpectedly.

### N4 — Commit the remaining executable boundary cases

The two new PostgreSQL tests are valuable, but the “co-present” test passes an
already ordered tuple directly to `add_observations`; it does not materialize
two membership units or execute `flush_entity_global_staging`. Add end-to-end
tests for two units/global staging, the fixed-UUID tied case used in this
review, withdrawn-testimony re-split, 2/3 versus 3/3 barrier fire, missing-state
completion, missing-row lifecycle, D56 sibling coordinates, shared-entity
forget, and a partial retry. The lifecycle addition at
`test_e3_claim_normalize_fanout.py:245-252` is still a SQL substring assertion,
not an executed cycle-finalization proof.

Carried lower-priority cleanup remains: pin the global staging processing join
to the exact D90 component version, register the D90 indexes in
`EXPECTED_INDEXES`, and make hard-forget resolve exact unit processing ids from
membership before deleting membership.

## Verification on `1fa5fb8b`

```text
git rev-parse HEAD
1fa5fb8bed54f7dec2fb87103b49d235ed849a9f

PostgreSQL 16:
uv run pytest src/tests/spine/test_observation_adjudication.py \
  -k 'd90_staggered_late_arrival_resplit_shapes or d90_copresent_global_order_shapes'
2 passed, 10 deselected in 39.68s

custom fixed-UUID tied staggered probe
A[t1,t2), B[t2,t2), A[t2,inf)  PASS

uv run pytest -q \
  src/tests/workers/test_e3_entity_obs_flush_fanout.py \
  src/tests/workers/test_e3_claim_normalize_fanout.py
22 passed in 2.60s

uv run ruff check src/ benchmarks/
All checks passed!

uv run ruff format --check src/ benchmarks/
Would reformat: src/tests/workers/test_e3_claim_normalize_fanout.py  FAIL (nit N1)

uv run pyright src/ benchmarks/ --pythonversion 3.13
0 errors, 0 warnings, 0 informations

python3 .github/ci/check_test_inventory.py
test inventory OK: unit=66 integration=53 discovered=119
```

`git diff --check main...1fa5fb8b` additionally reports Markdown hard-break
trailing spaces in the committed Codex r3 review only; no implementation file
is implicated.
