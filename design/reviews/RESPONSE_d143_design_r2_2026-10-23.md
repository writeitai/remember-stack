# Response to GPT-6.1 Sol D143 design review, round 2

Review: [REVIEW_gpt-6.1-sol_d143_design_r2_2026-10-23.md](REVIEW_gpt-6.1-sol_d143_design_r2_2026-10-23.md)
(reviewed head `9ebea962`). "Design" means `plan/designs/processing_layers_design.md`. All findings
accepted. The central change is one admission procedure for configured deployments.

| # | Finding | Resolution |
| --- | --- | --- |
| 1 (P1) | Gapless draining predecessor bypasses ordering | In a configured deployment every boundary records a gap and runs the same **advance** procedure as the backfill; admission requires every earlier sources-complete version to have reached the layer's frontier (entered-before-switch, draining and dead-lettered versions included). Deployments without a configuration row keep today's path. Design §1, §4.1, §5.2; test 8. |
| 2 (P1) | Living supersession guard misses the pre-boundary interval | The guard now requires the current version to be this reconcile's version or to have reached its claims frontier, checked, computed and applied in one transaction under the lineage lock (configured deployments only). Design §4.2; evidence lifecycle §5; test 7. |
| 3 (P1) | Living lineage loses its wake-up when the current version is still in the sources layer | Advance is invoked by every boundary that records a gap (and by frontiers, deletions, repointing and seeding); living lineages discharge older unentered gaps *before* evaluating the predecessor predicate; a target not yet sources-complete waits for its own boundary; a zero-chunk target reaches its frontier at once and re-runs advance. Design §5.2 steps 2–7; test 8. |
| 4 (P1) | Retired testimony can still become a fact during admitted/draining facts work | Configured deployments apply one eligibility rule: the facts fan-out selects current-testimony claims (D88 expected set; D90 empty flush when none) and application revalidates currency under the claim row lock, which currency transitions also take. Design §4.3; test 9. |
| 5 (P1) | Discharged gaps never recover when deletion repoints current | Advance step 2 reopens the current version's discharged gaps; deletion and repointing invoke advance. Reuse for the reopened version follows ordinary ingestion (earlier live versions only; deleted testimony never reused, D135); chunks and embeddings untouched. Design §5.2–§5.3; test 7. |
| 6 (P1) | Fact follow-ups hard-code the steady lane | Every follow-up of entered work takes the originating lane, including fact-application projection repair and reconcile-requested window reviews; steady remains the default and D67 promotion still applies. Design §5.5; orchestration §3; test 10. |
| 7 (P2) | Frontier vs readiness/receipt completeness | Defined separately: frontiers order lineages; readiness and receipts require every stage of the enabled/measured profile (standard: today's predicate, timestamp and dead-letter handling). Design §1, §4.5, §5.1; test 11. |
| 8 (P2) | Composition contract and D87/D114 contradict the synthetic child | Open query space §3.1 now states channel-scoped nomination and the per-admitted-set equality with the one synthetic fact child; D87 and D114 carry refinement notes; D143 lists them. Design §7.3. |
| 9 (P2) | Published P3/K mounts keep serving facts | P3 snapshots record the layer set they were built for and the publisher activates only a matching one (else the empty placeholder); `setup` builds a matching snapshot on a facts change; a stale-configuration build is never activated; the knowledge mount is not published without facts. Design §3.4, §7.6; test 13. |

The profile-refresh cost exception is now stated as such (design §4.2) and covered by test 14.
