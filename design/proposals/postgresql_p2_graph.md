# Proposal: replace Ladybug P2 with PostgreSQL graph execution

**Status:** open, unchosen
**Date:** 2026-08-14
**Binding baseline:** LadybugDB P2 under D13 and
[`p2_graph_design.md`](../../plan/designs/p2_graph_design.md)
**Analysis:**
[`postgresql_p2_graph_analysis.md`](../../plan/analysis/postgresql_p2_graph_analysis.md)

## Problem

D94 brings P1 into PostgreSQL. P2 still has a rebuild/export/publish/download/
open lifecycle and a separate hard-forget/restore inventory. If that lifecycle
becomes a material operational problem, PostgreSQL graph execution could reduce
the number of physical stores.

## Candidate shapes

### Direct recursive SQL

Implement only the bounded typed neighborhood/path helpers over authoritative
`relations` using recursive CTEs. This removes the graph projection completely.
It is the preferred candidate if full public Cypher is deliberately removed.

### Apache AGE

Keep the property-graph and Cypher product surface through graph label tables
inside PostgreSQL. This is the candidate if public Cypher remains required.
It still duplicates projection rows and places graph workload in the authority/
P1 database.

These are mutually exclusive product shapes. Adoption must choose one; the
implementation must not build both or introduce a graph-engine abstraction.

## Adoption trigger

Evaluate this proposal only when at least one condition is observed:

- P2 rebuild/publish/reader operations materially dominate incidents or
  operator time;
- snapshot freshness misses a contracted product need that live SQL/confirmed
  helpers cannot satisfy;
- measured deployment cost of Ladybug readers/snapshots is material; or
- the product deliberately removes public arbitrary Cypher, making a graph
  engine unnecessary.

## Proof gate

The selected candidate must pass the analysis §7 battery, including temporal
shortest-path correctness, graph-query/analytics parity, representative scale,
concurrent PostgreSQL resource isolation, hard-forget, restore and upgrade.

Apache AGE additionally must expose a verified traversal-time per-edge temporal
predicate for shortest paths. Enumerate-all-then-filter is not an acceptable
substitute. Direct SQL additionally requires an explicit binding decision to
remove the public Cypher surface.

## Non-goals

- no implementation in the P1 cutover;
- no automatic fallback between Ladybug, AGE and SQL;
- no dual graph writes for an indefinite comparison period;
- no RLS;
- no new external graph server.

## Why this remains a proposal

Ladybug already satisfies the source-verified temporal traversal contract and
isolates graph load. No measured operational pain currently justifies replacing
it, and AGE has an unresolved temporal-shortest-path proof gap. PostgreSQL
co-location is promising, but not itself an acceptance test.
