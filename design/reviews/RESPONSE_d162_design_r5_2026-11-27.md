# Response to D162 design review, round 5

PR #681, reviewed head `330285c7e`. Accepted all seven priority findings
and the adjacency nit.

Source probes reproduced the D143 admission race, OpenRouter's acceptance
of a singleton at index `1`, and the adapter/P1 disagreement on a
correctly indexed zero vector. The upstream BM25 archive matched the
image's pinned SHA-256. Existing focused tests passed 272 cases and
skipped 156 PostgreSQL cases with `REMEMBERSTACK_DATABASE_URL` unset. The
new concurrency, scale and benchmark cases are implementation release
gates.

| Finding | Resolution |
| --- | --- |
| P0 · Source-head timing controls facts admission | Search-ready publication records the facts obligation. D143 retains it and admits the oldest eligible basis. With C held before claims, B must build its fact and settle whether C took the source head before or after B's facts admission. [D143 advance](../../plan/designs/processing_layers_design.md#52-one-advance-procedure), [current target and admission](../../src/rememberstack/spine/processing_layers.py#L707-L756), [current discharge](../../src/rememberstack/spine/processing_layers.py#L897-L925) |
| P1 · Claims cutover can lose semantic matches and alter scores before commit | Grounding opens private claim embedding. Both claim indexes must be ready before the basis and currency switch. The gates stall embedding and compare both channels through a held-open switch and rollback. [Publication](../../plan/designs/split_oversized_chunks_design.md#publication), [claim worker](../../src/rememberstack/workers/p1.py#L99-L144), [partial indexes](../../src/rememberstack/spine/migrations/versions/p9_13_0034_postgres_p1_search.py#L118-L132) |
| P1 · Fixed vectors bypass the query embedder | The qualified E1 path owns strict decoding and retries. Protected readers pin query preparation, the embedder and its effective routing. A changed query path must replay recorded raw replies through the public tools, including the singleton case. [Benchmark admission](../../plan/designs/split_oversized_chunks_design.md#benchmark-admission), [query embed call](../../src/rememberstack/surfaces/query_engine.py#L4838-L4866), [shared adapter](../../src/rememberstack/adapters/openrouter.py#L1001-L1077) |
| P1 · Restored support can hide the reviewed evidence | The verdict authorizes its exact retained occurrence for reads and D140 periods. Verify its quote, support and interval after repair and through archive and unarchive. [Reviewed occurrences](../../plan/designs/split_oversized_chunks_design.md#publication), [restore implementation](../../src/rememberstack/spine/review.py#L352-L402), [interval query](../../src/rememberstack/spine/period_adjudication.py#L47-L85) |
| P1 · Eligibility can return before the entity worker sees retirement | The lineage transaction fences the sequence and attempt at last-membership loss. Decision publication, final apply and re-admission observe the fence. Repoint both before and after worker cleanup. [Dispatch](../../plan/designs/split_oversized_chunks_design.md#dispatch-and-writes), [stream and decision checks](../../src/rememberstack/spine/fact_applications.py#L402-L443) |
| P1 · A corpus rebuild can freeze writes and strand cutover | Build online, capture serving writes durably and bound the final switch. A warmed spare lets first publication index only new rows. Require successful repair and first publication under sustained writes. [Projection publication](../../plan/designs/split_oversized_chunks_design.md#search-projection-publication), [P1 scale gate](../../plan/designs/postgres_p1_search_projection_design.md#9-index-and-planner-contract), [BM25 scoring totals](https://github.com/timescale/pg_textsearch/blob/fa142d049d08eac45a263f5abc250c8877ae076e/src/scoring/snapshot.c#L178-L204) |
| P2 · Correctly mapped vectors can be unusable | Run `require_p1_vector` on every mapped item before attesting any. The valid-plus-zero batch fails as a typed invalid response. [E1 provider contract](../../plan/designs/split_oversized_chunks_design.md#4-provider-rejection-and-retry), [P1 validator](../../src/rememberstack/ports/p1_index.py#L26-L49) |
| Nit · Adjacency example | The example now uses a single target and `window=1`. [Windowing](../../plan/designs/adjacent_chunks_retrieval_design.md#1-principles-binding) |
