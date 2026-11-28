# Response to PR #681 design review, round 1

Review of `2a600a06f`. Accepted both P1 findings, both P2 findings and the nit.

## P1

- **Unarchive across the replay fence.** The outgoing unarchive resets work
  before restoring testimony, in one transaction. The reset wakes only rows
  returned by its update. A PostgreSQL `BEFORE UPDATE` guard skips E1
  dead-letter resets during the fence or when the row is permanently blocked.
  Unarchive can commit and wake eligible jobs on either release.
  [Reset statement](../../src/rememberstack/spine/lifecycle.py#L2024-L2053) ·
  [Unarchive](../../src/rememberstack/workers/reconcile.py#L1024-L1060) ·
  [Rollout](../../plan/designs/split_oversized_chunks_design.md#4-rollout).
- **Fit of persisted inputs on a binding switch.** E1 renders from saved
  spans and chunk counts, so even a completed grid can exceed the incoming
  cap. The D80 cutover checks every input needed for rebuild or eligible
  replay under the maintenance fence, using the incoming policy and framing.
  A failed fit check leaves semantic search serving on the outgoing binding.
  [E1 render](../../src/rememberstack/workers/e1.py#L262-L295) ·
  [D80 cutover](../../plan/designs/e1_embedding_input_policy.md#45-migration-triggers-and-vector-reuse).

## P2

- **D122 reuse.** Both earlier-version lookups now require an eligible donor
  and receiver in the design. Retries still replay committed results by chunk
  ID and extractor generation, as the workers do before those lookups.
  [Selection and Claimify replay](../../src/rememberstack/workers/e2.py#L537-L580) ·
  [D122 reuse](../../plan/designs/document_reference_context_design.md#reuse-including-negative-dependencies).
- **Both section-entry readers.** The current queries match block ordinals,
  which can repeat across pieces. A keyed heading's saved character start is
  its first block's start. Acceptance compares ordered handles on a fitting
  grid and requires just the piece containing that offset on a split grid.
  [Heading start](../../src/rememberstack/core/structure_skeleton.py#L567-L582) ·
  [Section history](../../src/rememberstack/spine/section_history.py#L737-L752) ·
  [Document references](../../src/rememberstack/spine/document_references.py#L1366-L1382) ·
  [Acceptance](../../plan/designs/split_oversized_chunks_design.md#6-acceptance).

## Nit

- **Identical-byte ingest.** The keyless no-op requires matching bytes on
  the latest version and a null `deleted_at`. A revert to older bytes or a
  new D140 key can create a successor. D159 forget and re-add starts a
  fresh lineage when explicitly allowed.
  [Registration](../../src/rememberstack/spine/document_catalog.py#L221-L250) ·
  [D140 key](../../src/rememberstack/spine/effective_time.py#L218-L249) ·
  [Terminal failure](../../plan/designs/split_oversized_chunks_design.md#5-terminal-failure).

Disposable PostgreSQL 17 probes reproduced the constraint-only rollback and
the extra ordinal matches. The proposed guard let the existing reset SQL
commit with wakes only for eligible jobs. The span predicate passed both
reader probes. Release integration, hosted qualification and frozen-checkpoint
equality remain implementation acceptance checks.
