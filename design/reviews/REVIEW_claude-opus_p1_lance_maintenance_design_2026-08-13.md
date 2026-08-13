# Design review — D91 P1 Lance bulk writes and two-layer maintenance

**Reviewer:** claude-opus
**Date:** 2026-08-13
**Branch:** `feat/d90-entity-obs-flush-fanout` (docs untracked)
**Under review:**
`plan/designs/p1_lance_maintenance_design.md` (binding draft, proposed D91),
`plan/analysis/p1_lance_maintenance_analysis.md` (non-binding)
**Verified against:** `src/rememberstack/adapters/selfhost/lance.py`,
`src/rememberstack/workers/p1.py`, `src/rememberstack/spine/backfill.py`,
`src/rememberstack/ports/p1_index.py`, `src/rememberstack/model/queue.py`,
`src/rememberstack/spine/catalog_contract.py`,
`src/rememberstack/spine/work_ledger.py`, `src/rememberstack/spine/readiness.py`,
`src/rememberstack/profiles/selfhost.py`, `compose.yaml`,
`plan/analysis/workers.md` §6.3, `plan/analysis/lance_indexing_maintenance.md`,
plus live probes of the pinned `lancedb==0.34.0` (`uv.lock:868`)

## Verdict

**REQUEST_CHANGES**

## Summary

The diagnosis is right and I could reproduce the reasoning end-to-end in the
code. `update_fact_metadata` really does loop `table.update` once per fact
(`lance.py:283-311`), really is called once per document across the whole
affected fact set inside `label_lock` (`workers/p1.py:290-294`), and the only
maintenance that exists is a process-local counter on the write path
(`lance.py:891-918`) plus a one-shot `build_search_indexes()` behind the
backfill barrier (`backfill.py:109-125`). Compose ships no maintenance route.
The three chosen moves — batch the metadata write, split light `optimize` from
heavy `create_index`, and give maintenance its own ledger-backed worker — are
the correct family of fix, and `workers.md` §6.3 already named the gap.

I also verified the thing most likely to be silently wrong in a design that says
"replace `update` with `merge_insert`": **a partial-column, matched-only merge
does not wipe vectors.** On the pinned `lancedb==0.34.0`, a payload carrying
only `(fact_id, kind, status, valid_from_us)` with `when_matched_update_all()`
and no insert clause leaves `label` and `vector` intact. §5.2.1's Option A is
sound, and its prohibition on `when_not_matched_insert_all()` is necessary
rather than cautious — with the insert clause, an unmatched key is written as a
row with `label=None, vector=None`. Both behaviours are reproduced below.

What blocks the design is not the write-path decision; it is everything the
worker half asserts about the ledger without checking it. Four items are
independently sufficient to break a deployment on the day PR4 lands: the
maintenance unit is scoped to a `deployment` while the physical objects it
maintains are deployment-agnostic Lance tables shared by every deployment under
one `lance_root` (B1); wiring the stage into `_expected_components` as §5.5.4
instructs makes every document version report `missing` forever (B2); the lane
choice is left as "steady … backfill allowed", and the backfill option
permanently deadlocks the drain barrier that this very design depends on (B3);
and the failure contract in §9 is built on a heartbeat/reaper discipline that
does not exist anywhere in `src/rememberstack`, which combined with §5.5.3's
coalesce-on-`running` rule converts a single crashed rebuild into permanent,
silent loss of all future maintenance (B4).

Two more are design-content gaps rather than wiring bugs: batching fixes commit
and fragment count but **not** unindexed-tail growth, because a merge update is
a delete-and-reinsert — so §5.2.2's "v1.1: skip unchanged" is the load-bearing
part, not the polish (B8); and the design asserts an `ensure_search_indexes()`
contract without enumerating the index set, which the companion rulebook names
as the review bar and which today is genuinely incomplete (no index on the facts
join key, no index set at all for `entities`) (B9).

None of this touches the decision. Bulk merge, the two-layer split, the separate
worker, and the three-job-family separation should all survive unchanged.

---

## Blocking issues

### B1 (P0) — The maintenance unit is deployment-scoped; the objects it maintains are not

**Anchors:** design §5.5.1 (`target_id = deployment_id`, `p1_maintain_units`
keyed on `deployment_id`), §5.6 (`p1_lance_table_stats` keyed
`(deployment_id, table_name)`), §5.7 rule 2
(`pg_advisory_lock(deployment_id, 'p1-lance-heavy')`), §5.5.3 coalesce, §10.
**Code:** `lance.py:75-80` (one connection per `lance_root`);
`lance.py:1040-1044`, `:1108-1112` (`table_count`/`row_count` are
`count_rows()` over the whole table); `lance.py:944-956` (`_build_vector_index`
sizes `num_partitions` from `table.count_rows()`); `ports/p1_index.py:288-294`
(`build_search_indexes()` takes no deployment); every P1 row carries
`deployment_id` as an ordinary *column* (`lance.py:86-101, 246-269`), not as a
table boundary.

`chunks`, `claims`, `facts`, `entities` are four physical datasets under one
`lance_root`, holding every deployment's rows. `optimize()`, `create_index`,
fragment counts and row counts are all properties of the *table*, not of a
deployment. Scoping the unit, the stats, and the lock to `deployment_id`
therefore gets three things wrong at once:

1. **The stats baseline is not what it claims.** `p1_lance_table_stats.row_count`
   / `last_heavy_row_count` per `(deployment_id, table_name)` would each record
   the *whole table's* count. `heavy_rebuild_row_growth_pct` (§5.4) then fires
   once per deployment for the same 25% growth, and each unit performs the same
   global IVF retrain.
2. **The heavy lock does not serialize heavy work.** §5.7 rule 2 exists to stop
   two heavy rebuilds colliding. Keyed on `deployment_id`, it stops two rebuilds
   *of the same deployment* — which cannot happen anyway once §5.5.3 coalesces
   one pending heavy per deployment — and permits N deployments to retrain the
   same `facts` index concurrently.
3. **Coalescing bounds nothing physical.** "one pending light unit per
   deployment" still allows N concurrent `optimize()` calls on one table.

`BackfillFinalizer.build_search_indexes(*, deployment_id)` (`backfill.py:109`)
already shows the seam: the *barrier* is per-deployment, the *port call* it
delegates to is not (`ports/p1_index.py:292`). The design should keep that
shape.

**Required change.** Bind the maintenance unit to the physical grain — the
`(lance_root, table)` pair — with `deployment_id` retained only for ledger
routing and ops attribution, and key both `p1_lance_table_stats` and the heavy
advisory lock on the table. If a deployment-scoped unit is kept deliberately
(e.g. because self-host is single-deployment by construction), say so as a
binding statement with its consequence for the cloud profile under D60/D61 —
do not leave a per-deployment growth policy evaluating table-global numbers.

### B2 (P0) — Wiring the stage into `_expected_components` makes every version permanently not-ready

**Anchors:** design §5.5.4 ("Wire handler in `profiles/selfhost.py` `_handler` +
`_expected_components`"), §6.4.
**Code:** `profiles/selfhost.py:877-906` (`_expected_components`);
`spine/readiness.py:188-215`.

`_expected_components` is not a registry of composed handlers. Readiness
iterates it **per document version**:

```
for version_id in version_ids:                      # readiness.py:190
    for stage, component_version in self._expected: # readiness.py:192
        row = by_key.get((version_id, stage, component_version))
        status = "missing" if row is None else ...
...
ready=all(item.status in {"succeeded","skipped"} and item.finished_at is not None ...)
```

`by_key` is built from `_VERSION_WORK`, which reads `document_version` target
rows. A maintain row whose `target_id` is a deployment (or a maintain-unit id)
can never key on a version id, so adding the stage to `_expected_components`
gives every version `status="missing"`, `finished_at=None`, `ready=False` —
forever, for every document, with no failing job anywhere. That propagates to
`/readiness`, to the lifecycle and connector waits that consume it, and to
benchmark gating.

**Required change.** State in §5.5.4 that `maintain_p1_index` is explicitly
**not** part of `_expected_components`, with the one-line reason (it is
deployment/table work, not per-version work, and readiness derives per-version
status). Then say where maintenance health *is* surfaced instead — §8's metrics
plus a maintain-specific ops read. The profile changes that *are* required are
`_SUPPORTED_WORKER_STAGES` (`profiles/selfhost.py:52-64`) and `_handler`
(`:625`), and §5.5.4 should name those two precisely.

### B3 (P0) — The lane is left open; `lane=backfill` permanently deadlocks the barrier this design relies on

**Anchors:** design §5.5.1 (`lane | steady for continuous; backfill allowed for
post-migration catch-up`), §5.5.4 (`worker --stage maintain_p1_index`), §5.5.3
self-seed.
**Code:** `profiles/selfhost.py:570` (`worker_loop` hardcodes
`lane=ProcessingLane.STEADY`); `catalog_contract.py:276-302` (`UNLANED_STAGES`
+ `lane_is_valid`); `backfill.py:160-168` (`_COUNT_UNRESOLVED`);
`work_ledger.py:1386-1400` (`_CLAIM_SELECT` matches
`lane IS NOT DISTINCT FROM :lane`).

Three separate problems ride on this one unresolved field:

1. **`lane='backfill'` is a deadlock, not an option.** The finalizer refuses
   maintenance while *any* backfill-lane row for the deployment is not
   `succeeded`/`skipped`:
   `WHERE lane = 'backfill' AND status NOT IN ('succeeded','skipped')`. A
   backfill-laned maintain unit is itself such a row, so
   `BackfillFinalizer.build_search_indexes` raises `BackfillNotDrainedError` and
   can never be satisfied — and §5.5.3's self-seed rule guarantees a pending
   unit exists most of the time, making it permanent.
2. **Nothing would claim it either way, as written.** `worker_loop` passes
   `lane=STEADY` unconditionally, so a backfill-laned or unlaned unit is never
   selected by `_CLAIM_SELECT` and the new compose service idles silently.
3. **The designed estate says unlaned.** `workers.md` §6 places
   `p1_batch_rebuild` + compaction in *Plane P — projection workers
   (scheduled)*, and every scheduled/deployment-orchestration stage in this
   codebase is unlaned: `build_snapshot`, `detect_communities`, `hard_forget`
   are all in `UNLANED_STAGES`. Choosing `steady` puts a Plane-P job on a
   Plane-E route.

**Required change.** Bind one lane. I recommend unlaned (add
`maintain_p1_index` to `UNLANED_STAGES`, consistent with `hard_forget`), bind
the matching `worker_loop`/`_SUPPORTED_WORKER_STAGES` change in §5.5.4, and
state explicitly that `lane='backfill'` is forbidden for this stage **with the
drain-barrier reason** — otherwise someone will reach for it during exactly the
post-migration catch-up §5.5.1 invites.

### B4 (P0) — §9 rests on a reaper that does not exist, and §5.5.3's coalesce turns one crash into permanent silent stall

**Anchors:** design §9 row "Stuck `running` maintain lease" ("Mirror
processing_state discipline: worker session end fails/requeues"), §5.4
(`maintain_lease_heartbeat_s`), §5.5.3 coalesce ("if a pending/running light
unit exists for deployment, do not insert another"), §5.5.2 self-seed ("if no
pending unit and last success older than …"), §13 open question 5 ("may ship as
follow-up PR outside D91 core").
**Code:** `grep -rn "heartbeat\|reaper\|stale_running" src/rememberstack` returns
one unrelated connector column and nothing else; `work_ledger.py:111-169` +
`_CLAIM_START` set `status='running'`, and only `complete()`/`fail()` leave it;
`adapters/selfhost/queue.py:120-137` (`run_for`) has no session-end requeue.

The premise is false: there is no processing_state discipline to mirror. A
worker killed mid-handler leaves `running` forever — which is precisely the
`embed_claim` zombie the analysis reports at §1.3. The design then builds three
things on top of that false premise:

- §9's "Crash mid-`create_index` … next heavy unit repairs" — there is no next
  unit, because the crashed one is still `running`.
- §5.5.3's coalesce suppresses new units while a `pending`/`running` one exists,
  and §5.5.2's self-seed only fires "if no pending unit". A single zombie
  therefore suppresses **all** future light and heavy maintenance for that
  deployment, permanently.
- The symptom is invisible: no failing job, no dead letter, just a tail that
  grows and a `p1_lance_last_heavy_unixtime` gauge nobody has alerted on yet.

Heavy rebuild is the longest-running handler in the estate, i.e. the one most
likely to be interrupted by a container restart — so this is the expected path,
not a corner.

Note also that a heartbeat column on `p1_maintain_units` (§9) cannot fix a
ledger row: `processing_state` is the D67 work truth, and a reaper writing
`failed` there from a side table's timestamp is a second authority for work
state.

**Required change.** Pick one and bind it:

- bring the heartbeat + reaper into D91 core, on `processing_state` (and say so
  in §15 as its own PR before PR4 — it cannot stay open question 5); **or**
- make coalesce consider only `pending` plus `running` newer than a stated age
  cutoff, and state the double-run safety argument explicitly (`optimize()` and
  `create_index(replace=True)` are idempotent, so a duplicate maintain is safe
  — that sentence is what makes the weaker rule acceptable, and it is missing).

Either way, §9's "mirror processing_state discipline" must be deleted or
replaced with what actually happens today.

### B5 (P1) — `delete_unverified=True` on the purge path becomes a corruption hazard once a second process maintains the same dataset

**Anchors:** design §5.3 ("hard-forget paths may use 0 as today"), §5.5.3 row
"Post hard-forget purge", §5.7, §9.
**Code:** `lance.py:1095-1106`
(`lance_table.optimize(cleanup_older_than=timedelta(0), delete_unverified=True)`
per table per purge); `work_ledger.py:126-134` (`ForgetInProgressError` is
raised at **claim** time only).
**Upstream contract** (`lancedb.table.Table.optimize` docstring, 0.34.0):

> `delete_unverified` … **warning:** This should only be set to True if you can
> guarantee that no other process is currently working on this dataset.
> Otherwise the dataset could be put into a corrupted state.

Today that guarantee is already thin (several compose workers hold
`LanceChunkIndex` over the same volume). This design makes it strictly worse by
adding a process whose whole job is long-running writes to those datasets. The
forget gate does not save it: `claim_one` refuses to *claim* non-`hard_forget`
work while a forget is open, but a maintain unit claimed a minute earlier keeps
running through the purge.

§5.3 waves this through as "hard-forget paths may use 0 as today" without noting
that `delete_unverified` travels with it.

**Required change.** Bind the interaction in §5.7 and §9: either the purge takes
the same maintenance lock (and the maintain handler must therefore be
interruptible/short-unit enough to release it), or `delete_unverified` is
dropped in favour of the 7-day age rule, or the design states the quiesce
requirement for purge. State the corruption risk, not just the disk-space one.

### B6 (P1) — §5.5.1 is a transcript of a decision, not a decision (CLAUDE.md Rule 1)

**Anchors:** design §5.5.1 in full.

The section contains, in order: a `target_kind` cell that argues with itself
("**v1 preferred:** `target_kind = document` is wrong … **or** use
`target_kind = snapshot` only if already abused — **binding:** add
`deployment`"), an idempotency discussion offering (A) and (B), a "**Binding
choice for v1:** (B) time-bucketed or sequence payload", then "or explicit
`enqueue_maintain(...)` … **implementation must use the ledger's real unique
key**", then "Inspect current unique key", then three more bullets including
"encode mode+period into `component_version` (ugly)", and finally a *different*
"**Binding v1 protocol**" built on a `p1_maintain_units` table. Two of those are
labelled binding and they are not the same protocol.

It is also wrong on a fact it half-checks: the time-bucketed **`content_hash`**
option never provided uniqueness at all, because `content_hash` is not part of
the key —
`UNIQUE (deployment_id, target_kind, target_id, stage, component_version)`
(`p0_02_0002_infrastructure_registries.py:94`), carried "for diagnostics/replay"
per the table comment.

Rule 1 asks for a doc a stranger can read cold. Delete the deliberation, state
the one protocol (unit table + `target_id = unit_id` + the chosen
`processing_target` value), and keep the rejected shapes in §12 with one line
each on why.

### B7 (P1) — Pervasive `v1` / phase framing in a binding design (CLAUDE.md Rule 2)

**Anchors:** §1.3 ("deployment-scoped (v1)", "v1 ships **one stage, mode in
payload**"), §4.2 ("in v1"), §5.2.2 ("v1 minimum … **v1.1**: skip unchanged"),
§5.4 ("Default (v1 proposal)"), §6.1 ("optional v1"), §12 ("S3-backed Lance in
v1"), §13, §14 K7, and the analysis §4 ("Reject v1").

Rule 2 is non-negotiable for design and decision docs. Sorting the instances:

- **Genuine scope boundaries** — object storage for Lance, Enterprise
  auto-index, ANN family change. Keep the content; restate as *non-goals /
  documented alternatives* without "v1" (§4.2 already nearly does this).
- **Genuine simplification** — one stage with a mode in the payload instead of
  two stages. Keep; drop "v1 ships" and say the split is a documented
  alternative if ops later wants separate autoscaling.
- **Real deferrals, which Rule 2 forbids** — §5.2.2's "v1 minimum: … still
  refreshes the full document set / v1.1: skip unchanged" is the important one
  (see B8), and §6.1's "optional v1" on `rebuild_text_indexes`.

Also per Rule 2, §5.4's numbers should be labelled as *starting points to be
measured*, not "v1 proposal" — see also N4, since two of them are copied from a
vendor rule of thumb rather than measured here.

Build sequencing belongs in §15 / `plan/plans/`, which already exists and is
fine.

### B8 (P1) — Batching fixes commits and fragments but not tail growth; the design does not model the churn it creates

**Anchors:** design §3 (cites `tables/update`: updated rows leave the index),
§5.2.2 ("v1 minimum: fix batching even if the post-pass still refreshes the full
document set"), §5.4 thresholds.
**Upstream contract** (`lancedb/table.py:1275-1277`, 0.34.0):

> Please note that the data may appear to be reordered as part of this
> operation. This is because **updated rows will be deleted from the dataset and
> then reinserted at the end** with the new values.

So a matched-only merge is a delete-and-reinsert per row, exactly like `update`.
Every fact refreshed by the metadata pass leaves the vector and scalar indexes
and joins the unindexed tail. Batching collapses N commits into
`ceil(N/500)` and stops the fragment storm — the incident — but the *tail* cost
per document is unchanged.

That matters because §5.2.2 keeps refreshing the whole affected fact set for
every document (`_SELECT_FACT_METADATA_FOR_DOCUMENT` in
`fact_catalog.py:905-970` returns every relation and observation touched by the
document *and* everything adjudicated against them), forever, at ingest cadence.
The steady-state input to every threshold in §5.4 is therefore
"≈ all facts of every document, re-tailed on every label job" — and that number
is never stated, so the thresholds cannot be sanity-checked by a reader.

"Skip rows whose Postgres scalars already match what P1 holds" is a
*simplification* (it removes work at any scale), not a phase. Under Rule 2 it
belongs in the design.

**Required change.** Bind the unchanged-skip as design content, or state the
churn budget explicitly and show §5.4's thresholds absorb it. Either way, add
the delete-and-reinsert property to §5.2.1 — a reader will otherwise assume a
merge "update" is an in-place scalar edit, which is the same wrong model that
produced the incident.

### B9 (P1) — `ensure_search_indexes()` is made binding without enumerating the index set, and today's set is incomplete

**Anchors:** design §5.3 (Ensure row: "scalar + FTS + vector if missing"), §5.2.1
("Scalar indexes: ensure BTREE/BITMAP on join and filter columns … *already
partially done on upsert paths*"), §5.5.1 payload (`tables` includes
`entities`), §6.1.
**Companion rulebook** (`lance_indexing_maintenance.md`): R1 — "a scalar index
on every column that ever appears in a `.where()` or as a `merge_insert` join
key … diff the set of filtered columns against the set of indexed columns — it
must be empty"; R6 — "when merging, index the join keys first"; §4 — "**The P1
table designs must enumerate the index set per table** as part of the schema,
not leave it to implementation … 'The schema is done when the index set is
written down' is the review bar".

Verified gaps in `lance.py` as it stands:

| Table | Gap |
| --- | --- |
| `facts` | **No index on `fact_id`** — the selective member of this design's own merge join key, and a `.where()` column in `_fact_membership_clause` (`:1209-1220`) and purge/verify (`:1031-1034`). `upsert_facts` ensures `deployment_id`, `kind`, `status`, and the four time columns (`:271-281`); `build_search_indexes` creates only `deployment_id` + `kind` (`:829-833`). |
| `entities` | `upsert_entities` (`:958-973`) ensures **nothing** and never calls `_maintain_indexed_tail`; `build_search_indexes` (`:799-833`) skips the table entirely. The entities table therefore has **no vector index, ever**, and `search_entities_scored`'s `type` filter (`:779-784`) is unindexed. |
| `facts.kind` | Created as `BTree` by `_ensure_scalar_index` on the write path (`:272`) but as `Bitmap` by `build_search_indexes` (`:832`), and both record the same `_scalar_indexes_ready` key (`:844-862`) — so which index exists depends on which ran first. R2 says `BITMAP` for this cardinality. |

The `fact_id` gap is not cosmetic for this design specifically: the whole PR1
benchmark is "one merge of 500 keys beats 500 updates". Without an index on the
join key, each merge scans a multi-GB table (R6 exists for exactly this), and
PR1 may under-deliver for a reason the design never named.

Making `ensure_search_indexes()` binding is the right call. It just has to say
what it ensures.

**Required change.** Enumerate the per-table index set (column → index type) in
§5.3 or §6.1, including the facts join key and the entities table, and note that
extending `ensure` to `entities` is a *behaviour change* at the backfill barrier
(entity ANN search stops being exhaustive), so it is reviewed rather than
discovered.

### B10 (P2) — Duplicate join keys in one batch now fail the whole batch

**Anchors:** design §5.2.1, §16 tests.
**Verified** on `lancedb==0.34.0` / `lance-8.0.0`: two source rows matching one
target row raise

```
RuntimeError: lance error: Invalid user input: Ambiguous merge inserts are
prohibited: multiple source rows match the same target row on
(fact_id = "a", kind = "relation").
```

Note the Python docstring is stale on this point — `lancedb/merge.py:43-55`
still says "the behavior is undefined … causes multiple copies of the row to be
created"; the shipped Rust engine errors instead. Per-row `update` was
duplicate-tolerant; a 500-row batch is not, so the blast radius of one duplicate
moves from one fact to one batch (and, with §9's "resume job re-reads Postgres
metadata", to a permanently failing job if the duplicate is deterministic).

`_SELECT_FACT_METADATA_FOR_DOCUMENT` should not emit duplicates today — the CTEs
use `UNION`, the two arms are joined one-row-per-relation / per-observation, and
the `UNION ALL` between them cannot collide because `kind` differs. So this is a
latent constraint, not a present bug — which is exactly the kind of thing a
binding design should pin before someone adds an arm to that query.

**Required change.** State "batches are deduplicated on
`(deployment_id, kind, fact_id)` before merge" in §5.2.1, with the tie-break
rule, and add the case to §16.

### B11 (P2) — The migration list omits the executable catalog contract, so PR3 as scoped fails CI

**Anchors:** design §6.3, §7, §15 PR3 ("Migration: enums + `p1_maintain_units`
(+ stats table); … Migration upgrade/downgrade where supported").
**Code:** `spine/catalog_contract.py` is executable and exact —
`EXPECTED_TABLES` (`:101-171`), `EXPECTED_INDEXES` (`:172-265`),
`EXPECTED_CONSTRAINT_COUNTS = {"c": 54, "f": 128, "p": 69, "u": 35, "x": 1}`
(`:334`), `commented_tables != len(EXPECTED_TABLES)` is a failure (`:563-576`,
so every new table needs `COMMENT ON TABLE`), column comments ≥ 300 (`:577-591`),
and `verify_schema_absent` (`:690`) requires the tables and enums to be gone
after downgrade-to-base.

Adding `p1_maintain_units` (+ optional `p1_lance_table_stats`) therefore also
edits `EXPECTED_TABLES`, the per-contype constraint counts, any named index, and
the table/column comments. Adding enum *values* is fine and precedented
(`p3_01_0008_document_version_target.py:35-38`,
`p6_06_0015_authored_dispatch_runtime.py:16`) and needs no contract edit, since
`EXPECTED_ENUMS` lists types, not values.

Separately: if the maintain component version is to be registered under D1
(`spine/component_versions.py`), there is no fitting `pipeline_component` enum
value — `model/component_version.py:25-50` has none for maintenance. Say whether
D91 registers one or deliberately does not (the `processing_state` reference is
only a logical FK, so "does not" is defensible — but it should be stated).

### B12 (P2) — The PR plan has no same-PR docs row (D66)

**Anchors:** design §15 (PR4 ships `worker-maintain-p1`; PR6 "runbook notes"),
§11 row 5.
**Repo rule:** CLAUDE.md, *The docs site ships with the code (D66)* — "Any PR
that changes user-facing behavior — CLI commands, API/MCP surface,
configuration, mounts, connectors, deployment … updates the affected
`website/src/app/docs/**/page.mdx` in the *same PR*", and keeps
`/docs/project-status` truthful.

PR4 adds a compose service and PR5/PR6 add operator-visible knobs and
procedures; §5.4 adds a whole settings group. Target pages already exist:
`website/src/app/docs/deployment/page.mdx` (which documents the compose
lifecycle), `configuration/`, `troubleshooting/`, `project-status/`. "Runbook
notes" in PR6 is not the same obligation and lands two PRs late.

---

## Non-blocking nits

- **N1 — `optimize(retrain=True)` is a deprecated no-op on the pinned version.**
  `Table.optimize(..., retrain: bool = False)` exists in 0.34.0 and its docstring
  says "This parameter is no longer used and is deprecated". This *confirms*
  §5.3's split (light genuinely cannot retrain; `create_index` is the only
  retrain path) — worth one sentence in §5.3 so a future implementer who reads
  the LanceDB reindexing page does not "simplify" heavy into `retrain=True`.
- **N2 — §13 open question 2 is answerable today.** `Table.stats()` is public in
  0.34.0 and needs no `pylance`; it returns
  `{'total_bytes', 'num_rows', 'num_indices', 'fragment_stats': {'num_fragments',
  'num_small_fragments', 'lengths': {...}}}`. `num_small_fragments` is a better
  trigger for `optimize_fragment_count` than raw fragment count and matches
  rulebook R4's "small fragments ≥ M". §8's `p1_lance_fragment_count` should
  name `stats().fragment_stats` as its source and drop the "if measurable"
  hedge.
- **N3 — the write-path probe is itself a cost.** `_maintain_indexed_tail`
  (`lance.py:891-918`) calls `list_indices()` **and** `index_stats()` per index
  on *every* write before deciding to skip. §6.2's "demote write-path
  `_maintain_indexed_tail`" should name this: one `stats()` call replaces the
  loop.
- **N4 — two defaults are a vendor rule of thumb, not a measurement.**
  `optimize_mutations=20` and `optimize_unindexed_rows=100_000` are verbatim from
  the `Table.optimize` docstring ("run optimize if you have added or modified
  100,000 or more records or run more than 20 data modification operations"),
  which is also where `lance.py:52-56` got them. Say so, so nobody treats them as
  BEAM-derived.
- **N5 — rollout gap between PR1 and PR4.** After PR1 the writer is fast and
  (per B8) grows the tail at least as quickly per document as today, while the
  only folding mechanism is still the process-local counter — and §13 open
  question 4 leaves "may the write path optimize at all" unresolved. §11 should
  bind the interim: what runs maintenance between PR1 and PR4, and on what
  trigger.
- **N6 — port/adapter naming.** §5.5.2's `open LanceChunkIndex(root=lance_root)`
  reads oddly for a handler that maintains four tables; the class name is a
  historical artifact worth a note. And §6.1 should state that
  `P1IndexMaintenancePort` stays deployment-free while its *callers* are
  deployment-scoped (`backfill.py:109` vs `ports/p1_index.py:292`) — which is
  also B1's point, stated positively.
- **N7 — forget blocks maintenance claims.** §5.5.3's "post hard-forget purge →
  enqueue light" is correct but incomplete: `claim_one` raises
  `ForgetInProgressError` for every non-`hard_forget` stage while a forget is
  open (`work_ledger.py:126-134`), so that unit waits for the forget to close.
  That is the right behaviour; say it.
- **N8 — §16 test table additions.** (a) duplicate join key in one batch (B10);
  (b) **metadata merge preserves `label` and `vector`** — this is the property
  that makes matched-only safe, and the exact property a well-meaning refactor to
  `when_not_matched_insert_all()` destroys (verified: the unmatched row lands as
  `label=None, vector=None`); (c) `MergeResult.num_updated_rows` vs batch length
  as the `metadata_miss` assertion. There is already a natural anchor test at
  `src/tests/adapters/test_lance_retrieval.py:196`, which updates a fact's
  metadata and then re-runs `search_facts_scored` — i.e. it would catch a wiped
  vector today. Cite it in PR1.
- **N9 — §5.2.1 Option B is unnecessary.** A matched-only merge with a key that
  is absent from the table silently no-ops and reports
  `num_updated_rows=0, num_inserted_rows=0` (verified). So `metadata_miss` is
  derivable for free from the result; the "bounded id lookup" option costs a
  second pass for nothing. Bind Option A and delete B (or keep it in §12).
- **N10 — analysis §1.4, "compose covers convert → label_relation only".**
  Compose actually runs eleven worker services (`compose.yaml:113-188`:
  convert, structure, chunk, embed_chunk, extract_claims, normalize_relations,
  adjudicate_observations, adjudicate_supersession, embed_claim, reconcile,
  label_relation). The conclusion — no maintenance route — is exactly right; the
  phrasing reads as if the estate were a linear chain.
- **N11 — analysis §7, "Partial-column merge_insert null insert | Medium if
  misused"** can be upgraded from speculation to a verified statement now (with
  the insert clause: `label=None, vector=None`; matched-only: no-op). That is the
  single most valuable line in the analysis for a future implementer; make it
  concrete.

---

## Strengths

- **The root cause is correctly identified and correctly bounded.** The analysis
  separates "embeds looked done but the job was not" (§1.2) from the actual hot
  path, and §1.5 explicitly lists what is *not* the problem — including the
  important one, that retrieval stays *correct* under an unindexed tail and only
  degrades in latency. That is the difference between a panic fix and a design.
- **The risky part of the write-path change is right.** Matched-only merge with
  a partial payload preserves `label` and `vector` on the pinned version
  (verified), the join key matches `upsert_facts`' key exactly
  (`lance.py:246-249`), and the prohibition on the insert clause is stated
  before anyone can trip over it. Most "just batch it" designs get this wrong.
- **The light/heavy distinction is real and version-verified**, not a stylistic
  split: `optimize()` cannot retrain on this version at all (`retrain` is a
  deprecated no-op), so §5.3's "light is not a substitute for heavy" is literally
  true rather than merely advisable.
- **§5.5.5 and analysis §6 separate the three job families** (light maintain /
  heavy reindex / content rebuild) and say what happens if they are confused.
  That is the failure this codebase is actually exposed to, given
  `build_search_indexes` is currently both the bootstrap and the only retrain.
- **§5.8 (physical storage) is exactly the cold-reader content Rule 1 asks for**
  — the Docker volume path, the backup/restore choice, and the "cloud may mount a
  network disk under the same port" boundary are all things a future operator
  cannot derive from the code.
- **§8 is concrete and declared ship-required** rather than a follow-up, and the
  dual trigger in §5.3/§5.4 matches rulebook R4 exactly.
- **The alternatives table (analysis §4, design §12) is honest** and each row is
  checkable against the code — including the correct rejection of "one huge
  `IN (...)` update", which would also have hit the same delete-and-reinsert
  behaviour with none of the keyed-merge benefits.

---

## Checklist against design contracts

| # | Contract | Result | Note |
| --- | --- | --- | --- |
| 1 | Two-layer maintain model complete (light ≠ heavy, both defined) | **Pass** | §5.3 defines both in operational terms; verified that `optimize()` genuinely cannot retrain on the pinned version (`retrain` deprecated), so the split is load-bearing, not stylistic. Add N1's sentence. |
| 2 | Bulk `merge_insert` correctness — vectors/labels not wiped | **Pass (verified)** | Reproduced on `lancedb==0.34.0`: matched-only + partial payload updates only the supplied columns; `label`/`vector` intact. With `when_not_matched_insert_all()` an unmatched key lands as `label=None, vector=None` — §5.2.1's prohibition is necessary. Bind Option A, drop Option B (N9). |
| 3 | Batch semantics fully specified (dedupe, misses, batch failure) | **Fail** | Duplicate join keys now hard-error and fail the whole batch (B10). Miss handling should use `MergeResult.num_updated_rows`, not a second lookup (N9). Neither is in §5.2.1. |
| 4 | Write amplification actually bounded end-to-end | **Fail** | Commits and fragments: yes. Unindexed tail: no — merge update is delete-and-reinsert, and §5.2.2 defers unchanged-skip to "v1.1" (B8). The steady-state churn figure that every §5.4 threshold depends on is never stated. |
| 5 | Index set enumerated for the tables being maintained | **Fail** | Rulebook R1/R6 make this the review bar; §5.3 asserts `ensure_search_indexes()` without a set. Facts has no index on the merge join key `fact_id`; `entities` has no index set at all and no vector index ever; `facts.kind` is BTree-or-Bitmap depending on call order (B9). |
| 6 | Worker/ledger identity resolves to one protocol | **Fail** | §5.5.1 states two different "binding" protocols and argues with itself; the `content_hash` variant never provided uniqueness because `content_hash` is not in the unique key (B6). |
| 7 | Ledger grain matches the physical objects maintained | **Fail** | Units, stats, and the heavy advisory lock are all keyed on `deployment_id`; `optimize`/`create_index`/row counts/fragments are table-global across all deployments under one `lance_root` (B1). |
| 8 | Lane / route binding valid against `lane_is_valid` + worker loop | **Fail** | Left open ("steady … backfill allowed"); `worker_loop` hardcodes STEADY so an unlaned/backfill unit is never claimed, and a backfill-laned unit permanently deadlocks `BackfillFinalizer` via `_COUNT_UNRESOLVED` (B3). `workers.md` §6 puts this work in Plane P, whose stages are unlaned here. |
| 9 | Readiness / profile wiring consequences understood | **Fail** | §5.5.4's `_expected_components` instruction makes every document version report the stage `missing` forever (`readiness.py:188-215`) (B2). The changes actually needed are `_SUPPORTED_WORKER_STAGES` + `_handler`. |
| 10 | Concurrency: writer ↔ maintain | **Concern** | Retry-with-jitter reuse is right (`_LANCE_COMMIT_RETRIES`, `lance.py:58-59, 934-937`) and adequate for optimize/create_index. Unaddressed: a partial matched merge is a full-row rewrite, so two concurrent partial merges on one fact are a read-modify-write pair — today `label_lock` (`workers/p1.py:202`) serializes fact writers per deployment, which the design should state as a standing invariant rather than leave implied. |
| 11 | Concurrency: maintain ↔ hard-forget purge | **Fail** | `delete_unverified=True` (`lance.py:1106`) is documented as safe only when no other process is working on the dataset; `ForgetInProgressError` gates claims, not already-running work (B5). §5.3 blesses the current behaviour without naming the hazard. |
| 12 | Crash / stuck-lease recovery is implementable | **Fail** | §9's premise ("mirror processing_state discipline") is false — no heartbeat or reaper exists anywhere in `src/rememberstack`. Combined with coalesce-on-`running`, one crashed heavy silently stops all future maintenance (B4), while §13 defers the fix outside D91 core. |
| 13 | `BackfillFinalizer` unified onto the shared port | **Pass (with concern)** | §5.3's "`build_search_indexes()` = ensure + heavy for all present tables" is the right unification. Concern: today it covers only chunks/claims/facts (`lance.py:799-833`), so "all present tables" is a behaviour change at the barrier (entities gains a vector index) — review it deliberately (B9). |
| 14 | Migrations complete against the executable catalog contract | **Fail** | §6.3/§15 PR3 omit `catalog_contract.py` (`EXPECTED_TABLES`, per-contype constraint counts, table/column comments, downgrade absence) and the `pipeline_component` question (B11). Enum-value additions themselves are precedented and fine. |
| 15 | Rollout plan realistic | **Concern** | "Ship PR1 first, don't block on the worker" is the right call and PR1 has a real anchor test (`test_lance_retrieval.py:196`). But the PR1→PR4 window has no defined maintenance owner while tails grow at least as fast as today (N5), and §13.4 leaves the governing knob open. |
| 16 | PR plan realistic per-PR | **Concern** | PR1/PR2 are well-scoped and independently shippable. PR3 fails CI as written (B11). PR4 depends on B2/B3 decisions not yet made. PR5's growth policy depends on the stats grain in B1. A reaper PR is missing entirely (B4). |
| 17 | Docs obligation (D66 same-PR) | **Fail** | No `website/src/app/docs/**` row anywhere in §15, though PR4 adds a compose service and §5.4 adds a settings group (B12). |
| 18 | CLAUDE.md Rule 1 (cold-reader legibility) | **Fail** | §5.8, §5.3, §9 are exemplary; §5.5.1 is unreadable as a binding statement (B6), and §5.5.2's handler sketch leaves "record stats … optionally evaluate policy" undefined. |
| 19 | CLAUDE.md Rule 2 (full scope, no phasing) | **Fail** | "v1/v1.1/optional v1/v1 proposal" throughout (B7); one of them (§5.2.2) hides a genuine design decision behind a phase label (B8). |
| 20 | CLAUDE.md Rule 3 (library boundary) | **Pass** | Everything stays in-repo and OSS: no Enterprise dependency, no control plane, no web UI assumed; §5.8 keeps `lance_root` as the port so a cloud disk mounts underneath rather than through a new authority. §10's tenancy statement is right in intent (its mechanics are B1). |
| 21 | Analysis ↔ code accuracy | **Pass (with nit)** | Every code claim in analysis §1.4 and §3 checks out against `lance.py`, `workers/p1.py`, `backfill.py`, `model/queue.py`, and `compose.yaml`. One phrasing nit (N10) and one claim now upgradeable from "if misused" to verified fact (N11). |

---

## What would make this an approve

1. Re-grain maintenance onto the physical `(lance_root, table)` unit — units,
   stats baseline, and the heavy lock — keeping `deployment_id` for routing and
   attribution only (B1).
2. Bind the ledger wiring end-to-end and say what it is *not*: one lane
   (recommend unlaned), the `worker_loop`/`_SUPPORTED_WORKER_STAGES` change,
   `lane='backfill'` forbidden with the drain-barrier reason, and
   `_expected_components` explicitly excluded with the readiness reason
   (B2, B3).
3. Bring stuck-lease recovery into D91 core, or weaken coalesce and state the
   idempotency argument that makes a duplicate maintain safe — and delete §9's
   claim about a discipline that does not exist (B4).
4. Bind the hard-forget purge interaction, naming `delete_unverified`'s
   corruption precondition rather than only its disk cost (B5).
5. Make §5.5.1 one protocol, and enumerate the per-table index set that
   `ensure_search_indexes()` guarantees (B6, B9).
6. Bind unchanged-skip as design content and state the delete-and-reinsert
   property that makes it necessary; drop the v1/v1.1 framing throughout
   (B7, B8).
7. Add the catalog-contract edits to PR3 and a same-PR docs row to PR4 (B11,
   B12).

The decision itself — batched matched-only merge, light `optimize` vs heavy
`create_index`, a dedicated ledger-backed maintenance worker, and three distinct
job families — should survive all seven unchanged.
