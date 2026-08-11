# Round-8 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Codex (gpt-5.6-sol)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `2db1acc0`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior review:** `design/reviews/REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r7_2026-08-11.md`

## Summary

The r7 blocker is fixed. Readiness now reaches claims through the D56
`chunk_claims` occurrence map (`src/rememberstack/spine/readiness.py:308-325`),
and connector-cycle waiting does the same while preserving its intentional
presence-only wait for legacy serial rows
(`src/rememberstack/spine/lifecycle.py:1052-1069`). I re-traced all six binding
expected-set consumers; fan-out, both barrier counts, handler membership,
`claims_for_chunks`, readiness, and cycle waiting now agree that
`claims.chunk_id` is origin provenance rather than occurrence membership.

The previously reviewed solid path also remains intact: the D84 handoff and
full fan-out share one transaction, claim completion is serialized by the D88
advisory lock, extractor/representation/version coordinates are pinned,
observation flush order comes from claim `asserted_at`, relation and observation
supersession orient by source time, ordinary observation inserts carry
`valid_from`, per-entity apply and staging retirement are atomic, and hard
forget scrubs staging.

One separate continuous-ingest blocker remains. D56 deliberately reuses the
same immutable `claim_id` in later versions, but the work ledger permits only
one normalize row per `(claim_id, component_version)`. A later version that
fans out while that shared row is unfinished neither gets its own payload nor
registers a version barrier subscription. When the row completes, it checks
only the earlier version carried in the original payload. The later version's
barrier can therefore miss permanently and never enqueue its observation flush
or downstream chain.

This is not yet the simplest solid mergeable v1.

## r7 finding: verified closed

All binding readers of the expected claim set use `chunk_claims`:

| Consumer | Evidence |
| --- | --- |
| Fan-out | `_SELECT_CLAIMS_FOR_NORMALIZE_FANOUT` joins `claims -> chunk_claims -> chunks`, retaining deployment, version, representation, chunker, and extractor pins (`src/rememberstack/spine/work_ledger.py:1156-1169`). |
| Barrier | Expected and succeeded counts use the same occurrence join and the same pins; the ready side additionally pins claim target, stage, normalizer version, and `succeeded` (`src/rememberstack/spine/work_ledger.py:1173-1207`). |
| Handler | The claim row is deployment/doc/extractor checked, then membership is validated with `claim_occurs_on_chunks`, whose SQL reads `chunk_claims` (`src/rememberstack/workers/e3.py:169-202`, `src/rememberstack/spine/claim_catalog.py:155-168,361-368`). |
| `claims_for_chunks` | The catalog selector joins through `chunk_claims`, so post-barrier relation selection sees reused occurrences (`src/rememberstack/spine/claim_catalog.py:125-139,341-349`, `src/rememberstack/workers/e3.py:773-784`). |
| Readiness | The derived claim population joins current-representation chunks to `chunk_claims`, then pins the extractor and normalizer generations (`src/rememberstack/spine/readiness.py:285-334`). |
| Cycle wait | Current-version chunks join through `chunk_claims`; only existing non-succeeded claim work blocks, preserving the legacy no-row rule (`src/rememberstack/spine/lifecycle.py:1052-1069`). |

The new assertions at
`src/tests/workers/test_e3_claim_normalize_fanout.py:64-70,210-221` encode the
two corrected joins. They are SQL-text tripwires rather than behavioral D56
regressions; that is test quality, not a defect in the two production fixes.

## Blocking finding

### B1 — One reused claim work row can serve only one version's barrier completion

D56 reattaches a prior claim ID to a later version's chunk without minting a
new claim (`src/rememberstack/spine/claim_catalog.py:79-106,269-278`). D88 now
correctly enumerates that occurrence, but creates work with
`target_kind=claim`, `target_id=claim_id`; the version and representation live
only in the payload (`src/rememberstack/spine/work_ledger.py:686-708`).

The ledger's idempotency key excludes the version and representation:
`ON CONFLICT (deployment_id, target_kind, target_id, stage,
component_version) DO NOTHING`
(`src/rememberstack/spine/work_ledger.py:906-919`). On conflict,
`enqueue_on` returns the existing processing row and does not update or merge
its payload (`src/rememberstack/spine/work_ledger.py:853-887,922-931`). Thus a
claim shared by V1 and V2 has one work row carrying whichever version payload
was inserted first.

The missed-fire sequence is:

1. V1 fans out claim C. Its normalize row is pending or running with V1's
   coordinates.
2. Before C succeeds, V2 reuses C through `chunk_claims` and finishes extract.
   V2 fan-out finds C, but the ledger conflict retains the V1 row. V2's one
   immediate barrier evaluation sees C as unfinished and does not enqueue
   observation flush (`src/rememberstack/spine/work_ledger.py:709-741`).
3. C runs with the retained V1 payload. The handler constructs exactly one
   `ClaimNormalizeBarrier` from those payload coordinates
   (`src/rememberstack/workers/e3.py:151-163,251-262`).
4. Completion checks and, if ready, opens only that barrier's version
   (`src/rememberstack/spine/work_ledger.py:331-377`). The only other call to
   `_normalize_claim_barrier_ready` is the already-passed fan-out-time check
   (`src/rememberstack/spine/work_ledger.py:710,744-779`), so V2 is never
   reconsidered.

An all-reused V2 stalls deterministically under that ordering. A mixed V2 also
stalls when its fresh claim rows finish before the shared row. Once C becomes
`succeeded`, the cycle wait no longer blocks on it, yet V2 has no observation
flush or downstream rows to wait for, so absence-based cycle finalization can
advance without V2's barrier chain. This contradicts the binding continuous
ingest rule that a new lineage version has its own set and barrier
(`plan/designs/e3_claim_level_normalize_fanout_design.md:68-80`) and the
acceptance requirement that two versions have independent barriers
(`plan/designs/e3_claim_level_normalize_fanout_design.md:293-310`).

Make barrier participation version-scoped. Viable shapes include distinct
version/claim work identities or a durable per-version subscription/manifest
whose barriers are all re-evaluated when shared claim work succeeds. The fix
must also preserve the observation-staging contract for every subscribed
version. Add a PostgreSQL regression where V2 attaches C while V1's C row is
unfinished; completing C must enqueue V2's downstream exactly once. Cover the
all-reused case and the mixed case where the shared claim is last.

## Solid-path recheck

- **Lock and atomic fan-out:** chunk extraction takes the representation lock,
  marks the chunk succeeded, evaluates the extract barrier, and enqueues the
  complete claim set inside one `engine.begin()` transaction
  (`src/rememberstack/spine/work_ledger.py:248-309`). Claim completion likewise
  acquires the dedicated D88 lock before success and barrier evaluation
  (`src/rememberstack/spine/work_ledger.py:311-378,1146-1153`).
- **Pins:** fan-out and both barrier sides pin deployment, version,
  representation, chunker, and extractor; the ready side pins the normalizer
  generation (`src/rememberstack/spine/work_ledger.py:1157-1207`). Claim work
  carries those coordinates and rejects missing/mismatched extractor or
  occurrence membership (`src/rememberstack/workers/e3.py:151-202`).
- **`asserted_at`, supersession orientation, reverse arrival, and
  `valid_from`:** staging loads in `(asserted_at, claim_id)` order
  (`src/rememberstack/spine/fact_catalog.py:650-659`); relation predecessor and
  successor are oriented by source time with a stable ID tiebreak
  (`src/rememberstack/spine/supersession.py:185-201,501-516`); observation
  reverse arrival inserts the source-earlier predecessor and caps it at the
  existing successor boundary (`src/rememberstack/spine/observation_adjudication.py:443-497`);
  ordinary inserts pass `asserted_at` as `valid_from`, and equivalent evidence
  may pull an open boundary earlier without crossing a later cap
  (`src/rememberstack/spine/observation_adjudication.py:197-305,673-718,920-941`).
- **Atomic staging retirement:** D43 apply and the per-entity delete use one
  transaction (`src/rememberstack/spine/observation_adjudication.py:121-182`),
  and the flush handler passes exact version/entity/generation coordinates
  (`src/rememberstack/workers/e3.py:740-772`).
- **Forget scrub:** hard forget deletes staging by deployment and document and
  audits its absence (`src/rememberstack/spine/forget.py:1249-1253,1555-1560`).
- **Race-test scope:** the two-connection last-claim race remains explicitly
  deferred for v1; correctness is intended to rest on the advisory-lock pattern
  (`plan/designs/e3_claim_level_normalize_fanout_design.md:177-184`). The new
  blocker is not that deferred two-last-claims race; it is a distinct shared-row,
  cross-version missed notification.

## Non-blocking notes

- The occurrence queries select join rows rather than `DISTINCT claim_id`
  (`src/rememberstack/spine/work_ledger.py:1157-1207`,
  `src/rememberstack/spine/claim_catalog.py:341-349`). A claim attached to two
  chunks of one version causes redundant enqueue attempts and duplicate catalog
  objects. Ledger idempotency and symmetric barrier counts preserve the D88
  result, but `DISTINCT` would match the expected-set contract and avoid legacy
  serial duplicate normalization.
- The atomic-retire, reverse-arrival, readiness, and cycle tests mainly inspect
  source/SQL strings (`src/tests/workers/test_e3_claim_normalize_fanout.py:64-70,161-221`).
  Behavioral PostgreSQL tests for reused-origin readiness/cycle blocking,
  stored reverse-order windows, and apply/delete rollback would materially
  improve regression strength.
- The source-earliest pull guard is entity-wide: any other capped observation
  on the entity can refuse the pull even if it is unrelated
  (`src/rememberstack/spine/observation_adjudication.py:689-709,929-941`). That
  fails safely by keeping a narrower window rather than creating overlapping
  current slices, but can leave `valid_from` completion-order dependent. A
  future refinement should scope the guard to the actual supersession
  neighbour and add a stored-window regression.
- Current CI hygiene still has test-quality/style-only failures. `ruff format
  --check src/ benchmarks/` would reformat
  `src/rememberstack/spine/supersession.py` and
  `src/tests/workers/test_e3_claim_normalize_fanout.py`; `pyright` reports three
  errors from the inferred `dict[str, str]` payload at
  `src/tests/workers/test_e3_claim_normalize_fanout.py:278-298`. These do not
  change the production verdict, but the branch will not pass the configured
  format/type gates (`.github/workflows/ci.yml:59-64`).

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **31 passed in 4.16s**.

Additional review checks:

```text
uv run ruff format --check src/ benchmarks/
```

Result: **failed** — 2 files would be reformatted.

```text
uv run pyright src/ benchmarks/ --pythonversion 3.13
```

Result: **failed** — 3 errors, all in the D88 test payload fixture.

## Mergeability

**No.** Commit `2db1acc0` correctly closes the r7 occurrence-map gap in
readiness and connector-cycle waiting, and all requested expected-set consumers
now use `chunk_claims`. The main lock/atomicity/pin/source-time/forget path is
otherwise a credible v1. However, a reused unfinished claim still has one
payload-bound work row for multiple version barriers, so a later version can
permanently miss its downstream handoff. That continuous-ingest correctness
hole must close before this is the simplest solid mergeable v1.
