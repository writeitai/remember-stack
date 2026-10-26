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
- **Configured deployment.** One that has a row in `deployment_processing_layers` — every
  deployment whose operator has ever set a layer variable to a non-default value. A deployment
  without a row takes exactly today's code paths everywhere.
- **Boundary.** The single point in the chain where a version enters a layer (§4.1).
- **Entered.** A version has entered a layer when its layer work was enqueued.
- **Frontier.** The point after which a version's layer products can be reused by later
  versions of its lineage (§5.1). It is what lineage ordering waits for. It is *not* readiness
  and not receipt completeness, which also wait for the layer's tail stages (§4.5).
- **Gap.** A record that a live version has not (yet) entered a layer (§5.1).
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
  lane          processing_lane NOT NULL,  -- route used when the version is entered (§4.1)
  entered_at    timestamptz,   -- advance entered the version (§5.2)
  discharged_at timestamptz,   -- superseded in a living lineage; never processed (§5.3)
  PRIMARY KEY (deployment_id, version_id, layer),
  CHECK (entered_at IS NULL OR discharged_at IS NULL)
);
CREATE INDEX ix_layer_gaps_open
  ON processing_layer_gaps (deployment_id, layer, doc_id)
  WHERE discharged_at IS NULL;
```

**No row in `deployment_processing_layers` means both layers enabled.** Existing deployments
need no data migration. A gap row is inserted once per version and layer and keeps its lane
through discharge and reopening until it is deleted. A gap is *open* while `discharged_at` is null; a layer is backfilling
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
   (layer, from, to, open gap count). Once written, the row stays — a deployment that has been
   configured keeps the configured code paths even if both layers are switched back on. Nothing
   else is written; switching off needs no other action (§6).
3. After committing it **seeds** every enabled layer that has open gaps (§5.4). It does this on
   every run, so an interrupted seed resumes on the next start.
4. When the facts setting changed, it builds and publishes a P3 snapshot for the new served set
   (§7.6) before the mounts are served.

No engine API changes layers: it is deployment configuration with a cost, not a caller action
(D60).

## 4. Ingestion

### 4.1 Boundaries

Each layer is entered at exactly one boundary, decided inside the transaction that completes
the previous stage, under the layer fence and the D135 lineage lock (§3.3):

- **claims boundary** — `embed_chunk` completion (today: one `extract_claims` job per chunk, or
  the D90 empty path for a version without chunks);
- **facts boundary** — the extract barrier (`complete_chunk_extract` when every chunk of the
  version is grounded) and the D90 empty path (today: the E3 normalisation fan-out, or the empty
  observation flush).

**A deployment without a configuration row** enters every layer exactly as today. No gap is
written and no check runs beyond reading that the row is absent, so the chain, its work rows
and its component versions are unchanged.

**A configured deployment** decides by one procedure:

1. If the layer is **disabled**: do not enter. Record a gap (at the claims boundary a claims gap
   and a facts gap) unless the version or its lineage is deleted. The facts boundary opens the
   **claims tail** (§4.2).
2. If the version or its lineage is **deleted**: enter as today, so the chain's reconcile retires
   the late work (D135); record no gap.
3. Otherwise record a gap for the version (lane `steady`, or the lane of the completing work)
   and run **advance** for the lineage (§5.2) in the same transaction. Advance either enters this
   version — stamping its gap entered — or leaves it waiting behind a predecessor that has not
   reached its frontier. What a facts boundary does for a version that does not enter facts is
   governed by §4.3.

Gaps recorded in step 1 carry lane `backfill`: the version will only be processed by a later
backfill.

So in a configured deployment the boundary and the backfill share one admission rule: a version
enters a layer only when every earlier version of its lineage has reached that layer's frontier,
whether the earlier version was entered by a boundary, by a backfill, or before a switch.
Stages after a boundary need no check: their work exists only if the boundary opened it.

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
**no `label_relation` and no fact-window review**. Facts that already exist (from a period when
facts were on) therefore keep their support counts and closures exact while facts are off. This
is a deliberate cost exception: when such facts exist, the profile refresh can make embedding
calls while the facts layer is off; when no facts exist it does nothing.

When the facts layer later reaches a version that ran the claims tail, the ordinary facts chain
ends in the full reconcile at `reconcile-2026.07` — a different idempotency key — which opens
`label_relation` and the window review as today.

**Living supersession guard (configured deployments).** A reconcile retires a `living` lineage's
claims that are not attached to the lineage's current version. In a configured deployment the
current version's occurrences may not be complete yet (§5.1) — it may still be converting,
chunking, deferred behind a predecessor, or claims may be disabled — and retiring against them
would retire claims the current version will reuse; reuse does not restore currency. So the
supersession step reads the current version, checks that it is this reconcile's own version or that
its occurrences are complete, computes the stale set and
applies the transitions **in one transaction under the lineage lock**; otherwise it skips the
step. The current version's own reconcile computes supersession lineage-wide when it runs. In a
deployment without a configuration row the step runs exactly as today.

### 4.3 The claims tail waits for earlier fact work (configured deployments)

The E3 handlers do not filter on currency: they normalise and adjudicate every claim of the
version they are given. In ordinary ingestion that is safe because a version's facts are built
before any later version's reconcile can retire its claims. A configured deployment keeps exactly
that ordering instead of filtering testimony: a version's **claims tail is opened only when no
earlier version of its lineage has fact work in flight** — that is, every earlier live version that
completed the sources layer has reached its facts frontier, or never entered facts (an unentered or
discharged facts gap). Until then the version waits with its facts gap, and nothing is enqueued for
it.

So for a version that does not enter facts at its facts boundary (facts disabled, or enabled but
deferred behind an earlier unentered gap), the boundary opens the claims tail at once if the
lineage has no fact work in flight, and otherwise leaves it to advance (§5.2 step 5), which opens it
when the last earlier fact work reaches its frontier — including for a version whose facts gap is
discharged, in the same advance, because a newer version of its living lineage became current. Opening a claims tail is idempotent (its two work
rows have fixed idempotency keys), so advance may re-issue it safely.

Consequences: an older version's fact work — including work draining after facts were switched off
(§4.4) — always runs while its claims are still current; the later version's reconcile then retires
and closes exactly as always-on processing would, with the same history. No fact is created from
retired testimony and no suppression state exists. Snapshot lineages never retire testimony by
succession, so for them the wait only orders work. A deployment without a configuration row keeps
today's chain unchanged.

### 4.4 Switching off drains; it never holds

A version that entered a layer finishes that layer: work already queued for it is claimed and runs
as normal after the layer is switched off. Every version therefore ends either past its frontier
in a layer or with a gap — never half-processed — and no claims tail can be stranded behind held
fact work. Only one kind of work is held: **`review_fact_windows` rows are not claimed while facts
are disabled.** They are lineage-level reviews that declarations and reconciles queue outside the
version chain; they run when facts are switched back on. A caller that needs spend to stop at once
uses the existing lane budgets (orchestration §4).

### 4.5 Readiness and receipts

**Serving readiness.** `PipelineReadinessCatalog` expects the stages of the enabled layers, in
today's order:

| Enabled layers | Expected stages |
| --- | --- |
| sources, claims, facts | today's twelve, unchanged |
| sources, claims | `convert`, `structure`, `chunk`, `embed_chunk`, `extract_claims`, `ground_claims`, `embed_claim`, `reconcile` |
| sources | `convert`, `structure`, `chunk`, `embed_chunk` |

With facts disabled, `reconcile` is satisfied by a succeeded row at **either** component version,
so a version that finished the full pipeline before facts were switched off stays ready. A version
whose gap was discharged (§5.3) reports that layer's stages as `skipped`. The capability checks
(`p1`, `live_graph`, `p3`) are unchanged. A version with an open gap in an enabled layer is not
ready until it is processed.

**Metering.** Every model call is metered per stage in `cost_ledger` as today. The managed text
meter stamps the `processing_profile_id` from the layers enabled at measurement time:

| Enabled layers | `processing_profile_id` | Stages its receipt requires |
| --- | --- | --- |
| sources, claims, facts | `doc-text-standard-v1` (unchanged) | today's twelve |
| sources, claims | `doc-text-claims-v1` | the eight above (`reconcile` at either version) |
| sources | `doc-text-sources-v1` | the four above |

The outcome receipt certifies **the measured profile**, not the current configuration and not a
frontier: `profile_complete` is true only when every stage the measured profile requires succeeded,
with the completion time and dead-letter handling of today's receipts (for the standard profile,
exactly today's predicate). A version that can no longer complete its measured profile — it has a
gap in a measured layer that is now disabled, or a discharged gap — gets a `failed` outcome with
reason `processing_profile_changed`. Layers enabled after measurement are not part of the profile
and do not delay its receipt. Pricing, including of a later backfill, belongs to the managed cloud
(D60), whose rate registry must know a profile id before a managed deployment uses it.

## 5. Switching a layer on later

### 5.1 Gaps and frontiers

Boundaries are the only writers of new gap rows (§4.1). Two predicates are used, both read from
the work ledger, never from the mere absence of rows:

- A version's **occurrences are complete** when its `embed_chunk` row succeeded for its current
  representation and chunker generation (so its chunk grid exists and is final) and either its
  extract barrier is ready — every chunk's Selection and Claimify results are committed, so every
  claim occurrence of the version is attached — or that grid is empty. A version that has not run
  `embed_chunk` is never "complete with zero chunks".
- A version reaches a layer's **frontier** when its products are reusable by later versions:
  - **claims** — its occurrences are complete and its `embed_claim` succeeded, so every claim it
    carries, including claims later versions will reuse by id, has its vector;
  - **facts** — its full reconcile succeeded.

Reaching the frontier deletes the version's gap of that layer. The frontier is only the ordering
point: readiness and receipts additionally wait for `label_relation` and the other tail stages
(§4.5). Deleting or forgetting a version deletes its gaps (§6.3). In a facts-enabled lineage the
claims frontier comes after the version's fact adjudication (that is when `embed_claim` runs), so a
successor's extraction waits for it; this is the price of never admitting a successor whose reused
claims are not yet embedded.

### 5.2 One advance procedure

Reuse across versions only looks at earlier versions of a lineage (Selection reuse requires a
smaller `version_no`; Claimify and claim attachment follow from it). So a configured deployment
enters a layer **one version of a lineage at a time**, and different lineages run in parallel. One
procedure, **advance(lineage, layer)**, decides it. It runs under the layer fence and the lineage
lock, and is invoked by:

- a boundary that recorded a gap (§4.1);
- a version reaching the layer's frontier, and (for facts) a claims-mode reconcile succeeding;
- a version deletion and any current-version repointing it causes;
- `setup`'s seeding (§5.4).

Steps, in this order:

1. **Living lineages, always first — even when the layer is disabled:** reopen any discharged gap of
   the lineage's current version (reopening a claims gap also reopens its facts gap), so a version
   made current again by a deletion is never left discharged. A disabled layer enqueues nothing for
   the reopened gap; it waits for the layer to be switched on and seeded.
2. If the layer is disabled: for claims, stop; for facts, run step 5 (release owed claims tails)
   and stop. Otherwise, for living lineages, discharge every open, *unentered* gap of the lineage's
   other versions (§5.3). Entered gaps are never discharged: their work is running and must finish.
   **Discharge follows the layer dependency:** discharging a version's claims gap also records its
   facts gap as discharged (it will never extract, so it can never reach a facts boundary).
3. Choose the **target**: for a living lineage its current version, if it has an open unentered gap;
   for a snapshot lineage the oldest version with an open unentered gap.
4. Decide whether the target is **admitted**:
   - its sources layer is complete (otherwise its own claims boundary will record its gap and run
     advance again; a target whose sources work failed terminally stays unprocessed and visible in
     `remember ops inspect`);
   - every earlier live version of the lineage that completed the sources layer has reached this
     layer's frontier or has a discharged gap (otherwise that version's frontier runs advance again;
     this includes versions entered before a switch and still draining, and dead-lettered versions,
     which block until they are replayed or deleted);
   - for facts: its claim occurrences are complete (otherwise its facts boundary runs advance
     again), and, if it already has a claims tail (it waited, or facts were off), that tail's
     claims-mode reconcile has succeeded (otherwise that reconcile's completion runs advance again).
     A version's currency-changing claims-mode reconcile thus never overlaps its own admitted fact
     work.
5. **For facts, release owed claims tails** — after the discharges of step 2 and the admission
   decision of step 4, before anything returns. A version *owes a claims tail* when its claim
   occurrences are complete, it has no claims-tail rows yet, it **has never entered facts** — it
   has a facts gap whose `entered_at` is unset, open or discharged; a version admitted to facts
   earlier (an entered gap) or before the deployment was configured (no gap) keeps only its
   ordinary draining chain and never gets a claims-mode tail — and it will not enter facts now:
   facts are disabled, its facts gap is discharged (including by step 2 of this invocation), or
   (snapshot lineages) it waits behind an earlier unentered facts gap. The version admitted in step 4 never
   owes a tail — its ordinary facts chain embeds its claims and runs its full reconcile. The
   obligation is derived from the gap and work-ledger state of every version of the lineage, never
   only from the current version's gap, so discharging a superseded version's facts gap cannot
   strand its already-entered claims layer. Every owed tail whose earlier versions have no fact
   work in flight (§4.3) is opened now (idempotently).
6. If no target was admitted, stop.
7. **Enter** the admitted target: stamp its gap's `entered_at` and enqueue exactly what its boundary would
   have enqueued — the per-chunk `extract_claims` jobs (or the empty path) for claims, the
   ordinary E3 fan-out (or the empty observation flush) for facts — on the gap's recorded lane
   (`steady` for a version deferred during ingestion, `backfill` for a gap recorded while the layer
   was disabled). A zero-chunk target goes straight to its facts boundary in the same transaction.

Every gap is thus eventually entered by an event that is certain to happen — its own boundary, a
predecessor's frontier, a deletion, or `setup` — with no polling job and no new stage.

### 5.3 Which versions are processed

- **Snapshot lineages** — version succession changes no claim's currency, which is D140's model for
  editions — are processed **every version, oldest first**. Each version finds its predecessor's
  results exactly as in normal ingestion, and the facts built later equal those always-on processing
  would have built.
- **Living lineages** — the document *is* its current version — are processed **at their current
  version only**. Older unentered gaps are discharged (their stages read `skipped`), so content that
  only older versions carried is never extracted (claims) or turned into facts (facts): a living
  lineage's history in a layer it did not process starts at its current version.
- **Deletion that repoints a living lineage** to an older version reopens that version's discharged
  gaps (step 1, even while its layers are disabled), so it is processed like any current version. Its claims are extracted as ordinary
  ingestion of that version would extract them: reuse comes from earlier live versions only, and
  testimony of the deleted version is never reused (D135). Its chunks and their embeddings, which
  exist from the sources layer, are untouched.

### 5.4 Seeding and coverage

`setup` seeds an enabled layer by running advance once for every lineage with an open unentered gap
of that layer, in bounded batches, on every run. Coverage is derived: an enabled layer is
backfilling while an open gap of a live version exists, and complete otherwise (one indexed
`EXISTS`). Nothing stores or settles a coverage value.

### 5.5 Lanes

Backfill work is enqueued on the backfill lane (orchestration §3) with its own budget
(`WorkLedgerSettings.budgets`), and a self-host worker serves its stage's backfill route only when
its steady route has no due row. **Lane is queue priority and budget attribution for work rows; it
is not isolation of shared state.** Precisely:

- Every follow-up row that entered work opens takes the lane of the work row that opened it,
  including the rows opened outside the version chain: fact-application projection repair
  (`label_relation` on fact-application targets) and the window reviews requested by a reconcile.
  These no longer hard-code `steady`; `steady` remains the default where no work row is the cause.
  D67's promotion rule (a steady enqueue promotes a backfill duplicate of the same row) is unchanged.
- Fact applications are applied in each entity's canonical order by whichever observation-flush
  unit holds that entity's stream (D90/D118); a steady unit can therefore apply a backfill
  version's earlier application on a shared entity. Its model calls are metered on the route of the
  unit that applies it, and the projection repair it opens takes that unit's lane. A backfill that
  shares entities with fresh ingestion can thus spend on the steady route and wait on the same
  entity locks; the backfill budget bounds the rows the backfill lane claims, not every call its
  applications cause. Budgets are checked when a row is claimed, as today.

### 5.6 Rule 4: what a backfill pays for

A backfill re-enters only the layer that was missing. It never re-chunks, never re-embeds a chunk,
and changes no reuse input: chunking (D58), `extraction_input_hash` and `reuse_identity_hash` (D56,
D140 text origin, fixed when the chunk was created), embedding carry-forward (D80) and the D122
Claimify window are those of normal ingestion, because the backfill runs the ordinary handlers in
lineage order. A snapshot lineage processed oldest first pays what always-on ingestion of the same
versions would have paid: for the first version everything; for each later version, Selection for
its changed chunks and their immediate same-section neighbours, and Claimify additionally within the
known D122 window (a Rule 4 gap that D143 neither causes nor fixes). A living lineage pays for its
current version once. Claim embeddings skip claims that are already embedded.

## 6. Switching a layer off

### 6.1 Effects

- Boundaries stop entering the layer (§4.1); new versions get gaps.
- Versions that already entered the layer finish it (§4.4).
- Retrieval stops serving the layer immediately (§7); readiness stops expecting it (§4.5).
- The layer's data is kept. It is not deleted, tombstoned or served. Facts that exist keep exact
  support counts and closures through the claims-mode reconcile (§4.2). Switching the layer back
  on reuses everything: versions past the layer's frontier have no gap and are not
  re-processed.

### 6.2 Work outside the version chain

Window reviews are held while facts are off (§4.4). Review-queue verdicts that an operator applies
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

**Composition (refines D87/D114).** With every channel admitted, `combined_context` stays the exact
composition of its two children, each field-for-field equal to the direct call. With a narrowed
set, the `claims_and_sources` child equals a direct `claims_and_sources_context` call with the same
admitted channels (its nomination runs only those channels), and the `facts` child equals a direct
`facts_context` call when facts are admitted. A child none of whose channels is admitted is the
only kind of child the bundle may build itself, and it is symmetric: when facts are not admitted,
a fact-grain envelope with no results; when neither sources nor claims is admitted (for example
`layers=facts`), an evidence-grain envelope with no results. Either synthetic child carries the
`temporal_scope` its direct operation would report for the request's time selector, the bundle's
snapshot freshness, no truncation, and a `boundary` negative naming the unadmitted layers. Every
non-empty selection therefore has exactly one valid bundle. The canonical
four-operation registry, its descriptors and their manifest entries are unchanged; only the
*served* list (`GET /operations`, catalogues) is filtered (§7.4).

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
served; the page views are facts views (§7.5), and the knowledge mount is not published while facts
are not served.

The P3 corpus filesystem is an immutable published snapshot behind an atomically swapped pointer.
Each P3 snapshot records whether it was built with facts (with `entities/` and the entity indexes)
or without them; a snapshot built without facts omits `entities/` and every index or orientation
line that links to it, so the tree it exposes is self-consistent. The mount publisher activates the
**latest published snapshot built for the currently served set**, never an incompatible one; until
one exists it serves the empty-corpus placeholder. `setup` builds and publishes a matching snapshot
when the facts setting changes (§3.4), and a build that started under the old configuration and
finishes later is recorded for that configuration, so it is never activated for the new one. The
retained data is untouched. Without a configuration row every snapshot is built and served exactly
as today.

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
7. **Living backfill.** A living lineage with three versions ingested with claims off is processed
   at its current version only: older gaps are discharged and report `skipped`; current testimony
   afterwards equals the current version's claims. With v2 paused between `structure` and
   `embed_chunk` (already current), v1's reconcile skips supersession; after v2 completes, every
   reused unchanged claim is current. Deleting the current version after claims and facts were
   discharged on the older one reopens and processes it; the cycle repeats correctly after another
   off/on switch. Discharge older versions → finish the current one → switch both layers off →
   delete the current version while they are off → switch them on: the restored version's gaps
   reopen at the deletion, `setup` seeds it without any other event, coverage reads backfilling
   until its work completes, its stages are no longer `skipped`, and its chunks, D140 identities
   and chunk vectors survive with no chunk-embedding call.
8. **Ordering and races.** Claims on → v1 entered → claims off (v1 draining) → v2 ingested → claims
   on before v1 reaches its frontier: v2 records a gap and enters only after v1's frontier, with
   provider-call counts and claim-id reuse equal to an always-on run. A version crossing its boundary
   while its predecessor is backfilling waits the same way. A living lineage whose current version
   is still in the sources layer at seeding completes without another `setup` run, including when
   that version has no chunks. A dead-lettered head blocks until replay or deletion, and deleting it
   advances the successor; a living version whose deferred claims gap is discharged because a newer
   version became current never blocks that version's facts (its facts gap is discharged with it),
   and deleting the newer version reopens both of its layers; a late boundary for a deleted version
   writes no gap; a configuration
   change concurrent with a boundary decision serializes on the fence.
9. **Facts later.** A snapshot lineage processed with facts off and switched to facts on gets the
   facts an always-on run produces. In a facts-enabled living lineage, with v1 paused after its
   `embed_claim` and before its full reconcile, v2 reaches its facts boundary and waits, and v3
   becomes current: when v1's reconcile completes, v2's discharged facts gap still gets its claims
   tail, both of v2's gaps settle, v3 enters and becomes ready, and v2's Selection, Claimify and
   vectors are reused, not repeated — the first facts advance after v1's full reconcile opens v2's
   tail although that same invocation discharges v2's facts gap; the same holds with facts switched
   off and on while v2 waits.
   A version admitted to facts gets no claims tail; a version whose claims-mode reconcile is still
   pending when facts are switched on enters facts only after it succeeds — in both schedules a
   newer current version that drops claim C never lets C's fact stay open with zero support. With
   facts switched off while v1's admitted E3 work drains, a deletion that advances the lineage opens
   no claims-mode tail for v1: v1 keeps its ordinary chain, v2 cannot pass v1's claims frontier
   before v1's ordinary embedding hand-off, and C's fact closes after v2 removes it, with no
   extraction or embedding repeated.
   Facts on → off (a living version removes a fact's only claim
   inside a connector cycle that finalizes) → on leaves the fact closed. With facts enabled and v1
   entered but not yet normalised, a living v2 that removes claim C opens neither facts nor its
   claims tail until v1's full reconcile succeeded; v1 builds its fact from C (a fake normaliser
   yielding an open-window fact) and v2's reconcile then closes it, exactly as an always-on run does.
   The same holds when facts are switched off while v1's fact work drains. Every non-empty `layers`
   selection of `combined_context` returns a valid bundle (facts-only included).
10. **Lanes and inherited embeddings.** A facts backfill on a lineage that shares no entity with
    steady work leaves its projection repairs on the backfill lane with zero backfill budget; with a
    shared entity, the attribution follows §5.5. With claims enabled and facts off, v1 backfilled
    to grounding but its `embed_claim` pending (then dead-lettered) keeps v2 un-entered; after v1's
    replay, v2 enters and every claim v2 reuses has exactly one vector. Gaps recorded while a layer
    was disabled enter on `backfill`, gaps deferred during ingestion on `steady`, across a restart.
11. **Receipts.** A version measured under `doc-text-standard-v1` whose facts boundary falls after
    facts are switched off gets `failed` / `processing_profile_changed`; with the full reconcile
    succeeded but `embed_claim` or `label_relation` pending the receipt waits, and a dead letter there
    fails it as today; a version measured under `doc-text-sources-v1` succeeds when its four stages
    complete even if claims are switched on meanwhile; default receipts are unchanged.
12. **Deletion.** Deleting a document while facts are off closes its facts, rebuilds its document
    bindings and deletes its gaps, without switching facts on.
13. **Mounts.** An all-on P3 publication followed by switching facts off serves a snapshot without
    `entities/` (and no index line pointing to it) and no knowledge mount; a build that started before
    the switch and publishes after it is not activated.
14. **Off/on cost.** Provider-call fixtures for facts off record the profile-refresh exception only
    when facts exist.

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
