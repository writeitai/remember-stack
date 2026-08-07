# Design proposals

Live, **unchosen** alternatives and deferred improvement tracks. These are not
binding architecture. Binding product design remains under `plan/designs/` and
`decisions.md`. Binding observation/adjudication behavior remains in
`plan/designs/observations_design.md` (D43) and the decision log.

| Proposal | Status | One-line |
| --- | --- | --- |
| [`provider-health-routing.md`](provider-health-routing.md) | Open | Shared health scores so workers demote slow/erroring OpenRouter hosts dynamically |
| [`observation-adjudication-efficiency.md`](observation-adjudication-efficiency.md) | Open — not implemented | Algorithmic ways to cut LLM/embed cost and wall time on the E3 observation-adjudication tail |
| *(promoted)* chunk-level extract | **Accepted** → D84 + `plan/designs/chunk_level_extract_design.md` | E2 Claimify work grain is the chunk so extract workers parallelize on one doc |

## How to use this directory

- Write proposals when an idea is worth keeping but **not** accepted yet.
- Each proposal states the problem, options, tradeoffs, adoption triggers, and
  explicit non-goals.
- When something is chosen: promote the chosen path into a binding design (and
  decision log if high-level); mark the proposal adopted or supersede it.
- When something dies: mark it rejected with a one-line why — do not delete
  history that explains a later choice.

Related operational notes (benchmarks, run duration, scale-out of workers)
live under [`../benchmarks/`](../benchmarks/).
