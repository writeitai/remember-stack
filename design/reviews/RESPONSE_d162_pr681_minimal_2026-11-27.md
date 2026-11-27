# PR #681 review response for the minimal D162 rewrite

This replaces the design reviewed at `11b3b805f`, `75542dc72` and
`d89ac335b`. The owner narrowed D162 to a full-input ceiling, private CHUNK
recovery and one offline benchmark check. A fitting document uses the
original packer output and stored rows. A replacement piece runs fresh E1
and eligible E2 on its own exact source span.
[D162 design](../../plan/designs/split_oversized_chunks_design.md).

## Findings carried into the rewrite

- The offline check compares every column of both stored row sets plus the
  complete rendered inputs, effective E2 location pairs and ordered D122
  producers. The three negative controls exercise changed ordinals, a header
  moved into the body, and a dropped typed section title.
- The implementation release check records frozen baseline identities and
  digests, exact restore and check commands, hosted qualification and offline
  results. The E1 acceptance YAML moves to the new contract with implementation.
  The analysis labels 352,993 as the enumerated sizing spans.
- Full fit checks account for header changes at each seam and at dispatch.
  Gap-only pieces get precise evidence labels and clipped provenance. Section
  links return the first intersecting piece. Body confirmation uses the
  embedding hash and keeps the header and normalized body separate.
- Legacy SQL and partial P1 writes can expose a grid before E1 success.
  Private recovery verifies continuous isolation, retains the parent's recorded
  date, and fences stale attempts before replacement and ledger replay. New
  indexed replies require one valid vector per request position.

## Findings resolved by removing their mechanism

- The round-one heading-change issue, round-two donor ordering and lookup,
  and round-three whole-block-to-fragment reuse apply to split-piece
  carry-forward. Every replacement piece now pays for fresh work, including
  a fragment whose bytes match an earlier whole block.
- Cross-layout citation remapping, slice-based D140 inheritance and retained
  origin donor records drop out with split-piece reuse. Fitting chunks use
  their existing reuse and date rules. Vector mapping is validated on new
  calls without retroactive proof requirements for those fitting rows.
- SQL enrollment, staged first publication, manifest cutover, rollout and
  rollback rehearsals, and live benchmark replay belonged to the removed
  publication and admission design. Recovery uses existing private rows
  and ledger state. The benchmark check runs offline.
