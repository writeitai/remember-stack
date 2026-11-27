# Response to the D162 design review, round 3

Review of PR #681 at `1e47872c3`. The review reported no P0 findings.

## P1

- **Selection and Claimify at activation.** Both delivery paths use the
  same stage and lane predicate. Legacy Selection can omit a gap-only
  target when its neighbour supplies a passage. Legacy Claimify can attach
  an earlier completion without checking its original inputs. Activation
  drains legacy handlers for all four stages across both delivery paths and
  lanes before ordinary ingestion or replay resumes.
  [Claim route](../../src/rememberstack/spine/work_ledger.py#L2176-L2201) ·
  [Passage fallback](../../src/rememberstack/core/source_passages.py#L124-L148) ·
  [Donor attachment](../../src/rememberstack/workers/e2.py#L832-L859) ·
  [Activation](../../plan/designs/split_oversized_chunks_design.md#1-check-the-complete-embedding-input)
- **P1 model and pending pins.** Setup's stored-vector guard can pass
  before the first vector lands, and semantic search filters on the active
  model. Pin admission and model publication now share a deployment lock.
  Setup checks stored vectors and live pins in one snapshot, so work
  finishing during setup still blocks an incompatible switch. Replay
  re-admits the pin before reopening terminal work.
  [Setup guard](../../src/rememberstack/adapters/postgres_p1.py#L201-L222) ·
  [Search filter](../../src/rememberstack/adapters/postgres_p1.py#L874-L889) ·
  [Pin admission](../../plan/designs/split_oversized_chunks_design.md#1-check-the-complete-embedding-input)
- **Fresh work at a split.** A whole chunk and a split piece can share
  the same keys, and the UUID only identifies the row. A split target
  requires a completed split donor to reuse embedding, Selection or
  Claimify. Its initial work is fresh when only whole-chunk donors match.
  Retries use the row's committed results. A later version with the same
  split inputs may reuse a matching split completion. Requiring fresh
  calls again for that case would charge for an edit outside the split's
  inputs, contrary to D56. D140 date lookup and D122 dependency keys keep
  their stable basis.
  [Vector lookup](../../src/rememberstack/workers/e1.py#L319-L356) ·
  [Selection lookup](../../src/rememberstack/workers/e2.py#L655-L671) ·
  [Claimify lookup](../../src/rememberstack/spine/claim_catalog.py#L425-L447) ·
  [Reuse boundary](../../plan/designs/split_oversized_chunks_design.md#2-split-only-failing-candidates)
- **Nonblank inputs need usable vectors.** The adapter rejects an empty
  vector, then legacy E1 can close the singleton as a successful skip.
  The revised contract requires a usable P1 vector for nonblank input and
  gives unusable responses bounded retries. `empty_body` is the only skip.
  Confirmed input rejections still dead-letter immediately.
  [Adapter validation](../../src/rememberstack/adapters/openrouter.py#L1052-L1077) ·
  [Singleton skip](../../src/rememberstack/workers/e1.py#L463-L478) ·
  [Readiness](../../plan/designs/orchestration_design.md#embed_chunk-durability-d80--minimum-contract)

## P2

- **E1 path in the overview.** The packer counts whitespace tokens and
  starts new runs at accepted content anchors. The D80 renderer builds the
  normalized body and optional deterministic header. The overview, packing
  contract and reference deployment now name that path.
  [Packer](../../src/rememberstack/core/chunker.py#L173-L240) ·
  [D80 render](../../src/rememberstack/core/embedding_input_policy.py#L188-L234) ·
  [E1 overview](../../plan/designs/overall_design.md#4-plane-e-ingestion-pipeline-per-document-chain-d12)
