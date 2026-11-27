# Response to D162 rescope review, round 1

PR #681 at reviewed head `36fe419d6`. Accepted all five P1 findings, both P2
findings and the BEAM provenance note.

## P1

- **Legacy handles.** E0 marks structure ready before E1, SQL can expose
  chunk IDs, and legacy E1 writes each successful batch to P1. Require proof of
  continuous privacy, gate D162 chunk reads on first E1 publication and keep
  ordinary replay on the existing grid when privacy is unverified. The release
  check covers a legacy failure after one successful batch.
  [SQL visibility](../../src/rememberstack/spine/migrations/versions/p9_38_0059_d140_effective_time.py#L701-L719) ·
  [Batch commit](../../src/rememberstack/workers/e1.py#L535-L572) ·
  [Design §3](../../plan/designs/split_oversized_chunks_design.md#3-recover-the-unpublished-version-at-chunk).
- **Fitting recovery.** The ledger's unique key includes component version.
  Give every CHUNK recovery a distinct embed delivery and make readiness and
  D143 resolve it. The wider-envelope check exercises zero new cuts, stable
  IDs and idempotent replay. [Ledger key](../../src/rememberstack/spine/work_ledger.py#L1996-L2023) ·
  [D143 lookup](../../src/rememberstack/spine/processing_layers.py#L1163-L1181) ·
  [Design §3](../../plan/designs/split_oversized_chunks_design.md#3-recover-the-unpublished-version-at-chunk).
- **Internal anchor.** The existing packer combines the fitting paragraph and
  the dense append. The paragraph keeps its D80 hash when emitted whole.
  Try fitting whole-block runs before internal anchors and cover the
  qualifying sentence in the zero-embedding-call test.
  [Packer](../../src/rememberstack/core/chunker.py#L221-L240) ·
  [Design §1](../../plan/designs/split_oversized_chunks_design.md#1-size-contract-and-split-points).
- **Recorded origin.** The donor search excludes deleted versions, while E2
  uses the recorded date. Copy it from the verified same-version parent to
  its pieces and preserve committed cuts. Cover deletion of the original donor
  between B's CHUNK and recovery. [Donor lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L345) ·
  [E2 date](../../src/rememberstack/workers/e2.py#L1794-L1801) ·
  [Design §2](../../plan/designs/split_oversized_chunks_design.md#2-coordinates-and-reuse).
- **Protected ingest.** A raw HTTP mock confirmed that the legacy decoder
  accepts a singleton at index `1` and P1 validates its vector. Keep pinned
  ingests and replays until transport replay through both E1 paths matches
  delivery outcomes and stored passage vectors. [Decoder](../../src/rememberstack/adapters/openrouter.py#L1034-L1077) ·
  [Design §5](../../plan/designs/split_oversized_chunks_design.md#5-benchmark-admission-and-release-checks).

## P2

- **Repeatable read parity.** Freeze application and SQL evaluation clocks
  and normalize the named per-call SQL telemetry. Compare ordered results
  and require a same-reader comparison to pass. [SQL response](../../src/rememberstack/surfaces/query_sandbox/executor.py#L1061-L1085) ·
  [Design §5](../../plan/designs/split_oversized_chunks_design.md#5-benchmark-admission-and-release-checks).
- **Bounded section links.** Both lookups currently collect every covering
  chunk. On a split grid, return only the first published piece that
  intersects the section's first block. Test both reads with a split first
  block. [References](../../src/rememberstack/spine/document_references.py#L1366-L1382) ·
  [History](../../src/rememberstack/spine/section_history.py#L737-L753) ·
  [Design §2](../../plan/designs/split_oversized_chunks_design.md#2-coordinates-and-reuse).

## Fixture note

Reproduced the 14,187-byte maximum for the role-labelled BEAM rendering.
The audit records the recipe, the 14,958-token upper bound and the pinned
source chat. [Fixture audit](../../plan/analysis/split_oversized_chunks_analysis.md#benchmark-fixture-audit).
