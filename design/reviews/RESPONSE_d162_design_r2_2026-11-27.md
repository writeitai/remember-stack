# Response to the D162 design review, round 2

Review of PR #681 at `6c64e3e77`. The review reported no P0 findings.
I accepted all three P1 findings, the P2 finding, and the citation nit.

## P1

- **D140 during recovery.** Origin lookup can use chunks from an
  embed-failed version. CHUNK records that date once, and E2 uses it as
  the assertion date for fresh claims. Both replay paths drain other-version work
  before cleanup and hold lineage intake and queued work through replacement
  CHUNK commit. Replay is refused if that exclusion cannot be guaranteed.
  [Origin lookup](../../src/rememberstack/spine/chunk_catalog.py#L332-L346) ·
  [Origin assignment](../../src/rememberstack/workers/e1.py#L167-L217) ·
  [Claim date](../../src/rememberstack/workers/e2.py#L1345-L1348) ·
  [Recovery](../../plan/designs/split_oversized_chunks_design.md#3-recover-an-unpublished-version)
- **Original Claimify inputs.** The legacy lookup can return V2's copy of
  V1's claims. The same hash stamp also covers copied zero-claim transcripts,
  so either kind of copy needs proof of which inputs produced it.
  Reuse now requires a provable original or a verified equivalent. A V3
  that matches only the unverified copy completes from its own frozen inputs.
  Verification commits with the claims or zero-claim transcript.
  [Legacy donor lookup](../../src/rememberstack/spine/claim_catalog.py#L425-L447) ·
  [Legacy attachment](../../src/rememberstack/spine/claim_catalog.py#L164-L203) ·
  [Reuse contract](../../plan/designs/document_reference_context_design.md#reuse-including-negative-dependencies)
- **Cards outside the cap.** Both occurrences rerank and cap all cards in
  the frozen producer window. Reuse compares admitted inputs after remapping
  and records cap diagnostics for the current occurrence. An unadmitted
  change can reuse the result. A card that enters the request changes the
  comparison, and the ordered D122 producer keys still cover the full window.
  [Rank and cap](../../src/rememberstack/core/selection_references.py#L277-L306) ·
  [Grounding inputs](../../src/rememberstack/workers/e2.py#L911-L960) ·
  [Reuse checks](../../plan/designs/split_oversized_chunks_design.md#5-acceptance)

## P2

- **Ordinary activation.** The same claim route can deliver new CHUNK work
  to a legacy worker. Stop and drain legacy CHUNK and EMBED_CHUNK workers
  before ordinary activation or replay. Ordinary CHUNK pins the qualified
  binding on its work row and carries it through the embed handoff and
  both stages' retries.
  [Claim route](../../src/rememberstack/spine/work_ledger.py#L2176-L2201) ·
  [Embed handoff](../../src/rememberstack/workers/e1.py#L794-L812) ·
  [Activation](../../plan/designs/split_oversized_chunks_design.md#1-check-the-complete-embedding-input)

## Nit

- The analysis now cites the deferred-admission guard alongside the derivation
  of sources completion.
  [Admission guard](../../src/rememberstack/spine/processing_layers.py#L734-L744)
