# Review: writeitai/remember-stack #507 — round 2

## CLOSURE AUDIT

Fetched `origin` and reviewed `git diff origin/main...origin/docs/incremental-ingestion-rule`, head `c834b96f88e718ffce494f6e3b8ec1b66acda371`. Locations below refer to that head; prior finding identifiers refer to the two supplied round-1 reviews. Implementation paths are under `src/rememberstack/` unless otherwise stated.

The separation between the binding owner requirement, current implementation, and explicitly unfixed gaps is sound. Recording those gaps does not require implementing their solutions in this documentation PR. No P0/P1 issue remains; the partially closed items below have only P2 residuals.

### REVIEW_rs507_sol.md

Round 1 reported no P0 findings.

1. **P1-1 — D122 omitted / false section isolation: Closed.** `CLAUDE.md:97–105` separates Selection and Claimify identities, including the preceding eight document-order Selection hashes and `REFERENCE_POLICY_VERSION`; `CLAUDE.md:112–113` explicitly identifies cross-section Claimify invalidation as an unfixed gap. `CLAUDE.md:150–154` requires separate stage call counts and a D122 case. This matches `workers/e2.py:574–582`, `workers/extraction_references.py:54–61`, and D122. The example at `CLAUDE.md:76–79` is now clearly a requirement, not a claim about shipped isolation.

2. **P1-2 — Fixed invalidation radius / overstated anchors: Closed.** `CLAUDE.md:88–92` explains independent section packing and within-section drift to an accepted anchor or the section end; `CLAUDE.md:114–115` acknowledges that the whole remainder can repack. The former three-chunk guarantee is gone. This agrees with `core/chunker.py:181–238`, D58, and `plan/designs/e1_chunks_design.md:227–239`, assuming section ownership stays unchanged (structure drift is separately acknowledged at `CLAUDE.md:119–121`).

3. **P2-1 — Incomplete identities and migration inventory: Partially closed — remaining P2.** `CLAUDE.md:93–105` now names the vector, Selection, origin, and Claimify identities, and `CLAUDE.md:132–139` names the previously omitted converter, blockizer, origin, eligibility, reference, and embedding generations and requires a stage-specific migration statement. **Remaining problem (`CLAUDE.md:101–102`):** `reuse_identity_hash` is not merely the Selection identity without its date. `core/text_origin.py:38–47` additionally hashes `REUSE_IDENTITY_VERSION` and `blockizer_version`; `workers/e1.py:648–653` supplies the own `chunk_content_hash` and ordered previous/next chunk-content hashes. Those extra inputs can prevent origin inheritance even when the undated Selection inputs are unchanged. **Concrete fix:** replace the parenthetical with “the date-free Selection inputs plus `REUSE_IDENTITY_VERSION` and `blockizer_version`”; identify the own input as `chunk_content_hash` (the hash of ordered block hashes), with `""` for an absent neighbour. The declaration checklist mitigates the omission, so this is not a blocker.

4. **P2-2 — Fabricated noise confused with real source changes: Closed.** `CLAUDE.md:125–128` limits the prohibition to fabricated volatility, preserves real source dates, and recognizes genuine title/file-name changes as D134 boundaries. It removes the inaccurate claim that any local decoration changes every key. This agrees with `model/chunks.py:75–85`, `workers/e1.py:725–748`, and D134.

5. **P2-3 — Generated context can destroy reuse: Closed.** `CLAUDE.md:129–131` expressly prohibits generated context in identity and requires committed context to carry forward. `CLAUDE.md:119–121` honestly identifies fallback structure/role drift as an implementation gap rather than asserting replay already solves it. This reflects D56 and `plan/designs/e1_chunks_design.md:332–362`; the current model calls are in `workers/e0.py:1243–1308` and `:1404–1453`.

6. **P2-4 — Stable lineage missing: Closed.** `CLAUDE.md:140–145` requires stable `(source_kind, source_ref)`, prohibits revision/hash suffixes, explains byte-identified one-shot uploads, and addresses ingestion order and date consistency. `CLAUDE.md:148` requires two versions of one lineage. This matches D55, `workers/e0.py:242–253`, `:304–323`, and the D140 eligibility/query rules in `core/text_origin.py:52–83` and `spine/chunk_catalog.py:331–345`.

7. **P2-5 — Regression oracle unreliable: Partially closed — remaining P2.** `CLAUDE.md:146–154` now specifies dated same-lineage versions, stable names, untouched surrounding sections, a multi-chunk edited section, provider counts by stage, occurrence claim IDs, packing and D122 cases, and named expected failures for acknowledged gaps. **Remaining problem (`CLAUDE.md:150–154`):** “unchanged chunks keep their ... Selection and text-origin identities” is unqualified. A chunk with unchanged body beside the edit intentionally changes both identities because its neighbour changed; this is D56 behavior, not one of the four listed gaps. The direct E1 reproduction confirmed this for both immediate neighbours of a single changed chunk with fixed packing. **Concrete fix:** distinguish unchanged body/embedding inputs from unchanged complete Selection/origin inputs. Require no embed calls for unchanged embedding identities; assert Selection/origin preservation only for chunks with unchanged local inputs, and same claim IDs only when the Claimify identity and span remap also match. Retain the explicit expected failures for violations of the stronger owner requirement. Also assert `claimify_input_hash` and remapped evidence spans, including a zero-card producer case, to make the stage oracle unambiguous.

8. **P2-6 — Chunk occurrences confused with reusable products: Closed.** `CLAUDE.md:83–86` explicitly describes full deterministic repacking, fresh occurrence `chunk_id`s, and reuse of content identities and paid products. This matches `workers/e1.py:138–175`, `:681–705`; it no longer claims that unchanged chunk rows themselves are reused.

### REVIEW_rs507_agy.md

1. **P0-1 — Conflated embedding/Selection/Claimify scopes and broken test: Partially closed — remaining P2 only.** `CLAUDE.md:93–107` correctly separates the three stages, states that neighbours are absent from the vector key, and includes span remapping. `CLAUDE.md:109–121` labels the actual locality violations as gaps; `CLAUDE.md:146–154` replaces the blanket three-chunk test with stage counts and explicit gap cases. The P0 technical conflation is resolved. Only the narrower test wording problem recorded above under Sol P2-5 remains; apply that concrete fix.

2. **P1-2 — Section containment versus anchors: Closed.** `CLAUDE.md:88–92` and `:114–115` distinguish section isolation from unbounded within-section drift. Whole-representation repacking is explicit at `:83–86`. Evidence and the stable-ownership qualification are the same as Sol P1-2 above; no packing implementation change is required here.

3. **P1-3 — Incomplete reuse keys, remapping, and D80 carry-forward: Partially closed — remaining P2 only.** The substantive missing stages, vector policy/model requirements, eligibility input, and remapping precondition are now present at `CLAUDE.md:93–107`; the migration symbols are named at `:132–139`. The origin-key formula still needs the small correction under Sol P2-1. **Additional remaining completeness problem (`CLAUDE.md:94–95`):** the rendered vector input depends on more than the three listed title/role fields. `_location_facts` supplies `source_shape`, `chunk_count`, and message coordinates (`workers/e1.py:624–638`); mode selection uses shape/count (`core/embedding_input_policy.py:249–274`), and rendering can include channel, author, and message timestamp (`:303–335`). **Concrete fix:** add a short sentence naming those mode/metadata dependencies and link `plan/designs/e1_embedding_input_policy.md`. The composite rendered-input hash itself is correctly stated, so the remaining omission is P2 rather than P1.

4. **P1-4 — Five silent reuse killers: Closed.** All five are addressed: (a) stable lineage and one-shot uploads, `CLAUDE.md:140–143`; (b) dated/undated and chronological ingestion, `:143–145`; (c) raw-whitespace/remapping sensitivity, `:106–107` and `:155–157`; (d) fallback/role nondeterminism, gap 4 at `:119–121`; (e) summary keys containing ordinals/paths, gap 3 at `:116–118`. The latter two are valid disclosures of unfixed behavior. Evidence: `core/source_passages.py:265–305`, `workers/e0.py:1243–1308`, `:1404–1453`, and `workers/e0_summary.py:761–816`, alongside the lineage/date evidence above.

5. **P2-5 — Rule count and scope: Closed.** `CLAUDE.md:13–14` now says four rules and explicitly extends Rule 4 to code, tests, and reviews.

6. **P2-6 — Exact reuse-boundary constants: Partially closed — remaining P2.** `CLAUDE.md:132–139` includes the requested packing parameters, origin, eligibility, reference policy, and indirect `SECTION_ORIENTATION_VERSION` dependency. **Remaining problem (`CLAUDE.md:139`):** `E3_NORMALIZER_VERSION` is still absent. Reused claim IDs do not avoid a new normalization model call when that generation changes: `workers/e3.py:326–343` looks up the frozen normalization by `(claim_id, normalizer_version)` before calling the model. **Concrete fix:** add `E3_NORMALIZER_VERSION`, explicitly distinguishing downstream normalization invalidation from Selection/Claimify/vector invalidation. This was already a round-1 P2, not a new blocker.

The round-1 nits are also closed: count/scope at `CLAUDE.md:13–14`, precise `header_title()`/`source_kind` at `:97–100`, anchor explanation/navigation at `:86–92`, and removal of the incorrect universal renumbering blast-radius claim at `:125–128`. `AGENTS.md:3–5` remains an accurate pointer to the working agreement.

## Fresh review — new findings only

### P0

None.

### P1

None. The owner requirement is clearly binding, and the implementation shortfalls are explicitly unsolved. They do not block this documentation PR.

### P2

No additional findings. The residual documentation corrections are recorded with locations, evidence, and concrete fixes in the closure audit; they are not re-filed as new findings.

### Nits

None additional. The rewritten requirement/mechanism/gaps/checklist structure is understandable without the earlier discussion and is consistent with the surrounding working agreement.

Validation: 28 existing core tests passed across chunk packing, embedding-input policy, and D122 reference identity. A direct check using the actual E1 key builders confirmed neighbour-sensitive Selection/origin invalidation and the additional blockizer dependency of the origin identity. The attempted worker-test collection was blocked by the local environment (`boto3` missing and a conflicting installed `tests` package); PostgreSQL integration tests were inspected, not executed. No tracked files were modified; no commit or push was performed.

Verdict: Approve
