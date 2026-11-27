# Evidence-driven fact eligibility on PostgreSQL 18

The WriteIt brain Project spent 23.09 of 30.54 seconds confirming two batches
of facts on 2026-11-27. PostgreSQL looked through a large fraction of the
Project's chunks for each candidate before checking which claims belonged to
that fact. The proposed D153 amendment keeps both the source and time checks
and starts with the candidate's evidence. [Design](../designs/shared_retrieval_eligibility_design.md#43-facts-one-source-qualified-projection-one-endpoint-rule)

## Captured workload

The [hosted capture receipt](evidence_driven_fact_eligibility_hosted_capture_20261127.md)
records file hashes, redacted plans, the paired SQL and native replay counters.

The investigation ran on RememberStack v0.22.4 (`c1f15b783`, schema
`p9_55_0076`) on a 2-vCPU/4-GB PostgreSQL 18.6 host. The Project contained
40 documents and 40 versions, 812 chunks, 3,274 claims and 3,274 chunk–claim
occurrences, 5,307 mentions and 969 entities. There were 2,625 observations
with 2,651 evidence rows and 535 relations with 538 evidence rows. Each
evidence table had 64 nonempty hash partitions by fact ID. The chunk and
occurrence tables each had ten `created_at` partitions including default,
with Project rows in one. All claims had one occurrence in this capture.
Of the 812 chunks, 430 carried claims, averaging 7.61 links in those chunks
(maximum 29). The store had zero periodised versions and zero carried claims.

`native-replay.out` records a read-only `facts_context` replay at 18:38 UTC
using one stored vector and zero paid calls. It returned 50 facts and 40
evidence rows in 30.536 s wall and 30.2135 s SQL across 154 statements.
At candidate depth 200, the first fetch requests up to `4 × (200 + 1) = 804`
IDs per kind. The replay used one fetch per kind, followed by final hydration
in groups of 30 split by kind. There was no negative result or refill.

## Live confirmation plans

`live-plans.out` contains the catalog, counts, and standalone observation and
relation `EXPLAIN (ANALYZE, BUFFERS, VERBOSE, SETTINGS, FORMAT JSON)` plans
at 18:35 UTC. These and the paired plans were read-only, with serving join
planner settings, JIT off and a 3 GB effective cache estimate.

| P1 confirmation | Input → eligible | Native SQL | Plan execution / planning | Shared hits | `chunk_claims` leaf probes | Late evidence probes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Observation | 804 → 786 | 13.224 s | 15.683 s / 198 ms | 8,155,986 | 3,093,280 | 1,232,750 |
| Relation | 535 → 518 | 9.867 s | 12.178 s / 143 ms | 5,509,219 | 1,884,710 | 757,668 |

Both plans had zero shared reads and temp writes. Inside the D140 support
subplan the physical order was:

```text
candidate fact
  → chunks joined to version scope (394.05 / 364.34 chunk rows per gate call)
  → chunk_claims Append, indexed by chunk_id across ten created_at leaves
  → fact-ID hash leaf, test (fact_id, claim_id) and stance = supports last
```

The hot intermediate join estimated 13 rows per call and produced 1,568.38
for observations and 1,462.68 for relations. The late probe counts above
are summed across executing leaves. `src/rememberstack/core/text_scope.py`
already spells the joins in evidence-first order. PostgreSQL can reorder
ordinary inner joins, so the fact filter only became selective at the end
of these plans. This creates work proportional to candidates reaching the
gate times unrelated chunks visited times retained partitions, plus the
claim links encountered. The evidence check is separate from D153's fact
source membership and shared `entities_current` endpoint checks. [D140 gate](../../src/rememberstack/core/text_scope.py#L226) · [P1 batch builder](../../src/rememberstack/adapters/postgres_p1.py#L1664) · [Shared authority](../../src/rememberstack/spine/retrieval_eligibility.py#L172)

The relation's `fact_sources` subplan also applied its `object_entity_id`
join filter after scanning all 969 entities for each of 535 source rows.
That is 518,415 object-chain seeds and about 2.7 s in the live plan. The
subject chain was already seeded by its stored entity ID. A smaller cost in
both kinds came from re-reading a materialized historical-claim stream inside
source qualification (about 1,567/1,460 rows per fact). The scope gate
rewrite leaves these source-helper costs in its measured totals. They explain
why the complete fact path also needs keyed endpoint and source-claim probes.

## Paired evidence-first prototype

`candidate-driven-plans.out` at 18:36 UTC compares the two SQL shapes on the
same candidate IDs, snapshot and scope. The rewritten branch uses correlated
`LATERAL` subqueries with `OFFSET 0` to keep the outer fact's ID attached to
the evidence lookup, then the claim ID to the occurrence lookup and the chunk
ID to the reading lookup. Its hot order was:

```text
candidate fact ID → one fact-ID hash leaf
  → chunk_claims by claim_id → chunks by chunk_id
  → document_versions and document_version_scope by version/deployment
```

| Kind | Old branch | Evidence-first branch | Shared hits, old → rewrite | Eligible IDs on each side | Symmetric difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Observation | 16.400 s | 0.434 s | 8,155,984 → 70,616 | 786 | 0 |
| Relation | 11.349 s | 2.919 s | 5,509,219 → 577,885 | 518 | 0 |

These are executor times of the two CTE branches, excluding the paired
statement's planning (394/288 ms) and difference computation. Both directions
of `EXCEPT` returned no IDs. The support subplans executed one evidence leaf
per gate call and, with early exit, touched 3,930 observation and 2,590
relation occurrence leaves in total. The populated occurrence leaf used the
existing `ix_chunkclaims_claim` child index. The small observation hash leaves
sometimes used local sequential scans. The relation object walk still seeded
518,415 chains in the rewritten plan. The capture measures the gate rewrite
only. Hosted latency for the endpoint change and equivalence with periodised,
replaced, deleted or contradiction-only evidence still need qualification.

## Source helper and survivor probes

The deployed memory survivor view joins the seed and terminal deployment
around a walk that joins the entity registry again on each redirect. A
correlated lookup of that view constrains its result. A seed keyed by the
stored endpoint and a separately keyed lookup inside recursion constrain the
work. Put the walk in the deployment-keyed view so the graph adapter can
project it without maintaining a second walk.
[Memory adapter](../../src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py#L64) ·
[Forward walk](../../src/rememberstack/spine/migrations/versions/p9_45_0066_fact_counts_demand.py#L13)

PostgreSQL can pull up an `EXISTS` with the candidate correlation in its
top-level `WHERE` as a semi-join. A claim lookup can be correctly keyed
while that join still does broad evidence work. Putting the fact and
deployment predicates inside the raw evidence lateral probe makes the
dependence explicit before either claim arm runs.
[PostgreSQL 18.6 planner](https://github.com/postgres/postgres/blob/REL_18_6/src/backend/optimizer/plan/subselect.c#L1452-L1512) ·
[Deployed source helper](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L316-L363)

A disposable PostgreSQL 18.7 fixture used the seed and recursive hop keys
from §4.3, the existing entity indexes, and 64 hash leaves per evidence
table. The prepared source-helper query fixed the deployment, kind and
fact ID. It used two fixed candidates per kind and grew unrelated entities
and evidence rows per kind together. The positive relation had three
subject and five object redirects. The positive observation used the same
subject. The negative candidates had one invisible claim and one carried
claim at a mismatched document coordinate.

| Unrelated rows per table | Prepared plan modes | Relation subject index searches (seed + hops) | Relation object index searches (seed + hops) | Observation subject index searches (seed + hops) | Negative evidence per kind (leaves / rows returned by raw probe / rows rejected by raw probe) |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1,000 | custom and generic | 1 + 3 | 1 + 5 | 1 + 3 | 1 / 2 / 7 |
| 100,000 | custom and generic | 1 + 3 | 1 + 5 | 1 + 3 | 1 / 2 / 0 |
| 1,000,000 | custom and generic | 1 + 3 | 1 + 5 | 1 + 3 | 1 / 2 / 0 |

The entity index conditions used both deployment and entity ID. At 1,000
unrelated rows the negative probes scanned one small leaf. At both larger
sizes each used one fact-ID index search. The result was one row for each
positive candidate and zero for each negative. Missing seeds, cycles,
dangling targets and wrong-deployment seeds or redirects also returned no
survivor. The fixture modeled claim visibility with boolean flags. Full
qualification uses the migrated authority and real historical and carried
membership paths. [D163 qualification](../designs/shared_retrieval_eligibility_design.md#91-d163-fact-path-qualification)

## Round-two source checks

- BEAM skips saved answers by arm and question ID and uses the supplied URL
  for the rest. LoCoMo checks serving revision and discovery, which reads a
  packaged manifest. Neither check proves which database serves the next
  request. Compare the live catalog and verified restore binding in the
  harness before any question runs. This also works with the published
  engine, which needs no new endpoint.
  [BEAM loop](../../benchmarks/rs_harness_beam/answer_agent.py#L725) ·
  [LoCoMo checks](../../benchmarks/locomo/runner.py#L641) ·
  [Discovery](../../src/rememberstack/surfaces/query_sandbox/discovery.py#L106)
- The hosted replay exercised the supports-only path. A plain correlated
  `NOT EXISTS` can be planned as a hashed scan across evidence leaves.
  `OFFSET 0` inside that subquery prevents the rewrite. At the source-view
  boundary, knowing the claim ID still leaves its chunk and visible version
  as reorderable joins. Bind the chunk inside the historical view and reuse
  that view for the carried path's origin-absence check. A missing origin
  then skips the version and representation lookups.
  [Hosted SQL](evidence_driven_fact_eligibility_hosted_capture_20261127.md#paired-sql) ·
  [PostgreSQL 18.6 planner](https://github.com/postgres/postgres/blob/REL_18_6/src/backend/optimizer/plan/subselect.c#L243-L280) ·
  [Historical view](../../src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py#L257) ·
  [Carried view](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L174)
- `CREATE OR REPLACE VIEW` takes an exclusive lock on its target. A reader
  can lock the fact view and then wait for its survivor dependency, while a
  transaction replacing both waits for that reader. Each intermediate
  definition supports the existing caller, so commit each in its own
  revision and let the native runner retry ordinary lock timeouts from the
  last committed revision.
  [PostgreSQL 18.6 view replacement](https://github.com/postgres/postgres/blob/REL_18_6/src/backend/commands/view.c#L89-L107) ·
  [Native runner](../../src/rememberstack/spine/migrations/env.py#L68)

The deployed survivor adapter checks the seed and terminal deployments.
The same-deployment foreign key rules out a round trip through another
deployment in normal writes. The new walk fails closed when legacy corruption
breaks that invariant, so the oracle needs a specific exception for that chain.
[Adapter](../../src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py#L64) ·
[Redirect foreign key](../../src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L32)

## Prior qualification and alternatives

- D142's 300k-chunk gate measured BM25 chunk retrieval at 263/267 ms unfiltered
  p95 on an 8-vCPU/64-GB host. It scoped exact ranking and attested broad
  nomination. Fact support checks were outside that latency workload.
  [D142 latency amendment](../../decisions.md#d142-ranked-search-nominates-from-the-index-and-confirms-in-one-snapshot)
- #542 cut median fact SQL from 12.30 s to 2.22 s on analyzed conv-47 data
  (31 documents, 889 facts, 822 chunk–claim links). Growing unrelated entities
  to 100k held facts and mentions fixed. The change made unused counts prunable,
  bound hydration to IDs and reused identical request embeddings. The
  following #561 replay measured 2.253 s median and 2.345 s p95 for facts.
  [#542](https://github.com/writeitai/remember-stack/pull/542) · [#561](https://github.com/writeitai/remember-stack/pull/561) · [Work amplification analysis](retrieval_work_amplification_20261106.md)
- D153 qualified fixed-store native fact reads below 20 s on retained conv-26
  and conv-30 copies (584/514 visible facts). Scoped facts took 9.17/6.74 s,
  and broad conv-26 facts took 9.62 s. Its 120-month coverage and
  million-document bridge tests measured other parts of the read path. D153
  permits linear leaf probes for **a known coordinate**. This incident is
  the multiplication of those probes by unrelated coordinates.
  [D153 qualification](shared_eligibility_qualification_20261112/qualification-summary.md)
- A join-method override can help one statement and remove efficient indexed
  point lookups elsewhere. The whole-query nested-loop override timed out in
  the investigation. In #542, restoring ordinary join planning reduced a
  fenced 100k-entity replay from 31.4 s to 2.45 s. Query-local correlated
  inputs express the real dependency under normal planning. A full-project
  materialization, persisted flags, a new global coordinate map or more CPU
  would either add unrelated work or introduce a new state or capacity cost.
  The prototype used the existing serving indexes and showed the gain with
  the existing partition layout. [Commit 52de39926](https://github.com/writeitai/remember-stack/commit/52de39926fc6b4c22b055db8993d9c731f5df9e8) · [#542 replay](retrieval_work_amplification_20261106.md#bound-the-final-hydration-too)

## Statistics after bulk ingestion

The catalog showed valid/ready `ix_chunkclaims_claim` and both D153 indexes
(mention lookups and the ordered document bridge). The sampled partition
parents had neither manual nor automatic analysis recorded. Only 9 of 64
observation-evidence leaves and 0 of 64 relation-evidence leaves showed
autoanalyze. The populated occurrence leaf last autoanalyzed at 12:50 UTC
and estimated 3,055 tuples with 3,274 live. All 64 leaves per evidence table
held rows, including relation leaves with single-digit populations.

Sparse statistics plausibly contributed to the bad cost estimate. The
morning plan is unavailable and the capture ran no `ANALYZE`, so neither the
trigger for the plan change nor the effect of refreshed statistics is known.
PostgreSQL 18 autoanalyzes individual leaves above a modification threshold
with a default 50-change base. Autovacuum does not analyze the partition
parent. `ANALYZE` on a named parent samples the partition tree and by default
also refreshes the leaves. On a restored copy, compare both SQL shapes with
sparse and refreshed statistics, measuring plans, row work and latency.
Targeted parent analysis after bulk ingestion or restore is operator
maintenance where that experiment shows a benefit. The evidence-first plan
must pass with sparse statistics too. PostgreSQL 18 references checked
2026-11-27. [ANALYZE](https://www.postgresql.org/docs/18/sql-analyze.html) · [Autovacuum thresholds](https://www.postgresql.org/docs/18/runtime-config-vacuum.html#guc-autovacuum-analyze-threshold)
