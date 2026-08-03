# Fable — D80 embedding-input implementation review (post-must-fix)

**Reviewer:** Fable  
**Branch:** code/d80-embedding-input-impl  
**Verdict:** Request changes

**Scope:** Re-review after prior must-fixes against
`plan/designs/e1_embedding_input_policy.md` and
`plan/designs/orchestration_design.md` § embed_chunk durability (D80 minimum
contract). Surfaces: policy, `EmbedChunksHandler`, E2 location grounding, Lance
P1, chunk model/ports/catalog, query hydration, migration `p8_01_0021`, and
focused tests. Prior reviews: this file (first pass) and `codex_impl_code.md`.

**Method:** all listed implementation files and tests read in full; design
§4.3/§3.3/§4.5/§7 and orchestration rules 1–6 checked point-by-point. Review
only; no production edits.

---

## 1. Verdict

**Request changes.**

The prior blocking gaps on pure policy, generation-keyed P1 storage, per-batch
P1→PG order, typed E2 location membership, whole-field headers, §4.3 mode gate,
legacy hydration strip, extractor version bump, `_prefix_prompt` removal, and
`p8_01_0021` revision-graph membership **are substantially closed**. That is real
progress: the D80 conceptual core is now present in code, not only design.

What remains is still contract-breaking:

1. **Poison split misclassifies total outages as per-chunk poison**, stamps
   `skip:poison_chunk` for every size-1 failure, treats those as closed readiness
   skips, and chains E2 — so a downed embedder can “succeed” with zero searchable
   vectors (orchestration rule 4 violated in the dangerous direction).
2. **Active `(policy_generation, embedder_generation)` is not applied at query**
   despite dual-generation rows and port support — cutover search can mix
   generations (design §4.5 / §5.2 incomplete).
3. **Two DB-backed integration suites still assert retired prefix-LLM behavior**
   (`test_e2_chain`, `test_retrieval_api`); unit/red-suite items Codex called out
   are fixed, but the suite is not green end-to-end when Postgres is present.
4. **D66 docs** still describe a generated `context_prefix` and verbatim
   `chunk_text`.

Until (1) is fixed, the durability contract is not safe to ship. Until (2)+(3)
are fixed, dual-generation cutover and CI-with-DB remain false friends.

---

## 2. Design fidelity checklist

| # | Contract | Verdict | Evidence |
|---|---|---|---|
| F1 | **No location LLM on default path** (§1.3, §6.3) | ✅ | `EmbedChunksHandler` only calls `model_provider.embed` (`e1.py:361–366`); `prefix_model` retained but unused (`e1.py:78–79`); E1 integration asserts zero generate prompts (`test_e1_chain.py:229`) |
| F2 | **Total pure policy**, ordered first-match, empty-body skip, policy version pins char counter | ✅ | `render_embedding_input` / `_decide_mode` (`embedding_input_policy.py:188–236, 249–274`); `EMBEDDING_INPUT_POLICY_VERSION = "e1-embed-input-v1:char"` (`:17`) |
| F3 | **P1 text = body only**; header on PG stamp / hydration separate | ✅ | `P1ChunkRow.text=rendered.body` (`e1.py:435`); hydration returns `chunk_text` + `context_prefix`/`location_header` (`query_engine.py:1554–1575`) |
| F4 | **E2 grounding**: free-form header out of union; typed LocationElements in | ✅ | `_source_grounding_elements` + `_location_grounding_pairs` (`e2.py:693–796`); bundle line only pairs (`e2.py:722–731`) |
| F5 | **Closed kind allowlist** (§3.3); no summary/ordinal/prefix/section_role kinds | ✅ | Enum (`embedding_input_policy.py:34–44`); E2 allowlist (`e2.py:734–745`); rejects `section_role`/`summary` (`test_d80_recovery_and_hydration.py:137–204`) |
| F6 | **Vector recovery gated on triple + hash** | ✅ | `match_chunk_embeddings` (`lance.py:141–175`); hash compare (`e1.py:222–239`); dual-gen coexist test (`test_d80_recovery_and_hydration.py:80–134`) |
| F7 | **Batch `call_key`** `embed_chunks:{sorted_first}:{count}` | ✅ | `min(chunk_id)` + count (`e1.py:358–359`) — equivalent to sorted-first for UUIDs |
| F8 | **Legacy alias dual-write** (`context_prefix` / `prefixer_version`) | ✅ | `EmbeddingUpdate` dual fields (`e1.py:460–461`) |
| F9 | **Claims channel does not inherit message scalars** (§5.5) | ✅ | `P1ClaimRow` unchanged (`model/chunks.py:213–229`) |
| F10 | **§4.3 step 4** — multi-chunk header only with real section title **or** transcript/thread/channel_export | ✅ | Fixed (`embedding_input_policy.py:259–265`); proof (`test_d80_recovery_and_hydration.py:219–239`) |
| F11 | **No numeric `node_path` / `i of N` in headers** (§3.1/§4.4) | ✅ | Full header omits bare path (`embedding_input_policy.py:299–314` comment at 305) |
| F12 | **Header bounded by whole fields, never mid-slice** | ✅ | `_join_header_fields` (`embedding_input_policy.py:334–348`); tests (`test_embedding_input_policy.py:64–102`) |
| F13 | **Cross-store order: per batch P1 then PG; crash recovery without re-embed** | ✅ (write path) / △ (query cutover) | Per-batch `_commit_batch` P1→PG (`e1.py:398–467`); recovery from P1 triple+hash (`e1.py:222–289`). Active search pointer still missing (F23) |
| F14 | **Poison split** — batch fail → halve to size 1; size-1 poison typed skip; **not total outage** | ❌ | Halve path exists (`e1.py:367–393`) but **every** exception at size-1 becomes `poison_chunk` closed skip (`e1.py:368–370, 315–323, 325–339`) — total outage → “success” + E2 |
| F15 | **Generation-keyed P1 rows** `(chunk_id, policy_generation, embedder_generation)` + stored hash | ✅ | `P1ChunkRow` (`model/chunks.py:172–192`); Lance upsert key triple (`lance.py:89–95`); schema bootstrap (`lance.py:714–732`) |
| F16 | **Universal P1 scalars** `source_kind`, `source_shape`, `section_role`, generations | ✅ write / △ produce | Written on upsert (`e1.py:440–441`, `lance.py:81–85`). Production `ChunkSource` never loads `source_shape`/message refs from spine (`chunk_catalog.py:162–172`) — always defaults (`model/chunks.py:53–57`) |
| F17 | **Readiness + typed skips** (`empty_body`, …) | △ | Empty-body and poison stamped (`e1.py:200–212, 315–323, 469–504`); missing vectors raise (`e1.py:330–339`). Poison-as-success undermines readiness meaning (F14) |
| F18 | **E2 extractor generation bump** (bundle + union changed) | ✅ | `E2_EXTRACTOR_VERSION` includes `d80-location-elements-1` (`e1.py:60–65`) |
| F19 | **D66 same-PR docs** | ❌ | API reference still: generated `context_prefix` + verbatim `chunk_text` (`website/.../api/page.mdx:183–185`) |
| F20 | **Zero-call attestation on policy-version change** when hash+embedder match | △ | Cross-version carry-forward only (`e1.py:241–259`); same-doc policy bump re-embeds (cost) |
| F21 | **Embedder generation identity** = model + dim + metric + params | △ | Bare `embedding_model` string (`e1.py:183`) |
| F22 | **§6.2 prepare stamps** before provider (facts/mode/hash durable pre-embed) | △ | Stamps only after successful vector or typed skip (`e1.py:408–504`), not pure-prepare-first |
| F23 | **Query targets active `(policy, embedder)` pointer** (§4.5/§5.2) | ❌ | Port accepts filters (`p1_index.py:102–124`, `lance.py:302–352`); `query_engine` never passes them (`query_engine.py:711–718`); `chunk_texts` unscoped (`lance.py:362–385`) |
| F24 | **Provenance membership rule at E2 boundary** (§3.3) | △ | Rejects disallowed provenance when present (`e2.py:780–783`); **admits `provenance is None`** |
| F25 | **§4.3 pure policy mode procedure** (message_atom compact default, etc.) | ✅ | Matches design steps 1–6 (`embedding_input_policy.py:249–274`) |
| F26 | **Migration `p8_01_0021` in linear revision graph** | ✅ | `test_migrations.py:102`; revision file present |
| F27 | **No `_prefix_prompt` / retired LLM prefix path** | ✅ | `test_d79_summary_consumption.py` no longer imports it; uses `_location_*` |

---

## 3. Bugs / residual must-fixes

### M1 — Poison split turns total outages into “ready, empty” documents (blocking)

**Design (orchestration rule 4):** on batch failure that is **not** a total outage,
halve until size 1; a single-chunk poison is typed fail/skip for **that** chunk
only. A provider-wide failure must not convert every sibling into a successful
skip and advance the chain.

**Impl:**

```355:393:src/rememberstack/workers/e1.py
        """Embed one batch; on failure, halve until size-1 poison is typed-skip."""
        ...
        except Exception:
            if len(batch) == 1:
                poison_skips.append(batch[0])
                return
            mid = len(batch) // 2
            # recurse both halves
```

Then poison skips are stamped and counted as **closed readiness** (`e1.py:315–339`),
so the handler returns `_extract_follow_up` with **no** vectors and **no** raise.

**Effect:** embedder timeout/auth/network outage ⇒ every chunk `skip:poison_chunk`
⇒ document “embed-ready” ⇒ E2 runs on an unsearchable representation. That is
worse than dead-lettering the job: it **silently destroys** the passage channel
for the document under the active generation.

**Fix direction:** distinguish outage vs content poison (typed provider errors, or
“all size-1 halves failed with the same infrastructure error ⇒ re-raise”). Never
treat a size-1 transient failure as a closed skip that advances readiness. Only
stamp `poison_chunk` for confirmed non-retryable single-chunk failures; otherwise
fail the attempt so the work ledger retries.

### M2 — Active generation not enforced at query / hydration (blocking for cutover)

Write path correctly dual-keys P1 rows. Search ports accept
`policy_generation` / `embedder_generation`, but the only production consumer —
`QueryEngine._nominate_chunks` — never passes them
(`query_engine.py:711–718`). `chunk_texts` has no generation scope
(`lance.py:362–385`).

During dual-generation cutover (the migration story §4.5/§7 **requires**),
nominations and projections can mix `legacy` and current rows, or two policy
generations, for the same `chunk_id`. There is also **no** deployment-scoped
active-pointer store that cutover can flip.

**Fix direction:** deployment/query-scope active pair (config or spine); pass it
on every chunk search and text projection; refuse unscoped chunk search once
D80 rows exist (or default active pair to current policy + embedder settings).

### M3 — Stale DB-backed integration assertions (blocking CI-with-DB)

Prior B2 was only partially cleaned:

| Test | Still asserts |
|---|---|
| `test_e2_chain.py:413–416` | `"state where this passage sits"` in prompts (retired location LLM) |
| `test_retrieval_api.py:785–788` | `context_prefix == "Sits in the staffing note."` (LLM product) |
| `test_retrieval_api.py:793–836` | D48 drop when PG `context_prefix` mismatches / is null — hydration no longer implements that gate for D80 body-only rows (`query_engine.py:1554–1575`) |

`test_e1_chain.py` was updated correctly (deterministic stamps, zero generate
prompts). `_prefix_prompt` import and `p8_01_0021` graph membership are fixed.

**Fix:** rewrite E2/API proofs for D80: zero location-LLM prompts; deterministic
header (or null body_only); body-only `chunk_text`; drop the prefix-agreement
hydration tests or replace with generation/hash confirmation properties.

### M4 — D66 docs still wrong (project rule)

`website/src/app/docs/reference/api/page.mdx:183–185` still claims chunk results
disclose a **generated** `context_prefix` separately from **verbatim**
`chunk_text`. Under D80 the header is deterministic (when present) and P1/body
is **normalized** body, not necessarily the raw `document.md` slice after
whitespace collapse.

Same-PR docs obligation (Claude.md / D66) is unmet.

---

## 4. Should-fix / nits

### S1 — Connector packaging never reaches E1 from the spine

`ChunkSource` has `source_shape` / channel / author / time fields
(`model/chunks.py:53–57`), and `_location_facts` forwards them
(`e1.py:517–531`), but `_SELECT_CHUNK_SOURCE` only loads title + `source_kind`
(`chunk_catalog.py:162–172`). Production always defaults `source_shape="document"`
and null refs. Policy unit tests for `message_atom` cannot fire on the live path.

Design §3.2 allows structure-only until connectors implement the contract — so this
is not an invent-on-embed bug — but full-scope D80 needs spine columns (or
version/span metadata) and catalog projection before message connectors can
satisfy the bound compact-header path. Track as incomplete wiring, not a
renderer defect.

### S2 — Missing provenance admitted into the grounding union

```780:783:src/rememberstack/workers/e2.py
                    if provenance is not None and str(provenance) not in (
                        _LOCATION_ELEMENT_PROVENANCE
                    ):
                        continue
```

`LocationElement` requires provenance in the pure builder, but the E2 boundary
should **reject** missing provenance the same way it rejects `model_derived`
(§3.3 membership rule). Fail closed: require `provenance in allowlist`.

### S3 — Stale E2 prompt + docstring still sell free-form prefix as union member

- Claimify instruction text: “stored CONTEXT PREFIX” (`e2.py:157–160`)
- Grounding docstring: “the stored prefix, though LLM text, is a designed union
  member” (`e2.py:485–487`)

Code membership is correct; the model still receives contradictory
instructions, which invites `added_context` that the gate then rejects (noise
and false drop pressure). Update prompts/docs to LOCATION elements only.

### S4 — `hasattr(match_chunk_embeddings)` softens a now-required port method

`ChunkIndexPort` declares `match_chunk_embeddings` (`p1_index.py:37–48`), but E1
gates recovery on `hasattr` (`e1.py:224–232`). A partial fake/adapter silently
disables crash recovery and re-buys embeddings. Call the port method directly.

### S5 — `normalize_body` collapses **all** whitespace including newlines

(`embedding_input_policy.py:104–106`). P1 BM25 text and hydrated `chunk_text`
become single-line. Policy-artifact choice is allowed (§4.1) but undocumentedas
such; prefer preserving newlines (collapse only horizontal runs) or freeze the
collapse rule in the policy version description.

### S6 — Invented `"untitled"` coordinate filter

`_has_useful_coordinates` drops title `"untitled"`
(`embedding_input_policy.py:278`). Not in design; can hide a real title. Delete
or record in the policy artifact.

### S7 — Migration dead locals

`p8_01_0021_d80_embedding_input.py:30–34` still does `name = …; del name`.

### S8 — Embedder generation identity too thin (prior △)

Still bare model id (`e1.py:183`). Design wants model + dimension + metric +
provider params for interchangeability (§4.5, §8).

### S9 — Zero-call policy-version attestation (prior △)

Policy bump with identical hash should copy vector into a new
`policy_generation` row without provider call (§4.5). Not implemented.

### S10 — Pure prepare not stamped before provider (prior △)

§6.2 allows durable prepare stamps (facts, mode, hash) before embed batches.
Current stamps ride on success/skip only. Recovery still works via P1 match, so
this is durability polish, not a silent correctness hole once M1 is fixed.

### S11 — No handler-level fault-injection tests

Unit proofs cover Lance triple match and dual-gen coexist, but nothing drives
`EmbedChunksHandler` through: P1 written / PG not; provider outage vs single
poison; readiness refuse. Add once M1 is fixed.

### S12 — `chunk_texts` first-row wins under multi-generation

Unscoped projection (`lance.py:379–385`) can return an arbitrary generation’s
text for a `chunk_id`. Fold into M2.

---

## 5. Test coverage assessment

### Solid (present and aligned)

| Area | Coverage |
|---|---|
| Pure policy modes (multi-chunk header, message_atom ± coords, empty skip) | `test_embedding_input_policy.py` |
| Whole-field header bounding | `test_embedding_input_policy.py:86–102` |
| §4.3 multi-chunk without section title → body_only | `test_d80_recovery_and_hydration.py:219–239` |
| Lance triple match + wrong embedder empty | `test_d80_recovery_and_hydration.py:20–77` |
| Dual-generation row coexistence | `test_d80_recovery_and_hydration.py:80–134` |
| E2 rejects bad kind / model_derived / section_role; free-form out of bundle | `test_d80_recovery_and_hydration.py:137–204`, `test_d79_summary_consumption.py:131–137` |
| Legacy prefix strip helper | `test_d80_recovery_and_hydration.py:208–216` |
| Migration graph includes `p8_01_0021` | `test_migrations.py:75–103` |
| E1 happy path D80 stamps + no generate prompts | `test_e1_chain.py:187–231` (DB) |

### Missing or wrong

1. **Rewrite red DB tests** (M3): E2 chain + retrieval API.
2. **Poison vs outage** (M1): size-1 content poison stamps skip; full-batch
   infrastructure failure re-raises; siblings already committed stay durable.
3. **Handler recovery**: kill after P1 before PG; retry zero provider calls for
   matched hashes.
4. **Active-generation search** (M2): two generations present → only active
   pair nominated.
5. **Missing-provenance rejection** (S2).
6. **Carry-forward locality**: same body+location hash across versions, zero
   embed; section retitle changes hash → re-embed (policy unit or handler).
7. **Oversized single field → body_only** (code path at
   `embedding_input_policy.py:347–348` untested).
8. **Empty-body skip stamp** visible on PG (`embedding_ref=skip:empty_body`)
   inside a mixed document.

---

## 6. Executive summary

1. **Verdict: Request changes** — core D80 policy + generation-keyed write path
   landed; operational failure modes and query cutover still break the bound
   contract.
2. **Prior pure-policy must-fixes closed:** §4.3 mode gate, whole-field headers,
   no numeric path, no location LLM, body-only P1 text.
3. **Prior durability write-path must-fixes closed:** P1 triple key + hash,
   `match_chunk_embeddings`, per-batch P1→PG, empty-body skip stamps, E2
   version bump.
4. **Prior E2 membership must-fixes closed:** closed kinds, provenance filter
   (when set), no `section_role`, free-form header out of union/bundle.
5. **Prior red-suite items partially closed:** `_prefix_prompt` gone;
   `p8_01_0021` in revision graph; **e2_chain + retrieval_api still red** on
   prefix-LLM assertions when DB is present.
6. **M1 (blocking):** poison split converts total outages into per-chunk closed
   skips and chains E2 with no vectors — inverted durability.
7. **M2 (blocking for cutover):** dual-generation rows written; search/hydrate
   never filter active `(policy, embedder)` pair.
8. **M3/M4 (blocking process/docs):** rewrite stale integration tests; fix D66
   API wording (deterministic header; normalized body).
9. **Should-fix:** spine wiring for `source_shape`/message refs; reject null
   provenance; refresh E2 prompts/docstrings; drop `hasattr`; newline policy;
   remove `"untitled"` heuristic; embedder-generation identity; zero-call
   policy attestation; handler fault-injection tests.
10. **What is already good enough to keep:** deterministic
    `render_embedding_input`, generation-safe Lance upsert, recovery match API,
    typed location elements, legacy hydration strip helper, E1 chain proof shape.
11. **Fix order:** M1 poison/outage semantics → M3 green DB suite → M2 active
    query pointer → M4 docs → should-fix list.
12. **Bar for Approve:** outage cannot mark a document embed-ready; CI-with-DB
    green on D80 assertions; query scoped to active generation (or explicit
    single-generation deployment mode documented as the only supported search
    mode until the pointer ships — but dual-gen **write** without query scope is
    not that documentation).

---

### Prior must-fix re-verification (summary)

| Prior item | Status after re-review |
|---|---|
| 1. P1 composite key + hash recovery | **Done** (write/recovery). Residual: query active pair (M2), `hasattr` (S4) |
| 2. Poison split + typed skips + readiness | **Partial** — machinery present; **outage misclassification** remains (M1) |
| 3. source_shape / E2 validation / no section_role / free-form out | **Mostly done** at E2 boundary; source_shape production wiring incomplete (S1); null provenance admitted (S2) |
| 4. Legacy P1 hydration strip | **Done** (`_strip_legacy_prefix` + policy_generation gate) |
| 5. Red suite `_prefix_prompt` + `p8_01_0021` | **Done** for those two; **other red DB tests remain** (M3) |
| Pure policy §4.3 / whole-field / no location LLM | **Done** |
