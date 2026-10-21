# D142 ranked-search harnesses

Scripts for the release gates of [D142](../../decisions.md#d142-ranked-search-nominates-from-the-index-and-confirms-in-one-snapshot)
(the [P1 search design §9](../../plan/designs/postgres_p1_search_projection_design.md#9-index-and-planner-contract)).
Each is one command; none calls a model.

| Script | Work package | What it does |
| --- | --- | --- |
| `explain_real.py` | WP-RS.0 | `EXPLAIN (ANALYZE, BUFFERS)` of the `main`-shaped one-statement BM25 search and of the D142 nomination statement on a **copy** of a real deployment |
| `sweep.py` | WP-RS.8 | Size sweep (default 30k → 300k → 3M chunks) of BM25 chunk search per stratum (`current`, 1% document filter, `history`, with superseded duplicate editions): p50/p95, returned-`k` rate, terminations, fetched candidates, nomination buffers. Run in a `main` checkout and in this branch against separate databases |
| `recall.py` | WP-RS.9 | HNSW recall@k against exact scoring per stratum; exits 1 below the design's starting thresholds (mean ≥ 0.95, no stratum < 0.90) |
| `locomo_gate.py` | WP-RS.9 | The predeclared paired non-inferiority test over ≥ 3 LoCoMo runs per side (bootstrap over questions, 1.0 pp margin); exits 0 pass, 1 fail, 2 inconclusive |

`explain_real.py` and the LoCoMo runs it gates are pending as of 2026-10-21:
a full LoCoMo run occupies the real deployment, and the paired LoCoMo runs
are paid and need the owner's approval.
