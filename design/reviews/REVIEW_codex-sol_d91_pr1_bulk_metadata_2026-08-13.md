# Implementation review: D91 PR1 bulk fact metadata

**Reviewer:** Codex (`gpt-5.6-sol`), dual independent passes  
**Date:** 2026-08-13  
**PR:** #271  
**Branch:** `feat/d91-pr1-bulk-metadata` @ `73bcadbd`  
**Scope:** `src/rememberstack/adapters/selfhost/lance.py` and
`src/tests/adapters/test_lance_retrieval.py`

## Verdict

**REQUEST_CHANGES**

The core partial merge has the right shape: it uses the full facts join key,
has no unmatched-insert clause, omits `label` and `vector` from the source
payload, deduplicates last-write-wins, and skips scalar-identical rows. The new
tests call the shipped `LanceChunkIndex` adapter and confirm vector/label
preservation, no skeleton insertion for a miss, and no second Lance version for
an unchanged refresh.

Two blockers remain. The skip-unchanged pre-read does not query the complete
composite key and can silently suppress a real update. Separately, D91's
no-synchronous-optimize writer contract has not landed: fact upserts can still
run `table.optimize()` while `LabelFactsHandler` holds `label_lock`.

## Findings

### P1.1 — The skip-unchanged lookup can silently skip a changed row of the requested kind

`_fact_metadata_by_key()` groups requested keys by deployment, but its Lance
predicate contains only `deployment_id` and `fact_id`, then caps the result at
`len(items)` (`lance.py:386-399`). `kind` is part of the authoritative join key
everywhere else (`lance.py:302`, `lance.py:350-365`). A relation and an
observation may therefore have the same UUID without being the same facts row.

For a one-row relation refresh, the lookup can return the observation row and
stop at its limit. The requested relation key is then absent from `found`.
`fact_metadata_scalars_differ(existing=None, ...)` deliberately returns false
for a real Lance miss (`lance.py:111-123`), so the relation is incorrectly
classified as not mergeable and remains stale.

I reproduced this through the shipped adapter with one observation followed by
one relation sharing `(deployment_id, fact_id)`, then refreshed only the
relation. The persisted result was:

```text
statuses [('observation', 'active'), ('relation', 'active')]
```

The relation should have become `invalidated`. Make the pre-read honor the
complete requested `(deployment_id, kind, fact_id)` key without truncating away
valid matches, and add this cross-kind collision as an adapter regression.

### P1.2 — Synchronous optimize remains on the fact write path under `label_lock`

Removing `_maintain_indexed_tail()` from `update_fact_metadata()` is correct,
but it does not complete the D91 PR1 writer invariant. `upsert_facts()` still
calls `_maintain_indexed_tail()` (`lance.py:335`), which calls
`_optimize_with_retry()` once its local threshold trips (`lance.py:1006-1033`),
and that method synchronously invokes `Table.optimize()` (`lance.py:1035-1039`).
The production caller executes fact upserts inside `LabelFactsHandler`'s
`label_lock` (`workers/p1.py:202`, `workers/p1.py:260-290`). A sufficiently
large label job can therefore still compact/prune/update indexes while holding
the lock and lease that D91 explicitly keeps free of maintenance.

The same helper is still called by chunk and claim upserts (`lance.py:169`,
`lance.py:276`), so the general no-write-path-optimize rule is not true either.
Remove the synchronous optimize edge from ordinary writers as required by PR1;
maintenance may be enqueued later, but this PR need not wait for the worker.

The existing retry test currently reinforces the old behavior: it lowers the
mutation threshold, performs chunk upserts, and requires two optimize attempts
(`test_lance_retrieval.py:379-440`). Adjust that coverage with the implementation
so it no longer requires maintenance on a write path, and add a direct guard
that fact metadata refresh/upsert cannot call `Table.optimize()`.

### P2.1 — Normal facts stores retain a BTree for `kind`, not the binding Bitmap join-key index

`upsert_facts()` first creates a BTree for `kind` via `_ensure_scalar_index()`
(`lance.py:325-327`). `_ensure_facts_join_indexes()` later asks only whether
*any* index covers the column and creates the Bitmap only when none exists
(`lance.py:368-378`). The normal shipped path consequently retains the BTree;
the reproduction above reported:

```text
indices [('BTree', ['deployment_id']),
         ('BTree', ['fact_id']),
         ('BTree', ['kind'])]
```

That is inconsistent with D91 PR1's required join-key set: deployment/fact IDs
use BTree and low-cardinality `kind` uses Bitmap. The metadata test asserts only
the two BTree indexes after the update (`test_lance_retrieval.py:273-275`), so
it misses the inconsistency. Make the ensure path type-aware and cover the
expected index types.

### P2.2 — Join-key ordering is asserted only as post-state

The code currently calls `_ensure_facts_join_indexes()` before the pre-read and
merge (`lance.py:345-365`), which is the correct order. The test does not lock
that order down: it creates the row through `upsert_facts()`—already installing
two key indexes—and inspects indexes only after `update_fact_metadata()`
returns (`test_lance_retrieval.py:219-275`). Moving index creation after merge
would still pass.

Add an upgraded/raw facts table with no indexes and observe the shipped merge
execution, asserting all contracted join-key indexes exist before `execute()`.
This also covers the upgrade case the BEAM-scale safeguard is meant to protect.

## Confirmed behavior

- **Matched-only/no vector wipe:** `_merge_insert_matched()` configures only
  `when_matched_update_all()` (`lance.py:405-418`); it has no unmatched-insert
  clause. The partial payload contains no vector or label. The persisted-state
  test confirms both survive and a missing key does not add a row
  (`test_lance_retrieval.py:213-275`). This matches LanceDB's documented
  [matched-only merge behavior](https://docs.lancedb.com/tables/update#update-matched-rows-only).
- **Skip-unchanged:** for an unambiguous complete key, comparison covers all
  five mutable eligibility scalars and the second identical call produces no
  new table version (`test_lance_retrieval.py:278-313`). P1.1 is the exception
  that must be fixed.
- **Shipped adapter, not a reimplementation:** the tests construct
  `LanceChunkIndex`, call its public methods, and inspect the real Lance table.
  `_fact_lance_row()` is only a persisted-state reader.
- **Index rationale:** LanceDB recommends scalar indexes on merge join columns;
  its scalar-index guidance assigns BTree to high-cardinality values and Bitmap
  to low-cardinality categories
  ([merge guidance](https://docs.lancedb.com/tables/update#use-scalar-indexes-to-speed-up-merge-insert),
  [scalar index types](https://docs.lancedb.com/indexing/scalar-index)).

## Verification

```text
uv run pytest -q src/tests/adapters/test_lance_retrieval.py
7 passed in 18.36s

uv run ruff check \
  src/rememberstack/adapters/selfhost/lance.py \
  src/tests/adapters/test_lance_retrieval.py
All checks passed!

uv run ruff format --check \
  src/rememberstack/adapters/selfhost/lance.py \
  src/tests/adapters/test_lance_retrieval.py
2 files already formatted

uv run pyright \
  src/rememberstack/adapters/selfhost/lance.py \
  src/tests/adapters/test_lance_retrieval.py \
  --pythonversion 3.13
0 errors, 0 warnings, 0 informations

git diff --check origin/main...HEAD
clean
```

## Approval gate

Fix P1.1 and P1.2, then add shipped-adapter regressions for the composite-key
collision, join-index-before-merge ordering/types, and absence of synchronous
write-path optimize. P2.1 should be resolved in the same join-index correction;
P2.2 is the test needed to keep that correction from regressing.
