# Temporal work — simplify the design, then implement coherent changes

**Status:** revised 2026-09-07 for D114. PR #384 is a design correction and
withdrawal of an incomplete, unshipped implementation. It does not implement
D114 runtime behavior. Explicit user approval is required before merge/release.

**Binding target:** [mutable facts with one world-time window](../designs/mutable_fact_windows_design.md).
**Reasoning:** [audit and alternatives](../analysis/lean_mutable_fact_windows.md).

## Change plan for PR #384

1. Preserve the old draft checkpoint `7a64e34d` in Git history and local branch
   `archive/temporal-fact-framework-7a64e34d`. Do not rewrite the PR's history.
2. Replace fixed fact categories, two fact windows, immutable seed authority,
   dedicated temporal corrections and current-only cache machinery with D114.
   Mark superseded documents and SQL so future implementers cannot mistake them
   for binding instructions. Keep original reasoning accessible.
3. Remove the draft-only runtime, migrations, tests and protocol roll built for
   the superseded framework. Restore those paths to current main `c0f5c010`.
   Preserve already merged canonical SQL (#375), extraction vocabulary, and all
   evaluator providers, including the Codex subscription evaluator (#382).
4. Update the design index, numbered decision log, affected authority pointers,
   project status and PR description. Remove instructions for the abandoned
   experimental upgrade. Do not claim that main already supports D114.
5. Verify that runtime, tests, CI and benchmark pins match main exactly. Check
   local document links, supersession pointers and whitespace. Runtime acceptance
   of the removed implementation does not transfer to its replacement.
6. Have Antigravity and Grok independently review the resulting diff and design,
   fix findings, and record their actual conclusions. Keep the PR draft and
   unmerged for the user's explanation and approval.

The PR change plan above is complete. [Review and validation record](../implementation_evals/temporal_simplification_review_20260907.md) records both independent verdicts,
resolved findings and the exact design-only acceptance limits.

## Replacement implementation sequence

These are separately reviewable implementation packages, not alternate designs.
A package cannot claim readiness until its named behavior and dependencies work.

### A. Concrete storage and application contract

Before replacement write-path code, publish the exact narrow preparation/receipt
schema, adjudication output models, application identity, lock order, stale-input
check, deletion inventory and recovery procedure required by D114 §§3–4,7–8.
Implement D114 §2's shape table with fact-specific CHECK constraints; do not copy
claim CHECKs or canonicalize incomplete/already-canonical fact windows. Specify
D114 §5's per-result `temporal_match: confirmed | possible` in the exact versioned
response schema; existing closed envelopes cannot accept an unversioned extra
field. Define the unique application key/index using deployment, normalized
assertion identity (including its normalization generation) and adjudicator
generation. Text, triple or interval equality is not a retry key.
Show why each new durable field is needed; reuse the existing ledger, transcripts
and source lifecycle wherever possible. Independent review must cover concurrent
helpers, late inference replies, identity changes and forget before code lands.
The withdrawn D110/D113 SQL is not a shortcut through this gate.

### B. Ordinary mutable-fact adjudication and conversion

Implement both relation and observation paths together with their workers,
barriers, readiness, source withdrawal and forget participation. Stage relations
before identity, include historical candidates, remove hard kind/date/exact-text
identity vetoes, and replace the entire chosen window through ordinary decisions.
Support explicit history splits/evidence assignments with atomic result receipts.
Remove the same-triple overlap exclusion without allowing retry duplicates.

Convert existing source-time fact windows only when evidence grounds world time;
otherwise preserve unknown dates. Retain fact IDs where identity remains valid.
Prove bounded restartable conversion, exclusive cutover and all-writer/readiness
coverage. Do not expose partially converted windows to existing consumers.

Required tests: same-event date corrected earlier and later; extended/reopened
end; dated/undated contextual identity; distinct same-day same-triple events;
no source/ingestion-clock fallback; late A→B→A with explicit support assignments;
concurrent/retried application; rollback; changed inputs during inference; forget
followed by retry and rebuild without resurrection. Run supported PostgreSQL,
full migration, worker, surface and Compose checks for the integrated change.

### C. Retrieval, profiles and consumer cutover

**Release blocker:** do not enable B's data conversion or serving cutover without
C's historical/achievement caller routing, prompts and versioned response changes.
Every served consumer must use the compatible contract; unsupported legacy
generations must be upgraded or refused at the cutover. Completed events must not
vanish behind a caller still using the current default for a historical question.
Reuse current/at/overlap/history, add honest precision
and unknown-date handling, update P1 and final SQL confirmation consistently, and
preserve dated evidence through deduplication. Assured historical and bounded
queries must include relevant unknown-date candidates with explicit uncertainty.
Exact counts must distinguish confirmed matches, possible matches and truncation.

Make profiles date-qualified historical summaries and K pages dated/snapshot
content; refresh on mutations. Update graph/routing predicates that currently use
only `valid_until IS NULL`. Do not introduce timer/certificate stores for this
content. Update API/CLI/MCP/consumption skill, schemas, examples and website docs
with the behavior they actually ship.

Required tests: 2022 win retrievable in 2026 history and 2022 overlap; historical
and current CEO at a half-open transition; undated extra win not counted as a
confirmed 2022 match; truncation not reported as complete; future and historical
profile lines date-qualified; date correction and forget invalidate derived text;
no clock-only change makes dated prose assert something new as current.

### D. Evaluation and release evidence

Roll only generations whose behavior changes. Roll the LoCoMo protocol for the
integrated observable semantic change and retain every evaluator-provider variant.
Compare like-for-like stores and record measured cost/quality, without claiming a
benchmark gain from design acceptance. Run full supported acceptance and request
user review of the concrete implementation before merge/release.

## Already merged and independent work

T.0a canonical arithmetic, T.0b published canonical SQL and T.4 extraction
vocabulary/full source timestamp are retained. The query-role sandbox remains
unchanged. Main's Full-v24 protocol is restored when withdrawing the unshipped
Full-v25 framework; this is not a rollback of a released protocol.

The former #365–#368 topics remain useful questions, with revised answers:
ordering belongs to ordinary application; date correction belongs to ordinary
adjudication; profiles use stable dated content; forget covers the smaller actual
storage inventory. Their old framework specifications no longer gate code; the
concrete D114 contracts and acceptance above do.
