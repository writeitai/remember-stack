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

### 4.1 Entity provenance: follow the candidate's recorded coordinates

Preserve the public columns and membership of `memory_v1.entities_current`.
For one terminal entity, enumerate its absorbed members using the existing
reverse merge index. An explicit live document-entity bridge qualifies as it
does now. Otherwise begin from those members' unsuperseded resolution
decisions, select the same latest decision for each mention, and follow that
mention's recorded chunk ID to the chunk, version, representation and lineage.

The chunk lookup must stay correlated to the mention's deployment, chunk ID
and document ID before the larger version/reading joins. The planner must not
turn one recorded mention into a broad join of unrelated chunks or versions.
A correlated lookup boundary is acceptable where required; materializing an
unrestricted current-content view is not. Exact requested counts still read
current content; an identity-only lookup must not execute those count aggregates.

The contract is bounded work over the candidate's reverse members and their
actual provenance, with primary-key/source-coordinate probes. It is not a
promise of constant time for an entity with arbitrarily large provenance.
Unrelated entities, chunks and versions must not multiply that work. Missing,
retired, merged-away or provenance-free entities remain absent. Merge cycles
retain the existing termination/absence semantics, with no invented depth cap.

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

### 4.3 Facts: factor source visibility from endpoint eligibility

The visible-fact authority currently combines survivor endpoint resolution,
endpoint visibility and visible historical claim evidence. Its endpoint checks
can repeat the same entity work for many facts. Factor it into two private
relations, without exposing a new public query surface:

| Relation | Contract |
| --- | --- |
| Source-qualified fact projection | One row per deployment/kind/fact ID, same survivor endpoints and fact fields as today; requires the existing exact historical claim-coordinate evidence predicate, but does not yet assert endpoint visibility |
| Fully visible fact authority | Source-qualified facts joined to authoritative visible endpoints: subject always, object for relations; this remains the single complete fact membership contract |

The source-qualified relation can carry the existing fact presentation
columns, precision, support state and demand-prunable lineage counts.
Presentation/count expressions are defined once and consumed by the public
view and bounded native reads. ID-only nomination must not force unrequested
evidence-count aggregates. Public `facts_visible_history` and evidence bridges
continue to consume fully qualified membership and retain their output shape.
Nomination materializes only required IDs, endpoints, ranking/time fields;
hydration requests the presentation/count fields. A `SELECT *` that forces
unrequested counts into an intermediate materialization is not acceptable.

For bounded native fact reads, materialize the candidate IDs first, obtain
their source-qualified rows through per-ID constrained lookups, collect the
distinct subject/object survivor IDs, and evaluate those endpoints once.
Join the endpoint result to all candidate facts. An objectless observation
needs no object check; a relation with equal subject/object needs one distinct
entity lookup but retains the existing coverage rule. Missing visibility for
either required endpoint excludes the relation. No candidate is accepted
merely because the caller mentioned its entity ID.

Use one maintained SQL assembly contract for the complete endpoint predicate
and presentation fields, shared by the public wrapper and native bounded
reads. Tests compare their complete row values. Do not hand-maintain a second
fact-truth query or implement runtime string replacement of public view DDL.

The D140 supporting-evidence/time gate is still applied in addition to this
historical source-membership gate, before nomination limits. Current support
counts still count distinct current-testimony lineages; they are not frozen
historical counts. Reconstructed belief/identity queries keep their existing
historical authority and may share only proofs from that same regime.

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
  Predicates have one authored source; variable table/column fragments are
  internal enums/literals. Every SQL path binds deployment and IDs normally.
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

1. **Complete semantic equality:** compare original public/native references
   and new rows, all columns and counts, on arbitrary synthetic fixtures and
   the complete retained-store candidate sets. A previously timed-out full
   reference may run under a separately recorded longer diagnostic bound or
   in stable small batches; never claim full equality from a truncated oracle.
2. **Visibility/time matrix:** current and superseded testimony, selected dated
   occurrences, changed readings, deleted lineages, forgotten/merged identities,
   older-version-only provenance, document-entity bridges, duplicate mentions,
   missing entity endpoints, self-relations, unknown/open windows, support
   withdrawal and contradiction-only facts. Test existing reconstructed-time
   paths and unsupported-combination errors as well.
3. **Actual-plan work:** coverage and endpoint visibility do not run once per
   candidate sharing an entity; coordinate lookups stay tied to their input
   IDs. Add large unrelated entity/chunk/version sets and show work does not
   multiply with them. Verify unrequested counts are absent from identity/ID
   plans. Assert work/cardinality bounds, not one exact planner node spelling.
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
   Publish measured before/after limits and unresolved risks accurately.

A full LoCoMo answer/judge replay can follow the merged, qualified fix using
the retained stores and original model/prompt policy, with separately saved
answers and verified GCS backups. It cannot be used in place of eligibility
or concurrency qualification.
