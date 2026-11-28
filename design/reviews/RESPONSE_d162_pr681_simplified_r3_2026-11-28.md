# Response to PR #681 simplified design review, round 3

Review of `6af906963`. Accepted the P1, both P2 findings and the sizing nit.
The review reported no P0.

## P1

- **Query embedding through online limit changes.** Self-host shares the
  provider across E1, `QueryEngine` and open SQL. P1 readiness checks the
  model, dimension and policy stamps. A shared byte-cap mock fit all sample
  passages and returned 413 for a formerly accepted query in both serving
  paths. D80 permits online publication for E1-local changes and for shared
  changes that preserve the full outgoing query request envelope. It refuses
  a shrinking or unverified shared change and keeps the outgoing binding
  serving. D162 acceptance covers refusal and compatible changes.
  [E1 wiring](../../src/rememberstack/profiles/selfhost.py#L1878-L1887) ·
  [Query wiring](../../src/rememberstack/profiles/selfhost.py#L1424-L1449) ·
  [P1 readiness](../../src/rememberstack/adapters/postgres_p1.py#L2049-L2096) ·
  [D80 cutover](../../plan/designs/e1_embedding_input_policy.md#45-migration-triggers-and-vector-reuse).

## P2

- **Unarchive no-ops.** The connector calls source-only unarchive on fetched
  same-byte observations. Calling the existing transaction with a mock
  catalog confirmed that live and caller-archived documents return zeros
  without resetting work. D80 reads the fence under its shared lock and
  preserves the no-op results. An actual undo gets a retryable refusal with
  the archive and work intact. D159 and D162 acceptance reflect both outcomes.
  [Connector call](../../src/rememberstack/workers/sync.py#L190-L197) ·
  [Unarchive transaction](../../src/rememberstack/workers/reconcile.py#L1014-L1049) ·
  [D159 undo](../../plan/designs/archive_and_forget_design.md#23-undo-and-coming-back).
- **Reuse after an ordinary edit.** The E1 example follows the body change
  through embedding, Selection and Claimify. A changed Selection hash can
  invalidate Claimify for the next eight chunks, including across sections.
  The lifecycle summary now links to D122 and includes the rendered hash
  and policy alongside the content hash and embedder generation.
  [Selection neighbours](../../src/rememberstack/workers/e1.py#L734-L747) ·
  [Claimify hash](../../src/rememberstack/core/selection_references.py#L70-L86) ·
  [Vector donor lookup](../../src/rememberstack/spine/chunk_catalog.py#L422-L440) ·
  [E1 example](../../plan/designs/e1_chunks_design.md#1-the-three-layer-model).

## Nit

- **BEAM sizing.** Sum individual block whitespace-token counts for the
  400-token budget. Measure bytes across the complete normalized span,
  including gaps.
  [Base packer](../../src/rememberstack/core/chunker.py#L198-L238) ·
  [Sizing recipe](../../plan/analysis/split_oversized_chunks_analysis.md#benchmark-sizing).

The focused baseline suite passed 41 tests. Hosted qualification and
frozen-checkpoint equality remain release checks.
