# Temporal follow-up contracts: synthesis of the independent analyses

**Status:** non-binding synthesis for the D107/D108 amendment. 2026-09-06.
**Baseline:** main `1a909768`, including D108 implementation in PR #380.

The independent analyses are `temporal_relation_staging.md`,
`temporal_autonomous_corrections.md`, and `temporal_cache_and_forget.md` in
this directory. This note resolves their alternatives before writing the
binding amendment. The intended outcome is the complete D107 temporal program;
build order remains in `plan/plans/temporal_clocks.md`.

## Authority and scope

D108 retires the public human review queue and describes autonomous truth
maintenance. D107 §4.3 requires a human review verdict. Both cannot determine
the same correction. Preserve D107's distinction between immutable source
claims, an adjudicated fact window, and derived occurrence metadata, and replace
its human correction mechanism with an explicit autonomous adjudication.

A date earlier than the current start is a reason to consider a correction,
not permission to take the minimum of evidence dates. The existing semantic
ladder must establish that the testimony refers to this fact. It selects
precomputed canonical endpoints backed by eligible claim IDs. An uncertain
result is complete uncertainty, not an unresolved task for a human to approve.

This choice adds no arbitrary edit API and does not make a new public function
that can mutate memory. Open SQL continues to expose only read contracts.

## One write history, several input receipts

Normalization receipts, relation-application receipts and temporal operations
answer different questions: what complete normalization answer was accepted,
whether an assertion has been adjudicated, and which atomic fact mutation
actually committed. Keep these roles explicit. Existing adjudication tables
retain narrative/model/confidence authority; temporal operations provide typed
before/after receipts and ordering. Do not create another narrative
`temporal_window_verdicts` table.

Relation batches preserve finite admitted input sets and ordinal order.
`temporal_blocks` supplies the only mutation sequence/revision shared by
seeding, evidence, caps, source removal, corrections, migration and identity
changes. Batch ordinals order input application, not a second competing clock
for replaying corrections. Every assertion may emit several adjudications and
fact operations; a result must not be squeezed into one adjudication ID.

The existing work ledger is the only lease/retry owner. Remove the alternative
lease, retry-counter and next-attempt columns from the correction analysis's
DDL when promoting it. A discrepancy is a durable target ID, with a processing
row; a boundary refresh is a different durable target ID, with a processing
row. Do not poll either domain table as a second ready queue.

## Locks and inference

The analyses agree that locking only the fact is insufficient: another writer
can insert a neighbouring slice. All temporal writers must follow the same
order: deployment acceptance/forget fence, identity epoch, sorted logical
blocks, sorted facts, sorted cache-source keys. Barrier locks are acquired
only after releasing apply locks. Implementation must audit and update the
existing inverted callers together; adding a helper used only by corrections
cannot establish this guarantee.

For relation batches, the ordering analysis proposes retaining a dedicated
session advisory lock across model calls. A bounded alternative is to freeze
the head assertion durably, prepare under the block lock, release locks for
inference, then revalidate the full block and fact revisions before apply.
Only the least unapplied admitted ordinal may commit a relation identity
verdict. Corrections and source removal can commit between assertions and
invalidate a prepared answer; they cannot reorder two relation assertions.
This preserves ordered relation application while avoiding a long-held
connection/lock during remote inference. It requires explicit revision checks
on every writer and stored prepared output for crash recovery; otherwise the
session-lock design is safer. The binding synthesis must pick one and specify
its crash/retry behavior, rather than describe them as interchangeable code.

## The honest ordering guarantee

An immutable seed chosen today cannot depend on an earlier-dated source that
arrives tomorrow. D90's current implementation drains the presently available
staging set and does not establish invariance across arbitrary admission
histories. Adopt deterministic order within each closed admitted set plus
exact replay of recorded admission/application history. A complete finite
import must finish its declared membership before admitting that set.
Different continuous-ingest admission histories may choose different seeds;
world-time chronology and late-arrival re-splitting still follow D107.
Global identity invariance across unseen inputs would require retractable
identity/seed decisions and is a different design, not a batch-size setting.

## Cache freshness is a checked property

Profiles are generated entity descriptions and their embeddings, consumed by
identity resolution and search. K pages are generated knowledge artifacts.
A timer can arrange refresh, but cannot prove the old bytes remain true when
the timer is late. Each generated cache therefore needs an evaluation instant,
a deadline, a generation and a complete dependency revision certificate.
Readers validate it before consuming text or vectors. Late work means a
missing/stale cache and fresh fact fallback, never a false current summary.

Include future facts before top-k selection and future routing (`part_of`)
before a page begins matching them. Parent pages inherit child source
certificates, not merely unchanged child text hashes. Source minima require
indexed membership so a removed earliest boundary does not trigger an entire
corpus scan. Reuse the existing profile/K writers and work ledger.
Distinct event IDs are necessary because processing work uniqueness omits
content_hash and completed work is not resurrected by another enqueue.

## Forget is an extension of an existing invariant

Hard forget already accepts an inventory, drains conflicting writers, scrubs
the spine/projections/artifacts, and verifies residuals. Every new receipt,
shadow, canonical date projection, dependency, label and vector joins that
inventory in the same implementation. Nulling a seed pointer is insufficient
if the forgotten source's date remains in the fact or a before/after receipt.

Recompute surviving occurrence metadata. For verdict endpoints whose authority
was erased, apply a recorded sanitized erase checkpoint using independently
surviving support/operations; never infer new authority from historical min/max.
Preserve independent later caps and belief-time invalidation. The checkpoint
must sever dependencies on erased history and become the replay starting
point for that surviving fact; replaying scrubbed predecessors is forbidden.
The exact checkpoint schema and retained-content rules belong in the binding
contract and D74 inventory before implementation.

Use manifest v2 for separate affected surviving relation/observation IDs;
existing `fact_ids` means exclusive facts to delete and must retain that
meaning. Preserve accepted v1 canonical bytes/hashes exactly. A v1 restore
still runs the new inventory discovery from its claim/document identities.

## Required D107 textual repairs

- `unknown` is the only uncertain shape; `undated` is not an enum value.
- Undated occurrence-shaped relation claims follow occurrence matching;
  they must not be forced into the generic undated-state shortcut.
- Relation idempotency is assertion/generation grain because one claim can
  yield several distinct triples.
- CHECK constraints cannot enforce old/new write authority or inspect an
  operation log; typed transactional writers enforce those invariants.
- D55 refuses an invalid cap by preserving existing endpoints, including a
  finite end, and always recording belief-time closure at the persisted
  reconciliation instant. Refusal cannot erase an independent prior cap.
- Replay uses committed dependency order, not all caps followed by wall-clock
  review timestamps.
- Completed migration with explicit legacy/unknown diagnostics may be ready;
  incomplete conversion or replay conflicts cannot be ready. No human queue
  acceptance gate is reinstated.

## External mechanism verification

PostgreSQL official documentation inspected 2026-09-06:
[advisory locks and deadlock ordering](https://www.postgresql.org/docs/current/explicit-locking.html),
[constraints](https://www.postgresql.org/docs/current/ddl-constraints.html), and
[transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html).
Session locks survive transaction rollback; transaction locks do not. At Read
Committed, successive statements may see different snapshots. CHECK constraints
do not provide cross-row invariant enforcement. These mechanisms support the
chosen protocol only when every relevant writer participates.


## Final cross-review resolutions (2026-09-07)

Independent review of the binding draft found an output/snapshot race that
simple fact revision revalidation would not prevent: a late worker could store
its old response beside a helper's new snapshot. The draft now pins a preparation
attempt UUID/fingerprint and immutable snapshot/output pair, uses first-output
CAS, and records a stale outcome before replacing an attempt. Existing logs
hold the audit; no second work queue is added.

The active relation batch is unique across adjudicator generations, not one
per generation. The next generation cannot race its own head ordinal against
an undrained older batch. Atomic assertion effects may depend on preceding
effects in their same transaction; replay preserves that group boundary.
All read/write blocks, including empty candidate blocks, have explicit
footprint/completeness records. Read witnesses advance the sequence but not
the mutation revision. These resolve the concrete findings recorded in
`temporal_autonomous_corrections.md` §11.

The final schema uses the existing endpoint basis field for `erased` and
conservative whole-operation support/checkpoints rather than a new per-component
proof engine or bitmask. Its exact predecessor-schema probes and limits are
recorded in `temporal_relation_staging.md` §8.4. That execution found and verified
the corrected conversion ordering; it does not certify a full running store.
