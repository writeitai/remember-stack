# Completed identity uncertainty with autonomous reconsideration

Status: viable unchosen alternative to D112's exact dated-state support rule.

An accepted normalized assertion could complete with no fact when identity is
uncertain. A materially changed relevant input fingerprint would create a new
immutable identity-attempt UUID, scheduled through the existing processing
ledger. Indexed dependencies would let writers enqueue affected attempts without
whole-corpus scans or repeated timer-driven inference. The first successful
identity application would become terminal; later attempts would complete as
obsolete, and old uncertain records would remain immutable.

This requires attempt/dependency stores, a processing target extension, receipt
and adjudication keys that distinguish initial admission from reconsideration,
single-success guards, bounded model inputs, and full replay/forget/consumer
participation. It cannot be implemented by overwriting a receipt or changing a
work row's content hash: existing work identity excludes that hash.

D112 chooses complete multi-target evidence for exact overlapping dated states
because there is already a proven support relation and the evidence schema is
many-to-many. Preserving several supported identities removes an artificial
exclusive choice without introducing reconsideration machinery.

Adoption trigger: a binding requirement needs an assertion to complete without
any fact, then autonomously reconsider genuinely unproven identity after material
input changes. That decision must include the complete lifecycle above and
truthful retrieval of unresolved testimony. See
`../analysis/temporal_state_evidence_targets.md` for the comparison.
