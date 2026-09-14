# D119 implementation handoff to Grok

**User authority:** implement the accepted coherent multi-span claim extraction
with version reuse operating from the outset. Keep it simple. The parent Codex
agent will review the final work after external reviews. Do not merge the PR.

## Workspace and authority

Work only in `/Users/jpuc/code/moje/ultimate_memory/ugm-multispan-claims`, branch
`feat/multi-span-claim-extraction`, started from latest main `b2d32a5a`.
Read `CLAUDE.md`, README planning hierarchy, root `decisions.md` D119,
`plan/designs/multi_span_claim_extraction_design.md`, and its local analysis.
Those design files are already committed by the parent; extend the same PR with
implementation, exact schema details and validation. Preserve unrelated worktrees.

## Required implementation

- Coherent source-supported claims, without indiscriminate decomposition or loss
  of independently dated/attributed assertions. Keep Selection and Claimify calls.
- Deterministic labels for exact source passages in the existing bounded target
  and same-section neighbor context. Model selects provided references; engine
  resolves/validates offsets. Summaries cannot ground claims.
- Bounded multi-span support on each claim occurrence, same source version and
  representation; origin ownership and complete support remain distinct concepts.
- D56 reuse of **same claim IDs** for unchanged extraction inputs. Remap all spans
  per occurrence; no offset/temporary-label keys, no first-match ambiguity, no
  blanket reuse disablement. Include 500-version deterministic regression evidence.
- Updated grounding/loss accounting, occurrence/media provenance, atomic writes,
  hard-forget and stale-work non-resurrection, plus necessary surface schemas,
  manifest/version/protocol artifacts and shipped documentation. Consumer updates
  needed to expose correct provenance are in scope; reader/answering optimization
  and evaluator-model changes are not.
- Respect clean-store migration refusal; do not add a legacy conversion framework
  and never run destructive migration/reset against any existing live store.
- Keep assertions/facts/date semantics from D118; no adjudication batching, model
  confidence router, second date window, new temporal categories or locks around
  inference. No wider context search or extraction-stage fusion in this PR.

## Tests and simplicity

Run meaningful unit and PostgreSQL integration checks for affected code. Use an
isolated local test DB/Compose project if necessary; preserve other running jobs.
Test source reference forgery, multiple spans and topic switches, selected-origin
accounting, context edits, position movement, repeated identical spans, changed
secondary evidence, timestamp invalidation, replay, source deletion and mixed
media provenance. Test unique claims, occurrence rows, provider call counts and
fact evidence counts separately across 500 synthetic versions. No paid model is
needed to prove reuse; a test double should fail on unexpected extraction calls.

Evidence-quality fixtures should be drawn from actual LoCoMo source cases, with
source-grounded expected meanings rather than existing model outputs as an oracle.
Do not copy full licensed datasets into git. UMC evidence paths are linked in the
analysis. No new paid LoCoMo run is authorized by this handoff; do not access or
modify the existing remote v27/v28/v18 stores. The user is continuing design
conversation while you implement. High-reasoning Luna was intended for evaluation
only, not processing; do not repeat or silently change that setup. Any later paid
processing experiment must explicitly verify requested Vertex/Gemma stage bindings.

Prefer existing source block maps, catalog transactions and occurrence storage.
No extra queue, evidence graph, vector store, general dependency engine or permanent
quote cache. Explain necessary additions plainly; do not solve hypothetical future
cases with new abstractions. Inspect the documented timestamp invalidation caveat
and report its measured current behavior without inventing a new clock contract.

## PR and required independent reviews

Keep the PR concrete and reviewable: describe actual code behavior, design choices,
validation with exact commands/results, limitations and any unresolved findings.
Rebase to latest origin/main before final review. If a consequential design gap
appears, document it and surface it; do not silently weaken reuse or provenance.
Routine field/schema choices consistent with D119 can be resolved in the same PR.

After implementation and local checks, obtain both reviews using exactly these
reviewer CLIs (prompts must reference the exact commit/worktree/diff, D119, and
request correctness, simplicity, reuse, grounding, forget and migration review):

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"
cursor-agent --yolo --model cursor-grok-4.6-high -p "<review prompt>"
```

Do not substitute native subagents for either reviewer. Use the multi-hour
Antigravity print timeout. Preserve review outputs, address material findings,
and obtain follow-up review after fixes where necessary. Reviewers should inspect
read-only; avoid races with implementation edits. Separate review checkouts are
appropriate. Do not send Slack/email or merge/publish a release.

Finish with a durable report in `plan/implementation_evals/` and your final output:
PR URL/head SHA; files/behavior changed; how version reuse works; test commands and
results; cost/quality evidence actually obtained; both review verdicts and fixes;
remaining risks and follow-ups. Explicitly distinguish mocked processing tests
from paid/model semantic evidence. The parent reviews this final output and diff.

## Parent integration update, 2026-09-14

The user has authorized parent final merges/releases and CLA assent. Parent
checked this PR's CLA. You still finish with a reviewed PR; do not merge yourself.
Design PR #401 is now merged as `4a803c1d` on main. It accepts subsequent D120–D123
work, but does not expand this D119 implementation: finish its original bounded
multi-span scope. The later lanes integrate your completed result.

Before final checks/review, read `/tmp/ugm-processing-supervision.md` and the
parent's preliminary draft observations at
`/tmp/ugm-d119-grok-20260914/parent-review-notes.md`. Recheck those observations
against your finished code; they are not a demand to preserve unfinished code.
Docker Desktop is available with a cached PostgreSQL 19beta3 CI image. Use an
isolated DB/container and preserve other lanes. Keep the authorized CLA checkbox
when rewriting the PR body. Rebase on main and resolve decision-log ordering so
D119 precedes D120–D123, preserving all decisions.
