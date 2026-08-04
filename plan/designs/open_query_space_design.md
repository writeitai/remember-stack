# Open query space — binding design

*2026-08-04. Binding once accepted. Replaces the growing default recipe
catalog with a versioned, invariant-compiled PostgreSQL query space, a full
read-only native P2 graph surface, and three platform-assured one-call
operations. Rationale:
`plan/analysis/open_query_space_codex.md` and
`plan/analysis/open_query_space_grok.md`. This design refines the public
surface bound by `agent_retrieval_surface_design.md`; D41, D48, D49, D54,
and D80 remain controlling.*

**Bound two-layer retrieval headline (reused verbatim):**

> RememberStack has two deliberately separate truth layers. Claims are
> immutable source testimony (“what was asserted, by whom, when”);
> facts—relations and observations—are the adjudicated current worldview (“what
> the system currently holds true”): supersession-adjudicated, clocked on two
> time axes (when a fact held in the world, and when the system learned it),
> evidence-counted per distinct source—repetition is not corroboration—and
> contradiction-tracked. The `fact_claim_evidence` association is the auditable
> bridge between the layers, recording which claims support or contradict each
> fact. Query claims to inspect testimony; query facts to answer current-truth
> questions, then follow the bridge to see why the system believes the fact.
>
> (Internally these guarantees are decisions D41 and D54.)

## 1. Principles (binding)

1. **PostgreSQL authorizes every live result.** The live public data language IS
   PostgreSQL 16 SQL over the versioned `memory_v1` schema. Physical tables,
   raw projection tables, and operator schemas are never public. PostgreSQL
   views compile row-level invariants; Lance and P2 nominations that are
   promoted to live or evidence-grade output receive PostgreSQL confirmation.
   Full read-only Cypher over P2 is separately public as a disclosed
   point-in-time snapshot surface.
2. **The default assured surface has exactly three intent operations.**
   `resolve_entity`, `question_context`, and `current_context` are the complete
   shipped platform-operation set. They retain their full D49 `Envelope`
   contracts. SQL/Cypher execution, discovery, saved-query execution, and the
   allowlisted SQL functions are query infrastructure, not additional intent
   operations.
3. **Open computation is graded by contract.** Every ad-hoc or saved SQL
   execution returns `QueryResult/v1` with grade `exploratory_tabular`; every
   Cypher execution returns it with grade `snapshot_graph`. A SQL view's source
   grain never becomes a claim about an arbitrary outer query's result grain,
   and a graph row or aggregate never becomes a claim about state after the
   disclosed snapshot instant.
4. **D41 is compiled, not taught.** A relation named `current` applies world
   validity and transaction validity at one disclosed evaluation instant.
   Claim validity remains immutable source testimony and never answers what
   currently holds.
5. **D48 is fail-closed at each stated time boundary.** Each live
   lineage-derived row has a surviving live-document provenance path. The P2
   rebuild applies the same visibility rule at its consistent export snapshot,
   so no lineage tombstoned as of `built_at` enters that generation. P1 and P2
   nominations that claim live confirmation receive PostgreSQL confirmation;
   graph paths confirm as units; chunk bodies confirm their current coordinate
   and hashes. Missing, orphaned, mismatched, or incompletely forgotten state is
   absent from live output or fails the call. A later deletion can remain in an
   older `snapshot_graph` result only with that older `built_at` disclosed.
6. **D54 counting has one meaning.** `evidence_count` and
   `contradict_count` count distinct current-testimony document lineages per
   `supports` or `contradicts` stance. `support_state` is exactly `current` or
   `withdrawn`; `withdrawn` comes only from the open processing-driven
   `support_withdrawn` review state. A zero count MUST NOT manufacture it.
7. **The graph is first-class in its native language.** Full read-only Cypher
   over the P2 projection IS public in v1. Each immutable generation IS a
   consistent point-in-time projection: the rebuild applies D48 visibility in
   one repeatable-read export, `built_at` names that cut, and the snapshot does
   not contain lineages tombstoned at that instant. It ages rather than lies. A
   Cypher aggregate IS therefore a correct aggregate of memory as of `built_at`,
   the same epistemic object as a read replica with disclosed lag. PostgreSQL
   remains the live-truth authority; `memory_v1.graph_edges_*`, recursive CTEs,
   `graph_path`, and `graph_neighborhood` remain the live traversal surface.
   Honesty comes from the `snapshot_graph` grade and mandatory snapshot
   provenance, not aggregate, absence, or projection bans.
8. **Lance-backed SQL preserves D80 and D48.** `semantic_claims`,
   `semantic_chunks`, `semantic_facts`, `semantic_entities`,
   `lexical_claims`, `lexical_chunks`, and `fetch_chunk_bodies` pin one ready
   Lance projection and every applicable embedding-input-policy/embedder
   generation per invocation, then perform target-specific in-function
   PostgreSQL confirmation before exposing rows or bytes. `lexical_facts` is
   absent until the §10 P1 indexing trigger is met.
9. **Customer semantics stay customer-owned.** Saved SQL and agent-authored
   Cypher inherit the platform sandbox, tenancy, applicable time boundary,
   limits, and execution provenance. Their filters, aggregates, labels, and
   interpretations are not platform-endorsed fact semantics. Shipped examples
   live under the same rule.
10. **Bounds are part of the public contract.** SQL and Cypher work, results,
    recursive traversal, Lance nomination/body fetch, concurrency, and retained
    telemetry have defaults and hard caps in §4. No cap is silent.
11. **The schema is discoverable before use.** The same checked-in manifest
    owns SQL view/function and P2 graph-type/property comments, grains, keys,
    examples, compatibility, and `surface_manifest_hash`. Raw `pg_catalog`
    discovery is not exposed.
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
14. **The caller owns planning.** There is no product NL-to-SQL/Cypher planner
    or LLM on the query path. Raw-table SQL, DuckDB/exports as a live
    correctness path, and content-level ACL emulation inside one deployment are
    non-goals.

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
| `claims_hybrid_rrf` | `examples.claims_hybrid_rrf` | `semantic_claims` + `lexical_claims` with documented SQL RRF; no parity claim with the legacy hybrid |
| `chunks_hybrid_rrf` | `examples.chunks_hybrid_rrf` | `semantic_chunks` + `lexical_chunks` with documented SQL RRF; no parity claim with the legacy hybrid |
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
| `multi_hop_context` | `examples.multi_hop_context` | `graph_path`/`graph_neighborhood` plus semantic/lexical SRFs and explicit joins |
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
| `explain_sql(sql, parameters)` | `EXPLAIN (FORMAT JSON)` without execution; the same parser, relation, function, and operator gates |
| `query_cypher(cypher, parameters, max_rows?)` | One process-isolated, read-only LadybugDB statement; `QueryResult/v1` with grade `snapshot_graph`; the request execution option `confirm` defaults to `false` |
| `explain_cypher(cypher, parameters)` | Engine plan without query execution; the same Cypher parser, read-only, tenancy, and cap gates |
| `describe_query_space(pattern?, include_examples=false)` | Manifest-backed exact schema, functions, comments, examples, versions, hashes, and limits |
| `search_query_space(query, k=10)` | Search over checked-in manifest text only; `k` range 1–25 |
| `list_saved_queries(namespace?, status?)` | Registry metadata only |
| `describe_saved_query(namespace, name, version?)` | Immutable version, parameters, declared columns, validation state, and hashes |
| `run_saved_query(namespace, name, version, parameters)` | Same executor and `QueryResult/v1` as `query_sql` |

The public SRFs are reached only as allowlisted SQL calls through `query_sql`,
`explain_sql`, or saved SQL; none is a fourth assured operation or a top-level
intent tool:

| SRF family | Members |
|---|---|
| Bitemporal facts | `facts_as_of` |
| Semantic P1 nomination | `semantic_claims`, `semantic_chunks`, `semantic_facts`, `semantic_entities` |
| Lexical P1 nomination | `lexical_claims`, `lexical_chunks`; `lexical_facts` is the explicit §10 deferral |
| Confirmed body fetch | `fetch_chunk_bodies` |
| Live PG graph | `graph_neighborhood`, `graph_path` |

The three assured operations are pinned in the manifest as:

| Operation | Initial retained version | Contract |
|---|---:|---|
| `resolve_entity` | 1 | D49 `Envelope`; ranked survivor candidates, `unknown_entity`/`boundary`, current identity regime |
| `question_context` | 4 | D49 recall `Envelope`; separately typed claims/chunks and opt-in facts/evidence/entities channels, D48 drops, freshness, bounds |
| `current_context` | 1 | D49 fact `Envelope`; current facts, both evidence stances, explicit associations/totals, support state |

`question_context` v4 retains its existing `query`, `k`, and `candidate_k`
inputs and adds the optional `include_facts: boolean = false` and
`include_entities: boolean = false`. With `include_facts=true`, the operation
uses semantic P1 facts nomination, confirms current relations and observations
in PostgreSQL, and applies the same source-diverse, both-stance backing
machinery as `current_context`, with `evidence_per_fact` fixed to that
operation's default of 3. The channel returns at most `min(k, 30)` facts, and
its fact associations share `current_context`'s existing 60-record evidence
budget; the optional channel does not multiply that budget. Because P1 does
not yet expose lexical fact
nomination, v4 is semantic-only for facts; satisfying the §10 trigger adds the
lexical channel through a later versioned descriptor change. With
`include_entities=true`, exact name/alias resolution and semantic description
nomination both contribute survivor candidates: resolved candidates precede
semantic-only candidates, each channel retains its native rank, duplicates
collapse on survivor `entity_id` before PostgreSQL confirmation, and the
combined channel returns at most 20 candidates (resolution first, then
semantic by rank). The channels
join the existing typed `facts[]`, `fact_evidence[]`, `evidence_totals[]`, and
`entities[]` fields, with backing claims deduplicated into the existing
`evidence[]` field. Claims, chunks, facts, fact-evidence associations, and
entity candidates remain separately grain-labeled and are never flattened into
one generic result list.

This is an evolution of `question_context`, with a version bump plus atomic
tool-catalog and `surface_manifest_hash` rolls; it is not a fourth operation
and does not enter the §1.13 anti-accretion gate. The decision follows measured
one-call ergonomics of approximately 2.2 calls per question and observed agent
routing through the default operation: the recall endpoint is maximally
complete when opted in. `current_context` remains the unchanged dedicated
current-facts operation. In descriptors and discovery, `resolve_entity` means
assured name/alias resolution with D49 negatives, while
`semantic_entities` means exploratory description-similarity nomination.

An implementation change that alters any descriptor, selection semantics,
bound, field, negative, or association increments that operation's version and
rolls `surface_manifest_hash`. P2 use inside an assured operation is an internal
implementation path distinct from the public Cypher contract: eligible graph
expansion in `question_context` and `current_context` uses P2 nomination,
confirms every returned unit in PostgreSQL, and falls back to bounded PG
traversal when P2 is unavailable. P2 never changes the Envelope guarantee.

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

**Complete public facts-layer surface.** For the `memory_v1` query space it is
exactly `facts_current`, `facts_visible_history`, `facts_as_of`,
`fact_claim_evidence_live`, `evidence_lineage`,
`contradiction_members_current`, `testimony_currency_events_visible`,
`graph_edges_current`, `graph_edges_visible_history`, and `semantic_facts`.
Together these expose current and historical fact membership, both clocks,
current source-lineage evidence, the auditable claim bridge, contradiction
membership, testimony-currency transitions, relation-shaped graph projection,
and semantic fact-label entry. `lexical_facts` is not in this surface because
the audited P1 port and Lance adapter do not lexically index fact labels; its
only admission path is the §10 trigger. Every future facts-layer capability
MUST land in this enumeration in the same change as its contract, manifest,
tests, and documentation; an omitted capability is not public.

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
| Public P2 Cypher | Apply D48 during the repeatable-read rebuild; disclose the immutable generation and `built_at`; post-build changes wait for rebuild; apply post-hoc PG confirmation only when `confirm=true` |
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
PG statement snapshot. The four semantic functions, two lexical functions,
and body-fetch function are `VOLATILE` because they consult an external
projection or body store and emit invocation telemetry; the executor evaluates
each syntactic invocation once and never duplicates it as a planner
optimization.

| Function | Signature and row contract |
|---|---|
| `facts_as_of` | `(valid_at timestamptz, believed_at timestamptz, max_rows int)` → fact fields with `evidence_count_current`, `contradict_count_current`, `support_state_current`, applied instants, and `identity_regime = 'current'`; default/hard bounds are in §4.3 |
| `semantic_claims` | `(query text, k int, filters jsonb DEFAULT '{}', embedding_input_policy_version text DEFAULT NULL, embedder_generation text DEFAULT NULL)` → confirmed `claim_id`, rank, score, channel and generation/freshness columns; default/hard `k` is in §4.3 |
| `lexical_claims` | `(query text, k int, filters jsonb DEFAULT '{}')` → the same confirmed claim result columns as `semantic_claims`, with lexical-channel rank/score semantics below |
| `semantic_chunks` | `(query text, k int, filters jsonb DEFAULT '{}', embedding_input_policy_version text DEFAULT NULL, embedder_generation text DEFAULT NULL)` → confirmed `chunk_id`, rank, score, channel, separately labeled `source_text` and `location_header`, coordinate/hash and generation/freshness columns |
| `lexical_chunks` | `(query text, k int, filters jsonb DEFAULT '{}')` → the same confirmed chunk result columns as `semantic_chunks`, with lexical-channel rank/score semantics below |
| `fetch_chunk_bodies` | `(chunk_ids uuid[])` → `input_ordinal`, confirmed `chunk_id`, current document/version/representation/section coordinate, source/embedding hashes, separately labeled `source_text` and D80 `location_header`, policy/embedder generations, and freshness columns; no nomination or ranking columns |
| `semantic_facts` | `(query text, k int, filters jsonb DEFAULT '{}', embedding_input_policy_version text DEFAULT NULL, embedder_generation text DEFAULT NULL)` → confirmed `(fact_kind, fact_id)`, rank, score, channel and generation/freshness columns; confirmation is against `facts_current` |
| `semantic_entities` | `(query text, k int, filters jsonb DEFAULT '{}')` → description/profile-vector search over `entities.lance`, returning PG-confirmed survivor `entity_id`, entity type/name/profile orientation fields, rank, score, channel and generation/freshness columns; confirmation is against `entities_current`. The scored entity-nomination method does not exist on the shared P1 port today and is ADDED by this change (the port exposes only id-addressed `entity_vectors`), parallel to the lexical score extension |
| `graph_neighborhood` | `(start_entity_id uuid, max_depth int, predicates text[] DEFAULT NULL, valid_at timestamptz DEFAULT NULL, believed_at timestamptz DEFAULT NULL, max_edges int)` → deterministic `(path_id, hop, path_position, relation_id)` plus edge fields; default/hard traversal bounds are in §4.3 |
| `graph_path` | `(from_entity_id uuid, to_entity_id uuid, max_depth int, predicates text[] DEFAULT NULL, valid_at timestamptz DEFAULT NULL, believed_at timestamptz DEFAULT NULL, max_paths int, max_edges int)` → deterministic `(path_id, path_length, path_position, relation_id)` plus edge fields; default/hard traversal bounds are in §4.3 |

Semantic filters are typed JSON objects with target-specific allowlists:
claims—and therefore `semantic_claims` and `lexical_claims` equally—permit
`doc_id`, `source_kind`, `entity_id`, `asserted_from`, and `asserted_to`;
chunks—and therefore `semantic_chunks` and `lexical_chunks` equally—permit
`doc_id`, `source_kind`, `source_shape`, `section_role`, and `language`;
facts permit `fact_kind`, `predicate`, `subject_entity_id`,
`object_entity_id`, and `support_state`; entities permit only `entity_type`.
`fetch_chunk_bodies` accepts no filters. Unknown keys, wrong types, or
user-authored predicates are rejected. Fact `support_state` is exactly
`current` or `withdrawn`; stance filters, where exposed, are exactly `supports`
or `contradicts`. Lance applies eligible filters before top-k and PG repeats
every authorization-relevant filter. In v1 `source_shape` is a Lance-side D80
location-fact filter only: it is not authorization-relevant, PG does not repeat
it, and no `source_shape` column exists in the PG spine.

`lexical_claims` and `lexical_chunks` perform BM25/exact-term nomination through
the same P1 lexical port and Lance FTS/BM25 adapter path used by the internal
claim and chunk hybrids; a second
lexical implementation or PostgreSQL FTS substitute is forbidden. The port's
result extends to carry the already-computed score without adding another
search. `channel = 'bm25'`; `rank` is the one-based Lance nomination position
and can contain gaps after PG confirmation drops stale IDs; `score` is the raw
Lance BM25 relevance score, where larger is better. A lexical score is neither
normalized nor comparable to a semantic score. Score ties break by stable item
ID. Both lexical SRFs perform the same target-specific PG confirmation
statement as their semantic siblings immediately before exposing results.

`semantic_entities` embeds the query once and reaches `entities.lance` only
through the shared P1 search port's entity-nomination method. It searches the
entity description/profile vector rather than aliases, then confirms survivor
identity and `entity_type` against `entities_current` in the same function.
Direct Lance access from SQL or the bridge function is forbidden.

Each semantic or lexical nomination invocation selects or validates exactly one
ready `p1_projection_generation`, `embedding_input_policy_version`, and
`embedder_generation` before any query embedding or search. An explicitly
requested unavailable generation fails with `generation_unavailable`; it never
falls forward. Results are ranked deterministically by score, then stable item ID.
The function performs Lance nomination first and one target-specific PG
confirmation statement immediately before materializing rows. The PG statement
snapshot is the D48 linearization point and is emitted as `pg_confirmed_at`.
A deletion committed before that snapshot removes the row; a commit after it is
a normal later state change. Lexical invocation skips query embedding but pins
and reports the same projection and applicable D80 generations.

`semantic_chunks`, `lexical_chunks`, and `fetch_chunk_bodies` share one body
path. It obtains bytes only after PG confirms the current ready
document/version/representation/section coordinate, then verifies
source-content hash, embedding-text hash, and generated-prefix separation. It
returns source body and D80 deterministic location header in separate columns;
the header is never asserted evidence. `fetch_chunk_bodies` is this exact path
minus nomination: `input_ordinal` records first input position, duplicate IDs
collapse to that first position, and more than 50 IDs fails
`invalid_parameter` before any store read. An outer query requires
`ORDER BY input_ordinal` to contract row order under §4.4. Missing, stale,
tombstoned, coordinate-mismatched,
prefix-mismatched, or hash-mismatched IDs return no row. Each category and the
total absent count appear in the invocation drop disclosure. A mixed
projection/policy/embedder generation fails the entire invocation. All three
body-bearing functions share the §4.3 chunk-text byte caps.

The executor captures every semantic, lexical, and body-fetch invocation,
including one returning zero rows, into the existing
`QueryResult.semantic_invocations[]` with requested/nomination count,
confirmed count, stale/body-mismatch drop counts, generations, P1 snapshot,
PG confirmation time, applicable embedding/search/body latency, and
termination reason. For `fetch_chunk_bodies`, requested IDs occupy the existing
nomination-count slot; no new `QueryResult/v1` field is added. Lance
unavailability fails a Lance-backed statement with `lance_unavailable`; plain
PG SQL remains available. PG confirmation unavailability fails the statement.
Partial unconfirmed output is forbidden.

Graph helpers traverse only PG views, use simple-path visited-node semantics,
and order shortest depth first, then relation-ID sequence. Their default/hard
depth, edge, and path bounds are defined only by the §4.3 limits table. Reaching
a cap is disclosed in QueryResult. Raw recursive CTEs remain available only
under §4.1's template linter and the PG snapshot.

### 3.5 Full P2 Cypher read surface

The v1 dialect IS the read portion implemented by the repository-pinned
`ladybug==0.18.2` engine and the P2 adapter's verified recursive-pattern
syntax. It is not a promise of every clause in another openCypher
implementation. An engine upgrade or dialect change rolls
`surface_manifest_hash` and reruns the §9 dialect gate.

One accepted statement consists of one read query, including `UNION` or `UNION
ALL` arms. The allowed clauses are `MATCH`, `OPTIONAL MATCH`, `WHERE`, `WITH`,
`RETURN`, and `UNWIND`; `RETURN DISTINCT`, arbitrary projections and
expressions, aliases, `ORDER BY`, `SKIP`, and `LIMIT` are allowed. Expressions
include parameters, property access, arithmetic/boolean/string/list operators,
read-only built-in scalar/list/path functions, simple and searched `CASE`, bare
pattern predicates, and `EXISTS { MATCH ... }` or `COUNT { MATCH ... }`
subqueries. Aggregation is unrestricted by result meaning: `count`, `collect`,
`min`, `max`, `avg`, `sum`, grouping projections, and engine-supported
read-only aggregates are allowed.

Patterns allow directed or undirected relationships, labels/types, inline
property predicates, named paths, and variable-length relationships. The
engine's recursive modes `SHORTEST`, `ALL SHORTEST`, `WSHORTEST(property)`,
`ALL WSHORTEST(property)`, `TRAIL`, and `ACYCLIC`, including the
adapter-verified inline recursive predicate form `(r, n | WHERE ...)`, are
allowed. The engine's v0.18.2
recursive upper bound is 30 hops and is also an executor hard cap. Unsupported
engine syntax, including list comprehensions over path elements, fails
`cypher_parse_error`; the surface does not silently translate it into a
different query.

The parser rejects all mutation and external-action paths before the engine
sees them: `CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH DELETE`, and `REMOVE`;
node/relationship/schema/type/sequence/graph/index DDL and every drop/alter
form; standalone or in-query `CALL`/procedures; `LOAD FROM`, `LOAD CSV`, `COPY`,
`IMPORT`, `EXPORT`, `ATTACH`, and every file or network source; extension
install/load/update including `INSTALL`, `FORCE INSTALL`, `UPDATE` (extension
update), and every function capable of file, network, attachment, or extension
access; engine maintenance statements including `ANALYZE` and `CHECKPOINT`;
macros, `COMMENT ON`, transactions, `USE`, `PROFILE`, and embedded `EXPLAIN`;
and multi-statement scripts. Every such parsed construct, including a construct
hidden in a `UNION` arm, comment boundary, subquery, or mixed read/write query,
fails `cypher_not_allowed`. Invalid text or syntax not implemented by the
pinned dialect fails `cypher_parse_error`.

The parser reject list is the mandatory control, and the serving worker adds
defense-in-depth beneath it: the worker opens the published generation with
`Database(..., read_only=True)`, which blocks mutation but does NOT block
`COPY TO`/`EXPORT`/`LOAD`/`INSTALL`-class file and extension actions — which is
exactly why those constructs MUST die in the parser before the engine sees
them. The worker process is filesystem-confined to the read-only snapshot
directory and a bounded scratch area; it has no write path to host-visible
locations and no outbound network capability. A parser bypass reaching the
engine is a §9 gate failure, not a tolerated fallback.

The P2 rebuild opens one PostgreSQL `REPEATABLE READ` export snapshot, applies
D48 visibility inside it, and records that export cut as `built_at` — bound
concretely as the export transaction's PostgreSQL `transaction_timestamp()`,
captured once at export start and stored immutably with the published
generation (never a wall clock read at publish or query time). The
published LadybugDB generation is therefore one consistent projection of
memory at that instant, not a partially refreshed graph. It excludes lineages
tombstoned by `built_at`; later ingests, deletions, withdrawals, identity
changes, and relation changes appear only after another rebuild. Snapshot
aggregates and absence tests are correct for that disclosed cut. This is the
same epistemic contract as arbitrary agent-authored SQL computation with
provenance and caller-owned interpretation, except that the relevant authority
instant is `built_at` rather than a live PostgreSQL statement snapshot.

The initial exposed P2 property schema, versioned as projection contract
`p2-rebuild-2026.07`, is exhaustive:

| Graph type | Exposed snapshot properties |
|---|---|
| Node `Entity` | `id UUID`, `type STRING`, `name STRING`, `normalized_name STRING`, `summary STRING`, `created_at TIMESTAMP` |
| Node `Document` | `id UUID`, `title STRING`, `source_uri STRING`, `published_at DATE` |
| Edge `RELATES` | `relation_id UUID`, `subject_id UUID`, `object_id UUID`, `predicate STRING`, `fact STRING`, `evidence_count INT64`, `contradict_count INT64`, `confidence DOUBLE`, `contradiction_group UUID`, `valid_from TIMESTAMP`, `valid_until TIMESTAMP`, `ingested_at TIMESTAMP`, `invalidated_at TIMESTAMP` |
| Edge `MENTIONED_IN` | `mention_count INT64`, `first_seen TIMESTAMP` |
| Edge `DOC_CROSSREF` | `from_doc_id UUID`, `to_doc_id UUID`, `kind STRING`, `context STRING` |
| Edge `IS_DOCUMENT` | no user properties |

Rows carry exactly the values projected by `RETURN`, including scalar,
aggregate, list, path, node, relationship, and exposed property values. Engine
structural node/relationship/path values are serialized as typed QueryResult
values; engine-local physical offsets are not stable identifiers. There is no
identifier-only projection restriction, aggregate ban, absence ban, or generic
Cypher-result-to-Envelope adapter.

Agents compose the two public data languages by projecting entity or relation
IDs from `query_cypher` and binding them as `query_sql` parameters, or by
passing them to an assured operation, whenever the answer requires live
confirmation or evidence-grade composition. The `confirm=true` request option
asks the executor to identify top-level typed `Entity`/`RELATES` values and
scalar projections derived by the parsed AST from `Entity.id` or
`RELATES.relation_id`, then check those IDs in one PostgreSQL confirmation
statement under D48/D41 at the confirmation instant. A row containing a
recognized candidate that fails confirmation drops as a unit. Nested IDs
inside an aggregate or collection are not rewritten. Confirmation covers ONLY
`Entity` nodes and `RELATES` relationships: `Document` nodes, `MENTIONED_IN`,
`DOC_CROSSREF`, and every other projected graph type are never confirmed by
this option and pass through snapshot-scoped. The discovery resource and
consumption skill state this in one line: "confirm=true checks live membership
of projected entity/relation IDs; it does not make any other part of the
result live."

Confirmation reports `confirmation = {requested, pg_confirmed_at, nominated,
confirmed, dropped_stale}`. These are unique confirmable-ID counts and
`confirmed + dropped_stale = nominated`. Confirmation does not rerun the Cypher
plan, change path selection, reconstruct absence, or re-ground any aggregate
value. The result retains grade `snapshot_graph`, its aggregate values remain
scoped to `built_at`, and an invocation with no confirmable top-level ID reports
zero for all three counts. `confirm` is an executor option outside the Cypher
text and bound parameter map; Cypher text and parameters cannot enable, weaken,
or redirect confirmation.

## 4. Query sandbox, tenancy, limits, and result contract

### 4.1 Grammar and rejection contract

One SQL request contains one `SELECT`, `VALUES`, or read-only `WITH [RECURSIVE]`
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
`&&`, `->`, `->>`, `#>`, `#>>`, `~`, `~*`, `!~`, and `!~*`, plus casts among
exposed scalar types. The
exact `pg_catalog` function allowlist is
`count`, `sum`, `avg`, `min`, `max`, `bool_and`, `bool_or`, `array_agg`,
`string_agg`, `jsonb_agg`, `jsonb_object_agg`, `coalesce`, `nullif`,
`greatest`, `least`, `lower`, `upper`, `trim`, `btrim`, `length`,
`octet_length`, `substring`, `replace`, `regexp_replace`, `abs`, `ceil`,
`floor`, `round`,
`date_trunc`, `extract`, `make_interval`, `array_length`, `cardinality`,
`jsonb_typeof`, `jsonb_array_length`, `jsonb_build_object`, `row_number`,
`rank`, `dense_rank`, `lag`, `lead`, `first_value`, and `last_value`. The §3.4
functions are the only non-`pg_catalog` calls. Per the operator's measure-first
directive of 2026-08-04, regex operators and `regexp_replace` are admitted;
pre-banning them is speculative, and a runaway expression burns at most one
statement timeout before cancellation.

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
| Cypher parse/validation | `cypher_parse_error`, `cypher_not_allowed` |
| Admission | `quota_exceeded`, `concurrency_exceeded`, `saved_query_not_found`, `saved_query_disabled`, `saved_query_incompatible`, `saved_query_revalidation_pending` |
| Execution | `statement_timeout`, `lock_timeout`, `cancelled`, `resource_limit`, `execution_error` |
| Store/confirmation | `pg_unavailable`, `lance_unavailable`, `p2_unavailable`, `corpus_body_unavailable`, `generation_unavailable`, `confirmation_failed` |

The two `cypher_*` codes are Cypher-phase codes emitted before engine
execution: forbidden parsed constructs use `cypher_not_allowed`, while invalid
or unsupported text uses `cypher_parse_error`. `p2_unavailable` is public for
both Cypher entry points; an engine process fault or Cypher execution timeout
maps to it with no partial rows. It remains internal to a core operation when
that operation completes through its PostgreSQL fallback. Zero rows is success
with `empty_result = true`, never an error and never a D49 negative. The store
codes and fallback behavior cross-reference the §7 failure matrix.

### 4.2 Ownership, RLS, and trust boundaries

D68 physical database-/schema-per-deployment routing is the PRIMARY tenancy
boundary. The gateway authenticates a principal, resolves exactly one
deployment, and pool checkout selects that deployment's database and a
deployment-bound login role. SQL text and parameters never choose deployment.

P2 uses the same D68 posture: every deployment's snapshot files and local
reader cache are physically separate. The bridge derives the deployment from
the authenticated session identity and resolves that deployment's latest
published, non-quarantined snapshot path server-side. Cypher text, parameters,
labels, properties, engine functions, and execution options never select a
snapshot path, snapshot ID, deployment, database attachment, file, or URI.
Cypher execution occurs in a process-isolated graph worker behind the existing
worker/RPC boundary; the agent process never opens snapshot files directly.

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
internal SRF bridge performs its bounded nomination, confirmation, or body work.

The adversarial CI suite runs under the real query and bridge roles. It MUST
cover two deployments with distinguishable sentinels; direct qualification,
CTEs, subqueries, unions, lateral joins, casts, error messages, global lookup
joins, function indirection, recursive queries, saved queries, malicious Lance
IDs, stale P2 paths, tombstoned bodies, and A→B→A pool reuse. It also fuzzes at
least 10,000 valid parsed ASTs per release. Acceptance is zero unauthorized
rows/bytes/identifiers in results, errors, notices, or retained logs; zero base
object access; and zero cross-checkout session contamination.

The same suite MUST attempt cross-deployment P2 access through every graph RPC
field, malformed worker message, snapshot identifier, path-like parameter,
Cypher literal, and A→B→A worker reuse sequence. Acceptance is zero selection or
disclosure of another deployment's snapshot bytes, schema, IDs, rows, errors,
plans, age, or generation metadata.

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
same language schemas, grammar, tenancy boundary, applicable D48 time boundary,
QueryResult contract, and nomination/body/graph caps. The table below is the
single normative source for every §3.4 function
default and hard bound and every executor resource bound; prose elsewhere names
the applicable default/hard class and cites this table.

| Resource | Default | Interactive hard cap | Analytical hard cap |
|---|---:|---:|---:|
| SQL statements/request | 1 | 1 | 1 |
| Cypher statements/request | 1 | 1 | 1 |
| SQL text | — | 64 KiB | 64 KiB |
| Cypher text | — | 32 KiB | 32 KiB |
| SQL or Cypher bound parameters / encoded bytes | 64 / 256 KiB | 256 / 1 MiB | 256 / 1 MiB |
| SQL or Cypher statement timeout | 5 s | 15 s | 60 s |
| Lock timeout | 250 ms | 1 s | 2 s |
| Idle transaction | 5 s | 10 s | 15 s |
| SQL or Cypher returned rows | 200 | 1,000 | 10,000 |
| SQL or Cypher returned encoded bytes | 1 MiB | 8 MiB | 64 MiB |
| `work_mem` | 16 MiB | 32 MiB | 64 MiB |
| Temporary files | 64 MiB | 256 MiB | 1 GiB |
| Recursive CTEs / maximum depth | 1 / 4 | 1 / 6 | 1 / 6 |
| `facts_as_of` returned rows | 200 | 1,000 | 1,000 |
| Semantic or lexical nomination SRF calls / `k` each / total nominations | 1 / 20 / 100 | 3 / 100 / 200 | 3 / 100 / 200 |
| `fetch_chunk_bodies` calls / chunk IDs each | 1 / 50 | 3 / 50 | 3 / 50 |
| Chunk source text across `semantic_chunks`, `lexical_chunks`, and `fetch_chunk_bodies` | 512 KiB/invocation | 4 MiB/statement | 4 MiB/statement |
| Neighborhood depth / edges | 2 / 100 | 4 / 500 | 4 / 500 |
| Path depth / paths / edges | 4 / 3 / 100 | 6 / 10 / 500 | 6 / 10 / 500 |
| Cypher variable-length upper bound (engine-native, not an executor add-on) | 30 | 30 | 30 |
| Concurrent SQL + Cypher statements per principal | 2 | 4 | 1 |
| Concurrent SQL + Cypher statements per deployment | 8 | 16 | 4 |
| Principal SQL + Cypher statement-seconds / rolling 60 s | 30 | 60 | 60 |
| Deployment SQL + Cypher statement-seconds / rolling 60 s | 120 | 240 | 240 |

Parallel query is disabled for interactive roles. SQL and Cypher consume the
same per-principal/deployment concurrency slots and rolling statement-second
quotas; a caller cannot evade either quota by alternating languages. Cypher
parameters are typed and bound through the engine API and are never
interpolated into text. Client disconnect triggers PG and projection
cancellation within one second. The graph supervisor terminates an unresponsive
worker at that deadline; no buffered partial result crosses the RPC boundary. A
wire row cap does not bound work below an aggregate or sort; timeout, process
isolation, memory/temp limits, concurrency, and rolling quotas remain
mandatory. Larger exports use the separate governed scan/export surface, not
either open interactive language.

### 4.4 `QueryResult/v1`

Every successful, empty, truncated, rejected, or failed SQL/Cypher query carries
this provenance header before any rows:

```text
contract = "QueryResult/v1"
query_language = "sql" | "cypher"
grade = "exploratory_tabular" | "snapshot_graph"
request_id, deployment_id
surface_manifest_hash, query_space_schema = "memory_v1" | null
query_hash
saved_query = {query_id, namespace, name, version, query_hash} | null
referenced_views[], referenced_functions[], source_grain_tags[]
referenced_graph_types[], referenced_graph_properties[]
columns[{name, type, nullable}], rows[]
returned_row_count, returned_byte_count
limits{row_cap, byte_cap, statement_timeout_ms, analytical_tier}
truncated, truncation_reason
exact_total_known, exact_total
ordered_result
empty_result, negative_kind = null
execution_started_at, evaluated_at, pg_snapshot_at, elapsed_ms
termination_reason, error_code, warnings[]
semantic_invocations[]
confirmation = {requested, pg_confirmed_at, nominated, confirmed,
                dropped_stale} | null
p2_snapshot = {snapshot_id, snapshot_version, built_at, age_seconds} | null
```

`query_hash` is SHA-256 over the language-specific normalized AST plus parameter
type vector, not parameter values. `columns[].type` uses canonical PostgreSQL
types for SQL and pinned LadybugDB logical types for Cypher. SQL sets
`query_space_schema = "memory_v1"`, grade `exploratory_tabular`, and
`p2_snapshot = null`. Cypher sets `query_space_schema = null`, grade
`snapshot_graph`, and a non-null `p2_snapshot` for both entry points. The
gateway pins `snapshot_id`, `snapshot_version`, and `built_at` before dispatch;
`age_seconds` is the non-negative difference between `execution_started_at` and
`built_at` and appears on every Cypher result. `query_cypher` never returns a
null `p2_snapshot`.

`exact_total_known` is true only for a completed, parser-recognized outer
exact-count query; a cap probe establishes truncation but not an exact total.
`source_grain_tags` describe referenced SQL relations and do not grade the
result. `referenced_graph_types` and `referenced_graph_properties` are the
parsed Cypher equivalents. `evaluated_at` is non-null only when EVERY referenced
SQL relation/function is a current-or-as-of fact/graph surface and those
references supply one compatible applied instant. Any SQL mix with evidence,
history, or live-content views forces `evaluated_at = null`; Cypher sets it to
null and uses `p2_snapshot.built_at` as its sole authority instant. A
`confirm=true` Cypher request additionally sets `pg_snapshot_at` and
`confirmation.pg_confirmed_at` to its post-hoc confirmation instant.

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

`snapshot_graph` explicitly does **not** guarantee:

- state after `p2_snapshot.built_at`; ingests, deletions, withdrawals, identity
  changes, and relation changes after that instant are not reflected;
- that an aggregate, shortest-path choice, pattern existence result, or absence
  result describes live memory; each is correct only for the complete snapshot
  cut at `built_at`;
- D49 intent negatives, evidence completeness, contradiction-group
  completeness, or a live fact/evidence grade;
- live completeness after `built_at`, even when `confirm=true`; confirmation
  can drop stale projected IDs but cannot add post-build graph state or
  reconstruct the query;
- freedom from caller-authored projection, filter, grouping, path, aggregate,
  or interpretation errors.

Live completeness and live evidence grade require the SQL surface or one of the
three assured operations. Snapshot aggregates and absence claims remain
snapshot-scoped under `confirm=true`; confirmation does not re-ground them.

The three §3.1 core operations continue to return full D49 `Envelope`s with
typed grain, negatives, contradiction handling, freshness, truncation,
hydration drops, and explicit associations. A generic QueryResult-to-Envelope
adapter is forbidden for either grade; in particular, there is no generic
Cypher-result-to-Envelope adapter.

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
schemas, rejects interpolation and forbidden AST nodes, runs safe EXPLAIN for
validation diagnostics, verifies default limits, and executes operator-owned
positive, empty, tombstone, and cap fixtures. Parameters use JSON Schema
scalar/array types and are bound, never rendered into SQL. A version pins the
exact manifest hash on validation.
Publication of any `surface_manifest_hash` change and the
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

Every view, column, SQL function, Cypher entry point, graph type, and graph
property has manifest-owned comments stating its grain/type, key or identity
meaning, current/history/snapshot meaning, null-clock convention, deletion
boundary, count exactness, orientation/evidence status, limits, and failure
behavior. Comments are complete sentences and avoid private PostgreSQL table
names and snapshot paths. Each SQL function and Cypher entry point has a valid
example.

The compact first-call discovery resource, the consumption skill, and the OSS
retrieval docs each open with the exact **Bound two-layer retrieval headline
(reused verbatim)** paragraph under that heading at the top of this design,
before any language or operation choice. That paragraph is the retrieval docs'
opening section, not a sidebar or warning. The first-call resource then
presents the three choices without a preferred language hidden in prose:

- Cypher gives native graph power over a complete, point-in-time P2 snapshot
  with mandatory `built_at` and age;
- SQL gives live PostgreSQL state and direct evidence composition;
- the three assured operations give one-call typed answers with D49 guarantees.

It includes the query-space hash, hard limits, and worked examples for the two
truth layers, current facts, testimony, aggregation, native Cypher
traversal/aggregation, Cypher-ID-to-live-SQL composition, and
semantic-to-relational composition. It states that empty SQL is untyped,
Cypher absence is snapshot-scoped, and claims do not answer current truth.

The bound two-layer example set includes all four examples below verbatim.

**Contrast pair.** This is the WRONG current-truth query because a claim's
immutable validity window says when a source's testimony applies, not what the
system currently believes:

```sql
SELECT claim_id, claim_text, claim_valid_from, claim_valid_until
FROM claims_live
WHERE claim_valid_from <= $1::timestamptz
  AND (claim_valid_until IS NULL
       OR claim_valid_until >= $1::timestamptz);
```

This is the RIGHT query: start from adjudicated current facts and join each fact
to the current testimony that supports or contradicts it.

```sql
SELECT f.*, e.claim_id, e.stance, e.source_handle
FROM facts_current AS f
JOIN fact_claim_evidence_live AS e
  USING (deployment_id, fact_kind, fact_id)
ORDER BY f.fact_kind, f.fact_id, e.stance, e.claim_id;
```

**Predicate-vocabulary discovery.** This discovers the deployed fact
vocabulary before the caller writes predicate filters:

```sql
SELECT predicate, count(*) FROM facts_current GROUP BY 1 ORDER BY 2 DESC;
```

**Full audit trail.** This walks fact → live evidence association → immutable
claim → live source lineage, answering “why do we believe this, per source”:

```sql
SELECT f.fact_kind, f.fact_id, f.predicate,
       e.stance, e.source_handle,
       c.claim_id, c.claim_text, c.asserted_at,
       d.doc_id
FROM facts_current AS f
JOIN fact_claim_evidence_live AS e
  USING (deployment_id, fact_kind, fact_id)
JOIN claims_live AS c
  USING (deployment_id, claim_id)
JOIN documents_live AS d
  ON d.deployment_id = c.deployment_id
 AND d.doc_id = c.doc_id
WHERE f.fact_id = $1::uuid
ORDER BY e.stance, c.asserted_at DESC, d.doc_id, c.claim_id;
```

**Two-layer divergence.** This finds a current adjudicated fact whose newest
current testimony contradicts it; the divergence is visible through the bridge
and does not silently rewrite adjudication:

```sql
WITH ranked_testimony AS (
  SELECT e.deployment_id, e.fact_kind, e.fact_id,
         e.claim_id, e.stance, c.claim_text, c.asserted_at,
         row_number() OVER (
           PARTITION BY e.deployment_id, e.fact_kind, e.fact_id
           ORDER BY c.asserted_at DESC NULLS LAST, c.claim_id
         ) AS testimony_rank
  FROM fact_claim_evidence_live AS e
  JOIN claims_live AS c
    USING (deployment_id, claim_id)
)
SELECT f.*, r.claim_id, r.claim_text, r.asserted_at, r.stance
FROM facts_current AS f
JOIN ranked_testimony AS r
  USING (deployment_id, fact_kind, fact_id)
WHERE r.testimony_rank = 1
  AND r.stance = 'contradicts';
```

Every batch ships same-change OSS documentation for each surface it adds.
Batch F ships the integrated retrieval-docs rewrite with the two-layer
headline as its opening section and all four examples; earlier batches ship
their own view/function/operation pages and never defer those pages to Batch F.

`describe_query_space` reads the checked-in manifest and verified runtime
introspection. `search_query_space` searches only names, comments, tags, and
examples in that manifest. Neither reads tenant content or exposes arbitrary
`pg_catalog`. A runtime/manifest mismatch disables open SQL for that deployment
with `schema_version_mismatch`; a P2 dialect/property-contract mismatch disables
both Cypher entry points with the same code before worker execution.

After its mandatory two-layer opening, the consumption skill presents the same
choice plainly: Cypher for native graph power with point-in-time semantics, SQL
for live and evidence-composable reads, and assured operations for one-call
typed answers. It includes the same worked examples as discovery and contains
no steering language that hides or discourages the Cypher surface. It warns
that claim rows are testimony, empty SQL is not `known_empty`, Cypher
absence/aggregates are scoped to `built_at`, outer queries can erase
grain/contradiction/evidence context, and every cap requires inspection. It
contains no benchmark name or benchmark-tuned hint.

`surface_manifest_hash` is the lowercase hexadecimal SHA-256 of UTF-8 RFC 8785
canonical JSON with exactly these top-level members:

1. `views_schema`: PostgreSQL major, schema major, views sorted by qualified
   name, ordered columns/types/nullability, keys, grain/clock tags, comments,
   and a canonical AST serialization of each definition;
2. `function_signatures`: functions sorted by qualified name with ordered
   argument names/types/defaults/bounds, ordered return columns/types,
   volatility, parallel/security mode, contract version, and comments. This
   includes the exact §3.4 signatures for `lexical_claims`, `lexical_chunks`,
   `semantic_entities`, and `fetch_chunk_bodies`, including lexical
   rank/score meaning, entity confirmation, body/drop disclosure, and shared
   byte caps, plus the exact `query_cypher(cypher, parameters, max_rows?)` and
   `explain_cypher(cypher, parameters)` entry-point signatures, parameter
   schemas, the `confirm` execution option/default, result contract, and
   comments;
3. `core_operation_descriptors`: the three sorted descriptors with name,
   version, input schema, Envelope version, grain/intent, bounds, and
   implementation-chain hash. `question_context` is v4, its input schema
   contains both default-false booleans, and its descriptor names the opt-in
   fact/evidence/entity channels, channel grains, 60-record fact-evidence
   budget, semantic-only fact nomination until §10 is resolved, resolution
   precedence, and PG confirmation. The v4 descriptor change rolls the public
   tool catalog and this manifest atomically;
4. `limits`: the exact SQL grammar/operator/function allowlists; the exact
   Cypher allowed-clause and rejected-clause enumeration; the P2 projection
   contract version and exhaustive node/edge property schema from §3.5; and all
   default, interactive, analytical, semantic/lexical/body, graph, Cypher,
   concurrency, and quota limits.

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
version/signature change and therefore changes the hash. Any Cypher entry-point
signature, confirmation option, allowed/rejected construct, cap, P2 graph type,
or exposed property-set change changes the hash; a property-set change also
increments the P2 projection contract version.

A future v10 protocol MUST pin `surface_manifest_hash` in place of the tool-
catalog hash. v9 catalog-hash runs and v10 surface-manifest runs are explicitly
noncomparable. Full v10 protocol design is outside this document. The migration
measurements in §8 require legacy, hybrid-three-core, and open-only arms on the
same store, model, prompt budget, time anchor, and resource budget; traces
record manifest hash, core calls, SQL/Cypher hashes, P2 snapshot provenance,
errors, caps, latency/cost, and projection-confirmation statistics. Raw
SQL/Cypher and parameters follow §7 retention. No arm receives product behavior
unavailable to customers.

## 7. Observability, retention, and failure behavior

Every attempt emits an audit event with request, deployment and principal IDs;
query language; surface/manifest/query/saved-query hashes; referenced public
objects or graph types/properties; admission decision; PG/Lance/P2/corpusfs
generations and freshness when touched; P2 `built_at` and `age_seconds`;
timings; plan-cost estimate where available; rows/bytes/temp work; limits;
cancellation/error code; Lance/body and Cypher nomination/confirmation/drop
counts; graph depth/rows/cap events; worker exit/fault class; and core-operation
name/version when applicable. Cost attribution charges PG statement time/temp
work, query-embedding and Lance work, graph-worker time, confirmation work, and
returned bytes to principal and deployment. The cost ledger is operator-only.

Default telemetry never persists raw ad-hoc SQL or Cypher, parameter values,
result rows, chunk bodies, or raw PG/LadybugDB error text. It retains audit
metadata for 30 days and non-content aggregate cost/reliability metrics for 13
months. Saved SQL follows registry retention. An operator-enabled debug capture
is deployment-scoped, encrypted under a separate key, access-audited, excludes
results/bodies, redacts parameters by schema, lasts at most seven days, and
auto-purges. Legal hold is an operator policy outside the library and cannot
make forgotten corpus content reappear through a live query surface.

The monitored freshness metric IS `p2_snapshot_age_seconds` for the latest
published non-quarantined generation of each deployment. The operational
target IS at most 3,600 seconds at query dispatch. The operator alert threshold
IS age greater than 5,400 seconds for five continuous minutes; rebuild duration,
publish failures, and the count/rate of Cypher results above target accompany
the alert. Crossing either threshold does not relabel, reject, or silently
refresh a snapshot query: every result still discloses its exact age. V1 never
starts or waits for a P2 rebuild in response to a query.

Incident controls include per-principal/deployment open-SQL and open-Cypher kill
switches, per-function bridge disablement, graph-worker drain, saved-query fleet
disablement, role revocation, pool drain, manifest-version quarantine, and
operator-invoked projection-generation quarantine. Engine faults fail the
request `p2_unavailable` with zero partial rows and are counted in telemetry;
acting on repeated faults (quarantine, generation rollback, engine pinning or
replacement) is an operator decision informed by that telemetry — automatic
quarantine state machines are deliberately not built in v1 (operator
measure-first directive, 2026-08-04; the engine remains replaceable behind the
adapter boundary, so evidence of unusability triggers replacement, not
hardening investment). Recovery never grants raw tables, bypasses snapshot
provenance, or returns partial confirmation output.

| Failure/disagreement | Binding behavior |
|---|---|
| PostgreSQL unavailable | SQL, saved-query, and core paths fail `pg_unavailable`; unconfirmed Cypher remains available at grade `snapshot_graph`; `confirm=true` fails the entire request `pg_unavailable` with no rows |
| Lance unavailable | Plain PG SQL/graph remains available; semantic and lexical SRFs and P1-backed body fetch fail `lance_unavailable`; a core operation can return only a descriptor-permitted PG channel with a D49 `boundary`, otherwise it fails |
| P2 absent/quarantined, graph worker faults, or Cypher execution times out | `query_cypher` and `explain_cypher` fail `p2_unavailable` with no partial results; SQL remains available; core operations use bounded PG traversal and disclose the P2 boundary |
| P2 age exceeds target or alert threshold | Cypher remains available with exact `built_at`, `age_seconds`, and a freshness warning; live SQL remains authoritative; the request never triggers a rebuild |
| Corpusfs/P1 body unavailable | Metadata SQL remains available; body-bearing candidates drop and are counted; a body-required invocation with no valid body fails `corpus_body_unavailable` |
| PG and Lance disagree | PG wins; stale candidates drop; mixed generation or authorization uncertainty fails the semantic invocation |
| PG and P2 differ after `built_at` | Unconfirmed Cypher remains the correct snapshot result; `confirm=true` drops failing projected ID rows and counts them, while paths, aggregates, existence, and absence remain snapshot-scoped; live SQL and assured-operation output follow PG |
| Body coordinate/hash/prefix disagrees | Candidate drops; no bytes return; systemic mismatch quarantines the projection generation |
| Manifest/runtime disagrees | The affected open SQL, saved-query, or Cypher surface fails `schema_version_mismatch`; the three core operations remain available only if their own descriptors/invariants verify |
| Forget is pending or incomplete | Live SQL/core lineage paths fail closed under D48/D74; an older P2 generation can reflect the lineage only as of its disclosed pre-forget `built_at`, and the first post-forget rebuild removes it from all later generations |

## 8. Dual-surface migration and terminal criteria

Migration is a protocol deprecation, not a catalog deletion:

`Frozen` means a capability/Envelope-shape freeze. Security fixes, D41/D48/D54
invariant fixes, and tenancy fixes are REQUIRED throughout the window. New
parameters, new grains, and new behavior are forbidden.

That freeze applies to compatibility versions. `question_context` v4 is the
target core operation evolution bound in §3.1; its v3 compatibility version
remains frozen and callable during the window. Both versions share one
operation name and do not increase the three-operation count.

1. **Introduction.** Ship `memory_v1`, QueryResult, both full read-only Cypher
   entry points, discovery, semantic/lexical/body/graph functions, registry,
   and examples alongside all 20 frozen legacy adapters. The discovery/
   first-call resource and consumption skill lead with the verbatim two-layer
   retrieval headline,
   then present Cypher as native graph power with point-in-time semantics, SQL
   as live/evidence-composable, and assured operations as typed one-call
   answers. HTTP `/recipes` and `/recipe/{name}`, SDK
   `recipes()`/`run_recipe()`, CLI `remember query list/run`, MCP recipe tools,
   and existing Envelope parsing remain unchanged. New entry points are
   additive.
2. **Default cutover.** After §9 gates and the same-condition hybrid arm meet
   the noninferiority gates below, default MCP/prompt discovery contains the
   three core operations plus SQL, full Cypher, discovery, and registry
   infrastructure without language-hiding steering. The other 17 adapters
   remain callable but acquire deprecation metadata; their `examples.*`
   replacements and migration guides ship in the same release.
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
higher; invalid/repaired SQL and Cypher rate at most 5% in each language; and
every cap/drop visible in the contract. Failing a gate keeps the dual surface
active and requires redesign.

Exactly three platform operations remain after noncore removal. Removing the
final one-call recipe layer is not decided here; it is the explicit deferral in
§10. Legacy compatibility adapters during migration are frozen compatibility
surface, not precedent for a fourth operation.

## 9. Validation gates

Each implementation batch passes its relevant gates before merge; all gates
pass before default cutover.

1. **DDL/manifest identity.** Runtime introspection equals the checked-in
   manifest exactly; all 24 view keys are unique on fixtures; all comments and
   SQL/Cypher examples validate; Cypher entry-point signatures, dialect lists,
   caps, P2 projection-contract version, graph types, and property schema match
   the adapter and worker exactly; pinned-AST-serializer golden vectors pass;
   two independent builds produce the same manifest hash, including across
   supported PostgreSQL minor versions.
2. **D48 deletion matrix.** Deleting each fixture lineage, version,
   representation, claim, fact provenance, K target, P1 candidate, P2 edge, and
   corpus body yields zero leaked rows/bytes across every live view, helper,
   semantic/lexical target, entity nomination, ID-addressed chunk-body fetch,
   core operation, legacy adapter, saved query, count, and artifact fetch. A
   path with one invalid edge returns no partial live path.
   The snapshot cells assert the different, explicit boundary: a generation
   built after the deletion contains zero affected node/edge/property values,
   while a generation built before it can return them only with its earlier
   `built_at` and grade `snapshot_graph`. The enumerated targets × surfaces
   coverage-matrix artifact is generated, reviewed, versioned, and checked into
   the repository. `100%` means that exact artifact passes; no implicit or
   sampled cells count. Fixtures use the D48 INNER JOIN/`EXISTS` template and
   contain no legacy LEFT JOIN orphan branch.
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
   the count of accepted top-level syntactic forms. Cross-deployment graph RPC,
   snapshot selector, worker-reuse, path/URI, and malformed-message attempts
   expose zero snapshot bytes, data, schema, plan, identifier, generation, or
   freshness metadata from the other deployment.
6. **Resource enforcement.** Cartesian, recursive, sort, aggregate, sleep-
   attempt, nomination-fanout, disconnect, and concurrency probes terminate at
   or before the configured hard cap plus one-second cancellation grace. Rows,
   bytes, temp work, calls, and quotas never exceed caps; every intervention is
   reported. Fixtures include a Cartesian `FROM` after three materialized
   nomination CTEs, malicious recursive-AST fuzz cases (depth reassignment,
   nonliteral/`OR` bounds, extra recursive terms, and nonunit increments), and
   cancellation no later than one second after timeout. The former
   per-aggregate input-cardinality fixtures are removed. Allowlist conformance
   covers every admitted operator and function; collection aggregates remain
   bounded by the ordinary statement timeout, cancellation, memory, temp, row,
   and byte controls.
7. **Lance SRF confirmation and channel correctness.** On frozen candidate
   sets, every semantic and lexical sibling's confirmed IDs equal the existing
   target-specific D48 hydrator exactly. Frozen exact-term fixtures prove
   `lexical_claims` and `lexical_chunks` emit the same P1 ordering as the
   corresponding internal hybrid channel from the same single port read, with
   correct gap-preserving rank, raw BM25 score, and stable-ID ties. Entity
   fixtures prove `semantic_entities` searches the P1 profile vectors, repeats
   `entity_type`, and returns only `entities_current` survivors. Body fixtures
   prove `fetch_chunk_bodies(ids)` equals the semantic-chunk body path minus
   nomination, rejects a 51st ID before store access, shares statement byte
   caps, preserves first-input ordinal after deduplication, omits every
   missing/stale/tombstoned/hash-mismatched row, and counts each drop exactly.
   Across 10,000 injected stale/mismatched candidates, zero unconfirmed rows or
   bytes return, every drop category is counted exactly, and no invocation
   mixes projection, policy, or embedder generations.
8. **Graph authority.** PG helpers equal exhaustive ground truth on generated
   graphs through their caps, use deterministic ordering, prevent cycles, obey
   both clocks, and report all caps. P2-accelerated core outputs equal their
   PG-confirmed units with zero partial paths. P2 snapshot queries equal the
   complete generated graph at their recorded `built_at`, without a live-state
   claim.
9. **Full Cypher read surface (essentials).** Per the operator's
   measure-first directive (2026-08-04), this gate covers correctness and
   read-only safety, not speculative hardening. Reject-list tests cover every
   §3.5 mutation/procedure/file/extension/maintenance construct, including
   constructs hidden in `UNION` arms, subqueries, and comments; a modest
   accept-list conformance suite proves each allowed clause and recursive mode
   executes on the pinned engine. A read-only smoke proves accepted queries
   change no database file and that `explain_cypher` executes nothing; the
   engine handle is verified `read_only=True`. Snapshot-consistency fixtures
   prove: an edge deleted after build remains in Cypher with the old
   `built_at` and disappears after rebuild; an edge created after build is
   absent from Cypher and present in live SQL; every result discloses the
   correct generation and age. `confirm=true` fixtures prove the confirmed
   projected-ID set and `nominated`/`confirmed`/`dropped_stale` counts equal
   the shared PostgreSQL D48/D41 confirmation machinery at `pg_confirmed_at`,
   while snapshot aggregates remain byte-for-byte unchanged; coverage
   negatives prove `Document`, `MENTIONED_IN`, and `DOC_CROSSREF` projections
   pass through unconfirmed. An engine fault or timeout returns
   `p2_unavailable` with zero partial rows. The adversarial hardening suite
   (large fuzz corpora, overflow-class traversal fuzz, quarantine automation)
   is the §10 deferral below — adopted on evidence, not in advance.
10. **Result contracts.** Every success, empty, truncation, rejection, timeout,
   cancellation, and store failure contains the complete QueryResult header and
   correct grade, snapshot provenance, confirmation block, and non-guarantee
   state. The three core operations pass the existing D49 Envelope suite without
   weakened grain, negative, contradiction, freshness, or association
   guarantees. Named honesty fixtures reproduce the reviewer's queries (b) and
   (d) across `facts_visible_history`, `facts_as_of`, and
   `graph_edges_visible_history`: after current testimony changes, historical/
   as-of membership stays correct while `evidence_count_current`,
   `contradict_count_current`, and `support_state_current` reflect the live read
   state and never claim to be historical values. The consumption skill and
   discovery warnings against claim-to-current and snapshot-to-live laundering
   are gate-tested; worked examples include the wrong claim-window query with
   its correct `facts_current` replacement, predicate-vocabulary discovery,
   full fact → `fact_claim_evidence_live` → `claims_live` → `documents_live`
   source audit, latest-contradicting-testimony divergence, and a
   snapshot-ID-to-live-SQL composition. `question_context` v4 fixtures prove
   both flags default false; each flag works independently and together;
   facts match `current_context`'s current-membership, both-stance,
   source-diverse association/total machinery within the existing 60-record
   fact-evidence budget, fixed evidence-per-fact default, and 30-fact ceiling;
   entity candidates combine resolution and semantic
   nomination with survivor deduplication and PG confirmation; and claims,
   chunks, facts, associations, totals, and entities remain in their existing
   typed fields without grain flattening. The same fixtures prove the catalog
   and manifest roll to v4 while the operation count remains three and
   `current_context` v1 output is unchanged.
11. **Registry/governance.** Mutation attempts cannot alter versions; agents
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
12. **Migration parity.** Each frozen legacy adapter passes its existing
    contract suite throughout the window; every surface emits the same
    deprecation date/replacement; the §8 hybrid noninferiority gate passes
    before default cutover.
13. **Telemetry/retention.** Cost totals reconcile to PG/Lance/graph-worker
    counters within
    1%; kill switches stop new work within five seconds; default logs contain
    zero raw SQL/Cypher, parameter values, rows, bodies, or private PG/engine
    errors; snapshot-age target/alerts and quarantine transitions emit their
    exact metrics; debug captures expire within seven days.

## 10. Recorded deferrals (each with its trigger)

| Deferred | Bound reason | Adoption trigger and required decision gate |
|---|---|---|
| `lexical_facts(query, k, filters)` | The audited P1 contract and store do not provide it: `P1FactRow` stores `label`, but `P1SearchPort` exposes only vector `search_facts`; the Lance facts table builds vector and scalar indexes, no label FTS index or lexical-search adapter method, and the E-wave added lexical reads only for claims and chunks. A public function cannot manufacture a PostgreSQL substitute and call it the P1 facts channel. | Trigger: **fact-label lexical index lands in P1**. Adoption in the same change requires a scored lexical-facts method on the shared P1 port, Lance FTS over `facts.label`, parity with the claims/chunks lexical contract (same P1 port and FTS analyzer, same rank/score semantics, same in-function confirmation) on frozen exact-term fixtures — there is no internal facts hybrid today, so the lexical-facts channel and any internal fusion are introduced in that same change, never cited as pre-existing — `facts_current` confirmation equivalence, the facts-filter allowlist and lexical rank/score contract from §3.4, addition to the §3.2 complete facts-layer enumeration and manifest, a `question_context` descriptor/version roll adding lexical fact fusion, and same-change OSS docs. |
| Complete removal of the three-operation recipe layer | One-call typed defaults remain measured product value; this design does not decide their deletion | A future v10 open-only arm shows no material loss from removing the one-call fallback: overall success lower 95% bound ≥ -2 points versus hybrid, every critical category ≥ -5 points, zero added D41/D48/D54/security violations, median calls increase ≤1, and p95 latency/cost increase ≤20%; the full API/SDK/CLI/MCP deprecation schedule for all three names is also complete. Removal then requires a separate major-version binding decision. |
| Automatic P2 rebuild/refresh on query-time staleness | Query latency and admission are not rebuild-control-plane authority; v1 serves the pinned snapshot with exact age and never starts or waits for a rebuild on a query | Reconsider only after at least 1% of Cypher requests across three deployments observe `p2_snapshot_age_seconds > 5,400` for 30 consecutive days despite the scheduled rebuild service meeting its assigned resources. Adoption requires a separate design for authenticated trigger authority, per-deployment deduplication, backpressure, budget isolation, failure storms, no query waiting, and proof that query-triggered work cannot replace or starve the scheduled rebuild path. |
| Cypher adversarial hardening suite (large fuzz corpora, overflow-class traversal fuzz against the real engine, automatic generation-quarantine state machine, engine-specific abuse caps) | Operator measure-first directive (2026-08-04): no speculative constraints on graph queries or the engine; benchmarking and production telemetry locate real issues first, and an unusable engine is replaced rather than hardened around | Adopt (or replace the engine instead) when telemetry shows engine faults/timeouts on >0.1% of Cypher requests over any 7-day window, any single fault class recurs across three deployments, or a benchmarking campaign reproduces a fault; the §7 fault telemetry and kill switches ship in v1 either way, so the evidence arrives without the machinery |
| Migration from Lance to pgvector | SQL ergonomics alone does not justify vector relocation or dual-write risk | Evaluate when bridge-caused semantic p95 exceeds 750 ms or availability falls below 99.9% for three consecutive 30-day windows, or measured total bridge operations cost exceeds a pgvector projection by 2× at representative scale. Adopt only with recall@k loss ≤1 point, p95 ≤80% of the bridge, ingest p95 ≤120% of current, storage ≤150%, full multi-target/generation/D80-filter support, equivalent RPO/RTO and tenancy, and zero D48 failures. The required cutover is per-generation hash-verified backfill → idempotent outbox dual-write → 30-day shadow-read parity → reversible read switch → 30-day rollback window → Lance retirement after audit. |
| Media-segment public views/SRF | The binding media row/embedding contract is not yet part of this schema | First production corpus requiring SQL composition over D65 media segments; a separate design adds typed locators/derivation and rolls the schema/hash. |
| Saved-query marketplace and shared dependencies | Signing, supply-chain review, publisher liability, and fleet recall are outside customer-local registry v1 | First operator-approved cross-deployment sharing requirement, followed by a signing/install/revocation design and adversarial supply-chain suite. |
| Saved-query registry import/export | Portable registry bytes need a format, trust boundary, and compatibility rule that local registry v1 does not yet need | First customer migration between deployments requiring registry transfer. Adoption requires a versioned interchange format, explicit trust/signing model, and source/target manifest-hash pinning and compatibility validation before activation. |

## 11. Implementation sequence (binding)

1. **Batch A — schema contract:** machine-readable manifest, full DDL note,
   invariant views, comments, canonicalizer/hash, D41/D48/D54 tests.
2. **Batch B — safe execution:** roles/RLS, parser/allowlists, limits,
   QueryResult, discovery, audit/cost controls, adversarial suite.
3. **Batch C — complete Lance bridge:** `semantic_claims`,
   `semantic_chunks`, `semantic_facts`, `semantic_entities`,
   `lexical_claims`, `lexical_chunks`, and `fetch_chunk_bodies`; shared P1-port
   paths, D80 generation pinning, in-function PG confirmation, body
   verification, rank/score and drop disclosure, caps, cancellation/telemetry,
   manifest signatures, and per-surface OSS pages. `lexical_facts` remains only
   the §10 deferral.
4. **Batch D — graph: PG views + helpers + full Cypher read surface:** PG edge
   views, bounded helpers, recursive-CTE linter, both Cypher entry points, pinned
   dialect parser, snapshot schema/provenance, process-isolated worker/RPC,
   shared caps/quotas, optional confirmation, quarantine/observability, and
   P2-confirmed acceleration with PG fallback inside the two context operations.
   This batch also evolves `question_context` to v4 with both default-false
   flags, fact backing and entity-candidate channels, the atomic catalog/
   manifest roll, channel fixtures, and its same-change OSS operation page;
   `current_context` remains v1.
   Its merge gates are §9.1–§9.10, including the
   dialect/read-only, snapshot-consistency, confirmation-equivalence, tenancy,
   fault-containment, and cap proofs.
5. **Batch E — customer space:** immutable registry, governance, all 17
   `examples.*` mappings, drift and deletion behavior.
6. **Batch F — dual surface:** API/SDK/CLI/MCP additions, frozen adapters,
   consumption-skill/docs rewrite led by the verbatim two-layer headline and
   all four bound facts-layer examples, noninferiority run, deprecation
   telemetry.

A later batch cannot merge around a failed dependency gate. Each behavioral
change ships with same-change OSS documentation, manifest/version updates, and
the relevant §9 evidence. Batch F integrates and leads with the rewrite; it
does not substitute for the per-surface documentation in earlier batches.

Batch D's unconfirmed Cypher path depends on Batches A and B and has no Batch C
dependency. Its `confirm=true` slice and `question_context` v4 channels depend
on Batch C because they reuse the shared PostgreSQL D48/D41 confirmation and P1
nomination/body machinery; neither slice can merge around a failed Batch C
confirmation gate.
