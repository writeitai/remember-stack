# Temporal clocks — sequencing (D107)

**Status:** proposed sequencing; no work package is started by this document

**Binding design:** `plan/designs/temporal_clocks_design.md`

**Analysis:** `plan/analysis/time_handling_audit.md`

Order follows dependency and answer impact: the shared canonical-bounds
function first (everything compares through it), then the fact model and
cutover (it makes the documented `valid_at` promise true), then the prompts
that decide supersession and identity, then retrieval keys and envelopes,
then extraction vocabulary and consumer labels. Each package rolls the
generations it touches and lands with tests that assert the *clock*, not
only the outcome.

| WP | Scope | Audit findings closed | Generations / surfaces rolled | Acceptance |
| --- | --- | --- | --- | --- |
| **WP-T.0 — canonical bounds** | `canonical_bounds(from, until, precision)` (design §5) used by `claims_as_of`, D106's disjointness test, and every later package; claim storage unchanged | 4.13 | none (pure comparison) | an intraday `claims_as_of` window finds a day-precision claim; a year stored as 2022-01-01…12-31 canonicalises to `[2022-01-01, 2023-01-01)`; an `instant` is a non-empty point that overlaps itself; two same-day events from different wordings overlap |
| **WP-T.1 — fact model and cutover** | migration: `temporal_kind`, `valid_from_basis`, `valid_until_basis`, `occurs_from`/`occurs_until`/`occurs_precision`, `seed_claim_id`; state-only verdict `EXCLUDE`, occurrence `EXCLUDE`, undated unique key; seed once by kind from the D90-first claim; matching by kind (§4.2); no automatic verdict revision, review items for discrepancies (§4.3); succession by any successor-supplied world-time instant incl. ending occurrences, else coexist (§4.4); D90 re-split by occurrence start (§4.5); D55 `source_removed`; no `now()`; the current-fact predicate with expiry sweep (§7.1); stop-drain-migrate-rebuild-readiness (§9) | 4.2, 4.4, 4.10, 4.11, 4.17 | normaliser, both adjudicators, obs flush component; schema head; fact-layer generation in readiness | `lookup_relations(valid_at=2010)` returns the 2010 employer and excludes the 2024 one; a January and an October visit are two occurrence relations under the new `EXCLUDE`; the same ingest replayed on different days yields byte-identical windows; both adjudicators orient one undated/dated pair identically (coexist); a dated resignation still caps an undated "is CEO" state (D106 preserved); the staggered D90 case with said-on and is-about orders reversed yields world-ordered slices; a state with a future end is current today and its profile refreshes when the end passes; a store whose fact generation predates D107 is refused by readiness |
| **WP-T.2 — two clocks in every temporal prompt** | relation supersession prompt and laterals show `said on` / `is about`; T4 candidate salient facts carry occurrence windows and kinds and rank by evidence then occurrence recency; K writer claims carry `asserted_at` + D41 fields; answer-agent prompt names the envelope fields | 4.3, 4.6, 4.8, 4.16 | relation adjudicator, resolver, K writer; LoCoMo protocol | a 2024 retrospective spell does not supersede a 2023 current fact in the supersession proofs; T4 sees windows and kinds in its candidate JSON; the answer prompt fixture contains the two-clock paragraph |
| **WP-T.3 — retrieval keys, envelopes, skill** | dedupe on the full D41 tuple (or `asserted_at` when unknown) with `grouped_members`; `Validity`, `GraphEdge`, the K fact model and `memory_v1` fact views gain bases, kind and occurrence; `resolve_entity@2`, `testimony_context@2`, `fact_context@3`, `answer_context@3`; open-query confirmation returns the full D41 tuple and fact bases/occurrence; P1 is-about claim filters and the `occurs` fact mode; timeline by occurrence with an `undated` bucket; consumption skill teaches the three clocks and two fact kinds and defines `claims_as_of` over world-time | 4.1, 4.9, 4.12, 4.18, 4.19 | assured operation versions, surface manifest hash, query-space manifest, generated OpenAPI/SDK; LoCoMo protocol | identical text on two dates is two evidence rows in `testimony_context`, and a grouped row lists every member's times; a 2015–2020 archive imported today produces 2015–2020 timeline buckets; the regenerated skill text contains the corrected `claims_as_of` definition; an open-query claim row carries precision and kind |
| **WP-T.4 — extraction vocabulary and anchor** | all four D41 kinds and `open` taught with examples and field descriptions; full-timestamp header | 4.14, 4.15 | extractor; LoCoMo protocol | "has been CEO since 2019" extracts as `proposition_validity` / `open`; "three hours ago" in a source stamped 19:30 resolves to an `instant` at 16:30 of that day, and the same words in a second same-day source stamped 22:00 resolve to a different instant |
| **WP-T.5 — dated labels and consumer surfaces** | labels derived from statement + occurrence window (obs label, `FactResult.label`, profile lines); K fact sheet columns by basis with an `about` column; observation history by `occurs_from` | 4.5, 4.7 | P1 labels, K page generation | an observation minted from "last week" carries the resolved date in its label but not its statement; a sheet never prints a said-on date under a world-time heading |

Dependencies: T.0 first. T.1 depends on T.0. T.2, T.3 and T.5 depend on
T.1's columns and cutover. T.4 is independent and may run in parallel. Every
package updates the same-PR documentation the CLAUDE.md rule requires
(concepts and API pages for `valid_at`, the benchmark README when the
protocol rolls) and the project-status page.
