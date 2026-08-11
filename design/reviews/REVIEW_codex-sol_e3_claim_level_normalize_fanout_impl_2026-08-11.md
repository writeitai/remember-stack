# Implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES  
**Reviewer:** Codex (gpt-5.6-sol)  
**Date:** 2026-08-11  
**Branch:** `feat/e3-claim-level-normalize-fanout`  
**Commit:** `ce89b4c5`

## Summary

The implementation has the right broad topology: claim-targeted normalize work,
an atomic extract-handoff transaction, a representation advisory lock around
claim completion and barrier evaluation, a post-barrier observation stage, an
origin-claim relation selector, claim-derived readiness, and self-host/compose
wiring. The D86 generate-only soft boundary is still exercised through the
existing single-claim normalization function
(`src/rememberstack/spine/work_ledger.py:268-308,310-374`,
`src/rememberstack/workers/e3.py:133-244,503-586`,
`src/rememberstack/profiles/selfhost.py:715-764`).

The branch is not safe to merge yet. Connector-cycle finalization can run while
claim normalization is pending or dead-lettered; relation and cross-version
observation adjudication are still arrival-order dependent; the expected claim
set is not pinned to the extract generation; payload lineage is not validated
against deployment/version; a retry can mistake a partially written claim for a
fully normalized claim; and the new plaintext staging table is outside the hard
forget contract. These violate binding D88 correctness, continuous-ingest, and
tenancy requirements.

## Findings

### Blocker — connector cycles do not wait for D88 claim work, including DLQ

`_SELECT_READY_CYCLES` blocks on nonterminal document-version rows and on chunk
`extract_claims` rows, but it never joins expected claims to claim-targeted
`normalize_relations` rows. Once all extract chunks succeed, D88 has no pending
version-level normalize coordinator, so a sync cycle can finalize while claim
jobs are pending/running/failed; a claim DLQ also becomes invisible forever
(`src/rememberstack/spine/lifecycle.py:1024-1051`,
`src/rememberstack/spine/work_ledger.py:294-307,668-691`). This breaks D55's
support-swap/retraction barrier, not just status reporting.

Add a deployment/version/representation-scoped expected-claim anti-join to
cycle readiness. Missing, pending, running, failed, and `dead_letter` claim rows
at the D88 normalizer generation must all block. Cover claim DLQ and missing-child
states with PostgreSQL integration tests.

### Blocker — claim coordinates are not deployment/version safe

The claim handler loads by globally addressed `claim_id`, verifies only `doc_id`,
and separately checks that the origin chunk appears under the supplied
representation. It never validates that the representation belongs to
`work.deployment_id`, that it belongs to `version_id`, or that all four lineage
coordinates agree. It also ignores the payload's `claim_id`
(`src/rememberstack/workers/e3.py:140-169`,
`src/rememberstack/spine/claim_catalog.py:141-153,333-338`). The subsequent
relation/staging writes use `work.deployment_id` with claim/doc identifiers from
the loaded row (`src/rememberstack/workers/e3.py:222-231,458-466`). Because fact
evidence identifiers are logical rather than referential FKs, a corrupted or
cross-tenant work payload can attach tenant A testimony to tenant B facts
(`src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py:235-246`).

Validate the target and payload in one deployment-scoped
claim -> origin chunk -> representation -> version -> document join before any
model, resolver, or fact write. Add wrong-version, wrong-document,
wrong-representation, wrong-target-claim, and cross-deployment negative tests.

### Blocker — supersession remains ordered by processing/ingestion, not `asserted_at`

The post-barrier selector correctly finds relations evidenced by origin claims
at the named normalizer generation
(`src/rememberstack/spine/fact_catalog.py:459-478,650-658`), but the adjudicator
still treats whichever relation ID the worker visits as `NEW` and the blocked row
as `EXISTING` (`src/rememberstack/workers/e3.py:753-776,858-864`,
`src/rememberstack/spine/supersession.py:163-193`). It chooses each relation's
representative evidence with `ORDER BY c.ingested_at DESC`, orders candidates by
relation ingestion, and closes the presumed old row at the presumed new row's
assertion time (`src/rememberstack/spine/supersession.py:248-282,388-395,411-417,451-462`).

Thus, if a source-older version finishes after a source-newer version, the older
fact can be presented as the successor and can close the newer fact. The block
lock serializes writes but does not orient time. Bind predecessor/successor from
supporting claims' `asserted_at` and make relation-ID iteration and reverse
version-completion tests converge to the same windows and adjudications.

### Blocker — the expected claim set is not tied to the extract generation

`ExtractChunkBarrier` carries `extractor_version`, but the handoff drops it before
claim enumeration (`src/rememberstack/workers/base.py:44-56`,
`src/rememberstack/spine/work_ledger.py:287-306`). Fan-out and both barrier counts
select every origin claim under `(representation_id, chunker_version)` without
filtering `claims.extractor_version`
(`src/rememberstack/spine/work_ledger.py:1126-1161`). Readiness repeats the same
unversioned claim join (`src/rememberstack/spine/readiness.py:280-316`). A
re-extraction on the same representation can therefore enqueue stale claims from
prior extractor generations or enlarge an already materialized barrier with
later claims, violating the fixed set captured when the extract barrier fired.

Carry `extractor_version` into every claim-set query and payload/barrier contract,
and scope the set through deployment, representation, version, and document.
Test same-representation re-extraction, old-generation exclusion, delta-only
reuse, and two independently normalizing versions.

### Major — a retry can convert a partial claim write into success

Claim normalization writes relations in independent transactions, then stages
observations in later independent transactions
(`src/rememberstack/spine/fact_catalog.py:39-105`,
`src/rememberstack/workers/e3.py:222-231,458-500`). On retry, however, the handler
skips the whole claim as soon as *any* relation or observation evidence exists,
without checking the current normalizer generation or whether every output was
written (`src/rememberstack/workers/e3.py:170-186`,
`src/rememberstack/spine/entity_registry.py:128-136,199-204`). A crash after the
first relation commit but before observation staging therefore causes the retry
to return the barrier immediately; the missing observations are never staged and
the claim is marked succeeded.

Use a durable current-generation claim-complete marker or safely rerun the
idempotent single-claim output path; an arbitrary evidence row is not a completion
marker. Include crash/retry tests after relation write and between staged
observations. Also include `normalizer_version` in the staging uniqueness key:
the current primary key/conflict target can suppress a new generation's row while
the load filters that row out by version
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:22-34`,
`src/rememberstack/spine/fact_catalog.py:614-637`).

### Major — observation order is fixed only within one version

Staged rows are sorted correctly for one version, and grouping preserves that
per-entity order (`src/rememberstack/spine/fact_catalog.py:629-638`,
`src/rememberstack/workers/e3.py:721-740`). Separate versions nevertheless flush
independently. The D43 adjudicator interprets each arriving assertion as the new
state and caps the open candidate at that assertion's `asserted_at`; it does not
compare the incoming assertion time with the candidate's source time
(`src/rememberstack/spine/observation_adjudication.py:121-174,305-327,407-447,723-754`).
An older version whose flush acquires the entity lock second can still cap a
newer observation. This violates D88's continuous-ingest rule that observation
correctness not depend on completion order.

Orient out-of-order inserts by source time (or perform an equivalent deterministic
recomputation) and test two versions of the same entity in both flush orders.

### Blocker — hard forget leaves staged plaintext behind

The migration introduces a table containing raw observation `statement` text and
claim/document coordinates (`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:22-34`), but hard forget neither deletes from
`normalize_observation_staging` before deleting claims nor checks that table in
its post-scrub proof (`src/rememberstack/spine/forget.py:1235-1259,1341-1358,1485-1557`).
There are no FKs or cascades to rescue this omission. A forget that lands while
an observation flush is pending can report success while retaining the forgotten
source's statement indefinitely.

Delete staging rows by deployment plus doc/claim/version coordinates, add them to
the verification query and residual-token test, and cover pending-flush forget.

### Major — the acceptance suite does not exercise the database contracts

The new test module explicitly uses no PostgreSQL and covers only version-string
shape plus one successful claim-handler path
(`src/tests/workers/test_e3_claim_normalize_fanout.py:1-157`). It does not test
atomic full-set fan-out, the two-connection last-claim race, a claim DLQ,
zero-claim flow, ordered observation flush, reverse-version supersession,
readiness precedence, connector-cycle blocking, cross-tenant payload rejection,
or BEAM-scale query plans. The fan-out itself is also 15k-style Python iteration
through `enqueue_on`, not the binding set-based insert, and no plan evidence is
present (`src/rememberstack/spine/work_ledger.py:634-691`).

Add the binding design's PostgreSQL acceptance matrix before merge. In
particular, force the last-claim interleaving with two real connections and prove
exactly one observation-flush row; exercise all readiness/DLQ states; and capture
`EXPLAIN` evidence for the expected-set and barrier queries at representative
scale.

### Minor — fan-out coordinator target handling is not version-aware

Every non-claim normalize row enters the legacy serial whole-version handler,
regardless of `work.component_version` (`src/rememberstack/workers/e3.py:133-138,246-307`).
A document-version row at the D88 fan-out generation would therefore normalize
serially and enqueue supersession/embed directly instead of acting as fan-out-only
coordinator. Distinguish pre-fan-out legacy component versions from any fan-out
generation coordinator, and add a coordinator-success-does-not-open-readiness
test.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py src/tests/workers/test_e3_unknown_entity_type_gate.py src/tests/profiles/test_selfhost_profile.py -q
```

Result: **29 passed in 4.13s**.

The passing result confirms the unit wiring and retained D86 helper behavior. It
does not cover the PostgreSQL race, generation, lifecycle, tenancy, temporal, or
forget findings above.
