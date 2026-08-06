# Implementation review: observation rank cache, P1 checkpoints, deterministic labels

**Reviewer:** `codex-sol`  
**Date:** 2026-08-06  
**Branch:** `feat/obs-cache-checkpoint-deterministic-labels` (`87ab3a42`)  
**Compared with:** `origin/main` (`73c6402a`)  
**Verdict: Request changes**

## Summary

The branch has the right broad shape: the SQL observation block remains exhaustive; the
rank cache hashes the exact embedded NEW text; only `_insert_new` aliases a NEW vector to
an observation id; cache writes validate cardinality, dimensions, finiteness, and norm;
relation label stamping clears the embedding ref; the ref encodes the requested embed
generation; relation labels are deterministic; and claim/fact embeddings are written and
stamped in batches with Lance-before-Postgres ordering.

The implementation does not yet satisfy the designs or the dual-review P0s. In particular,
fact search has no current-generation readiness authority, embed-only configuration changes
do not schedule a new pass, Phase E stamps are not conditional on the label generation they
embedded, and a cold rank request larger than the LRU capacity fails after paying to embed it.
These are correctness/recovery gaps, not nits, so I did not apply fixes.

## P0 — blocking

### 1. Fact search still serves stale or mixed embedding generations

`LabelFactsHandler` computes `embed_generation`, but it is stored only inside the opaque PG
ref (`workers/p1.py:189-190`, `fact_catalog.py:547-577`). `P1FactRow` and the Lance facts
payload carry no generation or label-input identity (`model/chunks.py:232-242`,
`adapters/selfhost/lance.py:230-246`). Both fact search methods filter only by deployment and
kind (`lance.py:425-447`, `647-674`), while PG hydration checks fact liveness but not
`fact_label_embedding_ref` / `obs_label_embedding_ref`
(`surfaces/query_engine.py:3019-3054`, `3254-3266`).

Consequently, clearing the PG ref during Phase L does not make the old Lance row unready. A
model or label-generation rotation leaves old rows searchable beside new rows, and the query
embedder can search an incompatible vector space. A crash after Lance upsert but before the
PG stamp is also publicly visible under a readiness model that claims the stamp matters.

Choose and implement the design's readiness authority: preferably carry the complete embed
generation in each Lance fact row and filter reads to it, or gate every nomination through PG
current-generation readiness with sufficient overfetch/replenishment.

### 2. An embed-generation-only change does not cause existing documents to run Phase E

The selector would re-embed if the handler ran, but the durable work identity for
`label_relation` remains only `FACT_LABEL_VERSION`: reconcile enqueues that value
(`workers/reconcile.py:196-211`), and profile readiness expects that same value
(`profiles/selfhost.py:639-663`). `processing_state` deduplicates by target, stage, and
component version. Changing only `REMEMBERSTACK_P1_EMBEDDING_MODEL` therefore leaves every
previous `label_relation` row succeeded and does not invoke the new selector. Readiness also
continues to accept the old stage row; model bindings are reported, not compared
(`spine/readiness.py:68-105`, `136-143`). The same problem remains for `embed_claim`, whose
work component version is independent of its embedding generation.

This violates the acceptance criterion that an embed-generation bump re-embeds without
re-labeling. The embed generation must participate in the scheduled/rebuild work identity or
there must be a concrete generation backfill mechanism that invokes the selectors.

### 3. Phase E stamps are not CAS-bound to the label that was embedded

`_STAMP_FACT_EMBEDDING` checks only `relation_id` and whether the target ref differs
(`fact_catalog.py:567-577`). It does not require
`fact_label_version = :label_generation`, deployment scope, or an exact label/input hash;
`record_fact_embedding` does not receive those values and ignores rowcount
(`fact_catalog.py:260-272`). The observation stamp similarly permits last-writer-wins across
generations (`fact_catalog.py:601-614`).

If the advisory-lock session is lost, an old and new worker overlap during a rolling deploy,
or label input changes between selection and stamp, an old Lance vector can overwrite the row
and then be advertised against the newer PG label. The full-pass advisory lock reduces the
ordinary race but the design explicitly requires row-level CAS as the final defense. Phase E
must stamp only when the current label generation/input still matches the item selected and
embedded, assert the affected row count, and leave mismatches unready for reconciliation.

### 4. A cold hub larger than the cache bound fails after embedding

`resolve_rank_vectors` fills every miss into the bounded LRU, then re-reads every miss from
the cache (`rank_embed_cache.py:150-160`). `_put_unlocked` evicts immediately at the bound
(`196-203`). When the number of cold misses exceeds `max_entries`, early vectors needed by
the same resolve have already been evicted, so the method raises `RuntimeError` instead of
returning the exhaustive candidate set.

This was reproduced with `max_entries=2` and a rank request containing NEW plus two open
items: all three texts were embedded, then resolution failed with `rank embed cache miss after
fill`. The default has the same failure at more than 8,192 cold keys. Vectors needed by the
active resolve must be retained independently of the bounded reusable LRU.

### 5. Embedder identity is still a model-name string, not the required generation

The rank cache generation is only `obs-rank-embed-v1|{model}`
(`rank_embed_cache.py:34-36`); the fact generation is
`FACT_LABEL_VERSION+embedding_model` (`workers/p1.py:189-190`); claim stamps still use the
bare model (`workers/p1.py:122-125`, `157-160`). These omit stored dimension/truncation,
input/distance policy, provider/adapter generation, and other parameters required by D63 and
the revised cache design. The rank key also omits the design's `deployment_id`.

A provider alias or dimension/input-policy change under the same model string is therefore
treated as the same cache/readiness generation. Resolve and persist the complete immutable
embedder generation and use it consistently in cache keys, PG selectors/refs, Lance rows,
query filtering, and scheduled work.

### 6. The cache is lock-protected but has no single-flight for concurrent misses

The cache checks entries while holding `_lock`, releases it, calls the provider, and later
reacquires it (`rank_embed_cache.py:124-159`, `163-194`). Two callers missing the same key can
both leave the first critical section and both issue billable embedding calls. This fails the
design's explicit single-flight requirement and the intended “open texts embedded once”
property under concurrent handler use. A thread-safe `OrderedDict` is not single-flight; the
implementation needs per-key in-flight ownership/waiting and failure propagation.

## P1 — required before approval

### 7. Provider limits are count-only, and validation can poison cache state

Both rank and P1 batching use fixed/configured text counts (`64`, at most `1024`) but have no
active-provider token budget. Observation statements and fact/claim labels have no maximum
length, so one batch—or one text—can still exceed the provider cap. This falls short of the
design's text-count **and token-cap** chunking contract.

Also, `_embed_misses` sets `_dims` from the first returned vector before validating it
(`rank_embed_cache.py:187-194`). One empty first response sets `_dims = 0`, raises, and causes
all later valid retries to fail dimension validation. Validate the entire response before
mutating generation state or inserting any entries.

### 8. The advertised LRU memory bound and observability are incomplete

The `~128MB` comment assumes packed fp32 storage, but the cache retains Python tuples of Python
floats. At 4,096 dimensions, 8,192 entries are roughly 1 GiB before `OrderedDict`, key, and
allocator overhead. That is a risky default for each long-lived E3 worker. The implementation
also exposes mutable hit/miss counters only; it has no cache-size metric or eviction counter as
required by the design. Use a measured byte-aware bound or packed representation and export
size/eviction telemetry.

### 9. Deterministic-label cutover has no conformance or retrieval evidence

The code removes the LLM from the critical path, which is deterministic and operationally
desirable, but the only behavioral assertion changed is the `works_for` punctuation in two
existing integration tests. There are no unit tests across all governed predicates, direction,
unknown/`other:*` fallback, or byte stability. The fallback comment says “raw slug,” while the
implementation replaces underscores and leaves the `other:` prefix in rendered text
(`workers/p1.py:46-74`). More importantly, neither dual review allowed the LLM-to-S4 binding
change without the frozen retrieval non-inferiority/fidelity eval. Add the conformance suite
and provide or reference that eval result before accepting the production cutover.

### 10. Tests do not cover the new checkpoint state machine or its failure boundaries

The PR adds only two cache happy-path tests. It adds no checkpoint-specific test file. Existing
P1 integration coverage checks a final non-null ref, but its “zero new calls” replay assertion
counts generated LLM prompts even though this handler no longer generates any
(`tests/workers/test_e3_chain.py:669-692`); it would not detect duplicate re-embedding.

Missing required coverage includes:

- evidence collapse with differently worded NEW and no alias under the existing id;
- generation/dimension change, provider chunk and token limits, positional vector pairing,
  malformed count/dimension/NaN/zero output, LRU eviction, and concurrent same-key misses;
- kill/restart after a label checkpoint and after each successful embed batch;
- label-generation bump forcing re-embed and embed-only bump avoiding label production;
- two workers racing the same relation, lost lock, failed CAS, and rowcount assertions;
- stale/orphan Lance rows at top-k under the chosen readiness authority;
- batching and per-batch stamping for both claims and facts;
- deterministic-label predicate/template conformance and retrieval quality.

## P2 — nits / follow-up

- The source docstrings link to the two design files, but neither design nor either prior
  review exists on this branch. They were read from
  `origin/design/obs-embed-cache-checkpoint-p1-analysis`. Merge or otherwise publish the
  referenced designs with the implementation so repository links are not broken.
- `_last_rank_new_vector` / `_last_rank_new_statement` are mutable adjudicator-wide scratch
  state (`observation_adjudication.py:95-96`, `566-568`, `628-636`). They can lose a safe
  write-through or use a stale same-text vector under concurrent calls. Return an explicit
  per-rank token/vector to the insertion path instead of using shared “last call” state.

## Verification performed

- `git diff --check origin/main...HEAD` — passed.
- Ruff on all changed Python implementation/test files — passed.
- Pyright on the four key implementation files — passed (0 errors/warnings).
- `pytest` for rank-cache, observation-adjudication, and E3-chain files — `2 passed, 16
  skipped`; all database-backed tests were skipped in this environment.
- New rank-cache tests with coverage — `2 passed`; 83% line/branch coverage, with eviction,
  malformed vectors, metering, and error paths uncovered.
- Direct cold-LRU reproduction — failed as described in P0.4.

