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
- the **facts boundary** is the extract barrier (today's `_enqueue_claim_normalize_fanout`,
  and the D90 empty path).

When the layer is off at its boundary, nothing of it is enqueued. Making the decision at a
single point per layer, inside the completing transaction, avoids scattering `if layer_on`
checks through every handler, and later stages need no check because their work is never
created.

The two couplings in §2 need one change each. When facts are not entered, the facts boundary
opens the **claims tail** instead: `embed_claim` and a claims-only `reconcile`. The
claims-only reconcile runs testimony currency and nothing else. It is a different
component version of the same stage (`reconcile-2026.07` vs `reconcile-2026.07:claims`),
the same pattern the engine already uses when a setting changes a stage's work
(`active_flush_version`, `label_relation_component_version`). With all layers on, the chain,
the component versions and the readiness list are exactly today's.

### 5.2 Readiness

Readiness waits for the stages of the layers that are on *now*. A version ingested while a
layer was off becomes not-ready again if the layer is switched on, until the backfill has
processed it; that is the honest answer. The P1 and live-graph capabilities are unchanged
because they check configuration, not data. With every layer on, the expected stage list is
byte-identical to today's, so the LoCoMo readiness gate is unaffected.

### 5.3 Switching a layer off

| Option | Assessment |
| --- | --- |
| Stop new work, keep serving existing data | Serves an incomplete layer as if complete: versions ingested after the switch have no facts, while older facts stay "current" with nobody reconciling them. Wrong for law. |
| **Stop new work, keep data, stop serving** | Safe and cheap. Re-enabling reuses everything already paid for. |
| Delete or tombstone the layer's data | Destroys paid work, makes re-enabling a full re-run, and needs a deletion design of its own. |

**Chosen: the middle row.** Concretely: the boundaries stop entering the layer; work rows of
the layer that are already queued are held (not claimed, no attempt spent) and resume if
the layer is switched back on; retrieval and readiness stop using the layer immediately; the
data stays. Deletion (D135) and hard forget are never held: they always remove a
document's contribution from every layer, so data kept while a layer is off never outlives
its source.

### 5.4 Switching a layer on later

The new work is exactly the layer's work for the versions that do not have it. The hard part
is Rule 4: Selection reuse only looks at earlier versions (§2), so a backfill that processed
all versions at once would re-extract every unchanged chunk of every later version.

Options:

| Option | Assessment |
| --- | --- |
| Seed every missing version at once | Fast, but pays for every unchanged chunk again (Rule 4 violation proportional to versions per lineage). |
| Re-key reuse so order does not matter | Changes the D56/D140 reuse identity and every reuse lookup; out of proportion. |
| **Lineage-ordered backfill** | Each lineage advances one version at a time, oldest first, through the ordinary chain, so every version finds its predecessor's results exactly as in normal ingestion. Different lineages run in parallel. |

**Chosen: lineage-ordered backfill.** It needs to know which versions lack a layer. Deriving
that from `processing_state` is possible but awkward: the facts layer has no per-version
row at its start, and claim rows are per chunk. A small **gap table** records it directly:
when a boundary does not enter a layer for a version, it writes a gap row; when the backfill
enters the version it stamps the row; when the layer completes for the version the row is
deleted. All-on deployments never write a gap row, so nothing changes for them, and no
migration of existing versions is needed.

The ordering rule has two halves:

- **Advance on completion.** When a layer completes for a version (claims: the extract
  barrier; facts: the full reconcile) and the layer is backfilling, the lineage's oldest
  unentered gap is entered if every earlier version of the lineage has the layer complete.
- **Defer at the boundary.** While a layer is backfilling, a newly arriving version whose
  lineage has an earlier gap is not entered either; it gets a gap row and waits its turn.
  Otherwise a fresh amendment would be extracted before its predecessors and pay for every
  unchanged chunk.

`setup` starts the backfill by advancing every lineage once. Everything else is the chain
rule (orchestration §1): no polling seeder, no new stage. Both halves and the seeding take a
lineage advisory lock inside their transactions, so a boundary and a completion that race
cannot both conclude that the other will advance the lineage.

Backfill work runs on the **backfill lane** (orchestration §3), so it never delays fresh
ingestion and has its own budget. Today no self-host process claims the backfill lane; the
worker loop must serve it whenever its steady route has no due row.

**What a facts backfill produces.** E3 reads only current testimony. For snapshot lineages
(D140's model for editions: version succession changes no claim's currency) this is the same
input always-on processing would have seen, so the result is the same. For living lineages,
content that was removed before facts were switched on never becomes a fact, so the fact
history of such a lineage starts when facts were enabled. That is a declared boundary, not
an error: the alternative is replaying testimony currency backwards, which no other part of
the engine does.

**Reconcile after a claims-only run.** A version that ran the claims-only reconcile has already
had its currency transitions applied. When the facts backfill reaches it, the full reconcile
must also count those transitions, otherwise a fact supported only by a claim that the
earlier run retired would stay open. The full reconcile therefore treats the transitions
recorded by the version's claims-only run as its own, using the same union the retry path
already uses (`recorded_transitions`).

### 5.5 Cost metering

Every model call is already metered per stage in `cost_ledger`, so layer costs are visible
without change. The managed text meter stamps a `processing_profile_id` on each ingest
measurement (`doc-text-standard-v1` today), which the managed cloud prices. The profile must
reflect the layers: all on keeps `doc-text-standard-v1`; sources and claims is
`doc-text-claims-v1`; sources only is `doc-text-sources-v1`. The cloud's rate registry must
know the new ids before a managed deployment switches a layer off; that is a cross-repository
follow-up. Pricing a later backfill is the cloud's decision (D60).

## 6. Retrieval semantics

### 6.1 Defaults and per-request selection

By default a request consults every layer the deployment serves; a deployment that serves
everything answers exactly as today.

**Per-request selection** is what makes claim evaluation possible: the same agent, the same
corpus, with and without claims. Three placements were considered:

| Option | Assessment |
| --- | --- |
| A `layers` argument on the assured-operation tools | Changes the tool schemas agents see, the query-surface manifest and the LoCoMo catalogue hash; an agent could start passing it and change benchmark behaviour. |
| A request header on every route | Uniform, but invisible in OpenAPI unless declared on every route; MCP clients cannot set headers per tool call. |
| **A `layers` query parameter on `POST /operations/{name}` and the sandbox execution routes, a per-call SDK argument, and a connection-level `--layers` for `remember mcp`** | Visible in OpenAPI and both SDKs, leaves agent-facing tool schemas untouched, and lets an evaluation run an unchanged agent with a narrower layer set. |

**Chosen: the third row.** The selection goes where an agent's reads go: the assured
operations (of which `claims_and_sources_context` and `combined_context` mix layers) and the
sandbox, whose views span all three layers. Single-layer reads (claim search, fact lookups,
resolve, graph) have nothing to select; an MCP session with a narrower selection simply does
not list them.

### 6.2 Saying what is missing

The owner's requirement is explicit, not silent: an answer must not look like "no answer"
when the layer that would have answered is absent. Three situations:

| Situation | Response |
| --- | --- |
| A single-layer read (or the sandbox, or an explicit `layers` request) needs a layer the deployment does not serve | Refusal: HTTP 409 `layer_unavailable` naming the layer; SDK `LayerUnavailable`; MCP tool error with the same code. |
| A mixed operation runs by default and one of its layers is not served | The served part is answered; the envelope carries a `layers` notice (`off`); a wholly unserved part (the facts side of `combined_context`) is an empty envelope with a `boundary` negative. |
| A layer is on but its backfill has not finished | Served, with a `backfilling` notice: older versions may not have it yet. |

The notice is a new optional envelope field that is **omitted from the JSON when empty**, so a
deployment serving everything returns byte-identical responses and existing clients never see
it. The negative-kind taxonomy is frozen (envelope §`NegativeKind`), so no new kind is added;
`boundary` already means "the system cannot answer past this point".

The MCP catalogue lists only tools whose layer is served (`facts_context` and
`resolve_entity` disappear when facts are off), so an agent is not offered a tool that would
only refuse. `GET /deployment` reports the layer states when any layer is not fully served.

### 6.3 The query sandbox and plane K

Every sandbox view and function reads one layer's data; a statement that references a view or
function of an unserved layer is refused with `layer_unavailable` before execution. Without
this, a deployment that never had facts would return empty fact views ("no facts"), and one
that switched facts off would return stale ones. The layer of each view is a code-side
mapping, outside the hashed query-surface manifest, so the LoCoMo pin is unaffected. Plane K
compiles pages about entities, so it is a facts consumer: with facts unserved, nothing
compiles and the page views are refused. P3 omits its `entities/` tree.

## 7. Contract surfaces

| Surface | Change |
| --- | --- |
| Settings / compose | `REMEMBERSTACK_SELFHOST_CLAIMS_LAYER`, `REMEMBERSTACK_SELFHOST_FACTS_LAYER` (default `true`). |
| Schema | `deployment_processing_layers` (one row per deployment, only when not default) and `processing_layer_gaps`; one Alembic migration and its five pinned surfaces. |
| REST / OpenAPI | `layers` query parameter on `POST /operations/{name}` and the sandbox execution routes; 409 `layer_unavailable`; optional `Envelope.layers`; optional `DeploymentBuildInfo.processing_layers`. |
| Python SDK | `layers=` on the context operations and sandbox execution; `LayerUnavailable` error. |
| TypeScript client | The same, under D141 parity (inventory, generated contracts, fixtures). |
| MCP | Tool list filtered by served layers; `remember mcp --layers`. Tool schemas unchanged. |
| CLI | `--layers` on `remember query text` and `remember operations run`; layer state in `remember ops inspect`. |
| Readiness | Expected stages follow the enabled layers; models unchanged. |
| Managed metering | `processing_profile_id` follows the enabled layers (§5.5). |
| LoCoMo | Unaffected: all layers on, no tool schema, manifest or stage-list change. |

## 8. Rejected and unchosen alternatives

- **Per-document, per-source-kind or per-ingest layer selection** — coverage becomes
  per-document and retrieval cannot disclose it honestly per result (§4).
- **Tombstoning a disabled layer's data** — destroys paid work for no correctness gain (§5.3).
- **Unordered backfill** — violates Rule 4 (§5.4).
- **A separate deployment-level retrieval default** distinct from the ingested layers — the
  per-request and per-connection selection covers the evaluation need without a second
  configuration that can disagree with ingestion.
- **A `layers` argument on the agent tools** — changes agent-visible schemas and the pinned
  benchmark surface (§6.1).
- **A summaries layer or an entities layer** — see §3.

## 9. Risks

- **Held work after switching off.** Queued rows of a disabled layer stay `pending`. They are
  visible in `remember ops inspect` and resume on re-enable; they are not dead letters.
- **A blocked lineage during backfill.** A dead-lettered version stops its lineage's backfill
  at that version until it is replayed. This is visible (gap count, dead-letter list) and
  matches how a dead letter blocks normal ingestion readiness.
- **Freshness during backfill.** New versions of a lineage that is still backfilling wait for
  their predecessors. The alternative pays again for unchanged text; the owner's Rule 4 takes
  precedence.
- **Managed pricing ids.** New `processing_profile_id` values need the cloud's rate registry
  (§5.5).
