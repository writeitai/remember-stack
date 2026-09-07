# Observation application receipts and support provenance (D113)

> **D118 supersession (2026-09-07; effective when merged).** D113 storage, proof graph and incorporated SQL are withdrawn. D118 retains the requirements for assertion provenance, atomic support changes and stable retry results.
> The [mutable fact window design](mutable_fact_windows_design.md) is the current
> authority. The text
> below is historical rationale, including any old “binding” or “required” labels.

Historical status: superseded by D118. This formerly completed D110 §2's
observation prepare/apply storage contract and amends D90's detailed locking and
staging rules. It does not establish implementation or release readiness.
[Analysis](../analysis/observation_temporal_applications.md) explains the gap and
alternatives; [SQL](observation_temporal_application_schema.sql) is normative
alongside the existing D110 schema.

## 1. Problem and decision

The observation worker currently holds an entity lock while asking a model to
judge statements. Its disposable, version-qualified staging and fact/claim
evidence keys cannot remember the complete answer, reuse that answer across
D56 versions, or prove which of several normalized statements from one claim
moved when a later state cap re-split history. D110 requires durable preparation,
model inference outside locks, and exact atomic application on both fact planes.

Preserve D90's version-scoped entity work units and existing `processing_state`
leases. Add observation admission batches and assertion-grain application rows,
plus their adjudication linkage. Retain staging as exact version membership with
an applied timestamp. Each application stores an immutable original result and
a separately owned current support location. No extra scheduler, generic fact
application framework, normalized-observation assertion table or support table
is needed.

## 2. Identity, generations and version membership

An observation assertion is one distinct accepted `(subject_entity_id, statement)`
tuple in an immutable complete normalization receipt. Preserve its exact statement
and normalizer shape; D41 precedence is applied when determining fact kind.
Claim ID alone and output-array position are not assertion identities. Use UUIDv5
with `NAMESPACE_URL` over the UTF-8 compact JSON array
`["rememberstack:observation-assertion:1", deployment_uuid, receipt_uuid,
normalized_subject_uuid, statement]`, with lowercase UUID strings, `ensure_ascii=False`, compact separators
`(",", ":")`, and no Unicode normalization. Use JSON string escaping for quotes,
backslashes and control characters; leave other Unicode as UTF-8. Non-ASCII and
control-character test vectors pin the encoding. The immutable receipt makes this stable for D56 reuse. Entity
redirects change the canonical application block, never this assertion handle.

Encoding vectors use deployment `00000000-0000-0000-0000-000000000001`, receipt
`00000000-0000-0000-0000-000000000002` and normalized subject
`00000000-0000-0000-0000-000000000003`: statement `CEO` yields
`44daeb43-2baf-5ca3-9930-8c785c1e43f7`; statement `Café` followed by a newline and
`CEO` yields `9fa88577-9020-5e35-9cbf-a8e3c237ff66`. Escaping the accented character
as ASCII instead of preserving UTF-8 would produce a different, invalid handle.

The semantic application key is deployment, assertion UUID and observation
adjudicator generation. The row references and validates its exact tuple against
the complete normalization receipt before admission; duplicates cannot change
statement, shape or source coordinates.

D90 version-state and unit identity includes normalizer generation, observation
adjudicator generation and the exact composed flush component generation; units
also include their normalized subject entity. Version assertion membership has
those same pins and the assertion UUID. The flush pin must agree with the
registered composition and the actual `processing_state.component_version`.
A flush-only orchestration change may reuse an unchanged semantic application;
a changed adjudicator may not. A normalizer roll is not required merely to
change adjudication policy.

Materialization, complete expected unit count, every accepted observation
membership, and unit work enqueue commit together. Validate membership against
the entire accepted observation output, including claims reused from another
version, with the version's own representation, hash, extraction pins and lane.
Membership rows have a generated internal UUID primary key and a unique exact
version/generation/assertion key; statement text is not copied into a new B-tree
identity. An empty output requires an explicit empty completion certificate. A missing
membership or application is not empty success. Retain `applied_at` membership
witnesses; ordinary completion no longer deletes staging.

Only the currently leased unit may complete after application locks are released.
Before unit/version completion or readiness, verify the exact expected assertion
set, all generation pins, successful unit work with terminal timestamps, complete
application receipts and valid current support. A historical original target
missing without an accepted sanitized checkpoint cannot be resurrected or replaced.
Missing/corrupt support or receipts block completion. D56 reuse marks membership
from the certified existing application without another identity judgment or
reattaching support to its original historical target.

## 3. Closed admission and preparation

Under the shared D110 deployment/identity/canonical-observation-block locks,
freeze all currently eligible unapplied applications from materialized, non-dead-
letter D90 units. Admit one semantic adjudicator generation at a time. There is
one active batch per canonical entity across generations; finish or explicitly
retire its admitted work before another generation can take the head. New arrivals
wait for the next closed batch.

Order admitted assertions by `asserted_at NULLS LAST, claim_id, statement COLLATE
"C", assertion_id`. This source order is a work order; it supplies no world-time
endpoint. Store the complete admitted count and membership and assign immutable
ordinals. Only the least unapplied ordinal may prepare/apply. Concurrent helpers
reuse the same head and may not mark each other's work successful.

D110 §2 governs each head: snapshot exact source, fact/block revisions, canonical
identity and policy inputs under complete sorted locks; allocate an attempt UUID;
release locks for bounded inference; publish the first complete typed output by
CAS against the attempt/fingerprint and empty output; reacquire/revalidate before
application. Persist the full dependent re-split plan too. Stale completed attempts
receive recorded dispositions before replacement. Late answers cannot publish
beside another attempt or reintroduce forgotten source data. Model calls hold no
fact/block transaction or session lock.

Corrections and source removal may interleave while inference runs. Their
participating revisions make stale application fail safely. Assertion applications
within a closed batch remain ordered; this does not claim independence from
unseen future input or byte-identical provider retries.

## 4. Identity and atomic application

Observation identity selects one fact. A unique identical, temporally compatible
**state** can use the deterministic shortcut. Multiple eligible state candidates
need grounded single-target selection; unresolved selection uses D43's conservative
new/coexist outcome. Identical event text alone does not prove occurrence identity.
D107's kinds, datedness, occurrence identity, temporal nomination and cap guards
apply. D112's relation-shaped multi-target rule is not an observation rule.

Apply the accepted decision, evidence, seed or existing-fact effects, all re-split
effects, ordinary adjudications, typed temporal operations, application completion,
retired memberships and cache/correction intents in one transaction. D110's
complete sorted fact/source locks and revalidation cover every participant.
The revision-zero insertion never escapes the transaction; seed, evidence,
creator and revision-one authority commit together. Evidence attachment changes
occurrence metadata and support counters but never verdict endpoints or seed.

Completed original fields—outcome, original observation ID, admitted coordinates,
input digest and completion time—are immutable. Retry consumes them and the
recorded effects without model calls or target renomination. The adjudication
junction identifies every effect in the atomic application group, including
historical re-splits. Logical original handles and current support references
are validated under locks; they cannot recreate missing facts.

## 5. Current support and late-arrival re-split

Suppose assertion S originally evidenced A, then a later world-time cap requires
S to become the subsequent A2 slice. The original receipt continues to say A.
The later cap's atomic group moves S's **current** support location to A2. A
D56 retry of S reuses its receipt and current location, and cannot move it back.

Store `support_state`, `current_observation_id` and `support_owner_operation_id` on the application
row, separately from the immutable original result. One current location is
sufficient for one selected observation identity. The owner is the operation that
most recently established that location. Each relocation is an explicitly typed,
versioned payload in the ordinary adjudication's features, linked to its temporal
operation, with fields:

- schema `observation-support-move:1`;
- assertion UUID and observation adjudicator generation;
- previous and destination observation UUIDs;
- previous support owner operation UUID;
- establishing operation UUID and causal cap operation UUID.

These fields are machine-readable authority, not free-form rationale. The guarded
writer checks exact previous location/owner, source assertion eligibility,
complete locked old/new facts, the establishing effect and causal cap, and the
recorded operation dependencies before changing support. Initialization is part
of the original application; it establishes current=original and the seed/evidence
operation as owner. Replay applies original application and later moves in their
recorded dependency order, validating the old location/owner. It never reruns an
already receipted assertion as fresh ordinary ingestion.

D107 §4.5 decides re-split eligibility from the attached state assertion's canonical
world-time start relative to cap T, never publication time. Use each original
normalized statement and shape from its application, not the capped fact's display
statement. If further identity inference is needed, prepare the complete dependent
plan outside locks, then revalidate/apply it with the triggering cap group. Bounded
reads and inference do not permit silently omitting qualifying support or committing
half a re-split group.

`observation_evidence` combines preserved legacy support with the fact/claim
aggregation of current assertion support. During conversion, existing evidence
rows acquire `legacy_support=true`; new links default false, and evidence upserts
never clear a true baseline. The baseline records known fact/claim support whose
original normalized assertion was not retained; it is not a fabricated application. Moving S removes `(A, claim)` only if it has no legacy baseline and no other surviving assertion
application for that claim still supports A. This preserves another statement
from the same claim. Add destination evidence idempotently, recompute affected
occurrence metadata from attached non-forgotten testimony, and retain independent
document-lineage evidence counts. Support moves, both facts' revisions, evidence
aggregation, operations and invalidation intents commit together. If a cap would strand qualifying legacy evidence and its original normalized
assertion is not recoverable, refuse the cap with a durable
`legacy_assertion_unrecoverable` discrepancy. Preserve the old bounds and baseline;
complete the incoming observation conservatively as new/coexist. This explicitly
amends D107 §4.5: unknown original provenance cannot justify a guessed re-split.
Fresh normalization may establish new authority, but cannot be called recovery
of the original assertion. A generation
change cannot silently discard another generation's support; replacement/rebuild
is an explicit recorded lifecycle operation.

## 6. Forget, conversion, failure and operations

D74 inventory includes batches, application tuples/snapshots/outputs, retained
memberships, adjudication junctions and support-move payloads. Source normalization
receipt deletion cascades its applications/memberships; reverse original/current
fact and operation lookups participate in closure. Scrub forbidden payloads and
repair current evidence aggregation before releasing the forget fence. Independently
supported surviving facts retain their authority through D110's sanitized checkpoint
contract. Repeated forget resolves support per component; retained logical handles
are not permission to rehydrate source data or recreate a deleted fact.

The existing fact-window checkpoint alone cannot restore assertion support after
scrubbing support-move payloads. `temporal_checkpoint_observation_support` extends
the same closed-block checkpoint with each surviving application's clean current
assignment, root operation and **per-assignment** original support attestation.
Its value fingerprint hashes SHA-256 over UTF-8 JSON with the §2 escaping rules
for the tuple of assertion,
adjudicator generation, support state and current observation UUID (or NULL).
Validate the entire application inventory for the covered blocks, exact matching
fact roots, surviving source eligibility and complete independent assignment proof
before marking the checkpoint verified. Each application retains an exact active `support_checkpoint_id` witness;
looking up any historical erased root is insufficient. Linked current support uses the fact root
as owner; a subsequent ordinary support relocation clears the active checkpoint
pointer atomically as it installs its new operation owner. Resolving a checkpoint
owner follows this support row to its original supporting
operation, not the union of fact endpoint witnesses.

If an assignment's required proof was erased or remains unproven, the clean root
records `support_state=erased` with NULL current observation and owner. Remove that
application's evidence contribution without removing surviving legacy/other
assertion support. Reclassify newly unsupported facts through the existing D74
deletion/belief eligibility rules; a surviving source claim alone cannot keep a
zero-support current fact. Its immutable original receipt remains historical and cannot
restore the link. Completed-but-erased support is handled uncertainty only with
the explicit verified checkpoint disposition; arbitrary missing support still
blocks readiness. Erased support is terminal for that application/adjudicator generation. New
assertions or explicit ingestion under a distinct adjudicator generation may
establish independent support through their own receipts; receipt retry cannot
reassociate erased support. This grants no new identity correction or model retry
path. Ordinary corrections do not change identity (D110 §4).
Replay restores linked/erased support roots before later ordinary effects.
Repeated forget rechecks the per-assignment proof recursively and scrubs any
newly forbidden handles/payloads; absent proof cannot become authority merely
because it was copied into an older checkpoint.

Hard forget cannot silently remove an admitted ordinal and let the next one pass.
Under the D74 fence, close an affected batch with an explicit
`retired_by_checkpoint_id` tied to the verified closed-block checkpoint. Completed
applications keep their original admitted coordinates. Only surviving **unapplied**
applications may have admission coordinates/preparations cleared for fresh closed
admission; no effect from such an abandoned attempt may have committed. Verify
the complete surviving pending assertion/generation/source-membership inventory
and include it in the checkpoint inventory hash; scrub forbidden attempts, and retain
the retirement witness before readiness reopens. Ordinary active admission never
clears or reorders coordinates. Replay skips the explicitly covered retired
admission rather than treating its reduced membership as ordinary completion;
new closed admission supplies the next recorded order. Provider responses arriving after
forget fail the exact application/attempt lookup and are discarded.

The stopped/drained T.1 conversion marks old unpinned D90 metadata explicitly
`legacy-unpinned:pre-d113`. It never assigns the running worker's generation as
historical proof. Current writers require exact pins and assertion/application
references; new-generation readiness cannot use historical unpinned completion.
Legacy staging has no fabricated receipt. Runtime cutover drains or explicitly
retires old work before current admission; reconstruction from published new-
generation normalization receipts creates the current memberships when required.

A crash before output publication follows existing cost/retry rules. A crash after
publication reuses the answer; after application it reuses the completed receipt
and repairs only its own work/barrier. Missing expected state, stale revision,
wrong generation or lost support owner fails closed with the existing typed
conflict/retry or corruption path. No custom poller or lease state is introduced.
Expose admitted batch/head, preparation fingerprint, completion and source/current
fact handles through existing internal inspection conventions; public open SQL
receives no grants to these tables or mutation capability.

The schema adds batches, applications, their adjudication junction and support
checkpoint roots, plus generation-qualified memberships and a legacy evidence
baseline. Storage grows with accepted assertions and their retained application provenance;
lock and re-split cost grows with the complete affected entity set. These costs
are required for exact recovery. A generic application store could reduce future
code duplication but currently adds migration and cross-plane authority complexity;
its [proposal](../proposals/generic_temporal_application_store.md) records an
adoption trigger. Removing separate assertion/support tables is the chosen
simplification, not reduced correctness scope.

## 7. Acceptance

Required proofs include unique and multi-statement claim identities; D56 reuse
without reattachment; independent normalizer/adjudicator/flush rolls; complete
and empty barriers; missing/substituted receipt/support refusal; concurrent
helpers and closed ordering; lock-free model calls, first-output CAS and stale
revalidation; overlapping/disjoint/undated occurrence identity; multi-candidate
state coexistence; late-arrival A/B/A history with reversed source/world clocks;
re-split of one of several statements from one claim; total group rollback;
exact replay of current-support moves; source erasure and repeated forget;
legacy evidence preservation and cap refusal, linked/erased support roots and
independent repeated-forget proof, legacy cutover, full supported PostgreSQL migrations and all consumer readiness.
Design acceptance alone satisfies none of these implementation gates.
