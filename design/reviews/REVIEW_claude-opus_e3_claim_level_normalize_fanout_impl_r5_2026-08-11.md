# Round-5 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Claude (opus-5)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `3af2bb1b`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior round:** `REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r4_2026-08-11.md`

## Summary

The three r4 blockers are genuinely closed in production code. Two new defects
found this round are not in the r4 scope and were not raised before: the
post-barrier observation flush is not retry-idempotent, and the new
connector-cycle claim wait has no generation scope, so it stalls a cycle
forever on any claim that will never receive a claim-grain row (the legacy
drain the design itself plans for). Both are D88-introduced, both are in the
mainline v1 path, and neither is covered by a test. Additionally, the
cross-tenant/version coordinate validation that r4 blocked on — the security
check itself — landed with zero test coverage of any rejection branch.

### r4 blockers: verified closed

**r4 B1 — ordinary observation inserts lose source time. Closed.** All nine
`_insert_new` call sites now pass `valid_from=asserted_at`
(`src/rememberstack/spine/observation_adjudication.py:216,245,274,370,398,429,488,525,558`),
and every `_remember_candidate` call carries it too, so an in-batch successor
sees the predecessor's source time rather than `None`
(`src/rememberstack/spine/observation_adjudication.py:224-229,253-258,282-287,381-386,409-414,463-469,508-513,542-548,566-571`).
The reverse-arrival branch's candidate read
(`src/rememberstack/spine/observation_adjudication.py:420-421`) therefore sees
a real timestamp on rows written by the first-mention, no-open-candidate, and
clear-novelty paths. The specific r4 failure — a 2024 observation stored with
`valid_from = NULL`, then a 2019 assertion capping it at 2019 — can no longer
arise from a D88-era write. Pre-existing rows written before this commit still
carry `NULL` and will still take the forward branch; that is a backfill
question, not a code defect, but it should be stated in the cutover runbook.

**r4 B2a — fan-out and barrier not deployment/version bound. Closed.** All
three claim-set statements now bind the deployment on both sides of the join
and the version on the chunk
(`src/rememberstack/spine/work_ledger.py:1156-1204`), and every caller supplies
them (`src/rememberstack/spine/work_ledger.py:346-354,647-655,710-718,744-779`).

**r4 B2b — handler coordinates incomplete. Closed.** The handler now rejects a
payload `claim_id` that disagrees with `target_id`
(`src/rememberstack/workers/e3.py:164-168`), rejects a claim whose
`deployment_id`, `doc_id`, `claim_id`, or `extractor_version` disagrees with
the work row (`src/rememberstack/workers/e3.py:174-183`), and requires the
claim's chunk to be present in the supplied representation **at the payload
version** (`src/rememberstack/workers/e3.py:184-197`). That chain is
sufficient: `claims.chunk_id` identifies exactly one chunk row, so proving that
chunk is in `representation_id` and carries `version_id` binds representation
and version to a claim already proven to belong to `work.deployment_id`. The
cross-tenant payload lie the design requires rejecting
(`plan/designs/e3_claim_level_normalize_fanout_design.md:131-133,259-264`) is
now actually rejected.

Also re-verified intact from earlier rounds: the dedicated barrier advisory
lock (`src/rememberstack/spine/work_ledger.py:332-335,1146-1154`), atomic
full-set fan-out inside the extract barrier transaction
(`src/rememberstack/spine/work_ledger.py:294-308`), no partial-skip on claim
retry (`src/rememberstack/workers/e3.py:198-199`), source-time supersession
orientation (`src/rememberstack/spine/supersession.py:185-201`), hard-forget
staging scrub with erasure-proof coverage
(`src/rememberstack/spine/forget.py:1249-1254,1558-1560`,
`src/rememberstack/spine/catalog_contract.py:144`), and the legitimately
deferred two-connection race test
(`plan/designs/e3_claim_level_normalize_fanout_design.md:181-184`).

## Blocking findings

### B1 — The post-barrier observation flush is not retry-idempotent

`AdjudicateObservationsHandler.handle` loads the staged set, applies it with
one `add_observations` transaction **per entity**, and only then deletes the
staging rows in a **separate** transaction
(`src/rememberstack/workers/e3.py:735-759`,
`src/rememberstack/spine/fact_catalog.py:445-457`). Any exception between the
first entity's commit and the delete — a provider blip inside the D43 ladder is
the obvious one, and this stage runs an LLM ladder for every entity of a
document version — is a retryable failure
(`src/rememberstack/workers/base.py:255-262`), so the work retries with the
full staging set still present and re-applies every entity that already
committed.

Re-application is not a no-op whenever the first pass produced a supersede.
Concretely: entity E, staged assertions A (`asserted_at` 2019) then B (2024).
First pass inserts A open, then B supersedes A — A is capped at 2024
(`src/rememberstack/spine/observation_adjudication.py:471-514`). Crash before
`clear_staged_observations`. On retry, A no longer matches the exact-open
short-circuit because it is capped
(`src/rememberstack/spine/observation_adjudication.py:189-207`), so it ranks
against open B, the ladder returns supersede again, and the reverse-arrival
branch inserts **a second A row** as a source-earlier predecessor
(`src/rememberstack/spine/observation_adjudication.py:416-470`). The evidence
`ON CONFLICT (observation_id, claim_id)` guard does not help — the duplicate
has a new `observation_id`
(`src/rememberstack/spine/observation_adjudication.py:853-864`). The result is
duplicated history plus a second full round of ladder spend for every
already-flushed entity.

This is a regression relative to the path D88 replaces: the serial handler
skipped already-normalized claims on retry
(`src/rememberstack/workers/e3.py:286-293`), so a version-normalize retry never
re-adjudicated. D88 deliberately dropped partial-skip in the *claim* handler,
which is safe there because staging is an idempotent upsert
(`src/rememberstack/spine/fact_catalog.py:614-628`); the flush stage inherited
the "always re-run" stance without the idempotence that makes it safe.

The design binds the ordered flush but says nothing about its retry semantics
(`plan/designs/e3_claim_level_normalize_fanout_design.md:211-228`), so the
contract needs stating as well as fixing. Retire each entity's staged rows in
the same transaction that applies them (the adjudicator would need to accept a
caller-owned connection, the pattern `enqueue_on` already uses), or record a
per-`(version, entity, normalizer_version)` flush marker consulted on entry.
Cover it with a test that fails the flush after the first entity and asserts
the second attempt adds no observation rows.

### B2 — The connector-cycle claim wait has no generation or lineage scope

The new D88 clause blocks cycle finalization while any claim of any version in
the cycle lacks a succeeded claim-grain normalize row — including when the row
is entirely absent (`w.processing_id IS NULL`)
(`src/rememberstack/spine/lifecycle.py:1053-1068`). It filters neither
`component_version` nor `chunker_version` nor extract generation, and it reaches
chunks of every representation of the version, not the one whose extract
barrier fired.

That makes the wait unsatisfiable for any claim that will never be fanned out:

- **Legacy drain.** The design's own cutover keeps pre-fanout version-level
  rows running the serial loop until drained
  (`plan/designs/e3_claim_level_normalize_fanout_design.md:147-152,236-239`),
  and the handler still supports that path
  (`src/rememberstack/workers/e3.py:144-145`). Those versions' claims never get
  a `target_kind=claim` row, so every not-yet-finalized cycle containing them
  stalls permanently.
- **Chunker generation change.** Fan-out pins `c.chunker_version`
  (`src/rememberstack/spine/work_ledger.py:1165`); this clause does not, so
  claims under a superseded chunk grid block forever.

A stalled cycle is silent and not self-healing: `cycles_ready_to_finalize`
gates absence-based closure and the LOSSY-cycle guard
(`src/rememberstack/spine/lifecycle.py:528-548`), so retraction of
no-longer-observed facts simply never runs for those lineages, with no error
anywhere. Recovery needs manual SQL.

Scope the clause the way the barrier scopes its expected set — the fan-out
generation and the chunk grid the barrier pinned — or require the claim row
only for versions whose normalize is expected at the fan-out generation. A test
with one legacy version (claims present, no claim-grain rows) in an unfinalized
cycle would pin the behavior.

### B3 — The coordinate validation that closed r4 B2 has no test

`grep` across `src/tests` finds no test exercising any rejection branch added
in this commit: no wrong-deployment claim, no wrong `doc_id`, no payload
`claim_id`/`target_id` disagreement, no wrong `version_id`, no wrong
representation, no extractor-generation mismatch
(`src/rememberstack/workers/e3.py:164-197`). The only claim-handler rejection
under test is the missing-`extractor_version` pin
(`src/tests/workers/test_e3_claim_normalize_fanout.py:70-77`). r4 asked
explicitly for this coverage, and the design lists "cross-tenant payload lie →
rejected" as an acceptance test
(`plan/designs/e3_claim_level_normalize_fanout_design.md:308`).

The existing harness in that file already fakes the claim catalog and chunk
catalog (`src/tests/workers/test_e3_claim_normalize_fanout.py:70-130,184-250`),
so each case is a few lines. A tenancy check nobody tests is a tenancy check
that silently rots on the next refactor.

## Non-blocking findings

### F1 — The supersession selector is not pinned to the extract generation

The flush handler builds the origin-claim set with `claims_for_chunks`, which
has no extractor pin (`src/rememberstack/workers/e3.py:761-771`,
`src/rememberstack/spine/claim_catalog.py:326-334`), even though the payload
carries `extractor_version` (`src/rememberstack/spine/work_ledger.py:373`) and
`expected_claim_ids` is defined as generation-closed
(`plan/designs/e3_claim_level_normalize_fanout_design.md:84-95,190-195`). After
a re-extract at a new generation, the selector over-includes claims of the old
generation. The `normalizer_version` filter in
`relation_ids_for_origin_claims` narrows this but does not close it, because a
re-extract does not necessarily bump the normalizer string
(`src/rememberstack/spine/fact_catalog.py:651-659`). The failure mode is
re-adjudicating more relations than the version owns, not missing any — hence
non-blocking — but it is a straight deviation from a section marked "not open"
(`plan/designs/e3_claim_level_normalize_fanout_design.md:341-342`), and the
payload already carries the value the query needs.

### F2 — Derived normalize readiness reports zero claims as `succeeded`

`_NORMALIZE_CLAIM_STATUS` maps "no claims" to `succeeded`
(`src/rememberstack/spine/readiness.py:293`) and reaches chunks through
`v.current_representation_id` (`src/rememberstack/spine/readiness.py:309-316`)
rather than the representation the barrier pinned. A version whose extract has
not produced claims yet is therefore indistinguishable from a version whose
extract legitimately produced none, and a version whose current representation
was swapped mid-flight (D65 allows several readings per version) reports on the
new reading while the barrier tracks the old one. The extract stage row keeps
the aggregate report honest in both cases, so this is a misleading cell rather
than a false ready — but the barrier and readiness should agree on what the
expected set is, and today they compute it differently.

### F3 — The verdict prompt is still arrival-oriented; only the write is corrected

`_ladder` is called with `existing = candidate`, `new = incoming` before any
source-time orientation (`src/rememberstack/spine/observation_adjudication.py:327-333`),
and orientation is applied afterwards to the write only
(`src/rememberstack/spine/observation_adjudication.py:416-470`). The prompt
asks the model to reason about which window "should cap" and about values
changing over time (`src/rememberstack/spine/observation_adjudication.py:42-59`),
so forward and reverse arrival can put the same pair to the model in opposite
roles and get different outcomes — not just different directions. r4 raised
this; it remains open. The write path is now correct, which is why this is not
blocking, but orienting the pair *before* the ladder would make the whole
decision order-independent instead of only its effect. Relation supersession
already does exactly that (`src/rememberstack/spine/supersession.py:185-201`).

### F4 — `normalize_observation_staging` puts unbounded text in the primary key

The PK includes `statement` and `normalizer_version`
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:31-34`).
PostgreSQL's btree index tuple limit (~2704 bytes) makes a sufficiently long
observation statement fail the staging insert outright, and nothing bounds
statement length on the normalize path. A hash of the statement in the key, or
a unique index on `(…, md5(statement), …)`, removes the cliff.

## Test quality

`test_ordinary_observation_inserts_pass_valid_from` asserts on
`inspect.getsource(...).count("valid_from=asserted_at") >= 3`
(`src/tests/workers/test_e3_claim_normalize_fanout.py:173-182`). It does not
execute the code path, does not observe a stored `valid_from`, passes if three
call sites exist while a fourth is missing, and breaks on any refactor that
extracts a helper or renames the local. The SQL-substring tests have the same
shape (`src/tests/workers/test_e3_claim_normalize_fanout.py:48-68`). These are
acceptable as tripwires beside a behavioral test; they are not a substitute for
one, and right now they are the only coverage of the r4 fixes.

The reverse-order end-to-end proofs r4 asked for — observations and
supersession in both version-completion orders, from the design's acceptance
table (`plan/designs/e3_claim_level_normalize_fanout_design.md:304-305`) —
remain absent. The PostgreSQL-backed suites that would host them skip entirely
without a database (see Verification), so the D43 window behavior this branch
changes has no runtime coverage in a default developer environment. That is a
standing gap rather than a new one, but it is why B1 above went unnoticed for
five rounds.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **28 passed in 6.57s**.

Supplementary:

```text
uv run pytest src/tests/spine/test_observation_adjudication.py \
  src/tests/spine/test_supersession.py -q
```

Result: **17 skipped in 1.05s** — `REMEMBERSTACK_DATABASE_URL is required for
real PostgreSQL D43 proofs`. No runtime coverage of observation windows in this
environment.

## Mergeability

**No — but the gap is narrow.** The r4 blockers are properly fixed, and the
D88 skeleton (lock, atomic fan-out, generation-pinned expected set, bound
coordinates, source-time orientation, ordered flush, forget scrub) is coherent
and reads as finished work. What holds it back is two failure paths the design
requires to work and this branch introduces: a flush retry that duplicates
observation history and re-spends the ladder (B1), and a cycle wait that stalls
finalization permanently during the legacy drain the design explicitly plans
(B2). Neither is exotic; both are silent when they happen. B3 is small work and
should ride along, because an untested tenancy check is the one thing in this
diff that fails invisibly.
