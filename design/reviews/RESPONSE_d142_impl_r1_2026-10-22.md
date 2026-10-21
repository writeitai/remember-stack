# Response — D142 implementation review, round 1 (GPT-6.1 Sol)

Review: `REVIEW_gpt-6.1-sol_d142_impl_r1_2026-10-21.md` (on `304ada30b`).
Coordinator judgements applied: (a) keep bounded reconciliation, fix the
float slack and the stopping proof; (b) entity-scoped exact fact scans only
with a proven input-row budget; (c) every read of an operation in one
snapshot with pinned instants; (d) the LoCoMo harness must load a main
baseline with its own fingerprint; (e) accounts propagate end to end;
(f) measure.

Post-approval design changes made in this round, for the reviewers to judge:
the filter pushdown for document-filtered chunk searches (design §8.3,
P1-9), the operation context carrying a deadline and a pinned instant
(§8.2, D142), the query-dependent BM25 margin (§8.3, P1-2), `hnsw.ef_search`
200 in the semantic loop (§9 recall gate), full fact authority before the
nomination cut (§8.5, P1-5), and the restated owner-invariant scope (D142).

## P1

1. **Accounts end to end.** Port methods now return `P1SearchResult` /
   `P1SearchIds` explicitly (test doubles follow); `merge_accounts` lives in
   the port and keeps limits. `search_claims`/`search_chunks` disclose
   `p1_<reason>` and answer a capped empty search with a boundary; the
   testimony hybrid carries each channel's stop reason through RRF,
   `_bound_testimony_result` and `combine_evidence`; fact fusion and the
   profile channel (also with zero profile ids) merge accounts; `facts_context`
   reads the account. The sandbox publishes `fetched`/`rejected`. Failing-first
   tests: `src/tests/surfaces/test_d142_accounts.py` (4 of 5 fail on the old
   code).
2. **Float slack.** The margin is `(1+ε(n))⁴/(1−1/9)` with
   `ε(n) = 2(n+16)·2⁻²⁴` and `n = ⌊(len+1)/2⌋` analyzed-term bound; the
   reviewer's 40,000-term counterexample is emulated in float32 (ratio
   1.12533 exceeds the old margin, stays under the new one). On the built
   image, a randomized half-spilled corpus with lengths across the exact and
   quantized ranges and multi-term queries checks both inequalities and the
   index order for every streamed row against `bm25_get_current_score()`.
3. **One snapshot.** `QueryEngine._operation` owns a read-only REPEATABLE READ
   snapshot (deadline, pinned instant) for `search_claims`, `search_chunks`,
   `nominate_*`, `claims_and_sources_context` (entity check included),
   standalone `facts_context`; confirmation, hydration, entity checks and
   pending/freshness reads use its connection (`_read`, `_consistent_read`).
   The sandbox import spans confirmation and body hydration.
   `DocumentSearch` reads in one read-only REPEATABLE READ transaction.
4. **Pinned instant.** The pool's snapshot pins `evaluated_at` (its `now()`,
   or the exporter's); the adapter takes default scopes and fact times from
   it; engine searches always pass an explicit scope; the sandbox exports
   `now()` with the snapshot and `pg_confirmed_at` reports that instant.
5. **Fact authority before the cut.** The fact loop and the scoped branches
   join `facts_visible_history` before a row counts toward the candidate cut.
6. **Entity budgets.** The budget counts mention rows (and endpoint fact
   rows for scoped fact scans), reading at most budget + 1, before anything is
   scored; `nominate_testimony_scored` runs the shared coverage tiers; every
   caller answers `entity_scope_too_broad` with a typed boundary.
7. **Deadline.** The operation deadline bounds every nested read: the pool
   clamps `statement_timeout` to the remaining time on each checkout, the loop
   checks it before every fetch, and the sandbox passes the request's one
   statement deadline to every SRF. Timeout expiry answers `p1_deadline`.
   Tests: a deadline between fetches stops with `deadline`; nested reads run
   under a clamped timeout.
8. **Pending cap.** Both enumerations apply explicit `doc_ids` before the
   200-lineage cap and read one row past it; an overflow makes
   `scope_pending.complete` false even when nothing was found.
9. **Scaling stratum.** Filter pushdown (post-approval design change): a
   document-filtered chunk search enumerates its eligible rows from the
   matching versions; up to 5,000 are scored exactly, up to 20,000 are
   pushed into the index scan as a membership filter (limit 20,000, measured).
   At 3M chunks the 1% stratum's early stops fall from 25% to 0 (adapter p50
   618 ms / p95 827 ms, against 3.9 s / 4.5 s in the plain loop). **The
   latency half of the binding gate still fails**: p95 ≈ 300 ms is not met
   by the 1% stratum (0.8–3.1 s) nor by unfiltered `current` (2.3 s) at 3M on
   the test VM. Analysis §8.2 proposes design-level answers for the owner
   (immutable `doc_id` on `chunk_search` as an index-scan predicate; the
   distinct-text index; an explicit budget for filtered ranked search); none
   is implemented.

## P2

1. LoCoMo gate: per-run integrity against each run's recorded hashes (not the
   current protocol pins), identical question sets, complete judges, shared
   item/document/dataset hashes; tests include an old-fingerprint baseline.
2. Recall gate: the oracle shares the adapter's eligibility relation,
   snapshot and instant; the filtered stratum is sized above the exact tier;
   exact-tier answers make it inconclusive. Run on an isolated clustered
   100k-vector corpus: at pgvector's default `ef_search` 40 it **failed**
   (0.887); with `ef_search` 200 now set by the loop it **passes**:
   recall@10 0.999 unfiltered, 0.9975 with a 1,000-document filter (min 0.9).
   Synthetic vectors; a real-embedding run remains with WP-RS.0's copy.
3. Image gates: every image build now runs pg_textsearch's upstream
   regression suite (82/82 pass on the pinned image); the plan marks the
   remaining adversarial cases accurately.
4. Sweep: public operation, fixed query set across sizes, eligibility
   counts, corpus/query hashes, 0.1%/1%/10% filters and both sides of the
   5,000-row tier, production-statement work proxy; results archived.
5. Owner invariant: traced per input for living and dated lineages
   (Selection, embedding by input hash, claim ids); the Claimify window is a
   strict expected failure; D142 states exactly what is proved.
6. Entity profiles validate vectors through the shared port contract;
   exhaustion counts skip zero-norm rows.

## Nits

- The tier is described as a chunk-search tier; claims use the loop.
- Attestation: `cursor_tuple_fraction = 1` plans the cursor like the attested
  SELECT, `plan_cache_mode = force_custom_plan` keeps prepared runs on it,
  and the caller's own settings are restored (Postgres test over eight
  auto-prepared runs).
