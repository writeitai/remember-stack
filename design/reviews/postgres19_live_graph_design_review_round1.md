# PostgreSQL 19 live-graph design review — round 1

**Date:** 2026-08-27  
**Scope:** OSS D98 analysis, binding graph/schema/open-query/retrieval/K designs,
decision-log amendments, cutover plan, and the UMC D43 analysis/design/plan.

## Review commands

The reviewers received repository status, the D98/D43 documents, the relevant
binding designs and decisions, and the complete staged/untracked diff.

```text
claude --dangerously-skip-permissions --model claude-opus-5 --effort xhigh -p "<design-review prompt>"
agy --dangerously-skip-permissions --print-timeout 180m0s -p "<design-review prompt>"
```

## Verdicts

- Claude Opus 5: **REQUEST_CHANGES**.
- Antigravity: **APPROVE_WITH_NITS**.

## Findings and disposition

| Finding | Disposition |
| --- | --- |
| Community products still had consumers in K and schema contracts | Resolved by removing communities, `entity_graph_metrics`, community events/rules/pages, and their schema/plan contracts. Degree is computed from live relation adjacency when needed. |
| Existing recursive helpers could scan/materialize the eligible graph and enumerate all paths before limiting | Resolved in the binding design: deployment-required, level-at-a-time frontier traversal; tenant/anchor-first indexes; early exit at the first target-bearing level; hard depth/frontier/examined-edge/result/temp/time budgets; truncation fields. |
| Only an arm64 experiment existed | Resolved as a blocking linux/amd64 + linux/arm64 release matrix; amd64 blocks managed rollout. |
| SQL/PGQ and recursive traversal overlapped without a parity contract | Deliberately retained per operator direction. Exact one/two-hop parity fixtures now bind the overlap; SQL/PGQ is server-owned fixed-pattern execution, recursive SQL owns variable/shortest traversal. |
| UMC frozen `require_projections` contract still implied P2 | Resolved by D43: capability-specific live-graph readiness plus P3 readiness; no generic P2 compatibility state. |
| Older binding documents and decisions still presented Ladybug/P2/community as current | Resolved with explicit D98 supersession plus cold-reader updates across graph, schema, open-query, retrieval, overall, registry, K, benchmark, packaging, hard-forget, and sequencing documents. Historical completed plans/decisions are labeled as history. |
| Time-source wording disagreed | Resolved on one statement/transaction evaluation clock as specified by each closed helper contract; predicates and emitted evaluation time use the same captured value. |
| SQL/PGQ readiness checked only object existence | Resolved after the complete official documentation review: readiness verifies semantic graph catalogs, exact keys/endpoints/labels/properties/types, graph privilege, and underlying element-view privileges. |
| PostgreSQL repeatable-element semantics could admit repeated vertices/edges | Resolved by explicit UUID repeat exclusion in simple fixed-pattern templates and generated parity/cycle tests. |
| PostgreSQL 19 prerelease, pg_textsearch patch, architecture, license, billing/version, and rollback consequences needed sharper gates | Resolved in D98/D43 and both cutover plans: immutable image/source pins, patch provenance/license, per-architecture tests, fresh-instance logical restore on every beta/RC/GA bump, measurement-version roll, and no Ladybug fallback. |

Round 2 is required after all findings are incorporated. It is not satisfied by
this record.
