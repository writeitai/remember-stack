# PostgreSQL 19 live-graph implementation review — round 2

**Status:** non-binding review evidence; `CHANGES_REQUESTED` on the moving
working tree. **Reviewed:** 2026-08-27.

Claude Opus was invoked with the required read-only command form:

```text
claude --model opus --dangerously-skip-permissions --print "<round-2 implementation-review prompt>"
```

The reviewer confirmed every round-1 blocker and the main isolation, catalog,
capacity, immutable-render, proxy, billing, and compatibility fixes. It also
correctly refused to certify a tree that changed while it was reading it.

Remaining findings were: the graph clock error string did not match the helper;
the directed citation example and pure open-query carrier/parser tests were
missing; no executable removal audit existed; a superseded, unexposed
`QueryEngine.multi_hop_context` implementation still performed non-atomic
multi-transaction graph hydration; pg_textsearch license/source/patch/per-arch
evidence was incomplete; and several low-level contract drifts remained
(unclamped PGQ result bound, self-comparison in capacity planning, stale MCP
class name, fixture graph env omission, and missing `depth_budget` acceptance).

The exact Antigravity command was attempted and again failed because the
`antigravity` executable is absent from `PATH`; no substitute is represented as
that review.

Fixes are followed by a final frozen-tree recheck. PostgreSQL 19 database and
image/cutover evidence remain release gates rather than review defects.

