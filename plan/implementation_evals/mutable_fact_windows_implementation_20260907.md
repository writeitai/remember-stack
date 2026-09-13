# Mutable fact windows: implementation and independent review

Initial checkpoint: 2026-09-07. Implementation PR: [#393](https://github.com/writeitai/remember-stack/pull/393).
Base inspected: main `0125daaf`. This record covers implementation of
[D118](../designs/mutable_fact_windows_design.md) and its
[application contract](../designs/mutable_fact_application_contract.md).
The earlier [#384](https://github.com/writeitai/remember-stack/pull/384) is a
separate design-only draft; this implementation includes that revised design.
Neither PR has been authorized for merge or release.

## Current scope after the September 11 simplification

The September 7 evidence below is historical: retained-store conversion, readiness
fences and legacy evidence support were subsequently removed by user-approved
scope change. Populated pre-D118 deployments must be recreated and re-ingested;
the migration refuses them. Hard-forget coverage of the new stores remains.
The binding authority is mutable fact windows design §8 and application contract
§§7–8; the rationale and observation plan are in
[application storage analysis](../analysis/mutable_fact_application_storage.md#no-existing-store-conversion-2026-09-11)
and the [watch list](../analysis/mutable_fact_windows_watch_list.md).

Empty candidate sets now create a fact without model inference. Exact triples
and statements take priority within the existing twenty-candidate limit; equality
still does not decide identity. Direct lookups return flagged possible temporal
matches. Inference remains outside locks with the existing revalidation protocol.

## September 13 integration checkpoint

The branch is rebased onto main `a3943035`, including #399. Its absolute-date
claim extraction and `temporal-anchor-4` generation remain intact. The combined
fact contract advances LoCoMo to Full-v28 and regenerates the surface manifest
hash; this version identifies changed behavior, not measured benchmark quality.

Cursor and Antigravity reviewed the prior pushed head `9eadcd4c` and reported no
blockers. Cursor identified unused retired writer SQL and a supersession prompt,
stale conversion wording, and thin lookup documentation. Those leftovers are now
removed or clarified; the diagnostic observation evaluator remains in use.

That head's worker CI had 612 passing tests and two succession fixture failures.
The first assertion had no candidates, so the new deterministic path correctly
refused an injected model window. The fixture now supplies an explicitly ongoing
claim window before application, then tests an ordinary successor decision. It
still checks the evidenced world-time cap and preservation of belief history.

### Final review and validation evidence

Both requested reviewers inspected implementation commit `abb35783` in separate
read-only detached checkouts, comparing the previous approval `9eadcd4c`, the
rebased follow-up and main `a3943035`. Commands used:

- `cursor-agent --yolo --model cursor-grok-4.6-high -p ...`
- `agy --dangerously-skip-permissions --print-timeout 180m0s -p ...`

Both reported **no blockers**. Cursor verified that extractor/grounding files
match #399 and the lock/CAS/forget files match the prior approved implementation.
Both checked the four simplifications and generated contract pins. Their reviews
are code/static verification, not substitutes for the independently run suites.

Cursor's final optional findings were resolved where they improve this change:
normalize now uses the same recreate-and-re-ingest wording as flush; the succession
test also asserts the predecessor has the source-derived open window before the
successor caps it. The offline observation evaluator remains used by evaluation;
nomination scan cost remains a measurement item in the watch list. Neither calls
for new runtime machinery here.

Validation checkpoints (overlapping runs, not additive coverage):

- Local benchmark protocol/runner/store-backup suite: **151 passed**.
- Local PostgreSQL writer/succession suite: **24 passed**.
- After Cursor's pre-cap assertion: succession suite **8 passed** on a separate
  disposable PostgreSQL database.
- At `abb35783`, all technical CI checks passed: quality, unit, contract smoke,
  worker/surface/adapter integration, Compose quickstart, three client versions,
  and docs build. The worker/spine lane reported **623 passed**, including the
  two previously failing succession variants
  ([CI run](https://github.com/writeitai/remember-stack/actions/runs/34781526239)).
- Local Ruff/format, Pyright (zero errors), test inventory, five import-boundary
  contracts, offline OpenAPI equality and the website production build passed.

The PR checks are the authority for subsequent commit results. The contributor
agreement check remains unsuccessful because the author has not checked assent;
no agent has signed it. No production migration, paid benchmark, release or merge
was performed. User approval before merge remains required.

## Historical September 7 checkpoint

## What changed, and why the remaining machinery exists

Claims retain source testimony. Facts have one chosen world-time window, with
precision and claim witnesses. Ordinary adjudication chooses identity and can
revise dates in either direction. Repeated events need no stored event/state
category, and corrections need no second date window or dispute status.

Two internal stores retain the original normalization response and staged/applied
assertion receipts. These support retry without changing the original assertion,
ordered application through the existing entity work units, and validation after
model inference runs outside database locks. They are not a new correction queue.
Retrieval, SQL/graph eligibility, labels, dated profiles and K sheets consume the
same chosen window. Conversion reuses existing workers and checks completion
before reopening an existing store. Erasure covers the new provenance stores.

## Independent reviews

The requested CLIs were executed with `agy --dangerously-skip-permissions
--print-timeout 180m0s -p ...` and `grok --always-approve --model grok-4.6 -p ...`.
Final follow-up reviews used an isolated detached checkout. Reviews inspected
code; reviewers were asked not to run database tests or modify production.

| Reviewer / checkpoint | Verdict and follow-through |
| --- | --- |
| Antigravity, `73a3df32` | Approved writer, lock coordination, lifecycle, projection repair, predicate usage and unmerge handling in the inspected scope. |
| Grok, `73a3df32` | No runtime conversion/forget blocker found. Identified stale project-status and withdrawal-cap documentation; both corrected. Requested clearer evidence for real-Git erasure and zero-output replay. |
| Antigravity, `e4aa669e` | Approved the Git fix, retired-page inventory and documentation corrections. Did not claim the pending zero-output test was green. |
| Grok, `e4aa669e` | No new runtime blocker; Git erasure and both documentation findings closed. Pointed out a remaining old expected SQL predicate, now replaced and exercised with unknown and partial windows. |

The real-Git proof exposed an actual recovery bug: after history erasure, `git add`
was given an absent compiled-page path. The purger now stages only surviving
current bytes. Deletion and purge run twice in the test for both stale and retired
pages, with a source marker absent from reachable history and unrelated files
preserved. This closes a gap that an in-memory Git double could not reveal.

Zero-output conversion now has a composed regression: no fact applications are
created, yet the ordinary document-label work clears Alice's no-longer-supported
profile and rebuilds Acme's dated observation summary. It does not add a profile
scheduler or a separate conversion repair queue.

## Validation evidence and limits

Local database checks used a private PostgreSQL 19 instance with the required
extensions. Focused suites covered contextual identity, full-window corrections,
concurrent application, stale inputs, retry receipts, support moves, lifecycle,
retained-claim conversion, erasure, profiles, public SQL and graph authority.
Counts below are separate runs and must not be added as independent coverage:

- 31 passed in the final writer/forget/E3/Git run; its added empty-replay fixture
  needed correction. The corrected empty replay plus P1 adapter suite then passed
  all 16 tests.
- All 12 structural migration tests passed, including refusal of a lossy downgrade.
- Real-Git conversion forget: 3 passed (including stale/retired parametrization).
- Final worker/parity/lifecycle/cost-export run: 67 passed; a newly merged claim
  provenance fixture needed the shared disposable reset and subsequently passed
  all 3 tests.
- Graph/envelope/query-role follow-through: 35 passed. Earlier query-space run:
  55 passed with two fixture failures, both included in the passing follow-through.
- Ruff, type checking and the website production build passed.
- Initial full unit run: 1,774 passed, with two version assertions subsequently
  corrected and two environment-sensitive failures. Both environment failures
  reproduced on unchanged main: doctor recognizes a `uvx` launcher; this macOS
  MIME registry does not recognize `.md`. CI's Linux unit suite passed.

At `e4aa669e`, CI quality, unit, contract smoke, full surfaces integration, all
three client compatibility variants, and docs build passed. Adapter fixtures and
the Compose prior-schema setup required follow-through: date precision/profile
policy fixtures were updated, and the upgrade test now creates an empty previous
schema instead of invoking a deliberately forbidden downgrade. The PR carries the
current CI results; this checkpoint record does not turn a pending run green.

No paid LoCoMo run or improved benchmark score is claimed. Full-v28 identifies
the changed protocol, not measured quality. Candidate/evidence payloads are
bounded by row counts and are not an exhaustive identity search. Populated-store
conversion requires stopped old workers and ordinary model/embedding spend;
there is no automatic lossy downgrade. K pages remain dated snapshots and require
their configured compiler to rebuild after conversion. No production migration,
release, merge, or contributor-agreement assent was performed.
