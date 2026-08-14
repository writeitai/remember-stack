# Proposal: promote pgvectorscale StreamingDiskANN to the default P1 vector index

**Status:** open, unchosen
**Date:** 2026-08-14
**Binding baseline:** pgvector HNSW in
[`postgres_p1_search_projection_design.md`](../../plan/designs/postgres_p1_search_projection_design.md)
**Analysis:**
[`postgres_p1_search_projection_analysis.md`](../../plan/analysis/postgres_p1_search_projection_analysis.md)

## Problem

HNSW is the simplest vector index because it has no training lifecycle and is
maintained by ordinary PostgreSQL writes. Its graph is memory-hungry. At a
large enough vector count/dimension, keeping its useful working set resident
can become more expensive than the rest of the P1 simplification.

Pgvectorscale adds a compressed, disk-oriented StreamingDiskANN index while
retaining pgvector's SQL distance operators and PostgreSQL storage boundary.

## Proposed change

Replace a target's HNSW index with `USING diskann`. Do not change natural rows
or `chunk_search`, stable IDs, authority joins, the one current channel
configuration, RRF, public operations
or failure envelopes.

## Adoption trigger

Evaluate this proposal when a representative target, after ordinary HNSW and
PostgreSQL tuning, meets either condition:

- its measured HNSW working set cannot stay within the deployment's assigned
  memory without violating the transactional PostgreSQL reserve; or
- its contracted filtered semantic p95/throughput cannot be met within the
  approved compute envelope.

Adopt only when DiskANN passes the binding design's recall, filtering,
ingestion, vacuum, recovery and hard-forget gates and materially fixes the
triggering resource constraint.

## Costs and cautions

- another native extension and upgrade surface;
- a less mature index implementation than pgvector HNSW;
- optimized label filtering is limited to `smallint[]`; arbitrary UUID,
  entity and temporal predicates use streaming post-filtering unless the
  schema supplies another safe plan;
- index builds are resource-intensive and need an operator-controlled window;
- relaxed result ordering requires a final stable sort when the public
  contract requires strict distance ordering.

## Rejected automatic policy

The library will not switch index type automatically at a row threshold.
Vector dimension, selectivity, recall target, memory and hardware matter more
than row count alone. Adoption is a measured release/deployment decision, not
a runtime self-tuning subsystem.
