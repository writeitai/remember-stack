# Implementation re-review (r3) — D90 entity-grain observation flush fan-out

**Agent:** claude-opus
**Date:** 2026-08-12
**Target:** branch `feat/d90-entity-obs-flush-fanout`, PR #265, commit `0bb37204`
**Design:** [e3_entity_obs_flush_fanout_design.md](../../plan/designs/e3_entity_obs_flush_fanout_design.md) (D90)
**Prior implementation reviews:**
[claude-opus r1](REVIEW_claude-opus_e3_entity_obs_flush_fanout_impl_2026-08-12.md),
[codex-sol r1](REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_2026-08-12.md),
[codex-sol r2](REVIEW_codex-sol_e3_entity_obs_flush_fanout_impl_r2_2026-08-12.md)

---

## Verdict

**REQUEST_CHANGES.**

Both headline blockers from the dual r1/r2 reviews are genuinely closed, and I
verified them by **executing** the two binding acceptance cases against real
PostgreSQL rather than by reading the code — §5.5.1 co-present and §5.5.3
staggered both now produce exactly `A[t1,t2), B[t2,t3), A[t3,∞)`. The barrier
anti-join, the derived readiness aggregate, DLQ exclusion, and staging retirement
are all executable-correct as well. That is a substantial, real fix, and the
apply engine is now the one D90 specifies.

Two things hold the merge gate.

First, the new `_resplit_later_evidence` re-materializes evidence **without
regard to `claims.is_current_testimony`**. Testimony the deployment has already
withdrawn at the document level is promoted back into a *new open observation*,
and it caps a slice that is backed by current testimony. I have an executed
repro (B1). This is a defect the branch introduces; no prior review saw it
because no prior review ran the code.

Second, after three rounds D90 still has **zero executable tests**. All seven
tests in the new file are `inspect.getsource` substring assertions; the §9
"Test plan (minimum)" table has 0/17 executable coverage; and the one existing
guard test that covered staging retirement was weakened until it no longer
covers the entity path at all. I wrote executable proofs for four of those §9
rows in one sitting using the repo's own fixtures — the coverage is not blocked
by anything (B2).

---

## What I executed

Everything below ran against a real PostgreSQL 16 at structural head
`p9_10_0031` (`downgrade base` → `upgrade head`), using the repo's
`FakeModelProvider` with a deterministic same-value→`evidence` /
different-value→`supersede` router. This is what separates this review from r1
and r2, both of which were code-read only.

```text
uv run ruff check src/                     All checks passed
uv run ruff format --check src/            348 files already formatted
uv run pyright <5 D90 impl files>          0 errors, 0 warnings
uv run pytest -q src/tests/workers src/tests/spine
                                           144 passed, 293 skipped
uv run pytest -q src/tests/spine/test_migrations.py   (with REMEMBERSTACK_DATABASE_URL)
                                           6 passed  — confirms HEAD claim 5
                                           (69 tables, head p9_10_0031, constraint counts)
git diff --check main...HEAD               clean
```

### §5.5.1 co-present units — **correct**

Entity `E`; V1 unit stages `{t1:"hc 500", t3:"hc 500"}`, V2 unit stages
`{t2:"hc 600"}`; both units `pending`; one `flush_entity_global_staging` call.

```text
('hc 500', 2024-01-01 .. 2024-02-01)
('hc 600', 2024-02-01 .. 2024-03-01)
('hc 500', 2024-03-01 .. open)
staging rows left: 0
```

### §5.5.3 staggered late arrival — **correct**

Unit A `{t1,t3}` flushed and retired alone first (yielding a single open
`hc 500` slice with `t3` collapsed as evidence), then unit B `{t2}`
materialized and flushed:

```text
('hc 500', 2024-01-01 .. 2024-02-01)   evidence rows: 1
('hc 600', 2024-02-01 .. 2024-03-01)   evidence rows: 1
('hc 500', 2024-03-01 .. open)         evidence rows: 1
staging rows left: 0
```

The re-split correctly moves the post-boundary evidence claim off the capped row
and re-enters the D43 ladder, so open `B` is capped at `t3` rather than left
open beside a blind insert. This is the case r1-B2 and r2-B2 said was absent.

### Barrier anti-join (§5.4, §9 rows "2/3" and "3/3") — **correct**

Three units on one version, completed one at a time through
`WorkLedger.complete_entity_obs_flush`:

```text
after 1/3 units: 0 sibling rows
after 2/3 units: 0 sibling rows
after 3/3 units: 2 sibling rows
version_state: {'fanout_status': 'barrier_complete', 'done': True}
siblings: [('adjudicate_supersession', 'document_version', targets_version=True),
           ('embed_claim',             'document_version', targets_version=True)]
```

### Derived readiness aggregate (§5.8 row 1) — **correct**

```text
empty    status=succeeded  finished_at=True      (empty_complete → completed_at)
partial  status=pending    finished_at=False     (1 of 2 units succeeded → blocks)
legacy   status=missing    finished_at=False     (no D90 state at this generation)
after re-pinning the empty version's state to an OLD normalizer generation:
empty    status=missing    finished_at=False     (stale generation cannot satisfy)
```

All three of codex r2-B3's sub-failures are closed.

### Other executed probes

| Probe | Result |
| --- | --- |
| Dead-letter unit's staging excluded from the drain | correct — DLQ slice left staged, peer applied |
| Re-claim of an already-drained entity | clean no-op, byte-identical slices |
| Same unit carrying two processing rows at different generations | no double-apply (exact-statement collapse absorbs it) |
| Peer staging survives the entity path | correct — no version-wide clear |
| `_SELECT_READY_CYCLES` with the new D90 guard | executes; valid SQL |

---

## Prior-blocker disposition

| Prior issue | Raised by | r3 status | Evidence |
| --- | --- | --- | --- |
| Per-unit apply instead of entity-global merge-apply | claude B1, codex B1 | **Closed** | `flush_entity_global_staging` (`observation_adjudication.py:168-225`) loads, applies and retires the whole entity-global stream under one lock; §5.5.1 case executed correct |
| §5.5.3 late-arrival re-split absent | claude B2, codex B2 | **Closed** | `_resplit_later_evidence` (`observation_adjudication.py:885-948`) re-enters `_add_with_block`; §5.5.3 case executed correct |
| Readiness `missing` for every version forever | claude B3 | **Closed** | `_ENTITY_OBS_FLUSH_STATUS` (`readiness.py:429-470`) derives from units + version state; executed |
| Empty path has NULL `finished_at` → permanently not-ready | codex B3.2 | **Closed** | `completed_at` stamped by `_UPSERT_OBS_FLUSH_VERSION_STATE`; `COALESCE(max(s.completed_at), …)`; executed |
| Readiness joins not pinned to normalizer generation | codex B3.3 | **Closed** | both `s` and `u` joins pin `:normalizer_version`; stale-generation probe executed |
| E1/E2 zero-chunk paths bypass D90 version state | codex B3.1 | **Closed** | both now return `EmptyObsFlushComplete` (`e1.py:640-658`, `e2.py:1067-1085`) routed through `complete_empty_obs_flush` |
| Connector-cycle finalization ignores units | claude B4, codex B4 | **Closed** | `_SELECT_READY_CYCLES` D90 `NOT EXISTS` guard incl. `dead_letter` (`lifecycle.py:1071-1087`); query executes |
| Branch red (failing test, ruff, format, pyright) | claude B5, codex | **Closed** | all four clean; 144 passed |
| Hard forget does not scrub the new tables | claude B6 | **Closed** | `forget.py:1255-1273` deletes both tables; unit processing rows are reached by the existing payload scrub via `content_hash` — the ordering concern codex raised does not bite because that predicate never reads membership |
| Legacy/fan-out exclusivity one-way | codex B5 (part) | **Closed** | `has_obs_flush_fanout` fail-closed in the legacy handler (`e3.py:820-828`) + `_COUNT_LEGACY_OBS_FLUSH` in fan-out; the two checks interlock because a running legacy row is itself non-terminal |
| PostgreSQL `min(uuid)` in fan-out | codex r1 | **Closed** | `(array_agg(s.doc_id ORDER BY s.claim_id))[1]` (`work_ledger.py:1697-1709`); exercised on real PostgreSQL |
| Authoritative version-state completion | codex B5 (part) | **Partial** | barrier requires `expected > 0`, so membership must exist before `barrier_complete` can be written — but completion still does not require a `materialized` state row and still trusts handler-supplied coordinates (nit 3) |
| Executable D90 coverage | claude, codex ×2 | **Open — blocker** | still 0 of §9's 17 cases; see B2 |

---

## Blockers

### B1 — The re-split promotes **withdrawn testimony** into a new current observation

`src/rememberstack/spine/observation_adjudication.py:885-948`, `:1173-1183`

`_SELECT_EVIDENCE_FOR_OBS` selects every `stance='supports'` evidence row on the
observation being capped, with no filter on `claims.is_current_testimony`. Every
row whose claim is strictly later than the cap boundary is then **deleted** from
the capped observation and re-applied through `_add_with_block`, which inserts it
as a fresh observation slice.

`is_current_testimony` is the D54/lifecycle flag meaning "this document version's
testimony has been superseded by a newer version and no longer speaks for the
deployment" (`lifecycle.py:803-834`). Evidence links from such claims stay on the
observation; that is why `_RECOUNT` counts only `is_current_testimony` claims
(`observation_adjudication.py:1214-1226`). The re-split ignores the flag, so a
withdrawn claim can create a fact rather than merely fail to support one.

Executed repro (real PostgreSQL, same harness as the §5.5.3 proof above):

1. Unit A `{t1:"hc 500", t3:"hc 500"}` flushes alone → one open `hc 500` slice
   carrying `t1` and `t3` as evidence.
2. The `t3` document version is superseded, so lifecycle flips that claim to
   `is_current_testimony = false`.
3. Unit B `{t2:"hc 600"}` materializes and flushes.

```text
stmt=hc 500  from=2024-01-01  until=2024-02-01  evid_count=1  all_current=True
stmt=hc 600  from=2024-02-01  until=2024-03-01  evid_count=1  all_current=True
stmt=hc 500  from=2024-03-01  until=open        evid_count=0  all_current=False
```

Two things are wrong in that final row. The **current** answer to "what is the
headcount" is now `500`, sourced entirely from testimony the deployment has
withdrawn, with `evidence_count = 0`. And `hc 600` — which *is* backed by current
testimony and was the open slice — has been **capped at `t3` on the strength of
that withdrawn claim**. Without step 2 the same flush leaves `hc 600` open only
until `t3` for a legitimate reason; with step 2 the cap has no live support at
all.

Nothing downstream repairs this before it is served. `_SELECT_FACT_SHEET_OBSERVATIONS`
(`knowledge.py:4694-4705`) returns `evidence_count` but does not filter on it, so
the zero-evidence slice is read out as a current fact. Reconcile's
`_SELECT_ZERO_OBSERVATIONS` (`lifecycle.py:906-920`) would eventually route it to
a `support_withdrawn` review — but only if that observation falls inside the
cycle's scope, and its only evidence claim belongs to an *older* version than the
one whose flush created it. Even when review does fire, the cap on `hc 600` has
already happened: D22/D54's rule is that withdrawn support is a matter for a
reviewer, never for mechanics, and here mechanics both created a fact and ended
another one.

This is not a pre-existing D43 behaviour. Before this commit nothing in the
codebase deleted `observation_evidence` rows except hard forget
(`forget.py:1244`), and no path turned an evidence link into a new observation.

**Direction:** restrict `_SELECT_EVIDENCE_FOR_OBS` to `claims.is_current_testimony`
(the flag is already joined for `asserted_at`), so withdrawn testimony stays
attached to the historical slice it originally supported and never re-materializes.
If D90 §5.5.3 is meant to re-split regardless of testimony currency, that is a
design decision that has to be written into the design with its rationale
(CLAUDE.md Rule 1) rather than fall out of an unfiltered `SELECT` — but I do not
think it is: §5.5.3's stated purpose is recovering a slice lost to *evidence
collapse*, not resurrecting superseded documents.

Secondary, same function: the evidence deletion is **unlogged**. `_add_with_block`
writes an adjudication row for the new slice, so where the claim *went* is
auditable, but nothing records that it was detached from the capped observation.
Given this module's own binding contract ("every cap writes a reason row … no
silent caps"), the detach deserves an `observation_adjudications` row too.

### B2 — D90 still has zero executable coverage after three rounds

`src/tests/workers/test_e3_entity_obs_flush_fanout.py` (7 tests)

Every test in the file is an `inspect.getsource` substring assertion. None
constructs a unit, runs a flush, opens a barrier, or evaluates readiness. The
design's §9 table is titled "Test plan (**minimum**)"; its scoreboard is
unchanged from r1:

| §9 case | Executable coverage |
| --- | --- |
| Claim barrier, 3 staging entities → 3 units + 3 processing rows | missing |
| Two versions, same subject entity → 2 distinct `unit_id`s | missing (this is dual-review B1, the sole reason `target_id` is `unit_id`) |
| V2 after V1 succeeded for same entity | missing |
| Same entity, two pending units → single apply stream | missing |
| Unit A `{t1,t3}` + unit B `{t2}` co-present | missing — **and it now passes**; see above |
| Unit A alone, then B `{t2}` (§5.5.3) | missing — **and it now passes**; see above |
| Supersession payload reconstruction fields | missing |
| Zero-chunk empty path | partial — `test_chunk_level_extract.py` now asserts the `EmptyObsFlushComplete` shape, but nothing asserts no `document_version` row lands at the fan-out version |
| Empty staging → empty signal + supersession + `embed_claim` | missing |
| 2/3 units succeeded → no supersession | missing |
| 3/3 succeeded → supersession + `embed_claim` once | missing |
| Unit DLQ → no supersession | missing |
| Within-entity multi-statement same claim, `statement` tie-break | missing |
| Forget doc A entities → version B's units still runnable | missing (the §5.8 trap the design names explicitly) |
| No version-wide staging clear on entity path | source-substring only |
| Legacy non-terminal blocks fan-out | missing |
| Partial unit retry → no double evidence corruption | missing |

Beyond §9, §5.5.1's closing sentence also *requires* a case for "undated/tied
assertions split across units". There is none, and when I ran it the result is
worth a decision rather than silence: staging `{undated:"hc 500"}` in one unit
and `{t2:"hc 600"}` in another yields

```text
('hc 500', valid_from=NULL, open)                       ← [always, ∞)
('hc 600', valid_from=2024-02-01, valid_until=now())
```

— two overlapping current-state windows for the same property. §5.5 row 4
deliberately defers undated boundary semantics to "existing D43 undated rules",
so this is arguably out of scope for the *fix*; it is not out of scope for the
*test*, and pinning the behaviour is how a future reader learns it was a choice.

Three specific regressions in the existing tests compound this:

1. `test_e3_claim_normalize_fanout.py:199-205` was rewritten to
   `assert "clear_staging=" in entity_source or "clear_staging=" in legacy_source`.
   The entity path no longer contains `clear_staging=` at all (it calls
   `flush_entity_global_staging`), so the assertion is now satisfied purely by
   the legacy branch. The test named "obs flush retires staging in same txn as
   apply" no longer covers the D90 path in any way.
2. `test_enqueue_entity_fanout_source_pins_membership:28` still reads
   `assert "entity-fanout" in source or "obs_flush_component_version" in source`;
   the second disjunct is a parameter name in the function under inspection, so
   the assertion is unconditionally true. Lines 22 and 25-27 share the pattern.
3. `test_entity_handler_applies_global_stream_and_row_clear` asserts the
   *absence* of two strings (`unit_assertions`, `row["version_id"] == version_id`).
   Negative source assertions pass for any rewrite that renames a local.

There is no environmental blocker here. `src/tests/spine/test_observation_adjudication.py`
already provides the exact fixture shape needed (`database_engine` module fixture,
`DeploymentBootstrapper`, `FakeModelProvider` with a router, `_entity`), and
`src/tests/spine/test_operations.py` already builds a `WorkLedger` against
PostgreSQL. Both proofs in this review were written on top of those in a single
session. At minimum the two cases D90 exists to get right — §5.5.1 co-present and
§5.5.3 staggered — plus the 2/3-blocks / 3/3-fires barrier pair, belong in the
suite before merge, so the next refactor cannot silently reopen them.

---

## Nits

Carried items are marked; the rest are new at this HEAD.

1. **(carried, r1 nit 1)** `obs_flush_component_version=OBS_FLUSH_VERSION` is
   still the module constant, not `work.component_version` (`e3.py:795`). The
   barrier counts succeeded rows at that literal
   (`_COUNT_OBS_FLUSH_UNITS_SUCCEEDED`), so an ops replay at any other generation
   counts zero and stalls the version. My PROBE 3 confirmed a unit *can* carry
   rows at two generations without erroring, which is exactly the situation this
   would mis-handle.

2. **(carried, r1 nit 5)** `_SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY`
   (`observation_adjudication.py:1150-1171`) still does not pin
   `p.component_version` on the `LEFT JOIN`. With two processing rows on one unit
   the join multiplies and each staging row enters the stream twice. Executed:
   the duplicate is absorbed by the exact-statement evidence collapse (1
   observation, 1 adjudication), so this is benign today — but it is benign by
   luck, and it burns a redundant `_DELETE_OBS_STAGING_ROW` and an extra cost key.

3. **(carried, codex r2 B5)** `complete_entity_obs_flush`
   (`work_ledger.py:414-503`) marks the supplied `processing_id` succeeded and
   builds the barrier from handler-supplied coordinates without loading the
   processing row or requiring `fanout_status='materialized'` in the same
   transaction. The `expected > 0` guard in `_entity_obs_flush_barrier_ready`
   (`work_ledger.py:1147-1152`) means membership must exist before the barrier can
   fire, which is most of the protection — but loading the claimed unit under the
   completion lock and deriving `representation_id` / `normalizer_version` from it
   would make the durable expected-set the authority §5.2 says it is, instead of a
   cross-check.

4. **(carried, r1 nit 4)** Both staging queries still inner-`JOIN claims`
   (`work_ledger.py:1697`, `observation_adjudication.py:1150`). A staging row
   whose claim is absent is invisible to fan-out *and* to apply: never applied,
   never deleted, never blocks the barrier — silent loss under a `succeeded`
   version. `LEFT JOIN` (undated already sorts last) or fail non-retryable.

5. **(carried, r1 nit 11)** Both early exits in `_enqueue_entity_obs_flush_fanout`
   still `return []` silently (`work_ledger.py:989-990`, `:1000-1001`). The legacy
   branch is the one that matters: if fan-out is skipped because a legacy row was
   non-terminal and ops later dead-letters and removes it, nothing re-triggers
   fan-out, no durable marker exists, and the version stalls with no signal. At
   minimum `log()` it.

6. **(carried, r1 nit 9)** `ix_obs_flush_units_version`,
   `ix_obs_flush_units_entity` and `ix_procstate_entity_obs_flush` are still
   absent from `EXPECTED_INDEXES` (`catalog_contract.py`). Confirmed empty by
   introspection. The head check filters by `indexname = ANY(:names)`, so
   unregistered indexes pass silently — which is why the omission matters.

7. **(carried, r1 nit 8)** Neither new table has
   `REFERENCES deployments(deployment_id)`; `EXPECTED_CONSTRAINT_COUNTS` keeps
   `f` at 128, confirming zero FKs. `processing_state` and the p9_06 registry
   tables both carry the tenancy FK.

8. **(carried, r1 nit 10)** `_COUNT_LEGACY_OBS_FLUSH` still detects the legacy
   generation with `component_version NOT LIKE '%entity-fanout%'`
   (`work_ledger.py:1771`). `OBS_FLUSH_LEGACY_VERSION` now exists as a named
   constant (`e3.py:68`) — compare against it, or a declared legacy set.

9. **New — dead code and a duplicated query.**
   `FactCatalog.load_unapplied_obs_staging_for_entity` (`fact_catalog.py:514`) and
   its `_SELECT_UNAPPLIED_OBS_STAGING_FOR_ENTITY` (`fact_catalog.py:765`) have no
   callers: the handler now goes through the adjudicator, which carries its own
   byte-identical copy of the SQL (`observation_adjudication.py:1150`). Two copies
   of a load-bearing eligibility query is exactly how nit 2 gets fixed in one
   place and not the other. Delete the `fact_catalog` pair.

10. **New — `OBS_FLUSH_LEGACY_VERSION` is now unreachable.** No call site enqueues
    at it (`e3.py:68`, zero import sites). That is the intended terminal cutover
    state, so it is correct — but a reader cannot tell whether the legacy handler
    is live or vestigial. One line in the constant's docstring saying "no code
    path creates rows at this version; the handler exists only to drain rows
    enqueued before the D90 deploy" would settle it.

11. **New — re-split cost keys collapse.** `_resplit_later_evidence` passes
    `call_key="d90_late_arrival_resplit"` verbatim
    (`observation_adjudication.py:945`), with no entity, unit, or index
    qualification. Every re-split ladder call across every entity in the
    deployment lands on one cost key, which defeats §7's "Ops UI: resolve
    `unit_id` → subject entity + version". The primary path was fixed
    (`observation_flush:{entity_id}:{index}` now matches §7); this one was missed.

12. **New — the whole entity-global drain is one transaction with LLM calls
    inside it.** `flush_entity_global_staging` opens `self._engine.begin()`, takes
    `pg_advisory_xact_lock`, and holds both across the full ladder for every
    unapplied assertion on the entity *across all versions*, plus any recursive
    re-split ladders. §5.6 explicitly permits the single-transaction shape, so
    this is not a design violation — but D90 exists for BEAM-scale hubs (§2, §11
    "largest hub still bounds the critical path"), and this commit made the
    transaction strictly longer than the pre-D90 per-version one. Worth a measured
    note in the PR on idle-in-transaction limits, and worth remembering that §5.6
    names session-lock + per-assertion commit as the scale path when it bites.

13. **New — re-materialized slices carry the capped observation's statement text,
    not the claim's.** `_resplit_later_evidence` re-applies with `capped_statement`
    (`observation_adjudication.py:940`). When the original collapse was a semantic
    `evidence` verdict rather than an exact match, the claim said something
    textually different ("staff count: 100" collapsing onto "headcount is 100").
    The new slice is attributed to that claim but shows the other wording. Almost
    certainly the right choice — it is the same fact — but it is a choice, and the
    function's docstring should say so.

14. **Hard-forget residue verifier does not cover the two new tables.** The
    `UNION ALL` residue chain (`forget.py:1690+`) was not extended with
    `obs_flush_entity_units` / `obs_flush_version_state`, so the existing
    hard-forget residue test cannot observe whether the new deletes at
    `forget.py:1255-1273` actually fire. The deletes look right; nothing proves it.

---

## What I checked and found correct

Recording these so r4 does not re-derive them:

- `target_id = unit_id`, not `subject_entity_id` — the D12 identity collision of
  design §1.2 / dual-review B1 is genuinely avoided.
- `complete_entity_obs_flush` acquires `_ADVISORY_LOCK_NORMALIZE_BARRIER` keyed on
  `representation_id` — the same key family as `complete_claim_normalize`, so
  §5.4.1's shared-namespace option holds and the "two keys, fixed order" caveat
  does not apply.
- The barrier anti-join blocks on 1/3 and 2/3 and fires exactly once on 3/3,
  writing `barrier_complete` with `completed_at`; both siblings target
  `document_version` / `version_id`, never `unit_id` (§5.4.4). **Executed.**
- The entity lock is taken as the first statement of the flush transaction,
  *before* the staging snapshot — the specific fix this commit claims. Two
  co-present workers therefore cannot both materialize the same stream.
- Staging is retired row-by-row by its true primary key
  (`deployment_id, version_id, claim_id, subject_entity_id, statement,
  normalizer_version`), matching `p9_08_0029`. **Executed:** zero rows left after
  a multi-version drain, and a dead-lettered peer's rows correctly left staged.
- The entity path never invokes a version-wide staging clear; peer staging
  survives. **Executed.**
- Dead-letter units are excluded from the drain per §5.3.2. **Executed.**
- Re-claiming an already-drained entity is a clean no-op (§5.3.3). **Executed.**
- E1 and E2 no longer write a `document_version` row at the fan-out component
  version; both route through `EmptyObsFlushComplete` → `complete_empty_obs_flush`
  → durable `empty_complete` + supersession + `embed_claim` (§5.7, §5.8).
- Legacy/fan-out mutual exclusion interlocks in both directions, and is not racy:
  the legacy handler's `has_obs_flush_fanout` check runs while its own row is
  `running`, which is precisely what `_COUNT_LEGACY_OBS_FLUSH` treats as
  non-terminal, so fan-out cannot slip in behind it.
- The r1 nits on `content_hash` (now from membership) and `doc_id` (now
  `UUID(str(...))` rather than a silent type-mismatch drop) are fixed
  (`e3.py:776-781`).
- Migration `p9_10_0031` applies, downgrades and re-upgrades cleanly on real
  PostgreSQL; table count 69 and the constraint-count bump `{c: 54, p: 69, u: 35}`
  are correct. HEAD claim 5 verified.
- Docs obligation (CLAUDE.md / D66): checked and **not triggered**. D90 changes no
  CLI, API, MCP, config, mount, or connector surface — the stage name is unchanged
  and `remember ops replay <processing-uuid>` is generation-agnostic. No
  `website/src/app/docs/**` page describes work grains or `target_kind`, and
  `/docs/project-status` makes no claim this branch falsifies. D90 is already
  recorded in `decisions.md:3576`.
