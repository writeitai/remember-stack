# Design corpus (repo-facing)

High-level map for cold readers. Binding product/architecture design for the
engine lives primarily under [`../plan/designs/`](../plan/designs/) and the
decision log [`../decisions.md`](../decisions.md). This tree holds
**operational design notes** and **unchosen proposals** that should not be
lost in chat.

| Directory | Role |
| --- | --- |
| [`benchmarks/`](benchmarks/) | How to run and interpret LoCoMo/BEAM-style benchmarks; findings and work queue |
| [`proposals/`](proposals/) | Unchosen alternatives and deferred efficiency/architecture tracks |

Analysis and research notes: [`../plan/analysis/`](../plan/analysis/).

## Engine design cross-links (selected)

| Topic | Binding | Analysis |
| --- | --- | --- |
| Context operations and temporal fact retrieval (D87) | [`../plan/designs/open_query_space_design.md`](../plan/designs/open_query_space_design.md) §3.1 | [`../plan/analysis/context_operation_model_analysis.md`](../plan/analysis/context_operation_model_analysis.md) |
| Chunk-level E2 extract (D84) | [`../plan/designs/chunk_level_extract_design.md`](../plan/designs/chunk_level_extract_design.md) | [`../plan/analysis/chunk_level_extract_analysis.md`](../plan/analysis/chunk_level_extract_analysis.md) |
| Question turns in E2 claims | [`../plan/designs/e2_e3_claims_relations_design.md`](../plan/designs/e2_e3_claims_relations_design.md) | [`../plan/analysis/question_turn_claim_extraction_analysis.md`](../plan/analysis/question_turn_claim_extraction_analysis.md) |
| Unknown entity type gate (E3 retry-then-drop, D86) | [`../plan/designs/e3_unknown_entity_type_gate_design.md`](../plan/designs/e3_unknown_entity_type_gate_design.md) | [`../plan/analysis/e3_unknown_entity_type_gate_analysis.md`](../plan/analysis/e3_unknown_entity_type_gate_analysis.md) |
