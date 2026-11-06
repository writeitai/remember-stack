# Response to WP-ET.7 review round 3 (PR #506, reviewed head `dc44041f`)

Review: `REVIEW_gpt-6.1-sol_d140_wpet7_r3_2026-10-21.md` (Sol, request changes: three P1s, no
P2s, no nits).

R3-P1-1 and R3-P1-3 are fixed together, by one rewrite of the ordinary-apply period watch.
R3-P1-2 is waiting on an owner decision: its behaviour is unchanged in this round, as the
coordinator directed.

Each new test **fails on `dc44041f`'s engine sources** and passes on the new head. Both runs
were on PostgreSQL 19, with `src/rememberstack` replaced by `git archive dc44041f`; no shim
was needed.

| Finding | Fix | Test (result on `dc44041f`) |
| --- | --- | --- |
| **R3-P1-1:** the watch can still certify periods the validated decision never saw | The watch is now **anchored before final validation**. It is read from the prepared inputs, which the final validation must match, and it is **one SQL statement**, so all of its parts come from one database snapshot. It returns which candidate facts are already stale, one digest over the periods of every claim the bases can cover, and the shown claims' own periods. Final validation now also requires that the validated snapshot's shown periods equal the watch's. If they differ, a writer committed between the two reads, so the decision is discarded and prepared again. Bases are then recorded only for changed facts that were not stale. Afterwards the digest is re-read over the same claims (an application only adds or removes evidence of shown claims), and any difference rolls the application back. | `test_a_correction_after_final_validation_is_never_certified`: the reviewer's first interleaving, a correction committed right after apply's final snapshot read. The retry decides on the corrected periods. On `dc44041f` it certifies the open window. `test_a_correction_inside_the_watch_is_never_certified`: the reviewer's second interleaving, a correction to unshown evidence between the stale check and the period read. The fact's basis stays stale and the fan-out queues its review. On `dc44041f` the corrected basis is certified and no review is queued. |
| **R3-P1-3:** the watch materialises all evidence in the worker | The whole comparison happens in SQL. Only three things reach the worker: a digest, the stale ids (at most the candidate count) and the shown claims' periods (at most 100). Evidence coverage is complete: every evidence claim of every candidate, both stances, with nothing truncated. | `test_the_period_watch_stays_bounded_over_5001_evidence_claims`: one candidate with 5,001 unshown dated evidence claims. The watch's peak traced worker memory must stay under 256 KiB, and a later change to those claims' periods must still be detected. On `dc44041f` the peak was **4,684,364 bytes**. |
| **R3-P1-2:** period-only edits still re-embed a profile that shows the fact | **Owner decision pending.** Behaviour is unchanged (option (a): a profile is re-embedded only when its rendered text changes). The estimate for option (b), a date-free embedded profile text with the dated text shown for display, is in the PR body and the coordinator report. | — |

The two round-2 ordinary-apply tests, and every other D140 proof, still pass. Design §8.2's
paragraph "an ordinary application certifies only what it saw" now describes the four-step
protocol: watch, validate, record, re-check.
