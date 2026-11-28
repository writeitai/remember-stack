# PR #681 response to review rounds 1–3

- **R1, reuse.** Immutable split rows gate both sides by `version_id`, including intact siblings. The lineage lock orders first grid writes and reuse commits. [Reuse gate](../../plan/designs/split_oversized_chunks_design.md#3-version-wide-reuse-gate).
- **R2 and R3, routing.** The owner scoped the 16,384-token constant to the existing hosted `nebius/bf16` pin with ZDR and fallbacks off. Other bindings keep their errors and routing. The findings on qualification, consumption proofs and pre-claim validation concern the superseded proposal. The dated probe supports the new ceiling. [Probe](../../plan/analysis/split_oversized_chunks_analysis.md).
- **R1 and R2, acceptance.** Frozen LoCoMo and BEAM replay uses the recorded non-hosted bindings, clears target replay state and checks every row column offline. The interrupted-copy test verifies that vector and authority writes roll back together. [Acceptance](../../plan/designs/split_oversized_chunks_design.md#6-acceptance).
- **R1–R3, clarifications.** D55 checks the highest-numbered version and its deletion state. E1 describes stage-specific hash lookups and D80's section fields in the vector input. [D55](../../plan/designs/split_oversized_chunks_design.md#5-terminal-failure) · [E1 identity](../../plan/designs/e1_chunks_design.md#1-the-three-layer-model).

[Earlier round responses](https://github.com/writeitai/remember-stack/tree/44ee93cc8dfccd09ad62cdd56b335daa0cbb25f6/design/reviews) remain in Git.
