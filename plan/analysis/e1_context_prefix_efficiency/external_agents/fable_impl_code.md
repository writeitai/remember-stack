# Fable — D80 embedding-input implementation review (post residual fixes)

**Reviewer:** Fable  
**Branch:** `code/d80-embedding-input-impl` (residual fixes through commit `f09a774` and earlier)  
**Verdict:** Approve with nits

**Scope:** Re-review after claimed residual must-fixes against
`plan/designs/e1_embedding_input_policy.md` and
`plan/designs/orchestration_design.md` § embed_chunk durability (D80 minimum
contract). Focus: poison vs outage (M1), query active generation pointer (M2),
`test_e2_chain` + `test_retrieval_api` (M3), website API wording (M4). Broader
D80 fidelity rechecked where residual work could regress it. Prior reviews:
this file (previous **Request changes** pass) and `codex_impl_code.md`.

**Method:** read `workers/e1.py` poison path, `surfaces/query_engine.py`
nomination + hydrate, `adapters/selfhost/lance.py` search/`chunk_texts`,
`ports/p1_index.py`, the two integration test files, `website/.../api/page.mdx`,
OpenRouter embed error taxonomy, pure policy + d80 unit tests, E2 grounding,
chunk catalog load path. Review only; no production edits.

---

## 1. Verdict

**Approve with nits.**

The four residual blockers from the prior re-review are **closed in code**:

| Residual | Prior status | Now |
|---|---|---|
| **M1** Poison split stamps total outages as closed `poison_chunk` readiness | ❌ blocking | ✅ Outages re-raise; only `ProviderInvalidResponseError` is size-1 poison |
| **M2** Query never filters active `(policy, embedder)` | ❌ blocking | ✅ `QueryEngine` passes both on semantic, BM25, and `chunk_texts` |
| **M3** DB integration tests still assert prefix-LLM product | ❌ blocking | ✅ Rewritten for D80 (no location-LLM; deterministic header / body-only) |
| **M4** D66 API docs still claim generated prefix + verbatim body | ❌ process | ✅ Deterministic `context_prefix` / body-only `chunk_text` wording |

The D80 conceptual core that already landed in the previous pass remains intact:
pure policy, body-only P1 text, generation-keyed Lance rows, per-batch P1→PG,
typed location elements at E2, §4.3 mode gate, whole-field headers, legacy
hydration strip, extractor version bump, `p8_01_0021` in the revision graph.

What remains is **should-fix / coverage / polish**, not another inverted
durability or cutover-search hole. Strict bar for full **Approve** (no nits)
would still want: handler-level poison/outage proofs, production embed adapter
ability to *signal* content poison (so rule-4 isolation is reachable, not only
outage-safe), E2 prompt/docstring cleanup, and spine packaging fields for
message shapes. None of those re-open the dangerous M1 failure mode.

---

## 2. Residual must-fix re-verification

### M1 — Outage vs poison ✅ closed

**Design (orchestration rule 4):** split only on non-outage batch failure; size-1
content poison is typed skip for that chunk; a provider-wide failure must not
convert every sibling into a successful closed skip and advance readiness.

**Impl (`workers/e1.py`):**

```369:376:src/rememberstack/workers/e1.py
        except Exception as exc:
            # Rule 4: split only on non-outage failures; total outages re-raise
            # so the stage retries instead of stamping every chunk as poison.
            if _is_provider_outage(exc=exc):
                raise
            if len(batch) == 1:
                poison_skips.append(batch[0])
                return
```

```513:529:src/rememberstack/workers/e1.py
def _is_provider_outage(*, exc: BaseException) -> bool:
    """Whether a provider failure is a total outage (retry) vs content poison.

    Only ``ProviderInvalidResponseError`` is treated as chunk-attributable
    poison eligible for size-1 typed skip. Transport failures, generic
    ``ProviderCallError``, timeouts, and OS-level connection errors re-raise
    so readiness never closes on an empty all-poison set.
    """
    if isinstance(exc, ProviderInvalidResponseError):
        return False
    if isinstance(exc, ProviderCallError):
        return True
    if isinstance(exc, (TimeoutError, OSError, ConnectionError)):
        return True
    # Unknown exceptions: fail closed as outage (retry), not silent skip.
    return True
```

**Why this is correct:**

1. `ProviderInvalidResponseError` subclasses `ProviderCallError`
   (`model/model_provider.py`), but the classifier checks the **narrower** type
   first — order is right.
2. Unknown exceptions default to **outage (retry)**, not silent skip — fail
   closed in the safe direction relative to the prior bug.
3. OpenRouter embed path today raises `OpenRouterProviderError` /
   `ProviderCallError` for HTTP ≥400 and unusable embedding bodies
   (`adapters/openrouter.py:244–279`) — those re-raise as outage. A downed or
   503/timeout embedder can no longer “succeed” with zero vectors.

**Unit proof:** `test_provider_outage_classifier_distinguishes_poison` in
`src/tests/core/test_d80_recovery_and_hydration.py` covers InvalidResponse vs
`ProviderCallError` / `TimeoutError` / `ConnectionError` / unknown.

**Residual (nit, not re-block M1):**

- **R1 — Production poison isolation is mostly unreachable.** OpenRouter
  `embed` never raises `ProviderInvalidResponseError` (only generate does).
  Every embed failure is classified as outage → re-raise → no halve, no
  size-1 skip. Safer than false-ready; weaker than full rule-4 isolation when
  one oversized/bad text poisons a multi-chunk batch (whole job retries / DLQ
  rather than skipping the single chunk). Fix on the adapter: raise
  `ProviderInvalidResponseError` for unusable embedding payloads / clear
  client-content failures; keep 5xx/transport as outage.
- **R2 — No handler-level fault injection.** Classifier unit tests exist; no
  `EmbedChunksHandler` proof that (a) outage re-raises with zero
  `skip:poison_chunk` stamps, (b) size-1 InvalidResponse stamps one skip and
  keeps sibling vectors. Keep as should-fix coverage.

### M2 — Active generation at query / hydrate ✅ closed

**Design §4.5 / §5.2:** dual-generation rows coexist; search and projection use
the active `(policy_generation, embedder_generation)` pointer.

**Impl:**

- `QueryEngine.__init__` binds  
  `_policy_generation = EMBEDDING_INPUT_POLICY_VERSION`,  
  `_embedder_generation = embedding_model`  
  (`query_engine.py:158–162`).
- `_nominate_chunk_ids` passes both into `search_chunks` and
  `search_chunks_lexical` (`query_engine.py:720–732`).
- Hydration `chunk_texts` passes both (`query_engine.py:1558–1562`).
- Lance applies filters when columns exist
  (`lance.py:319–324`, `355–360`, `388–393`, `_chunk_search_where` `772–788`).
- Self-host profile wires `QueryEngine(..., embedding_model=p1_settings.embedding_model)`
  (`profiles/selfhost.py:260–264`), matching the default E1 embed model id.

Prior S12 (unscoped `chunk_texts` first-row-wins under multi-gen) is **closed
for the production query surface** because QueryEngine always scopes. Ports
still accept `None` for fakes/tests — fine.

**Residual (nits):**

- **R3 — Active pointer is process config, not a flipable deployment store.**
  Design cutover “flip only when required records exist” is not a spine table;
  library single-tenant binds constants at construction. Acceptable for the
  residual M2 bar (“pass active pair on every search/hydrate”); full cutover
  orchestration remains a product gap, not a silent mix of generations on the
  default path.
- **R4 — Write vs query embedder settings are dual-sourced.** Embed writes use
  `E1Settings` (`REMEMBERSTACK_E1_…`); QueryEngine uses `P1Settings`
  (`REMEMBERSTACK_P1_…`). Defaults match (`qwen/qwen3-embedding-8b`). Divergent
  env config would scope search to a generation with no rows. Prefer one
  shared chunk-embed generation binding in the profile.
- **R5 — No search-path dual-gen unit test.** Lance dual-gen coexistence is
  proven for `chunk_vectors` / `match_chunk_embeddings`, not for
  `search_chunks` / `chunk_texts` prefilters under two policy generations.

### M3 — Integration tests rewritten for D80 ✅ closed

**`test_e2_chain.py` (prior red assertion → D80):**

```413:424:src/tests/workers/test_e2_chain.py
    # D80: no location-LLM prefix stage; orientation still reaches E2 extraction.
    assert not any(
        "state where this passage sits" in prompt
        for prompt in rig.provider.generated_prompts
    )
    assert any(
        "Selection stage of a claim extractor" in prompt
        and "SECTION SUMMARIES (orientation only; never quote as source):" in prompt
        and summary_line in prompt
        and "LOCATION:" in prompt
        for prompt in rig.provider.generated_prompts
    )
```

**`test_retrieval_api.py` (prior LLM prefix equality → D80):**

```786:792:src/tests/surfaces/test_retrieval_api.py
    # D80: context_prefix is the deterministic location header (or null for
    # body_only); P1 / chunk_text is body-only and never embeds the header.
    assert chunk["context_prefix"] is None or isinstance(chunk["context_prefix"], str)
    if chunk["context_prefix"]:
        assert "Sits in the staffing note." not in chunk["context_prefix"]
        assert chunk["context_prefix"] not in chunk["chunk_text"]
    assert "Alice Novak works for Acme as an engineer." in chunk["chunk_text"]
```

Hydration honesty tests now use **section_role disagreement** and **null
header still body_only-valid**, not free-form prefix mismatch gates that D80
removed (`test_retrieval_api.py:798–852`). Current-version pointer drop for D48
stale source remains (`:869–896`).

**Residual (nit):**

- **R6 — Dead canned `ContextPrefix` fixtures** remain in
  `test_retrieval_api.py:98–99`, `test_e2_chain.py:188`, and likely sibling
  chain fixtures. Harmless (D80 path never calls generate for that schema) but
  confuses readers; delete when convenient.
- DB-backed suites were not re-executed in this review environment when
  `REMEMBERSTACK_DATABASE_URL` is absent (they skip). Assertions are
  structurally green; run them in CI/with DB before merge confidence.

### M4 — D66 website API wording ✅ closed

```183:186:website/src/app/docs/reference/api/page.mdx
verdict. Chunk results disclose an optional deterministic `context_prefix`
(location header under the embedding-input policy) separately from body-only
`chunk_text` and carry the current document, version,
representation, offsets, and source timestamps confirmed by the live spine.
```

Matches the dual-write alias (`context_prefix` ← `location_header`) and P1
body-only product.

**Residual (nit):**

- **R7 — Nearby recipe prose still says “verbatim source passages in
  `chunks[]`”** (`page.mdx:194`). Under D80, hydrated `chunk_text` is
  **normalized body** (whitespace collapsed), not necessarily a raw
  `document.md` slice. Prefer “body-only source passage text” or note
  normalization.

---

## 3. Design fidelity checklist (current)

| # | Contract | Verdict | Evidence |
|---|---|---|---|
| F1 | No location LLM on default path | ✅ | `EmbedChunksHandler` only `embed` (`e1.py:363–367`); E1 chain asserts zero generate prompts |
| F2 | Total pure policy, ordered first-match, empty-body skip | ✅ | `render_embedding_input` / `_decide_mode` |
| F3 | P1 text = body only; header on PG / API separate | ✅ | `P1ChunkRow.text=rendered.body`; API dual fields |
| F4 | E2 free-form header out of union; typed elements in | ✅ | `_source_grounding_elements` / `_location_grounding_pairs` |
| F5 | Closed kind allowlist | ✅ | Enum + E2 allowlist; unit rejects `summary`/`section_role`/`model_derived` |
| F6 | Vector recovery gated on triple + hash | ✅ | `match_chunk_embeddings` + hash compare |
| F7 | Batch `call_key` `embed_chunks:{first}:{count}` | ✅ | `min(chunk_id)` + count (UUID-order equivalent) |
| F8 | Legacy alias dual-write | ✅ | `context_prefix` / `prefixer_version` on stamps |
| F9 | Claims channel no message scalar inheritance | ✅ | `P1ClaimRow` unchanged |
| F10 | §4.3 multi-chunk header gate | ✅ | Policy + unit proof |
| F11 | No bare numeric `node_path` in headers | ✅ | Full header fields omit path |
| F12 | Whole-field header bounding | ✅ | `_join_header_fields` |
| F13 | Per-batch P1 then PG; crash recovery without re-embed | ✅ | `_commit_batch`; recovery match |
| F14 | Poison split not total outage | ✅ | `_is_provider_outage` (see M1 / R1) |
| F15 | Generation-keyed P1 rows + stored hash | ✅ | Upsert key triple |
| F16 | Universal P1 scalars incl. generations | ✅ write / △ produce | Written; spine still defaults `source_shape` (S1) |
| F17 | Readiness + typed skips | ✅ | empty_body + poison only after non-outage proof |
| F18 | E2 extractor generation bump | ✅ | `d80-location-elements-1` in version string |
| F19 | D66 same-PR docs | ✅ | API page (R7 nit) |
| F20 | Zero-call policy-version attestation | △ | Cross-version carry-forward only; same-doc policy bump re-embeds |
| F21 | Embedder generation = model+dim+metric+params | △ | Bare model id still |
| F22 | §6.2 prepare stamps before provider | △ | Stamps after success/skip |
| F23 | Query targets active `(policy, embedder)` | ✅ | QueryEngine + Lance filters (R3/R4 nits) |
| F24 | Provenance membership at E2 boundary | △ | Still admits `provenance is None` when kind allowlisted |
| F25 | §4.3 pure mode procedure | ✅ | Policy steps 1–6 |
| F26 | Migration `p8_01_0021` in linear graph | ✅ | `test_migrations.py` + revision file |
| F27 | No retired `_prefix_prompt` path | ✅ | Location helpers only |

---

## 4. Should-fix / nits (ordered)

### S1 — Connector packaging never reaches E1 from the spine (unchanged)

`ChunkSource` models `source_shape` / channel / author / time
(`model/chunks.py:52–57`), and `_location_facts` forwards them (`e1.py:541–556`),
but `_SELECT_CHUNK_SOURCE` only loads title + `source_kind`
(`chunk_catalog.py:162–172`). Live path always defaults
`source_shape="document"`. Policy unit tests for `message_atom` cannot fire in
production until connectors + catalog project packaging. Design §3.2 allows
structure-only until connectors implement the contract — track as incomplete
wiring, not a renderer defect.

### S2 — Missing provenance still admitted into the grounding union (unchanged)

```780:783:src/rememberstack/workers/e2.py
                    if provenance is not None and str(provenance) not in (
                        _LOCATION_ELEMENT_PROVENANCE
                    ):
                        continue
```

Fail closed: require `provenance in allowlist` (reject `None`).

### S3 — Stale E2 Claimify prompt + grounding docstring (unchanged)

- Prompt still says additions may come from “stored CONTEXT PREFIX”
  (`e2.py:157–160`) while bundle code uses typed `LOCATION:` only
  (`e2.py:679`, `:722–731`).
- Grounding docstring still calls the stored prefix a designed union member
  (`e2.py:485–487`) — false under D80.

Code membership is correct; the model still receives contradictory
instructions. Update prompts/docs to LOCATION elements only.

### S4 — `hasattr(match_chunk_embeddings)` softens a required port method (unchanged)

`ChunkIndexPort` declares the method (`p1_index.py:37–48`); E1 still gates on
`hasattr` (`e1.py:226`). Partial fakes silently disable crash recovery. Call
the port method directly.

### S5 — `normalize_body` collapses all whitespace including newlines (unchanged)

Policy-artifact choice is allowed; freeze the rule in the policy version
description or preserve newlines (collapse horizontal runs only).

### S6 — Invented `"untitled"` coordinate filter (unchanged)

`_has_useful_coordinates` drops title `"untitled"`
(`embedding_input_policy.py:278`). Not in design; delete or record in the
policy artifact.

### S7 — Migration dead locals (unchanged)

`p8_01_0021_d80_embedding_input.py:30–34` still does `name = …; del name`.

### S8 — Thin embedder generation identity (unchanged)

Still bare model id (`e1.py:185`). Design wants model + dimension + metric +
provider params for interchangeability (§4.5, §8).

### S9 — Zero-call policy-version attestation (unchanged)

Policy bump with identical hash should copy vector into a new
`policy_generation` row without provider call (§4.5). Not implemented.

### S10 — Pure prepare not stamped before provider (unchanged)

§6.2 durability polish; recovery via P1 match still works.

### S11 — Handler-level fault-injection tests (elevated importance post-M1)

Add proofs for: provider outage re-raises / no all-poison stamps; size-1
`ProviderInvalidResponseError` → one `skip:poison_chunk`; P1 written / PG not
→ retry zero provider calls for matched hashes; dual-gen search returns only
active pair.

### S12 — Production embed adapter poison signal (new, from M1 residual R1)

Teach OpenRouter `embed` to raise `ProviderInvalidResponseError` for unusable
embedding bodies / clear content failures so rule-4 split can isolate one bad
chunk without treating infrastructure errors as success.

### S13 — E1 vs P1 embed model dual config (new, from M2 residual R4)

Single shared binding for chunk-embed generation across write and query
surfaces in the self-host profile.

### S14 — Dead `ContextPrefix` test fixtures (new, from M3 residual R6)

Delete retired generate payloads once no test asserts them.

### S15 — Docs “verbatim” vs normalized body (new, from M4 residual R7)

Align recipe wording with body-only normalization.

---

## 5. Test coverage assessment

### Solid (present and aligned)

| Area | Coverage |
|---|---|
| Pure policy modes + whole-field headers + empty skip | `test_embedding_input_policy.py` |
| §4.3 multi-chunk without section title → body_only | `test_d80_recovery_and_hydration.py` |
| Lance triple match + wrong embedder empty | same |
| Dual-generation row coexistence (`chunk_vectors`) | same |
| E2 rejects bad kind / model_derived / section_role | same |
| Legacy prefix strip helper | same |
| Outage classifier (InvalidResponse vs transport) | same |
| Migration graph includes `p8_01_0021` | `test_migrations.py` |
| E1 happy path D80 stamps + no generate prompts | `test_e1_chain.py` (DB) |
| E2 no location-LLM prompt; LOCATION in selection | `test_e2_chain.py` (DB) |
| API chunk body-only / no LLM prefix product | `test_retrieval_api.py` (DB) |

### Still missing (should-fix)

1. Handler outage vs size-1 poison end-to-end (S11).
2. Handler recovery: kill after P1 before PG; zero re-embed on hash match.
3. Active-generation **search** with two policy rows present (R5).
4. Missing-provenance rejection (S2).
5. Zero-call policy attestation across versions (S9).
6. Oversized single header field → body_only (`embedding_input_policy.py:347–348`).
7. Empty-body skip stamp visible on PG in a mixed document.

---

## 6. Executive summary

1. **Verdict: Approve with nits** — residual M1–M4 are closed; remaining work is
   polish, adapter poison signaling, spine packaging, and coverage depth.
2. **M1 closed:** only `ProviderInvalidResponseError` is size-1 poison; outages
   and unknown errors re-raise so readiness cannot close on an empty all-poison
   set from a total outage.
3. **M2 closed:** QueryEngine always passes active policy + embedder generation
   into Lance search and hydration; production path no longer mixes dual-gen
   rows on the default surface.
4. **M3 closed:** E2 chain and retrieval API proofs assert D80 product (no
   location LLM; deterministic header alias; body-only text; D48 gates that
   match actual hydration).
5. **M4 closed:** D66 API docs describe deterministic `context_prefix` and
   body-only `chunk_text`.
6. **Prior pure-policy + write-path + E2 membership wins remain green.**
7. **Highest-value nits next:** S11/S12 (prove and enable real poison
   isolation), S3 (E2 prompt truthfulness), S1 (spine `source_shape` wiring),
   S13 (one embedder binding), S2/S4 (fail closed).
8. **Not blocking:** flipable deployment cutover store, zero-call policy
   attestation, rich embedder identity, prepare-before-embed stamps — full-scope
   completeness, not residual M1–M4 regressions.
9. **Merge confidence:** unit proofs for classifier + Lance generations are in
   tree; re-run DB-gated `test_e1_chain` / `test_e2_chain` / `test_retrieval_api`
   with Postgres before treating CI-with-DB as observed green in this review.
10. **Bar for full Approve (no nits):** S11 handler proofs green; OpenRouter
    embed can signal content poison; E2 prompts no longer mention CONTEXT
    PREFIX as a quotable union member.

---

### Prior residual must-fix re-verification (summary)

| Prior residual | Status after this re-review |
|---|---|
| M1 outage vs poison | **Done** — classifier + re-raise; residual R1/R2 are isolation completeness, not false-ready |
| M2 QueryEngine active generations | **Done** — nomination + hydrate scoped; residual R3–R5 are cutover ops / config dual-source / search unit gaps |
| M3 test_e2_chain + test_retrieval_api | **Done** — D80 assertions; residual dead fixtures |
| M4 website API docs | **Done** — deterministic / body-only wording; residual “verbatim” nit |

### What to keep

Deterministic `render_embedding_input`, generation-safe Lance upsert and recovery
match, typed location elements at E2, per-batch P1→PG order, QueryEngine
active-pair filters, outage-safe poison classifier, rewritten integration
assertions, and corrected public API docs.
