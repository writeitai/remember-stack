# Response to GPT-6.1 Sol D143 design review, round 4

Review: [REVIEW_gpt-6.1-sol_d143_design_r4_2026-10-23.md](REVIEW_gpt-6.1-sol_d143_design_r4_2026-10-23.md)
(reviewed head `7bf148e5`, Sol high). All findings accepted.

| # | Finding | Resolution |
| --- | --- | --- |
| 1 (P1) | Discharging a superseded facts gap strands its entered claims layer | Advance now begins (step 0, facts) by releasing every *owed claims tail* — occurrences complete, facts not entered (open or discharged gap), no tail rows yet — derived from the whole lineage's gap and ledger state, before target selection or any early return, subject to §4.3's earlier-fact-work ordering. §4.3 names the discharged case; test 9 adds the three-version schedule (pause after v1's `embed_claim`, before its full reconcile) and the off/on variant. |
| 2 (P2) | Three definitions of the claims frontier / guard | D143's summary now says "occurrences complete and `embed_claim` succeeded"; the evidence-lifecycle guard uses the named "occurrences complete" predicate including the succeeded `embed_chunk` for the representation and grid. |
| 3 (P2) | Orchestration still promises lane isolation | Orchestration §3 now says lanes order and budget work rows, follow-ups take the opening row's lane, and lanes do not isolate shared entity streams or lineage order; the budget paragraph is qualified accordingly. |
