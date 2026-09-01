# Benchmarks corpus

Everything an agent or human needs to run, reason about, and act on the
LoCoMo benchmark, written for a cold reader. The protocol itself (models,
prompts, budgets, fingerprints) is defined in code at
`benchmarks/locomo/protocol.py` and designed in
`plan/designs/locomo_benchmark_design.md`; this directory holds the
**operational knowledge, historical findings, and the current work queue**.

| Document | What it answers |
| --- | --- |
| [`runbook.md`](runbook.md) | How to actually run a smoke or a full publication run: hosts, commands, failure modes and their recoveries, sharding, merging, costs, timings. Binding operational procedure. |
| [`findings-2026-07-31.md`](findings-2026-07-31.md) | Historical pre-v10 findings (517/1540, F1 0.305). They describe a retired protocol and are not evidence about the current system. |
| [`../../plan/analysis/locomo_v11_score_regression_analysis.md`](../../plan/analysis/locomo_v11_score_regression_analysis.md) | V11 result (979/1540, F1 0.5417), observational route-partitioned analysis, and proposed improvement ladder. Analysis, not binding design. |
| [`../../plan/analysis/locomo_counterfactual_answer_analysis.md`](../../plan/analysis/locomo_counterfactual_answer_analysis.md) | Full-v18 conv-26's one counterfactual miss, evidence that retrieval succeeded, and the prompt-only Full-v19 response. Analysis, not binding design. |
| [`next-steps.md`](next-steps.md) | The deliberately small post-V11 work queue: fix retrieval routing, replay cheaply, then decide whether another full run is warranted. |
| [`review-pr193-risks.md`](review-pr193-risks.md) | Post-merge review of the hybrid-retrieval PR (#193): no correctness holes; six ranked operational risks and the re-scoring measurement plan. |
| [`../proposals/observation-adjudication-efficiency.md`](../proposals/observation-adjudication-efficiency.md) | Unchosen E3 observation-adjudication efficiency options (embed cache, verdict batching, stage split, …). Proposal only — not binding. |

Infrastructure specifics (which servers, which secrets, which OpenRouter
key, how clones are provisioned) intentionally live in the private infra
repo (`ultimate-memory-cloud`, `infra/benchmarks.md`), not here — this repo
is public. The runbook references environment variable names only.
