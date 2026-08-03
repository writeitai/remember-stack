# Codex — D80 implementation review (post residual fixes)

**Verdict: Approve with nits**

Re-review of branch `code/d80-embedding-input-impl` after residual must-fix
pass (claimed fixed as of commit `f09a774+`). Scope: verify the five prior
blockers, re-check design fidelity for embed_chunk rules 1–6 and D80 §3–§7,
and reassess residual risk. Review only; no production `src/` edits.

---

## Claimed residual must-fixes — verification

| # | Claimed fix | Status | Evidence |
|---|---|---|---|
| 1 | Outage vs poison classification | **Fixed** | `_embed_batch_with_poison_split` re-raises when `_is_provider_outage` (`workers/e1.py:369–373`). Only `ProviderInvalidResponseError` is non-outage / size-1 poison (`:513–528`). `ProviderCallError`, timeouts, `OSError`/`ConnectionError`, and unknown exceptions fail closed as outage (retry). |
| 2 | Active generation filter on QueryEngine search + `chunk_texts` | **Fixed** | `QueryEngine` binds `_policy_generation = EMBEDDING_INPUT_POLICY_VERSION` and `_embedder_generation = embedding_model` (`surfaces/query_engine.py:158–162`). `_nominate_chunk_ids` passes both into semantic and BM25 search (`:720–732`). Hydration `chunk_texts` is generation-scoped (`:1558–1562`). Lance applies filters via `_chunk_search_where` and generation-aware limits (`adapters/selfhost/lance.py:319–324, 370–417, 772–788`). Profile wires `embedding_model` into `QueryEngine` (`profiles/selfhost.py:260–264`). |
| 3 | Stale e2_chain + retrieval_api assertions | **Fixed** | `test_e2_chain.py:413–423` asserts **absence** of retired `"state where this passage sits"` and presence of `LOCATION:` in extractor prompts. `test_retrieval_api.py:786–792` accepts deterministic header or null; forbids canned free-form prose in header; checks header not embedded in `chunk_text`. Hydration skew test mutates `section_role` (real D48 drop path) and nulls `location_header`/`context_prefix` without treating null header as drop (`:798–852`). |
| 4 | D66 API docs | **Fixed** (one residual wording nit) | `website/src/app/docs/reference/api/page.mdx:183–185` now says optional **deterministic** `context_prefix` (location header under embedding-input policy) separately from **body-only** `chunk_text`. The later “verbatim source passages in `chunks[]`” line (`:194`) is slightly imprecise under whitespace normalization (S-doc below). |
| 5 | Outage classifier unit test | **Fixed** | `test_provider_outage_classifier_distinguishes_poison` in `src/tests/core/test_d80_recovery_and_hydration.py:219–229` covers invalid-response vs call-error / timeout / connection / unknown. |

**Conclusion on prior blockers:** all five residual must-fixes are present in
code and tests. No remaining **blocking** contract violations found in this
pass.

---

## Must-fix findings

*None.* Prior M1–M5 are closed.

---

## Should-fix findings

### S1 — E1 write embedder vs QueryEngine active pointer can desync

- **Write path:** `embedder_generation = E1Settings.embedding_model`
  (`REMEMBERSTACK_E1_…`, `e1.py:185`).
- **Query path:** `QueryEngine(embedding_model=P1Settings.embedding_model)`
  (`REMEMBERSTACK_P1_…`, `selfhost.py:250–264`).

Defaults match (`qwen/qwen3-embedding-8b`). If only one env is set, ANN/BM25
and hydration filter on a generation that was never written → empty chunk
evidence with no loud failure. Prefer one shared settings key (or wire QueryEngine
from the same object E1 uses).

### S2 — Production OpenRouter embed path never raises poison-class errors

`_is_provider_outage` only treats `ProviderInvalidResponseError` as chunk
poison. OpenRouter `embed` raises `OpenRouterProviderError` (`ProviderCallError`)
for HTTP failures **and** unusable embedding bodies
(`adapters/openrouter.py:267–270, 275–278`). So size-1 `skip:poison_chunk` is
effectively unreachable with the production adapter.

This is **fail-closed** (correct direction vs the old all-poison readiness
bug): outages and unusable bodies retry / DLQ rather than false embed-ready.
But rule 4’s typed poison path is unexercised in production. Map
schema/body-shape failures on embed to `ProviderInvalidResponseError` (or a
dedicated subclass) when the failure is response-content, not transport.

### S3 — No handler-level poison-split / outage re-raise test

Classifier unit test lands (M5). Still missing:

- batch of N → one invalid-response size-1 skip + siblings committed;
- total outage on batch → exception propagates, **no** `skip:poison_chunk`
  stamps, readiness does not close.

Without that, a future `except Exception: poison_skips.append` regression is
easy.

### S4 — E2 Claimify instruction still says “CONTEXT PREFIX”

`workers/e2.py:160` still lists “stored CONTEXT PREFIX” as a quotable bundle
source. Bundle members are typed `LOCATION:` pairs only. Align prompt with
§3.3 (same as prior S2).

### S5 — Missing `provenance` still admitted into grounding union

`_location_grounding_pairs` (`e2.py:780–783`) skips only when provenance is
present and disallowed; `None` passes. Prefer reject-missing for closed records.

### S6 — Connector packaging never loads from spine (prior S1)

`ChunkSource` defaults `source_shape="document"` and message refs `None`
(`model/chunks.py:52–57`). `_SELECT_CHUNK_SOURCE` still selects only document
title/source_kind (`chunk_catalog.py:162–172`); no packaging columns in spine.
Message-atom compact headers remain unit-test-only.

### S7 — `normalize_body` collapses all whitespace

`embedding_input_policy.py:104–106` turns lists/code/paragraphs into one line
for both embed input and P1 BM25/evidence text. Policy-owned, but not pinned
in the version artifact description. Prefer preserving newlines or document in
`EMBEDDING_INPUT_POLICY_VERSION` text.

### S8 — Invented `"untitled"` coordinate heuristic

`_has_useful_coordinates` (`embedding_input_policy.py:278`) treats title
`"untitled"` as absent. Not in §4.3; freeze in policy artifact or delete.

### S9 — Migration dead code

`p8_01_0021_d80_embedding_input.py:30–34` still binds `name` / `del name`.
Harmless; remove.

### S10 — Embedder generation is still bare model id

`embedder_generation = embedding_model` only (`e1.py:185`). Design §4.5/§8
wants model + dimension + metric + provider params. Silent param drift can
reuse wrong vectors. Tracked follow-on is acceptable if explicit.

### S11 — `test_e1_chain` still does not assert P1 body-only text

Stamps and zero LLM prompts are asserted (`test_e1_chain.py:219–231`); Lance
`text == normalize(body)` while provider may embed `header + body` is not.
Add one projection assertion.

### S12 — Docs nit: “verbatim source passages”

`page.mdx:194` still says “verbatim source passages in `chunks[]`”. Under D80
the payload is normalized body-only (plus separate deterministic header).
Prefer “body-only source passages” for consistency with lines 183–185.

---

## Residual risks / deferred work

1. **Cutover pointer is code/settings, not readiness-gated.** Query always
   filters the active pair from the policy constant + embedder string. Bumping
   `EMBEDDING_INPUT_POLICY_VERSION` or the embed model instantly hides prior
   rows until re-embed completes. Dual-generation *storage* works; dual-generation
   *ops* (flip only when required rows exist; retirement of old rows) is still
   an operational follow-on, not implemented as a durable pointer store.
2. **E1/P1 settings split (S1)** — misconfiguration → silent empty chunk search.
3. **Poison path dead under OpenRouter (S2)** — safe, incomplete rule-4 coverage.
4. **Connector metadata contract (§3.2)** — product path for message atoms still
   blocked on spine + SELECT (S6).
5. **Recipe filters on new P1 scalars** — `source_kind` / `source_shape` written;
   no evidence recipes declare operators yet.
6. **Claims do not inherit message scalars** — correctly not implemented (§5.5).
7. **Handler fault-injection §10.4** — no kill-between-P1-and-PG automated proof
   at handler level (S3 adjacent).
8. **H_MAX≈48** — compact message headers still drop whole fields; measure and
   version.
9. **D74 purge** — confirm forget deletes all generation-keyed Lance rows and
   `location_facts_json` (not re-audited this pass).
10. **Search-filter unit proof** — dual-gen coexistence is tested for
    `chunk_vectors` / `match_chunk_embeddings`, not for
    `search_chunks` / `search_chunks_lexical` filtering out the inactive
    generation. Worth one Lance unit test.

---

## Design fidelity notes

### Prior residual must-fixes (this pass)

| Prior | Status after residual pass |
|---|---|
| M1 e2_chain + retrieval_api | **Fixed** |
| M2 total-outage → poison | **Fixed** (fail-closed classifier + re-raise) |
| M3 query active generation | **Fixed** (settings stamp + Lance prefilter) |
| M4 D66 API docs | **Fixed** (wording nit S12 remains) |
| M5 classifier test | **Fixed** (handler-level still open as S3) |

### Orchestration embed_chunk rules 1–6

| Rule | Verdict | Evidence |
|---|---|---|
| 1 Claiming row / pure prepare in job | ✅ | Single `EMBED_CHUNK` handler; prepare in-process |
| 2 Batching ≤ capability; no cross doc/rep/lane/gen | ✅ | `embed_batch_size` default 64; one representation per claim |
| 3 `call_key = embed_chunks:{first}:{count}` | ✅ | `e1.py:360–361` (`min` UUID as first id) |
| 4 Poison split | ✅ with S2 | Outages re-raise; only `ProviderInvalidResponseError` size-1 skips; OpenRouter rarely emits that type |
| 5 P1 then PG; retry without provider if triple+hash match | ✅ | `_commit_batch` order; `match_chunk_embeddings` recovery; re-stamp recovered pairs missing PG |
| 6 Readiness under active pair or typed skip | ✅ | Missing non-skip vectors raise; empty_body / poison_chunk closed skips |

### Policy §3–§7 highlights

| Contract | Verdict |
|---|---|
| No location LLM on default path | ✅ |
| Total pure `render_embedding_input` + version `e1-embed-input-v1:char` | ✅ |
| §4.3 ordered modes; step 4 without bare `"document"` shape | ✅ |
| No numeric `node_path` header fallback | ✅ |
| Whole-field header bound under `H_MAX` | ✅ |
| Empty body → typed skip | ✅ |
| P1 text = normalized body only | ✅ write; under-tested (S11) |
| Header separate on PG + API field | ✅ dual-write |
| LocationElement closed kinds; no summary | ✅ |
| Free-form header out of grounding union | ✅ |
| P1 key `(chunk_id, policy_generation, embedder_generation)` | ✅ |
| Query filters active pair | ✅ |
| Claims do not inherit message scalars | ✅ |
| E2 extractor generation bumped | ✅ |

---

## Test results

**Command requested:**

```bash
uv run pytest \
  src/tests/core/test_embedding_input_policy.py \
  src/tests/core/test_d80_recovery_and_hydration.py \
  src/tests/workers/test_d79_summary_consumption.py \
  src/tests/spine/test_migrations.py::test_revision_graph_is_one_linear_structural_chain \
  src/tests/adapters/test_lance_retrieval.py -q
```

**This review agent has no shell execution tool**, so pytest was **not**
executed here. Static review of those modules shows:

- Policy / recovery / outage classifier tests are self-contained and import the
  live `_is_provider_outage` / Lance / hydration helpers.
- `p8_01_0021` remains on the linear migration chain (file present;
  `down_revision = p5_07_0020`).
- No remaining imports of removed `_prefix_prompt` in the d79 suite.
- Integration assertion rewrites (e2_chain, retrieval_api) match D80 product
  behavior on paper.

**Operator action before merge:** run the focused command above (and, with
Postgres fixtures, `test_e2_chain` + `test_retrieval_api` smoke) in the branch
environment. Expect green if env matches CI.

---

## Executive summary

1. **Verdict: Approve with nits** — all five residual must-fixes (outage vs
   poison, active generation on search/hydration, stale integration assertions,
   D66 API wording, classifier unit test) are present and correct in code.
2. Pure-policy core, generation-keyed P1, P1→PG recovery, typed E2 location
   membership, and legacy hydration strip remain solid.
3. Poison classification is now fail-closed; the remaining gap is that production
   OpenRouter embed errors never become typed poison (S2) and there is no
   handler-level fault-injection test (S3).
4. Query cutover filtering is real via settings stamp; ops still lack a
   readiness-gated pointer flip and E1/P1 embedder env can desync (S1).
5. Non-blocking follow-ups: connector packaging spine path (S6), E2 prompt
   “CONTEXT PREFIX” (S4), missing provenance reject (S5), body-only P1 assert
   (S11), docs “verbatim” nit (S12), bare embedder identity (S10).
6. **Approve for merge** of the D80 implementation on this branch once the
   focused pytest command is green locally/CI. Treat S1–S12 as tracked nits /
   follow-ons, not merge blockers — unless an operator plans to diverge
   `REMEMBERSTACK_E1_EMBEDDING_MODEL` from `REMEMBERSTACK_P1_EMBEDDING_MODEL`,
   in which case S1 should be fixed first.
