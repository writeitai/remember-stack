# Retrieval database deadline and slow identity lookups

Status: non-binding analysis, 2026-11-06.

The completed conv-47 validation at engine revision 9266b8dd recorded 24 empty
whole-operation deadline boundaries in 69 tool calls: 13 entity resolutions,
eight fact contexts and three claim/source contexts. Its 25-second timeout was
hard-coded in QueryEngine. These are incomplete retrieval attempts, distinct
from terminal answer-generation or judging failures. Preserve that run and its
verified GCS archives; diagnostics use an isolated copy of its saved store.

A replay of exact-name resolution for John spent 24.176 seconds in its T0 SQL
statement, returning one entity. EXPLAIN ANALYZE took about 31 seconds, with
22,643,495 shared buffer hits and no disk reads. A nested entity/provenance
subplan ran 362,732 times. PostgreSQL reported active SQL with no wait event;
connection admission and snapshot setup took milliseconds. Repeating with JIT
on and off produced the same plan and approximately 31 seconds in both cases.
This establishes repeated authority-view work on this request, not an embedding
or connection-pool bottleneck. It does not yet establish the cause of every
fact/source timeout. The index-driven chunk search measurements in PR #505 did
not measure this exact entity-resolution query.

Expose a finite positive per-engine database budget, with a 120-second default
and REMEMBERSTACK_SELFHOST_RETRIEVAL_DATABASE_BUDGET_S in the stock profile.
Library hosts can set it independently in each EngineSettings deployment or
QueryEngine constructor. A single absolute deadline still covers admission and
nested reads; a new statement must not reset it. Admission concurrency and its
short queue-wait limit remain separate. Longer budgets can occupy a read slot
longer and consume more CPU, so retain bounded pools and typed deadline results.
Client and proxy timeouts must exceed the configured budget. Combined context
runs two independently bounded children, so LoCoMo will freeze a 300-second
transport timeout alongside the new server setting, allowing both 120-second
children plus overhead.
Existing saved profiles and results are not rewritten.

Budgets above 3600 seconds, including an unlimited timeout, are rejected because expensive requests could monopolize a
slot indefinitely. Merely raising the deadline does not correct repeated SQL
work. Candidate materialization and per-candidate authority checks will be
measured on the copied store before changing resolution SQL. Bypassing the
current-entity authority or weakening forgotten/merged-entity visibility is not
an acceptable optimization. The schema change below replaces only a view. No ingestion generation,
extraction input, embedding identity, model binding or paid-product reuse
boundary changes here.

## Candidate-first identity check

Materializing distinct exact-alias candidate IDs and hydrating each through a
correlated `entities_current` lookup with `OFFSET 0` returned the same entity
in 0.688 seconds (85 ms planning, 572 ms EXPLAIN execution), with 338,044 buffer
hits. Disabling nested-loop joins globally took 3.76 seconds and still performed
1,784,472 hits, so the narrower query rewrite is preferred. Neither variant
changed data or the visibility authority. These are copied-store observations,
not a latency guarantee or an end-to-end benchmark score.

Use the same candidate-first hydration for fuzzy/phonetic candidates. Exact
homonyms remain uncapped and all candidates still pass the current-alias and
current-entity authorities in one snapshot. The SQL barrier stops the planner
from expanding a full current-entity view for every alias; it does not cache or
materialize a stale entity projection. Existing identity tests must continue
to exclude merged and forgotten entities and preserve homonym ambiguity.

## Unused entity count work affects fact confirmation

A complete fact-context replay still hit the raised 120-second deadline. One
P1 confirmation statement took 94.525 seconds to confirm 804 observations;
another took 18.300 seconds for 72 relations. A saved entity-scoped source
request completed in 50.412 seconds with 50 claims and 50 chunks. These are
SQL costs, not an answering-model delay. Its query embeddings took 0.84–2.49
seconds and their recorded charges totaled $0.00000021 for the two requests.

`entities_current` joins a one-row count aggregate with CROSS JOIN LATERAL.
Even identity-only consumers then execute the unused mention/document count
work repeatedly as fact views check entity visibility. Replacing this join
with LEFT JOIN LATERAL ... ON true allows PostgreSQL to remove it when neither
count is selected. The aggregate has no GROUP BY or HAVING and always returns
exactly one row, so count values and row membership are unchanged. Explicit
provenance EXISTS checks remain mandatory.

A rollback-only copied-store prototype preserved both counts for all 118
entities. The original unmodified exact-name SQL completed in 0.370 seconds.
The 72-relation and 804-observation statements completed in 3.966 and 3.863
seconds respectively, with identical candidate counts. Apply this as a view-only
Alembic migration; it rewrites no data or paid artifacts. Existing visibility,
forgetting, merge and exact-count checks remain applicable. Add a PostgreSQL
EXPLAIN regression proving identity reads omit the sum aggregate while explicit
count reads still calculate it. This guards the performance contract rather
than matching the SQL spelling.

The longer server budget also requires longer first-party transport defaults.
Set Python and TypeScript request defaults to 300 seconds, and expose the CLI
request timeout through --timeout and REMEMBER_TIMEOUT with the same default.
Keep explicit client overrides and the one-second admission wait independent.
A larger operator-selected server budget also requires a correspondingly larger
client/proxy timeout. Transport expiry can leave server SQL running until its
own deadline; this change does not add disconnect-driven cancellation.

Frozen older v39 profiles remain readable as recorded runs, but current-pin
validation correctly refuses to execute them under different query settings.
Prepare a new identity to run the changed policy on a restored store. Do not
rewrite an old fingerprint or trigger re-ingestion.

## Public surface identity

The view rewrite changes its canonical SQL AST even though row semantics are
preserved. Register the new migration's authored DDL in query-space source
definitions and regenerate the manifest. A dedicated regression compares the
migration's definition with the published definition. Roll the current LoCoMo
surface pin and SDK metadata; old recorded runs retain their existing hashes.

On ordinary setup, publishing the new manifest atomically moves active saved
query versions to pending_revalidation. Shipped examples are reseeded through
the existing setup policy, and customer-authored queries require revalidation.
That is the existing surface-change contract, not ingestion or paid-product
reprocessing. The direct copied-store query timings above precede this metadata
integration correction; the retrieval SQL and view body remain identical.
