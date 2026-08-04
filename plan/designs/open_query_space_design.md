# Open query space — binding design

*2026-08-04. Binding once accepted. Replaces the growing default recipe
catalog with a versioned, invariant-compiled PostgreSQL query space while
preserving three platform-assured one-call operations. Rationale:
`plan/analysis/open_query_space_codex.md` and
`plan/analysis/open_query_space_grok.md`. This design refines the public
surface bound by `agent_retrieval_surface_design.md`; D41, D48, D49, D54,
and D80 remain controlling.*

## 1. Principles (binding)

1. **PostgreSQL authorizes every live result.** The public data language IS
   PostgreSQL 16 SQL over the versioned `memory_v1` schema. Physical tables,
   raw projection tables, and operator schemas are never public. PostgreSQL
   views compile row-level invariants; Lance and P2 only nominate candidates.
2. **The default assured surface has exactly three intent operations.**
   `resolve_entity`, `question_context`, and `current_context` are the complete
   shipped platform-operation set. They retain their full D49 `Envelope`
   contracts. SQL execution, discovery, saved-query execution, and the
   allowlisted SQL functions are query infrastructure, not additional intent
   operations.
3. **Open SQL is exploratory by contract.** Every ad-hoc or saved SQL execution
   returns `QueryResult/v1` with grade `exploratory_tabular`. A view's source
   grain never becomes a claim about an arbitrary outer query's result grain.
4. **D41 is compiled, not taught.** A relation named `current` applies world
   validity and transaction validity at one disclosed evaluation instant.
   Claim validity remains immutable source testimony and never answers what
   currently holds.
5. **D48 is fail-closed at every boundary.** Each lineage-derived row has a
   surviving live-document provenance path. P1 and P2 candidates receive
   PostgreSQL confirmation before return; graph paths confirm as units; chunk
   bodies confirm their current coordinate and hashes. Missing, orphaned,
   mismatched, or incompletely forgotten state is absent or fails the call.
6. **D54 counting has one meaning.** `evidence_count` and
   `contradict_count` count distinct current-testimony document lineages per
   `supports` or `contradicts` stance. `support_state` is exactly `current` or
   `withdrawn`; `withdrawn` comes only from the open processing-driven
   `support_withdrawn` review state. A zero count MUST NOT manufacture it.
7. **The graph remains a projection.** `memory_v1.graph_edges_*`, recursive
   CTEs, `graph_path`, and `graph_neighborhood` are authoritative because they
   execute against PostgreSQL. P2 remains an internal accelerator for
   `question_context` and `current_context`; there is no public Cypher surface
   in v1.
8. **Semantic SQL preserves D80 and D48.** `semantic_claims`,
   `semantic_chunks`, and `semantic_facts` pin one ready Lance projection,
   embedding-input-policy version, and embedder generation per invocation and
   perform in-function PostgreSQL confirmation before exposing rows.
9. **Customer semantics stay customer-owned.** Saved queries inherit the
   platform sandbox, tenancy, deletion, limits, and execution provenance. Their
   filters, aggregates, labels, and interpretations are not platform-endorsed
   fact semantics. Shipped examples live under the same rule.
10. **Bounds are part of the public contract.** SQL work, results, recursive
    traversal, semantic nomination, concurrency, and retained telemetry have
    defaults and hard caps in §4. No cap is silent.
11. **The schema is discoverable before use.** The same checked-in manifest
    owns view/function comments, grains, keys, examples, compatibility, and
    `surface_manifest_hash`. Raw `pg_catalog` discovery is not exposed.
12. **No benchmark-specific product behavior exists.** A future v10 protocol
    consumes the customer surface unchanged. Dataset names, question classes,
    benchmark-only views, prompts, functions, branches, or limits are forbidden
    in product code.
13. **Accretion requires evidence.** A fourth platform intent operation can be
    proposed only when all of these gates pass:
    - the intent is at least 5% of retrieval-bearing requests across at least
      10,000 requests and three independent deployments, or at least 500 blind
      questions across two non-benchmark-specific corpora;
    - against the best composition of the existing surface at the same model
      and budget, the proposed operation improves end-task success by at least
      5 absolute percentage points with the 95% confidence interval lower
      bound above zero, or removes at least one median agent call and 20% p95
      latency with no more than 1 point of success loss;
    - the failure is not repairable by a view, allowlisted function, discovery
      change, or example saved query, and the operation packages a semantic or
      safety guarantee that client SQL cannot enforce;
    - invariant, security, resource, and non-benchmark reviews pass. Passing
      these gates authorizes a separate binding design; it does not add the
      operation automatically.
14. **The caller owns planning.** There is no product NL-to-SQL planner or LLM
    on the query path. Raw-table SQL, DuckDB/exports as a live correctness
    path, and content-level ACL emulation inside one deployment are non-goals.

## 2. Naming alignment with the existing corpus

The target default surface contains the three retained names below. Each of
the other 17 names remains callable through its frozen legacy adapter during
§8's dual-surface window and is seeded as a discoverable, non-default saved
query in the `examples` namespace. An `examples.*` query returns
`QueryResult/v1`, carries customer-space semantics, is editable only by
copying it to a customer namespace, and is not a platform contract or a
top-level MCP tool.

| Existing recipe | Target disposition | Example implementation after demotion |
|---|---|---|
| `resolve_entity` | **Retained platform operation** | Full D49 `Envelope`; no saved-query substitute |
| `relation_current` | `examples.relation_current` | Filter `facts_current` to `fact_kind = 'relation'` |
| `observation_current` | `examples.observation_current` | Filter `facts_current` to `fact_kind = 'observation'` |
| `entity_timeline` | `examples.entity_timeline` | Group `facts_visible_history` by disclosed time bucket |
| `claims_verbatim` | `examples.claims_verbatim` | `semantic_claims` joined to `claims_live` |
| `claims_hybrid_rrf` | `examples.claims_hybrid_rrf` | Semantic nomination plus documented SQL ranking; no parity claim with the legacy hybrid |
| `chunks_hybrid_rrf` | `examples.chunks_hybrid_rrf` | `semantic_chunks`; no parity claim with the legacy hybrid |
| `question_context` | **Retained platform operation** | Full D49 evidence `Envelope` |
| `documents_about` | `examples.documents_about` | `entity_document_mentions` joined to `documents_live` |
| `claims_about` | `examples.claims_about` | `mentions_live` joined through `claim_occurrences_live` to `claims_live` |
| `claims_as_of` | `examples.claims_as_of` | Inclusive claim-evidence overlap over `claims_visible_history`; unknown precision excluded and counted |
| `chunk_neighbors` | `examples.chunk_neighbors` | Current-section ordinal neighbors from `chunks_live`; bodies use the confirmed body-fetch path |
| `current_context` | **Retained platform operation** | Full D49 fact `Envelope` with explicit fact/evidence associations |
| `explain` | `examples.explain` | `facts_visible_history`, `fact_claim_evidence_live`, `evidence_lineage`, and sources |
| `identity_as_of` | `examples.identity_as_of` | Bounded `identity_events_visible` transcript; interpretation remains customer-owned |
| `changed_since` | `examples.changed_since` | Bounded `changes_visible` query |
| `pages_about` | `examples.pages_about` | `pages_live` joined to `page_evidence_visible` |
| `multi_hop_context` | `examples.multi_hop_context` | `graph_path`/`graph_neighborhood` plus semantic SRFs and explicit joins |
| `graph_neighborhood` | `examples.graph_neighborhood` | Direct call to `graph_neighborhood` |
| `graph_path` | `examples.graph_path` | Direct call to `graph_path` |

The examples preserve familiar discovery names, not exact legacy behavior.
Only the legacy adapters promise their existing versions and `Envelope`
shapes during the compatibility window.

`examples.claims_as_of(:from, :to)` uses the shipped inclusive overlap
predicate `claim_valid_from <= :to AND (claim_valid_until IS NULL OR
claim_valid_until >= :from)`. Every demotion example follows the §3.3 D48
INNER JOIN/`EXISTS` authorization template; none carries forward a legacy
LEFT JOIN orphan branch.

## 3. Public query contract

### 3.1 Surface entry points and assured core

The protocol entry points are:

| Entry point | Contract |
|---|---|
| `query_sql(sql, parameters, max_rows?)` | One sandboxed statement; `QueryResult/v1` |
| `explain_sql(sql, parameters)` | `EXPLAIN (FORMAT JSON)` without execution; the same parser, relation, function, and cost gates |
| `describe_query_space(pattern?, include_examples=false)` | Manifest-backed exact schema, functions, comments, examples, versions, hashes, and limits |
| `search_query_space(query, k=10)` | Search over checked-in manifest text only; `k` range 1–25 |
| `list_saved_queries(namespace?, status?)` | Registry metadata only |
| `describe_saved_query(namespace, name, version?)` | Immutable version, parameters, declared columns, validation state, and hashes |
| `run_saved_query(namespace, name, version, parameters)` | Same executor and `QueryResult/v1` as `query_sql` |

The three assured operations are pinned in the manifest as:

| Operation | Initial retained version | Contract |
|---|---:|---|
| `resolve_entity` | 1 | D49 `Envelope`; ranked survivor candidates, `unknown_entity`/`boundary`, current identity regime |
| `question_context` | 3 | D49 evidence `Envelope`; separately typed claims/chunks, D48 drops, freshness, bounds |
| `current_context` | 1 | D49 fact `Envelope`; current facts, both evidence stances, explicit associations/totals, support state |

An implementation change that alters any descriptor, selection semantics,
bound, field, negative, or association increments that operation's version and
rolls `surface_manifest_hash`. P2 use is internal: eligible graph expansion in
`question_context` and `current_context` uses P2 nomination, confirms every
returned unit in PostgreSQL, and falls back to bounded PG traversal when P2 is
unavailable. P2 never changes the Envelope guarantee.

### 3.2 `memory_v1` view catalog

`memory_v1` is the only schema on the query role's `search_path` besides
`pg_catalog`. The relation set below is exhaustive for v1. Every row has a
non-null `deployment_id`; every identifier join uses `(deployment_id, id)`;
UUIDs use `uuid`, instants use `timestamptz`, counts use non-negative `bigint`,
structured open fields use `jsonb`, and finite vocabularies use manifest-bound
text enums. Summaries, profiles, location headers, and page summaries are
labeled orientation text, never evidence.

| View | Row grain and key | Required semantics |
|---|---|---|
| `documents_live` | one live document lineage; `(deployment_id, doc_id)` | Current version/representation coordinates, source identity, D55 mode, origin and source clocks; deleted lineages absent |
| `document_versions_visible` | one visible version of a live lineage; `(deployment_id, version_id)` | Stable `doc_id`, version number/hash, representation, status, ingest/supersession clocks, `is_current_version`; tombstoned versions absent |
| `sections_live` | one section in a current ready representation; `(deployment_id, section_id)` | Parent/path, block/character/page ranges, title/role/ordinal, orientation summary |
| `chunks_live` | one chunk coordinate in a current ready representation; `(deployment_id, chunk_id)` | Metadata only: document/version/representation/section, ordinals/ranges, content and embedding-text hashes, D80 location facts/header/policy/embedder generations; no authoritative body column |
| `claims_live` | one current-testimony claim; `(deployment_id, claim_id)` | Claim/source text and anchors, attribution, asserted clock, immutable D41 claim-validity fields, audit state, source handle |
| `claims_visible_history` | one historically visible claim with surviving lineage; `(deployment_id, claim_id)` | Same claim fields plus `is_current_testimony`; name and comment forbid current-fact interpretation |
| `claim_occurrences_live` | one current claim occurrence; `(deployment_id, claim_id, chunk_id, derivation_kind)` | Explicit claim-to-chunk/version/representation/section association and source locators |
| `testimony_currency_events_visible` | one visible D54 transition; `(deployment_id, event_id)` | Claim/lineage/basis/reconciliation coordinates, transition direction/reason and occurrence time; forgotten lineages absent |
| `entities_current` | one externally visible survivor entity; `(deployment_id, entity_id)` | Type/name/profile orientation, live mention/degree summaries; derived entities without surviving provenance absent |
| `entity_aliases_current` | one current alias-to-survivor mapping; `(deployment_id, alias_id)` | Source and survivor IDs, normalized alias, provenance/confidence and clocks |
| `mentions_live` | one mention in current content; `(deployment_id, mention_id)` | Exact document/chunk/claim anchors, nullable resolution, survivor identity, method/confidence; unresolved mentions remain visible |
| `identity_events_visible` | one visible resolution/merge/split event; `(deployment_id, object_kind, event_id)` | Participants, outcome/method/confidence, supersession state and decision clock; source-only forgotten events absent |
| `entity_document_mentions` | one survivor entity × live document; `(deployment_id, entity_id, doc_id)` | Exact live mention count and first/last mention clocks |
| `facts_visible_history` | one historically visible relation or observation; `(deployment_id, fact_kind, fact_id)` | Raw valid/transaction clocks, subject/predicate/object or statement/label, contradiction group, `evidence_count_current`, `contradict_count_current`, and `support_state_current`; manifest comments state these are live current-testimony values, not historical reconstructions; at least one surviving historical provenance lineage |
| `facts_current` | one currently valid relation or observation; `(deployment_id, fact_kind, fact_id)` | §3.3 D41 predicate, one shared `evaluated_at`, D54 counts/support state |
| `fact_claim_evidence_live` | one current claim-to-fact association; `(deployment_id, fact_kind, fact_id, claim_id, stance)` | Stance is exactly `supports` or `contradicts`, matching shipped `evidence_stance`/`FactEvidence.stance`; claim-validity fields and source handle; current testimony and live lineage only |
| `evidence_lineage` | one fact × current-testimony document lineage × stance; `(deployment_id, fact_kind, fact_id, doc_id, stance)` | Stance is exactly `supports` or `contradicts`; claim count is descriptive only; representative claim and assertion range; this view is the sole public input for D54 counts |
| `contradiction_members_current` | one current contradiction-group member; `(deployment_id, contradiction_group, fact_kind, fact_id)` | Member clocks/counts/support state and shared evaluation instant; SQL callers can still select one side, so QueryResult disclaims completeness |
| `graph_edges_current` | one current relation edge; `(deployment_id, relation_id)` | Survivor endpoints, predicate/label, D41 clocks, contradiction group, D54 counts/state, shared evaluation instant |
| `graph_edges_visible_history` | one historically visible relation edge; `(deployment_id, relation_id)` | Survivor endpoints, raw valid/transaction clocks, `evidence_count_current`, `contradict_count_current`, and `support_state_current`; manifest comments state these are live current-testimony values, not historical reconstructions; surviving historical provenance required |
| `document_crossrefs_live` | one live document cross-reference; `(deployment_id, crossref_id)` | Both endpoint lineages live; kind, bounded context, creation clock |
| `changes_visible` | one externally visible change event; `(deployment_id, object_kind, event_id)` | Referenced object remains visible; deletion events and labels cannot become a tombstone side channel |
| `pages_live` | one visible K artifact; `(deployment_id, artifact_id)` | Kind/path, orientation summary, compilation clock, stale/status/open-flag/redaction state; compiled grain only |
| `page_evidence_visible` | one visible K artifact-to-target association; `(deployment_id, artifact_id, role, target_kind, target_id)` | Target passes its own visibility gate; chunk hash is a locator, not an authorization bypass |

`identity_events_visible` is a UNION view over `resolution_decisions` and merge
events; `changes_visible` is a UNION view over its typed change sources. Every
arm emits a synthetic `(object_kind, event_id)` key. Proving key uniqueness
across the underlying ID spaces is a Batch A manifest obligation.

The column-by-column view DDL is implementation-note territory. Batch A MUST
land a checked-in `memory_v1` schema manifest before executable DDL. That note
MUST enumerate, for every view, ordered columns, SQL types, nullability, enum
values, row key, join keys, grain tag, clock semantics, exact definition,
comments, indexes used by the definition, and at least one positive and one
negative fixture. Merge acceptance is exact equality between the running
database introspection and the manifest, zero undocumented grants, successful
key-uniqueness checks, and all §9 invariant tests. Missing or extra columns,
type/nullability drift, a non-unique declared key, or a definition hash mismatch
blocks merge.

Within `memory_v1`, adding a nullable column at the end, adding a view/function,
or correcting an invariant rolls the manifest hash. Removing, renaming,
reordering, narrowing, changing type/nullability, changing grain/key/clock
meaning, or weakening visibility requires `memory_v2`. `SELECT *` is valid SQL
but never a compatibility promise. Old major schemas remain read-only during
their published compatibility window.

### 3.3 Invariant compilation

**D48 template.** Every lineage path includes an inner authorization chain of
this form, specialized to its coordinates:

```sql
JOIN spine.documents AS d
  ON d.deployment_id = x.deployment_id
 AND d.doc_id = x.doc_id
 AND d.deleted_at IS NULL
```

Version-derived rows additionally require a nondeleted version. Current-content
rows additionally require equality with the lineage's current version and
current ready representation. Fact, entity, count, change, and K rows without a
direct `doc_id` use `EXISTS` through their explicit provenance association to at
least one surviving lineage. A permissive legacy/orphan branch is forbidden.
Demotion examples and every §9 gate MUST use this INNER JOIN/`EXISTS` template
and MUST NOT reintroduce the legacy LEFT JOIN orphan branch.

| Boundary | Required D48 behavior |
|---|---|
| Public views | Apply the full live lineage/version/representation chain before projection or aggregation |
| SQL helpers | Read only invariant-bearing public views or equivalent private subqueries covered by the same tests |
| Lance SRFs | Nominate in Lance, confirm target-specific IDs and filters in PG, then expose rows; stale IDs drop |
| P2 use in core operations | Confirm every node/edge and drop a path as one unit when any member fails |
| Saved queries and legacy adapters | Execute through the same role/views/helpers; no cached result rows bypass confirmation |
| Counts | Aggregate from `evidence_lineage` after liveness/current-testimony filtering, never from cached claim counts |
| K artifacts | Expose links only when each target is visible; K prose stays compiled and cannot be promoted to live fact |
| Corpusfs/P1 bodies | Confirm current coordinate, source hash, generated-prefix separation, and policy generation before body return |
| Legacy/orphan data | Record in an operator-only quarantine report; omit from every public path until repaired |

**D41 current time.** `facts_current` and `graph_edges_current` evaluate one
`statement_timestamp()` value `T` and emit it as `evaluated_at`:

```sql
ingested_at <= T
AND invalidated_at IS NULL
AND (valid_from  IS NULL OR valid_from  <= T)
AND (valid_until IS NULL OR valid_until >  T)
```

Relation/observation fact world-validity alone uses half-open
`[valid_from, valid_until)` intervals; null endpoints are open. Claim-evidence
overlap is inclusive because shipped `instant` precision requires
`claim_valid_from = claim_valid_until`, which a half-open convention would make
empty. All references to a current view in one SQL statement observe the same
statement snapshot and evaluation instant.

The bounded `facts_as_of(valid_at, believed_at, max_rows)` SRF is the only
platform-labeled bitemporal fact API in v1. Both instants are required; its
default/hard `max_rows` bounds are defined only by the §4.3 limits table. It
applies:

```sql
ingested_at <= believed_at
AND (invalidated_at IS NULL OR invalidated_at > believed_at)
AND (valid_from  IS NULL OR valid_from  <= valid_at)
AND (valid_until IS NULL OR valid_until >  valid_at)
```

It emits both instants, `identity_regime = 'current'`, and
`evidence_count_current`, `contradict_count_current`, and
`support_state_current`. The shipped identity enum is only `current` or `as_of`;
this SRF does not offer as-of identity reconstruction. Manifest comments state
that its `*_current` columns are live current-testimony values, not historical
reconstructions. Graph helpers accept either neither instant (current semantics)
or both; supplying only one is `invalid_parameter`. PostgreSQL's unbounded
belief history is authoritative.

**D54 provenance and state.** `facts_visible_history` requires surviving
historical provenance, not current support. A processing-withdrawn fact can
therefore remain visible with zero current support. Count columns equal counts
of distinct `doc_id` rows in `evidence_lineage` for the `supports` and
`contradicts` stances. `support_state` and `support_state_current` take exactly
`current` or `withdrawn`. They are derived at read time exactly as the shipped
query engine does: `withdrawn` iff an open `review_queue` row has
`item_kind = 'support_withdrawn'`, the fact ID in its candidate, and status
`pending` or `deferred`; otherwise `current`. A stored support-state column that
can drift from that queue is forbidden. Deletion and zero counts MUST NOT infer
the state. Source-driven loss follows D54 retraction/closure and does not open
the flag.

### 3.4 SQL-callable functions

Only the functions below are public. Each is schema-qualified, bounded inside
the function independently of an outer `LIMIT`, `SECURITY DEFINER`,
`PARALLEL UNSAFE`, owned by a no-login bridge owner, and defined with an
immutable internal `search_path`. The bridge owner has only the exact PG reads
and local projection RPC capability required by the function; callers cannot
supply URLs, relation names, code, or raw filter expressions.

`facts_as_of`, `graph_neighborhood`, and `graph_path` are `STABLE` within the
PG statement snapshot. The three semantic functions are `VOLATILE` because
they consult an external projection and emit invocation telemetry; the executor
evaluates each syntactic invocation once and never duplicates it as a planner
optimization.

| Function | Signature and row contract |
|---|---|
| `facts_as_of` | `(valid_at timestamptz, believed_at timestamptz, max_rows int)` → fact fields with `evidence_count_current`, `contradict_count_current`, `support_state_current`, applied instants, and `identity_regime = 'current'`; default/hard bounds are in §4.3 |
| `semantic_claims` | `(query text, k int, filters jsonb DEFAULT '{}', embedding_input_policy_version text DEFAULT NULL, embedder_generation text DEFAULT NULL)` → confirmed `claim_id`, rank, score, channel and generation/freshness columns; default/hard `k` is in §4.3 |
| `semantic_chunks` | same arguments → confirmed `chunk_id`, rank, score, channel, separately labeled `source_text` and `location_header`, coordinate/hash and generation/freshness columns |
| `semantic_facts` | same arguments → confirmed `(fact_kind, fact_id)`, rank, score, channel and generation/freshness columns; confirmation is against `facts_current` |
| `graph_neighborhood` | `(start_entity_id uuid, max_depth int, predicates text[] DEFAULT NULL, valid_at timestamptz DEFAULT NULL, believed_at timestamptz DEFAULT NULL, max_edges int)` → deterministic `(path_id, hop, path_position, relation_id)` plus edge fields; default/hard traversal bounds are in §4.3 |
| `graph_path` | `(from_entity_id uuid, to_entity_id uuid, max_depth int, predicates text[] DEFAULT NULL, valid_at timestamptz DEFAULT NULL, believed_at timestamptz DEFAULT NULL, max_paths int, max_edges int)` → deterministic `(path_id, path_length, path_position, relation_id)` plus edge fields; default/hard traversal bounds are in §4.3 |

Semantic filters are typed JSON objects with target-specific allowlists:
claims permit `doc_id`, `source_kind`, `entity_id`, `asserted_from`, and
`asserted_to`; chunks permit `doc_id`, `source_kind`, `source_shape`,
`section_role`, and `language`; facts permit `fact_kind`, `predicate`,
`subject_entity_id`, `object_entity_id`, and `support_state`. Unknown keys,
wrong types, or user-authored predicates are rejected. Fact `support_state` is
exactly `current` or `withdrawn`; stance filters, where exposed, are exactly
`supports` or `contradicts`. Lance applies eligible filters before top-k and PG
repeats every authorization-relevant filter. In v1 `source_shape` is a
Lance-side D80 location-fact filter only: it is not authorization-relevant, PG
does not repeat it, and no `source_shape` column exists in the PG spine.

Each semantic invocation selects or validates exactly one ready
`p1_projection_generation`, `embedding_input_policy_version`, and
`embedder_generation` before embedding the query. An explicitly requested
unavailable generation fails with `generation_unavailable`; it never falls
forward. Results are ranked deterministically by score, then stable item ID.
The function performs Lance nomination first and one target-specific PG
confirmation statement immediately before materializing rows. The PG statement
snapshot is the D48 linearization point and is emitted as `pg_confirmed_at`.
A deletion committed before that snapshot removes the row; a commit after it is
a normal later state change.

`semantic_chunks` obtains body bytes only after PG confirms the current ready
document/version/representation coordinate. It verifies source-content hash,
embedding-text hash, and generated-prefix separation. It returns source body
and D80 deterministic location header in separate columns; the header is never
asserted evidence. A missing body, prefix mismatch, coordinate mismatch, or
hash mismatch drops that candidate. A mixed projection/policy/embedder
generation fails the entire invocation.

The executor captures every invocation, including an invocation returning zero
rows, into `QueryResult.semantic_invocations[]` with nomination count,
confirmed count, stale/body-mismatch drop counts, generations, P1 snapshot,
PG confirmation time, embedding/search latency, and termination reason. Lance
unavailability fails a semantic statement with `lance_unavailable`; plain PG
SQL remains available. PG confirmation unavailability fails the statement.
Partial unconfirmed semantic output is forbidden.

Graph helpers traverse only PG views, use simple-path visited-node semantics,
and order shortest depth first, then relation-ID sequence. Their default/hard
depth, edge, and path bounds are defined only by the §4.3 limits table. Reaching
a cap is disclosed in QueryResult. Raw recursive CTEs remain available only
under §4.1's template linter and the PG snapshot.

## 4. SQL sandbox, tenancy, limits, and result contract

### 4.1 Grammar and rejection contract

One request contains one `SELECT`, `VALUES`, or read-only `WITH [RECURSIVE]`
statement. `UNION`, `INTERSECT`, `EXCEPT`, subqueries, `LATERAL`, joins,
filters, grouping, `HAVING`, windows, ordering, `LIMIT`, and `OFFSET` are
allowed. `explain_sql` alone accepts `EXPLAIN (FORMAT JSON)` and never
`ANALYZE`. Every value originating outside the saved statement uses typed
positional parameters; interpolation is forbidden.

`DISTINCT` and `DISTINCT ON` are allowed, as is aggregate `FILTER (WHERE ...)`.
`TABLESAMPLE`, `WITHIN GROUP`, and all ordered-set aggregates are rejected with
`statement_not_allowed`. A CTE name that shadows any `memory_v1` relation name
is rejected with `relation_not_allowed`.

The normative SRF algorithm is: parse → normalize → enumerate every public SRF
invocation. An invocation is accepted only when its syntactic position is a
top-level `FROM` item or a top-level CTE-body `FROM` item and every argument is
a literal or bound parameter. Each accepted invocation is extracted into its
own `MATERIALIZED` CTE, counted against the default/hard §4.3 invocation caps,
and replaced at its original position by a reference to that CTE. An invocation
in an `IN`/`EXISTS` subquery, a `UNION` arm, a correlated or lateral position, a
nested-subquery `FROM`, or with any row-valued argument is rejected with
`function_placement_not_allowed`. No invocation reaches planning before this
rewrite completes.

The exact scalar/operator allowlist is `=`, `<>`, `<`, `<=`, `>`, `>=`, `IS
[NOT] NULL`, `IS [NOT] DISTINCT FROM`, `IN`, `BETWEEN`, `LIKE`, `ILIKE`, `AND`,
`OR`, `NOT`, `EXISTS`, `ANY`, `ALL`, `+`, `-`, `*`, `/`, `%`, `||`, `@>`, `<@`,
`&&`, `->`, `->>`, `#>`, and `#>>`, plus casts among exposed scalar types. The
exact `pg_catalog` function allowlist is
`count`, `sum`, `avg`, `min`, `max`, `bool_and`, `bool_or`, `array_agg`,
`string_agg`, `jsonb_agg`, `jsonb_object_agg`, `coalesce`, `nullif`,
`greatest`, `least`, `lower`, `upper`, `trim`, `btrim`, `length`,
`octet_length`, `substring`, `replace`, `abs`, `ceil`, `floor`, `round`,
`date_trunc`, `extract`, `make_interval`, `array_length`, `cardinality`,
`jsonb_typeof`, `jsonb_array_length`, `jsonb_build_object`, `row_number`,
`rank`, `dense_rank`, `lag`, `lead`, `first_value`, and `last_value`. The §3.4
functions are the only non-`pg_catalog` calls. The linter admits `string_agg`,
`array_agg`, `jsonb_agg`, and `jsonb_object_agg` only under the default/hard
per-aggregate input-row cap in §4.3: cost admission rejects an estimate above
the cap, and a runtime guard cancels the statement before any aggregate consumes
more than that cap.

DDL, DML, data-modifying CTEs, `SELECT INTO`, row locks, `COPY`, `CALL`, `DO`,
`SET`, transaction control, temporary objects, prepared-statement SQL,
large-object APIs, filesystem/network access, `dblink`, advisory locks,
sleep functions, sequence mutation, extension functions, and all unlisted
relations/functions/operators are rejected from a parsed AST. Schema
qualification cannot escape the allowlist. Raw `WITH RECURSIVE` is restricted
to one documented template shape: one recursive term; a strictly increasing
integer column named `depth`, initialized to `0`, incremented by exactly `1`,
and bounded by the literal predicate `depth < N` where `N <= 6`; no depth
reassignment and no `OR` in the bound predicate. The parsed AST enforces the
complete shape. Everything else is rejected with `unbounded_recursion`; deep
traversal beyond this template is the graph helpers' job.

Only a top-level `ORDER BY` sets `ordered_result = true`; absent it, row order
is explicitly nondeterministic. The parser and PostgreSQL both enforce
read-only execution. Raw PostgreSQL error details, object names outside the
public schema, and query fragments are not returned.

The public error codes are exhaustive:

| Phase | Codes |
|---|---|
| Parse/validation | `parse_error`, `multiple_statements`, `statement_not_allowed`, `relation_not_allowed`, `function_not_allowed`, `function_placement_not_allowed`, `operator_not_allowed`, `invalid_parameter`, `schema_version_mismatch`, `unbounded_recursion` |
| Admission | `estimated_cost_exceeded`, `quota_exceeded`, `concurrency_exceeded`, `saved_query_not_found`, `saved_query_disabled`, `saved_query_incompatible`, `saved_query_revalidation_pending` |
| Execution | `statement_timeout`, `lock_timeout`, `cancelled`, `resource_limit`, `execution_error` |
| Store/confirmation | `pg_unavailable`, `lance_unavailable`, `p2_unavailable` (core-operation-internal only; never returned by public SQL), `corpus_body_unavailable`, `generation_unavailable`, `confirmation_failed` |

Zero rows is success with `empty_result = true`, never an error and never a
D49 negative. The store codes and fallback behavior cross-reference the §7
failure matrix.

### 4.2 Ownership, RLS, and trust boundaries

D68 physical database-/schema-per-deployment routing is the PRIMARY tenancy
boundary. The gateway authenticates a principal, resolves exactly one
deployment, and pool checkout selects that deployment's database and a
deployment-bound login role. SQL text and parameters never choose deployment.

The role split is fixed:

- a no-login migration/table owner owns base objects;
- a distinct no-login view owner has the minimum base-table `SELECT` grants,
  is neither superuser nor `BYPASSRLS`, and owns `security_barrier` views with
  `security_invoker = false`;
- a deployment-bound login role has `USAGE`/`SELECT` only on `memory_v1` and
  `EXECUTE` only on §3.4 functions;
- bridge, gateway-admin, migration, and audit roles are absent from the agent
  pool.

Every tenant-bearing base table has `ENABLE ROW LEVEL SECURITY` and `FORCE ROW
LEVEL SECURITY`. Its policy keys on `session_user`, which PostgreSQL preserves
as the authenticated login role across owner-evaluated views and `SECURITY
DEFINER` functions even though `current_user` changes in those contexts. The
normative predicate is:

```sql
USING (
  deployment_id = (
    SELECT deployment_id
    FROM ops.tenant_role_map
    WHERE role_name = session_user
  )
)
```

`ops.tenant_role_map` is owner-maintained and is not visible to query roles;
one login maps to one deployment. The view owner is not `BYPASSRLS`, and base
tables use `FORCE ROW LEVEL SECURITY`, so the policy remains bound during
owner-context expansion of the `security_barrier`, `security_invoker = false`
views. Each §3.4 `SECURITY DEFINER` bridge function independently re-derives
the deployment from `session_user` through the same map and passes that value
explicitly to every Lance/P2 RPC filter it invokes. A NULL map lookup fails
closed with zero rows and performs no projection RPC. These RLS rules are
defense in depth behind D68 physical routing, not a shared-database tenancy
alternative.

A client-writable custom GUC is not an authority; `SET` and `set_config` are
unavailable. `PUBLIC` has no create, usage, table, function, or default
privileges. The query role has no base-schema usage, role membership, outbound
network, server-file, large-object, or operator-table capability.

Every checkout resets all session state and reapplies role, `search_path`,
timeouts, memory, temp, parallelism, and read-only transaction state before use;
every check-in rolls back and discards the session on reset failure. Query
transactions use `READ ONLY, READ COMMITTED` and have one statement unless an
internal semantic bridge performs its bounded nomination/confirmation work.

The adversarial CI suite runs under the real query and bridge roles. It MUST
cover two deployments with distinguishable sentinels; direct qualification,
CTEs, subqueries, unions, lateral joins, casts, error messages, global lookup
joins, function indirection, recursive queries, saved queries, malicious Lance
IDs, stale P2 paths, tombstoned bodies, and A→B→A pool reuse. It also fuzzes at
least 10,000 valid parsed ASTs per release. Acceptance is zero unauthorized
rows/bytes/identifiers in results, errors, notices, or retained logs; zero base
object access; and zero cross-checkout session contamination.

A deployment is one content trust domain. The engine enforces cross-deployment
and operator-table isolation, read-only behavior, caps, audit, and mass-read
metering. It does not infer whether an authorized within-deployment read was
caused by prompt injection. Trusted/untrusted prompt separation, tool approval,
outbound destinations, and egress policy belong to the agent harness. Any two
populations that must not read one another require separate deployments; a
saved-query filter is not an authorization boundary.

### 4.3 Limits

The gateway clamps a requested value to the interactive hard cap or rejects it
when clamping would change query semantics. The analytical tier requires an
operator entitlement and a separate one-concurrent-query pool; it retains the
same schema, grammar, RLS, D48 checks, QueryResult contract, and semantic/graph
caps. The table below is the single normative source for every §3.4 function
default and hard bound and every executor resource bound; prose elsewhere names
the applicable default/hard class and cites this table.

| Resource | Default | Interactive hard cap | Analytical hard cap |
|---|---:|---:|---:|
| SQL statements/request | 1 | 1 | 1 |
| SQL text | — | 64 KiB | 64 KiB |
| Bound parameters / encoded bytes | 64 / 256 KiB | 256 / 1 MiB | 256 / 1 MiB |
| Statement timeout | 5 s | 15 s | 60 s |
| Lock timeout | 250 ms | 1 s | 2 s |
| Idle transaction | 5 s | 10 s | 15 s |
| Returned rows | 200 | 1,000 | 10,000 |
| Returned encoded bytes | 1 MiB | 8 MiB | 64 MiB |
| `work_mem` | 16 MiB | 32 MiB | 64 MiB |
| Temporary files | 64 MiB | 256 MiB | 1 GiB |
| Planner `total_cost` admission | 1,000,000 | 5,000,000 | 50,000,000 |
| Recursive CTEs / maximum depth | 1 / 4 | 1 / 6 | 1 / 6 |
| Input rows per `string_agg`/`array_agg`/`jsonb_agg`/`jsonb_object_agg` invocation | 10,000 | 10,000 | 10,000 |
| `facts_as_of` returned rows | 200 | 1,000 | 1,000 |
| Semantic SRF calls / `k` each / total nominations | 1 / 20 / 100 | 3 / 100 / 200 | 3 / 100 / 200 |
| Semantic chunk source text | 512 KiB/invocation | 4 MiB/statement | 4 MiB/statement |
| Neighborhood depth / edges | 2 / 100 | 4 / 500 | 4 / 500 |
| Path depth / paths / edges | 4 / 3 / 100 | 6 / 10 / 500 | 6 / 10 / 500 |
| Concurrent statements per principal | 2 | 4 | 1 |
| Concurrent statements per deployment | 8 | 16 | 4 |
| Principal statement-seconds / rolling 60 s | 30 | 60 | 60 |
| Deployment statement-seconds / rolling 60 s | 120 | 240 | 240 |

Parallel query is disabled for interactive roles. Client disconnect triggers PG
and projection cancellation within one second. A wire row cap does not bound
work below an aggregate or sort; timeout, cost admission, memory/temp limits,
concurrency, and rolling quotas remain mandatory. Larger exports use the
separate governed scan/export surface, not open interactive SQL.

### 4.4 `QueryResult/v1`

Every successful, empty, truncated, rejected, or failed ad-hoc/saved query
carries this provenance header before any rows:

```text
contract = "QueryResult/v1"
grade = "exploratory_tabular"
request_id, deployment_id
surface_manifest_hash, query_space_schema = "memory_v1"
query_hash
saved_query = {query_id, namespace, name, version, query_hash} | null
referenced_views[], referenced_functions[], source_grain_tags[]
columns[{name, sql_type, nullable}], rows[]
returned_row_count, returned_byte_count
limits{row_cap, byte_cap, statement_timeout_ms, analytical_tier}
truncated, truncation_reason
exact_total_known, exact_total
ordered_result
empty_result, negative_kind = null
execution_started_at, evaluated_at, pg_snapshot_at, elapsed_ms
termination_reason, error_code, warnings[]
semantic_invocations[]
p2_snapshot = null                         # public SQL never reads P2
```

`query_hash` is SHA-256 over the normalized AST plus parameter type vector, not
parameter values. `exact_total_known` is true only for a completed, parser-
recognized outer exact-count query; a cap probe establishes truncation but not
an exact total. `source_grain_tags` describe referenced relations and do not
grade the result. `evaluated_at` is non-null only when EVERY referenced public
relation/function is a current-or-as-of fact/graph surface and those references
supply one compatible applied instant. Any mix with evidence, history, or
live-content views forces `evaluated_at = null`.

`exploratory_tabular` explicitly does **not** guarantee:

- a platform result grain or correctness of the caller's interpretation;
- D49 `unknown_entity`, `known_empty`, or `boundary` intent negatives;
- contradiction-group co-member completeness;
- fact-to-evidence association or evidence completeness;
- exact totals unless `exact_total_known` is true;
- deterministic order unless `ordered_result` is true;
- current-fact semantics when the query reads evidence/history views or
  projects away clocks;
- as-of or historical row membership with `*_current` columns does not assert
  those counts held at the historical instant;
- a non-null `evaluated_at` for a query that mixes a current-or-as-of fact/graph
  surface with evidence, history, or live-content views; the mix forces null;
- semantic completeness, exhaustive absence, or freedom from caller-authored
  filter/join/aggregation errors.

The three §3.1 core operations continue to return full D49 `Envelope`s with
typed grain, negatives, contradiction handling, freshness, truncation,
hydration drops, and explicit associations. A generic QueryResult-to-Envelope
adapter is forbidden.

## 5. Saved-query registry

The registry has a stable identity row and immutable versions:

```text
saved_queries(
  deployment_id, query_id, namespace, name, description, owner_principal,
  origin, created_at, disabled_at, latest_version
)
saved_query_versions(
  deployment_id, query_id, version, sql, query_hash,
  parameter_schema, declared_result_schema, declared_interpretation,
  query_space_major, validated_surface_manifest_hash, default_limits,
  status, assurance, author_principal, approver_principal,
  validation_report, created_at, superseded_at
)
```

`(deployment_id, namespace, name)` and `(deployment_id, query_id, version)` are
unique. Versions are append-only; edits create a version. Status is `draft`,
`pending_revalidation`, `active`, `deprecated`, `disabled`, or `broken`. Origin
is `human`, `agent`, `import`, or `shipped_example`. Assurance is
`customer_authored`, `customer_reviewed`, or `shipped_example`; none means
platform fact assurance. The maximum is 1,000 query identities per deployment,
50 versions per identity, and 64 KiB SQL per version.

Per principal, at most 50 draft identities and 200 draft versions may exist,
at most 10 query identities may be created in a rolling hour, and the encoded
SQL plus draft registry metadata counts toward a 4 MiB draft-byte ceiling.
Drafts are excluded from default discovery. Exceeding any deployment or
principal registry limit returns `quota_exceeded`.

Saving parses against a declared `memory_vN`, validates parameter and result
schemas, rejects interpolation and forbidden AST nodes, runs safe EXPLAIN/cost
admission, verifies default limits, and executes operator-owned positive,
empty, tombstone, and cap fixtures. Parameters use JSON Schema scalar/array
types and are bound, never rendered into SQL. A version pins the exact manifest
hash on validation. Publication of any `surface_manifest_hash` change and the
registry transition are one atomic operation: every `active` version moves to
`pending_revalidation` before the new hash is visible. That state is
non-executable, and an execution attempt fails admission with
`saved_query_revalidation_pending`. An incompatible major or failed
revalidation moves the version to `broken`.

Re-approval from `pending_revalidation` to `active` requires the same principal
class as first activation: a deployment operator or an explicit deployment
policy. Successful automatic revalidation MAY restore `active` only when the
new manifest is minor-compatible and all validation fixtures pass; the actor,
old/new hashes, report, and transition are audited. A validator captures the
manifest hash at start and compare-and-swaps that same hash at transition: if
the hash changes while validation runs, its result cannot activate the version,
which remains or returns to `pending_revalidation` for a fresh validation.

Agents can create drafts. Only a deployment operator or explicit deployment
policy can activate, make discoverable-by-default, or approve a version.
Saved queries are invoked through `run_saved_query`; they do not become
top-level MCP tools. Customer review can attest an application schema and test
fixtures, but execution still returns `exploratory_tabular`. Promotion to a
platform operation is possible only through §1.13 and a new binding design.

The platform owns sandboxing, tenancy, D48 view/function behavior, execution
limits, and truthful QueryResult provenance. The saved-query owner owns the
meaning and completeness of filters, joins, aggregates, labels, and declared
interpretation. There is no v1 marketplace, transitive dependency, function
chain, or auto-install path.

Disabling or deleting an identity is enforced at admission time: every version
becomes non-executable for new attempts and leaves normal discovery immediately.
Already-running statements finish under their existing caps, with best-effort
cancellation requested. Registry SQL can contain customer data, so a hard
deletion purges SQL, descriptions, schemas, and validation samples;
non-reversible audit retains only IDs, hashes, actor, timestamps, and action.
Corpus deletion never requires rewriting a saved query: live execution observes
the invariant views and no result cache exists.

## 6. Discovery, manifest, and benchmark identity

Every view, column, and function has manifest-owned comments stating one-row-
per grain, key/join path, current/history meaning, null-clock convention,
deletion exclusions, count exactness, orientation/evidence status, limits, and
failure behavior. Comments are complete sentences and avoid internal table
names. Each view/function has a valid example; the compact first-call resource
includes the three core operations, the query-space hash, hard limits, and
worked examples for current facts, testimony, aggregation, graph traversal,
and semantic-to-relational composition. It states that empty SQL is untyped and
that claims do not answer current truth.

`describe_query_space` reads the checked-in manifest and verified runtime
introspection. `search_query_space` searches only names, comments, tags, and
examples in that manifest. Neither reads tenant content or exposes arbitrary
`pg_catalog`. A runtime/manifest mismatch disables open SQL for that deployment
with `schema_version_mismatch`.

The consumption skill's default motion is the three assured operations for
common question answering and SQL for aggregation, joins, graph composition,
and the long tail. It includes the same five worked examples as discovery and
warns that claim rows are testimony, empty SQL is not `known_empty`, outer
queries can erase grain/contradiction/evidence context, and every cap requires
inspection. It contains no benchmark name or benchmark-tuned hint.

`surface_manifest_hash` is the lowercase hexadecimal SHA-256 of UTF-8 RFC 8785
canonical JSON with exactly these top-level members:

1. `views_schema`: PostgreSQL major, schema major, views sorted by qualified
   name, ordered columns/types/nullability, keys, grain/clock tags, comments,
   and a canonical AST serialization of each definition;
2. `function_signatures`: functions sorted by qualified name with ordered
   argument names/types/defaults/bounds, ordered return columns/types,
   volatility, parallel/security mode, contract version, and comments;
3. `core_operation_descriptors`: the three sorted descriptors with name,
   version, input schema, Envelope version, grain/intent, bounds, and
   implementation-chain hash;
4. `limits`: the exact grammar/operator/function allowlists and all default,
   interactive, analytical, semantic, graph, concurrency, and quota limits.

The canonical AST serializer and version are pinned in the manifest generator;
golden-vector fixtures for that pinned serializer are checked in and MUST pass.
Raw SQL text is never a hash input and is not hashed as an intermediate
surrogate; only the pinned canonical AST serialization represents SQL
definitions. Manifest and runtime-introspection type names are normalized to
the canonical names returned by `pg_catalog.format_type` before comparison and
serialization. Formatting and comments outside manifest fields cannot change
the hash. The resulting hash is independent of PostgreSQL minor version (the
declared PostgreSQL major remains a manifest field). Physical indexes, plans,
statistics, data, projection contents, saved queries, and legacy adapters are
excluded. Any semantic view change, even with unchanged columns, changes the
definition AST and the hash. Any semantic function change requires a contract-
version/signature change and therefore changes the hash.

A future v10 protocol MUST pin `surface_manifest_hash` in place of the tool-
catalog hash. v9 catalog-hash runs and v10 surface-manifest runs are explicitly
noncomparable. Full v10 protocol design is outside this document. The migration
measurements in §8 require legacy, hybrid-three-core, and open-only arms on the
same store, model, prompt budget, time anchor, and resource budget; traces
record manifest hash, core calls, SQL hashes, errors, caps, latency/cost, and
projection-confirmation statistics. Raw SQL/parameters follow §7 retention.
No arm receives product behavior unavailable to customers.

## 7. Observability, retention, and failure behavior

Every attempt emits an audit event with request, deployment and principal IDs;
surface/manifest/query/saved-query hashes; referenced public objects; admission
decision; PG/Lance/P2/corpusfs generations and freshness when touched; timings;
plan-cost estimate; rows/bytes/temp work; limits; cancellation/error code;
semantic nomination/confirmation/drop counts; graph depth/rows/cap events; and
core-operation name/version when applicable. Cost attribution charges PG
statement time/temp work, query-embedding and Lance work, confirmation work,
and returned bytes to principal and deployment. The cost ledger is operator-
only.

Default telemetry never persists raw ad-hoc SQL, parameter values, result rows,
chunk bodies, or PG error text. It retains audit metadata for 30 days and
non-content aggregate cost/reliability metrics for 13 months. Saved SQL follows
registry retention. An operator-enabled debug capture is deployment-scoped,
encrypted under a separate key, access-audited, excludes results/bodies,
redacts parameters by schema, lasts at most seven days, and auto-purges. Legal
hold is an operator policy outside the library and cannot make forgotten corpus
content reappear through the query surface.

Incident controls include per-principal/deployment open-SQL kill switches,
per-function bridge disablement, saved-query fleet disablement, role revocation,
pool drain, manifest-version quarantine, and projection-generation quarantine.
Recovery never grants raw tables or returns unconfirmed projection rows.

| Failure/disagreement | Binding behavior |
|---|---|
| PostgreSQL unavailable | All query and core paths fail `pg_unavailable`; no projection result escapes |
| Lance unavailable | Plain PG SQL/graph remains available; semantic SRFs fail `lance_unavailable`; a core operation can return only a descriptor-permitted PG channel with a D49 `boundary`, otherwise it fails |
| P2 unavailable/stale | Public PG graph remains authoritative; core operations use bounded PG traversal and disclose P2 freshness/boundary; stale P2 units drop after PG confirmation |
| Corpusfs/P1 body unavailable | Metadata SQL remains available; body-bearing candidates drop and are counted; a body-required invocation with no valid body fails `corpus_body_unavailable` |
| PG and Lance disagree | PG wins; stale candidates drop; mixed generation or authorization uncertainty fails the semantic invocation |
| PG and P2 disagree | PG wins; a failing edge drops the complete nominated path; P2 aggregates never become final answers |
| Body coordinate/hash/prefix disagrees | Candidate drops; no bytes return; systemic mismatch quarantines the projection generation |
| Manifest/runtime disagrees | Open SQL and saved queries fail `schema_version_mismatch`; the three core operations remain available only if their own descriptors/invariants verify |
| Forget is pending or incomplete | Every affected lineage path fails closed under D48/D74; absence is indistinguishable from never existed |

## 8. Dual-surface migration and terminal criteria

Migration is a protocol deprecation, not a catalog deletion:

`Frozen` means a capability/Envelope-shape freeze. Security fixes, D41/D48/D54
invariant fixes, and tenancy fixes are REQUIRED throughout the window. New
parameters, new grains, and new behavior are forbidden.

1. **Introduction.** Ship `memory_v1`, QueryResult, discovery, semantic/graph
   functions, registry, and examples alongside all 20 frozen legacy adapters.
   HTTP `/recipes` and `/recipe/{name}`, SDK `recipes()`/`run_recipe()`, CLI
   `remember query list/run`, MCP recipe tools, and existing Envelope parsing
   remain unchanged. New entry points are additive.
2. **Default cutover.** After §9 gates and the same-condition hybrid arm meet
   the noninferiority gates below, default MCP/prompt discovery contains the
   three core operations plus query/discovery/registry infrastructure. The
   other 17 adapters remain callable but acquire deprecation metadata; their
   `examples.*` replacements and migration guides ship in the same release.
3. **Deprecation window.** Each of the 17 names remains supported for at least
   two minor releases and 180 days after its published deprecation date.
   HTTP emits `Deprecation` and dated `Sunset` headers; SDKs emit typed warnings;
   CLI emits stderr warnings; MCP descriptions name the example replacement;
   release notes and direct notices cover known active integrations.
4. **Noncore removal.** A demoted adapter can disappear only in a major API/
   protocol release, after its minimum window, zero unresolved contracted
   blockers, replacement documentation across API/SDK/CLI/MCP, and less than
   1% of eligible retrieval calls using it for 60 consecutive days. Stored
   legacy Envelopes remain parseable. No adapter fabricates an Envelope from
   its example saved query.

For the removal gate, `eligible retrieval calls` means all authenticated
retrieval-bearing requests across all deployments, with no sampling and no
steering exclusions. Measurement is owned by the operator telemetry pipeline
and §7 cost ledger. Any traffic steering away from an adapter during its
measurement window voids that window and restarts the 60-day clock.

The hybrid noninferiority gate for default cutover is: overall end-task success
lower 95% confidence bound no worse than -2 absolute points versus the legacy
arm; no critical question category worse than -5 points; zero D41/D48/D54 or
cross-deployment violations; p95 latency and metered cost no more than 25%
higher; invalid/repaired SQL rate at most 5%; and every cap/drop visible in the
contract. Failing a gate keeps the dual surface active and requires redesign.

Exactly three platform operations remain after noncore removal. Removing the
final one-call recipe layer is not decided here; it is the explicit deferral in
§10. Legacy compatibility adapters during migration are frozen compatibility
surface, not precedent for a fourth operation.

## 9. Validation gates

Each implementation batch passes its relevant gates before merge; all gates
pass before default cutover.

1. **DDL/manifest identity.** Runtime introspection equals the checked-in
   manifest exactly; all 24 view keys are unique on fixtures; all comments and
   examples validate; pinned-AST-serializer golden vectors pass; two independent
   builds produce the same manifest hash, including across supported PostgreSQL
   minor versions.
2. **D48 deletion matrix.** Deleting each fixture lineage, version,
   representation, claim, fact provenance, K target, P1 candidate, P2 edge, and
   corpus body yields zero leaked rows/bytes across every view, helper, semantic
   target, core operation, legacy adapter, saved query, count, and artifact
   fetch. A path with one invalid edge returns no partial path. The enumerated
   targets × surfaces coverage-matrix artifact is generated, reviewed,
   versioned, and checked into the repository. `100%` means that exact artifact
   passes; no implicit or sampled cells count. Fixtures use the D48 INNER JOIN/
   `EXISTS` template and contain no legacy LEFT JOIN orphan branch.
3. **D41 clocks.** Tests cover equality at `valid_from`, exclusion at
   `valid_until`, null endpoints, future ingestion, invalidation equality,
   distinct `valid_at`/`believed_at`, one shared statement instant, and current
   identity regime. Claim-window fixtures cover inclusive endpoint overlap and
   an `instant` claim whose equal endpoints still match. Acceptance is exact
   expected membership with no claim row accepted as current fact.
4. **D54 lifecycle.** Re-extracting one lineage or repeating a claim leaves
   both counts unchanged; adding a second lineage changes the appropriate
   count by one; `supports` and `contradicts` remain distinct; processing loss
   alone opens `support_withdrawn` and yields `support_state = 'withdrawn'`;
   source deletion never does. Closing the queue row restores
   `support_state = 'current'` without updating a stored fact-state column.
   Acceptance is exact counts/state on every relation and observation fixture.
5. **Sandbox/RLS.** The §4.2 adversarial suite and 10,000-AST fuzz run produce
   zero cross-deployment/operator/base-object disclosures, zero state changes,
   and zero pool contamination under production roles. The suite proves that
   owner-context view expansion filters on the authenticated `session_user`,
   that the same identity is preserved and enforced inside every `SECURITY
   DEFINER` function, that a NULL `ops.tenant_role_map` lookup returns zero rows
   and performs no RPC, and that all properties survive A→B→A pool reuse. The
   SRF fuzz corpus also proves that post-rewrite invocation count equals exactly
   the count of accepted top-level syntactic forms.
6. **Resource enforcement.** Cartesian, recursive, sort, aggregate, sleep-
   attempt, semantic-fanout, disconnect, and concurrency probes terminate at
   or before the configured hard cap plus one-second cancellation grace. Rows,
   bytes, temp work, calls, and quotas never exceed caps; every intervention is
   reported. Fixtures include pathological regex/operator/function attempts
   rejected during parsing, a Cartesian `FROM` after three materialized semantic
   CTEs, runtime overflow probes for every capped collection aggregate,
   malicious recursive-AST fuzz cases (depth reassignment, nonliteral/`OR`
   bounds, extra recursive terms, and nonunit increments), and cancellation no
   later than one second after timeout.
7. **Semantic confirmation.** On frozen candidate sets, confirmed IDs equal the
   existing D48 hydrator exactly. Across 10,000 injected stale/mismatched
   candidates, zero unconfirmed rows or bytes return, every drop category is
   counted exactly, and no invocation mixes projection, policy, or embedder
   generations.
8. **Graph authority.** PG helpers equal exhaustive ground truth on generated
   graphs through their caps, use deterministic ordering, prevent cycles, obey
   both clocks, and report all caps. P2-accelerated core outputs equal their
   PG-confirmed units with zero partial paths.
9. **Result contracts.** Every success, empty, truncation, rejection, timeout,
   cancellation, and store failure contains the complete QueryResult header and
   correct non-guarantee state. The three core operations pass the existing D49
   Envelope suite without weakened grain, negative, contradiction, freshness,
   or association guarantees. Named honesty fixtures reproduce the reviewer's
   queries (b) and (d) across `facts_visible_history`, `facts_as_of`, and
   `graph_edges_visible_history`: after current testimony changes, historical/
   as-of membership stays correct while `evidence_count_current`,
   `contradict_count_current`, and `support_state_current` reflect the live read
   state and never claim to be historical values. The consumption skill and
   discovery warnings against claim-to-current laundering are gate-tested; the
   five worked examples include one negative example showing the wrong claim-
   window query and its correct `facts_current` replacement.
10. **Registry/governance.** Mutation attempts cannot alter versions; agents
    cannot activate drafts; quota boundaries return `quota_exceeded`; deletion
    disables admission immediately; every `examples.*` query parses, executes
    within caps, and passes its documented fixture without being exposed as an
    assured tool. State-machine fixtures cover activation authority,
    `active` → `pending_revalidation` atomically with every manifest-hash change,
    pending execution rejection with `saved_query_revalidation_pending`, manual
    re-approval authority, minor-compatible automatic restoration only after
    fixtures pass, failure/incompatibility to `broken`, and audited transitions.
    A barrier test changes the hash during a running validation and proves its
    stale compare-and-swap cannot activate the version; it remains pending until
    validation against the new hash succeeds.
11. **Migration parity.** Each frozen legacy adapter passes its existing
    contract suite throughout the window; every surface emits the same
    deprecation date/replacement; the §8 hybrid noninferiority gate passes
    before default cutover.
12. **Telemetry/retention.** Cost totals reconcile to PG/Lance counters within
    1%; kill switches stop new work within five seconds; default logs contain
    zero raw SQL, parameter values, rows, bodies, or private PG errors; debug
    captures expire within seven days.

## 10. Recorded deferrals (each with its trigger)

| Deferred | Bound reason | Adoption trigger and required decision gate |
|---|---|---|
| Complete removal of the three-operation recipe layer | One-call typed defaults remain measured product value; this design does not decide their deletion | A future v10 open-only arm shows no material loss from removing the one-call fallback: overall success lower 95% bound ≥ -2 points versus hybrid, every critical category ≥ -5 points, zero added D41/D48/D54/security violations, median calls increase ≤1, and p95 latency/cost increase ≤20%; the full API/SDK/CLI/MCP deprecation schedule for all three names is also complete. Removal then requires a separate major-version binding decision. |
| Public P2-snapshot Cypher subset | Snapshot aggregates/absence/path choice cannot be repaired by ID hydration; PG is authoritative | Begin a follow-on design only after either PG graph helper p95 exceeds 250 ms at bound on three representative deployments for three consecutive 30-day windows, or at least 5% of retrieval-bearing production requests require graph patterns not expressible in one SQL/helper call. Adoption additionally requires a finite ID/path-nomination-only subset, exhaustive syntax list, no final aggregate/absence claim, D48 unit confirmation, current limits, and §9 security tests. Until then the supported public subset is empty. |
| Migration from Lance to pgvector | SQL ergonomics alone does not justify vector relocation or dual-write risk | Evaluate when bridge-caused semantic p95 exceeds 750 ms or availability falls below 99.9% for three consecutive 30-day windows, or measured total bridge operations cost exceeds a pgvector projection by 2× at representative scale. Adopt only with recall@k loss ≤1 point, p95 ≤80% of the bridge, ingest p95 ≤120% of current, storage ≤150%, full multi-target/generation/D80-filter support, equivalent RPO/RTO and tenancy, and zero D48 failures. The required cutover is per-generation hash-verified backfill → idempotent outbox dual-write → 30-day shadow-read parity → reversible read switch → 30-day rollback window → Lance retirement after audit. |
| Media-segment public views/SRF | The binding media row/embedding contract is not yet part of this schema | First production corpus requiring SQL composition over D65 media segments; a separate design adds typed locators/derivation and rolls the schema/hash. |
| Saved-query marketplace and shared dependencies | Signing, supply-chain review, publisher liability, and fleet recall are outside customer-local registry v1 | First operator-approved cross-deployment sharing requirement, followed by a signing/install/revocation design and adversarial supply-chain suite. |
| Saved-query registry import/export | Portable registry bytes need a format, trust boundary, and compatibility rule that local registry v1 does not yet need | First customer migration between deployments requiring registry transfer. Adoption requires a versioned interchange format, explicit trust/signing model, and source/target manifest-hash pinning and compatibility validation before activation. |

## 11. Implementation sequence (binding)

1. **Batch A — schema contract:** machine-readable manifest, full DDL note,
   invariant views, comments, canonicalizer/hash, D41/D48/D54 tests.
2. **Batch B — safe execution:** roles/RLS, parser/allowlists, limits,
   QueryResult, discovery, audit/cost controls, adversarial suite.
3. **Batch C — semantic bridge:** three Lance SRFs, D80 generation pinning,
   in-function PG confirmation, body verification, cancellation/telemetry.
4. **Batch D — graph:** PG edge views, bounded helpers, recursive-CTE linter,
   P2-confirmed acceleration and PG fallback inside the two context operations.
5. **Batch E — customer space:** immutable registry, governance, all 17
   `examples.*` mappings, drift and deletion behavior.
6. **Batch F — dual surface:** API/SDK/CLI/MCP additions, frozen adapters,
   consumption-skill/docs rewrite, noninferiority run, deprecation telemetry.

A later batch cannot merge around a failed dependency gate. Each behavioral
change ships with same-change documentation, manifest/version updates, and the
relevant §9 evidence.
