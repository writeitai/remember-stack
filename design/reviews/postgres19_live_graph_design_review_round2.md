# PostgreSQL 19 live-graph design review — round 2

**Date:** 2026-08-27  
**Scope:** final OSS D98 and UMC D43 analysis, decisions, binding designs,
implementation plans, adjacent contracts, full working-tree diffs, and the
complete official PostgreSQL 19 SQL/PGQ documentation-derived contract.

## Commands

Every rerun used the user-required command forms; the prompt named both
repositories, the changed corpus, prior findings, and the approval rule.

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<design-review prompt>"
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<design-review prompt>"
```

## Review sequence and final gate

1. The first round-two Claude pass found inconsistent evaluation clocks,
   incomplete helper/readiness contracts, an impossible write-using `STABLE`
   helper shape, missing truncation transport, and incomplete D66 sequencing.
   Antigravity approved with nits. The corpus was revised.
2. Claude then found stale requirements/schema/retrieval text, unsafe internal
   CTE naming, and incomplete supersession/indexing; those were revised.
3. Antigravity found the last stale LoCoMo readiness signature and retrieval
   snapshot field; both were revised.
4. Claude's final adversarial pass found four behavior-level gaps: tenant
   sentinels in runtime readiness, impossible over-budget PGQ/frontier byte
   parity, order loss through the status carrier, and missing D98 decision-log
   amendments. All four were resolved in binding designs and gates.
5. Final targeted verdicts after those corrections were:
   - **Antigravity: APPROVE**, no blocker or high finding.
   - **Claude Opus 5: APPROVE_WITH_NITS**, explicitly no blocker or high
     finding and all four behavior blockers closed.

Claude's remaining corpus-hygiene findings were then resolved before code
implementation: the binding benchmark runbook is `full-v14`; scope registry
DDL/comments no longer publish `PROJECT_GRAPH_CYPHER`; public deployment
mismatch uses the exhaustive `invalid_parameter` code; D16/D61 carry visible
D98 amendments; 18 saved examples include citation path; readiness proves an
anchor absent in the same read-only snapshot; and the UMC D41/D43 and UI
wording is precise. `git diff --check` passes in both repositories.

## Final behavior dispositions

| Review issue | Binding disposition |
| --- | --- |
| Empty deployment could not pass live-graph readiness | Runtime readiness is data-independent, writes no sentinel, proves a generated UUID absent in the same repeatable-read snapshot, then exercises PGQ/helpers. Seeded edges and invalid-short/valid-longer paths are isolated release/restore/dogfood fixtures only. |
| PGQ refused over-budget work while the helper returned a prefix | Byte parity is under-budget only. Fixed depth-one/two typed operations always use PGQ and return zero data plus `expansion_budget`; direct helpers may return a deterministic prefix. Both are separately gated; no runtime fallback exists. |
| Status `RIGHT JOIN` could destroy caller ordering | The AST rewrite carries hidden sort keys/order ordinal, preserves `LIMIT`/`OFFSET` including `LIMIT 0`, orders at the outermost select, and rejects shapes whose semantics cannot be preserved. |
| Old decisions still read as current Ladybug/P2 authority | D98 amendments and current wording cover D9/D16/D48/D49/D50/D61/D69/D70/D73/D79/D87/D94/D96 and the complete supersession list. |
| PostgreSQL 19 SQL/PGQ scope was overclaimed | Shipped PGQ stays fixed one/two-hop, server-owned, implicit-correlation only, with repeatable-element UUID exclusion. Quantified/shortest/path-variable and other unsupported features are negative tests. |
| Multiarch evidence was overstated | The experiment remains arm64 evidence only. linux/amd64 and linux/arm64 are release gates, and amd64 blocks managed rollout. |

The design gate is closed. Implementation and a separate dual implementation
review remain required.
