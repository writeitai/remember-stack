# Temporal cache freshness and incremental hard-forget inventory

Status: non-binding independent analysis for spikes #367 and #368. Written
2026-09-06 against the `ugm-temporal-contracts` checkout. This document recommends
contracts; it neither accepts an architecture amendment nor establishes that the
behavior ships. Implementation remains gated by D107 §12.

## 1. Problem and inspected authority

A profile is a disposable description of an entity, not a user account. For
example, Alice's description and embedding may include “CEO of Acme.” If her
accepted tenure ends at midnight, time passing changes the evidence appropriate
for that description even when no document arrives. A knowledge (K) page has the
same problem, with an additional complication: already-written prose cannot be
made current merely by filtering the underlying facts on a later query.

Hard forget has a different trigger but shares the dependency problem. If the
forgotten source supplied Alice's start date, erasing its claim while retaining
that date in a correction record, profile, or scheduled payload leaves derived
source information behind. This is an extension of the existing deletion
contract, not justification for replacing the deletion system.

Inspected binding sources:

- `decisions.md`, D95 and D96: entity identity is a referent; evidence-backed
  profile prose and embeddings support T3/T4 resolution and are not identity
  keys. D99/D100 amendments preserve guarded publication and convergence.
- `decisions.md`, D98: graph reads use live PostgreSQL authority; there is no
  graph snapshot purge, graph generation, or community-page scheduler to add.
- `plan/designs/temporal_clocks_design.md`, §§4, 7.1, 7.4, 9, and 12: full
  current-fact containment, occurrence-derived labels, activation/expiry work,
  conversion, and the expressly unsettled cache/deletion contracts.
- `plan/designs/entity_identity_and_retrieval_design.md`, §3 and implementation
  contracts: profile input verification, provider calls outside locked database
  transactions, and post-publication convergence.
- `plan/designs/k_layers_design.md`, §§4–6 and 10: mechanical rules, exact input
  manifests, sole automated Git committer, authored ownership, and deletion.
- `plan/designs/hard_forget_design.md`, §§2–5 and 7: portable content-free
  manifests, admission barrier, one resumable purge, verification and restore.
- `plan/designs/unified_remember_distribution_design.md`, §3.3, and
  `decisions.md`, D108: autonomous adjudication replaces public temporal human
  review. D107's human temporal queue language needs explicit reconciliation;
  it must not silently become a newly implemented public queue.

Implementation sources establishing the actual starting point:

| Source | Observed contract or gap |
| --- | --- |
| `src/rememberstack/spine/profile_refresher.py`, `EntityProfileRefresher.refresh`, `_prepare`, `_commit_if_current`, `current_profile_entity_ids` | Exact evidence revalidation already exists. Provider failures clear stale cached data; paid vectors are discarded when inputs change. |
| Same file, `_SELECT_SALIENT_FACTS` | Current implementation deliberately requires `valid_until IS NULL`, misses future-start exclusion, and ranks by `updated_at`. Its comment explicitly recognizes unscheduled wall-clock expiry. |
| `src/rememberstack/spine/resolver.py`, `_snapshot_from_rows` | Candidate summaries are compared with freshly selected facts; differing cached summaries are omitted. This existing guard can be extended, but its present fact selector is temporally incomplete. |
| `src/rememberstack/spine/profile_convergence.py`, profile publication wrapper | A successful refreshed profile nominates local convergence. Expiry refresh must retain this behavior. |
| `src/rememberstack/spine/knowledge.py`, `route_delta`, `stale_artifacts`, `_SELECT_SUBTREE_MEMBERS` | Routing and exact manifest comparison already exist. `part_of` membership currently uses `valid_until IS NULL`. Rule membership itself can change at a boundary. |
| `src/rememberstack/core/knowledge_hashing.py`, `knowledge_inputs_hash` | Hashes facts, source candidate fingerprints, rules, curation, child/model summaries and writer version; time certification is absent. |
| `src/rememberstack/workers/knowledge_driver.py`, `KnowledgeRoutingDriver.route_and_mark_stale` and compile cycle | Routing only nominates; exact hashes determine work; one driver owns commits. |
| `src/rememberstack/spine/forget.py`, `scrub_postgres`, `_POSTGRES_SCRUB`, `verify_postgres_scrubbed` | Explicit inventory and residual checks already delete exclusive facts, scrub audit features and triggering claim references, and invalidate K artifacts. New temporal objects need corresponding statements and residual checks. |
| `src/rememberstack/workers/forget.py`, `HardForgetHandler.honor` | Existing sequence runs normal lineage deletion, PostgreSQL scrub, shared-entity profile refresh, object/P3/K purge, verification and reopening. |
| `src/rememberstack/model/forget.py`, `ForgetManifest` | Portable format is currently version 1 only. `fact_ids` identifies exclusive facts for deletion; it must not be repurposed to mean all shared facts needing temporal recomputation. |

## 2. Recommendation for #367: certified cache intervals plus durable work

Use a cache certificate: an artifact is valid for an evaluation interval, under
one input revision and one generator version. A worker makes it fresh; every
consumer checks whether that certificate still holds. The scheduler is therefore
responsible for prompt refresh, while the certificate is responsible for truth.

The certificate should record `evaluated_at`, `fresh_until` (the earliest future
boundary, or null for none), generation, input hash, and the dependency/routing
revision it actually inspected. Require `evaluated_at <= E` and either no
`fresh_until` or `E < fresh_until`, together with matching generation/revisions.
Fix `E` once for each query or compilation snapshot. Never equate `compiled_at`
with the world-time evaluation instant. Never hash the continuously advancing
wall clock itself: that would invalidate every artifact on every check.

### 2.1 Dependencies include candidates that are not current yet

For a profile, find all supported, non-invalidated facts for the entity and its
redirect members before applying temporal containment and the salient top-eight
ranking. The current inputs are the eligible ranked subset. Future activation
and expiry minima come from the whole eligible candidate set: a ninth-ranked
fact may enter when one above it expires; a future high-ranked fact is absent
from today's text but still supplies a future dependency. Include both relation
endpoints, and recompute redirect membership when identities merge or unmerge.
This is an indexed entity-local operation, not a deployment-wide fact scan.

For K pages, the same distinction applies to the rule candidate universe versus
the facts included at E. Include candidate membership and relevant next
boundaries in the manifest in addition to existing input fingerprints. Preserve
D54's stable claim fingerprint policy; raw claim IDs need not enter the content
hash just to support time scheduling. Dependency tables may separately retain
IDs for routing and forget.

Structural rules need an additional explicit path. A future `part_of` relation
can bring an entirely new entity into a subtree, so today's materialized keys
cannot nominate all future affected pages. Schedule every finite fact boundary
at the routing layer, even when no existing page matches that fact. At such a
boundary, route an ordinary fact-state delta, rematerialize affected subtree keys
at E, and evaluate both old and new membership. Removed children matter as much
as added children. New pages and rule edits must evaluate already stored future
boundaries during their creation transaction; they cannot wait for new ingest.

Recommend storing routing wakeups and artifact wakeups in the same indexed
boundary-work family, with a discriminated target kind, rather than inventing a
second clock service. Routing targets carry only fact identity, boundary kind,
boundary timestamp and revision. They must not carry copied text or verdict
windows beyond the boundary needed for execution. A content mutation uses the
ordinary routing outbox in the same transaction as the authority write.

### 2.2 Atomic maintenance and scale

Every mutation affecting support, belief validity, world bounds, identity
membership or page rules must atomically do three things: write authority,
advance the appropriate revision, and replace/cancel its pending boundary work
or append a durable invalidation event. The paths include seed, evidence attach,
succession, autonomous correction/reversal, source removal, merge/unmerge,
hard-forget, conversion and rule changes. A successful fact write followed by a
crash must never lose its refresh obligation.

Exact artifact reconstruction may be expensive and should not occur under every
fact-write lock. Use durable routing with revision fencing instead. Until a
routing event has been evaluated, a consumer cannot certify an affected cache as
current merely because its old timestamp is still in range. For direct entity
and predicate dependencies, indexed revision keys provide this fence. For a
structural change whose recipients are not yet known, advance a deployment
structural-routing revision: subtree-dependent artifacts with the old revision
are temporarily uncertified. The existing router can process rule owners in
bounded keyset pages and certify unchanged manifests without paying a writer.
This conservative temporary loss of subtree-cache availability is preferable to
scanning every subtree synchronously or serving a missed dependency.

The amendment needs an explicit revision-to-rule-kind matrix; a bare global
“generation changed” label is insufficient. Profiles depend on entity/identity
revisions; subtree pages also depend on structural routing; scope-interest,
manual and document rules depend on their own configuration and matched evidence
keys. Child and shared-model summaries propagate their certification to parents:
a parent cannot remain certified current when one of its included children is
uncertified, even if its stored child-summary hash has not changed yet.

Use the existing work ledger for attempts, leases, retries and dead letters.
Boundary rows are durable nominations, not another retry database. A unique key
on deployment, target kind/id, boundary kind/time and source revision makes
repeated nomination idempotent. Index due work by deployment and due instant;
index dependencies in both directions (target → sources, source → targets).
Claim small batches and release the nomination transaction before provider
calls. PostgreSQL documents `SKIP LOCKED` as useful for queue-like access, while
warning it is not a consistent general-purpose read; only the claim operation
should use it. Snapshot consistency and locked publication remain separate.
[PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html),
retrieved 2026-09-06.

### 2.3 Publication, downtime, failure and consumers

On dequeue, discard obsolete revisions, group due boundaries by live target,
choose the current database instant E, and rebuild once from current authority.
Do not replay a month of missed boundaries into a month of obsolete summaries.
The due events retain their original boundary times for diagnostics and
idempotency, but the rebuilt artifact uses E. This explicitly resolves the
contradiction between D107 §7.1's boundary-time wording and §12's current-time
catch-up requirement.

Before publication, repeat input/revision checks and check whether the next
boundary passed during the provider call. If it did, discard the result and
retry/coalesce at a newer E. The existing optimistic profile refresher is the
pattern. For K, revalidation must cover the final serving pointer/certificate,
not just the moment the writer returned. Git commit recovery may retain stale
bytes, but it must not certify them current. Do not hold fact locks across a
model call or Git network operation. Separate database statements can observe
different committed states under default read-committed isolation, so the
implementation must deliberately use a fixed snapshot for preparation and
fresh locked revalidation for publication.
[PostgreSQL transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html),
retrieved 2026-09-06.

Reads of expired profiles suppress cached summary and vector authority; T4 may
receive freshly loaded current facts, and T3/convergence cannot merge using the
expired vector. Apply this to P1 entity nomination/hydration, open SQL's published
profile columns, resolver evidence and convergence, not only to one helper.
Missing cache is an availability condition; it does not mean the entity or its
facts disappeared.

K's existing ability to serve a stale but consistent page requires an explicit
D49 envelope: evaluated-at, current/stale/unknown state, and stale reason. For
surfaces without a metadata envelope (mounted Markdown and Git-readable page
bodies), embed an evaluation timestamp and freshness deadline in the published
artifact so the page cannot imply indefinite current truth. Engine-controlled
“current” retrieval must withhold expired prose or return an explicitly stale
artifact; filtered current facts remain available. Historical downloaded bytes
cannot be recalled merely because time passes. Authored pages remain authored:
activation/expiry updates their evidence flags and subscriptions, not their
prose. D108's removal of temporal human review does not confer permission to
rewrite authored content.

At startup resume due nominations and pending routing checkpoints. Late work,
provider outage and downtime must leave expired certificates visibly stale.
Expose due count, oldest overdue age, uncertified artifacts by kind, retries and
last successful sweep. A sweep-health metric is not permission to bypass a stale
certificate. Catch-up is eventual refresh, not a deployment-wide public-read
barrier; hard forget retains its stronger existing barrier.

## 3. Recommendation for #368: extend the existing purge in the same change

The new storage needs an explicit, tested inventory. Nothing here requires a
new backup policy, hosted control plane, graph purge, scheduler or deletion
queue. Normal source withdrawal keeps D55 history; hard forget additionally
removes source-derived content under D74. Structural audit IDs can survive when
needed for replay, but stored dates and rationales are content, not harmless
structure.

| New or changed storage | Action and surviving-state rule |
| --- | --- |
| `seed_claim_id` on relations/observations | Null every forgotten reference, including shared facts. Do not select a replacement seed silently: the creator is provenance, not a mutable “best evidence” pointer. |
| `occurs_from`, `occurs_until`, `occurs_precision` | Recompute the union using surviving attached evidence under the agreed D107 eligibility rule; clear to unknown when none supplies a known window. Include all affected shared facts before evidence deletion destroys the join. |
| World verdict bounds and basis whose justification depends on forgotten claims/verdicts | Run an explicit autonomous reconciliation from remaining admissible justifications. Preserve independently supported bounds, remove unsupported bounds with `erased` basis (the §7 amendment), and record content-free erasure effects. Do not copy erased seed dates into an allegedly clean audit. A start correction must not erase an independently supported successor cap. |
| Temporal correction verdicts: cited claims, prior/next windows, feature snapshots, rationale, actor/model metadata, dependency links | Delete source-exclusive records, or scrub source-bearing fields while retaining necessary structural IDs/status for dependent records. Withdraw affected verdicts from replay eligibility; traverse dependent verdicts and revalidate them. Treat “before” snapshots and rejected/no-op verdicts as content too. A fixed engine actor identity can survive; source-derived free text cannot. |
| Migration adjudication features, staging/shadow columns, conversion checkpoints | Scrub source-derived old/new bounds and features on affected surviving facts; delete exclusive rows and orphan staging data. Preserve only content-free progress IDs and counts. Resume may not restore data from a pre-forget shadow copy. |
| Relation staging/drain records added by #365 | Delete forgotten lineage proposals, payloads and dependent units; retain only minimal content-free completion information needed to make replay idempotent. Apply the same inventory discipline as existing observation staging. |
| Boundary rows, artifact dependency edges, pending routing/refresh work | Delete obsolete source-bearing nominations and dependency rows; reconstruct future boundaries from cleaned surviving authority. Invalidate generations so an already nominated job cannot republish old text. Queue payloads contain IDs/revisions rather than copied source text. |
| Fact labels, search document text, embeddings and embedding hashes | Rebuild deterministic labels from cleaned statement/window; clear vectors and attestations before regeneration if their input changed. Include relations and observations, not just entity profiles. |
| Profiles, K summaries, child/model summary hashes and freshness certificates | Clear affected cached content/certificates, rebuild from clean authority, then use existing P3 and K-history purge. Follow identity redirects and dependency edges to shared survivors and parent artifacts. |
| Archived prompts, correction transcripts and failure payloads | Inventory object keys before deletion and erase with the existing object port. A prompt may contain a forgotten candidate even when a different candidate won. Logs/errors must not retain full model payloads outside the inventory. |

The authority correction row must record its complete evidence dependencies when
it is written. Scanning only `triggering_claim_id` misses a forgotten seed in a
previous-window snapshot, a cited losing candidate, or an inherited cap. An
indexed verdict-to-claim/verdict dependency relation provides an exact closure;
when legacy free-text features lack trustworthy provenance, conservatively scrub
the affected audit family, following the existing planner-transcript policy.
This is a source-erasure action, not permission to invent new truth from a
minimum or maximum date.

Inventory must distinguish exclusive facts to delete from shared facts to
recompute. Recommend a versioned extension of the portable manifest with
separate affected-fact and temporal-verdict identity sets, collected under the
existing drained admission cut before scrub. Keep all new sets content-free,
canonical, sorted and deployment-scoped. Preserve reading/replaying version-1
manifests: restoring a database with new temporal tables against an old manifest
must discover dependencies from its existing claim/doc/fact IDs before deletion
and apply the new scrubber. If exact new dependency provenance is unavailable,
clear the uncertain source-bearing audit material rather than treating old
manifest version as an exemption. Never mutate already accepted portable bytes
or repurpose `fact_ids`, which currently drives physical deletion.

The scrub transaction must invalidate affected temporal caches and remove
pending old publications before reopening is even possible. The worker then
uses the existing rebuild/purge stages and verifies the rebuilt store. A
provider failure leaves the existing forget barrier closed. Restore runs the
same purge, recomputes safe schedules, verifies residuals and only then permits
clock workers, ingestion or serving. This extends `HardForgetHandler.honor`; it
does not add a second lifecycle transition or a side-channel forget handler.

## 4. Alternatives and costs

- **Refresh only on ingest:** simplest operationally, but cannot satisfy future
  activation or expiry, and profiles presently exclude useful finite states to
  avoid exactly this problem.
- **Periodic full rebuild:** handles time eventually, but costs scale with all
  artifacts rather than due changes; it still needs stale-read checks. A rare
  integrity audit is useful, not the normal clock mechanism.
- **Regenerate synchronously on every read:** avoids cached lies but turns query
  latency and availability into provider dependencies, contrary to D9. Fresh
  deterministic fact fallback is appropriate; query-time prose generation is not.
- **Only schedule present rendered inputs:** misses future facts, rank promotion,
  structural rule changes and parent pages. Incorrect regardless of sweep rate.
- **Per-fact boundary router with certified artifacts (recommended):** adds
  indexes, durable nominations and certification checks, while reusing routing,
  the work ledger and publication machinery. Structural fences can temporarily
  suppress more subtree pages than actually changed; measure this availability
  cost and certify unchanged pages without model calls.
- **Retain correction dates as “audit” after forget:** violates the existing
  source-content erasure boundary. Minimal structural records and independent
  surviving justification are sufficient alternatives.
- **Delete every shared fact touched by forgotten evidence:** easier inventory,
  but destroys independently supported knowledge. Targeted recomputation is
  required; conservative broad scrubbing is appropriate only for untraceable
  derived/audit payloads, not an excuse to erase unrelated authority.
- **Build a new forget subsystem:** adds failure and reconciliation paths while
  duplicating a deployed barrier, portable log, scrubber and restore worker.
  Extending the existing inventory is both smaller and more complete.

At millions of documents, measure boundary-index size, due-batch transaction
latency, dependency fan-out, topology-fence catch-up, and provider work per
changed artifact. The acceptance criterion is indexed/bounded enumeration and
no global fact scan on ordinary reads; no guessed fixed batch size is a binding
product limit. Memory/row caps must checkpoint and resume rather than silently
skip a dependency.

## 5. Required amendments and acceptance evidence

#367 needs matching amendments in temporal §7.1/§12, entity profile publication,
K routing/manifests/serving, retrieval freshness and the schema. #368 needs the
hard-forget workflow/inventory/canary matrix, schema constraints and portable
manifest version/replay rules. The autonomous-correction amendment from #366
must define what temporal evidence justifies each bound; deletion cannot guess
that authority contract. Binding amendments should explicitly resolve D107's
boundary-time versus catch-up-time wording and D108's human-review retirement.

Existing tests are useful starting points, not proof of the new contract:
`src/tests/spine/test_profile_refresher.py` covers provider-time mutation and
empty-cache failure; `src/tests/spine/test_knowledge_control_plane.py` covers
exact manifests and subtree rematerialization;
`src/tests/spine/test_forget_catalog.py` already covers shared-survivor profiles
and old-database restore; `src/tests/workers/test_hard_forget_handler.py` covers
the all-store sequence and fail-closed behavior. Extend those real-database
paths with the following behavioral cases:

1. Future-start exclusion, finite-ended current inclusion, exact activation and
   half-open expiry, with no ingestion between reads. Profile prose, T3 vectors,
   T4 evidence, open SQL and P1 must agree.
2. A future high-ranked fact and a ninth-ranked fact promoted by expiry; relation
   facts refresh both endpoints and nested merge survivors.
3. An unseen future `part_of` edge activates a subtree member; expiry removes it.
   A page/rule created after scheduling still observes the right boundaries;
   parent/model summaries cannot mask stale children.
4. Crash between authority write and routing, schedule replacement, event claim
   and completion; two workers; replay; old generation jobs; missed activation
   and expiry during downtime coalesced at current E.
5. A provider or Git commit crosses a boundary or races a correction. Its old
   result is never certified current. A failed provider leaves deterministic
   fact retrieval available and stale metadata truthful.
6. Unique forbidden tokens and uniquely identifiable dates in seeds, correction
   before/after windows, failed verdicts, migration shadows, labels, vectors,
   transcripts and scheduled payloads. Forget removes all such derived content
   while independently justified dates and shared facts survive.
7. Delete the seed but retain another supporting source; delete a correction's
   evidence but retain an independent intervening cap. Replay and restart must
   not resurrect the removed dates or silently reseed identity.
8. Failure/retry after each forget stage, plus restore of a pre-forget database,
   older manifest format, P3 snapshot, K history and queued temporal job. Ordinary
   admission and clock work remain barred until the existing all-store verifier
   proves the extension's residual queries empty.

No tests were run for this analysis-only file. The concrete implementation and
its PostgreSQL-backed acceptance evidence remain required work.

## 6. DDL proposal and work-ledger integration (independent follow-up)

This section is a concrete candidate for the D110 schema amendment, not executed
DDL. The names below are proposed. They make the atomicity and queue-identity
requirements reviewable; the binding author may choose a simpler equivalent
only if it preserves the same behavior. Existing tables use UUID entity,
relation, observation, artifact and deployment keys. `entities` and
`knowledge_artifacts` have tenancy-safe `(deployment_id, id)` unique keys;
`processing_state.target_id` is deliberately a logical polymorphic reference.
See migrations `p0_02_0002_infrastructure_registries.py` (`processing_state`),
`p0_02_0003_entities_evaluation_e0_e1.py` (`entities`) and
`p0_02_0005_projection_knowledge_retrieval.py` (`knowledge_artifacts`,
`knowledge_page_rules`, `knowledge_rule_keys`), with D98 amendments in
`p9_17_0038_postgres19_live_graph.py`.

### 6.1 The work identity must change per event, not per generator

The existing unique work key is:

```
(deployment_id, target_kind, target_id, stage, component_version)
```

`content_hash` is not part of that key. `spine/work_ledger.py`, `_INSERT_WORK`
(the INSERT used by `enqueue_on`) uses `ON CONFLICT ... DO NOTHING`, and existing
work is only promoted between lanes when still pending/failed. Re-enqueuing
`refresh_profile` for Alice with the same generator after its previous success
therefore does not run it again. Do not encode time, artifact revision or an
arbitrary event UUID in `component_version`: it denotes the registered code and
model generation, not an invocation.

Recommend one new logical target and one unlaned stage:

```sql
ALTER TYPE processing_target ADD VALUE 'temporal_event';
ALTER TYPE pipeline_stage ADD VALUE 'refresh_temporal';
ALTER TYPE pipeline_component ADD VALUE 'temporal_refresher';
```

Apply enum additions in the migration's supported transaction ordering before
inserting work using them. `refresh_temporal` is registered as an unlaned stage
in the typed catalogs and worker route registry. Its registered component is `temporal_refresher`, with one real code/model
generation registered in `pipeline_component_versions`; the handler composes
existing profile and K services. There is no fabricated generation per
boundary, new delivery provider, custom retry counter, or timer thread.

A durable `fact_expiry_schedule.event_id` below becomes `target_id`, even when
its target is a routing key or page rather than a fact. The established D107
name is retained, with comments explaining that activation and immediate
invalidation belong to the same family. The work ledger remains the *only*
place holding execution status, attempts, defer reason, not-before and DLQ.

### 6.2 Source revision keys and indexed future-boundary membership

A source revision key denotes a deterministic set of inputs: a fact, entity,
predicate, document, document source, scope configuration, page rule owner, page
publication, or the deployment's structural routing. Both known dependencies
and not-yet-present candidates can name a key. For example, a predicate page
watches the predicate key even before any matching fact exists. Source IDs are
actual UUIDs where available; predicate/document-source keys use a deterministic
UUID derived from a versioned canonical key encoding. Raw free text is not
stored in these keys. Existence/type/deployment checks for logical IDs happen
inside the authority writer, as they already do for `processing_state` targets.

```sql
CREATE TABLE temporal_sources (
  deployment_id uuid NOT NULL REFERENCES deployments,
  source_kind text NOT NULL CHECK (source_kind IN (
    'relation','observation','entity','predicate','document','doc_source',
    'scope','rule_owner','page_publication','structural','broad_rule'
  )),
  source_id uuid NOT NULL,
  revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
  next_boundary_at timestamptz,
  PRIMARY KEY (deployment_id, source_kind, source_id)
);
CREATE INDEX ix_temporal_sources_next
  ON temporal_sources (deployment_id, next_boundary_at, source_kind, source_id)
  WHERE next_boundary_at IS NOT NULL;

-- Each fact is a leaf; memberships attach its two future boundaries to
-- deterministic routing keys. No recursive source-key graph is introduced.
CREATE TABLE temporal_source_members (
  deployment_id uuid NOT NULL,
  source_kind text NOT NULL,
  source_id uuid NOT NULL,
  fact_kind text NOT NULL CHECK (fact_kind IN ('relation','observation')),
  fact_id uuid NOT NULL,
  fact_revision bigint NOT NULL CHECK (fact_revision >= 0),
  activation_at timestamptz,
  expiry_at timestamptz,
  PRIMARY KEY (deployment_id, source_kind, source_id, fact_kind, fact_id),
  FOREIGN KEY (deployment_id, source_kind, source_id)
    REFERENCES temporal_sources (deployment_id, source_kind, source_id)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, fact_kind, fact_id)
    REFERENCES temporal_sources (deployment_id, source_kind, source_id)
    ON DELETE CASCADE
);
CREATE INDEX ix_temporal_members_activation
  ON temporal_source_members
    (deployment_id, source_kind, source_id, activation_at, fact_kind, fact_id)
  WHERE activation_at IS NOT NULL;
CREATE INDEX ix_temporal_members_expiry
  ON temporal_source_members
    (deployment_id, source_kind, source_id, expiry_at, fact_kind, fact_id)
  WHERE expiry_at IS NOT NULL;
CREATE INDEX ix_temporal_members_fact
  ON temporal_source_members (deployment_id, fact_kind, fact_id);
```

The two membership timestamps are routing projections of existing fact fields,
not second temporal authority. The authority write updates/removes memberships
for both old and new routing keys, then recomputes each touched source's
`next_boundary_at` as the least indexed `activation_at > E` or `expiry_at > E`.
Only supported, belief-current candidate facts contribute; a fact need not be
world-current yet. Every finite future `part_of` boundary contributes to the
`structural` key regardless of whether an active subtree page currently matches
it. An expired timestamp can remain in a membership until the worker visits it:
read-side comparisons treat an overdue `temporal_sources.next_boundary_at` as
uncertified, and recomputation deliberately searches only boundaries after E.

Fact leaf revisions and routing-key revisions increment on their mutations.
Locks are taken in the amendment's agreed global identity/fact order, then
source keys in `(source_kind, source_id)` order; publication must never invert
that order by retaining a source-key lock and waiting for a fact lock. Boundary
processing locks the affected facts/keys in the same order, verifies the
referenced revision, advances touched key revisions, recomputes minima at the
current E, and schedules their new minima atomically. Bulk structural/identity
changes may use bounded resumable routing while the structural key remains
uncertified. They cannot write a fresh structural certificate before all its
required key rematerializations have completed.

The membership table is justified by indexed recomputation of the minimum when
the earliest future fact is removed or corrected. Computing a minimum by
scanning every fact in a broad predicate/source is an avoidable scale cost.
The two partial indexes support seeking the next future activation/expiry for
one key. Transactions still write O(changed routing memberships), so high-fanout
rules and identity redirect closures need measured plans and bounded routing
checkpoints; no cap may silently discard a membership.

### 6.3 Cache certificates and dependency revision attestations

```sql
CREATE TABLE temporal_artifact_certificates (
  certificate_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES deployments,
  entity_id uuid,
  artifact_id uuid,
  generation text NOT NULL,
  input_hash text,
  evaluated_at timestamptz,
  fresh_until timestamptz,
  publication_revision bigint NOT NULL DEFAULT 0
    CHECK (publication_revision >= 0),
  CHECK (num_nonnulls(entity_id, artifact_id) = 1),
  CHECK ((input_hash IS NULL) = (evaluated_at IS NULL)),
  CHECK (evaluated_at IS NOT NULL OR fresh_until IS NULL),
  CHECK (fresh_until IS NULL OR fresh_until > evaluated_at),
  UNIQUE (deployment_id, certificate_id),
  FOREIGN KEY (deployment_id, entity_id)
    REFERENCES entities (deployment_id, entity_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, artifact_id)
    REFERENCES knowledge_artifacts (deployment_id, artifact_id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_temporal_profile_certificate
  ON temporal_artifact_certificates (deployment_id, entity_id)
  WHERE entity_id IS NOT NULL;
CREATE UNIQUE INDEX uq_temporal_page_certificate
  ON temporal_artifact_certificates (deployment_id, artifact_id)
  WHERE artifact_id IS NOT NULL;
CREATE INDEX ix_temporal_certificate_expiry
  ON temporal_artifact_certificates (deployment_id, fresh_until, certificate_id)
  WHERE fresh_until IS NOT NULL;

CREATE TABLE temporal_artifact_dependencies (
  deployment_id uuid NOT NULL,
  certificate_id uuid NOT NULL,
  source_kind text NOT NULL,
  source_id uuid NOT NULL,
  observed_revision bigint NOT NULL CHECK (observed_revision >= 0),
  PRIMARY KEY (deployment_id, certificate_id, source_kind, source_id),
  FOREIGN KEY (deployment_id, certificate_id)
    REFERENCES temporal_artifact_certificates (deployment_id, certificate_id)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, source_kind, source_id)
    REFERENCES temporal_sources (deployment_id, source_kind, source_id)
    ON DELETE RESTRICT
);
CREATE INDEX ix_temporal_dependencies_source
  ON temporal_artifact_dependencies
    (deployment_id, source_kind, source_id, certificate_id);
```

An entity/page may have a certificate handle with null input/evaluation while it
is missing or dirty. There is no stored `is_current` Boolean: current status is
a function of E, certificate fields and observed revisions. Replacing a
certificate's dependency set, exact input hash, deadline and cached bytes
requires one database publication transaction; K's Git publication pointer also
must refer to that exact content hash under the existing commit-recovery
protocol. Revalidation failure leaves the certificate null/stale, never partly
updated. Publication revision changes even when a rebuilt text hash is
identical but its dependency certification changes.

A current-check query has the following shape (the caller additionally checks
the required generation and cached content/embedding attestation):

```sql
SELECT c.certificate_id
FROM temporal_artifact_certificates c
WHERE c.deployment_id = :deployment_id
  AND c.certificate_id = :certificate_id
  AND c.generation = :generation
  AND c.input_hash IS NOT NULL
  AND c.evaluated_at <= :evaluation_time
  AND (c.fresh_until IS NULL OR :evaluation_time < c.fresh_until)
  AND NOT EXISTS (
    SELECT 1
    FROM temporal_artifact_dependencies d
    JOIN temporal_sources s USING (deployment_id, source_kind, source_id)
    WHERE d.deployment_id = c.deployment_id
      AND d.certificate_id = c.certificate_id
      AND (
        d.observed_revision <> s.revision
        OR (s.next_boundary_at IS NOT NULL
            AND s.next_boundary_at <= :evaluation_time)
      )
  );
```

The dependency set must contain required sentinel keys even for empty candidate
sets. Otherwise an empty dependency set would incorrectly certify a page
forever. Dependency completeness is established by the typed rule evaluator and
pinned generation, not by trusting a consumer-supplied list. A missing source
row cannot be silently omitted: restrictive FK deletion forces certificate
invalidation/removal first. Hard forget follows that order.

Parents include the flattened union of their consumed children's dependency
keys, in addition to child `page_publication` keys and the existing child-summary
hashes. This means a fact or structural deadline invalidates the parent before a
late child worker has republished anything. Flattening source-key dependencies
is deterministic over the existing compile DAG and bounded by keyset batches;
cycles remain a compile error. Do not hold a parent fresh on the strength of an
unchanged old child hash. Page publication keys increment when bytes or
certification changes, including clear/quarantine.

### 6.4 Boundary rows are event identities; processing_state schedules them

```sql
CREATE TABLE fact_expiry_schedule (
  event_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL REFERENCES deployments,
  source_kind text,
  source_id uuid,
  source_revision bigint,
  certificate_id uuid,
  certificate_revision bigint,
  event_kind text NOT NULL CHECK (event_kind IN (
    'activation','expiry','invalidation','artifact_refresh','routing_resume'
  )),
  boundary_at timestamptz NOT NULL,
  generator_version text NOT NULL,
  cursor_kind text,
  cursor_id uuid,
  CHECK (
    (source_kind IS NOT NULL AND source_id IS NOT NULL
     AND source_revision IS NOT NULL AND source_revision >= 0
     AND certificate_id IS NULL AND certificate_revision IS NULL)
    OR
    (source_kind IS NULL AND source_id IS NULL AND source_revision IS NULL
     AND certificate_id IS NOT NULL AND certificate_revision IS NOT NULL
     AND certificate_revision >= 0)
  ),
  CHECK ((cursor_kind IS NULL) = (cursor_id IS NULL)),
  UNIQUE (deployment_id, event_id),
  FOREIGN KEY (deployment_id, source_kind, source_id)
    REFERENCES temporal_sources (deployment_id, source_kind, source_id)
    ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, certificate_id)
    REFERENCES temporal_artifact_certificates (deployment_id, certificate_id)
    ON DELETE CASCADE
);
CREATE UNIQUE INDEX uq_temporal_source_event
  ON fact_expiry_schedule (
    deployment_id, source_kind, source_id, source_revision,
    event_kind, boundary_at, generator_version
  ) WHERE source_kind IS NOT NULL;
CREATE UNIQUE INDEX uq_temporal_artifact_event
  ON fact_expiry_schedule (
    deployment_id, certificate_id, certificate_revision,
    event_kind, boundary_at, generator_version
  ) WHERE certificate_id IS NOT NULL;
CREATE INDEX ix_temporal_event_source
  ON fact_expiry_schedule (deployment_id, source_kind, source_id)
  WHERE source_kind IS NOT NULL;
CREATE INDEX ix_temporal_event_artifact
  ON fact_expiry_schedule (deployment_id, certificate_id)
  WHERE certificate_id IS NOT NULL;
CREATE INDEX ix_temporal_event_boundary
  ON fact_expiry_schedule (deployment_id, boundary_at, event_id);
```

`boundary_at` is event semantics/identity, not an independently polled ready
queue. The mutation transaction uses `enqueue_on(connection=..., work=...)` to
insert its work row with `target_kind='temporal_event'`, this event ID,
`stage='refresh_temporal'`, `lane=NULL`, registered component version, and
`not_before=boundary_at`. Immediate invalidation fixes its event timestamp once
in the mutation transaction. Existing due polling and NOTIFY wake the worker;
the schedule table has no work-status column. Pipeline work payload contains
only an event ID and generation; the durable row owns the inspectable routing
cursor. No arbitrary SQL, source prose or verdict snapshot belongs there.

Routing source events only certify routing/update stale flags and enqueue
artifact-refresh events atomically with their own completion. Artifact events
invoke the existing profile refresher or K driver, not another writer or Git
committer. Each nominated refresh has its own certificate revision, so a new
input has a new event even when the generator is unchanged. The handler may
coalesce other pending due events for the same source/artifact at current E,
but marks them skipped/complete only after atomically recording which current
revision superseded them; it must not change another worker's running row.
Handler claim/revalidation detects expired source/certificate revisions and
returns the existing skipped outcome without model work.

An extended routing walk uses cursor fields for work progress, not retries. It
must retain the relevant stale fence while yielding and enqueue a new
`routing_resume` event with a new deterministic continuation identity if it
finishes the current processing row. Prefer resumable bounded enumeration
within the existing attempt/checkpoint contract where available. The binding
implementation must specify how continuation identity incorporates its cursor;
the DDL unique key above would require a new event boundary/revision or an
explicit continuation sequence column if multiple completed continuation events
share all other fields. Do not fake timestamps for uniqueness. A precise
sequence extension is:

```sql
ALTER TABLE fact_expiry_schedule
  ADD COLUMN continuation_sequence bigint NOT NULL DEFAULT 0
    CHECK (continuation_sequence >= 0);
DROP INDEX uq_temporal_source_event;
CREATE UNIQUE INDEX uq_temporal_source_event
  ON fact_expiry_schedule (
    deployment_id, source_kind, source_id, source_revision,
    event_kind, boundary_at, generator_version, continuation_sequence
  ) WHERE source_kind IS NOT NULL;
```

On rollback neither nomination nor work exists. On restart the existing due
ledger discovers it. On obsolete revision, skip it. On forget, remove/scrub event
rows and pending work as inventory permits; a missing event in a still-present
content-free work record is a terminal skipped outcome, not regeneration.
Retain ordinary work/cost audit structural records according to D74. A generation
roll creates new event/work identities under the new registered generation and
invalidates certificates before serving resumes.

### 6.5 Rule-to-source dependency and invalidation matrix

| Target | Required source keys and future routing |
| --- | --- |
| Entity profile | Its entity key; every relevant merged-member entity key; identity/structural fence where membership is being rebuilt; direct fact keys for selected current facts. Entity-key membership includes all supported future candidates before rank limits and both relation endpoints. |
| K `entity` rule | Entity key plus rule-owner key; optional predicate restriction in exact evaluation. Direct IDs alone cannot watch a future matching fact. |
| K `predicate_beat` | Predicate key plus optional endpoint entity keys and rule-owner key. Predicate membership contains future facts. |
| K `entity_subtree` | Structural sentinel key, rule-owner key and entity keys for present members. Every `part_of` future boundary updates the structural sentinel even when it currently connects no matched member. At a boundary route old and new closure memberships. |
| K `doc_set` | Stable document/document-source keys when the filter supplies them, rule-owner key, and broad-rule sentinel for filters without a complete indexed nomination key. The exact metadata filter still decides membership. |
| K `scope_interests` | Scope configuration key, rule-owner key and all available entity/predicate/document-source nomination keys; broad-rule sentinel covers the existing coarse fallback. D96 removes entity-type interests, and D98 removes community interests. |
| K `manual` | Rule-owner key and explicit fact/entity/document keys. Explicit future fact IDs are dependencies while absent from current rendered inputs. |
| Parent/shared-model consumer | Child publication keys and flattened child/model dependency keys plus own rules/curation configuration. |
| Authored page/subscription | Same rule sources as compiled rules, but consequence is evidence flag/dispatch; there is no generated authored body certificate. A subscription must receive activation/expiry deltas even when there is no compiled page. |

`knowledge_rule_keys` currently admits entity, predicate and document-source
keys after D98; do not assume arbitrary new kinds already work there. The
proposed temporal source-key index is a correctness fence and nomination aid;
`KnowledgeControlPlane.route_delta` and exact existing rule evaluators remain
the routing authority. If broad fallback source keys invalidate many pages,
route/certify them in indexed owner batches without invoking writers for equal
manifests. This is a deliberate measurable cost for rules without narrower
mechanical nomination, not permission to discard those rules.

### 6.6 Consumer inventory requiring verification

The following is the complete cache-consumer inventory identified in this
checkout. Implementers must repeat the searches after integration because
additional consumers may land concurrently. Transport wrappers must inherit a
checked shared read; adding guards only to CLI or one API endpoint is
insufficient.

| Consumer/source | Required behavior |
| --- | --- |
| `spine/profile_refresher.py`, `load_entity_profile_evidence_many`, `current_profile_entity_ids`, preparation and publication | Check certificates and full current fact predicate at E. Never attest a provider result after its deadline. |
| `spine/resolver.py`, `_snapshot_from_rows`, T3 cached-vector acceptance and T4 candidate construction | Suppress stale cached summary/vector authority; provide current independently selected facts. Revalidate before identity commit. |
| `spine/clustering.py`, calls to `current_profile_entity_ids`; `spine/profile_convergence.py` | No nomination/convergence merge using an uncertified vector. Preserve post-success convergence on temporal refresh. |
| `adapters/postgres_p1.py`, `search_entities_scored`, entity vectors via `_natural_vectors`, and any direct entity-vector nomination | Filter stale profile vectors before ranking/acceptance; hydration alone does not recover candidates displaced by stale vectors. Preserve candidate recall limits and disclose resulting unavailable channel/partial nomination according to existing envelopes. |
| Published `memory_v1.entities_current` profile column, graph entity-source views in `p9_18_0039_graph_entity_provenance_plan.py`, and `spine/graph_catalog.py` properties | Null stale summary and expose certification fields where appropriate. Entity existence and graph membership remain unchanged. Property-graph routes cannot leak unguarded cached prose. |
| `surfaces/query_sandbox/nomination.py`, entity confirmation fields; `spine/query_space/catalog.py`, source definitions and generated manifest | Carry truthful profile/K freshness through confirmation and direct published-view reads; roll descriptors and generated artifacts with semantics. Direct SQL must not require a hidden application check. |
| `spine/knowledge.py`, `_input_snapshot`, writer bundles, parent/model page summary loads, planner snapshots | Only consume current summaries for current compilation; otherwise mark unavailable/stale and block certification or use an explicitly defined current-fact fallback. A planner must not treat expired page prose as fresh evidence. |
| `workers/knowledge_driver.py`, compilation ordering and finalization; `workers/knowledge_writer.py`, `knowledge_fact_sheet.py`, `knowledge_authored.py` | Preserve evaluation instant and certificate through final publication, failure and commit recovery. Authored evidence flags/dispatch remain distinct from machine rewrite. |
| `surfaces/query_engine.py`, K page hydration around `page_summary` / `last_compiled_at`; `model/envelope.py`, page result | Return checked stale state and evaluation/deadline, not only stored `status='active'`. API/CLI/MCP use this envelope. |
| Published K view catalog (`spine/query_space/catalog.py`, `page_summary`), saved/open-query page access | Same checked state for SQL consumers, with explicit evaluated-at and freshness data. |
| `spine/projection.py`, `_SELECT_CORPUS_ENTITIES`; `workers/p3.py`, `CorpusFsBuilder` | Export checked descriptions and evaluation/deadline metadata. P3 snapshot publication does not confer permanent profile freshness. |
| `adapters/selfhost/mounts.py`, real knowledge-root view; mounted Markdown/Git page body | Plain filesystem access bypasses database hydration. Include immutable evaluated-at/deadline in bytes and clearly historical meaning; engine-owned current access must enforce current certification. Existing downloaded files cannot be retroactively changed. |
| `workers/forget.py`, `HardForgetHandler.honor`; restore readiness and projection rebuild | Clear/invalidate certification before rebuild, reject stale queued publications, and reopen only after existing all-store verification covers new temporal storage. |

Do not change facts' current/history eligibility merely because an orientation
cache is stale. A historical profile query also cannot reuse today's certificate
for an earlier E; unless a historical profile is actually built and certified,
return historical facts with the cache absent.

### 6.7 Portable manifest extension and erasure SQL obligations

The JSON format change requires typed support for both accepted version-1 bytes
and new version-2 bytes, not a schema check that silently permits unparsed fields.
Proposed additional canonical arrays for version 2:

```json
{
  "schema_version": 2,
  "affected_relation_ids": [],
  "affected_observation_ids": [],
  "temporal_verdict_ids": [],
  "temporal_event_ids": [],
  "temporal_certificate_ids": []
}
```

This excerpt supplements every existing manifest field. Each value is a sorted,
duplicate-free UUID set. The separate relation and observation arrays avoid assuming UUID uniqueness
across distinct authority tables. These sets
are erasure/recomputation nominations, never interpreted by existing physical
`fact_ids` deletion statements. No dates, rationale, free-text keys, evidence
quotes or provider credentials belong in the portable log.

`forget_manifests.schema_version` is already a smallint and its `manifest` is
JSONB, so storing v2 needs no extra JSON columns in PostgreSQL. Required changes
are the `ForgetManifest` typed union/parser/canonical-byte logic, pre-scrub
inventory and residual verifier. Preserve existing v1 canonical bytes exactly:
adding empty defaults during reserialization would change their accepted hash.
Version-specific models or preserving validated original bytes avoids this trap.
For v1 replay against a new store, derive affected new rows from persisted
claim/doc IDs before removing them, including indirect correction dependencies.
No new append log is introduced and previously accepted entries are immutable.

New scrub SQL must explicitly perform the following within the existing
barrier/workflow, in dependency order:

1. Collect the complete affected fact/verdict/cache/event closure under the
   drained acceptance cut, including shared survivors and pre/post correction
   snapshots. Save content-free identities in v2 inventory.
2. Invalidate affected certificates and clear dependent cached fields/vectors;
   remove artifact dependency rows before restrictive source FK deletion.
3. Remove obsolete event nominations/payloads and apply terminal skip/scrub to
   their ordinary work rows. Preserve only allowed structural cost/work records.
4. Null source seed/triggering references and scrub or delete temporal verdict
   payloads; reconcile affected surviving bounds using #366's authority contract.
5. Recompute occurrence windows, derived labels and source memberships/minima
   from cleaned surviving facts; delete exclusive fact leaf sources and their
   memberships. Directly source-bearing orphan key hashes may be retained only
   under D74's existing content-free-hash policy.
6. Rebuild safe future nominations and refresh certificates using existing
   workers. Schedule rows referencing erased revisions cannot resurrect values.
7. Residual queries verify forgotten seed/dependency references, payload dates,
   erased corrections, migration shadows, pending stale publications and
   certificates with removed source inputs are absent. Repeat after restore.

The source-membership timestamps themselves belong in the delete/recompute
inventory: they duplicate boundaries even though they are “only an index.”
This closes the otherwise easy mistake of carefully erasing occurrence fields
while leaving the same date in a routing helper table.

## 7. Sanitized replay checkpoints for hard forget

This section resolves the remaining replay hole identified in
`temporal_autonomous_corrections.md` §10.5. It supersedes this document's earlier
unspecified “scrub or delete” choice for temporal replay roots. It is a proposed
complete contract, not accepted architecture or an executed migration.

### 7.1 Choose closed block checkpoints, with existing narrative logs

A checkpoint is the cleaned state of a finite set of adjudication blocks at an
accepted forget barrier. It is a replay starting point, not a new adjudicator.
For example, after forgetting the sole source of Alice's 2019 start, retain her
independently established 2025 end and existing belief invalidation, clear the
start, and save exactly that clean state. Replay may start there; it may not
replay the erased 2019 seed or a before-snapshot containing that date.

Recommend **block-closure checkpoints**, rather than attempting minimal per-fact
replay surgery. Start with blocks owning affected facts and operations whose
payload/support references the lineage. Expand through every multi-block
operation, and through pre-cut operation dependencies/read-footprints crossing
from an uncovered block into a covered one. Repeat to a fixed point. Every fact
in a covered block gets a clean snapshot if it survives. Include all pre-cut
operations in those blocks in the covered set, even clean ones. If a historical
operation has no complete read-footprint, conservatively include its complete
recorded logical block; if even its block cannot be established, that deployment
must use a full temporal checkpoint under the same barrier. No incomplete
prefix can be called a verified checkpoint.

This can checkpoint more unaffected facts than exact per-fact surgery, but it
eliminates guessed revision substitutions and preserves the sequencer's
meaning. The existing deployment forget fence already drains writers. A rare
forget can pay a large bounded, checkpointed enumeration cost; it must not turn
that cost into a second scheduler or an unbounded in-memory closure. Store work
progress in the existing forget processing record/manifest preparation state,
with IDs-only batch cursors. A block root has a known resume sequence/revision,
and no live pre-cut operation straddles a covered/uncovered block boundary.

All historical operations are still explained by existing relation/observation
adjudication logs. Clean covered receipts can retain their audit payloads;
source-bearing covered receipts become payload-free tombstones retaining only
identity, order, result classification, safe generation IDs and links. Neither
kind is replayed below the checkpoint frontier. A tombstone is a trace of an
erased operation, not an operation that can be force-applied.

### 7.2 Reuse whole-operation support conservatively

Do not introduce a second semantic proof engine. Reuse
`temporal_operation_evidence` as the complete claim-input set and
`temporal_operation_dependencies` as the operation-input set. Add a small
attestation saying whether the complete accepted support is available. Ordinary
apply records every substantive claim consumed by the accepted judgement,
including contrary/candidate inputs, with the existing role/fingerprint fields.
A claimed proof is incomplete when required prompt input was truncated or only
an evidence subset was retained. Historical uninstrumented operations are
`unproven`, never assumed complete.

Distinguish an operation's **ordering dependency** from a **semantic premise**.
An operation can follow the seed in the block stream without deriving its end
boundary from that seed's start. Add an explicit `required_for_semantics` flag
to existing dependency edges; default it true for unclassified old edges.
Endpoint ownership pointers and block sequences still enforce execution order.
A flag can be false only when the typed writer records that the predecessor
supplies sequencing/precondition history rather than evidence needed to justify
the chosen endpoint. This is catalog-authored metadata, not model-supplied SQL.

To preserve an existing endpoint, select an already accepted operation that
established **that exact endpoint and basis**, whose complete required claim set
survives, and whose semantic-operation dependencies recursively remain
independent. No source-date search, `min`/`max`, inference rerun, or selection of
a merely similar historical claim is allowed. An operation that changed only
the start cannot justify an inherited end copied in its new-state tuple. An
unchanged endpoint can be certified only by its recorded owner/accepted explicit
attestation, not by incidental duplication in another operation snapshot.
Use the same complete-operation support conservatively for both endpoints when
one judgement changed both: if any required support is erased, neither endpoint
is independently justified by that operation. An independent later cap survives
because its own accepted support remains complete. A later cap that actually
relied on the erased source is not independent and must clear.

Capture the support classification before deleting evidence rows. Persist
`support_state='erased'` when any required member disappears; otherwise deleting
the offending member could turn an incomplete set into a falsely “complete”
smaller one. References retained in checkpoint component attestations contain
IDs and exact-value fingerprints, not copied rationale or old erased bounds.
Dates are stored only in the clean root state that those surviving attestations
justify. If no complete proof exists for a formerly known endpoint, clear its value
and record basis `erased`; an already unknown endpoint remains `unknown`.
The existing `invalidated_at` is belief time: preserve it exactly, independently
of endpoint support, and never reopen belief while erasing source content.

### 7.3 Unknown after erasure must remain visibly uncertain

Clearing a known bound to null may widen a state range so it overlaps another
same-key state. The existing exclusion in
`p0_02_0004_claims_facts_evidence.py` excludes invalidated rows and rows with
`contradiction_group`; D107 adds the state-kind predicate. Keeping an erased
date to satisfy that index is forbidden.

I considered reusing `contradiction_group`. It already means competing live
facts that cannot be adjudicated, and `retrieval_design.md`'s S23 requires
co-member disclosure. Possible temporal overlap after erasure does not establish
contradictory testimony. Reusing the UUID would require a new group-reason
discriminator and changes to every raw SQL/envelope consumer that currently
labels such groups as contradictions. It does not become simpler by omitting
that semantic distinction.

Recommend extending the existing endpoint basis vocabulary with **`erased`**.
A formerly known start/end whose retained justification was erased becomes
`NULL` with basis `erased`; an endpoint already unknown remains `NULL/unknown`.
This is a provenance distinction in the field already responsible for
provenance, not an extra two-bit mask or another clock. Preserve fact identity,
independent other endpoint, existing contradiction membership and belief
closure. The interval exclusion applies only when neither endpoint basis is
`erased`. A future accepted correction may establish an erased endpoint by the
same grounded path as an unknown endpoint, recording its new `verdict` basis;
no timer or occurrence recomputation may relabel it. Explicitly amend D107's
basis enum, statement that unknown is the only missing-bound basis, operation
snapshot basis checks, schema, labels and already-planned envelope basis fields.

Current eligibility becomes three-valued for these rows. If known retained
bounds or belief closure rule out E, the fact is not current. If an erased
bound could affect membership at E, membership is **unknown**, not confidently
current. APIs and SQL expose the existing per-endpoint basis fields with the
new value and disclose membership uncertainty. Profiles, T3 vectors and current
K prose must not state such a role as current. Ordinary fact retrieval can
return it under an explicit uncertain annotation; complete counts, absence and
“currently” answers disclose excluded uncertain rows rather than interpreting
their omission as a negative. Historical evidence remains retrievable through
its separate grain. No arbitrary identity merge, state deletion or fabricated
contradiction is performed to make the index pass.

### 7.4 Exact proposed PostgreSQL additions

This migration fragment assumes §9's `temporal_operations`/block/evidence tables
from the corrections analysis, consolidated per its §10. It uses the existing
fact basis columns `valid_from_basis`/`valid_until_basis`. Rename only those
symbols if the binding schema chooses different names. This assumes T.1
supplies the `fact_temporal_basis` enum for fact columns; the corrections
analysis currently uses text-plus-CHECK bases on operation receipts, so both are
amended below. These are local-shape constraints; §7.5's transaction verifier
supplies cross-row proof.

```sql
-- T.1's binding schema owns this enum; commit enum addition before its use.
ALTER TYPE fact_temporal_basis ADD VALUE 'erased';
ALTER TABLE relations
  ADD CHECK (valid_from_basis::text <> 'erased' OR valid_from IS NULL),
  ADD CHECK (valid_until_basis::text <> 'erased' OR valid_until IS NULL);
ALTER TABLE observations
  ADD CHECK (valid_from_basis::text <> 'erased' OR valid_from IS NULL),
  ADD CHECK (valid_until_basis::text <> 'erased' OR valid_until IS NULL);

-- Names below are the default constraint names in the corrections analysis DDL.
ALTER TABLE temporal_operations
  DROP CONSTRAINT temporal_operations_old_from_basis_check,
  DROP CONSTRAINT temporal_operations_old_until_basis_check,
  DROP CONSTRAINT temporal_operations_new_from_basis_check,
  DROP CONSTRAINT temporal_operations_new_until_basis_check,
  ADD CHECK (old_from_basis IN
    ('world_time','verdict','source_removed','legacy','unknown','erased')),
  ADD CHECK (old_until_basis IN
    ('world_time','verdict','source_removed','legacy','unknown','erased')),
  ADD CHECK (new_from_basis IN
    ('world_time','verdict','source_removed','legacy','unknown','erased')),
  ADD CHECK (new_until_basis IN
    ('world_time','verdict','source_removed','legacy','unknown','erased')),
  ADD CHECK (old_from_basis <> 'erased' OR old_valid_from IS NULL),
  ADD CHECK (old_until_basis <> 'erased' OR old_valid_until IS NULL),
  ADD CHECK (new_from_basis <> 'erased' OR new_valid_from IS NULL),
  ADD CHECK (new_until_basis <> 'erased' OR new_valid_until IS NULL);

CREATE TABLE temporal_operation_support (
  deployment_id uuid NOT NULL,
  operation_id uuid NOT NULL,
  support_state text NOT NULL CHECK
    (support_state IN ('complete','erased','unproven')),
  expected_claim_count integer NOT NULL CHECK (expected_claim_count >= 0),
  expected_semantic_dependency_count integer NOT NULL
    CHECK (expected_semantic_dependency_count >= 0),
  support_fingerprint text NOT NULL,
  PRIMARY KEY (deployment_id, operation_id),
  FOREIGN KEY (deployment_id, operation_id)
    REFERENCES temporal_operations (deployment_id, operation_id)
);
ALTER TABLE temporal_operation_dependencies
  ADD COLUMN required_for_semantics boolean NOT NULL DEFAULT true;

CREATE TABLE temporal_forget_checkpoints (
  checkpoint_id uuid PRIMARY KEY,
  deployment_id uuid NOT NULL,
  forget_id uuid NOT NULL,
  checkpoint_format integer NOT NULL DEFAULT 1 CHECK (checkpoint_format = 1),
  state text NOT NULL DEFAULT 'preparing'
    CHECK (state IN ('preparing','verified','superseded')),
  created_at timestamptz NOT NULL,
  verified_at timestamptz,
  superseded_by uuid,
  inventory_hash text NOT NULL,
  policy_generation text NOT NULL,
  CHECK ((state = 'preparing') = (verified_at IS NULL)),
  CHECK ((state = 'superseded') = (superseded_by IS NOT NULL)),
  CHECK (superseded_by IS DISTINCT FROM checkpoint_id),
  UNIQUE (deployment_id, checkpoint_id),
  UNIQUE (deployment_id, forget_id),
  FOREIGN KEY (deployment_id, forget_id)
    REFERENCES forget_manifests (deployment_id, forget_id),
  FOREIGN KEY (deployment_id, superseded_by)
    REFERENCES temporal_forget_checkpoints (deployment_id, checkpoint_id)
);

CREATE TABLE temporal_checkpoint_blocks (
  deployment_id uuid NOT NULL,
  checkpoint_id uuid NOT NULL,
  block_key text NOT NULL,
  covered_through_sequence bigint NOT NULL CHECK (covered_through_sequence >= 0),
  covered_through_revision bigint NOT NULL CHECK (covered_through_revision >= 0),
  resume_sequence bigint NOT NULL CHECK (resume_sequence >= 0),
  resume_revision bigint NOT NULL CHECK (resume_revision >= 0),
  CHECK (resume_sequence >= covered_through_sequence),
  CHECK (resume_revision >= covered_through_revision),
  PRIMARY KEY (deployment_id, checkpoint_id, block_key),
  FOREIGN KEY (deployment_id, checkpoint_id)
    REFERENCES temporal_forget_checkpoints (deployment_id, checkpoint_id),
  FOREIGN KEY (deployment_id, block_key)
    REFERENCES temporal_blocks (deployment_id, block_key)
);
CREATE INDEX ix_temporal_checkpoint_block
  ON temporal_checkpoint_blocks (deployment_id, block_key, resume_sequence);

ALTER TABLE temporal_operations
  ADD COLUMN replay_class text NOT NULL DEFAULT 'ordinary'
    CHECK (replay_class IN ('ordinary','checkpoint_root','covered')),
  ADD COLUMN covered_by_checkpoint_id uuid,
  ADD CHECK ((replay_class = 'covered') =
    (covered_by_checkpoint_id IS NOT NULL)),
  ADD FOREIGN KEY (deployment_id, covered_by_checkpoint_id)
    REFERENCES temporal_forget_checkpoints (deployment_id, checkpoint_id);

CREATE TABLE temporal_checkpoint_facts (
  deployment_id uuid NOT NULL,
  checkpoint_id uuid NOT NULL,
  fact_kind text NOT NULL CHECK (fact_kind IN ('relation','observation')),
  fact_id uuid NOT NULL,
  root_operation_id uuid NOT NULL,
  temporal_kind text NOT NULL CHECK (temporal_kind IN ('state','occurrence','unknown')),
  occurs_from timestamptz,
  occurs_until timestamptz,
  occurs_precision text NOT NULL CHECK (occurs_precision IN
    ('instant','day','month','quarter','year','open','unknown')),
  seed_claim_id uuid,
  ingested_at timestamptz NOT NULL,
  temporal_revision bigint NOT NULL CHECK (temporal_revision >= 0),
  CHECK (occurs_from IS NULL OR occurs_until IS NULL OR occurs_until > occurs_from),
  CHECK (occurs_precision <> 'unknown'
    OR (occurs_from IS NULL AND occurs_until IS NULL)),
  PRIMARY KEY (deployment_id, checkpoint_id, fact_kind, fact_id),
  UNIQUE (deployment_id, checkpoint_id, root_operation_id),
  FOREIGN KEY (deployment_id, checkpoint_id)
    REFERENCES temporal_forget_checkpoints (deployment_id, checkpoint_id),
  FOREIGN KEY (deployment_id, root_operation_id)
    REFERENCES temporal_operations (deployment_id, operation_id),
  FOREIGN KEY (deployment_id, seed_claim_id)
    REFERENCES claims (deployment_id, claim_id)
);
CREATE INDEX ix_temporal_checkpoint_fact
  ON temporal_checkpoint_facts (deployment_id, fact_kind, fact_id);
CREATE INDEX ix_temporal_checkpoint_seed
  ON temporal_checkpoint_facts (deployment_id, seed_claim_id)
  WHERE seed_claim_id IS NOT NULL;

CREATE TABLE temporal_checkpoint_components (
  deployment_id uuid NOT NULL,
  checkpoint_id uuid NOT NULL,
  root_operation_id uuid NOT NULL,
  component text NOT NULL CHECK (component IN ('from','until','kind')),
  supporting_operation_id uuid NOT NULL,
  value_fingerprint text NOT NULL,
  PRIMARY KEY (deployment_id, checkpoint_id, root_operation_id, component),
  FOREIGN KEY (deployment_id, checkpoint_id, root_operation_id)
    REFERENCES temporal_checkpoint_facts
      (deployment_id, checkpoint_id, root_operation_id),
  FOREIGN KEY (deployment_id, supporting_operation_id)
    REFERENCES temporal_operation_support (deployment_id, operation_id)
);
```

The fact snapshot deliberately reads its clean world bounds, bases and belief
invalidation from its root operation's `new_*` fields; it does not duplicate
those fields in a second mutable table. It stores only the extra temporal
columns missing from the operation receipt. Root operations are
`operation_kind='forget_recompute', result='applied'`, increment the one fact
revision, and carry **clean** old/new fields (equal values are permitted by the
proposed receipt checks). Their `replay_class='checkpoint_root'` declares that
they restore a verified snapshot, not an ordinary before-state diff. They link
an erasure adjudication in the existing plane log with a fixed content-free
reason, never a new narrative ledger. Both endpoint ownership pointers on the
fact become the root ID; compensation is not allowed to reverse a checkpoint
root and resurrect its covered history.

Retaining covered receipt IDs after exclusive fact deletion conflicts with the
corrections analysis's physical target FKs. Choose logical historical targets,
as `processing_state` already does, and retain the existing XOR target check:

```sql
ALTER TABLE temporal_operations
  DROP CONSTRAINT temporal_operations_deployment_id_relation_id_fkey,
  DROP CONSTRAINT temporal_operations_deployment_id_observation_id_fkey;
```

These are the automatic FK names produced by that analysis's exact DDL. The
live catalog validates fact existence/tenant under locks before every ordinary
operation insert; a missing logical target is allowed only for a covered
receipt under a verified forget checkpoint. Current fact endpoint pointers
still physically reference existing operation IDs. `temporal_checkpoint_facts`
uses a logical target for the same reason: later forget must scrub/remove a
historical snapshot of a fact that becomes exclusive. No current public view
may turn a retained historical handle into a live fact.

Recreate the existing relation exclusion with its actual constraint name read
from the catalog, preserving deployment/subject/predicate/object and range
operands, while adding `temporal_kind = 'state' AND valid_from_basis <> 'erased'
AND valid_until_basis <> 'erased'`
to the existing `invalidated_at IS NULL AND contradiction_group IS NULL`
predicate. Do not hardcode an invented constraint name. A literal replacement
DDL depends on the binding T.1 migration's chosen name; this analysis does not
execute a catalog rename or weaken that constraint on the current branch.

### 7.5 Ordered checkpoint formation, publication and replay

1. **Freeze and inventory.** Under the existing preparing/accepted forget fence,
   drain ordinary processing. Enumerate the block closure to fixed point and
   persist its identities in the content-free manifest extension. Capture each
   block's current sequence/revision. All worker, correction, identity and clock
   paths remain barred from new authority writes. An unverified partial closure
   never permits scrub/publication.
2. **Classify clean authority before scrubbing.** For every surviving fact,
   evaluate each current endpoint's existing owner and complete required
   support. Preserve exact independently justified values/bases; clear the
   others and set their basis to `erased`. A pre-existing genuinely
   unknown endpoint keeps basis `unknown`; null alone does not imply erasure.
   Preserve belief invalidation and ingestion instants. Recompute occurrence
   metadata from surviving eligible evidence, explicitly excluding the accepted
   manifest's nominated lineage/claims even while their physical rows still exist
   during preparation. Preserve `state`/`occurrence`
   shape only where retained accepted support establishes it; otherwise use
   `unknown`, disclosed as erasure uncertainty rather than inventing a new
   identity. Null forgotten seed references without reseeding.
3. **Stage clean roots idempotently.** Derive checkpoint/root UUIDs from forget
   ID and fact identity. Insert the single checkpoint set and each clean root
   operation/snapshot/component attestation. One fact/root batch transaction
   records its receipt and state update atomically; record root sequence numbers
   through the same block sequencer. Prepared staging holds only sanitized
   values. It must never persist an additional pre-erasure full snapshot whose
   own cleanup would become recursive. Existing operation history is inspected
   under the fence until scrub. Retried root IDs return the same clean state;
   they do not select evidence again from a partially scrubbed store.
4. **Cover and scrub.** Mark every pre-cut operation in covered blocks as
   `covered`, pointing to the set. Remove source-bearing dates, before/after
   payloads, rationale/transcripts and discrepancy prepared snapshots from
   contaminated receipts and their linked existing adjudications. Set erased
   receipts' before/after bounds and invalidation snapshots to null, bases to
   `erased`, changed flags false, fingerprint to a content-free erasure digest
   and reason to `hard_forget_checkpointed`; preserve original operation IDs,
   sequencing and revision numbers. Existing local CHECKs still hold because
   both old/new sides are sanitized identically. Clear any source-bearing
   optional discrepancy reference before deleting that discrepancy. A retained
   `reverses_operation_id` is a structural reference to a covered tombstone,
   never permission to replay it. Set affected support attestations to erased
   before their claim links disappear.
5. **Repair current pointers; verify the frontier.** Live fact endpoint owners
   point at clean roots. Preserve assertion receipt IDs/results and map their
   original effects to covered operation IDs; they prove “already applied” but
   must never replay erased bounds. Block `resume_*` values include root
   operations. Verify no pre-cut ordinary operation overlaps a covered block,
   no multi-block operation crosses the covered frontier, all required fact
   roots exist, every retained component fingerprint equals the root value,
   and every support attestation recursively remains complete. Validate fact
   shape, belief closure, erased-basis predicates, tenancy, neighbour rules and
   occurrence recomputation. Update cache/source revisions and safe schedules
   in the same guarded publication sequence.
6. **Publish under the existing forget barrier.** Atomically mark the set
   verified once database roots and frontier checks pass. The deployment still
   does not reopen: existing object/P3/K purge and all-store verification must
   finish. A verified checkpoint is the replay root during those retries, not
   evidence that the entire forget request completed.
7. **Replay.** Load the newest verified applicable checkpoint roots for covered
   blocks, restore their clean temporal rows and resume counters, and skip
   covered pre-frontier operations. Replay only later ordinary operations in
   persisted dependency order. A later operation may depend on a checkpoint
   root; it may not treat an arbitrary covered predecessor as an executable
   operation. Encountering a covered dependency outside the verified frontier,
   a missing root, an incomplete support set, or a revision mismatch fails
   readiness. No general “ignore missing dependencies after forget” switch is
   introduced. Full fact/evidence/identity rebuilding retains its existing
   contracts; these roots restore the temporal portion, not fabricated facts
   whose independent statement support was deleted.

Root verification is a cross-row catalog operation, not a CHECK constraint.
It needs bounded keyset scans, dependency cycle detection, exact counts and
hashes, a complete closure marker, and concrete residual SQL. A cycle or a
missing dependency is an integrity failure, not an opportunity to guess an
endpoint. Unknown clean state is valid and does not require a model/human
approval; a structurally incomplete checkpoint is invalid and cannot reopen.

### 7.6 Repeated forget, older manifests, and crash recovery

A later forget treats checkpoint roots, component support, retained operation
support, prior snapshot occurrence fields and source memberships as ordinary
source-bearing inventory. It computes closure from the current live facts *and*
all historical roots/attestations that reference the new lineage. Any prior root
containing erased dates is scrubbed as a covered receipt; its snapshot is
removed or sanitized, and its component attestations are removed before their
supporting claims are deleted. The new verified set supplies fresh clean roots.
An old checkpoint set is marked wholly superseded only when every one of its
blocks has a newer verified replacement. Otherwise keep the set verified for
its untouched blocks and select roots per block by the verified frontier;
never erase another block's only replay root as a side effect of a partial
replacement. The catalog rejects inconsistent mixed checkpoints for a
multi-block operation. Full closure expansion includes all blocks of any prior
root operation it replaces, so atomically selecting the new component frontier
remains possible.

The `superseded_by` link is content-free; it cannot justify serving or replaying
old snapshot payload. Component attestations use retained operation support
IDs, so a later forgotten required input is discoverable even after the
original before/after receipt has been scrubbed. A later checkpoint may carry
forward an exact component attestation from an already verified clean root
when its retained support remains complete; it does not need the original
source-bearing before/after fields to be reconstructed. Preserve the original
independent support operation ID through this carry-forward, rather than
combining all root components into one new semantic proof that would make an
independent cap depend on an unrelated erased start. Never decide independence by
looking only at whatever claim links happen to remain after an earlier purge.
Persisted expected membership counts/fingerprints and erased/unproven states
prevent that mistake.

Version-1 portable manifests have no checkpoint IDs. Restore still derives the
closure from their persisted document/claim IDs before any scrub, or resumes
the deterministically named checkpoint already present for that forget ID.
Accepted v1 bytes/hashes are untouched. If restored data contains partial roots
or a preparing checkpoint, readiness continues the same forget worker under
the fence. It does not start ordinary replay through erased operations first.
Restoring an older database without temporal operations must run its supported
schema/conversion and forget reconciliation before temporal serving; no
unsupported format is silently accepted.

Crashes before a batch commit leave no batch effect; after commit, stable root
IDs and recorded preparation outcome make retries reuse the clean result. A
crash after destructive scrub must not require old source data to finish:
therefore sanitized root outcomes, complete support classifications and closure
inventory must be durable before scrub begins. A crash after checkpoint
verification but before P3/K purge leaves the ordinary forget barrier closed.
The existing processing ledger handles attempts, backoff and dead letters;
checkpoint `state` records semantic verification only. There is no new worker
lease, scheduler, timer or portable erase log.

### 7.7 Acceptance cases and limits of this proposal

- Seed-only start disappears; independently supported later cap and existing
  belief invalidation remain byte-for-byte equal before/after replay.
- A cap that depended on the forgotten seed loses its endpoint; an independent
  cap whose only causal predecessor was the seed survives. The distinction
  comes from recorded required support, not date coincidence.
- Clearing a bound causes overlap: fact IDs and proven state kinds survive,
  erased basis discloses unknown membership, exclusion permits uncertified intervals,
  profiles omit them as current and aggregates disclose uncertainty. No false
  contradiction group is created.
- A compensation's before snapshot contains the forbidden date even though its
  current endpoint does not: the receipt is covered/scrubbed and replay cannot
  resurrect it. New compensation cannot target checkpoint roots.
- Multi-block re-split, identity change, or neighbour-sensitive apply expands
  the closure. An unrecorded old read-footprint triggers conservative coverage,
  not a partial proof. Unaffected blocks keep their previous frontier.
- Delete an exclusive fact referenced by surviving historical receipts: logical
  tombstone targets remain non-readable; all current pointers target live roots.
- Forget a second source supporting a prior checkpoint, including one that
  affects only an old snapshot. The new checkpoint erases that date everywhere,
  retains independently supported other components, and preserves untouched
  blocks' valid roots.
- Crash/retry after every classification/root/scrub/verification step and
  restore from pre-forget PostgreSQL plus old manifest, queued correction,
  old root and P3/K copies. Reopened authority is exactly the cleaned root plus
  valid later operations, with all forbidden source-bearing payload absent.

The principal cost is checkpointing a dependency-connected block component,
which can reach the deployment on heavily connected history. That is an honest
rare-forget cost, comparable to the existing conservative K-history purge.
Exact per-fact surgery becomes attractive only if complete historical read sets
and component-scoped proofs demonstrate smaller closure without weaker replay.
This proposal avoids a second semantic log or proof language, while accepting
that conservative whole-operation support can turn more erased endpoints into
explicit unknowns. Neither proposed DDL nor cross-row verifier was executed as
part of this analysis-only task.
