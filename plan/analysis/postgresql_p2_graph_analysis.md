# Analysis: should P2 also move into PostgreSQL?

**Status:** non-binding analysis supporting the D13 reaffirmation
**Date:** 2026-08-14
**Repository baseline:** `design/pgvector-retrieval-plane` at `9451a598`, based
on `origin/main` `cc8cb23e`
**Question:** after D94 moves P1 search into PostgreSQL, should P2 graph
traversal also move there through Apache AGE or another extension?

## 1. Conclusion up front

Not yet. Keep LadybugDB as the binding P2 engine while retaining a measured
PostgreSQL replacement proposal.

Apache AGE is a real option, not a toy: it is an Apache top-level project,
Apache-2.0 licensed, supports PostgreSQL 18, stores property graphs in
PostgreSQL tables, runs Cypher through a SQL set-returning function and shares
PostgreSQL ACID/backup machinery. It is the only current PostgreSQL extension
that plausibly preserves the product's read-only Cypher surface.

It does not currently prove one load-bearing RememberStack requirement:
shortest path **inside a bi-temporally filtered subgraph**. The PG18 v1.8.0
source's shortest-path functions accept edge types, direction and hop bounds,
but no arbitrary per-edge predicate. AGE variable-length paths can be filtered
after enumeration, but that is not equivalent: choosing a shortest path first
and rejecting it afterwards can miss a longer valid path, while enumerating all
paths before sorting can explode with graph fan-out.

There is also a systems reason not to infer “P1 moved, therefore P2 should.” P1
must combine semantic/BM25 nominations with PostgreSQL authority filters on
every query; co-location removes a correctness-critical round trip. P2 is an
explicit point-in-time analytical projection. Its local read-only snapshot
isolates path/analytics CPU and engine faults from the PostgreSQL instance that
now owns authority, ANN and BM25. AGE removes snapshot transport, but it places
graph work inside that shared database and still stores a separate graph
projection.

If the product ever drops full public Cypher and needs only the bounded
`graph_neighborhood`/`graph_path` helpers, direct PostgreSQL recursive CTEs over
authoritative relations are the simpler alternative to evaluate first. If full
Cypher remains required, AGE is the PostgreSQL candidate to spike.

## 2. The contract an alternative must preserve

The binding P2 behavior is not merely “can traverse edges.” Relevant contracts
are:

- P2 is derived; PostgreSQL relations/observations remain authority
  (`plan/designs/p2_graph_design.md` §1).
- Only relations project as semantic edges; evidence remains relational and
  hydrates by stable `relation_id` (§2).
- current and two-axis as-of traversal apply temporal predicates **during**
  expansion, including shortest-path search (§4 and
  `plan/analysis/ladybug_query_semantics.md` R2–R3).
- serving exposes a complete immutable generation with `built_at`, not a graph
  assembled across changing fact states (§5).
- open Cypher is read-only, bounded, deployment-bound and dialect-pinned
  (`plan/designs/open_query_space_design.md` §3.5).
- graph helpers and Cypher carry typed freshness/confirmation semantics rather
  than pretending a projection is live authority.
- PageRank, Louvain and bounded path/neighborhood workloads have explicit
  uses; an engine change cannot silently drop them.

Any proposed engine must pass those contracts, not only a CRUD demo.

## 3. What PostgreSQL 18 changes

PostgreSQL 18 is the current stable major release. PostgreSQL 19 is still a
beta line as of this analysis. The reference design should therefore pin major
18 and continuously take current 18.x security/bug-fix releases rather than
supporting a 17/18 range. PostgreSQL 18.4 was the current minor release checked
on 2026-08-14.

PostgreSQL 18 itself does not add a documented property-graph/SQL-PGQ surface.
It does provide recursive CTEs with `SEARCH` and `CYCLE`, which are sufficient
to implement bounded graph helpers over relational edges. That is a query
building block, not a general Cypher database.

Sources retrieved 2026-08-14:

- current major: <https://www.postgresql.org/about/press/faq/>
- current patched release: <https://www.postgresql.org/about/news/postgresql-184-1710-1614-1518-and-1423-released-3297/>
- recursive query semantics: <https://www.postgresql.org/docs/18/queries-with.html>
- PostgreSQL 18 release notes: <https://www.postgresql.org/docs/18/release-18.html>

## 4. Apache AGE

### 4.1 What it genuinely buys

AGE is a PostgreSQL extension implementing an openCypher-derived property
graph surface. `cypher(graph_name, query, parameters)` returns a PostgreSQL row
set, so SQL and Cypher can compose in one transaction. AGE uses PostgreSQL's
transaction/cache/storage layers and supports PG18.

Creating an AGE graph creates a PostgreSQL namespace. Vertex and edge labels
become PostgreSQL tables; values are exposed as `agtype`, with AGE-managed
`graphid` identifiers. Property indexes use PostgreSQL indexes. Consequently:

- graph rows participate in PostgreSQL WAL, PITR and operational tooling;
- authority confirmation can be composed in one PostgreSQL statement;
- build-new-graph/validate/switch/drop-old can model P2 generations;
- no graph snapshot needs downloading to each API node;
- hard-forget and backup inventory lose one physical storage technology.

Official sources retrieved 2026-08-14:

- architecture/license: <https://age.apache.org/overview/>
- supported PostgreSQL versions/setup: <https://github.com/apache/age>
- graph storage: <https://age.apache.org/age-manual/master/intro/graphs.html>
- Cypher/SQL surface: <https://age.apache.org/age-manual/master/>
- PG18 releases: <https://github.com/apache/age/releases>

### 4.2 What it does not buy automatically

An AGE graph is not a view over the existing `relations` table. It is another
set of label tables containing projected vertices/edges and property maps.
Co-location removes a physical database boundary, but not the logical
projection, generation, repair or parity contracts.

Keeping the projection synchronously current would couple every fact/merge/
forget transaction to graph writes. Keeping it asynchronous preserves recall
lag and generation logic. Either choice must be explicit; installing AGE does
not make drift disappear.

AGE also moves arbitrary bounded Cypher and traversal CPU into the PostgreSQL
server that now owns the spine plus P1 ANN/BM25. A bad native-extension fault
or resource-heavy path query affects a database backend and can contend with
ingest/search/autovacuum. Ladybug faults and analytical CPU stay in query
processes over replaceable local snapshots.

### 4.3 Temporal shortest-path gap

The PG18 branch was inspected at commit
`e43dc1a12b78fba4acef9835b2b10379b8d243b4` (the v1.8.0 release commit).

`age_shortest_path` and `age_all_shortest_paths` are BFS helpers over AGE's
cached global adjacency. Their public arguments are:

`graph, start, end, edge_types, direction, min_hops, max_hops`.

The implementation explicitly bypasses the variable-length-edge grammar and
offers no arbitrary relationship-property predicate. Source:
<https://github.com/apache/age/blob/e43dc1a12b78fba4acef9835b2b10379b8d243b4/src/backend/utils/adt/age_vle.c>.

AGE variable-length patterns support exact property maps and path materializing
functions such as `relationships(path)`. The inspected tests do not establish a
range predicate evaluated on each edge **before** BFS expansion. An outer
`WHERE all(...)` is insufficient for shortest path: it can filter away the
shortest path AGE selected without asking AGE to find the shortest path among
eligible edges.

This is a current proof gap, not a claim that AGE can never support the
contract. A replacement spike may close it with a different verified query or
new upstream capability. Until then, the engine cannot replace Ladybug's
source-verified inline traversal predicate.

### 4.4 Other maturity/fit observations

- AGE's PG18 artifacts are currently tagged with `-rc0`, even when described as
  the 1.8.0 release.
- Current public documentation is largely a moving `master` manual; the site
  exposes little versioned documentation beyond an old 0.6 manual.
- No PageRank, Louvain, K-core or connected-component implementation was found
  in the inspected PG18 core source. NetworkX integration is not equivalent to
  an in-database analytical contract.
- AGE requires `LOAD 'age'` per backend session and careful `search_path`
  handling. Our sandbox would use fully qualified objects and restricted roles;
  RLS remains forbidden.

These are reasons for a focused spike, not categorical rejection.

## 5. Other alternatives

### A. Direct PostgreSQL recursive CTEs

Use the authoritative `relations` rows directly for bounded breadth-first
neighborhood/path helpers. Put deployment, validity, belief-time and cycle
predicates in the recursive term. This eliminates the P2 copy entirely and is
the smallest architecture.

It preserves live/current correctness and is easy to test. It does not provide
the current public Cypher surface or specialized columnar graph execution, and
large fan-out/path analytics can contend with P1/OLTP. It becomes compelling
only if bounded typed helpers are the real product requirement and open Cypher
is deliberately removed.

### B. Apache AGE

Best PostgreSQL candidate when public Cypher is mandatory. It retains a graph
projection but shares PostgreSQL transactions/backups. Temporal filtered
shortest-path semantics, graph analytics, resource isolation and upgrades must
be proven before adoption.

### C. pgRouting

PgRouting is mature for geospatial/network routing over an edge SQL query and
provides many path algorithms. It depends on the PostGIS/Boost-routing model
and is not a general property-graph/Cypher surface. It does not fit the P2
ontology or open-query contract. Source: <https://github.com/pgRouting/pgrouting>.

### D. pg_graphql

`pg_graphql` reflects relational tables and foreign keys into a GraphQL API.
GraphQL controls response shape; it is not a property-graph traversal language
or path engine. Source: <https://supabase.github.io/pg_graphql/>.

### E. pg_ripple

`pg_ripple` is a new PostgreSQL 18 RDF/SPARQL/SHACL extension. It is pre-1.0
and would replace the accepted property-graph/Cypher model with RDF triples,
IRIs and SPARQL. That is a much larger product/data-model change than the
operational simplification being sought. Source:
<https://github.com/trickle-labs/pg-ripple>.

### F. AgensGraph or an external graph server

AgensGraph is a PostgreSQL fork; its extension successor points users to AGE.
Neo4j, Memgraph, FalkorDB and similar servers add another networked authority/
backup/upgrade boundary rather than simplifying the current embedded snapshot
design. Neither path wins this question.

## 6. Comparison

| Option | One physical DB | No duplicate graph rows | Public Cypher | Proven temporal shortest | Isolates graph load | Current fit |
| --- | --- | --- | --- | --- | --- | --- |
| Ladybug snapshot | no | no | yes | yes | yes | binding baseline |
| direct recursive SQL | yes | yes | no | implementable in recursion | no | simplest if Cypher is dropped |
| Apache AGE | yes | no | yes | not yet proven | no | credible spike candidate |
| pgRouting | yes | yes | no | domain-specific | no | wrong graph model |
| pg_ripple | yes | no | SPARQL, not Cypher | different model | no | premature/rewrite |
| external graph server | no | no | varies | varies | partly | more operations |

## 7. Recommendation and adoption gate

1. Lock the reference database to PostgreSQL **18**, continuously patched to
   the current 18.x minor.
2. Keep D13/Ladybug binding for P2. D94 does not automatically extend from P1
   to P2.
3. Keep a PostgreSQL-P2 proposal open, with direct recursive SQL evaluated
   first if public Cypher becomes optional and AGE evaluated if it remains
   mandatory.
4. Do not implement or package AGE merely because the PostgreSQL image now has
   other native extensions.

Adoption requires a representative, reproducible spike proving:

- exact current, valid-at and believed-at neighborhood/path parity;
- shortest-path correctness when today's shortest edge is temporally invalid
  but a longer eligible path exists;
- public Cypher dialect/parameters/limits or an explicit decision to remove it;
- PageRank/Louvain/community-output parity or a documented replacement;
- ingest/P1/query/autovacuum isolation under concurrent graph load;
- build/switch/repair, hard-forget, dump/PITR restore and extension-major
  upgrade behavior;
- materially lower operational cost than the Ladybug rebuild/publish/reader
  estate.

Until a real pain signal and this gate exist, switching graph engines would be
architecture churn rather than simplification.
