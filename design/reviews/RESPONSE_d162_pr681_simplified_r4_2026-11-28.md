# PR #681 response to simplified review rounds 1–4

The owner removed the mechanisms behind these findings.

- **Unarchive P1s (R1, R2), replay note (R2) and unarchive note (R3).** The replay guard,
  reset refusals and unarchive fence are gone. E1 uses ordinary typed
  terminal failure and existing ledger operations.
- **R1 P1 on saved inputs and R2 P2 on online readiness.** The live
  limit-switch protocol is gone. A new ceiling ships in a release. Saved
  grids and attestations survive it, and D80 migrations still require
  coverage. A pending saved input that fails fit takes the terminal path.
- **R2 P1 on reuse publication.** A lookup checks the donor's exact saved
  grid. Immutable rows keep its split status fixed through commit, even
  if another representation of the same version produces split pieces.
- **R3 P1 on queries and R4 P1 on claim and fact embedding.** The E1 ceiling
  is local to passage fitting and batching. The shared-provider limit
  switch and its consumer coordination are gone.

Retained the D122 reuse and empty-Selection rules, the section-entry checks,
D55/D140 successor behavior, per-stage edit locality and common-anchor
packing. [Current design](../../plan/designs/split_oversized_chunks_design.md).
Earlier replies are available at `13000f073`.
