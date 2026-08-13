# Implementation re-review: D91 PR1 bulk fact metadata

**Reviewer:** Codex (`gpt-5.6-sol`)
**Date:** 2026-08-13
**PR:** [#271](https://github.com/writeitai/remember-stack/pull/271)
**Branch:** `feat/d91-pr1-bulk-metadata`
**Commit:** `8bbc42b3238ec6002fad2390f67730640f987e0d`
**Base:** `1576fed3c2cd64b5507efd37690759b9f1d5587f`
**Prior review:**
`design/reviews/REVIEW_codex-sol_d91_pr1_bulk_metadata_2026-08-13.md`

## Verdict

**APPROVE_WITH_NITS**

Both requested P1 fixes are complete. `_fact_metadata_by_key()` now reads the
full facts identity, including `kind`, so a relation and observation sharing a
`fact_id` cannot consume each other's bounded lookup result. Ordinary chunk,
claim, and fact writes no longer call synchronous `optimize()`. The new
regressions exercise the shipped adapter and pass against real LanceDB.

No correctness or write-path latency blocker remains in the reviewed scope.
The outstanding items below are cleanup or explicitly later index-matrix work.

## Requested-change disposition

### P1.1 — Full-kind metadata lookup: resolved

`_fact_metadata_by_key()` now groups candidates first by `deployment_id`, then
by `kind`, and issues a predicate containing all three identity dimensions:

```text
deployment_id = ... AND kind = ... AND fact_id IN (...)
```

The per-query limit is now `len(fact_ids)` for that deployment/kind group, so a
row of the other kind cannot take a requested row's result slot. The lookup
also projects only the three key columns plus the five mutable eligibility
columns; it no longer decodes `label` or `vector` for skip-unchanged checks.

`test_fact_metadata_honors_kind_in_join_key` is a good regression for the exact
failure: it stores an observation and relation with the same deployment and
UUID, updates only the relation, then asserts that the relation changes while
the observation and both vectors remain intact. This failed under the previous
under-keyed/limited lookup and passes at the reviewed head.

### P1.2 — No synchronous optimize in ordinary writers: resolved

The `_maintain_indexed_tail()` calls have been removed from:

- `upsert_chunks()`
- `upsert_claims()`
- `upsert_facts()`

`update_fact_metadata()` already had no such call. A repository-wide call-site
check finds no caller of `_maintain_indexed_tail()` or `_optimize_with_retry()`.
The only live direct `Table.optimize()` call left in `lance.py` is the explicit
hard-forget purge path, `_purge_table_rows()`, which performs physical cleanup
after deletion; it is not an ordinary embed/label writer.

The tests cover both sides of this change:

- The chunk retry test forces the former mutation threshold and now requires
  zero optimize attempts after two upserts.
- `test_fact_writes_do_not_call_optimize` patches `Table.optimize`, forces the
  threshold to one, runs `upsert_facts()` plus `update_fact_metadata()`, and
  requires zero calls.

This matches D91's rollout contract between PR1 and the maintenance worker:
ordinary writers leave index maintenance out of the lease path. LanceDB's
public [reindexing guidance](https://docs.lancedb.com/indexing/reindexing)
treats index updating as an explicit maintenance operation, while its merge API
documents matched-only update separately from unmatched insert behavior.

## Other behavior reconfirmed

- Metadata rows are deduplicated last-write-wins on
  `(deployment_id, kind, fact_id)` before lookup and merge.
- Skip-unchanged compares exactly `status`, `valid_from_us`, `valid_until_us`,
  `ingested_at_us`, and `invalidated_at_us`; an all-unchanged refresh creates no
  new Lance table version.
- The merge is matched-only: `_merge_insert_matched()` calls
  `when_matched_update_all()` and has no `when_not_matched_insert_all()` clause.
  Its source payload omits `label` and `vector`.
- The real-adapter preservation test confirms exact vector/label retention and
  confirms that an unknown key does not insert a skeleton row.
- Join-key ensure runs before the lookup and merge, and ensures BTree indexes on
  `deployment_id` and `fact_id` plus an index on `kind`.

The matched-only shape agrees with LanceDB's public
[update and merge documentation](https://docs.lancedb.com/tables/update), which
distinguishes `when_matched_update_all()` from
`when_not_matched_insert_all()`.

## Nits

### N1 — Keep `facts.kind` Bitmap consistency tracked for PR2

The normal `upsert_facts()` path still creates a BTree for `kind`.
`_ensure_facts_join_indexes()` checks whether *any* index covers the column and
therefore accepts that BTree instead of creating the D91 matrix's preferred
Bitmap. This does not leave the merge key unindexed and is not a correctness
problem. Although the PR1 row names a `kind` Bitmap, the next row in the ordered
D91 plan explicitly assigns full index-matrix and `kind`-Bitmap consistency to
PR2. The discrepancy should remain tracked there.

The type choice itself is consistent with LanceDB's public
[scalar-index guidance](https://docs.lancedb.com/indexing/scalar-index): Bitmap
is the intended fit for low-cardinality categorical values such as the two fact
kinds.

### N2 — Remove stale inline-maintenance scaffolding when PR2 takes ownership

`_maintain_indexed_tail()`, `_optimize_with_retry()`, their mutation state and
threshold constants, and the unused `flaky_optimize` body in
`test_writes_and_maintenance_retry_commit_conflicts` remain even though no
ordinary writer calls them. In particular, `_maintain_indexed_tail()` still
says maintenance stays on the write path, the opposite of the new invariant.
Deleting or moving this scaffolding when the maintenance port lands would make
the boundary harder to regress.

### N3 — PR-wide `git diff --check` is not clean

The first-round Codex review added in the PR contains trailing Markdown spaces
on its metadata lines. The reviewed Python files are clean; this is repository
hygiene only.

## Verification

```text
uv run pytest -q src/tests/adapters/test_lance_retrieval.py
9 passed in 7.58s

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
```

Public PR metadata confirms that #271 is open at the reviewed head and targets
the stated base. No private connector or non-public source was used.

## Final assessment

The two P1 blockers from round one are resolved with targeted shipped-adapter
coverage. **APPROVE_WITH_NITS**; N1 belongs on the already-planned PR2 index
consistency work, and N2-N3 are cleanup.
