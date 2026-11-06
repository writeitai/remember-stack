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

An unlimited timeout is rejected because expensive requests could monopolize a
slot indefinitely. Merely raising the deadline does not correct repeated SQL
work. Candidate materialization and per-candidate authority checks will be
measured on the copied store before changing resolution SQL. Bypassing the
current-entity authority or weakening forgotten/merged-entity visibility is not
an acceptable optimization. No schema, ingestion generation, extraction input,
embedding identity, model call or paid-product reuse boundary changes here.

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
