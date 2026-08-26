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

Revised designs after these reviews:

- `plan/designs/observation_rank_embedding_cache_design.md` (rev 2)  
- `plan/designs/pipeline_checkpointing_design.md` (rev 2)  
- `plan/analysis/relation_fact_labels_in_p1.md` (eval + S4 lean updated)
