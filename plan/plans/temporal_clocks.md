# Temporal clocks — sequencing (D107)

**Status:** proposed sequencing; no work package is started by this document

**Binding design:** `plan/designs/temporal_clocks_design.md`

**Analysis:** `plan/analysis/time_handling_audit.md`

Order follows answer impact and dependency: as-of correctness first (it makes
the documented `valid_at` promise true), then the prompts that decide
supersession and identity, then retrieval keys and envelopes, then extraction
vocabulary and consumer labels. Each package rolls the generations it touches
and lands with tests that assert the *clock*, not only the outcome.

| WP | Scope | Audit findings closed | Generations / surfaces rolled | Acceptance |
| --- | --- | --- | --- | --- |
| **WP-T.0 — precision-aware comparison** | one shared overlap function with precision-derived effective ends (design §5), used by `claims_as_of`, D106's disjointness test, and later packages; no storage change | 4.13 | none (pure comparison) | an intraday `claims_as_of` window finds a day-precision claim; two same-day events from different wordings overlap; `instant` still matches its own point |
| **WP-T.1 — two windows, seeding, matching, closing** | migration for `valid_from_basis` / `valid_until_basis` / `occurs_*`; seed verdict windows once by kind; interval-aware evidence-target matching for bounded slices (incl. the `EXCLUDE` cases); recorded `extend_start` / `date_undated` verdicts; temporal succession separate from D90 processing order; caps only at world-time starts, else coexist; D55 keeps `source_removed`; no `now()` anywhere; occurrence window recomputed on recount | 4.2, 4.4, 4.10, 4.11, 4.17 | normaliser, both adjudicators, obs flush component (D106 stop-drain-rebuild); schema head | `lookup_relations(valid_at=2010)` returns the 2010 employer and excludes the 2024 one on a seeded corpus; the same ingest replayed on different days yields byte-identical windows; both adjudicators orient one undated/dated pair identically (coexist); a late-discovered older spell inserts as its own slice; no cap boundary equals a wall-clock instant |
| **WP-T.2 — two clocks in every temporal prompt** | relation supersession prompt and laterals show `said on` / `is about`; T4 candidate salient facts carry occurrence windows and rank by evidence then occurrence recency; K writer claims carry `asserted_at` + D41 fields; answer-agent prompt names the envelope fields | 4.3, 4.6, 4.8, 4.16 | relation adjudicator, resolver, K writer; LoCoMo protocol | a 2024 retrospective spell does not supersede a 2023 current fact in the supersession proofs; T4 sees windows in its candidate JSON; the answer prompt fixture contains the two-clock paragraph |
| **WP-T.3 — retrieval keys, envelopes, skill** | testimony grouping keys include the full D41 tuple (or `asserted_at` when unknown) and grouped rows carry every member's times; `Validity`, `GraphEdge`, the K fact model and `memory_v1` fact views gain bases + occurrence; `fact_context@3` / `answer_context@3`; P1 is-about claim filters and the `occurs` fact mode; timeline by occurrence with an `undated` bucket; consumption skill teaches the three clocks and defines `claims_as_of` over world-time | 4.1, 4.9, 4.12, 4.18 | assured operation versions, surface manifest hash, generated OpenAPI/SDK, query-space manifest; LoCoMo protocol | identical text on two dates is two evidence rows in `testimony_context`; a 2015–2020 archive imported today produces 2015–2020 timeline buckets; the regenerated skill text contains the corrected `claims_as_of` definition |
| **WP-T.4 — extraction vocabulary and header** | all four D41 kinds and `open` taught with examples and field descriptions; full-timestamp header | 4.14, 4.15 | extractor; LoCoMo protocol | "has been CEO since 2019" extracts as `proposition_validity` / `open`; a same-day second session resolves "this morning" to a different instant than the first |
| **WP-T.5 — dated labels and consumer surfaces** | labels derived from statement + occurrence window (obs label, `FactResult.label`, profile lines); K fact sheet columns by basis with an `about` column; observation history by occurrence start | 4.5, 4.7 | P1 labels, K page generation | an observation minted from "last week" carries the resolved date in its label but not its statement; a sheet never prints a said-on date under a world-time heading |

Dependencies: T.0 first (T.1's matching and T.3's dedupe use it). T.2, T.3
and T.5 depend on T.1's columns. T.4 is independent of all of them and may
run in parallel. Every package updates the same-PR documentation the
CLAUDE.md rule requires (concepts and API pages for `valid_at`, the benchmark
README when the protocol rolls) and the project-status page.
