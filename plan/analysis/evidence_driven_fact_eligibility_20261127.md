# Evidence-driven fact eligibility on PostgreSQL 18

On 2026-11-27, a read-only replay of `facts_context` took 30.54 s on the
hosted WriteIt brain Project (2 vCPU, 4 GB, PostgreSQL 18.6, RememberStack
v0.22.4 at `p9_55_0076`). Two P1 confirmation batches took 23.09 s.
The replay used a stored vector and returned 50 facts and 40 evidence rows.
[D163 design](../designs/shared_retrieval_eligibility_design.md#43-facts-one-source-qualified-projection-one-endpoint-rule)

## Hosted workload and paired prototype

The Project had 40 documents, 40 versions, 812 chunks, 3,274 claims and
occurrences, 969 entities, 2,625 observations and 535 relations. Each claim
had one occurrence. Chunk and occurrence tables had ten range leaves each,
with Project rows in one. Evidence tables had 64 populated fact-ID hash
leaves. The store had zero periodised versions and carried claims.

With normal join planning, PostgreSQL reordered the D140 check to start
from roughly 380 chunks per candidate. It probed `chunk_claims` across the
range leaves for each chunk and checked fact evidence last, producing
1.23 million observation and 758,000 relation evidence probes. The paired
SQL uses correlated `LATERAL` subqueries with `OFFSET 0` so the candidate
binds its evidence, then each claim binds its occurrences and each chunk
binds its reading. Both branches used identical candidate IDs, snapshot
and time scope. Times below are branch execution times excluding planning.

| Kind | Candidates | Old gate | Evidence-first gate | Shared hits (old → new) | Eligible IDs | Symmetric difference |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Observation | 804 | 16.400 s | 0.434 s | 8,155,984 → 70,616 | 786 | 0 |
| Relation | 535 | 11.349 s | 2.919 s | 5,509,219 → 577,885 | 518 | 0 |

The capture exercised the supports-only branch. Both relation plans still
seeded 518,415 object walks, and both fact kinds reread unrelated historical
claims during source membership. The receipt includes five file hashes,
serving settings, plan counters and the native replay excerpt.
[Redacted plans and paired SQL](https://github.com/writeitai/remember-stack/blob/d441b89d99524230d3e7ae3ab39479f62400dffb/plan/analysis/evidence_driven_fact_eligibility_hosted_capture_20261127.md)

## Candidate lookup boundaries

PostgreSQL can treat an `EXISTS` with the candidate key only in its outer
`WHERE` as a semi-join. It can also hash a simple correlated `NOT EXISTS`.
Putting the fact key inside the evidence probe binds it before either claim
arm runs. In a reduced PostgreSQL 18.7 fixture the unfenced raw-support
absence check used all 64 leaves at 30,000 unrelated rows. With `OFFSET 0`
it used one leaf per candidate at 30,000 and 300,000 rows.
[PostgreSQL subquery planner](https://github.com/postgres/postgres/blob/REL_18_6/src/backend/optimizer/plan/subselect.c#L243-L280)

The deployed source view uses `EXISTS` for each fact kind. A join that
emits both qualifying links of one fact would repeat that fact, and broad
confirmation's ID map would hide the extra SQL row. Final confirmation
joins back to the input ranks. If the same fact is nominated at ranks 1
and 2, the correct result is one row at each rank. A set of fact IDs misses
both kinds of multiplicity error.
[Source view](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L315-L363) ·
[Broad confirmation](../../src/rememberstack/adapters/postgres_p1.py#L1675-L1691) ·
[Final confirmation](../../src/rememberstack/surfaces/query_engine.py#L6231-L6249)

Source membership and D140 time scope can succeed through different links.
A claim in a visible historical edition can supply source membership even
when that edition is out of scope. A second claim can supply in-scope support
through a surviving occurrence in an undeclared lineage after its origin
version is deleted. That claim is absent from history, and carried source
membership requires declared periods. Joining the two checks on one evidence
link would drop the fact.
[Source membership](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L322-L330) ·
[Carried membership](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L163-L179) ·
[D140 time check](../../src/rememberstack/core/text_scope.py#L256-L303)

Scoped nomination ranks by distinct requested anchor coverage, then vector
distance. Final confirmation counts distinct requested anchors again and
sorts each confirmed batch by coverage before the result cap. Two stored
endpoints can resolve to one requested anchor. Counting that anchor twice
can change the top `k` with identical fact IDs and nomination ranks.
[Scoped ranking](../../src/rememberstack/adapters/postgres_p1.py#L1450-L1465) ·
[Confirmation coverage](../../src/rememberstack/surfaces/query_engine.py#L6208-L6249) ·
[Result cap](../../src/rememberstack/surfaces/query_engine.py#L1515-L1538)

A keyed claim alone leaves the origin chunk and visible-version joins
reorderable. Keying the origin chunk first lets a missing origin skip
readability probes, including in the carried branch's shared origin-absence
check. A carried occurrence must be in the claim's own document because
`chunk_claims` carries no document key. The production history predicate
also admits ready origins in superseded versions or replaced readings.
[Historical claim SQL](../../src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py#L258) ·
[Carried claim SQL](../../src/rememberstack/spine/migrations/versions/p9_48_0069_shared_eligibility.py#L174)

The origin fence belongs in the bounded native path. A reduced PostgreSQL
18.7 fixture with 120,000 claims, 480,000 chunks and 120 range leaves plus
default showed why. Broad set-based history and current-testimony reads
took 336 ms and 242 ms. Applying the origin fence to those views made both
hit the five-second deadline. Broad fact and evidence reads inherit source
qualification and can incur the same multiplication through that path.

Bind each stored relation endpoint before recursion and bind its next
redirect inside recursion. A reduced PostgreSQL 18.7 fixture used one seed
and one lookup per hop for both endpoints (three and five hops) with
1,000, 100,000 and 1,000,000 unrelated rows in custom and generic plans.
Negative source probes stayed on one evidence leaf. The same-deployment
foreign key prevents an A → B → A chain in normal writes. The deployed
adapter checks the deployment at the seed and terminal, so it can accept
such a legacy-corrupt round trip. The keyed walk rejects its first foreign
hop. [Deployed adapter](../../src/rememberstack/spine/migrations/versions/p9_04_0025_coordinate_binding.py#L64) ·
[Redirect foreign key](../../src/rememberstack/spine/migrations/versions/p0_02_0003_entities_evaluation_e0_e1.py#L34)

The probes used existing indexes. `claims` has an unpartitioned primary
key. Retained range-leaf costs apply to `chunk_claims` and `chunks`. Sparse
statistics may have contributed to the hosted misestimate. A forced
nested-loop plan timed out. Refreshed statistics would still leave the
join order cost-based. The fences bind the inputs under default planning.

## Retained-store oracle

The conv-26 and conv-30 captures used PostgreSQL 19. LoCoMo backups archive
stopped volumes, which are tied to their PostgreSQL major. A one-time logical
copy into a disposable PostgreSQL 18 schema keeps the stored rows and vectors
available for the deployed-schema comparison.
[Conv-26 metadata](retrieval_filtering_20261112/conv26/metadata.json#L2) ·
[Conv-30 metadata](retrieval_filtering_20261112/conv30/metadata.json#L2) ·
[Volume backup](../../benchmarks/locomo/sharding/store_backup.py#L1248-L1263) ·
[PostgreSQL storage compatibility](postgres18_reference_server.md#failure-recovery-and-boundaries)

pgvector draws a random graph level for each HNSW index entry. Independent
builds can therefore change the ordered candidates before the gate sees them.
Physical copies of one prepared PostgreSQL 18 fixture preserve the index.
Comparing the raw cursor batches verifies that both gates saw the same inputs.
[pgvector graph construction](https://github.com/pgvector/pgvector/blob/v0.8.6/src/hnswutils.c#L245-L253) ·
[D142 fact cursor](../../src/rememberstack/adapters/postgres_p1.py#L1693-L1717)

## Migration evidence

An older D153 source definition lacks D159's rule making a withdrawal
review moot while its claim's document is archived. Use the deployed
`p9_55_0076` definition and its regression test for a fact with surviving
support from another document.
[Archive-aware DDL](../../src/rememberstack/spine/migrations/versions/p9_55_0076_archive.py#L187) ·
[Archive regression](../../src/tests/workers/test_lifecycle_reconciliation.py#L2255)

A reduced PostgreSQL 18.7 fixture reproduced a `40P01` deadlock when one
transaction replaced both a survivor view and a dependent fact source
view while a reader held the fact view and waited for the survivor.
Separate compatible revisions let the fixture upgrade and downgrade
after `55P03`.
[D161 runner](../../src/rememberstack/spine/migrations/env.py#L68)

The v0.22.4 image ships an `alembic.ini` that sets a 60-second lock wait.
The runner sets the timeout for the session, so it applies to the advisory
lock and to DDL. An interactive saved query queued behind an exclusive view
lock request can hit its 250 ms lock limit. The runner's three-second
default bounds each migration lock wait, and retry backoff gives readers a
chance to acquire locks between attempts.
[Released config](https://github.com/writeitai/remember-stack/blob/v0.22.4/alembic.ini#L5-L6) ·
[Image build](../../Dockerfile#L21) ·
[Runner configuration](../../src/rememberstack/spine/migrations/env.py#L57-L81) ·
[Session lock timeout](../../src/rememberstack/spine/migrations/env.py#L98-L114) ·
[Interactive lock limit](../../src/rememberstack/surfaces/query_sandbox/limits.py#L48-L52)

The first backoff lasts 250 ms, so API dispatch can race the next exclusive
view lock request. The existing migration test pauses the runner at the
retry log, after the failed connection closes and before the next attempt.
Use that synchronization to run the saved query with the shipped timeouts.
[Retry delay](../../src/rememberstack/spine/migrations/env.py#L30-L33) ·
[Retry barrier](../../src/tests/spine/test_migrations.py#L244-L284)

Stock `setup` applies migrations and then publishes the new manifest hash,
suspending active saved queries in the same transaction. A previous-release
API still expects its own hash and rejects the new one even after successful
revalidation. Running Alembic directly keeps the old hash and active versions
in place through the compatible DDL. After the old readers drain, the
incoming release's `setup` can publish using the existing registry transaction.
[Setup](../../src/rememberstack/profiles/selfhost.py#L1248-L1268) ·
[Registry hash check](../../src/rememberstack/surfaces/query_sandbox/saved_queries.py#L1364-L1373) ·
[Registry publication](../../src/rememberstack/surfaces/query_sandbox/saved_queries.py#L1846-L1897)

Full-schema equivalence, migration and latency are implementation gates.
[D163 qualification](../designs/shared_retrieval_eligibility_design.md#91-d163-fact-path-qualification)
