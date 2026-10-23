# Processing layers — delivery (D143)

Build order for
[D143](../../decisions.md#d143-processing-layers-can-be-switched-off-per-deployment-and-per-request)
as specified in [the processing layers design](../designs/processing_layers_design.md).
Architecture lives in the design; this file only sequences it.

**Where the work lands.** The design merges on its own pull request after review. The
implementation follows in one pull request from the updated `main`, reviewed again as a whole,
with every work package below and the design's §9 acceptance tests.

**Operational constraint.** Development and tests use isolated databases only. No work package
connects to a running benchmark host, its database or deployment, and no work package makes
paid model calls; tests use the recorded or fake model providers.

| WP | Scope | Design | Depends on |
| --- | --- | --- | --- |
| WP-PL.1 | Vocabulary and state: `ProcessingLayer`, `layer_of(stage, component_version)`, `RECONCILE_CLAIMS_VERSION`; settings `REMEMBERSTACK_SELFHOST_CLAIMS_LAYER` / `_FACTS_LAYER` (compose forwards them); migration for `deployment_processing_layers` and `processing_layer_gaps` with its five pinned contract surfaces (catalog contract tables and constraint counts, migration chain, head, table count, CI upgrade gate); a spine catalog that reads the state (no row = all on, complete), writes gaps, and performs `setup`'s reconcile (§3.3). | §2, §3 | — |
| WP-PL.2 | Boundaries and the claims tail: the claims boundary at `embed_chunk` completion and the facts boundary at the extract barrier and the D90 empty path, both inside the completing transaction under the layer-row share lock and the lineage lock; claims-only reconcile mode; the full reconcile's union with the claims-only run's transitions. | §4.1–§4.2 | WP-PL.1 |
| WP-PL.3 | Held work and readiness: the claim gate on disabled layers; readiness expected stages from the enabled layers; `processing_profile_id` from the enabled layers; `remember ops inspect` layer section (state, coverage, gap counts, held rows). | §4.3–§4.5 | WP-PL.1 |
| WP-PL.4 | Backfill: advance on completion, defer at the boundary, seeding from `setup`, coverage completion, backfill lane served by self-host workers when their steady route is idle; deletion and hard forget delete gap rows. | §5, §6.3 | WP-PL.2 |
| WP-PL.5 | Retrieval gate: per-operation layer declarations, the `layers` selection on operations and sandbox execution, 409 `layer_unavailable` / 422 rules, `Envelope.layers` notices (omitted when empty), the facts boundary envelope, the sandbox view/function layer map, plane-K and P3 gating, `GET /deployment` `processing_layers` (omitted when all on), MCP catalogue filtering. | §7 | WP-PL.1 |
| WP-PL.6 | Client surfaces: Python SDK (`layers=`, `LayerUnavailable`), CLI (`--layers`), `remember mcp --layers`; OpenAPI export; TypeScript client under D141 parity (inventory, generated contracts, fixtures, hand-written facade); docs site pages for configuration and retrieval (D66). | §7, §8 | WP-PL.5 |
| WP-PL.7 | Acceptance tests §9, including the Rule 4 backfill regression with provider call counts compared against an always-on run, and the unchanged LoCoMo protocol pins. | §9 | WP-PL.2–6 |
