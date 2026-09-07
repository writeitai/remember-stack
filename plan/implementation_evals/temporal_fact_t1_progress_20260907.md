# T.1 implementation evidence — 2026-09-07

Status: work in progress in PR #384. This is implementation evidence, not a
new design or a claim that T.1 is complete. D110 is accepted on main in #383
(`df262aaf044d6490d2921ee9c7edeb08e44b6cd6`). The full scope remains
`plan/plans/temporal_clocks.md`: T.1 followed by T.2/T.3/T.5 and release.

## Foundation at 9c77a55d

The first commit adds typed temporal state and shared pure rules for seeding,
occurrence union, separate world/belief membership, guarded caps, grounded
monotonic corrections, endpoint-owned compensation and D55 withdrawal.
The occurrence reducer streams evidence with constant intermediate storage.
49 focused temporal tests pass, including late archive ingestion, future
activation/expiry, erased membership, finite-cap refusal, and compensation
preserving an independent later cap and belief invalidation. Ruff, Pyright,
import boundaries and test inventory checks pass.

Three new revisions implement the accepted schema boundaries: committed enum
additions (`p9_28_0049`), expansion and legacy-exclusion removal
(`p9_29_0050`), and conversion-guarded final validation (`p9_30_0051`).
The final revision derives generation certificates from completed conversion
runs; it does not bootstrap deployments or silently fill fact dates.

## First PostgreSQL 19 CI run

[CI34065082780](https://github.com/writeitai/remember-stack/actions/runs/34065082780)
ran the supported PostgreSQL19 image and full Alembic graph. Quality and unit
jobs passed. The contract smoke proved fresh installation, exact catalog
inventory (98 tables; checks182, foreign keys195, not-null748, primary keys98,
unique58, exclusion1), empty downgrade/re-upgrade, negative missing-partition
verification, and no-op upgrade. These checks passed without weakening catalog
assertions. They establish schema compatibility, not runtime temporal correctness.

The run failed overall:

- The old D79 populated-deployment fixture and repeated Compose setup reached
  finalization without conversion receipts. The new guard correctly refused
  them. The required converter and upgrade orchestrator are not implemented
  yet; do not remove the guard or invent completion to make these tests pass.
- Integration modules reset shared isolated test databases by explicitly
  downgrading to `base`. The initial partial-rollback protection also blocked
  this intentional whole-schema teardown, causing cascading setup errors.
  The follow-up distinguishes the Alembic destination: teardown to base may
  remove everything and does not temporarily restore a legacy exclusion over
  data being deleted. Partial populated rollback remains refused. This fix
  still requires its own PostgreSQL19 validation.

Local Docker failed a bounded 10-second responsiveness check. No shared daemon
was restarted. Local migration tests passed graph discovery and skipped nine
PostgreSQL cases. Earlier D110 PostgreSQL15 design probes remain separately
scoped evidence, not substitutes for supported-runtime tests.

## Remaining implementation

The pure rules are not yet wired into runtime authority. Required work includes
common locks/journal/support and replay; normalization receipts and complete
ordered relation/observation application; current evidence-backed correction
work; all authority participants (currency/reconciliation, review, identity,
forget); cache certificates, boundary events and all consumer checks; complete
forget inventory and repeated-forget checkpoints; bounded in-place conversion,
startup orchestration and readiness; versions/protocol and user documentation;
full acceptance and Antigravity review. No release is authorized by the
foundation evidence alone.

## Guarded journal implementation

The shared application context now acquires the deployment fence, identity
lock, sorted full canonical blocks, sorted facts, and one combined sorted
cache-source set. It checks the actual fact subject against the declared
canonical block, rejects source registry collisions, and mirrors fact revisions
into cache leaves. A savepoint and failed-session guard prevent an application
error from committing earlier effects in the same group, even when a caller
catches the error. New identities must receive their seed receipt atomically.

Typed effects constrain ordinary field authority. The journal writes existing
narrative adjudications, exact temporal effects, consumed evidence, support
attestations, and complete read/write block sequences in one transaction.
Read-only witnesses advance ordering without changing truth revisions.
Correction endpoints must match current linked supporting candidates.
Compensation validates the recorded target and restores only still-owned
components, preserving an independent later cap. These mechanisms do not
replace the still-required identity ladder, semantic adjudication, complete
candidate discovery, or authority participation by existing writers.

Seventeen tracked integration cases were exercised on a private local
PostgreSQL15 instance using original table constraints from migrations
0001–0005/0017 plus the exact temporal enum/expansion DDL. The checks cover both
fact planes, correction→cap→compensation, atomic rollback after a caught error,
foreign canonical blocks, redirect resolution, missing generation, source key
collisions, concurrent reverse input ordering, stale currency, unselected dates,
read-only witnesses, and session-timezone invariance. This probe found and fixed
a real issue: decoded timestamptz values and evidence fingerprints now normalize
to UTC regardless of the connection display timezone. The private server was
stopped after each run; the shared Docker daemon was not changed.

This local scope omits unrelated projections/indexes and is **not** a full
Alembic or supported PostgreSQL19 proof. The tracked test module uses the full
migration graph in CI and is included in the integration inventory. Local Ruff,
Pyright, import boundaries, inventory checks and the existing 49 temporal rule
cases pass. PostgreSQL19 validation and Antigravity journal review remain pending
at this entry. Full T.1, T.2/T.3/T.5 and release remain unfinished.

CI34065359766 subsequently completed: quality, unit, surfaces and adapters passed;
contract smoke, workers and Compose failed. The successful surfaces/adapters runs
provide additional evidence for the complete-schema teardown fix. Populated
conversion/startup remains an explicit implementation gap; no guard was weakened.

## Durable conversion catalog

The catalog now captures a pinned campaign only at the committed C revision
with no unfinished legacy work. It prepares immutable per-fact shadows, applies
each through the guarded journal in an owned transaction, and verifies exact
state, identity, selected legacy history, evidence, narrative and support before
recording campaign completion. Batches bound the fact frontier; claim witnesses
stream in bounded batches even for highly redundant facts. Completion does not
publish a serving generation; the final migration retains that responsibility.

Conversion preserves historical and retired-subject facts and never invents a
creator from the earliest attached claim. A converted successor's original
before-image remains available to predecessor conversion, so either recorded
application order produces the same policy input. The predecessor operation
records the consumed successor seed witness as well as its own attached claims.
Ambiguous creator ties fail to establish authority even when the first two
physical rows agree and a third disagrees. Later observation withdrawal does
not replace its earlier recorded supersession cap.

Local validation: 69 pure tests, 38 PostgreSQL15 catalog/journal cases, Ruff,
targeted Pyright, import boundaries and test inventory pass. A stream failure
after a completed evidence batch rolls back all operation, narrative and support
rows and preserves the original fact revision. The supported PostgreSQL19
evidence for the preceding commit and Antigravity's scoped second approval are
recorded in `temporal_journal_review_20260907.md`. The new catalog still needs
its supported-runtime CI and review.

Startup orchestration and enforced intake/serving fences are not wired. The
converter requires a quiescent old runtime; its shared lock does not retroactively
make legacy writers participate. The existing unconditional self-host upgrade
therefore remains a known failure. All remaining live writers, complete staging,
correction execution, cache consumers, hard forget/replay, readiness and
T.2/T.3/T.5 remain required. No release or merge is justified by these proofs.

## Maintenance startup and finalization

Self-host setup now invokes the library upgrade orchestrator. It checks for
unfinished legacy work before changing the schema, serializes setup callers
with a database session lock across commits, commits C explicitly, resumes all
conversion phases for every deployment, then invokes D/head separately. A retry
at C never reruns the legacy-exclusion removal. A retry at head neither
reconverts live facts nor manufactures a missing generation certificate.

An empty identity created at C uses the ordinary zero-row converter. A new
identity bootstrapped after D verifies the actual constraint definitions and
records its explicit zero-row conversion and fact certificate in the same
creation transaction. The constraint check compares parsed SQL expressions,
retaining Boolean grouping, enum casts and function identities; it also rejects
unvalidated constraints and unexpected additional exclusions. It does not
infer schema readiness from a constraint name or default column value.

D now rechecks exact fact/shadow values, migration kind/result/revision,
narrative after-image and support counts. Extra conversion shadows, edited fact
dates, changed operation kinds or missing support cannot hide behind a campaign
already marked complete. The D79 migration proof now uses the real orchestration
entry point. No finalization guard or catalog count was weakened.

Validation: 90 temporal/profile pure cases pass, as do Ruff, targeted Pyright,
import boundaries and inventory checks (98 unit files, 57 integration files).
The documentation production build and Pagefind indexing pass. Eleven private
PostgreSQL15 upgrade cases pass using original table constraints, installed C,
the real D revision and Alembic environment with separate commits. This probe
omits the full pre-C graph and the pre-C drain fixture; the tracked module's
twelve supported PostgreSQL19 lifecycle cases remain required in CI.

The maintenance runner requires operators to stop old intake/serving/worker
processes after draining them. A new advisory lock does not retroactively make
old binaries participate. Full runtime generation/serving gates, complete
staging and writer participation, corrections, caches, hard forget/replay,
readiness, T.2/T.3/T.5 and release remain unfinished. This startup implementation
does not certify those remaining mechanisms.

## Complete normalization publication

The typed publication catalog now records one complete resolved answer per
claim and normalizer generation: all distinct relation triples, all accepted
observations, and an explicit accepted/empty/soft-drop disposition. It snapshots
immutable source inputs before inference, revalidates them before publication,
resolves identity redirects under the identity epoch, and retains the
normalizer's shape judgment. D41 kind precedence belongs at fact application,
not in the immutable normalization answer (D110 §3.1). One transaction writes the receipt and every relation assertion;
it does not insert facts or attach evidence. Competing accepted attempts reuse
the first complete receipt without mixing outputs or replacing empty results.
Reads verify both the output digest/counts and the actual assertion set.

Staging now has a shared admission/identity context. The fact journal retains
its nonempty complete-block requirement; no empty-block escape was introduced
to authorize fact mutation. Its existing admission and identity lock prefix was
extracted without changing maintenance authority or lock modes.

Eleven private PostgreSQL15 publication cases pass, including concurrent
different outputs, rollback after the first assertion, missing-assertion
refusal, exact generation reuse, empty and soft-drop receipts, stale source
refusal, foreign deployment identity refusal and redirect deduplication. The
local probe includes actual source/table constraints and temporal finalization,
not the full supported migration graph. The 38 existing journal/converter
database cases and 90 temporal/profile pure cases still pass after extracting
the admission prefix. Ruff, targeted Pyright, import boundaries and inventory
checks pass. The new module requires its own PostgreSQL19 CI and scoped review.

The publication-only commit `519c3453` passed full
[CI34072158644](https://github.com/writeitai/remember-stack/actions/runs/34072158644).
Antigravity round five approved that scope, with defensive predicate locking
and additional forget/observation-only proofs suggested. Those changes are
included in the subsequent integration work below. Neither this CI nor the
scoped approval proves the new integration.

## Normalizer and closed-version handoffs

The claim worker now loads an existing complete receipt before inference,
publishes every resolved assertion when absent, and returns its version barrier.
It no longer creates relation facts, adds relation evidence, stages observations
by enumerating currently known versions, or refreshes fact profiles during
normalization. Its unused fact-application/profile dependencies and the old
serial normalization writer were removed. Old normalization generations are
refused under the upgrade's existing requirement to drain old work before C.
The normalizer generation adds `complete-receipt-1:temporal-shape-1`; the prompt
and candidate schema now ask for state/occurrence/unknown shape.

The version barrier loads observations from complete receipts for the exact
chunker/extractor/representation membership. A later D56 version reuses the
receipt without another normalizer call. Re-evaluating a closed version cannot
enlarge its recorded input set. Current-generation claim completion requires a
complete receipt before changing ledger status. Barrier transactions acquire
shared deployment/identity admission before representation locks, and missing
receipts or missing assertion rows refuse materialization.

After observation completion, the same transaction records relation version
state, subject/predicate units, all distinct assertion memberships and existing
work-ledger rows using the entity-unit convention. Empty results have explicit
empty relation completion and start reconciliation plus claim embedding.
Existing application receipts supply completion time when a later version
reuses an assertion. No fact or evidence is created by this handoff.

Validation for this working integration: 123 focused worker/temporal/profile
cases pass, targeted Pyright and Ruff pass, and import boundaries remain intact.
Twenty-one private PostgreSQL15 proofs exercise publication and real worker/
barrier catalog paths: source/forget admission, observation-only output,
D56 receipt reuse, missing receipt/assertion refusal, complete multiple-triple
membership, observation-first ordering, explicit empty completion, worker retry,
ledger refusal to complete missing output, and rollback of both barriers and
membership when the final work enqueue fails. The harness uses actual relevant
DDL and the final temporal constraints; it is not full migration-graph or
PostgreSQL19 evidence.

**Unfinished and not runnable to full fact completion:** the ordered relation
applier is the next integration. Entity-unit supersession work explicitly refuses
to complete through the old fact-ID handler. This fence is temporary work in
progress, not an implementation of ordered application. Observation journal
integration, all other authority writers, correction/replay, cache certificates
and events, hard forget, serving/readiness checks, remaining generation/LoCoMo
protocol rolls, consumer packages and release are still required. The complete
pipeline must pass supported PostgreSQL19 CI and Antigravity review before this
PR can merge. No full-pipeline success is claimed for the handoff increment.
