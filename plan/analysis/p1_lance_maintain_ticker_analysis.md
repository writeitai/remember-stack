# Analysis: drop ledger units for P1 Lance maintain

**Status:** non-binding analysis  
**Date:** 2026-08-14  
**Question:** Does OSS Lance require claimed `processing_state` work for
`optimize()` / `create_index()`, or is a locked ticker enough?  
**Related binding (amended after this note):** D93,
`plan/designs/p1_lance_maintenance_design.md`  
**Rejected-but-viable:**
[`plan/proposals/p1_lance_maintain_ledger_units.md`](../proposals/p1_lance_maintain_ledger_units.md)

## 1. The question

The first D93 draft modeled continuous maintain as an unlaned ledger stage
(`maintain_p1_index`) with table-scoped units, coalesce, attempt-fenced
complete/fail, reclaim, and a heartbeat side-thread. That draft was internally
consistent with D67. It is not required by Lance.

This note records why the simpler control plane is the better default.

## 2. What Lance requires (public docs, 2026-08-14)

| Fact | Source |
| --- | --- |
| Concurrent **writes** are supported; too many writers can exhaust commit retries | [FAQ — concurrent operations](https://docs.lancedb.com/faq/faq-oss) |
| `optimize()` is compaction + prune + **incremental** index update; run after large writes or on a schedule | [Performance — maintenance](https://docs.lancedb.com/performance), [Reindexing](https://docs.lancedb.com/indexing/reindexing) |
| While new rows arrive during reindex, queries use the old index plus a brute-force scan of the unindexed tail | [Reindexing](https://docs.lancedb.com/indexing/reindexing) |
| `optimize(retrain=True)` is a deprecated no-op on pinned `lancedb==0.34.0` | Engine API (same pin as D93 r4) |
| Full IVF/FTS retrain is `create_index(..., replace=True)` | [Vector indexes](https://docs.lancedb.com/indexing/vector-index) |
| `optimize(..., delete_unverified=True)` is only safe when **no other process** is working the dataset | LanceDB 0.34 `Table.optimize` docstring |

Lance does **not** require a job claim, attempt number, or heartbeat. The ops
are versioned dataset commits. If the process dies, the lock (if any) dies with
the session and the next tick can run the same idempotent op.

Writers (`merge_insert`) may run during `optimize()` and during
`create_index(replace=True)`. The new index may be immediately a little stale
(new rows sit in the unindexed tail). That is the product model, not a bug.
Commit collisions are retryable on the **writer** side.

The one hard exclusivity rule is **`delete_unverified` purge vs anyone else**
(forget). Two maintainers compacting or retraining the **same table** should
also be serialized so they do not waste CPU on colliding commits. That is
maintain-vs-maintain and maintain-vs-purge — not writer-vs-maintain.

## 3. What the ledger machine was solving

Once maintain is a D67 attempt, the protocol fights its own defaults:

- A normal job is minutes; heavy IVF is hours. `running` + a wall-clock stale
  cutoff will steal a live rebuild unless a heartbeat exists.
- Reclaim reuses the same `processing_id` and increments `attempts`, so a
  late `complete`/`fail` from the dead process must be attempt-fenced.
- Writer enqueue races completion, so `rerun_requested` must be consumed in
  the same transaction as success.
- Coalesce cannot be a partial unique index on ledger status (status lives on
  another table), so an advisory xact lock plus `SELECT … FOR UPDATE` appears.

None of that is a Lance constraint. It is the ledger talking to itself after
we modeled a filesystem call as a claimed attempt.

## 4. Alternatives

| Option | Shape | Cost | Verdict |
| --- | --- | --- | --- |
| **A. Locked ticker (chosen)** | One compose process; per-table try-lock; choose ensure / optimize / retrain; writers bump a stats row and never take the lock | One loop, one stats table, no reclaim | Fits Lance; matches forget lock already in PR2 |
| **B. Ledger units (first D93 draft)** | Unlaned stage, units, coalesce, reclaim, heartbeat, attempt fence | Large, review-heavy, easy to get CHECK/lock-order wrong | Viable if we later need many maintain replicas or DLQ semantics; not needed for self-host one-root |
| **C. Cron only** | `optimize`/`create_index` on a timer with no stats | Misses change-mass; cannot tell chunks from facts; no `awaiting_operator` | Too dumb for BEAM-scale tails |
| **D. Inline optimize on writers** | Status quo before PR1 | Multi-hour `label_lock`; fragment storms | Rejected (the incident) |

## 5. Recommendation

Amend D93:

- Keep bulk merge, skip-unchanged, index matrix, table advisory lock, writer
  exclusion from that lock, change-mass on a durable stats row, gates default
  off, vectors Lance-only.
- Replace “one unlaned stage + units + reclaim” with **one ticker process**
  that holds the **same** table lock as forget/finalizer, then picks at most
  one of {ensure, optimize, retrain}.
- Writers increment `p1_lance_table_stats` after a **vector rewrite**. They
  do not enqueue ledger work.
- Process death needs no reclaim: the session lock is gone; the next tick
  retries the idempotent Lance op.
- Move option B to a proposal with an adoption trigger (multi-replica
  maintain fleet, or a requirement that maintain failures share the D67 DLQ).

## 6. What would be wrong

- Holding the maintain lock to **stop writers** during optimize/retrain —
  not a Lance requirement; it would recreate the `label_lock` stall.
- Running `delete_unverified` without the table lock — corruption hazard.
- Two tickers compacting the same table without the lock — wasted trains.
- Calendar-only heavy — still wrong; change-mass stays.
