# P2 projection hang on BEAM 100K smoke (2026-08-07)

## Symptom

`docker compose --profile operations run --rm projections` (or
`project --plane p2`) opens a `projection_snapshots` row with
`status=building` and then stalls for tens of minutes at near-zero CPU.
Killing the container leaves the row orphaned in `building` until manually
marked failed. P3 never starts when the command is `--plane all` because P2
never returns.

Observed process state: `wchan=do_sys_poll`, open Postgres socket, **no**
parquet files written under `/var/lib/rememberstack/projection-work`.

## Root cause

P2 rebuild blocks inside `GraphExport.watermark()` / graph export on:

```sql
SELECT max(ingested_at)
FROM memory_v1.graph_edges_visible_history
WHERE deployment_id = $1;
```

That view filters `memory_v1.facts_visible_history` through **two**
`EXISTS` semi-joins on `memory_v1.entities_current`. The
`entities_current` definition is extremely heavy: for every active entity it
proves provenance via mentions → chunks → document versions → lateral
`resolution_decisions` → survivor, plus a recursive survivor CTE and
document-section joins.

`EXPLAIN` of the watermark query on the smoke DB estimates cost on the order
of **~10⁷** with deep nested loops and recursive CTEs — even with only ~195
relations and ~774 entities. The rebuild *does* set `jit=off` and collapse
limits inside `graph_export`, but the plan remains pathological.

This is **not** “still building graph offline with no logs.” It is a
**stuck / impractically slow SQL read** on the memory_v1 invariant views
before any graph load work begins.

## Evidence (lab stack, 2026-08-07)

- `pg_stat_activity`: multiple `active` backends for
  `max(ingested_at) FROM memory_v1.graph_edges_visible_history` aged 4–40+
  minutes.
- `SELECT count(*) FROM memory_v1.entities_current` timed out at 15–60s.
- Base tables are small: `relations` 195, `entities` 774, `mentions` 3502,
  `chunks` 749 — so the cost is planner/view expansion, not data volume.

## Workarounds (operator)

Scope every cleanup to the **target deployment** and avoid killing the current
backend or unrelated sessions:

```sql
-- 1) Cancel only stuck export queries for this database (not this session)
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = current_database()
  AND pid <> pg_backend_pid()
  AND state = 'active'
  AND query ILIKE '%graph_edges_visible_history%';

-- 2) Fail only orphan building snapshots for the lab deployment
UPDATE projection_snapshots
SET status = 'failed',
    validation = jsonb_build_object(
      'gate', 'exception',
      'error', 'orphaned building after operator terminate'
    )
WHERE status = 'building'
  AND deployment_id = '11111111-1111-4111-8111-111111111111';
```

3. Answer/score paths that only need **P1** (claim search) can proceed without
   P2/P3.

## Follow-up engineering

Done (follow-up PR after dual review):

- `SET LOCAL statement_timeout = '120s'` inside `graph_export` so hangs become
  exceptions that `mark_failed` can record.
- Watermark + Entity/RELATES/IS_DOCUMENT export rewritten to use base tables +
  TEMP `graph_survivor` instead of `memory_v1.entities_current` /
  `graph_edges_visible_history`.

Still open:

- Materialize or simplify `entities_current` for interactive query-space use
  (export path no longer depends on it).
- Progress logging per export table.
- Startup sweep for stale `building` rows older than N minutes.
