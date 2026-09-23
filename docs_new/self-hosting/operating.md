---
title: Operating the pipeline
description: Inspect a self-hosted RememberStack pipeline, replay failed work, release parked conversions, set spend budgets, and repair the graph catalog.
applies_to: [self-hosted]
---

# Operating the pipeline

Every piece of pipeline work is a row in PostgreSQL: which version, which
stage, how many attempts, and what went wrong last. Workers claim these
rows, retry failures, and set aside work that keeps failing. This page shows
how to see that state and act on it.

## Running operator commands

The operator commands are part of the `remember` CLI, under
`remember ops`. They talk to PostgreSQL directly rather than to the API, so
they run inside the engine image, where the database settings already are.
They are hidden unless `REMEMBERSTACK_INTERNAL_OPS` is set.

Read the deployment id from `.env` once per shell:

```bash
DEPLOYMENT_ID=$(grep '^REMEMBERSTACK_SELFHOST_DEPLOYMENT_ID=' .env | cut -d= -f2)
```

Then run each command in the `api` container:

```bash
docker compose exec -T -e REMEMBERSTACK_INTERNAL_OPS=1 api \
  remember ops inspect --deployment "$DEPLOYMENT_ID"
```

Every `remember ops` command prints one JSON document on standard output.
Pipe it through `python3 -m json.tool` to read it.

| Command | What it does |
|---|---|
| `remember ops inspect --deployment ID` | Report pipeline, dead-letter, projection and consistency state |
| `remember ops replay PROCESSING_ID --deployment ID` | Give one dead-lettered item more attempts |
| `remember ops resume-no-route --deployment ID` | Release conversions parked for lack of a route |
| `remember ops rebuild --deployment ID --snapshot-root DIR --version V` | Build a filesystem snapshot into a local directory |
| `remember ops graph-catalog ensure` | Check and repair the PostgreSQL graph definitions |
| `remember ops cost-export --deployment ID` | Print one page of the cost ledger ([Observability](observability.md#cost-export)) |
| `remember budget inspect --deployment ID` | Show spend budgets and how much of each is used |

## Inspect the pipeline

```bash
docker compose exec -T -e REMEMBERSTACK_INTERNAL_OPS=1 api \
  remember ops inspect --deployment "$DEPLOYMENT_ID" | python3 -m json.tool
```

The report has five parts:

| Field | Contents |
|---|---|
| `routes` | For each stage and lane, how many items are in each status (`pending`, `running`, `succeeded`, `failed` while waiting to retry, `dead_letter`, `skipped`). A large `pending` count on one stage shows where the pipeline is behind. |
| `dead_letters` | Items that ran out of attempts: a total, groups by stage and error class, and individual items with their `processing_id`, attempts and `last_error`. |
| `poison_targets` | Items that dead-lettered under two or more component versions: an upgrade did not fix them. |
| `latest_projections` | The latest filesystem snapshot, if any. |
| `currency` | A consistency check between each claim's cached "current" flag and the ledger it is derived from. `mismatch_total` should be 0. |

Lists in the report are capped at `REMEMBERSTACK_OPERATIONAL_SAMPLE_LIMIT`
entries (default 20).

## Retries and dead letters

When a stage fails on an item:

- A **retryable** failure, such as a provider timeout or a `5xx`, is retried
  after a back-off: 2 seconds after the first failure, doubling each time,
  never more than 60 seconds
  (`REMEMBERSTACK_WORK_RETRY_BACKOFF_BASE_S`, `REMEMBERSTACK_WORK_RETRY_BACKOFF_MAX_S`).
- An item gets **3 attempts** in total: the first try and two retries.
- A **non-retryable** failure, such as a file that cannot be converted,
  goes straight to the dead letters.
- An item that uses its last attempt goes to the dead letters.

HTTP 429 answers from OpenRouter are handled inside the provider call and do
not use attempts ([Models and providers](models.md#openrouter-routing-and-limits)).

A dead-lettered item stays where it is. Its version is not ready, and the
work after it in the pipeline waits. Nothing retries it on its own.

### Replay a dead letter

Find the `processing_id` in the `dead_letters.items` of the inspect report,
fix the cause (a key, a model, a converter route), then replay it:

```bash
docker compose exec -T -e REMEMBERSTACK_INTERNAL_OPS=1 api \
  remember ops replay 3f2a9c1e-8b7d-4e21-9a0f-5c6d7e8f9a0b --deployment "$DEPLOYMENT_ID"
```

| Option | Default | Meaning |
|---|---|---|
| `--attempts N` | `1` | How many more attempts to grant |
| `--lane steady\|backfill` | the item's lane | Run it in another lane |
| `--not-before 2026-09-24T08:00:00+00:00` | now | Do not run it before this time |

The command prints the item's new state: its route, `attempts`,
`max_attempts` and `not_before`. The worker for that stage picks it up.

## Parked conversions

An uploaded file whose MIME type has no conversion route is parked with the
reason `no_route` rather than failed. It uses no attempts. After you add a
route and restart with `docker compose up -d`, release it:

```bash
docker compose exec -T -e REMEMBERSTACK_INTERNAL_OPS=1 api \
  remember ops resume-no-route --deployment "$DEPLOYMENT_ID"
```

The output lists the released `processing_id` values. Items whose MIME type
is still unrouted stay parked. See [File formats and converters](converters.md).

## Spend budgets

A budget caps what one pipeline stage may spend on model calls, in US
dollars, within a fixed time window. Before a worker runs an item, it
checks the budget for that stage and lane. If the window's spend has
reached the ceiling, the item is parked until the window ends, and the
worker moves on. Nothing is dropped.

`REMEMBERSTACK_WORK_BUDGETS` is a JSON list; each entry names the
deployment, the stage, the lane (`steady` or `backfill`), the window in
seconds, and the ceiling:

```bash
# engine.env
REMEMBERSTACK_WORK_BUDGETS=[{"deployment_id":"<your deployment id>","stage":"extract_claims","lane":"steady","window_seconds":86400,"ceiling_usd":"5.00"},{"deployment_id":"<your deployment id>","stage":"normalize_relations","lane":"steady","window_seconds":86400,"ceiling_usd":"5.00"}]
```

Only one budget may exist per deployment, stage and lane. Stages without a
budget are not capped.

`compose.yaml` does not pass this variable. Put it in `engine.env`
([Configuration](configuration.md#what-compose-passes-through)): the
workers enforce it, and `remember budget inspect` in the `api` container
reads it.

```bash
docker compose exec -T -e REMEMBERSTACK_INTERNAL_OPS=1 api \
  remember budget inspect --deployment "$DEPLOYMENT_ID"
```

It prints one line per budget: the current window, `spent_usd`,
`remaining_usd`, whether it is `exhausted`, how many items are parked, and
the spend per model tier.

## The graph catalog

The memory's graph is a set of PostgreSQL property-graph definitions over
the stored relations. Migrations create them. If a manual database change
or a restore leaves them missing or different,
`remember ops graph-catalog ensure` compares them with what this release
expects, replays the definitions if needed, and reports the result:

```bash
docker compose exec -T -e REMEMBERSTACK_INTERNAL_OPS=1 api \
  remember ops graph-catalog ensure
```

The output has `ready`, `changed`, `problems_before`, `problems_after` and
the list of `definitions`. It also checks the versions of `pgvector`,
`pg_textsearch` and `pg_partman`.

## When a hard forget is in progress

While a hard forget is running, the API answers `503` with
`{"code": "forget_in_progress"}`, and `replay`, `resume-no-route`,
`rebuild`, the projection build and `mounts` refuse to run. This protects
data that is being removed from being served or copied mid-removal.

A deployment started from `compose.yaml` has no command that starts a hard
forget, so it does not enter this state on its own. The API also refuses to
start if the `forget-manifests` volume holds hard-forget manifests for this
deployment, as it would after restoring data from an installation that ran
a hard forget; see [Upgrades and migrations](upgrades.md#back-up).

remember.dev operates the pipeline for you; there, work parked by a spend
cap resumes after a credit purchase, see [Spend caps and auto top-up](../cloud/spend-controls.md).
