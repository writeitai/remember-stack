# PostgreSQL 19 SQL/PGQ Live Graph — Implementation Plan

**Status:** implementation complete; release execution gates remain open  
**Date:** 2026-08-27  
**Binding design:** [`../designs/p2_graph_design.md`](../designs/p2_graph_design.md)  
**Analysis:**
[`../analysis/postgres19_sqlpgq_live_graph_analysis.md`](../analysis/postgres19_sqlpgq_live_graph_analysis.md)

## 1. Delivery rule

This is a direct pre-user cutover. There is no dual write, compatibility mode,
data conversion, Ladybug fallback, or dormant Cypher endpoint. Each batch ends
in a runnable repository and is independently reviewable, but D98 is complete
only when the removal audit and end-to-end gates pass.

## 2. Batch A — PostgreSQL 19 reference image

1. Change `Dockerfile.postgres` from the PostgreSQL 18 pgvector image to the
   exact official PostgreSQL 19 Beta 3 digest used by the experiment.
2. Install pinned PGDG `postgresql-19-pgvector=0.8.6-1.pgdg13+1` and
   `postgresql-19-partman=5.5.0-1.pgdg13+1` packages.
3. Build pg_textsearch 1.3.1 from pinned upstream commit
   `578ff529894992fb9e67cae4c69424e65c84868e` with the minimal PG19
   compatibility patch captured in-repository with source/checksum comments.
   Do not use a floating source branch.
4. Keep `shared_preload_libraries=pg_textsearch,pg_partman_bgw` and prove both
   load before migrations.
5. Update version assertions, compose/dev docs, and image metadata from 18 to
   19. Document that prerelease volumes are disposable and image bumps replay
   migrations from empty storage.
6. Preserve pg_textsearch's PostgreSQL License notice; record the patch owner
   (OSS release engineering), upstream compatibility issue, source and patch
   checksums, compiler/toolchain, and built artifact checksums.
7. Gate on **both** linux/amd64 and linux/arm64: image build and per-arch
   digest; `SHOW server_version`; extension creation; upstream pg_textsearch
   71-test suite; HNSW plan/result; BM25 plan/ranking; pg_partman parent;
   SQL/PGQ and traversal smoke. Amd64 is blocking for managed deployment.

Rollback inside the prerelease period means selecting the last proven image
and recreating the empty/dev database. It does not reuse an unverified PG19
data directory across prerelease versions.

## 3. Batch B — property-graph schema and query contracts

1. Add one Alembic revision after head that:
   - creates `memory_v1.memory_current` over current entity/document,
     relation, mention, and cross-reference views;
   - creates `memory_v1.memory_history` over survivor entities and visible
     historical relation edges;
   - uses composite deployment-scoped keys and explicit properties;
   - grants only the required property-graph `SELECT` privileges;
   - deletes any obsolete P1/P2 snapshot rows, creates a P3-only replacement
     `projection_plane` type, rewrites `projection_snapshots.plane`, swaps
     types, and drops the old type because PostgreSQL cannot drop enum values
     in place; D94 P1 is current in-database state, not a snapshot generation;
   - removes the now-unused `community_algorithm` type and keeps
     `snapshot_status` only for P3 snapshot records;
   - supplies a tested downgrade that recreates the old enum/schema shape
     without pretending removed Ladybug data can be restored.
2. Replace—not retain—`graph_neighborhood` and `graph_path`, and add
   `graph_citation_path`, with required `deployment_id` first. Implement
   a `STABLE`, `SECURITY INVOKER`, SELECT-only PL/pgSQL level loop with bounded
   function-local arrays and direct deployment+endpoint index joins—no temp or
   regular table writes; remove `edges AS MATERIALIZED`; add hard frontier, examined-edge,
   depth, returned-edge/path, time, and temp budgets; stop entity path after
   the first target-bearing level; emit data rows plus exactly one terminal
   status row with truncation/budget metadata. Replace the sticky
   `graph_cap_reached` GUC with the one-statement status-row/right-join AST
   rewrite specified by the open-query design, including a status-only carrier
   for empty caller results. Citation
   traversal is directed.
3. Add static, parameterized one- and two-hop SQL/PGQ statements in a small
   typed module for current and as-of (`memory_history`) shapes. Specify anchor
   and cycle exclusion, dedup, minimum-hop/lexicographic path selection,
   budgets, and truncation so depths one/two are byte-identical to canonical
   frontier results. Put cross-element UUID exclusions only in the graph-pattern
   `WHERE` after the full `MATCH`. Execute a separate static
   deployment/anchor-first indexed degree guard in the same transaction and
   branch before sending `GRAPH_TABLE`, stopping each fixed level at
   `expansion_budget + 1`; PG19 has no inside-match work counter and planner
   short-circuit is not an admission boundary. The admitted PGQ uses PG19's
   implicit comma-join correlation—never an explicit `LATERAL` keyword. No SQL
   text generation and no arbitrary PGQ admission.
4. Update query-space catalog/discovery metadata to describe live graph
   helpers and graph catalog objects without claiming the current pglast gate
   can parse PGQ. Update the exhaustive `memory_v1` manifest with all three
   deployment-first signatures, frontier/expansion/result/time/temp limits,
   terminal-status fields, and the explicit D98 pre-release v1 replacement
   exception. Rewrite `examples.multi_hop_context`,
   `examples.graph_neighborhood`, and `examples.graph_path` for the new
   signatures, budgets, and terminal-status contract; add
   `examples.graph_citation_path`. Roll their manifest identities and move
   prior active saved versions to `pending_revalidation`.
5. Preserve D41 current views and no-clock helper calls on one captured
   `statement_timestamp()` per statement. For multi-statement typed operations,
   capture one operation instant, pass it explicitly as both graph clocks, and
   use `memory_history` PGQ so search/graph/hydration share that instant inside
   one `REPEATABLE READ, READ ONLY` transaction.
6. Add operator-only `remember ops graph-catalog ensure`: compare the exact
   semantic contract through all ten PG19 graph information-schema views,
   expose `pg_get_propgraphdef()` for diagnostics, transactionally recreate
   only property-graph metadata and explicit grants, and never touch authority
   rows. Missing metadata otherwise fails readiness.
7. Test property-graph grants twice: PostgreSQL accepts `SELECT ON PROPERTY
   GRAPH`, and an invoker cannot use graph-owner privileges to bypass missing
   underlying-view grants.
8. Diff PG18/PG19 keywords, supported AST nodes, and admitted built-ins; add
   negative tests proving all new/unrecognized PG19 syntax/functions default
   deny through the PG18 pglast gate.
9. Gate: fresh migration and downgrade/upgrade cycle; `\dG+`,
   `pg_get_propgraphdef()`, graph catalogs, and all ten information-schema
   views; only the documented PG19 supported feature subset in shipped SQL;
   explicit negative smoke for quantified/shortest/path-variable syntax;
   property graph over views; current visibility immediately after commit;
   uniqueness of every declared view `KEY`; tenant isolation; structural edge
   direction; repeatable-element walk exclusion; fixed current/as-of two-hop
   parity for under-budget inputs; separate zero-data PGQ and
   deterministic-prefix helper over-budget outcomes; proof that guard refusal
   never executes PGQ; separately anchored relational-guard and PGQ plans;
   frontier work bounds and early termination;
   `READ ONLY` + `STABLE` execution with no temp/regular writes; terminal status
   capture when caller rows are empty, filtered, joined, or aggregated; reserved
   `__rememberstack_` CTE/relation/column/output identifier rejection;
   `QueryResult` truncation without GUC/session state;
   privileges; parser still rejects public PGQ.

## 4. Batch C — typed graph API cutover

1. Rewrite `surfaces/graph_queries.py` around a PostgreSQL connection provider:
   - neighborhood depth 1–2 uses the fixed SQL/PGQ statements;
   - depth 3–4 uses `memory_v1.graph_neighborhood`; temporal depth 1–2 uses
     `memory_history` SQL/PGQ and temporal depth 3–4 uses the helper;
   - entity path uses `memory_v1.graph_path`;
   - citation path uses its bounded recursive PostgreSQL helper;
   - responses preserve `Envelope`, graph node/edge/path grains, applied
     temporal scope, deterministic paging, complete paths, and explicit
     truncation.
2. Remove Ladybug imports, engine retries/markers, snapshot readers, local
   graph path discovery, `built_at` freshness, and the 30-hop engine clamp.
3. Wire graph connections through the existing PostgreSQL dependency boundary.
   Add graph-specific read-only transaction, timeout, and bounded pool config;
   do not create a generalized graph-engine abstraction.
4. Update retrieval execution so search, graph expansion, and hydration use one
   `REPEATABLE READ, READ ONLY` transaction where an assured multi-statement
   operation needs a common snapshot, with one stable evaluation instant.
5. Gate: typed neighborhood/path/citation suites; invalid-short/valid-longer
   temporal route; SQL/PGQ/frontier byte parity at depths one/two; current/
   history parity with direct views; cross-deployment UUID collision;
   high-fanout expansion-budget truncation; function-local-state A→B→A pool reuse;
   newly committed relation visibility; paging/truncation; timeout/cancel;
   retrieval envelopes and channel provenance.

## 5. Batch D — delete the Ladybug and public Cypher product

1. Delete `surfaces/query_sandbox/cypher.py`, `cypher_executor.py`, public
   `query_cypher`/`explain_cypher` HTTP, SDK, CLI, and MCP wiring, schemas,
   errors, audit fields, examples, generated prose, and tests.
2. Remove `ladybug==0.18.2` from every dependency group and regenerate the lock.
3. Delete snapshot reader/build/export code, the P2 worker and analytics worker,
   self-host graph paths/config, object-store graph prefix handling, manifests,
   readiness gates, and graph generation operations.
4. Remove `P2_graph` projection snapshots, `communities`,
   `entity_graph_metrics`, and their migrations/runtime contracts. Remove K
   `community` rules, `community_changed`, topic/community pages,
   `_SELECT_COMMUNITY_MEMBERS`, schema/manifest types, fixtures, and docs.
   Preserve entity/source/manual K routing, P1, and P3.
5. Replace cached latest-P2 `graph_degree` with a live relation-adjacency
   expression/query. Delete PageRank, k-core, WCC, Louvain, and community
   ranking/topic dependencies rather than returning permanent zeros under the
   old names.
6. Remove Ladybug/P2 backup, restore, and hard-forget branches. Prove the normal
   PostgreSQL deletion/views and backup contract cover graph visibility.
7. Gate: repository removal searches; dependency lock audit; all public schema
   snapshots regenerated; no route advertises Cypher or graph generations;
   hard-forget/restore tests cover the one-store contract.
8. Fulfil D66 in the same cut: update `website/src/app/docs/**`, especially
   `retrieval/open-query/page.mdx`, `architecture/page.mdx`, and
   `project-status/page.mdx`; update README/quickstart/operations examples.
   A repository gate rejects current-product prose containing the removed
   `/query/cypher`, `query_cypher`, `explain_cypher`, `PROJECT_GRAPH_CYPHER`, Ladybug/P2 snapshot,
   graph `built_at`/generation, PostgreSQL 18, or community-product claims.
   Historical analysis/review records are allowlisted explicitly, never by a
   broad directory exclusion. Each behavioral batch updates its own public page
   in the same commit; this Batch D sweep removes the deleted surface.

## 6. Batch E — retrieval, readiness, and operations reconciliation

1. Remove P2 freshness/degradation fields and conditions. Introduce only the
   live-graph health/provenance fields required by the binding design.
2. Update retrieval ranking and the clustering blast-radius query to calculate
   degree from live deployment-scoped adjacency. Do not add the count to
   `entities_current`. Remove PageRank/community inputs and confirm graph
   syntax choice does not change scores.
3. Replace P2 rebuild/readiness/operations CLI surfaces with PostgreSQL graph
   catalog and smoke checks. Explicitly update
   `src/rememberstack/spine/readiness.py` so only `P3_corpusfs` is a projection
   and the graph has catalog/query health. Delete the P2 hang/runbook; add a compact live
   graph operations section covering timeouts, slow queries, pool saturation,
   catalog recreation, and restore.
4. Update packaging profiles and README topology. Self-host contains one
   PostgreSQL 19 service with pgvector, pg_textsearch, pg_partman, and SQL/PGQ;
   no Ladybug native wheel or graph volume.
5. Gate: readiness from empty storage; degraded database behavior; metrics do
   not contain fact text; no generation/built-at fields; cancellation does not
   leave long transactions; restore recreates property graphs.

## 7. Batch F — performance and release proof

1. Seed deterministic small correctness fixtures plus representative skew:
   tenant-isolated chains, cycles, a high-degree hub (separate over-budget
   contracts, not byte parity), invalid direct plus valid
   longer route, historical intervals, mention edges, and citation chains.
2. Capture `EXPLAIN (ANALYZE, BUFFERS)` for PGQ one/two-hop and frontier
   two/four-hop, shortest path, and citation path. Fail on an unanchored tenant
   relation scan, any expansion beyond the declared work budget, or unexpected
   temp spill at representative target scale.
3. Run bounded concurrent graph reads with authority writes, HNSW, and BM25.
   Record hardware, cardinality, query mix, pool size, timeouts, p50/p95/p99,
   write/search latency change, temp bytes, and cancellations. This is a
   resource-isolation gate, not a universal scale claim.
4. Run fresh install, complete test/lint/type suite, logical backup/restore,
   forget drill, and image restart. Inspect restored property graphs and rerun
   all four capabilities.
5. Run Claude Opus and Antigravity implementation reviews with the exact user
   commands. Resolve every blocker/high finding and rerun reviewers on the
   changes until both return approval or only explicitly accepted non-blocking
   follow-up.
6. Preserve the design-review command transcripts, verdicts, findings, and
   dispositions under `design/reviews/`; D98's round-one record is
   `design/reviews/postgres19_live_graph_design_review_round1.md` and final
   design-gate record is
   `design/reviews/postgres19_live_graph_design_review_round2.md`.

## 8. Cross-batch acceptance checklist

- [ ] PostgreSQL 19 image and all three extensions are pinned and proven.
- [ ] SQL/PGQ is exercised by production server-owned code, not only a smoke.
- [ ] Variable/shortest traversal is deployment-scoped, frontier/work bounded,
      early-terminating, and applies per-edge time filtering.
- [ ] A committed graph change is visible without a build or generation.
- [ ] Retrieval freshness, provenance, and transaction behavior match D98.
- [ ] No Ladybug dependency/runtime/test path remains.
- [ ] No public Cypher or misleading public arbitrary PGQ path remains.
- [ ] No P2 data snapshot/generation/analytics worker remains.
- [ ] HNSW, BM25, pg_partman, forget, backup, and restore pass on PG19.
- [ ] Documentation and examples describe only the shipped architecture.
- [ ] Claude and Antigravity approve design and implementation after fixes.

## 9. Explicit follow-up triggers, not current work

- Adopt a PG19-capable `pglast` and design public arbitrary SQL/PGQ only when a
  caller needs it.
- Evaluate pgGraph or another native BFS implementation only when recorded
  representative queries miss the supported SLO after SQL/index tuning and the
  candidate preserves per-edge bitemporal eligibility.
- Reintroduce global graph analytics or community/topic K routing only for a
  named product consumer with freshness, resource, schema, and migration
  contracts.
- Upgrade from PostgreSQL 19 prerelease to each later beta/RC/GA immediately
  after the Batch A/F capability and restore gates pass.
