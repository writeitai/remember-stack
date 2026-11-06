# Review (Round 2): PR writeitai/remember-stack #507 (`docs/incremental-ingestion-rule`)

Reviewed commit `c834b96f88e718ffce494f6e3b8ec1b66acda371` (`origin/main...origin/docs/incremental-ingestion-rule`) against the Round-1 reviews (`REVIEW_rs507_sol.md` and `REVIEW_rs507_agy.md`), `CLAUDE.md`, `AGENTS.md`, `decisions.md` (D55, D56, D57, D58, D80, D119, D122, D133, D134, D140), and the implementation (`src/rememberstack/core/chunker.py`, `src/rememberstack/core/text_origin.py`, `src/rememberstack/core/embedding_input_policy.py`, `src/rememberstack/core/selection_references.py`, `src/rememberstack/core/source_passages.py`, `src/rememberstack/workers/e0.py`, `src/rememberstack/workers/e0_summary.py`, `src/rememberstack/workers/e1.py`, `src/rememberstack/workers/e2.py`).

The restructured Rule 4 cleanly separates **The requirement (owner, binding)** from **How the engine implements it today**, **Known gaps against the requirement**, and **Rules for every change**. Every Round-1 P0, P1, P2, and Nit from both reviewers is closed, and no new P0, P1, or P2 inaccuracies were introduced.

---

## Round-1 Closure Audit

### `REVIEW_rs507_sol.md` (Sol)

| Item | Summary | Status | Evidence in `c834b96f8` (`CLAUDE.md`) |
| --- | --- | --- | --- |
| **P1-1** (`CLAUDE.md:83`) | Extraction identity omitted D122's 8-chunk cross-section Claimify window and contradicted the § 12 section-isolation example | **Closed** | `CLAUDE.md:76-81` states the owner's section-isolation requirement; `CLAUDE.md:97-105` defines **Selection (D56, D140)** (`extraction_input_hash`) and **Claimify (D122)** (`claimify_input_hash` = target Selection hash + previous eight chunks in document order + `REFERENCE_POLICY_VERSION`) separately; `CLAUDE.md:112-113` (Known gap 1) explicitly documents that the D122 Claimify window crosses section boundaries into § 13+; `CLAUDE.md:149-154` requires per-stage call counting and a D122 window test case. |
| **P1-2** (`CLAUDE.md:87`) | Anchor stabilization did not establish the claimed fixed 3-chunk invalidation radius | **Closed** | `CLAUDE.md:88-92` explains that chunks are whole-block runs packed per deepest-owner section and that within the edited section a length change can shift boundaries until the next accepted content anchor (`uint64(block_hash) mod anchor_modulus == 0` after `anchor_min_gap_tokens`) or the section's end; `CLAUDE.md:114-115` (Known gap 2) notes that long sections with few anchors can re-pack the rest of that section; `CLAUDE.md:152-153` requires covering a packing-boundary case. |
| **P2-1** (`CLAUDE.md:86`) | Reuse-input and migration inventory left independent invalidators unnamed | **Closed** | `CLAUDE.md:93-105` names the separate embedding, Selection, text-origin (`reuse_identity_hash`), and Claimify keys; `CLAUDE.md:132-139` lists `BLOCKIZER_VERSION`, `CHUNKER_VERSION` and `ChunkerParams`, `E0_CONVERT_VERSION` and converter routes, `E0_STRUCTURE_VERSION`, `SKELETON_PARSER_VERSION`, `REUSE_IDENTITY_VERSION`, `EXTRACTION_ELIGIBILITY_POLICY_VERSION`, `E2_EXTRACTOR_VERSION` (including `SECTION_ORIENTATION_VERSION`), `REFERENCE_POLICY_VERSION`, `EMBEDDING_INPUT_POLICY_VERSION`, `E1_EMBED_VERSION`, and the embedding model, noting each re-keys some stage. |
| **P2-2** (`CLAUDE.md:89`) | Conflated fabricated volatile noise with authoritative source content and genuine D134 renames | **Closed** | `CLAUDE.md:125-128` bans *fabricated volatile data in keyed inputs* while explicitly stating that a genuine rename of the title or file name is an intended boundary (D134) and real dates in the text stay. |
| **P2-3** (`CLAUDE.md:93`) | Omitted D56 prohibition on generated LLM context entering identity or drifting during reuse | **Closed** | `CLAUDE.md:129-131` adds **No model output in an identity (D56)** (summaries, prefixes, section paths, and offsets never enter a reuse key; committed derived context is carried forward); `CLAUDE.md:119-121` (Known gap 4) records model-based structure fallback and title-role classification nondeterminism. |
| **P2-4** (`CLAUDE.md:102`) | Missing stable lineage identity `(source_kind, source_ref)` (D55) vs. content-addressed one-shot upload | **Closed** | `CLAUDE.md:140-143` requires stable connector-native `(source_kind, source_ref)` (D55), forbids appending revisions/hashes to `source_ref`, and notes that one-shot uploads are byte-identified and reuse nothing when edited. |
| **P2-5** (`CLAUDE.md:97`) | Required regression test did not define a reliable per-stage oracle | **Closed** | `CLAUDE.md:146-154` specifies two versions of one lineage with distinct source dates and stable names, untouched sections before and after an edited multi-chunk section, per-stage provider call counts (Selection, Claimify, embedding), identity and `chunk_claims` assertions, zero embedding calls for unchanged chunks, packing-boundary and D122 window cases, and expected-failure markers for known gaps. |
| **P2-6** (`CLAUDE.md:75`) | Described cheap reconstruction as if chunk rows (`chunk_id`s) themselves were reused | **Closed** | `CLAUDE.md:83-86` explicitly states that E1 deterministically re-packs every new representation into new chunk rows with fresh `chunk_id`s and carries over content identities and the paid products keyed on them. |
| **Nits** (`CLAUDE.md:13, 84`) | Stale "three rules" count; ambiguous "document kind"; unlinked decisions/anchor predicate | **Closed** | `CLAUDE.md:13-14` updated to "four rules"; `CLAUDE.md:90-105` defines the anchor predicate, uses `source_kind`, and links `e1_chunks_design.md` and `document_reference_context_design.md`. |

### `REVIEW_rs507_agy.md` (Argon)

| Item | Summary | Status | Evidence in `c834b96f8` (`CLAUDE.md`) |
| --- | --- | --- | --- |
| **P0-1** (`CLAUDE.md:76-79, 97-101`) | Omitted D122's 8-chunk cross-section Claimify dependency and conflated embedding, Selection, and Claimify blast radii in prose and test spec | **Closed** | `CLAUDE.md:76-122` separates the owner requirement from current stage-by-stage mechanics (Embeddings, Selection, Claimify) and Known gap 1 (`CLAUDE.md:112-113`); `CLAUDE.md:146-154` rewrites the test specification per stage and marks known-gap collisions as expected-failure. |
| **P1-2** (`CLAUDE.md:77-78, 87-88`) | Misstated how section containment vs. content anchors (`D58`) bound chunk-boundary shifts | **Closed** | `CLAUDE.md:88-92` distinguishes per-deepest-owner-section packing from within-section drift up to the next accepted content anchor (`uint64(block_hash) mod anchor_modulus == 0` after `anchor_min_gap_tokens`) or section end, plus Known gap 2 (`CLAUDE.md:114-115`). |
| **P1-3** (`CLAUDE.md:83-87`) | Incomplete specification of `reuse_identity_hash`, `extraction_input_hash`, `claimify_input_hash`, D119 span remapping, and D80 embedding carry-forward | **Closed** | `CLAUDE.md:93-107` covers D80 carry-forward (`chunk_content_hash`, `embedding_text_hash`, policy generation, embedding model), Selection (`extraction_input_hash` and `reuse_identity_hash`), Claimify (`claimify_input_hash`), and D119 span remapping. |
| **P1-4** (`CLAUDE.md:89-104`) | Silent reuse killers not named (lineage identity vs. one-shot upload, D140 date consistency/order, intra-block whitespace in D119 remap, E0 fallback structuring/role classification, E0 summary cache key) | **Closed** | Covered across `CLAUDE.md:116-121` (Known gaps 3 and 4), `CLAUDE.md:140-145` (lineage identity, one-shot uploads, oldest-first ingestion, date consistency), and `CLAUDE.md:155-157` (exact-whitespace converter/connector output). |
| **P2-5** (`CLAUDE.md:13`) | Stale rule count ("three rules") and narrow scope ("When editing any of these") | **Closed** | `CLAUDE.md:13-14` now reads: *"When editing any of these, four rules are **non-negotiable** (Rule 4 also governs code, tests and reviews)."* |
| **P2-6** (`CLAUDE.md:93-96`) | Name the exact version constants and parameters that act as reuse boundaries | **Closed** | `CLAUDE.md:132-139` enumerates the concrete version constants and parameter sets. |
| **Nits 7 & 8** (`CLAUDE.md:84-85, 89-91`) | Align `header_title()` / `source_kind` field names; clarify heading renumbering impact | **Closed** | `CLAUDE.md:98` names `header_title()` and `source_kind`; `CLAUDE.md:95` notes `section_title` in `embedding_text_hash` and `CLAUDE.md:126` bans generated renumbering. |

---

## Round-2 Fresh Review

## P0

None.

## P1

None.

## P2

None.

## Nits

1. **`CLAUDE.md:101-102` — `reuse_identity_hash` also includes `REUSE_IDENTITY_VERSION` and `blockizer_version`.**
   - **File / lines:** `CLAUDE.md:101-102`
   - **Problem:** The parenthetical `(`reuse_identity_hash` is the same identity without the date)` slightly compresses `src/rememberstack/core/text_origin.py:38-48`, where `reuse_identity_hash` hashes `REUSE_IDENTITY_VERSION` and `blockizer_version` in addition to the undated `extraction_input_hash` fields (both constants are already listed under **Declare every reuse boundary** at `CLAUDE.md:134-136`).
   - **Concrete fix:** Optionally tweak the parenthetical to `(`reuse_identity_hash` is the same identity without the date, plus `REUSE_IDENTITY_VERSION` and `blockizer_version`)`.

Verdict: Approve
