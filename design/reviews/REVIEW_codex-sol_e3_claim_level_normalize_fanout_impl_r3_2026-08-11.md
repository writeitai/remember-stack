# Round-3 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `33082f1c`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`

## Summary

The latest delta resolves the two immediate round-2 regressions it targets:
claim retries no longer treat partial evidence as a completion marker
(`src/rememberstack/workers/e3.py:177-223`), connector-cycle finalization now
waits on claim-grain normalize rows including missing and dead-lettered rows
(`src/rememberstack/spine/lifecycle.py:1052-1069`), the observation-staging key
includes `normalizer_version`
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:31-34`),
and the zero-chunk worker assertion matches the observation-flush chain
(`src/tests/workers/test_chunk_level_extract.py:121-150`). The requested suite
passes with 34 tests.

The core D88 mechanics also read correctly: the complete fan-out is inserted
inside the extract-completion transaction
(`src/rememberstack/spine/work_ledger.py:268-308`), the dedicated D88 advisory
lock is held from before claim completion through the succeeded-only anti-join
and downstream enqueue (`src/rememberstack/spine/work_ledger.py:330-374,1118-1163`),
and claim jobs stage observations for a post-barrier `(asserted_at, claim_id)`
flush (`src/rememberstack/workers/e3.py:214-235`,
`src/rememberstack/spine/fact_catalog.py:630-639`). Deferring the dedicated
two-connection race test is consistent with the revised binding design and is
not a finding.

This is nevertheless not mergeable as a solid v1. Three correctness contracts
remain open: the barrier does not retain the atomic extract-generation claim
set, temporal adjudication is still oriented by completion order across
versions, and hard forget does not erase the new plaintext staging surface.

## Blocking findings

### B1 — The normalize barrier re-queries a mutable, unversioned claim set

The extract barrier carries `extractor_version`
(`src/rememberstack/workers/base.py:49-56`), but `complete_chunk_extract` drops
it when invoking the normalize fan-out
(`src/rememberstack/spine/work_ledger.py:287-306`). Fan-out, expected-count, and
ready-count SQL then select every direct claim under only
`(representation_id, chunker_version)`; none filters `claims.extractor_version`
or persists the exact set materialized by the handoff
(`src/rememberstack/spine/work_ledger.py:1128-1163`). Readiness independently
repeats the same all-generation join
(`src/rememberstack/spine/readiness.py:303-316`).

The child rows are atomically inserted, but that atomic child set is not what
later barrier checks count. A re-extraction of the same representation can add
claims after the old fan-out. The old barrier then expands to those claims; if
they belong to a newer normalizer generation, the old version can wait forever,
and if they share the old normalizer generation they can be folded into a
handoff that was supposed to be closed. This violates the binding fixed-set
contract in design sections 5.1-5.2 and makes cutover/re-extraction behavior
timing-dependent.

Carry the extract generation through fan-out, barrier, readiness, and payload
coordinates, or persist and query the atomic manifest that was actually
materialized.

### B2 — Relation supersession still assigns predecessor/successor by processing order

The new SQL correctly prefers each relation's latest supporting claim by
`asserted_at` (`src/rememberstack/spine/supersession.py:388-417`), but the
adjudicator still unconditionally treats the relation whose work is executing
as `new` and every blocked live relation as `old`
(`src/rememberstack/spine/supersession.py:163-193`). A supersede verdict then
closes `old` at `new["asserted_at"]` without comparing or swapping their source
times (`src/rememberstack/spine/supersession.py:248-282`). The version selector
also feeds relation IDs in UUID order
(`src/rememberstack/spine/fact_catalog.py:651-658`).

Consequently, a source-older version that completes second can still be treated
as the successor and close a source-newer relation at the older assertion time.
Changing evidence selection order does not satisfy the binding direction rule
that late older testimony must not win because it normalized later. Orient the
pair from supporting-claim source time before applying the verdict, and cover
reversed version completion.

### B3 — Observation ordering is deterministic within one flush, not across versions

One version's staging rows are loaded in the required order
(`src/rememberstack/spine/fact_catalog.py:630-639`) and applied as a batch
(`src/rememberstack/workers/e3.py:713-737`). Independent version flush jobs can
still acquire the same entity lock in either order. D43's candidate load does
not carry the candidate's source assertion time
(`src/rememberstack/spine/observation_adjudication.py:723-732`); on a supersede
outcome it always caps the existing observation at the incoming assertion time
and inserts the incoming assertion as successor
(`src/rememberstack/spine/observation_adjudication.py:407-448,747-754`).

Thus a 2019 version flushing after a 2024 version can cap the 2024 observation
at 2019. The entity lock prevents concurrent writes but does not provide the
design's completion-order independence for continuous ingestion. Make the D43
application source-time-aware (or deterministically recompute the entity's
ordered assertions) and cover both version completion orders.

### B4 — Hard forget leaves staged observation plaintext behind

`normalize_observation_staging` stores raw `statement` text plus claim/document
coordinates and has no cascading foreign keys
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:22-35`).
Hard forget deletes fact evidence, claims, and chunks but never deletes staging
rows (`src/rememberstack/spine/forget.py:1235-1259,1341-1364`), and its
post-scrub proof omits the staging table
(`src/rememberstack/spine/forget.py:1485-1557`).

If forget runs while observation flush is pending or dead-lettered, it can
report success while retaining source-derived plaintext indefinitely. After the
claim is deleted, the staging load's inner join makes the row invisible to the
normal cleanup path (`src/rememberstack/spine/fact_catalog.py:630-639`). Add an
explicit deployment/document-scoped staging delete before claim deletion and
include the table in the verification and residual-token proofs.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_e3_unknown_entity_type_gate.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **34 passed in 3.01s**.

Supplementary worker-suite check: **105 passed, 90 skipped in 5.21s**.
