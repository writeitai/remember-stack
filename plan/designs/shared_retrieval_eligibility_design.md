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

Its per-kind `UNION ALL` arms keep the raw relation and observation fields.
For each stored endpoint, use an inner `LATERAL (... OFFSET 0)` lookup of
`v_memory_entity_survivor` filtered by the fact's deployment and endpoint
ID. An observation resolves its subject. A relation resolves its subject
and object.

Put the one forward merge walk in `v_memory_entity_survivor`. Its exposed
`(deployment_id, entity_id)` comes directly from the `entities` seed,
so the caller's two predicates bind that row before recursion. For each
redirect, the recursive term uses its own `LATERAL (... OFFSET 0)` point
lookup with `deployment_id = seed.deployment_id` and
`entity_id = chain.merged_into` inside the lookup. Recursive `UNION`
deduplicates `(entity_id, merged_into)` pairs, and only a terminal row
with `merged_into IS NULL` emits a survivor. Missing seeds, dangling
targets, cycles and cross-deployment redirects emit no row. The existing
two-column `v_graph_survivor` projects this same walk.

In each source-membership `EXISTS`, first select raw evidence through a
`LATERAL (... OFFSET 0)` probe of the kind's evidence table. Put the
deployment and candidate fact ID in that probe's `WHERE`. A second
`LATERAL (... OFFSET 0)` probe binds the evidence row's
`(deployment_id, claim_id, doc_id)` inside both sides of
`claims_visible_history UNION ALL v_memory_claim_carried_periodised`.
Binding the raw evidence inside its probe prevents semi-join conversion even
when no claim qualifies. Either stance can qualify, including claims carried
by a newer occurrence after loss of the origin.

Put the shared origin probe in `memory_v1.claims_visible_history`. From the
claim row, look up its origin chunk in `LATERAL (... OFFSET 0)` using the
claim's `(deployment_id, doc_id, chunk_id)` inside the lookup. From that
chunk, use a second such lookup to check its visible version and ready
representation by their full coordinates. Keep the existing live-lineage
and historical visibility rules, including ready origins in superseded
versions or replaced representations.
In `v_memory_claim_carried_periodised`, use `NOT EXISTS (... OFFSET 0)`
against that same history view keyed by the claim's deployment and ID.
Keep the current-testimony and period predicates. Bind the carried
occurrence by deployment and claim ID, then its chunk by deployment and
chunk ID, and check that version's current ready reading using those inputs.

Keep the widened `v_memory_fact_claim_live` for current-lineage membership.
Each arm starts with raw evidence and uses a `LATERAL (... OFFSET 0)` lookup
that binds the evidence's deployment, claim and document IDs inside both
branches of `memory_v1.claims_live UNION ALL v_memory_claim_carried_periodised`.
The ordinary branch reuses the keyed origin probe through `claims_live`.

D54 counts still come from `v_memory_evidence_lineage_live` through the left
lateral aggregate. Bind deployment, fact kind and fact ID below the lineage
grouping so the raw evidence scan reads only that fact's leaf and key.
Count every eligible lineage once per stance, including exact zero totals.
PostgreSQL can omit the aggregate when the caller requests no counts.
The raw fact precision is cast to text. For `support_state_current`, bind
deployment, fact kind and fact ID inside a `LATERAL (... OFFSET 0)` probe
of `review_queue`. Use the partial B-tree `ix_review_open_support_fact`
on `(deployment_id, (candidate ->> 'fact_kind'), (candidate ->> 'fact_id'))`
for `support_withdrawn` reviews in `pending` or `deferred` status. The
text keys match the stored JSON and the existing comparison. For each
matching review, use a fenced `NOT EXISTS` check with keyed claim and
document lookups to exclude it while its claim's document is archived
(D159). Any remaining review marks the fact withdrawn. Endpoint visibility
is checked separately.

`v_memory_fact_visible` requires the subject survivor to match
`entities_current` by deployment and ID. For a relation the object must
match too. `memory_v1.facts_visible_history` projects the same
eighteen columns explicitly from `v_memory_fact_visible`. The helper owns
fact presentation. Preserve existing output shapes for consumers of
whole-row or `*` projections.

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
**zero raw `supports` rows** and the either-stance probe succeeds. In both
fact kinds, put `OFFSET 0` inside the raw-support `NOT EXISTS` along with the
correlated deployment and fact IDs and `stance = 'supports'`. This keeps
PostgreSQL from turning the absence check into a hashed scan of unrelated
evidence. A raw support row disables contradiction fallback even when its
only occurrence was deleted or left in a replaced reading. An empty evidence
set fails. `scope_window` is D140's inclusive `TextScope` window.
The scope row enforces live lineage/version membership. Undeclared lineages
keep their any-live-version case, while periodised lineages require a
selectable version overlapping the window. Reused claims qualify through
their recorded occurrences. D118 fact clocks and D54 presentation are
independent. The historical-belief ledger arm and reconstructed identity use
their existing authorities. D140's pending-lineage counterfactual stays a
freshness probe with the distinct content regime in §4.2.

The time and source gates reuse the existing serving indexes. The fact ID
selects one hash leaf and leads its evidence primary key.
`ix_chunkclaims_claim` retrieves occurrences by claim ID and the
`chunks_pkey` prefix retrieves their chunks. Version and
scope probes use the primary keys `document_versions(version_id)` and
`document_version_scope(deployment_id, version_id)`. Source membership uses
the unpartitioned `claims` primary key. The survivor walk uses existing
entity keys, with the deployment bound at the seed and each hop.

With `E` evidence rows and `O` matching occurrences for one candidate, one
occurrence probe needs up to `E × P_occ + O × P_chunk` range-leaf lookups,
where each `P` is that table's retained partition count. Sum these and the
fact-evidence and version lookups over candidates, with a bounded number
of stance checks per fact. This applies D153's per-partition allowance to
known claim and chunk coordinates, removing the unrelated-chunk multiplier.
Retain per-leaf planning and index costs in the measurements. Use default
PostgreSQL join planning for eligibility SQL. The D142 index cursor retains
its existing attestation contract.

#### Representative evidence and document filters

Start `_facts_context_evidence` from confirmed fact keys. Probe
`v_memory_fact_claim_live` by deployment, kind and fact ID inside a
`LATERAL (... OFFSET 0)` lookup before grouping. Reuse the claim-keyed
occurrence and readable-version probes from `fact_in_scope` in `claim_in_scope`
and when fetching the chosen claims' displayed occurrences. Keep D140's
existing selectors for each read. Use those probes in the D156 fact paths
too. In `fact_supported_matching`, bind the supporting evidence and its
claim with the same lookup boundaries and keep its live-claim predicate.
`matching_occurrence_exists` checks scope and document metadata on the same
occurrence, with the metadata lookup bound to that occurrence's version.

Keep the existing D54 grouping by lineage and stance. Compute exact
in-scope and matching-lineage totals and ranks from that grouping, then
materialize the rows within each stance's display limit. From each selected
row, read `claims` by deployment, representative claim ID and lineage ID
inside a `LATERAL (... OFFSET 0)` lookup. Read the display metadata from
`memory_v1.documents_live` through a second such lookup by deployment and
document ID. The claim read uses its stored fields even if its origin chunk
has been deleted. Preserve D156's matching-first order, timestamp and ID
tie breaks, stance budgets and citation coordinates. The fact's current
lineage counts remain independent of the request's time and document filters.

#### Consumers and exact scope

| Consumer | Required integration |
| --- | --- |
| `facts_visible_history` | Explicit eighteen-column projection of the full authority; same rows, types, counts and values |
| `fact_claim_evidence_live`, `evidence_lineage`, `v_memory_page_citation_visible`, `changes_visible` | Keep complete membership through `v_memory_fact_visible`; audit whole-row projections, preserve all public values and do not add a second source/endpoint truth query |
| P1 scoped/keyed and scored fact nomination, broad fact-loop confirmation | Shared bounded source/endpoint builder and D140 gate before the cut, with the existing kind/time/evidence/model/filter/coverage predicates |
| `_CONFIRM_FACTS_CONTEXT_BY_KIND` | Same bounded builder and gate for full eligible hydration and original nomination ranks |
| `_facts_context_evidence`, selected evidence occurrences | Keyed live-claim and occurrence probes, exact totals and ranks, then keyed representative reads within the display limits |
| `_FACTS_CONTEXT_CONTRADICTION_MEMBERS` | Enumerate matching group fact IDs, then apply shared endpoint qualification and the D140 gate with existing group count/member semantics and deadline |
| `graph_queries.py`, SQL sandbox `nomination.py` | Preserve complete public authority for graph traversal/sandbox SQL; use shared builder where they call bounded native fact nomination. No private helper becomes caller-reachable |

Keep the source helper in `AUTHORIZATION_HELPER_VIEWS` as a hashed dependency.
It remains owned by `rememberstack_view_owner` and outside public discovery
and query-role reachability.

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

D163 starts from a database migrated to `p9_55_0076` (v0.22.4). Use its
deployed definitions as the upgrade and downgrade reference, including
D159's archive-aware `FACT_SOURCE_ARCHIVE_DDL`. The `p9_46_0067` oracle and
pin transitions belong to [D153's historical delivery](../plans/shared_retrieval_eligibility.md).
[Archive migration](../../src/rememberstack/spine/migrations/versions/p9_55_0076_archive.py#L187)

Build `ix_review_open_support_fact` concurrently in its own Alembic
revision. Reuse the session lock and invalid-index retry checks in §4.2
and verify the index definition and validity before use. Then use
`CREATE OR REPLACE VIEW` in separate revisions, one view per transaction.
Apply the view revisions in this order.

1. `v_memory_entity_survivor`, with the deployment-bound walk.
2. `v_graph_survivor`, projecting that walk.
3. `memory_v1.claims_visible_history`, with the shared origin probe.
4. `v_memory_claim_carried_periodised`, using the keyed origin-absence check.
5. `v_memory_fact_claim_live`, with the keyed live-claim lookup.
6. `v_memory_fact_source_qualified`, with bounded membership, counts and support state.

Downgrade in reverse order, restoring the exact `p9_55_0076` definitions and
comments, then drop the review index concurrently in its own revision.
Between the survivor replacements both views walk `entities` directly,
so callers can use either view and see the same results on valid chains.
Preserve columns, types, owners, grants and public comments at every step.
Each commit releases the target's exclusive lock. A reader can then
finish through a dependency before the migration needs an exclusive lock on
the reader's caller view. Keep historical migrations immutable and keep one
serving path using typed builders with fixed SQL identifiers and bound values.

Register the changed authored DDL in `source_definitions.py`, retaining the
changed private views in the hashed dependency set. Regenerate the manifest
and any affected deletion matrix, verify catalog definitions and ACLs, and run
normal saved-query revalidation. A changed private dependency changes the
manifest identity even with stable public columns. Advance the D152
schema-head pins in `storage_capture_guard.py` and `storage_capture_catalog.py` from
`p9_55_0076` to the new head. Add the review index to the native catalog
inventory and qualify the guards and resource inventory at that head.
Protected benchmark identities follow §9.1.

Verify previous-release reads and writes at each intermediate revision under
D160. Keep the D161 session migration lock across the commits, with the
three-second default lock wait and bounded `55P03` retry from the last
committed revision. Start the new release at the final head.
[Migration compatibility](packaging_distribution_design.md#61-migration-compatibility)

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

The partial review index adds storage and write work for open withdrawals.
Record index size, build duration and WAL. Request-local ID and coverage
materialization can spill to disk. Keep projected fields narrow and inspect
temporary block usage.
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

These are D153's original implementation gates. They retain the baseline,
schema transitions and approval record for that change. D163's proposed
qualification is in §9.1.

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

### 9.1 D163 fact-path qualification

**Equivalence oracle.** Compare the v0.22.4 native SQL and its actual
`p9_55_0076` views against D163 on isolated copies with identical data,
frozen vectors and pinned clocks. Use PostgreSQL 18 copies of retained
LoCoMo conv-26/30 and conv-47 data and a hosted-shaped store with the
observed partition and evidence distribution. Compare gate booleans on
complete input IDs and both directions of the `(fact_kind, fact_id)` set
difference before cuts. Compare ordered native results with multiplicities,
scores, counts, representative evidence coordinates, filters, and freshness
and boundary metadata. Include scoped and keyed nomination, broad batch
confirmation, final hydration and contradiction members. Apply §9's
visibility, snapshot and cancellation checks to the changed path. Exercise
populated upgrade and downgrade with live catalog and row comparisons at
`p9_55_0076` and at the new head. In both directions, admit a previous-release
reader between replacements and hold its transaction open through the next
contended view replacement. Check that the reader completes and that the
migration resumes from the last committed revision after `55P03`. Any `40P01`
fails the test.

Use a legacy-corruption fixture with a redirect from deployment A through
deployment B to a terminal in A. The composite redirect foreign key prevents
this in normal writes. The deployed adapter accepts that chain because its
seed and terminal share a deployment. D163 rejects the first foreign hop, so
the chain has no survivor and a fact using that endpoint is absent. Record
those differences explicitly in the oracle and require equality on valid
chains.

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
and that pending nomination supplies only freshness metadata. Retain the
D159 regression for a fact still supported by another document. Check both
pending and deferred withdrawals. A review on the archived document's claim
stops marking the fact withdrawn while archived and applies again on
unarchive. An open review on another active document still applies.
[Archive regression](../../src/tests/workers/test_lifecycle_reconciliation.py#L2255)

**Work and latency.** Collect actual plans with buffer counts and separately
time uninstrumented SQL including planning, with at least 40 warm samples per
stratum. Report p50/p95, planning time, leaf loops, rows read and rejected,
temp spill and completion. Vary unrelated rows, retained partitions and
candidate-owned evidence/occurrences independently. Require one evidence hash
leaf per probe, claim/chunk-keyed range probes and one seed per stored endpoint.
Hold candidate IDs and leaf counts fixed as unrelated rows grow. Measure
both relation endpoints separately with multi-hop chains and verify that
each step visits only its next entity. Explain both source-helper arms
separately from the time gate. Use the real history and carried views with a
missing origin chunk, testing both a qualifying carried occurrence and no
qualifying occurrence. Grow unrelated chunks in the claim's own document
and require origin work to stay keyed to its chunk ID, with only the
existing range-leaf overhead. A visible origin still excludes the claim
from the carried branch.

Exercise the raw-support absence check for both kinds with in-scope
contradictions. A contradiction-only fact passes. Adding raw support whose
occurrences are all out of scope makes it fail. Confirm from executed plans
that the absence check visits only the candidate evidence leaf as unrelated
evidence grows, including in that leaf. Also measure source qualification
when raw evidence exists and no historical or carried claim qualifies.
Require bounded rows read and rejected as unrelated rows grow.

Collect executed plans for full fact hydration with counts, support state,
representatives and displayed occurrences, with and without `documents`
filters. Keep requested facts, their evidence, reviews and partition counts
fixed. Independently grow unrelated open reviews in the same deployment,
claims, documents and occurrences. Require fact-keyed review probes even
when nothing qualifies, and claim and document point reads only for the
selected representatives. Compare support state, both stance counts, in-scope
and matching totals, representative order and citation coordinates against
the oracle. Include several claims from one lineage. Verify that a reused
claim with one in-scope occurrence and a different metadata-matching
occurrence fails the matching-occurrence check. Retain the counts-omitted
plan check for ID-only reads.

Run forced custom and generic prepared plans under sparse and refreshed
statistics. At each scale, pair full native runs on the same data. Include
absent and high-fanout evidence, cold cache and eight-reader behavior under
the existing deadline. [PostgreSQL 18 probe evidence](../analysis/evidence_driven_fact_eligibility_20261127.md#source-helper-and-survivor-probes)

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

#### Benchmark admission and lifetime

An external launcher guards LoCoMo `answer` and live `retrieval-ablation`,
and BEAM `answer-retrieval`, on every start and resume, including BEAM
`--force`. Keep immutable pins alongside the run manifests for endpoint,
serving revision, packaged manifest hash, live Alembic revision and
definitions of the views changed by D163. When the scoring store is frozen,
pin its deployment ID, verified snapshot or restore manifest digest and
storage image identities. Key the store binding by run and sample in LoCoMo
and by run and arm in BEAM.

For BEAM, also pin the SHA-256 of the complete ordered `questions.json`,
the evaluator revision and the effective reader settings, including the
answer model after defaults are resolved. Run the clean evaluator with
its locked dependencies, a read-only snapshot of the verified questions
and the pinned settings. Question text and ability drive retrieval and
prompting even when IDs match. Recover legacy pins only from original records
or a verified restore for that identity. Missing or mismatched pins abort
before retrieval, provider calls or checkpoint writes.
[BEAM resume](../../benchmarks/rs_harness_beam/answer_agent.py#L725) ·
[BEAM CLI](../../benchmarks/rs_harness_beam/cli.py#L174)

Acquire the store lock before starting or verifying the store. Reuse the
shard runner's lock when it already holds it. Restore, wipe and service
replacement share that lock. Run only the private API and its backing
services with setup, migrations and automatic restarts disabled. Exclude
ingestion, workers and projection refresh. Keep the retrieval data, object
store and any corpus mount read-only for the invocation.

Bind every effective API database connection, including the primary,
retrieval and graph pools and the open-query role, to the same private
PostgreSQL listener and database on the verified restored volumes.
Resolve the running instance's connection settings, including
`query_role_host`, `query_role_port` and driver overrides. Allow only that
database destination. Before dispatch, confirm each role connects to the
process serving the verified volumes. Check the packaged manifest and verify
`public.alembic_version` and `pg_get_viewdef` on that database.
[Pinned query-role factory](https://github.com/writeitai/remember-stack/blob/78a83408011fddbd1d62ed8957b799f456b23bbd/src/rememberstack/profiles/selfhost.py#L784-L822)

Give every reader the route to that exact API. This includes BEAM's
per-question clients and LoCoMo's `codex-p3-mcp` child, which receives the
engine route and credentials through `REMEMBER_API_URL` and `REMEMBER_API_KEY`.
Use a private network binding with no alternate engine, redirect or proxy
route for reads. A stopped instance closes the route and the launcher ends
the invocation. Hold the lock and the binding until the evaluator and its
reader children exit and the last checkpoint is written. The shard can then
switch to the next sample's verified store while still holding its lock.
[BEAM client](../../benchmarks/rs_harness_beam/answer_agent.py#L692) ·
[MCP child](../../benchmarks/locomo/ablation.py#L1821) ·
[Store lock](../../benchmarks/locomo/sharding/store_backup.py#L247)

Stage a LoCoMo restore into empty targets using the recorded backup tool
and archive layout, keeping later answer checkpoints separate. Restore
without `--start`. Start the recorded backing-service images, then the
private API with its Compose setup dependency disabled. The published
94.09% run uses six archives including the forget-manifest volume,
its recorded PostgreSQL 19 image and extensions, engine and evaluator
`78a83408011fddbd1d62ed8957b799f456b23bbd` and schema `p9_48_0069`.
[Historical restore](https://github.com/writeitai/remember-stack/blob/78a83408011fddbd1d62ed8957b799f456b23bbd/benchmarks/locomo/sharding/store_backup.py#L1483-L1609) ·
[Historical graph check](https://github.com/writeitai/remember-stack/blob/78a83408011fddbd1d62ed8957b799f456b23bbd/src/rememberstack/spine/graph_catalog.py#L88-L98) ·
[Published run](../../benchmarks/locomo/README.md#published-results)

Run LoCoMo in a clean checkout with the recorded evaluator's locked
environment, existing checkpoint and restored mount. Native `answer` takes
its evaluator revision from `run.json`. An ablation takes it from
`ablation_runner_revision` in `ablation.json`. For `full`, pass the output's
frozen `answer-prompt.txt` with `--answer-prompt`. Recreate the fingerprinted
CLI arguments from `ablation.json`, including the profile, selected items,
answer and judge models, ordered provider allowlist, worker count, API
timeout and judge-prompt choice, where applicable. Preserve optional
settings according to the recorded evaluator's rules. The pinned CLI
enforces its original revision, readiness, protocol and fingerprint checks.
[Native guard](../../benchmarks/locomo/runner.py#L1660) ·
[Historical ablation resume](https://github.com/writeitai/remember-stack/blob/78a83408011fddbd1d62ed8957b799f456b23bbd/benchmarks/locomo/ablation.py#L823-L853)

Through both LoCoMo CLI paths, resume a historical partial checkpoint on
a historical-format restore with its recorded PostgreSQL image. Complete a
remaining question with the fingerprint, prior answers and readiness checks
intact. Use the published prompt override and saved options for the
full-profile ablation test. Resume a partial BEAM run on its pinned store with
its original reader inputs. Test a two-sample shard that accepts each sample's own restore and
rejects the other's.

Reject an endpoint change, a store swap at the same endpoint and a migrated
copy behind the pinned image. In both harnesses, point only the SQL role at a
second database while native reads target the verified restore. Test both
a migrated clone and a separate copy with a matching schema. For BEAM also
change the question text or ability under the same ID, reorder the file,
change evaluator revision and change `--model` in independent cases. Each
mismatch must abort before retrieval and leave output and checkpoints
byte-identical. Cover starts and resumes, including BEAM `--force`.

In a synchronized test, pause after admission and again before the last
checkpoint. Attempt same-address service replacement, store swap, migration
and corpus writes while the lock is held. Verify that every reader, including
the MCP child, and each new checkpoint receive data only from the admitted
service and store.

#### Model-free qualification for benchmark adoption

Require explicit D163 opt-in with a fresh run identity and output directory.
Save it with the pins and verify it on resume. Test that D163 is rejected
without opt-in and that an existing protected run refuses conversion.

Replay LoCoMo's saved tool calls and run BEAM's retrieval and rendering on
paired restores under the original budgets. Archive each retrieval call's
query vectors in embedding call order with the model, dimensions and exact
input text, including embeddings used by SQL semantic calls. Archive the
evaluation instant, world and belief times, SQL transaction and statement
times used by the query or its views, and the rendered SQL
`execution_started_at`. Feed the vectors through a replay-only
embedding adapter and pin those clocks in the disposable services and
databases. Keep elapsed-time deadlines active and disable provider access.
A missing or mismatched vector or clock fails the replay before it can report
parity. Tool traces omit vectors and can omit internal clocks. Where exact
historical inputs are missing, run a separately identified paired fixture
with its own captured vectors and clocks. The fixture qualifies that input
set only.
[Tool records](../../benchmarks/locomo/model.py#L373) ·
[Embedding call](../../src/rememberstack/surfaces/query_engine.py#L4838) ·
[SQL snapshot clock](../../src/rememberstack/surfaces/query_sandbox/executor.py#L1166)

Run a same-build control on two identical restores with the same reader,
calls, vectors and clocks. Include SQL calls in the control. In both
comparisons, report retrieval equivalence over ordered calls and responses,
including rows, evidence, limits, typed negatives, errors and deadlines.
Omit only `request_id`, `elapsed_ms` and `surface_manifest_hash` from SQL
result envelopes for this comparison. The first two can vary in the control
and D163 changes the manifest hash.

Also retain and diff the full rendered reader inputs byte for byte, including
those SQL fields. A deadline becoming a success changes the reader input.
Exact rendered equality is required for answer parity. A score for changed
inputs requires fresh answering and judging under the new identity.
Preserve original answers, grades and run identities.
[SQL response fields](../../src/rememberstack/surfaces/query_sandbox/executor.py#L1061) ·
[LoCoMo rendering](../../benchmarks/locomo/protocol.py#L717)

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
