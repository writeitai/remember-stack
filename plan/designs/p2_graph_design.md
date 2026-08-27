# Live PostgreSQL Graph — Binding Design

**Status:** binding under D98

**Date:** 2026-08-27

**Supersedes:** the LadybugDB P2 design under D13/D44/D82

**Analysis:**
[`postgres19_sqlpgq_live_graph_analysis.md`](../analysis/postgres19_sqlpgq_live_graph_analysis.md)

## 1. Problem and decision

RememberStack needs entity neighborhoods, relationship paths, temporal graph
questions, and document citation navigation. PostgreSQL already owns every
entity, fact, clock, merge redirect, and document identity that those answers
depend on. The former LadybugDB P2 copied that state into immutable graph
generations and added an export/build/publish/download/open lifecycle.

There are no compatibility consumers. The graph is therefore served directly
from PostgreSQL 19:

- PostgreSQL authority views are the graph element tables. Graph rows are not
  copied into another store.
- PostgreSQL 19 SQL/PGQ property graphs and `GRAPH_TABLE` implement fixed
  one-hop graph patterns in the cutover architecture.
- Work-bounded PostgreSQL frontier functions implement variable-depth
  neighborhoods and shortest paths, including temporal filtering during
  expansion.
- LadybugDB, public Cypher, P2 data snapshots, generations, manifests, graph
  files, reader swaps, and graph-specific backup/restore state are removed.

This is one storage model with two query syntaxes, not two graph engines:

```text
PostgreSQL authority tables
        |
        +-- memory_v1 live/history views -- SQL/PGQ fixed patterns
        |
        +-- memory_v1 live/history views -- recursive SQL bounded traversal
```

The supported capability is the **live graph**. The legacy filename is retained
only to keep established corpus links stable; it is not a current plane name.
P1 remains a private,
rebuildable PostgreSQL search projection; P3 remains the artifact projection.

## 2. Product contract

The live graph provides:

| Operation | Default | Engine | Hard database clamp |
| --- | ---: | --- | ---: |
| Current/as-of entity neighborhood | 2 hops | SQL/PGQ at 1 hop; work-bounded frontier BFS at 2–4 hops | 4 hops, 500 returned edges and a 2,000-edge expansion budget |
| Entity-to-entity path | 4 hops | work-bounded frontier BFS | 6 hops, 10 complete paths, 500 returned edges and a 2,000-edge expansion budget |
| Citation/document path | 6 hops | directed work-bounded frontier BFS | 6 hops and the same complete-path/result/expansion budgets |
| Fixed relationship shape | explicit pattern | SQL/PGQ | 1 hop in shipped statements |

These are product bounds, not suggested client values. Server code and the SQL
functions clamp them even if a caller asks for more. Any tier limit may be
lower. The response discloses truncation and the effective bounds.

The graph query path is read-only and makes zero LLM calls. A graph hop always
starts from a resolved id. Name resolution and semantic/BM25 entry-point search
remain retrieval responsibilities, not graph behavior.

## 3. Authority and element model

### 3.1 Sources

The property-graph element tables are private live relational views chosen to
keep PostgreSQL 19's current SQL/PGQ rewriter from flattening the full
evidence-rich public authority graph into pathological plans. They contain no
stored rows. Public result hydration and audit authority remain the normalized
`memory_v1` views:

| Graph element | Source view | Key |
| --- | --- | --- |
| `entity` vertex | `rememberstack_graph_internal.entities_live` | `(deployment_id, entity_id)` |
| `document` vertex | `rememberstack_graph_internal.documents_live` | `(deployment_id, doc_id)` |
| `relates` current edge | `rememberstack_graph_internal.relations_current` | `(deployment_id, relation_id)` |
| `relates` historical edge | `rememberstack_graph_internal.relations_history` | `(deployment_id, relation_id)` |
| `mentioned_in` edge | `entity_document_mentions` | `(deployment_id, entity_id, doc_id)` |
| `document_crossref` edge | `rememberstack_graph_internal.crossrefs_live` | `(deployment_id, crossref_id)` |

The private views contain active survivor entities only and require surviving
relation provenance. They resolve original endpoints through survivor
membership, so a merge is a redirect rather than an edge rewrite or
disappearance. Retired or forgotten endpoints do not appear. The server uses
SQL/PGQ only to select bounded relation/node identifiers, then hydrates them
from `memory_v1.graph_edges_current` or
`memory_v1.graph_edges_visible_history` in the same repeatable-read,
read-only transaction. Facts, evidence, claims, and observations remain
ordinary PostgreSQL records; only normalized binary relations become semantic
edges.

All keys contain `deployment_id`, and every SQL/PGQ or frontier-BFS operation
takes it as an explicit first argument and joins on it. A source or destination
reference therefore cannot cross a deployment even if UUID generation or a
fixture is faulty. Property-graph `KEY` clauses over views are declarations,
not backing unique constraints; acceptance tests independently assert key
uniqueness on every source view.

### 3.2 Property graphs

Migrations create two catalog objects:

- `memory_v1.memory_current`: current entity/document vertices, semantic
  edges, mentions, and document cross-references;
- `memory_v1.memory_history`: current survivor entity vertices and all
  historically visible semantic edges, including their two time windows.

The executable core of the semantic declarations is:

```sql
CREATE PROPERTY GRAPH memory_v1.memory_current
  VERTEX TABLES (
    rememberstack_graph_internal.entities_live AS entity
      KEY (deployment_id, entity_id)
      LABEL entity PROPERTIES
        (deployment_id, entity_id, canonical_name, profile_summary),
    rememberstack_graph_internal.documents_live AS document
      KEY (deployment_id, doc_id)
      LABEL document PROPERTIES
        (deployment_id, doc_id, title, source_uri, published_at)
  )
  EDGE TABLES (
    rememberstack_graph_internal.relations_current AS relates
      KEY (deployment_id, relation_id)
      SOURCE KEY (deployment_id, subject_entity_id)
        REFERENCES entity (deployment_id, entity_id)
      DESTINATION KEY (deployment_id, object_entity_id)
        REFERENCES entity (deployment_id, entity_id)
      LABEL relates PROPERTIES
        (deployment_id, relation_id, predicate),
    memory_v1.entity_document_mentions AS mentioned_in
      KEY (deployment_id, entity_id, doc_id)
      SOURCE KEY (deployment_id, entity_id)
        REFERENCES entity (deployment_id, entity_id)
      DESTINATION KEY (deployment_id, doc_id)
        REFERENCES document (deployment_id, doc_id)
      LABEL mentioned_in PROPERTIES
        (deployment_id, mention_count, first_mentioned_at, last_mentioned_at),
    rememberstack_graph_internal.crossrefs_live AS document_crossref
      KEY (deployment_id, crossref_id)
      SOURCE KEY (deployment_id, from_doc_id)
        REFERENCES document (deployment_id, doc_id)
      DESTINATION KEY (deployment_id, to_doc_id)
        REFERENCES document (deployment_id, doc_id)
      LABEL document_crossref PROPERTIES
        (deployment_id, crossref_id, kind, context, created_at)
  );
```

The history graph uses the same composite entity vertex and maps the private
`relations_history` view as `relates`, exposing only deployment/relation ids,
predicate, and the four clock columns required to filter expansion. Migrations
own the complete DDL. A source-view shape migration drops and recreates
dependent property graphs in the same transaction. The public authority views
remain the contract for returned labels, confidence, evidence/support counts,
contradiction state, and provenance; private element properties are planning
inputs, not a second public truth surface.

D98 drops the six legacy `v_graph_entities`, `v_graph_documents`,
`v_graph_relates`, `v_graph_mentioned_in`, `v_graph_crossref`, and
`v_graph_is_document` snapshot-export views. `v_graph_survivor` remains only as
the pre-existing private merge-resolution authority behind the
deployment-labelled `v_memory_entity_survivor`; neither is a graph copy or a
public query-space surface.

Property graphs are schema metadata. They contain no copied rows and have no
data generation id. Migrations create them after restore. If catalog metadata
is missing at schema head, readiness fails closed; the operator-only
`remember ops graph-catalog ensure` command compares the expected semantic
contract through PostgreSQL 19's `property_graphs`, `pg_element_tables`,
`pg_element_table_key_columns`, `pg_edge_table_components`,
`pg_element_table_labels`, `pg_element_table_properties`, `pg_labels`,
`pg_label_properties`, `pg_property_data_types`, and
`pg_property_graph_privileges` information-schema views. It may use
`pg_get_propgraphdef()` as diagnostic output, but does not make a brittle raw
DDL-string comparison its authority. It transactionally recreates only the
catalog objects and their explicit grants. It never repairs or writes
authority rows.

### 3.3 No entity types

D96 removed entity classes. The `entity` label means only “registry entity”; it
does not reinstate Person/Organization/etc. `entities.type`, typed labels, and
domain/range gates must not reappear through graph DDL. Classification-like
facts live in observations and profile prose.

## 4. Query execution

### 4.1 SQL/PGQ from day one

Server-owned fixed patterns use PostgreSQL 19 SQL/PGQ. For example, a directed
one-hop shape has this form:

```sql
SELECT *
FROM GRAPH_TABLE (
  memory_v1.memory_current
  MATCH (a IS entity)-[r IS relates]->(b IS entity)
  WHERE a.deployment_id = $1
    AND a.entity_id = $2
  COLUMNS (
    r.relation_id AS relation_id,
    r.predicate AS predicate,
    b.entity_id AS neighbor_entity_id
  )
) AS g;
```

The admitted PG19 subset is deliberately explicit: fixed path concatenation;
vertex and full-edge patterns; directed or either-direction matching; element
variables; element-local and graph-pattern predicates; individual labels or
label disjunction; property references; and a mandatory `COLUMNS` result.
Named views, composite keys and references, explicit labels, and explicit
column/expression properties are allowed in graph DDL. `GRAPH_TABLE` remains
an ordinary relational `FROM` item, so server-owned statements may alias,
join, filter, order, and implicitly correlate it to an earlier `FROM` item.
PostgreSQL 19 does **not** accept an explicit `LATERAL` keyword before
`GRAPH_TABLE`; shipped SQL uses the working comma-join form.

No production query assumes support for path variables, different-edge,
TRAIL/SIMPLE/ACYCLIC modes, quantified paths or edges, shortest/any-path
search, path alternation/union, non-local element predicates, label
conjunction/negation/wildcards, graph-element identity/topology predicates,
within-match aggregates, path functions, optional/export result modes, or
inline view element tables. Those features are explicitly absent from the
PostgreSQL 19 conformance table. The application exposes ordinary UUID
properties when it needs element identity.

Shipped PGQ statements are static application SQL, parameterized, deployment
scoped, and limited to one explicit hop. Current shapes use `memory_current`;
fixed as-of shapes use `memory_history` and put both
half-open clock predicates on every matched edge. The result is relational and
may join authority tables for hydration in the same statement.

The one-hop neighborhood is the deliberate dual-implementation seam. The
recursive/frontier result contract is canonical. The PGQ statement and its
bounded server-side result fold together must:

1. match only semantic `relates` edges, exactly like the frontier helper; the
   mention and document-cross-reference labels in the same property graph are
   not neighborhood edges;
2. treat semantic discovery as undirected while retaining stored edge
   direction;
3. exclude the anchor as a returned neighbor;
4. have PGQ return the admitted one-hop edge set with predicate/time filters;
5. have the server choose the lexicographically smallest relation-id
   representative per endpoint, then order and page those identifiers;
6. disclose the same returned-edge cap, expansion budget, and truncation state
   as the canonical helper.

PostgreSQL 19's default is repeatable-elements matching. The one-hop statement
therefore keeps anchor exclusion (`y.entity_id <> x.entity_id`) in the
graph-pattern `WHERE` after the complete `MATCH`, where cross-element
comparisons are valid.

The private entity element view materializes its complete
`(deployment_id, survivor_entity_id)` provenance keyset once per view
expansion, then semi-joins active registry entities to that keyset. This is a
planner-shape requirement, not a projection: the CTE copies no durable rows and
the view retains exactly the D48 surviving-provenance membership rule. A
correlated provenance `EXISTS` at each entity row is forbidden here because the
PG19 Beta 3 graph rewrite duplicates that branch across vertex and endpoint
copies and can exhaust the graph statement timeout on a small admitted
neighborhood.

Because PG19 has no inside-match work counter, each fixed PGQ operation begins
with a separate static, indexed, tenant-and-anchor-first relational guard in
the same read-only repeatable-read transaction. The guard expands the anchor's
merge membership, probes both raw relation endpoint indexes, counts temporal-
and-predicate-eligible candidate rows, and uses a `budget + 1` limit. Counting
before the graph views remove missing provenance is deliberately conservative:
those rows are still possible rewrite work. The graph role receives only the
columns needed for this guard. Application control flow inspects its one
decision row and does not send the `GRAPH_TABLE` statement to PostgreSQL unless
the guard admits it. Refusal therefore cannot depend on planner short-circuit
behavior: it returns zero graph data plus the disclosed truncation reason and
effective-budget metadata without planning or evaluating the graph pattern.
The admitted PGQ remains static and uses PG19's implicit comma-join correlation
from its deployment/anchor bounds into `GRAPH_TABLE`; it never spells an
explicit `LATERAL`. `EXPLAIN (ANALYZE, BUFFERS)` acceptance proves both the
standalone guard and graph rewrite use endpoint indexes and do not scan a
deployment-wide eligible edge relation. `statement_timeout` remains a final
safety boundary, not the work-budget implementation.

Byte-identical PGQ/frontier parity is required at depth one whenever
the guard proves the request is within `expansion_budget`; fixtures include
skew, parallel edges, and temporal filtering below that budget. The
over-budget contracts are deliberately different and separately tested:
server-owned PGQ returns zero data rows plus
`truncation_reason = 'expansion_budget'` without evaluating the graph pattern,
while a direct recursive-helper call may return its deterministic partial
prefix plus the same reason. The typed depth-one operation always uses the PGQ
rule; depth two and above always use the frontier helper, so routing is fixed
before execution and there is no runtime fallback that could hide a mismatch.
PostgreSQL 19 Beta 3's view-backed two-hop rewrite exceeded the binding
transaction bound and did not retain endpoint-anchored access. It is therefore
not on the request path until a later PostgreSQL release passes both gates.
Dense-hub fixtures assert each disclosed contract; they are not part of the
byte-parity set. This deliberate one-hop implementation satisfies the operator
requirement to use the standard graph surface from the cutover without making
SQL/PGQ the traversal engine.

PostgreSQL 19 does not implement element-pattern quantifiers or shortest-path
modes. No implementation may emulate runtime depth by generating an unbounded
number of joins. When the operation is variable-length or asks for shortest,
it uses the frontier functions.

### 4.2 Work-bounded frontier traversal

A recursive common table expression explains the graph semantics, but the
existing `memory_v1.graph_neighborhood` and `memory_v1.graph_path` functions
are not retained implementations. They materialize the tenant-wide edge set
and enumerate simple paths to the depth cap before limiting results. D98
replaces them with a level-at-a-time, work-bounded frontier implementation.

The public SQL functions have a clean-cut signature with required
`deployment_id` first. Neighborhood, entity path, and citation path each take
explicit returned-result and expanded-edge budgets, internally clamped to the
product maxima. They emit zero or more `row_kind = 'data'` rows and exactly one
terminal `row_kind = 'status'` row carrying truncation, examined-edge/frontier
counters, and every effective budget—even for an empty result. The query
sandbox-visible data rows repeat the same final truncation and work counters,
so even a broad projection cannot report a known partial traversal as complete.
The terminal carrier remains authoritative because it alone survives an empty
caller result. The query
sandbox materializes each invocation once, exposes only data rows to the caller
query, and right-joins the final caller result to aggregated reserved metadata
columns so the executor can suppress the status-only carrier and populate
`QueryResult.graph_invocations` plus aggregate truncation fields. This remains
one PostgreSQL statement and one evaluation clock;
no transaction/session GUC communicates truncation.

On the public SQL surface the first argument is not caller-selectable: the AST
binder requires the reserved authenticated deployment parameter. A literal,
caller-owned parameter, absent binding, or value unequal to that binding fails
with the public `invalid_parameter` code before the function runs and performs no graph
access. This cannot be reported as an honest-but-wrong empty neighborhood.

The reference implementation is a `STABLE`, `SECURITY INVOKER` PL/pgSQL level
loop whose frontier, seen vertices/edges, candidate paths, counters, and result
buffer live only in bounded local variables/arrays. It performs `SELECT`-only
indexed adjacency probes and writes no regular or temporary table. This is the
only named implementation because graph transactions are `READ ONLY` and a
`STABLE` function cannot execute SQL writes. An alternative set-based form may
replace it only after proving identical semantics and bounds. It must expand only the current frontier through direct
indexed joins on `(deployment_id, subject_entity_id)` and
`(deployment_id, object_entity_id)`. It must not materialize an unanchored edge
view. Function-local state ends with the invocation; pool reuse tests still
prove no session or transaction state crosses calls.

Every expansion must:

1. filter `deployment_id` before joining an edge;
2. filter predicate selection before expansion;
3. filter both temporal windows before expansion;
4. apply the operation-specific visited rule below;
5. stop before exceeding the database depth, frontier, expanded-edge, returned
   result, or statement-time clamps;
6. preserve each edge's stored subject/object direction even when semantic
   discovery is undirected;
7. order deterministically before applying a limit;
8. report truncation from returned data rather than connection-local state.

Visited state is not one global rule. Neighborhood keeps a global
`best_depth_by_vertex`, admits all candidates discovered on the same frontier
level, then deterministically chooses the minimum-hop/lexicographically smallest
representative for each neighbor after that level completes; distinct parallel
edge ids remain eligible under the returned-edge contract. Entity and citation
path helpers keep visited vertices and edge ids **per candidate path**, so they
can return bounded equal-length alternatives, while one global
`first_target_depth` prevents any deeper expansion. Every retained candidate and
alternative counts against the frontier/expansion/result budgets.

For shortest path, the implementation completes one deterministically ordered
frontier level at a time and stops after the first target-bearing level (or
after collecting the bounded number of equal-length paths on that level). It
never enumerates deeper paths after a shortest result is known. If a frontier
or expanded-edge budget is reached before proof completes, it returns no false
path and marks the result truncated/inconclusive. An invalid one-hop edge never
enters the frontier and cannot hide a valid two-hop route. Filtering a path
after shortest-path selection is a correctness bug.

Traversal is bounded BFS semantics implemented inside PostgreSQL. A native
graph extension is a non-goal unless measurements later prove that this
implementation misses a contracted SLO while preserving the same correctness
and work budgets.

Citation traversal is directed from `from_doc_id` to `to_doc_id`; it follows
`document_crossref.from_doc_id -> to_doc_id`. It never treats co-citation or a
reverse link as a citation chain.

### 4.3 Temporal semantics

With neither instant supplied, traversal reads `graph_edges_current`. With both
instants supplied, it reads `graph_edges_visible_history` and applies
both half-open windows to every edge:

```sql
(ingested_at IS NULL OR ingested_at <= believed_at)
AND (invalidated_at IS NULL OR invalidated_at > believed_at)
AND (valid_from IS NULL OR valid_from <= valid_at)
AND (valid_until IS NULL OR valid_until > valid_at)
```

The API and SQL functions accept both clocks or neither. Supplying exactly one
raises PostgreSQL `invalid_parameter_value`; public surfaces map that to the
exhaustive `invalid_parameter` code. It never invents a second instant. The
answer is the shortest path in the eligible subgraph, not a filtered path from
the unfiltered graph.

Current structural edges are not presented as historical. A future need for
historical mentions or cross-references requires authority history and a new
decision; it must not infer history from current rows.

### 4.4 Transaction snapshot

A statement sees one PostgreSQL MVCC snapshot. A relation committed before a
later statement becomes graph-visible without a rebuild. `memory_current`,
`graph_edges_current`, and a graph helper called with neither explicit instant
capture one `statement_timestamp()` value and use it for every current predicate
and emitted `evaluated_at` in that statement, preserving D41.

A compound typed operation that searches, traverses, and hydrates across
multiple statements uses a `REPEATABLE READ, READ ONLY` transaction, captures
one `evaluation_at` at operation entry, and passes that value explicitly as
both graph instants. Its PGQ step uses `memory_history` with the same explicit
predicates rather than `memory_current`. Thus all stages see one MVCC snapshot
and one temporal instant without changing the public current-view definition.
Long read transactions remain bounded by statement/transaction timeouts so
they do not pin vacuum indefinitely.

“Live” means no projection publication lag. It does not erase valid-time or
belief-time semantics.

## 5. Retrieval behavior

The default retrieval path remains D97:

1. semantic/BM25 search finds chunks, facts, observations, or candidate entity
   names in PostgreSQL P1;
2. authority confirmation and identity resolution produce survivor entity ids;
3. the live graph expands those ids with no required predicate filter;
4. fact text, observations, claims, and evidence are hydrated from authority;
5. the existing fusion, diversity, token, and evidence rules construct the
   answer context.

The change improves freshness and consistency: P1 search, graph expansion, and
authority hydration can share one database snapshot. Retrieval no longer
reports or reasons about `p2_built_at`, graph generation availability, local
graph hydration, or a pointer swap.

It does not make graph expansion the universal first stage. Unanchored
questions still need semantic/BM25 entry points. Observations still are not
graph neighbors. Predicate is optional narrowing, not the default recall key.

The implementation records channel provenance such as `graph_pgq` or
`graph_recursive`, effective bounds, truncation, and query duration. That is
execution provenance; it must not change ranking solely because one syntax was
used.

## 6. Global graph analytics

The system does not compute Ladybug PageRank, k-core, weakly connected
components, Louvain communities, community labels, or K community/topic
routing. The P2 analytics worker, snapshot-keyed metrics, K `community` rule,
and `community_changed` trigger are absent. These were real K-plane consumers,
not retrieval signals; D98 deliberately removes that unproved product surface
rather than preserving a generation lifecycle for it.

The one retained scalar, entity graph degree, is computed from current
PostgreSQL relation adjacency inside the clustering/blast-radius query. The
pre-D98 `entities.graph_degree` and `memory_v1.entities_current.graph_degree`
columns remain only as clean-cut compatibility shape: the migration resets the
base column to zero and no writer refreshes it. Consumers must not interpret
that compatibility value as degree. This avoids rebuilding the broad dependent
public-view graph merely to remove one column while ensuring there is no
background graph snapshot or stale metric to serve.

Reintroducing global analytics requires a separate analysis with a concrete
consumer, freshness contract, scale measurement, and resource/isolation plan.
It may use PostgreSQL batch SQL or an external disposable analytical job, but
its artifacts do not become graph authority.

## 7. Security and admission boundary

- Database roles are least privilege. The migration owner owns property
  graphs; the internal read role receives explicit `SELECT ON PROPERTY GRAPH`
  for only the graphs it needs plus explicit access to the element views and
  traversal functions. Property-graph ownership does not lend base-relation
  privileges to a caller, and PG19 documents no `ALL PROPERTY GRAPHS IN
  SCHEMA` or default-privilege shortcut.
- Every application statement contains a deployment predicate. Composite
  element keys are defense in depth, not a substitute for the predicate.
- Typed graph endpoints call only fixed SQL or the bounded functions. They do
  not interpolate identifiers, labels, properties, predicates, or depth.
- Public Cypher is removed.
- Public arbitrary SQL/PGQ is not admitted while the exhaustive `pglast` gate
  embeds PostgreSQL 18 grammar. `pglast` 8.4 rejects PGQ syntax, so bypassing
  the parser or maintaining a second lexical allowlist is forbidden.
- A future PG19-capable AST parser may admit SQL/PGQ only after the existing
  default-deny query-space review is extended for property graphs,
  `GRAPH_TABLE`, functions, cost checks, and result limits.
- The PostgreSQL-18 grammar gate in front of a PostgreSQL-19 server is itself
  an explicit compatibility boundary. Release tests diff reserved/unreserved
  keywords, admitted statement/node shapes, and callable built-ins between
  majors and prove that new PG19 syntax/functions are rejected by default.
  An AST accepted under the PG18 grammar may execute only through the existing
  function/view allowlists; no name-resolution semantic drift is assumed safe.

SQL/PGQ starts immediately in server-owned statements; public SQL/PGQ syntax
is a separate admission feature.

### Surface and transport contract

The three bounded graph operations are first-class typed HTTP and Python SDK
methods in D98. A dedicated `remember graph …` CLI command and dedicated MCP
graph tools are intentionally outside this cut. CLI and MCP retain their
general ingest and assured-recall contracts; where the OSS open-query
capability is enabled, their existing SQL query tools can call only the
allowlisted bounded graph helpers under the same parser, role, timeout, and
result limits. That helper access is not public arbitrary SQL/PGQ syntax and is
not an excuse to synthesize query text.

Any later dedicated CLI/MCP graph verb must delegate to the typed operation
rather than generate Cypher, SQL/PGQ, or a divergent traversal. Its adoption
trigger is demonstrated need for graph topology as a first-class agent/shell
result; transport expansion does not block removal of the snapshot system.

## 8. Capacity and isolation

Live graph reads share PostgreSQL CPU, buffer cache, I/O, temp space, and
autovacuum with authority writes, pgvector, and pg_textsearch. The API uses a
separate graph connection pool/role with:

- a short graph-specific `statement_timeout`;
- `transaction_timeout` and `idle_in_transaction_session_timeout`;
- bounded pool concurrency;
- conservative `work_mem` applied transaction-locally before entering the
  graph role;
- read-only transactions;
- API row/byte limits in addition to SQL clamps;
- cancellation when the client request ends.

Indexes remain on authority tables, because graph sources are views. At
minimum, relation access supports `(deployment_id, subject_entity_id)`,
`(deployment_id, object_entity_id)`, current-row predicates, predicate
filtering, and temporal range filtering. Cross-reference and mention sources
support both endpoints under `deployment_id`. `EXPLAIN (ANALYZE, BUFFERS)` on
representative fixtures is an implementation gate.

The scale contract is bounded work at every supported cardinality: no operation
may examine more than its expansion budget or retain more than its frontier,
returned-result, temp-space, and time budgets. At representative target-scale
fixtures, fixed one-hop PGQ, default two-hop neighborhood, and four-hop path
queries must complete inside the configured statement timeout without spills
or an unanchored scan of the tenant relation set. The one-hop fixture includes
enough entity provenance, resolution partitions, evidence, and documents to
expose repeated view expansion even when the anchor has only a few incident
edges. Measurements record hardware, cardinality, skew, and p50/p95/p99 rather
than weakening correctness to a small-fixture promise.

## 9. PostgreSQL 19 and extension posture

The reference image moves directly to the latest PostgreSQL 19 prerelease,
initially the exact PostgreSQL 19 Beta 3 image digest proven in the analysis.
There are no user databases to preserve. Until GA:

- no prerelease database contains irreplaceable production data;
- migrations and representative fixtures must replay from empty storage;
- every beta/RC bump rebuilds the image and runs the full migration,
  pgvector/HNSW, pg_textsearch/BM25, pg_partman, PGQ, traversal, backup, and
  restore gates;
- the complete extension/PGQ/traversal matrix passes on both `linux/amd64` and
  `linux/arm64`, with per-architecture image/artifact digests recorded;
- prerelease-to-prerelease data-directory compatibility is not assumed;
- GA is another tested image replacement, after which ordinary supported
  major/minor upgrade policy resumes.

The reference image pins pgvector and pg_partman packages. `pg_textsearch`
1.3.1 needs the small reviewed PostgreSQL 19 source compatibility patch proven
by 71/71 upstream regression tests; the image builds it from a pinned source
commit and patch checksum until upstream publishes a PostgreSQL 19 build. Its
PostgreSQL License permits use, modification, and distribution when the
required notices accompany copies. OSS release engineering owns rebasing and
retiring the patch, links the upstream compatibility issue before release, and
records source, patch, notice, compiler/toolchain, and per-architecture binary
checksums. No floating `main` branches enter the image.

## 10. Failure, recovery, forget, and upgrade

### Failure

A graph-query timeout or cancellation fails that operation and does not affect
authority state. There is no stale local graph fallback and no previous graph
generation to serve. Retrieval may continue with its non-graph channels only
when the operation's assurance contract permits degradation and the response
discloses it.

### Recovery

Database recovery restores authority, P1, graph source views, functions, and
property-graph catalog definitions together. Property graph DDL is replayable
metadata. At schema head, missing/mismatched catalog metadata fails readiness
and is repaired only by the reviewed operator `graph-catalog ensure` command or
fresh restore/replay—not silently by a no-op Alembic invocation. There is no
graph object-store inventory, download, validation, pointer reconciliation, or
local cache recovery.

### Hard forget

The authority deletion/tombstone transaction and live source views remove
forgotten material from later graph statements immediately. Backups follow the
single PostgreSQL retention/erasure contract. There are no Ladybug files or P2
generations to enumerate or purge. In-flight statements may retain their MVCC
snapshot only until the bounded transaction ends.

### Schema and version upgrades

Migrations create/drop property graphs transactionally around incompatible
view changes. Rollback means deploying the preceding application and replaying
its schema on a compatible PostgreSQL image; it never means reviving Ladybug.
During PostgreSQL 19 prereleases, rollback is rebuild-and-restore from a
logical/known-compatible source, not reusing an unverified data directory.

## 11. Observability and readiness

Graph health is database/query health, not projection-generation health.
Per-deployment readiness is data-independent and therefore also succeeds for a
new empty deployment. It checks:

1. PostgreSQL is reachable at the required schema revision;
2. both property graphs exist with the expected element aliases;
3. graph catalogs, exact grants, and helper definitions/versions match;
4. fixed one-hop PGQ and each helper execute under the deployment-bound role
   against a UUID proven absent in the same read-only repeatable-read snapshot,
   returning an empty data result (and the helper terminal status) without
   crossing deployment scope; and
5. transaction-local graph limits and statement timeout are effective after
   `SET LOCAL ROLE`. The graph role's `rolconfig` is audited as a defensive
   login baseline, but PostgreSQL does not activate `ALTER ROLE ... SET`
   values merely because a session executes `SET ROLE`.

No synthetic entity, document, edge, or sentinel is written into tenant
authority for readiness. Seeded-edge immediate visibility and the
invalid-short/valid-longer temporal path are CI, release, restore-drill, and
dogfood acceptance fixtures only; they are removed after their isolated test
transaction/deployment and never enter customer retrieval, P3/K, forget, or
storage-meter state.

The absence probe generates a candidate UUID, verifies `NOT EXISTS` across the
entity and document element views for the authenticated deployment, and only
then uses it as the anchor in that same snapshot. A collision causes another
bounded attempt and then a typed readiness failure; it never turns a
probabilistic guess into the contract and never writes a reservation row.

The clean-cut public readiness request is
`{"version_ids":[…],"require":{"pipeline":true,"p1":true,"live_graph":true,"p3":false}}`.
Those four Boolean keys are exhaustive; `require_projections` and compatibility
aliases are absent. The response retains exact stage rows and contains one
`capabilities` member per key with `required`, `ready`, `checked_at`, and a
typed non-secret reason. Overall `ready` is the conjunction of required members.
`live_graph` applies the checks above and never waits for a build; `p3`
separately proves a published version newer than the requested terminal stages.

Metrics include operation, execution mode, depth, examined/returned
edges/paths, truncation, duration, timeout/cancel/error, pool wait, temp bytes,
and PostgreSQL query identity. They contain ids/cardinalities, not fact text or
secret connection material.

There is no `p2_generation`, `built_at`, snapshot age, download state, local
graph path, or rebuild-ready signal.

## 12. Acceptance gates

Implementation is accepted only when all of these pass:

1. fresh PostgreSQL 19 migration creates both property graphs over views and
   each declared element `KEY` query proves unique;
2. one-hop SQL/PGQ tests prove tenant isolation, direction, labels,
   current/as-of visibility, anchor exclusion, deduplication, deterministic
   truncation, and byte-identical parity with the canonical traversal at depth
   one for under-budget inputs; separate
   dense-hub tests prove PGQ's zero-data refusal and the helper's deterministic
   partial-prefix over-budget contracts;
3. frontier tests prove required `deployment_id`, current/history source
   selection, both half-open clocks, invalid-short/valid-longer shortest path,
   directed citation behavior, cycle safety, early target termination,
   deterministic ordering, whole-path limits, hard frontier/expansion/result
   clamps, and clean scratch/pool reuse;
4. typed graph API parity covers neighborhood/path/citation results without
   Ladybug or Cypher;
5. retrieval tests prove live newly committed edges are available without a
   graph build and that graph provenance/truncation are preserved;
6. forget and restore tests find no graph artifact inventory and prove view
   disappearance after commit;
7. HNSW, BM25, pg_partman, migration, PG18-parser/PG19-executor negative tests,
   and query-space tests pass on both linux/amd64 and linux/arm64 images;
8. repository searches and dependency locks contain no active Ladybug runtime,
   P2 generation worker, public Cypher route, or P2 readiness contract;
9. representative query plans prove frontier-anchored deployment-first index
   access and the configured concurrency test does not starve authority
   writes/search;
10. `SELECT ON PROPERTY GRAPH` DDL is proven; the ten graph information-schema
    views and `pg_get_propgraphdef()` report the expected semantic contract;
    and least-privilege tests determine and enforce invoker ACL behavior on
    every underlying view (no owner-based privilege escalation);
11. cold-start, backup/restore, missing-catalog fail-closed, and operator
    `graph-catalog ensure` drills pass;
12. K tests and repository searches prove no community scope/rule/event/page
    or snapshot-keyed analytics consumer remains.

## 13. Costs and rejected alternatives

The decision trades Ladybug's optimized embedded adjacency and built-in global
algorithms for a much smaller operational system and live consistency. Deep
traversal can consume more PostgreSQL resources; this is contained with bounds,
indexes, timeouts, and pool isolation and measured before adding machinery.

Rejected by this design:

- retaining Ladybug for public Cypher or analytics;
- Apache AGE, because its label tables duplicate/dual-write current authority
  and its shortest-path helper does not carry the arbitrary two-clock edge
  predicate;
- pgGraph, because its CSR artifacts reintroduce generations and its inspected
  release lacks PostgreSQL 19 and the required per-edge temporal predicate;
- SQL/PGQ alone, because PostgreSQL 19 lacks quantified and shortest paths;
- recursive SQL alone, because the operator explicitly requires SQL/PGQ as the
  standard one-hop fixed-pattern surface from the cutover;
- closure tables, unbounded generated joins, parser bypasses, and a permanent
  dual-run migration.

The escalation trigger is evidence: if supported, representative traversals
miss their SLO after query/index tuning, record the workload and evaluate a
native accelerator in a new proposal. A native bounded BFS/DFS implementation
is an optimization, not authority and not permission to weaken temporal
correctness.
