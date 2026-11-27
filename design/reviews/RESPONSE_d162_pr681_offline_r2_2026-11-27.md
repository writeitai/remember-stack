# Response to PR #681 offline grid review, round 2

Reviewed head `75542dc72`. The review reported no P0 findings.

## P1

- **Extraction donors.** The source queries return one row by hash. A local
  probe found that `Orchard` hides a matching `Terrace` row in both stages.
  The design now requires Selection and Claimify to try eligible donors in
  deterministic order until one verifies and remaps. A shared digest of the
  exact E2 slots finds donors across layouts, including pre-D162 fitting
  grids. The release regression requires both stages to skip the rejected
  donor, reuse the verified result and make zero Selection and Claimify calls
  for the target.
  [Selection query](../../src/rememberstack/spine/selection_catalog.py#L116-L125) ·
  [Claimify query](../../src/rememberstack/spine/claim_catalog.py#L425-L447) ·
  [Reuse design](../../plan/designs/split_oversized_chunks_design.md#2-coordinates-and-reuse)
- **Offline parity.** Removing the typed `section_title` from E1's payload
  changed E2's admitted location pairs with the embedding bytes and grid held
  fixed. The offline check compares those ordered pairs from the stored
  baseline and the candidate's prepared payload. The candidate builds its
  own passage facts. The release check includes the dropped-title regression.
  [E1 payload](../../src/rememberstack/workers/e1.py#L551-L567) ·
  [E2 validation](../../src/rememberstack/workers/e2.py#L1691-L1737) ·
  [Offline check](../../plan/designs/split_oversized_chunks_design.md#5-offline-benchmark-and-release-checks)

## P2

- **Text origin.** The lifecycle summary now includes D162's uniquely
  verified target-and-neighbour slice match and links to D140 for eligibility,
  date ordering and fallback.
  [Lifecycle summary](../../plan/designs/evidence_lifecycle_design.md#6-content-addressed-reuse-d56--cost-proportional-to-the-edit) ·
  [D140 date rule](../../plan/designs/effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time)
- **Implementation release checks.** The implementation PR records fixture
  digests, exact restore and check commands, and the results of the
  frozen-grid comparison, hosted qualification and rollout rehearsal. It
  updates the E1 acceptance YAML. The rehearsal checks previous-release SQL
  and active saved queries on the expanded schema, manifest cutover,
  late-worker fencing and both rollback paths.
  [Release checks](../../plan/designs/split_oversized_chunks_design.md#5-offline-benchmark-and-release-checks) ·
  [Setup and manifest publication](../../src/rememberstack/profiles/selfhost.py#L1234-L1262) ·
  [Acceptance YAML](../../plan/implementation_evals/eval_checks/e1_chunks_whole_blocks_no_overlap.yaml#L13-L23)

## Nit

The reuse citations now cover the stable header fields, the ineligibility
marker and both key builders.
[Header facts](../../src/rememberstack/workers/e1.py#L768-L791) ·
[Dated key](../../src/rememberstack/workers/e1.py#L678-L731) ·
[Date-free key](../../src/rememberstack/workers/e1.py#L656-L675)

## Verification

Provider-free probes of the source SQL and E1 payload reproduced both P1
findings. The focused PostgreSQL 18 run had 70 passes and the expected D122
window failure. Local links and `git diff --check` passed. The frozen-grid
comparison, hosted qualification and rollout rehearsal are implementation
release checks.
