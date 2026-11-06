# D140 design review round 1 — response

Review: [REVIEW_gpt-6-sol_d140_design_r1_2026-09-30.md](REVIEW_gpt-6-sol_d140_design_r1_2026-09-30.md)
(verdict: request changes; 0 P0, 12 P1, 7 P2, 2 nits). Every finding was checked against
`origin/main` 1dc23f21 before changing the corpus. "Design" below means
`plan/designs/effective_time_and_section_references_design.md`; the reasoning behind each
choice is in `plan/analysis/version_effective_time_and_section_references.md` §8.

| Finding | Verified | Resolution |
| --- | --- | --- |
| P1-1 served version / P3 handle | yes (`document_catalog.py:642-666`) | `current_version_id` is now described as the served version everywhere (design §1, §2.4; D55 note; `evidence_lifecycle_design.md` §2; schema comment). Every scoped result carries version, representation and `source_open(version_id)` handles; `p3_path` is returned only when it opens the selected version (`served_version` flag) and becomes optional (design §3.3; `document_metadata_and_search_design.md` §3). |
| P1-2 pinned address is a mutable cursor | yes (`_ADVANCE_VERSION_CURSOR`) | New immutable, per-lineage-unique `document_versions.version_key` supplied at ingest; pinned references name `to_version_key`; `source_version_ref` stays the cursor. Deletion yields `pinned_version_unavailable` (design §1, §2.1, §2.3, §6.1, §9; schema §6). |
| P1-3 floating resolution for ranges/overlaps | — | Temporal join between each source window and target intervals; one row per target version with `applies_during`, `concurrent` flag for overlaps, point queries return all concurrent targets; defined order and keyset paging; `chunk_id` pins its source version and its default window (design §6.2). |
| P1-4 facts from text not in force | yes (D54 snapshot currency) | Deterministic read-time **evidence gate** on all fact reads under a time scope, plus `fact_in_scope_support`; D118 stays the window authority; conflicting claim dates handled (both conditions must hold). Delivery: WP-ET.3 must ship with WP-ET.2 in #500; the E3 input stays a separate PR and is no longer needed for correct defaults (design §8.1; `mutable_fact_windows_design.md` §3; plan). |
| P1-5 reused claim cites origin | yes (`_confirm_claims`, origin evidence) | Scoped claims return their occurrence in the selected version with per-occurrence spans/locators; `effective` describes that occurrence; hydration never goes through the origin (design §3.4; refines D134 §4 for this case). |
| P1-6 ownership FKs | yes | Composite FKs `(deployment_id, doc_id, version_id)` on periods, reference sets and crossrefs, and `(deployment_id, version_id, reference_set_id)` from crossrefs to sets; write-path checks (design §2.1, §6.1; schema §6). |
| P1-7 soft delete, not cascade | yes (`_TOMBSTONE_VERSION`) | No cascades; D135 is a visibility rule (reads join non-deleted versions/lineages), ledger rows remain as history; D74 hard forget deletes/unbinds explicitly (design §9; schema comments). |
| P1-8 last-period retraction resurrects text | — | Sticky `documents.effective_time = declared`; only `DELETE …/effective-periods` (`clear_effective_time`) returns a lineage to served-version semantics (design §2.3–§2.4). |
| P1-9 text-origin identity | yes (`e1.py:629-660`) | Stored, indexed `reuse_identity_hash` (includes blockizer/structurer/extractor versions); deterministic order `(text_origin_at, version_no, ordinal)`; replaces both date facts; recorded once, immutable, replayed; deletion of the origin later does not change it; new chunks never draw on deleted versions (design §5; D56 note; `e1_chunks_design.md` §7; `evidence_lifecycle_design.md`). |
| P1-10 header date vs `asserted_at` | yes (`e2.py:1293-1296`) | One per-chunk date for the reuse key, E2 header and fresh claims' `asserted_at`; D55's source-time rule refined accordingly (design §5; D56 note; lifecycle §2). |
| P1-11 section `changed` misses children | yes (`structure_skeleton.py:111-118`) | `subtree_content_hash` drives `changed`, `own_content_hash` drives `own_changed`; history ordered by effective start; explicit `absent` rows (design §4.2–§4.3, §6.2). |
| P1-12 A→B→A | partly: the catalog does create a new version, and migration `p3_01_0008` already dropped the per-lineage uniqueness; only the schema design was stale | Schema design corrected; D140 no longer assumes one version per content in a lineage; a text back in force is either a new version (re-ingest) or a new declaration on an old version (design §1, §2.1; D55 note). |
| P2-1 readiness and freshness | yes (`Freshness` fields) | Derivation (`effective_intervals`) ignores status; selection (`versions_in_scope`) requires ready; new `Freshness.scope_pending` names pending lineages (design §2.2, §3.2, §3.7). |
| P2-2 `versions=all` and paging | — | For periodised lineages `versions: all` means all versions within the scope; paging pins `as_of` as the declaration belief instant (design §3.5–§3.6; `document_metadata_and_search_design.md` §3). |
| P2-3 exhausting references; supply limit | — | Keyset cursor with pinned instants, defined order, index shapes for incoming/outgoing and descendant keys (bounded, `too_broad` negative); supplied sets are one NDJSON request bounded by bytes, complete-set replacement (design §6.2–§6.3). |
| P2-4 invalid source section broadened | — | Whole set rejected with item-level errors; previous set stays live; status readable via `GET …/references` (design §6.3; `e0_files_design.md` §4A). |
| P2-5 visibility of unresolved rows | — | Operations read private tables through a fixed authorization-checked query; `target_unavailable` covers never-ingested, deleted and forgotten targets; source-named target returned only as source content of a live version; public view unchanged (design §6.2, §6.5). |
| P2-6 plan and migration | yes | Plan states #500 holds design only and implementation commits are added to #500 after design approval (owner decision); migration section: nullable hashes + deterministic backfill, parser-generation rollover, guarded `document_crossrefs` recreation (plan). |
| P2-7 `amends` without a date | — | `change_date_known` required for `amends`, date required when known and forbidden when unknown (design §6.1; schema §6). |
| Nit 1 `from`/`to` in SQL | — | SQL functions use `range_start`/`range_end`; the wire scope keeps `from`/`to` (design §3.2, §7; `open_query_space_design.md`). |
| Nit 2 "bounded fan-out" | — | Rationale stated directly: no section-grain traversal need; paging and work bounds are explicit (design §7). |

No finding was rejected. P1-12 was already true on `main` in code; the fix was to the stale
schema design and to D140's own assumption.
