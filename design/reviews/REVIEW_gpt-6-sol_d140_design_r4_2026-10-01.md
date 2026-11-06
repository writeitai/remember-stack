# D140 design review — round 4

Compared `git diff origin/main...HEAD` at `d7e4173cd8c6493cd3550192035e077b69760df4` (merge base `1dc23f21`), the complete changed planning files, the relevant `origin/main` migrations/workers/read paths, and the round-1–3 reviews and responses. This is a design review; implementation in #500 after approval follows the owner's instruction. The D140 mechanisms remain general-purpose: the statute book supplies identities, keys, periods and taxonomy labels, while the engine does not encode legal vocabulary or amendment replay.

## Closure audit

### Every round-3 finding

| Round-3 finding | Status | Evidence |
| --- | --- | --- |
| P1-1 — pending supplied set can override a later PUT of active A | **Closed** | `plan/designs/effective_time_and_section_references_design.md:757-777` orders PUTs under the version lock, cancels pending B on PUT A, and makes the worker recheck pending status under that lock. The schema has per-version `request_seq` (`postgres_schema_design.md:1454-1470`). |
| P1-2 — historical page candidates lost from the current-only projection | **Closed** | `effective_time_and_section_references_design.md:318-322,390-405` walks lineage candidates in a belief-independent order and evaluates ledgers at the pinned belief instant. `versions_in_scope` requires `doc_ids` for a past `believed_at` (`:300-306`; `open_query_space_design.md:629`). The adjacent D134 order wording still needs reconciliation (P2-1). |
| P1-3 — section-key uniqueness blocks another structure generation | **Closed** | `effective_time_and_section_references_design.md:448-455` and `postgres_schema_design.md:1355,1373,1382` use `structure_generation_id`; key reads select the current representation's current generation. This matches `origin/main` migration `p1_04_0019_d79_structure_generations.py:155-165` and `document_catalog.py:973-985`. Two adjacent summaries still say “per version” (P2-2). |
| P2-1 — decision rejects the allowed keyed retry | **Closed** | `decisions.md:6754-6757` allows the latest version's same-byte retry and rejects other reuse, matching design `:195-208`. |
| P2-2 — schema comments misstate the date fallback and version identity | **Closed** | `postgres_schema_design.md:1143-1155,1165,1175,1182-1183` describes `source_modified_at` then `published_at`, latest-version byte no-op, new-key same-byte edition and A→B→A. A separate stale claims column comment remains (P2-3). |
| P2-3 — D140 says main reads the most recently ingested version | **Closed** | `decisions.md:6734-6741` now says newest served/ready. `origin/main` `document_catalog.py:642-666` moves the pointer only after processing completes. |
| P2-4 — 64 MiB supplied-set limit lacks evidence | **Closed** | `plan/analysis/version_effective_time_and_section_references.md:389-427` records the corpus, serialization method and 4,158,303-byte observed maximum; design `:741-752` explains the 64 MiB bound and alternative. |
| P2-5 — public view says it reveals the pinned target version | **Closed** | `open_query_space_design.md:450` lists the target lineage/section and excludes source-named target identity; design `:805-811` agrees. |
| P2-6 — decision contains PR sequencing | **Closed** | `decisions.md:6811-6823` states architectural consequences and points to the delivery plan; PR order is in `plan/plans/effective_time_and_section_references.md:6-21`. |
| Nit — malformed supplied-reference sentence | **Closed** | `e0_files_design.md:603-608` separates artifact storage and validation. |

### Earlier items still marked partially closed in round 3

| Earlier finding | Status | Evidence |
| --- | --- | --- |
| Round 2 P1-1 / round 1 P1-2 — immutable version-key identity and retry | **Closed** | Design `:195-208`, decision `:6754-6757` and schema `:1175,1187` now agree. |
| Round 2 P1-5 — supplied A→B→A while B is pending | **Closed** | Design `:757-777` gives PUT A precedence over pending B and guards activation. |
| Round 2 P1-8 — clear breaks a belief-pinned page | **Closed** | Mode-event ledger `:116-121,223-242`; ledger-based candidate evaluation `:318-322,390-405`. |
| Round 2 P2-1 — pre-D140 missing key misreported as absence | **Closed** | Backfill and `not_indexed` contract `:448-464,727-735`; schema key uniqueness is per generation at `postgres_schema_design.md:1382`. |
| Round 2 P2-3 / round 1 P2-2 — bounded historical candidate access and `versions: all` paging | **Closed** | `:300-325,368-405` defines bounded candidate batches, representative/matching editions and cursor clocks. See P2-1 for the adjacent wording. |
| Round 2 P2-4 / round 1 P2-3 — supplied-set size and replacement | **Closed** | Measured bound `:741-752`; latest-intent generations `:757-782`; keyset paging `:722-725`. |
| Round 2 P2-5 — stale schema source-time comment | **Closed** | `postgres_schema_design.md:1148-1165` uses the `source_modified_at`/`published_at` fallback. |

## P0

None.

## P1

None.

## P2

1. **`plan/designs/document_metadata_and_search_design.md:156-161` — filter-only order still says the judged version's ingest time.** For a periodised lineage, the judged edition can change after a correction; that order contradicts D140's stable lineage order (`effective_time_and_section_references_design.md:395-405`) and would reintroduce the round-3 paging bug if implemented literally. **Fix:** refine D134 §3 to order by the lineage's newest version ingested by the pinned `as_of`, independently of the edition judged under the scope; keep the representative edition separate from the cursor key.

2. **`plan/designs/e0_files_design.md:378-379` and `plan/plans/effective_time_and_section_references.md:17` — both still say section keys are unique “per version.”** D140 and the schema correctly use a structure generation, since one version can have several (`effective_time_and_section_references_design.md:448-455`; `postgres_schema_design.md:1382`). **Fix:** change both summaries to “per structure generation,” with reads through the selected current generation.

3. **`plan/designs/postgres_schema_design.md:1695,2871` — two old schema annotations survived the D140 rewrite.** `claims.asserted_at` still describes the version's own timestamp, and the D36 mapping names `document_crossrefs.crossref_version`, which the new table no longer has (it belongs to `document_reference_generations`). **Fix:** point the claim comment to immutable `chunks.text_origin_at` and the mapping to `document_reference_generations.crossref_version`.

4. **`plan/designs/effective_time_and_section_references_design.md:172-173` — the public period view is said to expose projection rows, but the projection has one version/multirange row and no `period_id`.** The manifest gives `document_effective_periods_live` period grain and a derived end (`open_query_space_design.md:433`). **Fix:** say the view derives its current rows from live declaration rows (or `effective_intervals`) and uses the projection only for version-scope selection.

5. **`plan/designs/effective_time_and_section_references_design.md:850-873` — the stated fact-gate probe uses only the current projection even though the public helper accepts a past `believed_at`.** After a retroactive correction, a literal `EXISTS (... document_version_scope)` cannot reproduce the support set at the earlier belief instant. **Fix:** for a supplied past belief, collect the fact's bounded support lineages and use `versions_in_scope(..., believed_at, doc_ids)`/the ledgers; keep the projection probe for current-belief calls. This is a query-plan clarification for the already specified historical semantics.

## Nits

1. **`plan/analysis/version_effective_time_and_section_references.md:319-329` — the cost section still says reference row counts “must be measured” and describes a GiST listing scan.** §10 now measures the row count, and the revised filter-only listing plan walks the stable lineage order. **Fix:** update the non-binding analysis summary so a cold reader sees the measured count and the chosen access path.

Verdict: Approve
