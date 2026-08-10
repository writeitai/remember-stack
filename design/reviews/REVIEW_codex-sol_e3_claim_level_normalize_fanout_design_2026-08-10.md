# Design review: D88 claim-level E3 normalize fan-out
**Verdict:** REQUEST_CHANGES
**Reviewer:** Codex (gpt-5.6-sol)
**Date:** 2026-08-10

## Summary

Claim-level ledger grain is the right scaling direction. It addresses the actual
BEAM bottleneck, retains the D67 work ledger as work truth, keeps D86's
retry-then-drop policy local to a claim, and correctly separates immutable
claims from relation supersession. A strict per-version barrier is also the
right shape; neither FIFO nor a deployment-wide quiet period is a valid
correctness mechanism (`plan/designs/e3_claim_level_normalize_fanout_design.md:11-36`,
`plan/analysis/e3_claim_level_normalize_fanout_analysis.md:44-66`).

The design is not yet safe to implement. Five contracts are blocking:

1. Completing a claim and checking siblings in one transaction does not by
   itself prevent both last completions from missing each other. D84's landed
   implementation takes a representation advisory lock before completion; D88
   does not bind an equivalent serialization rule.
2. The allowed chunked fan-out has no durable cursor/manifest. A crash after a
   partial fan-out can leave a permanently missing child set with no work edge
   that repairs it.
3. Observation adjudication is order-sensitive today. The entity lock prevents
   lost writes but serializes arbitrary worker arrival; it does not make the
   result independent of claim completion order.
4. The version-scoped supersession input and temporal ordering are left open,
   even though the current handler consumes a worker-local `relation_ids` list
   and chooses evidence by ingestion order.
5. A succeeded legacy version row is indistinguishable from a succeeded new
   coordinator at the same component version. The proposed readiness fallback
   can therefore report normalize complete while claim children remain in
   flight or dead-lettered, and connector-cycle finalization explicitly omits
   dead-lettered claim children.

These are correctness and recovery gaps, not objections to claim-grain fan-out.
Once they are bound, the design can retain its chosen work grain.

## Checklist vs design discipline

| Discipline | Assessment | Review |
| --- | --- | --- |
| Problem and decision | Pass | The bottleneck and the chosen work grain are clear and measured (`plan/designs/e3_claim_level_normalize_fanout_design.md:38-54`). |
| Rationale and alternatives | Pass | The design explains why worker scaling, FIFO, incremental supersession, global barriers, and coercion are wrong (`plan/designs/e3_claim_level_normalize_fanout_design.md:250-259`). |
| Costs | Partial | Model and row-count costs are covered, but fan-out transaction size, notification amplification, and repeated barrier-query complexity are not (`plan/designs/e3_claim_level_normalize_fanout_design.md:199-203`). |
| Security and tenancy | Incomplete | “Internal UUIDs” and “deployment-scoped workers” do not define coordinate validation across claim/version/representation/doc payload fields (`plan/designs/e3_claim_level_normalize_fanout_design.md:93-105,194-197`). |
| Failure and recovery | Incomplete | Per-claim retry/DLQ behavior is sound, but partial fan-out recovery, missed-barrier recovery, legacy-row classification, and rollback after claim work exists are not bound (`plan/designs/e3_claim_level_normalize_fanout_design.md:183-193`). |
| Implementation contracts | Incomplete | The expected set, supersession selector, barrier lock, fan-out commit protocol, component version, and readiness precedence remain ambiguous (`plan/designs/e3_claim_level_normalize_fanout_design.md:73-84,135-174,221-223,261-268`). |
| Barrier races and continuous ingest | Fail | Per-version scope is correct, but the completion race and out-of-order fact adjudication remain (`plan/designs/e3_claim_level_normalize_fanout_design.md:56-69,135-156`). |
| Fact-layer separation | Partial | Claims and relations are separated correctly; observation writes and supersession inputs do not yet satisfy order non-reliance (`plan/designs/e3_claim_level_normalize_fanout_design.md:158-166`). |
| D84 consistency | Partial | D88 adopts atomic completion conceptually, but omits D84's landed advisory-lock serialization and leaves fan-out durability open (`src/rememberstack/spine/work_ledger.py:248-314`). |
| D86 preservation | Partial | Retry/drop and soft-success policy are preserved in prose, but the claim-grain acceptance suite does not cover D86's generate-only soft boundary or systemic resolver failure (`plan/designs/e3_unknown_entity_type_gate_design.md:103-153`). |
| Readiness and migration | Fail | Legacy/coordinator precedence, dead-letter handling, exact zero-claim status, safe rollout, and rollback are not implementable as written (`plan/designs/e3_claim_level_normalize_fanout_design.md:168-174,205-223`). |

## Findings (blocker / major / minor / nit)

### Blocker

#### B1. The barrier can miss its only firing edge without per-version serialization

Section 5.4 requires completion and barrier evaluation in one ledger
transaction, then relies on an idempotent downstream insert for racing winners
(`plan/designs/e3_claim_level_normalize_fanout_design.md:135-153`). That prevents
success-without-follow-up and duplicate work, but it does not prevent a missed
fire under PostgreSQL's usual read-committed behavior:

1. Claim A's transaction marks A succeeded and sees B's uncommitted row as
   running.
2. Claim B's transaction marks B succeeded and sees A's uncommitted row as
   running.
3. Both skip downstream and commit. No later completion rechecks the barrier.

D84's current implementation closes exactly this race by taking a
representation-scoped transaction advisory lock before marking the row
succeeded and evaluating the barrier
(`src/rememberstack/spine/work_ledger.py:270-295`). D88 says “Mirror D84” but
does not make that lock, its scope, or lock ordering part of the algorithm.
`ON CONFLICT DO NOTHING` solves double enqueue, not missed enqueue.

Bind one serialization key containing at least deployment, representation (or
version), and normalize generation. Every claim completion and every
coordinator/enqueue-time barrier check must acquire it before changing or
reading barrier state. Under that lock, mark the current row succeeded, verify
the complete expected set with a set-based anti-join, and enqueue both terminal
branches in the same transaction. Add a two-real-connection test that forces
the interleaving above and proves one downstream pair exists; a test that only
allows both calls to run to completion may exercise duplicate prevention but
miss the lost-fire race.

#### B2. Fan-out durability is an unresolved correctness choice

The analysis leaves “15k claim rows in one transaction vs chunked batches” open
and the design merely recommends batching where possible
(`plan/analysis/e3_claim_level_normalize_fanout_analysis.md:106-110`,
`plan/designs/e3_claim_level_normalize_fanout_design.md:199-203,261-265`). Those
are different recovery protocols, not interchangeable performance details.

The ledger's current chain invariant is that parent completion and all
follow-ups commit atomically (`src/rememberstack/spine/work_ledger.py:225-246`).
If D88 marks the extract handoff or legacy coordinator succeeded after only a
prefix of chunked fan-out batches, a crash leaves expected claims with no
processing row. The strict barrier correctly stays closed, but nothing is
pending to create the missing rows. If the coordinator remains retryable while
children run, the design needs durable progress and a rule preventing premature
barrier fire. Idempotency alone does not identify whether enumeration finished.

Bind one of these protocols before implementation:

- a set-based `INSERT ... SELECT` of the complete expected claim set in the same
  transaction as the D84 extract-barrier handoff/coordinator completion, plus a
  bounded wake strategy; or
- a durable, restartable fan-out manifest/cursor whose completion is itself a
  barrier prerequisite and whose owner remains recoverable until all children
  exist.

Whichever is chosen must also evaluate the barrier after fan-out, so a replay or
migration case in which every relevant child already succeeded cannot wait for
a completion edge that will never recur. Test crashes before the first batch,
between every batch boundary, and after the final batch but before coordinator
completion.

#### B3. The observation path is explicitly order-sensitive; an entity lock is not order independence

D88 says each claim writes observations under the existing D43 entity lock, no
entity-wide wait is required, and job completion order is not a correctness
input (`plan/designs/e3_claim_level_normalize_fanout_design.md:29-36,158-166`).
The current adjudicator does not provide that property. Its contract says that
assertions “apply in order,” with later assertions seeing facts created or
closed earlier in the batch (`src/rememberstack/spine/observation_adjudication.py:121-174`).
It loads candidates ordered by creation time
(`src/rememberstack/spine/observation_adjudication.py:723-732`) and, on a
supersede verdict, caps the existing row at the arriving assertion's
`asserted_at` (`src/rememberstack/spine/observation_adjudication.py:407-448,747-754`).

The entity lock prevents simultaneous writes, but the lock winner is whichever
claim worker arrives first. An older assertion processed after a newer one can
therefore be treated as the successor and cap the newer state at the older
timestamp. Even within one version, splitting the current ordered entity batch
into unrelated one-claim transactions changes which candidates each ladder
decision sees. This violates the design's central fact-layer claim and can make
continuous multi-document ingest converge to different facts for different
worker schedules.

Either keep observations behind a durable post-normalize per-version/per-entity
batch with a defined authoritative order, or amend D43 with an out-of-order
insertion/recomputation algorithm that orients predecessor and successor by
source time and proves commutativity. Merely retaining the lock is insufficient.
Acceptance must run older/newer observation claims in both completion orders,
including concurrent versions and documents, and require the same observation
windows, evidence, and adjudication transcript semantics.

#### B4. Version-scoped supersession has neither an exact input set nor an order-independent time contract

The correct high-level choice is to keep relation supersession after the
barrier. However, the design leaves the load query as “all open relations with
evidence from this version” versus “all relations created/touched in this
version” (`plan/designs/e3_claim_level_normalize_fanout_design.md:261-268`). This
is a fact-correctness fork, not an implementation detail. The current normalize
handler can pass only its serial, worker-local list of newly created relation
IDs (`src/rememberstack/workers/e3.py:229-270`); claim fan-out destroys that
aggregation point.

The current adjudicator also does not consume a version-scoped evidence basis.
For a relation with multiple supporting claims, it selects the most recently
*ingested* evidence globally (`src/rememberstack/spine/supersession.py:373-397`),
and candidate ordering is likewise ingestion-based
(`src/rememberstack/spine/supersession.py:400-452`). A per-block advisory lock
serializes two adjudicators, but it does not ensure the later source assertion
is treated as the successor. Thus two document-version barriers completing in
opposite orders can still choose different “new” evidence or close a newer fact
from an older late-arriving version. A per-version barrier establishes complete
normalization for that version; it does not establish chronological order
across continuously ingested versions.

Bind the exact relation selector to the expected claim set and generations. A
safe starting shape is all relation IDs with supporting evidence from those
exact origin claims at the expected E3 generation; the existing adjudicator's
generation replay check can skip relations already adjudicated
(`src/rememberstack/spine/supersession.py:79-125,330-342`). Do not use row
creation/touch time as a substitute for evidence coordinates. Also bind how the
triggering claim/evidence and predecessor/successor direction are chosen from
`asserted_at`, including late older testimony and two version barriers racing on
one subject/predicate block. Tests must reverse both relation-id iteration and
version completion order and obtain the same fact windows and outcomes.

#### B5. Legacy coordinator success can bypass claim readiness, and connector finalization omits claim dead letters

The design says readiness is true when all expected claim rows succeed **or** a
legacy version-level row succeeded under an old image
(`plan/designs/e3_claim_level_normalize_fanout_design.md:168-174`). Yet D88 keeps
the D86 component version and turns an in-flight version-level row into a
coordinator that succeeds immediately after fan-out
(`decisions.md:3485-3497`). The ledger has no image-generation or “serial versus
coordinator” field; its unique identity is only deployment, target, stage, and
component version (`src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py:75-105`).
A read query cannot infer “under old images” from a succeeded row.

Consequently, a literal `claim aggregate OR version succeeded` implementation
reports normalize ready as soon as the new coordinator succeeds, even when a
claim is pending or dead-lettered. The connector requirement is also internally
inconsistent: Section 5.6 lists pending/running/failed children but omits
`dead_letter`, while the strict barrier says dead letters block
(`plan/designs/e3_claim_level_normalize_fanout_design.md:143-156,168-174`). D84's
landed connector-cycle query explicitly includes dead-lettered chunk children
(`src/rememberstack/spine/lifecycle.py:1011-1038`). Early cycle finalization can
violate D55's support-swap/retraction barrier, not merely a dashboard status.

Define an observable precedence/migration rule. For example, a complete
claim-fan-out marker or complete child set makes claim aggregation authoritative
and causes readiness to ignore the coordinator row; only explicitly
grandfathered pre-cutover serial rows may satisfy the legacy branch. This rule
depends on B2's atomic/durable enumeration. Bind zero-claim `finished_at`,
missing-child, failed, dead-letter, replay, and `skipped` semantics. Update
connector-cycle finalization to block on every expected non-succeeded claim,
including missing and dead-lettered children, and add readiness plus cycle tests
for each state.

### Major

#### M1. The expected claim set must preserve D56 delta-only reuse and name the extraction basis

“Accepted claims of chunks of R at the extract generation in force” is close,
but not exact enough for this schema (`plan/designs/e3_claim_level_normalize_fanout_design.md:73-84`).
D56 reattaches the same immutable claim to later version chunks through
`chunk_claims`; the occurrence map intentionally distinguishes carried testimony
from the claim's origin chunk (`plan/designs/evidence_lifecycle_design.md:346-351`).
The measured reuse contract explicitly says E3 is delta-only: it reads claims by
origin chunk, while reused chunks carry only occurrence links
(`plan/analysis/reuse_hit_rate_spike.md:50-55`).

An implementation that enumerates `chunk_claims` would fan out carried claims,
destroy the delta-only boundary, and make one global claim work row appear in
multiple version barriers even though its unique work key has only one payload.
An implementation that queries `claims.chunk_id` without `extractor_version`
can include superseded extraction generations on a re-extracted representation;
the current E3 query has exactly that missing generation predicate
(`src/rememberstack/spine/claim_catalog.py:310-316`).

Bind the expected set as deployment-owned **origin** claims whose origin chunks
belong to the named representation/packing generation and whose
`claims.extractor_version` equals the extraction generation captured at the D84
handoff. Exclude occurrence-only passthrough claims. Carry `extractor_version`
and the relevant chunk/representation generation in a typed barrier contract;
the claim payload currently carries neither
(`plan/designs/e3_claim_level_normalize_fanout_design.md:93-105`). An all-reused
version is then the zero-new-claims terminal path. Add all-reused, mixed
reused/new, same-representation re-extraction, and old-generation exclusion
tests.

#### M2. The rolling-upgrade statement identifies the hazard but does not provide a safe cutover or rollback

The design correctly notes that an old serial handler claiming a claim-targeted
row is unsafe, but “all normalize workers from one image revision” is a desired
state, not a transition protocol
(`plan/designs/e3_claim_level_normalize_fanout_design.md:205-210`). The current
E3 handler never branches on target kind; it loads the representation and loops
all claims (`src/rememberstack/workers/e3.py:126-170`). During a normal rolling
deploy, one new producer can enqueue thousands of claim rows while an old E3
replica still claims the same stage. Each claimed row can then run the whole
document loop, producing catastrophic duplicate BEAM work before replay markers
converge.

Specify an enforceable two-phase capability gate or stop/drain cutover: upgrade
all consumers first while claim production remains disabled, verify the minimum
worker revision, then enable fan-out. State how to roll back once claim rows
exist; an old image must never be allowed to consume them. The runbook item in
the implementation plan is not sufficient without an engine/UMC mechanism that
enforces the ordering. Include a mixed-image negative test or deployment check.

#### M3. Component-version policy is internally inconsistent and affects migration/readiness

D88 and Sections 1/5 use the D86 `E3_NORMALIZER_VERSION`, while the implementation
plan says a `:claim-fanout-1` suffix is preferred for auditability
(`decisions.md:3485-3495`,
`plan/designs/e3_claim_level_normalize_fanout_design.md:13-23,221-223`). The choice
also changes whether pre-D88 serial rows can collide with or satisfy D88
readiness, how existing DLQs are replayed, and which generation relation
evidence claims to represent. It cannot remain an implementer preference.

Choose one versioning rule and give the migration consequences. If work grain is
declared output-equivalent and stays on the D86 version, add an explicit
coordinator marker/cutover rule for B5. If the version changes, register the new
component and define backfill/replay behavior without treating old evidence as
proof of a new-generation run. In either case, note that B3 currently changes
observable observation ordering, so “outputs are identical” is not true until
order independence is resolved.

#### M4. The tenant and coordinate-validation contract is too weak

Each work item duplicates `claim_id`, `version_id`, `representation_id`, and
`doc_id`, but the handler contract says only to load by target/payload claim ID,
and security is reduced to internal UUIDs
(`plan/designs/e3_claim_level_normalize_fanout_design.md:93-105,126-133,194-197`).
UUIDs are not authorization or relational integrity. Several large tables use
logical rather than physical foreign keys, and the processing unique key is
deployment-qualified. A malformed/stale payload must not normalize a foreign
claim under the claiming deployment, use an unrelated document for evidence,
or let a foreign representation alter an expected count.

Derive `doc_id`, version, and representation membership from deployment-scoped
spine joins; do not trust the payload's `doc_id` for evidence. Require
`work.target_id == payload.claim_id`, and require the claim's origin chunk,
representation, version, document, and deployment to match the complete barrier
coordinate. Apply deployment predicates to every expected/ready/supersession
join. Coordinate mismatch should be a non-retryable, opaque worker error. Add
cross-deployment and mismatched-coordinate fixtures, including deliberately
reused UUID values where the schema permits logical keys.

#### M5. The BEAM cost analysis omits potentially quadratic barrier work and enqueue/wake amplification

The cost section counts O(claims) rows but not O(claims) barrier evaluations
(`plan/designs/e3_claim_level_normalize_fanout_design.md:199-203`). A full count
or Python probe of all expected claims after each of approximately 15k
completions is potentially quadratic. The current D84 barrier counts its full
expected and ready sets on every completion
(`src/rememberstack/spine/work_ledger.py:530-555,922-962`); copying it literally
at a denser claim grain is not enough evidence of BEAM suitability. A Python
tuple of 15k individual enqueues also creates a large completion transaction and
one insert-trigger wake per row under the current ledger schema.

Bind a set-based, indexed negative-existence query or a durable counter/manifest
with proven race semantics. Review the required claim-origin and processing
indexes; the current claim index is only on `chunk_id`, while generation is a
barrier predicate
(`src/rememberstack/spine/migrations/versions/p0_02_0004_claims_facts_evidence.py:67-74`).
Require `EXPLAIN (ANALYZE, BUFFERS)` and a 15k-claim fan-out/barrier load test,
including database pool pressure, queue wake count, completion latency, and
steady retry behavior. Also measure repeated per-claim catalog/prompt loads that
the old version job performed once.

#### M6. Acceptance coverage does not prove D86's changed work boundary or lifecycle consumers

The acceptance table covers persistent illegal types but not D86's most
important exception split (`plan/designs/e3_claim_level_normalize_fanout_design.md:225-239`).
D86 makes only a normalizer-generate `ProviderInvalidResponseError` claim-soft;
the same exception from resolver/T4, database errors, mint refusal, and generic
provider failures remain systemic
(`plan/designs/e3_unknown_entity_type_gate_design.md:103-121,138-153`). At claim
grain, these outcomes now decide whether the individual row succeeds and opens
the version barrier.

Add tests proving:

- generate-invalid-response soft-success counts as a succeeded child and can
  complete the barrier;
- resolver/T4 invalid response, database failure, and mint refusal retry/DLQ
  the child and hold the barrier;
- retry and failure cost keys remain attributed once to the claim processing
  attempt;
- readiness and connector cycles remain blocked for missing, pending, running,
  failed, budget-parked, and dead-lettered children and recover after replay;
- zero-claim and all-reused versions still run reconcile through the no-op
  supersession branch and embed readiness;
- the post-barrier supersession query includes relations created by different
  claim workers and excludes unrelated versions/deployments.

D86's version-level `e3.claims_processed` and `normalize_all_soft_failed`
signals also change meaning when every job contains one claim
(`plan/designs/e3_unknown_entity_type_gate_design.md:169-188`). Define whether
they become per-claim attempt events plus a barrier summary, or document the
loss of the version aggregate; “retain logs per claim” does not preserve the
original denominator contract.

### Minor

#### m1. Narrow the process-order non-reliance statement around entity typing

Section 5.5 simultaneously says process order is not a correctness input and
that first-under-lemma-lock wins entity type
(`plan/designs/e3_claim_level_normalize_fanout_design.md:158-166`). The latter is
intentionally arrival-order-dependent. If this remains an accepted status-quo
nondeterminism, say that barrier membership, evidence attachment, observation
state, and relation temporal semantics are order-independent while initial type
choice is the explicit exception. Otherwise the blanket claim is false even
after B3/B4 are fixed.

#### m2. Backlog metrics need state and lane semantics, not only pending/running counts

Retryable work normally sits in `failed` with a future `not_before`, while
budget-parked rows are `pending` but not runnable
(`src/rememberstack/model/processing.py:32-48`,
`src/rememberstack/spine/work_ledger.py:770-798`). A pending/running count alone
can both hide an outage and scale replicas against exhausted budget. Define
separate deployment/lane gauges for due pending+failed work, running work,
retry-backoff, budget parking, dead letters, and oldest due age before calling
the backlog a correct autoscaling signal
(`plan/designs/e3_claim_level_normalize_fanout_design.md:176-181`).

#### m3. “Missing claim” needs distinguishable stale-work and integrity behavior

Section 5.3 maps a missing claim directly to non-retryable failure
(`plan/designs/e3_claim_level_normalize_fanout_design.md:126-133`). After
deployment-scoped coordinate validation, distinguish a legitimately removed
hard-forget target, a stale/superseded generation, and an integrity mismatch at
least in internal diagnostics, while keeping the public/tenant-facing result
opaque. The barrier must define whether a hard-forgotten expected claim cancels
the version tree or requires an audited terminal override; silently succeeding
would contradict the strict barrier.

### Nit

#### n1. Use “succeeded normalize row,” not “terminal normalize success”

`dead_letter` is terminal but is not successful. The normative barrier already
uses `status=succeeded`; use that exact phrase throughout to avoid an
implementation treating every terminal state as complete
(`plan/designs/e3_claim_level_normalize_fanout_design.md:20-23,143-156`).

#### n2. Move the supersession query out of “open implementation choices”

Once B4 is resolved, Section 14 should retain only operational tuning such as
batch size. The fact selector and triggering evidence coordinate belong in
Section 5.5 as binding fact-layer contracts, with their tests adjacent.

## Residual risks

- Strict barriers intentionally trade availability of one document version for
  fact completeness. A durable blocked-version diagnostic and replay path will
  remain operationally necessary even after the SQL is correct.
- Claim-level concurrency will expose provider quotas, database pool limits,
  lemma/entity lock hot spots, and D43 model-cost bursts sooner than the serial
  path. Replica caps are necessary but do not replace per-deployment due-work
  and spend controls.
- Making D43/D4 genuinely out-of-order-safe may be broader than D88. If that
  work is deferred, D88 must retain an ordered observation/supersession
  coordination stage rather than asserting that the current locks provide the
  property.
- O(claims) ledger growth is acceptable only with retention/bloat monitoring
  and verified query plans. The denser work grain increases cost-ledger and
  operational-row cardinality even when model tokens stay constant.
- The D56 origin-claim rule must remain aligned with future representation or
  extractor-generation migrations. Occurrence testimony and normalization work
  are deliberately different grains.

## Recommendation

Keep the D88 product decision—claim-targeted normalize work with a strict
version-scoped downstream barrier—but do not mark the design implementation-
ready yet. Approval should require a revision that:

1. binds a serialized complete+barrier transaction and a crash-safe full
   fan-out protocol;
2. freezes the deployment-scoped origin-claim set, extraction basis, D56 reuse
   behavior, and tenant coordinate validation;
3. supplies order-independent or explicitly ordered D43 observation and D4
   relation adjudication contracts with an exact version-scoped relation set;
4. defines observable legacy/coordinator readiness precedence, connector-cycle
   dead-letter handling, component versioning, safe cutover, and rollback; and
5. adds real concurrency, continuous-ingest, D86-boundary, lifecycle-readiness,
   cross-tenant, D56 reuse, and BEAM-scale query/load acceptance tests.

With those changes, the design should be re-reviewed; the underlying fan-out
choice itself does not need to be reopened.
