# Design proposals

Live, **unchosen** alternatives and deferred improvement tracks. These are not
binding architecture. Binding product design remains under `plan/designs/` and
`decisions.md`.

| Proposal | Status | One-line |
| --- | --- | --- |
| [`provider-health-routing.md`](provider-health-routing.md) | Open | Shared health scores so workers demote slow/erroring OpenRouter hosts dynamically |
| [`observation-adjudication-efficiency.md`](observation-adjudication-efficiency.md) | Open (may land via separate PR) | Cut E3 observation-adjudication LLM/embed cost |

## How to use this directory

- Write proposals when an idea is worth keeping but **not** accepted yet.
- Each proposal states the problem, options, tradeoffs, adoption triggers, and
  non-goals.
- When chosen: promote into binding design + decision log if high-level; mark
  the proposal adopted.
