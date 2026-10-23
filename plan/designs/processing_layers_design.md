# Processing layers (D143)

**Status:** binding design. **Decision:**
[D143](../../decisions.md#d143-processing-layers-can-be-switched-off-per-deployment-and-per-request).
**Analysis:** [processing layer toggles](../analysis/processing_layer_toggles.md).
**Delivery order:** [processing layers delivery plan](../plans/processing_layers.md).

The engine processes every document in three **layers**, and serves retrieval from the same
three. A deployment can switch the claims layer and the facts layer off for ingestion, and a
request can narrow which layers it reads. **Every layer is on by default**: a deployment that
configures nothing ingests, reports readiness and answers exactly as before this design.

A running example: a company keeps its *policy library* — handbooks and procedures, each
with several editions — in one deployment. Questions are answered from the policy text, so
the operator switches the facts layer off and keeps claims. A year later they want entity
pages about the people named in the policies and switch facts on; the engine builds facts
for the existing editions, lineage by lineage, without re-extracting or re-embedding text it
already processed. The Czech-legislation product that motivated the design (analysis §1)
runs the same way with facts off.

## 1. Concepts

- **Layer.** A named part of the pipeline and the data it produces:
  - **sources** — conversion, structure (including section summaries), cross-references,
    chunks and chunk embeddings; documents, sections, chunks and their search channels;
  - **claims** — claim extraction (Selection and Claimify), claim embeddings and testimony
    currency; claims and their search channels;
  - **facts** — normalisation and entity resolution, fact adjudication, fact follow-ups and
    reconciliation, fact labels, fact-window reviews and entity profiles; entities,
    mentions, relations, observations, the entity graph and plane-K pages.
- **Dependency.** Each layer is built from the one before: sources ← claims ← facts. The
  legal sets are {sources}, {sources, claims} and {sources, claims, facts}. Sources is always
  on.
- **Enabled.** A layer the deployment is configured to process (§3).
- **Served.** A layer retrieval may read: exactly the enabled layers. A disabled layer is
  not served even if data from an earlier period exists.
- **Boundary.** The single point in the chain where a version enters a layer (§4.1).
- **Gap.** A version that has not been processed by a layer (§5.1).
- **Coverage.** For an enabled layer: `complete` when no version has a gap, otherwise
  `backfilling`.

## 2. Stages by layer

| Stage | Layer | Notes |
| --- | --- | --- |
| `ingest`, `convert`, `structure`, `crossref`, `chunk`, `embed_chunk` | sources | Section summaries (D79) are part of `structure` and not separately switchable: they feed E1/E2 section orientation, which is an input of `E2_EXTRACTOR_VERSION`. |
| `extract_claims`, `ground_claims`, `embed_claim` | claims | |
| `reconcile` at `reconcile-2026.07:claims` | claims | Testimony currency only (§4.2). |
| `normalize_relations`, `adjudicate_observations`, `adjudicate_supersession`, `label_relation`, `review_fact_windows` | facts | |
| `reconcile` at `reconcile-2026.07` | facts | The full reconcile, which also performs testimony currency. |
| `hard_forget` | none | Never gated (§6.3). |

Plane-K stages (`compile_knowledge`, `reflect_knowledge`, `lint_knowledge`,
`dispatch_knowledge`) consume the facts layer and run only while facts are served.

A stage's layer is a function of its stage and component version, `layer_of(stage,
component_version)`; it is defined once, in code, next to the stage vocabulary.

## 3. Configuration and state

### 3.1 Settings

The deployment profile carries two settings (core value 2: settings are the one home of
configuration):

| Variable | Default | Meaning |
| --- | --- | --- |
| `REMEMBERSTACK_SELFHOST_CLAIMS_LAYER` | `true` | Process and serve the claims layer. |
| `REMEMBERSTACK_SELFHOST_FACTS_LAYER` | `true` | Process and serve the facts layer. Requires claims. |

There is no per-document, per-source-kind or per-ingest-call setting (§10). A product that
needs different layers for different corpora uses separate deployments (D16).

### 3.2 Stored state

The database is the runtime authority, so every process agrees whatever its environment:

```sql
CREATE TABLE deployment_processing_layers (
  deployment_id     uuid PRIMARY KEY REFERENCES deployments ON DELETE CASCADE,
  claims_enabled    boolean NOT NULL,
  facts_enabled     boolean NOT NULL,
  claims_coverage   text NOT NULL CHECK (claims_coverage IN ('complete', 'backfilling')),
  facts_coverage    text NOT NULL CHECK (facts_coverage IN ('complete', 'backfilling')),
  changed_at        timestamptz NOT NULL,
  CHECK (claims_enabled OR NOT facts_enabled)
);

CREATE TABLE processing_layer_gaps (
  deployment_id uuid NOT NULL,
  version_id    uuid NOT NULL,
  doc_id        uuid NOT NULL,
  layer         text NOT NULL CHECK (layer IN ('claims', 'facts')),
  recorded_at   timestamptz NOT NULL,
  entered_at    timestamptz,             -- set when the backfill enters the version
  PRIMARY KEY (deployment_id, version_id, layer)
);
CREATE INDEX ix_layer_gaps_lineage
  ON processing_layer_gaps (deployment_id, layer, doc_id);
```

**No row in `deployment_processing_layers` means every layer enabled with coverage
`complete`.** Existing deployments therefore need no data migration. A coverage value is
meaningful only while its layer is enabled. Like `obs_flush_version_state`, gap rows carry
no foreign key; document deletion and hard forget delete them explicitly (§6.3).

### 3.3 `setup` reconciles the setting

`setup` (the profile's idempotent bootstrap command, run on every start) is the only
writer of `deployment_processing_layers`:

1. It refuses facts on with claims off, naming both variables.
2. It locks the deployment's layer row (`FOR UPDATE`, creating it if needed) and compares
   the configured set with the stored one (or the all-on default).
3. A layer switched **off**: `*_enabled = false`. Nothing else is written (§6).
4. A layer switched **on**: `*_enabled = true`; coverage is `backfilling` if any gap row of
   that layer exists for a live version, otherwise `complete`.
5. It commits, then seeds every enabled layer whose coverage is `backfilling` (§5.3). Seeding
   is idempotent, so re-running `setup` resumes an interrupted seed.
6. When the stored state equals the all-on default and no row exists, it writes nothing.

`setup` logs every change as one structured line (layer, from, to, gap counts). No engine API
changes layers: it is deployment configuration with a cost, not a caller action (D60).

## 4. Ingestion

### 4.1 Boundaries

Each layer is entered at exactly one boundary, decided inside the transaction that completes
the previous stage, while holding a share lock on the deployment's layer row and a lineage
advisory lock (keyed by `doc_id`):

**Claims boundary — `embed_chunk` completion.** With all layers on and coverage complete it
enqueues what it enqueues today: one `extract_claims` job per chunk, or the D90 empty path for
a version without chunks. Otherwise:

| Claims state | Action |
| --- | --- |
| disabled | Record a claims gap and a facts gap; enqueue nothing. |
| enabled, `backfilling`, and an earlier version of the lineage has a claims gap | Record a claims gap and a facts gap; enqueue nothing (it waits its turn, §5.2). |
| enabled otherwise | Enter claims as today. |

**Facts boundary — the extract barrier** (`complete_chunk_extract` when every chunk of the
version is grounded) and the D90 empty path. With all layers on and coverage complete it
opens the E3 normalisation fan-out exactly as today. Otherwise:

| Facts state | Action |
| --- | --- |
| disabled | Record a facts gap; open the **claims tail** (§4.2). |
| enabled, `backfilling`, and an earlier version of the lineage has a facts gap | Record a facts gap; open the claims tail. |
| enabled otherwise | Enter facts as today (stamping the version's own facts gap `entered_at`, if one exists). |

Stages after a boundary need no check: their work is created only by the chain that the
boundary opened.

### 4.2 The claims tail and the two reconcile modes

Today `embed_claim` is opened only after fact adjudication, and `reconcile` performs both
testimony currency (a claims job: claims of content a living document dropped stop being
current testimony, evidence lifecycle §3) and the fact steps of evidence lifecycle §5. When
facts are not entered, the facts boundary opens the **claims tail** instead:

- `embed_claim` for the version (unchanged handler);
- `reconcile` at component version **`reconcile-2026.07:claims`**, which performs the
  deleted-testimony retirement and the currency transitions of evidence lifecycle §5 step 2
  and nothing else: no recount, no closure, no flags, no `evidence_changed`, no profile
  refresh, no window review, no `label_relation`.

The full reconcile (`reconcile-2026.07`, opened by `adjudicate_supersession` as today) gains
one rule: it **treats the currency transitions recorded by the same version's claims-only
run as its own** — the union its retry path already applies to its own reconciliation id. A
fact supported only by a claim that the claims-only run retired is therefore recounted and
closed when the facts layer later reaches the version.

With all layers on, the chain, every component version and the readiness list are
unchanged.

### 4.3 Held work

A worker never claims a work row whose `layer_of(stage, component_version)` is disabled. The
row stays `pending` with no attempt spent and runs if the layer is switched back on. Nothing
rewrites queued rows when a layer is switched off. `remember ops inspect` reports held rows
per layer.

### 4.4 Readiness

`PipelineReadinessCatalog` expects the stages of the enabled layers, in today's order, with
`reconcile` at the full version when facts are enabled and at the claims version when only
claims are. The capability checks (`p1`, `live_graph`, `p3`) are unchanged: they check
configuration and publication, not data. Consequences:

- A facts-off deployment reports a version ready after `convert`, `structure`, `chunk`,
  `embed_chunk`, `extract_claims`, `ground_claims`, `embed_claim` and `reconcile`
  (claims version); a claims-off deployment after the first four.
- A version with a gap in a layer that is switched on is not ready until the backfill
  processes it.
- With every layer on, the report is byte-identical to today's.

### 4.5 Metering

Every model call is metered per stage in `cost_ledger` as today. The managed text meter
stamps the `processing_profile_id` from the layers enabled at measurement time:

| Enabled layers | `processing_profile_id` |
| --- | --- |
| sources, claims, facts | `doc-text-standard-v1` (unchanged) |
| sources, claims | `doc-text-claims-v1` |
| sources | `doc-text-sources-v1` |

`profile_complete` on the outcome receipt keeps its definition — the version is ready — and so
follows §4.4. The managed cloud's rate registry must know a profile id before a managed
deployment uses it; how the cloud prices a later backfill is outside the engine (D60).

## 5. Switching a layer on later

### 5.1 Gaps

A gap row says "this live version has not been processed by this layer". Boundaries write
gap rows (§4.1); nothing else creates them. The backfill stamps `entered_at` when it enters
the version, and the row is deleted when the layer completes for the version:

- **claims complete** — the extract barrier is ready (every chunk grounded) or the version had
  no chunks;
- **facts complete** — the full reconcile succeeded.

Deleting or forgetting a version deletes its gap rows. All-on deployments never write a gap
row.

### 5.2 Lineage-ordered backfill

Reuse across versions only looks at earlier versions of a lineage (Selection reuse requires a
smaller `version_no`; Claimify and claim attachment follow from it). A backfill therefore
advances **each lineage one version at a time, oldest first**, and runs different lineages in
parallel. Two rules, both evaluated under the lineage lock:

- **Advance.** When a layer completes for a version while the layer is `backfilling`, the
  completing transaction deletes the version's gap and then takes the lineage's oldest gap
  that has not been entered. It enters that version if every earlier version of the lineage
  that completed the sources layer has completed this layer. For facts, the candidate must
  also have claims complete; if its claims are still pending, its own facts boundary will
  enter facts when they complete.
- **Defer.** A boundary that finds an earlier gap of its layer in the lineage records a gap
  instead of entering (§4.1), so a fresh version waits behind its predecessors instead of
  being extracted before them.

**Entering** a version means enqueueing exactly what its boundary would have enqueued, on the
**backfill lane**: the per-chunk `extract_claims` jobs (or the empty path) for claims; the E3
normalisation fan-out for facts. Earlier versions that never completed the sources layer (a
failed conversion) are ignored; a version whose layer work dead-letters stops its lineage's
backfill at that version until it is replayed, which `remember ops inspect` shows.

### 5.3 Seeding and completion

`setup` seeds a layer switched on (§3.3) by running **advance** once for every lineage with an
unentered gap of that layer, in bounded batches. From then on the chain drives the backfill:
each completion advances its own lineage. No polling job and no new stage exist.

When a transaction deletes the last gap row of an enabled layer (on completion or on version
deletion), it sets the layer's coverage to `complete` in the same transaction.

### 5.4 Lanes

Backfill work runs on the backfill lane (orchestration §3): it never delays fresh ingestion and
has its own budget (`WorkLedgerSettings.budgets`). A self-host worker serves its stage's
backfill route whenever its steady route has no due row.

### 5.5 Rule 4: what a backfill pays for

A backfill re-enters only the layer that was missing. It never re-chunks, never re-embeds a
chunk, and changes no reuse input: chunking (D58), `extraction_input_hash` and
`reuse_identity_hash` (D56, D140 text origin, fixed when the chunk was created), embedding
carry-forward (D80) and the D122 Claimify window are exactly those of normal ingestion,
because the backfill runs the ordinary handlers in the ordinary order. A lineage backfilled
oldest first pays for exactly what always-on ingestion of the same versions would have paid:
for the first version everything, for each later version its changed chunks, their immediate
same-section neighbours and the known D122 window. Claim embeddings skip claims that are
already embedded.

### 5.6 What a facts backfill produces

E3 reads only current testimony. For snapshot lineages — version succession changes no
claim's currency, which is D140's model for editions — the facts built later are the facts
always-on processing would have built. For living lineages, content removed before facts were
switched on never becomes a fact: **the fact history of a living lineage starts when the
facts layer covers it.** This is a declared boundary; reconstructing it would mean replaying
testimony currency backwards, which nothing else in the engine does.

## 6. Switching a layer off

### 6.1 Effects

- Boundaries stop entering the layer (§4.1); new versions get gaps.
- Queued work of the layer is held (§4.3).
- Retrieval stops serving the layer immediately (§7); readiness stops expecting it (§4.4).
- The layer's data is kept. It is not deleted, tombstoned or served. Switching the layer back
  on reuses it: versions that completed the layer have no gap and are not re-processed.

### 6.2 Running work

A row claimed before the switch finishes; its follow-ups are created and held. Nothing is
cancelled mid-attempt.

### 6.3 Deletion and forgetting are never gated

Document deletion (D135) and hard forget remove a document's contribution from every layer,
whatever the layer state — including fact closure and profile refresh — so data kept while a
layer is off never outlives its source. They also delete the version's gap rows.

## 7. Retrieval

### 7.1 Served layers and the per-request selection

By default a request reads every served layer. A caller may narrow it with **`layers`**: a
non-empty set of `sources`, `claims`, `facts`. It is accepted by:

- `POST /operations/{name}` (query parameter, repeatable), and the SDK methods
  `claims_and_sources_context`, `combined_context` and the generic operation call;
- the sandbox execution routes `POST /query/sql`, `POST /query/sql/explain` and the saved-query
  run route, and their SDK methods;
- `remember mcp --layers` (engine mode): applied to every operation and sandbox call of the
  session, and tools that read only excluded layers are not listed;
- `remember query text --layers` and `remember operations run --layers`.

It is deliberately **not** an argument of the agent tools: tool schemas, the query-surface
manifest and the answer-tool catalogue are unchanged, so an evaluation can run an unchanged
agent with a narrower layer set.

Rules, applied by one gate in the query engine:

1. A requested layer the deployment does not serve → **409 `layer_unavailable`** naming the
   layer and its status (`off`).
2. A selection that excludes every layer the operation reads → **422 `invalid_parameter`**.
3. Without `layers`, the operation reads its layers that are served.

### 7.2 Which layers each read uses

| Read | Layers | When a needed layer is not served |
| --- | --- | --- |
| `search_chunks`, `adjacent_chunks`, document list/search, section history, document references, `graph/citation-path` | sources | — (always served) |
| `search_claims` | claims | 409 |
| `resolve`, `lookup_relations`, `lookup_observations`, `transcript_relation`, `hydrate_relation`, `graph/neighborhood`, `graph/path` | facts | 409 |
| `facts_context`, `resolve_entity` (operations) | facts | 409 |
| `claims_and_sources_context` | sources, claims; facts when `entity_ids` is given (entity scoping reads mentions) | serves the available channels with a notice; with `entity_ids` and facts unserved, 409 |
| `combined_context` | sources, claims, facts | serves the available sides with notices; a side with no layer left is an empty envelope |
| Sandbox views and functions | per view (§7.5) | 409 before execution |

### 7.3 Saying what is missing

`Envelope` gains `layers: tuple[LayerNotice, ...]`, **omitted from the JSON when empty**, so a
deployment that serves everything returns byte-identical responses:

```python
class LayerNotice(BaseModel):
    layer: Literal["sources", "claims", "facts"]
    status: Literal["off", "excluded", "backfilling"]
```

- `off` — the operation would have read the layer, but the deployment does not serve it.
- `excluded` — the request's `layers` left it out.
- `backfilling` — read, but older versions may not have been processed yet (§5).

An envelope whose every layer is `off` (the facts side of `combined_context` in a facts-off
deployment) also carries a `boundary` negative explaining that the layer is not ingested and
pointing to `claims_and_sources_context`. An envelope whose layers were `excluded` carries no
negative: the caller chose it. The negative-kind taxonomy is unchanged (it is frozen).

The 409 body is `{"code": "layer_unavailable", "layer": ..., "status": "off", "message": ...}`.
The Python SDK raises `LayerUnavailable` (a `MemoryApiError` with `layer` and `status`); the
TypeScript client mirrors it; MCP returns a tool error with the same code.

### 7.4 Discovery

- `GET /deployment` adds `processing_layers: {"claims": state, "facts": state}` with states
  `on`, `off` and `backfilling`, **omitted when both are `on`**.
- `GET /operations`, the `tools` map of `GET /deployment` and the MCP catalogue (in-process
  and engine mode) list only operations and tools whose layers are served: with facts off,
  `facts_context` and `resolve_entity` are not listed; `combined_context` stays (it answers
  from what is served and says so). With every layer on, all three are unchanged.

### 7.5 The query sandbox

Each sandbox view and function belongs to one layer; a view that reads several belongs to the
highest:

- **sources:** `documents_live`, `document_versions_visible`, `sections_live`, `chunks_live`,
  `chunks_all_versions_live`, `document_effective_periods_live`, `document_crossrefs_live`;
  `semantic_chunks`, `lexical_chunks`, `fetch_chunk_bodies`, `versions_in_scope`,
  `effective_intervals`, `graph_citation_path`;
- **claims:** `claims_visible_history`, `claims_canonical`, `claims_live`,
  `claim_occurrences_live`, `testimony_currency_events_visible`; `semantic_claims`,
  `lexical_claims`;
- **facts:** every entity, mention, fact, evidence, contradiction, graph and page view, and
  `changes_visible` (it reads fact and claim events); `semantic_facts`, `semantic_entities`,
  `facts_as_of`, `graph_neighborhood`, `graph_path`, `fact_in_scope_support`.

Validation rejects a statement (including a saved query) that references a view or function of
a layer that is unserved or excluded, with `layer_unavailable` (HTTP 409), before any planning.
The mapping lives in code beside the view contracts, **outside the hashed query-surface
manifest**, so the manifest and its hash are unchanged. `/query/space` is unchanged.

### 7.6 Plane K and P3

Plane K compiles pages about entities and predicates from facts, so it runs only while facts
are served; the dispatcher does not compile otherwise, and the page views are facts views
(§7.5). The P3 corpus filesystem omits its `entities/` tree when facts are not served; its
`documents/` tree is unchanged.

## 8. Compatibility

- **No configuration, no change.** Without a layer row: no gap rows, no held work, the same
  chain, component versions and readiness list; responses omit every new field; the MCP
  catalogue, tool schemas, query-surface manifest and answer-tool catalogue are unchanged.
- **LoCoMo** runs with every layer on and is unaffected: its readiness gate, surface manifest
  hash and catalogue hash stay as pinned.
- **Clients.** New response fields are optional and omitted unless a layer is not fully
  served; the new request parameter is optional. The Python SDK and TypeScript client gain it
  together (D141 parity).

## 9. Acceptance tests

The implementation is accepted when these hold (in CI, against PostgreSQL where the behaviour
is database-level):

1. **Defaults.** With no layer configuration the readiness stage list, component versions and
   a representative operation response are identical to the pre-D143 output; no gap row or
   layer row is written.
2. **Facts off.** A version ingests to `ready` with the claims-version reconcile; no
   `normalize_relations`, `adjudicate_*`, `label_relation` or `review_fact_windows` row exists
   and no E3 provider call is made; claim currency still transitions for a living lineage that
   drops a paragraph; claim search returns only current testimony.
3. **Claims off.** A version is ready after `embed_chunk`; no claims or facts rows exist.
4. **Retrieval with claims off never touches claim relations.** A statement-capture test
   asserts that `claims_and_sources_context` and `combined_context` issue no SQL that reads a
   claim table or view; `search_claims` returns 409; the envelope carries the `off` notice.
5. **Explicit selection.** `layers=sources` on `claims_and_sources_context` reads no claim
   relation and reports `claims: excluded`; requesting an unserved layer is 409; a selection
   that excludes everything the operation reads is 422.
6. **Rule 4 backfill.** Two versions of one lineage (distinct source dates, stable names; a
   one-paragraph edit inside a multi-chunk section with untouched sections before and after)
   ingest with claims off; claims are switched on. The backfill makes no `chunk` or
   `embed_chunk` work and no chunk-embedding call; v1's Selection runs for every chunk; v2's
   Selection runs only for the edited chunk and its same-section neighbours; every other v2
   chunk reuses v1's claim ids; the per-stage provider call counts equal those of the same two
   versions ingested with every layer on. The existing Rule 4 tests
   (`test_reuse_lifecycle.py`, `test_d140_text_origin_pg.py`) still pass unchanged.
7. **Ordering.** During a claims backfill, a new version of a lineage whose earlier version
   has a gap records a gap and is entered only after its predecessor completes.
8. **Facts later.** A facts-off deployment switched to facts on builds facts for a snapshot
   lineage equal to always-on processing, and the full reconcile closes a fact whose only
   support was retired by an earlier claims-only reconcile.
9. **Off then on.** Switching facts off holds queued facts rows (no attempt spent) and makes
   readiness stop expecting them; switching back on runs them; coverage returns to
   `complete` when the last gap is gone.
10. **Deletion.** Deleting a document while facts are off still closes its facts and deletes
    its gap rows.

## 10. Non-goals and scope boundaries

- **Per-document, per-source-kind or per-ingest-call layer selection.** Coverage would become
  per-document and retrieval could not disclose it per result; use separate deployments.
- **Deleting a disabled layer's data.** Switching off never deletes; deletion is D135's and
  hard forget's job.
- **A deployment-level retrieval default different from the enabled layers.** Narrow per
  request or per MCP connection instead.
- **Reconstructing living-lineage fact history from before the facts layer covered it** (§5.6).
- **A runtime API that changes layers.** Layers are deployment configuration (§3.3).

## 11. Worked example

The policy library from the introduction, with `REMEMBERSTACK_SELFHOST_FACTS_LAYER=false`:

1. *Travel Policy* edition 1 is ingested. Its chain runs sources and claims; at the extract
   barrier the facts boundary records a facts gap and opens `embed_claim` and the claims
   reconcile. Readiness reports it ready after the claims reconcile.
2. Edition 2 changes one paragraph of § 4. Selection runs for that chunk and its same-section
   neighbours; every other chunk reuses edition 1's claims. Facts gap again.
3. An agent calls `combined_context("per-diem allowance")`. The `claims_and_sources` side
   answers from claims and chunks; the `facts` side is empty with a `boundary` negative and
   `layers: [{"layer": "facts", "status": "off"}]`. The MCP catalogue does not list
   `facts_context`.
4. The operator sets the facts variable to `true` and restarts. `setup` marks facts
   `backfilling` and advances every lineage: edition 1 enters facts on the backfill lane. When
   its full reconcile completes, the lineage advances to edition 2. When the last gap is gone,
   facts coverage is `complete` and the `backfilling` notices disappear.
