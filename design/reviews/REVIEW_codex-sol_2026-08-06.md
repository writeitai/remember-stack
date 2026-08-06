# Review: observation rank cache, pipeline checkpoints, and relation fact labels

**Reviewer:** `codex-sol`

**Date:** 2026-08-06

**Disposition:** Documentation review only; no code was changed.

## Executive summary

- **Observation rank embedding cache:** **Accept with changes**; the optimization is sound, but the key is not a D63 embedding generation and the NEW/evidence-collapse alias rules can poison the cache.
- The cache must remain a memoization layer over the exhaustive Postgres block; a miss, corrupt entry, batch limit, or cache outage must never remove a candidate.
- **Incremental pipeline checkpointing:** **Accept with changes**; the two-phase direction is right, but the proposed state machine permits stale-vector false readiness on generation changes.
- `P1FactRow`/Lance search currently carry no generation or PG readiness state, so §4.5's “missing ref = not in channel” claim is false against the implementation.
- The checkpoint design also needs a normative concurrency protocol; “whole-document lock or batch lock” is not an interchangeable implementation choice.
- **Relation fact labels:** keep relations in P1 under the current binding; dropping them is a retrieval product change and is not justified by ingest cost alone.
- Deterministic labels are preferable to ingest-critical LLM labels, but S4 full predicate templates should be the primary trial and S1 the fallback/baseline, not the presumed winner.
- Neither design should enter the binding corpus until the P0 items in the prioritized fix list are incorporated and tested.

## Review basis

The five requested documents were read in full. The binding and implementation checks included:

- `plan/designs/observations_design.md` §3, especially the exact/exhaustive block, ordering-only hub rank, D43 no-cap rule, and fail-safe coexist contract.
- `plan/designs/p2_graph_design.md` §6, especially the binding division “Lance = entry; LadybugDB = structure” and the one-row-per-relation semantic index.
- `decisions.md` D63, which defines an embedding version as model, dimension, and parameters—not merely a model-name string.
- `src/rememberstack/spine/observation_adjudication.py`, especially `add_observations`, `_add_with_block`, `_rank`, `_adjudicate_residue`, and `_insert_new`.
- `src/rememberstack/workers/p1.py`, especially `EmbedClaimsHandler.handle` and `LabelFactsHandler.handle`, plus the selectors/stamps in `src/rememberstack/spine/fact_catalog.py` and the Lance facts implementation in `src/rememberstack/adapters/selfhost/lance.py`.

## 1. `plan/designs/observation_rank_embedding_cache_design.md`

### Verdict: Accept with changes

The core decision is correct: reuse write-path statement vectors without using Lance as candidate membership, while leaving the exhaustive block and D43 verdict ladder intact. The design is **not acceptable as-is**, however, because its cache identity is weaker than the repository's binding embedding-generation contract, its NEW alias semantics are ambiguous, and it does not define safe behavior for malformed vectors, concurrent misses, provider caps, or durable-cache failure.

### Correctness risks

| Priority | Risk | Evidence and consequence |
| --- | --- | --- |
| P0 | **The cache key is not a complete embedding generation.** | Design §§2, 4.1 key only by `embedding_model`. D63 (`decisions.md` D63) says the version resolves model, stored dimension, and parameters. Provider/model aliases, truncation dimension, task/input policy, or adapter revision can change while the model string stays constant. The durable PK would then reuse or overwrite incompatible vectors. The `dims` payload column detects only one symptom after the wrong lookup; it is not an identity boundary. |
| P0 | **NEW's normalized hash does not identify the actual embedded input.** | Design §4.1 hashes `normalize_ws(statement)` but `_rank` currently embeds the raw `statement` (`observation_adjudication.py:_rank`, lines 535–557), and exact evidence collapse compares raw strings (`_add_with_block`, lines 183–201). Two raw inputs can share a key while only the first input was embedded. Either hash exact encoded input bytes or embed the exact normalized form; do not mix the two contracts. |
| P0 | **Evidence-collapse aliasing is ambiguous and can poison an authoritative observation key.** | Design §4.2 mentions `_insert_new` / evidence collapse around write-through. An evidence verdict can return an existing observation whose immutable stored `statement` differs from NEW (`_adjudicate_residue`, lines 327–346). NEW's vector must never be stored under that existing `observation_id`; the id-keyed vector must represent the stored prior statement. Only a newly inserted row may receive the already-computed NEW vector under its new id. |
| P0 | **Corrupt or malformed vectors can become persistent cache hits.** | Design §5 covers empty/partial durable rows only generally. Current `_cosine` silently maps dimension mismatch or zero norm to `0.0` (`observation_adjudication.py`, lines 685–694), which can turn corruption into a false “clear novelty” exit. Count, expected dimension, finiteness, and nonzero norm must be validated before any cache write and again on durable read. An invalid entry is a miss; repeated invalid provider output is an error, never a cached value. |
| P0 | **“At most once per process” has no concurrency mechanism.** | Design §2 promises at-most-once process embedding, while §4.3 defines an adjudicator/process lifetime. The self-host profile constructs one stage handler per worker process (`src/rememberstack/profiles/selfhost.py:_handler`, lines 390–499), so the instance can span many documents. Concurrent calls need a thread-safe single-flight per key; an ordinary dict gives duplicate provider calls and makes the promise false. A durable cache alone does not prevent two processes from embedding the same miss. |
| P0 | **Exhaustive block must not be weakened by cache I/O.** | D43 (`observations_design.md` §3 steps 1–4) binds the Postgres entity block as exact/exhaustive. Current code obtains all non-invalidated rows with `_BLOCK_ENTITY`, then filters open rows in memory (`add_observations` and `_add_with_block`, lines 133–168 and 223–255). Durable cache lookup must consume that candidate list; it must never inner-join, ANN-select, or omit candidates lacking cache rows. A missing row means embed the candidate, not drop it. |
| P1 | **Rank corruption is not wholly harmless.** | The analysis/design correctly note that rank is ordering-only and cannot cap without a positive adjudicator verdict. But `_rank` also drives the novelty-floor short circuit and which `hub_top_k` candidates reach the ladder (`_add_with_block`, lines 250–288). Bad vectors can materially increase duplicates, LLM work, or exposure to a wrong positive verdict. D43's coexist fail-safe limits severity; it does not excuse weak validation. |
| P1 | **The cache lifetime is internally unclear and potentially unbounded.** | Design §2 says per process; §4.3 says “one adjudicator instance / worker process job.” In the current composition, the instance is process-scoped across jobs. A no-eviction map grows with every observation/NEW statement the process sees. If the intended scope is one handler call, define reset at the call boundary; if it is process-wide, define memory bounds and accept that eviction weakens literal at-most-once behavior. |

### Missing failure modes

- Provider request exceeds text-count or token limits while warming a large exhaustive block. Design §4.2 says “batch miss embed in one provider call”; misses must instead be chunked to the active provider's declared cap without capping block membership.
- Provider returns fewer/more vectors than inputs, mixed dimensions, NaN/Inf, or zero vectors without raising.
- Two threads/processes miss the same key, embed concurrently, and race durable upserts; correctness may survive, but the cost guarantee does not.
- Durable cache read/write times out or its table/migration is unavailable. Because the cache is an optimization, the design must say whether to degrade to process-local/live embed (recommended) or fail the assertion; it currently specifies neither.
- Crash or DB rollback after `_insert_new` aliases a vector under an uncommitted observation id. This is not likely to poison a future UUID, but it creates an unreachable process entry and must not create a durable row that outlives a failed observation insert.
- Statement-hash mismatch races between two writers. “Treat as miss” is insufficient without an atomic replace/upsert rule and post-write validation.
- Durable `bytea` decoding ambiguity (float width, endianness, compression, schema version, checksum) and a `dims` value inconsistent with decoded bytes.
- Model aliases silently changing implementation under the same configured string.
- Hard forget/deployment deletion. A durable vector is derived personal data. The sketch has no composite FK/cascade or purge integration; process-lifetime retention is also not discussed.
- Metering with multiple provider chunks. Each billable miss call needs a stable, unique call key; cache hits must not be recorded as billable usage.

### Implementation hazards versus current code

- `_rank` returns only `(candidate, score)` pairs, not NEW's vector. `_insert_new` therefore cannot safely alias an already-computed vector without either a cache lookup/alias operation or a changed return contract.
- `_insert_new` runs inside the same transaction as block/adjudication and is used by first-mention, clear-novelty, coexist, contradiction, and supersede branches. Most first-mention inserts have no rank vector to write through; implementation must not trigger a new embed merely to satisfy an insert hook.
- Exact-match evidence exits before `_rank`, while semantic evidence collapse happens after `_rank`. The two paths must not be treated as having the same available vector.
- Newly inserted observations are appended only to the in-memory candidate list via `_remember_candidate`; that record has no vector. The cache must be keyed/updated separately without changing candidate membership.
- Current `_rank` sends NEW plus all candidates in one provider call. Introducing cache misses requires preserving input-to-key alignment across multiple provider batches and reconstructing results in candidate order.
- Current calls occur while an entity advisory transaction lock is held. Cache storage must not open a conflicting nested transaction on the same observation row; durable writes need a defined transaction/connection strategy.
- A durable table introduces migration, backup, retention, hard-forget, and tenancy obligations absent from design §9. `deployment_id` must participate in API lookups even though the logical API currently omits it.

### Concrete required changes

- **`plan/designs/observation_rank_embedding_cache_design.md` §§2, 4.1:** replace `embedding_model` identity with a resolved `embedder_generation` containing provider/model revision, dimension/truncation, distance/input policy, and relevant parameters. Include `deployment_id` in durable APIs and use tagged key variants so UUID ids and text digests cannot collide.
- **§4.1:** define the exact embedded bytes. Prefer `sha256(statement.encode("utf-8"))` for NEW because current exact matching and embedding both use raw text. If whitespace normalization is desired, normatively embed the normalized text too and version the normalizer.
- **§4.2:** add an explicit alias rule: NEW-hash → newly created observation id only after/with a successful insert; never alias NEW under an existing id on evidence collapse. Define how `_rank` exposes or aliases its NEW vector without re-embedding.
- **§4.2:** require provider-cap-aware chunking, strict input/output cardinality, one expected dimension per generation, finite/nonzero vectors, and all-or-nothing cache insertion for each validated provider response.
- **§4.2/§4.3:** require a thread-safe single-flight for process misses. Define the actual lifetime and memory policy. If bounded eviction is allowed, weaken “embedded at most once per process” to a measurable best-effort cache objective.
- **§4.4:** make the durable schema use `embedder_generation` in its PK; specify vector encoding/version/checksum; add a composite FK `(deployment_id, observation_id)` with deletion behavior or explicit hard-forget hooks; define atomic `INSERT ... ON CONFLICT ...` behavior for statement-hash mismatch.
- **§4.4/§5:** state that durable cache failure falls back to validated process cache/live embedding and never changes the SQL candidate set. A provider failure still propagates as today. A cache write failure after a usable embed should not turn an otherwise correct adjudication into a false decision.
- **§4.5 and `plan/designs/observations_design.md` §3:** update all relevant prose, not only the hub-narrowing sentence. §3's novelty gate, deferred label/embed step, and cost-lever paragraphs also refer to “precomputed embeddings”/future blocking. Make clear that E3 uses the rank cache over immutable `statement`, while P1 uses `obs_label` for retrieval and is never E3 membership authority.
- **§8 acceptance criteria:** add tests for whitespace variants, evidence collapse with differently worded NEW, concurrent same-key misses, provider cap chunking, malformed vector responses, dimension/generation changes with unchanged model name, durable-cache outage/corruption, transaction rollback, and hard-forget cleanup. Assert that `_BLOCK_ENTITY` membership and D43 cap/coexist behavior are unchanged.
- **`src/rememberstack/spine/observation_adjudication.py` implementation plan:** name a cache port/object injected into `ObservationAdjudicator`; do not hide durable I/O in `_cosine` or make `_rank` a candidate selector.

### Optional improvements

- Log hit/miss/invalid/eviction/single-flight-wait counts by deployment, entity, and embedding generation; retain cost-ledger entries only for actual provider calls.
- Add a bounded prewarm for the exact open block at `add_observations` start, but let the first `_rank` lazily warm if the batch exits through exact/first-mention paths.
- Record cache age and bytes, not merely entry count; embeddings dominate memory.
- Consider storing normalized fp16/halfvec durably only after recall parity is measured; do not silently change numeric representation in v1.

## 2. `plan/analysis/observation_rank_embedding_cache.md`

The analysis reaches the right architectural conclusion: current behavior repeats immutable open-statement embeddings, process-local then optional durable caching is the least coupled fix, and Lance must not become write-path membership authority. Its complexity argument and distinction between E3 rank text and P1 retrieval labels are sound.

The analysis nevertheless understates four issues that the binding design must repair:

- §4 says “same model + version,” but the proposed design regresses this to a model string. D63's complete generation must survive the analysis-to-design transition.
- §4 calls rank permutation lower severity. That is directionally true because D43 requires a positive match before capping, but rank also drives the novelty shortcut and top-k ladder exposure; corruption is not cost-only.
- §§2–3 omit process memory growth, concurrency/single-flight behavior, provider request caps, and the response-validation boundary.
- §5 assumes statement immutability but does not cover hard-forget, durable-cache orphan rows, or cache-store outage.

These are correctable omissions; they do not invalidate the recommendation.

## 3. `plan/designs/pipeline_checkpointing_design.md`

### Verdict: Accept with changes

Incremental Phase L and Phase E checkpoints are the correct recovery model, and Lance-before-ref must remain binding. The proposed design cannot yet be accepted because it does not define a coherent label/vector generation state machine, asserts a readiness rule the current P1 search path does not enforce, and leaves concurrency as an implementer's choice even though different choices have different correctness properties.

### Correctness risks

| Priority | Risk | Evidence and consequence |
| --- | --- | --- |
| P0 | **Generation refresh can falsely look embedded.** | Suppose G0 has a non-null ref. Design §4.1 stamps a G1 `fact_label_version`; §4.2 selects G1 labels whose ref is missing. The design never requires Phase L to clear/version the old ref, so it can skip Phase E and advertise a G1 label backed by a G0 vector. Current schema has no `fact_label_embedding_version` (`postgres_schema_design.md` relation columns), making the states indistinguishable. |
| P0 | **The proposed generation omits required provenance.** | Design §3 binds `FACT_LABEL_VERSION + embedding_model`. It omits `label_model` and its revision/parameters for LLM labels, predicate-template/registry version for deterministic labels, canonical-name inputs, and D63's embedding dimension/parameters. A label-model or registry change can therefore be silently skipped, while an embedding-model change unnecessarily forces a new LLM label. Label generation and embedding generation are distinct identities and must be stored separately. |
| P0 | **§4.5 readiness is false against current search.** | `P1FactRow` contains id/deployment/kind/label/status/vector but no ref or generation (`model/chunks.py:P1FactRow`). Lance `search_facts` and `search_facts_scored` filter only deployment and optional kind (`adapters/selfhost/lance.py`, lines 425–447 and 647–674). Query hydration confirms fact validity but does not filter `fact_label_embedding_ref`/`obs_label_embedding_ref` (`surfaces/query_engine.py:current_context` and `lookup_observations`). Clearing a PG ref does not remove or hide an old Lance row. The design must define and implement where readiness is enforced. |
| P0 | **Batch-lock and document-lock options are not correctness-equivalent.** | A relation can have evidence from multiple documents, so two document jobs can select the same relation. Current code avoids interleaving with a deployment-wide session advisory lock (`FactCatalog.label_lock`, lines 171–190). If the lock is released per batch without per-row claims/CAS, workers can produce labels A/B, upsert vector A, store label B, and stamp a ref that falsely associates them. Design §4.4 must choose a normative v1 protocol. |
| P0 | **The embed stamp is not tied to the exact label embedded.** | §4.3 stamps by row id after Lance succeeds, but does not require `(label_generation, label_input_hash, embedding_generation)` compare-and-set. A label/name/status change between selection and stamp can make the vector stale at the moment it is advertised. Lance rows likewise carry no label hash/generation. |
| P0 | **The existing stamp method violates the proposed Phase L contract.** | `FactCatalog.record_fact_label` currently writes `fact_label`, `fact_label_version`, and `fact_label_embedding_ref` together (`fact_catalog.py`, lines 215–227 and SQL lines 486–494). Reusing it for Phase L would stamp readiness before Lance and directly violate §4.3. Separate label-checkpoint and embedding-stamp operations are mandatory. |
| P1 | **Handler success is not defined as an authoritative empty recheck.** | Once work is chunked, selecting one snapshot and returning can mark the document stage complete while current-generation rows remain due to races, limited selectors, or a changed input. Completion must recheck Phase L and E selectors for the document under the chosen concurrency protocol. |
| P1 | **The design overclaims billing idempotence.** | A crash after an LLM/embed response but before its PG checkpoint necessarily reissues paid work unless the provider supports idempotency or the output itself is durably journaled. Similarly, Lance-upsert-before-ref has an unavoidable retry window. Checkpointing gives at-least-once execution and idempotent durable completion; it minimizes, but cannot promise to eliminate, duplicate billing. |
| P1 | **A raw Lance orphan can be visible before its PG stamp.** | Lance-before-ref deliberately permits “upsert succeeded, stamp failed.” If Lance presence itself makes a row searchable—as current code does—the orphan is queryable. If PG ref is intended as the gate, all public hydration paths must reject it and compensate for top-k starvation. The design currently claims both models at once. |

### Required state model

The binding design should define these states explicitly rather than infer them from one overloaded version and one nullable ref:

| State | Label state | Embedding state | Allowed behavior |
| --- | --- | --- | --- |
| U | absent/stale | absent or older generation | select for Phase L; not current-ready |
| L | current `label_generation` + `label_input_hash` | absent/stale for target `embedding_generation` | select for Phase E; may remain available only under an explicitly defined stale-generation policy |
| V (transient/orphan) | current | Lance upsert exists for exact label hash/generation; PG ref not stamped | retry/reconcile; must not be returned as ready if PG ref is the readiness gate |
| E | current | Lance row and PG ref/version match exact label hash + embedding generation | current semantic channel ready |

The transition `L -> V -> E` preserves Lance-before-ref. A Phase E stamp must be a conditional update against the label generation/hash it actually embedded. Failed compare-and-set leaves V orphaned for reconciliation and reselects the row; it must never stamp a mismatched label/vector pair.

### Missing failure modes

- LLM returns successfully and the worker dies before the Phase L commit; exact-once cost is impossible without output journaling/provider idempotency.
- Phase L PG update affects zero rows because the relation was deleted, hard-forgotten, or changed; current `record_*` methods do not check rowcount and scope stamps only by id, not `(deployment_id, id)`.
- Relation/observation is invalidated, superseded, hard-forgotten, or changes label input after Phase E selection but before Lance upsert/stamp, creating a zombie/stale index row.
- Entity canonical name, entity type, predicate label template, or registry surface changes without changing the relation triple id. Version-only selectors do not notice unless an input hash or invalidation fanout exists.
- Lock connection loss releases a session advisory lock while provider work continues; correctness cannot depend on the process believing it still owns a lock.
- Two document jobs share a relation and run under a shorter batch-lock policy.
- Provider returns malformed count/dimension/non-finite vectors, or a request exceeds provider token/count limits despite being under the example `1024` texts.
- Lance call returns after a partial physical write or raises after committing. Retry is safe only if upsert-by-id and row payload/generation are idempotent and readiness confirmation is explicit.
- PG stamps partially succeed for a Lance batch. The retry selector must skip the stamped prefix and reconcile/re-embed only the remainder.
- Generation/config changes during a handler. Generations and provider caps must be pinned once at handler start; no mixed-generation batch is valid.
- Stale/unready Lance rows fill top-k and are dropped by PG hydration, yielding false empty/short results unless nomination overfetch/replenishment is specified.
- Observation and relation labels have different production paths but share a batch. One malformed row must not cause prior successfully checkpointed batches to be replayed.
- Cost-meter failure after provider success and before checkpoint; define whether it blocks the checkpoint and causes rebilling.

### Implementation hazards versus current code

- `LabelFactsHandler` currently materializes every relation label and every observation row, embeds them in one provider call, performs one Lance upsert, then stamps rows one at a time (`workers/p1.py`, lines 149–225). Both phases and batching must be structurally separated.
- `relations_for_labeling` selects only rows whose `fact_label_version` differs and returns subject/predicate/object—not stored labels for Phase E. A new `relations_for_embedding` selector/model is required.
- `_SELECT_RELATIONS_FOR_LABELING` uses version inequality but does not independently require non-null/nonblank `fact_label`; the design's “or null label” condition must be implemented explicitly.
- `observations_for_embedding` uses `obs_label_version` as the embedding-generation stamp. The proposed design calls this a label version even though observations have no Phase L labeler. Rename/split the semantic meaning or stale/current logic will remain confusing.
- `record_fact_label` currently sets the ref; it must split into `record_fact_label_checkpoint` and a conditional `record_fact_embedding`, with deployment scope and rowcount checks.
- `P1FactRow` and the Lance facts schema lack `embedding_generation` and `label_input_hash`, so current search cannot filter current rows or reconcile exact payloads.
- `EmbedClaimsHandler` is also a single document-sized provider request followed by one Lance write and one all-ids PG stamp (`workers/p1.py`, lines 76–116). Design §5 requires real batching and per-batch stamping, not just a comment.
- `P1Settings` has no provider batch-size/token budget setting. The acceptance criterion's hard `<=1024` is not sufficient for providers with lower limits.
- Current `label_lock` is deployment-wide and held across all provider calls. Retaining it is the simplest safe v1 choice; shortening it requires additional durable row ownership/CAS machinery, not merely moving the context-manager boundary.
- Public fact search is nomination-first and PG-confirmed for truth, but current confirmation SQL does not check embedding readiness/generation. That blast radius extends beyond `workers/p1.py` and `fact_catalog.py` to P1 models/ports, Lance adapter/schema, query hydration, and migrations.

### Concrete required changes

- **`plan/designs/pipeline_checkpointing_design.md` §§3–4:** separate `label_generation`, `label_input_hash`, `embedding_generation`, and `embedding_input_hash/ref`. For LLM labels, generation includes label model/revision/parameters and prompt version; for deterministic labels, template/registry version; embedding generation uses the full D63 component version, not the model string.
- **§4.1:** define an atomic Phase L checkpoint that writes label + label generation + input hash and marks the target embedding stale without falsely claiming the old ref is current. Define invalidation when canonical names/templates change.
- **§§4.2–4.3:** add a stored-label relation embed selector and require the `L -> V -> E` compare-and-set transition above. The PG stamp must match deployment, fact id, label hash/generation, and embedding generation and must assert exactly one affected row.
- **§4.3:** require Lance rows to carry at least fact kind/id, deployment, label, label hash, and embedding generation. Upsert idempotency and partial/error-after-write semantics must be contractual on `FactIndexPort` or a narrower checkpoint-aware port.
- **§4.4:** choose one v1 locking protocol. Recommended minimal v1: retain the deployment lock for the whole handler while checkpointing each label/batch, then consider batch locks only after per-row leasing/CAS exists. In all cases, CAS is the final defense against mutable inputs and lost locks.
- **§4.5:** choose and document one readiness authority. If PG ref/version is the gate, every public Lance nomination must be PG-confirmed against E state, with overfetch/replenishment and tests proving stale/orphan rows cannot produce false results. If Lance presence is the gate, rewrite the missing-ref claim and explain what the PG ref proves. Do not leave the present contradiction.
- **§5:** define `EmbedClaimsHandler` chunking by the active provider's text and token caps, then `upsert_claims -> record_claim_embeddings` for each successful batch before continuing. Pin one embedding generation for the handler.
- **§6:** state the at-least-once billing boundary explicitly. Name the orphan-reconciliation path (re-upsert from PG label, hash-check existing Lance row, or rebuild) instead of “detect via rebuild path” without an owner/trigger.
- **§9 acceptance:** add adversarial tests for G0-ref/G1-label refresh, two docs racing the same relation, label change between embed and stamp, lost lock, Lance error-after-write, partial PG stamps, stale/orphan top-k crowding, hard forget between phases, malformed provider output, and final empty-selector completion. Preserve the existing kill/restart tests.
- **`src/rememberstack/spine/fact_catalog.py` implementation plan:** add distinct label and embedding selectors/stamps, conditional updates, deployment scoping, rowcount assertions, and input hashes/versions. Do not repurpose the current `record_fact_label` unchanged.
- **`src/rememberstack/model/chunks.py`, `ports/p1_index.py`, `adapters/selfhost/lance.py`, migrations, and query surfaces:** include and enforce generation/hash/readiness fields according to the chosen §4.5 model. These are required touchpoints missing from design §10.
- **`src/rememberstack/workers/p1.py`:** loop batches until authoritative selectors are empty; do not load or embed the entire document at once; use distinct stable cost call keys per label and embed batch.
- **Binding cross-links:** update the P1 worker module docstring as proposed, plus `p2_graph_design.md` §6 / relevant P1 retrieval prose if label generation or readiness semantics change. A checkpoint design must not silently amend the fact-index row contract.

### Optional improvements

- Use small PG label checkpoint batches for deterministic labels to reduce transaction overhead, provided the batch is still durable before Phase E and resume selectors remain exact.
- Store progress counters for operator visibility, but derive correctness/readiness from row state, never from counters.
- Before re-embedding an orphan, hash-check the existing Lance row and stamp it if it exactly matches; this can close the Lance-before-ref retry window without another billable embed.
- Consider a durable sub-work/outbox table if batch locking or cross-store reconciliation becomes operationally complex; defer it only after the simpler state machine is proven.
- Report `selected`, `labeled`, `embedded`, `stamped`, `orphan_reused`, `reembedded`, and `hydration_dropped_unready` per generation.

## 4. `plan/analysis/pipeline_checkpointing.md`

The analysis correctly identifies `label_relation` as the critical first target and correctly preserves Lance-before-ref. The hybrid “persist label, then embed/stamp in batches” recommendation is sound, as is treating observation adjudication separately because it already commits per entity.

The following claims need correction before they are carried into binding design:

- §4's “idempotent resume must not double-bill if outputs exist” is true only after the output checkpoint commits. Response-before-commit and Lance-before-ref windows remain at-least-once.
- The stage-ranking table describes `embed_claim` as stamped per batch “if” so implemented, but current `EmbedClaimsHandler` makes one document-sized embed call and stamps all ids afterward. The design must treat batching as a real code change.
- §4's readiness rule assumes query paths filter by embedding ref. Current Lance facts search and PG hydration do not. This is a verified implementation gap, not merely an acceptance-test chore.
- §4's lock discussion does not handle shared relations selected by different document jobs or lock loss. Shorter locks require ownership/CAS.
- The analysis omits generation/input-hash separation, canonical-name/template invalidation, provider caps, and orphan visibility/top-k starvation.

The recommendation remains valid after those corrections.

## 5. `plan/analysis/relation_fact_labels_in_p1.md`

### 5.1 Is keep-versus-drop argued soundly?

**Yes, with one framing correction.** Keeping relation vectors is not merely an analysis default: it is the current binding architecture. `p2_graph_design.md` §6 says relation semantic entry lives in Lance, keyed by `relation_id`, because LadybugDB is the structure engine. Dropping relation rows/vectors would remove a bound free-text entry channel and requires an explicit P1/P2/retrieval amendment plus an end-to-end retrieval gate. BEAM ingest cost alone is not evidence that the channel lacks product value.

The analysis is also right that observations are a separate non-graph channel and cannot be swept into a “drop facts” optimization.

Corrections/omissions:

- Separate “drop dense relation vectors” from “drop relation records from the P1 facts channel.” Structured relation lookup can use PG/P2; semantic relation nomination is what is actually at stake.
- Evaluate the implementation that exists. Although `p2_graph_design.md` §6 describes semantic+BM25 entry, current P1 facts expose semantic vector search only; `open_query_space_design.md` §10 explicitly defers lexical facts. Do not credit a nonexistent fact-BM25/RRF path to either arm.
- Claims may cover some edge questions, but their redundancy/noise and evidence grain are exactly why the bound relation channel exists. Only a paired end-to-end ablation can show whether agent workloads compensate through entity resolution + graph traversal.

### 5.2 Is deterministic S1/S4 sound versus an LLM?

**The deterministic direction is sound; the S1-first recommendation is too optimistic.** A canonical relation label should preserve a stored triple, not introduce an ingest-critical generative failure point. Deterministic production eliminates hallucination, direction reversal, prompt drift, retry cost, and nondeterminism. L4 (optional LLM display prose) is acceptable only off the readiness-critical path.

I would trial **S4 as a full predicate-specific template**, with S1 as the fallback/baseline:

- A `surface_verb` is insufficient. Predicates need full argument-aware templates such as `{s} works for {o}`, `{s} is part of {o}`, or `{s} reports to {o}`; grammar and direction are predicate-specific.
- S1 raw slugs are tolerable embedding tokens for governed predicates such as `uses`, but degrade on `other:*`, prepositional/noun predicates, inverse phrasings, and multilingual queries.
- Template identity must include the predicate-registry revision and the exact rendered inputs. Canonical entity names/types can change while `(subject_id, predicate, object_id)` remains constant, so “pure function of stored triple + registry version” needs an input hash or invalidation fanout.
- S5 types and predicate aliases/synonyms may improve generic or inverse questions; measure them rather than assume shorter is always better.
- D63's multilingual rationale makes multilingual and inflected query slices mandatory. An English S4 template may still work with the multilingual embedder, but that must be measured.

### 5.3 Eval gates required before binding

Use a frozen, provenance-tracked corpus and query set, with gold `relation_id` and answer/route outcomes. Split by document/entity cluster when computing confidence intervals so paraphrases of one fact are not treated as independent samples.

**Before replacing LLM labels with deterministic labels:**

1. Compare current LLM labels, S1, full-template S4, and S4+types/aliases on the actual semantic facts channel, using the same embedder generation and index parameters.
2. Cover direct triple statements, paraphrases, inverse/directional questions, vague/no-known-entity entry, entity-scoped history, same-entity/different-predicate hard negatives, rare and `other:*` predicates, canonical-name aliases, type-dependent questions, and multilingual/inflected queries.
3. Gate on Recall@k of gold `relation_id`, MRR/nDCG@k, precision/top-k crowding, and end-to-end answer/route success after PG hydration and optional P2 expansion—not Recall@k alone.
4. Pre-register non-inferiority margins. Recommended minimum: lower 95% confidence bound for deterministic minus LLM is at least **-2 percentage points overall** and **-5 points in every critical slice** for Recall@10 and end-to-end success; no critical predicate/direction slice may collapse behind a good aggregate.
5. Require **zero label-fidelity errors** on the registry template conformance suite: subject/object direction preserved, no added facts, no dropped qualifier encoded by the selected template, deterministic byte-identical output for the same generation/input.
6. Run checkpoint/rebuild tests proving a template or registry version change reselects/re-embeds exactly the affected facts and never exposes a mismatched label/vector pair.
7. Record ingest wall time, provider calls, dollars/doc, index bytes, and query p95. Quality is the gate; cost/latency establish the reason to switch.

**Before dropping the relation semantic channel:**

1. Run an end-to-end ablation of full relations+claims+observations+P2 versus claims+observations+P2, using shipped agent recipes rather than isolated vector recall only.
2. Use the same non-inferiority confidence gates above for relation-question answer success and gold-relation discovery, plus absolute product SLOs. Include vague/no-entity queries, where P2 cannot be the initial entry path.
3. Measure claim redundancy/crowding, number of query/tool steps, graph traversal success after entity resolution, latency, and evidence correctness. A claims hit that never resolves to the gold edge is not coverage.
4. Require no regression in temporal/directional relation questions and no compensation that adds query-time LLM calls contrary to the zero-LLM core search path.
5. Only after the ablation passes should a separate binding amendment remove relation vectors and update `p2_graph_design.md` §6, `retrieval_design.md`, P1 schemas/ports, recipes, readiness, rebuild, and migration behavior.

### 5.4 Disagreement with the analysis lean

- I agree with **keep relations in P1** and **remove per-relation LLM labels from the readiness-critical path**, subject to the eval above.
- I disagree with making S1 the presumptive production shape and using S4 only after S1 fails. Governed full templates are cheap and eliminate known grammatical/directional defects; S1 is the safe fallback for predicates without a template.
- “Recall within tolerance” in §8 is too vague and too narrow. The gate must be pre-registered, confidence-bounded, stratified, and end-to-end.
- An optional LLM display label must be a separate field/generation and must never gate or overwrite the deterministic retrieval label.

## Prioritized fix list

### P0 — required before either design is accepted into the binding corpus

1. **Checkpoint state/readiness:** split label and embedding generations/hashes; define U/L/V/E transitions; bind conditional stamps; choose and implement a real readiness authority across PG and Lance.
2. **Checkpoint concurrency:** choose a normative v1 lock/claim protocol and require CAS. Do not present batch and document locks as interchangeable without per-row ownership.
3. **Cache identity:** use the full D63 embedding generation and the exact embedded input; forbid NEW-vector aliasing onto an existing evidence-collapse observation.
4. **Cache safety:** require single-flight, provider-cap chunking, strict vector validation, exhaustive-candidate preservation, durable-store fallback, and hard-forget behavior.
5. **Cross-store recovery:** specify Lance orphan visibility/reconciliation and the at-least-once provider-billing boundary; remove exact-once implications.

### P1 — required before implementation PR acceptance

6. Split `FactCatalog` label and embed methods/selectors; add deployment-scoped CAS/rowcount checks and schema migrations for generation/input hashes.
7. Add generation/hash fields to P1 fact rows/Lance and enforce readiness in every public fact-search hydration path, including overfetch/replenishment for stale nominees.
8. Batch `LabelFactsHandler` and `EmbedClaimsHandler` under actual provider text/token caps; checkpoint/stamp each successful batch and recheck selectors before handler success.
9. Expand kill/race/corruption/generation/hard-forget tests listed in the two design sections; retain explicit assertions for Lance-before-ref, exhaustive block, D43 no-cap, and fail-safe coexist.
10. Update all affected binding prose and touchpoint lists. The blast radius includes observations §3, P1 model/port/Lance/query contracts, P2 §6/retrieval prose, Postgres schema/migrations, and forget/rebuild behavior.

### P1 — required before binding deterministic relation labels or dropping relation vectors

11. Run the frozen S1/S4/LLM retrieval non-inferiority and fidelity eval with critical-slice confidence bounds.
12. Keep relation vectors unless the separate end-to-end channel ablation passes; ingest cost and smoke completion are not substitutes for retrieval evidence.

### P2 — non-blocking

13. Add cache memory/age/eviction and checkpoint/orphan/readiness-drop telemetry.
14. Optimize PG checkpoint batch sizes and orphan vector reuse only after correctness tests pass.
