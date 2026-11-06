# Response to GPT-6.1 Sol D143 design review, round 3

Review: [REVIEW_gpt-6.1-sol_d143_design_r3_2026-10-23.md](REVIEW_gpt-6.1-sol_d143_design_r3_2026-10-23.md)
(reviewed head `c1fe9bd6`). All findings accepted. Findings 1–3 shared one root cause — the round-2
rule that filtered non-current testimony at fact application — so that rule is withdrawn and
replaced by ordering that reproduces always-on processing.

| # | Finding | Resolution |
| --- | --- | --- |
| 1 (P1) | Current-testimony filtering needs a suppression/completion protocol through D118 | Withdrawn. Design §4.3 now orders work instead: a version's claims tail (whose reconcile retires claims) waits until no earlier version of its lineage has fact work in flight. Older fact work therefore always runs while its claims are current, the D88/D90/D118 sets and barriers are untouched, and the later reconcile closes exactly as always-on. Test 9. |
| 2 (P1) | Deletion restoring suppressed testimony cannot restore missing facts | No suppression exists any more, so nothing has to be restored; facts are built and later closed/reopened by the ordinary lifecycle. Discharged gaps are still reopened on repointing (§5.2 step 2). |
| 3 (P1) | Supplementary retired assertions can create facts | Moot under ordering: no fact work runs after a later version retired its lineage's claims in a configured deployment, so the decision inputs are those of always-on processing. |
| 4 (P1) | Successor ready while reused claims were never embedded | The claims frontier now requires the version's `embed_claim` to have succeeded (§5.1); successors are admitted only after it. In facts-enabled lineages this orders extraction after the predecessor's adjudication — stated as the price. Test 10. |
| 5 (P1) | `combined_context(layers=facts)` has no permitted construction | Symmetric synthetic child: an evidence-grain empty `boundary` envelope when neither sources nor claims is admitted. Every non-empty selection has exactly one valid bundle. Design §7.3; open query space §3.1; D87/D114 notes; test 9. |
| 6 (P2) | Lane propagation vs the entity-global application stream | §5.5 now states that lane is queue priority and budget attribution for work rows, not isolation: applications are applied in canonical entity order by whichever flush unit holds the entity and are metered on that unit's route; follow-up rows take the lane of the row that opened them. Orchestration §3 and D143 match. Test 10. |
| 7 (P2) | Gap schema lacks the lane | `lane processing_lane NOT NULL` on `processing_layer_gaps`, inserted once (`backfill` when recorded because the layer was disabled, the completing work's lane when deferred), preserved through discharge/reopen. Design §3.2, §4.1; schema design. Test 10. |
| 8 (P2) | Zero-chunk frontier proof and stale guard wording | "Occurrences complete" requires the version's `embed_chunk` to have succeeded for its representation and generation, then the extract barrier or an empty grid; the guard and the facts entry use it. D54 note and the chunk-boundary body now reference the one admission procedure. Design §4.2, §5.1–§5.2. |
