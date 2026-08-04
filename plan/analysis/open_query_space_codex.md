# The open query space: replacing or reshaping the recipes layer

Status: exploratory analysis, not a binding design. This document deliberately distinguishes recommendations from decisions that still require measurement or an operator choice.

## Executive finding

The best target is a **federated open query surface**, with PostgreSQL as the authoritative query and policy plane:

1. Give agents read-only PostgreSQL SQL over a small, versioned schema of curated views, not over physical tables.
2. Make live graph edges ordinary rows in that schema, so recursive SQL is always available; add bounded path/neighborhood helpers for ergonomics. Keep P2 openCypher only as a candidate-generating acceleration path whose returned IDs and complete paths are revalidated in PostgreSQL.
3. Compose Lance semantic nominations with SQL through gateway-managed, bounded relations. Do not make a database extension or a pgvector migration a prerequisite for opening the query space.
4. Retain a very thin shipped intent layer—initially `resolve_entity` and `question_context`—while moving all other named retrievals into a versioned, per-deployment saved-query registry. The SQL/schema, semantic, graph, and artifact-fetch operations are infrastructure tools rather than twenty memory-intent tools.
5. Return ad-hoc SQL in a new `exploratory_tabular` result contract. Do not pretend arbitrary tabular results have the semantic guarantees of an `Envelope`.

This accepts the operator's central premise with an important qualification. Frontier agents are generally better served by documented SQL than by an arbitrary, growing menu, especially for counts, group-bys, distributions, comparisons, and joins. But the current evaluation evidence also says agents tend to choose one obvious tool and make few calls. SQL introduces schema selection, temporal logic, join, dialect, and cost failure modes. The premise should therefore be tested in a v10 protocol against both the current menu and a hybrid surface; it should not be treated as proved by model reputation.

## Constraints translated into query-surface rules

| Source | Non-negotiable consequence |
|---|---|
| D41 | A `current` fact applies both world-validity and transaction-validity at one disclosed evaluation time. No view called `current` may apply only `invalidated_at IS NULL`. |
| D48/D74 | P1/P2 may nominate; PostgreSQL authorizes. Every lineage-derived public row inner-joins a live document. Tombstoned lineages, versions, claims, bodies, counts, graph edges, page evidence, and change labels must not leak through another view or function. Incomplete forget is fail-closed. |
| D54 | Fact support and contradiction counts are distinct **current-testimony document lineages**, not claim counts. Processing-driven support withdrawal leaves a visible fact with `support_state = 'support_withdrawn'`; source deletion does not manufacture that flag. |
| D55 | Stable lineage and version identities remain distinct. Historical version views do not collapse snapshot and living-document semantics. |
| D80 | Semantic results disclose and pin the embedding-input policy and embedder generation. Location headers are deterministic retrieval aids, not asserted evidence. |
| Retrieval principles 1–9 | Public grains and associations are explicit; common retrieval remains one-call; descriptions are honest; every operation is bounded; negatives are typed where the operation has enough intent; resolution is uniform; product code has no benchmark-specific behavior. |
| D68 | Physical schema/database isolation remains primary. Every public row and join key still includes `deployment_id`; RLS is defense in depth, not a replacement for routing isolation. |

The current recipe executor, linter, and `Envelope` enforce several of these rules because the possible programs are finite. Opening the language moves enforcement downward into views, roles, the gateway, and a more modest result contract. Anything that cannot actually be enforced there must be disclosed rather than implied.

## 1. Query language and sandbox

### Findings

SQL over curated, versioned views is the strongest default for frontier agents. It is expressive enough for the missing surface—aggregation, grouping, joins, window functions, conditional counts, and recursive traversal—and it is a language models have encountered extensively. It also lets the engine publish one semantic contract independently of physical schema changes.

The recommended public schema is `memory_v1`; unqualified physical schemas are never on `search_path`. Its SQL dialect and supported features must be declared, for example PostgreSQL 16 `SELECT`, `WITH [RECURSIVE]`, `VALUES`, aggregates, windows, and a small allowlisted function set. A request contains one statement. `EXPLAIN (FORMAT JSON)` is allowed; `EXPLAIN ANALYZE` is not, because the latter executes the query.

Read-only is necessary but not sufficient. PostgreSQL read-only transactions disallow ordinary writes and DDL, but callable functions can have effects and temporary-table behavior needs separate control ([PostgreSQL `SET TRANSACTION`](https://www.postgresql.org/docs/16/sql-set-transaction.html)). The gateway must combine:

- parser validation of one `SELECT`/read-only `WITH`/`VALUES` or non-`ANALYZE` `EXPLAIN` statement, rejecting data-modifying CTEs, `SELECT INTO`, and row-locking clauses;
- a transaction set `READ ONLY`, with a short transaction lifetime;
- `SELECT` only on `memory_v1` views and `EXECUTE` only on specifically allowlisted functions;
- no grants on base tables, P1 metadata tables, filesystem/network functions, `dblink`, large-object APIs, `COPY`, unsafe extensions, user-defined functions, or system-admin functions;
- no `CREATE`, `TEMP`, role switching, schema mutation, or arbitrary `SET` capability;
- statement and lock timeouts, bounded output rows and bytes, constrained `work_mem`/temporary files, pool and deployment concurrency limits, and rolling compute quotas;
- cancellation on client disconnect and auditable query hashes, parameters, duration, output size, and termination reason.

A returned-row cap does not bound work done below a sort, join, recursive CTE, or aggregation. A statement timeout and server resource governance remain mandatory. An estimated-cost gate can reject obviously catastrophic plans, but estimates are fallible and cannot be the sole safety mechanism.

### Schema discovery ergonomics

Opening SQL without excellent discovery would recreate an unreachable spine in a different form. The core surface should include:

- `describe_query_space(pattern?, include_examples=false)`: returns schema version/hash; view and function names; exact columns and SQL types; primary grain; join keys; current/history semantics; completeness; clocks; and limits.
- `search_query_space(natural_language)`: searches the same checked-in schema manifest and comments, not tenant data.
- `query_sql(sql, parameters, semantic_bindings=[])`: accepts bound scalar/array parameters and optional bounded P1 relations.
- `explain_sql(sql, parameters)`: a convenience for safe JSON `EXPLAIN`, governed by the same parser and role.

Every view and column needs a `COMMENT` that states its one-row-per grain, whether it is current or historical, which deletions are excluded, how null clocks behave, how it joins, and whether its counts are exact. These comments and a concise set of examples belong in the versioned schema manifest. They should be available before a model's first query, either as a compact prompt resource or a discovery call. Dumping hundreds of raw catalog rows on demand is poor ergonomics.

### Alternatives

| Option | Strengths | Weaknesses | Assessment |
|---|---|---|---|
| Curated versioned views + SQL | Full relational expressivity; semantic firewall; stable docs; query plans; direct aggregation | Requires careful view design and agent SQL competence; cannot enforce result meaning after arbitrary projection | Best primary surface |
| Raw-table SQL + RLS only | Fastest to expose; maximum expressivity | Couples agents to migrations; exposes internal status/audit/orphan rows; makes D41/D48/D54 repeatable query-writing chores; RLS solves tenancy, not semantic safety | Reject |
| GraphQL | Typed discovery, validation, predictable nesting | Aggregation, arbitrary joins, windows, temporal predicates, and recursion need an expanding custom schema; becomes recipes in type form | Useful for application APIs, not the open agent language |
| Datalog | Excellent recursive and temporal rule potential; safer logical subset | Much less model familiarity and ecosystem support; another semantic/runtime layer; SQL data still needs mapping | Interesting research path, not v1 |
| DuckDB attached to a replica/export | Excellent analytical SQL and isolation from serving PG | Replica/export staleness directly conflicts with current-fact and deletion guarantees; attachment and extension surface is broad; no natural authoritative RLS | Good offline analytics tier only, never the authoritative read path |
| PostgREST-style URL filters | Easy HTTP integration; typed basic filters | Weak joins/aggregation/recursion; URL grammar becomes another proprietary catalog | Complement, not replacement |
| Generic PostgreSQL MCP | Familiar `query` tool and schema resources | MCP is transport, not a safety or semantic design; generic servers normally expose whatever DB role can see and may not report provenance/caps | Use MCP as one adapter to this gateway, not as the architecture |

## 2. The view layer

### Proposed `memory_v1` contract

This is a proposed minimum public schema, not pseudonyms for every physical table. Columns are intentionally explicit. Every view includes `deployment_id`, even when a database currently serves only one deployment. IDs are UUIDs unless the physical contract says otherwise; clock columns are timestamp-with-time-zone. No public query may use `SELECT *` as a compatibility promise.

#### Corpus and claim views

- `documents_live(deployment_id, doc_id, source_kind, source_ref, source_uri, versioning_mode, origin, title, current_version_id, current_representation_id, source_modified_at, published_at, language, first_seen_at, last_observed_at)`: one row per live document lineage. It has no `deleted_at` column because deleted rows do not exist in this relation.
- `document_versions_visible(deployment_id, doc_id, version_id, version_no, content_hash, current_representation_id, source_version_ref, source_modified_at, published_at, language, status, ingested_at, superseded_at, is_current_version)`: one row per nondeleted version of a live lineage. `content_hash` makes dedupe identity inspectable, while `is_current_version` preserves D55 rather than quietly treating history as current.
- `sections_live(deployment_id, section_id, doc_id, version_id, representation_id, parent_section_id, node_path, block_start, block_end, title, role, char_start, char_end, page_start, page_end, ordinal, summary, placement_path)`: one row per section in the current ready representation. `summary` is explicitly orientation text, not evidence.
- `chunks_live(deployment_id, chunk_id, doc_id, version_id, representation_id, section_id, ordinal, block_start, block_end, chunk_content_hash, char_start, char_end, token_count, section_path, section_title, section_role, location_facts_json, location_header, policy_generation, embedding_version, embedding_text_hash)`: one row per current ready chunk, metadata only. `location_header` is labeled derived retrieval context under D80.
- `claims_live(deployment_id, claim_id, doc_id, origin_chunk_id, section_id, claim_text, source_span, char_start, char_end, is_attributed, asserted_at, claim_valid_from, claim_valid_until, claim_valid_precision, claim_valid_kind, audit_status, kept_flagged, ingested_at, document_title, source_kind, source_ref, source_uri)`: one row per current-testimony claim from a live lineage.
- `claims_visible_history(deployment_id, claim_id, doc_id, origin_chunk_id, section_id, claim_text, source_span, char_start, char_end, is_attributed, is_current_testimony, asserted_at, claim_valid_from, claim_valid_until, claim_valid_precision, claim_valid_kind, audit_status, kept_flagged, ingested_at, document_title, source_kind, source_ref, source_uri)`: historical testimony only while its document lineage remains live. The view is not named `live` because it includes noncurrent testimony.
- `claim_occurrences_live(deployment_id, claim_id, doc_id, chunk_id, version_id, representation_id, section_id, chunk_ordinal, section_role, derivation_kind, evidence_mode, source_locators)`: one row per explicit current claim occurrence. This is the canonical claim↔chunk association; agents must not infer occurrence from proximity.
- `testimony_currency_events_visible(deployment_id, event_id, claim_id, doc_id, reconciliation_id, became_current, reason, from_extractor_version, from_version_id, occurred_at)`: one row per currency transition whose lineage is still live. It supports D54 audit without exposing a forgotten lineage.

PG intentionally does not store authoritative chunk bodies. Current bodies remain in P1/P3 and are returned only through bounded, PostgreSQL-confirmed semantic bindings or the existing artifact/mount fetch path. Putting a `chunk_text` column in a PG view would falsely describe the architecture and weaken the body-authorization rule.

#### Entity and identity views

- `entities_current(deployment_id, entity_id, entity_type, canonical_name, normalized_name, profile_summary, mention_count, graph_degree, created_at, updated_at)`: one row per survivor entity. `profile_summary` is orientation text.
- `entity_aliases_current(deployment_id, alias_id, source_entity_id, entity_id, alias_text, normalized_lemma, provenance, confidence, first_seen_at, last_seen_at)`: one row per alias after survivor redirection; both original and survivor IDs are explicit.
- `mentions_live(deployment_id, mention_id, doc_id, chunk_id, claim_id, surface_form, normalized_lemma, char_start, char_end, entity_id, canonical_name, entity_type, resolution_method, resolution_confidence, decided_at)`: one row per mention in current content with its current resolution decision.
- `identity_events_visible(deployment_id, event_id, event_kind, entity_id, counterpart_entity_id, mention_id, outcome, method, confidence, decided_at, is_superseded, details)`: one row per visible resolution/merge/split event. Events whose only source is a deleted lineage are absent.
- `entity_document_mentions(deployment_id, entity_id, doc_id, mention_count, first_mentioned_at, last_mentioned_at)`: one row per survivor entity × live document, aggregated from live mentions.

Resolution itself remains a core operation because the semantic negative `unknown_entity` requires a target resolution attempt and ambiguity policy, not merely a query returning zero rows.

#### Fact, evidence, and contradiction views

- `facts_visible_history(deployment_id, fact_kind, fact_id, subject_entity_id, predicate, object_entity_id, statement, fact_label, valid_from, valid_until, ingested_at, invalidated_at, contradiction_group, evidence_count_current, contradict_count_current, support_state)`: one row per historically visible relation or observation with at least one surviving provenance lineage. Clock columns are raw; the name does not imply present truth.
- `facts_current(deployment_id, fact_kind, fact_id, subject_entity_id, predicate, object_entity_id, statement, fact_label, valid_from, valid_until, ingested_at, invalidated_at, contradiction_group, evidence_count, contradict_count, support_state, evaluated_at)`: one row per fact satisfying both clocks at `evaluated_at`. The two count columns use D54's exact names and semantics and are recomputed from current-testimony lineages, not copied from claims.
- `fact_claim_evidence_live(deployment_id, fact_kind, fact_id, claim_id, doc_id, stance, asserted_at, claim_valid_from, claim_valid_until, document_title, source_kind, source_ref, source_uri)`: one row per current-testimony claim explicitly linked to a fact and live lineage. `stance` is `support` or `contradict`; the association is never implied by result ordering.
- `evidence_lineage(deployment_id, fact_kind, fact_id, doc_id, stance, claim_count, representative_claim_id, first_asserted_at, last_asserted_at)`: one row per fact × current-testimony document lineage × stance. `COUNT(*) FILTER (WHERE stance='support')` over this view is the D54 evidence count; summing `claim_count` is not.
- `contradiction_members_current(deployment_id, contradiction_group, fact_kind, fact_id, fact_label, valid_from, valid_until, ingested_at, invalidated_at, evidence_count, contradict_count, support_state, evaluated_at)`: one row per current member. It makes group expansion possible without reverse-engineering physical tables.

For both fact kinds, `facts_current` uses one statement-stable instant:

```sql
invalidated_at IS NULL
AND (valid_from  IS NULL OR valid_from  <= statement_timestamp())
AND (valid_until IS NULL OR valid_until >  statement_timestamp())
```

The view emits that same `statement_timestamp()` as `evaluated_at`. A future as-of surface must accept both `valid_at` and `believed_at`; it must never overload one timestamp to mean both.

`facts_visible_history` needs a provenance gate, not a current-support gate: the fact must have historical evidence attached to at least one still-live document lineage. This keeps a processing-withdrawn fact visible with `evidence_count = 0`, as D54 requires, while excluding a stale fact whose only provenance disappeared through deletion. `support_state = 'support_withdrawn'` is sourced only from the reconciliation path; the view must not infer it from a zero count, because deletion-driven zero is semantically different.

D55 dedupe is principally an ingest invariant, not a predicate agents should repeat. The public layer preserves its outcome by exposing stable lineage, version, `content_hash`, and `is_current_version` separately; it never counts physical ingestion attempts as sources. D80 is similarly compiled partly into `chunks_live` policy/hash columns and partly into semantic-binding generation pinning. Neither invariant is safely recreated by giving agents raw ingestion/index tables.

#### Graph, cross-reference, changes, and K-artifact views

- `graph_edges_current(deployment_id, relation_id, from_entity_id, to_entity_id, predicate, fact_label, valid_from, valid_until, ingested_at, invalidated_at, contradiction_group, evidence_count, contradict_count, support_state, evaluated_at)`: one row per current relation, with survivor endpoints.
- `graph_edges_visible_history(deployment_id, relation_id, from_entity_id, to_entity_id, predicate, fact_label, valid_from, valid_until, ingested_at, invalidated_at, contradiction_group, support_state)`: one row per historically visible relation, for explicit bitemporal recursive queries.
- `document_crossrefs_live(deployment_id, crossref_id, from_doc_id, to_doc_id, crossref_kind, context, created_at)`: one row per cross-reference where both document lineages are live.
- `changes_visible(deployment_id, object_kind, change_kind, object_id, label, changed_at)`: one row per change whose referenced object remains externally visible. It must not become a tombstone side channel. A separate privileged deletion-audit surface, if needed, is outside the agent query role.
- `pages_live(deployment_id, artifact_id, page_kind, git_path, page_summary, last_compiled_at, status, stale, open_review_flags, redaction_required)`: one row per visible K-layer artifact; `page_summary` is orientation.
- `page_evidence_visible(deployment_id, artifact_id, role, target_kind, target_id, claim_chunk_content_hash)`: one row per page-evidence link whose target remains visible. Body access stays through corpusfs/artifact fetch, not SQL.

Media-segment views should be added only when the media schema is actually binding and implemented. Advertising imaginary columns would violate the honesty principle.

### How D48 compiles into every view

The public definition pattern is deliberately stricter than a permissive left join:

```sql
... JOIN spine.documents d
       ON d.deployment_id = x.deployment_id
      AND d.doc_id = x.doc_id
      AND d.deleted_at IS NULL
```

Version-derived rows additionally require `document_versions.deleted_at IS NULL`; current-content views require `documents.current_version_id`, the current ready representation, and matching version/representation IDs. Public views must not use an “allow missing legacy document” branch. Orphaned legacy rows are repair/quarantine material, not public memory.

Every join is composite on `(deployment_id, id)`. Aggregates begin from the live lineage relation, so deletion cannot leave a stale count. Fact and K rows that do not carry `doc_id` directly use an `EXISTS`/association join to surviving provenance. P1/P2 functions perform the same authorization after nomination. Tests must delete a lineage and exercise **every** view, helper, semantic target, graph mode, saved query, count, and artifact fetch.

### What views cannot guarantee

Views can guarantee tenant filtering, deletion filtering, current-fact clocks, lineage-distinct aggregate inputs, explicit association rows, and stable column meanings. They cannot guarantee what an arbitrary outer query chooses to say:

- `SELECT count(*) ...` changes the output grain; a view cannot truthfully assign it a platform `fact` or `evidence` grain.
- A caller can project away IDs, clocks, contradiction partners, or evidence associations.
- A gateway cap can say it stopped returning rows, but cannot know an exact total without completing a count. `LIMIT 1001` proves only “more than 1000.”
- Zero rows do not distinguish `unknown_entity`, `known_empty`, a too-restrictive predicate, a clock mismatch, or an empty deployment.
- Even a complete contradiction-members view cannot force the caller to select all members.
- A view cannot bound work in the outer query or guarantee deterministic ordering.

Therefore ad-hoc SQL must return a generic `QueryResult`, not an inferred `Envelope`:

```text
contract = exploratory_tabular/v1
deployment_id, query_hash, query_space_version, query_space_hash
referenced_relations[] and source_grain_tags[]
columns[{name, sql_type}], rows[]
returned_row_count, returned_byte_count
row_cap, byte_cap, truncated_by_gateway
exact_total_known, exact_total                 # false/null unless query computed it
ordered_result                                 # true only with a recognized outer ORDER BY
execution_started_at, pg_snapshot_time, elapsed_ms, termination_reason
p1_projection/policy/embedder generations and p2 snapshot time when used
nomination_count, post_pg_confirmation_count, stale_candidate_drop_count when used
semantic_negative = empty_result               # never promoted to unknown_entity/known_empty
grade = exploratory
```

The gateway may syntactically identify referenced public relations for provenance, but it must not claim to understand arbitrary SQL semantics. The result header reports execution facts, not a fabricated answer grain. Exact totals are available when the agent explicitly asks for a count and it completes. Typed negatives and `Envelope` grain remain available from core operations and optionally from reviewed saved queries with a declared result contract.

## 3. Graph queries

### The central conflict

Arbitrary openCypher against P2 and unconditional D48-correct results cannot both be promised. PostgreSQL can revalidate a finite set of nodes, edges, or whole paths nominated by a snapshot. It cannot repair an aggregate already computed over stale snapshot contents: a `count`, distribution, centrality score, absence test, or shortest-path choice may change when a deleted edge is removed. Hydrating the rows after the fact does not reconstruct the query.

That makes P2 unsuitable as the authoritative open graph language under the stated invariants.

### Recommended graph surface

1. `graph_edges_current` and `graph_edges_visible_history` are ordinary SQL relations. Agents can use recursive CTEs, joins, aggregation, and bitemporal predicates without a separate graph tool.
2. Ship two SQL-callable conveniences over those same PG-authorized views:
   - `graph_neighborhood_live(start_entity_id, max_hops, predicates, valid_at, believed_at, max_edges)` returning `path_id, hop, relation_id` plus all current edge columns;
   - `graph_paths_live(from_entity_id, to_entity_id, max_hops, predicates, valid_at, believed_at, max_paths)` returning `path_id, path_length, path_position, relation_id` plus edge columns.
3. Hard-cap depth, paths, edges, wall time, and bytes in both functions. Require simple-path/visited-node semantics and deterministic tie-breaking. Defaults and maxima need workload measurement; the current P2 discipline of bounded hops and shortest-path syntax is a useful floor.
4. Keep an optional `graph_snapshot_query`/openCypher tool only for candidate node/edge/path IDs. It is labeled `projection_nomination`, exposes the snapshot time/generation, forbids or marks final aggregates, and revalidates every edge in PG. If one edge is stale, a path drops as a unit and the drop is counted.

Recursive SQL is more verbose and may be slower than a graph engine. The helper functions cover common traversal without making them the only graph door. P2 remains valuable for accelerated path nomination and topology exploration, but not for an answer whose truth depends on snapshot completeness.

If the operator instead wants unrestricted authoritative openCypher, the design must either move current/deletion enforcement into a transactionally maintained graph store or explicitly weaken D48. “Run Cypher on the snapshot and hydrate whatever came back” is not an honest third option.

This combination also avoids the next unreachable spine: agents can always see and aggregate edge rows in the same discovered SQL schema, while graph helpers and P2 are accelerators rather than exclusive catalogs.

## 4. Semantic/vector search in open queries

P1 is not merely an index type that can be swapped casually. It holds chunk, claim, fact, and entity projections, multiple embedding/policy generations, filtered vector and text retrieval, and bodies that PostgreSQL intentionally does not own. Any proposal must account for consistency, authorization, body hydration, generation pinning, operational load, and migration—not just SQL syntax.

### Options

| Option | Cost and consistency | Latency/composition | Migration/operational risk |
|---|---|---|---|
| Gateway-managed semantic relations over Lance | Reuses P1; gateway nominates then PG confirms IDs. Can report exact projection/policy/embedder generation and stale drops. | One agent call can compose bounded candidates with SQL; one internal Lance round trip plus PG query. | Moderate gateway work; query parser/binder must safely expose bounded relations. Recommended. |
| Separate semantic nomination tool + SQL ID list | Simplest and clearest two-plane contract; existing behavior mostly reusable. | Two model calls, token-heavy ID lists, race between calls, more agent planning failures. | Lowest migration risk; retain as fallback and debugging interface. |
| PostgreSQL set-returning function proxying Lance | Attractive syntax such as `semantic_claims(...)`. | Potentially one SQL statement, but the DB function needs a reliable RPC/extension/FDW path and creates planner, cancellation, backpressure, and transaction-boundary surprises. | High portability and failure-domain coupling; a `SECURITY DEFINER` bridge expands the sandbox. Do not lead with it. |
| Lance FDW | In principle makes P1 tabular and pushdown-capable. | Quality depends on predicate/ANN/top-k pushdown; cross-engine plans can be unpredictable. | Requires a mature, audited FDW with generation and RLS semantics. Today this is architecture work, not configuration. |
| Migrate vector projections to pgvector | Natural joins, RLS, transactions, SQL functions, and fewer query planes. | Removes one network hop; may simplify filtering. | Large data migration and dual-write cutover; WAL/vacuum/index pressure; multiple dimensions/generations and D80 reuse rules; chunk bodies/P3 still remain. Not justified solely by agent ergonomics. |

### Recommended composition contract

`query_sql` accepts bounded named semantic bindings, for example:

```json
{
  "sql": "select f.fact_label, s.rank, f.evidence_count from memory_v1.facts_current f join :semantic_facts s on s.item_id = f.fact_id order by s.rank",
  "semantic_bindings": [{
    "name": "semantic_facts",
    "target": "facts_current",
    "query": "where did Alice live?",
    "k": 100,
    "filters": {"fact_kind": "relation"}
  }]
}
```

The gateway runs Lance first, pins a projection and D80 embedding-input/embedder generation, authorizes each candidate through the corresponding `memory_v1` view, and binds a bounded rowset to PG. A common binding has `(item_id, rank, score, channel, policy_generation, embedder_generation, p1_snapshot_at)`. A chunk binding may additionally carry bounded `chunk_text` only **after** the current document/version/representation is confirmed; it is transient query input, not a new PG source of truth. Candidate and confirmed counts are returned in the result header.

Bindings are target-specific so an agent cannot nominate a claim ID and accidentally authorize it through an unrelated entity view. Filter fields are allowlisted and documented. `k`, total bound bytes, number of bindings, and simultaneous semantic requests are capped. Failed PG confirmation drops a candidate; it never silently substitutes stale content.

This is a federated gateway feature, not benchmark logic. It should be available identically to customers and evaluations.

The pgvector fork should remain open as a separately measured storage-consolidation decision. Compare end-to-end recall/latency, index-build and update cost, multigeneration support, backup/restore, tenant isolation, operational staffing, and the P3 body architecture. “Agents like SQL” is not enough reason for a high-risk vector migration.

## 5. Fate of the recipes layer

### (a) Complete removal

Immediate deletion is technically possible only as a breaking major version. It would break or invalidate:

- the canonical twenty-recipe catalog, descriptor serialization, catalog hash, version lookup, and recipe protocol identity;
- HTTP `/recipes` and `/recipe/{name}`, CLI `remember query list/run`, SDK `recipes()`/`run_recipe()`, MCP recipe tools, mounts/help text, and published API/CLI/MCP documentation;
- agents and customers whose prompts or integrations name current recipes or deserialize an `Envelope`;
- all consumers that rely on `Envelope` fields such as `grain`, typed negatives, exact associations, freshness, truncation, stale-candidate drops, sources, paths, and evidence budgets;
- `RecipeSurface`, `RecipeExecutor`, stock recipe definitions, static operation vocabulary, and recipe linter assumptions;
- benchmark v9's pinned catalog hash, tool schemas, prompt, traces, and comparability; removing recipes does not retroactively turn a v9 score into an open-query score;
- operational dashboards, telemetry, runbooks, examples, consumption skills, and support expectations keyed by recipe name/version;
- the one-call retrieval path that current agents overwhelmingly choose, before v10 proves an adequate replacement.

The code can be removed eventually, but a “clean architecture” benefit does not justify an abrupt protocol break.

### (b) Per-customer recipes as saved queries

This is valuable if it is treated as a saved-query registry, not as customer-authored code that inherits platform truth claims.

Each immutable saved-query version should have at least:

`deployment_id, query_id, version, name, description, sql, parameter_schema, declared_result_schema, declared_grain, query_space_version, query_space_hash, default_limits, status, owner, created_at, superseded_at, validation_report, query_hash`.

Parameters are typed and bound, never string-interpolated. Saving validates syntax, public-relation/function use, declared columns, and current sandbox limits. Execution still uses the live gateway; a saved query cannot bypass RLS, D48 views, timeouts, caps, or provenance headers. Registry search and `run_saved_query(name/version, params)` avoid rendering hundreds of custom queries as top-level model tools—the same catalog-selection failure in tenant clothing.

Correctness ownership is layered:

- RememberStack owns tenant/deletion isolation, view semantics, allowed operations, execution caps, and truthful execution provenance.
- The author owns the business meaning of filters, joins, aggregation, labels, and declared interpretation.
- By default, output remains `exploratory_tabular`, even if the author calls it a fact. Customers may aggregate claims and call the rows “accounts”; the engine should not police every domain ontology, but it must not attach a platform `fact`/`evidence` grain or typed negative to that output.
- An optional `reviewed_contract` status may map a saved query into a typed application result after static review, test fixtures, cap/negative rules, and explicit ownership. It still should not be confused with a stock engine invariant unless the platform certifies it.

Thus a customer query can be semantically dishonest, but it cannot defeat platform deletion/tenancy controls, and the platform does not endorse its grain. That is the defensible boundary.

### (c) Thin shipped core plus customer space

Initially keep:

- `resolve_entity`: uniform survivor resolution, ambiguity boundary/candidates, and `unknown_entity` semantics;
- `question_context`: one-call high-recall, bounded evidence retrieval with `Envelope` honesty for agents that do not successfully plan a query.

Keep `query_sql`, schema discovery, semantic bindings, bounded graph helpers, and artifact fetch as infrastructure operations, not recipes. `question_context` may internally use the same public view/function primitives, but it remains a maintained product contract. Do not keep a “tiny” collection of five or ten additional intent recipes without measured evidence; that is the path back to accretion.

### Ranked recommendation for recipe fate

1. **Thin core + open query space + versioned customer saved queries.** Best balance of expressivity, migration safety, common-intent performance, and semantic honesty.
2. **Complete removal after a measured deprecation window.** A plausible later endpoint if v10 and customer telemetry show that `question_context`/intent wrappers add no value. Do not promise it now.
3. **Continue or expand the shipped canonical menu.** Lowest migration cost but preserves the arbitrary coverage lag and poor agent uptake; use only as a temporary compatibility tier.

## 6. Security and tenancy

SQL injection is not the primary threat when the agent intentionally authors SQL. The relevant threats are tenant boundary failure, privilege escalation through SQL features/functions, resource exhaustion, extraction of the entire authorized deployment, and downstream exfiltration by a prompt-injected agent.

### Cross-deployment isolation

D68 physical schema/database routing stays primary. The gateway authenticates a principal already bound to exactly one deployment before choosing a connection pool. It does not accept `deployment_id` from SQL as routing authority.

Defense in depth should add RLS to every physical table reachable under a view. PostgreSQL table owners normally bypass RLS, while superusers and `BYPASSRLS` roles always do; table owners can be forced under it with `FORCE ROW LEVEL SECURITY` ([PostgreSQL RLS documentation](https://www.postgresql.org/docs/16/ddl-rowsecurity.html)). A safe role split is:

- no-login migration/table owner;
- no-login view owner, not superuser/BYPASSRLS and subject to forced RLS;
- per-deployment or protected-principal query login, with only `USAGE` and `SELECT` on `memory_v1` plus allowlisted functions;
- gateway/admin roles outside the agent pool.

Tenant identity should derive from the authenticated session/physical database or a protected `session_user` mapping, not from a client-writable custom GUC. All policies fail closed, all base tables use `ENABLE` plus `FORCE ROW LEVEL SECURITY`, and cross-deployment tests run under the actual query role. View-owner and invoker semantics must be chosen deliberately: PostgreSQL defaults underlying access and RLS to the view owner, while `security_invoker` changes this; `security_barrier` is intended for views providing row-level security ([PostgreSQL `CREATE VIEW`](https://www.postgresql.org/docs/16/sql-createview.html)). The design must specify and test one model rather than assume “view + RLS” is self-explanatory.

The query role receives no base-schema `USAGE`, no direct raw-table grants, no role membership that can reach another deployment, and no outbound network/file capability. Revoke default `PUBLIC` privileges and schema/database creation privileges. The gateway must reapply role, `search_path`, timeouts, and transaction state on every pooled checkout.

### Resource abuse

Controls should exist at several levels:

- per-statement wall and lock timeout;
- hard result row and byte caps, plus maximum semantic `k`, graph depth/edges/paths, recursive work, and SQL text/parameter sizes;
- constrained memory/temp-spill budgets and no parallelism setting that lets one tenant dominate the server;
- per-principal and per-deployment concurrency queues, rolling CPU/query quotas, rate limits, and cancellation;
- cost/plan inspection as an early rejection heuristic, never the only control;
- metering of attempts, errors, retries, rows scanned where available, rows/bytes returned, semantic and graph work, and termination reason.

Exact initial numbers should come from representative workloads. A design may set conservative defaults (for example a low-seconds interactive timeout and hundreds rather than millions of returned rows) plus separately authorized analytical tiers; it should not bake arbitrary values from this analysis.

Audit logs should retain query and contract hashes, identity, timing, limits, and failure reason. Raw SQL and parameters may themselves contain customer data, so content logging needs retention, access, and redaction rules rather than an unconditional debug log.

### Prompt-injection exfiltration within one deployment

RememberStack can limit the blast radius: least-privilege views, output/rate caps, audit, sensitive-view entitlements, separate deployments for distinct trust boundaries, and no network egress from the database role. It cannot reliably distinguish “the user wants a complete export” from “an injected document told the agent to export” when both are syntactically authorized queries.

Intent mediation, trusted/untrusted prompt separation, tool approval, destination controls, and egress policy belong primarily to the agent harness. The engine should expose enough provenance and sensitivity metadata for that harness to decide. If two populations inside one deployment must not be mutually visible, that is not merely prompt injection; it is an authorization boundary and requires separate deployment/role policy. The current deployment-wide trust-domain premise must remain explicit.

## 7. Migration and benchmark protocol

### Deprecation path

1. Ship `memory_v1`, discovery, SQL, semantic bindings, and new `QueryResult` alongside the full recipe catalog. Generate the old recipes from or cross-test them against the same invariant-bearing primitives where practical.
2. Run a v10 A/B evaluation: current twenty recipes, open-only, and hybrid thin-core. Instrument schema discovery, SQL retries/errors, calls, latency, timeouts, stale drops, token use, and answer accuracy.
3. Add saved-query import/export and offer stock-recipe-to-saved-query examples. Do not automatically certify converted ad-hoc output as an `Envelope`.
4. Mark the eighteen noncore shipped intent recipes deprecated after the hybrid meets explicit quality/safety/latency gates. Keep HTTP/CLI/SDK/MCP adapters for a published window, with versioned warnings and migration docs.
5. Remove the catalog and linter only in a major protocol/API version. Keep a compatibility service longer if contracted customers require it. Later evaluate whether the two core operations still earn their cost.

Existing `Envelope` consumers have three paths: keep using the two core envelopes; adopt `QueryResult` and handle tabular provenance/caps explicitly; or call a reviewed saved query with a declared application schema. A generic adapter must not infer `Envelope.grain`, typed negatives, evidence associations, or exact totals from arbitrary SQL.

### What replaces the catalog hash

The catalog hash was useful reproducibility machinery, not an argument for recipes. v10 should pin a protocol manifest containing independent hashes for:

- tool names, input/output schemas, and compact prompt resource;
- `memory_v1` view/function contract: names, ordered columns, types, comments, definitions, grain tags, and semantic version;
- sandbox policy and hard limits;
- semantic target/binding contract plus P1 projection, embedding-input policy, and embedder generations;
- graph helper/openCypher candidate contract and P2 snapshot generation when used;
- core-operation versions;
- any saved-query IDs, immutable versions, SQL hashes, declared schemas, and owners used by that run.

Do not hash physical indexes or query plans as part of the public contract; those may change without semantic change. Conversely, changing a deletion predicate while retaining the same columns is a contract change and must change the query-space hash.

### A v10 benchmark protocol

A suitable identity is something like `RS-LoCoMo-OpenQuery-v10`, explicitly noncomparable to v9. It gives the evaluated agent the same discovery, SQL, semantic, graph, artifact, and thin-core operations available to customers. There is still no benchmark-specific recipe, column, prompt hint, or branching behavior in product.

The protocol pins conversation/deployment construction, query-space/tool/sandbox hashes, model and budgets, semantic/graph generations, and time anchors. It records all calls, SQL and bound parameter hashes (with controlled raw trace retention), discovery use, parser/planner errors, timeouts, cap events, retry repairs, candidate confirmations/drops, and returned provenance. Alongside answer score, report resource cost, latency, invalid-query rate, cross-tenant/deletion adversarial tests, and reliance on the core fallback.

The decisive comparison is not merely recipe versus SQL answer accuracy. It is whether the hybrid improves coverage of aggregation/structured questions without raising wrong-clock, wrong-grain, omission, timeout, or exfiltration failures beyond agreed thresholds.

## 8. Prior art and recent tool-use practice

The closest memory products mostly expose curated semantic search, filters, and graph-search presets rather than raw data languages:

- [Graphiti search](https://help.getzep.com/graphiti/working-with-data/searching) combines semantic/BM25/graph methods and documents a lower-level search configuration with predefined recipes. Its [official MCP server](https://github.com/getzep/graphiti/blob/main/mcp_server/README.md) exposes named node/fact/episode operations rather than arbitrary Cypher.
- [Mem0 v2 filters](https://docs.mem0.ai/platform/features/v2-memory-filters) provide a structured metadata/logical filter language around memory search, not relational SQL.
- [Letta's agent tools](https://docs.letta.com/guides/get-started/for-agents) expose bounded archival and conversation search; its [passage search API](https://docs.letta.com/api/python/resources/passages/methods/search/) is a parameterized search endpoint.
- [LangGraph store semantic search](https://langchain-ai.github.io/langgraph/cloud/deployment/semantic_search/) is namespace/query/filter/limit oriented, and [LangMem search tools](https://langchain-ai.github.io/langmem/reference/tools/) wrap that store interface for agents.

This is evidence that curated semantic retrieval remains useful, especially for one-call context. It is not evidence that the RememberStack recipe catalog is sufficient: those surfaces also do not solve the requested arbitrary aggregation and bitemporal joins.

At the other end, general analytical agent tools do expose SQL. The archived [Model Context Protocol PostgreSQL reference server](https://github.com/modelcontextprotocol/servers-archived/tree/main/src/postgres) offered a read-only query tool and schema resources, illustrating the simple generic pattern; its archival and reference status also warn against mistaking a sample role wrapper for a production sandbox. [MotherDuck's MCP server](https://motherduck.com/product/mcp-server/) pairs catalog discovery and SQL execution with sandboxed analytical compute and query traceability.

The useful lesson is hybrid, not ideological. Models benefit from a small number of orthogonal, composable tools with strong descriptions and visible schemas. Open SQL is particularly strong once the question asks for a grouping or join that a curated menu did not anticipate. Curated domain operations remain strong when they package difficult intent semantics—resolution, typed negatives, evidence budgeting, and multi-channel context—in one call. MCP itself changes transport and discovery, not the underlying correctness boundary.

There is no primary-source prior art here that proves arbitrary open SQL dominates a good memory retrieval primitive on conversational QA. RememberStack should treat its richer clocks, lineage rules, and federated stores as a harder case than generic warehouse analytics and measure the hybrid directly.

## Overall ranked recommendation

1. **Versioned PG view schema + sandboxed SQL + thin core + customer saved queries + gateway semantic bindings.** Make PG edge views and recursive SQL authoritative; P2 remains a hydrated candidate accelerator. This is the recommended v10 target.
2. **The same open view schema with complete recipe removal.** Simpler eventual surface, but only after v10 shows no material loss from removing the one-call fallback and after API/customer migration.
3. **Open SQL plus the full legacy recipe catalog indefinitely.** Safe near-term transition, but long-term tool clutter and dual semantics make it a poor destination.
4. **Raw tables/RLS or unrestricted authoritative snapshot Cypher.** Maximum apparent openness, minimum invariant safety. Reject under the mandate.

## What the design document must bind

The binding design cannot leave these choices implicit:

1. The exact public schema name/version and the exhaustive views, columns, SQL types, row grains, keys, clock/null semantics, comments, and compatibility rules.
2. The precise D48 definition template for every lineage path, plus a coverage test matrix spanning views, helpers, P1 bindings, P2, saved queries, counts, K artifacts, and corpusfs bodies. It must decide how legacy/orphan rows are quarantined.
3. The D41 time predicate, shared evaluation instant, valid/until boundary convention, and any bitemporal as-of API.
4. The D54 provenance gate, lineage-distinct support/contradiction calculation, and authoritative source of `support_withdrawn`; zero-count inference must be forbidden.
5. The allowed SQL grammar, PostgreSQL version/features, function/operator allowlist, parameter model, safe `EXPLAIN`, deterministic-order disclosure, and exact rejection/error taxonomy.
6. The PostgreSQL ownership, `security_invoker`/view-owner choice, `security_barrier`, forced-RLS policy, protected deployment binding, grants, `search_path`, pool reset, and adversarial tenant tests.
7. Default and hard limits for time, locks, rows, bytes, memory/temp work, SQL/parameters, concurrency, recursive graph work, semantic bindings, quotas, cancellation, and analytical-tier escalation.
8. The complete `QueryResult` provenance header and what `exploratory_tabular` explicitly does **not** guarantee: platform grain, typed intent negatives, contradiction completeness, fact-evidence completeness, or exact totals.
9. The schema discovery manifest, first-call prompt/resource policy, examples, comment quality standard, and hashing/canonicalization algorithm.
10. The graph authority split: PG recursive edges/helpers versus P2 candidate Cypher; exact supported Cypher subset, forbidden/final aggregate behavior, path-unit validation, caps, freshness, and drop disclosure.
11. The semantic-binding protocol: targets, columns, Lance filters, generation pinning, D80 fields, chunk-body hydration, PG confirmation transaction, race behavior, caps, failure modes, and stale-drop accounting.
12. Whether pgvector is explicitly deferred or separately evaluated, with decision criteria and a dual-write/cutover plan if chosen. It cannot remain a vague “future simplification.”
13. The saved-query registry schema, immutable versioning, validation, ownership, discovery, parameters, result grading, promotion/review process, quotas, and deletion behavior.
14. Which shipped core operations survive, their versions and output contracts, and an anti-accretion rule requiring measured evidence to add another platform intent operation.
15. The API/SDK/CLI/MCP compatibility and deprecation schedule, adapter limitations, customer communication, and terminal major-version removal criteria.
16. The v10 protocol manifest and hashes, evaluation arms, quality/safety/resource gates, trace policy, noncomparability statement, and the absolute prohibition on benchmark-only product behavior.
17. The division of responsibility for in-deployment data extraction: engine controls and audit versus harness intent/egress controls, including when a new deployment/trust boundary is mandatory.
18. Observability, query-content retention/redaction, cost attribution, incident response, and fail-closed behavior when PG, Lance, P2, or corpusfs disagree or are unavailable.

Until those are bound and tested, “let the agent run SQL” is a direction, not a safe product contract.
