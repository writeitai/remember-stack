# Response to the scoped D163 design review, round 3

Review of PR #682 at `7187235c3`. I accepted the P1, both P2 findings and the nit.

## P1

- **Retained-store oracle.** The conv-26 and conv-30 metadata identify
  PostgreSQL 19, and the backup tool archives the stopped database volume.
  The design uses a one-time logical row and vector transfer for PostgreSQL 19
  restores, then prepares all three conversations at the deployed PostgreSQL 18
  schema. It clones each prepared fixture with its HNSW indexes intact because
  pgvector randomizes graph levels during construction. Matching ordered raw
  batches are a prerequisite for the top `k` comparison.
  [Retained-store evidence](../../plan/analysis/evidence_driven_fact_eligibility_20261127.md#retained-store-oracle) ·
  [Qualification](../../plan/designs/shared_retrieval_eligibility_design.md#91-d163-fact-path-qualification)

## P2

- **Migration contention.** The first retry waits 250 ms. Use the existing
  log-handler event barrier after `55P03` to finish the previous-release saved
  query while the original reader still holds its lock. Release the reader and
  resume the retry with the shipped timeout settings.
  [Retry delay](../../src/rememberstack/spine/migrations/env.py#L30-L33) ·
  [Existing retry test](../../src/tests/spine/test_migrations.py#L244-L284)
- **Independent evidence links.** The deployed source check accepts a visible
  historical claim of either stance. The D140 time check can use a supporting
  occurrence that survives deletion of its claim's origin version in an
  undeclared lineage. The design requires a fixture where the two links satisfy
  separate gates and the fact passes nomination and confirmation.
  [Source check](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L322-L330) ·
  [Time check](../../src/rememberstack/core/text_scope.py#L256-L303)

## Nit

- Both historical-claim source links now point to the coordinate joins.
  [Historical claim SQL](../../src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py#L258)

Full-schema equivalence, migration and latency remain implementation gates.
