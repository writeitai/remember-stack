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
| [`next-steps.md`](next-steps.md) | The deliberately small v10 work queue, reset pending the fresh current-system score. |
| [`review-pr193-risks.md`](review-pr193-risks.md) | Post-merge review of the hybrid-retrieval PR (#193): no correctness holes; six ranked operational risks and the re-scoring measurement plan. |
| [`../proposals/observation-adjudication-efficiency.md`](../proposals/observation-adjudication-efficiency.md) | Unchosen E3 observation-adjudication efficiency options (embed cache, verdict batching, stage split, …). Proposal only — not binding. |

Infrastructure specifics (which servers, which secrets, which OpenRouter
key, how clones are provisioned) intentionally live in the private infra
repo (`ultimate-memory-cloud`, `infra/benchmarks.md`), not here — this repo
is public. The runbook references environment variable names only.
