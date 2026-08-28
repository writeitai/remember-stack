# D99 proposal-convergence lock convoy

**Date:** 2026-08-28

**Status:** non-binding analysis

**Scope:** the proposal-only convergence throughput failure observed while
validating RememberStack v0.7.1 on LoCoMo `conv-26`. This note does not select
an identity-quality threshold, enable automatic merging, accept a review
proposal, or make the incomplete run a benchmark score.

## Finding

D99's first lock follow-up removed the exclusive deployment identity-epoch
starvation, but a different convoy remained. A report-only clustering pass
could hold hundreds of entity evidence locks while waiting for one evidence
lock held by a slow observation transaction. The deployment-wide clustering
lock then put every other profile-triggered nomination behind that pass.

The result was safe from a stale merge and did not deadlock, but throughput was
not acceptable. At the retained stopping point, four observation workers had
moved only six jobs in roughly eight minutes while hundreds remained. A review
proposal does not justify making fact/profile publication wait this way because
proposal generation cannot mutate identity.

## Run evidence

| Field | Value |
| --- | --- |
| Host | `umc-locomo-bench-01` (`178.104.208.70`) |
| Compose project | `locomov071c26` |
| Deployment | `9310e065-2776-4871-acae-a63a60c48c37` |
| Release | v0.7.1, `037ebe16e75fb70ddbefe56f7ee5664cc4c2d6f5` |
| Run | `.benchmark-runs/locomo-v071-conv26-smoke` |
| Protocol | `RS-LoCoMo-Full-v16`; 19 sessions, eight selected questions |
| Stop state | 539 busy observation jobs; zero dead letters |
| Recorded pipeline cost | USD 3.3549 |

The retained database had 605 active entities: 338 exact `Caroline`, 239 exact
`Melanie`, and two `LGBTQ conference` fragments. Current resolution decisions
contained 585 provisional and 464 authoritative outcomes. The deciding-tier
split was 20 T0 mints and 1,029 T4-small decisions: 585 mints and 444 links.
T3 again made zero decisions. At the sampled point, the review queue contained
18 pending and seven auto-resolved merge proposals.

PostgreSQL's live blocker graph showed the convoy directly:

1. An observation transaction held one `deployment:obs:<Caroline entity>`
   advisory lock while its worker was outside PostgreSQL doing provider-backed
   work.
2. A proposal-only convergence transaction had already acquired the shared
   identity epoch and hundreds of globally ordered observation locks, then
   waited for that Caroline lock.
3. Other convergence/profile workers waited behind the clustering transaction
   or one of its acquired member locks.
4. Statement timeout eventually unwound a pass, but the next profile-triggered
   nomination recreated the same convoy.

There were no `identity-epoch` timeout errors. This distinguishes the problem
from the v0.7.0 failure fixed in v0.7.1.

## Why ordering was necessary but insufficient

Globally sorting the union of member identity closures prevents two passes from
acquiring overlapping entity locks in opposite order. It therefore prevents a
lock cycle. It does not prevent a large pass from acquiring many locks and then
waiting a long time for the next one. Correct ordering solved deadlock risk but
not convoy duration or redundant repeated work.

## Selected correction

Proposal-only convergence is best-effort, coherent-snapshot report generation:

- coalesce duplicate nominations with a nonblocking lock keyed by deployment
  and normalized lemma;
- try every globally sorted member evidence lock and end the nomination
  immediately if any member is busy; and
- rely on the later successful profile publication to nominate the same
  neighborhood again.

The liveness argument is local. If pass A owns the lemma lock while profile
publisher B still owns an evidence lock, A defers. After B commits its profile,
B invokes convergence and can acquire the released lemma lock. If A sees B's
committed profile and acquires every evidence lock, its coherent proposal makes
B's duplicate nomination unnecessary.

Automatic merge is deliberately unchanged: it retains the global blocking
neighborhood lock, exclusive identity epoch, and blocking member evidence locks
because it can split or merge identity authority.

## Alternatives considered

| Alternative | Reason not selected |
| --- | --- |
| Raise the statement timeout | Makes the convoy longer and does not remove redundant work. |
| Add a new convergence queue/worker/debouncer | Can coalesce durably, but adds machinery before the simple try-lock contract is disproved. |
| Serialize benchmark workers | Hides a production concurrency failure and invalidates the established-workload comparison. |
| Disable profile-triggered convergence | Restores the pre-D99 lifecycle gap. |
| Let proposal-only passes block in global order | Deadlock-safe but reproduced the observed multi-minute convoy. |

## Limitation and next measurement

This correction addresses throughput and retry amplification only. It does not
make 585 provisional fragments automatically become two people. With
`auto_merge_enabled=false`, the ordinary v16 score may remain fragmented until
an operator accepts a proposal. The next fresh run must separately report:

- drain duration, contention retries, and dead letters;
- active Caroline/Melanie fragments and T3 outcomes;
- final pending proposal membership and blast radius; and
- the ordinary no-acceptance score before any clearly labeled post-review
  diagnostic.

## v0.7.2 validation follow-up

The fresh v0.7.2 `conv-26` run used Compose project `locomov072c26`, deployment
`8e874002-50e1-40bf-816b-7c80431ce2b1`, and exact release commit
`009f868af732c58387f364febeee418825c46715`. The original proposal convoy did
not recur: observation waves advanced and broad proposal passes deferred when
they encountered an already-busy evidence lock. Short waits still appeared in
the opposite direction while a proposal transaction that had acquired all of
its try-locks completed database-only review work.

The run nevertheless stopped safely with one `adjudicate_supersession` dead
letter. Its profile-refresh step exhausted all three work attempts waiting on
an observation lock held by provider-backed adjudication transactions. The
profile refresher deliberately bounds this wait with `statement_timeout`, but
the resulting PostgreSQL `57014` advisory-lock cancellation escaped as generic
`OperationalError`; the evidence-worker boundary therefore could not apply its
existing safe `ProfileRefreshContendedError` policy. At stop, the driver had
reported one dead letter and preserved the stack; no score was produced.

The narrow follow-up is to translate only SQLSTATE `57014` cancellations whose
cause names an advisory-lock wait inside profile snapshotting into the existing
typed profile-contention outcome. This does not swallow slow queries,
connection failures, or other database errors. Authoritative facts are already
durable at that boundary, and the profile is disposable/fail-closed, so replaying
paid normalization is not required; later evidence publication owns another
refresh attempt. A real PostgreSQL regression proof holds the entity evidence
lock on one connection and verifies the contender receives the typed outcome.
