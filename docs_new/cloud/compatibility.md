---
title: What remember.dev serves
description: Which RememberStack version remember.dev runs, which deployment routes it serves, what differs from the version these docs describe, and how changes are announced.
applies_to: [remember.dev]
---

# What remember.dev serves

A remember.dev deployment is a RememberStack engine answering at its own
hostname. It serves the engine's HTTP API directly, with remember.dev's
authentication and limits in front. This page says which engine version
that is, what it serves, and where it differs from what the rest of these
docs describe.

## Engine version

| | Version |
|---|---|
| remember.dev deployments run | RememberStack **v0.16.0** |
| These docs describe | RememberStack **v0.17.0** |

remember.dev chooses the engine version and upgrades deployments; you do
not. A move to a newer engine is in progress. Until it lands, the
differences below apply.

`GET /deployment` on your deployment returns the build it is running.

## Served routes

With a deployment API token, your deployment at
`https://<deployment-id>.dp.remember.dev` serves:

| Area | Routes | Notes |
|---|---|---|
| Ingest | `POST /ingest` | Text only; see [Ingest limits](limits.md#ingest) |
| Documents | `GET /documents` | |
| Readiness | `POST /readiness` | |
| Search | `GET` and `POST /search/claims`, `GET` and `POST /search/chunks` | |
| Assured operations | `GET /operations`, `POST /operations/{name}` | v0.16.0 operation names; see below |
| Entities and facts | `GET /resolve`, `GET /lookup/relations`, `GET /lookup/observations`, `GET /transcript/relation/{relation_id}`, `GET /hydrate/relation/{relation_id}` | |
| Graph | `POST /graph/neighborhood`, `POST /graph/path`, `POST /graph/citation-path` | At most 2 graph queries at a time |
| SQL queries | `POST /query/sql`, `POST /query/sql/explain`, `GET /query/space`, `GET /query/space/search`, `GET /query/saved`, `GET /query/saved/{namespace}/{name}`, `POST /query/saved/{namespace}/{name}/run` | |
| Deployment | `GET /deployment`, `GET /healthz` | `/healthz` needs no token |

SQL queries run over the query space (`memory_v1`): prepared, read-only
views and functions. Every statement is parsed and checked against that
query space before it runs, and anything outside it is refused. See
[Explore memory with SQL](../guides/sql.md).

The routes behave as the [HTTP API reference](../reference/http-api/index.md)
describes for v0.17.0, except where this page says otherwise.

## Not served

| Route or feature | What happens |
|---|---|
| `GET /chunks/{chunk_id}/adjacent`, `POST /chunks/adjacent` | Added in v0.17.0. Not present on remember.dev yet (`404`) |
| Connectors (`/connectors…`) | The routes exist on v0.16.0, but remember.dev does not run connectors for you and gives them no credentials. Push documents with ingest instead |
| Files other than UTF-8 Markdown and plain text | Refused before storage; see [Ingest limits](limits.md#ingest). A self-hosted engine can convert PDFs and other formats |
| Filesystem views of the corpus | Not offered on remember.dev |
| Direct database access | Not offered. Use SQL queries over the query space |

## Operation names

v0.17.0 renamed three of the four assured operations. remember.dev serves
the v0.16.0 names:

| On remember.dev (v0.16.0) | In these docs (v0.17.0) | Result |
|---|---|---|
| `resolve_entity` | `resolve_entity` | Envelope |
| `testimony_context` | `claims_and_sources_context` | Envelope |
| `fact_context` | `facts_context` | Envelope |
| `answer_context` | `combined_context` | `ContextBundle/v1` on remember.dev, `ContextBundle/v2` in v0.17.0 |

What this means for your code today:

- The `remember` client methods `claims_and_sources_context`,
  `facts_context` and `combined_context`, and `remember query`, call the
  v0.17.0 names, which a v0.16.0 deployment does not know.
- Call the v0.16.0 names directly instead, with `POST /operations/{name}`,
  `remember operations run <name> --arg key=value`, or
  `client.run_operation(name="fact_context", arguments={...})`.
- The `remember` client validates operation results against
  `ContextBundle/v2`, so it cannot parse `answer_context`'s
  `ContextBundle/v1` result. Read that result as JSON over HTTP.
- `GET /operations` and `remember operations list` show exactly the names
  your deployment serves.
- [Hosted MCP](hosted-mcp.md) already uses the v0.16.0 names.

When remember.dev moves to v0.17.0, the new names replace the old ones.

## Authentication

| | Self-hosted | remember.dev |
|---|---|---|
| Machine credential | Whatever you configure on the engine | Deployment API token `umc_dp_…`, signed, expiring after 365 days |
| How to get one | You create it | Console, API, or `remember login`; see [Tokens and sign-in](tokens-and-sign-in.md) |
| Scope | Configured | Read and write, one deployment |
| Browser credentials | Not applicable | Short-lived read-only or upload-only credentials for the console |

Send the token as `Authorization: Bearer <token>`. An expired or revoked
token is refused by the deployment.

## Limits and errors that only exist on remember.dev

| Situation | Response |
|---|---|
| Body over 100,000,000 bytes | `413`, `body_too_large` |
| Text over 10,000,000 bytes | `413`, `source_bytes_limit_exceeded` |
| Binary file | `409`, `rate_class_unavailable` |
| Other text types, invalid UTF-8, markup | `422`, `rate_class_ambiguous` |
| Empty document | `422`, `empty_text` |
| Provider-cost safeguard reached | `423`, `dispatch_parked:<reason>`, or `403`, `dispatch_refused:<reason>` |
| Safeguard service unreachable | `503`, `spend_lease_unavailable` |
| Deployment blocked for funding (today; see [Spend caps](spend-controls.md)), or closed | The hostname stops answering |

All of them are listed in [Limits](limits.md), and the money side in
[Spend caps and auto top-up](spend-controls.md).

## The retired bridge

Earlier, remember.dev relayed memory requests through
`https://remember.dev/app/api/dp/v1/…`. That path is gone. It answers
every request with `410` and the code `dp.bridge_removed`. Send requests to
your deployment's own hostname.

## Changes

The engine version remember.dev runs changes over time, and routes, error
codes or limits can change with it. Engine releases, including the
operation renames above, are listed in the
[changelog](../project/changelog.md). Check `GET /deployment` to see which
build your deployment runs.
