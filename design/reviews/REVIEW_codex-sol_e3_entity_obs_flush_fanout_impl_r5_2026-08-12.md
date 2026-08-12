# Implementation re-review: D90 entity-grain observation flush fan-out (r5)

**Agent:** `codex-sol`  
**Date:** 2026-08-12  
**PR:** #265  
**Branch:** `feat/d90-entity-obs-flush-fanout` @ `4adbc875`  
**Prior review:** `REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_r4_2026-08-12.md`

## Verdict

**REQUEST_CHANGES**

I cannot confirm the lifecycle repair or issue the requested `APPROVE` /
`APPROVE_WITH_NITS` verdict. The predicate no longer contains
`%:entity-fanout-%`, but the replacement SQL comment introduces the same class
of bind error:

```sql
-- Avoid ':name' inside text() (SQLAlchemy bind). Match D90 generation.
AND w.component_version LIKE '%entity-fanout%'
```

SQLAlchemy scans the entire `text()` body, including SQL comments. It therefore
parses `:name` in the comment as a required bind parameter. At `4adbc875`:

```text
sorted(lifecycle._SELECT_READY_CYCLES._bindparams)
['deployment_id', 'name']

connection.execute(_SELECT_READY_CYCLES, {'deployment_id': 'probe'})
InvalidRequestError: A value is required for bind parameter 'name'
```

`LifecycleRepository.cycles_ready_to_finalize()` supplies only
`deployment_id`, so connector-cycle finalization still raises before the query
reaches PostgreSQL. This is the same merge-blocking lifecycle outage identified
by Claude r4, with the accidental bind moved from the predicate to its comment.

Remove or reword the comment so it contains no colon-prefixed identifier, then
assert that `_SELECT_READY_CYCLES._bindparams` contains exactly
`deployment_id`. An executable cycle-finalization regression test should land
with the repair; the current local run did not exercise that path because all
12 PostgreSQL lifecycle tests were skipped.

No other implementation changed in r5. Codex r4's residual nits therefore
remain non-blocking, including the broad generation substring match; after this
bind defect is fixed, the expected verdict remains `APPROVE_WITH_NITS` unless
the follow-up changes introduce another issue.

## Verification

```text
uv run python <bind/execution probe>
binds ['deployment_id', 'name']
InvalidRequestError: A value is required for bind parameter 'name'

uv run pytest -q \
  src/tests/workers/test_lifecycle_reconciliation.py \
  src/tests/workers/test_e3_claim_normalize_fanout.py
15 passed, 12 skipped

uv run ruff check src/rememberstack/spine/lifecycle.py
All checks passed!

uv run ruff format --check src/rememberstack/spine/lifecycle.py
1 file already formatted
```
