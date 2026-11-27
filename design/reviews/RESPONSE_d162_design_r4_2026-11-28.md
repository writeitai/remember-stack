# Response to the D162 design review, round 4

Review of PR #681 at `8bfcbed45`. The review reported no P0 findings.

## P1

- **Replacement CHUNK dead letter.** Attempt exhaustion can leave both
  stages terminal with no stored vectors. The outstanding recovery keeps
  its lineage hold and model pin. An operator can reopen that same CHUNK
  row once per observed failed attempt with a bounded allowance. Explicit
  cancellation applies the existing version tombstone and current-version
  repoint, fences further claims and replay for the version, and releases
  the hold and pin in the same transaction.
  [Terminal transition](../../src/rememberstack/spine/work_ledger.py#L793-L826) ·
  [Version lifecycle](../../src/rememberstack/spine/lifecycle.py#L658-L709) ·
  [Recovery](../../plan/designs/split_oversized_chunks_design.md#3-recover-an-unpublished-version)
- **Split-donor grounding.** I verified that D32 accepts `Orchard` from
  the donor's typed title and rejects that added context after the title
  changes to `Terrace`. Each remapped claim from a split donor now has to
  pass the current occurrence's grounding check before attachment. If
  every eligible donor fails, Claimify uses the current frozen Selection.
  [Grounding inputs](../../src/rememberstack/workers/e2.py#L1622-L1659) ·
  [Donor checks](../../plan/designs/document_reference_context_design.md#reuse-including-negative-dependencies)
- **Whitespace-only eligibility interval.** I verified that E0 accepts
  two eligible blocks around a separately labelled ineligible `\n\n`.
  E1 assigns split eligibility from nonblank text and attaches separator
  intervals to adjacent text deterministically, retaining their bytes
  and provenance. The schema states the same rule.
  [E0 validation](../../src/rememberstack/core/extraction_eligibility.py#L50-L71) ·
  [Split rules](../../plan/designs/split_oversized_chunks_design.md#2-split-only-failing-candidates)
- **Benchmark request equality.** Legacy E1 batches by item count after
  filtering reused vectors. The benchmark binding preserves the ordered
  batches and provider framing. The offline gate compares whole requests
  before replaying saved responses and fails when any qualified limit
  would change a request.
  [Legacy batching](../../src/rememberstack/workers/e1.py#L388-L409) ·
  [Provider payload](../../src/rememberstack/adapters/openrouter.py#L1034-L1046) ·
  [Acceptance](../../plan/designs/split_oversized_chunks_design.md#5-acceptance)

## P2

- **Section entry handles.** Both queries match by block ordinals, which
  multiple pieces can share. The keyed section's stored start comes from
  its first block. Both read operations select the chunk covering that
  offset, with acceptance cases for split and unsplit sections.
  [Reference lookup](../../src/rememberstack/spine/document_references.py#L1366-L1382) ·
  [History lookup](../../src/rememberstack/spine/section_history.py#L737-L753) ·
  [Section start](../../src/rememberstack/core/structure_skeleton.py#L567-L582) ·
  [Read contract](../../plan/designs/effective_time_and_section_references_design.md#62-reading-references)

## Nit

- D162 now says “could never embed in full.” The legacy adapter's checks
  cover response shape, including count and dimensions.
  [Response validation](../../src/rememberstack/adapters/openrouter.py#L1052-L1077)
