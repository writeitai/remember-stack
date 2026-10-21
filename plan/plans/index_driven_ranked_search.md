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
the paid part of WP-RS.9 wait until that run has finished.

| WP | Scope | Design | Depends on | Delivery |
| --- | --- | --- | --- | --- |
| WP-RS.0 | Real-data plan check: `EXPLAIN (ANALYZE, BUFFERS)` of every ranked statement on a **copy** of a real deployment (a LoCoMo database after the current run). Record plans in the analysis; a cause not covered by analysis §2 is raised before WP-RS.2. | analysis §1–§2 | LoCoMo run finished | before WP-RS.2 |
| WP-RS.1 | Image: `pg_textsearch` 1.5.1 (retire the D98 patch) plus the reviewed scorer-identity patch (memtable scorer quantizes length and shares the standalone routine; reported upstream, pinned by checksum, removed when an upstream release passes the gate) and the scorer-identity gate on every build; pgvector ≥ 0.8; upstream SQL tests on the pinned PostgreSQL 19 image (local gate); update every pin — `Dockerfile.postgres` arguments, the extension version map in `spine/graph_catalog.py`, `src/tests/test_d98_removal_audit.py`; license/notice files re-verified. Writers reject zero-norm and non-finite vectors. | design §6, §8.6 | — | first implementation commit |
| WP-RS.2 | Ranked-read context: one `READ ONLY, REPEATABLE READ` transaction per operation with pinned `evaluated_at`/`believed_at`, owned by the query engine and passed to every P1 port call; snapshot export from the SQL sandbox executor and import by the adapter (failure → `p1_unavailable`); optional helper connections only by snapshot import. | design §8.2 | WP-RS.1 | — |
| WP-RS.3 | Nomination cursor loop: one helper implementing declare → fetch (disjoint, doubling) → confirm → stop with the five termination reasons; planner controls scoped to `DECLARE`; `EXPLAIN (FORMAT JSON)` plan attestation; tie lookahead; HNSW exhaustion proof by exact count. Chunk and claim methods (semantic and BM25) split into nomination statement + confirmation query reusing their predicate builders. BM25 positive-match-only on every path. | design §8.1, §8.3, §8.6, §9 | WP-RS.2 | — |
| WP-RS.4 | Structured search result through the port, RRF, the fact merge, envelopes (`known_empty` only when all channels exhausted), `QueryResult.semantic_invocations[]` and `Freshness.scope_pending.complete`. | design §8.6; D140 §3.7 | WP-RS.3 | — |
| WP-RS.5 | Coverage-first nomination: testimony coverage from mentions, fact coverage from survivor-resolved endpoints with `ranking_entity_ids`; entity work budget → `boundary`; exact scoring of tiers ≤ threshold; loop within large tiers; `nominate_testimony_scored`, the `scope_pending` probe and the fact nominators use it. | design §8.4–§8.5 | WP-RS.3 | — |
| WP-RS.6 | Facts and entities: one loop per fact branch with the evidence gate in confirmation; merge `(coverage DESC, distance, fact_kind, fact_id)`; entity profile search on the same loop. | design §8.5 | WP-RS.5 | — |
| WP-RS.7 | Tests (each failing before its fix): scorer identity — 40/41-token documents with equal frequency and reversed stable IDs, mixed spilled/unspilled rows, multi-term near-ties, the 48/50/67 single-term counterexample, both sides of the exact-tier threshold; BM25 equivalence with canonical scoring under one scoring generation; absent terms, empty analyzed query, fewer-than-`k` matches, both sides of the exact-tier threshold; concurrent term-skewed inserts plus spill/compaction between fetches; HNSW `max_scan_tuples` and memory stops vs genuine exhaustion; empty-cap and one-channel-cap end-to-end envelopes; deletion committed before the snapshot (absent) vs between fetches (still visible to that invocation, absent on the next); concurrent insert/delete and effective-period boundaries through the real SQL bridge; two-anchor vs one-anchor facts across both tables, redirected endpoints, graph-expanded scopes, overlapping relation/observation UUIDs; plan attestation rejecting a forced sequential plan; prepared statements. | analysis §8 | WP-RS.3–6 | — |
| WP-RS.8 | Scaling benchmark: extend `benchmarks/d140_scope` into the §9 size sweep and strata, `main` against the change, reporting latency with returned-`k` rate, truncation rate, fetched candidates, coverage rows, plan-attestation cost and index-internal work (buffer reads; posting/block/scoring-pass counts and HNSW visits where exposed). | design §9, §9.1 | WP-RS.7 | release gate |
| WP-RS.9 | Quality gates: HNSW recall@k per stratum against exact scoring; LoCoMo answer accuracy by the predeclared paired non-inferiority test (design §9; margin, seed and question set fixed and owner-confirmed before the first run; owner-approved paid run); the BM25 positive-match change and the `pg_textsearch` upgrade measured separately against today's answers. | design §9 | WP-RS.7, LoCoMo run finished | release gate |
| WP-RS.10 | Docs (D66): the lexical positive-match behaviour, truncation reporting and `scope_pending.complete` on the docs site, same PR as the behaviour. | — | WP-RS.4 | same PR as the behaviour |

## Decision point: distinct-text search index

The [distinct-text search index proposal](../proposals/distinct_text_search_index.md)
stays unchosen until its prerequisites are met:

| step | what | needs |
| --- | --- | --- |
| P-1 | LegalIt pilot corpus copy (20–50 heavily amended acts, all versions) and a fixed query set, outside the engine repository's runtime | the LegalIt pilot |
| P-2 | Isolated prototype measurements (D142 alone vs the proposal): examined candidates per result by rejection reason, share attributable to shared indexed inputs, tie-run lengths, latency, returned-`k`/truncation rates, lexical/hybrid quality | WP-RS.3–6 on a branch; P-1 |
| P-3 | Owner decision against the proposal's investigation thresholds and adoption prerequisites | P-2 |

If adopted, it becomes WP-RS.3a onward, with its schema migration and E1/
forget changes, in the adopting pull request.

## Not in this delivery

- Embedding reuse across versions already exists in E1 (D80 carry-forward).
  The LegalIt pilot counts embedding calls for a second version of an act; a
  material miss rate (for example from renumbered section headers in the D80
  location header) is handled as a separate E1/D80 change.
- Reranking (for example the Jev model): a separate, later, reorder-only
  experiment after this delivery.
