# D140 design review round 2 — response

Review: [REVIEW_gpt-6-sol_d140_design_r2_2026-09-30.md](REVIEW_gpt-6-sol_d140_design_r2_2026-09-30.md)
(request changes; closure audit 13 closed / 6 partial; new: 0 P0, 8 P1, 5 P2, 1 nit). Each
finding was checked against `origin/main` 1dc23f21. "Design" means
`plan/designs/effective_time_and_section_references_design.md`; choices are argued in
`plan/analysis/version_effective_time_and_section_references.md` §9. The round's rule: the
simplest contract that is correct at scale — narrowing or rejecting before adding machinery.

| Finding | Resolution |
| --- | --- |
| P1-1 keyed ingest vs A→B→A / same-byte editions | One identity rule (design §2.3, §6.1): a `version_key` is assigned only at creation; a **new key always creates a version** (identical-byte editions become two versions sharing one content object); an existing key is accepted only as a retry of the latest version with the same bytes and is otherwise rejected (`409`, pointing to the period API). No aliases. Schema comment and D140 updated. |
| P1-2 back-filled older edition inherits a later date | Adopted the reviewer's rule: a match is eligible only if its `text_origin_at` is known and not later than the incoming version's date; an unknown incoming date disables inheritance; existing claims are never re-dated. Wording now says the date is a conservative estimate, not proof (design §5; D56 note; `e1_chunks_design.md` §7; lifecycle §4). |
| P1-3 `search_documents` result grain | Lineage grain kept; representative = latest-starting candidate (`versions: current`) or latest-starting matching candidate (`versions: all`), defined per mode in a table; every result lists `matching_editions` with version handles (design §3.5; `document_metadata_and_search_design.md` §3). |
| P1-4 gate after top-k | The gate is an eligibility predicate in every nomination channel and in confirmation, before `ORDER BY … LIMIT`, per D87; ranking and budgets unchanged (design §8.1; D140 item 3; plan WP-ET.3). |
| P1-5 supplied set cannot be restored | Supplied PUTs create generations; only a body equal to the active/pending generation is a no-op; any other body (including an older one) is a new generation reusing the stored artifact; one active generation per version and origin, swapped atomically; worker idempotent on `generation_id` (design §6.1, §6.3; schema). |
| P1-6 unreadable target reported resolved | Target versions are chosen by force/key first, then must be readable (`selectable`); otherwise `target_processing`, with no fallback to another version; applies to point, range and pinned joins (design §6.2). |
| P1-7 extracted identity and D65 | Extracted generations keyed by `(version_id, representation_id, crossref_version)`; rows store `from_representation_id`; activation happens with the D65 representation swap, superseding the previous extracted generation (design §6.4; `e0_files_design.md` §4A; schema). |
| P1-8 clear breaks belief-pinned reads | Mode transitions are ledger events (`document_effective_time_events`); periodised-at-`b` is evaluated from the ledger; the `documents` column is removed (design §2.1, §2.3–§2.4; schema; plan tests include clear/re-declare paging). |
| P2-1 pre-D140 sections reported absent | The backfill derives section keys along with hashes from stored heading blocks; unbackfilled versions report `not_indexed` / `section_not_indexed`, never `absent` (design §4.2, §6.2; plan migration). |
| P2-2 predicate lacks `believed_at` | `fact_in_scope_support(…, believed_at)`, default = evaluation instant, forwarded by graph helpers (design §8.1; `open_query_space_design.md`). |
| P2-3 no bounded query plan | `document_version_scope` projection maintained per lineage in the writing transaction; scoping is a primary-key probe per ranked candidate inside the ranked statement; GiST index for listings; ledger evaluation only for a page's candidate lineages; concrete verification target (design §2.2, §3.2; analysis §6; plan). |
| P2-4 sets above 64 MiB | Kept as an argued scope boundary: references are anchored in text, so 64 MiB (~200k references) exceeds what any supported document can anchor; staging rejected as machinery for an impossible case (design §6.3; analysis §9). |
| P2-5 stale assertion-time comments | `document_versions` comments now describe the `text_origin_at` path and the source-date fallback (schema §6). |
| Nit — non-amends dates | `CHECK (kind = 'amends' OR change_effective_from IS NULL)` added (design §6.1; schema). |

Disagreements: none. P2-4 is resolved by justifying the bound as a scope boundary rather than by
the staged protocol the reviewer offered first; the review allowed either.
