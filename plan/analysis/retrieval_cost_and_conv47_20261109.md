# Retrieval work, proposed simplifications and conv-47 evidence

Date: 2026-11-09. **Non-binding analysis.** This document records the owner's
questions, the inspected implementation, alternatives and measurement evidence.
It does not amend D3, D56, D140 or D142 and does not authorize a ranking or
history-policy change. The owner authorized the analysis PR and measurement of
the existing behavior, then investigation of conv-47's three canonical misses.
The full LoCoMo campaign remains paused.

## 1. Problem and scope

After engine [PR #542](https://github.com/writeitai/remember-stack/pull/542),
the retained 31-document conv-47 store answered all 20 selected development
questions without terminal failures. It scored 17/20. Its 60 retrieval calls
had no typed negative results, including no database-budget boundaries.
Live median fact-context and claims/sources-context times were 1.545 and
1.776 seconds. Those are complete tool timings, not bare index-search times.

The owner considers that latency too high for this small corpus and asks
which work is necessary, which work is duplicated, and whether search can
return useful historical testimony for an agent to interpret. Correctness
must survive at the engine's intended million-document scale. A benchmark
improvement alone does not establish that requirement.

The immediate deliverable is measurement, with no new ingestion, rewriting,
reranker, model substitution, result-depth change, judge adjustment or
question-specific retrieval rule. Claims intentionally remain immutable source
testimony; adjudicated semantic supersession remains on facts.

## 2. Source inventory and baseline identities

Inspected engine main: `fb4a5f5a4cc2eb415df3a8f5b7908bb506bf58d0`
(v0.19.0). Its query engine, PostgreSQL P1 adapter and text-scope implementation
are unchanged from the evaluated merge
`8cd181fe0905dc4f3e764fb4958a5551c4b836ee`. Pin the actual measured source and
runtime in every report rather than treating that equality as a full-image
or ingestion equivalence claim.

Binding contracts and implementation:

- [Immutable claims and evidence lifecycle](../designs/evidence_lifecycle_design.md),
  especially sections 2, 3, 7 and 8: source-version modes, testimony currency,
  history and deletion.
- [P1 ranked search](../designs/postgres_p1_search_projection_design.md),
  sections 8.2–8.6: one snapshot, nominate/confirm/continue, entity coverage,
  fact eligibility and truthful early-stop reporting.
- [Effective document time](../designs/effective_time_and_section_references_design.md)
  and [compiled time predicates](../../src/rememberstack/core/text_scope.py).
- [Context operations](../designs/open_query_space_design.md), section 3.1:
  facts and claims/sources are separate operations with configurable depth.
- [Query engine](../../src/rememberstack/surfaces/query_engine.py):
  `facts_context`, `_claims_and_sources_context_retrieval`, `_confirm_claims`,
  `_confirm_chunks`, `_touched_pending`, `_scoped_freshness` and `_embed`.
- [PostgreSQL P1 adapter](../../src/rememberstack/adapters/postgres_p1.py):
  `nominate_testimony_scored`, `_entity_tiers`, `nominate_facts_scored` and
  `chunk_texts`.
- [D91 request metering](../designs/request_path_metering_and_cost_export_design.md):
  embedding reuse must record actual provider calls once.
- Historical measurements, source-reuse provenance, answers and verified GCS
  receipts: [UMC PR #783](https://github.com/writeitai/ultimate-memory-cloud/pull/783),
  merged as `69cc16469703e6ed36686fdc5c2d7c44176a8b9b`.
  Latest saved R4 state SHA-256:
  `2e6c04cfdb77bddee0293a474d21cc80954551fbbfc36fbddd647cbd64d2c2dd`.

## 3. Vocabulary: chunks, claims, facts and loading content

A **chunk** is a retrieval unit of converted source text. It can retain
surrounding dialogue, paragraph details and converted image/OCR information
that extraction did not turn into a claim. Use the canonical word **chunk**
for this unit throughout the analysis.

A **claim** is an immutable record of what a source asserted, with source
spans and dates. It is evidence, not the engine's current-world conclusion.
A **fact** is an adjudicated relation or observation with belief and valid-time
state, support and contradiction information. Semantic supersession belongs
there, not on claims.

`claims_and_sources_context` searches claims and chunks, then combines their
results. Its two branches are currently sequential. Each uses semantic and
keyword nominations followed by rank fusion and content loading. Fact context
uses fact nomination, confirmation and supporting evidence retrieval instead.

**Hydration** means loading content and response fields from candidate IDs.
The old conversational shorthand "validation time" was too broad: the measured
steps combine eligibility predicates, loading text, source joins, citation
coordinates and result construction. They do not ask Jev or a generative LLM
to decide whether each result is true.

## 4. What #542 changed, and what remains

The earlier implementation could perform broad entity-provenance and survivor
work while looking up a small candidate set. Fact evidence-count aggregates
were evaluated even when the caller requested only identities. Ordinary joins
and old planner overrides could pull final hydration back toward unrelated
registry scans.

#542 introduced indexed candidate-bound paths, made unused counts prunable,
bounded final fact hydration and removed the conflicting planner overrides.
It also shares identical query embeddings inside one request. Previously,
claims and chunks could each embed the same question; a combined operation
could embed it three times. Separate requests still have separate caches and
accounting. Different query strings are not treated as equivalent.

Saved-vector replays reduced median fact SQL from 12.30 to 2.22 seconds across
eight requests. Those runs made zero network embedding calls. They are distinct
from the live tool medians above; subtracting those medians would not produce
an embedding-time estimate because the queries, cache states and executions
differ. Three separate real OpenRouter/Nebius probes completed in 2.05–7.07
seconds, with TCP/TLS around 27 ms. That trace cannot separate provider routing
from inference.

The speedup removes identified waste. It does not establish that the remaining
seconds are acceptable or that arbitrary large/high-degree scopes are cheap.

## 5. Deployment identity is not a per-result validation stage

The engine is single-deployment memory infrastructure and does not require a
managed control plane. SQL already binds `deployment_id` in its predicates
and joins. That is ordinary query scoping, not an additional scan of every hit.

In the separate managed product, a project is paired 1:1 with a deployment and
gets its own database. The wrapper verifies the database's own deployment row
when constructing its tenant context. That catches routing/configuration
mistakes. It is separate from claim/chunk content loading and should not be
presented as a cause of per-candidate hydration cost. The engine analysis must
not import managed tenancy as an engine prerequisite.

## 6. Where visibility lives and how filtering works

There is no universal authoritative `active` bit copied to every derived row.

| Record | Relevant authority |
| --- | --- |
| Document lineage | `deleted_at`, `current_version_id` and versioning mode |
| Document version | `deleted_at`, readiness, `current_representation_id`, `superseded_at` |
| Representation | Readiness and selected structure generation |
| Chunk | Document/version/representation coordinates; searchable text lives in `chunk_search` |
| Claim | Immutable content/time plus cached `is_current_testimony`, backed by currency events |
| Fact | Adjudicated valid-time/belief state and live supporting evidence |

The current `documents_live`, `document_versions_visible` and
`chunks_all_versions_live` SQL views derive child eligibility from the parent
records. A document tombstone therefore hides its children immediately through
the query, without waiting for a flag to be rewritten on every child.

A consolidated chunk read can join `chunk_search` to chunk coordinates,
document versions and documents, applying deletion, readiness and selected
representation predicates while selecting eligible results. Existing views
already express much of this authority. The desired simplification is to
avoid equivalent work at multiple stages, not to remove the source rules.

Apply eligibility before the **final** top-k cut. An ANN candidate batch may
still require filtering and refill. Stopping after the first 50 unfiltered IDs
and returning only eight survivors would lose recall. D142's work limits and
truthful truncation disclosure continue to apply.

An alternative is to cache eligibility on every search row. It may help index
filtering, but an asynchronous cache cannot be the sole deletion authority:
it can lag a tombstone. Maintaining it synchronously introduces write fanout
and contention. Measure a candidate-bound joined read before adding another
state-maintenance mechanism.

### 6.1 What representation consistency protects

A representation is converted text for one observed document version. A PDF
version can be read as R1, then reread as R2 after an OCR/conversion correction.
Chunk offsets and a section tree belong to the named representation, so text
from R1 cannot be cited with R2's offsets. Old representations are processing
history, not automatically alternative source testimony.

The current chunk path first reads published coordinates and metadata, then
`chunk_texts` joins indexed text back to the published chunk relation, and the
caller compares the section role read through both paths. Selecting body and
metadata through one authoritative join could make their correspondence
structural. Any removal of the comparison requires proving it sees the same
section/generation and scope in both paths. This is not semantic validation by
a model and must not be described as a reason to reprocess unchanged content.

## 7. Snapshot consistency and repeated checks

The current ranked-read context pins one read-only repeatable-read snapshot,
deadline and evaluation instant across search, confirmation, loading and
freshness reads. A generic claim that "the source may change between these two
reads" does not justify repeating the same predicate inside that snapshot.

However, predicate sets are not assumed equal. Build a comparison for each
path: which source, version, mention, policy and support conditions nomination
actually enforces; which are added at final loading; and which are equivalent.
Remove repeated work only after proving that selected eligible rows, ordering,
refill and early-stop behavior remain correct.

Combining SQL is not an end in itself. A large all-row CTE or a planner fence
that scans the corpus could undo #542. The plan must remain driven by bounded
candidates and appropriate indexes at large corpus sizes.

## 8. Internal candidate depth versus returned result count

This benchmark uses final `k=50` and `candidate_k=200`. For claims/sources,
semantic and keyword lanes nominate separately; their deduplicated union can
exceed 200 for one result type. The full union is currently hydrated before
the final cut. In a saved request, 301 claim IDs and 116 chunk IDs were loaded
to produce bounded results. "Return 50" does not mean "read only 50".

The proposed alternative keeps the recall pool: determine eligible IDs and
ranking/deduplication using compact fields, then load full content for selected
results. Preserve source-occurrence choice and tail refill. A duplicate group
or an ineligible head must not silently reduce the requested result count.

## 9. Entity membership and coverage-first ranking are different choices

An entity filter asks whether a claim/chunk concerns any requested identity.
It can often be expressed as an indexed `EXISTS` test. This is not additional
entity information that the answerer needs in the response.

The implementation also counts distinct requested identities per candidate,
groups candidates into coverage tiers and ranks higher coverage before
semantic/keyword score. D142 section 8.4 currently requires that ordering.

For a single resolved entity, every eligible candidate has coverage one, so
the count adds no ranking distinction. A membership-only path is a promising
equivalent simplification there, subject to merged-identity and mention tests.
For several entities, removing coverage-first ordering is a behavior change:
a highly relevant item about Alice can outrank a less relevant item mentioning
both Alice and Bob. That matches the owner's relevance-first preference, but
needs an explicit D142 amendment and quality/latency comparison before coding.

Removing the aggregate can save grouping, tier enumeration and repeated scoped
work; entity membership and visibility still need to be enforced. It is not yet
a measured estimate of how much of the live 1–2 seconds the count consumes.

## 10. History, extraction currency and semantic supersession

An old message is not necessarily an obsolete document version. A 2022 chat
document whose source is still live remains useful evidence. Likewise,
retained earlier versions of a policy can answer historical questions.

Claims intentionally have no semantic invalidation/supersession state.
`is_current_testimony` is source/extraction bookkeeping, not "latest truth".
For example, testimony that someone lived in Paris in 2021 can coexist with
testimony about moving to Berlin in 2024. Facts represent the adjudicated
intervals and any correction/contradiction judgments.

Separate three cases: deleted/forgotten content stays excluded; retained
earlier source versions can be useful history; obsolete processing attempts
are not automatically useful additional evidence. The snapshot/living source
mode and declared effective periods determine current behavior. Existing
history-oriented interfaces must be audited rather than assuming one flag
exposes every older version or extraction row.

In particular, the default **snapshot** lineage treats each observed version
as independent dated testimony and keeps old-version claims current testimony.
A **living** lineage retires testimony present only in replaced versions.
The chunk relation can represent any live ready version, while a particular
text scope still decides which versions it selects. Thus "search only current"
and "search all old evidence" are both inadequate descriptions of the current
combined claim/chunk semantics.

An agent-facing history policy could return relevant retained versions with
source dates, version IDs, served/current-version status, effective intervals,
occurrences and applicable fact state. Some fields already exist. A complete
semantic "superseded claim" badge does not exist and is not proposed. Broader
history needs deduplication and ranking so old duplicates do not crowd out
useful evidence. Evaluate it separately from query-cost preservation work.

## 11. Authorized measurement and possible follow-ups

Only measurement is authorized for implementation by this analysis task.
Use retained ingestion, a disposable isolated database copy and fixed saved
query vectors to isolate database work. Compare first observed passes with
subsequent passes; do not call a first pass an OS-cache-cold measurement.
Preserve original stores, source hashes, answers, grades and immutable GCS
archives. No ingestion workers, extraction or re-embedding of stored data run.

Measure nested operation phases, SQL statement counts/time, content-loading
sizes and response serialization. An SQL statement that jointly enforces
eligibility and fetches text cannot honestly be split into separate eligibility
and text percentages by wrapping its Python caller. Report that mixed work
explicitly. Include empirical p50/p95 with sample counts and avoid treating a
small fixture as a production latency SLO.

Potential optimization work, requiring a subsequent approved design/change:
consolidate equivalent SQL eligibility work; load selected content only;
separate entity membership from coverage-first ranking; audit history metadata.
Do not shrink result depth or add a model to hide the current costs. The
aspiration is database work in a few hundred milliseconds on this fixture,
not an established result or an end-to-end promise including an external model.

## 12. Measurements completed on 2026-11-09

[Machine-readable measurements](artifacts/retrieval_20261109/measurements.json)
and the [diagnostic harness](artifacts/retrieval_20261109/profile.py) preserve
the experiment. No serving method's arguments, return value, SQL or ranking
was changed. Timing wrappers observe existing calls; a finite saved-vector
provider rejects unknown embedding inputs instead of making a network call.

The input is the 24 requests from the earlier R3 database-boundary audit:
eight fact contexts, three claims/sources contexts and thirteen entity
lookups. Five passes produce 120 completed operations with zero negative
results. This is a deliberately failure-selected fixture, not a random sample
of production traffic or all 60 R4 tool calls. First-pass numbers are first
observations after starting the copy, not a verified OS-cache-cold test.

An isolated PostgreSQL RC1 copy of the retained R4 volume was used. Original
R3/R4 containers and their paid data stayed stopped and unchanged. The base
application image/dependencies are `rememberstack-retrieval:8cd181fe0`; its
`/app/src` was mounted read-only from source `fb4a5f5a4`, whose relevant query
files are byte-identical to the evaluated source. This is a direct-engine
measurement of schema `p9_45_0066`, not a full v0.19 HTTP deployment/readiness
claim. The evaluation instant was pinned to 2026-11-07 00:30:16 UTC. No schema
migration, setup, ingestion worker or paid ingestion stage ran.

| Operation | Samples | Median wall | Empirical p95 | Median SQL commands |
| --- | ---: | ---: | ---: | ---: |
| Entity lookup | 65 | 47 ms | 62 ms | 5 |
| Facts context | 40 | 2,253 ms | 2,345 ms | 146 |
| Claims/sources context | 15 | 1,478 ms | 1,688 ms | 53 |

The p95 is the nearest-rank empirical statistic, with repeated queries on one
small store; it is not a production SLO. The median cursor SQL times are
2,162 ms for facts and 1,404 ms for claims/sources. Cursor timings include
statement execution/transport through the local Unix socket, not a pure
server CPU clock. Decoding, model construction and instrument logging add
other time. Diagnostic instrumentation overhead was not independently
subtracted.

### 12.1 Where the time goes

| Component | Median inclusive wall time |
| --- | ---: |
| Claim nominations, semantic plus keyword | 82 ms |
| Claim eligibility/content/citation loading | 808 ms |
| Chunk nominations, semantic plus keyword | 95 ms |
| Chunk eligibility/metadata/content loading | 438 ms |
| Raw indexed chunk-body read, nested inside the preceding row | 11 ms |
| Fact nomination, including its internal eligibility work | 1,366 ms |
| Final fact confirmation | 809 ms |
| Supporting fact evidence read | 49 ms |
| Fact enrichment | 2 ms |

Nested phase times overlap: the raw chunk-body time is included in chunk
loading, and the embedding wrapper is included in nomination. Component
medians need not add to the overall median. JSON serialization of the final
model was below 1.5 ms in every sample. Layer admission and text freshness/
pending probes were around 1–2 ms at their medians. The saved-vector lookup
was under a millisecond and is not a provider latency measurement.

This refines the earlier hypothesis. Copying raw chunk text and serializing
JSON are not the demonstrated dominant costs. The expensive areas are the
mixed eligibility/metadata/citation reads and fact nomination/confirmation.
The current wrappers cannot separate eligibility CPU from fetching columns
inside a single SQL statement. Use representative query plans before claiming
which join or field dominates. Reducing the number of fully processed
candidates remains worth considering, but simply deferring a string copy is
not established as the solution.

Warm passes did not remove the cost: facts medians were 2,261 ms in the first
pass and 2,253 ms subsequently; claims/sources were 1,466 and 1,479 ms. Thus
these observations do not support a cold-start explanation. They also do not
yet measure the isolated benefit of removing entity-coverage counting.

Three representative archived statements were also explained once on the
same copy with read-only execution, JIT off and no parallel gather workers.
See [plans-summary.json](artifacts/retrieval_20261109/plans-summary.json).

| Statement | Planning | Execution | Returned rows | Shared-buffer hits |
| --- | ---: | ---: | ---: | ---: |
| Claim eligibility/content, 256 requested IDs | 113 ms | 663 ms | 256 | 628,498 |
| Chunk eligibility/metadata, 116 IDs | 47 ms | 515 ms | 116 | 135,588 |
| Scoped fact nomination | 333 ms | 83 ms | 201 | 29,421 |

All three recorded zero shared-read blocks in these observations. Buffer hits
are repeated logical accesses, not distinct pages or a dataset-size estimate.
They show substantial cached database work; the fact statement also spends
more time planning than executing. They do not establish which individual
join to remove. This is one observation per statement, using the archived
parameters, not a p95 measurement of each phase. Compact query structure and
repeated eligibility work merit investigation alongside entity coverage.

### 12.2 External embedding latency is independent

Three separate live embedding probes used the retained Qwen3-Embedding-8B,
1,536-dimension, Nebius-pinned configuration. They completed in 0.836, 1.701
and 0.610 seconds, with reported total provider cost `$0.00000048`. See
[embedding-probes.json](artifacts/retrieval_20261109/embedding-probes.json).
These are three independent transport observations, not an end-to-end context
benchmark or a provider availability guarantee. They are excluded from the
120 saved-vector replays. No answer or judge model call was made.

### 12.3 Preservation and operational notes

All fifteen checked product fingerprints (thirteen stored-table multisets and
two complete published views) match the saved R4 source after the experiment;
see [store-evidence.json](artifacts/retrieval_20261109/store-evidence.json).
That includes the unchanged ingestion cost ledger. Query flags, results and
D56 identities were not rewritten. Initial harness startup failed before
measurement because a full `/app` mount hid the image's dependency environment,
then because the report directory was not writable by the image user. The
successful run mounted only source read-only and used a writable private report
directory. Neither failure generated answers, changed data or made model calls.

The raw timing events, SQL text without parameters, three query plans, executed
harness, copied-store fingerprints, runtime identities and aggregate/miss
reports are archived in GCS. The archive was downloaded to verify its SHA-256;
the manifest and receipt were also downloaded byte-for-byte. See the
[receipt](artifacts/retrieval_20261109/gcs-receipt.json) and
[runtime record](artifacts/retrieval_20261109/runtime.json). After verification
the temporary database container and its copied data were removed. No original
benchmark container was started. The full campaign remains paused.

## 13. Why the three latest conv-47 answers are wrong canonically

The inspected run is R4, not the older R3 run that suffered database timeouts.
All three generated answers are `Unknown`; all three frozen judgments are
WRONG. The canonical score remains **17/20**. We did not regenerate answers,
rejudge, alter a reference, or award an alternative score. The
[miss record](artifacts/retrieval_20261109/misses.json) captures the question,
reference, original dialogue and availability in the returned chunks.

All 31 rendered session documents and the raw conversation were searched;
the cited turns, neighboring context, targeted matches, saved tool responses
and frozen answering prompt were inspected. The reader trace
projection removes ranking/empty containers, not chunk text. Thus reference
presence here is more than presence somewhere in the database: it is in the
evidence supplied for a subsequent answer-agent call. Availability still does
not prove the model attended to or interpreted a particular sentence.

### 13.1 qa/0000: suspected health problems; gold `Obesity`

Raw reference D1:27 is John on 17 March 2022: his fingers are too big for
bowling, and perhaps he should take up exercise and run in the morning.
It does not state body weight, obesity, a diagnosis or a medical suspicion.
The turn appears at returned chunk positions 26, 34 and 19 in three successive
claims/sources searches, each returning 50 chunks. There is no timeout or
missing-reference explanation for the final abstention.

A secondary engine observation is that the stored claims preserve John's
dislike of bowling and his praise of James's bowling, but targeted keyword
searches of the 822 stored claim texts found no corresponding large-fingers/
exercise cue. This is not a proof that no differently worded cue exists.
This can weaken the fact-first route. It did not prevent the complete cue
from reaching the answerer through chunks. We have not inspected the original
Selection decision sufficiently to label the omission an extraction bug.

An earlier D1:23 metadata caption mentions a medical chart, but the raw turn
has no image URL or diagnosis, and its spoken text discusses a game project.
That caption does not establish obesity. No stronger explicit obesity/weight
statement was found in the full rendered conversation or targeted raw-text
inspection.

The frozen prompt makes conversation evidence authoritative and permits
`Unknown` when the deployment lacks an answer. Its explicit qualified-inference
rule is framed around hypothetical/counterfactual dependencies, not every
open-domain question. The observed outcome is consistent with conservative
abstention instead of the benchmark's expected health inference. The gold is
weakly supported by its cited reference; this does not prove every annotation
or the model's internal reasoning wrong. It is not evidence of a retrieval
timeout, an OCR miss or a strict judge rejecting a correct generated entity.

### 13.2 qa/0006: Connecticut residence; gold `Likely yes`

Raw D5:1, dated 12 April 2022, says James adopted a puppy from a shelter in
Stamford the previous week. The latest source response contains that raw turn
at chunk position 15/50 and the extracted claim about adopting the puppy in
Stamford. The earlier R3 missing-reference problem is absent in R4.

The gold requires interpreting Stamford as the Connecticut location and then
inferring likely residence from where a puppy was adopted. Adoption location
is not an explicit residence statement. The source date is also April, whereas
the answerer's searches ask about current residence. Later D29:10 says James
rented an apartment near McGee's bar but gives no state; we should not assign
that bar an external location and silently turn it into a residence fact.

This is the clearest answering-policy/calibration opportunity: the benchmark
accepts a qualified commonsense inference and the agent returns `Unknown`
despite having the premise. A general rule about supported, qualified inference
could be tested separately. Hard-coding a Stamford-to-residence rule would be
benchmark-specific and is not proposed. The trace supports this classification
but does not reveal the model's internal cause. The judge correctly marks
`Unknown` wrong against its frozen reference; no strictness error is shown.

### 13.3 qa/0011: feelings on April 10; gold `seeking solitude`

The raw dataset dates D6 to **20 April 2022**. D6:7 says John wanted to be
alone with nature **two days ago**, resolving to **18 April**, not April 10.
The original QA nevertheless asks April 10 and cites D6:7. This conflict exists
in the raw dataset, before rendering or engine time normalization. Neighboring
sessions are April 4 and April 12; there is no April 10 session to reinterpret
as the cited April 20 turn.

The cited chunk arrived at positions 24/50 and 33/50 in two source reads. The
extracted claim explicitly says John wanted to be alone with nature on
2022-04-18. The agent also made an April-10 semantic chunk search. This case
therefore demonstrates a correct extracted relative date and a concrete
question/reference conflict, rather than evidence that the engine converted
April 10 to April 18 or failed to return the cited mood statement.

`Unknown` is a defensible response to the literal April-10 question. It stays
canonical WRONG because the frozen judge compares the question, gold and
answer without the underlying source dialogue. This is an annotation conflict,
not a demonstrated judge malfunction. Changing the judge cannot make the
source dates agree.

### 13.4 Shared observations and bounded conclusions

The three answers have no answer or judge failure, and their tool responses
have no typed negatives. Correct-reference coverage alone does not establish
that a question has a uniquely supported answer. Here two references demand
inference of different strength and the third contradicts the question's date.

The recorded input-token totals are 541,216, 238,404 and 518,742 across their
respective agent loops. They are sums across model calls, not individual
context-window sizes or proof of overflow. Individual claims/sources envelopes
are roughly 122–128 thousand JSON characters before reader compaction, and
successive traces repeat prior content. That is a real context-volume/cost
observation; attention dilution is a hypothesis, not an established cause.
No context-length provider failure is recorded.

The next optimization should address measured engine work. A separate
answering-policy experiment can examine calibrated inference, while the date
conflict remains an observed benchmark issue. Neither a reranker nor more
candidate depth is established as a cure for these three latest abstentions.

## 14. Costs, correctness and scope limits

This work changes no ingestion generation, content identity or D56 reuse key.
It performs no paid ingestion and introduces no serving algorithm or schema
change. Any explicit query-embedding transport probe is bounded, metered and
reported separately from saved-vector replays. No answer/judge model calls
are needed to inspect already saved misses.

Read-only measurement should preserve every checked product fingerprint.
Retain report source/runtime hashes, method and raw traces off-host; only
non-secret aggregate measurements and bounded benchmark evidence enter git.
The isolated fixture does not establish arbitrary graph-degree or full-corpus
performance, explain provider routing versus inference, or resolve benchmark
annotation ambiguity by itself. Opus review and execution evidence are distinct
from the owner's acceptance of a new binding retrieval contract.
