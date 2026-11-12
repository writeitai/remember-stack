# Shared retrieval eligibility and candidate-driven provenance

Status: proposed design, awaiting the owner's requested Claude Opus review.
No runtime change is authorized by this document alone. The owner authorized
implementation after that review approves the design. This is a candidate
amendment to D142/D149, not a replacement of their eligibility or ranking rules.

## 1. Problem, evidence and intended result

Retrieval must return evidence that satisfies its source, identity and time
rules. Today some SQL recalculates the same entity's source visibility for
every candidate. Even a single visibility lookup can expand into excessive
source-coordinate work. A finite search deadline consequently returns an
incomplete answer on a small, otherwise healthy store.

The [analysis and measured plans](../analysis/retrieval_filtering_reliability_20261112.md)
reproduce this on two retained LoCoMo stores with no model calls. Native
16-claim hydration takes 15.35 seconds and executes entity visibility 16 times;
a SQL-only shared-coverage prototype returns identical complete values in
1.02 seconds. It loads 256 claims in 1.16/1.17 seconds on the two stores.
Scoped fact nomination and a 36-ID unscoped fact confirmation batch also
exceed 20 seconds. Their failures are not evidence of an infinite refill loop.

The change removes repeated eligibility work and keeps selective predicates
attached to their candidate IDs in the actual PostgreSQL execution plan.
It preserves what qualifies, what ranks, what is counted and what an early
stop means. It must work for arbitrary projects and data sizes; no question,
person, conversation, keyword, gold answer or benchmark exception enters SQL.

## 2. Scope and invariants

This design covers current-identity entity visibility, scoped claim/chunk
coverage, fact nomination/confirmation/hydration that uses the existing
visible-fact authority, and their shared SQL definitions. The engine remains
a single-deployment memory library. No cloud-owned truth or provider reranker
is introduced.

Every read retains:

- deployment ownership and the active model/policy generation;
- deletion, live-lineage, version, ready-reading and coordinate eligibility;
- survivor/merge identity, active status and the latest unsuperseded resolution
  decision ordered by decision timestamp and ID;
- D140 selected-version/occurrence and fact-evidence gates;
- world-time and belief-time predicates, unknown/open date distinctions,
  superseded facts and the current-versus-reconstructed identity regime;
- D54 support/contradiction counts and zero-support behavior;
- distinct requested-entity coverage, deterministic score/ID ordering,
  nomination limits, hydration drops and explicit truncation/negative results.

An entity may remain visible through an older version of a live lineage even
when its current mention count is zero. The document-entity bridge remains an
independent source of visibility. Claims do not acquire semantic supersession.
Neither a stored vector nor an active flag proves complete source eligibility.

An existing unsupported query combination keeps its current explicit error;
optimization must not silently implement it using a different time regime.

## 3. Read lifetime and sharing boundary

One operation retains D142's admitted read-only repeatable-read snapshot,
deadline and pinned evaluation instant. Nested reads join it. An eligibility
result is reusable only inside that operation and under exactly the same
deployment, entity/identity regime and time parameters.

Use SQL materialization for **distinct bounded input IDs and shared membership
results**, not complete project exports. SQL statement batches remain bounded
by the existing hydration size. Within each statement a distinct endpoint's
visibility is evaluated once and reused across all matching candidates. A
statement that follows may repeat that bounded calculation: this design does
not promise a persistent temporary table or exactly one lookup across every
statement in a multi-batch operation. The underlying lookup must therefore
also be candidate-driven.

There is no process-wide cache, TTL, cross-request memoization, temporary
write table or new persistent visibility projection. No cached proof is
passed into an unrelated standalone operation. Shared source eligibility is
a SQL relation of IDs, not an untyped application-side boolean that lets a
caller skip checks.

## 4. One maintained source of eligibility

### 4.1 Entity provenance: constrain the existing probe chain

The current D149 definition already walks reverse merge members, starts from
unsuperseded decisions for those members, and checks the latest decision per
mention. These semantics are retained. The defect is physical: the captured
`conv26/scoped-claims_and_sources_context/plan-004.json.gz` joins 3,701 decisions
to 19 versions (70,301 pairs), probes chunk partitions by document for every
pair, and joins the mention's own chunk last. Its document-bridge alternative
also builds a hashed set from every live document. No corrected entity-lookup
prototype has been measured yet; this design's probe hypothesis must pass the
actual-plan gate, or return to design review.

For one terminal entity, retain reverse merge enumeration but enforce the
following correlated chain. Each stage projects only the coordinates needed
by the next, and uses a lookup boundary where needed to prevent reordering:

| Probe | Key and existing access path |
| --- | --- |
| Terminal and reverse members | `entities` primary/unique entity keys; `ix_entities_redirect (merged_into)` |
| Member's candidate decisions | `ix_resdec_entity_live (deployment_id, entity_id, mention_id) WHERE superseded_by IS NULL` |
| Latest decision for that mention | `ix_resdec_live (mention_id) WHERE superseded_by IS NULL`, ordered among **all** matching decisions by `decided_at DESC, decision_id DESC`; not restricted to the candidate member |
| Decision's mention | Partition primary-key prefix `mention_id`; check deployment |
| Mention's chunk | Partition primary-key prefix `chunk_id`; check the mention's deployment and `doc_id` before other joins |
| Chunk's version | `document_versions` primary key `version_id`, through the existing visible-version relation with deployment/document checks |
| Recorded reading | `document_representations` primary key `representation_id`, checked against the chunk's version/deployment and `status=ready` |
| Independent document bridge | Correlated per-member probe through `ix_documents_entity (document_entity_id)`, plus deployment and nondeleted document; no all-document hashed scan |

The mention/chunk lookups must precede the broader version/reading joins.
An entity qualifies through **any ready reading of any nondeleted version of
its live lineage**, including a replaced reading that remains ready. Do not
substitute `chunks_live`, `chunks_all_versions_live`, current content or the
version's current reading for this entity-provenance rule. The latest decision
is chosen across all unsuperseded decisions of the mention, including a later
decision that moved it to a non-member. A document bridge still qualifies
independently without inventing a ready-version requirement for that bridge.

Current mention/document counts remain separate, demand-prunable current-content
aggregates. Visibility with zero current counts remains valid. Missing,
inactive, merged-away and provenance-free requested entities remain absent;
merge cycles/dangling redirects and deployment boundaries keep their existing
meaning, without an invented recursion-depth cap.

#### Partition cost is explicit, not claimed away

Mention/chunk UUID probes lack the partition timestamp. Their primary keys
include `created_at`; decisions are partitioned on `decided_at`. These lookups
can visit each retained partition. This design intentionally **accepts linear
partition-probe overhead** and does not introduce a new global UUID locator,
writer-maintained routing state or partition/reuse-key migration to eliminate
it. It must not be described as constant work independent of partition count.

The forbidden amplification is scanning unrelated rows inside those partitions
or multiplying candidate decisions by unrelated versions/documents/chunks.
Measure and report planning time and per-partition point probes as partitions
grow. Existing finite operation deadlines remain the reliability boundary;
excessive partition fanout is an explicit measured limitation, not an empty
successful search or a reason to relax visibility. This is a deliberate scope
choice for this fix and must be accepted in review.

### 4.2 Claims and chunks: calculate coverage as a set

Use one shared typed SQL builder for claim-ID and chunk-ID coverage. Its
variable coordinate is an internal allowlisted choice, not user-provided SQL.
For a statement's candidate IDs and requested entity IDs:

1. Materialize the distinct requested entities that pass the authoritative
   current-entity visibility relation for the operation's deployment.
2. Read `v_memory_mention_current_content`, restricted to that deployment and
   candidate claim/chunk IDs, and join its survivor IDs to the visible set.
3. Group by candidate ID and count distinct requested survivor entity IDs.
4. Join positive coverage to the existing claim/chunk hydration query. Keep
   its existing testimony, source-coordinate, selected-version and occurrence
   predicates before any result cut.

This factors the existing `mentions_live` semantics: it is current-content
mentions left-joined to visible survivor entities; an explicit resolved-entity
filter turns the nullable join into the corresponding positive membership.
Unresolved mentions and mentions of another entity still do not give coverage.
Duplicated mentions, redirect members and repeated requested IDs do not inflate
coverage. The claims and chunks paths share this definition, including D140's
scoped statement builder. Unscoped queries do not pay for this CTE.

The existing current-content projection remains the source-coordinate
authority. Do not copy its deleted-source/reading predicates into a second
approximate query. Historical-identity reconstruction continues to use its
own existing authority; a current-entity CTE cannot substitute for it.

#### Access paths and the missing chunk index

Claim coverage starts through `ix_mentions_claim (claim_id)`, filtered by
deployment. Chunk coverage gets a new parent B-tree
`ix_mentions_deployment_chunk (deployment_id, chunk_id)` with matching leaf
indexes, so a selected chunk does not scan all mentions of a long document.
Both still incur the explicit partition fanout above. Reading the existing
current-content projection must retain its nullable claim-ID gate: a mention
whose claim is not visible contributes no claim coverage.

Build the new nonunique index without a corpus-wide write outage: create a
parent index on `ONLY mentions`, build matching indexes concurrently on its
leaves/default partition, then attach them and verify parent/leaf validity.
New partitions inherit the parent index. Use a database-scoped **session**
advisory migration lock in the native Alembic runner, acquired before reading
revision state and held across autocommit index-build phases. Current
`migrations/env.py` has no such lock, so it is explicitly part of this change,
not assumed existing. Ordinary reads/ingestion do not take that lock.

Concurrent builds run outside transaction blocks; final view/manifest-related
DDL and the revision update run normally after index qualification. Under the
same native migration lock, an interrupted own-named invalid leaf index is
dropped concurrently and rebuilt; an existing valid index is reused only if
its table/key/definition matches exactly. A foreign or mismatched object fails
setup. Re-enumerate partitions before declaring the parent valid and serialize
the final attachment metadata step with native DDL locking. Do not start
serving a new schema revision while this migration remains incomplete. This
is forward completion of one index build, not a release recovery framework.

The new index adds one B-tree entry per mention, build I/O/WAL and ongoing
write/storage cost. Record index bytes, WAL/build duration and peak resources;
no zero-cost or customer-margin claim is made. Build/index lock qualification
is required on populated data. The PostgreSQL procedure is supported by
[CREATE INDEX](https://www.postgresql.org/docs/19/sql-createindex.html), retrieved
2026-11-12; online parent creation itself is not supported, hence leaf builds.

### 4.3 Facts: one source-qualified projection, one endpoint rule

Add the private view `public.v_memory_fact_source_qualified`. Its exact ordered
columns are:

```text
deployment_id, fact_kind, fact_id, subject_entity_id, predicate,
object_entity_id, statement, fact_label, valid_from, valid_until,
ingested_at, invalidated_at, contradiction_group, confidence,
evidence_count_current, contradict_count_current, support_state_current,
valid_precision
```

Its per-kind `UNION ALL` arms retain the current raw relation/observation
fields, **inner joins** to `v_memory_entity_survivor`, and the existing exact
historical claim-coordinate evidence predicate. Cyclic/dangling survivor chains
therefore do not create source-qualified rows. Precision is the same raw fact
precision cast to text. The existing D54 count expressions consume only
`v_memory_evidence_lineage_live` through a demand-prunable left lateral aggregate;
current support state uses the existing review-queue expression. No source
eligibility is inferred from counts. This view does **not** yet assert that
its survivor endpoints pass `entities_current`.

Redefine `v_memory_fact_visible` on this helper plus exactly this endpoint rule:
subject must match `entities_current` by deployment and survivor ID; for a
relation the object must match too, while an observation needs no object.
Keep the existing first fourteen columns and append the four presentation
columns listed above. Rewrite `memory_v1.facts_visible_history` as an explicit
projection of these eighteen columns, removing its superseded inline count/
precision/support expressions. This is the only fact presentation source.
Audit private consumers of whole-row/`*` projections and pin their prior column
sets where extra columns would change a public row or JSON payload. This is
not permission to expose a changed public column set.

The runtime builder reads only the source-qualified helper and authoritative
`entities_current`. Its **only independently assembled eligibility rule** is
the endpoint conjunction above. That small rule is pinned structurally and by
complete-row tests against the immutable migration's wrapper. All historical
source predicates, presentation and count arithmetic live in the helper;
runtime code must not copy them or rewrite DDL strings. This resolves the
migration/runtime boundary: migrations retain literal immutable definitions,
while the tested runtime builder assembles a bounded equivalent of the one
endpoint conjunction. A change to that conjunction requires a new migration
and updated parity tests, never editing an old migration.

For a native statement, materialize source-qualified **candidate IDs and
required endpoint/ranking/time fields only** through per-ID constrained
lookups. Materialize distinct subject/object survivor IDs. Drive correlated
`entities_current` point lookups from that endpoint set, with a lookup boundary,
and materialize their membership results. A plain unbounded `IN` join that
reorders into a deployment-wide entity export is insufficient. Join the
visible endpoint set to all candidate facts. A relation whose endpoints are
equal has one lookup but retains distinct-entity coverage; either missing
endpoint excludes it. Hydration obtains presentation/count fields only for
eligible candidates. No `SELECT *` forces counts into nomination materialization.

The D140 `fact_in_scope` supporting-evidence/time predicate stays **per fact**
and before limits. It is never shared merely because facts share an endpoint.
Reconstructed belief/identity queries keep their own existing authority and
may share only proofs from the same regime.

#### Consumers and exact scope

| Consumer | Required integration |
| --- | --- |
| `facts_visible_history` | Explicit eighteen-column projection of the full authority; same rows, types, counts and values |
| `fact_claim_evidence_live`, `evidence_lineage`, `v_memory_page_citation_visible`, `changes_visible` | Keep complete membership through `v_memory_fact_visible`; audit whole-row projections, preserve all public values and do not add a second source/endpoint truth query |
| `_FACT_AUTHORITY_JOIN`: scoped nomination, fact loop and scored fact search | Replace per-row endpoint checks with the shared bounded builder, preserving kind/time/evidence/model/filter/coverage predicates |
| `_CONFIRM_FACTS_CONTEXT_BY_KIND` | Same bounded builder for full eligible hydration and original nomination ranks |
| `_FACTS_CONTEXT_CONTRADICTION_MEMBERS` | Enumerate matching group fact IDs with current time predicates, then shared endpoint qualification; exact existing group count/member semantics, same deadline, no invented group cap |
| `graph_queries.py`, SQL sandbox `nomination.py` | Preserve complete public authority for graph traversal/sandbox SQL; use shared builder where they call bounded native fact nomination. No private helper becomes caller-reachable |

Register the new helper in `AUTHORIZATION_HELPER_VIEWS`, revoke PUBLIC and all
query-role grants, and assign `rememberstack_view_owner`. Its AST becomes a
hashed dependency; it is absent from public discovery/reachability.

There is no successful fact prototype yet. A scope below the unchanged 50,000
raw-row budget may still have expensive source qualification or many distinct
endpoints. It must finish within the configured operation deadline or return
the existing explicit deadline boundary. This change neither recalibrates
that budget nor claims it bounds every physical row access. The worst case
is a required measured test; unresolved fact deadlines fail acceptance.

## 5. Filtering and ranking paths

Keep the existing hybrid strategy, with shared eligibility on each path:

- **Bounded scoped searches:** enumerate their complete bounded raw candidate
  scope through relational predicates, apply full source/endpoint eligibility,
  then rank by the existing exact vector/lexical score and coverage order.
  Relations can obtain endpoint matches through separate indexed subject and
  object branches with ID deduplication; same-subject/object does not double
  count. Scope work beyond the existing bound returns an explicit boundary.
- **Broad searches:** retain D142's attested HNSW/BM25 nomination cursor,
  applicable scalar/membership `WHERE` filters, disjoint fetches, canonical
  score rules, tie handling and unchanged finite limits. Confirm each batch
  against the same eligibility contract, sharing endpoint work within it.
- **Final hydration:** use the candidate-ID constrained, fully eligible row
  source and retain every returned count, source, clock and coverage value.

All eligibility predicates apply before the eligible top-k is chosen. Do not
return the first k raw neighbors and filter afterward. Do not add an unbounded
exact fallback, retry loop or second scoring generation to hide a short result.
ANN recall remains approximate; `k_filled` does not prove global exact vector
top-k. Existing early-stop reasons remain visible and are not converted into
`known_empty`. Rewriting index/continuation policy, changing its numerical
thresholds or adding a new public exact-search mode is outside this change.

## 6. Implementation contracts and migration

- Add simple typed SQL builders at the engine-owned core/spine boundary.
  Source/presentation authority is the new private helper; only the small
  endpoint conjunction is assembled at runtime and pinned to the migration.
  Variable table/column fragments are internal enums/literals. Every SQL path
  binds deployment and IDs normally.
- Replace the per-row claim/chunk coverage aggregates and obsolete callers
  in the same change. Keep no feature-flagged old SQL path or hidden fallback.
- Install private fact projection/wrapper and entity-provenance view changes
  through a new Alembic migration. Historical migration files remain immutable;
  the working runtime/source manifest has one current authority definition.
- Keep private helper ACLs/ownership consistent with the existing query-space
  design: not part of `memory_v1`, no grant to PUBLIC or the query role, and no
  new route around the public SQL sandbox's reachability checks.
- Register changed authored DDL in query-space source definitions, regenerate
  the canonical manifest/deletion matrix where affected and exercise normal
  saved-query revalidation. Public columns/types remain stable, but a changed
  public view AST is a real query-space identity change, not an unchanged pin.
- Update current LoCoMo surface pins if their qualified manifest changes;
  preserve frozen historical profiles and results. New validation/answer runs
  record their actual source and execution identity; do not edit old fingerprints.

No converter, structurer, extractor, resolver, normalizer or embedding generation
changes. No new paid-product key, source identity or source-origin input.
Existing documents, claims, facts and vectors are reused without ingestion.
D56 remains unchanged. A schema/read-path migration does not authorize a
paid rebuild or relabeling existing ingestion metadata.

## 7. Failure, security and operational behavior

All reads remain bounded by configured pool admission and operation deadlines.
Per-ID lookup boundaries must not disable timeout/cancellation propagation.
If a scope exceeds its finite bound or SQL cannot complete, return the existing
explicit incomplete boundary. Source/public view predicates are never weakened
to turn a timeout into a successful-looking result.

Materialized membership is private to the statement/snapshot. A later request
sees deletion, merge, reading or evidence changes through its new snapshot;
there is no invalidation service or stale cross-request cache. Unsupported
historical combinations remain errors. A migration failure stops setup under
the existing native locking contract. No rollback orchestration, retry trigger,
new recovery coordinator or automatic DLQ reset is introduced.

Native full-store snapshots and original answers remain protected before
operational validation. Prototype and production results have separate
provenance. The unrelated partial-ingestion failure in the active campaign
must not be relabeled a retrieval failure or silently marked ready.

## 8. Economics and alternatives

No new model/provider calls, persistent search copy or per-document paid work
is introduced. Request-local ID/coverage materialization uses PostgreSQL memory
and may spill; keep projected fields narrow and inspect temp block usage.
Work grows with actual bounded scope/provenance, not just requested k.
Exact ranking 50,000 1,536-dimensional vectors still examines 76.8 million
components. Do not claim it is free or make all-project exact scans the default.
No customer price or cost saving is established by the current single-reader
measurements; eight-reader CPU/memory measurements qualify the resource effect.

Rejected alternatives:

- Removing source/entity validation changes which data is visible.
- Adding `WHERE` text alone leaves view expansion/join ordering unbounded.
- Per-ID fences alone did not fix the measured repeated-entity cases.
- Materializing every eligible project row recreates full-corpus work.
- Persistent activity flags would require a new atomic propagation contract
  for deletion, versions and merges; this evidence does not justify that state.
- A larger deadline masks repeated work, and another model/reranker does not
  repair SQL eligibility execution.

## 9. Acceptance and approval gates

Before runtime implementation, Claude Opus must approve this concrete design
or its reviewed revision. Store review input/output and findings in the PR.
Implementation must then satisfy all of these gates before merge:

1. **Independent complete oracle:** retain pre-change definitions from
   `p9_45_0066`, `p9_04_0025` and `p9_14_0035`, plus copies of pre-change native
   statements, **in test code only**. Install them in an isolated test-only
   schema or a dedicated pre-migration database with equivalent names and
   complete dependency closure, using identical seeded/cloned data. Never
   compare a new view against itself or keep an old runtime fallback. Compare
   all columns/counts on deterministic randomized fixtures and complete
   retained-store candidate sets on isolated copies. The protected original
   stores remain read-only. A long reference may use a separately recorded
   longer bound or stable batches; a truncated oracle proves no full equality.
2. **Visibility/time matrix:** current and superseded testimony, selected dated
   occurrences, changed readings, deleted lineages, forgotten/merged identities,
   older-version-only provenance, document-entity bridges, duplicate mentions,
   missing entity endpoints, self-relations, unknown/open windows, support
   withdrawal and contradiction-only facts. Include merge cycles, dangling
   redirects and un-merges; two deployments and a redirect through another
   deployment; multiple unsuperseded decisions with timestamp/ID ties and a
   latest decision moving to a non-member; mismatched mention/chunk document
   IDs; unready and replaced-but-ready readings; merged-away/inactive/invisible
   requested IDs; nullable claim IDs caused by invisible claims; non-current
   chunk versions; and periodised claims whose origin version was deleted.
   Test reconstructed-time paths and unsupported-combination errors as well.
3. **Actual-plan work:** coverage and endpoint visibility do not run once per
   candidate sharing an entity; coordinate lookups stay tied to their input
   IDs. Add a large document with many mentions in other chunks, large
   unrelated entity/chunk/version sets, millions-of-document bridge strata,
   many partitions, and entities whose early decisions reference invisible
   versions. Unrelated row counts must not multiply probe work. Report the
   accepted linear partition-probe/planning overhead separately; do not claim
   partition-independent work. Verify unrequested counts are absent from
   identity/ID plans. Assert work/cardinality bounds, not a planner node name.
4. **Full native operations:** both affected fact and claims/chunks operations
   finish without a deadline boundary on retained stores under the original
   production budget, with frozen-vector SQL comparison under a stricter
   recorded 20-second diagnostic bound. An unresolved fact path means the
   retrieval fix is incomplete, even if claim coverage improves.
5. **Concurrency and failure:** eight simultaneous reads, source changes
   between snapshots, deletion/merge after one read begins, admission saturation,
   timeout/cancellation, no leaked connections or stale proofs, unchanged
   early-stop accounting and no second nomination scoring generation.
6. **Integration/provenance:** migration, manifest and ACL/reachability checks,
   normal saved-query revalidation, relevant PostgreSQL/surface/adapter suites,
   required CI, zero provider calls during SQL qualification and no ingestion.
   Qualify the migration session lock across autocommit, populated leaf-index
   build/attach, invalid own-index interruption, mismatched-object rejection
   and new-partition index inheritance. Publish actual limits and risks.

A full LoCoMo answer/judge replay can follow the merged, qualified fix using
the retained stores and original model/prompt policy, with separately saved
answers and verified GCS backups. It cannot be used in place of eligibility
or concurrency qualification.

## 10. Adoption and current binding corpus

The proposal becomes binding only after the requested Opus review approves
its revision. Before runtime work, move this file to
`plan/designs/shared_retrieval_eligibility_design.md`, update all inbound links,
and record D153 (accepted design, implementation qualification still pending).
In that same checkpoint reconcile:

- `postgres_p1_search_projection_design.md` §8.3–8.5 and a shared-eligibility
  subsection: full authority may be assembled from the canonical private source
  and tested endpoint rule, with current ranking/continuation semantics intact;
- `entity_identity_and_retrieval_design.md` §7: current entity provenance probe
  semantics, any-ready-reading rule, set-based coverage and explicit partition
  costs;
- `open_query_space_design.md` §3.3 and §6: private helper authority/ACL,
  full public row parity and manifest/saved-query identity consequences;
- an amendment inside D149 identifying its changed physical execution contract
  and linking D153; its snapshot, embedding reuse and eligibility rules survive.

Accepted design is not evidence that implementation ships. Delivery sequencing
and measured completion belong in `plan/plans/` and PR validation, not promises
of unqualified runtime behavior in the binding design. Absolute throughput,
eight-reader resource measurements and temporary spill usage are recorded
observations; semantic, structural-work, cancellation/isolation, native-query
completion and integration gates are mandatory.
