# Codex review — full-scope conventional embedding input architecture

**Review status:** independent analysis, not binding design. The proposal in
`plan/analysis/e1_context_prefix_efficiency/FULL_SCOPE_ARCHITECTURE.md` is also non-binding.
The current binding baseline remains D56/D58/D63/D79 in `decisions.md`,
`plan/designs/e1_chunks_design.md`, and the adjacent E2, retrieval, orchestration, and schema
designs.

The direction is good: replace an LLM-authored, always-on prefix with a deterministic,
versioned input renderer and make embedding progress durable below document grain. It is not yet
safe to promote verbatim. The largest unresolved issue is that a location-sensitive embedding
cannot keep D56's current content-only reuse rule. The proposal also treats “scalars exist” as if
filters will automatically be applied, leaves the Slack metadata contract undefined, and does not
specify a generation-safe P1 migration or readiness barrier.

## 1. High-level decisions — agree / disagree / amend

| ID | Verdict | Rationale |
|---|---|---|
| **H1** | **Accept** | Conventional `texts -> vectors` embedders and easy substitution are sound product constraints. This is a change to the binding baseline, however: D63 currently keeps the contextual branch as a fully designed alternate, so an amendment must explicitly retire that alternate rather than merely call it a non-goal in analysis. “Interchangeable” must cover dimension, distance metric, query/document preprocessing, provider caps, and generation migration—not just the Python method signature. |
| **H2** | **Accept with changes** | Splitting location facts, policy, and exact embedding input is the right correction. Add two more separations: canonical body/display text must remain distinct from embedding text, and E2 needs a typed **groundable location projection** distinct from non-quotable orientation metadata. Otherwise the overload simply moves from `context_prefix` into a broad JSON object or P1 `text`. |
| **H3** | **Accept with changes** | E1 location resolution and rendering should make no model-provider call. “No LLM” must mean no LLM call in this path, not that every input fact has deterministic provenance: D79 section roles and fallback structure can still be model-assisted upstream. Every location field needs provenance (`source`, `connector`, `deterministic_derived`, or `model_derived`) so rendering and grounding can make different safe choices. |
| **H4** | **Accept with changes** | A conditional header is superior to an always-on one. The proposed predicates are not yet a total policy: the `body_only` list has unclear AND/OR semantics, short channel-export chunks conflict with the message-atom rule, and precedence is unspecified when several rules match. `T_short`, `H_max`, and alpha are hypotheses, not design facts; their counter and exact normalization must be model-independent and versioned. |
| **H5** | **Needs decision** | The shape-aware distinction is correct, and a compact deterministic header for a long channel/thread export is sensible. The proposed default “one Slack message -> body only + scalars” is not established. Scalars help only when a caller or recipe actually supplies filters; they do not improve an unfiltered vector search. Elliptical atoms such as “yes, ship it” need location more, not less, and claims do not rescue questions, non-claim utterances, or passage-only retrieval. Bind this only after a body-only vs compact-header vs (if needed) dual-vector evaluation, including filtered and unfiltered queries. |
| **H6** | **Accept** | This is already substantially binding under D79: summaries may orient E1/E2 but are not members of the grounding union. Keep them out of default embedding input as well. Do not confuse “out of E2 grounding” with “absent from the E2 bundle”; orientation-only use remains legitimate and already designed. |
| **H7** | **Accept with changes** | Provider work needs chunk-grain durable identity and bounded call batches. Cheap pure location resolution/rendering does not need three queue stages or one `processing_state` row per pure function. Prefer a hybrid: persist prepared inputs per chunk in a document transaction, fan out durable chunk embed work, coalesce compatible chunk rows into provider batches, and finish through a document/representation barrier. Cross-store P1/PG ordering, poison-batch splitting, stale-generation rejection, and last-child readiness must be binding. |
| **H8** | **Accept with changes** | E1 §5 is the correct normative home for input semantics, with D63 and orchestration amendments. The amendment set is wider than proposed: D56/A3, D58, D79/E2 grounding, retrieval filter semantics, P1 generation/index design, Postgres schema, component/stage enums, readiness, worker inventory, and hard-forget all change. Do not create a second standalone policy design unless the E1 section becomes unwieldy; one normative home plus explicit cross-references is simpler. |
| **H9** | **Accept with changes** | Free-form rendered headers should leave the E2 grounding union. Replace them with a typed, allowlisted set of source-derived location elements carrying provenance pointers. Channel, author, message timestamp, and source title can be groundable when supplied by the source/connector; section role, synthetic ordinal, policy mode, and model-derived orientation cannot. Removing `context_prefix` without this replacement would reduce decontextualization quality. |

## 2. Mechanism deep-dives

### 2.1 New design section: “Embedding input policy”

**Strengths.** A pure renderer gives reproducible bytes, removes an O(chunks) LLM dependency,
supports exact A/B attribution, and keeps the embedder port conventional. E1 owns how block-aligned
chunks become embedding inputs, so `plan/designs/e1_chunks_design.md` §5 is a better normative home
than retrieval design. Retrieval should own only filter/query semantics; E2 should own grounding.

**Risks.** The proposed table is not executable as written:

- “all that match” under `body_only` is ambiguous, and no precedence resolves simultaneous rules.
- “Token count” silently imports a tokenizer. If it is the configured embedder's tokenizer, the
  policy changes when the model changes and H1 is violated. If it is some other tokenizer, that
  tokenizer and version must be named.
- `H_max` bounds only the header, not the complete request. E1 deliberately permits oversized
  atomic chunks (`plan/designs/e1_chunks_design.md` §4), so the port still needs a typed
  over-limit policy for the whole text.
- Header fields need canonical Unicode normalization, whitespace rules, escaping/delimiters,
  null rendering, timestamp/time-zone format, field-level truncation, and protection against
  source text injecting fake header fields.
- `ordinal/N` is volatile: inserting one early chunk changes `N` for every occurrence and can
  force a document-wide re-embed, defeating D56's edit-local economics. Numeric ordinal also has
  little query value.
- The proposed `T_short=48`, `H_max=48`, and alpha=1.0 encode an unproven theory that vector
  influence is proportional to visible token counts. Embedding behavior is not linear in that
  way.

**Alternatives.** An LLM renderer is unnecessary. A separate standalone design would avoid making
E1 §5 long, but would create another authority for a mechanism whose output is specifically an E1
artifact. A per-source plugin renderer is also premature: it risks connector-specific semantic
drift and makes cross-model evaluation harder.

**Recommendation.** Put one total pure function in E1 §5.x. Its version must content-address all
behavior: location schema version, normalized field allowlist/order, renderer template,
normalizer, length counter, constants, source-shape routing, and null/escape rules. Use a
policy-pinned model-independent counter (or a byte/character bound) for mode selection; embedder
request limits remain capabilities of the embedder generation. Do not branch policy behavior on
model ID.

Define migration and provider-call triggers precisely:

| Change | Required action |
|---|---|
| Body or a header-affecting location field changes | Re-render; call the embedder only if the exact `embedding_text_hash` changes. |
| Policy version changes | Re-render every in-scope occurrence; stamp the new derivation version. An unchanged exact text hash may reuse the same vector under the same embedder generation. |
| Scalar-only metadata changes | Update/rebuild the P1 scalar projection; do not re-embed. |
| Embedder generation changes (resolved model/revision, dimension, metric, normalization/instruction params) | Re-embed and rebuild/swap the corresponding P1 generation. |
| Summary-only regeneration | No render or embed action unless a separately allowed location field changed. |

This is more exact than “any policy change means re-embed”: a policy bump is always a migration
trigger, but vector computation is a pure function of the effective text plus embedder generation.

### 2.2 P1 scalars expansion

**Strengths.** Scalar prefilters are the right place for exact source/role/time constraints and
avoid stuffing every coordinate into prose. They also let message atoms share body embeddings when
the retrieval request already carries a precise channel/thread scope. `section_role` establishes
the pattern in D58.

**Risks.** The proposal currently uses “source kind” for two different concepts. In the binding
schema, `documents.source_kind` is a connector kind such as `google_drive`, `email`, or `url`
(`plan/designs/postgres_schema_design.md` §6); it does not say whether the document is a message
atom, thread, channel export, or long-form document. The current `P1ChunkRow` exposes only IDs,
`section_role`, `text`, and `vector` (`src/rememberstack/model/chunks.py::P1ChunkRow`), and
`LanceChunkIndex.upsert_chunks` mirrors exactly those fields. Adding columns is therefore a schema
migration and backfill, not just a model change. High-cardinality `thread_id`, `message_id`, and
`author_id` can make indiscriminate bitmap indexes expensive; mutable display names also create
staleness and privacy problems. Most importantly, a stored scalar is inert unless the search port,
recipes, and caller expose and use its typed filter.

**Alternatives.** Put all metadata in Postgres and hydrate/filter after ANN; this is simpler but
loses prefilter selectivity and can damage recall. Put arbitrary connector JSON in Lance; this
avoids schema work but produces an ungoverned recipe surface and adapter-dependent filters.

**Recommendation.** Define a small universal scalar contract and add dimensions only with a query
use case and selectivity measurement:

- **Lance, universal low-cardinality:** `source_kind` (connector), a separate `source_shape`
  (`document | message_atom | thread | channel_export | transcript | media`), `section_role`, and
  active embedding generation.
- **Lance, source-specific only when recipes support them:** stable deployment-scoped
  `channel_ref`, `thread_ref`, `author_ref`, and an instant or time range. Use bitmap indexes for
  bounded enums and measured B-tree/range indexes for selective high-cardinality fields. Do not
  index every declared scalar automatically.
- **Postgres authority:** the complete typed location snapshot, raw connector references,
  provenance, display names, exact timestamps, and ID/name mappings. Use stable opaque IDs in
  Lance; keep mutable names out unless measured search semantics require them.
- **P3/mounts:** friendly reconstructed presentation (paths, channel/user names, deep links), never
  the authority and never the only copy of a filter key.

Every scalar needs a declared recipe operator, null semantics, namespace, and hard-forget path.
Channel/user metadata duplicated into P1 or embedding text is still personal/source data; D74 must
purge it with the chunk rows, and metadata correction/rename behavior must be specified.

### 2.3 Storage shape

**Strengths.** Persisting location provenance, policy generation, exact input hash, and the text
that P1 actually embedded makes audits and migrations testable. Retaining an optional header makes
mode behavior inspectable.

**Risks.** Storing the full `embedding_text` in the Postgres `chunks` row duplicates every chunk
body and contradicts the existing D37/D8 split: the binding schema says the body is an artifact
slice, text/vector live in Lance, and PG holds offsets and stamps
(`plan/designs/postgres_schema_design.md` §7). A single JSON object without a schema version and
provenance is not a contract. A single `embedding_ref`/`embedding_version` on `chunks` also cannot
safely represent old and new vector generations during a re-embed. Current P1 upserts by
`chunk_id`, so a backfill overwrites the active row in place
(`src/rememberstack/adapters/selfhost/lance.py::LanceChunkIndex.upsert_chunks`). That makes an
atomic generation cutover impossible.

There is also a mutable-join problem. `ChunkCatalog._SELECT_CHUNK_SOURCE` reads `documents.title`
at execution time, while the title is lineage metadata rather than an immutable version field. A
rename can therefore alter a would-be canonical header without a content version or defined
re-embed event.

**Alternatives.** The minimum-change shape adds fields directly to `chunks` and continues
overwriting P1 by chunk ID. It is workable only if migrations may expose mixed generations and if
there is no need for rollback. A separate generation record costs more schema work but matches
D63's promised version-scoped re-embed migration.

**Recommendation.** Use three storage layers:

1. On the chunk occurrence (or a one-to-one typed location row), store immutable
   `location_facts`, `location_facts_schema_version`, provenance, and `location_facts_hash`.
   Connector metadata changes must either mint a new snapshot/generation or explicitly retain the
   old snapshot; never silently change a join result.
2. Store derivation state in a `chunk_embedding_generations`-like record keyed by chunk and
   embedder generation: `policy_version`, `mode`, `embedding_text_hash`, `embedding_generation`,
   state, and `embedding_ref`. This supports retries and old/new coexistence. The current columns
   may become the active pointer or be retired.
3. In P1, store the exact `embedding_text`, vector, body/display text as a separate field if
   lexical/display consumers need it, and typed scalars. Key rows by chunk plus embedding
   generation (or use generation-separated datasets) so a completed generation can be swapped in
   atomically.

Do not store the full body-bearing embedding text in PG. A deterministic policy can reconstruct it
from the immutable body slice and location snapshot; PG stores the SHA-256 of exact normalized
UTF-8 bytes and P1 stores the actual input. Rename `context_prefix` to
`rendered_location_header` only if the small rendered value is useful for audit; otherwise derive
it. Existing LLM prefixes should migrate as an explicit `legacy_llm_prefix_*` policy generation,
not be silently treated as v1 deterministic facts.

### 2.4 Work graph and durability

**Strengths.** The proposal correctly rejects the current blast radius. Today
`EmbedChunksHandler.handle` loads every chunk, resolves every prefix sequentially, makes one
embedding call for all fresh texts, then writes P1 and PG
(`src/rememberstack/workers/e1.py::EmbedChunksHandler.handle`). `_embed_follow_up` preserves the
document-version target and uses a model-independent `E1_EMBED_VERSION`, despite the orchestration
design already saying E1 is the chunk fan-out point (`plan/designs/orchestration_design.md` §2).
Smaller durable units align implementation with that binding intent.

**Risks.** “One chunk for render, one batch for embed” does not yet map to work identity. The
`processing_state` uniqueness key is target/stage/component version; `content_hash` is diagnostic,
and there is no `embedding_batch` target or resolve/render stage in the binding enums. A batch also
needs a stable manifest: otherwise retrying with a different B changes call identity and cost
attribution. One poison text can repeatedly fail and dead-letter innocent batch peers. P1 and PG
cannot commit atomically. Finally, no rule says who enqueues E2 after all children finish, how an
empty document completes, or how stale children from an obsolete representation are rejected.

**Alternatives.** Three queue stages per chunk make every state explicit but create large
`processing_state`/delivery overhead for two cheap pure functions. One document row plus chunk
sub-stamps is simpler, but then the row's three-attempt budget and one-worker ownership remain a
poor match for hundreds of independently failing provider batches.

**Recommendation.** Use a hybrid graph:

1. Chunk packing resolves and renders all location inputs in the same document/representation
   transaction, or a single document-grain `prepare_embedding_inputs` stage does so. Persist
   per-chunk derivation stamps, but do not enqueue pure work per chunk.
2. Fan out one durable `embed_chunk` work identity per chunk and embedding generation. A batch
   runner may co-claim compatible pending rows from the same deployment, document, lane,
   generation, dimension, and request-limit envelope. Batching is a provider-call optimization;
   chunk rows remain the durability and retry units, matching D58's designed E2 pattern.
3. Give each call a deterministic batch-manifest hash and ledger owner. Validate response count,
   dimension, resolved model generation, and per-item mapping before any completion stamp. On a
   content-dependent batch failure, deterministically bisect down to the poison item; transient
   provider failures retry the unchanged manifest.
4. Write P1 idempotently first, then stamp the PG generation, then mark child work succeeded.
   Retrying after either crash point re-upserts safely. Treat the PG stamp as readiness authority;
   repair orphan P1 rows. Do not expose the new P1 generation to queries until the representation
   barrier succeeds.
5. The last child idempotently enqueues a document/representation finalizer. The finalizer verifies
   the expected chunk-manifest hash (including zero chunks), all active-generation rows, and no
   stale representation before chaining E2 or swapping the current P1 generation.

The embedding component version must identify resolved model/revision, dimension, metric,
normalization/instruction parameters, adapter generation, and policy compatibility. The current
raw model setting plus `E1_EMBED_VERSION` is not enough for D12 idempotency or a safe model swap.

### 2.5 Slack and message-atom policy

**Strengths.** Ingest shape is more important than connector brand. One message, a thread, and a
channel dump should not receive the same rendering. Deterministic channel/thread/time coordinates
are better than LLM prose, and keeping long headers off tiny bodies is a legitimate concern.

**Risks.** The necessary metadata substrate does not exist in the proposal or current E1 model.
`ChunkSource` has document title/source kind and version dates; it has no channel, message, author,
or thread fields (`src/rememberstack/model/chunks.py::ChunkSource`). Connector identity stores a
`source_ref`, but no binding contract maps Slack metadata to block/chunk occurrences. For a channel
export, one chunk may contain several authors or threads, so a singular `speaker`/`thread_id` header
can be false. A compact header can also leak private channel names/user identities into P1 text,
and those names are mutable.

The body-only premise is weakest for the shortest, most elliptical messages. It works when the
query is already filtered to the right channel/thread, but ordinary unfiltered semantic search has
no way to use inert scalars. “Claims are the needle index” does not cover dropped questions,
greetings, advice, or the user's request for the original passage.

**Alternatives.** Always use a compact source-derived header; require scoped retrieval recipes for
message atoms and use body-only; or store two conventional vectors (body and compact-context) and
fuse them. The dual-vector option preserves H1 but adds cost/index complexity and should be a
measured fallback, not the initial design.

**Recommendation.** First bind a connector/source-shape contract: stable message/channel/thread/
author refs, immutable event time, display snapshots, provenance, and a block-to-message mapping
that survives chunk aggregation. Make thread boundaries packing/section signals if the product
expects thread-specific retrieval. For a multi-author chunk, render channel plus time range and
thread scope only when singular; never invent one speaker for a group.

Evaluate at least three arms on message atoms: body-only with no filter, body-only with explicit
channel/thread filters, and compact header plus body. Segment by self-contained versus elliptical
messages, exact duplicate bodies, multilingual text, DMs/private channels, and time/author queries.
Until that result exists, H5's atom default should remain an explicit decision, not v1 policy.

### 2.6 Claims versus chunks

**Strengths.** The D58 distinction remains useful: claims are decontextualized needles and chunks
are coherent passages. Keeping section summaries out of grounding prevents second-order facts
from laundering into claims.

**Risks.** The proposal leans too heavily on claims to justify weak chunk vectors. D58 explicitly
keeps chunks as an independently searchable passage channel (`plan/designs/e1_chunks_design.md`
§5); claims are not a substitute for passage recall. Conditional headers also affect E2 today:
`src/rememberstack/workers/e2.py::_bundle_text` includes `context_prefix`, and
`_source_grounding_elements` admits it to the D32 membership union. Simply omitting or removing it
can reduce the model's ability to resolve “he”, “there”, or relative message context.

**Alternatives.** Keep deterministic rendered headers in the grounding union. That is safer than
the current LLM prefix but still treats formatting prose as evidence. Or exclude all location from
grounding, which makes connector assertions such as author and timestamp unusable for
decontextualization.

**Recommendation.** Give E2 two explicit views:

- `location_orientation`: all safe hints, including non-quotable role/path and D79 summaries.
- `groundable_location_elements`: only source/connector-derived strings with typed source refs and
  immutable occurrence provenance.

Allow E2 additions to cite the second set with new closed source kinds such as `document_title`,
`message_author`, `channel`, and `message_time`; keep role, ordinal, summary, and rendered-header
syntax out. Bump the extractor/grounder generation and update D32/D79 together. Then header mode can
change chunk vectors without silently changing what E2 is allowed to assert.

### 2.7 A3 / D56 carry-forward and duplicate locations

**Strengths.** Storing policy/version/hash makes exact reuse possible. Deterministic rendering also
removes A3's original reason for replaying nondeterministic LLM bytes.

**Risks.** This is the proposal's most important correctness conflict. Binding D56 says embeddings
key on `(chunk content hash, embedding version)`, and E1 §7 A3 carries an unchanged prefix even when
the chunk moves. Current code implements that literally: `ChunkCatalog._SELECT_CARRY_FORWARD` uses
`DISTINCT ON (chunk_content_hash)` and chooses the lowest-ordinal duplicate from the nearest prior
version; `EmbedChunksHandler._resolve_prefix` then reuses it by content hash. If v1 embeds current
section path, channel, or position, identical bodies at different locations must not all inherit
that one prior occurrence's vector.

There is also an economy conflict. Including global ordinal or `N_chunks` in canonical text means
a one-chunk insertion may change the exact input of every later chunk. The design cannot both claim
current canonical location and promise cost proportional to the edit unless it limits embedded
coordinates to stable/local facts.

**Alternatives.** Preserve old headers for unchanged bodies, accepting stale location; or put every
coordinate into the header and accept broad re-embedding. Neither is a good default. Stale location
violates the new contract, while ordinal-driven fan-out spends heavily for weak retrieval signal.

**Recommendation.** Amend D56/A3 explicitly:

- Always recompute deterministic location facts and render bytes for the current occurrence.
- Reuse a vector only on `(embedding_text_hash, embedder_generation)`, not content hash alone.
- Use chunk content hash only to avoid rereading/re-normalizing the body and to locate candidate
  reuse; verify exact input hash before copying.
- Preserve A3 only for legacy nondeterministic LLM prefixes. Deterministic v1 needs byte
  reproducibility, not blind carry-forward.
- Exclude global `ordinal/N` from the default embedded header. Keep them as structured provenance
  or scalars. If an evaluated source policy truly needs position, make its re-embed fan-out an
  explicit accepted cost.
- Treat equal body-only texts in different locations as deliberately sharing vector values but
  retaining separate chunk rows/scalars. Once a location header differs, their input hashes and
  vectors differ.

This amendment is required before the architecture can claim both D56 correctness and duplicate
location disambiguation.

### 2.8 Embedder interchangeability

**Strengths.** The existing `EmbeddingRequest` already has the desired narrow shape—model plus a
non-empty tuple of texts (`src/rememberstack/model/model_provider.py::EmbeddingRequest`). A pure
model-agnostic renderer does not reintroduce contextual APIs.

**Risks.** The proposal understates what changes with a conventional model. Dimensions, preferred
distance metric, vector normalization, document/query instructions, maximum per-text length,
batch-size/token ceilings, and provider model aliases can differ. The self-host Lance adapter
currently hard-codes IVF_FLAT with L2 distance
(`src/rememberstack/adapters/selfhost/lance.py::_build_vector_index`). A dimension or metric change
is a P1 generation rebuild, not merely config plus calls. Likewise, using the configured model's
tokenizer for `T_short` makes renderer output model-specific. A provider alias can also resolve to
new weights while the configured string remains unchanged.

**Alternatives.** Standardize on one model/dimension/metric forever, which violates H1 in practice;
or let every adapter silently rewrite texts, which destroys auditability.

**Recommendation.** Define an immutable embedder-generation descriptor covering resolved weights
or revision, document and query preprocessing, dimension, metric, normalization, limits, and
adapter version. Query embedding and the active P1 generation must switch together. The renderer
must output the same canonical string for every conventional model. If a model requires an
instruction prefix, represent it explicitly in the embedder generation/effective input audit—do
not let an adapter silently prepend text. Batching may vary by provider capability without changing
canonical input or policy mode.

## 3. Missing or under-specified items that block binding design

1. **A total v1 policy.** Exact precedence, normalization, escaping, nulls, length unit,
   deterministic truncation, whole-input overflow behavior, and typed empty/whitespace handling
   are missing.
2. **The D56/A3 amendment.** Current content-only vector reuse, moved chunks, duplicate occurrences,
   volatile ordinals, metadata-only changes, and exact-input reuse are not reconciled.
3. **A typed location schema.** Field types, schema version, provenance, stable versus display
   values, source shape, namespace, cardinality, and which fields may be embedded, filtered,
   oriented, or grounded must be closed.
4. **Connector contracts.** There is no Slack/message sidecar or block-to-message mapping and no
   rule for channel/thread/user/time mutation, rename, deletion, copy, or export aggregation.
5. **Grounding semantics.** H9 needs a closed structured grounding union, new source-kind/provenance
   records, an E2 generation bump, and tests proving summaries/model-derived fields remain
   non-quotable.
6. **Work identity and finalization.** Exact `processing_state` targets, component versions, batch
   manifests, co-claim semantics, attempt ownership, cost-ledger call keys, poison splitting,
   empty-document completion, stale-representation cancellation, and the last-child barrier are
   unspecified.
7. **P1/PG consistency and generation cutover.** Write order, retry repair, old/new vector
   coexistence, query generation filters, active pointer swap, rollback, orphan cleanup, dimension
   change, and index rebuild are required.
8. **Retrieval filter contract.** The search port/recipes need typed fields and operators. The
   design must state when filters are automatically available versus caller-supplied; otherwise
   “body-only + scalars” is not a retrieval behavior.
9. **P1 scalar cost plan.** Schema evolution, backfill, index type per field, selectivity/cardinality
   measurements, null behavior, timestamp ranges, and maintenance cadence are missing. Current
   `build_search_indexes` does not even build chunk-table scalar/vector indexes.
10. **Privacy, retention, and tenancy.** Location snapshots and P1 copies must remain deployment
    scoped under D68, use namespaced connector IDs, follow D74 hard-forget, and define treatment of
    private channel/user names, mutable identities, and metadata correction. Embedding a name makes
    its removal a vector/text purge, not just a PG update.
11. **Migration of existing data.** Existing LLM `context_prefix` rows and P1 text need an explicit
    legacy policy stamp, backfill/dual-read strategy, readiness behavior during migration, and an
    eventual column retirement plan. Lance column additions need a real dataset migration.
12. **Embedder-generation contract.** Resolved model revision, query/document preprocessing,
    dimension, metric, normalization, input limits, provider aliases, and atomic query-index switch
    are absent.
13. **Failure taxonomy.** Invalid UTF-8/control characters, header field overflow, oversized atomic
    bodies, provider partial/count/dimension mismatch, one poison text, rate limiting, timeout after
    provider billing, P1 success plus PG failure, and deleted/repacked chunks need typed outcomes.
14. **Evaluation gates.** “Measure later” is insufficient for binding the Slack default and numeric
    knobs. Predeclare metrics and non-inferiority margins for passage recall/MRR/nDCG, filtered and
    unfiltered metadata queries, duplicate/elliptical short messages, long chats and papers,
    multilingual corpora, claims/grounding quality, edit-local reuse, provider cost/latency, P1
    storage/index cost, crash-resume/no-rebill bounds, and hard-forget. Run at least two conventional
    embedders so v1 is not accidentally tuned to Qwen alone.
15. **Observability.** Expose counts by policy mode/source shape, header/body length ratio, render
    failure, vector reuse reason, re-embed fan-out, batch split/retry, scalar-filter selectivity,
    and readiness lag. Without these, policy drift and Slack regressions will be hard to diagnose.

## 4. Ranked recommendations to the author

### 4.1 Must change before this can become binding design

1. Replace content-only embedding reuse with exact `embedding_text_hash + embedder_generation`
   reuse; amend D56/A3 and remove global `ordinal/N` from the default embedded header.
2. Define the typed immutable location/source-shape contract, including connector provenance and a
   separate E2 groundable subset. Do not use an ungoverned JSON bag as both orientation and
   evidence.
3. Specify the actual durable graph: chunk work identity, batching/co-claim or batch manifest,
   component versions, cross-store commit order, poison isolation, representation finalizer, and
   generation-safe readiness/cutover.
4. Make the v1 policy total and model-independent. Freeze normalization, escaping, precedence,
   length counter, caps, and re-render/re-embed triggers in an immutable policy artifact.
5. Keep the one-message Slack default unresolved until evaluation proves body-only plus real query
   filters is non-inferior. The long export compact-header direction may proceed.
6. Define a minimal typed P1 scalar/query surface and its Lance migration/index/privacy costs;
   scalars that callers cannot filter on do not satisfy the retrieval contract.
7. Add migration and acceptance plans for legacy prefixes, P1 generations, two conventional
   embedders, short messages, fault injection, reuse locality, and D74 purge.

### 4.2 Should change

1. Keep the normative policy in E1 §5.x and make retrieval, E2, orchestration, schema, D56, D63,
   and D79 point to it; avoid a standalone policy document unless the contract no longer fits.
2. Use an explicit chunk-embedding-generation record and generation-keyed/separate P1 rows rather
   than overwriting one `embedding_ref` and one Lance row per chunk during migrations.
3. Keep exact embedding text in P1 and only its hash/stamps in PG; separate body/display text from
   embedding input for future lexical and hydration consumers.
4. Use stable opaque connector refs in P1 and reconstruct mutable display names through Postgres/P3.
5. Make provider batch limits capability-driven and add deterministic split-to-poison behavior.
6. Record policy-mode/reuse/filter metrics from day one so later knob changes have evidence.

### 4.3 Fine as-is / spike later

1. Conventional-only `texts -> vectors` as the product boundary.
2. No E1 LLM call for default location rendering.
3. Conditional rather than always-on headers as the architectural shape.
4. D79 summaries remaining orientation-only and absent from default embedding text and grounding.
5. Starting numeric values for `T_short`, `H_max`, alpha, and B may remain spike inputs, provided
   none is promoted as a default before the declared evaluation and each frozen value versions the
   affected artifact.

## 5. Executive verdict

1. **Ship this as design direction, not yet as binding design.**
2. The deterministic conventional-input renderer is the right replacement for per-chunk LLM prose.
3. Conditional headers and summary exclusion are sound; the proposed Slack atom default is unproven.
4. The current draft has a binding conflict with D56/A3: current-location text cannot reuse vectors by body hash alone.
5. Resolve that with exact-input-hash reuse and avoid volatile global position in embedded text.
6. Bind a typed connector/location/grounding contract; scalars without query filters are not a solution.
7. Make provider work chunk-durable, batches coalesced, and P1 generations atomically cut over.
8. This needs a substantial contract amendment across E1/D56/D63/E2/orchestration/retrieval/schema, not a conceptual rewrite.
9. No fatal product flaw remains once those items and the short-message evaluation gate are closed.
