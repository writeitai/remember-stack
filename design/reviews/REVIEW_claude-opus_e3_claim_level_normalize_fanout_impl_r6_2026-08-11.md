# Round-6 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Claude (opus-5)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `99673d80`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior round:** `REVIEW_claude-opus_e3_claim_level_normalize_fanout_impl_r5_2026-08-11.md`,
`REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r5_2026-08-11.md`

## Summary

All four r5 items are closed in production code, and I re-derived each one
rather than taking the commit message at its word. One new defect: the fix for
Codex r5 B1 — pulling an equivalent observation's `valid_from` back to the
source-earliest assertion — has no guard against pulling it behind a slice that
was already capped at the old boundary. The result is two mutually superseding
observations with overlapping validity windows, both reported `CURRENT` by the
fact sheet, with no contradiction group. That artifact did not exist before this
commit: evidence collapse previously never touched the window.

Everything else on the branch reads as finished. This is one clamp plus a test
away from mergeable.

### r5 items: verified closed

**Codex B1 — evidence collapse leaves `valid_from` completion-order dependent.
Closed for the case it was raised on.** Both collapse paths now call
`_pull_valid_from_earlier`: the exact-statement fast path
(`src/rememberstack/spine/observation_adjudication.py:210-216`) and the
adjudicated `ObservationOutcome.EVIDENCE` path
(`src/rememberstack/spine/observation_adjudication.py:357-363`). The helper
short-circuits on a missing or already-earlier boundary
(`src/rememberstack/spine/observation_adjudication.py:678-682`), the UPDATE is
itself guarded (`src/rememberstack/spine/observation_adjudication.py:893-900`),
and the in-memory block is kept current so a later assertion in the same batch
sees the pulled value
(`src/rememberstack/spine/observation_adjudication.py:691`). Working the two
orders by hand: `S@2019` then `S@2024` inserts at 2019 and the pull is a no-op;
`S@2024` then `S@2019` inserts at 2024 and pulls to 2019. Both land on
`[2019, ∞)`. The asymmetry Codex raised is gone.

Worth recording because it bounds the blast radius below: within one version the
flush applies assertions in ascending `asserted_at`
(`src/rememberstack/spine/fact_catalog.py:658`), so `_pull_valid_from_earlier`
can only fire across versions.

**Claude B1 — flush not retry-idempotent. Closed.** Each entity's staging is
retired immediately after its apply commits
(`src/rememberstack/workers/e3.py:747-762`,
`src/rememberstack/spine/fact_catalog.py:459-478,671-679`), with the whole-version
delete demoted to a safety net (`src/rememberstack/workers/e3.py:763-768`).
`add_observations` is one transaction per entity
(`src/rememberstack/spine/observation_adjudication.py:139`), so an entity that
throws rolls back whole and re-adjudicating it on retry is correct. The r5
failure — a provider blip on entity 40 forcing entities 1–39 to re-adjudicate
and duplicate their supersede history — can no longer happen. See N1 for the
residual.

**Claude B2 — cycle wait stalls on absent claim rows. Closed.** `LEFT JOIN` plus
`w.processing_id IS NULL` became a plain `JOIN`
(`src/rememberstack/spine/lifecycle.py:1052-1068`), so a version with claims but
no claim-grain rows — the legacy serial drain
(`src/rememberstack/workers/e3.py:144-145`) — no longer blocks finalization
forever.

I checked the obvious way this relaxation could go wrong: can a cycle now
finalize while fan-out is merely *late*, before the claim rows exist? No.
Fan-out is enqueued inside the same transaction that completes the last chunk
extract row (`src/rememberstack/spine/work_ledger.py:287-308`), so claims and
their normalize rows become visible atomically; until then the preceding
chunk-extract clause blocks (`src/rememberstack/spine/lifecycle.py:1040-1051`).
Blocking on `dead_letter` is intentional and matches the design's
"Connector-cycle | Waits on claim DLQ" acceptance row.

**Claude B3 — coordinate rejection untested. Closed.** Four behavioral rejection
cases now drive the real handler
(`src/tests/workers/test_e3_claim_normalize_fanout.py:213-318`): wrong extractor
generation, wrong `doc_id`, payload `claim_id` ≠ `target_id`, and a claim whose
chunk is not at the payload version. They exercise all three rejection branches
in `src/rememberstack/workers/e3.py:164-197`. See N3 for the one dimension still
not asserted.

Re-verified intact from earlier rounds: the dedicated barrier advisory lock
(`src/rememberstack/spine/work_ledger.py:332-335`), atomic full-set fan-out
(`src/rememberstack/spine/work_ledger.py:294-308`), the extractor pin on all
three claim-set statements (`src/rememberstack/spine/work_ledger.py:1156-1204`),
`valid_from=asserted_at` on every ordinary insert, source-time supersession
orientation (`src/rememberstack/spine/supersession.py:185-201`), and the
hard-forget staging scrub.

## Blocking finding

### B1 — Pulling `valid_from` earlier can put an observation underneath a slice already capped at its old boundary

`_pull_valid_from_earlier` moves an open observation's `valid_from` to the
incoming assertion's `asserted_at` whenever that is strictly earlier
(`src/rememberstack/spine/observation_adjudication.py:678-691`). It consults
only the row's own `valid_from`. It does not ask whether some other slice of the
same entity was capped *at* that boundary — which is exactly what the supersede
paths do when they cap a predecessor at the successor's `valid_from`
(`src/rememberstack/spine/observation_adjudication.py:457-464,490-497`).

Concretely, one entity, three versions, all within D88's continuous
out-of-source-order ingest:

1. V1 flushes `"Acme HQ is Berlin"` `asserted_at=2024`. No candidates, so it
   inserts open at 2024 (`src/rememberstack/spine/observation_adjudication.py:218-240`).
   → **Y** `[2024, ∞)`.
2. V2 flushes `"Acme HQ is Munich"` `asserted_at=2019`. Ladder returns supersede;
   2019 is source-earlier than Y, so the reverse-arrival branch inserts it as a
   historical predecessor and caps it at Y's `valid_from`
   (`src/rememberstack/spine/observation_adjudication.py:433-464`).
   → **X** `[2019, 2024)`, **Y** `[2024, ∞)`. Disjoint, correct.
3. V3 flushes `"Acme HQ is Berlin"` again — an older document restating the same
   fact — `asserted_at=2015`. It exact-matches Y, which is still open
   (`src/rememberstack/spine/observation_adjudication.py:189-197`), attaches
   evidence, and pulls Y back to 2015.
   → **X** `[2019, 2024)`, **Y** `[2015, ∞)`.

X and Y now overlap on `[2019, 2024)`, and they are the two sides of a supersede.
An as-of query for 2020 classifies both as `CURRENT` — `valid_from ≤ as_of` and
`valid_until > as_of` for each
(`src/rememberstack/core/knowledge_fact_sheet.py:156-166`) — and observations
reach that classifier with their raw windows
(`src/rememberstack/spine/knowledge.py:4694-4706`). Neither carries a
`contradiction_group`, because supersede records `related`, not a group
(`src/rememberstack/spine/observation_adjudication.py:454-455`), so nothing marks
the pair as conflicting. The reader is told Acme's HQ was simultaneously Berlin
and Munich in 2020, as settled fact.

Step 3 is not a contrived input. "An older document restates the fact that
currently wins" is the ordinary shape of backfill, and backfill is the case D88
exists to serve (`plan/designs/e3_claim_level_normalize_fanout_design.md:43-46`).
The precondition is just: a supersede has capped something at Y's `valid_from`,
and a source-earlier restatement of Y arrives afterwards.

This is a regression, not a carried-over limitation. Before `99673d80`, `_evidence`
never touched the window, so collapse could not move a boundary underneath a
capped neighbour. The r5 fix is right in intent and right for the two-assertion
case it was raised on; it needs a bound.

The minimal fix that keeps the r5 behavior: refuse the pull when the entity has a
capped slice whose `valid_until` is later than the proposed boundary — attach the
evidence and leave the window, as before. In the trace above that leaves Y at
`[2024, ∞)` and the windows disjoint, while the two-assertion case (no capped
neighbour) still converges. Representing the 2015 restatement as its own
historical slice is the richer answer, but it is a design question and does not
belong in a bug fix. Either way the invariant should be stated in the design
alongside the ordered flush
(`plan/designs/e3_claim_level_normalize_fanout_design.md:211-228`): collapse may
widen a window, never across an existing boundary.

**Why this survived six rounds.** The design's acceptance row "Observations
reverse completion order | Same windows after ordered flush"
(`plan/designs/e3_claim_level_normalize_fanout_design.md:304`) still has no
executable coverage. Codex r5 asked specifically for two dated equivalent
assertions and two dated superseding assertions in both completion orders,
asserting identical stored windows. What landed instead is a substring assertion
on the generated SQL (`src/tests/workers/test_e3_claim_normalize_fanout.py:186-189`).
That test passes on the broken three-assertion trace above. The PostgreSQL-backed
suites that would host a real proof skip entirely without a database (see
Verification), so no window behavior on this branch executes in a default
developer environment. A DB-marked test walking the three flushes and asserting
non-overlapping windows would have caught this in r5 and would catch the next
one.

## Non-blocking findings

### N1 — The flush retry gap is narrowed, not closed

Apply and retire are still separate transactions
(`src/rememberstack/workers/e3.py:750-762`;
`clear_staged_observations_for_entity` opens its own `engine.begin()` at
`src/rememberstack/spine/fact_catalog.py:467`). A process death or connection
loss in the gap between an entity's apply commit and its delete commit leaves
that one entity staged, and re-adjudicating it can still duplicate history by the
r5 B1 mechanism. The exposure drops from "the whole prefix of a long LLM-driven
flush" to "one entity, one statement's worth of wall clock", which is the right
trade for v1 — but it is a narrowed window, not idempotence, and the design
should say so rather than leave a reader to infer it from the code.

### N2 — Three of the four new tests assert on source text, not behavior

`test_obs_flush_retires_staging_per_entity`
(`src/tests/workers/test_e3_claim_normalize_fanout.py:191-198`) greps
`handle`'s source for the string `clear_staged_observations_for_entity`. It
passes if the call is moved outside the loop — that is, if the fix is undone in
the one way that matters. `test_cycle_wait_does_not_block_on_missing_claim_rows`
(`:201-210`) slices the SQL on the comment text `"claim-grain normalize"`, so
rewording a comment breaks it while a semantic regression that keeps the comment
does not. `test_ordinary_observation_inserts_pass_valid_from` (`:173-189`)
counts occurrences of a literal. These are serviceable tripwires next to a
behavioral test; as the only coverage of the fixes they are close to no coverage.
`test_claim_handler_rejects_coordinate_mismatches` shows the alternative is cheap
here.

### N3 — The coordinate test does not assert the cross-tenant dimension

The four cases at `src/tests/workers/test_e3_claim_normalize_fanout.py:265-318`
never vary `claim.deployment_id`. The `doc_id` and extractor cases cover the same
`if` branch, so the branch is exercised, but the design's acceptance case is
"Cross-tenant payload lie | Rejected"
(`plan/designs/e3_claim_level_normalize_fanout_design.md:308`), and a future
refactor that drops the `claim.deployment_id != deployment_id` conjunct
(`src/rememberstack/workers/e3.py:176`) leaves every test green. One more case,
two lines.

### N4 — The cycle wait is still unscoped by generation

Removing the `LEFT JOIN` fixed the absence half of r5 B2. The presence half
stands: the clause filters neither `w.component_version` nor `c.chunker_version`
(`src/rememberstack/spine/lifecycle.py:1057-1067`), while fan-out pins both
(`src/rememberstack/spine/work_ledger.py:1165`). A claim-grain row left `failed`
or `dead_letter` under a superseded chunk grid or normalizer generation blocks
its cycle indefinitely, even though nothing will ever retry it at that
generation. Lower severity than the r5 form — it needs a stale row rather than
merely an absent one — but the recovery is still manual SQL and the stall is
still silent.

### N5 — Carried from r5, unchanged

- **F1.** The supersession selector still builds its origin-claim set with
  `claims_for_chunks`, which has no extractor pin
  (`src/rememberstack/workers/e3.py:773-775`,
  `src/rememberstack/spine/claim_catalog.py:125-137`), though the payload carries
  `extractor_version` (`src/rememberstack/spine/work_ledger.py:373`). Over-includes
  after a re-extract; never under-includes.
- **F2.** `_NORMALIZE_CLAIM_STATUS` still maps zero claims to `succeeded`
  (`src/rememberstack/spine/readiness.py:293`) and still reaches chunks through
  `v.current_representation_id` (`src/rememberstack/spine/readiness.py:310`)
  rather than the representation the barrier pinned.
- **F3.** `_ladder` is still called with arrival roles, with source-time
  orientation applied only to the write
  (`src/rememberstack/spine/observation_adjudication.py:337-343` vs `:433-487`).
- **F4.** `normalize_observation_staging` still has unbounded `statement` in its
  primary key
  (`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:31-34`),
  and nothing bounds statement length on the normalize path. Its blast radius grew
  this round: a statement past the btree tuple limit fails the staging insert,
  the claim dead-letters, and the new cycle clause
  (`src/rememberstack/spine/lifecycle.py:1052-1068`) then blocks that cycle's
  finalization until an operator intervenes. Worth folding in while B1 is open —
  `md5(statement)` in the key is a one-line migration.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **31 passed in 4.47s** (28 in r5, plus the three new tests).

Supplementary:

```text
uv run pytest src/tests/spine/test_observation_adjudication.py \
  src/tests/spine/test_supersession.py -q
```

Result: **17 skipped in 1.25s** — `REMEMBERSTACK_DATABASE_URL` unset. Unchanged
from r5: no observation-window behavior executes without a database, which is the
gap B1 lives in.

## Mergeability

**Not yet — one finding.** The r5 round closed cleanly: the evidence-collapse
asymmetry, the flush retry prefix, the cycle stall, and the missing tenancy tests
are all genuinely fixed, and I verified the cycle-wait relaxation does not open a
premature-finalization window on the other side. What blocks is that the
evidence-collapse fix moves a boundary without checking whether another slice was
capped at it, which publishes two contradictory observations as simultaneously
`CURRENT` — a memory system asserting a fact and its negation for the same year,
silently. The fix is a guard in one helper
(`src/rememberstack/spine/observation_adjudication.py:678-691`).

Ship it with the DB-marked reverse-order test the design's acceptance table has
been asking for since r4. Three rounds of window bugs have now been found by
reading rather than by running, and B1 is the second one introduced by the fix
for the first. N3 and F4 are small enough to ride along; the rest of the
non-blocking list is fine to carry.
