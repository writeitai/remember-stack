# Adjacent Chunks Retrieval Primitive — Binding Design

**Status:** Accepted; binding technical design  
**Date:** 2026-09-21  
**Authority:** D130 (Engine)  
**Related Documents:** `plan/analysis/adjacent_chunks_retrieval_analysis.md`, `plan/designs/retrieval_design.md`

---

## 1. Principles (Binding)

1. **Chunk Neighborhood Determinism:**
   * Given a visible, live source chunk `chunk_id`, its document neighborhood is strictly defined by document identity `doc_id`, version `version_id`, and ordinal sequence `ordinal` within `memory_v1.chunks_live`.
   * Expanding context around a chunk must never cross document or version boundaries.
2. **Symmetric Windowing (`window: int = 1`):**
   * The retrieval primitive accepts `chunk_id: UUID` and a symmetric `window: int = 1` (constrained to `1 <= window <= 2`).
   * A `window=1` query returns up to 3 chunks: `[ordinal - 1, ordinal, ordinal + 1]` in document order.
   * A `window=2` query returns up to 5 chunks: `[ordinal - 2, ..., ordinal + 2]` in document order.
   * Edges of documents truncate naturally (e.g. if target chunk is ordinal 0, only ordinal 0 and 1 are returned).
3. **Consistent Wire Contract:**
   * The operation returns a standard `Envelope` with `grain = Grain.EVIDENCE`.
   * Result chunks are instances of `ChunkEvidenceResult` ordered by `ordinal ASC`.
   * If `chunk_id` does not exist or fails visibility/provenance gates, the operation returns an empty `Envelope` with `Negative(kind=NegativeKind.KNOWN_EMPTY)`.
4. **Simplicity Over Complex Grammar:**
   * The primitive avoids complex sandbox SQL or multi-turn coordination by providing a direct, typed endpoint across HTTP, SDK, CLI, and benchmark harness.
   * No database migrations are required; the PostgreSQL table `chunks` is indexed by `ix_chunks_doc (deployment_id, doc_id)` and per-document chunk counts are bounded.

---

## 2. API & Surface Specifications

### 2.1 Engine Specification (`rememberstack.surfaces.query_engine.QueryEngine`)

Add method:
```python
@_with_surface(SurfaceCostKind.SEARCH)
def adjacent_chunks(
    self,
    *,
    deployment_id: UUID,
    chunk_id: UUID,
    window: int = 1,
) -> Envelope:
    """Fetch surrounding source chunks within a window around a target chunk in document order."""
```

**Implementation Steps:**
1. Validate `ADJACENT_CHUNKS_MIN_WINDOW <= window <= ADJACENT_CHUNKS_MAX_WINDOW`; raise `ValueError` if out of bounds.
2. Execute target lookup over `memory_v1.chunks_live`:
   ```sql
   SELECT doc_id, version_id, ordinal
   FROM memory_v1.chunks_live
   WHERE deployment_id = :deployment_id AND chunk_id = :chunk_id
   ```
   If no row matches, return an empty `Envelope` with `NegativeKind.KNOWN_EMPTY`.
3. Query surrounding chunk identifiers:
   ```sql
   SELECT chunk_id
   FROM memory_v1.chunks_live
   WHERE deployment_id = :deployment_id
     AND doc_id = :doc_id
     AND version_id = :version_id
     AND ordinal >= :ordinal_start
     AND ordinal <= :ordinal_end
   ORDER BY ordinal ASC
   ```
4. Confirm and hydrate chunks using existing `self._confirm_chunks(deployment_id=deployment_id, chunk_ids=adjacent_ids)`.
5. Return `_envelope(grain=Grain.EVIDENCE, chunks=chunks, freshness=_freshness(), dropped_by_hydration=dropped, negative=...)`.

### 2.2 HTTP API (`rememberstack.surfaces.http_api`)

Add routes:
* `GET /chunks/{chunk_id}/adjacent?window=1` -> `Envelope`
* `POST /chunks/adjacent` with JSON body:
  ```json
  {
    "chunk_id": "UUID",
    "window": 1
  }
  ```
  -> `Envelope`

### 2.3 Python SDK (`remember.client.MemoryClient`)

Add method:
```python
def adjacent_chunks(
    self,
    *,
    chunk_id: UUID | str,
    window: int = 1,
) -> Envelope:
    """Fetch surrounding source chunks within a window around a target chunk."""
```
Invokes `GET /chunks/{chunk_id}/adjacent` with parameter `window`.

### 2.4 CLI (`remember.cli`)

Add subcommand under `remember query`:
```bash
remember query adjacent-chunks <chunk_id> [--window 1]
```
Outputs standard `Envelope` JSON containing `chunks` (`ChunkEvidenceResult` list ordered by ordinal).

### 2.5 Benchmark Harness & Surface Boundary

1. **Benchmark Tool Descriptor (`benchmarks/locomo/retrieval.py`):**
   * Register `adjacent_chunks` in `_primitive_tool_descriptors()` with properties `chunk_id` (uuid) and `window` (int, default 1, 1..2), expanding the catalog from 21 to 22 tools.
   * Dispatch in `_dispatch_primitive`.
   * Include in `_has_content_bearing_attempt` direct tools in `benchmarks/locomo/runner.py`.
2. **Top-level MCP Scope Boundary (D50, D83, D87, amended by D137):**
   * Originally, raw primitives did not mint top-level MCP tools. Under D137, `adjacent_chunks` is elevated to a first-class read tool in the shared MCP catalogue (`remember.mcp_tools`, D136) and exposed across `OperationMcpServer` and `EngineMcpServer` to ensure complete client parity across HTTP API, Python SDK, CLI, and MCP.
