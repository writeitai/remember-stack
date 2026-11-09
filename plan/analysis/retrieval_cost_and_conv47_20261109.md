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

## 12. Conv-47 diagnosis and measurement results

Pending in this first draft: fresh stage measurements and reinspection of the
three latest wrong answers against original dialogue, exact reader-visible
traces and the frozen prompt. The existing canonical result is 17/20. Do not
rerun, relabel, change a reference or attribute a miss solely from the score.

## 13. Costs, correctness and scope limits

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
