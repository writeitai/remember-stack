# Response to GPT-6.1 Sol D142 design review, round 3 (Approve)

Review: [REVIEW_gpt-6.1-sol_d142_design_r3_2026-10-21.md](REVIEW_gpt-6.1-sol_d142_design_r3_2026-10-21.md).
No P0/P1. Both non-blocking P2s are resolved before implementation starts.

| # | Resolution |
| --- | --- |
| P2-1 scorer assurance | The canonical score now pins per-term float4 scores **and** their float8 accumulation in query-term order (the standalone operator's accumulator at 1.5.1, linked), shared by the patched index path (P1 search design §8.3, D142). "Equals today's ordering" is qualified to "intended, measured": the upgrade comparison explicitly includes the scorer patch and reports whether canonical ordering and multi-term tie sets equal today's standalone ordering (§9, analysis §6.1). The image identity/monotonicity gate now names multi-term accumulation. |
| P2-2 residual wording | `retrieval_design.md` central invariant → "ranked-read transaction snapshot (D142)"; `open_query_space_design.md` `lexical_facts` adoption row → "D142 ranked-read nomination/confirmation contract"; `postgres_schema_design.md` D41 summary distinguishes the ordinary SQL filter in `claims_as_of` from the D142 confirmation predicate. |
