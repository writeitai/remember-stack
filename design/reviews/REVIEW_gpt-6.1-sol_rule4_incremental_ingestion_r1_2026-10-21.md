# Review: writeitai/remember-stack #507

Reviewed the fetched diff `origin/main...origin/docs/incremental-ingestion-rule`, head `ae8f908dc318ef1c24f4019e874dd3d916d85b9f`, against the implementation and binding decisions. The owner's incremental-ingestion requirement is treated as binding; implementation discrepancies below should be resolved explicitly, not silently turned into exceptions.

## P0

None.

## P1

1. **`CLAUDE.md:83` — The stated extraction identity omits D122, which also contradicts the section-isolation example at line 79.**

   **Problem:** The listed inputs accurately summarize `extraction_input_hash` for Selection, but matching that hash does **not** guarantee reuse of extracted claims. Claimify additionally hashes the target Selection input, the ordered Selection inputs of the previous eight source chunks (including producers with zero cards), and `REFERENCE_POLICY_VERSION`. The preceding window crosses section boundaries. Consequently, an edit in §12 can rerun Claimify in §13 while §13's own text and `extraction_input_hash` remain unchanged. This is implemented in `workers/e2.py:574`, `core/selection_references.py:66`, and `workers/extraction_references.py:54` (all under `src/rememberstack/`). D122 explicitly amends D56. The existing `src/tests/spine/test_d122_e2.py:155` asserts that changing Part 4 reruns Claimify for unchanged Part 7 while reusing its Selection.

   **Concrete fix:** Describe Selection and Claimify identities separately, name the preceding-source and reference-policy dependencies, and require tests for both stages. Explicitly reconcile D122's cross-section invalidation with the owner's unchanged-section requirement in the rule and authoritative contract; do not claim that the present implementation confines all extraction to immediate same-section neighbours.

2. **`CLAUDE.md:87` — Anchor stabilization does not establish the claimed fixed invalidation radius.**

   **Problem:** `src/rememberstack/core/chunker.py:219` greedily repacks to budget and restarts at accepted content anchors. A length-changing edit can shift many subsequent chunks before packing realigns; sparse or absent anchors can allow drift through the entire section or synthetic root. D58 and `plan/designs/e1_chunks_design.md:227` explicitly describe this limitation. With the default parameters, I reproduced a 40-paragraph section with no anchor blocks: lengthening only paragraph 0 changed packing from 10 chunks to 11, with **zero** reusable content hashes. Thus lines 77–78 and the proposed three-chunk test bound are not general properties of the engine.

   **Concrete fix:** Replace the unconditional explanation with the actual mechanism: packing can drift until a common accepted anchor or section/run boundary, with no fixed chunk-count guarantee. State the stronger locality promise as a requirement rather than shipped behavior; if the immediate-neighbour maximum is retained, require packing changes and a regression covering length changes, insertions/deletions, and long sections without anchors before asserting it is satisfied.

## P2

1. **`CLAUDE.md:86` — The reuse-input and migration inventory leaves independent invalidators unnamed.**

   **Problem:** Passage vector reuse also requires the same embedding-input policy generation and embedder generation, not just matching content and rendered-input hashes (`src/rememberstack/workers/e1.py:267`, `:328`; D80). Separately, D140's date-free `reuse_identity_hash` includes `REUSE_IDENTITY_VERSION` and `blockizer_version`; a change can prevent origin inheritance and thereby change the extraction date despite unchanged block text (`src/rememberstack/core/text_origin.py:38`). Ineligible chunks additionally key `ineligible:<EXTRACTION_ELIGIBILITY_POLICY_VERSION>` (`src/rememberstack/workers/e1.py:739`), and eligibility transitions force packing cuts (`core/chunker.py:225`). Converter route/version changes are also explicit D57 reuse boundaries. Lines 93–95 omit several of these concrete mechanisms.

   **Concrete fix:** Name the separate origin, extraction, and vector identities. Add converter route/version, blockizer and origin-identity generations, extraction-eligibility labels/policy, and embedder generation to the review checklist, alongside D122's reference policy. Require pinned conversion routes and an explicit statement of each migration's affected scope; do not describe every boundary as necessarily redoing every stage.

2. **`CLAUDE.md:89` — The prohibition conflates generated noise with authoritative source content and real metadata changes.**

   **Problem:** “Dates ... never in the keyed text” would forbid preserving meaningful dates in contracts, messages, and meeting notes. D140 replaces the *version-date header inputs* with inherited text origin; it does not remove source dates from block text. Likewise, “same title and file name for every version” at lines 103–104 contradicts D134: genuine renames are supported, and the changed name deliberately invalidates claims naming the old document (`src/rememberstack/model/chunks.py:75`, `workers/e1.py:728`; `src/tests/workers/test_self_reference_naming.py:234`). Also, a banner changed once in the body does not inherently change **every** key; shared header facts have that reach, while body edits affect dependent regions.

   **Concrete fix:** Ban *fabricated volatile decoration* in shared extraction/embedding inputs. Preserve real source dates, headings, titles, and file names. Distinguish source changes from generated suffixes/banners, local body invalidation from shared-header invalidation, and explicitly document the D134 correctness boundary for genuine renames.

3. **`CLAUDE.md:93` — The rule omits the central D56 prohibition on generated context entering identity or drifting during reuse.**

   **Problem:** D56 explicitly excludes LLM outputs—summaries, free-form prefixes and model-generated section paths—from extraction identity and requires replay of committed context for unchanged regions. Merely avoiding version timestamps does not prevent a nondeterministic summary from destroying every reuse hit. Even without changing extraction keys, redrawing section ownership can change packing, and changing section titles/roles can change rendered embedding inputs (`src/rememberstack/workers/e1.py:614`; `core/embedding_input_policy.py:303`). These changes need no extractor/parser version-string change to break reuse.

   **Concrete fix:** Add an explicit requirement to keep offsets, occurrence IDs, summaries, and freshly generated LLM context out of extraction/origin identity; replay committed derived context for unchanged regions. Preserve applicable section ownership and embedding-location facts under the pinned generation, and test that summary regeneration and unrelated structural changes do not silently re-extract or re-embed unchanged content. Link D56 and `plan/designs/e1_chunks_design.md:332`.

4. **`CLAUDE.md:102` — Stable lineage identity is missing from the connector requirements.**

   **Problem:** Byte-identical unchanged text and stable names are insufficient if each observation uses a new source identity. `UploadIngestor.ingest_observed` derives `doc_id` from deployment, `source_kind`, and `source_ref` (`src/rememberstack/workers/e0.py:304`). Both origin matching and cross-version reuse are lineage-scoped. A connector that appends a revision ID or content hash to `source_ref` therefore pays for the whole document every time while satisfying the listed rendering constraints. Ordinary one-shot uploads are content-identified and have the same issue for edited bytes (`e0.py:188`). D55 makes this distinction explicit.

   **Concrete fix:** Require a stable connector-native `(source_kind, source_ref)` for the logical document, keeping revision cursors/version keys separate. State that the v1/v2 regression must observe two versions of that same lineage, rather than perform two unrelated uploads.

5. **`CLAUDE.md:97` — The required regression does not define a reliable oracle for the promise.**

   **Problem:** An undated fixture misses the exact full-document re-extraction failure D140 prevents. A section containing only one chunk never exercises same-section neighbours. The combined “new extractions and embeddings” assertion also conflates different dependencies: unchanged neighbours may need fresh Selection but should still reuse vectors when their embedding identities match; Claimify has the separate D122 dependency above. Vector equality alone cannot prove reuse because a deterministic embedder can recompute an identical vector.

   **Concrete fix:** Specify a same-lineage fixture with untouched sections before and after an edited section containing several chunks, distinct non-null v1/v2 source dates, stable names, and pinned toolchain generations. Measure provider calls separately for Selection, Claimify, and embedding; assert exact content/extraction/Claimify identities, inherited immutable `text_origin_at`, same reused `claim_id`s through `chunk_claims`, correctly remapped evidence spans, and zero embed calls for unchanged embedding identities—including neighbours. Add a packing-boundary case and a zero-card D122 producer case, and make any conflict with the owner's unchanged-section requirement fail visibly. Define text origin as the earliest eligible matching origin in the live lineage, not the new observation date (`core/text_origin.py:52`).

6. **`CLAUDE.md:75` — The opening describes cheap reconstruction and stable hashes as if chunk rows themselves were reused.**

   **Problem:** For a new representation, `ChunkHandler.handle` loads the entire block grid and calls `pack_blocks` across all sections (`src/rememberstack/workers/e1.py:138`), then creates new rows with fresh `chunk_id`s (`:164`, `:681`). The current implementation reuses content/extraction identities and downstream products; it does not literally re-chunk only the changed chunk or reuse previous row IDs. This distinction matters to a cold reader interpreting “previous chunk keys” and “only ... are re-chunked.”

   **Concrete fix:** Name `chunk_content_hash` and extraction identities explicitly, distinguish them from occurrence `chunk_id`s, and distinguish whole-document deterministic scanning/repacking from provider extraction/embedding work. If avoiding repacking unchanged sections is itself the owner's required behavior, state it as an enforced requirement and identify the current implementation gap instead of calling it already built.

## Nits

- **`CLAUDE.md:13` — Problem:** The introduction still says “three rules,” but this PR adds Rule 4. **Concrete fix:** Change it to “four rules” or “the following rules.”
- **`CLAUDE.md:84` — Problem:** “document kind” is ambiguous: the keyed field is `source_kind` (connector/source class), not the document's format/family. Decision numbers and “anchor-stabilized” are also names without navigation or a plain-language definition. **Concrete fix:** Use “source kind (`source_kind`)”; link the relevant decision entries and explain that content anchors restart packing at selected block hashes.

`AGENTS.md:3` correctly delegates to the existing working agreement; no finding on that file.

Validation: 87 focused tests passed across chunk packing, D119 reuse, D140 text origin, D122 reference identity, embedding policy/recovery, self-reference naming, and extraction eligibility. One additional PDF-limit test failed during import because the available environment lacks `pypdfium2`; this is not attributed to the documentation diff. PostgreSQL integration tests were inspected, not executed. The small reproductions above used the actual current modules. No tracked files were modified; no commit or push was performed.

Verdict: Request changes
