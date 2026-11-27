# Adjacent Chunks Retrieval Primitive — Binding Design

**Status:** Accepted; binding technical design  
**Date:** 2026-09-21  
**Authority:** D130 (Engine)  
**Related Documents:** `plan/analysis/adjacent_chunks_retrieval_analysis.md`, `plan/designs/retrieval_design.md`

---

## 1. Principles (Binding)

1. **Chunk Neighborhood Determinism:**
   * Given a visible, live source chunk `chunk_id`, its document neighborhood is strictly defined by document identity `doc_id`, version `version_id`, and ordinal sequence `ordinal` within `memory_v1.chunks_all_versions_live` (D140): the chunk may belong to any non-deleted ready version of a live lineage — a time-scoped search can return a passage of a version that is not the lineage's newest — and is read in that version's current representation.
   * Expanding context stays within the target's document, version and representation. Under [proposed D162](split_oversized_chunks_design.md#passage-cutover-and-reads), a visible retired handle used by a published claim resolves to the published pieces intersecting its original half-open span. Each page shares one snapshot for lookup, expansion and hydration. The original citation ID, quote and coordinates still identify the cited evidence.
2. **Symmetric Windowing (`window: int = 1`):**
   * The retrieval primitive accepts `chunk_id: UUID` and a symmetric `window: int = 1` (constrained to `1 <= window <= 2`).
   * Return the target and up to `window` chunks on each side, in document order. A single target at ordinal 0 with `window=1` returns ordinals 0 and 1 when both exist.
   * For a D162 retired handle, page the covering pieces and up to `window` chunks outside each edge of that interval. Every page has at most `2 * window + 1` chunks. A non-null `next_cursor` signals more of that range. Continue with the same `chunk_id`, `window` and the returned cursor.
3. **Consistent Wire Contract:**
   * The operation returns a standard `Envelope` with `grain = Grain.EVIDENCE`.
   * Result chunks are instances of `ChunkEvidenceResult` ordered by `ordinal ASC`.
   * If `chunk_id` does not exist or fails visibility/provenance gates, the operation returns an empty `Envelope` with `Negative(kind=NegativeKind.UNKNOWN_ENTITY)`.
4. **Simplicity Over Complex Grammar:**
   * The primitive avoids complex sandbox SQL or multi-turn coordination by providing a direct, typed endpoint across HTTP, SDK, CLI, and benchmark harness.
   * The path uses D140's `chunks_all_versions_live` view and an ordered lookup over the selected representation's chunks.

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
    cursor: str | None = None,  # D162 retired-handle continuation
) -> Envelope:
    """Fetch surrounding source chunks within a window around a target chunk in document order."""
```

**Implementation Steps:**
1. Validate `ADJACENT_CHUNKS_MIN_WINDOW <= window <= ADJACENT_CHUNKS_MAX_WINDOW`; raise `ValueError` if out of bounds.
2. Execute target lookup over `memory_v1.chunks_all_versions_live` (D140; before D140 this read `chunks_live`, which holds only the newest version):
   ```sql
   SELECT doc_id, version_id, representation_id, ordinal
   FROM memory_v1.chunks_all_versions_live
   WHERE deployment_id = :deployment_id AND chunk_id = :chunk_id
   ```
   Under D162, if no row matches, validate the retained source handle's
   citation authorization and seek the first and last intersecting ordinals
   by character coordinate on that published grid. A live target has one
   ordinal. If neither lookup resolves, return an empty `Envelope` with
   `NegativeKind.UNKNOWN_ENTITY`.
3. Query identifiers between the first target ordinal minus `window`
   and the last plus `window`:
   ```sql
   SELECT chunk_id
   FROM memory_v1.chunks_all_versions_live
   WHERE deployment_id = :deployment_id
     AND doc_id = :doc_id
     AND version_id = :version_id
     AND representation_id = :representation_id
     AND ordinal >= :ordinal_start
     AND ordinal <= :ordinal_end
   ORDER BY ordinal ASC
   ```
   For a retired handle use an ordinal keyset and fetch at most the page cap
   plus one ID to detect more rows. Bind the cursor to the deployment, retired
   ID, version, representation, published basis, window and last page ordinal.
   Recheck access on every page. A cursor for another request or a changed
   basis gets the D154 cursor error. Restart from the original handle on the new
   basis. Advance past the last selected ordinal even if hydration drops a row.
4. Confirm and hydrate only the page's IDs. Under proposed D162, read the
   bodies and coordinates in the same snapshot as steps 2 and 3.
5. Return the evidence envelope with `dropped_by_hydration` and an opaque
   `next_cursor` when more rows remain. The final page has a null cursor.

### 2.2 HTTP API (`rememberstack.surfaces.http_api`)

Add routes:
* `GET /chunks/{chunk_id}/adjacent?window=1` -> `Envelope`. Under D162 it
  accepts the optional `cursor` query parameter.
* `POST /chunks/adjacent` with JSON body (D162 also accepts `cursor`):
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
    cursor: str | None = None,
) -> Envelope:
    """Fetch surrounding source chunks within a window around a target chunk."""
```
Invokes `GET /chunks/{chunk_id}/adjacent` with `window` and the optional
D162 `cursor`. The TypeScript client exposes the same continuation (D141).

### 2.4 CLI (`remember.cli`)

Add subcommand under `remember query`:
```bash
remember query adjacent-chunks <chunk_id> [--window 1] [--cursor <cursor>]
```
Outputs the `Envelope` with chunks in ordinal order. Under D162, pass
the returned `next_cursor` as `--cursor` to continue a retired lookup.

### 2.5 Benchmark Harness & Surface Boundary

1. **Benchmark Tool Descriptor (`benchmarks/locomo/retrieval.py`):**
   * Register `adjacent_chunks` in `_primitive_tool_descriptors()` with properties `chunk_id` (uuid) and `window` (int, default 1, 1..2), expanding the catalog from 21 to 22 tools.
   * Dispatch in `_dispatch_primitive`.
   * Include in `_has_content_bearing_attempt` direct tools in `benchmarks/locomo/runner.py`.
2. **Top-level MCP Scope Boundary (D50, D83, D87, amended by D137):**
   * Under D137, `adjacent_chunks` is a first-class read tool in the shared MCP catalogue (`remember.mcp_tools`, D136) and exposed across `OperationMcpServer` and `EngineMcpServer` for client parity. The D162 reader exposes the optional cursor in both MCP and harness dispatch. Protected readers follow [D162's parity gate](split_oversized_chunks_design.md#benchmark-admission).
