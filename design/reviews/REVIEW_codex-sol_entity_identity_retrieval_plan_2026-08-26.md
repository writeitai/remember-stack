# Independent implementation-plan review — entity identity and retrieval (D95–D97)

**Reviewer:** Codex (`gpt-5.6-sol`, reasoning effort xhigh)  
**Date:** 2026-08-26  
**Branch reviewed:** `feat/d95-entity-identity-retrieval`  
**PR:** <https://github.com/writeitai/remember-stack/pull/304>  
**Review target:** `plan/plans/entity_identity_and_retrieval.md`  
**Verdict:** **Request changes**

## Verdict

**Observed:** The plan correctly points toward the accepted D95–D97 product behavior, but its dependency graph is not safe to execute. In particular, WP-I.2 removes `type` from the mint writer while the current database still requires `entities.type`; WP-I.3 changes T0 before WP-I.4 removes the name-only T3 shortcut; and WP-I.7 is allowed to ship after WP-I.1 even though its public contract and graph inputs remain typeful until a much larger projection/API cutover.

**Inference:** Following the table literally can cause, in order, a hard insert outage, silent same-name false merges, and a retrieval release whose stored operation descriptor, HTTP/SDK contract, P2 snapshot, and P3 paths disagree. These are implementation-plan defects, not objections to D95, D96, or D97.

**Gap:** The plan needs an explicit expand/write-cutover/contract sequence, a profile-and-eval safety gate before enabling the new T0 behavior, a generation-drain or re-enqueue procedure for old E3 work, a D74 integration package, and an enumerated type-removal blast radius. The suggested table below supplies that structure.

## Files and code paths reviewed

### Primary and binding corpus

- **Observed:** `plan/plans/entity_identity_and_retrieval.md` was read in full; its WP table is the primary review target.
- **Observed:** `plan/designs/entity_identity_and_retrieval_design.md` was read in full, with particular attention to §§3, 4, 5, 7, 9, and 11.
- **Observed:** `plan/analysis/entity_identity_and_retrieval_analysis.md` was read through §3, treating it as non-binding analysis and checking its as-built claims against code.
- **Observed:** `decisions.md` D17, D18, D21, D86, D87, and D95–D97 were read. D95–D97 are treated as frozen.
- **Observed:** The related contracts inspected were `plan/designs/retrieval_design.md` §3, `plan/designs/registries_design.md` (profile maintenance), `plan/designs/e3_unknown_entity_type_gate_design.md` (version/replay), `plan/designs/orchestration_design.md` (work identity/replay), `plan/designs/hard_forget_design.md`, and the P3 path contract in `plan/designs/e0_files_design.md`.

### As-built implementation and representative tests

- **Observed:** Resolver/extract/model: `src/rememberstack/spine/resolver.py`, `src/rememberstack/spine/entity_registry.py`, `src/rememberstack/workers/e3.py`, `src/rememberstack/model/relations.py`, and `src/rememberstack/model/resolution.py`.
- **Observed:** Registry/bootstrap/governance: `src/rememberstack/core/core_manifest.py`, `src/rememberstack/core/extension_packs.py`, `src/rememberstack/spine/deployment_bootstrap.py`, `src/rememberstack/spine/extension_packs.py`, `src/rememberstack/spine/fact_catalog.py`, `src/rememberstack/spine/knowledge.py`, and `src/rememberstack/spine/catalog_contract.py`.
- **Observed:** Retrieval and public contracts: `src/rememberstack/surfaces/query_engine.py`, `graph_queries.py`, `http_api.py`, `sdk.py`, `operation_executor.py`, query-sandbox code, `src/rememberstack/spine/assured_operations.py`, `src/rememberstack/model/envelope.py`, `src/rememberstack/ports/p1_index.py`, and `src/rememberstack/adapters/postgres_p1.py`.
- **Observed:** Projections: `src/rememberstack/spine/projection.py`, `src/rememberstack/workers/p2.py`, and `src/rememberstack/workers/p3.py`.
- **Observed:** Schema: migrations `p0_02_0001`, `p0_02_0002`, `p0_02_0003`, `p0_02_0004`, `p0_02_0006`, `p9_01_0022`, and `p9_04_0025`; current Alembic head and migration tests were also inspected.
- **Observed:** Forget/eval/tests: `src/rememberstack/spine/forget.py`, `src/rememberstack/eval/resolution.py`, resolver, E3/D86/signature, migration, bootstrap/governance, retrieval/operation, P2/P3, and hard-forget test modules under `src/tests/**`.
- **Observed:** User-visible references were sampled under `website/src/app/docs/**`, especially ingestion, retrieval, primitives, API, concepts, and getting-started pages.

## P0 findings — release blockers in the plan

### P0.1 — WP-I.2 before the schema compatibility migration makes mint fail

**Observed:** WP-I.2 promises a name-only `EntityRef` and an `_INSERT_ENTITY` with no type, but depends only on WP-I.1. WP-I.6, which owns the Alembic change, comes later and itself depends on WP-I.2. In `p0_02_0003_entities_evaluation_e0_e1.py`, `entities.type` is `text NOT NULL` and has a composite foreign key to `entity_types`. The current `_mint` in `spine/resolver.py` explicitly validates the type and includes it in `_INSERT_ENTITY`.

**Inference:** If WP-I.2 lands or deploys before a compatibility migration, the first novel name reaches an insert that either omits `type` or supplies `NULL`; PostgreSQL rejects it. Existing exact hits may appear healthy, so the outage can be partial and corpus-dependent.

**Gap:** Add a migration/cutover WP before the name-only writer. There are two safe choices:

1. **Atomic stop-and-cutover:** drain and stop every E3 worker, migrate, deploy all readers and writers together, then resume. The plan must say that a rolling mixed-version window is prohibited.
2. **Expand/contract:** first ship readers that tolerate an absent type; then add a new head migration that drops only `entities.type`'s `NOT NULL` constraint while retaining the FK, column, and registry (a foreign key permits `NULL`, and old writers can still validate/write their registered value); then ship the name-only writer; only after old workers and old-generation work are drained, drop the dependent views, FK, index, legacy columns/tables, and rebuild derived projections.

**Gap:** Do not edit `p0_02_0003`. Add a migration after the observed head `p9_13_0034`, update the hard-coded linear revision tuple in `src/tests/spine/test_migrations.py`, and test an upgrade with populated entities. The plan must also state downgrade behavior. Once untyped rows exist, restoring `NOT NULL` and the FK requires inventing types, which D96 forbids; the contract migration is therefore either deliberately irreversible on populated data or its downgrade must fail with a clear data guard.

### P0.2 — WP-I.3 before profile/T3 safety recreates the John merge one tier later

**Observed:** The current mint path embeds `reference.name` immediately and stamps that vector with `ENTITY_INPUT_POLICY`. `_decide` accepts a T3 score at or above `0.88` by default. WP-I.3 changes common-name exact matches from T0 verdicts into cascade candidates, but WP-I.4—later—owns profile embeddings and their refresher.

**Inference:** After WP-I.3 but before WP-I.4, a second `John` correctly fails the common-name T0 auto-accept check, then compares the query embedding for `John` with the first entity's name-only `John` vector. That is effectively the same exact-name merge at T3. The tier label changes; the false merge does not.

**Gap:** Split profile work. A pre-I.3 safety slice must stop minting a name-only vector as if it were a ready profile, treat missing/stale profile vectors as ambiguity, and force a common or guarded exact name with no positive identity evidence through T4 or to a fail-safe non-match/mint outcome. The full debounced refresher can continue in parallel, but WP-I.3 must not be enabled until the safety slice and its golden gate pass.

**Observed:** The first `SAP` mention has no candidate and mints. A later unique, distinctive, unopposed `SAP` exact hit can auto-accept at T0 even before a profile exists; that is the accepted good case. A first `John` also mints, but the next `John` must not match merely because both candidate and query lack evidence beyond the spelling.

**Gap:** The design states that an empty-profile T4 sees claim text plus candidate name, but it does not define a fail-safe result when this input is insufficient. The plan must pin the acceptance rule: absence of positive evidence must not become a same-name match merely because the frontier call returned `match=true` at arbitrary confidence.

### P0.3 — “Drop types” is much larger than WP-I.6 and cannot be a leaf after WP-I.2

**Observed:** WP-I.6 names Alembic, `core_manifest.py`, a schema document, untyped graph nodes, and a P2 rebuild. Code inspection found type-bearing contracts in the writer, bootstrap, P1, query-space, API, assured operations, P2, and P3. Several of those deserialize `type` as a required string, so making the database column nullable is not enough.

**Inference:** A migration that drops `entities.type` while these consumers remain will fail views and SQL immediately, fail DTO validation on reads, fail P2/P3 rebuilds, or change public JSON without a versioned operation contract. An executing agent will discover this only after starting WP-I.6 unless the inventory below is part of the WP.

**Gap:** Replace WP-I.6 with an explicit compatibility and contract-cutover group. It must cover every consumer listed in “Type-removal blast radius” below, including P3. It also needs an as-built deployment sequence: reader compatibility, schema expand, writer generation cutover, old-work drain, public contract/operation version switch, projection rebuild and validation, then schema contract/drop.

**Observed:** PostgreSQL views including legacy `v_graph_entities` and `memory_v1.entities_current` depend on `entities.type`; later query-space views depend on those views. `predicate_signatures` also depends on `entity_types` through its foreign keys.

**Gap:** The contract migration must drop and recreate dependent views in dependency order—never use an unreviewed `DROP ... CASCADE`—then drop the entity type FK/index/column, drop `predicate_signatures` before `entity_types`, and update `catalog_contract.py` plus the checked-in query-space manifest. `mentions.emitted_type` is already nullable, so writer cutover can stop populating it before a later explicit column/view cleanup.

### P0.4 — WP-I.4 can retain forgotten source text on a surviving shared entity

**Observed:** `spine/forget.py` distinguishes `resolved_entity_ids` (every entity touched by the forgotten document) from `entity_ids` (only entities with no surviving mentions). Its entity scrub clears `profile_summary` and embedding columns only for `entity_ids`. The verification query makes the same restriction. Existing `test_forget_catalog.py` exercises exclusive and shared entities but does not assert that a shared survivor's profile is rebuilt without the forgotten document.

**Inference:** Once WP-I.4 summarizes observations and relations into profile prose, a shared entity may survive a forget while its cached profile continues to quote or paraphrase the forgotten source. That violates D74 even if the underlying observation/evidence rows were correctly removed.

**Gap:** Add a D74 integration WP as a ship blocker for WP-I.4. Under the forget barrier it must invalidate and synchronously recompute, or clear until recomputed, the profile summary and embedding for every affected `resolved_entity_id`; cancel or obsolete queued profile work that captured the old input; scrub any new profile input hashes/version rows; rebuild the guard cache if it is deleted deployment-wide; and extend forget verification plus S55 with a shared-survivor test. P2/P3 publication must remain blocked until the scrubbed profile is the only one reachable.

## P1 findings — the executing contract is incomplete

### P1.1 — WP-I.1 cannot write the actual source alias with the current DTO

**Observed:** `EntityRef` contains only `name` and `type`; the E3 prompt requires that name to be a canonical nominative form. `CascadeResolver._record` writes that same value into both `mentions.surface_form` and `canonical_name_form`, and mint writes only an `llm_canonical` alias. There is no raw endpoint surface or span in `NormalizationResponse`.

**Inference:** The resolver cannot satisfy WP-I.1's “surface form actually seen in the claim” contract. Searching the claim for the canonical name is not reliable for inflection, aliases, repeated names, or model-normalized spelling.

**Gap:** Before WP-I.1 implementation, clarify the input contract and schema ownership. Pass an exact source surface separately from the canonical name (and offsets when available), or change E3 structured output to carry both. This does not reintroduce types. Add tests where source `App` canonicalizes to `Application`, an inflected surface canonicalizes to a nominative form, and two endpoints occur in one claim. Alias insertion on mint and match must be an idempotent upsert that updates `last_seen`; current alias inserts have no `ON CONFLICT` clause.

### P1.2 — exact multiplicity is schema-safe, but query and exclusion semantics are missing

**Observed:** `aliases` has `UNIQUE (deployment_id, entity_id, normalized_lemma, provenance)`. The same normalized lemma on two different entity IDs is already legal; no uniqueness migration is required for D95's second mint. The current `_T0_EXACT` nevertheless orders aliases, uses `LIMIT 1`, and the caller uses `.one_or_none()`.

**Inference:** Removing `LIMIT 1` without deduplicating by entity can count one entity twice when it has both `source` and `llm_canonical` rows for the same lemma. Conversely, leaving the limit makes “exactly one active exact hit” untestable.

**Gap:** WP-I.3 must specify `SELECT DISTINCT` entity IDs (or group by entity), return all active survivors deterministically, and count distinct survivors rather than alias rows. It must test one entity with two provenances and two entities with the same lemma. After a T4 non-match and mint, insert the exclusion with UUIDs in low/high order and an idempotent conflict policy.

**Observed:** `resolution_exclusions` is an entity-to-entity pair. Before a fresh mention is resolved, it has no provisional entity ID.

**Gap:** The design sentence that exclusions prevent T4 from being “re-asked forever” is not executable as written for a new mention. An A≠B exclusion protects clustering and can prune comparisons only after the mention is associated with A or B; it cannot by itself determine which identity a new `John` belongs to. Clarify in the design whether WP-I.3 promises only merge/cluster protection or define the additional state/algorithm that makes exclusions applicable during mention resolution.

### P1.3 — WP-I.4 is a new worker, not an extension, and lacks its operating contract

**Observed:** `pipeline_stage.refresh_profile` and `pipeline_component.profile_summarizer` exist in enums/models, and `registries_design.md` describes a dedicated worker. No handler, composition wiring, queue trigger, profile selection query, or writer implementation exists. The only as-built profile write found outside migrations/projection is hard-forget clearing it.

**Gap:** WP-I.4 must say “create and compose” rather than imply an existing refresher. It needs all of the following:

- **Gap — selection:** Define eligible facts, temporal state, and a deterministic cap. At minimum choose whether current observations only or current observations plus incident relations are included, how `evidence_count`, contradiction state, confidence, and recency rank, the observation/relation split, the exact count, and a stable tie-breaker.
- **Gap — provenance:** Store a profile component/version, input hash or ordered source-ID fingerprint, refreshed timestamp, summary model/prompt pin, embedding model/dimension/input-policy pin, and enough state to prove that summary and embedding refer to the same selected facts.
- **Gap — debounce:** Name every material trigger: fact create/upsert, evidence-count change, supersession/invalidation, validity-window change, merge/unmerge, and forget. Define the delay/coalescing key, single-flight behavior, and what happens when changes arrive during a build.
- **Gap — failure/recovery:** Define idempotency, retries/DLQ, stale result rejection, and an atomic publish rule. A summary must not publish with an embedding from another input set.
- **Gap — cost:** Pin the small model, temperature, batch/input limits, ledger `call_key`s, and budget/lane behavior. T4 already meters small/frontier calls; the new profile generation and embedding calls need equally unique accounting.
- **Gap — consistency:** T4's blurb and salient facts and T3's profile embedding should derive from the same selection/version. State how the resolver behaves while a profile is absent, stale, failed, or awaiting rebuild.

**Inference:** `evidence_count` is a useful starting signal because it represents distinct current-testimony lineages, but it cannot be the only sort key: a highly repeated obsolete or contradicted fact must not outrank a current disambiguating fact. The exact recipe is a design choice still missing, not something the implementation agent should invent.

### P1.4 — the common-name list and `generic_identifier_guard` have no authority or starting values

**Observed:** The table exists and hard-forget deletes it; no production writer or resolver reader was found. `ResolverConfig` contains trigram, limits, T4 candidate count, default thresholds, and `thresholds_by_type`, but no common-name list, too-short policy, or guard threshold.

**Inference:** A derived guard cannot solve the first homonym. Until at least two IDs already own `john`, its distinct-entity count is one. A versioned static common-name/short-token policy is therefore required to protect the first split; the live guard becomes useful after multiplicity exists.

**Gap:** WP-I.1/WP-I.3 must assign authority and starting values, not say only “measured.” Put the static list/version, minimum token rule, distinct-entity threshold, and down-weight behavior in resolver version configuration; stamp them on decisions. Define the writer as a recomputation or upsert over distinct active survivor IDs after alias mint/match/merge/unmerge/forget. State exactly what “down-weighted” means at T1/T2. Test that hard-forget's deployment-wide delete is followed by deterministic rebuild before T0 can auto-accept again.

### P1.5 — D86/signature removal needs a generation cutover, not just deleted branches

**Observed:** `E3_NORMALIZER_VERSION` includes `unknown-type-gate-1`; claim normalization, staging, observation-flush membership/state, facts, and evidence carry the normalizer version. `e3.py` has both claim-fanout and legacy version-serial paths. The D86 design explicitly requires a version bump and distinguishes replaying an old row from re-enqueueing under a new version.

**Inference:** Replaying an old `normalize_relations` row after deploying name-only code under its old `component_version` produces new semantics with old provenance. Dropping type/signature tables while an old binary or queued old job can still run instead produces hard failures. Mixed normalizer generations can also leave observation-flush barriers looking for the wrong staging generation.

**Gap:** Add a cutover sub-WP to WP-I.2: bump `E3_NORMALIZER_VERSION` and `RESOLVER_VERSION`; inventory pending/claimed/failed/dead-lettered old normalize rows; choose drain-before-migration or transform/re-enqueue at the new version; handle `normalize_observation_staging`, `obs_flush_version_state`, and `obs_flush_entity_units`; prove that mixed generations neither deadlock a barrier nor duplicate facts; and only then remove D86/signature schema. Keep or deliberately replace coverage in `src/tests/workers/test_e3_unknown_entity_type_gate.py`, `test_e3_chain.py::test_signature_gate_binds_on_resolved_stored_types`, `src/tests/spine/test_deployment_bootstrap.py`, and `src/tests/spine/test_governance.py`—do not merely delete the failures they expose.

### P1.6 — the current golden harness cannot measure the production failure by tier

**Observed:** `eval/resolution.py` seeds six synthetic pairs. It has no same-spelling `no_match` row. `run_resolution_suite` discards the tier returned by `judge_pair` and records per-type aggregate precision/recall. `judge_pair` returns `(True, "T0")` immediately for equal lemmas, accepts an `entity_type`, and operates registry-free over surface/context pairs. `golden_pairs.entity_type` is `NOT NULL` and indexed. Existing resolver tests assert `Person` and `Organization` curves.

**Inference:** Merely deleting the exact-lemma return is insufficient. A registry-free pair call has no exact-hit multiplicity, common-name guard, stored profiles, salient facts, or exclusions, so it still does not exercise the production D95 cascade. The current aggregate also cannot report which tier caused a false merge.

**Gap:** Move the harness/schema preparation before the WP-I.3 activation gate, then run it after the new resolver/profile code. Replace the type stratum with a neutral case/category field or nullable legacy field; record TP/FP/FN/TN and precision/recall by deciding tier plus one global curve; keep undefined denominators failing rather than treating 0/0 as success. Pin global floors to replace today's `0.90` precision and `0.80` recall per-type floors, and add an explicit false-merge/T0 floor if that is the release invariant.

**Gap:** Extend fixtures with exact-spelling match and exact-spelling non-match cases, each carrying enough candidate facts/profile input to exercise the real decision: father/son, two employees at different sites, Java language/island, SAP shorthand/SAP SE, SAP SE/S/4HANA, one person moving city, bare nouns, dual role, and bank/Italy retrieval. Either seed a miniature deployment and call the production resolver, or extend the pair fixture schema so the same production decision function receives exact-hit count, guard/common status, profile, and salient facts. Preserve `is_synthetic` and require human-adjudicated non-synthetic data for any production threshold claim.

### P1.7 — D97's end-to-end recipe is not yet a callable operation contract

**Observed:** Current `fact_context` semantically nominates relations/observations and confirms them, optionally constrained to caller-supplied entity IDs. It does not call graph neighborhood. `multi_hop_context` separately calls P2. `answer_context` composes testimony and facts. D87 says assured operations do not silently resolve names; current operations accept IDs after an explicit `resolve_entity` call.

**Inference:** “Resolve → lookup → graph → text search” must therefore be a caller-visible two-step flow or a newly specified internal operation input; it cannot silently resolve arbitrary names inside `fact_context` without changing D87. WP-I.7's current one-line deliverable leaves this distinction to the implementer.

**Observed:** The as-built query-time `QueryEngine.resolve` is explicitly a “T0 skeleton”: it runs only `_RESOLVE_T0` over exact aliases and optionally ranks those exact candidates by context adjacency. The full `CascadeResolver` is not a read primitive—it writes mentions/decisions and may mint—so it cannot simply be called from a zero-side-effect retrieval path. `retrieval_design.md` promises a read-only T0–T3 primitive.

**Gap:** Add the missing read-only query-resolution work to WP-I.7 (or a prerequisite): type-free T1/T2 candidate generation and T3 profile-vector ranking, survivor following, ambiguity/cap behavior, and tests. Reuse the cascade's normalization/config where safe without allowing query reads to mint, append decisions, or call T4. Otherwise the first arrow in D97 remains exact-only and the plan's recipe is incomplete.

**Gap:** Specify which operation owns the D97 composition and its exact inputs/outputs. If `fact_context` is amended, state how direct observations and incident relations are unioned with one-hop graph IDs and text nominations; ranking/deduplication; caps per stage; temporal scope; evidence hydration; and truncation accounting. Pin ordinary hops to one even though the primitive currently permits/defaults other values. Keep `predicates=()` as “all `RELATES`” and permit any stored predicate, including `other:`.

**Gap:** Define failure behavior when resolution is ambiguous, P2 has no snapshot, the snapshot is stale, graph hydration drops an edge, or fact-text search is unavailable. Direct PostgreSQL facts should not silently disappear merely because the optional derived graph is unavailable. No query-path completion is added.

**Observed:** `retrieval_design.md` still documents `resolve` as `text, type?, context_entities?`; `/resolve`, the SDK, the assured `resolve_entity` descriptor, `OperationExecutor`, and the envelope candidate all expose type. `AssuredOperationRegistry.by_name` currently asks specifically for version 1.

**Gap:** WP-I.7/WP-I.6 must atomically revise the registry descriptor, executor, HTTP, SDK, CLI/MCP-derived schemas and docs, and their parity tests. If the operation schema changes, assign a new operation version and make registry lookup select the declared active version rather than hard-code version 1.

## P2 findings — important completeness and maintainability fixes

### P2.1 — transaction scope and resolver instance state need explicit failure tests

**Observed:** The lemma advisory lock is transaction-scoped, and the transaction currently spans embedding and T4 model calls. `CascadeResolver` also stores `_last_rejection` as mutable instance state, while the resolver is composed as a reusable service.

**Inference:** Slow model calls can hold the lemma lock and database transaction for seconds; timeouts or concurrent reuse can leak one resolution's rejection metadata into another mint. D95 increases how often exact names reach those slower tiers.

**Gap:** Add concurrency, timeout, rollback, and resolver-reuse tests to WP-I.3. Prefer per-call decision state over `_last_rejection` on the resolver object. If the lock intentionally covers T4 to serialize same-lemma mints, document the timeout/connection-pool cost and bound it; otherwise define a two-phase compare-and-recheck protocol before insert.

### P2.2 — API schema work must describe the repository's actual OpenAPI posture

**Observed:** The FastAPI app sets `openapi_url=None`; no committed OpenAPI artifact was found. The route schema can still change in FastAPI's in-memory model, while assured-operation JSON schemas and SDK models are separately maintained.

**Gap:** Do not add a vague “regenerate OpenAPI” task. Add an API-contract task that either (a) introduces and owns a checked-in/generated OpenAPI artifact deliberately, or (b) records that no OpenAPI document is shipped and adds schema/response snapshot tests over the in-memory app plus assured-operation descriptors. In both cases, verify that `entity_type` disappears from requests and entity/graph `type` disappears from response schemas.

### P2.3 — docs and observability need named checks, not a blanket WP-I.8 row

**Observed:** Website pages still show `entity_type` in resolve examples, describe typed resolution, and document D86/signature soft drops. WP-I.8 says only “same-PR website pages.”

**Gap:** Give each shipping WP a concrete docs checklist: ingestion eligibility and removed D86 behavior; identity ambiguity/profile orientation; resolve request/response changes; untyped graph/query-space/P3 paths; and the D97 two-step recipe/failure modes. Add structured counters for T0 auto-accept/escalate reason, exact multiplicity, guard hits, T3 missing/stale profile, T4 outcomes, second mints, profile lag/failures, and D97 stage/truncation outcomes. These labels must be bounded and version-stamped.

## Type-removal blast radius: consumers that fail or lie when types disappear

The following inventory should be copied into the replacement for WP-I.6 and checked off. “Fails” includes a compile/test failure, SQL failure, Pydantic validation failure, or a public contract that continues to claim a type exists.

| Consumer group | As-built evidence | Required plan action |
|---|---|---|
| Canonical tables | **Observed:** `entities.type` is required/FK/indexed; `mentions.emitted_type` exists; `resolver_versions.thresholds_by_type` and `golden_pairs.entity_type` are required; `entity_types`, `predicate_signatures`, and the `scope_interest_kind.entity_type` value exist. | **Gap:** Expand then contract migrations; neutral resolver thresholds/golden strata; decide how existing `scope_interests` of kind `entity_type` are rejected or migrated without turning types into facts silently. |
| E3/resolution DTOs | **Observed:** `EntityRef.type`, `ResolvedEntity.entity_type`, `ResolutionCandidate.type`, and `P1EntityRow.type` are required. | **Gap:** Remove/replace them together, including serialization tests and all fixtures. |
| Resolver paths | **Observed:** `_T0_EXACT`, T1/T2 blocking, thresholds, T4 prompt, mint, mention recording, config persistence, and `judge_pair` read type. | **Gap:** Make all tiers and stored decision features type-free under a new resolver version. |
| E3/D86/signatures | **Observed:** `_NORMALIZE_PROMPT`, retry suffix, allowed-type checks, pre/post `_signature_allows`, FK alarms, and both claim-fanout and legacy handlers use types/signatures. | **Gap:** Remove as one new normalizer generation with the replay/drain procedure above; preserve unknown-predicate handling. |
| Legacy `EntityRegistry` | **Observed:** It still has a typeful `resolve_t0` and is constructed by the production self-host E3 composition, although E3 currently uses it for normalized-claim markers. | **Gap:** Delete or narrow the class so a future/legacy path cannot mint typeful entities; update construction/tests. |
| Core/bootstrap/extensions | **Observed:** `core_manifest.py` defines eight entity types and predicate signatures; `deployment_bootstrap.py` inserts/verifies/counts them; deployment results expose `entity_types_count`; extension packs can add types; bootstrap/governance tests pin this behavior. | **Gap:** Remove type/signature bootstrap and result contracts, retire type-only extension-pack behavior, update manifest validation and catalog inventory, and keep predicate extensions. |
| Fact catalog/knowledge | **Observed:** `FactCatalog` loads type parents/signatures. `knowledge.py` handles `scope_interests.interest_type='entity_type'` and queries `entities.type`. | **Gap:** Remove dead readers and define the treatment of existing type-scoped knowledge rules/interests. |
| P1 | **Observed:** `P1EntityRow`, `P1IndexPort.search_entities_scored`, and `PostgresP1Index` carry/filter `entity_type`; query-sandbox bridge and nomination expose the filter. | **Gap:** Remove the field/filter and rebuild/verify P1 entity/profile indexing under the new profile attestation. |
| Public resolve/API/SDK | **Observed:** `QueryEngine.resolve`, `_RESOLVE_T0`, `/resolve`, SDK `resolve`, and `OperationExecutor` accept `entity_type`; `EntityCandidate.type` is required. | **Gap:** Remove it atomically across HTTP, SDK, CLI/MCP operation schemas, examples, envelope serialization, and parity tests. |
| Other query forms | **Observed:** `aggregate.typed_absence` requires `entity_type`; context hydration and confirmation SQL also select entity types. | **Gap:** Remove `typed_absence` and every hydration/confirmation dependency, with a public boundary/version note rather than silently changing its meaning. |
| Assured-operation registry | **Observed:** `CANONICAL_OPERATIONS.resolve_entity` publishes `entity_type`; stored result schema includes the typeful envelope; `by_name` requests version 1. | **Gap:** Version and replace descriptors/executor together; verify all four surfaces see the same active schema. |
| SQL query space | **Observed:** `memory_v1.entities_current.entity_type`, `mentions_live.emitted_type`, query-space catalog/manifest, open-query prose, nomination allowlists, and AST/golden vectors expose type. | **Gap:** Replace the SQL views via a new migration; regenerate `memory_v1_manifest.json` and AST golden vectors; update schema hashes and query examples. |
| P2 export/storage | **Observed:** `projection._EXPORT_SQL['Entity']` selects `e.type`; `P2_PROJECTION_SCHEMA`, schema hash, `GRAPH_DDL`, Parquet row order, and COPY contract include it. | **Gap:** Bump `P2_REBUILD_VERSION` and projection contract/hash, change export/DDL/positional Parquet specs together, build and validate a new snapshot, then atomically publish it. |
| P2 readers/wire | **Observed:** `GraphQueries` projects `b.type`; path parsing and `GraphNode.type` require it; QueryEngine hydration replaces node type from PostgreSQL. | **Gap:** Make graph reads and envelope nodes untyped before publishing the new snapshot; cover neighborhood/path/hydration and old-snapshot incompatibility. |
| P3 corpus filesystem | **Observed:** `P3_BUILDER_VERSION` is `p3-corpusfs-2026.07`; canonical entity paths are `entities/<type>/<entity_id>` and are documented as stable Tier-1 paths that never move. The builder reads `entity['type']` in path, frontmatter, and indexes. | **Gap:** This is a conflict between D96 and an older binding path contract. Clarify the design before coding: choose the untyped canonical path, define redirects/compatibility for held old paths, bump the builder version, rebuild and publish P3, and update mount/docs tests. P2-only rebuild is insufficient. |
| Tests and website | **Observed:** Resolver, E3, bootstrap, governance, retrieval, query-space, P2/P3, envelope, SDK, and surface-parity fixtures construct or assert entity types; docs contain typeful resolve examples and D86 text. | **Gap:** Assign these suites/pages to the owning WPs rather than discovering them in a final broad test run. |

## Answers to the ten review questions

### 1. WP order

**Observed:** The published order is not safe. WP-I.6 cannot follow WP-I.2 as currently written; a compatibility migration must precede the name-only mint. WP-I.4 safety and WP-I.5 measurement cannot wait until after WP-I.3 is enabled. WP-I.7 cannot ship after WP-I.1 alone because it consumes the identity contract and the rebuilt untyped P2 snapshot.

**Inference:** Useful work can still parallelize. After the missing contracts are closed, the E3 eligibility/source-alias work, reader compatibility inventory, golden fixture/harness rewrite, profile-worker foundation, D74 tests, and P2/P3 rebuild code can be developed in parallel. Their release gates remain ordered: compatible schema/readers → name-only writer and old-generation cutover → safe profile/T3 behavior and measured eval → T0 activation → untyped projection publication → D97 default retrieval.

### 2. WP-I.2 versus WP-I.6

**Observed:** They can split only as expand/contract work, not at the current boundary. Mint without type before migration fails `entities.type NOT NULL`; dropping the column/tables before old workers and readers are gone also fails. The minimal safe expand is `DROP NOT NULL` while retaining the FK/registry for old writers; the destructive contract step comes last.

**Gap:** Either merge I.2/I.6 into one stopped atomic cutover or split them into reader compatibility, a nullable-column expand migration that retains the FK/registry, writer cutover, queue drain, and final FK/column/table contract cleanup. The plan must choose one deployment model.

### 3. T0 second mint and alias uniqueness

**Observed:** Same lemma on two IDs does not violate the current unique constraint because `entity_id` is part of the key. No alias schema change is needed for multiplicity.

**Gap:** The query must return distinct entity IDs, source/canonical alias upserts must be idempotent, and exclusions must canonicalize UUID order. The unresolved issue is how an entity-pair exclusion avoids repeated adjudication of a future unbound mention.

### 4. Profile worker, salience, debounce, and first mint

**Observed:** There is no existing worker to extend. Only reserved stage/component vocabulary exists. Salient fact count/order, debounce, provenance, and recovery are not specified.

**Inference:** Distinctive `SAP` is safe to auto-accept on the next unique exact mention under D95. A common `John` is unsafe if the name-only mint vector remains, because T3 will reproduce exact-name matching. With no profile/vector, it must reach an evidence-aware/fail-safe decision rather than gain certainty from absence of evidence.

### 5. `judge_pair` and golden set

**Observed:** Tests and fixtures exist, but they are type-stratified, tiny, and do not contain a same-spelling non-match. The runner discards tier. The pair API cannot model the production guard/profile/multiplicity state.

**Gap:** Add neutral strata, per-tier confusion data, exact-name match and non-match rows, production-equivalent seeded state or structured profile/fact inputs, one-curve floors, and a pre-I.3 release gate.

### 6. Graph, P2, and retrieve consumers

**Observed:** Every known type consumer is enumerated in the blast-radius table above. The most easily missed are query-space manifests/views, `typed_absence`, assured-operation versioning, P1 filters, legacy `EntityRegistry`, scope interests/knowledge, P2 Parquet positional order, and P3's supposedly stable type-bearing path.

**Gap:** Make that inventory an acceptance checklist in the plan. “Graph nodes untyped; P2 rebuild” alone does not cover it.

### 7. D86/signatures and replay

**Observed:** Old `component_version` rows remain replayable and observation staging/barriers are keyed by normalizer generation. Reusing an old version with new name-only semantics is provenance-wrong; dropping dependencies while old code can claim work is operationally unsafe.

**Gap:** Bump versions, drain or re-enqueue old work explicitly, reconcile staging/barrier rows, test a mixed-generation cutover, and drop schema only after old claims are impossible.

### 8. Guard and common names

**Observed:** No writer exists, no starting threshold exists in config, and the table is currently only deleted by forget. A live distinct-ID guard is necessarily late for the first `John` split.

**Gap:** Add a versioned static common-name/min-length policy for the first split, a distinct-active-survivor recomputation writer for later promiscuity, exact T0/T1/T2 semantics, initial measured values, and rebuild after forget.

### 9. Missing WPs

**Gap:** Add explicit WPs or sub-WPs for:

1. contract closure for source surface, empty-profile fail-safe, exclusions, profile selection/debounce, D97 composition/failures, P3 path migration, and migration downgrade;
2. reader compatibility plus schema expand/contract and deployment cutover;
3. E3/resolver generation drain/re-enqueue;
4. D74 profile/guard/queued-work scrub and S55 coverage;
5. public API/schema/assured-operation/SDK/CLI/MCP cutover, acknowledging that no OpenAPI artifact is currently shipped;
6. P1 rebuild and P2/P3 versioned rebuild/publication;
7. bootstrap/catalog/extension/scope-interest cleanup;
8. one-curve eval floors and per-tier false-merge reporting;
9. model/prompt/temperature/input-selection/embedding pins and cost-ledger keys for profile/T4 changes; and
10. rollout observability, rollback limits, and old-snapshot compatibility.

### 10. Acceptance tests

**Observed:** Design §11 is a good behavioral nucleus but is insufficient and several tests are assigned to the wrong WP. For example, “two same-name vectors differ” needs profile worker plus T3; “list banks” needs P1/retrieval as well as profile; “works_for with no types” needs writer plus schema/bootstrap; and neighborhood behavior needs the new P2 contract, not WP-I.7 alone.

**Gap:** Add migration-with-data and mixed-version tests; empty-profile `John`; distinct exact-hit counting across alias provenances; concurrent same-lemma resolution; source-alias fidelity and idempotency; profile selection/debounce/stale-result/atomicity/failure; shared-entity forget; per-tier eval with non-empty denominators; bootstrap/query-space/API/P1/P2/P3 schema contracts; assured-operation version parity; and D97 end-to-end failure/truncation tests. Place each on the WP that can introduce the failure, and keep the final exit run as confirmation rather than first discovery.

## WP-by-WP disposition

| Existing WP | Disposition | Required change |
|---|---|---|
| WP-I.1 | **Split / keep early.** | **Gap:** Keep bare-noun eligibility early. First close the source-surface contract; then add idempotent source/canonical alias writers. Static common-name configuration can land here. Guard-writer code can develop here, but its multi-ID acceptance depends on I.3 and its post-forget rebuild depends on D74 work. |
| WP-I.2 | **Split and move behind schema expand.** | **Gap:** Separate reader compatibility, name-only DTO/writer, D86/signature removal, and old-generation cutover. It cannot promise “no type FK on insert” before the migration. |
| WP-I.3 | **Keep, but gate later.** | **Gap:** Depend on compatible schema/readers, name-only writer, missing-profile T3 safety, common-name config, and the eval gate. Specify distinct exact hits, alias duplicate handling, exclusion insert/use, concurrency, and failure behavior. |
| WP-I.4 | **Split; move safety before I.3.** | **Gap:** I.4a defines schema/selection/versioning and removes name-only-vector certainty; I.4b creates/composes the worker, backfills profiles, and enables T3/T4 consumption. Add D74 integration before either profile output ships. |
| WP-I.5 | **Split preparation from final run; preparation moves before I.3.** | **Gap:** Schema/harness/fixtures and floors are a pre-activation gate. Final measured runs occur after I.3/I.4. Persist per-tier and global results. |
| WP-I.6 | **Replace with compatibility + contract-cutover group.** | **Gap:** Include all consumers in the blast-radius table, old-worker drain, public operation versioning, P1/P2/P3 rebuilds, bootstrap/extension cleanup, query-space regeneration, and downgrade/rollback rules. |
| WP-I.7 | **Keep implementation parallelizable; move ship dependency.** | **Gap:** It may be coded once the recipe contract is closed, but it cannot ship until identity behavior, untyped public schemas, and a validated untyped P2 snapshot are active. Specify composition, ranking/caps, and failure behavior. |
| WP-I.8 | **Keep as a cross-cutting rule, not a final package.** | **Gap:** Name the exact pages/tests per WP and update only when the corresponding behavior ships. |

## Suggested revised WP table

This table describes release gates. Work noted as parallel may be developed concurrently but cannot be activated before its dependencies.

| Revised WP | Goal | Depends | Can parallelize with | Acceptance gate |
|---|---|---|---|---|
| WP-R.0 Contract closure | Resolve source-surface representation; empty-profile decision; exclusion use; profile selection/debounce/version; D97 composition/failures; P3 path; downgrade policy. | D95–D97 | — | **Gap:** Binding design amendments answer each item without changing D95–D97. |
| WP-R.1 Reader/schema compatibility | Make all readers tolerate the future untyped row; add the expand migration that drops `entities.type NOT NULL` but retains its FK, column, and registry for old writers. | R.0 | R.2, R.3, R.4 test/fixture work | **Gap:** Populated upgrade passes; old writer still operates; compatible reader handles both typed and untyped fixtures; rollback rule tested/documented. |
| WP-R.2 Extract eligibility and aliases | Bare-noun refusal, exact source surface + canonical alias, idempotent upsert, static common-name/min-token config, guard recomputation implementation. | R.0 | R.1, R.3, R.4 | **Gap:** `game` dropped, `FIFA 23` allowed, `App`/`Application` fidelity proven, repeated replay updates rather than violates alias uniqueness. |
| WP-R.3 Eval foundation | Migrate golden schema away from type strata; production-equivalent harness; per-tier/global curves and explicit floors; §8 fixtures. | R.0 | R.1, R.2, R.4 | **Gap:** Same-spelling non-match appears as a T0/T3/T4 error when regressed; undefined strata fail. |
| WP-R.4 Profile safety and D74 | Define/store selected inputs and attestation; remove name-only ready vector; create/compose debounced refresher; shared-survivor forget invalidation/rebuild; version/cost pins. | R.0, R.1 | R.2, R.3, R.5 preparation | **Gap:** Missing/stale profile is fail-safe; deterministic salience and debounce/replay pass; shared forget leaves no forgotten profile text. |
| WP-R.5 Name-only writer cutover | Name-only `EntityRef`; no emitted type; remove D86/signature gates; bump E3/resolver versions; drain/re-enqueue old generation and reconcile staging/barriers. | R.1, R.2 | R.3, R.4 | **Gap:** Novel mint succeeds without type; dual-role `works_for` persists; mixed-generation test drains without duplicate facts or stuck barriers. |
| WP-R.6 T0/second-mint activation | D95 T0 conditions, all distinct exact hits, second mint, exclusion insertion/use, guard behavior, bounded lock/failure handling. | R.3, R.4, R.5 | projection rebuild preparation | **Gap:** SAP, empty-profile John, father/son, Java, two-employee, alias-provenance, and concurrent-race cases pass; release floors pass before activation. |
| WP-R.7 Type contract and projections | Drop legacy type/signature registries after old work is impossible; update bootstrap/extensions/knowledge/P1/query space/API/envelopes/operations; version and publish P2/P3 rebuilds. | R.5, R.6, old-work drain | R.8 implementation | **Gap:** Blast-radius checklist is empty; public surface parity passes; new P2/P3 snapshots validate and publish atomically; no old worker/snapshot is served incompatibly. |
| WP-R.8 D97 default retrieval | Explicit resolve step followed by direct fact lookup, one-hop empty-predicate neighborhood, and ID-constrained fact-text search; optional dynamic predicate. | R.6, R.7 | Can be implemented earlier against fixtures | **Gap:** End-to-end `other:` relation + observation case passes; ambiguity, no/stale P2, hydration drop, caps, and partial-channel failures are explicit; zero new query-path completions. |
| WP-R.9 Rollout, eval record, and docs | Backfill profiles; rebuild guard/P1/P2/P3; record eval run; publish per-WP website/API docs and operational dashboards. | R.2–R.8 | — | **Gap:** One global curve and tier breakdown meet pinned floors; versions/cost keys visible; D66 docs describe only active behavior. |

## Verification status

### Verified directly in code or schema

- **Observed:** The current T0 exact query returns at most one alias row and immediately records confidence 1.0.
- **Observed:** Alias uniqueness permits one lemma on multiple entity IDs; source/canonical aliases on one entity can create duplicate rows for naive exact-hit counting.
- **Observed:** Mint requires and validates type, writes `emitted_type`, and creates a name-only entity embedding.
- **Observed:** T3 thresholds are selected per type; resolver-version persistence is `thresholds_by_type`.
- **Observed:** E3 prompt/output/retry/signature paths and their representative tests are type-dependent.
- **Observed:** There is no implemented profile refresher or guard writer.
- **Observed:** The eval runner is per type, drops the deciding tier, and lacks an exact same-name non-match.
- **Observed:** Empty predicate lists already make `GraphQueries.neighborhood` traverse all `RELATES` edges.
- **Observed:** Public resolve, envelope models, assured descriptors, P1, query-space, P2, and P3 expose entity type.
- **Observed:** P3 uses type in a path previously promised stable; P2 stores type in schema, DDL, Parquet/COPY order, and graph output.
- **Observed:** Hard-forget clears profile/embedding only for exclusive retired entity IDs, not all affected surviving resolved IDs.
- **Observed:** The current Alembic head is `p9_13_0034`; the migration test pins the complete revision list.
- **Observed:** The FastAPI query app disables its OpenAPI endpoint and no committed OpenAPI artifact was found.

### Inferred or still requiring implementation-time confirmation

- **Inference:** The exact production deployment model—rolling binaries versus a stopped worker cutover—was not established from the reviewed files. The plan must choose one; the revised table assumes expand/contract because it is robust to mixed versions.
- **Inference:** No live production database, queue inventory, snapshot catalog, or deployment-specific profile/guard data was inspected. The drain/rebuild WPs therefore require as-built operational inventory before execution.
- **Gap:** Exact salient-fact counts, ranking weights, common-name list, promiscuity threshold, global eval floors, profile model, and debounce duration are not present as accepted values. They must be measured and version-pinned; this review does not invent them.
- **Gap:** The compatibility promise for existing P3 type-bearing paths and the operational meaning of entity-pair exclusions during fresh-mention resolution require binding design clarification.
- **Observed:** No implementation code was changed and no live service or database test suite was run for this review. The only repository change is this review document; conclusions are based on direct static inspection of the named source, schema, and tests.

## Final recommendation

**Request changes.** Keep D95–D97 frozen, but replace the current linear WP dependencies with the revised gated cutover. The minimum bar before implementation begins is to close WP-R.0, put schema compatibility before the name-only writer, put profile/T3 safety and a tier-visible eval gate before T0 activation, and add the full type-consumer, D74, replay, P2, and P3 work to the plan. Without those changes, the plan asks the implementing agent to discover correctness, migration, and retention contracts while production behavior is already in transition.
