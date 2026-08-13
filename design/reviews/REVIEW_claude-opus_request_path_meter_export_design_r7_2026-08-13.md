# REVIEW r7 — Claude

**Verdict: APPROVE_WITH_NITS**

## r6 leftover — resolved

All four specified items are present and mutually consistent:

| Item | Location |
|---|---|
| bound = `min(cursor.horizon_at_issue, request_horizon)` | §5.2:519 |
| poll `NC1` after wait → still empty (frozen `H1`) | §5.2:548–549, §8:811 |
| poll `NC2` → row appears | §5.2:550, §8:812 |
| latency = `poll_interval + safety_lag` | §5.2:550–551 |

I traced the sequence against the stated cursor rule (`next_cursor` = last/incoming/zero key + **this** request's horizon). Empty page at `t0` → `NC1 = (zero, H1=t0−60s)`. Row inserted at `t0+ε`, so `occurred_at > H1`; polling `NC1` bounds at `min(H1, fresh) = H1` → correctly empty, and that same response mints `NC2` with `H2 = t2−60s > t_insert` → row appears. The off-by-one poll is gone, and §5.2:551–553 states *why* (a cursor issued before the wait can't absorb a future horizon without breaking replay) rather than just asserting the sequence. §8:811–812 mirrors it as an executable test.

Replay exactness also holds under the writer barrier: any row with `occurred_at <= horizon_at_issue` committed at most 15s after its stamp, i.e. ≥45s before `server_time`, so it cannot arrive between two replays of the same cursor.

## Nits (non-blocking)

1. **§5.2:505–507 — pin the statement order.** "`server_time` and `request_horizon` come from the same database `clock_timestamp()` inside that snapshot" doesn't say *when* inside. In Postgres the REPEATABLE READ snapshot is acquired by the first statement, so if `clock_timestamp()` were read *after* a long receipts query, `request_horizon` could outrun the snapshot; a row committed post-snapshot but stamped under the horizon would be invisible while the cursor advanced past it. The margin before that bites is 45s (`safety_lag` − barrier), and the design's own data flow already forces the safe order — you need `request_horizon` to build the `WHERE` predicate. One sentence makes it binding rather than incidental: *"`SELECT clock_timestamp()` is the first statement of the export transaction; it establishes the snapshot."*

2. **§8 — no test for the corrupt/future-cursor clamp.** §5.2:519–520 claims "a corrupt/future cursor cannot skip rows," which is exactly what `min()` buys. The empty-page test exercises the frozen-bound direction only. Add: cursor with `horizon_at_issue` in the future → bound clamps to `request_horizon`, rows past it are not skipped.

3. **§8 — duplication.** Line 813 ("same cursor twice with an insert between → identical receipt ids") and line 824 ("same cursor replay → same `cost_id`s") are the same test; likewise 812 and 825 partly overlap. Collapse to keep the list a checklist rather than a log of review rounds.

No implementation performed.
