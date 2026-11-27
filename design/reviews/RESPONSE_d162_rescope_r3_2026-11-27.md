# Response to D162 rescope review, round 3

PR #681 at reviewed head `8bd115b7a`. Accepted all three P1 and both P2
findings.

## P1

- **Fit at preferred seams.** D80 chooses its header after measuring the
  normalized body. In a local mock-transport probe, a line seam with a compact
  header used 343 serialized bytes while the longer 128-scalar cut with a
  full header used 293. At a 332-byte cap the preferred seam fails. D162
  requires a full fit check for every proposed cut, including separator
  ownership, and falls back to the proved scalar cut if no preferred seam
  fits. The packing release check covers this reversal.
  [D80 renderer](../../src/rememberstack/core/embedding_input_policy.py#L204-L223) ·
  [D162 §1](../../plan/designs/split_oversized_chunks_design.md#1-size-contract-and-split-points).
- **Pending before CHUNK.** The catalog commits E0 readiness before the
  structure handler returns the CHUNK follow-up. D162 now enrolls passages as
  pending in that readiness transaction. D140 uses the enrollment even
  before a grid exists. Only the selected delivery can publish it. The check
  covers scoped reads before CHUNK enqueue and after a CHUNK dead letter
  with zero rows.
  [E0 ready commit](../../src/rememberstack/spine/document_catalog.py#L827-L852) ·
  [Structure handoff](../../src/rememberstack/workers/e0.py#L1626-L1644) ·
  [CHUNK follow-up](../../src/rememberstack/workers/e0.py#L2185-L2203) ·
  [D140 §3.7](../../plan/designs/effective_time_and_section_references_design.md#37-results-carry-their-period-and-pending-lineages).
- **Claims tail basis.** The embedder can return success on zero claims after
  reading the configured grid. Reconcile passes the configured generation
  to a predicate that treats a different stamp as obsolete. Both tails now
  require the selected published delivery and packing stamp for claim lookup,
  reconciliation and completion. The release check requires semantic claim
  search and current testimony after each tail finishes on a fresh split grid.
  [Claim embedder](../../src/rememberstack/workers/p1.py#L99-L113) ·
  [Reconcile call](../../src/rememberstack/workers/reconcile.py#L153-L158) ·
  [Stale testimony predicate](../../src/rememberstack/spine/lifecycle.py#L1449-L1471) ·
  [D143 §4.2](../../plan/designs/processing_layers_design.md#42-the-claims-tail-and-the-two-reconcile-modes).

## P2

- **Date inheritance and paid reuse.** E1 records the origin during CHUNK,
  before any extraction output is needed. D140's numbered rule and E1's
  summary now accept a verified target-and-context slice as a date match.
  The inherited date feeds E2. D162 checks each paid result separately, so a
  whole-chunk donor can supply a date even when its paid inputs differ or its
  extraction never ran.
  [Origin lookup and write](../../src/rememberstack/workers/e1.py#L167-L216) ·
  [D140 §5](../../plan/designs/effective_time_and_section_references_design.md#5-reuse-across-versions-text-origin-time) ·
  [E1 §7](../../plan/designs/e1_chunks_design.md#7-reuse-mechanics-d56-bound-here-amendments-a1a3).
- **LoCoMo baselines.** The registry exposes Full-v47 and the published
  94.09% run used retained v45 ingestion. The design and CLI example now
  name Full-v47. Historical parity uses the published run's recorded
  serving and reader-prompt revisions, copies of its attested stores and
  its saved tool traces.
  [Protocol registry](../../benchmarks/locomo/protocol.py#L513-L553) ·
  [Published run](../../benchmarks/locomo/README.md#published-results) ·
  [LoCoMo §2 at this round](https://github.com/writeitai/remember-stack/blob/52466e912a53fc62630176861f2558c6a921be78/plan/designs/locomo_benchmark_design.md#2-fixed-protocol).

## Verification

The mock-transport probe used production D80 rendering and OpenRouter request
serialization. Stubbed catalogs confirmed the configured-stamp lookup in
claim embedding and both reconcile modes. Of the focused existing tests,
85 passed and 33 PostgreSQL tests skipped because no test database URL was
configured. [Implementation release checks](../../plan/designs/split_oversized_chunks_design.md#5-offline-benchmark-and-release-checks).
