# Design reviews

Adversarial reviews of proposed designs before binding acceptance.

| Review | Agents | Scope |
| --- | --- | --- |
| [`REVIEW_claude-fable_2026-08-06.md`](REVIEW_claude-fable_2026-08-06.md) | Claude Fable 5 | Rank embed cache, checkpointing, relation-label analysis |
| [`REVIEW_codex-sol_2026-08-06.md`](REVIEW_codex-sol_2026-08-06.md) | Codex gpt-5.6-sol | Same |

| [`REVIEW_codex-sol_entity_identity_retrieval_plan_2026-08-26.md`](REVIEW_codex-sol_entity_identity_retrieval_plan_2026-08-26.md) | Codex gpt-5.6-sol xhigh | D95–D97 implementation plan r1 |
| [`REVIEW_agy_entity_identity_retrieval_plan_2026-08-26.md`](REVIEW_agy_entity_identity_retrieval_plan_2026-08-26.md) | Antigravity (`agy`) | Same r1 |
| [`REVIEW_codex-sol_entity_identity_retrieval_plan_r2_2026-08-26.md`](REVIEW_codex-sol_entity_identity_retrieval_plan_r2_2026-08-26.md) | Codex gpt-5.6-sol xhigh | Hard-cut plan r2 (no BC) |
| [`REVIEW_agy_entity_identity_retrieval_plan_r2_2026-08-26.md`](REVIEW_agy_entity_identity_retrieval_plan_r2_2026-08-26.md) | Antigravity (`agy`) | Same r2 |
| [`REVIEW_codex-sol_optional_exact_t0_2026-08-26.md`](REVIEW_codex-sol_optional_exact_t0_2026-08-26.md) | Codex gpt-5.6-sol xhigh | PR #307 T0-never-merge + unchosen exact-T0 proposal |
| [`REVIEW_agy_optional_exact_t0_2026-08-26.md`](REVIEW_agy_optional_exact_t0_2026-08-26.md) | Antigravity (`agy`) | Same PR #307 |
| [`REVIEW_codex-sol_wp_i1_extract_aliases_r3_2026-08-26.md`](REVIEW_codex-sol_wp_i1_extract_aliases_r3_2026-08-26.md) | Codex gpt-5.6-sol xhigh | PR #308 WP-I.1 r3 (Approve; r1–r2 source-grounding) |
| [`REVIEW_agy_wp_i1_extract_aliases_r3_2026-08-26.md`](REVIEW_agy_wp_i1_extract_aliases_r3_2026-08-26.md) | Antigravity (`agy`) | Same PR #308 r3 |
| [`REVIEW_claude-opus_wp_i3_global_er_eval_r1_2026-08-27.md`](REVIEW_claude-opus_wp_i3_global_er_eval_r1_2026-08-27.md) | Claude Opus 5 xhigh | PR #312 WP-I.3 r1 blockers |
| [`REVIEW_claude-opus_wp_i3_global_er_eval_r3_2026-08-27.md`](REVIEW_claude-opus_wp_i3_global_er_eval_r3_2026-08-27.md) | Claude Opus 5 xhigh | PR #312 WP-I.3 r3 approval |
| [`REVIEW_agy_wp_i3_global_er_eval_r3_2026-08-27.md`](REVIEW_agy_wp_i3_global_er_eval_r3_2026-08-27.md) | Antigravity (`agy`) | Same PR #312 r3 approval |

Revised designs after these reviews:

- `plan/designs/observation_rank_embedding_cache_design.md` (rev 2)  
- `plan/designs/pipeline_checkpointing_design.md` (rev 2)  
- `plan/analysis/relation_fact_labels_in_p1.md` (eval + S4 lean updated)
