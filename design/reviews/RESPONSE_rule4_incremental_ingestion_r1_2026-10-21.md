# Response — Rule 4 (incremental ingestion), review round 1

Reviews: `REVIEW_gpt-6.1-sol_rule4_incremental_ingestion_r1_2026-10-21.md` (Sol) and
`REVIEW_antigravity-gemini-4-argon_rule4_incremental_ingestion_r1_2026-10-21.md` (Argon).
Both requested changes; the rule was rewritten after verifying each point in code.

| Finding | Resolution |
| --- | --- |
| Sol P1-1, Argon P0-1 — D122 Claimify window omitted; section-isolation claim false | Rule 4 now separates the owner **requirement** from **current behaviour**, names the Claimify key (Selection hash + previous eight chunks in document order + `REFERENCE_POLICY_VERSION`) and lists the cross-section window as known gap 1 (fix pending owner decision). |
| Sol P1-2, Argon P1-2 — anchor stabilization overstated | Packing described as per-section runs with drift inside the edited section until the next accepted anchor; long anchorless sections listed as known gap 2. |
| Argon P1-3, Sol P2-1 — keys incomplete | Embedding carry-forward key (content hash, rendered-input hash, policy generation, model), Selection key fields (`header_title()`, `source_kind`, text-origin date, `language`, `file_name`, eligibility policy), `reuse_identity_hash`, and span remapping are named; all re-keying constants are listed. |
| Sol P2-2 — conflated fabricated noise with real changes | Rule bans fabricated volatile data only; real renames (D134) and source dates are legitimate. |
| Sol P2-3 — model output in identity | Explicit D56 rule added. |
| Sol P2-4, Argon P1-4 (lineage, dated-ness, order) | Stable `(source_kind, source_ref)`, one-shot uploads never reuse, oldest-first one-at-a-time ingestion, consistent dated-ness. |
| Argon P1-4 (summary cache, fallback structuring, whitespace) | Summary cache and model-based structure/role nondeterminism listed as known gaps 3–4; exact-whitespace rendering required of converters. |
| Sol P2-5, Argon P0-1 test spec | Test spec rewritten per stage with call counts, `chunk_claims` reuse, zero embed calls, packing and D122 cases, expected-failure for known gaps; points to existing tests. |
| Sol P2-6 — chunk rows are re-created | Stated explicitly: rows are re-packed cheaply; content identities and paid products carry over. |
| Nits — rule count, `source_kind`, `header_title()`, navigation | Fixed; links to e1_chunks_design §4/§7 and document_reference_context_design. |
| Argon nit 8 — heading renumbering blast radius | Covered by the precise per-stage keys (section title is in the embedding input) rather than a separate paragraph, to keep the agreement short. |

Not adopted: listing every symbol's internal mechanics inline (Argon P1-3's long block) — the
agreement names the keys and links the binding design sections instead.
