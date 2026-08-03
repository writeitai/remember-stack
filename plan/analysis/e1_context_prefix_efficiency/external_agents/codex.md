# Independent analysis — E1 context-prefix efficiency

**Status:** analysis only; this report does not amend D63, D56/A3, or any binding design.

## 1. Problem restatement

RememberStack's default E1 path uses a conventional embedding model, so every chunk is embedded as `context_prefix + "\n\n" + verbatim_chunk`. The prefix is meant to say where the passage sits in the document. It is stored on `chunks.context_prefix` and carried forward with an unchanged chunk under D56/A3 (`plan/designs/e1_chunks_design.md` §§5 and 7).

The implementation turns that useful quality feature into a document-sized failure transaction. `EmbedChunksHandler.handle` in `src/rememberstack/workers/e1.py` loads every chunk, calls `_resolve_prefix` sequentially for all of them, and only after the last prefix succeeds does it call the embedder, upsert P1, and call `ChunkCatalog.record_embeddings`. A 749-chunk first ingest therefore makes about 749 serial chat-completion calls before any prefix is durable. One invalid completion near the end fails the single document-level `embed_chunk` work row; a retry begins again at chunk 1 because D56 carry-forward only finds completed earlier *versions*, while no current-version prefix was written.

The BEAM symptom is consequently not merely “a flaky model.” It is the product of three choices multiplying each other:

1. work and retry scope is the whole document;
2. generation is one serial LLM call per chunk; and
3. all writes are delayed until every generation and one potentially very large embedding batch succeed.

The cost ledger honestly records the successful calls and the failed provider response, but honest accounting does not make the work reusable. `Worker._record_failed_provider_usage` in `src/rememberstack/workers/base.py` records a usage-bearing exception, and `WorkLedger.record_call` deduplicates only within `(processing_id, attempt, call_key)` in `src/rememberstack/spine/work_ledger.py`. The next attempt is allowed to bill the same logical prefixes again.

There are two distinct objectives:

- **This week:** make a long first ingest finish, retaining the current prefix semantics and making every completed unit replayable.
- **Long term:** stop paying for hundreds of independent judgments when the output is only bounded location metadata, without silently reducing passage-retrieval quality.

## 2. Quality goal

The prefix is not a mini-summary and not evidence. Its job is to disambiguate a passage's retrieval coordinates: document, section/topic, role, and—especially for conversations—rough chronology or turn range. This helps a conventional embedder distinguish otherwise similar passages and gives a short passage enough context to match a query that names its enclosing subject.

That scope matters. The binding retrieval design assigns atomic/needle recall to embedded claims, exact text to BM25/FTS, and coherent reading material to chunks (`plan/designs/e1_chunks_design.md` §5). The prefix therefore need not recover every fact diluted inside a chunk. It should improve nomination of the right passage without competing semantically with the passage body.

“Good enough” should mean all of the following:

- On the D22 retrieval golden set and long-chat questions, chunk recall@k and first-relevant rank are non-inferior to the current LLM prefix under a predeclared margin. A starting hypothesis is no more than 1–2 absolute points of recall@10 loss, but the owner should set the margin before seeing results.
- It materially beats or at least matches bare-chunk embeddings on queries requiring document, section, speaker, or chronological context.
- It is short and bounded, so it cannot dominate the vector. A one-line, roughly tens-of-tokens prefix is the intended object; the current `ContextPrefix.prefix` model in `src/rememberstack/model/chunks.py` has no upper bound.
- It contains location metadata, not an asserted fact copied from an LLM summary. Hydrated source text remains the evidence.
- The exact bytes used to make a vector are versioned and replayable. An unchanged reused chunk keeps the same resolved prefix/vector under A3.

There is also an E2 constraint. `_bundle_text` and `_source_grounding_elements` in `src/rememberstack/workers/e2.py` pass the stored prefix to Claimify and allow it in the token-membership grounding union. Any new prefix that incorporates abstractive section summaries must **not** automatically inherit that evidentiary role. Embedding orientation and extraction-grounding context may need separate fields if their trust rules diverge.

## 3. Failure modes of current design

### Latency and scale

- `prefixes = tuple(self._resolve_prefix(...) for chunk in chunks)` is serial. Wall time is approximately the sum of all prefix-call latencies, not the maximum of bounded concurrent work.
- The prompt repeats the document title, section orientation, and instructions for every chunk. Prompt caching may reduce billed input, but it does not eliminate request latency, routing failures, or per-request overhead.
- After all generations, `EmbeddingRequest.texts` contains every fresh chunk in one request. `EmbeddingRequest` and `OpenRouterModelProvider.embed` have no caller-visible batch ceiling (`src/rememberstack/model/model_provider.py`, `src/rememberstack/adapters/openrouter.py`). Hundreds or thousands of texts make the final call another timeout/payload blast radius.

### Cost and retry amplification

- First-ingest prefix cost is O(chunks), even though most requested output is repeated section/document location.
- A late failure causes all earlier successful prefixes to be regenerated on the next attempt. With the schema default of three total attempts in `src/rememberstack/spine/migrations/versions/p0_02_0002_infrastructure_registries.py`, a failure late in each attempt can approach three document scans of prefix calls.
- `cost_ledger` preserves those costs by attempt. It cannot reconstruct or replay the generated prefix because only accounting, not output, was committed.

### Blast radius and durability

- `processing_state` has one `embed_chunk` row for the document version. One bad chunk can dead-letter all 749 chunks and prevent the E2 follow-up.
- `_resolve_prefix` already checks the current row's stored prefix first, but no prefix-only write path exists. `ChunkCatalog.record_embeddings` writes prefix and embedding stamps together only after P1 succeeds (`src/rememberstack/spine/chunk_catalog.py`). The code contains the replay hook but does not create the checkpoint that would make it useful during first ingest.
- P1 is written before Postgres. That ordering is conservative—an index write followed by a crash will be retried rather than falsely stamped complete—but at present the retry also regenerates all prefixes and vectors. Bounded index-then-PG commits would retain the safety with a much smaller redo window.

### Provider/protocol flakiness

- `OpenRouterModelProvider.generate` requests strict JSON schema, then applies `json.loads` to the whole content and Pydantic validation. A route that ignores the schema, returns prose, truncates JSON, or wraps it in extra text becomes `OpenRouterInvalidResponseError` (`src/rememberstack/adapters/openrouter.py`). The adapter intentionally makes one provider call; ledger-level retry is responsible for another attempt.
- The default `OpenRouterSettings.max_completion_tokens` is 32,000 and applies globally. It is appropriate as headroom for some reasoning calls but wildly larger than a one-sentence prefix. `ModelRequest` has no per-request output cap. The observed roughly 140K-character non-JSON response is compatible with a completion consuming that general-purpose ceiling.
- `ContextPrefix` requires only a non-empty string. A huge but valid JSON string would pass validation and then be prepended to the embedding input.
- A nonblank truncated response is parsed without first rejecting its `finish_reason`; diagnostic finish-reason handling is strongest only when content is absent.
- The prefix request sets temperature zero but does not set request-level `reasoning_effort`. The adapter can receive an environment map, but E1 does not express that this tiny task should use no/minimal reasoning. Generation routing also has no prefix-specific provider pin analogous to `embedding_provider`.
- Generic JSON repair would reduce visible errors while introducing a worse ambiguity: it could accept a refusal, partial object, or customer-derived prose as retrieval/grounding context. Repair is not the primary fix.

### Contrast with E2

The binding E1 design's §6 describes batched E2 calls with per-chunk bookkeeping. The current `ExtractClaimsHandler.handle` implementation is not yet section-batched, but it does have the important durability property: it checks `ClaimCatalog.chunk_already_extracted` per chunk and `ClaimCatalog.record_extraction` commits one chunk's claims/decisions atomically (`src/rememberstack/workers/e2.py`, `src/rememberstack/spine/claim_catalog.py`). If chunk 500 fails, chunks 1–499 are replayable state. E1 should acquire the same progress boundary even if its call batching evolves independently.

## 4. Options

The ratings below are relative and assume the current conventional embedder unless stated otherwise. “A3 fit” includes exact-byte replay and no LLM output in D56 identity keys.

| Option | Quality | Cost | Latency | Flakiness / blast radius | D56/A3 fit | Implementation risk |
|---|---|---|---|---|---|---|
| **Durable per-unit work** (chunk or bounded batch processing rows) | Same prefix quality | Same successful-path generation cost; very low retry waste | Parallelizable; queue overhead | Best isolation; one failure affects one unit | Excellent if resolved prefix/vector is committed before success | Medium–high: fan-out/fan-in, readiness barrier, more ledger rows |
| **Hierarchical/section or conversation-window prefix** | Likely equal/better for well-titled sections; one root prefix is too coarse for a long chat | O(sections + windows), not O(chunks) | Much lower | Shared-call failure affects only one bounded region | Good if exact resolved bytes are materialized per chunk before carry-forward | Medium; prefix trust and window identity need design |
| **Deterministic template** | Plausibly sufficient for the passage channel; must prove non-inferiority, especially on synthetic-root chats | No prefix LLM cost | Negligible | No model flakiness | Excellent; deterministic but should still be stored/stamped | Low code risk, medium binding risk because D63 currently binds per-chunk LLM generation |
| **Contextual embedder** | Potentially best native context quality, but this is a hypothesis until the project benchmark measures it | Removes prefix calls; embedding price/throughput is deployment-specific | Potentially lower, subject to context limits | Removes chat-completion failures but adds model/API dependency | **Unresolved:** a vector may change when surrounding document context changes, so content-hash-only vector reuse may be unsound | High near-term: port request shape, context limits, re-embed, and reuse semantics |
| **Model/protocol hardening** | Same when calls succeed | Prevents runaway output; does not remove O(chunks) calls | Better tail latency | Reduces, but cannot eliminate, provider failures | Neutral/strong if successful output is checkpointed | Low |
| **Two-phase embed** (template/bare now, upgraded later) | Fast provisional recall, but temporarily lower or heterogeneous quality | Embeds twice | Fast time-to-some-index, slower final convergence | Decouples readiness from prefix failures | Awkward: preprocessing generation and upgrade state must be explicit | High; query/readiness semantics and overwrite/reconciliation change |
| **Multi-chunk batch prefix** | Can match or improve quality by showing local context; attention/output omissions become a new risk | Fewer repeated headers and requests; source tokens remain proportional to text shown | Much lower call overhead | Failure radius is K chunks, bounded by batch size | Good if response is validated and each prefix is stored separately | Medium; exact membership validation and design amendment from “per-chunk call” |
| **Checkpointed flush in one document work item** | Identical | Same base cost; retry cost falls to the last checkpoint | Serial latency unchanged unless combined with bounded concurrency | Preserves successes, but the document row can still run long/DLQ | Excellent; directly activates `_resolve_prefix`'s stored-prefix replay | Low–medium; needs prefix-only and bounded embedding write APIs |
| **Bounded embedding batches with checkpointing** | Identical vectors/text | Same embedding tokens; retry waste falls | Avoids one huge terminal call; modest request overhead | Failure affects one batch | Excellent if P1 upsert precedes PG stamp and retries query completed rows | Low–medium and required regardless of prefix policy |

### Durable per-unit work

This is the cleanest ledger architecture when LLM generation remains. `processing_target` already includes `chunk`, so the data vocabulary can represent chunk work (`src/rememberstack/model/processing.py`). A document coordinator can enqueue chunk or small region units, each with its own attempts and cost attribution, then enqueue extraction only after all required units are stamped complete. This also permits rate-limited parallelism.

The cost is orchestration. Reusing the same `embed_chunk` stage for both coordinator and leaf work complicates the handler; adding a `derive_chunk_context` stage changes the binding enum/work graph. Either version needs an idempotent fan-in barrier and must preserve the current readiness meaning. It is the durable shape for generated fallback contexts, but it is more work than needed if the default prefix becomes deterministic.

### Hierarchical or section/window prefix

One generated descriptor per semantic section, plus a deterministic per-chunk suffix, removes most calls while retaining topic orientation. `document_sections` already stores `title`, `role`, spans, and summaries; however, `ChunkCatalog._SELECT_SECTIONS` currently omits the title and `SectionSpan` omits it. Source heading titles should be preferred over summaries.

For a 100K chat with one synthetic root, “one per section” degenerates to one prefix for 749 chunks and loses local topic/chronology. The unit must therefore be a bounded conversation window—such as a stable turn/time range or a fixed small run of chunks—with a deterministic position/speaker/time suffix. Exact window size is an eval parameter, not a design constant.

To preserve A3, a shared generated descriptor should be materialized into each chunk's resolved prefix. An unchanged carried chunk keeps its old bytes even if a later version's section/window layout changes. LLM output still stays out of reuse identity. If a section summary is used to generate the descriptor, that output is embedding orientation only and should not automatically enter E2's grounding union.

### Deterministic template

This is the simplest long-term candidate and should be tested first. Build a short prefix only from stable/source-derived coordinates already in the spine or conversion artifacts:

```text
Document: <title>; source: <kind>; section: <source heading chain>; role: <role>; passage: 318/749; turns: <time/speaker range if available>.
```

Do not insert the section summary or infer a topic fact. The prefix is predictable, safe for E2, multilingual insofar as source titles/names are preserved, and useful for near-duplicate documents and chronology. For chat transcripts, deterministic turn numbers, timestamps, and speaker names are likely more retrieval-relevant than a generic LLM sentence saying the passage is “in a conversation”; that is a hypothesis to test.

Even though the template can be recomputed, store the resolved string and a version that includes the field set, normalization, and conversation-metadata parser. Storage preserves auditability and exact indexed bytes. Vector reuse must require both embedding model generation and prefix-policy generation. Today `EmbeddingUpdate.embedding_version` is only the model id, while carry-forward separately filters `prefixer_version`; an architectural cleanup should make the compound derivation generation explicit so backfills cannot overlook a prefix-policy change.

Changing the shipped default from a per-chunk LLM prefix to this template is not a “mere optimization”: D63 and `plan/designs/e1_chunks_design.md` §5 explicitly bind conventional + per-chunk LLM prefix. It requires an amendment if adopted beyond an isolated spike/config experiment.

### Contextual embedder

D63 already permits a contextual-embedder deployment and says that configuration deletes the prefix stage (`decisions.md` D63; `plan/designs/e1_chunks_design.md` §5). This is the conceptually clean end state if measured retrieval, multilingual behavior, context limits, throughput, and operating model win.

The current port is not yet enough to prove that path: `ModelProviderPort.embed` accepts only `EmbeddingRequest(texts=...)`, with no document/chunk-boundary or contextual-session shape (`src/rememberstack/ports/model_provider.py`). More importantly, `EmbedChunksHandler._carried_vectors` copies a prior vector based on chunk content hash and model version. A truly contextual vector can change when neighbors or document context change even if the chunk bytes do not. Before this option is called a config-only production switch, the spike must establish the context scope and the corresponding reusable-input hash. Otherwise it trades prefix cost for stale-vector risk.

### Model/protocol hardening

This is necessary for the near-term patch regardless of the architectural winner:

- add a request-specific completion cap to `ModelRequest`/the adapter; start around 96–128 completion tokens and tune from observed valid outputs;
- set no/minimal reasoning for the prefix request and use a small schema-capable route;
- constrain `ContextPrefix.prefix` to a bounded normalized line (for example, at most 256–512 characters), reject embedded newlines/oversize output, and include these rules in `E1_PREFIXER_VERSION`;
- cap prompt orientation as well as response length;
- reject truncation/length finish reasons before accepting partial content;
- optionally pin an audited generation provider for this model rather than accepting heterogeneous route behavior;
- keep provider calls one-to-one with ledger calls. Retry at the durable work-unit boundary, not invisibly inside the adapter;
- do not use general “find the first JSON-looking substring” repair. A narrowly specified wrapper removal could be considered, but a deterministic location template is a safer explicit fallback than accepting arbitrary prose.

Hardening stops pathological 32K-token attempts but leaves the fundamental N-call cost.

### Two-phase embed

Two-phase indexing can make P1 nominally available quickly, but it creates a period where some rows use bare/template text and others use LLM-prefixed text. `P1ChunkRow` and `LanceChunkIndex` do not store an input-policy generation; rows are simply replaced by `chunk_id` (`src/rememberstack/model/chunks.py`, `src/rememberstack/adapters/selfhost/lance.py`). Readiness, query filtering, and crash reconciliation would all need new semantics. E2 also consumes the prefix, so “P1 ready” would not mean the pipeline is ready for extraction.

This is justified only if a measured product requirement values provisional retrieval enough to pay for duplicate embedding and generation-aware indexes. It is not the smallest BEAM fix.

### Multi-chunk batch prefix

A bounded request can return prefixes keyed by `chunk_id` for, for example, a local run within one section/window. It amortizes repeated instructions and document context and may improve local coreference. It must validate exactly one result for every requested id, no unknown/duplicate ids, per-prefix length limits, and a total response ceiling. Store each output immediately, and retry only missing ids; do not rerun a successful batch because a later batch failed.

This is preferable to 749 independent calls if deterministic prefixes fail the quality gate, but it still asks an LLM to repeat largely deterministic location statements. A single giant “return 749 prefixes” request merely recreates the same blast radius with a more fragile JSON body and should not be used.

### Checkpointed flush

This is the smallest safe implementation repair. Add a catalog operation that writes only `(context_prefix, prefixer_version)` for a chunk. Generate missing prefixes in bounded groups, record successful results as they complete, then raise/retry only after preserving those successes. `_resolve_prefix` will already replay them on the next attempt.

Next, embed only rows missing the desired compound generation in bounded batches. For each batch: upsert P1 first, then atomically stamp the chunk rows in Postgres. On retry, verify/read completed P1 vectors or re-upsert only the small ambiguous batch. This retains the current conservative cross-store ordering.

Checkpointing alone does not reduce first-attempt LLM tokens. Combine it with bounded concurrency for wall time and the protocol caps above. `SectionSummarizer` already uses bounded parallelism of eight independent calls as an implementation precedent (`src/rememberstack/workers/e0_summary.py`), although the model-provider concurrency contract and rate limits must be made explicit for E1.

## 5. Recommendation

### Ranked primary recommendation

1. **Near-term, preserve current output semantics but make progress durable.** Add prefix-only checkpoints, request-specific output/character limits, no/minimal reasoning, bounded concurrency, and bounded embedding batches. Resume the BEAM dead letter under a bumped component/prefix generation. This is the safest production change this week because retrieval bytes remain the same kind of LLM location sentence while failure cost becomes proportional to the unfinished suffix, not the document.
2. **In the BEAM harness, simultaneously A/B a deterministic source-derived template.** This is an experiment/config arm, not an implicit D63 change. If it is non-inferior on passage retrieval, amend the design and make it the conventional-embedder default. It removes the entire prefix LLM failure/cost surface and is the simplest durable winner.
3. **If the deterministic arm loses only on weakly structured genres, use generated context selectively at section/window granularity.** Materialize a shared durable descriptor plus deterministic per-chunk coordinates. For long chats use bounded stable turn/time windows, never the whole synthetic root. Multi-chunk generation is the fallback when chunks truly need distinct labels.
4. **Keep a contextual embedder as a strategic benchmark, not this week's unblocker.** It may ultimately beat all synthetic prefixes, but validate the API/context scope and fix context-sensitive D56 vector reuse first.

Architecturally, generation work that remains should become durable chunk/window units behind a document fan-in barrier. If deterministic prefixes win for most inputs, reserve that machinery for selective low-confidence genres rather than forcing every chunk through it.

For the immediate BEAM run, the deterministic arm can be an explicitly experimental harness policy. If production long-document ingest must be guaranteed to complete rather than merely made much more reliable, an owner-approved, versioned deterministic fallback after a prefix unit exhausts its bounded provider attempts is also required. Checkpointing and caps minimize provider failure; they cannot mathematically guarantee that every model call eventually returns valid JSON.

### Explicit “do not do”

- Do not solve the incident by increasing document-level retry counts or timeouts; that multiplies the same wasted calls.
- Do not silently fall back to bare chunks in the shipped default. Bare embeddings are an unmeasured quality change and violate the current conventional+prefix branch.
- Do not put an abstractive section summary into the E2 grounding prefix. If used for embedding orientation, separate its trust domain.
- Do not accept arbitrary non-JSON prose through generic repair, and do not allow an unbounded `prefix` string.
- Do not make one request for hundreds of prefix objects or one embedding request for thousands of texts.
- Do not deploy mixed provisional/final vectors without explicit generation filtering and readiness semantics.
- Do not switch the default to a contextual embedder until context limits, retrieval quality, and D56 vector reuse are demonstrated.

## 6. Spike plan

The smallest experiment should answer one question: **does a deterministic location template retain the retrieval contribution of the current LLM prefix?** It need not build new production orchestration.

1. Use the BEAM 100K conversation plus a small structured-document sample from the existing retrieval golden set. Preserve the exact current chunks and embedding model/dimension.
2. Build isolated indexes for four preprocessing arms: bare chunk, current LLM prefix, deterministic prefix, and deterministic + shared section/window descriptor. The primary comparison is LLM versus deterministic; bare quantifies whether any prefix helps.
3. The deterministic arm uses only document title/source kind, source heading-title chain and role, chunk ordinal/total, and available source-derived conversation turn/time/speaker range. It excludes section summaries.
4. Evaluate source/chunk recall@5 and @10, MRR/first-relevant rank, and the end-to-end BEAM answer score. Slice results by ordinary topic queries, near-duplicate passages, speaker/coreference, chronology, and synthetic-root versus structured sections.
5. Predeclare a non-inferiority margin and inspect regressions, not only the average. If deterministic passes, stop: it is the simpler design. If it fails only for synthetic-root/local-topic queries, test bounded window descriptors on those failures rather than adopting LLM generation corpus-wide.
6. Separately fault-inject one invalid completion after hundreds of chunks in a checkpointed prototype. Verify that completed prefix count survives, retry calls equal only the missing suffix, failed-response usage has a chunk/batch-specific call key, and P1/PG converge after a crash between index upsert and PG stamp.

Success criteria are: deterministic quality within the declared margin; zero prefix-model calls for that arm; bounded embedding requests; and O(unfinished units), not O(document chunks), work after an injected failure.

## 7. Binding design impact

### Pure implementation or faithful hardening

The following preserve the current conventional+LLM-prefix behavior and can be treated as implementation work, with normal component-version bumps:

- prefix-only checkpoint writes and replay of current-version prefixes;
- bounded concurrent scheduling of independent per-chunk calls;
- request-specific completion/reasoning limits, strict prefix length/line validation, and provider routing hardening;
- bounded embedding batches with index-first/PG-second checkpointing;
- better chunk/batch-specific failed-call attribution;
- preservation and metering of successful sibling calls before a concurrent group's provider error is re-raised;
- reconciliation that treats a missing P1 row as a cache miss and re-embeds a bounded unit;
- tests proving a late failure does not regenerate completed prefixes.

Using a deterministic template only inside a non-production spike does not amend the design.

### Requires a binding amendment or explicit decision update

- making deterministic prefixes the shipped conventional-mode default, because D63 and E1 §5 currently say per-chunk LLM call;
- replacing per-chunk calls with section/window generation or multi-chunk generation as the normal policy;
- adding a shared prefix artifact/reference and defining how exact bytes carry forward when section/window membership changes;
- splitting `embedding_context` from E2's grounding-eligible context, especially if summaries or other LLM orientation enter embeddings;
- changing E1's work graph to durable leaf jobs plus a fan-in readiness barrier, if that topology becomes architectural rather than an internal checkpoint loop;
- exposing provisional two-phase P1 readiness or allowing mixed input-policy generations;
- changing the shipped default embedder. A deployment's use of D63's already-bound contextual alternate is configuration, but changing the product default is a decision update;
- revising D56 vector reuse for a contextual embedder whose output depends on surrounding context.

No option should put LLM output into `chunk_content_hash` or `extraction_input_hash`. A3's replay/carry rule remains the governing invariant.

## 8. Open questions

1. What is the measured marginal gain of the current LLM prefix over (a) bare chunks and (b) a title/section/position template, by genre and query type? E1 design spike 8 already calls for this measurement.
2. For long conversations, which deterministic coordinates are reliably present after conversion—speaker, timestamp, message/turn id, or only ordinal? If absent, should the converter emit them as source-derived block metadata?
3. Are source heading titles sufficiently available through `document_sections.title` for the template, including fallback PageIndex sections? The current E1 load drops this field even though the table stores it.
4. Should an LLM-derived E1 prefix remain in E2's grounding membership union at all? The code calls the union source-derived, but the prefix itself is generated. At minimum, summary-derived orientation must be excluded.
5. What maximum prefix length optimizes retrieval before the prefix begins to dominate the passage vector? This should be measured alongside stored dimension.
6. What bounded concurrency and embedding batch sizes fit provider quotas and self-hosted throughput? These are deployment knobs, not identity constants.
7. Should vector generation be stamped as a compound of embedding model/dimension plus prefix policy, rather than storing only the model id in `embedding_version` and relying on a separate prefix field?
8. How should duplicate identical chunks in different positions be aligned? Current carry-forward selects one prior row per `chunk_content_hash`; location-aware prefixes can differ between identical occurrences.
9. For contextual embedders, what exact context scope affects a vector, and what stable hash must match before the vector may carry forward?
10. What fan-out volume and retention policy are acceptable if every chunk/window gets a `processing_state` row, and how does document readiness diagnose a small number of leaf DLQs?

## Executive recommendation

Ship checkpointed prefix writes, hard output caps, bounded concurrency, and bounded embedding batches now; retry only unfinished units.  
Run the deterministic title/section/position/turn template as the BEAM A/B arm this week.  
If it is retrieval-non-inferior, amend D63/E1 and remove per-chunk prefix LLM calls from the default.  
If it loses on weakly structured inputs, generate one durable descriptor per bounded section/chat window, not per chunk.  
Keep all resolved prefix bytes stored and carried under A3, and keep LLM output out of identity keys.  
Do not use bare silent fallback, generic JSON repair, giant batches, or contextual-vector reuse keyed only by chunk content.
