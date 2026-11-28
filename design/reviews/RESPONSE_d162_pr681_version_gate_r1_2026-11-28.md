# Response to PR #681 design review, round 1

Review of `03456d024`. I accepted all findings.

## P1

- **Version-wide reuse.** D65 stores multiple representations under one `version_id`.
  The gate now excludes donors and receivers with saved split rows anywhere in their
  version. D140 also checks the grid CHUNK is about to insert. A split takes effect at
  that grid's commit. The lineage lock orders that commit with new reuse writes, and
  results committed first remain available for same-chunk replay. Acceptance exercises
  intact siblings on both sides and races each reuse path against split publication.
  [D65 schema](../../src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L399-L415) ·
  [D140 write path](../../src/rememberstack/workers/e1.py#L176-L216) ·
  [E2 publication lock](../../src/rememberstack/spine/selection_catalog.py#L31-L53) ·
  [D162 reuse gate](../../plan/designs/split_oversized_chunks_design.md#3-version-wide-reuse-gate).

## P2

- **First grid write.** The current catalog checks for rows on a separate connection
  from the insert transaction. The design puts the saved-grid recheck and complete grid
  insert under one lineage lock and adds an overlapping first-write case.
  [Chunk catalog](../../src/rememberstack/spine/chunk_catalog.py#L55-L80).
- **Benchmark replay state.** The frozen UUIDs can hit the vector and Selection replay
  lookups and Claimify's completion check. The candidate clears those target products
  and target work so E1 and E2 rerun and recompute D122 producer order.
  [Vector replay](../../src/rememberstack/workers/e1.py#L319-L336) ·
  [Selection replay](../../src/rememberstack/workers/e2.py#L524-L562) ·
  [Claimify completion](../../src/rememberstack/spine/claim_catalog.py#L407-L422) ·
  [Producer order](../../src/rememberstack/workers/e2.py#L592-L606) ·
  [Acceptance](../../plan/designs/split_oversized_chunks_design.md#6-acceptance).

## Nit

- **D55 no-op.** The lookup orders by `version_no` before registration tests deletion.
  A keyless ingest is a no-op only when its bytes match that highest-numbered version
  and the version is non-deleted.
  [Latest-version lookup](../../src/rememberstack/spine/document_catalog.py#L1039-L1045) ·
  [Registration](../../src/rememberstack/spine/document_catalog.py#L221-L250).
