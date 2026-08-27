# Open query space — binding design

> **Binding D98 amendment (2026-08-27).** Public Cypher and the immutable P2
> graph surface are removed. `query_cypher` and `explain_cypher`, Cypher
> grammar/limits/errors, `snapshot_graph`, `p2_snapshot`, generation discovery,
> and P2 confirmation are not public or internal contracts. Server-owned,
> parameterized PostgreSQL 19 SQL/PGQ serves fixed one-hop shapes, while the
> replacement public bounded `graph_neighborhood`/`graph_path`/
> `graph_citation_path` SQL functions and
> typed graph operations use recursive SQL for variable/shortest traversal.
> Public arbitrary SQL/PGQ is outside the current surface until the
> default-deny AST gate can parse PostgreSQL 19. All plain-SQL, invariant
> compilation, tenancy, saved-query,
> discovery, result-cap, audit, and assured-operation rules below remain binding.
> The current graph contract is [`p2_graph_design.md`](p2_graph_design.md).

*2026-08-04; context-operation amendment 2026-08-10; PostgreSQL-native P1
amendment 2026-08-14 (D94, implementation pending). Binding once accepted.
Replaces the growing default recipe catalog with a versioned,
invariant-compiled PostgreSQL query space, typed live-graph helpers, and four
platform-assured operations aligned to identity, testimony,
facts, and their explicit composition. Rationale:
`plan/analysis/open_query_space_codex.md` and
`plan/analysis/open_query_space_grok.md`, with the pre-release cut analyzed in
`plan/analysis/open_query_space_clean_cutover.md` and the context model analyzed
in `plan/analysis/context_operation_model_analysis.md`. This design supersedes
the public catalog bound by `agent_retrieval_surface_design.md`; D41, D48, D49,
D54, D80, and D87 remain controlling.*

**Bound two-layer retrieval headline (reused verbatim):**

> RememberStack has two deliberately separate truth layers. Claims are
> immutable source testimony (“what was asserted, by whom, when”);
> facts—relations and observations—are the adjudicated worldview (“what the
> system holds or held true”): supersession-adjudicated, clocked on two
> time axes (when a fact held in the world, and when the system learned it),
> evidence-counted per distinct source—repetition is not corroboration—and
> contradiction-tracked. The `fact_claim_evidence` association is the auditable
> bridge between the layers, recording which claims support or contradict each
> fact. Query claims to inspect testimony; query facts to answer current or
> historical truth questions, then follow the bridge to see why the system
> believes or believed the fact.
>
> (Internally these guarantees are decisions D41 and D54.)

## 1. Principles (binding)

1. **PostgreSQL authorizes every live result.** The live public data language IS
   PostgreSQL 19 SQL over the versioned `memory_v1` schema. Physical tables,
   raw projection tables, and operator schemas are never public. PostgreSQL
   views compile row-level invariants; P1 ranked scans, SQL/PGQ fixed patterns,
   and recursive graph helpers apply those authorities in the same transaction.
2. **The assured surface mirrors the authority layers.** `resolve_entity`,
   `testimony_context`, `fact_context`, and `answer_context` are the complete
   shipped platform-operation set. `testimony_context` returns source testimony,
   `fact_context` returns temporally selected adjudicated facts, and
   `answer_context` composes their complete responses without flattening the two
   grains. SQL execution, discovery, saved-query execution, and the
   allowlisted SQL functions are query infrastructure, not additional intent
   operations.
3. **Open computation is graded by contract.** Every ad-hoc or saved SQL
   execution returns `QueryResult/v1` with grade `exploratory_tabular`. A SQL
   view's source grain never becomes a claim about an arbitrary outer query's
   result grain. Typed graph operations return their own closed live-result
   envelope rather than pretending that an arbitrary tabular result is assured.
4. **D41 is compiled, not taught.** A relation named `current` applies world
   validity and transaction validity at one disclosed evaluation instant.
   Claim validity remains immutable source testimony and never answers what
   currently holds.
5. **D48 is fail-closed at each stated time boundary.** Each live
   lineage-derived row has a surviving live-document provenance path. P1
   nominations receive same-statement authority joins; SQL/PGQ patterns and
   recursive traversal expand only invariant-bearing live edge views; graph
   paths fail as units; chunk bodies confirm their current coordinate and hashes.
   Missing, orphaned, mismatched, or incompletely forgotten state is absent from
   live output or fails the call.
6. **D54 counting has one meaning.** `evidence_count` and
   `contradict_count` count distinct current-testimony document lineages per
   `supports` or `contradicts` stance. `support_state` is exactly `current` or
   `withdrawn`; `withdrawn` comes only from the open processing-driven
   `support_withdrawn` review state. A zero count MUST NOT manufacture it.
7. **The graph is first-class and live.** A PostgreSQL property graph over
   deployment-keyed invariant views is the fixed-pattern substrate. Server-owned
   SQL/PGQ implements the admitted one-hop shape. Public bounded
   `graph_path` and `graph_neighborhood` functions, plus typed graph operations,
   use work-bounded recursive SQL from depth two and for shortest traversal.
   There is no copied graph, snapshot generation, public Cypher, or public
   arbitrary SQL/PGQ on the accepted surface.
8. **PostgreSQL-native P1 SQL preserves D80 and D48.** `semantic_claims`,
   `semantic_chunks`, `semantic_facts`, `semantic_entities`,
   `lexical_claims`, `lexical_chunks`, and `fetch_chunk_bodies` validate the one
   ready configured embedding-input-policy/embedder state per invocation.
   Ranked P1 scans join target-specific authority
   views in the same PostgreSQL statement before exposing rows or bytes.
   `lexical_facts` is
   absent until the §10 P1 indexing trigger is met.
9. **Customer semantics stay customer-owned.** Ad-hoc and saved SQL inherit the
   platform sandbox, tenancy, time boundary, limits, and execution provenance.
   Their filters, aggregates, labels, and interpretations are not
   platform-endorsed fact semantics. Shipped examples live under the same rule.
10. **Bounds are part of the public contract.** SQL work and results, recursive
    traversal, P1 ranking/body fetch, concurrency, and retained
    telemetry have defaults and hard caps in §4. No cap is silent.
11. **The schema is discoverable before use.** The same checked-in manifest
    owns SQL view/function comments, grains, keys, examples, graph-helper
    contracts, and `surface_manifest_hash`. Raw `pg_catalog` discovery is not
    exposed. The internal property-graph definition is verified separately from
    PostgreSQL's semantic catalogs and is not an open query surface.
12. **No benchmark-specific product behavior exists.** The separately
    fingerprinted v14 protocol consumes the customer surface unchanged.
    Dataset names, question classes,
    benchmark-only views, prompts, functions, branches, or limits are forbidden
    in product code.
13. **Accretion requires evidence.** D87 admits `answer_context` as one narrow
    exception because it is a typed transport bundle of two complete existing
    operations and adds no retrieval, ranking, hydration, or transformation.
    That exception does not generalize: a fifth assured operation, another
    bundle, or any composition that changes a child can be proposed only when
    all of these gates pass:
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
14. **The caller owns planning.** There is no product NL-to-SQL/PGQ planner
    or LLM on the query path. Raw-table SQL, DuckDB/exports as a live
    correctness path, and content-level ACL emulation inside one deployment are
    non-goals.

## 2. Naming alignment with the existing corpus

The shipping intent surface contains `resolve_entity`, `testimony_context`,
`fact_context`, and `answer_context`. The first is retained; the other three
replace the mixed-grain `question_context` and current-only `current_context`
contracts. There are no compatibility aliases.

The 17 previously demoted recipe names remain discoverable, non-default saved
queries in the `examples` namespace. D98 adds the new
`examples.graph_citation_path`, for 18 shipped examples total. An `examples.*` query returns
`QueryResult/v1`, carries customer-space semantics, is editable only by copying
it to a customer namespace, and is not a platform contract or a top-level MCP
tool. The two removed context names do not become saved-query tools:
`question_context` decomposes into testimony plus facts, and the existing
current-fact examples already cover `current_context`'s SQL pattern.

| Existing recipe | Target disposition | Example implementation after demotion |
|---|---|---|
| `resolve_entity` | **Retained platform operation** | Full D49 `Envelope`; no saved-query substitute |
| `testimony_context` | **Platform operation** | Full D49 evidence `Envelope`; claims and source passages only |
| `fact_context` | **Platform operation** | Full D49 fact `Envelope`; current, valid-at, overlap, or historical facts with both time axes and explicit evidence associations |
| `answer_context` | **Platform composition** | `ContextBundle/v1` containing the complete testimony and fact envelopes |
| `relation_current` | `examples.relation_current` | Filter `facts_current` to `fact_kind = 'relation'` |
| `observation_current` | `examples.observation_current` | Filter `facts_current` to `fact_kind = 'observation'` |
| `entity_timeline` | `examples.entity_timeline` | Group `facts_visible_history` by disclosed time bucket |
| `claims_verbatim` | `examples.claims_verbatim` | `semantic_claims` joined to `claims_live` |
| `claims_hybrid_rrf` | `examples.claims_hybrid_rrf` | `semantic_claims` + `lexical_claims` with documented SQL RRF; no parity claim with the former recipe implementation |
| `chunks_hybrid_rrf` | `examples.chunks_hybrid_rrf` | `semantic_chunks` + `lexical_chunks` with documented SQL RRF; no parity claim with the former recipe implementation |
| `question_context` | **Removed** | Replaced by `testimony_context`; facts and entities are no longer optional flags on testimony retrieval |
| `documents_about` | `examples.documents_about` | `entity_document_mentions` joined to `documents_live` |
| `claims_about` | `examples.claims_about` | `mentions_live` joined through `claim_occurrences_live` to `claims_live` |
| `claims_as_of` | `examples.claims_as_of` | Inclusive claim-evidence overlap over `claims_visible_history`; unknown precision excluded and counted |
| `chunk_neighbors` | `examples.chunk_neighbors` | Current-section ordinal neighbors from `chunks_live`; bodies use the confirmed body-fetch path |
| `current_context` | **Removed** | Replaced by the default-current mode of `fact_context` |
| `explain` | `examples.explain` | `facts_visible_history`, `fact_claim_evidence_live`, `evidence_lineage`, and sources |
| `identity_as_of` | `examples.identity_as_of` | Bounded `identity_events_visible` transcript; interpretation remains customer-owned |
| `changed_since` | `examples.changed_since` | Bounded `changes_visible` query |
| `pages_about` | `examples.pages_about` | `pages_live` joined to `page_evidence_visible` |
| `multi_hop_context` | `examples.multi_hop_context` | `graph_path`/`graph_neighborhood` plus semantic/lexical SRFs and explicit joins |
| `graph_neighborhood` | `examples.graph_neighborhood` | Direct call to `graph_neighborhood` |
| `graph_path` | `examples.graph_path` | Direct call to `graph_path` |
| — | `examples.graph_citation_path` | Direct call to directed `graph_citation_path`; new under D98, not a compatibility alias |

The examples preserve familiar discovery names, not the old recipe behavior or
`Envelope` shapes. No compatibility promise is made: the pre-release library
has no consumers to migrate, so the duplicate adapters are removed by §8.

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
| `describe_query_space(pattern?, include_examples=false)` | Manifest-backed exact schema, functions, comments, examples, versions, hashes, and limits |
| `search_query_space(query, k=10)` | Search over checked-in manifest text only; `k` range 1–25 |
| `list_saved_queries(namespace?, status?)` | Registry metadata only |
| `describe_saved_query(namespace, name, version?)` | Immutable version, parameters, declared columns, validation state, and hashes |
| `run_saved_query(namespace, name, version, parameters)` | Same executor and `QueryResult/v1` as `query_sql` |

The public SRFs are reached only as allowlisted SQL calls through `query_sql`,
`explain_sql`, or saved SQL; none is a platform intent operation or a top-level
intent tool:

| SRF family | Members |
|---|---|
| Bitemporal facts | `facts_as_of` |
| Semantic P1 nomination | `semantic_claims`, `semantic_chunks`, `semantic_facts`, `semantic_entities` |
| Lexical P1 nomination | `lexical_claims`, `lexical_chunks`; `lexical_facts` is the explicit §10 deferral |
| Confirmed body fetch | `fetch_chunk_bodies` |
| Live PG graph | `graph_neighborhood`, `graph_path`, `graph_citation_path` |

The four assured operations are pinned in the manifest as:

| Operation | Version | Contract |
|---|---:|---|
| `resolve_entity` | 1 | D49 fact `Envelope`; ranked survivor candidates, `unknown_entity`/`boundary`, current identity regime |
| `testimony_context` | 1 | D49 evidence `Envelope`; bounded hybrid claims and current source passages, never facts or entity candidates |
| `fact_context` | 1 | D49 fact `Envelope`; semantically nominated relations and observations under an explicit world-time scope, with both evidence stances and exact associations/totals |
| `answer_context` | 1 | `ContextBundle/v1`; the complete `testimony_context` and `fact_context` responses in separately named fields, never one mixed result list |

The exact public input schemas are below. Their numeric defaults and hard caps
are the starting contract carried from the measured predecessor paths; changing
one after measurement requires an operation-version and manifest roll.

| Operation | Inputs |
|---|---|
| `resolve_entity` | Existing v1 schema, unchanged |
| `testimony_context` | `query`: required string, length 1–8,192; `entity_ids`: optional array of 1–20 unique UUIDs; `k`: integer 1–100, default 50; `candidate_k`: integer 1–400, default 200 |
| `fact_context` | `query`: required string, length 1–8,192; `entity_ids`: optional array of 1–20 unique UUIDs; `k`: integer 1–30, default 15; `evidence_per_fact`: integer 1–5, default 3; `time`: the closed union below, default `{"mode":"current"}` |
| `answer_context` | `query`: required string, length 1–8,192; `entity_ids`: optional array of 1–20 unique UUIDs; `time`: the same closed fact-time union, default `{"mode":"current"}` |

All objects forbid unknown fields. A present `entity_ids` array cannot be empty
or contain duplicates, and `testimony_context.candidate_k` cannot be smaller
than its `k`. Malformed UUIDs, unknown fields, invalid bounds, an invalid time
shape, or `time.to < time.from` for overlap mode fail `invalid_parameter`
before retrieval. PostgreSQL then confirms every supplied ID as a current
survivor in the deployment. If any ID
is absent, retired, forgotten, or belongs to another deployment, the operation
returns no results and a D49 `unknown_entity` negative whose explanation does
not disclose which case occurred and whose workaround says to call
`resolve_entity` and retry. It never drops only the bad IDs or silently
re-resolves names.

`testimony_context` independently fuses semantic and lexical claim nominations
and semantic and lexical current-source chunk nominations, then confirms each
final list through PostgreSQL. It returns only `evidence[]` and `chunks[]`.
`candidate_k` applies to each of the four nomination channels and `k` applies
separately to the final claim and chunk lists; the maximum payload is therefore
`k` claims plus `k` chunks, never an ambiguously shared cut.
Omitting IDs preserves deployment-wide semantic retrieval. The operation never
returns entity candidates.

`fact_context` uses the discriminated `time` object below. Each variant forbids
fields belonging to another variant:

| Mode | Additional fields | World-time membership; system belief |
|---|---|---|
| `current` | none | Window covers the operation's disclosed evaluation instant; current system belief |
| `at` | `at: timestamptz` | Window covers `at`; current system belief |
| `overlap` | `from: timestamptz`, `to: timestamptz` with `to >= from` | Window overlaps the inclusive request bounds; current system belief |
| `history` | none | Every interval that began on or before the disclosed evaluation instant and the system still believes, whether ended or open; future-starting and retracted facts are absent |

For `at`, the half-open fact window applies
`valid_from IS NULL OR valid_from <= at` and
`valid_until IS NULL OR valid_until > at`. For `overlap`, membership is
`valid_from IS NULL OR valid_from <= to` and
`valid_until IS NULL OR valid_until > from`. Every non-current mode also
requires `ingested_at <= evaluated_at AND invalidated_at IS NULL` at the
operation's disclosed evaluation instant; an ended `valid_until` is history,
while an `invalidated_at` value is a retracted belief. D48
survivor/provenance conditions apply in every mode.
`history` additionally applies `valid_from IS NULL OR valid_from <=
evaluated_at`; callers use `at` or `overlap` explicitly for future world-time
windows rather than receiving scheduled facts in an operation named history.

Every `fact_context` result envelope carries a required `temporal_scope` block.
Its result schema is the following closed union; unknown fields are forbidden:

```text
{mode: "current", evaluated_at, believed_at, identity_regime}
{mode: "at", at, evaluated_at, believed_at, identity_regime}
{mode: "overlap", from, to, evaluated_at, believed_at, identity_regime}
{mode: "history", evaluated_at, believed_at, identity_regime}
```

All timestamps are `timestamptz`. `identity_regime` is the D49 value `current`
for these operations. `evaluated_at` is the shared statement/evaluation cut;
because these modes ask what the system believes now, `believed_at` is that
same instant. `at` or `from`/`to` echo the request after validation. The mode
and its required fields are never inferred from null timestamps.

These modes deliberately do not overload the separate audit question “what
did the system believe at T?” That two-axis reconstruction remains the
`facts_as_of(valid_at, believed_at, ...)`/open-SQL contract. Every returned fact
still discloses `valid_from`, `valid_until`, `ingested_at`, and
`invalidated_at`. All four modes confirm against `facts_visible_history` with
the predicates above, the current-system-belief predicate, and the same D48
survivor/provenance conditions. For `current`, this is the parameterized form
of `facts_current` membership at the envelope's exact disclosed evaluation
instant; the non-parameterized view would choose a new statement timestamp.
They
do not call the current-only public `semantic_facts` SRF and do not reuse
`facts_as_of` accidentally. The operation returns supporting and contradicting
current testimony, explicit `fact_evidence[]`, exact `evidence_totals[]`,
contradiction co-members, support state, and the same 60-record evidence budget
for every mode.

Fact identity is always the composite `(fact_kind, fact_id)`: relation and
observation UUID namespaces can overlap. Every `fact_evidence[]` association
and `evidence_totals[]` row therefore carries both `fact_kind` and `fact_id`,
and implementations must use both coordinates for partitioning, allocation,
counting, and lookup. Collapsing either structure by bare UUID is forbidden.

#### Eligibility precedes bounded ranking

For every testimony target and fact-time mode, scope is a nomination input,
not a hydration-only filter. Candidate depth is applied *within* the eligible
set:

- testimony claims and chunks are eligible when their current, confirmed
  mention/occurrence associations contain any supplied survivor ID;
- observations are eligible when their subject is anchored; relations are
  eligible when either endpoint is anchored; and
- fact world-time membership is the selected mode above.

The P1 projections therefore carry rebuildable nomination metadata for every
selector they apply directly. A backend may instead have PostgreSQL enumerate
the exact eligible composite IDs and ask P1 to score only those IDs; selectors
may choose either authority independently. The shipping self-host path stores
fact kind, `valid_from`, `valid_until`, `ingested_at`, and active/invalidated
status in P1 for time prefiltering, while supplied survivor IDs and multi-anchor
coverage come from exact PostgreSQL-selected fact IDs. Testimony entity scope
uses the same PostgreSQL-ID pattern. This deliberately avoids duplicating
entity associations into P1 when they are not used there. In every case, a
globally bounded P1 result followed by entity or time filtering is forbidden.
P1 metadata remains a proposal: live PostgreSQL repeats every selector before
returning data, so stale metadata can cost disclosed recall but never
correctness. `testimony_context` applies its public `candidate_k` inside this
scope. `fact_context` uses a descriptor-pinned internal candidate depth of 200
with a hard ceiling of 400; it is not another public planning knob.

Ending a fact's world-valid interval does not delete its label from P1: an
ended-but-still-believed fact remains searchable for `at`, `overlap`, and
`history`. Invalidation updates the proposal status so ordinary nomination can
avoid it, while PostgreSQL still makes the authoritative decision. D74 hard
forget purges the row rather than relying on that status filter.

Coverage count is also a pre-depth ranking key, not merely a final reranker:
the scoped nomination orders higher coverage before relevance and applies
candidate depth only afterward. PostgreSQL recomputes coverage during
confirmation; final ordering is confirmed coverage, retrieval score, then
stable composite ID. A claim or chunk mentioning both anchors therefore
ranks ahead of an otherwise comparable one-anchor result; a direct A–B
relation ranks ahead of an otherwise comparable A-only fact. Association with
any supplied ID remains sufficient—there is no public any/all switch. With no
IDs, deployment-wide nomination is unchanged.

`answer_context` passes the confirmed IDs unchanged to both children and the
time selector to `fact_context`. It deliberately exposes no child depth knobs:
`testimony_context` runs with `k=50, candidate_k=200`; `fact_context` runs with
`k=15, evidence_per_fact=3`. Call a single-layer operation directly to tune its
depth. The exact response is:

```text
ContextBundleV1 {
  contract:  literal("ContextBundle/v1")
  testimony: Envelope  // evidence grain; complete testimony_context result
  facts:     Envelope  // fact grain; complete fact_context result
}
```

The wrapper forbids extra fields. D87 refines D49: `Envelope.parts` and
`EnvelopePart` are removed, and this is the sole envelope-of-envelopes contract.
The `composite` grain may still describe one operation's cohesive typed result;
it cannot contain whole child responses. The bundle does not claim one atomic
cross-projection snapshot: each child retains its own freshness, truncation,
negative, drop disclosure, and `temporal_scope`. A typed child negative is a
complete child response and remains in the bundle. If entity validation fails, both children
contain the same opaque `unknown_entity` result. A schema, timeout, cancellation,
or store/execution failure in either child fails the entire request with no
half-bundle.

The bundle layer adds no nomination, ranking, hydration, or entity-resolution
implementation and does not rebuild either response. Under a frozen store,
active embedding configuration, and evaluation clock, each member is field-for-field
equal to its direct child call; no child field is exempt or regenerated at the
bundle layer. This pure composition is the only reason the fourth operation
exists and is the sole D87 exception to §1.13.

`question_context`, `current_context`, `include_facts`, and `include_entities`
are absent from the catalog, registry, manifest, API/SDK/CLI/MCP descriptors,
consumption skill, and benchmark prompt. No aliases or compatibility versions
remain. `resolve_entity` is the sole assured name/alias authority, while
`semantic_entities` remains exploratory description-similarity nomination.

An implementation change that alters any descriptor, selection semantics,
bound, field, negative, or association increments that operation's version and
rolls `surface_manifest_hash`. D97 applies that rule: `fact_context@2` adds the
bounded live-graph neighborhood before P1 fact-text nomination, and
`answer_context@2` carries that changed fact child. `resolve_entity@1` and
`testimony_context@1` remain unchanged. Explicit deeper or path-shaped graph
work still uses the live SQL helpers or `examples.multi_hop_context`; it is not
silently added to an assured response.

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
| `entities_current` | one externally visible survivor entity; `(deployment_id, entity_id)` | Type/name/profile orientation and live mention summary; degree is computed on demand from live relation adjacency and is not a column; derived entities without surviving provenance absent |
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

The fact relations share three private PostgreSQL authorities which are not
caller-reachable and are not additions to the 24-relation public surface.
`v_memory_fact_visible` owns historical fact membership;
`v_memory_fact_claim_live` owns the fully coordinate-bound current-testimony
claim association without asserting fact membership; and
`v_memory_evidence_lineage_live` owns the D54 fact × document-lineage × stance
aggregation. `fact_claim_evidence_live`, `evidence_lineage`, and
`facts_visible_history` compose those authorities and add fact membership once.
They MUST NOT independently reconstruct these rules from base evidence tables.
This factoring is semantic, not merely a planner hint: it keeps all consumers
on one D41/D48/D54 authority while avoiding recursive expansion of the same
visibility tree.

**Complete public facts-layer surface.** For the `memory_v1` query space it is
exactly `facts_current`, `facts_visible_history`, `facts_as_of`,
`fact_claim_evidence_live`, `evidence_lineage`,
`contradiction_members_current`, `testimony_currency_events_visible`,
`graph_edges_current`, `graph_edges_visible_history`, and `semantic_facts`.
Together these expose current and historical fact membership, both clocks,
current source-lineage evidence, the auditable claim bridge, contradiction
membership, testimony-currency transitions, relation-shaped graph projection,
and semantic fact-label entry. `lexical_facts` is not in this surface because
the audited P1 contract does not lexically index fact labels; its
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
meaning, or weakening visibility normally requires `memory_v2`. D98 is the one
explicit pre-release clean-cut exception: there are no consumers, so the
migration drops/recreates the unshipped v1 graph functions and rolls the
manifest in place. This exception expires with the first stable release and
does not weaken the future major-version rule. `SELECT *` is valid SQL but never
a compatibility promise. Old major schemas remain read-only during any later
published compatibility window.

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
| PostgreSQL P1 SRFs | Rank `chunk_search` or private in-row embeddings and apply target-specific authority joins/predicates in one statement before exposing rows |
| SQL/PGQ fixed patterns | Match only invariant-bearing deployment-keyed graph views at the caller's statement clock; explicitly reject repeated element UUIDs when simple-path semantics are required |
| Recursive graph helpers | Apply authority predicates during expansion and drop a path as one unit when any member fails |
| Saved queries and assured operations | Execute through the same role/views/helpers; no cached result rows bypass confirmation |
| Counts | Aggregate from `evidence_lineage` after liveness/current-testimony filtering, never from cached claim counts |
| K artifacts | Expose links only when each target is visible; K prose stays compiled and cannot be promoted to live fact |
| Corpusfs/P1 bodies | Confirm the current coordinate, reproducible embedding-text hash, generated-prefix separation, and policy generation before body return; the source-content hash remains a coordinate until its ordered-block inputs are available |
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
or both; supplying only one raises PostgreSQL `invalid_parameter_value`, which
the public surface maps to `invalid_parameter`. PostgreSQL's unbounded
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

Only the functions below are public. Each is schema-qualified and bounded
independently of an outer `LIMIT`. PostgreSQL-only `facts_as_of`,
`graph_neighborhood`, `graph_path`, and `graph_citation_path` are ordinary allowlisted
`SECURITY INVOKER` functions owned by the no-login view owner. Projection-backed
semantic, lexical, and body calls are recognized from the validated statement.
For semantic calls, the trusted executor computes the query embedding once and
passes it as a bound vector to one private PostgreSQL ranked statement; lexical
and body calls remain PostgreSQL-native. No untrusted language, filesystem or
network authority is loaded into PostgreSQL. The executor preserves each
invocation's caps, generation pin, authority-join point, telemetry, and
once-per-syntactic-call behavior. `facts_as_of` is `PARALLEL
SAFE`; graph helpers and executor-resolved projection calls are parallel-unsafe
at the sandbox boundary. Callers cannot supply URLs, relation names, code, raw
filter expressions, or projection locations.

`facts_as_of`, `graph_neighborhood`, `graph_path`, and `graph_citation_path` are `STABLE` within the
PG statement snapshot. The executor treats each of the four semantic calls,
two lexical calls, and the body-fetch call as external/volatile work: it
evaluates each syntactic invocation once and never duplicates it as a planner
optimization.

| Function | Signature and row contract |
|---|---|
| `facts_as_of` | `(valid_at timestamptz, believed_at timestamptz, max_rows int)` → fact fields with `evidence_count_current`, `contradict_count_current`, `support_state_current`, applied instants, and `identity_regime = 'current'`; default/hard bounds are in §4.3 |
| `semantic_claims` | `(query text, k int, filters jsonb DEFAULT '{}')` → confirmed `claim_id`, rank, score, channel and current embedding/freshness columns; default/hard `k` is in §4.3 |
| `lexical_claims` | `(query text, k int, filters jsonb DEFAULT '{}')` → the same confirmed claim result columns as `semantic_claims`, with lexical-channel rank/score semantics below |
| `semantic_chunks` | `(query text, k int, filters jsonb DEFAULT '{}')` → confirmed `chunk_id`, rank, score, channel, separately labeled `source_text` and `location_header`, coordinate/hash and current embedding/freshness columns |
| `lexical_chunks` | `(query text, k int, filters jsonb DEFAULT '{}')` → the same confirmed chunk result columns as `semantic_chunks`, with lexical-channel rank/score semantics below |
| `fetch_chunk_bodies` | `(chunk_ids uuid[])` → `input_ordinal`, confirmed `chunk_id`, current document/version/representation/section coordinate, source/embedding hashes, separately labeled `source_text` and D80 `location_header`, policy/embedder generations, and freshness columns; no nomination or ranking columns |
| `semantic_facts` | `(query text, k int, filters jsonb DEFAULT '{}')` → confirmed `(fact_kind, fact_id)`, rank, score, channel and current embedding/freshness columns; confirmation is against the requested current/history fact authority |
| `semantic_entities` | `(query text, k int, filters jsonb DEFAULT '{}')` → description/profile-vector search over the existing canonical entity/profile row, returning authority-confirmed survivor `entity_id`, entity type/name/profile orientation fields, rank, score, channel and current embedding/freshness columns; confirmation is against `entities_current` in the same statement. The scored entity-nomination method does not exist on the shared P1 port today and is ADDED by this change (the port exposes only id-addressed `entity_vectors`), parallel to the lexical score extension |
| `graph_neighborhood` | `(deployment_id uuid, start_entity_id uuid, max_depth int DEFAULT 2, predicates text[] DEFAULT NULL, valid_at timestamptz DEFAULT NULL, believed_at timestamptz DEFAULT NULL, max_results int DEFAULT 100, expansion_budget int DEFAULT 2000, frontier_budget int DEFAULT 1000, time_budget_ms int DEFAULT 1000)` → deterministic complete path rows plus exactly one terminal status row. Every row has `row_kind`; the status carries `truncated`, `truncation_reason`, `examined_edges`, `returned_paths`, and every effective depth/work/result/time budget |
| `graph_path` | `(deployment_id uuid, from_entity_id uuid, to_entity_id uuid, max_depth int DEFAULT 4, predicates text[] DEFAULT NULL, valid_at timestamptz DEFAULT NULL, believed_at timestamptz DEFAULT NULL, max_paths int DEFAULT 3, expansion_budget int DEFAULT 2000, frontier_budget int DEFAULT 1000, time_budget_ms int DEFAULT 1000)` → zero or more deterministic complete **equal-length shortest-tier** path rows plus the same terminal status; it never returns longer paths after a target-bearing level |
| `graph_citation_path` | `(deployment_id uuid, from_doc_id uuid, to_doc_id uuid, max_depth int DEFAULT 6, max_paths int DEFAULT 3, expansion_budget int DEFAULT 2000, frontier_budget int DEFAULT 1000, time_budget_ms int DEFAULT 1000)` → zero or more deterministic complete equal-length shortest-tier directed citation paths plus the same terminal status; citation follows only `from_doc_id → to_doc_id` and has current structural semantics |

Graph data rows repeat the invocation's final `truncated`,
`truncation_reason`, `examined_edges`, and `returned_paths` values so a raw
function projection cannot contradict the terminal row. The sandbox still
treats `QueryResult.graph_invocations` as authoritative because only its
reserved terminal carrier survives an empty caller result.

The graph helper implementation is `SELECT`-only `STABLE` PL/pgSQL with bounded
function-local arrays; it creates or mutates no regular or temporary table. A
terminal `row_kind = 'status'` row exists even when there are no data rows, so
work/truncation metadata cannot disappear with an empty result. Direct SQL may
inspect it. The query sandbox performs the one-statement rewrite below for each
admitted graph invocation:

1. materialize the function once as `__rememberstack_graph_all_N`;
2. expose only `row_kind = 'data'` through `__rememberstack_graph_data_N` to the caller query;
3. materialize the caller query as `__rememberstack_user_result` with an internal present-row
   marker; and
4. `RIGHT JOIN` it to a one-row JSON aggregate of every terminal status row,
   ordered by the graph invocation's stable AST traversal ordinal.

The rewrite also preserves the caller's top-level ordering and pagination. The
AST adds reserved hidden sort-key columns and a
`__rememberstack_order_ordinal` computed from the original top-level `ORDER
BY`; the materialized user result applies the original `LIMIT`/`OFFSET`, and
the outermost `SELECT` orders by that ordinal after the status join. A
status-only carrier sorts last (and is the only physical row when no caller row
survives); the executor removes it. If an admitted statement shape cannot
project equivalent hidden sort keys without changing `DISTINCT`, grouping, or
set-operation semantics, graph-helper use in that shape is rejected with
`statement_not_allowed` rather than falsely setting `ordered_result = true`.
This preserves `LIMIT 0` as zero public rows while still carrying terminal
status internally.

The final statement therefore returns either each caller row plus reserved
internal metadata columns, or one internal status-only row when the caller
result is empty. The executor removes the internal columns/status-only row and
sets `QueryResult.graph_invocations[]` from that array without merging unlike
budgets. Overall `truncated` is `bool_or(invocation.truncated)`; overall
`truncation_reason` is the first truncated invocation by AST ordinal, and
`warnings[]` contains one entry for every truncated invocation. Any caller CTE, relation alias, column alias, or output
name beginning `__rememberstack_` is rejected before rewrite. The rewrite preserves one PostgreSQL statement snapshot and
`statement_timestamp()`; it needs no GUC, temporary object, second statement,
or connection-local state.

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
or `contradicts`. P1 applies these filters through indexed normalized authority
joins inside the ranked statement; it does not copy them into search rows. In
v1 `source_shape` is a derived D80 location-fact filter only: it is not
authorization-relevant and remains on its typed location-fact authority.

This table is the public SRF filter allowlist, not the assured-operation
nomination contract. The §3.1 context operations additionally use their
descriptor-pinned entity/time scope metadata inside the shared P1 adapter; that
metadata does not become a caller-authored filter language. In particular,
`fact_context` non-current modes do not call the current-confirmed
`semantic_facts` SRF, and entity-scoped chunk retrieval does not manufacture a
public `semantic_chunks` filter absent from this allowlist.

`lexical_claims` and `lexical_chunks` perform BM25/exact-term nomination through
the same pg_textsearch BM25 indexes used by the internal claim and chunk
hybrids; built-in `ts_rank`/`ts_rank_cd` are not a substitute and cannot be
labelled BM25. The port's
result extends to carry the already-computed score without adding another
search. `channel = 'bm25'`; `rank` is the one-based eligible PostgreSQL
nomination position; `score` is the raw pg_textsearch BM25 relevance score
(the SQL operator orders its negative distance ascending, while the public
score is rendered as relevance where larger is better). A lexical score is neither
normalized nor comparable to a semantic score. Score ties break by stable item
ID. Both lexical SRFs perform the same target-specific authority join as their
semantic siblings inside the ranked statement.

`semantic_entities` embeds the query once and reaches the canonical entity or
profile row only through the shared P1 search port's entity-nomination method.
It searches the description/profile vector rather than aliases, then confirms
survivor identity and `entity_type` against `entities_current` in the same
statement. Direct access to private embedding columns from caller SQL is
forbidden.

Each semantic or lexical nomination invocation validates the one configured
embedding model/policy and target-channel readiness before any query embedding
or search. There is no caller-selectable historical search generation. An
unready configured generation fails with `generation_unavailable`; it never
falls forward. Results are ranked deterministically by score, then stable item ID.
The function performs ranking and target-specific authority confirmation in
one PostgreSQL statement. That statement snapshot is the D48 linearization
point and is emitted as `pg_confirmed_at`.
A deletion committed before that snapshot removes the row; a commit after it is
a normal later state change. Lexical invocation skips query embedding but
reports the same current search-configuration attestation and freshness.

`semantic_chunks`, `lexical_chunks`, and `fetch_chunk_bodies` share one body
path. It obtains bytes only after PG confirms the current ready
document/version/representation/section coordinate, then verifies the
reproducible embedding-text hash and generated-prefix separation. The stored
source-content hash remains a returned coordinate but is not claimed as
body-byte verification in v1: it is composed from ordered block hashes whose
inputs are not present in the P1 body row. A future store contract may add
those reproducible inputs and then strengthen verification without weakening
this rule. The path returns source body and D80 deterministic location header in separate columns;
the header is never asserted evidence. `fetch_chunk_bodies` is this exact path
minus nomination: `input_ordinal` records first input position, duplicate IDs
collapse to that first position, and more than 50 IDs fails
`invalid_parameter` before any store read. An outer query requires
`ORDER BY input_ordinal` to contract row order under §4.4. Missing, stale,
tombstoned, coordinate-mismatched,
prefix-mismatched, or hash-mismatched IDs return no row. Each category and the
   total absent count appear in the invocation drop disclosure. Mixed or stale
policy/embedder attestation fails the entire invocation. All three
body-bearing functions share the §4.3 chunk-text byte caps.

The executor captures every semantic, lexical, and body-fetch invocation,
including one returning zero rows, into the existing
`QueryResult.semantic_invocations[]` with requested/candidate count,
eligible count, body-mismatch/deep-hydration drop counts, generations, P1
snapshot, PostgreSQL authority time, applicable embedding/search/body latency, and
termination reason. For `fetch_chunk_bodies`, requested IDs occupy the existing
candidate-count slot; no new `QueryResult/v1` field is added. A missing,
incompatible or unready P1 extension/index fails the affected operation with
`p1_unavailable`; plain PostgreSQL SQL remains available when the database is
healthy. PostgreSQL unavailability fails the whole statement with
`pg_unavailable`. Partial unconfirmed output is forbidden.

Graph helpers traverse only PG views. Neighborhood uses a level-complete global
minimum-depth map with deterministic representative selection; entity/citation
path use per-candidate-path visited vertex/edge sets so bounded equal-length
alternatives and parallel edges remain possible. They order shortest depth
first, then relation-ID sequence. Entity and citation
path return only complete equal-length paths from the first target-bearing BFS
level; `max_paths` bounds alternatives on that tier, not longer paths. Their
default/hard depth, frontier, expansion, edge, path, temp, and time bounds are
defined only by the §4.3 limits table. The status-row/final-query rewrite above
discloses a cap in `QueryResult` even when the caller query returns no data rows.
Raw recursive CTEs remain available only under §4.1's template linter and the PG
snapshot.

The two entity graph helpers perform the paired-clock refusal inside their documented
function bodies. Omitting both clocks applies `statement_timestamp()` to both;
supplying both uses the two supplied half-open instants; supplying exactly one
fails with PostgreSQL `invalid_parameter_value`. `memory_v1` publishes no
auxiliary clock-guard function. Citation traversal uses only current structural
cross-reference edges and accepts no invented history clocks.
PUBLIC has no EXECUTE privilege on any function in the schema; the routed
deployment query role receives EXECUTE only on the functions enumerated by the
manifest. The migration also revokes PostgreSQL's default PUBLIC function
EXECUTE privilege for subsequent functions in this schema.

### 3.5 Graph query boundary

Public Cypher is removed. Public arbitrary SQL/PGQ is not admitted while the
default-deny `pglast` gate embeds PostgreSQL 18 grammar. The typed graph API and
server-owned SQL use the live-graph contract in
[`p2_graph_design.md`](p2_graph_design.md): fixed one-hop SQL/PGQ plus
deployment-scoped work-bounded traversal functions. Public `query_sql` may call
only the allowlisted traversal functions described in §3.4; it cannot submit
`GRAPH_TABLE`, property-graph DDL, generated joins, or arbitrary graph labels,
properties, identifiers, functions, or patterns.

A later public SQL/PGQ proposal requires a PG19-capable AST parser, a separate
default-deny admission design, exact supported-feature allowlist, plan/work
gates, result limits, security review, and a new manifest identity. It is not
a compatibility promise.


## 4. Query sandbox, tenancy, limits, and result contract

### 4.1 Grammar and rejection contract

One SQL request contains one `SELECT`, `VALUES`, or read-only `WITH [RECURSIVE]`
statement. `UNION`, `INTERSECT`, `EXCEPT`, subqueries, `LATERAL`, joins,
filters, grouping, `HAVING`, windows, ordering, `LIMIT`, and `OFFSET` are
allowed. `explain_sql` alone accepts `EXPLAIN (FORMAT JSON)` and never
`ANALYZE`. Every value originating outside the saved statement uses typed
positional parameters; interpolation is forbidden.

Raw SQL containing U+0000 is rejected with `parse_error` before the PostgreSQL
parser is called; a parser's treatment of a string terminator cannot authorize
or hide any suffix of the submitted request.

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

For a graph SRF, that materialization is the `__rememberstack_graph_all_N` status-preserving
CTE specified in §3.4, and the caller position is replaced by
`__rememberstack_graph_data_N`. The executor wraps the complete caller statement in
`__rememberstack_user_result`, adds its reserved present-row marker, and right-joins the
one-row status aggregate using reserved `__rememberstack_*` columns. This is an
AST rewrite, not string concatenation. It preserves one database statement even
when the caller filters, aggregates, or returns zero rows; the internal carrier
row/columns are removed only after truncation and budget metadata are recorded.
Caller CTE names, relation aliases, column aliases, or result columns with that
reserved prefix are rejected with `statement_not_allowed`; negative tests use
each identifier class, including a caller CTE named
`__rememberstack_graph_data_1`.

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

Only the caller's original top-level `ORDER BY` sets `ordered_result = true`;
the graph-status rewrite lifts that order to its outermost select as specified
in §4.1. Absent it, row order is explicitly nondeterministic. The parser and PostgreSQL both enforce
read-only execution. Raw PostgreSQL error details, object names outside the
public schema, and query fragments are not returned.

The public error codes are exhaustive:

| Phase | Codes |
|---|---|
| Parse/validation | `parse_error`, `multiple_statements`, `statement_not_allowed`, `relation_not_allowed`, `function_not_allowed`, `function_placement_not_allowed`, `operator_not_allowed`, `invalid_parameter`, `schema_version_mismatch`, `unbounded_recursion` |
| Admission | `quota_exceeded`, `concurrency_exceeded`, `saved_query_not_found`, `saved_query_disabled`, `saved_query_incompatible`, `saved_query_revalidation_pending` |
| Execution | `statement_timeout`, `lock_timeout`, `cancelled`, `resource_limit`, `execution_error` |
| Store/confirmation | `pg_unavailable`, `p1_unavailable`, `graph_unavailable`, `corpus_body_unavailable`, `confirmation_failed` |

`graph_unavailable` means the live graph catalog, views, or bounded functions
failed readiness. Zero rows is success with `empty_result = true`, never an
error and never a D49 negative. Rejected and failed results also carry zero
rows and set `empty_result = true`; their termination reason and error code
distinguish them from a successful empty read. The store codes and fallback
behavior cross-reference the §7 failure matrix.

### 4.2 Ownership, tenancy, and trust boundaries

D68 physical database-/schema-per-deployment routing is the PRIMARY tenancy
boundary. The gateway authenticates a principal, resolves exactly one
deployment, and pool checkout selects that deployment's database and a
deployment-bound login role. SQL text and parameters never choose deployment.

The typed graph surface uses the same deployment-bound PostgreSQL connection.
Every traversal takes `deployment_id` as its required first argument and every
property-graph key/reference includes it, but caller SQL cannot choose another
deployment or invoke arbitrary PGQ. There is no graph file, reader cache,
snapshot path, attachment, or URI to select.

The AST binder requires that first argument to be the reserved authenticated
deployment parameter; a literal, caller-owned parameter, missing binding, or
value unequal to the authenticated deployment fails with the public
`invalid_parameter` code before function execution and performs no graph
access. It never becomes a successful empty result that could be mistaken for
“no neighbors.”

The role split is fixed:

- a no-login migration/table owner owns base objects;
- a distinct no-login view owner has the minimum base-table `SELECT` grants,
  is not superuser, and owns the `memory_v1` views as PLAIN views —
  `security_barrier` is deliberately not used (it blocks planner predicate
  pushdown on every query; the view predicates are invariant filters, not
  caller-facing security, so the caller-visible rows are already the caller's
  entitlement — operator performance directive 2026-08-04);
- a deployment-bound login role has `USAGE`/`SELECT` only on its deployment's
  `memory_v1` and `EXECUTE` only on §3.4 functions;
- bridge, gateway-admin, migration, and audit roles are absent from the agent
  pool.

**Row-level security is deliberately NOT used** (operator decision 2026-08-04:
measured performance degradation and maintenance burden in prior systems;
with physical isolation primary, per-row policies are redundant complexity).
Tenancy is enforced entirely by: (a) D68 physical routing — the connection a
query runs on belongs to exactly one deployment's database or schema; (b)
grants — the login role can reference only its deployment's `memory_v1` views
and public functions, and holds no privilege on any other deployment's
objects or any base table; (c) pool discipline — checkout resets session
state and binds one login to one deployment, and check-in discards the
session on reset failure. The §3.4 executor-side projection bridge derives the
deployment from its authenticated, deployment-bound executor (never from SQL
text or parameters) and passes it explicitly to every P1 ranked statement;
a missing or mismatched binding fails before any projection access. The §9.5
adversarial suite targets this
model: routing, grants, pool reuse, and bridge-derivation — not policies.

A client-writable custom GUC is not an authority; `SET` and `set_config` are
unavailable. In every provisioned deployment database, `PUBLIC` has no
`CONNECT` or `TEMPORARY`, no access to the private product schema, and no
execute privilege on product routines in `public`. The `pg_catalog` baseline
remains available, with the parsed function/operator allowlists deciding what
caller SQL may invoke. Provisioning must revoke the database's default
`PUBLIC` privileges before deployment content or a query credential exists,
then grant `CONNECT` only to that database's derived query login. PostgreSQL
privileges are additive — a direct revoke from one login cannot override a
`PUBLIC` grant — so an unprovisioned or administrative database is not claimed
to have a per-login deny ACL; it MUST contain no deployment content and the
pool/HBA route MUST NOT offer it to a deployment login. See PostgreSQL
[Privileges](https://www.postgresql.org/docs/current/ddl-priv.html) and
[REVOKE](https://www.postgresql.org/docs/current/sql-revoke.html), retrieved
2026-08-05. The query role has no base-schema usage, role membership, outbound
network, server-file, large-object, or operator-table capability.

Every checkout resets all session state and reapplies role, `search_path`,
timeouts, memory, temp, parallelism, and read-only transaction state before use;
every check-in rolls back and discards the session on reset failure. Query
transactions use `READ ONLY, REPEATABLE READ`: bounded nomination confirmation
and the caller statement share one PostgreSQL snapshot even when one public
request needs several internal statements. The caller still submits exactly one
statement.

The adversarial CI suite runs under the real query and graph roles. It MUST
cover two deployments with distinguishable sentinels; direct qualification,
CTEs, subqueries, unions, lateral joins, casts, error messages, global lookup
joins, function indirection, recursive queries, saved queries, malicious P1
IDs, malicious graph UUIDs, tombstoned bodies, and A→B→A pool reuse. It also fuzzes at
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
same language schemas, grammar, tenancy boundary, applicable D48 time boundary,
QueryResult contract, and nomination/body/graph caps. The table below is the
single normative source for every §3.4 function
default and hard bound and every executor resource bound; prose elsewhere names
the applicable default/hard class and cites this table.

| Resource | Default | Interactive hard cap | Analytical hard cap |
|---|---:|---:|---:|
| SQL statements/request | 1 | 1 | 1 |
| SQL text | — | 64 KiB | 64 KiB |
| SQL bound parameters / encoded bytes | 64 / 256 KiB | 256 / 1 MiB | 256 / 1 MiB |
| SQL/graph statement timeout | 5 s | 15 s | 60 s |
| Lock timeout | 250 ms | 1 s | 2 s |
| Idle transaction | 5 s | 10 s | 15 s |
| SQL returned rows | 200 | 1,000 | 10,000 |
| SQL returned encoded bytes | 1 MiB | 8 MiB | 64 MiB |
| `work_mem` | 16 MiB | 32 MiB | 64 MiB |
| SQL/graph temporary files | 64 MiB | 64 MiB | 64 MiB |
| Recursive CTEs / maximum depth | 1 / 4 | 1 / 6 | 1 / 6 |
| `facts_as_of` returned rows | 200 | 1,000 | 1,000 |
| Semantic or lexical nomination SRF calls / `k` each / total nominations | 1 / 20 / 100 | 3 / 100 / 200 | 3 / 100 / 200 |
| `fetch_chunk_bodies` calls / chunk IDs each | 1 / 50 | 3 / 50 | 3 / 50 |
| Chunk source text across `semantic_chunks`, `lexical_chunks`, and `fetch_chunk_bodies` | 512 KiB/invocation | 4 MiB/statement | 4 MiB/statement |
| Graph helper invocations / statement | 1 | 3 | 3 |
| Neighborhood depth / returned edges / expansion budget | 2 / 100 / 500 | 4 / 500 / 2,000 | 4 / 500 / 2,000 |
| Entity path depth / equal-length shortest paths / returned edges / expansion budget | 4 / 3 / 100 / 1,000 | 6 / 10 / 500 / 2,000 | 6 / 10 / 500 / 2,000 |
| Citation path depth / equal-length shortest paths / returned edges / expansion budget | 6 / 3 / 100 / 1,000 | 6 / 10 / 500 / 2,000 | 6 / 10 / 500 / 2,000 |
| Graph frontier retained entries | 200 | 500 | 500 |
| Concurrent SQL statements per principal | 2 | 4 | 1 |
| Concurrent SQL statements per deployment | 8 | 16 | 4 |
| Principal SQL statement-seconds / rolling 60 s | 30 | 60 | 60 |
| Deployment SQL statement-seconds / rolling 60 s | 120 | 240 | 240 |

Parallel query is disabled for interactive roles. SQL consumes the
per-principal/deployment concurrency slots and rolling statement-second
quotas. Parameters are typed and never interpolated into text. Client
disconnect triggers PostgreSQL
cancellation within one second. A wire row cap does not bound work below an
aggregate or sort; engine timeout, memory/temp limits, concurrency, and rolling
quotas remain mandatory. Larger exports use the separate governed scan/export
surface, not the open interactive language.

### 4.4 `QueryResult/v1`

Every successful, empty, truncated, rejected, or failed public SQL query
carries this provenance header before any rows:

```text
contract = "QueryResult/v1"
query_language = "sql"
grade = "exploratory_tabular"
request_id, deployment_id
surface_manifest_hash, query_space_schema = "memory_v1"
query_hash
saved_query = {query_id, namespace, name, version, query_hash} | null
referenced_views[], referenced_functions[], source_grain_tags[]
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
graph_invocations[{ordinal, function, truncated, truncation_reason,
                   examined_edges, returned_paths, effective_depth,
                   effective_expansion_budget, effective_frontier_budget,
                   effective_result_budget, effective_time_budget_ms,
                   applied_valid_at, applied_believed_at, evaluated_at}]
confirmation = {requested, pg_confirmed_at, nominated, confirmed,
                dropped_stale} | null
```

`query_hash` is SHA-256 over the normalized PostgreSQL AST plus parameter type
vector, never parameter values. `columns[].type` and the hash type vector use
canonical PostgreSQL types. `exact_total_known` is true only for a completed,
parser-recognized outer exact-count query; a cap probe establishes truncation
but not an exact total. Only a top-level `ORDER BY` makes `ordered_result` true.
`evaluated_at` is non-null only when every referenced relation/function is a
current-or-as-of fact/graph helper using one compatible applied instant; any
mix with evidence, history, or live-content views forces it null.
For graph SRFs the executor reads each invocation's truncation/work/effective
limits from the reserved terminal-status array before discarding its internal
columns. It applies the ordinal/`bool_or` rule above and never infers
completeness from an outer `LIMIT` or a missing data row.

`exploratory_tabular` does not guarantee a platform result grain, caller
interpretation, D49 intent negatives, contradiction/evidence completeness,
exact totals unless disclosed, deterministic order unless disclosed, or
freedom from caller-authored filter/join/aggregation errors. There is no
`snapshot_graph` grade, graph-generation block, or generic result-to-Envelope
adapter. Typed graph operations and the four assured operations return their
own binding envelopes; `answer_context` returns the complete testimony and
fact envelopes in `ContextBundle/v1`.


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

Stock self-host `setup` owns this publication ordering on upgrade. It publishes
the checked-in hash before seeding `examples.*`, suspending ordinary active
versions atomically. The platform seed then installs one new active version for
each shipped example and deprecates that example's suspended prior version;
customer-authored versions remain `pending_revalidation`. When the authoritative
hash already matches, setup performs neither a publication audit nor shipped
version churn.

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

Every public view, column, and SQL function has manifest-owned comments stating
its grain/type, key or identity meaning, current/history meaning, null-clock
convention, deletion boundary, count exactness, orientation/evidence status,
limits, and failure behavior. Comments are complete sentences and avoid private
PostgreSQL table names. Each SQL function has a valid example. Internal
property-graph labels, properties, keys, endpoint mappings, and grants are
verified through PostgreSQL's semantic graph catalogs and readiness contract,
not presented as a customer-authored query language.

The compact first-call discovery resource, the consumption skill, and the OSS
retrieval docs each open with the exact **Bound two-layer retrieval headline
(reused verbatim)** paragraph under that heading at the top of this design,
before any language or operation choice. That paragraph is the retrieval docs'
opening section, not a sidebar or warning. The first-call resource then presents
the three public choices without hiding their contracts:

- relational SQL gives live PostgreSQL state and direct evidence composition;
- bounded SQL graph helpers give live neighborhood and shortest-path traversal;
- the four assured operations give one-call typed answers with D49 guarantees
  per authority layer and an explicit two-envelope bundle for the composite.

It includes the query-space hash, hard limits, and worked examples for the two
truth layers, current facts, testimony, aggregation, bounded live graph
traversal, and semantic-to-relational composition. It states that empty SQL is
untyped, a truncated graph result proves no absence beyond the reported search
boundary, and claims do not answer current truth.

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

`describe_query_space` reads the checked-in manifest. `search_query_space`
searches only names, comments, tags, and examples in that manifest. Neither
reads tenant content or exposes arbitrary `pg_catalog`. Before each SQL or
saved-query execution, the executor compares the live PostgreSQL major, view
set, ordered columns/types, and comments with the manifest; interface drift
disables that request with `schema_version_mismatch`. Exact view-definition
parity is a deploy/CI gate using the same-server reference comparator, where
printer output is comparable without becoming a runtime hash input. Graph
readiness separately validates the exact property-graph aliases, element views,
composite keys, endpoints, labels, properties, property types, and effective
graph/element-view privileges through PostgreSQL's semantic catalogs. A mismatch
disables typed graph operations and graph helpers with `graph_unavailable`.
Discovery publishes every named field of each tier's authoritative limit
record; it does not maintain a shorter hand-selected presentation that can
drift from the hashed `limits` member.

After its mandatory two-layer opening, the consumption skill presents the same
choice plainly: relational SQL for live evidence-composable reads, bounded live
graph helpers for traversal, and assured operations for one-call typed answers.
It includes the same worked examples as discovery. It warns that claim rows are
testimony, empty SQL is not `known_empty`, a truncated traversal proves no
absence beyond its reported boundary, outer queries can erase
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
   byte caps. The graph helpers carry valid examples and comments that state the
   both-or-neither clock rule, work/depth/result bounds, truncation fields, and
   `invalid_parameter_value` failure;
3. `core_operation_descriptors`: the four sorted descriptors with name,
   version, input schema, closed result schema, result contract, grain/intent,
   bounds, and
   `implementation_plan_hash`. The testimony descriptor names only its claim
   and source-passage channels; the fact descriptor contains the discriminated
   time selector, matching required `temporal_scope` result union, both-stance
   evidence budget, and optional confirmed entity anchors; the answer
   descriptor pins the exact two child descriptors and
   `ContextBundle/v1`; its plan hash incorporates the ordered child descriptor
   hashes, so a child roll necessarily rolls the bundle descriptor. The catalog replacement rolls the public tool catalog
   and this manifest atomically;
4. `limits`: the exact public SQL grammar/operator/function allowlists and all
   default, interactive, analytical, semantic/lexical/body, graph traversal,
   concurrency, and quota limits. The internal SQL/PGQ template set and property
   graph catalog identity are deployment artifacts governed by the graph design,
   not public open-query grammar.

The canonical AST serializer and version are pinned in the manifest generator;
golden-vector fixtures for that pinned serializer are checked in and MUST pass.
Raw SQL text is never a hash input and is not hashed as an intermediate
surrogate; only the pinned canonical AST serialization represents SQL
definitions. Manifest and runtime-introspection type names are normalized to
the canonical names returned by `pg_catalog.format_type` before comparison and
serialization. Formatting and comments outside manifest fields cannot change
the hash. The resulting hash is independent of PostgreSQL minor version (the
declared PostgreSQL major remains a manifest field). Physical indexes, plans,
statistics, data, property-graph contents, saved queries, and registry rows are
excluded. Any semantic view change, even with unchanged columns, changes the
definition AST and the hash. Any semantic function change requires a contract-
version/signature change and therefore changes the hash. A graph-helper signature
or cap change changes the hash. An internal property-graph catalog change rolls
the separate graph contract/version and must pass graph readiness and parity
tests before deployment.

`RS-LoCoMo-Full-v15` pins `surface_manifest_hash`, the four assured-operation
descriptors, and the complete 21-tool answer catalog. V9–v14 runs remain
self-describing historical evidence; the aborted v12 answer pass remains
self-describing operational evidence and is not a v13 score. There is no
surface compatibility arm. V15 traces record manifest hash, assured-operation
calls, SQL hashes, graph-helper operation/depth/truncation metadata, errors,
caps, and latency/cost. Raw SQL and parameters follow §7 retention. The answer agent receives only product behavior
available to customers, and accepting this design does not authorize a paid
run.

## 7. Observability, retention, and failure behavior

Every attempt emits an audit event with request, deployment and principal IDs;
surface/manifest/query/saved-query hashes; referenced public objects; admission
decision; PG/P1/corpusfs readiness and freshness when touched; timings; plan-cost
estimate where available; rows/bytes/temp work; limits; cancellation/error code;
P1/body candidate/drop counts; graph operation, depth, frontier, examined-edge,
result-row, truncation, and cap events; and core-operation name/version when
applicable. Cost attribution charges PostgreSQL statement time/temp work,
query-embedding and P1 index work, and returned bytes to principal and
deployment. The cost ledger is operator-only.

Default telemetry never persists raw ad-hoc SQL, parameter values, result rows,
chunk bodies, or raw PostgreSQL error text. It retains audit metadata for 30 days
and non-content aggregate cost/reliability metrics for 13 months. Saved SQL
follows registry retention. An operator-enabled debug capture is deployment-
scoped, encrypted under a separate key, access-audited, excludes results/bodies,
redacts parameters by schema, lasts at most seven days, and auto-purges. Legal
hold is an operator policy outside the library and cannot make forgotten corpus
content reappear through a live query surface.

Incident controls include per-principal/deployment open-SQL kill switches,
per-function graph disablement, saved-query fleet disablement, role revocation,
pool drain, and manifest/graph-contract quarantine. Graph catalog mismatch or a
traversal budget breach fails the request with zero partial rows unless the
closed result schema explicitly reports a complete truncated prefix. Recovery
never grants raw tables or bypasses deployment/time/provenance predicates.

| Failure/disagreement | Binding behavior |
|---|---|
| PostgreSQL unavailable | SQL, saved-query, graph-helper, and core paths fail `pg_unavailable` with no rows |
| P1 extension/index unavailable | Plain relational SQL and graph reads remain available while PostgreSQL is healthy; affected semantic/lexical/body operations fail `p1_unavailable`; a core operation can return only a descriptor-permitted non-P1 channel with a D49 `boundary`, otherwise it fails |
| One `answer_context` child returns a typed D49 negative | The request succeeds with both complete child envelopes; the other child is neither suppressed nor relabeled |
| One `answer_context` child has a schema, timeout, cancellation, or store/execution failure | The entire request fails with that typed transport error and returns no `ContextBundle/v1`; half-bundles are forbidden |
| Property-graph catalog or grants disagree with the graph contract | Typed graph operations and graph helpers fail `graph_unavailable`; unrelated relational SQL and assured operations remain available |
| Traversal reaches a declared work/result/depth cap | The closed graph result reports the reached boundary and `truncated=true`; it makes no absence or shortest-path claim beyond the explored boundary |
| Artifact/chunk body unavailable | Metadata SQL remains available; body-bearing candidates drop and are counted; a body-required invocation with no valid body fails `corpus_body_unavailable` |
| P1 state and authority disagree | For chunks, the same-statement authority join rejects an ineligible `chunk_search` row. For in-row targets, null/stale embedding attestation makes the semantic row unready. Authorization uncertainty always fails the invocation. |
| Body coordinate/hash/prefix disagrees | Candidate drops; no bytes return; systemic mismatch marks the chunk channel unready and schedules repair |
| Runtime public interface shape disagrees with the manifest | Open SQL and saved queries fail `schema_version_mismatch`. Exact semantic view-definition disagreement fails the deploy/CI same-server comparison. The four core operations remain available only if their own descriptors/invariants verify |
| Forget is pending or incomplete | Live SQL/core lineage and graph paths fail closed under D48/D74; no copied graph or older generation exists to retain forgotten content |

## 8. Pre-release surface cut and terminal criteria

There is no dual-surface migration. The library has no users or integrations
that require the 17 demoted adapters, so carrying a deprecation protocol would
protect nobody while preserving duplicate invariant logic.

1. Ship `memory_v1`, `QueryResult/v1`, read-only public SQL, bounded live graph
   helpers, discovery, allowlisted functions, saved-query governance, the 17
   demoted examples plus `examples.graph_citation_path`, and exactly four
   assured operations.
2. Seed and expose only `resolve_entity`, `testimony_context`, `fact_context`,
   and `answer_context`. Bootstrap atomically replaces the deployment catalog
   with those canonical descriptors, and registry reads pin their canonical
   versions, so neither an old row nor a same-name custom version can replace
   or add a tool. `question_context` and `current_context` are deleted rather
   than retained as aliases.
3. Remove the recipe-era intent transport names with the old catalog. HTTP uses
   `GET /operations` and `POST /operations/{name}`; the SDK uses
   `list_operations`/`run_operation`; the CLI uses `remember operations
   list|run`; MCP renders the four operation names directly. `/recipes`,
   `/recipe/{name}`, recipe-named SDK/CLI methods, and transport aliases are
   absent. Saved-query endpoints keep their own names because they represent a
   different customer-owned registry.
4. Do not ship deprecation headers, adapter warnings, compatibility-call
   counters, removal-denominator telemetry, or a product gate whose only
   purpose is preserving/removing the 17 adapters.
5. The paid benchmark remains operator-invoked. Full-v9 through v13 are
   immutable historical evidence over their pinned catalogs; v14 is the
   D98-amended protocol identity but this design does not authorize running it. Any result
   informs quality, not compatibility permission.

Exactly four platform operations remain. Removing those intentional one-call
contracts is not decided here; it remains the explicit product-quality
alternative in §10.

## 9. Validation gates

Each implementation batch passes its relevant gates before merge; all gates
for the shipping surface pass before release.

1. **DDL/manifest identity.** Per-request runtime introspection equals the
   manifest's PostgreSQL major and public view interface (all 24 view names,
   ordered columns/types, and comments). The same-server deploy/CI comparator
   proves exact view/helper definition parity against a freshly migrated
   reference; all 24 view keys are unique on fixtures; all comments and SQL
   examples validate; pinned-AST-serializer golden vectors pass; two independent
   builds produce the same manifest hash, including across supported PostgreSQL
   minor versions. A separate graph-readiness gate proves the exact property-
   graph aliases, element views, composite keys, endpoints, labels, properties,
   property types, and privileges through PostgreSQL's semantic catalogs.
2. **D48 deletion matrix.** Deleting each fixture lineage, version,
   representation, claim, fact provenance, K target, P1 candidate, graph edge, and
   corpus body yields zero leaked rows/bytes across every live view, helper,
   semantic/lexical target, entity nomination, ID-addressed chunk-body fetch,
   core operation, shipped example, saved query, count, and artifact fetch. A
   path with one invalid edge returns no partial live path. Fixed-pattern SQL/PGQ
   and recursive-helper cells observe the deletion in the next statement; there
   is no copied graph or generation to purge separately. The targets × surfaces
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
5. **Sandbox/tenancy.** The §4.2 adversarial suite and 10,000-AST fuzz run produce
   zero cross-deployment/operator/base-object disclosures, zero state changes,
   and zero pool contamination under production roles. The suite proves that
   the login role can reference no other deployment's objects and no base
   table under any grant path, that server-owned SQL/PGQ and graph helpers derive
   the deployment only from the authenticated binding (a missing or mismatched
   binding performs no graph access), and that all properties survive A→B→A pool reuse. The
   SRF fuzz corpus also proves that post-rewrite invocation count equals exactly
   the count of accepted top-level syntactic forms. Cross-deployment graph-anchor,
   endpoint, deployment-parameter, reader-reuse, and malformed-parameter attempts
   expose zero data, schema, plan, identifier, or readiness metadata from the
   other deployment.
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
7. **PostgreSQL P1 authority and channel correctness.** On frozen candidate
   sets, every semantic and lexical sibling's eligible IDs equal the existing
   target-specific D48 authority views exactly. Frozen exact-term fixtures prove
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
   mixes configured policy or embedder attestation.
8. **Graph authority and work bounds.** Server-owned fixed one-hop SQL/PGQ
   and recursive helpers equal exhaustive ground truth on generated graphs
   through their declared caps, use deterministic ordering, obey both clocks,
   and never cross deployments. Simple-path shapes explicitly exclude repeated
   element UUIDs because PostgreSQL's supported fixed patterns otherwise use
   repeatable-element semantics. The recursive implementation expands one
   frontier level at a time, stops after the first target-bearing level for
   shortest-hop results, filters authority in the recursive term, and enforces
   hard depth, frontier, examined-edge, result-row, temp, and statement-time
   budgets. Tests cover invalid-short/valid-longer paths, cycles, parallel edges,
   dense hubs, direct/helper empty results, caller filters/joins/aggregates that
   remove every data row, the reserved status-only right-join carrier, alias
   collisions, no GUC/session state across A→B→A pool reuse, truncation honesty,
   exact PGQ/recursive parity for the under-budget overlapping one-hop shape,
   and separately disclosed zero-data PGQ versus deterministic-prefix helper
   results on over-budget hubs. A forced hash-join fixture proves that the
   rewrite preserves top-level `ORDER BY`, `LIMIT`, and `OFFSET`, including the
   status-only carrier and `LIMIT 0`. `EXPLAIN` acceptance proves every admitted PGQ template is
   deployment/anchor selective and uses indexed endpoint access rather than an
   unbounded full-graph materialization.
9. **SQL/PGQ catalog and conformance.** Fresh migration and repair are
   idempotent and converge to one exact graph definition. Tests cover every
   admitted PostgreSQL 19 construct: fixed concatenation, directed/either-
   direction edges, element and graph-pattern predicates, individual/disjoined
   labels, property references, expression properties, composite keys/endpoints,
   views as element tables, aliasing, and implicit outer-reference correlation
   by comma-joined `GRAPH_TABLE` (with explicit `LATERAL GRAPH_TABLE` tested as
   rejected). Negative tests
   pin the unsupported surface: path variables, TRAIL/SIMPLE/ACYCLIC, shortest
   paths, alternation, quantified paths/edges, non-local predicates, richer label
   expressions, element/path functions and aggregates, richer row/export modes,
   and inline-view element tables. Graph and underlying element-view privileges
   are tested independently under the production role.
10. **Result contracts.** Every success, empty, truncation, rejection, timeout,
   cancellation, and store failure contains the complete QueryResult header and
   correct grade, truncation/work-bound fields, and non-guarantee state. The
   three single-layer operations pass the existing D49 Envelope
   suite without weakened grain, negative, contradiction, freshness, or
   association guarantees; `answer_context` passes the `ContextBundle/v1`
   composition contract. Named honesty fixtures reproduce the reviewer's queries (b) and
   (d) across `facts_visible_history`, `facts_as_of`, and
   `graph_edges_visible_history`: after current testimony changes, historical/
   as-of membership stays correct while `evidence_count_current`,
   `contradict_count_current`, and `support_state_current` reflect the live read
   state and never claim to be historical values. The consumption skill and
   discovery warnings against claim-to-current and truncated-to-complete laundering
   are gate-tested; worked examples include the wrong claim-window query with
   its correct `facts_current` replacement, predicate-vocabulary discovery,
   full fact → `fact_claim_evidence_live` → `claims_live` → `documents_live`
   source audit, latest-contradicting-testimony divergence, and a bounded live
   graph-to-relational composition. Context-operation fixtures prove:
   `testimony_context` returns claims/chunks and never facts/entities;
   `fact_context` default-current membership equals `facts_current` at the same
   evaluation instant; each result
   carries the exact required `temporal_scope` variant and applied timestamps;
   its `at`,
   `overlap`, and `history` modes include the correct ended intervals without
   returning currently retracted facts; a future-starting fact is absent from
   `history` and present in the matching explicit `at`/`overlap` request. All modes retain both-stance,
   source-diverse associations/totals, contradiction co-members, the
   60-record fact-evidence budget, and the 30-fact ceiling. With no entity IDs,
   both operations preserve deployment-wide retrieval. With confirmed IDs,
   scoped P1 ranking returns a relevant anchor result deliberately placed below
   the unscoped global candidate depth; the same fixture covers chunks, claims,
   current facts, and a narrow historical fact window. An unknown, retired,
   forgotten, or foreign ID returns the same opaque `unknown_entity` shape with
   no partial results; malformed, duplicate, empty, or over-cap arrays fail
   `invalid_parameter`; aliases never get silently re-resolved. Multi-anchor
   coverage orders a two-anchor claim/chunk and a direct relation ahead of
   otherwise equivalent one-anchor results without excluding the latter.
   `answer_context`'s literal child envelopes, including `temporal_scope`, are
   field-for-field equivalent to
   direct default child calls under a frozen store, active P1 configuration, and clock; typed
   child negatives remain independent, while an execution failure proves that
   no half-bundle returns. The same fixtures prove the catalog
   and manifest roll atomically to four operations and contain no removed name
   or flag.
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
12. **Clean-cut catalog.** Fresh and previously seeded fixture databases expose
    exactly the four assured operation descriptors plus the seven open-query
    tools. The 17 demoted names plus the new citation-path example exist only
    under `examples.*`;
    `question_context` and `current_context` exist nowhere in the active
    catalog. No public alias, deprecated descriptor, compatibility counter, or
    stale active stock row survives. The single-layer operations pass their
    D49 and `memory_v1` membership-equivalence suites, and the composite passes
    exact child equivalence.
13. **Telemetry/retention.** Cost totals reconcile to PostgreSQL/P1-index
    counters within
    1%; kill switches stop new work within five seconds; default logs contain
    zero raw SQL, parameter values, rows, bodies, or private PostgreSQL errors;
    graph budget/truncation/readiness events emit their exact metrics; debug
    captures expire within seven days.

## 10. Recorded deferrals (each with its trigger)

| Deferred | Bound reason | Adoption trigger and required decision gate |
|---|---|---|
| `lexical_facts(query, k, filters)` | D94 stores fact embeddings on natural relation/observation rows but deliberately does not expand the admitted facts lexical surface. The binding P1 contract exposes semantic facts only. | Trigger: **a pg_textsearch BM25 index and scored fact-label lexical method are designed together**. Adoption requires frozen exact-term fixtures, the same analyzer/rank/score and same-statement authority contract as claims/chunks, current/at/overlap/history equivalence, the facts-filter allowlist, manifest enumeration, a `fact_context` descriptor/version roll adding lexical fusion, and same-change OSS docs. |
| Complete removal of the assured-operation layer | One-call typed defaults remain product value; this design does not decide their deletion | An open-only evaluation shows no material loss from removing the one-call fallback: overall success lower 95% bound ≥ -2 points versus hybrid, every critical category ≥ -5 points, zero added D41/D48/D54/security violations, median calls increase ≤1, and p95 latency/cost increase ≤20%. Removal requires a separate binding decision and, if consumers exist by then, a migration plan proportional to actual usage. |
| Public arbitrary SQL/PGQ | The pinned default-deny public SQL parser does not understand PostgreSQL 19 property-graph grammar, and string/token allowlisting would not be an acceptable isolation boundary. The current release therefore exposes only server-owned parameterized PGQ templates and typed/bounded graph functions. | Adopt only after the public AST gate parses the complete admitted PG19 grammar and rejects every unsupported/mutating form by structure. A separate binding proposal must define the admitted subset, tenancy rewrite, budgets, result contract, fuzz corpus, and manifest representation. |
| Pgvectorscale StreamingDiskANN as the default ANN index | D94 binds pgvector HNSW first; installing an additional index extension without a present need violates YAGNI | The open proposal may inform a later binding decision. D94 does not install, test, benchmark, or pre-authorize it; a future promotion changes only the private vector index, never the public query contract or storage boundary. |
| Media-segment public views/SRF | The binding media row/embedding contract is not yet part of this schema | First production corpus requiring SQL composition over D65 media segments; a separate design adds typed locators/derivation and rolls the schema/hash. |
| Saved-query marketplace and shared dependencies | Signing, supply-chain review, publisher liability, and fleet recall are outside customer-local registry v1 | First operator-approved cross-deployment sharing requirement, followed by a signing/install/revocation design and adversarial supply-chain suite. |
| Saved-query registry import/export | Portable registry bytes need a format, trust boundary, and compatibility rule that local registry v1 does not yet need | First customer migration between deployments requiring registry transfer. Adoption requires a versioned interchange format, explicit trust/signing model, and source/target manifest-hash pinning and compatibility validation before activation. |

## 11. Implementation sequence (binding)

1. **Batch A — schema contract:** machine-readable manifest, full DDL note,
   invariant views, comments, canonicalizer/hash, D41/D48/D54 tests.
2. **Batch B — safe execution:** roles/grants tenancy, parser/allowlists, limits,
   QueryResult, discovery, audit/cost controls, adversarial suite.
3. **Batch C — PostgreSQL-native P1 operations:** `semantic_claims`,
   `semantic_chunks`, `semantic_facts`, `semantic_entities`,
   `lexical_claims`, `lexical_chunks`, and `fetch_chunk_bodies`; shared P1-port
   paths, D80 current-attestation readiness, same-statement authority joins, body
   verification, rank/score and drop disclosure, caps, cancellation/telemetry,
   manifest signatures, and per-surface OSS pages. `lexical_facts` remains only
   the §10 recorded alternative.
4. **Batch D — live graph:** deployment-keyed invariant vertex/edge views,
   exact PostgreSQL 19 property-graph DDL and grants, semantic-catalog readiness,
   server-owned fixed one-hop SQL/PGQ templates, and level-at-a-time bounded
   recursive helpers. It includes endpoint indexes; depth/frontier/examined-edge/
   result/temp/time budgets; deterministic shortest-hop behavior; explicit
   repeated-element exclusion where required; truncation telemetry; and the
   PGQ/recursive parity, tenancy, catalog, privilege, fault, and cap proofs in
   §9.1–§9.10. P1/PostgreSQL remain the authorities inside
   `testimony_context` and `fact_context`.
5. **Batch E — customer space:** immutable registry, governance, all 18
   `examples.*` mappings (17 demoted plus citation path), drift and deletion behavior.
6. **Batch F — integrated surface:** API/SDK/CLI/MCP additions, exactly four
   assured operation descriptors, consumption-skill/docs rewrite led by the
   verbatim two-layer headline and all four bound facts-layer examples. The 17
   demoted patterns ship only as `examples.*`; no automatic paid run or
   deprecation telemetry is part of delivery.

A later batch cannot merge around a failed dependency gate. Each behavioral
change ships with same-change OSS documentation, manifest/version updates, and
the relevant §9 evidence. Batch F integrates and leads with the rewrite; it
does not substitute for the per-surface documentation in earlier batches.

Batch D depends on Batches A and B and has no Batch C dependency. Assured context
operations depend on Batch C because they reuse the shared PostgreSQL D48/D41
authority and P1 nomination/body machinery; they cannot merge around a failed
Batch C confirmation gate.
