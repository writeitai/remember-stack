# D114 application contract: normalized assertions, decisions and support

**Status:** implementation contract under D114; independent pre-code review
pending. Runtime activation remains subject to the delivery gates.
**Date:** 2026-09-07.
**Authority:** [one mutable window](mutable_fact_windows_design.md).
**Analysis:** [storage alternatives and existing mechanisms](../analysis/mutable_fact_application_storage.md).
**DDL:** [application storage](mutable_fact_application_schema.sql).

## 1. Scope and storage

A normalization response turns a source claim into zero or more relation or
observation assertions. An application decides what one resolved assertion means
for the fact store. Both planes use the existing entity flush work units: relations
also wait there instead of being upserted before adjudication. Historical internal
names containing `obs_flush` do not imply a separate relation queue is required.
The old downstream relation supersession step becomes receipt/projection follow-up;
it must not adjudicate those assertions a second time.

Two new internal tables have distinct purposes:

- `normalization_outputs`: the complete first published response for
  `(deployment, claim, normalizer generation)`. Publish before resolving/applying
  its outputs. The same publication freezes `accepted_outputs`, a sorted list of
  `{kind, ordinal}` pairs surviving the normalizer's deterministic gates. Retrying
  uses the same response and dispositions, including empty responses; later
  predicate changes cannot silently change which outputs that receipt admitted.
- `fact_applications`: one resolved output's staging, prepared inference and
  completion receipt. The natural unique key is deployment, claim, normalizer
  generation, output shape (relation/observation), zero-based output ordinal and
  adjudicator generation. A UUID application ID addresses the row; text, triples
  and dates never establish retry identity.

The only new fact data are `valid_precision` and `window_claim_ids`: the latter
records the evidence grounding the current complete window, not a seed or an
endpoint owner. It changes whenever that window changes. Existing evidence tables
receive `legacy_support` to distinguish links not represented by application
support pointers. New application-derived links set it false. No temporal
operation, discrepancy, checkpoint or cache-certificate store is introduced.

The complete normalizer output remains unmodified. Each application stores resolved
entity IDs and obtains the statement from its output ordinal. `uses_claim_window`
on each normalized output explicitly says the claim's world window applies to
that assertion; false means initial dates are unknown. The normalizer sees the
source timestamp, claimed dates and source text. This reuses its existing call;
it cannot propagate one claim date indiscriminately to every extracted assertion.

## 2. Admission and work completion

Keep existing claim/version barriers and entity units. Extend
`normalize_observation_staging` with `application_id` and use
`(deployment_id, version_id, application_id)` as its primary key; retain its
existing routing columns. Stage every accepted resolved output, including
relations. Filtering invalid/bare-head-noun outputs happens before registration
and is frozen in the publication's accepted-output list, with ordinary processing
audit for the rejection reasons. Completion checks this frozen list exactly.
A source normalization work item cannot complete until every accepted output and
all its currently known version memberships have been staged.

Under the canonical entity lock, admit a finite set of eligible unadmitted
applications. Eligibility uses the existing materialized entity-unit join and
excludes dead-letter units. Order by source `asserted_at NULLS LAST`, claim UUID,
output shape, output ordinal and application UUID. Assign increasing
`admission_sequence` values using the dedicated sequence in that order; admit
later arrivals only after previously admitted work. The sequence supplies durable
work order, not a world-time or seed authority. Sequence gaps are harmless.

Each helper processes the least admitted, unapplied application for that canonical
entity. It cannot skip an unfinished head because a model call is slow. Independent
entities proceed concurrently. The composed entity flush generation pins the
normalizer and both fact adjudication contracts. An old or mixed generation cannot
complete the new version barrier. Completion counts application IDs, not statement
strings, and verifies every member's applied receipt before terminal follow-ups.

Application completion retires every staged membership for that application in
the same transaction. The receipt remains for later memberships and retry. Acquire
version-barrier locks only after releasing application locks. Reused source chunks
can add new version memberships to an already applied assertion; those memberships
retire by verifying its receipt, without relinking evidence.

## 3. Preparation and application locks

Use READ COMMITTED transactions with this acquisition order:

1. Shared transaction advisory lock on `hard-forget:<deployment UUID>`; verify the
   existing forget/availability fence while holding it. Forget preparation takes
   this lock exclusively. No payload publication bypasses this check.
2. Shared identity lock `<deployment UUID>:identity-epoch`. Merge/unmerge retain
   their existing exclusive form. Resolve redirected subjects before choosing
   canonical blocks; release/retry if the required subject set changes.
3. Existing entity block locks `<deployment UUID>:obs:<canonical entity UUID>`,
   sorted by UUID if an operation has several subjects.
4. Consumed claims sorted by UUID, then relation rows sorted by UUID, then
   observation rows sorted by UUID, using `FOR UPDATE`; finally application rows
   sorted by UUID. An evidence update must hold its fact lock first.

Preparation selects candidates from both current and completed world intervals
of the relevant fact plane, without kind or overlap gates. Semantic ranking may
bound model inputs; exact text/triple equality is nomination only. Record the
bound and truncation, and reject operations involving unseen facts or evidence.
Fingerprint canonical JSON of: generation and resolved identity; normalized
assertion; source claims' text, timestamps, D41 fields and currency; supplied fact
values, system intervals, chosen windows/witnesses, contradiction groups and
support links; and the complete block candidate-ID membership. Canonical JSON
sorts keys and all set-like lists; it uses normalized UTC timestamp strings.
Candidate membership may be streamed for hashing without copying all source text.

A fresh attempt UUID identifies this exact snapshot. Store its fingerprint and
bounded inputs before releasing locks for remote inference. First complete answer
wins via `UPDATE ... WHERE attempt_id = :attempt AND decision IS NULL`. Publication
checks the forget fence and surviving input claims. A late reply cannot overwrite
a replacement attempt or recreate an erased row.

Application reacquires the same locks, then re-reads and recomputes the snapshot.
It never applies a snapshot read before a lock wait. Any changed consumed input or
candidate membership rejects the output without fact effects; replace the attempt
and infer from the new input. Equal complete domain inputs permit reuse; there is
no new per-endpoint ownership or operation-sequence authority. All fact-creation,
identity/support movement and legacy direct-write entry points use this block
protocol. Lifecycle currency updates lock claims; its fact changes and evidence
recounts lock fact rows and cannot bypass the guarded re-read. Forget drains
ordinary work and holds its existing fence through scrubbing/recovery.

The accepted answer, facts, support assignments, evidence aggregates, ordinary
transcripts and completion receipt commit together. Injected failure at any step
rolls back all of them. Successful retry returns the original result and checks
current support; it does not replay old date or support writes.

## 4. Ordinary decision shape and validation

The closed typed decision has these fields (all arrays default empty):

| Field | Exact content |
| --- | --- |
| `target` | `{fact_id: UUID|null, new_handle: string|null}`; exactly one is present |
| `new_facts` | `{handle: nonempty string, assertion_application_id: UUID}` entries; the supplied original assertion defines subject, predicate/object or statement |
| `window` | `GroundedFactWindow|null` for the identity target; contains the complete canonical window and nonempty cited claim IDs |
| `updates` | `{target: same fact reference, window: GroundedFactWindow}` entries |
| `support_moves` | `{application_id: UUID, expected_fact_id: UUID, target: same fact reference}` entries |
| `legacy_support_moves` | `{claim_id: UUID, expected_fact_id: UUID, target: same fact reference}` entries, only for explicitly supplied whole-claim legacy support |
| `contradict_with` | UUIDs of supplied existing facts incompatible with the target |
| `confidence` | number from 0 through 1 |
| `rationale` | nonempty string |

Handles, update targets and moved applications cannot repeat. References must
resolve to supplied existing facts or this answer's new-fact handles. The initial
target is the incoming assertion's support destination. All targets and moved
assertions stay in the incoming fact plane and canonical entity block; relation
predicate identity is taken from the retained normalized output. Identity across
relation/observation representations is not inferred by this date change.
 New fact handles are local names converted to stable UUIDs derived
from application ID and handle; a replay cannot mint different IDs. An absent
window replacement preserves dates; a supplied all-unknown window clears them.

Every window is already canonical `FactWindow`. Claim windows are canonicalized
once at input construction; partial raw endpoints use the D114 unit rules without
filling missing sides. Initial creation uses the claim window only when the
normalizer established that it applies. Every later replacement and every
additional cap/update requires a nonempty rationale and cited admissible claims
present in the prepared input. Each participant belongs to the deployment,
canonical subject and supplied candidate set. Distinct identities may overlap.

Do not mechanically min/max testimony, orient succession by publication order,
override identity by date mismatch, or limit changes to earlier starts/shorter
ends. A same named event with a corrected date can remain one fact. A proposed
empty interval is invalid. Below the existing confidence threshold, preserve
coexistence and record why rather than apply destructive updates or support moves.

Support moves identify the original application and its expected current target.
Its retained normalized assertion must be supplied to the adjudicator. Update its
current support pointer; preserve its immutable original result. Recount the
source and destination evidence links from all surviving applications, with
`supports` dominating `contradicts` for a fact/claim pair. Preserve `legacy_support`
when present. A legacy link can move only through an explicit whole-claim decision
that has inspected that complete claim; date position alone cannot move it.
Statements and their support cannot be silently reconstructed from a different
fact's display label. All affected participants commit atomically.

The existing adjudication tables record decision, before/after windows, cited
claims, full consumed-claim inventory and application ID in `features`. Relation
outcomes retain add/noop/supersede/contradict; both fact planes gain the generic
`update` transcript value when existing fact values change. This is a readable
audit label, not a new worker, correction workflow or temporal operation store. On success, clear the application's prepared inputs and
answer; retain structural original result and current support pointers.

## 5. Retrieval and derived data

Versioned `FactResult.validity` adds `valid_precision`; fact results add
`temporal_match: confirmed | possible`. It is a query-specific classification.
The query engine and P1 use the same predicates. Known boundaries that disprove
a match exclude it. An incomplete window that may match is conservatively
`possible`; confirmed requires a complete finite or explicitly open window.
History excludes known future starts and includes completed windows. Unknown and
partial facts remain possible candidates. Strict primitives and counts include
only confirmed matches and report possible coverage separately; top-k retrieval
never certifies an exact complete count.

Date-qualified profiles and K snapshots consume the chosen window and its
precision. Open and unknown-end wording differ. Fact/date/evidence changes enqueue
existing projection repair; no clock-driven profile timer is introduced. Generated
labels and vectors are invalidated when dates change so stale derived text cannot
survive a correct database update. Publish with existing input revalidation.

## 6. Erasure inventory and recovery

Normalization outputs and source-owned applications cascade when their claim is
forgotten. For surviving applications whose `input_claim_ids` overlap the purge,
clear prepared inputs, fingerprint, attempt and answer before removal, under the
fence. Applied structural receipts remain applied and cannot restore anything.
Scrub ordinary transcript features whose complete consumed-claim inventory
intersects the purge, including before-images and rationale; existing triggering-
claim-only scrubbing is insufficient for these new multi-source decisions.

A current whole-window witness intersecting erased claims loses its chosen dates:
set endpoints NULL, precision unknown and witness array empty, and invalidate all
labels/vectors/profile/K inputs derived from it. This conservative action does not
claim the surviving evidence is false. Retain shared fact identity and independent
support according to D74; erase source-derived statement text through its existing
purge/reconstruction contract as well. Evidence aggregates must agree with remaining
application pointers and legacy support. No prepared fingerprint, cited UUID array,
cache or restored backup may reintroduce erased content. Verification includes
pending replies, retries, partial purge failure and restored older manifests.

## 7. Existing-store cutover

The migration requires stopped serving and drained old intake/workers/staging.
It adds the structural stores and marks the fact generation unready; it must not
silently assign unknown precision beside old source-time endpoints and serve them.
Clear ungrounded legacy windows under the fence, retaining IDs/system history and
legacy support. Reprocess retained source claims with the new normalizer and
ordinary adjudicator using existing work/replay facilities, with existing fact
identities available as candidates. This is in-place fact conversion, not source
re-extraction. Withdrawn historical facts retain their system closure; conversion
must not revive them. Already recorded source removal does not become a world end.

Replay is bounded by existing claim/entity work units and idempotent application
receipts. A crash resumes missing work; it cannot reopen readiness from a partial
store. New fact, retrieval and derived-content generations switch together only
when all expected source applications, evidence accounting and projection repairs
are verified. The current consumer cutover is a strict gate. Experimental archived
D110 schema heads fail with an explicit recovery requirement, never an automatic
downgrade. Conversion costs model calls on retained claims and must expose those
through existing metering; tests do not authorize running it against production.
