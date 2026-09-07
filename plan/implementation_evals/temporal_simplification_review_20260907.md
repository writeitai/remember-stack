# PR #384: temporal design simplification and independent reviews

**Date:** 2026-09-07.
**Scope:** [D114 design](../designs/mutable_fact_windows_design.md), withdrawal
of unshipped code, and [replacement delivery gates](../plans/temporal_clocks.md).
**Status:** design reviewed; replacement runtime is not implemented here.
The PR remains draft. The user has not authorized merge or release.

## What changed

The old checkpoint `7a64e34d` contained an incomplete framework with approximately
24,942 additions relative to main. Commit `5e2fd916` restored every runtime,
migration, test, CI and benchmark path to main `c0f5c010`, then replaced the
design with one mutable fact window and ordinary contextual adjudication.
The old commits remain reachable in PR history; a local archival branch also
preserves the checkpoint. No force push or production migration was performed.

The revised main diff is confined to the design corpus, decision log and truthful
website project status. Published canonical SQL, T.4 extraction and all Full-v24
evaluator variants—including the merged Codex subscription evaluator—remain intact.

## Review method

Antigravity and Grok were independently asked to inspect the final main diff,
the D114 design/analysis/plan and relevant main code. Both were explicitly limited
to read-only review; neither was authorized to edit, post, merge or deploy.
Antigravity ran with its 180-minute print timeout. Grok used a headless session.
Its first invocation ended at an interactive read-tool permission prompt without
a verdict; the same session was resumed with repository inspection authorized.
Only the subsequently completed review is reported below.

| Round | Reviewed revision | Antigravity | Grok |
| --- | --- | --- | --- |
| 1 | `5e2fd916` against `c0f5c010` | **Approve with nits** for design-only scope | **Request changes**: partial fact windows conflicted with claim shape/canonicalization rules |
| 2 | `74700bad`, including the focused `5e2fd916..74700bad` fix | **Approve** for design-only scope | **Approve with nits**; first-round blocker resolved |

These are the reviewers' conclusions, not assertions that the replacement engine
works. Both independently verified that runtime/tests/CI/benchmarks match main.

## Findings and dispositions

| Finding | Disposition |
| --- | --- |
| Grok R1: claim CHECKs reject partial windows; claim canonicalization fills a missing end | D114 §2 now has an explicit unknown/open/partial/finite shape table. Fact-specific constraints are required; claim CHECKs cannot be copied and claim `canonical_bounds()` cannot consume partial or already-canonical fact windows. Both reviewers accepted the fix in R2. |
| Antigravity R1: NULL and enum `unknown` precision would create two representations | New fact precision is explicitly non-null, default `unknown`, matching the existing vocabulary. Accepted in R2. |
| Antigravity R1: confirmed and possible candidates need an explicit closed response shape | D114 §5 specifies per-result `temporal_match`, relative to query scope; the concrete versioned schema is a pre-code gate. This is not a persisted dispute state. Accepted in R2. |
| Antigravity R1: conversion could hide completed events from current-default callers | Delivery package C makes coordinated conversion, historical caller routing and response-version cutover a strict release blocker. Accepted in R2. |
| Antigravity R1: removing overlap exclusion needs application uniqueness | Package A requires an exact deployment/assertion/generation uniqueness key; text/triple/interval equality is explicitly insufficient. Accepted in R2. |
| Grok R1: D43 measurement no-cap interpretation remained ambiguous | D114 §10 gives reporting periods finite world windows without invalidating belief merely because the period ended. Conflicting same-period figures still coexist. Accepted in R2. |
| Grok R1: old statuses, nested D110 precedence, EXCLUDE DDL and Full-v23 wording looked live | Supersession is marked at those sites. Withdrawn SQL is no longer incorporated. Accepted in R2. |
| Antigravity R2: D107 lacks an explicit status line | Added a **partially superseded** status, preserving canonical arithmetic, public SQL and extraction. Calling all of D107 superseded would be incorrect. |
| Grok R2: known side of a partial window still needs unit alignment | Added the requested sentence: normalize only known raw endpoints using UTC truncate/advance rules, keep the missing side NULL, and never apply whole-claim canonicalization to the partial fact. |
| Grok R2: profile wording could conflate open with unknown end | Explicitly reserve “since …; no end recorded” for `open`; partial windows say “start known; end unknown” without implying continuation. |

The final three wording nits were applied after the R2 approvals. No new storage,
runtime change or architecture choice was added. The analysis also clarifies that
the existing fact-sheet “ended” label describes world time; the concern is reader
confusion, not a claim that the helper already invalidates belief.

## Validation and limits

- Repeated main comparison shows runtime, migrations, tests, CI, benchmark pins,
  dependency manifest and lockfile unchanged from `c0f5c010`.
- `git diff --check` passed. Changed-document relative file links resolved
  (120 checked at the reviewed follow-up; final review-record links checked too).
- `npm run build` in `website/` passed compilation, TypeScript, static export
  and Pagefind indexing. The website source is unchanged after that build.
- At reviewed design `74700bad`, [CI 34116176363](https://github.com/writeitai/remember-stack/actions/runs/34116176363)
  passed quality, unit, contract smoke and PR gate. The
  [documentation build](https://github.com/writeitai/remember-stack/actions/runs/34116176472)
  and CLA check passed. Integration/Compose and deployment were skipped for this
  documentation-only PR; they are not represented as passing runtime acceptance.
- Latest main was fetched again and remained `c0f5c010`; the feature branch was
  zero commits behind it. No additional rebase was necessary.

The removed implementation's earlier private PostgreSQL tests and reviews do not
certify the replacement. Concrete storage/consumer contracts and integrated
PostgreSQL, migration, worker, surface, concurrency, deletion and benchmark
acceptance remain required by the delivery plan. No score improvement or release
readiness is claimed by accepting D114.
