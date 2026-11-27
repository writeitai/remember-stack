# Shared retrieval eligibility and candidate-driven provenance

Status: accepted design (D153); implementation/qualification status and exact
evidence are recorded in [the delivery plan](../plans/shared_retrieval_eligibility.md). Claude Opus 5.5 HIGH approved the contract at
`431900b1d` with zero remaining blockers; [review and execution identity](../analysis/retrieval_eligibility_reviews_20261112/opus-r3-review.md).
The owner authorized implementation after this review. The ordered bridge
amendment was approved at `68e156154` ([Opus review](https://github.com/writeitai/remember-stack/pull/587#issuecomment-6830637190));
its [analysis and rejected UUID-order plans](../analysis/ordered_document_bridge_20261112.md)
record why the existing document index must change. This amends D142/D149's
physical execution and D23's unsupported UUID-derived pruning assumption;
eligibility, ranking, finite budgets and D56 reuse semantics remain intact.

> **Proposed amendment (D163, 2026-11-27).**
> §4.3 and §9.1 specify evidence-first fact probes and stored-endpoint survivor
> lookups. The approvals above cover D153. [D163](../../decisions.md#d163-drive-fact-eligibility-from-its-own-evidence) · [Hosted plan evidence](../analysis/evidence_driven_fact_eligibility_20261127.md)

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
also builds a hashed set from every live document. The corrected probe must
pass the actual-plan gates; an equality predicate alone is not proof of
bounded work. The failed document-bridge hypothesis returned to design review
and produced the ordered replacement below.

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
| Independent document bridge | Per-member/deployment probe through the ordered `ix_documents_entity` definition below, with the nondeleted-document predicate; no all-document hashed or competing deployment-wide ordered scan |

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

#### Ordered document bridge and its online index replacement

A missing bridge must not scan unrelated documents. Under skew, an equality
predicate with `OFFSET 0` can still choose a sequential scan. An ordinary
UUID `ORDER BY doc_id LIMIT 1` can instead choose the existing deployment/UUID
unique index, scanning unrelated member IDs while expecting an early match.
Measured million-document fixtures demonstrated both failures. Widen the
existing `ix_documents_entity`, retaining one serving index, to:

```sql
CREATE INDEX ix_documents_entity ON public.documents
  (document_entity_id, deployment_id,
   ((doc_id::text) COLLATE pg_catalog."C"))
  WHERE document_entity_id IS NOT NULL;
```

The correlated membership probe keeps all three predicates (deployment,
member and `deleted_at IS NULL`) and uses `ORDER BY document.doc_id::text
COLLATE pg_catalog."C" LIMIT 1 OFFSET 0`. This order selects any live bridge;
it is not relevance, temporal or identity order. Only this native expression
index supplies it without sorting. `OFFSET 0` prevents PostgreSQL from
simplifying EXISTS and discarding the ordering. Fixed C collation gives byte
order independent of the database default or external ICU/glibc collation
versions. A UUID's canonical text is always bounded to 36 characters. No
user value, public row, scoring input or reuse key changes.

Tombstoned documents remain indexed: entity FK cleanup must find them too.
Do not add `deleted_at IS NULL` to the index predicate. A member with only
tombstoned bridges may scan its own history; qualify that cost separately
from unrelated-row amplification and retain the operation deadline. The
current source does not actively assign this compatibility bridge (D134 §7),
but historical/custom stores may populate it. Qualification must include
populated data, multiple deployments, absent/common/distinct keys and generic
prepared plans, using only the final serving index. This is actual-work
qualification of the supported planner, not a promise about future planners
or operator-added competing expression indexes.

The unreleased index revision builds only `ix_documents_entity_d153` as the
replacement, concurrently under the native session migration lock. Shape A is
the exact original one-UUID-key index; shape B is the definition above. Both
are public.documents ordinary nonunique/nonexclusion B-trees, with precisely
the stated key order, predicate and no INCLUDE columns. Require pg_catalog
UUID opclasses on UUID keys, pg_catalog text_ops on the expression, zero order
options, no expression in A and exactly UUID-to-text in B. Only B's third key
has pg_catalog C collation. A usable index has indisvalid, indisready and
indislive true. Any differently shaped/bound object at either fixed name
fails setup without being removed.

| Serving name | Staging name | Native action |
| --- | --- | --- |
| Valid source | absent | Build exact destination concurrently |
| Valid source | invalid exact destination | Drop/rebuild only own invalid staging index |
| Valid source | valid destination | Drop source concurrently, rename staging |
| Invalid/not-ready source | valid destination | Finish interrupted concurrent drop, rename staging |
| Invalid/not-ready source | absent or invalid | Fail; no validated replacement |
| absent | valid destination | Finish rename after committed drop |
| absent | absent or invalid | Fail; no validated replacement |
| Valid destination | absent | Completed replacement; continue revision stamp |
| Destination invalid or unexpected staging beside destination | any | Fail for inspection |
| Foreign/mismatched object | any | Fail without adopting/removing it |

Recheck the exact ready replacement before drop and rename. Upgrade uses A→B;
native downgrade uses B→A with the same fixed staging name, restoring the old
physical schema. A cancelled concurrent drop may leave the source invalid or
not ready; a cancelled create leaves the own staging index invalid. These are
native DDL completion states, not a journal or release recovery framework.
Each revision commits before the subsequent ordinary view revision can wait
on readers. Reject leftover staging objects and a wrong serving shape in the
current catalog contract. Previously built unmerged prototype stores are
recreated from protected originals when unreleased migration text changes;
never stamp edited SQL over them.

The index increases storage and write/build I/O. In a highly duplicated
million-document fixture its measured size is 95,600,640 bytes versus
6,504,448 bytes for the old index; ordinary distinct-key costs must also be
recorded. Both coexist temporarily during replacement, adding peak disk/WAL.
Measure rather than claiming a universal per-document cost or free capacity;
D152 native allocation capture includes the actual resulting forks.

#### Partition cost is explicit, not claimed away

Mention/chunk UUID probes lack the partition timestamp. Their primary keys
include `created_at`; decisions are partitioned on `decided_at`. These lookups
can visit each retained partition. This design intentionally **accepts linear
partition-probe overhead** and does not introduce a new global UUID locator,
writer-maintained routing state or partition/reuse-key migration to eliminate
it. It must not be described as constant work independent of partition count.
D23 §12's UUIDv7-derived time-bound pruning is not implemented: current writers
mint opaque `uuid4` values. This design does not rely on that promised routing
mechanism and amends the binding schema design accordingly. Never add a guessed
or correlated timestamp bound to an opaque-ID authority lookup: it can silently
exclude valid evidence. There is no ID-minting or timestamp-column change.

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
scoped statement builder. Explicit claim `entity_id` equality filters use the
same visible-entity/mention contract; equality predicates stay conjunctive,
and only requested ranking anchors contribute to coverage. Unscoped queries
without an entity filter do not pay for this CTE.

The existing current-content projection remains the source-coordinate
authority. Do not copy its deleted-source/reading predicates into a second
approximate query. Historical-identity reconstruction continues to use its
own existing authority; a current-entity CTE cannot substitute for it.

#### Entity-driven mode: nomination and its budget use the same relation

Coverage hydration begins from candidate claim/chunk IDs. Scoped search and
nomination also need the inverse direction: begin from requested entities,
without evaluating every current mention in the deployment. Its relation is:

1. Requested visible survivor IDs under the operation's current-identity scope.
2. Their reverse merge members through the existing `_SURVIVOR_SCOPE_BODY`.
3. Candidate decisions through `ix_resdec_entity_live`.
4. Latest unsuperseded decision across **all** decisions of each mention through
   `ix_resdec_live`, including decisions moved to another entity.
5. The decision's own mention by its partition primary-key prefix.
6. That mention's current-content coordinate via a correlated, mention-ID-bound
   lookup of the existing canonical current-content projection: correct chunk,
   document/version and current ready reading, with its nullable visible-claim
   gate preserved. This is distinct from §4.1's any-ready-reading entity gate.
7. Group by the claim or chunk coordinate and count distinct requested survivors.

`_run_testimony`, `nominate_testimony_scored`, `_entity_tiers` and the scope
budget helper must consume this same entity-driven relation. The budget counts
its qualifying mention rows, before grouping, not a different approximation.
A `LIMIT budget+1` on a deployment-wide `mentions_live` filter is insufficient.
The inverse relation must equal that public filter in the independent oracle.

D140's **pending-lineage counterfactual probe** is a different content regime,
not answer eligibility. When its explicit `pending_doc_ids` input is present,
it must still discover an entity mentioned only in an older readable edition
while the edition now in force is converting. Reuse the same requested-survivor,
reverse-member, latest-decision and own-mention probes, but bind that mention's
chunk through `chunks_all_versions_live` (the version's current ready reading).
Keep the existing pending-lineage/time predicates and mention-row budget in
that same statement. This can report pending freshness metadata; it never makes
the older text eligible as a current answer. Ordinary nomination and hydration
retain current-content coverage. The existing D140 three-edition pending test
is a required regression gate.

Filtering early decisions/current coordinates may still examine substantial
**requested-entity provenance**, including invisible early decisions. The row
budget is not a bound on every physical access; the existing deadline remains
explicit. Unrelated current mentions must not multiply work. Claim-ID hydration
still excludes nullable claim coordinates; chunk coverage retains a qualifying
mention even if its claim coordinate is null, matching today's semantics.

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
New partitions inherit the parent index. The native Alembic runner acquires
a database-scoped **session** advisory migration lock before reading revision
state and holds it across autocommit index-build phases. Ordinary reads and
ingestion do not take that lock.

Concurrent builds run outside transaction blocks; final view/manifest-related
DDL and the revision update run normally after index qualification. The native
runner commits each revision separately, releasing index-attachment metadata
locks before the following view revision can wait for readers. The runner
uses short lock waits and retries a contended revision with bounded backoff.
[The migration contract](packaging_distribution_design.md#61-migration-compatibility)
specifies the policy. Under the native migration lock, an interrupted
own-named invalid leaf index is
dropped concurrently and rebuilt; an existing valid index is reused only if
its table/key/definition matches exactly. A foreign or mismatched object fails
setup. Re-enumerate partitions before completing attachment and serialize
the final metadata step with native DDL locking. PostgreSQL marks the parent
valid when every partition has a matching attached index; verify this via
`pg_index.indisvalid` and the complete leaf inventory, rather than setting a
flag ourselves. Refuse offline `--sql` mode for this revision because its
catalog-driven concurrent leaf work cannot be emitted as a truthful offline
upgrade. Do not start
serving a new schema revision while this migration remains incomplete. This
is forward completion of one index build, not a release recovery framework.

The new index adds one B-tree entry per mention, build I/O/WAL and ongoing
write/storage cost. Record index bytes, WAL/build duration and peak resources;
no zero-cost or customer-margin claim is made. Build/index lock qualification
is required on populated data. The PostgreSQL procedure is supported by
[CREATE INDEX](https://www.postgresql.org/docs/18/sql-createindex.html), retrieved
2026-11-14; online parent creation itself is not supported, hence leaf builds.

### 4.3 Facts: one source-qualified projection, one endpoint rule

Use the private view `public.v_memory_fact_source_qualified`. Its exact ordered
columns are:

```text
deployment_id, fact_kind, fact_id, subject_entity_id, predicate,
object_entity_id, statement, fact_label, valid_from, valid_until,
ingested_at, invalidated_at, contradiction_group, confidence,
evidence_count_current, contradict_count_current, support_state_current,
valid_precision
```

Its per-kind `UNION ALL` arms retain the raw relation/observation fields.
For each stored endpoint, use an inner `LATERAL` lookup of
`v_memory_entity_survivor` with `OFFSET 0`. Bind the fact's deployment and
stored endpoint ID inside the lookup. A relation supplies its stored subject
and object and an observation supplies its subject. Those keys
must reach the seed of the existing cycle-safe forward merge walk. The
hosted plan applied the object filter after resolving all 969 entities for
each candidate. Binding it at the seed retains the same survivor, same
deployment checks and absent rows for missing, cyclic or dangling redirects.

Source membership follows each fact's evidence to its own claim coordinate.
Fence the lookup of `claims_visible_history` **UNION ALL**
`v_memory_claim_carried_periodised` by the evidence row's deployment, claim
and document IDs inside the correlated lookup. For the carried arm, probe
occurrences by that claim ID and then probe their chunk IDs. This retains the
membership predicates adopted by D153 from `p9_46_0067`, including either
stance and claims carried by a newer occurrence after loss of the origin.
Keep `v_memory_fact_claim_live`'s widened `p9_38_0059` definition for the
matching current-lineage/count authority. Precision is the raw fact precision
cast to text. The existing D54 count expressions consume only
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

The runtime source/presentation builder composes the source-qualified helper
with the one endpoint conjunction above. Pin that conjunction structurally
and by complete-row tests against the immutable migration's wrapper. The
helper owns historical source membership, presentation and count arithmetic.
The shared D140 time gate below supplies the request-specific predicate.

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

#### Evidence-driven D140 time gate

One typed `fact_in_scope` builder in `core/text_scope.py` serves both the
nomination/confirmation paths in PostgreSQL P1 and final fact hydration in
`surfaces/query_engine.py`. Use the predicate per fact, in the same D142
snapshot and before each eligible cut. Scoped or keyed exact nomination uses
it in the ranked statement. Broad nomination keeps its join-free index
cursor and uses it in batch confirmation. Final confirmation and contradiction
members use the same predicate and keep the request's ranks and filters.

For the current-belief arm, the following is the SQL shape of one occurrence
existence probe. Render the observation arm with its own fixed evidence table
and ID column. `supports_only` is a branch constant and `f` is the bounded
candidate fact. A lateral lookup takes inputs from the previous row.
`OFFSET 0` keeps PostgreSQL from flattening that lookup into freely reorderable
joins. Existence testing can stop at the first qualified occurrence.

```sql
EXISTS (
  SELECT 1 FROM LATERAL (
    SELECT e.deployment_id, e.claim_id FROM public.relation_evidence e
    WHERE e.deployment_id = f.deployment_id AND e.relation_id = f.fact_id
      AND (NOT :supports_only OR e.stance = 'supports')
    OFFSET 0
  ) e CROSS JOIN LATERAL (
    SELECT cc.deployment_id, cc.chunk_id FROM public.chunk_claims cc
    WHERE cc.deployment_id = e.deployment_id AND cc.claim_id = e.claim_id
    OFFSET 0
  ) occ CROSS JOIN LATERAL (
    SELECT c.deployment_id, c.version_id, c.representation_id FROM public.chunks c
    WHERE c.deployment_id = occ.deployment_id AND c.chunk_id = occ.chunk_id
    OFFSET 0
  ) c CROSS JOIN LATERAL (
    SELECT 1 FROM public.document_versions v
    JOIN public.document_version_scope s
      ON s.deployment_id = v.deployment_id AND s.version_id = v.version_id
    WHERE v.deployment_id = c.deployment_id AND v.version_id = c.version_id
      AND v.current_representation_id = c.representation_id
      AND (NOT s.periodised OR (s.selectable AND s.in_force && :scope_window))
    OFFSET 0
  ) readable
)
```

Accept if the supports-only probe succeeds, or if a fact-ID probe finds
**zero raw `supports` rows** and the either-stance probe succeeds. The
existence of a raw support row disables contradiction fallback even when
its only occurrence was deleted or left in a replaced reading. An empty
evidence set fails. `scope_window` is D140's inclusive `TextScope` window.
The scope row enforces live lineage/version membership. Undeclared lineages
keep their any-live-version case, while periodised lineages require a
selectable version overlapping the window. Reused claims qualify through
their recorded occurrences. D118 fact clocks and D54 presentation are
independent. The historical-belief ledger arm and reconstructed identity use
their existing authorities. D140's pending-lineage counterfactual stays a
freshness probe with the distinct content regime in §4.2.

Reuse the existing serving indexes. The fact ID selects one hash leaf and
leads its evidence primary key. `ix_chunkclaims_claim` retrieves occurrences
by claim ID, and the `chunks_pkey` prefix retrieves their chunks. Version and
scope probes use the primary keys `document_versions(version_id)` and
`document_version_scope(deployment_id, version_id)`. Source membership uses
the claim primary key. Survivor lookup uses `entities(deployment_id, entity_id)`
for the seed and `entities(entity_id)` for its redirects.

With `E` evidence rows and `O` matching occurrences for one candidate, one
occurrence probe needs up to `E × P_occ + O × P_chunk` range-leaf lookups,
where each `P` is that table's retained partition count. Sum these and the
fact-evidence and version lookups over candidates, with a bounded number
of stance checks per fact. This applies D153's per-partition allowance to
known claim and chunk coordinates, removing the unrelated-chunk multiplier.
Retain per-leaf planning and index costs in the measurements. Use default
PostgreSQL join planning for eligibility SQL. The D142 index cursor retains
its existing attestation contract.

#### Consumers and exact scope

| Consumer | Required integration |
| --- | --- |
| `facts_visible_history` | Explicit eighteen-column projection of the full authority; same rows, types, counts and values |
| `fact_claim_evidence_live`, `evidence_lineage`, `v_memory_page_citation_visible`, `changes_visible` | Keep complete membership through `v_memory_fact_visible`; audit whole-row projections, preserve all public values and do not add a second source/endpoint truth query |
| P1 scoped/keyed and scored fact nomination, broad fact-loop confirmation | Shared bounded source/endpoint builder and D140 gate before the cut, with the existing kind/time/evidence/model/filter/coverage predicates |
| `_CONFIRM_FACTS_CONTEXT_BY_KIND` | Same bounded builder and gate for full eligible hydration and original nomination ranks |
| `_FACTS_CONTEXT_CONTRADICTION_MEMBERS` | Enumerate matching group fact IDs, then apply shared endpoint qualification and the D140 gate with existing group count/member semantics and deadline |
| `graph_queries.py`, SQL sandbox `nomination.py` | Preserve complete public authority for graph traversal/sandbox SQL; use shared builder where they call bounded native fact nomination. No private helper becomes caller-reachable |

Register the new helper in `AUTHORIZATION_HELPER_VIEWS`, revoke PUBLIC and all
query-role grants, and assign `rememberstack_view_owner`. Its AST becomes a
hashed dependency; it is absent from public discovery/reachability.

A scope below the unchanged 50,000 raw-row budget may still have expensive
source qualification or many distinct
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
- Correct the existing source-manifest gap: `source_definitions.py` currently
  hashes the pre-D140 fact helper instead of the widened deployed definition,
  and omits `v_memory_claim_carried_periodised`. Register that carried-claim
  helper and the actual widened `v_memory_fact_claim_live`/fact authority text
  as hashed dependencies, with private ACLs/ownership. Re-emit the widened
  `v_memory_fact_claim_live` body as literal current migration DDL, unchanged
  semantically; hash that literal, not a stale pre-D140 definition. Do not claim a complete
  dependency hash while leaving that gap unresolved.
- Register changed authored DDL in query-space source definitions, regenerate
  the canonical manifest/deletion matrix where affected and exercise normal
  saved-query revalidation. Published view and every column comment remain
  explicit literal migration output, including `facts_visible_history`.
- Update `catalog_contract.py`'s `EXPECTED_INDEXES` and `EXPECTED_VIEWS` for
  the new index/helper. Move the single current schema-head pins in
  `storage_capture_guard.py` and `storage_capture_catalog.py` from
  `p9_46_0067` to the actual new head; do not add an old-head fallback. Qualify
  D152 capture guards, resource classification/inventory and index allocation
  at that head. The new index changes retained physical allocation. Public columns/types remain stable, but a changed
  public view AST is a real query-space identity change, not an unchanged pin.
- Update current LoCoMo surface pins if their qualified manifest changes;
  preserve frozen historical profiles and results. New validation/answer runs
  record their actual source and execution identity; do not edit old fingerprints.

For D163's physical amendment, replace the affected private view definitions
via `CREATE OR REPLACE VIEW` in a new native Alembic revision with the same
columns, types, comments, owners and ACLs. Verify the previous release reads
and writes while the new definitions are installed under D160. Apply D161's
session migration lock, three-second default lock wait and bounded `55P03`
retry from the committed revision. Exercise contention on the view lock and
apply the manifest, catalog and D152 checks above. [Migration compatibility](packaging_distribution_design.md#61-migration-compatibility)

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

After bulk ingestion or restore, operators can refresh stale statistics on
affected `chunk_claims`, `chunks` and fact-evidence parents. PostgreSQL 18's
ordinary parent `ANALYZE` also refreshes its leaves, including small evidence
leaves that seldom reach the autoanalyze threshold. Adopt this maintenance
where before/after measurements on a restored copy justify it. Both SQL
shapes are measured before and after. The serving query must qualify with
sparse statistics. [Statistics evidence](../analysis/evidence_driven_fact_eligibility_20261127.md#statistics-after-bulk-ingestion)

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

The required Claude Opus design approval is recorded in the linked round-3
review; prior blocking reviews and inputs are retained alongside it. Runtime
implementation must satisfy all of these gates before its merge:

1. **Independent complete oracle:** build a dedicated database by migrating
   the repository to **`p9_46_0067`**, including D140's real widened helper
   definitions, and use identical seeded/cloned data. A test-schema copy is
   allowed only if mechanically generated from that database's `pg_get_viewdef`
   output with complete dependency closure, never hand-picked migration
   constants. Keep pre-change native statements in test code only. Include
   facts whose only visible evidence is a carried periodised claim after
   origin-version deletion. Never compare a new view against itself or keep
   an old runtime fallback. Compare all columns/counts on deterministic
   randomized fixtures and complete retained-store candidate sets on isolated
   copies. Protected original stores remain read-only. A long reference may
   use a separately recorded longer bound or stable batches; a truncated
   oracle proves no full equality.
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
   at least 120 monthly partitions plus the default, and entities whose early
   decisions reference invisible versions. Add many current mentions unrelated
   to the requested entities to cover entity-driven nomination as well as
   candidate hydration. Unrelated row counts must not multiply probe work. Report the
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
   and new-partition index inheritance. Verify updated physical catalog/head
   pins and D152 capture guards/resource allocation at the new head. Verify
   the widened D140 dependency ASTs and carried-claim helper are actually hashed.
   Publish actual limits and risks.

A full LoCoMo answer/judge replay can follow the merged, qualified fix using
the retained stores and original model/prompt policy, with separately saved
answers and verified GCS backups. It cannot be used in place of eligibility
or concurrency qualification.

### 9.1 D163 fact-path qualification

**Equivalence oracle.** Replay the actual v0.22.4 native SQL and migrated
authority on isolated identical data with frozen vectors and pinned clocks.
Use PostgreSQL 18 copies of retained LoCoMo conv-26/30 and conv-47 stores
and a hosted-shaped store with the observed partition/evidence distribution.
Compare gate booleans on
complete input IDs, then both directions of the `(fact_kind, fact_id)` set
difference before cuts. Compare ordered native results with multiplicities,
scores, counts, representative evidence coordinates, filters, and freshness
and boundary metadata. Include scoped and keyed nomination, broad batch
confirmation, final hydration and contradiction members. Apply §9's
visibility, snapshot, cancellation and migration checks to the changed path.

Seed the hosted-shaped store with both fact kinds under D140 `current`, `at`,
`overlap` and `history`, including disjoint periods, exact interval boundaries,
retractions, a cleared declaration mode and a pinned earlier belief. Exercise
an unready in-force edition with older readable text, historical support in
undeclared lineages, a replaced reading, version deletion, archive/forget,
a carried claim with a deleted origin and a periodised claim with multiple
occurrences. Include absent evidence, contradiction-only evidence, support
outside the scope alongside an in-scope contradiction, mixed valid evidence
and both D118 clock filters. Verify invisible, merged and self-relation
endpoints. Check that an ineligible high-scoring fact takes no result slot
and that pending nomination supplies only freshness metadata.

**Work and latency.** Collect actual plans with buffer counts and separately
time uninstrumented SQL including planning, with at least 40 warm samples per
stratum. Report p50/p95, planning time, leaf loops, rows read and rejected,
temp spill and completion. Vary unrelated rows, retained partitions and
candidate-owned evidence/occurrences independently. Require one evidence hash
leaf per probe, claim/chunk-keyed range probes and at most two stored-endpoint
seeds per relation source lookup. Hold candidate IDs and leaf counts fixed
when adding unrelated rows and check that probe counts and row work stay
bounded. At each scale, also pair full native runs within the same store.
Exercise sparse and refreshed statistics, prepared statements and absent or
high-fanout evidence. Report cold cache and eight-reader behavior under the
existing deadline.

Proposed warm single-reader p95 targets on PostgreSQL 18.6, 2 vCPU/4 GB, with
frozen vectors and the existing `k=50` and candidate depth 200:

| Stratum | Qualification target |
| --- | --- |
| Hosted shape, 804 observation and 535 relation inputs | ≤1 s per P1 confirmation statement and ≤5 s for native `facts_context` wall time |
| 300k and 3M chunks, fixed candidate evidence, ten retained range leaves | ≤2 s per P1 batch and ≤10 s native wall time for matched broad and bounded scoped requests |
| 120 monthly leaves plus default, same candidates and their evidence | ≤5 s per P1 batch, with linear leaf-probe overhead and planning time reported separately |

The timed requests must complete without early stops. Also measure the
unchanged 50,000-raw-row scoped edge under the existing operation deadline.
Keep the recorded values distinct from these proposed acceptance targets.

**LoCoMo answer parity.** At fixed snapshots and vectors, broad nomination
produces the same scored stream. Equal eligibility selects the same rows and
makes the same continuation decisions in each completed batch. Exact scoped
ranking uses the same scores and tie-breaks on the same eligible set. Full
row and evidence parity then gives the same reader input. Verify this by
replaying the saved retrieval calls and comparing the serialized tool results
fed to the reader. With the same question, initial reader state and model
draws, identical tool results at each completed step give the same answer.
This proof uses zero new model calls. Track deadline recoveries separately
and retain the original answers and their run identities.

## 10. Binding authority and delivery record

D153 adopts this design after the owner's requested Opus approval. It amends
D149's physical provenance/confirmation work and D23's UUID-derived pruning
assumption, while preserving their other rules. The corresponding current
contracts are reconciled in P1 search §8.3–8.5, entity identity §7, query space
§3.3/§6, and PostgreSQL schema identifiers/§12. Both earlier decision entries
carry the amendment. The superseded proposal is removed rather than kept as
an alternative runtime or architecture path.

Accepted design is not evidence that implementation ships. Delivery sequencing
and qualification are tracked in [the delivery plan](../plans/shared_retrieval_eligibility.md)
and PR evidence. Absolute throughput, eight-reader resources and temporary spill
usage are recorded observations; semantic, structural-work, cancellation,
isolation, native-query completion and integration gates are mandatory.
