# Fact projection concurrency without a project-wide embedding lock

Non-binding analysis, 2026-11-11. The owner requested optimization after a
fresh eight-task ingestion run exposed serialized fact labeling. The current
binding write/readiness contract is
[PostgreSQL P1 §7](../designs/postgres_p1_search_projection_design.md#7-writes-and-readiness).

## Observed behavior and the reason for coordination

`FactCatalog.label_lock` on main `8d365c626` acquires the session advisory key
`<deployment_id>:label-facts`. `LabelFactsHandler.handle` holds it across the
label sweep, external embedding requests and natural-row vector writes.
Thus independent facts in one project queue behind a single provider call.
The database transaction is closed before provider I/O, but the lock remains.

Coordination has a real purpose. Different documents and fact-application
receipts can refer to the same fact. They must not independently pay to embed
the same missing vector. Label checkpoints and the conditional writes in
`PostgresP1Index.upsert_facts` also prevent stale or deleted source rows from
receiving an older vector: accepted label text and validity must still match.
Removing the mutex while retaining only those conditional writes would
protect stored state but permit avoidable duplicate provider charges.

The fresh Full-v43 conv-42 run is frozen on `de44762a9`; this optimization is
not part of it. At 10:56 UTC it had no dead letters. Earlier label retries
were `ProfileRefreshContendedError` from changing profile inputs, all then
recovered on attempt two. That post-label profile refresh is outside the
project label lock already. Narrowing fact coordination does not remove the
separate need to avoid committing a vector for an outdated entity profile.
Operational evidence is in cloud PRs #932/#935 and the native run archives.

## Selected approach

Discover the fact IDs that still need either a label or an embedding, using
the existing selectors and the work item's document, application or fact
scope. Deduplicate and deterministically order the `(kind, fact_id)` set.
Process bounded batches, no larger than the configured embedding batch size.

For each batch, acquire session advisory locks named by project, fact kind
and fact ID in a consistent order. Re-read label/embedding eligibility after
acquiring them. A competing job that already completed a shared fact makes
that fact a cache hit; no provider request is needed. Unrelated facts acquire
different keys and can reach the provider concurrently. Hold only that
bounded fact reservation through provider I/O, with no open SQL transaction.
Persist the existing conditional label and atomic vector/input attestation,
then release the batch before taking another one.

Do not lock every fact ever associated with a document. Already embedded,
shared facts would unnecessarily serialize otherwise independent work.
Do not expand a batch to newly discovered facts after acquiring its locks.
A concurrent fact application owns its own receipt-scoped projection wake;
the current pass only processes the IDs it reserved. Retain profile refresh
for the original work scope even when no fact vector remains to embed, so a
retry can finish a failed profile projection without re-embedding facts.

Release all attempted keys on failure, including partial acquisition; a
disconnected PostgreSQL session also releases its locks. Do not keep the old
project-wide locking path, a compatibility flag or a lease/coordinator table.
Upgrade label workers together when changing the lock namespace; the frozen
validation continues on its original workers. This requires ordinary worker
replacement, not rollout history, recovery or rollback orchestration.

## Alternatives and limits

| Alternative | Assessment |
| --- | --- |
| Keep one project lock | Correct overlap exclusion, but serializes independent facts and provider waits. |
| Remove locking and rely on conditional writes | Stale writes remain rejected, but two jobs can pay for the same embedding. |
| Lock by document/application | Unsafe for deduplication when different scopes share a fact. |
| Hold authority row locks during provider calls | Blocks authoritative fact mutations for a network wait. |
| Add persistent per-fact leases and a paid-result cache | Adds schema, uncertain-call settlement and expiry machinery to solve overlap already expressible with bounded session reservations. |
| Lock only pending facts in bounded batches | Preserves overlap deduplication, current checkpoints and conditional writes while allowing independent facts to proceed. Selected. |

This does not promise eightfold throughput. Overlapping facts, provider
capacity, SQL admission and independent entity-profile refreshes still limit
it. Each active reservation owns one database connection, as the old lock
did; provider I/O holds no database transaction or authority row locks.
Additional eligibility reads are bounded by the work scope, not corpus size.
Unchanged cached embeddings remain free. Crashes and uncertain provider
responses can still cause at-least-once charges under the existing retry
contract; this is not an exactly-once billing claim.

## Reuse, failure and proof

No label text, label generation, embedding model, input-policy version or
input hash changes. No chunk, extraction, normalization or paid-work reuse
generation changes. Existing vectors need no rebuild and completed work is
not re-enqueued just to change coordination. D56's unchanged-region Selection,
Claimify, claim-ID and vector reuse boundaries remain unchanged.

Use real PostgreSQL tests to prove that disjoint facts in the same project
can enter a blocking provider together; overlapping facts cause one paid
call and the second caller observes the durable result; opposite input
orders do not deadlock; provider failures release reservations; and changed
or deleted fact rows still reject a stale paid vector. Verify relation and
observation paths, model/label generations and batches. Run the required
two-version ingestion reuse proofs without weakening known D122 expected
failures. Offline tests prove coordination and correctness, not benchmark
accuracy or a production-scale latency result.
