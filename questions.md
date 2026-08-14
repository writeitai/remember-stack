# Open Questions & Underspecified Areas

The living register of **what is not settled yet** — open decisions, unwritten/underspecified
designs, known risks, and concrete inconsistencies to fix. It is the one place to look for "what's
still open"; it cross-links the two specialized trackers:

- **`decisions.md`** — what *is* decided (D1–D73).
- **`plan/analysis/objections.md`** — the step-back critique (O1–O6) with accept/reject status.
- The **design-doc index** in `plan/designs/overall_design.md` — which design docs are written
  (`current`) vs. `planned`.

Keep this current: when something here is decided, move it to a decision and prune it below.

---

## 1. Open decisions (undecided — answers shape the design)

**Scale & cost**
1. ~~**Document mix & arrival rate**~~ **ROUTED OUT OF OSS DESIGN (D60).** Corpus shape and
   arrival rate are deployment capacity inputs, not library architecture. Phase 7 uses fixed,
   reproducible synthetic profiles to test the D23 scale shape; the cloud/operator sizes real
   workers and rate limits from its own workload.
2. ~~**Monthly LLM/embedding budget ceiling**~~ **ROUTED OUT OF OSS DESIGN (D60).** The library
   ships metering and configurable budget parking; a real monetary ceiling is deployment policy.
   Tests use explicit fixture limits, and benchmark runs report observed cost without requiring
   an owner budget first.
3. ~~**Embedding model + dimension**~~ **RESOLVED (D63)** — the embedder is per-deployment
   port configuration (D61); shipped default `qwen/qwen3-embedding-8b` via the OpenRouter
   adapter (self-hosted open weights = second adapter). Conventional model → the E1
   context-prefix stage exists (the branch in `e1_chunks_design.md` §5 binds to
   conventional + prefix). **D94 amendment:** the PostgreSQL reference profile
   now pins Qwen output and pgvector columns to 1,536 dimensions; dimension no
   longer gates the P1 engine migration through a benchmark.
4. ~~**LLM per stage**~~ **RESOLVED for the extractor seat (D70)** — defaults are model-provider
   port configuration: extraction (and the cascades' small rung) defaults to `gpt-5.6-luna`,
   the frontier rung to `gpt-5.6-sol`; checker seats cross-family per D53. The phase-2/6
   seats inherit the principle and are gated by their phases' golden-set measurements.

**Knowledge plane (K)**
5. ~~**K3 belief content**~~ **RESOLVED (D73).** There is no shipped K3 belief tier. Personal
   or organizational principles are authored pages in a K2 purpose scope; compiled K2 pages may
   provide cited support or suggestions but never promote a stance automatically. E3 remains the
   system's current fact state. No numeric stance score is inferred.
6. **K1/K2 freshness window — measurement/configuration, not an owner gate.** The compile-cycle
   economics spike (`k_layers_design.md` §11) selects a shipped starting value; deployments may
   configure it. No hosted workload forecast blocks the library.

**Operations**
7. **PageIndex: hosted API or self-hosted? — resolved (D71): neither.** The structurer is a
   port-configured LLM seat inside the library; the deterministic snap guards its output.
   "PageIndex" names the output shape, not a dependency.
8. **Security / access model — trust model decided; deployment ops routed out of OSS design.** Decided
   (D50/D51): **content-level authorization and per-user scoping are library non-goals** — a
   deployment is one trust domain; isolation = separate deployments; perimeter security is
   deployment infrastructure; the raw mount requires data-access audit logging. IAM layout, key
   management, hosted perimeter mechanics, and audit-review cadence belong to the operator/cloud.
9. ~~**Postgres HA appetite**~~ **ROUTED OUT OF OSS DESIGN (D60).** Replicas, failover, acceptable
   downtime, and PITR operation belong to the deployment operator/cloud. The library owns schema,
   migrations, the portable-state definition, and fail-closed restore/non-resurrection contracts;
   operators move bytes with native tools (D75).
10. ~~**Observability stack**~~ **RESOLVED AT THE LIBRARY BOUNDARY (D60/D61).** OSS emits typed
    telemetry through the telemetry port and exposes durable state through agent/admin surfaces.
    Grafana, GCP-native monitoring, retention, alert routing, and dashboards are operator/cloud
    choices.
11. ~~**Backfill / reprocessing orchestration design**~~ **RESOLVED (D52/D67;
    `orchestration_design.md` §2–§4).** Phase 7 WP-7.1 implements the existing version-filtered
    seeder, separate lane, throttling, resumability, and rollback/replay semantics.
11a. **OSS governance & release readiness (D60/D61) — RESOLVED (D77, 2026-07-23);
    CLA active.** Conclusions (2026-07-08 comparables + 2026-07-23 owner resolution; full
    detail in the cloud repo's split analysis, `04_licensing_naming_findings.md`):
    - **License: stay Apache-2.0** (already shipped). Verified comparables: Sentry FSL-1.1-Apache-2.0
      (tightened only after a decade of dominance), E2B Apache-2.0, Hermes agent (Nous Research) MIT —
      agent-era infrastructure launches permissive; adoption is the existential game. Apache over MIT
      for the explicit patent grant. FSL stays a *documented trigger* (a competing hosted offering at
      meaningful scale), never a launch choice.
    - **The bounded CLA is specified by D77** rather than a plain DCO. Contributors retain
      ownership; the grant preserves a hosted-service-protective license option only when source
      remains available and self-hosting remains free. `CLA.md`, immutable versioned pull-request
      assent, and the required `CLA` status on `main` close this gate.
      `TRADEMARKS.md` separately allows truthful reference, interoperability, unmodified releases,
      and clearly distinguished forks while reserving confusing official-product uses.
    - ~~"Ultimate Memory" is a working title — a rename gates public release~~ **NAME DECIDED
      (D76, 2026-07-23): product `RememberStack`, canonical home `remember.dev`, attribution
      “RememberStack by WriteIt.ai.”** The release identifiers are repository
      `writeitai/remember-stack`, distribution/import `rememberstack`, CLI `remember`, container
      `ghcr.io/writeitai/remember-stack`, and configuration prefix `REMEMBERSTACK_*`. The domain
      remains the canonical public address; the full product mark appears in product prose and
      metadata. The pre-release working identifiers received one clean rename with no compatibility
      aliases. Preliminary availability research is not legal clearance; under D77 the owner
      explicitly accepts that risk and removes focused attorney clearance as a release gate.
    - ~~The packaging/distribution design~~ **RESOLVED (D62)** —
      `plan/designs/packaging_distribution_design.md`: the three artifacts, the client surface
      (lineage-aware ingest; connector management vs execution), delivery-only task execution
      over `processing_state` with the two shells + janitor, the enforced hexagonal layout,
      compose profiles, release/upgrade/portable-restore policy. D76 supplied the exact targets,
      the mechanical rename and D77 governance activation are complete.
    - ~~Stack-convention slots (roadmap §3).~~ **RESOLVED (2026-07-17;
      `PLAN-RECONCILIATION-WP-0.1-STACK-CONVENTIONS` / WP-0.1)** —
      [PR #39](https://github.com/writeitai/remember-stack/pull/39) merged `uv`/Hatchling,
      Ruff, the single-package `src` layout and naming, and GitHub Actions CI;
      [PR #41](https://github.com/writeitai/remember-stack/pull/41) merged the typed
      pydantic-settings/secret convention and direct-environment-access lint guard. The
      roadmap now links each choice to the exact repository evidence. The release rename and
      D77 `CLA` required-check activation are complete.

## 2. Open objections (unresolved critique — see `objections.md`)

12. ~~**O2 — collapse K1/K3?**~~ **RESOLVED (D47, refined by D73).** One compilation
    mechanism, N scopes; K1 is the default scope and K2 holds purpose-specific compiled and
    authored knowledge. The proposed K3 default was removed because normative principles are
    authored K2 content and evidence-qualified facts already live in E3. See
    `plan/designs/k_layers_design.md` §2/§8.
13. ~~**O4 — semantic regenerability of the K plane.**~~ **RESOLVED (D45/D46).** Every page
    carries routing rules + binding citations; compiled pages are semantically regenerable; the
    non-rebuildable surface narrows to human-authored content; the deletion cascade reaches K by
    citation reverse-lookup. See `k_layers_design.md` §4–§5, §10.
14. **O6 residual — the extraction/supersession eval harness.** O6 is only *partially* folded in:
    D22 covers the registry (ER) + retrieval eval, but the **E2/E3 side** has no eval harness yet —
    Selection precision / false-drop canaries, grounding safety, relation-normalization quality, and
    supersession/contradiction metrics (the E2/E3 design lists these as spikes). Needs an owner.

## 3. Underspecified / unwritten designs (`planned` in the design index)

15. ~~**K plane — highest risk, least designed.**~~ **RESOLVED (D45–D47, D73)** —
    `k_layers_design.md` is written (planner/writer/driver compile system; compiled vs authored
    pages; K1 plus K2 purpose scopes; the former K3 proposal is withdrawn). The fraught mechanism (concurrent
    sessions, merge retry, hot-file delays) is removed, not mitigated. Remaining K items: the
    spikes in `k_layers_design.md` §11 (rule-kind coverage, planner blast-radius bands, writer
    completeness eval, compile-cycle economics, git-history erasure) and #6 above.
16. ~~**Retrieval — the consumer surface.**~~ **RESOLVED (D48–D51)** — `retrieval_design.md` is
    written, driven by the S1–S63 scenario battery (`plan/analysis/retrieval_scenarios.md`):
    zero-LLM primitives + registry recipes, the response envelope (grain / contradictions /
    freshness / typed negatives), propose-dispose hydration, four mounts + filesystem-first
    precedence, the consumption skill. D94 replaces Lance with PostgreSQL-native
    P1 without a new benchmark gate. Remaining retrieval items: the spikes in
    `retrieval_design.md` §13 (hub pagination, rerank weights, envelope overhead,
    the S58 cold-agent protocol) and the deployment-security slice of #8.
17. **Spine schema (`postgres_schema_design.md`).** ~15 tables are sketched across separate decisions
    with no consolidated schema, FK map, indexes (only D23, registry-only), constraints, partitioning
    (only the 3 big tables), or migration convention. Mostly consolidation, not new invention.
18. ~~**E1 chunking.**~~ **RESOLVED (D57–D58)** — `e1_chunks_design.md` is written: the
    blockizer + block substrate, sections snapped to the block grid, non-overlapping
    whole-block chunk packing with anchors, multi-granularity retrieval (claims = needle
    index), extraction batching, the A1–A3 reuse mechanics. The embedding-model branch point
    is **resolved by D63** (conventional default → the prefix stage exists; #3).
19. **P1 search indexes.** Referenced everywhere, never a dedicated design (what's embedded, index
    params, FTS config, inline-write vs. rebuild path).
20. ~~**Cost / metering design**~~ **RESOLVED (`orchestration_design.md` §4; schema §2).** Phase 7
    implements per-stage/lane accounting and configurable park-never-drop enforcement. A hosted
    spend dashboard and real currency ceiling are deliberately outside the OSS design (D60).
20a. **P2 projection (LadybugDB) — spikes: RUN, all six recorded (WP-4.1, `plan/analysis/p2_spike_battery.md`).** Headlines: UUID PK confirmed; ATTACH-direct is dead on capability grounds (the scanner cannot attach enum-bearing schemas — Parquet transport confirmed); inline as-of predicates bind parameters and compose with SHORTEST (30-hop cap recorded); NULL timestamps safe; D69 retention stands. Original charge (D44,
    `plan/analysis/ladybug_translation_research/SYNTHESIS.md` §6).** The translation is designed (the
    `v_graph_*` views, §10.A), but verify on the deployed engine: (a) **UUID-as-node-PK** smoke test
    (source-verified; confirm the packaged build; STRING fallback documented); (b) **ATTACH cross-DB scan
    throughput + pushdown** at 10⁷–10⁸ rows — gates ATTACH-direct vs. the committed Parquet baseline,
    esp. the `MENTIONED_IN` aggregation + merge-survivor recursion; (c) **merge-redirect recursion**
    cycle guard + the pre-snapshot validation gate; (d) **inline multi-hop as-of path-filter performance**
    (projected-graph `MATCH` is unavailable — §4); (e) **invalidated-edge retention** — measure
    snapshot size, rebuild duration, and transaction-time demand to decide whether evidence justifies
    replacing D69's unbounded/default endpoint-bounded projection with a finite hot-snapshot horizon
    and explicit fallback contract; (f) NULL-`TIMESTAMP` semantics through the Parquet round-trip.

## 4. Known risks (in decided approaches)

21. ~~**K = non-deterministic agents writing a shared git repo at scale**~~ **RESOLVED (D45)** —
    the compile driver is the repo's only automated committer; writers compile disjoint pages in
    dependency order, so write contention is structurally impossible. The residual risk moved to
    **planner quality** and **writer completeness** — both inspectable state with an eval surface
    (`k_layers_design.md` §7/§11), not emergent session behavior.
22. **Cross-document coreference** — "the CEO" referring to an entity introduced in *another* document
    falls between intra-doc coref and named-mention ER. An unowned recall hole.
23. ~~**Mixed-freshness retrieval**~~ **RESOLVED (D48/D49)** — projections only *nominate*;
    every result is re-verified by-ID against live Postgres at hydration (staleness costs
    recall, never correctness), and the envelope stamps freshness per contributing source (PG
    live / P1 lag / P2 snapshot ts / K compiled_at + flags). See `retrieval_design.md` §2/§5.
24. ~~**End-to-end hard delete / GDPR.**~~ **RESOLVED (D74,
    `plan/designs/hard_forget_design.md`).** One append-first portable manifest is the durable
    lineage-forget intent outside the ordinary restore set; one fail-closed, idempotent worker
    reuses the normal lifecycle transition, scrubs PostgreSQL, purges objects/P1, rebuilds and
    removes old P2/P3 snapshots, and erases affected K paths from history. Serving readiness
    replays every manifest before traffic, so an old restore cannot resurrect content. Authored K
    and curation prose must be owner-redacted before acceptance; the library never rewrites it.
    Provider backup schedules/expiry remain operator/cloud responsibilities under D60. WP-7.5 now
    implements and activates the S55 gate; design resolution alone is not runtime completion.

## 5. Concrete inconsistencies to fix

25. ~~**P3 ↔ K: docs disagree on whether K is a structural input.**~~ **RESOLVED** — the
    cross-link-only model is adopted everywhere: K is never a structural input to P3 (P3 stays
    rebuildable from the E spine + artifacts). D40 carries a refinement note;
    `overall_design.md` §5 and `requirements_v3.md` §Plane P updated to match
    `e0_files_design.md` §6.
26. **D23 vs D25: gated vs. full extraction volume.** D25 re-stamped the three 10⁸ tables to **full
    extraction** sizing when the value gate was dropped (and `registries_design.md` agrees), but **D23
    still says** "row counts are contingent on the value gate — size against *gated* volume." Update
    D23.
27. ~~**P2 graph design ontology is stale vs D18.**~~ **RESOLVED (D44).** `p2_graph_design.md` §2/§3 now
    use the D18/D64 seed core (8 types + 16 predicates), `DOC_CROSSREF(kind)` (generalizing the old `CITES`),
    and the `IS_DOCUMENT` bridge — see the Postgres→LadybugDB translation analysis
    (`plan/analysis/ladybug_translation_research/SYNTHESIS.md`) and the `v_graph_*` projection views
    (`postgres_schema_design.md` §10.A).
28. **E3 worked example contradicts D18.** `e2_e3_claims_relations_design.md` §5 shows
    `"Project Atlas launched in 2024" → (Project Atlas, launched_in_year, 2024)` — but D18 says
    objects are entities, attributes stay in claims, and time is bi-temporal *edge metadata*, never a
    predicate/Date-node. Fix the example.
29. **E3 claim→predicate mapping is thin.** §5 delegates internals to the registries but doesn't
    specify how the governed predicate is *chosen* from claim text or where domain/range is enforced
    in the flow.
30. ~~**Requirements temporal-split paragraph predates D43/D49.**~~ **RESOLVED (PR #43).**
    `requirements_v3.md` §Retrieval now reads "current-fact validity lives on the fact layers —
    relations and observations" (the claim/fact split; citations extended with D43/D49). (Found
    while authoring `plan/implementation_evals/`.)
31. ~~**Requirements E3 bullet omits observations.**~~ **RESOLVED (PR #43).** The E3 bullet now
    names both fact layers (relations + observations, incl. D59 stance observations), both
    bi-temporal units of supersession/contradiction, with the graph-projection distinction. (Found
    while authoring `plan/implementation_evals/`.)

## Resolved since the last version of this file (moved to decisions)

- **The attributed-stance / qualitative-belief fork** (review F2; blocked scenario S37) →
  **D59**: attributed stance is a Selection keep class, normalized to holder-anchored
  observations (ordinary D43 machinery — changed minds are supersession); unattributed opinion
  still drops; surfaced distributions recorded as the documented alternative.

- **Re-extraction evidence inflation (review F3) + document versioning for watched sources**
  → **D54–D56** + `plan/designs/evidence_lifecycle_design.md` (testimony currency; evidence_count
  ≡ distinct current-testimony lineages; lineages/versions with snapshot|living semantics;
  content-addressed chunk reuse). Spikes tracked in that design §11.

- **The K plane design** (was #15 "highest risk, least designed", #12 O2, #13 O4, #21 the
  shared-repo bottleneck) → **D45–D47** + `plan/designs/k_layers_design.md`: manifest-driven
  planner/writer/driver compilation; compiled vs authored pages; one mechanism with K1 plus K2
  scopes (D73 removes the proposed K3 default). Open remainders stay tracked above (#6 cadence)
  and in the design's §11 spikes; D74 closes the hard-forget design residual.

- **Ontology seed** (was "what seeds the ontology") → **D18** (8 core types + 14 predicates, since grown to 16 by D64, with
  domain/range; extension packs; `other:` promotion).
- **Multi-tenant / ID scoping** (was "single user or multi-tenant") → **D16** + the deployment model
  (`registries_design.md` §1): separate deployments = separate Postgres instances + entity spaces.
- **Raw/artifact deletion** (was "hard-delete requirements") → **E0 §2** for normal deletion;
  **D74 / `hard_forget_design.md`** resolves the irreversible P1/P2/P3/K purge and restore
  non-resurrection contract. WP-7.5 implementation and S55 proof remain; physical backup
  scheduling/expiry is operator/cloud scope under D60.
