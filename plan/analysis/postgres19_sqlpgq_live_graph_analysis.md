# Analysis: PostgreSQL 19 SQL/PGQ as the live graph

**Status:** non-binding analysis for D98  
**Date:** 2026-08-27  
**Repository baseline:** `origin/main` on branch
`design/postgres19-sqlpgq-live-graph`  
**Question:** can RememberStack remove LadybugDB and P2 snapshot generations,
serve graph reads directly from PostgreSQL, begin using SQL/PGQ in PostgreSQL
19, and keep the existing vector, BM25, temporal-path, and retrieval contracts?

## 1. Conclusion

Yes, with a deliberately hybrid **query** implementation and no second graph
store:

- PostgreSQL remains the authority for entities, relations, documents, and
  structural edges.
- A PostgreSQL 19 property graph declares graph labels and properties over the
  existing live views. It copies no rows and can be queried with SQL/PGQ.
- Fixed one-hop graph shapes use SQL/PGQ in the cutover architecture.
- Depth-two and variable-length neighborhoods and shortest paths use new deployment-scoped,
  work-bounded frontier traversal functions until PostgreSQL implements
  quantified paths and shortest-path modes. The existing recursive helpers
  prove temporal semantics but are not retained implementations.
- LadybugDB, its Python dependency, Parquet export, object-store graph
  generations, local downloads, pointer swaps, public Cypher, and P2 rebuild
  readiness all go away in one cut. There are no compatibility consumers.

PostgreSQL 19 Beta 3 is usable now for a pre-user project if its risks are made
operational rather than rhetorical: pin the exact beta image digest, keep
migrations and fixtures replayable, hold no irreplaceable production data on
the beta, and treat each beta/RC/GA update as a tested major-image replacement.
The experiment in §8 proves source/API feasibility on arm64; release still
requires the complete linux/amd64 + linux/arm64 matrix because managed Hetzner
hosts are amd64:

| Capability | Result on `postgres:19beta3-trixie` |
| --- | --- |
| SQL/PGQ property graph over tables | pass |
| SQL/PGQ property graph over views | pass |
| `GRAPH_TABLE` outer correlation | implicit comma-join reference pass; explicit `LATERAL GRAPH_TABLE` syntax rejected |
| fixed-hop edge-time predicate | pass |
| quantified path `{1,2}` | expected rejection: not implemented |
| recursive shortest eligible path | pass; invalid hop 1 did not hide valid hop 2 |
| pgvector 0.8.6 HNSW | pass; index scan used |
| pg_partman 5.5.0 | pass; monthly parent created |
| pg_textsearch 1.3.1 BM25 | pass after a small PG19 compatibility patch |
| pg_textsearch upstream regression suite | **71/71 pass** |
| pglast 8.4 parsing SQL/PGQ | fail; it embeds PostgreSQL 18 grammar |

The last row means arbitrary public SQL/PGQ cannot safely ride the current
`query_sql` AST allowlist yet. Server-owned SQL/PGQ begins immediately;
public arbitrary SQL/PGQ waits for a PG19-capable `pglast` release or a
separately reviewed parser boundary. This is a parser admission limit, not a
database limit and not a reason to keep Ladybug.

## 2. What “live graph” means

PostgreSQL 19 SQL/PGQ does not create another graph engine. `CREATE PROPERTY
GRAPH` records how vertex and edge labels map onto ordinary relations.
`GRAPH_TABLE (... MATCH ... COLUMNS (...))` is a read-only relational query.
The source relations may be views; the experiment created and queried a
property graph over two ordinary PostgreSQL views.

For RememberStack, the graph source is the already-normalized query surface:

- survivor entities from `memory_v1.entities_current`;
- current semantic edges from `memory_v1.graph_edges_current`;
- historical semantic edges from
  `memory_v1.graph_edges_visible_history`;
- live documents and the existing structural cross-reference, mention, and
  document-identity views.

“Live” is therefore ordinary MVCC:

1. a relation commit becomes visible to a later graph statement without a
   rebuild, export, upload, download, or reader swap;
2. one statement sees one PostgreSQL snapshot;
3. a multi-statement assured operation runs in one `REPEATABLE READ`,
   `READ ONLY` transaction when it needs a stable compound answer;
4. explicit `valid_at` and `believed_at` still select the bitemporal subgraph;
   “live” does not mean “current facts only.”

The property graph is rebuildable *catalog metadata*, but there is no graph
data generation. Dropping it loses no graph rows because it owns none.

## 3. Current contracts that matter

The replacement is not accepted merely because it can match `(a)-[r]->(b)`.
The current implementation and binding corpus require:

1. **Authority.** PostgreSQL owns identities, facts, validity, merges, and
   evidence. A graph query never decides truth.
2. **Merge redirect.** Relations keep original entity ids; graph endpoints
   resolve through the survivor chain rather than disappearing when an entity
   is merged.
3. **Two clocks.** Eligible edges are selected with half-open valid-time and
   belief-time windows.
4. **Shortest in the eligible subgraph.** An invalid short edge must not cause
   a valid longer route to disappear.
5. **Bounded work.** Depth, returned edges, returned paths, statement time,
   rows, and bytes are all hard-clamped and truncation is disclosed.
6. **Stable edge direction.** Traversal is undirected for discovery, but each
   result preserves the fact's stored subject/object direction.
7. **Compound path integrity.** A path is returned whole or not at all.
8. **Structural navigation.** Citation chains remain supported.
9. **Zero-LLM reads.** The graph query path calls no model.
10. **Search continuity.** pgvector semantic search, pg_textsearch BM25, RRF,
    and PostgreSQL authority joins continue unchanged.

Two current contracts are architecture choices rather than product truths:
immutable P2 generations with `built_at`, and public Ladybug-flavoured Cypher.
Both exist because Ladybug is an embedded snapshot engine. Removing Ladybug
removes their reason to exist.

## 4. SQL/PGQ capability in PostgreSQL 19

Official sources retrieved 2026-08-27 are source-complete for the PostgreSQL 19
Beta 3 SQL/PGQ surface. The Beta 3 SGML tree was searched for every occurrence
of `SQL/PGQ`, `PROPERTY GRAPH`, `GRAPH_TABLE`, `propgraph`, and
`property_graph`; the corresponding rendered documentation was then read in
full:

- model and query language: [property graphs](https://www.postgresql.org/docs/19/ddl-property-graphs.html),
  [`CREATE PROPERTY GRAPH`](https://www.postgresql.org/docs/19/sql-create-property-graph.html),
  [`ALTER PROPERTY GRAPH`](https://www.postgresql.org/docs/19/sql-alter-property-graph.html),
  [`DROP PROPERTY GRAPH`](https://www.postgresql.org/docs/19/sql-drop-property-graph.html),
  [graph queries](https://www.postgresql.org/docs/19/queries-graph.html), and
  the [`GRAPH_TABLE` part of `SELECT`](https://www.postgresql.org/docs/19/sql-select.html);
- exact standard coverage: [supported SQL:2023 features](https://www.postgresql.org/docs/19/features-sql-standard.html)
  and [unsupported SQL:2023 features](https://www.postgresql.org/docs/19/unsupported-features-sql-standard.html);
- privileges and administration: [`GRANT`](https://www.postgresql.org/docs/19/sql-grant.html),
  [`REVOKE`](https://www.postgresql.org/docs/19/sql-revoke.html),
  [`COMMENT`](https://www.postgresql.org/docs/19/sql-comment.html),
  [`ALTER EXTENSION`](https://www.postgresql.org/docs/19/sql-alterextension.html),
  [`pg_get_propgraphdef`](https://www.postgresql.org/docs/19/functions-info.html),
  and [`psql` graph introspection](https://www.postgresql.org/docs/19/app-psql.html);
- all five graph catalogs:
  [`pg_propgraph_element`](https://www.postgresql.org/docs/19/catalog-pg-propgraph-element.html),
  [`pg_propgraph_element_label`](https://www.postgresql.org/docs/19/catalog-pg-propgraph-element-label.html),
  [`pg_propgraph_label`](https://www.postgresql.org/docs/19/catalog-pg-propgraph-label.html),
  [`pg_propgraph_label_property`](https://www.postgresql.org/docs/19/catalog-pg-propgraph-label-property.html),
  and [`pg_propgraph_property`](https://www.postgresql.org/docs/19/catalog-pg-propgraph-property.html);
- all ten graph information-schema views:
  [`pg_edge_table_components`](https://www.postgresql.org/docs/19/infoschema-pg-edge-table-components.html),
  [`pg_element_table_key_columns`](https://www.postgresql.org/docs/19/infoschema-pg-element-table-key-columns.html),
  [`pg_element_table_labels`](https://www.postgresql.org/docs/19/infoschema-pg-element-table-labels.html),
  [`pg_element_table_properties`](https://www.postgresql.org/docs/19/infoschema-pg-element-table-properties.html),
  [`pg_element_tables`](https://www.postgresql.org/docs/19/infoschema-pg-element-tables.html),
  [`pg_label_properties`](https://www.postgresql.org/docs/19/infoschema-pg-label-properties.html),
  [`pg_labels`](https://www.postgresql.org/docs/19/infoschema-pg-labels.html),
  [`pg_property_data_types`](https://www.postgresql.org/docs/19/infoschema-pg-property-data-types.html),
  [`pg_property_graph_privileges`](https://www.postgresql.org/docs/19/infoschema-pg-property-graph-privileges.html),
  and [`property_graphs`](https://www.postgresql.org/docs/19/infoschema-property-graphs.html);
- release status: [PostgreSQL 19 release notes](https://www.postgresql.org/docs/19/release-19.html),
  [Beta 3 announcement](https://www.postgresql.org/about/news/postgresql-186-1711-1615-1519-1424-and-19-beta-3-released-3365/),
  and [beta testing and upgrade warning](https://www.postgresql.org/developer/beta/).

The developer-meeting notes were also inspected as non-normative context:
<https://wiki.postgresql.org/wiki/PGConf.dev_2026_Graph_Database_Developer_Meeting>.

### 4.1 Exact supported subset

The conformance tables are more precise than the feature headline. PostgreSQL
19 supports fixed path concatenation; vertex, full-edge, and abbreviated-edge
patterns; directed and either-direction edge matching; element variables;
element-local and graph-pattern `WHERE`; label names and label disjunction;
property references; and `GRAPH_TABLE` with a required `COLUMNS` result. The
result is an ordinary `FROM` item: it may be aliased, joined, filtered, and
implicitly correlated to preceding `FROM` values. The grammar does not accept
an explicit `LATERAL` keyword before `GRAPH_TABLE`; the §8 experiment pins the
working comma-join form and the rejected explicit form.

The DDL subset supports persistent or temporary read-only property graphs over
tables, views, foreign tables, and similar relations; explicit composite
element keys and source/destination references; multiple labels; named column
or expression properties; view-backed element tables; add/drop/alter of
element tables, labels, and properties; ownership/rename/schema moves; and
drop dependency behavior. `CREATE PROPERTY GRAPH` stores catalog structure and
does not materialize graph rows.

The match mode is repeatable-elements: a fixed pattern is a walk unless the
query rejects repeated exposed vertex and edge identifiers itself. That is why
the two-hop parity query must explicitly reject a return to the anchor,
repeated vertices, and repeated relation ids rather than assuming simple-path
semantics.

Operationally, `SELECT` is the only property-graph privilege. A querying role
also needs access to the element relations; graph-owner privileges do not
substitute for caller privileges. There is no graph-wide default grant form in
the documented `GRANT` syntax, so migrations must grant each property graph
explicitly. Graph definitions, aliases, keys, endpoints, labels, properties,
types, and grants are inspectable through the ten information-schema views,
the five catalogs, `pg_get_propgraphdef()`, `psql \dG`, and `psql \d`.

That covers the graph explorer's shallow neighborhood and makes the schema
legible in the future SQL standard rather than in a private Cypher dialect.

### 4.2 Exact unsupported subset that affects this design

The official unsupported-feature table lists path variables; different-edge,
TRAIL, SIMPLE, and ACYCLIC modes; any/all/counted shortest search; path
alternation/union; quantified paths and edges, both bounded and unbounded;
non-local element predicates; label conjunction/negation/wildcards; graph
identity and topology predicates (`ELEMENT_ID`, `SAME`, `ALL_DIFFERENT`,
`PROPERTY_EXISTS`, source/destination tests); within-match/path-ordered
aggregates; path functions such as `PATH_LENGTH`; and the richer
`GRAPH_TABLE` row/export modes as unsupported. Inline view expressions are
also unsupported even though named views are supported element tables.

PostgreSQL 19 therefore rejects element-pattern quantifiers including
`{1,2}`, and has no native BFS/DFS or shortest-path operator. Consequently
SQL/PGQ alone cannot implement:

- “within one through N hops” for runtime N;
- shortest path to a target;
- the six-hop citation helper;
- the 30-hop engine clamp previously exposed by Ladybug;
- shortest path after filtering every edge by both temporal clocks.

Unrolling joins is acceptable at one or two hops and wrong as the general
answer. Element ids required for parity are exposed ordinary UUID properties,
not unsupported graph-element identity functions. Recursive SQL is the
missing execution primitive.

### 4.3 Why PostgreSQL frontier traversal is not a retreat

A recursive common table expression is a SQL query that seeds an initial row
set, repeatedly applies a recursive term to the previous frontier, and stops
when no row remains or an explicit bound is reached. For a graph walk:

```text
anchor:    start entity
recursive: join eligible incident edges to frontier entities
state:     depth + visited entity ids + traversed relation ids
stop:      depth/result/statement caps
```

The edge-time predicate lives in the recursive term, so an ineligible edge
never enters the frontier. Ordering reached paths by length implements bounded
breadth-first shortest path. Arrays of visited nodes prevent cycles.

RememberStack already ships the temporal core of this mechanism in migration
`p9_05_0026_graph_helpers.py`, and the invalid-short/valid-longer experiment
proves the eligible-subgraph rule. Source review found that those helpers take
no `deployment_id`, materialize the entire eligible edge view, and enumerate
simple paths through the depth cap before applying returned-result limits.
They therefore do **not** satisfy tenant-first indexed access or a bounded-work
BFS contract and must be replaced.

The replacement functions require `deployment_id`, expand one frontier level
at a time through endpoint indexes, cap frontier and examined edges, stop after
the first target-bearing level, and return truncation metadata directly. This
is meaningful implementation work, but the difficult truth/clock rule is
already proved and all graph data remains ordinary PostgreSQL rows.

## 5. Candidate comparison

### 5.1 LadybugDB

Ladybug supplies efficient embedded adjacency, recursive Cypher with an inline
edge predicate, shortest paths, PageRank, k-core, connected components, and
Louvain. Those are real advantages for deep or analytical graph workloads.

The cost is the entire P2 estate:

```text
PostgreSQL views -> export -> Ladybug build -> validate -> upload generation
-> publish pointer -> download per API node -> open read-only -> hot-swap
```

It also creates a second backup/forget/restore inventory, a Python native
dependency, a public dialect contract, and graph recall lag. The product has
no evidence that deep traversal dominates its workload. Its shipped defaults
are neighborhood 2, entity path 4, and citation path 6. The common path is
shallow and entity-anchored.

### 5.2 PostgreSQL 19 SQL/PGQ plus work-bounded frontier traversal

This option copies no graph data and removes P2 operational state. It preserves
the exact temporal-shortest-path contract and begins using the standard graph
syntax where the implementation is strong. It shares CPU, memory, I/O,
autovacuum, and failure blast radius with authority, ANN, and BM25, so it
requires resource controls rather than pretending co-location is free.

This is the selected shape.

### 5.3 evokoa/pggraph

Repository inspected at `0c853efc9b9b4123d450ee89e4eea398c8d6c101`
on 2026-08-26: <https://github.com/evokoa/pggraph>.

pgGraph reads ordinary PostgreSQL edge tables and provides native SQL graph
functions, including bounded BFS/DFS and shortest paths. Its query-time
execution uses a compact CSR graph, which is attractive when recursive SQL is
measured to be too slow.

It does **not** remove graph copies or generations. Its build produces
immutable `.pggraph` artifacts, sync logs and overlays; serving maps the
artifact into each backend. The documented filter surface supports node and
edge-type filtering, not RememberStack's arbitrary two-clock relation
predicate during shortest-path expansion. The inspected release supports
PostgreSQL 14–18, not 19.

pgGraph is therefore a possible future accelerator, not the day-one graph
model. Adoption requires all of: PostgreSQL 19 support, an edge predicate that
proves bitemporal shortest-path correctness, representative measurements that
recursive SQL misses the SLO, and an explicit decision to reintroduce graph
artifacts.

### 5.4 Apache AGE

Repository inspected at `0e30566226f017d53b7f52025803b38af3ad2b3f`
and official docs retrieved 2026-08-26:
<https://github.com/apache/age>, <https://age.apache.org/overview/>.

AGE offers Cypher inside PostgreSQL and transactional AGE graph tables. If AGE
tables are the authority, it serves live data. They are not views over the
existing `relations` authority, however: adopting AGE here means copying or
dual-writing vertices and edges into AGE label tables, or redesigning the
whole evidence model around AGE storage.

AGE's shortest-path helper accepts graph, endpoints, edge types, direction,
and hop bounds, but not the arbitrary bitemporal predicate needed during BFS.
AGE supports PostgreSQL through 18 in the inspected release line, not 19, and
does not supply the current analytics set. Its main advantage—public
Cypher—is no longer a requirement. It adds more than it removes.

### 5.5 Other options

- closure tables duplicate reachability and are painful under bitemporal
  changes and merge redirects;
- unrolled joins are useful only for fixed very small K;
- pgRouting is a routing/geospatial toolkit, not this property graph;
- GraphBLAS/OneSparse are analytics-oriented;
- DuckPGQ is tied to DuckDB;
- PuppyGraph adds another server and exposes Cypher/Gremlin;
- `ltree` models trees, not a general cyclic multigraph.

## 6. Retrieval consequences

### 6.1 What improves

- A just-committed eligible relation is immediately traversable; graph
  projection lag can no longer cost recall.
- Graph candidates and authority rows are read in the same database snapshot.
  The P2 “nominate, hydrate, maybe drop the path” correctness dance disappears.
- Merge, unmerge, retraction, hard-forget, and document deletion become visible
  through the existing views without a rebuild.
- `Freshness` reports the applied valid/belief instants and live PostgreSQL
  transaction, not a graph `built_at` age.
- D97's default entity neighborhood plus fact-text retrieval can join graph
  edges, entity names, pgvector candidates, and BM25 candidates without a
  cross-store round trip.

### 6.2 What becomes a shared-resource concern

A deep or high-fan-out traversal now competes with ingest, HNSW, BM25,
autovacuum, and ordinary authority reads. The required controls are:

- separate query connection pool/role;
- `READ ONLY` transactions;
- hard statement timeout;
- interactive depth 4 for neighborhood and 6 for paths initially;
- edge/path/row/byte caps enforced inside SQL and again at the API boundary;
- cycle prevention and deterministic ordering;
- query telemetry for elapsed time, rows, frontier/cap truncation, and timeout;
- admission/concurrency budgets already used by the open query sandbox;
- no automatic retry of an expensive traversal after timeout;
- later read-replica routing only if measured load requires it and temporal
  freshness semantics are designed explicitly.

The former Ladybug clamp of 30 is not a sensible default promise for a shared
OLTP/search database. The public typed helpers already clamp to 4/6. Removing
the unused 30-hop promise is a safety improvement, not a functional loss in
the shipped API.

### 6.3 Global analytics

PageRank, k-core, weakly connected components, and Louvain are not used on the
hot retrieval path. Current code computes them only as a post-P2-build pass;
`graph_degree` feeds merge blast radius and `entity_graph_metrics.community_id`
is a real K routing input for topic/community pages.

The simple cut is:

- compute merge blast radius from live current edges in the clustering query
  instead of a cached P2 degree;
- deliberately remove snapshot-keyed PageRank/k-core/WCC/Louvain, community
  labeling, the K `community` scope/rule, `community_changed`, and default topic
  pages from the live-graph contract;
- reintroduce global analytics only behind a separate measured design, using a
  transaction-consistent transient graph or a future accelerator, never by
  quietly restoring P2 serving snapshots.

No implemented retrieval ranker currently reads PageRank, but community had a
K-plane consumer. D98 explicitly supersedes D11 and removes that product
surface rather than misclassifying it as unused retrieval state.

## 7. PostgreSQL 19 beta and upgrade posture

Why take the beta now:

- the project has no users and no irreplaceable production database;
- SQL/PGQ is feature-frozen in the beta line;
- waiting for GA would preserve an architecture already chosen for removal;
- early use finds extension/parser/image problems while they are cheap.

Why not treat the beta like a normal production minor:

- PostgreSQL explicitly warns beta users not to use beta databases for
  production data;
- beta/RC catalog or on-disk changes can require dump/restore or a new cluster;
- extension compatibility may change between beta builds;
- the image tag is mutable unless a digest is pinned.

Operational contract:

1. pin `postgres:19beta3-trixie` by digest and pin all extension sources or
   packages;
2. preserve migrations, seed fixtures, and export/import commands as the
   recovery path;
3. use disposable development/dogfood data only until GA;
4. on every Beta 4/RC/GA candidate, build the image and run migrations,
   pgvector/BM25/partman checks, graph correctness checks, and the full suite;
5. replace the beta cluster rather than promising in-place major-beta upgrade;
6. move to RC/GA promptly; do not retain Beta 3 as a long-lived supported
   branch;
7. support one PostgreSQL major only. There is no PG18 fallback.

## 8. Experiment record

Run locally on 2026-08-26, Docker Desktop arm64, image
`postgres:19beta3-trixie@sha256:a48b19841e04b35b72a25e9a94314ac80546d32b5e2e3cd9279390cbd8a99572`.

Pinned extension sources/packages:

- pgvector 0.8.6, commit
  `8ee86c96f0fd72390f890aa8a336fda6d3ab4c6c`, PGDG package
  `postgresql-19-pgvector=0.8.6-1.pgdg13+1`;
- pg_partman 5.5.0, commit
  `f7e83b9c441c7e97066d815bbe14e02a9dc5ff94`, PGDG package
  `postgresql-19-partman=5.5.0-1.pgdg13+1`;
- pg_textsearch 1.3.1, commit
  `578ff529894992fb9e67cae4c69424e65c84868e`, source archive SHA-256
  `8632f91231251dc3e19395ef6a0d4d158d5f5920ba420691471771418e2a7cc7`.

PGDG already publishes the first two exact versions for PostgreSQL 19 Beta 3.
pg_textsearch officially supports 17/18 and has no PG19 binary. Unmodified
source failed at PostgreSQL 19 API changes. A forward-only patch was sufficient:
the checked-in patch has SHA-256
`a8c97f39714ab0193c82fcda3709d3e4df54bcc7f2804fde8f970710484dbdc6`.
Upstream PR [#460](https://github.com/timescale/pg_textsearch/pull/460)
(retrieved 2026-08-27) now tracks PostgreSQL 19 beta support but remains
unmerged, so the image retains and verifies the local delta rather than
silently following the PR head.

- include headers now required for `index_open`, tuple construction, and type
  OIDs;
- pass the new scan-options argument to `table_beginscan_tidrange`;
- accept PostgreSQL 19 planner-hook and const jumble-state signatures;
- replace removed fixed-tranche registration with one dynamically registered
  extension tranche;
- include the standard limits header.

After that patch:

- PostgreSQL started with
  `shared_preload_libraries=pg_textsearch,pg_partman_bgw`;
- `CREATE EXTENSION vector`, `pg_textsearch`, and `pg_partman` succeeded;
- HNSW and BM25 `EXPLAIN` each selected the intended index;
- pg_partman registered a monthly range parent;
- SQL/PGQ selected only the temporally valid outgoing edge;
- a recursive CTE returned edge path `{11,12}` at depth 2 while direct edge
  `10` was invalid at the requested instant;
- a property graph over views created and queried successfully;
- `GRAPH_TABLE` could reference an earlier comma-joined guard implicitly, while
  explicit `LATERAL GRAPH_TABLE` was a syntax error;
- `{1,2}` produced `ERROR: element pattern quantifier is not supported`;
- all 71 upstream pg_textsearch SQL regression tests passed.

These results do not establish amd64 compatibility. The release gate repeats
the full matrix on linux/amd64 (blocking for managed Hetzner hosts) and
linux/arm64 and records per-architecture artifacts/image digests.

`pglast 8.4` reported parse errors at `PROPERTY` and `MATCH`. PyPI exposed no
newer release on the experiment date. The public AST gate therefore stays on
its current PostgreSQL-18 grammar until an independently reviewed PG19 parser
is available. The release must also diff PG18/PG19 keywords, AST forms, and
admitted built-ins and prove new PG19 syntax/functions default-deny; the parser
version mismatch is a broader safety boundary, not only a PGQ limitation.

### 8.1 Post-merge one-hop provenance-plan finding

The first D97 composition test after the PostgreSQL graph cut exposed a plan
shape that the smaller graph fixture had not forced. On 2026-08-27, the Batch C
retrieval fixture contained 38 entities, 38 relations, 193 relation-evidence
rows, and 234 documents. Its admitted one-hop `GRAPH_TABLE` query hit the
five-second graph `statement_timeout` for both the current and explicitly
clocked history shapes. This was not dense-hub refusal: the relational guard
admitted two incident edges.

`EXPLAIN (FORMAT JSON)` showed that PostgreSQL 19 Beta 3 repeatedly inlined the
correlated provenance `EXISTS` from
`rememberstack_graph_internal.entities_live` through the vertex and endpoint
copies produced by the PGQ rewrite. The resulting plan repeatedly expanded the
survivor and partitioned-resolution branches before reaching the anchored
relation. Moving predicates into element patterns and splitting the undirected
match into directed shapes did not remove that expansion. A raw-table property
graph completed in milliseconds, but it was rejected because it would bypass
the surviving-provenance membership rule.

A semantic-equivalent prototype changed only the private entity view's SQL
shape: it materialized the complete `(deployment_id, survivor_entity_id)`
provenance keyset once inside each view expansion, then semi-joined active
entities to that keyset. The property-graph keys, rows, labels, grants, relation
source, and hydration contract did not change. The same production
`GraphQueries.neighborhood` call then completed in approximately 0.9 seconds
and returned the two admitted edges. This is the chosen repair because it
retains the D48 absence rule and D98's PGQ/current-snapshot semantics while
removing repeated evaluation rather than raising a timeout or introducing a
fallback traversal.

## 9. Recommendation and design inputs

Accept the live PostgreSQL graph and direct PG19 Beta 3 cut with these binding
inputs:

- one PostgreSQL authority/search/graph store;
- SQL/PGQ property graph over views, no graph row copies;
- SQL/PGQ for fixed shallow patterns, work-bounded frontier functions for
  variable/shortest traversal;
- public Cypher removed with Ladybug;
- public graph helpers keep their operation names but intentionally change
  signature: required `deployment_id` first plus explicit bounded-work/result
  inputs and direct truncation metadata;
- public arbitrary SQL/PGQ deferred only at the pglast admission boundary;
- no P2 graph generations, object paths, reader cache, or P2 readiness;
- current live edge degree replaces cached snapshot degree;
- global graph analytics removed pending measured need;
- exact beta image/source pins and replace/replay upgrades;
- a focused extension compatibility test becomes a release gate.

This analysis supersedes the conclusion of
`postgresql_p2_graph_analysis.md`. That older document remains useful history,
but it evaluated PostgreSQL 18 before the operator chose public Cypher as
optional, live graph as preferable, and early PostgreSQL 19 adoption as an
acceptable pre-user risk.
