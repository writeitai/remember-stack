# Temporal clocks — sequencing (D107)

**Status:** proposed sequencing; no work package is started by this document

**Binding design:** `plan/designs/temporal_clocks_design.md`

**Analysis:** `plan/analysis/time_handling_audit.md`

Order follows answer impact and dependency: as-of correctness first (it makes
the documented `valid_at` promise true), then the prompts that decide
supersession and identity, then retrieval deduplication, then extraction
precision and vocabulary. Each package rolls the generations it touches and
lands with tests that assert the *clock*, not only the outcome.

| WP | Scope | Audit findings closed | Generations rolled | Acceptance |
| --- | --- | --- | --- | --- |
| **WP-T.1 — fact windows and closing** | seed relation/observation windows from the claim's is-about window by kind; add `validity_basis`; widen only earlier; cap at the successor's world-time start, else said-on as upper bound, else coexist — never `now()`; one shared ordering comparator with undated-never-wins; apply to D55 retraction | 4.2, 4.4, 4.10, 4.11, 4.17 | normaliser, both adjudicators, obs flush component; schema head (migration) | `lookup_relations(valid_at=2010)` returns the 2010 employer on a seeded corpus; the same ingest replayed on different days yields byte-identical windows; the two adjudicators orient one undated/dated pair identically; no `now()` appears in any cap boundary |
| **WP-T.2 — two clocks in every temporal prompt** | relation supersession prompt and evidence laterals select and show `said on` / `is about`; T4 candidate salient facts carry windows and rank by evidence then window recency; K writer claims carry `asserted_at` + D41 fields; answer-agent prompt names the envelope fields | 4.3, 4.6, 4.8, 4.16 | relation adjudicator, resolver, K writer; LoCoMo protocol | a 2024 retrospective spell does not supersede a 2023 current fact in the supersession proofs; T4 sees windows in its candidate JSON; the answer prompt fixture contains the two-clock paragraph |
| **WP-T.3 — retrieval keys and envelopes** | testimony grouping keys include the D41 window; `Validity` gains `validity_basis`; P1 gains `valid_from`/`valid_until` filters; `aggregate(form="timeline")` buckets by `valid_from` with an `undated` bucket | 4.1, 4.9, 4.12 | query-space manifest / surface manifest hash; LoCoMo protocol | identical text on two dates is two evidence rows in `testimony_context`; a 2015–2020 archive imported today produces 2015–2020 timeline buckets |
| **WP-T.4 — extraction precision and vocabulary** | half-open precision-derived ends; full-timestamp header; all four kinds and `open` taught with examples and field descriptions; E3 statement carries the absolute date when known | 4.5, 4.13, 4.14, 4.15 | extractor, normaliser; LoCoMo protocol | `claims_as_of` over an intraday window finds a day-precision claim; "has been CEO since 2019" extracts as `proposition_validity`/`open`; an observation minted from "last week" carries the resolved date in its statement |
| **WP-T.5 — consumer labels** | K fact sheet columns by `validity_basis`; observation history sorted by fact window | 4.7 | K page generation | a sheet never prints a said-on date under a world-time heading |

Dependencies: T.2 and T.3 depend on T.1's `validity_basis` column for the
envelope and labels; T.4 is independent of T.1–T.3 and can run in parallel;
T.5 depends on T.1. Every package updates the same-PR documentation the
CLAUDE.md rule requires (concepts and API pages for `valid_at`, the benchmark
README when the protocol rolls) and the project-status page.
