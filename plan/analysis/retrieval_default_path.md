# Retrieval default path: closing the designed P1 channel gap

**Date:** 2026-07-30

**Status:** non-binding implementation analysis

**Question:** how should the shipped query path implement the semantic,
lexical, and source-text channels already required by D8/D9/D48–D50, without
adding a benchmark-only shortcut or weakening evidence-grain honesty?

## Finding

The accepted retrieval design is not missing a retrieval architecture. The
implementation exposes only a narrow subset of it:

- `claims_verbatim` performs one semantic claim search;
- `claims_hybrid_rrf` performs that same deterministic semantic search twice,
  so reciprocal-rank fusion (RRF) cannot add candidates or change their order;
- P1 writes chunk text and vectors, but `P1SearchPort` has no chunk nomination
  or hydration operation; and
- callers must choose among several small recipes instead of having one
  ordinary, high-recall evidence entry point.

The gap matters outside LoCoMo. Dense retrieval misses exact names, identifiers,
dates, and quoted phrases. Claim extraction can also omit useful source text.
The designed lexical channel and chunk fallback cover those different failure
classes.

## Selected implementation

### Lexical search stays in the P1 adapter

Claim and chunk bodies already live in the Lance P1 tables; Postgres holds
their authoritative metadata and offsets, not the bodies. Duplicating all text
into Postgres solely for full-text search would create a second projection,
another rebuild path, and an avoidable large-table migration.

The self-host adapter therefore uses Lance's native full-text/BM25 query over
the existing `text` column. The first P1 write bootstraps each table's explicit
FTS index, and the first lexical read does the same for an upgraded store that
predates this feature. Because Lance searches appended rows outside the built
index, ordinary writes run incremental index optimization after 20 mutations
or 100,000 unindexed rows. Reads search the residual tail but never compact;
the next ordinary write or explicit bulk-load maintenance enforces the bound.
Chunk-body hydration bootstraps a B-tree over `chunk_id` before its ID read.
Ordinary and upgraded-store reads also bootstrap missing deployment filter
B-trees and the claim-current bitmap needed by prefiltered retrieval. Those
one-time index-creation writes and write-path optimization use bounded retries
for Lance commit conflicts on the shared API/worker volume. The tokenizer is
deliberately language-neutral:
punctuation/whitespace tokenization, case folding, and ASCII folding, without
English stemming or English stop-word removal. Language-specific stemming can
improve one language while silently damaging another; it needs a measured
multilingual policy rather than an English default.

Semantic and lexical searches are independent nominations. Hybrid recipes use
projection-only nomination steps so candidate-depth IDs are fused before any
content is hydrated; each fused result is then D48-confirmed exactly once.
Claim search
applies the deployment and testimony-current projection scalars before
ranking. Chunk search applies the deployment scalar, deliberately
over-nominates across immutable versions, and relies on the D48 Postgres hop
below to keep only the live source coordinate. RRF then fuses each pair of ID
lists and a final limit caps the reader-facing result.

### Chunk results are evidence, but not claims

A chunk is source text, not an atomic assertion and not an adjudicated fact.
The response envelope gains a distinct `chunks` payload inside the existing
evidence grain. It is never laundered into `EvidenceResult` or `FactResult`.

P1 may nominate a chunk, but D48 still requires the live spine to dispose:

1. Lance nominates chunk IDs.
2. Postgres confirms that each ID belongs to the lineage's current, ready
   version and current, ready representation.
3. P1 supplies the indexed text for only those confirmed IDs.
4. The query engine checks that the stored context prefix agrees with
   Postgres, separates it from the source body, preserves candidate order, and
   reports every failed confirmation as `dropped_by_hydration`.

The returned record carries document/version/representation IDs, source
offsets, section role, title/source kind, source/assertion time, the generated
context prefix, and the source chunk text. An agent can use it for recall and
still audit the exact source coordinate.

### One ordinary high-recall recipe

`question_context` is a registry recipe, not a private endpoint. Its frozen
chain runs:

1. semantic and lexical claim nomination → RRF → claim hydration;
2. semantic and lexical chunk nomination → RRF → chunk hydration; and
3. an evidence-combine operator that keeps the two payloads separately typed.

Candidate depth is larger than the returned depth, and both are bounded and
declared in the public schema. The complete raw envelope remains the audit
artifact. Compact reader rendering is a consumer/harness concern and is not
hidden inside the query engine.

The existing `claims_hybrid_rrf` is repaired to use independent channels and
kept for callers that specifically want atomic assertions. `claims_verbatim`
remains the cheaper semantic-only option.

## Rejected alternatives

- **Two semantic passes with different labels.** No independent evidence enters
  the candidate set; this is the current defect.
- **Cross-fusing claim and chunk UUIDs into one unlabeled ranking.** A UUID
  alone does not disclose its table or grain shape, so later hydration cannot
  remain type-safe. Claims and chunks are fused within their own channels and
  combined as separately typed evidence payloads.
- **Returning raw Lance rows directly.** That bypasses D48 and can serve an old
  document version after the spine has moved on.
- **Treating chunks as facts.** Source text is testimony/evidence; extraction
  and adjudication are what create fact-grain records.
- **Adding an LLM query planner or LoCoMo-specific rewrite.** D9 forbids an LLM
  in the engine query path, and benchmark-only capability would not improve the
  product.
- **Implementing entity boosts and historical testimony in the same change.**
  Entity scoring needs an inspectable query-entity/claim-entity contribution
  contract. Historical search needs transaction-time nomination semantics,
  not merely `current_only=False`. Both remain valid D9/D41 work but should be
  measured and reviewed on their own contracts.

## Validation required

- a lexical-only candidate must survive when semantic nomination misses it;
- a chunk omitted by claim extraction must be reachable through the public
  chunk and `question_context` paths;
- stale-version chunks must be dropped by Postgres confirmation;
- the context prefix must be disclosed separately from source text;
- recipe execution must equal the same hand-composed primitive chain;
- API, SDK, CLI, and MCP recipe surfaces must remain in registry lockstep;
- FTS must be available after the first ordinary P1 write, on the first read
  of an upgraded unindexed store, and after explicit maintenance;
- ordinary mutation traffic must fold indexed tails after at most 20 writes
  or 100,000 unindexed rows, while deployment/current filters and chunk ID
  hydration use scalar indexes; and
- the LoCoMo protocol identity must change because the public tool catalog and
  retrieval behavior changed.

## Independent review findings and dispositions

Claude Opus and Grok independently reviewed the complete working diff before
the pull request. Both agreed that the typed chunk envelope and Postgres
authority boundary were sound. Their material findings changed the
implementation:

- the original hybrid chain hydrated both candidate-depth channels and then
  hydrated the fused list again; projection-only `nominate_claims` and
  `nominate_chunks` steps now make confirmation happen once after fusion;
- the original FTS bootstrap left an indefinitely growing unindexed tail
  during ordinary ingestion; bounded incremental optimization and read-time
  upgrade repair now cover that lifecycle;
- the original chunk body lookup lacked a scalar `chunk_id` index; the adapter
  now bootstraps one on write, maintenance, or first upgraded-store read;
- the original equivalence proof omitted `chunks_hybrid_rrf` and
  `question_context`; the proof now asserts exact coverage of every canonical
  recipe, and a separate execution proof counts one claim and one chunk
  confirmation;
- raw primitive `k` values and internal channel strings were insufficiently
  defended; both now have explicit bounds/allow-lists; and
- blanket reader `exclude_defaults` could hide meaningful audit fields; the
  compact projection now removes only ranking bookkeeping, nulls, and empty
  containers; and
- the first bounded-maintenance implementation could compact synchronously on
  reads and raised retryable Lance commit conflicts under API/worker
  concurrency; compaction now stays on write/maintenance paths and both
  index creation and optimization retry only the conflicts Lance labels
  retryable.

Non-blocking measurement work remains: tune the `question_context` evidence
budget against real LoCoMo context lengths, separate claim/chunk recall
diagnostics from coarse session coverage, measure entity-aware ranking, and
decide whether boilerplate section roles need a default penalty or filter.

## Follow-up measurements

The retrieval evaluation should record exact-dialog and complete-evidence
recall at `k={10,20,30,50,100,200}` for semantic claims, lexical claims,
hybrid claims, semantic chunks, lexical chunks, and `question_context`.
Entity-aware ranking, wider entity-resolution tiers, historical testimony,
and any cross-encoder are adopted only when those measurements identify the
remaining miss class.
