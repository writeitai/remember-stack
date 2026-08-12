# Round-4 design review: D90 entity-grain observation flush fan-out

**AGENT:** `claude-opus`
**Date:** 2026-08-12
**Branch:** `design/d89-entity-obs-flush-fanout` @ `6cc784ac`
**Under review:** `plan/designs/e3_entity_obs_flush_fanout_design.md` (§1.7, §5.5, §5.5.1
in focus), `decisions.md` §D90
**Cross-checked against:** `src/rememberstack/spine/observation_adjudication.py`,
`src/rememberstack/spine/work_ledger.py`, `src/rememberstack/spine/fact_catalog.py`,
`plan/designs/observations_design.md` §3
**Scope:** design review only — no code changes proposed here.

## Verdict

**APPROVE_WITH_NITS** — the Codex r3 B1 evidence-collapse case is genuinely closed by
entity-global merge-apply, and the closure is achievable with the D43 adjudicator as it
exists today (no companion D43 change is needed). No wrong D43 outcome remains that is
attributable to the fan-out design. Four items (M1–M4) should land before the impl PR
opens; three of them are doc-consistency defects in sections an implementer will read
literally, and one is a divergence in the canonical decision log.

## The focus question: does entity-global merge-apply close `{t1:A,t3:A}` + `{t2:B}`?

**Yes.** I traced both orders against `observations_design.md` §3 *and* the adjudicator
implementation, because the answer turns on whether a *capped* (historical) same-value
slice can still absorb a later assertion as evidence. It cannot — and that is what makes
source order sufficient.

**The broken order (per-unit apply, what r3 rejects).** Apply unit A whole, then unit B:

1. `t1:A` — no live observations → insert `O1 = A`, `valid_from=t1`, open.
2. `t3:A` — `O1` is still open, and its `statement` is identical, so the exact-match
   evidence gate fires (`observation_adjudication.py:197-206`): evidence row + count bump,
   **no new observation**. The A-at-`t3` state is now unrecoverable — nothing durable
   records that A was reasserted as a *distinct* later slice.
3. `t2:B` — supersedes the open `O1` → `A[t1,t2)`, `B[t2,∞)`.

Codex's B1 is real, and no window re-cap can rebuild step 2's lost slice. §5.5.1 states
this correctly and for the right reason.

**The bound order (entity-global merge of unapplied staging).** Apply `t1:A, t2:B, t3:A`:

1. `t1:A` → insert `O1 = A [t1,∞)`.
2. `t2:B` → same property, effective state, value changed → cap `O1` at `t2`, insert
   `O2 = B [t2,∞)`.
3. `t3:A` → `_BLOCK_ENTITY` returns both rows (the block is `invalidated_at IS NULL`, so
   capped history is *present* in the candidate set), but `is_open` is computed as
   `valid_until IS NULL OR valid_until > now()` (`observation_adjudication.py:889`), so
   the capped `A[t1,t2)` is **not open**. The exact-evidence gate requires
   `bool(candidate["is_open"])` (`:200-202`), and the residue path filters to
   `open_candidates` (`:251-255`). The only competitor is the open `B`, so the assertion
   adjudicates as supersede → cap `O2` at `t3`, insert `A[t3,∞)`.

Result: `A[t1,t2), B[t2,t3), A[t3,∞)` — exactly the §9 acceptance expectation. The
"capped slices are history, not competitors" rule that D43 already implements is what
makes source-ordered replay produce the right three slices, and the design's move from
unit-granular to assertion-granular ordering is the minimal correct fix. Rejecting
`min_asserted_at` slice ordering in §8 and demoting window recompute to a non-substitute
in §5.5.2 are both right calls.

I also checked that the merged drain is *expressible*: staging is keyed per assertion with
`normalizer_version` and joins `claims` for `asserted_at` (`fact_catalog.py:634-659`), and
staging deletes already exist at entity and per-row grain (`:671-`), so per-assertion
progress under one lock is crash-safe at the granularity §5.6 assumes.

## Prior-round findings

| Finding | Status at `6cc784ac` |
| --- | --- |
| Codex r3 B1 — min-time unit ordering collapses intermediate state | **CLOSED** — §1.7 / §5.5 bind entity-global merge; §8 rejects the unit-ordered variant; §9 carries the acceptance case |
| grok r3 B1 — follow-ups must be version-identified, not `unit_id` | **CLOSED** — §5.4.4 pins `target_kind=document_version`, `target_id=version_id` ("never `unit_id`") for both siblings |
| grok r3 H3 / Codex r3 nit — fan-out component version ambiguous | **CLOSED in the design** — literal `e3-obs-flush-2026.08a:claim-fanout-1:entity-fanout-1` (§1.1); **not** in D90 (see M4) |
| Claude r2 R1/R2, Codex r1–r2 identity / barrier / empty-path / cutover / forget | **Remain closed** — re-read §5.1, §5.2, §5.4, §5.7, §5.8; nothing in the r3 edit regressed them |
| grok r3 H1 — session lock needs dedicated-connection ownership | **Open** (N7) |
| grok r3 H2 — single-flight mechanism unbound | **Open, and now cheaper to close** (N4) |
| Codex r3 nit — stale analysis doc | **Open** (N9) |

## Findings (fix before impl freeze; none blocks the decision)

### M1 — §5.3 still specifies the apply rule §1.7 rejects

`plan/designs/e3_entity_obs_flush_fanout_design.md:159-173`. The handler contract — the
section an implementer implements from — was not updated with §1.7. It says: load staging
for `(deployment_id, version_id, subject_entity_id, normalizer_version)` (step 2,
version-scoped), "apply D43 for that entity only, total order **from step 2**" (step 4),
"ensure no staging remains for **that slice**" (step 5), and step 3's parenthetical
"cleared by **this** unit's prior progress only" — which is now precisely backwards, since
a sibling stream draining another version's slice is the expected case. A literal
implementation of §5.3 reproduces the rejected bug.

Rewrite §5.3 to load the entity-global unapplied set, and keep the membership join
explicit (see N1). The binding rule is stated correctly three times elsewhere (§1.7, §5.5,
§8), so this is doc drift rather than a wrong decision — but it is drift in the one
section that reads as the build contract.

### M2 — "the stream completes other units" is not expressible through the ledger contract

§1.7 line 64: "After the stream, every unit whose staging slice is empty is completed
(idempotent no-op complete if another stream already drained it)." The ledger's completion
path only transitions a **running** row: `_COMPLETE` is `UPDATE processing_state SET
status='succeeded' … WHERE processing_id = :processing_id AND status = 'running'`, and
`complete()` raises `WorkNotRunningError` on zero rows (`work_ledger.py:225-246`,
`:1047-1053`). A draining worker holds a lease on its own unit only; sibling units for the
same entity are typically `pending`, so it cannot complete them, and if one is `running`
under another worker the drain would be completing a row it does not lease.

Pin one of two shapes. The cheaper one preserves lease discipline: **the drain never
touches foreign rows** — each sibling unit is claimed normally, acquires the entity lock,
finds its slice empty, and succeeds via §5.3 step 3 (which already exists; it just needs
M1's parenthetical fixed). Barrier latency then depends on those units being claimed, which
is bounded because they are already queued. The alternative — a ledger path for
"drained by a sibling stream" — needs its own definition of the concurrent-claim race and
is strictly more machinery for the same outcome.

### M3 — state what merge-apply guarantees, and where §10's cross-reference now points

The merge covers staging that is unapplied **and** belongs to a materialized, non-dead-letter
unit *at drain time*. Two cases fall outside it and reproduce the same collapse shape:
V2's claim barrier fires after entity E's V1 drain already finished, and a dead-lettered
unit replayed by ops after its peers applied. Neither is a D90 regression — legacy
version-serial flush behaves identically, so D90 is weakly better everywhere and equal
otherwise — but the design should say so in a sentence rather than leave a reader to
derive it.

This matters more than usual because §10 line 359 still lists out-of-scope items "except
**reverse-arrival recompute §5.5.1**", and §5.5.1 no longer contains a recompute — r3
moved that to §5.5.2 and demoted it to "optional safety net". A cold reader cannot tell
whether the r2 co-requisite was deliberately dropped or accidentally orphaned. State the
scope boundary explicitly: schedule-independence is guaranteed among slices concurrently
pending at drain time; genuinely late-arriving earlier-dated evidence remains D43's
pre-existing reverse-arrival behavior (`_pull_valid_from_earlier`,
`observation_adjudication.py:673-718`), unchanged by D90.

### M4 — `decisions.md` §D90 still records the rejected rule

`decisions.md:3586-3587`: "Each worker applies D43 **serially within the unit** in total
order …". Only the Consequences paragraph was updated in `6cc784ac`; the Decision
paragraph — the part a reader treats as the ruling — still states unit-granular apply.
Same paragraph, `decisions.md:3583`: "`OBS_FLUSH_VERSION` with an `:entity-fanout-1`
generation suffix", while the design now pins the literal string; the ambiguity grok r3 H3
flagged (append vs replace) survives in the canonical record. Per `CLAUDE.md`, the
decision log is the canonical record — fix the Decision paragraph, not just Consequences.

## Nits

- **N1 — scope the drain by membership, not by "all staging for the entity."** Staging
  rows exist from the moment claim normalize writes them (`fact_catalog.py:401-425`),
  i.e. *before* that version's claim barrier fires and its units are materialized. An
  unqualified entity-global load would apply assertions from a version whose normalize
  generation is still incomplete — more staging for it would arrive later, recreating
  out-of-order apply. §1.7 and §5.5 word this correctly ("among non-dead-letter units");
  §5.3 must carry the join explicitly when M1 is rewritten.
- **N2 — cross-reference drift persists.** Line 54 and lines 166/168 still cite §5.7 for
  the entity lock (locking is §5.6) and for apply-stream exclusivity (that is §5.5/§1.7;
  §5.7 is legacy-vs-fanout cutover exclusivity). `6cc784ac` fixed only the §5.8→§5.7 refs
  in §1.8/§1.9.
- **N3 — `min_asserted_at` is described as a "claim-order key"** (§5.1 line 119) while §8
  line 333 rejects ordering by it. Keep the column if it is useful as a scheduling or ops
  hint, but re-label it so it does not read as license for the rejected implementation.
- **N4 — bind the single-flight mechanism** (grok r3 H2, still open). The r3 rule makes
  this nearly free: the entity advisory lock *is* the mechanism, since a second unit for
  the same entity blocks, then finds an empty slice on acquisition. One sentence: whether a
  contending claim blocks on the lock or requeues.
- **N5 — §5.9 indexes do not cover the new access path.** The drain reads staging by
  entity across versions joined to membership and processing status; today's staging
  select is version-keyed (`fact_catalog.py:650-659`) and §5.9 names only
  membership-by-version. Add membership by `(deployment_id, subject_entity_id)` and
  staging by `(deployment_id, subject_entity_id, normalizer_version)`.
- **N6 — cost attribution skews with merged streams.** §7's
  `observation_flush:{unit_id|entity_id}:{index}` bills a merged drain's ladder calls to
  whichever unit held the lease, so per-version cost is no longer version-true. Accept it
  in a sentence or key the meter by the assertion's own version.
- **N7 — session lock ownership** (grok r3 H1, unchanged): if §5.6's session-lock path is
  taken, the lock must be held on a dedicated connection not returned to the pool, with
  release in `finally` (precedent: `FactCatalog.label_lock`). Otherwise pool reuse either
  leaks the lock or unlocks the wrong session.
- **N8 — acceptance fixture must use past timestamps.** `is_open` is evaluated against
  wall-clock `now()`, not against the assertion's `asserted_at`
  (`observation_adjudication.py:889`). If the §9 case were written with `t2` in the
  future, the capped A slice would still count as open, `t3:A` would evidence-collapse,
  and the test would fail for a reason unrelated to fan-out ordering. Worth one line in
  the test-plan row so the impl author does not chase it.
- **N9 — refresh the (non-binding) analysis.** `plan/analysis/e3_entity_obs_flush_fanout_analysis.md`
  still names `ObservationFlushHandler`, states the shorter `(asserted_at, claim_id)` key
  (line ~99), and describes embed as following supersession rather than as a sibling
  (line ~96).
- **N10 — §5.5.2 reads as optional; the behavior it names already ships.**
  `_pull_valid_from_earlier` was added for D88 continuous ingest. "Optional safety net"
  should not be read as license to remove it — say "retained as defense in depth".
- **N11 — define "apply stream."** §1.7 introduces the term the whole correctness argument
  rests on without defining it. One clause (a lock-protected merged drain over one
  entity's unapplied staging, spanning versions) satisfies Rule 1 for a cold reader.
- **N12 — §5.6 "bind unless measured otherwise"** (Codex r3 nit, unchanged) still reads as
  if the no-interleaving invariant itself is provisional. Keep the invariant
  unconditionally binding; let only the concrete lock shape be measurable.

## Checklist

| # | Item | Assessment |
| --- | --- | --- |
| 1 | `{t1:A,t3:A}` + `{t2:B}` closed | **PASS** — verified against design §3 and adjudicator code |
| 2 | Fix achievable without a D43 change | **PASS** — capped slices already excluded from evidence/residue |
| 3 | Ordering rule total and NULL-placed | **PASS** — `(asserted_at NULLS LAST, claim_id, statement)` |
| 4 | Cross-entity parallelism preserved | **PASS** |
| 5 | Barrier membership still version-scoped | **PASS** — units unchanged; only apply order is entity-global |
| 6 | Follow-up lease identity | **PASS** — §5.4.4 version-level, never `unit_id` |
| 7 | Empty path / no `document_version` at fan-out generation | **PASS** |
| 8 | Crash-safety of a merged stream | **PASS** — per-assertion write + staging delete under the held lock |
| 9 | Drain scoped to materialized non-DLQ units | **PASS in §1.7/§5.5**, missing in §5.3 (M1/N1) |
| 10 | Sibling-unit completion expressible in the ledger | **FAIL** — M2 |
| 11 | Handler contract matches the decision | **FAIL** — M1 |
| 12 | Decision log matches the design | **FAIL** — M4 |
| 13 | Scope boundary for late arrival stated | **FAIL** — M3 (behavior is fine; the doc is silent/orphaned) |
| 14 | Rules 1–3 (cold-readable, full scope, library boundary) | **PASS** — §5.5.1 is the doc's strongest section; no phasing language; no cloud dependency |

## Recommendation

**Merge the design.** The r3 revision fixes the right defect for the right reason, and the
fix lands inside behavior D43 already implements. Absorb M1–M4 (small, mechanical: rewrite
§5.3, pick M2's completion shape, add M3's scope sentence and repoint §10, correct the D90
Decision paragraph) in one commit on this branch; nits can ride along or land with the
impl PR. The impl PR must carry the §9 `{t1:A,t3:A}` + `{t2:B}` case as an executable
acceptance test with past-dated timestamps, plus a case asserting that staging for a
version whose fan-out has not materialized is **not** drained.
