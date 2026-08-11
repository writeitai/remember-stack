# Round-4 implementation review: D88 claim-level E3 normalize fan-out

**Verdict:** REQUEST_CHANGES

**Reviewer:** Claude (opus-5)
**Date:** 2026-08-11
**Branch:** `feat/e3-claim-level-normalize-fanout` @ `c3818255`
**Binding design:** `plan/designs/e3_claim_level_normalize_fanout_design.md`
**Prior round:** `REVIEW_codex-sol_e3_claim_level_normalize_fanout_impl_r3b_2026-08-11.md`

## Summary

The solid-path properties confirmed in r3b are still in place and were not
disturbed: the dedicated barrier advisory lock
(`src/rememberstack/spine/work_ledger.py:332-335`), atomic full-set fan-out
inside the extract barrier transaction
(`src/rememberstack/spine/work_ledger.py:294-308`), post-barrier ordered
observation flush (`src/rememberstack/spine/fact_catalog.py:630-640`,
`src/rememberstack/workers/e3.py:720-744`), no partial-skip on claim retry
(`src/rememberstack/workers/e3.py:183-204`), DLQ-aware connector-cycle wait
(`src/rememberstack/spine/lifecycle.py:1050-1068`), hard-forget staging scrub
(`src/rememberstack/spine/forget.py:1249-1254,1360-1364`), and the validly
deferred two-connection race test
(`plan/designs/e3_claim_level_normalize_fanout_design.md:177-184`).

**B1 is partly closed.** The extract generation is now threaded through the
fan-out select, both barrier counts, derived readiness, the claim payload, and
`ClaimNormalizeBarrier` (`src/rememberstack/spine/work_ledger.py:1152-1188`,
`src/rememberstack/spine/readiness.py:106-124,314-316`,
`src/rememberstack/workers/base.py:69`). Two consumers of the same expected set
were missed — see F2.

**B2 is half closed.** `claims.asserted_at` is now persisted from the version
source stamp (`src/rememberstack/workers/e2.py:597`,
`src/rememberstack/model/claims.py:199`,
`src/rememberstack/spine/claim_catalog.py:273,280`) and the competing pair is
oriented before the verdict is applied
(`src/rememberstack/spine/supersession.py:185-201`). The orientation rule itself
introduces a new defect on undated testimony — see F1.

**B3's specific defect is fixed.** A source-earlier assertion arriving after a
source-later open observation now inserts as a capped predecessor instead of
capping the later slice at an older boundary
(`src/rememberstack/spine/observation_adjudication.py:407-461`). Two competing
versions converge to the same two slices in either completion order. A
three-or-more-version residual remains, and is non-blocking — see N2.

This is not yet the simplest solid mergeable v1. One finding is a behavioral
regression against the code this commit replaces (F1), one is an incomplete
application of the fix this commit claims (F2), two are reachable stalls or
silent non-application of work in ordinary documented ops flows (F3, F4), and
the branch does not pass its own CI format gate (F5).

## Blocking findings

### F1 — Undated or equal-time pairs are oriented by UUID, so new testimony can be retired in favor of stale

`_is_source_successor` falls back to `str(relation_id)` comparison whenever both
sides carry the same `asserted_at` or both are undated
(`src/rememberstack/spine/supersession.py:501-516`). The caller then feeds that
orientation straight into the closure
(`src/rememberstack/spine/supersession.py:185-201,256-291`), so on a supersede
verdict the loser is closed at the winner's `asserted_at`, degrading to `now()`
when that is null (`src/rememberstack/spine/supersession.py:463-471`).

Consequence: for an undated pair, roughly half the time the relation being
adjudicated — the one just normalized from the incoming document — is the one
capped at `now()`, leaving the older relation open and current. The previous
code passed the arriving relation as `new` unconditionally, so the newest
testimony always survived. This is a regression in which fact a current-state
query returns, not merely a change in tie-break policy.

The exposure is not narrow. `claims.asserted_at` has never been written before
this commit and no backfill accompanies it (`p9_08_0029_normalize_claim_fanout`
adds only staging + an index), so every claim already in a deployed database is
undated; `source_modified_at` and `published_at` are both optional on the ingest
envelope (`src/rememberstack/model/envelope.py:355`,
`src/rememberstack/model/documents.py:63`), so undated ingest continues after
upgrade. A dated-vs-undated pair is fine (dated wins). Undated-vs-undated is a
coin flip on UUID.

The design binds direction to source time
(`plan/designs/e3_claim_level_normalize_fanout_design.md:198-209`) and is silent
on the undated case, but the sane proxy is already loaded and already used for
ordering in both loaders: `c.ingested_at` in the evidence LATERAL
(`src/rememberstack/spine/supersession.py:401,424`) and `r.ingested_at` in the
candidate `ORDER BY` (`src/rememberstack/spine/supersession.py:459`). Adding one
of those to the two `SELECT` lists and using it as the tie-break before
`relation_id` makes the fallback both deterministic *and* semantically "later
testimony", which is what the pre-commit behavior delivered by accident.

### F2 — The origin-claim selector still spans every extract generation

The commit pins fan-out, barrier, readiness, and payload, and the barrier does
put `extractor_version` on the flush payload
(`src/rememberstack/spine/work_ledger.py:366-373`). The flush handler then never
reads it (`src/rememberstack/workers/e3.py:705-717`), rebuilds the origin-claim
set with an unpinned query (`src/rememberstack/workers/e3.py:746-751` →
`src/rememberstack/spine/claim_catalog.py:125-139,326-333`), and does not
forward the pin to `adjudicate_supersession`
(`src/rememberstack/workers/e3.py:770-776`). The fallback loader in
`AdjudicateSupersessionHandler` repeats the same unpinned load
(`src/rememberstack/workers/e3.py:844-856`).

`relation_ids_for_origin_claims` filters only on `normalizer_version`
(`src/rememberstack/spine/fact_catalog.py:651-660`), and the normalizer
generation does not change when the extractor is bumped, so it does not
substitute for the pin.

Consequence: version V's supersession stage can adjudicate relations evidenced
by claims of a *different* extract generation, including a generation still
mid-normalize. Because `_already_adjudicated` is terminal at the adjudicator
version (`src/rememberstack/spine/supersession.py:337-350`), that other
generation's own adjudication then skips those relations permanently — they were
judged against an incomplete competing block. That is the exact harm §3 names
("supersession needs a complete competing set"), and §5.5 defines the selector
as `claim_id IN expected_claim_ids(V)`, which §5.1 pins to the extract
generation in force when the barrier fired
(`plan/designs/e3_claim_level_normalize_fanout_design.md:84-95,190-196`).

Thread `extractor_version` from the barrier payload into the flush handler, add
a pinned claims-for-chunks query, and carry the pin on the
`adjudicate_supersession` payload for the fallback path.

### F3 — A second normalize generation for the same version strands its staged observations

The barrier is now pinned per extract generation, so each generation's last
claim independently evaluates its own set as complete
(`src/rememberstack/spine/work_ledger.py:346-353`). What it then enqueues is
keyed only by `(deployment_id, document_version, version_id,
adjudicate_observations, OBS_FLUSH_VERSION)`
(`src/rememberstack/spine/work_ledger.py:355-376`), and that insert is
`ON CONFLICT … DO NOTHING`
(`src/rememberstack/spine/work_ledger.py:898-912`). Once the first generation's
flush row has succeeded, the second generation's enqueue is a silent no-op.

Re-extraction at a new extractor version is a first-class implemented flow:
`BackfillSeeder.seed_batch` re-enqueues any plane-E stage — including
`extract_claims` — for every prior target at a new component version
(`src/rememberstack/spine/backfill.py:47-96`). Under it, the new generation's
claims are new rows with new `claim_id`s, so fan-out is *not* deduped: those
claim jobs run, spend model budget, upsert relations, and stage observations
(`src/rememberstack/workers/e3.py:220-229`). Their staged rows are then never
adjudicated, because the only flush row for that version already exists and
already ran its `clear_staged_observations`
(`src/rememberstack/workers/e3.py:740-744`,
`src/rememberstack/spine/fact_catalog.py:642-649`). No supersession or claim
embed runs for the new relations either. Nothing fails, nothing is dead-lettered,
and the rows sit in `normalize_observation_staging` indefinitely.

Before D88 a re-extract simply produced no new normalize work (the version-level
row already existed at that component version), so work was never performed and
then discarded. Making the barrier generation-pinned without also making the
downstream handoff generation-aware creates the new failure mode. Key the flush
row (and the supersession/embed chain it fires) by the extract generation, or
make the flush idempotently re-fireable when staging for the version is
non-empty.

### F4 — The connector-cycle wait blocks forever on claims that have no claim-grain row

The new predicate treats a missing row as unfinished work:
`w.processing_id IS NULL OR w.status IN ('pending','running','failed',
'dead_letter')` (`src/rememberstack/spine/lifecycle.py:1050-1068`), with no
component-version or extract-generation qualifier on the join.

Any claim that will never have a claim-grain normalize row therefore pins its
cycle at `completed_at IS NOT NULL, finalized_at IS NULL` permanently, and the
cycle's tombstone cascade — source-side deletions — never runs. Two reachable
populations: documents normalized by the legacy serial path, which the design
explicitly requires to keep working until drained
(`plan/designs/e3_claim_level_normalize_fanout_design.md:236,238-239`), and
claims of any extract generation whose barrier has not fanned out. No migration
backfills claim-grain rows for legacy claims
(`src/rememberstack/spine/migrations/versions/p9_08_0029_normalize_claim_fanout.py`).
The two sibling predicates above it are status-based only, so this is the one
clause that can wait on a row that will never exist.

The binding rule is narrower than the implementation: §5.7 requires the cycle to
wait on claim **dead_letter** (unlike a pending-only wait), not on absence. Scope
the clause to versions that actually fanned out — e.g. require the claim-grain
row only where any claim-grain row exists for that version, which the atomic
full-set insert makes a safe test (§5.2) — or qualify it by the fan-out
component version.

### F5 — The branch does not pass its own CI format gate

`uv run ruff format --check src/ benchmarks/` is a required CI step
(`.github/workflows/ci.yml:61-62`) and fails at `c3818255`:

```text
Would reformat: src/rememberstack/spine/supersession.py
Would reformat: src/tests/workers/test_e3_claim_normalize_fanout.py
2 files would be reformatted, 359 files already formatted
```

`src/rememberstack/spine/supersession.py:500-501` needs a second blank line
before `_is_source_successor`. Mechanical, but the branch is red as committed.
`ruff check`, `pyright` on the changed modules, `lint-imports`, and the test
inventory check all pass.

## Non-blocking notes

**N1 — Replay guard weakened by the swap.** On `contradict` / `coexist` the
transcript row is written under `new_relation_id`
(`src/rememberstack/spine/supersession.py:302-322`), which is now the *candidate*
whenever orientation swaps. `_already_adjudicated` is keyed on the relation being
processed (`src/rememberstack/spine/supersession.py:122-125,337-350`), so a
swapped subject keeps no guard row and is re-laddered on replay. The outcome
stays stable (a closed window records "window already closed"; a contradiction
reuses the stored group), so this is repeat model spend and transcript noise
rather than corruption.

**N2 — Three or more competing versions can still overlap.** The predecessor
branch caps the incoming slice at the *open* candidate's `valid_from`
(`src/rememberstack/spine/observation_adjudication.py:411-438`), and only open
candidates are ranked (`src/rememberstack/spine/observation_adjudication.py:232-234`,
`797-807`). With sources dated 2019/2021/2024 arriving newest-first, the 2019
assertion sees only the 2024 slice and is capped at 2024, overlapping the already
closed 2021 slice. Two-version convergence — what B3 named — is correct. Full
arrival-order independence for N versions is the commutative D43 redesign the
design puts out of scope
(`plan/designs/e3_claim_level_normalize_fanout_design.md:60-62,317`), so this is
a known-limit note, not a blocker. Worth a line in the design's §5.6 so the
boundary is written down rather than rediscovered.

**N3 — `_remember_candidate` records `valid_from` inconsistently.** Only the two
supersede paths pass it
(`src/rememberstack/spine/observation_adjudication.py:454-460,499-504`); the
novelty, no-rationale, below-margin, and contradict paths leave the in-memory
candidate's `valid_from` as `None`
(`src/rememberstack/spine/observation_adjudication.py:375-379,401-405,532-537,554-556`)
even where the inserted row has one. Benign today, because every claim of one
version shares that version's `asserted_at` and so `_is_strictly_earlier` is
always false within a batch — but it is a latent trap the moment intra-batch
assertion times can differ.

**N4 — Test depth does not match the fixes.** The added cases assert SQL
substrings and the two new pure predicates
(`src/tests/workers/test_e3_claim_normalize_fanout.py:48-167`). The repository
already has Postgres-backed suites for exactly the three modules this commit
changes — `src/tests/spine/test_supersession.py`,
`src/tests/spine/test_observation_adjudication.py`,
`src/tests/spine/test_work_ledger.py`,
`src/tests/spine/test_pipeline_readiness.py` — and none gained a case. The design
defers only the two-connection race test; the reverse-order outcome tests
("Observations reverse completion order", "Supersession reverse version order")
are listed as acceptance tests
(`plan/designs/e3_claim_level_normalize_fanout_design.md:304-305`), and r3b asked
specifically to "cover reversed version completion". A substring assertion cannot
catch F1, which is a behavior a two-relation fixture would have caught
immediately. This is not filed as blocking on its own, but it is why F1 and F3
survived the round.

**N5 — No `asserted_at` backfill.** Existing claims stay undated forever, so F1's
fallback governs the entire pre-upgrade corpus and the D88 source-time contract
is inert for it. A backfill from `document_versions.source_modified_at` /
`published_at` is mechanical and would make the new orientation rule actually
apply to existing data. Worth a decision either way; leaving it unstated is the
problem.

No `website/` change is required: no docs page names pipeline stages or the
`worker --stage` set, so the new `adjudicate_observations` stage adds no
user-facing surface to document.

## Verification

Requested command:

```text
uv run pytest src/tests/workers/test_e3_claim_normalize_fanout.py \
  src/tests/workers/test_chunk_level_extract.py \
  src/tests/profiles/test_selfhost_profile.py -q
```

Result: **27 passed in 3.72s**.

Also run:

- `uv run ruff check src/rememberstack src/tests` — passed.
- `uv run ruff format --check src/ benchmarks/` — **2 files would be reformatted** (F5).
- `uv run pyright` on `supersession.py`, `observation_adjudication.py`, `e3.py`,
  `work_ledger.py` — 0 errors.
- `uv run lint-imports` — 5 contracts kept, 0 broken.
- `python3 .github/ci/check_test_inventory.py` — OK (unit=64, integration=53).
- `uv run pytest src/tests/spine/test_supersession.py
  src/tests/spine/test_observation_adjudication.py
  src/tests/spine/test_work_ledger.py src/tests/spine/test_pipeline_readiness.py -q`
  — **36 skipped** (no Postgres bound in this environment). The changed
  adjudication semantics are therefore unexercised locally and unexercised in
  CI's integration lane too, since no case was added.

## Mergeability

**No — but the remaining distance is short and every fix is local.**

B1's pin, B2's persisted `asserted_at`, and B3's reverse-arrival predecessor are
all real progress, and the solid path (lock, atomic fan-out, no partial-skip,
DLQ-aware cycle wait, forget scrub) holds. What blocks is that the B2 orientation
rule regresses the undated case that dominates every existing database (F1), the
B1 pin stops one hop short of the selector it was meant to close (F2), the pinned
barrier now lets a second extract generation perform work whose output is
silently discarded (F3), the cycle wait can never be satisfied for legacy or
un-fanned-out claims (F4), and CI format is red (F5).

Suggested order: F5 (mechanical), F1 (one column into two `SELECT` lists plus a
tie-break change), F2 (thread the pin already on the payload), F4 (scope one
`NOT EXISTS`), F3 (key the flush handoff by generation). Then add the two
Postgres cases from §11 that would have caught F1 and F3 — reverse version
completion for supersession, and a second extract generation over one version.
