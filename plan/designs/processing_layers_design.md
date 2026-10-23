# Processing layers (D143)

**Status:** binding design. **Decision:**
[D143](../../decisions.md#d143-processing-layers-can-be-switched-off-per-deployment-and-per-request).
**Analysis:** [processing layer toggles](../analysis/processing_layer_toggles.md).
**Delivery order:** [processing layers delivery plan](../plans/processing_layers.md).

The engine processes every document in three **layers**, and serves retrieval from the same
three. A deployment can switch the claims layer and the facts layer off for ingestion, and a
request can narrow which layers it reads. **Every layer is on by default**: a deployment that
configures nothing ingests, reports readiness and answers exactly as before this design, down
to the bytes of its responses, operation descriptors and query-surface manifest.

A running example: a company keeps its *policy library* — handbooks and procedures, each with
several dated editions (snapshot lineages) — in one deployment. Questions are answered from the
policy text, so the operator switches the facts layer off and keeps claims. A year later they
want entity pages about the people named in the policies and switch facts on; the engine builds
facts for the existing editions, lineage by lineage and oldest edition first, without
re-extracting or re-embedding anything. The Czech-legislation product that motivated the design
(analysis §1) runs the same way with facts off.

## 1. Concepts

- **Layer.** A named part of the pipeline and the data it produces:
  - **sources** — conversion, structure (including section summaries), cross-references,
    chunks and chunk embeddings; documents, sections, chunks and their search channels;
  - **claims** — claim extraction (Selection and Claimify), claim embeddings and testimony
    currency; claims and their search channels;
  - **facts** — normalisation and entity resolution, fact adjudication, fact follow-ups, fact
    labels, fact-window reviews and entity profiles; entities, mentions, relations,
    observations, the entity graph and plane-K pages.
- **Dependency.** Each layer is built from the one before: sources ← claims ← facts. The legal
  sets are {sources}, {sources, claims} and {sources, claims, facts}. Sources is always on.
- **Enabled.** A layer the deployment is configured to process (§3).
- **Served.** A layer retrieval may read: exactly the enabled layers.
- **Boundary.** The single point in the chain where a version enters a layer (§4.1).
- **Entered / complete.** A version has *entered* a layer when its boundary enqueued the
  layer's work; it is *complete* in the layer when that work finished (§5.1).
- **Gap.** A record that a live version did not enter a layer at its boundary (§5.1).
- **Backfilling.** An enabled layer with at least one open gap. Coverage is derived from the
  gap table, never stored.

## 2. Stages by layer

| Stage | Layer | Notes |
| --- | --- | --- |
| `ingest`, `convert`, `structure`, `crossref`, `chunk`, `embed_chunk` | sources | Section summaries (D79) are part of `structure` and not separately switchable: they feed E1/E2 section orientation, which is an input of `E2_EXTRACTOR_VERSION`. |
| `extract_claims`, `ground_claims`, `embed_claim` | claims | |
| `reconcile` at `reconcile-2026.07:claims` | claims | The claims tail's reconcile (§4.2). |
| `normalize_relations`, `adjudicate_observations`, `adjudicate_supersession`, `label_relation`, `review_fact_windows` | facts | |
| `reconcile` at `reconcile-2026.07` | facts | The full reconcile, unchanged. |
| `hard_forget` | — | Never gated. |

Plane-K stages consume the facts layer and run only while facts are served (§7.6).

## 3. Configuration and state

### 3.1 Settings

The deployment profile carries two settings (core value 2: settings are the one home of
configuration):

| Variable | Default | Meaning |
| --- | --- | --- |
| `REMEMBERSTACK_SELFHOST_CLAIMS_LAYER` | `true` | Process and serve the claims layer. |
| `REMEMBERSTACK_SELFHOST_FACTS_LAYER` | `true` | Process and serve the facts layer. Requires claims. |

There is no per-document, per-source-kind or per-ingest-call setting (§10). A product that needs
different layers for different corpora uses separate deployments (D16).

### 3.2 Stored state

The database is the runtime authority, so every process agrees whatever its environment:

```sql
CREATE TABLE deployment_processing_layers (
  deployment_id  uuid PRIMARY KEY REFERENCES deployments ON DELETE CASCADE,
  claims_enabled boolean NOT NULL,
  facts_enabled  boolean NOT NULL,
  changed_at     timestamptz NOT NULL,
  CHECK (claims_enabled OR NOT facts_enabled)
);

CREATE TABLE processing_layer_gaps (
  deployment_id uuid NOT NULL,
  version_id    uuid NOT NULL,
  doc_id        uuid NOT NULL,
  layer         text NOT NULL CHECK (layer IN ('claims', 'facts')),
  recorded_at   timestamptz NOT NULL,
  entered_at    timestamptz,   -- the backfill entered the version (§5.2)
  discharged_at timestamptz,   -- superseded in a living lineage; never processed (§5.3)
  PRIMARY KEY (deployment_id, version_id, layer),
  CHECK (entered_at IS NULL OR discharged_at IS NULL)
);
CREATE INDEX ix_layer_gaps_open
  ON processing_layer_gaps (deployment_id, layer, doc_id)
  WHERE discharged_at IS NULL;
```

**No row in `deployment_processing_layers` means both layers enabled.** Existing deployments
need no data migration. A gap is *open* while `discharged_at` is null; a layer is backfilling
while it is enabled and has an open gap of a live version. Like `obs_flush_version_state`, gap
rows carry no foreign key; deletion and hard forget delete them explicitly (§6.3).

### 3.3 One fence

Every decision that reads the layer configuration and writes chain work or gaps takes the
**layer fence** — a transaction-scoped advisory lock keyed by the deployment
(`processing-layers:<deployment_id>`) — in *shared* mode; `setup` takes it in *exclusive* mode
to change the configuration. The fence exists whether or not a configuration row exists, so a
boundary can never decide under the old configuration and commit under the new one.

Lock order, everywhere: hard-forget fence (where already taken) → layer fence → the existing
barrier locks of the completing stage → the D135 lineage lock (`documents` row `FOR UPDATE`).

### 3.4 `setup` reconciles the setting

`setup` (the profile's idempotent bootstrap command, run on every start) is the only writer of
`deployment_processing_layers`:

1. It refuses facts on with claims off, naming both variables.
2. Under the exclusive fence it compares the configured set with the stored one (or the
   default) and, if they differ, writes the row and logs one structured line per changed layer
   (layer, from, to, open gap count). Nothing else is written; switching off needs no other
   action (§6).
3. After committing it **seeds** every enabled layer that has open gaps (§5.4). It does this on
   every run, so an interrupted seed resumes on the next start.

No engine API changes layers: it is deployment configuration with a cost, not a caller action
(D60).

## 4. Ingestion

### 4.1 Boundaries

Each layer is entered at exactly one boundary, decided inside the transaction that completes
the previous stage, under the layer fence and the lineage lock (§3.3):

- **claims boundary** — `embed_chunk` completion (today: one `extract_claims` job per chunk, or
  the D90 empty path for a version without chunks);
- **facts boundary** — the extract barrier (`complete_chunk_extract` when every chunk of the
  version is grounded) and the D90 empty path (today: the E3 normalisation fan-out, or the empty
  observation flush).

At each boundary:

| Condition (checked in order) | Action |
| --- | --- |
| The layer is disabled | Do not enter. Record a gap (at the claims boundary: a claims gap and a facts gap) unless the version or its lineage is deleted. The facts boundary opens the **claims tail** (§4.2). |
| The version or its lineage is deleted | Enter as today, so the chain's reconcile retires the late work (D135); record no gap. |
| An earlier live version of the lineage has an open gap of this layer | Do not enter; record a gap. The facts boundary opens the claims tail. The version waits for the backfill (§5.2). |
| Otherwise | Enter the layer exactly as today. |

With no configuration row and no gaps — every deployment that configures nothing — only the last
row ever applies, so the chain, its work rows and its component versions are unchanged. Stages
after a boundary need no check: their work exists only if the boundary opened it.

### 4.2 The claims tail and the two reconcile modes

Today `embed_claim` is opened only after fact adjudication, and `reconcile` performs testimony
currency (a claims job: claims of content that a living document dropped stop being current
testimony, evidence lifecycle §3) together with the fact steps of evidence lifecycle §5 and two
paid follow-ups. When the facts boundary does not enter facts, it opens the **claims tail**:

- `embed_claim` for the version (unchanged handler);
- `reconcile` at component version **`reconcile-2026.07:claims`**.

The claims-mode reconcile runs **the same handler logic as the full reconcile** — currency
transitions, recount, the zero-support policy (including the connector-cycle deferral to
`CycleFinalizer`), review flags, `evidence_changed`, entity-profile refresh, and the D135
deleted-testimony path with its document-binding rebuild — and differs in one thing: it opens
**no `label_relation` and no fact-window review**. So facts that already exist (from a period
when facts were on) keep their support counts and closures exact while facts are off, and the
work is a no-op when no facts exist. No union of transitions across runs is needed, and cycle
finalization sees correct counts.

When the facts layer later reaches a version that ran the claims tail, the ordinary facts chain
ends in the full reconcile at `reconcile-2026.07` — a different idempotency key — which finds no
new transitions, and opens `label_relation` and the window review as today.

**Living lineages and an unprocessed current version.** A reconcile computes `living`-mode
supersession against the lineage's current version's claim occurrences. If the current version
has an open claims gap (its claims are deferred or not yet backfilled), those occurrences do not
exist yet, and retiring against them would wrongly retire claims the current version will reuse.
In that case the reconcile skips the supersession step; the current version's own reconcile
computes it lineage-wide when it runs. Without gaps — every unconfigured deployment — the step
runs exactly as today.

### 4.3 Switching off drains; it never holds

A version that entered a layer finishes that layer: work already queued for it is claimed and
runs as normal after the layer is switched off. Every version therefore ends either complete in
a layer or with a gap — never half-processed — and no claims tail can be stranded behind held
fact work. Only one kind of work is held: **`review_fact_windows` rows are not claimed while
facts are disabled.** They are lineage-level reviews that declarations and reconciles queue
outside the version chain; they run when facts are switched back on. A caller that needs spend
to stop at once uses the existing lane budgets (orchestration §4).

### 4.4 Readiness

`PipelineReadinessCatalog` expects the stages of the enabled layers, in today's order:

| Enabled layers | Expected stages |
| --- | --- |
| sources, claims, facts | today's twelve, unchanged |
| sources, claims | `convert`, `structure`, `chunk`, `embed_chunk`, `extract_claims`, `ground_claims`, `embed_claim`, `reconcile` |
| sources | `convert`, `structure`, `chunk`, `embed_chunk` |

With facts disabled, `reconcile` is satisfied by a succeeded row at **either** component version,
so a version that finished the full pipeline before facts were switched off stays ready. A
version whose gap was discharged (§5.3) reports that layer's stages as `skipped`. The capability
checks (`p1`, `live_graph`, `p3`) are unchanged: they check configuration and publication, not
data. A version with an open gap in an enabled layer is not ready until the backfill processes it.

### 4.5 Metering

Every model call is metered per stage in `cost_ledger` as today. The managed text meter stamps the
`processing_profile_id` from the layers enabled at measurement time:

| Enabled layers | `processing_profile_id` |
| --- | --- |
| sources, claims, facts | `doc-text-standard-v1` (unchanged) |
| sources, claims | `doc-text-claims-v1` |
| sources | `doc-text-sources-v1` |

The outcome receipt certifies **the measured profile**, not the current configuration:
`profile_complete` is true only when the version is complete in every layer of the profile it was
measured under. A version that cannot reach that state — it has a gap in a measured layer that is
now disabled, or a discharged gap — gets a `failed` outcome with reason
`processing_profile_changed`. Layers enabled after measurement are not part of the profile and do
not delay its receipt. Pricing, including of a later backfill, belongs to the managed cloud (D60),
whose rate registry must know a profile id before a managed deployment uses it.

## 5. Switching a layer on later

### 5.1 Gaps, entry and completion

Boundaries are the only writers of new gap rows (§4.1). A version is **complete** in a layer when:

- **claims** — its extract barrier is ready (every chunk grounded), or it has no chunks;
- **facts** — its full reconcile succeeded.

Completion deletes the version's gap of that layer, if any. Deleting or forgetting a version
deletes its gaps (§6.3). Unconfigured deployments never write a gap.

### 5.2 Advance and defer

Reuse across versions only looks at earlier versions of a lineage (Selection reuse requires a
smaller `version_no`; Claimify and claim attachment follow from it). A backfill therefore enters
**one version of a lineage at a time**, and different lineages run in parallel. Two rules, both
under the fence and the lineage lock:

- **Defer** (at the boundary, §4.1): a version whose lineage has an earlier open gap of the layer
  records a gap instead of entering, so a fresh version waits behind its predecessors instead of
  being extracted before them.
- **Advance**: whenever a version completes a layer, or a version is deleted, while the layer is
  enabled and the lineage has an open, unentered gap of it, the transaction picks the lineage's
  **next target** (§5.3) and enters it if every earlier live version of the lineage that
  completed the sources layer is complete in this layer or has a discharged gap. For facts, the
  target must also be complete in claims; if it is not, its own facts boundary enters facts when
  claims complete. A target whose predecessor is still running waits for that predecessor's
  completion, which advances again.

**Entering** a version stamps its gap's `entered_at` and enqueues exactly what its boundary would
have enqueued — the per-chunk `extract_claims` jobs (or the empty path) for claims, the E3
normalisation fan-out (or the empty observation flush) for facts — on the **backfill lane**.
Versions that never completed the sources layer (a failed conversion) are ignored. A version whose
layer work dead-letters stops its lineage at that version until it is replayed or deleted; both are
visible in `remember ops inspect`.

### 5.3 Which versions a backfill processes

- **Snapshot lineages** — version succession changes no claim's currency, which is D140's model for
  editions — are backfilled **every version, oldest first**. Each version finds its predecessor's
  results exactly as in normal ingestion.
- **Living lineages** — the document *is* its current version — are backfilled **at their current
  version only**. When advance reaches a living lineage, it enters the current version and
  discharges (`discharged_at`) every other open, unentered gap of the layer in that lineage. Content
  that only older versions carried is never extracted (claims) or turned into facts (facts): a
  living lineage's claim and fact history in a backfilled layer starts at its current version.

This keeps the ordinary handlers' inputs truthful. The E3 handlers normalise and adjudicate every
claim of the version they are given, current or not; processing an old living version would turn
retired testimony into new facts. For snapshot lineages every claim stays current, so the facts a
backfill builds equal those always-on processing would have built.

### 5.4 Seeding and coverage

`setup` seeds an enabled layer by running **advance** once for every lineage with an open,
unentered gap of that layer, in bounded batches. From then on completions and deletions advance
their own lineages; there is no polling job and no new stage. Coverage is derived: an enabled layer
is backfilling while an open gap of a live version exists, and complete otherwise (one indexed
`EXISTS`). Nothing stores or settles a coverage value, so no race can leave it stale.

### 5.5 Lanes

Backfill work runs on the backfill lane (orchestration §3): it never delays fresh ingestion and has
its own budget (`WorkLedgerSettings.budgets`). A self-host worker serves its stage's backfill route
whenever its steady route has no due row.

### 5.6 Rule 4: what a backfill pays for

A backfill re-enters only the layer that was missing. It never re-chunks, never re-embeds a chunk,
and changes no reuse input: chunking (D58), `extraction_input_hash` and `reuse_identity_hash` (D56,
D140 text origin, fixed when the chunk was created), embedding carry-forward (D80) and the D122
Claimify window are those of normal ingestion, because the backfill runs the ordinary handlers in
lineage order. A snapshot lineage backfilled oldest first pays what always-on ingestion of the same
versions would have paid: for the first version everything; for each later version, Selection for
its changed chunks and their immediate same-section neighbours, and Claimify additionally within the
known D122 window (a Rule 4 gap that D143 neither causes nor fixes). A living lineage pays for its
current version once. Claim embeddings skip claims that are already embedded.

## 6. Switching a layer off

### 6.1 Effects

- Boundaries stop entering the layer (§4.1); new versions get gaps.
- Versions that already entered the layer finish it (§4.3).
- Retrieval stops serving the layer immediately (§7); readiness stops expecting it (§4.4).
- The layer's data is kept. It is not deleted, tombstoned or served. Facts that exist keep exact
  support counts and closures through the claims-mode reconcile (§4.2). Switching the layer back
  on reuses everything: versions complete in the layer have no gap and are not re-processed.

### 6.2 Work outside the version chain

Window reviews are held while facts are off (§4.3). Review-queue verdicts that an operator applies
still run their own fact updates; they are explicit operator actions.

### 6.3 Deletion and forgetting are never gated

Document deletion (D135) and hard forget remove a document's contribution from every layer,
whatever the configuration, through their existing cascades. Late pipeline work after a deletion
reaches a reconcile in either mode, both of which run the D135 deleted-testimony path in full
(§4.2). Deletion also deletes the version's gaps and advances the surviving lineage (§5.2) in the
same transaction, so a deleted head never blocks its successors; a boundary never records a gap
for a deleted version (§4.1).

## 7. Retrieval

### 7.1 Served layers and the per-request selection

By default a request reads every served layer. A caller may narrow it with **`layers`**: a
non-empty set of `sources`, `claims`, `facts`. It is accepted by:

- `POST /operations/{name}` (query parameter, repeatable), and the SDK methods
  `claims_and_sources_context`, `combined_context` and the generic operation call;
- the sandbox execution routes `POST /query/sql`, `POST /query/sql/explain` and the saved-query run
  route, and their SDK methods;
- `remember mcp --layers` (engine mode): applied to every operation and sandbox call of the
  session; tools that read only excluded layers are not listed;
- `remember query text --layers` and `remember operations run --layers`.

It is deliberately **not** an argument of the agent tools: tool input schemas, operation descriptors,
the query-surface manifest and the answer-tool catalogue are unchanged.

### 7.2 Which layers each read uses

A read has **channels** — layers it may consult, any subset of which can answer — and **mandatory
dependencies** — layers an argument requires. Both are validated after arguments and bound
parameters are decoded, before any embedding call, nomination, planning or confirmation.

| Read | Channels | Mandatory dependencies |
| --- | --- | --- |
| `search_chunks`, `adjacent_chunks`, document list/search, section history, document references, `graph/citation-path` | sources | — |
| `search_claims` | claims | — |
| `resolve`, `lookup_relations`, `lookup_observations`, `transcript_relation`, `hydrate_relation`, `graph/neighborhood`, `graph/path` | facts | — |
| `facts_context`, `resolve_entity` (operations) | facts | — |
| `claims_and_sources_context` | sources, claims | facts when `entity_ids` is non-empty (entity scoping reads mentions in every channel) |
| `combined_context` | sources, claims, facts | facts when `entity_ids` is non-empty |
| sandbox views and functions | per view (§7.5) | facts for a claim search with an `entity_id` filter |

Rules:

1. A read none of whose channels remain (disabled or excluded) is refused.
2. A mandatory dependency that is disabled is refused; one excluded by `layers` is refused as an
   invalid request — an entity filter is never silently dropped.
3. A layer named in `layers` that the deployment does not serve is refused.
4. Otherwise the read consults its remaining channels; the D140 `scope_pending` probes and their
   freshness accounting consult the same channels.

Refusals: REST and the operation route answer **409 `layer_unavailable`** (body
`{"code": "layer_unavailable", "layer": ..., "status": "disabled", "message": ...}`) for disabled
layers and **422 `invalid_parameter`** for selections that exclude every channel or a mandatory
dependency. The Python SDK raises `LayerUnavailable` (a `MemoryApiError` with `layer`), the
TypeScript client mirrors it, and MCP returns a tool error with the same code. The sandbox uses its
existing codes (§7.5).

### 7.3 Saying what is missing — inside the existing contract

The negative-kind taxonomy is frozen and the operation result schemas are hashed into the
query-surface manifest, so D143 adds **no response field**. Disclosure uses what exists:

- **Empty is never `known_empty` when a layer was not consulted.** When a read returns nothing and at
  least one of its channels was disabled, excluded or backfilling, its negative is `boundary` (not
  `known_empty`), with an explanation naming the layer and a workaround. `known_empty` keeps its
  meaning: every channel searched, nothing there.
- **A wholly unconsulted side is a boundary.** The facts side of `combined_context` in a deployment
  without facts, or with facts excluded, is an empty fact envelope whose negative is `boundary`
  ("the facts layer is not processed by this deployment"; workaround: `claims_and_sources_context`).
- **Sandbox results** carry a warning `layer_backfilling:<layer>` in `QueryResult.warnings` (an
  existing free-text list) for each referenced layer that is backfilling.
- **Discovery** (§7.4) states each layer's state machine-readably.

Non-empty results from a backfilling layer carry no marker, as with any other processing lag. With
every layer served and complete, every response is byte-identical to today's.

### 7.4 Discovery

- **`GET /processing-layers`** (new route, `memory:read`) returns
  `{"claims": state, "facts": state}` with states `enabled`, `backfilling` and `disabled`. Existing
  response models — including `GET /deployment` — are unchanged.
- `GET /operations`, the `tools` map of `GET /deployment` and the MCP catalogue (in-process and
  engine mode) list only operations and tools whose channels are served: with facts disabled,
  `facts_context` and `resolve_entity` are not listed. With every layer enabled, all three are
  unchanged.

### 7.5 The query sandbox

Each view and function belongs to one layer; a view reading several belongs to the highest:

- **sources:** `documents_live`, `document_versions_visible`, `sections_live`, `chunks_live`,
  `chunks_all_versions_live`, `document_effective_periods_live`, `document_crossrefs_live`;
  `semantic_chunks`, `lexical_chunks`, `fetch_chunk_bodies`, `versions_in_scope`,
  `effective_intervals`, `graph_citation_path`;
- **claims:** `claims_visible_history`, `claims_canonical`, `claims_live`, `claim_occurrences_live`,
  `testimony_currency_events_visible`; `semantic_claims`, `lexical_claims` (facts as a mandatory
  dependency when called with an `entity_id` filter, literal or bound);
- **facts:** every entity, mention, fact, evidence, contradiction, graph and page view, and
  `changes_visible` (it reads fact and claim events); `semantic_facts`, `semantic_entities`,
  `facts_as_of`, `graph_neighborhood`, `graph_path`, `fact_in_scope_support`.

After validation and before planning, a statement (including a saved query) that references a view
or function of a disabled or excluded layer is refused with the existing `relation_not_allowed` or
`function_not_allowed` code, and an excluded or disabled `entity_id` dependency with
`invalid_parameter`; the message names the layer. The public error-code set, the manifest and
`/query/space` are unchanged; the layer map lives in code beside the view contracts.

### 7.6 Plane K and P3

Plane K compiles pages about entities and predicates from facts, so it runs only while facts are
served; the page views are facts views (§7.5). The P3 corpus filesystem omits its `entities/` tree
when facts are not served; its `documents/` tree is unchanged.

## 8. Compatibility

- **No configuration, no change.** Without a configuration row: no gap rows, no held work, the same
  chain, component versions and readiness list; every response, `GET /operations` descriptor,
  `GET /deployment` body, MCP catalogue and tool schema, the query-surface manifest and its hash are
  byte-identical.
- **LoCoMo** runs with every layer enabled and is unaffected: its readiness gate, model bindings,
  surface manifest hash and catalogue hash stay as pinned.
- **Clients.** The request parameter is optional and the new route is additive; no response model
  changes, so clients of either age read every response. The Python SDK and TypeScript client gain
  the parameter and the route together (D141 parity).

## 9. Acceptance tests

The implementation is accepted when these hold, in CI, against PostgreSQL where the behaviour is
database-level, with recorded or fake model providers:

1. **Defaults are byte-identical.** With no configuration: the readiness stage list and component
   versions; the serialized `GET /operations`, `GET /deployment`, an operation response of each
   assured operation, both MCP tool lists with input schemas; the regenerated query-surface manifest
   and its hash; the LoCoMo protocol pins (`benchmarks/locomo/protocol.py`) — all equal the
   pre-D143 values. No configuration row or gap row is written.
2. **Facts off.** A version ingests to `ready` with the claims-mode reconcile; no E3 stage row and no
   E3 provider call exist; a living lineage that drops a paragraph retires that paragraph's claims;
   claim search returns only current testimony; a version that completed the full pipeline before
   the switch stays ready.
3. **Claims off.** A version is ready after `embed_chunk`; no claims or facts rows exist.
4. **Drain.** Switching facts off while a version is between the extract barrier and its full
   reconcile lets that version finish (including `embed_claim`) and become ready.
5. **No disabled or excluded read.** A statement-capture test with a pending periodised lineage
   (D140 probe path) shows that `claims_and_sources_context` and `combined_context` issue no SQL
   against a claim relation with claims disabled, and none with `layers=sources`; `search_claims` is
   409; an empty result's negative is `boundary`; `entity_ids` with facts excluded is 422 and with
   facts disabled 409; a sandbox `semantic_claims` with a bound `entity_id` filter is refused in both
   cases.
6. **Rule 4 backfill (snapshot).** Two dated versions of one lineage (stable names; a one-paragraph
   edit inside a multi-chunk section with untouched sections before and after) ingest with claims
   off; claims are switched on. The backfill does no `chunk` or `embed_chunk` work and makes no
   chunk-embedding call; v1's Selection runs for every chunk; v2's Selection runs only for the edited
   chunk and its same-section neighbours; every v2 chunk with unchanged Selection and text-origin
   identity keeps them; every v2 chunk with an unchanged Claimify identity reuses v1's claim ids;
   per-stage provider call counts equal those of an always-on run of the same versions. The D122
   window and packing-boundary cases are covered by expected-failure tests naming the known gap. The
   existing Rule 4 tests (`test_reuse_lifecycle.py`, `test_d140_text_origin_pg.py`) pass unchanged.
7. **Living backfill.** A living lineage with three versions ingested with claims off is backfilled
   at its current version only: older gaps are discharged and report `skipped`; current testimony
   after the backfill equals the current version's claims; a version ingested during the backfill is
   deferred, and the earlier version's reconcile does not retire claims it later reuses.
8. **Ordering and races.** A version crossing its boundary while its predecessor is in the backfill
   records a gap and enters only after the predecessor completes; a zero-chunk successor advances; a
   dead-lettered head blocks until replay or deletion, and deleting it advances the successor; a
   late boundary for a deleted version writes no gap; a configuration change concurrent with a
   boundary decision serializes on the fence.
9. **Facts later.** A snapshot lineage processed with facts off and switched to facts on gets the
   facts an always-on run produces; switching facts on → off (a living version removes a fact's only
   claim inside a connector cycle that finalizes) → on leaves the fact closed.
10. **Receipts.** A version measured under `doc-text-standard-v1` whose facts boundary falls after
    facts are switched off gets `failed` / `processing_profile_changed`; a version measured under
    `doc-text-sources-v1` succeeds when sources complete even if claims are switched on meanwhile;
    default receipts are unchanged.
11. **Deletion.** Deleting a document while facts are off closes its facts, rebuilds its document
    bindings and deletes its gaps, without switching facts on.

## 10. Non-goals and scope boundaries

- **Per-document, per-source-kind or per-ingest-call layer selection.** Coverage would become
  per-document and retrieval could not disclose it per result; use separate deployments.
- **Deleting a disabled layer's data.** Switching off never deletes; deletion is D135's and hard
  forget's job.
- **Holding a layer's in-flight work.** Switching off drains; budgets stop spend at once.
- **A deployment-level retrieval default different from the enabled layers.** Narrow per request or
  per MCP connection.
- **Backfilling a living lineage's superseded versions** (§5.3).
- **A per-response layer field.** It would change the hashed operation result schemas (§7.3).
- **A runtime API that changes layers** (§3.4).

## 11. Worked example

The policy library from the introduction, with `REMEMBERSTACK_SELFHOST_FACTS_LAYER=false`:

1. *Travel Policy* edition 1 is ingested. Its chain runs sources and claims; at the extract barrier
   the facts boundary records a facts gap and opens `embed_claim` and the claims-mode reconcile.
   Readiness reports it ready after that reconcile.
2. Edition 2 changes one paragraph of § 4. Selection runs for that chunk and its same-section
   neighbours; every other chunk reuses edition 1's results. Facts gap again.
3. An agent calls `combined_context("per-diem allowance")`. The `claims_and_sources` side answers
   from claims and chunks; the `facts` side is an empty fact envelope with a `boundary` negative
   naming the facts layer. The MCP catalogue does not list `facts_context`;
   `GET /processing-layers` says `{"claims": "enabled", "facts": "disabled"}`.
4. The operator sets the facts variable to `true` and restarts. `setup` seeds every lineage: edition
   1 enters facts on the backfill lane. When its full reconcile completes, the lineage advances to
   edition 2. While gaps remain, facts are `backfilling`, and an empty fact answer is a `boundary`.
   When the last gap closes, facts are `enabled`.
