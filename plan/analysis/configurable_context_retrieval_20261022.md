# Configurable context retrieval depth

Status: non-binding analysis, 2026-10-22. The accepted operating contract is in
the context-operation section of `open_query_space_design.md`.

## Problem and evidence

Context retrieval has two different bounds: how many candidates search nominates,
and how many confirmed records reach the consumer. The engine currently nominates
200 facts and defaults to returning 15, with a hard maximum of 30. Sources nominate
200 candidates independently in semantic and keyword channels for each grain and
return up to 50 claims and 50 chunks. The 20-claim/20-chunk cut in the controlled
LoCoMo experiment was an experimental shortlist, not the production default.

The source-only comparison on 60 development questions increased complete gold
spoken-turn wording in the candidate pool from 48/60 to 56/60 when retaining 60
claims and 60 chunks instead of 20 each. Final answer scores did not consistently
improve. Gold turns were checked after inference and never entered retrieval.
These measurements neither establish an optimal default nor measure facts, which
were excluded. The experiment and complete archived artifacts are recorded in
the UMC research PR https://github.com/writeitai/ultimate-memory-cloud/pull/762.

The old 15-fact default bounded expensive membership and evidence reads. The
earlier performance investigation in `fact_context_authority_performance.md`
documents real timeouts and the resulting shared deadline. It is not evidence
that 15 facts is enough for answer quality. Facts are the ordinary agent's primary
route, so retaining far fewer facts than source assertions has no measured quality
justification. Fifty per grain is a reasonable configurable starting default;
latency, returned text size and quality must be measured rather than assumed.

## External comparison, inspected 2026-10-22

Mem0's open-source search uses `internal_limit = max(limit * 4, 60)` for semantic
and keyword nomination, then scores and bounds the returned memory list to
`top_k`. Its current public LoCoMo runner defaults to 200 retrieved memories and
evaluates several final cutoffs. The managed platform has additional proprietary
behavior; inspecting OSS does not establish its internal candidate depths.

Primary sources:

- https://github.com/mem0ai/mem0/blob/main/mem0/memory/main.py
- https://github.com/mem0ai/memory-benchmarks/blob/main/benchmarks/locomo/run.py
- https://github.com/mem0ai/memory-benchmarks/blob/main/benchmarks/common/mem0_client.py

## Chosen limits and alternatives

Expose `k` and optional `candidate_k` uniformly on all three content operations.
Use k=50 by default. When candidate_k is omitted, derive max(4*k, 60). Accept
k from 1 through 100, candidate_k from 1 through 400, and require candidate_k >= k.
The source operation applies k independently to claims and chunks; the combined
operation applies the same explicit controls to both children. Preserve typed
collections and the existing ranking and eligibility authorities.

For the authorized full LoCoMo answer-and-judge run, freeze k=50 and candidate_k=200.
The user selected 50 facts, 50 claims, and 50 chunks after considering larger settings.
Allow custom depths at prepare time and fingerprint the resolved values. Enforce
them when dispatching context tools, record effective arguments in the trace, and
show the profile to the reader. This matches the inspected retrieval-depth rule,
not Mem0's architecture or total token budget: requesting all three grains may
return 150 primary records plus attached fact evidence.

A shared global top-k list is rejected because it introduces a new policy for
competition between facts and their source claims/passages. Changing query text
or adding a model reranker is also outside this change. Retaining a small fact
cap while only widening source search would leave the primary fact path bounded
by the unmeasured inherited cap.

## Cost, correctness, and failure consequences

Larger pools increase indexed search and confirmation work, response sizes, and
reader input costs. Keep bounded database batches and the existing whole-operation
deadline. A deadline must remain a disclosed boundary, not a fresh budget per
batch or an excuse to return unconfirmed text. No new unbounded scan is allowed.

Reserve max(60, 2*k) fact-evidence associations so every selected fact can show one
available supporting and contradicting claim before deeper evidence is allocated.
Return exact evidence totals and truncation. Do not let a fixed 60-association
budget leave the tail of a 100-fact result without available evidence.

Advance operation versions and the manifest when contracts change. Give a run
with different retrieval depths a distinct fingerprint, preserve canonical scores
and backup archives, and keep labels out of the reader. Reusing existing ingested
stores does not authorize silently rewriting an earlier run's identity.

Validation must cover bounds, automatic overfetch, both fact evidence stances at
the largest result size, propagation through combined retrieval, and benchmark
fingerprints and effective tool arguments. Measure the high-depth path against
isolated restored data, without re-ingestion, before claiming performance or answer-score improvements.
