# Response to GPT-6.1 Sol D143 design review, round 6

Review: [REVIEW_gpt-6.1-sol_d143_design_r6_2026-10-23.md](REVIEW_gpt-6.1-sol_d143_design_r6_2026-10-23.md)
(Sol high). Accepted.

| # | Finding | Resolution |
| --- | --- | --- |
| 1 (P1) | A living version whose facts gap is discharged by the same advance misses its tail | §5.2 is reordered: living discharge (step 2) and the admission decision (step 4) happen before the whole-lineage tail release (step 5), which runs before any return and counts gaps discharged in the same invocation. Both round-5 protections are kept (the admitted version gets no tail; an existing claims-mode reconcile must succeed before facts admission). Test 9 asserts the first facts advance after v1's full reconcile opens v2's tail. |
