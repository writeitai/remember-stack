# Registries Design — Entity Resolution, Ontology, Governance

The plane-E substrate that canonicalizes entities and predicates. Distills the registry
research (`plan/analysis/registry_research/SYNTHESIS.md`, R1–R10) and the entity-registry
analysis (`plan/analysis/entity_registry.md`) into binding design. Formalizes objection O5;
decisions **D15–D24** (and D4/D5). Numbers here are starting points to be measured on the
golden set (D22) / a corpus slice — not committed constants.

> **Amended 2026-08-26 (D95–D96).** Identity, T0-as-verdict, and **entity types**
> (including first-mint `entities.type` and domain/range) are superseded by
> [`entity_identity_and_retrieval_design.md`](entity_identity_and_retrieval_design.md).
> This file remains current for blocking, clustering, review, **predicate** packs,
> and eval harness shape except where that design speaks.

## 1. Role and scope

The registries are **cross-cutting substrate of plane E**, not a layer (D14): layers
*transform* (E0→E3), registries *canonicalize*. Two registries:

- **Entity registry** — maps entity *mentions* to canonical *entity IDs*; the identity
  authority for the whole system.
- **Predicate/type registry** — the governed ontology (D5, D15, D18): entity types, predicates,
  domain/range constraints, synonyms, scope ownership.

Why this is a first-class subsystem with metrics from day one (O5): resolution quality is
load-bearing in three places — the `(entity_id, predicate)` supersession blocking key (D4),
relation evidence aggregation (D2), and graph neighborhood/distance quality (D9). Failure here
is **silent**: a missed merge means a stale fact is served as current with no error. The
asymmetry governs every default — **under-merging degrades gradually; over-merging poisons
catastrophically** — so the system is recall-conservative and reversible throughout.

### Deployment model: one system, N independent instances

The system deploys as **independent instances**, one per problem domain — target deployments
include a personal assistant, the brain of an AI-native agency (development + marketing of
multiple online products), a data-migration project between enterprise systems for a
manufacturing company, and a knowledge engine for a law-related product. Rules:

- **Entity spaces are never shared across deployments.** D16's "one graph, one entity space"
  applies *within* a deployment; separate deployments are separate Postgres
  instances/schemas, separate registries, separate graphs (D68). A client project's data and a
  personal assistant's data must never co-resolve, and no shared operational database routes
  rows for both.
- **Each deployment = the universal core (D18/D64/D69) + chosen extension packs (§4) + its own K2
  scopes.** After Alembic creates structural head, the library-owned typed deployment bootstrap
  creates or verifies the D68 deployment row and §4's exact universal manifest in one transaction.
  The core is identical everywhere; packs and scopes are separate per-deployment choices.
- The multi-scope case *within* one deployment (e.g. the agency: multiple products as K2
  scopes over one shared entity space) is exactly D16's "scopes multiply, truth doesn't".
- **Language is a per-deployment property**: a deployment with Czech (or other
  inflected-language) corpora needs the multilingual matching of §5; English-only deployments
  do not.

## 2. Data model (Postgres — the single authority, D6)

The transcript/verdict epistemics of D2/D3 apply to resolution too: mentions are evidence,
entities are verdicts, resolution is re-adjudicable.

```
mentions (immutable — the transcript)        entities (the registry)
  mention_id, surface_form, normalized_lemma,  entity_id        ← NEVER reused
  context, claim_id|chunk_id, doc_id,          type (→ type registry), canonical_name,
  language, char_span                          status, merged_into (redirect chain),
        │                                       profile_summary, embedding,
        │                                       embedding_model, embedding_input_policy_version,
        │                                       embedding_text_hash
        ▼
resolution_decisions (append-only — the verdict)   aliases
  decision_id, mention_id → entity_id,               alias_id, entity_id, alias_text,
  method (T0–T4), confidence, features jsonb,        normalized_lemma, provenance
  resolver_version, decided_at, superseded_by        (source|llm_canonical),
                                                      confidence, first_seen, last_seen
merge_events (append-only — reversibility)
  merge_id, survivor_id, absorbed_id, evidence,
  pre_merge_membership_snapshot jsonb, decided_at, reversed_by
```

(No `external_ids` table — resolution is registry-self-contained, D20. If a *future*
deployment ingests structured data with its own authoritative keys (internal/domain IDs, not
3rd-party registries), those would attach as aliases or a per-deployment table — out of scope
now.)

Ontology tables:

```
entity_types(type, parent_type → entity_types, description, examples, schema_org_ref, status)
predicates(predicate, parent_predicate, subject_type, object_type, description, examples,
           synonyms[], status ∈ {core,extension,other,deprecated}, scope_id nullable)
scope_interests(scope_id, interest_type ∈ {entity_type,predicate,metadata,keyword}, value)
resolver_versions(resolver_version, tier_config jsonb, thresholds jsonb, configured_at)
```

Invariants: `entity_id` is **never reused**; a merge is a **redirect** (`merged_into`), never a
rewrite (Wikidata model) — everything downstream that stored the old ID still resolves; P2
rebuild (D7) re-points graph edges on merge/un-merge for free.

### Entity profiles — maintenance of `profile_summary` / `embedding`

The registry caches two derived fields per entity, and they are **inputs to the cascade
below**, so their maintenance is owned here. `profile_summary` — a short blurb ("Czech ML
researcher at CTU; works on entity resolution") — is shown on the P2 graph node and the P3
entity index, and is given to **T4 adjudication as candidate context** (comparing a new
"J. Novak" mention against a candidate is a much easier judgment when the candidate carries a
profile — the Graphiti lesson). The **profile embedding and its attestation**
live as private derived columns on the canonical entity row (D94); there is no
opaque reference or `p1_entity` mirror table. **T3** compares mention embeddings
against the ready current vector on that row.

They are maintained by a dedicated **profile refresher** worker: a batched micro-LLM job
(small model; stage `refresh_profile`, component `profile_summarizer` — schema §1), versioned
and replayable like every non-deterministic producer (D7/D12), **debounced on evidence
change** — an entity whose relations/observations materially changed since its last profile
build is re-summarized and re-embedded on the next batch (new entities get a first profile as
soon as they have any evidence to summarize; until then T3/T4 fall back to alias/mention
signals alone). Boundaries: the refresher writes **only** these two fields — never names,
aliases, types, or status (identity belongs to resolution) — and profile staleness degrades
inside the cascade's existing safety envelope: one global threshold set is golden-set-measured
(D22/D96), near-misses escalate rather than auto-reject (§3), and high-blast-radius merges route
to review regardless (§6) — a stale profile costs match quality, never an unreviewed
catastrophic merge.

## 3. Resolution cascade — T0–T4, block-loose / decide-tight (D17)

One canonical cascade. Stop at the first confident match. **Registry-self-contained — no
3rd-party external-authority tier** (D20).

| Tier | Mechanism | Role | Where |
|---|---|---|---|
| **T0** | exact match on the canonical name form (LLM-emitted, §5) | decision | Postgres |
| **T1** | fuzzy blocking — `pg_trgm` GIN, recall-first low floor | **candidate generation, NOT a decision** | Postgres |
| **T2** | phonetic — Daitch-Mokotoff (`fuzzystrmatch`), **not Soundex** | candidate generation | Postgres |
| **T3** | embedding similarity, residue only | decision (mid band) | PostgreSQL P1 (D94) |
| **T4** | LLM adjudication (small→frontier); human review for high blast-radius | decision (ambiguous band) | worker |

- **Thresholds are global, golden-set-measured, and versioned** (`resolver_versions`), stamped
  on every decision. No threshold ships without one global P/R curve plus blocking-stratum and
  deciding-tier diagnostics (D17, D22, D96). The old JW≥0.92 / cosine≥0.88 are placeholders to
  overwrite.
- Blocking (T1/T2) sets a hard recall ceiling, so cheap tiers **escalate near-misses to T4**,
  never auto-reject — textual recall is mediocre and over-rejection is a silent hole.
- Coreference (D19) is resolved *inside the E2 extraction call* (all languages) so mentions
  arrive with referents already grounded — no dedicated coref model. Likewise, each mention's
  canonical/nominative name form is LLM-emitted at extraction (§5), feeding T0.

## 4. Ontology — universal core + anchored extensions (D15, D18)

### Normative universal-core bootstrap manifest (D18, D64, D69)

This block is the one authoritative, machine-transcribable core manifest. It is data for every
deployment, not migration data and not an extension-pack definition. After structural Alembic head
exists, bootstrap_deployment supplies the same DeploymentBootstrapInput.deployment_id to every row.
The database supplies created_at. No omitted field is an implementation choice.

All eight entity types are roots: parent_type is null for every row. Document is aligned to the
external schema.org CreativeWork class through schema_org_ref; CreativeWork is not a ninth
entity_types row. By contrast, the parent shown for an extension-pack type such as Task under Event
is a real parent_type FK to an existing registry row.

The display order preserves the D18/D64 vocabulary. The executable insert order is exact:
entity_types in display order; related_to first among predicates so the parent FK exists; the other
fifteen predicates in display order; then the predicate_signatures
rows in expansion order. The manifest contains 8 entity_types rows, 16 predicates rows, and 116
derived predicate_signatures rows.

The schema.org anchors below were spot-checked against the canonical schema.org types/properties.
A null predicate anchor means schema.org has no faithful same-direction mapping for the complete
UGM signature; an inverse or narrower property is not recorded as if it were equivalent.
For every predicate, usage_count is the exact insert value zero. It is the sole mutable manifest
column: bootstrap retry accepts any existing non-negative count and does not reset it; every other
listed definition field is compared exactly.

~~~yaml
manifest_version: core-v1
deployment_id_source: DeploymentBootstrapInput.deployment_id
created_at_source: database_default

entity_types:
  - type: Person
    parent_type: null
    description: "A human individual, living, deceased, or fictional."
    examples: ["Ada Lovelace", "Grace Hopper"]
    schema_org_ref: "https://schema.org/Person"
    tier: core
    pack_id: null
    scope_id: null
    status: active
  - type: Organization
    parent_type: null
    description: "A structured group or legal or social entity that acts collectively."
    examples: ["Acme Corporation", "Open Source Initiative"]
    schema_org_ref: "https://schema.org/Organization"
    tier: core
    pack_id: null
    scope_id: null
    status: active
  - type: Place
    parent_type: null
    description: "A physical, geographic, or named location."
    examples: ["Prague", "Building 5"]
    schema_org_ref: "https://schema.org/Place"
    tier: core
    pack_id: null
    scope_id: null
    status: active
  - type: Document
    parent_type: null
    description: "An informational creative work that may be ingested, cited, authored, or discussed."
    examples: ["Quarterly report", "Research paper"]
    schema_org_ref: "https://schema.org/CreativeWork"
    tier: core
    pack_id: null
    scope_id: null
    status: active
  - type: Event
    parent_type: null
    description: "An occurrence bounded by time, place, or participants."
    examples: ["Product launch", "Annual conference"]
    schema_org_ref: "https://schema.org/Event"
    tier: core
    pack_id: null
    scope_id: null
    status: active
  - type: Concept
    parent_type: null
    description: "An abstract idea, topic, category, method, or field of knowledge."
    examples: ["Machine learning", "Supply-chain resilience"]
    schema_org_ref: "https://schema.org/DefinedTerm"
    tier: core
    pack_id: null
    scope_id: null
    status: active
  - type: Project
    parent_type: null
    description: "A coordinated effort with an intended outcome."
    examples: ["ERP migration", "Project Atlas"]
    schema_org_ref: "https://schema.org/Project"
    tier: core
    pack_id: null
    scope_id: null
    status: active
  - type: Product
    parent_type: null
    description: "A good, system, service offering, or tool that people or organizations create or use."
    examples: ["Beacon CRM", "Industrial sensor"]
    schema_org_ref: "https://schema.org/Product"
    tier: core
    pack_id: null
    scope_id: null
    status: active

predicates:
  - predicate: works_for
    parent_predicate: related_to
    description: "Employment or ongoing work relationship from a person to an organization."
    examples: ["Ada works_for Acme"]
    synonyms: ["works_at", "employed_by", "employee_of"]
    schema_org_ref: "https://schema.org/worksFor"
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: true
    exclude_from_graph_distance: false
    status: active
  - predicate: member_of
    parent_predicate: related_to
    description: "Formal or informal membership of a person in an organization."
    examples: ["Ada member_of Standards Council"]
    synonyms: ["belongs_to", "is_member_of"]
    schema_org_ref: "https://schema.org/memberOf"
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: true
    exclude_from_graph_distance: false
    status: active
  - predicate: affiliated_with
    parent_predicate: related_to
    description: "A looser advisory, partner, alumni, or institutional affiliation with an organization."
    examples: ["Ada affiliated_with University Lab", "Acme affiliated_with Trade Alliance"]
    synonyms: ["associated_with", "connected_with"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: true
    exclude_from_graph_distance: false
    status: active
  - predicate: founded
    parent_predicate: related_to
    description: "Creation or establishment of an organization by a person or organization."
    examples: ["Ada founded Beacon Labs", "Acme founded Acme Research"]
    synonyms: ["established", "started"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: false
    exclude_from_graph_distance: false
    status: active
  - predicate: located_in
    parent_predicate: related_to
    description: "Physical or operational location of an organization, place, or event within a place."
    examples: ["Acme located_in Prague", "Keynote located_in Hall A"]
    synonyms: ["based_in", "situated_in"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: true
    exclude_from_graph_distance: false
    status: active
  - predicate: part_of
    parent_predicate: related_to
    description: "Same-kind containment or component relationship."
    examples: ["Division A part_of Acme", "Prague part_of Czechia"]
    synonyms: ["component_of", "contained_in"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: true
    exclude_from_graph_distance: false
    status: active
  - predicate: authored
    parent_predicate: related_to
    description: "Authorship of a document by a person or organization."
    examples: ["Ada authored Quarterly report"]
    synonyms: ["wrote", "written_by"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: false
    exclude_from_graph_distance: false
    status: active
  - predicate: created
    parent_predicate: related_to
    description: "Creation of a product or concept by a person or organization, excluding document authorship."
    examples: ["Ada created Beacon CRM", "Acme created Resilience method"]
    synonyms: ["made", "developed"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: false
    exclude_from_graph_distance: false
    status: active
  - predicate: about
    parent_predicate: related_to
    description: "The entity or topic that a document or event concerns."
    examples: ["Quarterly report about Acme", "Workshop about Machine learning"]
    synonyms: ["concerns", "regarding"]
    schema_org_ref: "https://schema.org/about"
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: false
    exclude_from_graph_distance: false
    status: active
  - predicate: knows_about
    parent_predicate: related_to
    description: "A person's familiarity or expertise concerning a concept."
    examples: ["Ada knows_about Compiler design"]
    synonyms: ["expert_in", "familiar_with"]
    schema_org_ref: "https://schema.org/knowsAbout"
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: false
    exclude_from_graph_distance: false
    status: active
  - predicate: knows
    parent_predicate: related_to
    description: "A social or professional acquaintance between two people."
    examples: ["Ada knows Grace"]
    synonyms: ["acquainted_with"]
    schema_org_ref: "https://schema.org/knows"
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: false
    exclude_from_graph_distance: false
    status: active
  - predicate: participated_in
    parent_predicate: related_to
    description: "Participation by a person or organization in an event or project."
    examples: ["Ada participated_in Annual conference", "Acme participated_in Project Atlas"]
    synonyms: ["took_part_in", "joined"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: false
    exclude_from_graph_distance: false
    status: active
  - predicate: works_on
    parent_predicate: related_to
    description: "Active work or contribution by a person or organization on a project or product."
    examples: ["Ada works_on Project Atlas", "Acme works_on Beacon CRM"]
    synonyms: ["contributes_to", "develops"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: true
    exclude_from_graph_distance: false
    status: active
  - predicate: uses
    parent_predicate: related_to
    description: "Adoption or use of a product, system, or tool by a person or organization."
    examples: ["Ada uses Beacon CRM", "Acme uses Industrial sensor"]
    synonyms: ["utilizes", "operates_with"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: true
    exclude_from_graph_distance: false
    status: active
  - predicate: reports_to
    parent_predicate: related_to
    description: "An organizational reporting line from one person to another person."
    examples: ["Ada reports_to Grace"]
    synonyms: ["managed_by", "answers_to"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: true
    exclude_from_graph_distance: false
    status: active
  - predicate: related_to
    parent_predicate: null
    description: "A permissive relationship used only when no more specific governed predicate fits."
    examples: ["Project Atlas related_to Beacon CRM"]
    synonyms: ["connected_to"]
    schema_org_ref: null
    tier: core
    pack_id: null
    scope_id: null
    usage_count: 0
    is_change_prone: false
    exclude_from_graph_distance: true
    status: active

predicate_signatures:
  # Derived, never hand-listed (D69, refined 2026-07-18): expand the compact
  # domain/range unions below with the deterministic rule; the expansion yields
  # exactly 116 concrete {predicate, subject_type, object_type} rows.
  expansion_rule:
    product: pair every subject type with every object type, subject-major order
    same_kind: pair each core type with itself (part_of only)
    any: all eight core entity types, in display order
  expanded_row_count: 116
  domain_ranges:
    - {predicate: works_for, subject_types: [Person], object_types: [Organization]}
    - {predicate: member_of, subject_types: [Person], object_types: [Organization]}
    - {predicate: affiliated_with, subject_types: [Person, Organization], object_types: [Organization]}
    - {predicate: founded, subject_types: [Person, Organization], object_types: [Organization]}
    - {predicate: located_in, subject_types: [Organization, Place, Event], object_types: [Place]}
    - {predicate: part_of, same_kind: true}
    - {predicate: authored, subject_types: [Person, Organization], object_types: [Document]}
    - {predicate: created, subject_types: [Person, Organization], object_types: [Product, Concept]}
    - {predicate: about, subject_types: [Document, Event], object_types: any}
    - {predicate: knows_about, subject_types: [Person], object_types: [Concept]}
    - {predicate: knows, subject_types: [Person], object_types: [Person]}
    - {predicate: participated_in, subject_types: [Person, Organization], object_types: [Event, Project]}
    - {predicate: works_on, subject_types: [Person, Organization], object_types: [Project, Product]}
    - {predicate: uses, subject_types: [Person, Organization], object_types: [Product]}
    - {predicate: reports_to, subject_types: [Person], object_types: [Person]}
    - {predicate: related_to, subject_types: any, object_types: any}
~~~

The signature manifest is compact by design: the domain/range unions are the normative source,
and the deterministic expansion (product / same-kind / any) yields exactly 116 concrete rows —
derived and count-asserted at bootstrap and in the packaged manifest, never hand-listed. The
normalizer's parent-chain walk means an enabled extension subtype inherits
the signature of its core ancestor; no wildcard row or ninth catch-all type exists in Postgres.

Universal core, extension definition, and activation are distinct operations. The manifest above
always creates deployment-scoped tier=core rows with pack_id and scope_id null. A system-shipped
extension pack is defined separately in extension_packs and names tier=extension rows with a real
core parent and non-null pack_id. Enabling it for one deployment writes
deployment_extension_packs and then its declared extension registry rows. Pack definition or
activation never changes, replaces, or counts as part of the 8/16/116 universal core.
- **Extend, never fork:** every user type/predicate declares a core parent → blocking, graph
  queries, and cross-scope retrieval always fall back to the core level.
- **Domain/range enforced** exactly as Graphiti's `edge_type_map[(src,tgt)→[rel]]` — the only
  structural ontology gate any surveyed production system ships. Rejects a class of extraction
  hallucinations mechanically. **Not OWL** (no reasoners/cardinality/property-chains).
- **Prompts render from the registry** (types + predicates + descriptions + examples) — defining
  a scope is editing rows, not prompt engineering; captured by prompt-version (D12).
- **Three speeds, one registry:** core (slow, each element a commitment) → scope extensions
  (fast, each an experiment) → `other:<freetext>` escape (ungoverned, monitored — the promotion
  funnel). Frequent `other:` values are the system reporting an ontology gap.
- **Scopes share one graph and one entity space (D16):** a scope's vocabulary is its footprint
  in the shared graph; scope views are `PROJECT_GRAPH_CYPHER` projections declared in
  `scope_interests`, never separate databases.

### How an entity gets its type

Domain/range enforcement needs entity types, so they must be assigned — but this is *not* a
separate subsystem. Type comes free with extraction:

- **The E2 extractor emits the type** alongside the mention, constrained to the registry's type
  enum (8 core + enabled subtypes) — the same registry-rendered-into-the-prompt mechanism as
  predicates; a free-form type label is not allowed (it would fragment like ungoverned
  predicates).
- **A canonical entity's type = the majority / highest-confidence vote** across its mentions'
  types, stored on `entities.type`. Mentions of one entity almost always agree; no
  voting/metonymy machinery is needed — mentions of one entity almost always agree on type.
- **Domain/range (D18) validates relations against entity types.** A relation that fails is
  dropped — and is **re-derivable from its immutable claim** if the entity is later retyped, so
  no quarantine table is needed (claims are the durable record, D2).
- **Cross-mention type disagreement on the core type is a cheap over-merge signal** (Washington
  person/place ⇒ two referents likely merged) — logged for D24 review; a SELECT, not machinery.
- Low-confidence mentions abstain to the `other:` floor, **never** dumped into `Concept`
  (`Concept` is a positive type, not the unknown bin).

A fuller "typing subsystem" (a cascade with GLiNER/authorities, an append-only type-decision
ledger, a relation-quarantine table, elaborate vote reconciliation) was researched and
**deliberately scoped down** — the extraction LLM is already being called, so a cheap-first
typing cascade avoids a cost we already pay; the heavier options are recorded in
`../analysis/entity_typing_research/SYNTHESIS.md` to revisit only if the golden set (D22)
surfaces a specific failure.

### System-shipped extension packs — the "Work" pack

Extension packs are predefined, system-shipped sets of extension types + predicates a
deployment can enable as a unit. **Extensions are not second-class**: an extension type lives
in the same entity space, graph, ER machinery, and relations as core types — the tier is a
*governance* distinction (stability commitment, golden-set obligation), not a capability one.
Packs let work-shaped concepts be first-class entities from day one without burning core
slots or committing every deployment (e.g. the law engine) to them.

**Work pack** (for assistant / agency / project-management deployments):

| Type | Parent | Notes |
|---|---|---|
| `Task` | ⊂ Event | an intended occurrence with a lifecycle |
| `Decision` | ⊂ Event | a commitment made at a point in time |
| `Goal` | ⊂ Concept | a desired state — held, not occurring |

| Predicate | Domain → Range |
|---|---|
| `blocks` | Task → Task |
| `depends_on` | Task → Task |
| `concerns` | Task \| Decision → any |
| `decided_by` | Decision → Person \| Organization |
| `assigned_to` | Task → Person \| Organization |
| `pursues` | Project \| Organization → Goal |

The payoff for `Decision` is the bi-temporal machinery: a decision is *a fact that holds
until reversed* — its standing rides on relations with validity windows, so reversals are
ordinary supersession, "what was the standing decision on X as of March?" is an ordinary
as-of query, and stale decisions get zombie-fact protection like any other relation. K2
scopes still compile narrative decision-logs/task-boards — referencing these entity IDs (the
usual entities-feed-compilation pattern); the pack provides identity + graph linkage +
temporal validity, K2 provides synthesis. Neither replaces the other.

Anticipated future packs (defined when a deployment needs them, not before): legal
(`Statute/Ruling/Contract ⊂ Document`, `Jurisdiction ⊂ Place`), systems/migration
(`System/Module ⊂ Product`, `Requirement ⊂ Document`, `BusinessProcess ⊂ Concept`).

### Predicate watchlist & promotion rule

These predicates were considered for the core/Work pack and **deliberately held back** —
plausible but not yet proven. The default lives in claims (E2); promotion to a typed predicate
happens **on demonstrated demand, not intuition**: when extraction produces a matching
`other:<freetext>` value at volume (the D5 promotion funnel), the periodic review promotes it
into the appropriate pack with a tight signature. Adding one is a registry row; the cost of a
premature core predicate (prompt space, golden-set coverage, a split if it's wrong) is the
reason to wait.

**Two graduations (D64):** `uses` and `reports_to` were promoted into the core (rows 14–15
above) by owner decision — the named deployments made the demand case without waiting for
`other:` volume (an as-is system landscape runs on `uses`; people-centric retrieval runs on
`reports_to`). The rows below remain held back.

| Candidate | Tight signature | Future home |
|---|---|---|
| `owns` / `acquired_by` | Organization → Organization \| Product | business pack |
| `lives_in` | Person → Place | personal pack |
| `enables` | Concept → Concept (tight only) | research scope — guardrailed (see below) |

**Excluded on principle — `causes` and `enables` as general relations.** Causal predicates are
**not** admitted to the core, and `enables` only ever as a tightly-typed `Concept → Concept`
scope experiment. Three reasons: (1) **evidence aggregation fails** — causal claims rarely
repeat verbatim, so they produce thousands of `evidence_count=1` edges instead of a few
well-evidenced ones, and the mechanism that makes relations trustworthy never engages; (2)
**no supersession semantics** — a causal assertion isn't *ended* by an event (the bi-temporal
model), it is *contested by argument* and hedged/conditional, which is exactly what the claims
layer (E2) preserves and the relations layer discards; (3) **no domain/range bite** —
`causes: any → any` waves everything through the one structural gate we trust (D18
`edge_type_map`), and LLMs over-read causation ("leads to / drives / thanks to"), producing
hub nodes that poison graph-distance reranking (D9). The causal *content* is not lost — it
lives in claims, fully searchable with its hedges intact (P1); only graph *traversal over
causality* is forgone, which over LLM-extracted causal edges would be confidently wrong.
Admission ticket (if ever): a scope extension with tight domain/range, extraction gated to
causal-classified claims, and **exclusion from graph-distance reranking**.

## 5. Multilingual / inflected names — LLM canonicalization + deterministic matching tiers

Czech (and Slavic generally) declines names across ~7 cases → ~7 surface forms per name
("Jiří Puc" / "Jiřího Puce" / "Jiřímu Pucovi"), a direct attack on `(entity_id, predicate)`
blocking. Following the "per-mention understanding is free with extraction; at-scale matching
needs cheap tiers" principle (§4, D19), this is handled **without specialized ML models**:

- **Canonicalization is LLM-emitted at extraction** (no UDPipe/MorphoDiTa lemmatizer pass). The
  E2 extractor emits each mention's **nominative/canonical name form** alongside the surface
  form — the same free per-mention output as type and coref. The canonical form is stored as a
  first-class alias (`provenance=llm_canonical`); T0 exact match runs on it.
- **Residual variants are caught by the deterministic at-scale tiers** (these are cheap Postgres
  built-ins, not ML, and do what the per-mention LLM cannot — match against millions of existing
  aliases): T1 `unaccent` + `pg_trgm` (GIN), T2 `fuzzystrmatch.daitch_mokotoff` (UTF-8-safe,
  GIN-indexable — catches "Nowak/Novak"-class spelling/transliteration variants). Optional
  app-layer BMPM behind a flag only if D-M recall proves short. Transliteration handling only if
  a corpus is confirmed multi-script.
- **Coref is in the E2 call** for all languages (D19) — no dedicated multilingual model.

**Acceptance test:** measured reduction in missed-supersession rate on inflected-name pairs vs a
surface-form baseline. *(Open spike: whether LLM-emitted canonical forms are consistent enough on
inflected proper nouns, and D-M phonetic recall on declined names — measure on a Czech corpus
slice before trusting the multilingual path; no specialized model to fall back on, so the
deterministic tiers must carry the residual.)*

## 6. Clustering & reversibility (D21)

The resolution cascade (§3) only ever produces *pairwise* guesses — "mention A and mention B
are probably the same entity." This section turns those pairwise guesses into actual entity
*groupings* — cheaply, at scale, without catastrophic over-merging, and reversibly. Every rule
below exists because of one asymmetry: **over-merging (fusing two real entities) is catastrophic
and silent; under-merging (missing a match) is gradual and recoverable** — so the machinery is
deliberately paranoid in one direction.

**Never chain the guesses (no "transitive closure").** If the cascade says A≈B and B≈C, we do
**not** conclude A=C. Example: "Jim Smith"≈"J. Smith" and "J. Smith"≈"James Smith" — but Jim and
James may be two different people both abbreviated to "J. Smith". Each link is individually
reasonable; chaining them merges two real people, and a single weak link can fuse two large
groups. So chaining (the technical term is *transitive closure*) is forbidden — everything below
is how we avoid it.

**Gather loosely, then decide tightly (two stages).** (1) *Gather*: follow the pairwise links to
collect a rough candidate *blob* of mentions that are connected somehow (graph
*connected-components* — just everyone reachable via a match link). (2) *Decide*: inside the
blob, a stricter check looks at how similar the mentions actually are and **splits the blob into
the real entities** — so a Smith blob becomes "Jim Smith" (one entity) and "James Smith"
(another). That tighter check builds a similarity tree of the blob's members and cuts it at a
threshold, so each piece below the cut is one entity (*hierarchical agglomerative clustering
with a distance cut* — in practice dedupe's `linkage(centroid)` + `fcluster(distance)`). The
blob is only ever a *candidate pool*, never automatically one entity. (Community-detection
algorithms like Louvain/Leiden are for topic communities — D11 — and must never be used to
decide identity.)

**Cap runaway blobs (the "black-hole guard").** Occasionally a blob balloons to thousands of
mentions because one bad link or a generic name connected everything — almost always garbage,
and expensive to process. When a blob crosses a size limit, **raise the matching bar and
re-split it** rather than swallow the monster. ("Black hole" = an entity that sucks in
everything; the guard stops it.)

**Place new mentions locally, and independent of arrival order (incremental).** When a new
document adds a mention, we do **not** re-cluster the whole (million-entity) registry. We
re-examine only the small set of existing entities the new mention could plausibly be — its
blocking candidates, i.e. its *neighborhood* — and re-decide just that local pocket. Crucially,
we re-cluster the pocket **jointly**, rather than greedily gluing the new mention to its single
best match — because greedy attachment makes the result depend on **ingestion order**:

> if "R. Klein" arrives *before* "Robert Klein" has been seen, the only Klein in the system might
> be "Rachel Klein", so it wrongly attaches there; had "Robert Klein" arrived first, "R. Klein"
> would have joined Robert. Same documents, different *people*, purely from order.

Re-clustering the local neighborhood as a unit fixes this: when "Robert Klein" later arrives, his
neighborhood already contains "R. Klein", so the pocket is re-decided together and "R. Klein"
moves to Robert. This gives the **bounded cost of a local operation** *and* the **correctness of
a full re-cluster** — the same grouping no matter what order documents arrived in. (Technically:
re-resolve the *1-hop neighborhood* — the entities one match-link away from the new mention —
and look one link further only when the mention touches a *hub*, an entity already connected to
many others.)

**Keep every merge reversible.** Because over-merges are catastrophic *and* inevitable at scale,
every merge must be undoable. Three mechanisms, all in Postgres (the single authority, D6):
- resolution decisions are **append-only** — a better decision *supersedes* the old one
  (`superseded_by`); nothing is overwritten, so the full history survives;
- each merge records a **"before" snapshot** of which mentions belonged to which entity
  (`merge_events.pre_merge_membership_snapshot`) — to un-merge, replay the snapshot;
- a merge is a **redirect, not a deletion** (`merged_into`): the absorbed entity keeps its ID,
  pointing at the survivor (Wikidata-style), so undoing is just removing the redirect.

No OSS ER system (Splink, dedupe, Zingg, Graphiti) ships un-merge, so building it is genuinely
ours to do — and the P2 rebuild (D7) re-points the graph on every merge/un-merge for free.

**Distrust promiscuous signals (the "generic-identifier guard").** Some signals look identifying
but aren't — `info@company.com`, a placeholder, a very common name. If one alias suddenly links
to *many* distinct entities, that's a tell it is **generic, not identifying**: down-weight it
(stop trusting it as a match signal) and re-evaluate the merges it caused. (Senzing pioneered
this.)

**Blast-radius rule.** A merge's *blast radius* is how much it would affect if wrong — roughly the
combined size/connectedness of the two entities (their mention counts + graph degree). Never
auto-merge above a degree/evidence threshold — wrongly merging two
*hubs* is the worst case of all. High-impact merges are routed to human review (§8), ranked by
`expected_impact = blast_radius × (1 − confidence)`.

## 7. Governance — predicate promotion

A periodic job reviews frequent `other:<freetext>` predicates and either maps them to an
existing predicate or promotes them to a scope extension / the core. Promotion = inserting/
retyping rows; retyping is retroactively clean in P2 after rebuild (D7). **The one expensive
operation is *splitting* a heavily-used predicate** (D15 flags it; D7 retro-clean does not cover
splits cleanly) — hence start strict with a small core. *(Open: the promotion workflow owner +
the split cost are under-researched — registry SYNTHESIS G5.)*

## 8. Review tooling (D24)

**Build** a thin CLI cluster-review queue over Postgres (no OSS tool offers cluster-queue +
append-only reversible verdicts + provenance + blast-radius gating). Review **clusters, not
pairs**; route only the `expected_impact = blast_radius × (1 − confidence)` middle band to
humans; hub merges never auto-accept. Evidence panel borrows Splink's waterfall; 3-way verdict
ergonomics from Zingg; cluster-card-with-exclude interaction from OpenRefine. Every action
appends a reversible, provenance-stamped record to `resolution_decisions`/`merge_events`.

## 9. Scale & schema (D23)

- The D94-amended partition estate has exactly eight parents (schema §12). Six append-only tables use
  monthly RANGE children managed by `pg_partman`: `mentions(created_at)`,
  `resolution_decisions(decided_at)`, `chunks(created_at)`, `chunk_claims(created_at)`,
  `claim_extraction_decisions(decided_at)`, and
  `testimony_currency_events(occurred_at)`. Claims are non-partitioned so the
  current-testimony BM25/HNSW corpus has global ranking statistics without a
  mirror table. Two evidence joins use 64 static,
  migration-created HASH children: `relation_evidence` by `relation_id`, with PRIMARY KEY
  (`relation_id`, `claim_id`), and `observation_evidence` by `observation_id`, with PRIMARY KEY
  (`observation_id`, `claim_id`). The HASH count of 64 is a measured starting point. These hot
  tables are btree-only to cap write amplification and are never fuzzy-scanned.
- Do **not** partition `entities`/`aliases` (≤10⁷, the blocking targets). GIN `gin_trgm_ops` +
  GIN `daitch_mokotoff` blocking stays single-column because each deployment has its own
  Postgres instance/schema (D68): `ix_entities_name_trgm` on
  `entities USING gin (normalized_name gin_trgm_ops)`, `ix_aliases_lemma_trgm` on
  `aliases USING gin (normalized_lemma gin_trgm_ops)`, and `ix_aliases_lemma_dm` on
  `aliases USING gin (daitch_mokotoff(normalized_lemma))`. The alias key is
  `normalized_lemma`. Keep the btree composite `(subject_entity_id, predicate[, object])` on
  `relations`; `btree_gin` is not required.
- T0–T2 in PostgreSQL authority tables; the current T3 profile embedding lives
  on the canonical entity/profile row and is indexed privately (D94). The
  derived vector columns remain outside the public authority surface.
- **Row counts are sized against full extraction** (there is no value gate — D25); size the
  load-test against *ungated* volume.

## 10. Quality & evaluation (D22, O6)

- **Golden EVAL set** (unbiased, human-adjudicated): ~200 pairs/type (~100 hard positives incl.
  synthetic father/son/inflection/married-name + ~100 hard negatives; ~400/type for
  auto-merge-critical types); blocking-stratified positive over-sampling; **Wilson** CIs near
  p≈1. Held strictly separate from any future training set.
- **Per-tier metrics**, canary regression re-run per `resolver_version`.
- **Continuous health metrics:** cluster-size distribution (emerging giant cluster = over-merge),
  singleton rate per type (under-merge), unresolved-mention rate, merge-proposal acceptance rate,
  alias-per-entity growth.
- **Reversibility is an invariant:** any automated decision must be undoable by replaying lineage;
  anything that can't be undone goes to the review queue.

## 11. Open spikes (do before committing numbers)

1. **Golden-set labeling without circularity** — LLM-propose / human-verify loop; the denominator
   trap (recall needs ~370 true-positive pairs).
2. **LLM canonical-form consistency on inflected proper nouns + D-M phonetic recall** on declined names (no end-to-end
   Czech ER benchmark exists).
3. **Un-merge → bi-temporal supersession ripple** — confirm relation validity windows closed
   under a merged identity are correctly re-adjudicated on un-merge (this is where silent
   supersession failure lives; coordinate with E2 Selection's change-of-state never-drop safeguard,
   D25/D35).
4. **Scale load-test** real mentions-per-doc, GIN index sizes, monthly RANGE cadence, static HASH
   child count (64 is the starting point), and streaming throughput on a corpus slice (sized
   against full extraction — there is no value gate, D25).
5. **Predicate-promotion workflow + split cost** (G5).

## References
Decisions: D4, D5, D6, D7, D8, D9, D11, D15, D16, **D17–D24** (`decisions.md`). Analysis:
`plan/analysis/entity_registry.md`, `plan/analysis/registry_research/` (R1–R10, verify/,
SYNTHESIS.md). Concepts: `plan/analysis/concepts.md`.
