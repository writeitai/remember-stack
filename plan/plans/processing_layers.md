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
| WP-PL.1 | Vocabulary and state: `ProcessingLayer`, `RECONCILE_CLAIMS_VERSION`; settings `REMEMBERSTACK_SELFHOST_CLAIMS_LAYER` / `_FACTS_LAYER` (compose forwards them); migration for `deployment_processing_layers` and `processing_layer_gaps` with its five pinned contract surfaces; a spine catalog for the configuration (no row = both on), the layer fence, gaps and derived coverage; `setup`'s reconcile and seed (§3.4). | §2, §3 | — |
| WP-PL.2 | Boundaries and the claims tail: claims boundary at `embed_chunk` completion, facts boundary at the extract barrier and the D90 empty path, inside the completing transaction under the fence and lineage lock; claims-mode reconcile (no label, no window review); the living supersession guard. | §4.1–§4.2 | WP-PL.1 |
| WP-PL.3 | Window-review hold, readiness by enabled layers (reconcile at either version; discharged → `skipped`), `processing_profile_id` and measured-profile outcomes, `remember ops inspect` layer section (state, open/entered/discharged gaps). | §4.3–§4.5 | WP-PL.1 |
| WP-PL.4 | Backfill: advance on completion and deletion, defer at the boundary, snapshot vs living targets with discharge, seeding from `setup`, backfill lane served by self-host workers when their steady route is idle; deletion and hard forget delete gaps. | §5, §6.3 | WP-PL.2 |
| WP-PL.5 | Retrieval gate: channels and mandatory dependencies per read, the `layers` selection on operations and sandbox execution, 409/422 rules, `boundary` instead of `known_empty` when a channel was not consulted, the D140 probes following the selection, sandbox layer map with existing codes and `layer_backfilling` warnings, plane-K and P3 gating, `GET /processing-layers`, operations/tools/MCP catalogue filtering. | §7 | WP-PL.1 |
| WP-PL.6 | Client surfaces: Python SDK (`layers=`, `processing_layers()`, `LayerUnavailable`), CLI (`--layers`), `remember mcp --layers`; OpenAPI export; TypeScript client under D141 parity; docs site pages for configuration and retrieval (D66). | §7, §8 | WP-PL.5 |
| WP-PL.7 | Acceptance tests §9, including byte-identical defaults (descriptors, manifest hash, LoCoMo pins), the Rule 4 snapshot backfill with provider call counts against an always-on run, the living backfill, and the race schedules. | §9 | WP-PL.2–6 |
