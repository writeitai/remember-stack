# Response to GPT-6.1 Sol D143 design review, round 7

Review: [REVIEW_gpt-6.1-sol_d143_design_r7_2026-10-23.md](REVIEW_gpt-6.1-sol_d143_design_r7_2026-10-23.md)
(Sol high). Accepted.

| # | Finding | Resolution |
| --- | --- | --- |
| 1 (P1) | Disabled-facts advance could open a claims-mode tail for a version whose facts work is draining | The owed-tail predicate (§5.2 step 5) now requires a facts gap whose `entered_at` is unset (open or discharged); versions admitted earlier, or before the deployment was configured, keep only their ordinary draining chain. Test 9 adds the deletion-wake-up schedule. |
