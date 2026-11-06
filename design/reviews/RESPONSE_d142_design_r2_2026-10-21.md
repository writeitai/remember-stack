# Response to GPT-6.1 Sol D142 design review, round 2

Review: [REVIEW_gpt-6.1-sol_d142_design_r2_2026-10-21.md](REVIEW_gpt-6.1-sol_d142_design_r2_2026-10-21.md)
(reviewed head `82e6218a`). All round-1 P1s were closed; this round raised one
new P1 and three partially closed P2s. "Design" means
`plan/designs/postgres_p1_search_projection_design.md`.

## P1

| # | Finding | Resolution |
| --- | --- | --- |
| R2-P1-1 | Index and standalone BM25 scorers differ in 1.5.1 | **Verified** at commit `fa142d04`: `bmw.c` scores memtable rows with `tp_source_get_doc_length` (raw length); `query.c` always uses `decode_fieldnorm(encode_fieldnorm(raw))`, with a comment stating the intent that both agree; the fieldnorm table maps 40 and 41 to 40. Contract chosen: **one canonical BM25 score** — fieldnorm-quantized length, generation statistics, one shared float4 routine, ties by bitwise equality — used by index nomination, exact entity tiers, SRFs, the `scope_pending` probe and every oracle (design §8.3, §8.4). The image carries a small reviewed **scorer-identity patch** (memtable scorer quantizes length and shares the standalone routine), reported upstream, pinned by checksum and removed when an upstream release passes the gate (design §6, `p2_graph_design.md`, D98 note, D142). A **scorer-identity gate** on every image build requires bitwise-equal projected and standalone scores and a non-increasing stream, with the fixtures requested: 40/41-token ties with reversed stable IDs, mixed spilled/unspilled rows, multi-term near-ties, the 48/50/67 counterexample, both sides of the exact-tier threshold (design §9; plan WP-RS.1, WP-RS.7). Rejected alternatives recorded: raw-length index score as canonical (rank would change on a maintenance spill; not reproducible by exact tiers) and rescoring the fetched prefix (the true winner may never be fetched) — analysis §6.1, D142 Rejected. Because canonical = today's standalone scoring, this adds no ranking change beyond positive-match-only and the upgrade, which stay separately gated. |

## P2

| # | Finding | Resolution |
| --- | --- | --- |
| P2-1 | Fetched output still described as total work | Design §9.1 now separates **fetched-candidate and confirmation work** (the `k/s` model, measured by counters, bounded by the work budget) from **index-internal work** (BM25 posting/block traversal, memtable scoring, pg_textsearch's internal rescoring on batch growth, HNSW visits), which the deadline bounds and the benchmark reports separately (buffer reads; posting/block/scoring-pass counts and HNSW visits where exposed). Same wording in design §14, D142 Consequences, analysis §5 (with the `scan.c` source link) and plan WP-RS.8. |
| P2-4 | Same-statement clauses and design sequencing remained | Reconciled `retrieval_design.md` (`facts_context` paragraph; spike-results consequence), `open_query_space_design.md` (`semantic_entities` prose; executor-side bridge "ranked statement"), `postgres_schema_design.md` (`chunk_search` and claim-validity comments). The open query space design's §11 build sequence moved verbatim to `plan/plans/open_query_space_delivery.md`; the design keeps a pointer, and the Batch A implementation note's section reference is updated. A sweep found no remaining P1 same-statement clause (graph-traversal statement wording is unrelated and unchanged). |
| P2-5 | LoCoMo gate ambiguous; mutable source links | Predeclared **paired non-inferiority test** (design §9, analysis §8, plan WP-RS.9): question as sampling unit; three runs per side, per-question mean correctness; estimator = mean paired difference; one-sided 95% lower bound by paired bootstrap over questions (10,000 resamples, fixed recorded seed); fixed 1.0 pp margin (owner confirms before the run); pass if lower bound > −1.0 pp, fail if the one-sided upper bound < −1.0 pp, otherwise **inconclusive** (never a pass). Analysis §10 Sources now link immutable commits/tags for every external claim. |

## Disagreements

None. Note for the owner: closing R2-P1-1 needs a small local patch to
pg_textsearch (or a future upstream release); the patch is narrow and gated,
but it is a maintenance item until upstream fixes the memtable scorer.
