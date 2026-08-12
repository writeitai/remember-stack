# Implementation re-review: D90 entity-grain observation flush fan-out (r6)

**Agent:** `codex-sol`
**Date:** 2026-08-12
**PR:** #265
**Branch:** `feat/d90-entity-obs-flush-fanout` @ `86d8c599`
**Prior review:** `REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_r5_2026-08-12.md`

## Verdict

**APPROVE_WITH_NITS**

The r5 lifecycle blocker is closed. `_SELECT_READY_CYCLES` now has exactly one
SQLAlchemy bind parameter, `deployment_id`; the cautionary SQL comment no
longer contains a colon-prefixed token that `text()` can misinterpret as a
bind. `LifecycleRepository.cycles_ready_to_finalize()` therefore supplies the
complete parameter set again.

The added regression assertion directly protects the invariant:

```python
assert set(lifecycle._SELECT_READY_CYCLES._bindparams.keys()) == {"deployment_id"}
```

Runtime inspection at `86d8c599` independently produced:

```text
binds ['deployment_id']
deployment_occurrences 1
```

No correctness issue was found in the r6 implementation/test delta, and no
merge blocker remains from r5.

## Nit

The regression test inspects SQLAlchemy's private `_bindparams` attribute
rather than executing `cycles_ready_to_finalize()` against PostgreSQL. It is a
precise guard for this parser regression and is sufficient for approval, but a
public-path integration case would provide stronger protection against future
query/execution drift. The local PostgreSQL lifecycle tests remained skipped
because no `REMEMBERSTACK_DATABASE_URL` was configured.

The earlier non-blocking cleanup/coverage notes remain unchanged, including
the broad `LIKE '%entity-fanout%'` generation match and fuller end-to-end D90
barrier/retry coverage.

## Verification

```text
uv run python <_SELECT_READY_CYCLES bind probe>
binds ['deployment_id']
deployment_occurrences 1

uv run pytest -q \
  src/tests/workers/test_lifecycle_reconciliation.py \
  src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_e3_entity_obs_flush_fanout.py
22 passed, 12 skipped in 3.06s

uv run ruff check src/ benchmarks/
All checks passed!

uv run ruff format --check src/ benchmarks/
367 files already formatted

uv run pyright src/ benchmarks/ --pythonversion 3.13
0 errors, 0 warnings, 0 informations

uv run python .github/ci/check_test_inventory.py
test inventory OK: unit=66 integration=53 discovered=119
```
