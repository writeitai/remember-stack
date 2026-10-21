# Index-driven ranked search — delivery (D142)

Build order for
[D142](../../decisions.md#d142-ranked-search-nominates-from-the-index-and-confirms-in-one-snapshot)
as specified in [the P1 search design §8–§9 and §12](../designs/postgres_p1_search_projection_design.md#8-query-and-filtering-contract).
Architecture lives in the design; this file only sequences it. Issue
[#504](https://github.com/writeitai/remember-stack/issues/504).

**Where the work lands.** This pull request contains the design corpus only.
Implementation follows after design approval, in the same pull request or a
follow-up as the owner decides. Nothing below ships before it is implemented,
tested and reviewed.

**Operational constraint (2026-10-21).** A full LoCoMo run is in progress. No
work package may connect to, query or `EXPLAIN` against its database or
deployment; development and benchmarks use isolated databases. WP-RS.0 and
WP-RS.7 wait until that run has finished.

| WP | Scope | Design | Depends on | Delivery |
| --- | --- | --- | --- | --- |
| WP-RS.0 | Real-data plan check: `EXPLAIN (ANALYZE, BUFFERS)` of every ranked statement on a **copy** of a real deployment (a LoCoMo database after the current run). Record the plans in the analysis; any cause not covered by analysis §2 is raised before implementation proceeds. | analysis §1–§2 | LoCoMo run finished | before WP-RS.2 |
| WP-RS.1 | Image: `pg_textsearch` 1.5.1 (retire the D98 patch), keep pgvector ≥ 0.8; upstream SQL tests and image gates pass; readiness checks the new minimum. Update every pin: `Dockerfile.postgres` revision/SHA/patch arguments, the extension version map in `spine/graph_catalog.py`, and `src/tests/test_d98_removal_audit.py`. Verify license and notice files are unchanged or re-pinned. | design §6 | — | first implementation commit |
| WP-RS.2 | Adapter loop: one helper in `adapters/postgres_p1.py` implementing nominate → confirm → refill in a read-only `REPEATABLE READ` transaction, tie rule, work budget and truncation counters; the chunk and claim methods (semantic and BM25) split into index query + confirmation query reusing their existing predicate builders. A plan-shape test asserts every index query plans as an index scan. | design §8.1–§8.2, §9, §12 | WP-RS.1 | — |
| WP-RS.3 | Entity-scoped nomination: coverage tiers from mentions, exact scoring of small tiers, loop within large tiers; `nominate_testimony_scored` and the D140 `scope_pending` probe use it. | design §8.3 | WP-RS.2 | — |
| WP-RS.4 | Facts: one loop per fact branch with the authority view, fact time, entity scope and D140 evidence gate in confirmation (`search_facts_scored`, `nominate_facts_scored`). | design §8.4 | WP-RS.2 | — |
| WP-RS.5 | Tests: BM25 equivalence with an exact full scan on randomized corpora (deletions, periods, entity scopes, ties); adversarial cases from analysis §8 (item 3); open query space SRFs and `pg_confirmed_at`; D140 suites unchanged and green. | analysis §8 | WP-RS.2–4 | — |
| WP-RS.6 | Scaling benchmark: extend `benchmarks/d140_scope` into a size sweep (30k → 300k → 3M chunks; 50M on a short-lived VM if the sweep is flat), every channel and the context operations, `main` against the change; record in the analysis. | design §9 | WP-RS.5 | gate for release |
| WP-RS.7 | HNSW quality gate: recall@k against exact search on sampled real queries; LoCoMo answer accuracy before and after on identical data (paid run, owner-approved). Tune `ef_search`/iterative scan if needed. | design §9 | WP-RS.5, LoCoMo run finished | gate for release |
| WP-RS.8 | Docs: the docs site describes truncation behaviour changes only if user-visible (D66); `not-built-yet` unchanged unless the distinct-text proposal is adopted. | — | WP-RS.6–7 | same PR as the behaviour |

## Decision point: distinct-text search index

Before WP-RS.2 starts, the owner decides whether
[the distinct-text search index proposal](../proposals/distinct_text_search_index.md)
joins this delivery, using the LegalIt pilot's **effective duplication
factor** (index candidates examined per result for realistic `current`
queries over 20–50 heavily amended acts with all versions). Starting rule:
median ≥ 5, or p95 out of budget when extrapolated to the full corpus →
adopt (it then adds a schema migration and E1/forget changes, sequenced as
WP-RS.2a after WP-RS.2); otherwise leave it unchosen.

## Not in this delivery

- Embedding reuse across versions already exists in E1 (D80 carry-forward).
  The LegalIt pilot counts embedding calls for a second version of an act; a
  material miss rate (for example from renumbered section headers in the D80
  location header) is handled as a separate E1/D80 change.
- Reranking (for example the Jev model): a separate, later, reorder-only
  experiment after this delivery.
