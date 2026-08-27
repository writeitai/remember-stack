# Phase 4 — Projections

> **Historical completion record; graph portion superseded by D98
> (2026-08-27).** WP-4.1–WP-4.4 record what the former Ladybug/P2 implementation
> delivered and are not current architecture or work instructions. The binding
> replacement is [`postgres19_sqlpgq_live_graph.md`](postgres19_sqlpgq_live_graph.md):
> PostgreSQL 19 live graph views, SQL/PGQ, and bounded recursive SQL. WP-4.5 and
> WP-4.6 remain the P3 history and current P3 boundary.

**Goal:** the derived, rebuildable read surfaces: the graph, the corpus filesystem,
communities.

**Entry gates:** none beyond spikes (run inside).
**Exit criteria:** P2 rebuild → validate → snapshot → reader hot-swap cycle runs on schedule;
graph scenario classes (S17–S22) pass incl. multi-hop as-of; P3 mounts (corpus fs + artifacts
+ raw off-path) browsable with stable lineage paths (S44, S56, S59); community writeback
feeds `entity_graph_metrics`.

**Exit: met (2026-07-19).** The P2 cycle runs end to end with both validation gates and a
hot-swapping reader (PR #101); S17–S22 pass on the `graph` primitive incl. multi-hop as-of
(PR #102); Louvain/PageRank/k-core/WCC write back to `entity_graph_metrics` natively (PR #103,
**D72**); the corpus filesystem is browsable with Tier-1 paths proven stable across rebuilds
and the three D51 mount guardrails proven rather than assumed (PR #104). Six engine
constraints were discovered live and pinned as canaries along the way
(`plan/analysis/p2_spike_battery.md`). Scheduling the rebuild cadence rides the carried
GCP/ops work; nothing in the cycle depends on it.

| WP | Goal | Reads | Depends | Deliverable | Acceptance | Status |
|---|---|---|---|---|---|---|
| WP-4.1 | **D44 spike battery first**: UUID PK smoke, ATTACH throughput, merge-recursion gate, as-of path perf, retention, NULL timestamps | questions #20a; p2 §5b; ladybug rulebooks (analysis) | Phase 3 | spike report | all six recorded; transport decision confirmed | done (PR #100; `plan/analysis/p2_spike_battery.md` — ATTACH dead on capability grounds, Parquet transport confirmed) |
| WP-4.2 | `v_graph_*` views + rebuild worker (Parquet hop) + validation gate + snapshots + reader hot-swap | p2 §2, §5; schema §10.A; D7, D44 | WP-4.1 | P2 pipeline | rebuild on toy corpus; merge-redirect + keep-retracted tests | done (PR #101) |
| WP-4.3 | Graph retrieval: neighborhood/path primitives + as-of inline filters + graph-distance rerank | p2 §4, §6; retrieval §3 | WP-4.2 | `graph` primitive | S17–S22 green | done (PR #102) |
| WP-4.4 | Communities **native** pass + writeback (D11 → **D72**: Louvain ships on the deployed engine) | p2 §7; D11/D72 | WP-4.2 | community job | assignments in PG; K rule-key kind usable | done (PR #103) |
| WP-4.5 | P3 builder: placement reconciliation, tree, stubs, `_index.md`/`llms.txt` (+ K freshness mirror), snapshot swap | e0 §6; `p3_agent_navigation.md`; D40, D49 | Phase 3 | P3 worker | stable-lineage-path test across rebuilds; browse S44 | done (PR #104; K freshness mirror lands with Plane K in Phase 6 — nothing to mirror until pages exist) |
| WP-4.6 | Mount provisioning: corpusfs + artifacts + raw (off-path, audit-logged, mime storage classes) | e0 §2, §5; D51 | WP-4.5 | mount config | S56/S59 walkthroughs; audit log visible | done (PR #104) |
