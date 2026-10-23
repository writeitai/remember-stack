# Processing layers that can be switched off (analysis for D143)

**Status:** non-binding analysis, 2026-10-23. The accepted contract is
[the processing layers design](../designs/processing_layers_design.md) and
[D143](../../decisions.md#d143-processing-layers-can-be-switched-off-per-deployment-and-per-request).

## 1. The problem

Today every document version that enters a deployment runs the whole pipeline: conversion,
structure, chunking and chunk embeddings, then claim extraction (Selection and Claimify),
then the fact machinery (normalisation, entity resolution, fact adjudication, follow-ups,
reconciliation and fact labels). Retrieval likewise always consults every kind of result it
knows about. There is no way to say "this corpus does not need facts" or "answer this
request from source passages only".

The owner asked (2026-10-23): *"It should be possible to turn the layers on and off — both
in ingestion and retrieval. However, they must be on by default."*

The motivating corpus is a Czech-legislation product (Vigent) that will ingest about
118,000 document versions of statutes. For law, the binding text plus declared effective
periods (D140), section keys, caller-supplied references and embeddings answer the
questions. The fact layer adds cost and adds a model-derived reconciliation on top of a
version history that is already deterministic. The owner decided that Vigent's first full
ingestion runs **without the fact layer**, and that claim extraction is to be **evaluated
separately as a retrieval aid**. The engine therefore needs processing layers that can be
switched off, a way to switch them back on later without breaking incremental ingestion
(Rule 4 in `CLAUDE.md`), and retrieval that says plainly when a layer is not there.

### 1.1 What the layers cost

Measured on 2026-09-14 from the engine's own cost ledger of two retained LoCoMo stores
(conversation 42, 29 sessions, identical sources; the v27 and v28 extractor generations).
Source: the managed cloud repository's ingestion cost audit
(`design/analysis/locomo-ingestion-cost-quality-20260914.md`, not public), whose numbers
are read-only SQL aggregates over `cost_ledger`.

| Stage group | Layer (this analysis) | v27 USD | v28 USD |
| --- | --- | ---: | ---: |
| Structure, chunk embeddings, everything else | sources | 0.18 (4%) | 0.18 (1.5%) |
| Claim extraction (Selection + Claimify) | claims | 2.19 (46%) | 2.25 (18%) |
| Normalisation and entity resolution | facts | 0.69 (15%) | 0.81 (7%) |
| Observation and fact adjudication | facts | 1.66 (35%) | 9.16 (74%) |
| **Total** | | **4.72** | **12.40** |

A high estimate for the Vigent corpus prepared for the owner put the whole ingestion at
$90k–250k, with adjudication alone about 75% of it. Both sources agree on the shape: the
source layer is a few percent, claims are a fifth to a half, and facts are the rest. A
conversational benchmark is not a statute book, so the shares are indicative, not a
forecast.

## 2. The pipeline as built (2026-10-23, `origin/main` 981082273)

The pipeline is a chain of idempotent stages (orchestration design §1): a completing stage
enqueues the next one in the same transaction as its success mark. These are the stages the
self-host profile composes (`_SUPPORTED_WORKER_STAGES`, `profiles/selfhost.py`) and how they
connect:

| Stage | Grain | Opened by |
| --- | --- | --- |
| `convert`, `structure` (with D79 section summaries, skeleton, roles) | version | ingest; `convert` |
| `crossref` | reference generation | supplied references (D140 §6.3) |
| `chunk` → `embed_chunk` | version | `structure`; `chunk` |
| `extract_claims` (Selection) | chunk | `embed_chunk` completion (`workers/e1.py` `_extract_follow_up`) |
| `ground_claims` (Claimify) | chunk | Selection barrier (`WorkLedger.complete_chunk_selection`) |
| `normalize_relations` (E3 normalisation, mentions, entity resolution) | claim | extract barrier (`complete_chunk_extract` → `_enqueue_claim_normalize_fanout`) |
| `adjudicate_observations` (fact adjudication, "obs flush") | entity unit | normalize barrier (`complete_claim_normalize`) |
| `adjudicate_supersession` (fact follow-up, profile refresh) and `embed_claim` | version | obs-flush barrier (`complete_entity_obs_flush`) |
| `reconcile` | version | `adjudicate_supersession` (`workers/e3.py`) |
| `label_relation`, `review_fact_windows` | version / lineage | `reconcile` (`workers/reconcile.py`) |

A version with no chunks or no claims takes the D90 empty path: it writes a durable
`empty_complete` marker and opens `adjudicate_supersession` and `embed_claim` directly.

Two couplings matter for switching layers off:

1. **`embed_claim` waits for the facts.** It is opened only by the obs-flush barrier (or
   the D90 empty path). With no fact work, nothing would ever embed claims.
2. **`reconcile` does two jobs.** Its first step is *testimony currency* (evidence
   lifecycle §3): when a living document drops content, the claims of the dropped content
   stop being current testimony, and claim retrieval stops returning them. That is claim
   bookkeeping. Its later steps recount fact support, close facts whose support is gone,
   raise review flags, emit `evidence_changed`, refresh entity profiles and queue fact
   labels and window reviews. That is fact work. Claim retrieval needs the first step even
   when no facts exist.

**Readiness.** `PipelineReadinessCatalog` (`spine/readiness.py`) reports a version ready
when every stage in the profile's static `_expected_components()` map succeeded. It waits
for every stage above except `crossref` and `review_fact_windows`. The P1 and live-graph
capabilities are configuration checks that do not depend on data. The LoCoMo protocol pins
the exact stage list and component versions (`benchmarks/locomo/protocol.py`).

**Reuse across versions (Rule 4).** Selection reuse looks only at *earlier* versions of the
same lineage: `SelectionCatalog.prior` selects a frozen result whose version has a smaller
`version_no`. Claimify reuse (D122) and claim attachment (D56) follow from Selection. So if
two versions of one lineage are extracted at the same time, or the newer one first, the
unchanged chunks of the newer version are paid for again. Normal ingestion avoids this
because callers ingest a lineage oldest first, one version at a time.

**Facts that matter for a later backfill** (found in review round 1):

- *Living supersession reads the current version's occurrences.* Reconcile retires a
  living lineage's claims that are not attached (`chunk_claims`) to the lineage's current
  version (`lifecycle.py` `_SELECT_STALE_SUPERSEDED`). Reuse attaches claim ids to a newer
  version but does not restore currency. If an older version is reconciled while the
  current version's claims are not yet attached, unchanged claims are retired for good.
- *E3 does not filter on currency.* The normalise fan-out selects every claim occurrence of
  the version, and adjudication can create a new fact from a non-current claim. Processing
  an old living version later would turn retired testimony into facts.
- *Connector cycles close facts once.* For cycle-stamped versions, closure of zero-support
  facts is deferred to `CycleFinalizer`, which runs once per cycle and reads the stored
  counts. A recount that happens after the cycle finalized never closes anything.
- *The result schemas are hashed.* The assured operations' result schemas are generated from
  `Envelope` and `ContextBundleV2` and are members of the query-surface manifest hash that
  LoCoMo pins, so even an optional, omitted-when-empty response field changes the default
  descriptors and the hash.

**Scope units.** The deployment is the only partition (D16, D68): one database or schema,
one entity space, one trust domain. Nothing below it partitions documents for processing;
`source_kind` is a lineage-identity key, plane-K scopes are compile lenses, and the managed
cloud maps one project to one deployment. A product corpus such as Vigent is its own
deployment.

**Configuration.** Deployment configuration is Pydantic settings read per process
(`REMEMBERSTACK_SELFHOST_*`), and the `setup` command reconciles it with the database. The
`deployments` row is written once at bootstrap. No setting skips a stage today. The
`skipped` processing status exists in the enum but nothing writes it. The backfill lane
exists (orchestration §3) but no self-host process claims backfill work, and the
`BackfillSeeder` only re-runs a stage that already ran at another component version.

**Retrieval.** Results come from three families of data:

- *source passages* — the P1 chunk channels and document, section and reference reads;
- *claims* — the P1 claim channels and claim views;
- *facts* — entities, relations, observations, the live graph, profile vectors and the
  plane-K pages compiled from them.

`claims_and_sources_context` fuses the claim and chunk channels.
`facts_context` reads facts and attaches their claim evidence. `combined_context` returns
both side by side (`ContextBundleV2`). The MCP catalogue, the Python SDK, the TypeScript
client (D141 checked parity) and the query sandbox expose them. No request parameter selects
result families today. The LoCoMo protocol pins the query-surface manifest hash and the
answer-tool catalogue hash.

## 3. Which layers, and how they depend on each other

**Candidate split.** The natural cut follows the chain and the cost table:

- **sources** — conversion, structure (including section summaries), cross-references,
  chunks and chunk embeddings. It is what makes a document readable and searchable at all,
  so it cannot be switched off.
- **claims** — Selection, Claimify, claim embeddings, and testimony currency.
- **facts** — normalisation and entity resolution, fact adjudication, fact follow-ups,
  fact reconciliation, fact labels and window reviews, entity profiles, the entity graph
  and the plane-K pages compiled from facts.

The dependency is a straight line, sources ← claims ← facts: facts are built from claims,
and claims from chunks. So the only legal sets are {sources}, {sources, claims} and
{sources, claims, facts}.

**Should section summaries be their own layer?** No. They are produced inside the `structure`
stage, cost a small share of the sources layer, and feed three readers: the E1/E2 section
orientation, document search's overview, and P3's index files. Making them optional would
let the same `E2_EXTRACTOR_VERSION` (which includes `SECTION_ORIENTATION_VERSION`) extract
claims with or without orientation. That is two different extractors under one version
string, which breaks the meaning of the reuse key. Summaries stay in sources.

**Should entities be separable from facts?** No. Mentions and entity resolution happen inside
the normalisation stage that also produces fact candidates, and every entity read
(resolve, profiles, graph) is only useful with facts. A separate entity layer would split one
stage into two for no product need.

## 4. Where the setting lives

| Option | For | Against |
| --- | --- | --- |
| **A. Per deployment** | The deployment is the only partition (D16/D68) and already one product corpus; one homogeneous corpus means retrieval can say "this deployment has no facts" without per-result bookkeeping; the entity space and fact supersession are deployment-wide; serves Vigent exactly. | A deployment mixing corpora with different needs must split into deployments. |
| B. Per source kind / connector | Mixed corpora in one deployment. | `source_kind` is identity, not configuration; retrieval would have to disclose coverage per result; facts derived from a subset of the corpus look complete but are not. |
| C. Per document or per ingest call | Maximal flexibility. | Same coverage problem, worse: a lineage could have facts for some versions and not others, which breaks the ordered processing Rule 4 relies on (§2). Every client would need a new ingest parameter. |
| D. Per request at ingest *and* a deployment default | Flexible. | All of B and C's problems, plus an override hierarchy to explain. |

**Chosen: A for ingestion, with one scope and no override.** The smallest scope that serves
the case is the one that already exists. A product that needs different layers for
different corpora runs two deployments, which is what D16 says for different trust or
product boundaries anyway. B, C and D are recorded as rejected alternatives with the reason
that they make coverage per-document, which retrieval cannot disclose honestly at the
result level without new per-result machinery.

**Where the value is configured.** Two choices:

- *a runtime API or CLI that flips a database flag;*
- *a profile setting that `setup` reconciles into the database.*

The second matches how every other deployment property works (core value 2: settings are
the one home of configuration), keeps the configuration with the deployment's other
settings, needs no new mutating API (D60: no control plane in the engine), and gives one
writer. The database row stays the runtime authority, so a worker or API process with a
stale environment cannot disagree with the rest. Two booleans,
`REMEMBERSTACK_SELFHOST_CLAIMS_LAYER` and `REMEMBERSTACK_SELFHOST_FACTS_LAYER`, both default
`true`; `setup` refuses facts on with claims off.

**No row means everything on.** Existing deployments have no layer row and keep running
exactly as today; `setup` writes a row only when the configured set differs from the stored
one.

## 5. Ingestion semantics

### 5.1 Where layers begin

A layer is entered at exactly one point in the chain — its **boundary**:

- the **claims boundary** is `embed_chunk` completion (today's `_extract_follow_up`);
- the **facts boundary** is the extract barrier (today's `_enqueue_claim_normalize_fanout`)
  and the D90 empty path.

When the layer is off at its boundary, nothing of it is enqueued. Deciding at a single point
per layer, inside the completing transaction, avoids scattering checks through every handler:
later stages need no check because their work is never created.

The two couplings in §2 need one change each. When facts are not entered, the facts boundary
opens the **claims tail**: `embed_claim` and a reconcile at a second component version,
`reconcile-2026.07:claims` (the engine already varies a stage's component version with its
configuration: `active_flush_version`, `label_relation_component_version`).

What should the claims-mode reconcile do? The first draft limited it to testimony currency.
Review round 1 showed why that is wrong: facts that already exist (from a period when facts
were on) would keep support from retired claims, a connector cycle could finalize against those
stale counts and never close them, and late work after a deletion would leave its fact
contribution behind. All of reconcile's fact steps are deterministic database work (plus an
entity-profile refresh, a cheap embedding call that happens only when facts exist). So the
claims-mode reconcile runs **the same logic** and differs only by not opening the two facts
follow-ups (`label_relation`, the window review). Existing facts stay truthful while facts are
off, deletion settles fully in either mode, and nothing has to be replayed when facts return.

### 5.2 Readiness

Readiness waits for the stages of the layers that are on *now*; with facts off, `reconcile` is
satisfied at either component version, so versions that finished the old full pipeline stay
ready. A version ingested while a layer was off becomes not-ready again if the layer is switched
on, until the backfill has processed it; that is the honest answer. With every layer on, the
expected list is byte-identical to today's, so the LoCoMo readiness gate is unaffected.

### 5.3 Switching a layer off

| Option | Assessment |
| --- | --- |
| Stop new work, keep serving existing data | Serves an incomplete layer as if complete. |
| **Stop new work, keep data, stop serving** | Safe and cheap; re-enabling reuses everything already paid for. |
| Delete or tombstone the layer's data | Destroys paid work and needs its own deletion design. |

**Chosen: the middle row.** What about work already queued for versions that entered the layer?
The first draft *held* it (unclaimed until the layer returned). Review round 1 found that holding
strands versions half-processed: a version past the extract barrier with fact work queued would
never get its claims tail, and late deletion cleanup could sit behind held fact stages. The
replacement is simpler: **switching off drains**. A version that entered a layer finishes it;
only new entries stop. Every version is then either complete in a layer or has a gap, which is
the property the backfill relies on. Spend that must stop at once is what lane budgets are for.
The one exception is lineage-level fact-window review queued outside the version chain (by
period declarations and reconciles): it is held while facts are off and runs when they return.

Deletion (D135) and hard forget are never gated.

### 5.4 Switching a layer on later

The new work is the layer's work for the versions that do not have it. Rule 4 makes this hard:
Selection reuse only looks at earlier versions (§2), so a backfill that processed all versions at
once would re-extract every unchanged chunk of every later version.

| Option | Assessment |
| --- | --- |
| Seed every missing version at once | Pays for every unchanged chunk again. |
| Re-key reuse so order does not matter | Changes the D56/D140 reuse identity; out of proportion. |
| **Lineage-ordered backfill** | One version per lineage at a time, oldest first, through the ordinary chain; lineages run in parallel. |

**Chosen: lineage-ordered backfill**, driven by a **gap table**: a boundary that does not enter a
layer records a gap; the backfill stamps it entered; completion deletes it. Unconfigured
deployments never write a gap. A completion (or a deletion) advances its own lineage; a boundary
defers a version whose lineage has an earlier open gap; `setup` advances every lineage once when
a layer is switched on. Coverage is *derived* from the gap table rather than stored: the first
draft stored a coverage flag, and review round 1 showed two concurrent last-gap completions could
each miss the other's delete and leave it stale forever.

**Which versions.** The §2 facts settle a policy per versioning mode:

- *Snapshot lineages* (D140's editions): currency never changes, so every version is
  backfilled oldest first and the result equals always-on processing.
- *Living lineages*: backfilling old versions would either retire claims the current version
  reuses (reconcile against an unattached current version) or turn retired testimony into facts
  (E3 does not filter on currency). The document *is* its current version, so the backfill
  processes **the current version only** and discharges the older gaps; their stages read
  `skipped`. A living lineage's history in a backfilled layer starts at its current version.

One more guard covers versions arriving during a backfill: a reconcile skips the living
supersession step while the lineage's current version still has an open claims gap, because its
occurrences do not exist yet; the current version's own reconcile settles the lineage. Without
gaps this never applies.

**Locking.** One deployment-scoped advisory lock (the *layer fence*), shared by boundaries,
advances and seeding and exclusive for `setup`'s configuration change, serializes configuration
against chain decisions even before a configuration row exists. Lineage decisions also take the
existing D135 lineage lock (the `documents` row), so a deletion and a boundary of the same lineage
cannot interleave.

Backfill work runs on the **backfill lane** (orchestration §3), so it never delays fresh
ingestion and has its own budget. Today no self-host process claims the backfill lane; the worker
loop must serve it whenever its steady route has no due row.

### 5.5 Cost metering and receipts

Every model call is already metered per stage in `cost_ledger`. The managed text meter stamps a
`processing_profile_id` on each measurement (`doc-text-standard-v1` today), which the managed
cloud prices. It must follow the layers: `doc-text-standard-v1` (all on, unchanged),
`doc-text-claims-v1`, `doc-text-sources-v1`. Review round 1 pointed out that the outcome receipt's
`profile_complete` must certify the *measured* profile, not whatever is configured when the
outcome is computed; otherwise a standard-profile measurement could be certified complete without
its fact work. Outcomes are therefore evaluated against the measured profile's layers, and a
version that can no longer complete them gets `failed` / `processing_profile_changed`.

## 6. Retrieval semantics

### 6.1 Defaults and per-request selection

By default a request consults every layer the deployment serves; a deployment that serves
everything answers exactly as today. Per-request selection is what makes claim evaluation
possible: the same agent, the same corpus, with and without claims.

| Option | Assessment |
| --- | --- |
| A `layers` argument on the assured-operation tools | Changes the tool schemas agents see, the query-surface manifest and the LoCoMo catalogue hash. |
| A request header on every route | Invisible in OpenAPI unless declared everywhere; MCP clients cannot set headers per call. |
| **A `layers` query parameter on `POST /operations/{name}` and the sandbox execution routes, a per-call SDK argument, and `remember mcp --layers`** | Visible in OpenAPI and both SDKs; agent tool schemas untouched; an evaluation runs an unchanged agent with a narrower selection. |

**Chosen: the third row.** Reads have *channels* (layers any subset of which can answer) and
*mandatory dependencies* (layers an argument requires): an `entity_ids` scope reads mentions,
which are facts, even in the chunk channel, and the sandbox's claim search reads mentions for its
`entity_id` filter. Both are validated after arguments are decoded; a mandatory dependency is
never silently dropped.

### 6.2 Saying what is missing

The requirement: an answer must not look like "no answer" when the layer that would have answered
is absent. The first draft added an optional `Envelope.layers` notice, omitted when empty. Review
round 1 showed that even an omitted field changes the generated result schema, the default
operation descriptors and the pinned manifest hash. The design therefore discloses through the
existing contract:

| Situation | Disclosure |
| --- | --- |
| A single-layer read, an explicit selection or a mandatory dependency names a disabled layer | Refusal: 409 `layer_unavailable` (REST), `LayerUnavailable` (SDKs), tool error (MCP), existing sandbox codes with the layer named. |
| A read returns nothing and one of its channels was disabled, excluded or backfilling | `boundary` negative naming the layer, never `known_empty`. |
| A whole side of `combined_context` is unconsulted | Empty envelope with a `boundary` negative. |
| A sandbox statement reads a backfilling layer | A `layer_backfilling:<layer>` entry in `QueryResult.warnings`. |
| Any time | `GET /processing-layers` states each layer; catalogues list only served tools. |

The negative-kind taxonomy stays frozen; `boundary` already means "the system cannot answer past
this point". A non-empty answer from a backfilling layer carries no marker, as with any other
processing lag.

### 6.3 The query sandbox and plane K

Every sandbox view and function reads one layer's data; a statement referencing an unserved or
excluded layer is refused before planning. Without this, a deployment that never had facts would
answer fact views with empty results ("no facts"). The sandbox's public error-code set is part of
its contract, so the existing `relation_not_allowed`, `function_not_allowed` and
`invalid_parameter` codes are used, with the layer named in the message. Plane K compiles pages
about entities, so it is a facts consumer. P3 omits its `entities/` tree without facts.

## 7. Contract surfaces

| Surface | Change |
| --- | --- |
| Settings / compose | `REMEMBERSTACK_SELFHOST_CLAIMS_LAYER`, `REMEMBERSTACK_SELFHOST_FACTS_LAYER` (default `true`). |
| Schema | `deployment_processing_layers`, `processing_layer_gaps`; one migration and its five pinned surfaces. |
| REST / OpenAPI | `layers` query parameter on `POST /operations/{name}` and the sandbox execution routes; 409 `layer_unavailable`; new `GET /processing-layers`. No response model changes. |
| Python SDK | `layers=` on the context operations and sandbox execution; `processing_layers()`; `LayerUnavailable`. |
| TypeScript client | The same, under D141 parity. |
| MCP | Catalogue lists only served tools; `remember mcp --layers`. Tool schemas unchanged. |
| CLI | `--layers` on `remember query text` and `remember operations run`; layer section in `remember ops inspect`. |
| Readiness | Expected stages follow the enabled layers; models unchanged. |
| Managed metering | `processing_profile_id` and `profile_complete` follow the measured layers. |
| LoCoMo | Unaffected: all layers on; no response, descriptor, manifest, catalogue or stage-list change. |

## 8. Rejected and unchosen alternatives

- **Per-document, per-source-kind or per-ingest layer selection** — coverage becomes
  per-document and retrieval cannot disclose it honestly per result (§4).
- **Tombstoning a disabled layer's data** — destroys paid work (§5.3).
- **Holding queued work when switching off** — strands half-processed versions (§5.3).
- **A claims-mode reconcile limited to testimony currency** — leaves existing facts untruthful and
  connector cycles unsettled (§5.1).
- **Unordered backfill** — re-pays unchanged chunks (§5.4).
- **Backfilling every version of a living lineage** — retires reused claims or creates facts from
  retired testimony (§5.4).
- **A stored coverage flag** — racy; derived instead (§5.4).
- **A `layers` argument on agent tools** or **an `Envelope.layers` field** — changes the pinned
  agent surface (§6).
- **A separate deployment-level retrieval default** — per-request and per-connection selection
  covers the evaluation need without a second configuration that can disagree with ingestion.
- **A summaries layer or an entities layer** — see §3.

## 9. Risks

- **A blocked lineage during backfill.** A dead-lettered version stops its lineage's backfill
  until it is replayed or deleted. Visible in `remember ops inspect`.
- **Freshness during backfill.** New versions of a lineage that is still backfilling wait for
  their predecessors. The alternative pays again for unchanged text; Rule 4 takes precedence.
- **Draining costs.** Switching a layer off lets in-flight versions finish their layer; budgets
  are the immediate brake.
- **Managed pricing ids.** The two new `processing_profile_id` values need the cloud's rate
  registry before a managed deployment switches a layer off.
