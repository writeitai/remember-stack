# Round 3 implementation review: D86 E3 unknown entity type gate

**Verdict:** REQUEST_CHANGES  
**Reviewer:** Codex (`codex-sol`)  
**Date:** 2026-08-10  
**Branch:** `fix/e3-unknown-entity-type-retry-drop` at `acf605a5`  
**Previous review:** `design/reviews/REVIEW_codex-sol_e3_unknown_entity_type_gate_impl_r2_2026-08-10.md`

## Summary

Both Codex round-2 blockers are fixed.

The handler no longer turns an all-content-poison version back into a retry or
dead letter: it emits `e3.normalize_all_soft_failed` and continues to the
terminal branches (`src/rememberstack/workers/e3.py:217`,
`src/rememberstack/workers/e3.py:226`,
`src/rememberstack/workers/e3.py:238`). The failed-call meter now records
`normalize:{claim_id}:aN:failure` only for
`ProviderInvalidResponseError`; a generic `ProviderCallError` escapes without
an inner record and is metered once by the worker boundary
(`src/rememberstack/workers/e3.py:452`,
`src/rememberstack/workers/e3.py:457`,
`src/rememberstack/workers/base.py:234`,
`src/rememberstack/workers/base.py:299`). The binding design now states both
rules explicitly (`plan/designs/e3_unknown_entity_type_gate_design.md:110`,
`plan/designs/e3_unknown_entity_type_gate_design.md:129`,
`plan/designs/e3_unknown_entity_type_gate_design.md:142`).

The auxiliary R2 fixes are also present: the FK alarm is filtered before it is
emitted (`src/rememberstack/workers/e3.py:191`,
`src/rememberstack/workers/e3.py:516`), illegal labels are length-bounded on
all D86 log paths (`src/rememberstack/workers/e3.py:316`,
`src/rememberstack/workers/e3.py:399`,
`src/rememberstack/workers/e3.py:480`), and the new test invokes the real
`CascadeResolver._mint` guard (`src/tests/workers/test_e3_unknown_entity_type_gate.py:418`,
`src/rememberstack/spine/resolver.py:423`).

One correctness issue remains in the soft-failure boundary. The handler tests
the exception class around the whole `_normalize_claim`, not around the
normalizer generate call. `ProviderInvalidResponseError` can also come from a
resolver T4 structured generation. That path is soft-swallowed, but its usage
is not recorded by E3 and the exception never reaches the worker's fallback
meter. This reintroduces an unbilled provider-call path and can leave a claim
partially applied. It should be fixed before merge.

## Round-2 blocker disposition

| Round-2 blocker | Disposition | Evidence |
| --- | --- | --- |
| All-soft no-progress `RuntimeError` dead-letters the version | **RESOLVED** | The all-soft condition logs `e3.normalize_all_soft_failed` without raising, then returns adjudicate/embed terminal follow-ups (`src/rememberstack/workers/e3.py:217`, `src/rememberstack/workers/e3.py:226`, `src/rememberstack/workers/e3.py:238`). This matches the accepted contract (`plan/designs/e3_unknown_entity_type_gate_design.md:110`, `plan/designs/e3_unknown_entity_type_gate_design.md:146`). |
| Escaping usage-bearing `ProviderCallError` is double-billed | **RESOLVED** | Inner failure metering is guarded by `isinstance(exception, ProviderInvalidResponseError)` (`src/rememberstack/workers/e3.py:452`, `src/rememberstack/workers/e3.py:457`). Other provider errors re-raise (`src/rememberstack/workers/e3.py:199`) and are metered once as `provider_failure` (`src/rememberstack/workers/base.py:234`, `src/rememberstack/workers/base.py:307`). The direct regression test is at `src/tests/workers/test_e3_unknown_entity_type_gate.py:382`. |

## Finding requiring changes

### Major — resolver invalid responses are soft-swallowed without usage accounting

`handle` wraps the entire `_normalize_claim` call and treats every
`ProviderInvalidResponseError` as claim-soft
(`src/rememberstack/workers/e3.py:169`,
`src/rememberstack/workers/e3.py:199`,
`src/rememberstack/workers/e3.py:505`). `_normalize_claim` includes both
relation and observation resolver calls
(`src/rememberstack/workers/e3.py:352`,
`src/rememberstack/workers/e3.py:409`). The real resolver can issue structured
T4 `generate` calls (`src/rememberstack/spine/resolver.py:395`,
`src/rememberstack/spine/resolver.py:409`), and the shipped provider attaches
usage when those calls return invalid JSON/schema output
(`src/rememberstack/adapters/openrouter.py:235`,
`src/rememberstack/adapters/openrouter.py:243`).

If one of those resolver calls raises `ProviderInvalidResponseError`, E3 catches
and continues the claim loop. The attempt-level failure meter cannot help: it
exists only around the normalizer's own provider call
(`src/rememberstack/workers/e3.py:429`,
`src/rememberstack/workers/e3.py:452`). The worker's `provider_failure` fallback
also cannot help because the exception no longer escapes `handle`
(`src/rememberstack/workers/base.py:234`,
`src/rememberstack/workers/base.py:299`). The billable resolver call is therefore
absent from the cost ledger.

The boundary also makes “skip this claim” non-atomic. A resolver failure can
occur after an earlier relation from the same response has already been
upserted (`src/rememberstack/workers/e3.py:386`), after which the handler
soft-succeeds the version. The design describes the soft class as normalizer
structured-output poison on generate (`plan/designs/e3_unknown_entity_type_gate_design.md:110`),
not an exception-class exemption for every downstream structured call.

Narrow soft isolation to the normalizer generate boundary, or explicitly define
and meter resolver-invalid-response isolation with call-specific keys and
partial-claim semantics. Add a regression test that raises a usage-bearing
`ProviderInvalidResponseError` from `resolver.resolve` and proves the intended
outcome and exactly-once accounting.

## Non-blocking test gaps

- The requested test file still never calls `NormalizeRelationsHandler.handle`;
  its helpers call `_generate_normalize_response` or `_normalize_claim`
  directly (`src/tests/workers/test_e3_unknown_entity_type_gate.py:184`,
  `src/tests/workers/test_e3_unknown_entity_type_gate.py:250`). Consequently,
  the new all-soft success event and terminal follow-ups are not protected by a
  regression test even though they fix the first R2 blocker
  (`src/rememberstack/workers/e3.py:217`,
  `plan/designs/e3_unknown_entity_type_gate_design.md:193`).
- The failure-key test covers an `a1` invalid response only
  (`src/tests/workers/test_e3_unknown_entity_type_gate.py:345`). An illegal
  successful `a1` followed by a usage-bearing invalid `a2` is the D86-specific
  recovery failure and should assert `a1` plus exactly one `a2:failure` record.
- The five pre-existing mypy errors in the new test file remain at
  `src/tests/workers/test_e3_unknown_entity_type_gate.py:178`, `:211`, `:244`,
  `:279`, and `:308`. Production `e3.py` and `resolver.py` did not surface
  errors attributable to this patch; the repository-wide targeted invocation
  also reports unrelated missing optional stubs and existing errors.

## Verification

- `uv run pytest src/tests/workers/test_e3_unknown_entity_type_gate.py -q` —
  **11 passed in 1.23s**.
- `uv run ruff check src/rememberstack/workers/e3.py src/rememberstack/spine/resolver.py src/tests/workers/test_e3_unknown_entity_type_gate.py`
  — **passed**.
- Read-only review; no implementation files were modified.

## Recommendation

Keep the R3 changes: both R2 blockers are genuinely resolved, the FK alarm is
narrower, the design matches the intended all-soft/systemic split, and the mint
guard now has a direct unit test. Before merge, make the soft-failure boundary
site-aware so resolver structured-output failures cannot be silently unmetered
or partially apply a claim, and pin that boundary plus the all-soft handler
outcome with tests.
