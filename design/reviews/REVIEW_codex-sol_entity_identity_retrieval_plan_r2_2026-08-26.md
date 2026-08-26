# Round-2 implementation-plan review — entity identity and retrieval (D95–D97)

**Reviewer:** Codex (gpt-5.6-sol, reasoning effort xhigh)
**Date:** 2026-08-26
**Branch:** feat/d95-entity-identity-retrieval
**PR:** <https://github.com/writeitai/remember-stack/pull/304>
**Primary target:** plan/plans/entity_identity_and_retrieval.md at 58a79422
**Verdict:** **Request changes**

## Verdict

The revision fixes the two most important sequencing errors from round 1:
schema and name-only mint are now one migration-first hard-cut package, and
both eval reform and profile/T3 safety precede D95 T0 activation. I do not
request expand/contract, mixed-version drain, a compatibility resolve(type?),
or migration of old type values.

The plan is nevertheless not safe to execute yet. WP-I.2 says “rewrite type
consumers” but its named deliverables omit live consumers that will fail after
entities.type disappears, especially P1 entity search, assured-operation
descriptors, the query-space generated artifacts, and the P3 builder whose
binding Tier-1 path is entities/&lt;type&gt;/&lt;entity_id&gt;. Separately, WP-I.4's D74
wording does not cover a shared entity that survives a forgotten document; the
current scrub clears profile state only for exclusively retired entity IDs.
Those are P0 gaps in a hard cut, not backward-compatibility requests.

## Direct answers to the six round-2 questions

### 1. Did merging old I.2 + I.6 remove the schema deadlock?

**Yes, the circular WP dependency is removed.**

**Observed:** entities.type is text NOT NULL with a composite FK in
src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py:18-35,
and current _mint still validates and inserts it in
src/rememberstack/spine/resolver.py:431-496. Revised WP-I.2 now owns both the
schema change and name-only writer and states twice that the Alembic upgrade
runs first (plan/plans/entity_identity_and_retrieval.md:31-34,77-78).

**Inference:** A stopped hard-cut release can therefore avoid the old
NOT NULL mint outage without a dual writer or nullable compatibility phase.

**Gap:** “drop entities.type NOT NULL/FK/column (or stop using it)” is still
ambiguous. Merely stopping the writer is impossible while NOT NULL remains.
If the column is retained as dead/unused, the migration must still remove its
NOT NULL and FK and every reader must stop consuming it; that is not a
compatibility expand. More importantly, the package does not yet enumerate all
dependent views and readers, so the old deadlock is gone but the atomic cut is
still incomplete.

### 2. Is the I.1 common-name list plus I.5 T0 activation enough for John?

**Only together with I.4, and only after two contract clarifications.**

**Observed:** The current resolver returns the first exact alias at confidence
1.0 (resolver.py:107-130,688-698). Mint immediately stores a name-only vector
(resolver.py:483-495). A missing candidate vector escalates to T4, whose
current prompt has only spelling, type, and claim context; the existing test
even proves that a profile-less candidate may be matched
(src/tests/spine/test_resolver.py:337-363).

**Inference:** The static list closes the dynamic-guard chicken-and-egg problem,
and the revised I.4-before-I.5 dependency closes the name-only-T3 bypass. That
is the correct structure. The list alone does not make empty-profile T4 safe:
absence of contradictory evidence is not positive evidence that two Johns are
one person.

**Gap:**

- I.5 must assert that the second empty-profile John returns a **different
  entity_id regardless of deciding tier**, not merely that T0 did not
  “auto-merge” it. T3 and T4 must not gain certainty from spelling plus missing
  evidence.
- The production default list/min-length policy must be non-empty, include the
  tested john case, be version-stamped in resolver configuration, and be
  wired by src/rememberstack/profiles/selfhost.py. A test-only configured
  list does not protect cold-start production.
- I.1's acceptance currently says T0 must already reject common names even
  though I.5 owns activation. Either test the inert/configuration policy in
  I.1 and move runtime T0 acceptance to I.5, or make I.1 depend on the I.4
  safety gate. Do not partially divert current T0 into name-only T3 before I.4.

### 3. Is I.3 + I.4 before I.5 the right eval/profile gate?

**The order is right; the release gate is under-specified.**

**Observed:** Current judge_pair returns True, T0 solely on lemma equality
(resolver.py:200-203). run_resolution_suite groups by entity_type and
deletes the deciding tier (src/rememberstack/eval/resolution.py:137-177). It
is also registry-free, so it does not exercise alias multiplicity, the static
list, the guard, stored profile freshness, or the production T0 query.

**Inference:** Merging I.3 and I.4 before I.5 prevents both an unmeasured T0 cut
and the old “same spelling at T3” failure. But “I.3 merged” is not the same as
“the final post-I.4 resolver passed the gate.”

**Gap:** Make I.5 depend on a recorded pre-activation run after I.3 **and** I.4:
one global P/R curve with pinned floors, plus per-tier false-merge/false-split
diagnostics. At least the John, same-lemma two-ID, source+canonical alias, and
profile-present/profile-absent cases must run through production-equivalent
registry state. A rewritten pair helper may supplement that test; it cannot be
the only D95 activation gate.

### 4. Any remaining P0? Do aliases forbid two IDs with one lemma?

**Alias uniqueness does not forbid it. Two other P0s remain.**

**Observed:** aliases is unique on
(deployment_id, entity_id, normalized_lemma, provenance)
(p0_02_0003_entities_evaluation_e0_e1.py:51-63). Because entity_id
participates, the same lemma may legally belong to two entities. Source and
canonical provenances may also legally produce two rows for one entity.

**Inference:** No alias uniqueness migration is needed for D95. The lemma lock
can serialize two resolution attempts without imposing a uniqueness constraint
on the lemma.

**P0 gaps:**

1. WP-I.2's incomplete hard-cut inventory can leave the migration unable to
   drop the column or leave current P1/P3/public readers crashing afterward.
2. WP-I.4 does not require shared-survivor D74 invalidation. A surviving entity
   can retain profile prose derived from the forgotten document.

The exact-hit query still needs a P1 correction: count DISTINCT entity_id, not
alias rows. Retaining LIMIT 1 hides two entities; naively removing it makes a
single entity with source and llm_canonical aliases look like two hits.

### 5. Is WP-I.2's “rewrite type consumers” specific enough?

**No.** This repository's WP format says an agent should execute from the named
reads and deliverables. A generic search instruction is not a hard-cut
acceptance checklist, and the current parenthetical misses load-bearing code.

At minimum WP-I.2 must name these artifact groups:

- **Schema and generated query space:** a new head migration; dependent
  v_graph_entities in p0_02_0006_partitions_views.py; the current
  memory_v1.entities_current definition represented by
  p9_04_0025_coordinate_binding.py; spine/query_space/catalog.py,
  memory_v1_manifest.json, and ast_golden_vectors.json. Views must be
  dropped/recreated in dependency order before dropping the base column.
- **Resolver/E3 models and composition:** model/relations.py,
  model/resolution.py, spine/resolver.py, the still-composed typeful
  spine/entity_registry.py, workers/e3.py, and profiles/selfhost.py.
- **P1 and query nomination:** ports/p1_index.py,
  adapters/postgres_p1.py, and the entity filters in
  surfaces/query_sandbox/nomination.py and bridge.py.
- **Public and assured surfaces:** model/envelope.py (EntityCandidate.type,
  GraphNode.type), surfaces/query_engine.py, graph_queries.py,
  http_api.py, sdk.py, operation_executor.py, and
  spine/assured_operations.py. With no BC, replace the active descriptor and
  surface atomically; do not keep entity_type optional.
- **P2:** spine/projection.py plus workers/p2.py schema, hash, DDL,
  positional Parquet contract, rebuild version, and publication validation.
- **P3:** spine/projection.py's corpus entity export and workers/p3.py, which
  reads entity["type"] for paths, frontmatter, and indexes. Amend the older
  binding path in plan/designs/e0_files_design.md:605-611 to an untyped path,
  bump/rebuild P3, and wipe old snapshots if desired. No redirects are required
  under the frozen no-BC posture.
- **Registry/bootstrap/dead type vocabulary:** core/core_manifest.py,
  core/extension_packs.py, spine/extension_packs.py,
  spine/deployment_bootstrap.py, model/deployment.py,
  spine/fact_catalog.py, spine/knowledge.py (entity_type interests), and
  spine/catalog_contract.py. Predicate vocabulary remains.

The package's acceptance must include P1 search, assured-surface parity, query
manifest drift, and P3 rebuild—not only mint, P2, and /resolve.

### 6. Which round-1 items drop under no BC, and which remain?

**Correctly dropped:**

- expand/contract and a nullable compatibility phase;
- dual writers/readers and rolling mixed-binary support;
- a first-class drain/re-enqueue package or mixed-generation E3 test matrix;
- compatibility resolve(type?), API deprecation shims, and old DTO support;
- migrating old type values into hats;
- redirects/compatibility for old P3 paths and serving old P2 snapshots beside
  the new contract; and
- a reversible downgrade that reconstructs discarded types.

The normalizer and resolver version strings still must change; abandoning old
work does not permit new semantics to reuse the old generation identity.

**Still applicable:**

- migration-before-name-only-mint inside the atomic hard cut (now correctly
  addressed structurally);
- every current type consumer, including P1, P2, P3, query space, bootstrap,
  public DTOs, and assured operations;
- common-name/min-length policy before D95 activation;
- profile/T3 safety and a real eval gate before D95 activation;
- the missing source-surface contract for source aliases;
- distinct-entity exact-hit counting and canonical exclusion pairs;
- creation/composition of the profile worker, its debounce/stale-write rules,
  and shared-survivor D74 behavior;
- D97's concrete operation composition and failure/cap behavior; and
- resolver transaction/concurrency tests, including removal of process-shared
  per-call rejection state.

## Findings by severity

### P0.1 — WP-I.2 can still leave the hard cut physically or logically broken

**Observed:** In addition to entities.type, live SQL and code read the field
through v_graph_entities, memory_v1.entities_current, P1 entity search, P2
export/DDL, graph/envelope models, and P3 corpus paths. P3 is composed in the
self-host profile and rebuilds from spine/projection.py; it is not dead code.

**Inference:** Dropping the base column before replacing dependent views can
make the migration fail. Retaining the column but cutting only the named
consumers can make P3 rebuild raise on entity["type"], make P1 SQL reference
published.entity_type, or publish stale typeful assured schemas. Store wipes
do not fix code that still requires the field.

**Gap:** Replace “rewrite type consumers” with the named checklist above and
add acceptance for every publication/search surface. This remains one hard-cut
WP and one release; no compatibility phase is requested.

### P0.2 — D74 remains unsafe for a shared surviving entity

**Observed:** ForgetManifest distinguishes all resolved_entity_ids from
exclusive entity_ids (spine/forget.py:305-322). The entity scrub clears
profile_summary and embedding columns only where entity_id = ANY(:entity_ids)
(spine/forget.py:1331-1348), and verification checks the same exclusive set
(spine/forget.py:1631-1637).

**Inference:** After I.4, a profile synthesized from documents A and B can
survive forgetting A unchanged because the entity is still evidenced by B.
An in-flight refresher can also republish an A-derived summary after the scrub
unless its input generation is rejected.

**Gap:** I.4 must require invalidation and recomputation (or clear-until-
recomputed) for every affected resolved_entity_id, plus stale-result
rejection/cancellation for queued profile work. Acceptance needs a shared
survivor whose forgotten distinctive phrase is absent from summary, salient
inputs, vector attestation, and post-forget search.

### P1.1 — Source aliases still have no source surface

**Observed:** EntityRef currently carries only canonical name and type
(model/relations.py:13-20); E3 instructs canonical nominative names; and
CascadeResolver._record writes reference.name as both surface_form and
canonical_name_form (resolver.py:516-545).

**Inference:** I.1 cannot prove that source App and canonical Application
were both recorded because the resolver never receives App separately.

**Gap:** Close this input contract before I.1: carry exact source surface (and
offsets when available) beside the canonical name, or pass a separate mention
payload. “Name-only” must mean no entity type; it cannot erase the source form
while promising a source alias.

### P1.2 — Exact multiplicity and exclusions need an executable contract

**Observed:** _T0_EXACT uses LIMIT 1; alias provenance can duplicate a lemma
for one entity; resolution_exclusions requires low/high UUID ordering; and
current _mint retains only a resolver-instance _last_rejection, not the set
of T4-rejected candidate IDs.

**Inference:** A naive I.5 implementation can count aliases instead of
entities, conceal multiplicity, or write exclusions for the wrong candidates.

**Gap:** I.5 must fetch deterministic distinct active entity IDs, test one
entity/two provenances and two entities/one lemma, retain per-call T4 non-match
candidates, insert canonical low/high pairs idempotently, and define exclusions
as post-mint merge protection. An entity-pair exclusion cannot by itself decide
which existing John a future unbound mention denotes.

### P1.3 — The profile worker is acknowledged as new but not yet operationally bounded

**Observed:** PipelineStage.REFRESH_PROFILE and
PipelineComponent.PROFILE_SUMMARIZER exist, but no handler, route, enqueue
trigger, or profiles/selfhost.py composition exists. The revised plan now
correctly calls I.4 new work.

**Gap:** Name the handler/composition and pin: evidence selection and stable
tie-breaks; triggers for observation/relation changes, supersession,
merge/unmerge, and forget; coalescing key; input fingerprint/version; atomic
summary+embedding publish; stale-result rejection; retries; and model/cost
identity. These are required to make “debounce on evidence change” and
“missing profile is fail-safe” testable.

### P1.4 — The eval package is preparation, not yet a ship gate

**Observed:** I.3 acceptance requires visibility and no crash, while the plan's
passing curve appears only in the final exit. I.5 has no explicit dependency on
a recorded passing run of the post-I.4 resolver.

**Gap:** Add the pre-activation eval result to I.5 acceptance. Preserve one
global threshold curve while retaining per-tier diagnostics required by design
§8; “one curve” does not mean deleting the deciding tier.

### P2.1 — _last_rejection is process-shared per-call state

**Observed:** CascadeResolver stores _last_rejection on the instance
(resolver.py:85-86), mutates it during _decide, and consumes it in _mint.
The self-host profile reuses one resolver instance in a handler.

**Inference:** Concurrent resolves or an exception between decision and mint
can stamp another call's rejection metadata on a mint. I.5 will make this worse
if it also derives exclusions from that state.

**Gap:** Carry rejection/exclusion state in the local resolve return path and
add concurrent and exception-retry tests. This is not a product decision.

### P2.2 — D97 is ordered correctly but its deliverable is too anonymous

**Observed:** I.6 has the right dependencies and correctly treats empty
predicates as existing graph behavior. Its deliverable is only “recipes / D87
defaults.”

**Gap:** Name spine/assured_operations.py, surfaces/operation_executor.py,
and the query-engine composition. Test ambiguity, bounded IDs/hops/results,
missing/stale P2, partial lookup/search failure, and that ID-constrained fact
search cannot leak unrelated results. No new LLM or compatibility surface is
needed.

## WP-by-WP disposition

| WP | Disposition | Required change before execution |
|---|---|---|
| I.1 | **Amend** | Close the source-surface input; pin and wire the non-empty versioned common-name/min-length defaults; keep runtime T0 activation behind I.4/I.5. |
| I.2 | **Request changes (P0)** | Keep the one-PR migration-first hard cut, but replace the generic consumer phrase with the named P1/P2/P3/query/public/bootstrap checklist and acceptance. Remove the ambiguity around what schema change makes the retained column unused. |
| I.3 | **Amend** | Global untyped curve plus per-tier diagnostics and production-equivalent state; record a passing post-I.4 run as an I.5 prerequisite. |
| I.4 | **Request changes (P0/P1)** | Create/compose the worker with trigger, debounce, input-version, atomic-publish, and stale-result contracts; cover shared-survivor forget and empty-profile fail-safe. |
| I.5 | **Keep after the above gates** | Count distinct entities, not aliases; require second-John different-ID behavior across all tiers; record only canonical T4 exclusions; remove shared per-call state. |
| I.6 | **Keep, amend deliverable** | Name and test the D87/query composition, caps, ambiguity, and partial failures. Its ship dependency on I.2 and I.5 is correct. |
| I.7 | **Keep** | The same-PR documentation rule is correctly cross-cutting; assign the exact affected pages to their owning WP. |

## Minimum safe plan amendment

No new compatibility WPs are needed. The smallest safe revision is:

1. Expand I.2's deliverable/acceptance into the enumerated hard-cut checklist,
   including P1, assured operations, query artifacts, P3, and migration view
   ordering.
2. Close I.1's source-surface and production common-list contracts.
3. Make I.4 explicitly safe for empty-profile common names and shared-survivor
   forget, with stale-result rejection.
4. Add a recorded post-I.3+I.4 eval pass to I.5's activation gate and specify
   distinct-entity T0/exclusion behavior.
5. Name the I.6 D87/query composition and failure/cap tests.

## Verification status

**Observed directly:** the revised plan and design §§3.1, 3.3, and 9; both r1
reviews; current resolver T0/judge_pair/_mint; E3 type/signature path;
entities.type NOT NULL; alias uniqueness; eval runner; absence of a profile
handler; P1/P2/P3/query/public/bootstrap consumers; and current hard-forget
scrub/verification SQL.

**Inference:** Deployment behavior is inferred from the checked-in composition
and migration dependencies. No live database, queue, or snapshot registry was
inspected.

**Gap:** Exact common-name contents, profile salience/debounce values, model
pins, and eval floors remain implementation inputs to version and measure; this
review does not invent them.

No implementation code was changed and no backward-compatibility requirement
was added. The only repository change from this review is this document.
