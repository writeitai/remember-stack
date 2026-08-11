# Round-2 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES
**Reviewer:** Codex (gpt-5.6-sol)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `b49f7f5f`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`

## Summary

The round-2 delta correctly fixes the catalog constraint count and table
comment, routes both zero-chunk edges through `adjudicate_observations`, removes
the unstable readiness `now()`, prevents a fan-out-generation version row from
falling into the legacy serial loop, makes `OBS_FLUSH_VERSION` single-source,
and retains the transaction-scoped normalize barrier lock
(`src/rememberstack/spine/catalog_contract.py:330`,
`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:37-42`,
`src/rememberstack/workers/e1.py:628-650`,
`src/rememberstack/workers/e2.py:1063-1084`,
`src/rememberstack/spine/readiness.py:287-302`,
`src/rememberstack/workers/e3.py:133-145`,
`src/rememberstack/spine/work_ledger.py:330-374,632-635`). The requested test
command passes with 31 tests.

The dedicated two-connection race test can be deferred under the now-binding
§5.4 decision. The lock implementation itself is the correct D84-shaped
solution: it is acquired before `_COMPLETE`, remains held through the
`succeeded`-only anti-join, and the downstream enqueue is in the same
transaction (`src/rememberstack/spine/work_ledger.py:330-374,1118-1163`).

The branch is nevertheless not safe to merge for v1. Connector finalization
still ignores claim work; crash/retry can silently accept a partially written
claim; both observation and relation adjudication still orient predecessor and
successor by processing order across versions; the expected set is neither
extract-generation-pinned nor fully coordinate-bound; and hard forget does not
erase the new plaintext staging table. The broader worker suite is also red.

## Blocking findings

### B1 — Connector cycles can finalize while claim normalize is pending or dead-lettered

`_SELECT_READY_CYCLES` checks nonterminal document-version rows and chunk-level
extract rows, but has no claim-level normalize prerequisite
(`src/rememberstack/spine/lifecycle.py:1024-1051`). Before the last claim
succeeds, the version-level observation-flush row does not exist; it is created
only by `complete_claim_normalize` after the claim anti-join passes
(`src/rememberstack/spine/work_ledger.py:345-371`). Therefore, as soon as chunk
extract rows are successful, a connector cycle can appear ready while claim
rows are pending, running, failed, missing, or `dead_letter`.

That violates D88 §5.7 and can run absence-driven lifecycle finalization before
the cycle's new testimony has normalized. Add a version/representation-scoped
expected-claim anti-join at `E3_NORMALIZER_VERSION`; every expected row must be
`succeeded`, and missing/DLQ rows must block.

### B2 — A retry can turn a partial claim write into successful normalization

The replay shortcut treats the presence of any relation or observation evidence
as “claim normalized” (`src/rememberstack/workers/e3.py:177-193`,
`src/rememberstack/spine/entity_registry.py:128-136,199-204`). That is not a
completion marker. Relation writes each commit independently, and observation
staging commits separately afterward
(`src/rememberstack/spine/fact_catalog.py:39-105,401-425`,
`src/rememberstack/workers/e3.py:229-250`).

A transient failure after the first relation commit, during a later resolver
call, or during observation staging leaves evidence behind. The retry sees that
single evidence row, skips the model and all remaining outputs, and marks the
claim `succeeded`, allowing the barrier to open with missing facts. Use a durable
current-generation claim-complete marker, or rerun the idempotent output path;
an arbitrary evidence row cannot prove completion. Cover failures after a
relation write and between staged observations.

The staging idempotency key has the same generation hole: its primary key and
conflict target omit `normalizer_version`, although reads filter by it
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:22-33`,
`src/rememberstack/spine/fact_catalog.py:614-637`). A retained older-generation
row can suppress the new-generation row and then remain invisible to the new
flush.

### B3 — Relation supersession still uses completion order as temporal direction

The round-2 query now chooses each relation's representative support by latest
`asserted_at`, which is an improvement
(`src/rememberstack/spine/supersession.py:388-417`). It does not, however,
orient the pair.

The origin selector still returns relations in UUID order
(`src/rememberstack/spine/fact_catalog.py:650-658`), and the handler adjudicates
them in that order (`src/rememberstack/workers/e3.py:828-871`). Inside the
adjudicator, the target relation is unconditionally called `new` and the live
blocked relation `old` (`src/rememberstack/spine/supersession.py:163-193`). A
`supersede` verdict then closes `old` at `new["asserted_at"]` without comparing
or swapping their source times (`src/rememberstack/spine/supersession.py:248-282`).
Ordering candidate rows by `asserted_at` at
`src/rememberstack/spine/supersession.py:451` cannot change those roles.

Consequently, an older source version that finishes second can still close the
newer relation at the older assertion time. Even within one version, processing
the newer relation first and the older relation second can reverse an earlier
correct closure. Derive predecessor/successor from source time before applying
the verdict, and prove both reversed relation-ID iteration and reversed version
completion converge to the same windows.

### B4 — The post-barrier observation flush is ordered only within one version

The staging query correctly orders one version's rows by `(asserted_at,
claim_id)` (`src/rememberstack/spine/fact_catalog.py:629-637`), and the flush
applies that version's batches under the entity lock
(`src/rememberstack/workers/e3.py:728-752`). Separate version flushes can still
acquire that lock in either order.

D43 loads candidate statement/open state but not the candidate's source time
(`src/rememberstack/spine/observation_adjudication.py:723-732`). On a supersede
outcome it always caps the existing candidate at the incoming claim's
`asserted_at` and inserts the incoming assertion as the successor
(`src/rememberstack/spine/observation_adjudication.py:407-448,747-754`). Thus a
2019 version flushing after a 2024 version can cap the 2024 observation at 2019.
The entity lock prevents concurrent writes; it does not make the result
completion-order independent. D88's continuous-ingest guarantee needs
source-time-aware insertion/recomputation across version flushes, plus a
reverse-version-order test.

### B5 — The expected claim set is dynamic and not fully lineage/deployment bound

The extract barrier knows `extractor_version`, but drops it when invoking the
claim fan-out (`src/rememberstack/spine/work_ledger.py:287-306`). Fan-out and
both normalize barrier counts then select all claims under only
`(representation_id, chunker_version)`—no extractor generation, deployment,
version, or document predicate
(`src/rememberstack/spine/work_ledger.py:618-646,726-751,1128-1163`). A later
extractor generation on the same representation can therefore enlarge an
already-running old barrier or mix old and new claim generations. During a
normalizer cutover, the old barrier can wait forever on claims materialized only
at the new normalizer version.

The handler also loads the claim globally by `claim_id`, validates only
`doc_id` plus membership in a representation's chunk set, and never validates
the payload `version_id`, payload `claim_id`, or representation/deployment
lineage (`src/rememberstack/workers/e3.py:151-175`,
`src/rememberstack/spine/claim_catalog.py:141-153,333-338`,
`src/rememberstack/spine/chunk_catalog.py:255-269`). Because evidence FKs are
logical, a cross-deployment payload can write foreign claim/document IDs under
`work.deployment_id`.

Carry the extractor generation into the fan-out/barrier contract and select the
fixed expected set through deployment → version → representation → chunk →
claim. Validate the claim target and every payload coordinate in that same
scope before any model, resolver, or fact write.

### B6 — Hard forget strands staged source plaintext

`normalize_observation_staging` stores raw `statement` text with claim and
document coordinates and has no cascading FK
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:22-34`).
Hard forget deletes evidence and claims but never deletes staging rows
(`src/rememberstack/spine/forget.py:1235-1259,1341-1358`), and the post-scrub
proof enumerates claims/evidence/observations without this table
(`src/rememberstack/spine/forget.py:1485-1557`).

If forget races a pending observation flush, it can report success while the
source statement remains. The later flush joins staging to the now-deleted
claim (`src/rememberstack/spine/fact_catalog.py:629-637`), so the row becomes
permanently invisible to normal cleanup. Delete staging rows by deployment and
doc/claim/version before claim deletion, include them in the verification
query, and cover pending-flush forget.

## Merge hygiene

The exact requested command passes:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_e3_unknown_entity_type_gate.py \
  src/tests/profiles/test_selfhost_profile.py -q

31 passed in 3.52s
```

The complete worker suite is red:

```text
uv run pytest src/tests/workers -q --tb=short

1 failed, 104 passed, 90 skipped in 4.14s
```

`test_extract_follow_up_zero_chunks_enqueues_normalize` still requires a
`normalize_relations` row (`src/tests/workers/test_chunk_level_extract.py:121-145`),
while the corrected D88 behavior now emits `adjudicate_observations`
(`src/rememberstack/workers/e1.py:628-650`). This test is included in the worker
CI set (`.github/ci/unit-paths.txt:59-64`; `.github/workflows/ci.yml:163-164`),
so the intended zero-chunk change needs its existing assertion updated before
merge.

## What is acceptable as-is

- The advisory-lock implementation is sufficient for the last-claim race; the
  dedicated two-connection PostgreSQL test may remain deferred under revised
  design §5.4.
- Atomicity of fan-out and extract handoff is preserved because every enqueue
  occurs inside `complete_chunk_extract`'s transaction
  (`src/rememberstack/spine/work_ledger.py:268-308,636-723`).
- The catalog count/comment, zero-chunk observation-flush route, stable derived
  readiness timestamp, coordinator serial-path rejection, and single-source
  observation-flush version resolve the corresponding round-1 findings.

