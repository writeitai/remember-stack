# Benchmarks corpus

Everything an agent or human needs to run, reason about, and act on the
LoCoMo benchmark, written for a cold reader. The protocol itself (models,
prompts, budgets, fingerprints) is defined in code at
`benchmarks/locomo/protocol.py` and designed in
`plan/designs/locomo_benchmark_design.md`; this directory holds the
**operational knowledge, the current findings, and the work queue**.

| Document | What it answers |
| --- | --- |
| [`runbook.md`](runbook.md) | How to actually run a smoke or a full publication run: hosts, commands, failure modes and their recoveries, sharding, merging, costs, timings. Binding operational procedure. |
| [`findings-2026-07-31.md`](findings-2026-07-31.md) | What the first full publication run measured (517/1540, F1 0.305), the per-category picture, and the miss taxonomy that says *where* answers die. Analysis — cite it, but decisions belong in designs/decisions. |
| [`next-steps.md`](next-steps.md) | The ranked queue of what needs to be done, each item with its evidence and expected leverage. |

Infrastructure specifics (which servers, which secrets, which OpenRouter
key, how clones are provisioned) intentionally live in the private infra
repo (`ultimate-memory-cloud`, `infra/benchmarks.md`), not here — this repo
is public. The runbook references environment variable names only.
