# Design PR review — PR #270: D93 P1 Lance bulk writes and two-layer maintenance

**Reviewer:** claude-opus
**Date:** 2026-08-13
**Scope:** whole-PR gate review of `design/d91-p1-lance-maintenance` → `main`
(after dual r4 APPROVE_WITH_NITS and the subsequent trigger/change-mass
amendment). Files under review:
`plan/designs/p1_lance_maintenance_design.md` (esp. §5.4.1),
`plan/analysis/p1_lance_maintenance_analysis.md`, `decisions.md` D93,
`design/README.md` index line, review corpus r1–r4.
**Prior rounds:** r1–r4 dual reviews in this PR; r4 verdicts
APPROVE_WITH_NITS (Claude R17–R18; Codex nits 1–2, where Codex nit 1 = R17).
**Focus asked for this round:** trigger observers (writer / idle
`ensure_maintain_due` / finalizer-admin); three modes; change-mass vs
calendar; chunks more sensitive than short text; skip-unchanged excluded from
heavy mass; `decisions.md` D93 completeness.

**Re-verified against code (this round):**
`adapters/selfhost/lance.py` — the forbidden per-row loop exists exactly as
§2 describes (`update_fact_metadata` at 283–298: one `table.update` per row);
constants match the design (`LANCE_TARGET_PARTITION_ROWS = 8_192` at 45,
`_MIN_VECTOR_INDEX_ROWS = 256` at 51, `_LANCE_COMMIT_RETRIES = 8` at 58).
`spine/catalog_contract.py` — `UNLANED_STAGES` at 276 with `lane_is_valid`
enforcing `(lane is None) == (stage in UNLANED_STAGES)` at 302, so "enqueue
with a non-null lane is a hard error" (§5.5.6) is real.
`spine/work_ledger.py` — `fail` at 632 still takes no `expected_attempt`;
the design correctly treats the fence as a PR3 change, not existing behavior.
`ports/queue.py` — `announce(processing_id=, route_snapshot=,
not_before_snapshot=)` at 15–21 matches the reclaim and successor-announce
pseudocode.
`p0_02_0002_infrastructure_registries.py` — the CHECK constraints quoted in
§5.5.2 match (97–99), so the hand-rolled-reclaim prohibition is grounded.
`profiles/selfhost.py` — `_expected_components` at 877 (per-version readiness;
the exclusion rationale holds).
`surfaces/query_sandbox/nomination.py` — `LANCE_FILTER_COLUMNS` at 306 exists
and drives the prefilter rows of the §5.3.1 matrix.

## Verdict

**APPROVE_WITH_NITS**

The PR is design-corpus only (no runtime code), matching its stated scope.
The post-r4 amendment (§5.4.1 + D93 items 3–4) closes the gap it set out to
close: mode discovery is now an explicit observer contract, and heavy
discovery is durable change-mass, not calendar. All six requested focus areas
pass. The r4 nits (Claude R17–R18, Codex 1–2) are verifiably absorbed. The
remaining findings (R19–R22 below) are drafting-level residue of the R17/R18
absorption plus two cross-reference slips — none reopens a decision, none
blocks merge.

## Focus-area findings

### 1. Trigger observers — pass

§5.4.1 states the discovery contract the r4 text lacked: "There is no Lance
callback. Three observers decide to enqueue; the worker only runs claimed
units," followed by an observer table and a per-mode trigger table. The
division of authority is clean and consistently repeated:

- **Writer** (embed_chunk / embed_claim / label_relation / entity profile):
  bumps durable counters, may enqueue **light only**, never heavy, never
  synchronous `optimize()`/`create_index` under the lease. Consistent with
  §1.7, §5.2.3, §5.5.4 and K8.
- **Idle tick** (`ensure_maintain_due` before `claim_one`): durable stats
  first, Lance probe under floors (`maintain_probe_min_s`), may enqueue light
  or heavy; heavy only when `heavy_enabled`, not `awaiting_operator`, and
  write-rate does not defer. Consistent with the §5.5.4 pseudocode and its
  R16-parenthesized probe condition.
- **Finalizer / admin / CLI:** ensure + force heavy; finalizer explicitly not
  gated by `heavy_enabled` (§5.4 knob row, §5.5.4, K11).

D93 item 3 mirrors all three observers correctly ("not three crons"). Two
drafting slips in this area are R21 and R22 below.

### 2. Three modes — pass

One unlaned stage, mode in payload (`light` | `heavy` | `ensure_indexes`),
one unit = one table × one mode (§5.5.1). The semantics tables (§5.3) keep
the modes from collapsing: light = `optimize()` (compact, prune, incremental
fold; `retrain` is a deprecated no-op on pinned 0.34.0 — light **cannot**
retrain); heavy = `create_index(..., replace=True)` retrain, never on the
read path, never under `label_lock`; ensure = list-first create-if-missing,
never destructive. The third job family (content rebuild / embedding
migration) stays out of band (§1.8, §5.5.6). D93 item 2 carries the two
load-bearing negatives ("Light is not a retrain. Heavy is not on the read
path"). Mode trigger table (§5.4.1) assigns each mode a distinct discovery
channel; `ensure_indexes` is correctly "not on a clock."

### 3. Change-mass vs calendar — pass

The binding inversion is stated three times consistently (§1.14, §5.4.1
"Heavy = change-mass (binding)", D93 item 4): calendar
(`heavy_rebuild_min_hours`) is an **anti-thrash cap**, not the discovery
signal, and the §5.4 knob table's own description row says the same. The
four-condition trigger is complete for the failure modes named in the
analysis: changed-row fraction catches updates with flat `count_rows`
(the exact blind spot of growth-pct alone, called out at §5.4 and analysis
§6); change-mass catches few-but-heavy rewrites; growth-pct keeps the append
proxy; leftover post-light unindexed ratio backstops train-quality debt.
Counters are durable, table-scoped on `p1_lance_table_stats`, survive
successor units, and reset **only** after a successful heavy (§5.4.1, §5.6)
— which is what makes "N consecutive" and "since last heavy" implementable
across the succeed-as-skipped successor chain.

One observation, no change required: `heavy_change_mass` is an absolute
threshold while condition 1 is a fraction, so at BEAM chunk counts the mass
condition dominates (2e6 / cap 4096 ≈ 490 full-length chunk rewrites) and
heavy will tend to fire at the 24h cap under modest steady churn. That is a
defensible "chunks strictest" starting point, the doc labels every number
as to-be-measured, and the cap bounds the cost — but PR5/PR6 soak should
confirm the mass-vs-fraction interplay is the intended one rather than an
accident of the starting numbers.

### 4. Chunks more sensitive than short text — pass

The per-table sensitivity table (§5.4.1) gives all four tables values on all
three knobs with a stated *why* per row, and — correctly under Rule 2's
"numbers are starting points" — binds the **ordering** (chunks strictest),
not the numbers. Values are consistent everywhere they appear (§5.4 knob
rows for `heavy_changed_row_frac`, `heavy_change_mass`,
`change_mass_char_cap` all match §5.4.1). D93 item 4 carries the ordering
("Chunks are more sensitive … than short-text facts/claims"). The rationale
is legible cold: longer embedded text per row ⇒ each rewrite moves more of
the trained IVF distribution.

### 5. Skip-unchanged excluded from heavy mass — pass

§5.4.1 binds the increment rule positively ("only when the Lance vector
column is written") **and** negatively with the three forbidden cases:
skip-unchanged rows, matched-only metadata merges that don't touch
`vector`/`label`, and no-op upserts. This composes correctly with the
delete-and-reinsert property (§5.2.1): eligibility-only rows still re-enter
the unindexed tail, which is deliberately **light's** problem (dirt
thresholds) and not heavy's (mass), with condition 4 as the backstop if
light leaves a high ratio. "Eligibility churn must not look like retrain"
(§5.4.1 table) states the intent in one line a cold reader can hold. D93
item 4 mirrors the exclusion, and the Rejected list rejects the converse
("counting eligibility-only writes as change-mass"). PR5's validation column
carries the matching tests (flat row-count updates still trip heavy via
change-mass; eligibility-only does not increment mass; chunks trip sooner
than facts).

### 6. `decisions.md` D93 completeness — pass

All five focus concepts appear in the entry itself, not only in the design:
observers (item 3), three modes with the two negatives (item 2), change-mass
with the min-hours cap demoted to anti-thrash (item 4), chunk sensitivity
(item 4), skip-unchanged / eligibility-only exclusion (item 4), best-effort
heavy with `awaiting_operator` (item 5). Context has the BEAM incident
numbers and the dated LanceDB citations; Consequences carry the unlaned
route, the `_expected_components` exclusion, attempt-fenced reclaim, and the
separate rebuild family; the Rejected list matches §12's load-bearing rows;
Amends is correctly scoped (clarifies D8; does not touch D9/D48). The entry
is terse where the design is self-contained, which is the division CLAUDE.md
Rule 1 prescribes.

One optional addition (fold into R22 if taken): the only user-visible
consequence of D93 not surfaced in the entry is the index-matrix behavior
change at the backfill barrier — entities gain a vector index, so entity ANN
search stops being exhaustive once the min-row gate is met (§5.3). One line
under Consequences would let a decision-log reader see it without opening
the design.

## Disposition of r4 nits

| Nit | Status | Where |
| --- | --- | --- |
| **R17 / Codex 1** — defer streak and `awaiting_operator` must survive successor units | **Closed** | §5.5.1 "Escalation counters are table-scoped, not unit-local": `rate_defer_count`, `conflict_defer_count`, `first_defer_at`, `operator_state` live on `p1_lance_table_stats` keyed `(lance_root_key, table_name)`; successor units must not reset them; unit rows mirror for display only. §5.6 lists them as authoritative. Residue: two pseudocode readers not updated — see R19. |
| **R18** — `maintenance_writer_gate` needs a runtime-readable home | **Closed** | §5.6 adds durable `writer_gate` on `p1_lance_table_stats`, re-read by writers **each batch**, env/settings demoted to cold-start default. Residue: value-vocabulary mismatch — see R20. |
| **Codex 2** — stale "Revised r3" closing note | **Closed** | Closing note now reads "Revised r4 … dual APPROVE_WITH_NITS on r4." |

## Nits (R19–R22 — fix in this PR or the next design-touching commit; no re-review round needed)

- **R19 — two `awaiting_operator` readers still point at the unit row.**
  R17 moved authority to `p1_lance_table_stats`, and §5.5.1/§5.6 say so
  bindingly, but the handler pseudocode still checks
  `unit.operator_state == 'awaiting_operator'` (§5.5.3) and the
  `ensure_maintain_due` pseudocode still checks "table has open/terminal
  unit with operator_state == 'awaiting_operator'" (§5.5.4). A fresh
  successor unit's mirror is display-only and may be null, which is exactly
  the trap R17 named. One-line fix at each site: read the table-stats row
  (the §5.4 rule "while set for a `(root, table, mode=heavy)`" already has
  the right scope).
- **R20 — writer-gate value vocabulary is split.** §5.4 defines the knob as
  `open` (default) | `hold`; §5.6 defines the durable column as
  `run` | `hold`. Pick one pair (the durable column is authoritative, so
  probably `run|hold`) and align the knob row, §5.5.4, §9, and the
  `p1_lance_writer_gate` metric description.
- **R21 — idle discovery of `ensure_indexes` has no path in the pseudocode.**
  The §5.4.1 mode-trigger table binds "idle probe of `list_indices`" as a
  discovery channel for `ensure_indexes` (e.g. entities newly crossing the
  256-row gate with no vector index), and §5.3 lists "maintain tick" in the
  Ensure cadence — but the §5.5.4 `ensure_maintain_due` pseudocode only ever
  enqueues `light` or `heavy`, and the `light` handler branch does not run
  ensure. As written, an idle-detected missing index is only repaired when a
  heavy happens to run (heavy does ensure first). Either add an
  ensure-enqueue branch to the pseudocode or state explicitly which unit
  carries idle-detected ensures.
- **R22 — two cross-reference slips.** (a) §5.4.1 opens with "Three
  observers," but §5.5.4's source table has five rows — post-hard-forget
  purge (and admin/CLI as distinct from the finalizer). Purge is a
  writer-class source; one clause reconciles the count. (b) The §5.4
  `heavy_rebuild_row_growth_pct` row cites "§5.4.1" for its per-table values
  (chunks 5, claims 15), but the §5.4.1 sensitivity table does not carry a
  growth-pct column — either add the column or drop the cross-reference.
  Optionally add the D93 entities-index consequence line from focus item 6.

## Checklist

| # | Contract | Verdict |
| --- | --- | --- |
| 1 | PR scope = design corpus only (no runtime code) | **Pass** (matches PR description; `design/README.md` index line present) |
| 2 | §5.4.1 observers consistent with §5.5.4 / D93 item 3 | **Pass** (R21/R22 drafting residue) |
| 3 | Three modes distinct; light ≠ retrain; heavy off read path | **Pass** |
| 4 | Heavy = durable change-mass; calendar = cap only | **Pass** |
| 5 | Chunk-strictest ordering binding; numbers labeled measurable | **Pass** |
| 6 | Skip-unchanged / eligibility-only excluded from heavy mass | **Pass** |
| 7 | Counters survive successors; reset only on successful heavy | **Pass** (R17 absorbed; R19 residue in pseudocode reads) |
| 8 | D93 entry complete and consistent with design | **Pass** (optional entities-consequence line) |
| 9 | r4 nits (R17, R18, Codex 1–2) absorbed | **Pass** |
| 10 | As-built claims accurate against code | **Pass** (re-verified this round; list in header) |
| 11 | Rule 1 (cold-reader legibility) | **Pass** (observer/trigger tables are plain-language; jargon anchored) |
| 12 | Rule 2 (full scope; no phasing hedges; numbers as starting points) | **Pass** (§15 sequences the full scope; non-goals stated as boundaries) |
| 13 | Rule 3 (library boundary; no control-plane authority) | **Pass** (gate, escalation, runbook all in-repo) |
| 14 | D66 docs obligation | **Pass** (this PR ships no user-facing behavior; PR4/PR5 carry same-PR docs) |

## Closing

The amendment this PR was re-opened for — making trigger observers and
change-mass discovery explicit — is done, bindingly, and D93 carries it into
the decision log. Every as-built claim I re-checked this round is accurate,
and the r4 nit trail is closed. R19 is the only finding with teeth (the
same fresh-successor trap R17 identified, surviving in two pseudocode
reads); it and R20–R22 are one-to-three-line fixes that change no decision.
**Merge PR #270**; apply R19–R22 in this PR if convenient, otherwise in the
next commit that touches the design.
