# Response to WP-ET.7 review round 4 (PR #506, reviewed head `3b676c17`)

Review: `REVIEW_gpt-6.1-sol_d140_wpet7_r4_2026-10-21.md` (Sol, request changes: one P1, no
P2s, no nits). The profile-embedding question (R3-P1-2) is still waiting on the owner and is
untouched.

## The fix is structural: one consistency rule for every period-dependent read

R4-P1-1 was the third race in one family. In every case, a value was certified on the
strength of two reads that agreed across separate READ COMMITTED statements. A writer could
commit between those statements, and an A → B → A sequence could repeat the same torn
combination. So the fix is a rule, not another special case:

1. **One read.** Every period-dependent input is read by **one SQL statement**,
   `read_period_inputs_on`, so the whole read sees one committed state. That covers current
   bases, stale facts, the shown claims' periods and the digest over all coverable evidence.
   Preparation and final validation both use it, in:
   - the ordinary-apply watch and its post-write verification;
   - review preparation and validation;
   - the lineage fan-out's comparison of stored and current bases.
2. **Certify only that read.**
   - A review stores the basis from its validated read, which is also the read that produced
     the periods it showed the model.
   - An ordinary application stores the bases from its post-write verified read, and only if
     that read's digest equals the digest read before final validation. The earlier "record,
     then re-check" sequence is merged into this single read, so no gap remains between the
     basis stored and the check that justifies it.
3. **Order with the fence.** A writer that commits after the certifying read leaves that
   basis stale. The fan-out's exclusive barrier makes the writer's fan-out wait for the
   certifier to commit, so it sees the stale basis and queues the review.

### Why period writers do not take the fence (a deliberate deviation from the coordinator's step 1)

I checked what changes in-force intervals:
- **Declarations, clears, version and lineage deletions, and readiness and current-pointer
  moves.** All of these pass through one choke point: the deferred trigger function
  `refresh_document_version_scope`. They would have been cheap to fence.
- **Occurrences.** `_MARK_VERSION_READY` makes a version's representation current at
  structuring time, *before* extraction. So every `chunk_claims` write that extraction makes
  for that version changes the in-force union of the claims it reuses.

Freezing periods for the length of a decision would therefore need one of two things:
- an exclusive per-deployment lock in every extraction write of a periodised deployment,
  which serialises extraction deployment-wide (all of a legal corpus) and with every
  adjudication; or
- shared per-lineage locks on every lineage a decision's evidence touches. That number is
  unbounded (one candidate fact can have thousands of lineages) and can exceed
  `max_locks_per_transaction`.

Fencing only the scope writers would add contention and still leave occurrences unfenced, so
none of the existing mechanisms could be removed. The one-read rule makes freezing unnecessary
instead. Every write is either visible to the certifying read, which makes it consistent, or
it leaves a stale basis whose review is ordered after the certification. This holds for the
tests below, including real concurrent writers.

### What the rule made redundant, and what stays

| Mechanism | Status |
| --- | --- |
| Ordinary apply: record the basis, then re-check the digest in a separate statement | **Merged** into one verified read (bases + digest). The basis stored is exactly what was verified. |
| Ordinary apply: watch before validation (stale ids, digest, shown periods) and the shown-period equality check | **Kept.** The application snapshot still reads its shown periods in its own statement, so the equality check ties the watch to the validated snapshot. |
| Review: basis and shown intervals in separate statements | **Replaced** by the one statement. |
| Fan-out: stored and current bases in separate statements | **Replaced** by the one statement. A torn fan-out read could only cause an extra review or a deferred one, but uniformity costs nothing. |
| Shared period fence and exclusive first-page barrier | **Kept.** This is the ordering half of the rule. |
| `record_period_basis_on` | **Removed.** It is replaced by `verified_period_bases_on` + `store_period_bases_on`. |

**Deadlocks and contention:** nothing changed. `read_period_inputs_on` takes no locks. The lock
order is unchanged: hard-forget fence → identity epoch → entity lock → shared period fence →
row locks. The fan-out takes only the exclusive fence. Writers take no new locks, so ingestion
and extraction contention are unchanged.

## Tests

| Test | Result |
| --- | --- |
| `test_a_torn_review_read_cannot_certify_a_window_from_another_period`: the reviewer's interleaving, with corrections A → B → A around both the preparation and validation reads | Passes here. The certified window matches its basis, and a later restore leaves the basis stale, so a review is queued. **Fails on `3b676c17`** in three of three runs: a 2022 window is certified against a 2023 period. |
| `test_reviews_racing_real_period_writers_settle_on_the_final_periods`: separate `set_effective_periods` transactions move a version's start 48 times while the real `Worker`/`WorkLedger` runs reviews; then the queue drains | Passes here: every fact's stored basis is current and its window is the one the final periods give. It also passed on `3b676c17` in three runs, because the race window is too narrow to hit by timing alone. The deterministic interleaving above is the failing-first proof. |
| All earlier race tests | Pass. Two hooks moved to the new functions: `verified_period_bases_on` replaces `record_period_basis_on`, and `read_period_inputs_on` replaces the removed separate basis read. Each scenario is unchanged. |

Results:
- **PostgreSQL 19 (Cloud Shell):** `test_d140_period_adjudication_pg.py` (26 tests), the reuse
  regression and the forget proof: 28 passed.
- **Full suites:** see the PR body.

The run on `3b676c17` needed one collection-only shim: a stub `verified_period_bases_on`.
