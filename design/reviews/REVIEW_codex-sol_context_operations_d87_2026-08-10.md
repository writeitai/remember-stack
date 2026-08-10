# Review — D87 authority-aligned context operations

- **Reviewer:** Codex (`gpt-5.6-sol`, `xhigh`)
- **Date:** 2026-08-10
- **Scope:** D87, the context-operation analysis, and the binding retrieval,
  open-query, schema, lifecycle, packaging, overall, docs-site, and LoCoMo
  designs changed by this decision
- **Mode:** read-only; no files edited, tests run, or benchmarks started

## Verdict

**Approve.** No blocking or major findings remain.

## Review history

The first pass found two major contract inconsistencies:

1. `fact_context`'s `overlap` and `history` modes could not be distinguished in
   the D49 wire response because the envelope lacked a discriminated applied
   temporal scope.
2. The proposed registry mislabeled the retained `resolve_entity` v1 response
   as evidence grain rather than fact grain.

The corrections add the closed `temporal_scope` result union, require
`answer_context` to preserve that child field unchanged, retain
`resolve_entity` as fact grain, and store the exact result schema in the closed
operation descriptor. A final corpus scan then found the removed
`include_superseded_testimony` flag in the current evidence-lifecycle design;
that paragraph now points only to explicit historical audit paths.

The final recheck approved those corrections and reconfirmed the four-operation
catalog, `ContextBundle/v1`, and no-alias cutover.
