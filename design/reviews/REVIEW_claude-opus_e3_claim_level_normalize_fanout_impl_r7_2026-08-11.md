# Round-7 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Claude (opus-5)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `ec64a05f`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior round:** `REVIEW_claude-opus_e3_claim_level_normalize_fanout_impl_r6_2026-08-11.md`,
`REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r6_2026-08-11.md`

## Summary

All three r6 blockers are closed in production code, and I re-derived each one
rather than trusting the commit message. The D56 occurrence-map switch is the
substantive one, and it is right as far as it goes — but it went only half the
distance. The commit widened the set of claims the pipeline **produces** work
for (fan-out, both barrier counts, handler membership, selector) and left the
two surfaces that **report and gate on** that work still joining the origin
`claims.chunk_id`. The two sides now disagree, which they did not before this
commit: a version can be reported `ready` while claim-normalize rows this very
commit created are still pending or dead-lettered, and its connector cycle can
finalize without waiting for them.

Separately, the branch fails two CI gates. That is mechanical, but it means the
branch cannot merge as it stands, and both r6 reviews missed it because both ran
only `pytest`.

### r6 items: verified closed

**Codex B1 — expected set omits D56 reused occurrences. Closed on the producing
side.** All four producing statements now go through the occurrence map:
fan-out (`src/rememberstack/spine/work_ledger.py:1157-1171`), expected count
(`:1173-1186`), ready count (`:1188-1208`), and the version-scoped origin-claim
selector (`src/rememberstack/spine/claim_catalog.py:341-351`). The handler no
longer compares the immutable origin chunk against the representation's grid;
it asks the occurrence map directly
(`src/rememberstack/workers/e3.py:184-202`,
`src/rememberstack/spine/claim_catalog.py:155-169,361-368`).

I checked the interaction that would have made this fix hollow: the fan-out
still pins `cl.extractor_version = :extractor_version`
(`src/rememberstack/spine/work_ledger.py:1168`), and a D56-reused claim keeps
the *original* extractor's version stamp, so the pin could have excluded exactly
the rows the join was widened to admit. It does not, because
`extraction_input_hash` — the sole reuse key
(`src/rememberstack/spine/claim_catalog.py:245-259`,
`src/rememberstack/workers/e2.py:326-350`) — includes `extractor_version`
itself (`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:550`).
Reuse therefore never crosses an extract generation, and pin and occurrence map
are consistent. I also confirmed fresh extraction writes `chunk_claims`
(`src/rememberstack/spine/claim_catalog.py:214-222`), so the switch does not
starve the ordinary non-reuse path — worth stating explicitly because no test on
this branch would have caught it if it did.

**Codex B2 — per-entity staging retirement not atomic with D43 apply. Closed.**
`add_observations` now takes `clear_staging` and executes the delete on the same
connection inside the same `engine.begin()` block that wrote the observations
(`src/rememberstack/spine/observation_adjudication.py:144,167-182,944-951`), and
the handler passes the entity's coordinates instead of making a second call
(`src/rememberstack/workers/e3.py:752-766`). The `tuple(...)` at `:167` forces
the generator, so every assertion is applied before the delete runs. An
exception rolls back both. The r6 N1 window — apply committed, delete not — is
genuinely gone, not narrowed.

**Claude B1 — pulling `valid_from` under a capped neighbour. Closed.**
`_pull_valid_from_earlier` now consults `_HAS_LATER_CAP_BOUNDARY` before moving
the boundary (`src/rememberstack/spine/observation_adjudication.py:699-709,929-942`).
Walking my r6 trace: X `[2019, 2024)`, Y `[2024, ∞)`, restatement at 2015 — the
guard finds X with `valid_until = 2024 > 2015` and refuses. Y stays
`[2024, ∞)`, the windows stay disjoint, and nothing is published as
simultaneously `CURRENT`. The guard runs on the same connection inside the
batch transaction, so caps written earlier in the same flush are visible. It is
correct in the safe direction — it can only ever decline to widen. See N1 for
what it costs.

Re-verified intact from earlier rounds: the barrier advisory lock
(`src/rememberstack/spine/work_ledger.py:1140-1153`), atomic full-set fan-out,
`valid_from=asserted_at` on ordinary inserts, source-time supersession
orientation (`src/rememberstack/spine/observation_adjudication.py:443-497`), the
presence-only cycle wait, and the hard-forget staging scrub.

## Blocking findings

### B1 — The occurrence-map fix stopped short of readiness and the cycle wait, so the two now disagree with fan-out

Two statements still reach claims through the immutable origin chunk:

- **Readiness.** `_NORMALIZE_CLAIM_STATUS` joins
  `LEFT JOIN claims cl ON cl.chunk_id = c.chunk_id`
  (`src/rememberstack/spine/readiness.py:314-316`) and derives version status
  from that population, with `WHEN count(cl.claim_id) = 0 THEN 'succeeded'`
  (`:293`).
- **Connector-cycle wait.** The D88 clause joins
  `JOIN claims cl ON cl.chunk_id = c.chunk_id`
  (`src/rememberstack/spine/lifecycle.py:1060`).

Before `ec64a05f` this was under-coverage but not disagreement: fan-out used the
same origin join, so a reused claim got no work row, and readiness counting zero
of them was consistent with there being nothing to count. This commit changed
the producing side only. Now the fan-out creates a normalize row for every
occurrence (`src/rememberstack/spine/work_ledger.py:1157-1171,686-708`) while
readiness and the cycle wait cannot see the reused ones at all.

Concretely, and this needs no exotic input — it is what an edited document
looks like. Version V2 of a document has two chunks: `c1` changed, `c2`
unchanged.

1. E2 re-extracts `c1` (fresh claim **A**, origin `c1`) and takes the D56 reuse
   rung on `c2`, re-attaching claim **B** — whose origin chunk belongs to V1 —
   via `chunk_claims` (`src/rememberstack/workers/e2.py:344-348`).
2. Fan-out enqueues normalize rows for **both** A and B. The barrier expects 2.
   Correct, and this is the fix working.
3. A succeeds. B's provider call exhausts its retries and dead-letters.
4. `_NORMALIZE_CLAIM_STATUS` joins claims by origin chunk, so its population for
   V2 is `{A}` only. A succeeded, so the CASE returns `'succeeded'`
   (`src/rememberstack/spine/readiness.py:294-297`), with `finished_at` from A's
   row. `VersionPipelineReadiness.ready` is `True`
   (`src/rememberstack/spine/readiness.py:179-186`), and that is the
   client-facing report (`src/rememberstack/model/client.py:63,85`).
5. The cycle wait's `NOT EXISTS` likewise sees only A, which is `succeeded` and
   therefore not in the blocking status list, so the connector cycle finalizes
   (`src/rememberstack/spine/lifecycle.py:1052-1068`).

So the pipeline reports the version fully processed, and the sync cycle
complete, while a claim of that version sits in the DLQ and its facts are absent
from memory. The barrier itself holds correctly — adjudicate and embed never
fire — which makes it worse, not better: the version is permanently
half-normalized *and* advertised as done. Step 5 contradicts the design's
binding row directly: "Claim `dead_letter` | Version normalize **not** ready;
connector-cycle **must** wait (include DLQ children …)"
(`plan/designs/e3_claim_level_normalize_fanout_design.md:237`), and the
acceptance row "Connector-cycle | Waits on claim DLQ" (`:310`).

The all-reused variant is the same defect with a wider blast radius: a version
whose every chunk reuses (re-conversion producing a new representation over
identical blocks) has an origin-join population of zero, so `count = 0 →
'succeeded'` reports ready before any of its claim jobs has started.

The fix is the one already applied four times in this commit — join
`chunk_claims` — in these two statements. Note that readiness carries a second
divergence from the pinned set while you are in there (r6 F2, unchanged): it
reaches chunks through `v.current_representation_id`
(`src/rememberstack/spine/readiness.py:310-313`) rather than the representation
the barrier pinned, so the two disagree during a representation swap as well.

**Why the tests are green.** `test_fanout_and_barrier_pin_extractor_version`
asserts `"chunk_claims" in sql` for the three `work_ledger` statements
(`src/tests/workers/test_e3_claim_normalize_fanout.py:57-61`). Neither
`readiness._NORMALIZE_CLAIM_STATUS` nor `lifecycle._SELECT_READY_CYCLES` is in
that loop, so the one assertion that encodes this invariant does not cover the
two places it is now violated. Extending that test to every statement that must
agree on the expected set is two lines and would have caught this.

### B2 — The branch fails two CI gates

`.github/workflows/ci.yml:62` runs `uv run ruff format --check src/ benchmarks/`
and `:64` runs `uv run pyright src/ benchmarks/ --pythonversion 3.13`. Both
fail on this branch:

- `ruff format --check src/` → 2 files would be reformatted:
  `src/rememberstack/spine/supersession.py` (missing blank line before
  `_is_source_successor`, `:498-501`) and
  `src/tests/workers/test_e3_claim_normalize_fanout.py` (`:96-98`, `:143-150`).
- `pyright src/ benchmarks/` → 3 errors, all in
  `src/tests/workers/test_e3_claim_normalize_fanout.py:284,295`: `payload`
  infers as `dict[str, str]`, so `payload.update(payload_overrides)` and the
  `ClaimedWork(payload=…)` argument both reject. One annotation
  (`payload: dict[str, object] = {…}`) fixes all three.

Production code is pyright-clean — the full `src/ benchmarks/` run reports
exactly those 3 errors and nothing else. Both failures predate `ec64a05f` (I
checked `99673d80`'s copies of both files: same two format diffs). They are
listed as blocking only because the branch cannot merge with red CI; on their
own they would be nits, and they are five minutes of work. They are recorded
here mainly because two consecutive review rounds declared the branch one fix
from mergeable while running only `pytest`.

## Non-blocking findings

### N1 — The overlap guard is entity-scoped, which switches the r5 fix off for most real entities

`_HAS_LATER_CAP_BOUNDARY` blocks the pull when *any* other live slice of the
entity has `valid_until > boundary`
(`src/rememberstack/spine/observation_adjudication.py:929-942`). It does not
restrict that to slices the observation is in a supersede relation with. But
temporal overlap between two observations on the same entity is only a defect
when they contradict each other: "Acme CEO is Jane `[2010, 2020)`" and "Acme HQ
is Berlin `[2015, ∞)`" are both true and *should* overlap. The guard treats
every capped slice on the entity as a boundary to respect.

The practical effect is larger than it sounds, because capped slices are the
normal state of a long-lived entity, and because `_CAP_WINDOW` degrades an
undated successor to `now()` (`src/rememberstack/spine/observation_adjudication.py:910-918`).
One undated supersession anywhere on the entity therefore leaves a slice ending
at ingest time, which is later than every backfill boundary, and the pull is
refused for every statement on that entity from then on. That restores exactly
the asymmetry Codex raised as r5 B1: the same two assertions produce
`[2015, ∞)` if the older one flushes first and `[2024, ∞)` if it flushes second
— the acceptance row "Observations reverse completion order | Same windows after
ordered flush" (`plan/designs/e3_claim_level_normalize_fanout_design.md:304`).

This is not blocking: the guard only ever declines to widen, so nothing wrong is
published — the window is merely narrower than the evidence supports, and
order-dependent. But the r5 fix is now inert for the entities that matter most,
which is worth knowing before anyone concludes the reverse-order case is
handled. The narrowing is to scope the guard to slices this observation is
actually in a supersede relation with (the `related` edge already recorded at
`:433-465` and `:500-520`) rather than to the whole entity.

### N2 — The occurrence join can return the same claim twice

`chunk_claims` is one row per `(chunk, claim)` attachment, and its PK is
`(chunk_id, claim_id, created_at)`
(`src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:592`)
— so one claim may legitimately attach to two chunks of the *same* version. The
schema comment names the case ("duplicate identical chunks within a version
stay distinguishable", `:574`), and the reuse path produces it: two chunks
sharing an `extraction_input_hash` both resolve to the same prior chunk
(`src/rememberstack/spine/claim_catalog.py:245-259`) and copy the same claim
set. None of the new joins deduplicates.

I traced the consequences and they are mostly absorbed, which is why this is not
blocking:

- Fan-out enqueues the claim twice, but `enqueue_on` is idempotent on the
  natural key (`src/rememberstack/spine/work_ledger.py:820-852`), so one row
  results.
- Expected and ready both count join rows and inflate by the same factor, and
  the `processing_state` join contributes at most one row per expected row, so
  `_normalize_claim_barrier_ready` still fires exactly when it should
  (`src/rememberstack/spine/work_ledger.py:744-766`).
- The D88 selector passes duplicate ids to `relation_ids_for_origin_claims`,
  which is harmless.

The one place it changes behavior is the legacy serial path:
`claims_for_chunks` now returns the claim twice
(`src/rememberstack/spine/claim_catalog.py:341-351`), `normalized_claim_ids` is
computed once before the loop, so both copies miss the skip set and the claim is
normalized twice in one pass (`src/rememberstack/workers/e3.py:276-299`) — a
duplicated LLM call and a second adjudication record. `SELECT DISTINCT` there
and `count(DISTINCT cl.claim_id)` in the barrier are the whole fix.

### N3 — `clear_staged_observations_for_entity` is now dead code

Moving the delete into `add_observations` left
`src/rememberstack/spine/fact_catalog.py:459` with no callers anywhere in `src/`.
Delete it, or the next reader will assume there are two retirement paths and
wonder which is authoritative.

### N4 — The two new claim-catalog statements do not pin `deployment_id`

`_CLAIM_OCCURS_ON_CHUNKS` (`src/rememberstack/spine/claim_catalog.py:361-368`)
filters on `claim_id` and `chunk_id` only. Its inputs are separately
deployment-scoped — the claim's tenancy is checked at
`src/rememberstack/workers/e3.py:175-183` and the chunks come from the payload's
representation — so I could not construct a leak. But §7 of the design says
"payload coordinates must match the claim row's lineage"
(`plan/designs/e3_claim_level_normalize_fanout_design.md:262-266`) and every
other membership statement on this path carries the pin; a new statement that
does not is the kind of thing that stops being safe when a later caller
reuses it. `_SELECT_CLAIMS_FOR_CHUNKS` (`:341-351`) has the same gap plus the
missing extractor pin carried from r6 F1.

### N5 — The window-behavior test gap is now three rounds old

Nine of the thirteen tests in the D88 file drive real code; the three tripwires
flagged in r6 N2 are unchanged
(`src/tests/workers/test_e3_claim_normalize_fanout.py:175-190,193-206,209-218`),
and `test_obs_flush_retires_staging_in_same_txn_as_apply` was rewritten to grep
for `clear_staging` and `_DELETE_OBS_STAGING_ENTITY` in two sources rather than
to prove a retry adds no rows. `test_fanout_and_barrier_pin_extractor_version`
is the same shape, and B1 is what that shape misses.

The DB-marked reverse-order test the acceptance table has asked for since r4
(`plan/designs/e3_claim_level_normalize_fanout_design.md:304`) still does not
exist, and the suites that would host it still skip:
`test_observation_adjudication.py` + `test_supersession.py` → **17 skipped**,
unchanged across r5, r6, r7. Three window findings in three rounds have now been
found by reading. N1 is the fourth, and it is invisible to every test on the
branch.

### N6 — Carried, unchanged

- **F2 (partial).** `_NORMALIZE_CLAIM_STATUS` still maps zero claims to
  `succeeded` (`src/rememberstack/spine/readiness.py:293`) and still reaches
  chunks via `v.current_representation_id` (`:310`) rather than the pinned
  representation. Folded into B1 above.
- **F3.** `_ladder` is still called with arrival roles, source-time orientation
  applied only at the write
  (`src/rememberstack/spine/observation_adjudication.py:337-343` vs `:443-497`).
- **F4.** `normalize_observation_staging` still carries unbounded `statement` in
  its primary key
  (`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:29-34`).
  A statement past the btree tuple limit fails the staging insert, dead-letters
  the claim, and — via B1 — that dead-letter is now invisible to both the cycle
  wait and readiness for a reused claim. `md5(statement)` in the key is a
  one-line migration.
- **N4 from r6.** The cycle wait is still unscoped by `component_version` /
  chunker / extractor generation (`src/rememberstack/spine/lifecycle.py:1057-1068`),
  so a stale non-succeeded claim row under a superseded generation blocks its
  cycle indefinitely. Same statement as B1 — worth fixing in the same edit.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **31 passed in 4.39s**.

Supplementary:

```text
uv run pytest src/tests/spine/test_observation_adjudication.py \
  src/tests/spine/test_supersession.py -q     → 17 skipped in 1.14s
uv run ruff check <changed files>             → All checks passed
uv run lint-imports                           → 5 kept, 0 broken
uv run ruff format --check src/               → 2 files would be reformatted  ✗
uv run pyright src/ benchmarks/               → 3 errors, 0 warnings          ✗
git diff --check 99673d80 ec64a05f -- src     → clean
git diff --check 5b75a6c9 ec64a05f -- src     → clean
```

The two failures are B2. The 17 skips are the gap N1 and every prior window
finding live in: `REMEMBERSTACK_DATABASE_URL` is unset, so no observation-window
behavior executes in a default developer environment.

## Mergeability

**Not yet — one correctness finding plus red CI.** The three r6 blockers are
genuinely closed, and I re-derived the non-obvious parts rather than reading the
commit message: the extractor pin does not fight the occurrence map, fresh
extraction does write `chunk_claims`, the staging delete really is inside the
D43 transaction, and the new clamp really does keep my r6 overlap trace
disjoint. That is real progress and the hard parts of D88 — the lock, the atomic
fan-out, the generation pins, source-time orientation — remain sound.

What blocks is that the occurrence-map fix was applied to the four statements
that create work and not to the two that report on it, so the pipeline can now
tell an operator a version is ready, and a sync cycle complete, while a claim of
that version is in the DLQ. That is a worse failure than the one it replaced,
because the previous version of the bug at least kept the two sides agreeing.
The edit is the same `JOIN chunk_claims` in two more places
(`src/rememberstack/spine/readiness.py:314-316`,
`src/rememberstack/spine/lifecycle.py:1060`), plus extending the existing
`chunk_claims` assertion to cover both statements so the invariant is pinned in
one place rather than four.

Ship it with the DB-marked reverse-order window test. Four window findings in
four rounds have been found by reading, N1 is the current one, and nothing on
this branch would catch the fifth.
