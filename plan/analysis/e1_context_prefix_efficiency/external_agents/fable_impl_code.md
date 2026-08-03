# Fable — D80 embedding-input implementation review

**Reviewer:** Fable (external agent pass, 2026-08-03, branch `code/d80-embedding-input-impl`,
after commit dfe6e8d).
**Scope:** `core/embedding_input_policy.py`, `workers/e1.py` (EmbedChunksHandler),
`spine/chunk_catalog.py`, `workers/e2.py` (`_location_*`), `surfaces/query_engine.py`
(chunk confirm), migration `p8_01_0021`, `tests/core/test_embedding_input_policy.py` —
checked against `e1_embedding_input_policy.md` (binding) and
`orchestration_design.md` § embed_chunk durability (D80 minimum contract).
**Method:** all listed files read in full; policy probed empirically; integration suites
executed against a real pg_partman Postgres (they silently skip without
`REMEMBERSTACK_DATABASE_URL`, which is why the failures below were invisible).
Review only; no production edits made.

---

## 1. Verdict

**Request changes.**

The pure-policy half of D80 is genuinely good: a total, deterministic
`render_embedding_input` with a pinned char counter and policy version, no LLM anywhere
on the embed path, body-only P1 text, hash-gated vector carry-forward, and typed
location elements replacing the free-form prefix in the E2 grounding union. That is the
hard conceptual part, and it landed cleanly.

But the branch is **red**: three existing integration tests still assert the retired
prefix-LLM behavior and fail when a database is present. The header renderer
demonstrably mangles output on the default path (mid-word truncation embedded into
vectors, stored on PG, and returned through the public API). The mode decision deviates
from the bound §4.3 procedure in a way that reintroduces a coordinate the design
explicitly bans. And the orchestration design's *minimum* embed_chunk durability
contract (per-batch cross-store order, generation-keyed P1 rows, poison split, typed
skips) is not implemented and not acknowledged anywhere.

---

## 2. Design fidelity checklist

| # | Contract | Verdict |
|---|---|---|
| F1 | **No location LLM on default path** (§1.3, §6.3) — provider used only for `embed`; `prefix_model` retired to an unused setting; no `generate` call in E1 | ✅ |
| F2 | **Total pure policy function**, ordered first-match decision, typed empty-body skip, policy version content-names the char counter (`e1-embed-input-v1:char`) | ✅ |
| F3 | **P1 text column is body-only** (§7); header kept on the PG stamp and returned separately at hydration (`query_engine.py:1554–1565`) | ✅ |
| F4 | **E2 grounding union**: free-form `context_prefix` removed as a member; typed `LocationElement` pairs from the stamp enter instead; summaries excluded (`e2.py:686–761`) | ✅ |
| F5 | **Closed element allowlist** (§3.3): enum matches exactly; no summary/ordinal/prefix kinds emitted (`embedding_input_policy.py:34–44`) | ✅ |
| F6 | **Vector carry-forward gated on `embedding_text_hash` + `policy_generation` + embedder generation**, not content hash alone (`e1.py:223–234`, `chunk_catalog.py:231–247`); zero provider call on reuse | ✅ |
| F7 | **Batch call keys** `embed_chunks:{sorted_first_chunk_id}:{count}` (orchestration rule 3) (`e1.py:252–253`) | ✅ |
| F8 | **Legacy alias dual-write** (`context_prefix` / `prefixer_version` mirror the header/policy) — §2 allows this during transition | ✅ |
| F9 | **Claims channel does not inherit message scalars** (§5.5) — `P1ClaimRow` unchanged | ✅ |
| F10 | **§4.3 step 4 gate** — impl adds `"document"` to the shape set (`embedding_input_policy.py:257–262`), so *every* multi-chunk document passes step 4; design requires a real section title path **or** transcript/thread/channel_export shape | ❌ |
| F11 | **§3.1/§4.4 banned coordinates** — full header falls back to `Section path: {node_path}` (numeric, e.g. `0.3`) when no section title exists (`embedding_input_policy.py:292–293`). Numeric node_paths renumber on insert, so this recreates exactly the insert-cascade document-wide re-embed the design bans for `i of N` ordinals | ❌ |
| F12 | **Header bounded, never mangled** — `_truncate_header` slices at code point 48 mid-field (`embedding_input_policy.py:322–327`). Demonstrated on the default path (§3, B1) | ❌ |
| F13 | **embed_chunk rule 5 (cross-store order)** — design: *per successful batch*, (a) upsert P1 keyed `(chunk_id, policy_generation, embedder_generation)` with stored hash, (b) stamp PG; crash between ⇒ complete the stamp with **no** provider re-call. Impl runs all batches, then one P1 upsert, then one PG write (`e1.py:248–322`); recovery is impossible because P1 rows store no hash or generations | ❌ |
| F14 | **embed_chunk rule 4 (poison split)** — absent; one bad batch fails the whole attempt, dead-lettering finished siblings | ❌ |
| F15 | **§4.5/§7 generation-keyed P1 rows + dual-generation cutover** — `P1ChunkRow` has neither `policy_generation` nor `embedder_generation`; Lance upserts by `chunk_id` alone (`adapters/selfhost/lance.py:68–88`) — the exact "sole in-place upsert-by-chunk_id" §7 forbids as the migration story | ❌ |
| F16 | **§5.2 universal P1 scalars** (`source_kind`, `source_shape`, `policy_generation`, `embedder_generation`) — not projected; only `section_role` exists | ❌ |
| F17 | **Rule 6 readiness / typed skips** — `empty_body` skips are computed but recorded nowhere; chunks whose vector never materializes are silently dropped and the stage still chains extraction (`e1.py:285–287, 323`) | ❌ |
| F18 | **Extraction identity (D56)** — the E2 bundle text changed (`CONTEXT PREFIX:` → `LOCATION:` line with different content) and union membership changed, but `E2_EXTRACTOR_VERSION` stays `2026.07j` (`e1.py:60`). Every prior bundle change (07d–07j) bumped it; unbumped, cached extractions replay as if produced by the new bundle | ❌ |
| F19 | **D66 same-PR docs** — `website/src/app/docs/reference/api/page.mdx:183` still says chunk results disclose their "*generated* `context_prefix`" and "*verbatim* `chunk_text`"; both are now false (deterministic header; whitespace-collapsed body) | ❌ |
| F20 | **§4.5 zero-call attestation on policy-version change** — only cross-doc-version carry-forward exists; a same-version policy bump with unchanged hash re-embeds via the provider (cost, not correctness) | △ |
| F21 | **Embedder generation identity** — bare model-id string; design wants model + dimension + metric + provider params | △ |
| F22 | **§6.2 prepare stamps** — location facts / mode / hash are stamped only on embed *success*, not at prepare time | △ |

---

## 3. Bugs

**B1 — Header truncation mangles every default-path header (must-fix).**
`H_MAX = 48` code points, enforced by slicing. Empirically:

- multi-chunk doc → `'Document: Quarterly business review for the ente'` (section + role lost, garbage token embedded)
- test corpus → `'Document: skeleton; Section: skeleton; Role: bod'`
- compact message header → `'Channel: C0123456789; Author: U0456; Time: 2026-'` (timestamp destroyed)

The mangled string is what gets embedded, what is stored as `location_header` on PG,
and what `/search/chunks` returns as `context_prefix`. Note the design's own canonical
compact header (channel + author + ISO timestamp) is 63 chars and cannot fit
`H_MAX = 48` at all — the fix is dropping whole trailing fields to fit (plus
re-examining the char-counter calibration of `H_max`), never mid-field slicing.

**B2 — Three red integration tests (must-fix).** All hidden by the DB-skip:

- `test_e1_chain.py::test_document_reaches_lance_with_prefixed_embeddings` (line 218) — asserts the canned LLM prefix `"Sits early in the test document."`, one LLM prompt per chunk, and prefix-in-P1-text; all retired by this PR.
- `test_e2_chain.py::test_claims_land_grounded_with_drops_ledgered_and_stance_kept` (line 413) — asserts a prompt containing `"state where this passage sits"`, the retired location-LLM instruction.
- `test_retrieval_api.py::test_lexical_claim_and_live_chunk_search_are_public_and_typed` (line 785) — asserts the old prefix; actual API now returns `"Document: staffing; Section: staffing; Role: bod"` (also re-demonstrating B1 on the public surface).

**B3 — Recovery dead-end for stamped chunks with a lost P1 vector.** If a chunk row is
stamped under the current generations but the index lacks its vector, the handler
neither re-embeds it (excluded from `need_embed`) nor fails; the comment says "leave
for retry" (`e1.py:286`) but the stage returns success and chains extraction — the
chunk is silently unsearchable until its body changes.

**B4 — Crash between P1 upsert and PG stamp re-buys every vector.** Because nothing is
durable per batch and unstamped chunks never consult `chunk_vectors`, a retry after a
mid-run crash re-embeds the entire representation — the precise scenario spike §10.4
("no re-embed of completed chunks") and rule 5 exist to prevent. (Same root cause as
F13/F15: P1 rows carry no hash to verify against.)

**B5 — Mode gate over-breadth + numeric path fallback (F10+F11 composed).** A
multi-chunk document with *no* section titles gets `location_header` with
`Section path: 0.3` — header where the design says `body_only`, containing a
coordinate class the design bans. Verified empirically.

**B6 — `normalize_body` collapses *all* whitespace, including newlines**
(`embedding_input_policy.py:104–106`). P1 text is both the BM25 column and the
`chunk_text` evidence agents read at hydration; multi-paragraph chunks, lists, and code
blocks become one long line (the old path stored the verbatim slice). §4.1 does leave
normalization to the policy artifact, so this is a *choice* — but it is an invented one
with a real evidence-fidelity cost, and it is recorded nowhere. Decide deliberately
(suggest: collapse runs of spaces/tabs, preserve newlines) and document it in the
policy artifact description.

**B7 — Legacy free-form prefix leaks into the E2 bundle prompt.**
`_location_bundle_line` falls back to `chunk.location_header or chunk.context_prefix`
(`e2.py:726`) when no typed pairs exist. §3.3: free-form headers are "**not** bundle
members". Safe direction (it is not in the union), but it invites the extractor to
quote text the grounding gate must then reject.

**B8 — Stale docstring contradicts D80.** `_source_grounding_elements` still claims
"the stored context prefix" is a union member (`e2.py:695–698`); the code (correctly)
no longer includes it.

**B9 — No provenance filter when consuming stored elements.**
`_location_grounding_pairs` admits any `kind`/`text` from `location_facts_json` into
the union without checking `provenance` (`e2.py:742–754`). Today's writer never emits
`model_derived`, but §3.3 makes provenance the membership rule — enforce it at the
consumption boundary, not by trusting the producer.

**B10 — Minor.** Migration dead code (`name = column_sql.split()[0]` / `del name`,
`p8_01_0021:30–34`); `_has_useful_coordinates` special-cases the literal title
`"untitled"` (`embedding_input_policy.py:275`) — an invented heuristic, not in the
design; `_location_facts` is computed twice per chunk (`e1.py:201, 299`).

---

## 4. Missing tests

1. **Rewrite the three stale tests (B2)** to assert D80 behavior: deterministic header
   on PG, zero LLM prompts, provider-embedded text = header + body while **P1 text is
   body-only** (the one property the old test proved that nothing proves now).
2. **Reuse locality (spike §10.5):** new doc version, unchanged body + location ⇒
   carried vector, zero provider calls; section retitle only ⇒ new hash ⇒ re-embed.
   `carry_forward_sources` and the reuse branch have no test at all.
3. **Fault injection (spike §10.4):** kill between P1 upsert and PG stamp; retry must
   not re-call the provider. (Blocked on F13/F15 — write it with the fix.)
4. **Stamp → E2 round-trip:** `location_facts_json` elements appear as typed union
   members; the free-form header does not; `model_derived` elements are excluded (B9).
5. **Header bounding:** truncation drops whole fields, never splits one (B1); a header
   that cannot fit any field falls back to `body_only` (the code has this path — untested).
6. **Typed skip:** an empty chunk *inside* a non-empty document records its skip and
   does not block readiness (only the whole-empty-document case is covered today).
7. **Migration `p8_01_0021`:** direct up/down assertions in `test_migrations.py`
   (currently exercised only incidentally by other suites' fixtures).

---

## 5. Must-fix vs should-fix

**Must-fix (blocking):**

1. B2 — make the suite green; the branch currently fails against a real database.
2. B1 — field-boundary header truncation; never embed or store a sliced field.
3. B5 — remove `"document"` from the step-4 shape set; drop the numeric
   `Section path:` header fallback (or design a stable replacement in the policy doc).
4. F18 — bump `E2_EXTRACTOR_VERSION` (bundle text and union membership changed).
5. F13/F15/F17 (+F14) — implement the embed_chunk minimum contract: per-batch
   P1-then-PG order, P1 rows keyed `(chunk_id, policy_generation, embedder_generation)`
   with stored `embedding_text_hash`, crash recovery without provider re-calls, typed
   skip recording, poison split. If any part is deliberately sequenced to a follow-up
   PR, that belongs in `plan/plans/` and in this PR's description — the orchestration
   design calls this the *minimum* contract, so silence is not an option.
6. F19 — same-PR docs (D66): fix the API-reference `context_prefix`/`chunk_text`
   wording; keep `/docs/project-status` truthful about the retired prefix LLM.

**Should-fix (non-blocking, record if deferred):**

7. B6 — preserve newlines in `normalize_body`, or record the collapse as a policy-artifact decision.
8. B3 — turn the stamped-but-vectorless case into a real retry or a typed failure.
9. B7/B8/B9 — bundle-line fallback, stale docstring, provenance filter.
10. F20 — same-version zero-call attestation on policy bump (cost only).
11. F21 — widen embedder-generation identity beyond the model id.
12. B10 — dead code and the invented `"untitled"` heuristic (delete, or record in the policy artifact).

---

## 6. Executive summary

1. Verdict: **Request changes** — the deterministic core is right; the operational half is not there yet.
2. The D80 promise holds: no location LLM anywhere on the default path (verified in code and by zero LLM prompts at runtime).
3. The policy is a clean total pure function with a pinned char counter, versioned artifact string, and correct hash-gated vector reuse.
4. E2 now grounds on typed location elements; the free-form prefix is out of the union — the single most important grounding change landed correctly.
5. The branch is red: three integration tests still assert prefix-LLM behavior; they skip without a database, so this was invisible locally.
6. Every default-path header is truncated mid-field at 48 chars ("Role: bod", "Time: 2026-") — embedded, stored, and served through the API.
7. The mode decision gives headers to all multi-chunk docs and falls back to numeric `Section path: 0.3` — a coordinate class D80 bans for its re-embed cascade.
8. The orchestration embed_chunk *minimum* contract is unimplemented: no per-batch durability, no generation-keyed P1 rows, no poison split, no recorded skips; a mid-run crash re-buys the whole document's embeddings.
9. `E2_EXTRACTOR_VERSION` was not bumped despite a changed bundle and union — cached extractions replay under a false identity.
10. Docs (D66) still describe a "generated" prefix and "verbatim" chunk text.
11. Fix order: tests green → header rendering → mode gate → extractor bump → durability contract (or an explicit sequencing entry in `plan/plans/`).
12. With those closed, this is an approve — the design's hard part is already implemented well.
