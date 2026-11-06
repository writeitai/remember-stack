# D140 design review — round 3

Compared `origin/main...HEAD` at `9269748deded8688d6d48f4f555afd171f923e11` (merge base `1dc23f21`), including the changed corpus and the relevant schema, migrations, catalog, retrieval, lifecycle and D65 design on `origin/main`. This is a design review. The owner's instruction to add implementation to #500 after design approval is accepted.

## Closure audit

### Round 2

| Finding | Status | Evidence |
| --- | --- | --- |
| P1-1 — version-key identity, A→B→A and same-byte editions | **Partially closed** | The operational rule in `plan/designs/effective_time_and_section_references_design.md:195-208` handles all three cases. `decisions.md:6754-6756` instead rejects an existing key on *any* later observation, including the latest-version same-byte retry allowed by the design. See P2-1. |
| P1-2 — older back-filled edition inherits a later assertion date | **Closed** | `plan/designs/effective_time_and_section_references_design.md:478-509` permits inheritance only from a known date no later than the incoming version and leaves existing claims immutable. |
| P1-3 — `search_documents` result grain with several editions | **Closed** | `plan/designs/effective_time_and_section_references_design.md:363-385` defines lineage grain, representative, `matching_editions` and both `versions` settings; `plan/designs/document_metadata_and_search_design.md:129-144` agrees. |
| P1-4 — fact evidence gate after top-k | **Closed** | `plan/designs/effective_time_and_section_references_design.md:808-825` puts support eligibility in every nomination channel and confirmation before each relevance bound. |
| P1-5 — supplied A→B→A set cannot be restored | **Partially closed** | Generations allow a superseded A to return (`plan/designs/effective_time_and_section_references_design.md:730-744`), but an active A plus pending B makes a new PUT of A a no-op; B still activates. See P1-1. |
| P1-6 — unreadable target reported resolved | **Closed** | `plan/designs/effective_time_and_section_references_design.md:658-685` selects the target by force/key first, then tests readiness, returning `target_processing` without fallback. |
| P1-7 — extracted identity and D65 coordinate swap | **Closed** | `plan/designs/effective_time_and_section_references_design.md:753-768` keys extraction by version, representation and crossreferencer, stores the representation coordinate and swaps active generations with the D65 pointer. |
| P1-8 — clear breaks belief-pinned reads | **Partially closed** | The mode-event ledger at `plan/designs/effective_time_and_section_references_design.md:116-121,223-242` reconstructs clear/redeclare, but page candidates are still drawn from a current-only scope projection (`:316-319`). See P1-2. |
| P2-1 — pre-D140 section absence falsely called repeal | **Partially closed** | `plan/designs/effective_time_and_section_references_design.md:436-443,703-710` backfills keys and distinguishes `not_indexed`. The proposed per-version unique key prevents the backfill or a second structure generation from storing the same key; see P1-3. |
| P2-2 — fact-scope predicate lacks belief instant | **Closed** | `plan/designs/effective_time_and_section_references_design.md:834-839` and `plan/designs/open_query_space_design.md:631` add and forward `believed_at`. |
| P2-3 — no bounded scope query plan | **Partially closed** | `plan/designs/effective_time_and_section_references_design.md:142-170,306-325` adds a maintained projection and candidate probes for current belief. Its belief-pinned listing path loses candidates after corrections; see P1-2. |
| P2-4 — reference sets above 64 MiB | **Partially closed** | `plan/designs/effective_time_and_section_references_design.md:717-726` argues a bound, but its claim that supported documents cannot exceed it rests on per-version corpus counts expressly “to be confirmed” and assumes only a few hundred bytes per NDJSON item. See P2-4. |
| P2-5 — stale assertion-time schema comments | **Partially closed** | `plan/designs/postgres_schema_design.md:1145-1162` names `text_origin_at`, but still says the fallback is `source_modified_at` alone. `plan/designs/effective_time_and_section_references_design.md:480-489` specifies `source_modified_at or published_at`. See P2-2. |
| Nit — date on non-`amends` row | **Closed** | `plan/designs/effective_time_and_section_references_design.md:583-587` forbids it. |

### Round-1 findings marked partially closed in round 2

| Finding | Status | Evidence |
| --- | --- | --- |
| P1-2 — mutable cursor as pinned address | **Partially closed** | Immutable `version_key` and latest-key retry rule are at `plan/designs/effective_time_and_section_references_design.md:42-45,195-208`; the contradictory decision wording at `decisions.md:6754-6756` remains (P2-1). |
| P1-3 — floating range and overlap resolution | **Closed** | `plan/designs/effective_time_and_section_references_design.md:651-685` defines the temporal join, concurrency, pinned behavior and readiness statuses. |
| P1-4 — facts supported only by out-of-force text | **Closed** | `plan/designs/effective_time_and_section_references_design.md:808-839` makes the evidence gate deterministic and pre-limit; `plan/plans/effective_time_and_section_references.md:15-16` couples its delivery to text retrieval. |
| P1-9 — reproducible text-origin identity | **Closed** | `plan/designs/effective_time_and_section_references_design.md:472-500` stores an indexed identity, a date eligibility rule, an immutable date and a deletion rule. |
| P1-12 — A→B→A and content uniqueness | **Closed** | `plan/designs/effective_time_and_section_references_design.md:32-37,195-208` and `plan/designs/postgres_schema_design.md:1171-1183` preserve a new observation without per-lineage content uniqueness. |
| P2-2 — scoped `versions: all` and belief-pinned paging | **Partially closed** | `plan/designs/effective_time_and_section_references_design.md:363-385` fixes `versions: all` and records the belief instant, but the candidate access path at `:316-319` cannot enumerate an earlier scope after a correction (P1-2). |
| P2-3 — reference exhaustion and supplied-set replacement | **Partially closed** | `plan/designs/effective_time_and_section_references_design.md:698-701` supplies keyset paging; `:730-744` allows completed A→B→A but loses a later A request while B is pending (P1-1), and `:717-726` still has the unsupported size-bound assumption (P2-4). |

## P0

None.

## P1

1. **`plan/designs/effective_time_and_section_references_design.md:730-744` — a later supplied-set PUT can be ignored while an older replacement is pending.** Suppose A is active, B is pending, then the caller PUTs A to cancel B. The rule treats A's body hash as an idempotent retry of the *active* generation and does nothing. E0 subsequently activates B, contrary to the latest complete-set replacement request. This persists even though completed A→B→A now works. **Fix:** make pending intent take precedence: only a retry of the latest pending body is a no-op while one exists; a PUT matching active A must supersede pending B (or create a new A generation) atomically. Specify ordering under concurrent PUTs and the worker's check that its generation is still latest before activation.

2. **`plan/designs/effective_time_and_section_references_design.md:316-319` — belief-pinned pages cannot enumerate versions removed from the current scope projection.** The GiST scan is over `document_version_scope`, which `:142-165` stores only *current* belief. If page 1 pins belief B while edition X is in force, and a retroactive correction or clear removes X before page 2, the current index no longer yields that lineage as a candidate. Running `effective_intervals(..., B)` only on the page's candidates cannot restore it. A paged `search_documents` result can skip rows despite the promise at `:382-390`; this is the remaining part of round-2 P1-8/P2-3. **Fix:** obtain page candidates from a belief-independent stable order (for example the document/version metadata order) and evaluate the ledgers at B before applying scope and the keyset limit, or maintain an indexed historical candidate path. Make the public `versions_in_scope` contract consistent with that access path.

3. **`plan/designs/postgres_schema_design.md:1377` — section-key uniqueness has the wrong grain and blocks D65/D79 reprocessing.** The new unique index is `(version_id, section_key)`. On `origin/main`, migration `src/rememberstack/spine/migrations/versions/p1_04_0019_d79_structure_generations.py:155-165` deliberately removed per-version path uniqueness because one version can hold multiple structure generations; `document_catalog.py:973-985` writes sections per `structure_generation_id`. Reprocessing the same version with a new representation/generation will legitimately produce the same stable key twice, so the new index fails; backfilling old generations can fail too. **Fix:** enforce key uniqueness within `structure_generation_id` (and deployment), with reads resolving keys only in the selected version's current representation/current structure generation. Update `plan/designs/effective_time_and_section_references_design.md:432-443` and the schema's stale `UNIQUE (version_id, node_path)` sketch at `:1368` accordingly.

## P2

1. **`decisions.md:6754-6756` — the decision log contradicts the binding retry rule.** It rejects an existing `version_key` on *any* later observation, while `plan/designs/effective_time_and_section_references_design.md:201-206` accepts the latest version's same-byte keyed retry. **Fix:** state that precise retry exception inside D140; reject reuse on a different observation.

2. **`plan/designs/postgres_schema_design.md:1145-1162` — schema comments still misstate source-date fallback and version identity.** D140 takes `source_modified_at or published_at` (`plan/designs/effective_time_and_section_references_design.md:480-489`), so new text with no modification date can use publication time. The comment also says unchanged bytes never create a row and describes one row per lineage/content, while a new `version_key` creates a same-byte edition (`:195-208`). **Fix:** describe both date fallbacks and the keyed identical-byte exception; make `document_versions` comments match D140.

3. **`decisions.md:6735-6740` — D140's current-state claim is wrong.** It says `main` reads the “most recently ingested version”; `origin/main` moves `current_version_id` only when processing completes (`src/rememberstack/spine/document_catalog.py:642-666`), and its read view uses that served pointer (`p9_04_0025_coordinate_binding.py:191-197`). **Fix:** say “newest served/ready version”; the analysis and `plan/designs/effective_time_and_section_references_design.md:38-41` already use the correct term.

4. **`plan/designs/effective_time_and_section_references_design.md:717-726` — the 64 MiB complete-set boundary is not yet supported by its evidence.** The only PUT rejects larger sets, while “at most thousands” is explicitly unmeasured and NDJSON items can exceed the assumed few hundred bytes. This is a follow-up scale risk, not a reason by itself to reject the design. **Fix:** measure per-version count and serialized size on the motivating corpus and put a defensible accepted-content bound in the contract, or provide a bounded staged complete-set route if supported inputs can exceed 64 MiB.

5. **`plan/designs/open_query_space_design.md:450` — public-view columns disagree with the privacy rule.** The manifest table says `document_crossrefs_live` exposes the “pinned target version,” while `plan/designs/effective_time_and_section_references_design.md:770-776,789-796` excludes `to_version_key` and names no resolved target-version column. **Fix:** remove that phrase or specify an allowed column without disclosing the source-named pinned identity.

6. **`decisions.md:6818-6821` — delivery sequencing appears in the decision instead of the plan.** The sentence assigning D36 extraction and E3 input to separate PRs conflicts with `CLAUDE.md` Rule 2 and the design-corpus skill's source roles. **Fix:** keep the full architectural consequence here and leave PR order solely in `plan/plans/effective_time_and_section_references.md`.

## Nits

1. **`plan/designs/e0_files_design.md:603-606` — the supplied-reference sentence has two predicates joined without punctuation.** **Fix:** split “stored as an artifact” and “validated against the version's structure” into separate sentences.

Verdict: Request changes
