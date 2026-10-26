# Response to GPT-6.1 Sol D143 design review, round 5

Review: [REVIEW_gpt-6.1-sol_d143_design_r5_2026-10-23.md](REVIEW_gpt-6.1-sol_d143_design_r5_2026-10-23.md)
(Sol high). Both findings accepted.

| # | Finding | Resolution |
| --- | --- | --- |
| 1 (P1) | A claims tail could overlap its own version's admitted fact work | Step 0 now releases a tail only for versions that will not enter facts now (facts disabled, facts gap discharged, or a snapshot version waiting behind an earlier unentered facts gap); the version admitted in step 7 never gets one. Step 6 admits a version that already has a claims tail only after its claims-mode reconcile succeeded, and that completion is an advance trigger. Test 9 covers both schedules. |
| 2 (P1) | Discharging a deferred claims gap left a gapless facts blocker | Discharge and reopening follow the layer dependency: discharging a claims gap records the facts gap as discharged, and reopening the current version's claims gap reopens its facts gap. Tests 7–8 cover the three-version schedule and the delete/repoint variant. |
