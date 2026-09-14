# Delivery and supervision: D119–D123

**Status:** execution plan, 2026-09-14. Designs own contracts; this file owns
sequence. The user authorized Grok implementation, Antigravity review, parent
final review/adjustments/merges, CLA assent and appropriate releases. No separate
human prompt-exam exercise. Keep instructions understandable and the code simple.

## Lanes and merge sequence

1. **D119 extraction**, PR #400, `feat/multi-span-claim-extraction`: finish the
   already running Grok implementation, meaningful unit/PostgreSQL checks,
   Antigravity and originally requested Cursor reviews, then parent code and
   evidence review. This lane does not absorb D122 scheduling/context changes.
2. **D120/D121 clear prompts and concise inputs**: a separate Grok lane can work
   while D119 completes. It must preserve candidate/evidence membership, implement
   the typed prompt-reference adapter and write a complete field projection map.
   Rebase onto merged D119 before its final review; coordinate E2 wording against
   that implementation rather than overwriting its source-span schema.
3. **D122/D123 document references and context nomination**: another Grok lane
   starts from the committed designs, reviews/maps the implementation, then bases
   runtime edits on the completed D119 and D120/D121 branches. No parallel edits
   in shared worktrees. It implements Selection persistence/barrier, bounded
   negative dependencies and generic application context together so the source
   references actually reach observations and candidate nomination.
4. Parent checks the combined main branch and releases only a validated coherent
   set of runtime changes. Generation numbers/migration IDs/protocol fingerprints
   are reconciled during rebases and regenerated from final source. Never reuse
   a protocol identity for changed semantics. Follow existing release automation
   and do not bypass CI or publish a tag for an unvalidated snapshot.

Each implementation PR has an Antigravity review against its exact final commit:

```text
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<review prompt>"
```

Grok is invoked as:

```text
grok --always-approve --model grok-4.6 -p "<implementation prompt>"
```

Reviewers inspect read-only. Record findings, fixes, validation commands and exact
reviewed SHAs in `plan/implementation_evals/`. Re-review material fixes and rebases.
Only the parent merges/releases; implementers finish with reviewable PRs and reports.

## Acceptance and limits

Parent review checks factual assertion preservation, all date meanings, real
provider/schema wiring, deterministic retries, source ownership/forget, 500-version
reuse, candidate fallback/novelty, generation pins and shipped docs. CI integration
lanes must run on runtime changes. A mocked provider's chosen answer is not
evidence of model comprehension. Separate measured token arithmetic from real
inference cost/quality and distinguish tests from benchmark results.

Use isolated local test databases; do not modify the existing remote v27/v28/v18
benchmark stores, start paid LoCoMo runs, add provider fallbacks or change answering/
judging. Vertex/Gemma bindings are required before any later authorized paid
processing experiment. Do not add legacy store conversion. The clean-store
refusal policy never authorizes wiping an existing store.

Grouped assertions remain the [unchosen proposal](../../design/proposals/grouped_fact_adjudication.md).
No implementation work for grouping, new temporal types/windows, model judges,
event registries or locks during inference is included.
