# Adversarial implementation review: PR 311, WP-I.2 hard type cut (round 4)

**Reviewer:** Antigravity (`agy`)  
**Date:** 2026-08-26  
**PR:** `writeitai/remember-stack#311`  
**Branch:** `origin/feat/wp-i2-type-cut` vs `origin/main`  
**HEAD reviewed:** `cb3ce69b035f843ae0aea77a863ef1a834b625dd` (verified; clean)  
**Verdict:** **Approve**

The reviewer inspected the complete diff and the binding D95-D97 sources,
including `CLAUDE.md`, `decisions.md`, the accepted entity-identity design,
WP-I.2, the PostgreSQL schema design, the E0 file design, and both round-3
reviews. It treated `judge_pair` and the per-type evaluation configuration as
WP-I.3-owned and T0 candidate behavior as WP-I.5-owned.

## P0/P1 findings

None. All prior blocking findings are closed.

## Round-3 closure matrix

| Finding | Result on reviewed HEAD | Evidence |
|---|---|---|
| Four vacated public columns still described live type data | Closed | `p9_14_0035` authors always-NULL meanings for both columns in `entities_current` and `mentions_live`; `apply_view_ddl` supports `CREATE OR REPLACE VIEW`; the generated manifest contains the same descriptions. |
| Shipped docs and worked SQL still used `entity_type` and D86 behavior | Closed | Website examples, operation and primitive tables, benchmark descriptors, and `SNAPSHOT_ID_TO_LIVE_SQL` are untyped; the obsolete signature-gate prose is gone. |
| Binding PostgreSQL and E0 designs still described the typed architecture | Closed | Both accepted designs now describe the D95-D97 untyped full scope and ID-addressed entity paths. |
| `typed_absence` retained an ignored type input | Closed | The form is now `predicate_absence`; only `predicate` is accepted and bound. |
| Dead D18/D86 surfaces survived | Closed | P1 type fields and search arguments, pack/core signatures, exported exception and retry suffix, graph type hydration, and the skipped D86 test module are removed; type scope interests now fail explicitly. |
| Migration/view ordering and downgrade symmetry | Closed | Dependent views are replaced before authority columns are dropped; downgrade restores the old index, FK, table comment, and view contracts. |
| Stale Compose migration-head assertion | Closed | `.github/workflows/ci.yml` now asserts `p9_14_0035`; Compose quickstart passed on the reviewed SHA. |

The reviewer also verified the `predicate_signatures` removal, name-only
`EntityRef` and resolver surfaces, untyped P1/P2/P3 and `GraphNode`, absence of
public type filters, hard-forget behavior, unconstrained `works_for`, catalog
counts, manifest/protocol pins, and SQL insert arities.

## Commands and checks

- `uv run pyright` — 0 errors, 0 warnings.
- `uv run ruff check src benchmarks src/tests` — passed.
- `uv run ruff format --check src benchmarks src/tests` — 387 files already
  formatted.
- Targeted D96/entity-resolution suite — 85 passed, 19 skipped.
- `uv run pytest -q $(cat .github/ci/unit-paths.txt)` — 983 passed, 5 skipped,
  1 warning.
- Full local collection exercised 1,262 passing and 641 skipped tests; 19
  database-fixture setup errors were caused by the intentionally absent local
  `REMEMBERSTACK_DATABASE_URL`. The database lanes were green in GitHub Actions
  on the reviewed SHA.

## Verdict

**Approve.** PR 311 satisfies the WP-I.2 acceptance contract and is ready to
merge.
