# Response to GPT-6.1 Sol D143 design review, round 8

Review: [REVIEW_gpt-6.1-sol_d143_design_r8_2026-10-23.md](REVIEW_gpt-6.1-sol_d143_design_r8_2026-10-23.md)
(Sol high). Accepted.

| # | Finding | Resolution |
| --- | --- | --- |
| 1 (P1) | A deletion while both layers are off left the restored current version discharged forever | Reopening the current version's discharged gaps is now step 1 of advance and runs even when the layer is disabled (claims reopening carries facts); discharge of the other versions moved into step 2 after the disabled return. The reopened gaps are open, so `setup` seeds them when the layers return. Test 7 adds the exact off/delete/on sequence. |
