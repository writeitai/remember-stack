---
title: Wait until a document is queryable
description: Poll readiness for the versions you ingested, with a timeout that fits real processing times, and stop on failures instead of waiting forever.
applies_to: [remember.dev, self-hosted]
---

# Wait until a document is queryable

Ingest returns in a second; processing takes minutes. Until it finishes, a
question about the new document gets an answer that does not include it,
and nothing in that answer tells you so. This page shows how to wait for
exactly the versions you sent, how long to wait, and what to do when a
version fails.

Setup is in the [Quickstart](../start/quickstart.md). What each stage does
is in [The pipeline and readiness](../concepts/pipeline.md).

## Wait with the Python client

```python
import remember

client = remember.Client.from_env()

version = client.ingest(
    "notes/2026-09-17-standup.md",
    mime="text/markdown",
    source_kind="file",
    source_ref="notes/2026-09-17-standup.md",
)

report = client.wait_for_readiness(
    [version.version_id],
    timeout=1800,
    poll_interval=15,
)
print(report.ready)
```

`wait_for_readiness(version_ids, *, timeout=30.0, poll_interval=0.5,
require_p3=False)` polls the deployment until every listed version is
ready, then returns the last report. If `timeout` seconds pass first, it
raises `TimeoutError`, whose message includes the last report.

!!! warning
    The default `timeout` is 30 seconds and the default `poll_interval` is
    half a second. A small Markdown file takes minutes to process, so the
    defaults time out on almost every real document while polling twice a
    second. Always pass both. `timeout=1800` (30 minutes) and
    `poll_interval=15` suit single documents; raise the timeout for bulk
    loads.

It checks three capabilities for you: the pipeline stages for your
versions, the search index (`p1`) and the live graph (`live_graph`). Pass
`require_p3=True` only if you also read the published corpus snapshot
(filesystem views on a self-hosted deployment).

`wait_for_readiness` accepts version IDs as strings or UUIDs. A readiness
check covers at most 1,000 versions; split larger lists.

A version with `created=False` needs no new processing, but wait on it
anyway: the earlier run of the same bytes may still be going, and a
finished one returns ready on the first poll.

## Stop on failure

`wait_for_readiness` does not stop when a stage fails. A failed version is
never ready, so the call keeps polling until the timeout. When you wait on
many versions or long timeouts, poll yourself and stop on the first
terminal failure:

```python
import time

import remember
from remember import ReadinessRequirements

TERMINAL = {"failed", "dead_letter"}


def wait_or_fail(client, version_ids, *, timeout=1800.0, poll_interval=30.0):
    require = ReadinessRequirements(pipeline=True, p1=True, live_graph=True, p3=False)
    deadline = time.monotonic() + timeout
    time.sleep(min(30.0, timeout))
    while True:
        report = client.pipeline_readiness(version_ids=tuple(version_ids), require=require)
        if report.ready:
            return report
        failed = [
            (version.version_id, stage.stage, stage.status)
            for version in report.versions
            for stage in version.stages
            if stage.status in TERMINAL
        ]
        if failed:
            raise RuntimeError(f"processing failed: {failed}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"not ready after {timeout}s: {report.capabilities}")
        time.sleep(poll_interval)


client = remember.Client.from_env()
report = wait_or_fail(client, [version.version_id])
```

`pipeline_readiness` takes the version IDs as a tuple of `UUID` values and
a `ReadinessRequirements` naming all four capabilities.

## Read the report

`pipeline_readiness` and `wait_for_readiness` return a
`PipelineReadinessReport`:

| Field | Meaning |
|---|---|
| `ready` | `True` when every required capability is ready. |
| `versions[]` | One entry per version: `version_id`, `ready`, and `stages[]`. |
| `versions[].stages[]` | `stage`, `component_version`, `status`, `finished_at`. |
| `capabilities` | `pipeline`, `p1`, `live_graph`, `p3`, each with `required`, `ready`, `checked_at` and a `reason`. |
| `model_bindings`, `build_revision`, `document_binding_generation` | Which code and models are serving, for your records. |

A stage `status` is one of `missing`, `pending`, `running`, `succeeded`,
`failed`, `dead_letter`, `skipped`. A version is ready when every expected
stage has `succeeded` or been `skipped` and has a `finished_at`.

Capability reasons when not ready:

| Capability | Reason | Meaning |
|---|---|---|
| `pipeline` | `stage_incomplete` | At least one stage of one version has not finished. |
| `p1` | `search_channel_incomplete` | The search index is not ready. |
| `live_graph` | a `graph_…` reason, such as `graph_catalog_mismatch` | The live graph failed its catalog or health check. This is a deployment problem, not a problem with your document. |
| `p3` | `corpus_snapshot_incomplete` | No published corpus snapshot newer than your versions. |

The check reads state; it never starts or speeds up work.

## When a version fails

- **`failed`** means a stage gave up on this attempt; **`dead_letter`**
  means it ran out of attempts. Neither heals by waiting.
- **Stuck at `pending` on the first stage** on a self-hosted deployment
  usually means the file's MIME type has no converter: the version is
  parked until one is configured. See [File formats and
  converters](../self-hosting/converters.md).
- On a self-hosted deployment, the operator can inspect and replay
  dead-lettered work. See [Operating the pipeline](../self-hosting/operating.md).
- On remember.dev, report the `version_id` and the last `stages` list to
  [support](../cloud/support.md).

Re-sending the same bytes does not restart a failed version: identical
bytes are a no-op.

## Over HTTP

`POST /readiness` takes the version IDs and an explicit requirement for
each of the four capabilities:

```bash
curl -sS "$REMEMBER_API_URL/readiness" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "version_ids": ["6f1c2d0e-8a3b-4d5e-9f10-2a3b4c5d6e7f"],
    "require": {"pipeline": true, "p1": true, "live_graph": true, "p3": false}
  }'
```

The body is at most 1,000 version IDs. It is a read: a read-only token may
call it. See [Ingest, readiness, documents](../reference/http-api/ingest.md).

## Over MCP

An agent uses the `pipeline_readiness` tool with the arguments the
`ingest` tool returned in `pipeline.poll_with`:

```json
{
  "name": "pipeline_readiness",
  "arguments": {
    "version_ids": ["6f1c2d0e-8a3b-4d5e-9f10-2a3b4c5d6e7f"],
    "require": {"pipeline": true, "p1": true, "live_graph": true, "p3": false}
  }
}
```

The tool tells the agent how to poll, and your agent's instructions should
say the same:

1. Wait about 30 seconds after ingest before the first check.
2. Poll every 30 to 60 seconds, backing off gently, never faster than every
   15 seconds.
3. Stop at once if any stage is `failed` or `dead_letter`, and report that
   stage.
4. After 20 to 30 minutes without `ready: true` and without a failure, stop
   and hand the `version_id` and the last `stages` to a person.

An ingest that returned `created: false` needs one readiness check, not a
polling loop.

## Next

- [Give an agent context](agent-context.md)
- [Ingest files](ingest-files.md)
