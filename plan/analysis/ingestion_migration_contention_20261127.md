# Ingestion sessions and migration lock contention

2026-11-27. A production deployment with ten PostgreSQL sessions per project
saw pool waits during concurrent ingestion and `55P03` lock errors on upgrades.
The session advisory lock around fact projection takes one session while its
catalog, meter and vector writer can ask for another. Corpus reconcile keeps a
repeatable-read cursor open while it copies objects, retaining read locks across
that network work. An upgrade that needs an incompatible DDL lock then queues
behind the open transaction. Readers arriving behind the upgrade can also wait.

## Which coordination can move

Fact projection needs to serialize both the decision to pay and the vector
write for overlapping `(deployment, kind, fact_id)` sets. Suppose document A
and fact repair B both need the same vector. Releasing the reservation after
A reads the missing vector lets B read the same missing vector before A's call
returns. A conditional write can reject one result but both requests were paid.
The current waiter reads eligibility only after A releases the reservation.

A committed ownership record could serialize live calls without a PostgreSQL
session. Its recovery rule changes the behavior. The work ledger can reap an
attempt whose renewal stopped while its provider call and PostgreSQL session
are both alive. Today a successor still waits for that session to release the
fact reservation and can reuse its vector. With a reservation that expires
with the work claim, the successor can submit a second call. An ownership
record that never expires can strand the fact after a worker dies. The model
provider port cannot ask a provider to return the result of a previously
submitted request by a stable key. Independently, the current system can pay
twice after a disconnected session or an uncertain provider response.

Corpus publication writes objects and shared navigation at stable paths. A
database check after a PUT or DELETE cannot undo that operation. The storage
port provides complete-object replacement and idempotent deletion. D155 already
permits one request issued before session loss to land late and repairs it by
digest. Moving ownership to an expiring claim would also allow takeover while
the original healthy lock session would still exclude the successor. Plane K
checks Git refs, but its session lease also serializes pending compilation
receipts, their recovery and publication with forget. A Git ref check alone
cannot prevent another worker from recovering a receipt before its push.

Corpus reconcile uses only the document IDs from its initial selection. It can
read the same ordered membership in one statement and retain just the IDs.
Each document's publication still re-reads current authority as before. Reads
and marker writes can also use the already pinned publication session and end
their transactions before object calls. This removes nested checkouts without
changing the object mutation order, the renderer or who may publish.

## Migration retries

The runner's `SET LOCAL lock_timeout` applied to advisory lock acquisition and
ended before Alembic read its revisions. A session setting on the dedicated
migration connection also applies in every revision and concurrent index
phase. Prefer short lock waits and bounded retries to one long wait behind a
worker. Restarting the Alembic pass from its recorded revision rolls back the
contended transaction and preserves earlier revisions. Retrying one statement
inside the failed transaction would require savepoints and would keep earlier
locks from that revision. Concurrent index phases can commit some work before
a timeout. D153 checks committed index definitions and rebuilds its own invalid
staging indexes, and the existing autocommit enum additions are idempotent.
Other SQL errors remain terminal. The previous release's read/write contract
still governs each migration under D160.
