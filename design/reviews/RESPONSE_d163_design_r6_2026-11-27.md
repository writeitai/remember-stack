# D163 rescope after six review rounds

PR #682 now covers the evidence-driven D140 and shared eligibility checks
for P1 nomination and final confirmation, the stored-endpoint survivor
walk, its D161 migration, and eligible-ID equivalence and latency gates.
The review record through round six is preserved at the former branch tip.
[Earlier responses](https://github.com/writeitai/remember-stack/tree/12bba03fe68a61123e16c18aaf30ff74d795ad0a/design/reviews)

## Findings retained

- **R1.** Fence both source-membership arms at raw evidence. Key both
  survivor endpoints and each redirect. Use the archive-aware baseline and
  carry the moot-review regression, auditable hosted plans and current
  candidate-depth reference.
- **R2–R3.** Commit compatible view replacements separately and test both
  directions with an intervening reader. Fence raw-support absence and share
  the keyed origin and origin-absence check. Exercise the corrupt redirect
  exception separately from valid-store equivalence. The range leaves are
  in `chunk_claims` and `chunks`.
- **R5–R6.** Confine the origin and source fences to bounded candidates and
  check broad SQL for regressions through shared dependencies. Bind carried
  chunks to the claim's document and cover the graph's projection and
  ledger cases in the D140 description.

[D163 fact path](../../plan/designs/shared_retrieval_eligibility_design.md#43-facts-one-source-qualified-projection-one-endpoint-rule) ·
[Qualification](../../plan/designs/shared_retrieval_eligibility_design.md#91-d163-fact-path-qualification) ·
[Hosted evidence](../../plan/analysis/evidence_driven_fact_eligibility_20261127.md)

## Follow-ups (not part of D163)

| Rounds | Findings moved | Why |
| --- | --- | --- |
| R1–R2 | Protected run identities, engine/schema/store admission, start and resume checks including `--force` | The SQL oracle compares fixed stores and completes both gates under controlled inputs. |
| R3–R4 | Lifetime binding, per-sample stores, historical resume and restore, SQL-role connection, BEAM reader inputs, frozen LoCoMo prompt and options | These establish benchmark-run provenance across invocations. |
| R5–R6 | API artifact and effective settings, metering on frozen stores, evaluator provider settings, BEAM route loss, concurrent writers and interrupted checkpoints | These concern harness execution and checkpoint integrity. |
| R3–R4 | Full-hydration work on D54 live evidence/counts, document filters, `support_state_current`/`review_queue` and representative rows | These bound presentation work after eligibility. D163 uses the existing presentation logic. |
| R1, R3–R5 | Ordered reader payloads and deadline responses, archived replay vectors and clocks, reader-visible SQL metadata, and clock injection | These qualify full rendered replay. Equal eligible IDs with fixed gate inputs establish retrieval membership, and the latency gate measures completion. |
| R4 | General benchmark checklist edits for ingestion worker classes | These concern benchmark ingestion readiness. |

This revision verifies the documentation and the existing evidence receipt.
Database equivalence, migration and latency runs are implementation gates.
