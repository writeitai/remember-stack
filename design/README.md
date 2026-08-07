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
| Chunk-level E2 extract (D84) | [`../plan/designs/chunk_level_extract_design.md`](../plan/designs/chunk_level_extract_design.md) | [`../plan/analysis/chunk_level_extract_analysis.md`](../plan/analysis/chunk_level_extract_analysis.md) |
