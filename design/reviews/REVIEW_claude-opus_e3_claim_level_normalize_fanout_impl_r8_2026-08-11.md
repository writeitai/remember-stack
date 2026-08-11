# Round-8 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES *(no correctness finding — the sole blocker is red CI,
unchanged from r7)*

**Reviewer:** Claude (opus-5)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `2db1acc0`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior round:** `REVIEW_claude-opus_e3_claim_level_normalize_fanout_impl_r7_2026-08-11.md`,
`REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r7_2026-08-11.md`

## Summary

The r7 correctness blocker is closed, and closed correctly. `2db1acc0` carries the
D56 occurrence map into the last two consumers — derived normalize readiness and
the connector-cycle wait — so all six statements that participate in the expected
claim set now agree. **I found no correctness defect at this commit.** On the
correctness axis alone this is an APPROVE.

What still blocks is r7 B2, unchanged: the branch fails two required CI gates. That
is four lines of mechanical work with no design content, but it means the branch
cannot merge as it stands, and it has now survived a round after being reported.

### r7 B1: verified closed, and I re-derived the risky part

Both statements now traverse `chunk_claims`:

- Readiness — `LEFT JOIN chunk_claims cc ON cc.chunk_id = c.chunk_id` /
  `LEFT JOIN claims cl ON cl.claim_id = cc.claim_id`
  (`src/rememberstack/spine/readiness.py:315-319`).
- Connector-cycle wait — `JOIN chunk_claims cc` / `JOIN claims cl`
  (`src/rememberstack/spine/lifecycle.py:1061-1062`).

Walking the r7 B1 trace against the new SQL. V2 has chunks `c1` (changed) and `c2`
(unchanged); E2 re-extracts `c1` → claim **A**, and takes the D56 reuse rung on
`c2`, re-attaching claim **B** whose origin chunk belongs to V1. A succeeds, B
dead-letters.

- Readiness: the join now yields `{A, B}`, so `count(cl.claim_id) = 2` while
  `count(...) FILTER (WHERE p.status = 'succeeded') = 1`. The equality arm fails and
  `bool_or(p.status = 'dead_letter')` fires → `'dead_letter'`
  (`src/rememberstack/spine/readiness.py:292-302`). `VersionPipelineReadiness.ready`
  is `False` (`:181-185`). Previously this returned `'succeeded'`.
- Cycle wait: the `NOT EXISTS` now sees B's `dead_letter` row, so the cycle does not
  finalize (`src/rememberstack/spine/lifecycle.py:1052-1070`). This is the row the
  design requires: "Claim `dead_letter` | Version normalize **not** ready;
  connector-cycle **must** wait"
  (`plan/designs/e3_claim_level_normalize_fanout_design.md:237`).
- The all-reused variant — every chunk reuses, origin-join population zero — no
  longer hits `WHEN count(cl.claim_id) = 0 THEN 'succeeded'`
  (`src/rememberstack/spine/readiness.py:293`), because the occurrence rows exist.

**The load-bearing assumption, checked rather than assumed.** Switching readiness
from the origin edge to `chunk_claims` is only safe if *every* claim has an
occurrence row; if fresh extraction ever skipped one, this commit would turn a
narrow reused-claim bug into a false `ready` on the ordinary path — a far worse
regression, and no test on this branch would catch it. It holds:
`INSERT INTO claims` exists in exactly one place in production code
(`src/rememberstack/spine/claim_catalog.py:283`), and its only caller
`record_extraction` executes `_INSERT_CHUNK_CLAIM` for every claim inside the same
`engine.begin()` block (`:208-222`). The reuse path writes links too
(`_COPY_CHUNK_CLAIMS`, `:269-279`). So the new join is a strict superset of the old
one — it can widen the expected set, never narrow it. That is the safe direction.

I also confirmed the widening cannot block a version it shouldn't: a claim reached
through a current-version chunk's occurrence row *is* in this version's expected set
by D56, and it is exactly the set the fan-out enqueues
(`src/rememberstack/spine/work_ledger.py:1157-1171`). Producing and reporting sides
are symmetric again.

Duplicate occurrence rows (r7 N2) do not corrupt either new statement. Readiness
inflates `count(cl.claim_id)` and its `FILTER` count by the same factor, so the
equality arm is unaffected; the cycle wait is an `EXISTS`, so multiplicity is
irrelevant.

Re-verified intact from earlier rounds: the barrier advisory lock
(`src/rememberstack/spine/work_ledger.py:1146-1154`), atomic full-set fan-out
(`:684-741`), the extractor/chunker/representation pins on all three barrier
statements (`:1157-1208`), atomic staging retirement inside the D43 apply
(`src/rememberstack/spine/observation_adjudication.py:144-182`), the
later-cap clamp (`:929-942`), source-time supersession orientation, the
presence-only cycle wait, and the hard-forget staging scrub.

## Blocking finding

### B1 (= r7 B2, unchanged) — the branch fails two required CI gates

`.github/workflows/ci.yml:62` and `:64` run `ruff format --check` and `pyright` over
`src/ benchmarks/` in the required `Quality` job. Both fail at `2db1acc0`. CI installs
with `uv sync --locked` (`:54`), so the toolchain matches my local run exactly
(ruff 0.15.20).

**`ruff format --check src/ benchmarks/` → 2 files would be reformatted.**

- `src/rememberstack/spine/supersession.py:500` — one blank line missing before
  `_is_source_successor` at `:501` (two required after the module-level statement
  ending at `:499`). This is **production code**, and it is branch-introduced:
  `git show main:src/rememberstack/spine/supersession.py` formats clean.
- `src/tests/workers/test_e3_claim_normalize_fanout.py:100`, `:143-150`, `:268` —
  three call/literal wrappings.

**`pyright src/ benchmarks/ --pythonversion 3.13` → 3 errors.** All three are one
root cause in `src/tests/workers/test_e3_claim_normalize_fanout.py`: `payload` at
`:279-286` infers as `dict[str, str]`, so `payload.update(payload_overrides)` at
`:287` rejects a `dict[str, object]` (2 errors), and passing `payload` to
`ClaimedWork(payload=…)` at `:298` rejects `dict[str, str]` against
`dict[str, object] | None` (1 error, `dict` being invariant in its value type). One
annotation — `payload: dict[str, object] = {…}` at `:279` — clears all three.

Production code is otherwise pyright-clean: the full `src/ benchmarks/` run reports
exactly those 3 errors. `ruff check` and `lint-imports` both pass.

I am recording this as blocking rather than as a nit only because it is literally
true that the branch cannot merge with a red required gate, and because it is the
same finding as r7 B2 one round later. It carries no design risk and no correctness
risk. If your merge gate is "correctness reviewed and clean," treat this review as
APPROVE and land it behind the four-line fixup.

## Non-blocking findings

### N1 — The D56 behavioral regression both r7 reviews asked for still does not exist

`2db1acc0` added three assertions, all string greps over rendered SQL:
`assert "chunk_claims" in sql` for readiness
(`src/tests/workers/test_e3_claim_normalize_fanout.py:70`) and for the cycle wait
(`:221`). They pin that the token appears; they cannot distinguish a correct join
from a wrong one, and they would pass against a `chunk_claims` join on the wrong
column.

Codex r7 asked specifically for a behavioral case — origin chunk in an older
version, a current-version `chunk_claims` link, a current-generation pending or
dead-lettered claim row, asserting readiness does not report `succeeded` and the
cycle does not finalize. That test does not exist. The handler fixture nominally
covering D56 still sets `claim.chunk_id = chunk_id` equal to the current chunk
(`:232`, `:238`, `:256`) and its `claim_occurs_on_chunks` fake returns
`claim.chunk_id in set(chunk_ids)` (`:249-252`) — the origin-equals-current case,
which is precisely the case that was never broken.

So the invariant this round exists to establish is the one thing the round did not
test. It is non-blocking because I verified the behavior by derivation above and it
is right — but the next person to touch these six statements has no guard.

### N2 — Carried unchanged from r7, all still open

Nothing in `2db1acc0` touches any of these; I re-checked each at this commit rather
than assuming.

- **N1 (r7) — the overlap guard is entity-scoped.** `_HAS_LATER_CAP_BOUNDARY`
  refuses the pull when *any* other live slice of the entity has
  `valid_until > boundary`
  (`src/rememberstack/spine/observation_adjudication.py:929-942`), not just slices
  the observation contradicts. Since `_CAP_WINDOW` degrades an undated successor to
  `now()` (`:910-918`), one undated supersession anywhere on an entity leaves a slice
  ending at ingest time and refuses every later backfill pull on that entity —
  reinstating the order-dependence of the acceptance row "Observations reverse
  completion order | Same windows after ordered flush"
  (`plan/designs/e3_claim_level_normalize_fanout_design.md:304`). Safe direction
  (only ever declines to widen), but the r5 fix is inert for long-lived entities.
- **N2 (r7) — no `DISTINCT` on the occurrence joins.** Absorbed everywhere except
  the legacy serial path: `_SELECT_CLAIMS_FOR_CHUNKS`
  (`src/rememberstack/spine/claim_catalog.py:341-350`) can return a claim twice,
  `normalized_claim_ids` is computed once before the loop, and the claim is
  normalized twice in one pass (`src/rememberstack/workers/e3.py:276-299`) — a
  duplicated LLM call. `SELECT DISTINCT` is the whole fix.
- **N3 (r7) — dead code.** `clear_staged_observations_for_entity`
  (`src/rememberstack/spine/fact_catalog.py:459`) still has zero callers in `src/`.
- **N4 (r7) — missing pins.** `_CLAIM_OCCURS_ON_CHUNKS`
  (`src/rememberstack/spine/claim_catalog.py:361-368`) has no `deployment_id` pin;
  `_SELECT_CLAIMS_FOR_CHUNKS` (`:341-350`) has neither `deployment_id` nor the
  extractor pin. Not exploitable today — callers are separately scoped
  (`src/rememberstack/workers/e3.py:175-183`) — but every other membership statement
  on this path carries the pin.
- **F2 (partial) — readiness reaches chunks via `v.current_representation_id`**
  (`src/rememberstack/spine/readiness.py:309-310`) while the barrier pins the
  representation from the payload
  (`src/rememberstack/spine/work_ledger.py:1166,1182,1204`). The occurrence map now
  agrees across all six statements; the *representation* still does not, so the two
  diverge during a representation swap. This is the one remaining place where
  producing and reporting sides disagree about the expected set.
- **F3.** `_ladder` still called with arrival roles; source-time orientation applied
  only at the write (`src/rememberstack/spine/observation_adjudication.py:337-343`
  vs `:443-497`).
- **F4.** `normalize_observation_staging` still carries unbounded `statement` in its
  primary key
  (`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py:29-34`).
  A statement past the btree tuple limit fails the staging insert and dead-letters
  the claim. Now that B1 is fixed that dead letter is at least *visible* to readiness
  and the cycle — which is a real improvement, since this round removed the
  compounding failure. `md5(statement)` in the key remains a one-line migration.
- **r6 N4 — the cycle wait is unscoped by generation.** `_SELECT_READY_CYCLES` joins
  `chunks c ON c.version_id = v.version_id` with no representation, chunker, or
  extractor pin (`src/rememberstack/spine/lifecycle.py:1059-1067`), so a stale
  non-succeeded claim row under a superseded generation blocks its cycle
  indefinitely. This round's `chunk_claims` join makes that set strictly wider —
  stale reused occurrences now count too. The direction is safe (over-blocking, not
  under-blocking), but the widening compounds an already-open finding in the exact
  statement that was edited.

### N3 — The window-behavior test gap is now four rounds old

`test_observation_adjudication.py` + `test_supersession.py` → **17 skipped**,
unchanged across r5–r8: `REMEMBERSTACK_DATABASE_URL` is unset, so no observation
window behavior executes in a default developer environment. The DB-marked
reverse-order test the acceptance table has asked for since r4
(`plan/designs/e3_claim_level_normalize_fanout_design.md:304`) still does not exist.
Five window findings across five rounds have now been found by reading; N2's first
bullet is the current one, and nothing on this branch would catch the sixth.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **31 passed in 4.26s**.

Supplementary:

```text
uv run ruff check src/ benchmarks/                     → All checks passed
uv run lint-imports                                    → 5 kept, 0 broken
uv run ruff format --check src/ benchmarks/            → 2 files would be reformatted  ✗
uv run pyright src/ benchmarks/ --pythonversion 3.13   → 3 errors, 0 warnings          ✗
uv run pytest src/tests/spine/test_observation_adjudication.py \
              src/tests/spine/test_supersession.py -q  → 17 skipped in 0.91s
git diff --check ec64a05f 2db1acc0 -- src              → clean
```

The two failures are B1. Note that all three suites in the requested command pass
both before and after `2db1acc0` — the requested command cannot distinguish this
commit from its parent on behavior, only on the three added string assertions.

## Mergeability

**Not as it stands — but on mechanics, not on correctness.**

The D88 correctness work is done. The occurrence-map cutover is now complete and
consistent across all six statements that touch the expected claim set — fan-out,
both barrier counts, the version-scoped selector, handler membership, readiness, and
the cycle wait — and I verified the one way this commit could have gone badly wrong
(fresh extraction always writes `chunk_claims`, so the join only ever widens) rather
than taking the commit message for it. The hard parts of D88 — the barrier advisory
lock, the atomic full-set fan-out, the generation pins, the atomic D43 apply/retire,
source-time supersession orientation — are sound, and I have no correctness finding
at `2db1acc0`.

What blocks is that the branch fails two required CI gates
(`.github/workflows/ci.yml:62,64`), one of them on production code
(`src/rememberstack/spine/supersession.py:500`, branch-introduced) and three pyright
errors resolvable with a single annotation
(`src/tests/workers/test_e3_claim_normalize_fanout.py:279`). Run
`uv run ruff format src/ benchmarks/`, add the annotation, and this merges.

Two things worth carrying rather than shipping blind. The D56 regression both r7
reviews asked for still does not exist (N1), so the invariant this round established
is unguarded. And the reverse-order window test has been outstanding for four rounds
while the suite that would host it skips entirely (N3) — that is where the next
finding will be, as it has been in each of the last four rounds.
