# Analysis: heavy maintain must stay autonomous (no human-escalation flag)

**Date:** 2026-08-14  
**Status:** non-binding analysis; the binding change is D93 autonomy amendment  
**Question:** when IVF retrain cannot finish because writers never quiet, should
the engine stop and raise a “needs a human” flag?

## The question

Lance IVF retrain (`create_index(..., replace=True)`) can run for hours. Official
OSS docs allow concurrent writes; they also warn that too much concurrent write
traffic can fail a commit. Compact (`optimize()`) is cheaper and can still run.

D93 r1–r4 answered a *different* honesty question: do not promise that a
multi-hour retrain will *eventually* succeed while ingest never stops. The
chosen mechanism was a durable `operator_state = awaiting_operator` bit plus a
`writer_gate=hold` so a person could create a quiet window.

That mechanism is a product mistake for RememberStack.

## What “needs a human” actually does

Once `awaiting_operator` is set, continuous retrain **stops** until someone:

- flips the flag off, or
- holds writers (`writer_gate`), force-runs one heavy, then releases the hold, or
- decides to live with a stale index and leaves the flag set forever.

Compact may still run. Search still works (Lance reads the unindexed tail). The
only thing the flag adds is a **terminal stop** that a person must notice.

Self-host RememberStack is an unattended engine. There is no on-call rotation
for “IVF is a bit stale.” A flag nobody is watching is a silent stop with extra
steps. A flag someone *is* watching is an ops product we did not agree to
build.

## Alternatives

### A. Terminal `awaiting_operator` + optional writer hold (current binding)

**Pros.** Cannot thrash 8 full trains per minute; the stop is visible in SQL.  
**Cons.** Progress requires a human. The default path if nobody acts is “stay
stopped.” That is an ops console, not an engine. **Reject.**

### B. Keep retrying with backoff; never stop for a person (chosen)

Rate-defer when recent write rate is high. After a post-train commit conflict,
wait a long interval (minutes, not sub-seconds) and try again on a later tick.
Cap the backoff. Compact and ensure keep running every time they are due.

When an ingest wave ends, write rate drops, and the next tick retrains by
itself. No bit to clear. No hold gate.

**Honesty that remains:** if writes *never* quiet, full IVF retrain may be
deferred for a long time. Compact still folds tails into the *existing* index.
Search stays correct. Quality is best-effort, not a page.

This is not fake eventual-success. It is “the engine keeps trying whenever
conditions allow.” That is the same contract as compact today.

### C. Automatically pause writers when retrain starts

Would raise the chance a train commits, but it stalls embed/label — the path
D93 already forbids expanding (`label_lock` across `create_index`). Concurrent
writes are allowed. **Reject** as a required mechanism. Writers stay outside
the maintain lock.

### D. Always retrain when due, ignore write rate

Simplest. Under BEAM-scale continuous ingest it will collide, retry, and burn
hours. The *defer* part of B is still needed. Autonomy does not mean
spin-forever at full cost.

## Compact vs rebuild (retrieval speed)

`optimize()` incrementally attaches newly written rows to the **existing**
index. Each pass can add another indexed piece. Ten optimizes can mean ten
extra index pieces: better than a brute-force tail, worse than one rebuilt
index. Official docs call this incremental index update, not a full retrain
([reindexing](https://docs.lancedb.com/indexing/reindexing), 2026-08-14).

So:

- Run `optimize()` after a **bound of new/changed rows** (or fragment dirt).
  Not on a tight “as often as possible” loop.
- **Do not postpone `create_index(..., replace=True)` indefinitely** because
  ingest is still running. A never-ending ingest would otherwise leave a
  growing stack of incremental index pieces and retrieval would keep getting
  slower.
- Short conflict backoff (minutes after a lost train commit) is still
  allowed. “Writers are active, wait until they stop” is not. Ingest can
  run for days.

Retrieval speed is a first-class constraint. Autonomy means the ticker
keeps scheduling rebuilds itself. It does **not** mean compact-only forever
while a wave never ends.

## Decision this analysis supports

- No `awaiting_operator`, no “needs a human” state, no required `writer_gate`.
- Autonomous policy is **backoff and retry**, not **stop and page**.
- `optimize()` after a row/fragment threshold is enough light work; it is
  not a substitute for periodic full rebuild.
- Full rebuild must fire on durable change-mass / growth **even during
  sustained ingest**. Rate-defer is only a short anti-thrash after a
  conflict, not “defer until quiet.”
- Schema columns already shipped (`operator_state`, `writer_gate`) stay unused
  (always null / `run`) and may be dropped later. Do not write them.
- PR4b, if built, is only: short conflict backoff, then try rebuild again
  (writers still running). No escalation ladder. No wait-for-ingest-to-end.

Adoption trigger for bringing A back: a staffed ops product that *wants* a
ticket queue for index quality. That is not this engine.
