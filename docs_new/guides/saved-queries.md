---
title: Saved queries
description: List, inspect and run the 18 SQL queries every deployment ships with, and understand when a saved query runs and when it refuses.
applies_to: [remember.dev, self-hosted]
---

# Saved queries

A saved query is a SQL query stored in the deployment under a name, with
its parameters described, so an agent or a script can run it without
writing SQL. Every deployment ships with 18 of them, covering the questions
people ask most: claims about an entity, documents that mention it, what
changed since a date, why a fact is held. They run over the same query
space as any [SQL query](sql.md): prepared, read-only views and functions,
with every statement checked before it runs.

Setup is in the [Quickstart](../start/quickstart.md).

## Names

A saved query is addressed by a `namespace` and a `name`, written
`examples.claims_about`. The shipped queries all have the `namespace`
`examples`. Each one also has integer versions; a run uses the newest
active version unless you ask for a specific one.

## List them

```python
import remember

client = remember.Client.from_env()

for query in client.list_saved_queries(namespace="examples"):
    print(f"{query['namespace']}.{query['name']} v{query['version']} — {query['description']}")
```

`list_saved_queries(namespace=None, status=None)` returns, for each saved
query: `query_id`, `namespace`, `name`, `version`, `status`,
`description`, `origin`, `assurance`, `query_hash` and the hash of the
query space it was validated against. Without `status`, only `active`
versions are listed.

CLI and MCP:

```bash
remember query list-saved --namespace examples
```

```json
{"name": "list_saved_queries", "arguments": {"namespace": "examples"}}
```

## The 18 shipped queries

Parameters are positional, in this order. IDs are UUID strings; instants
are ISO 8601 timestamps with a time zone.

| Name | Answers | Parameters | Rows at most |
|---|---|---|---|
| `claims_verbatim` | Claims as asserted, found by meaning. | search text | 20 |
| `claims_about` | Claims that mention an entity, newest first. | entity ID | 50 |
| `claims_as_of` | Claims whose stated time overlaps a window. | from, to | 50 |
| `claims_hybrid_rrf` | Claims found by meaning and by words, fused. | search text | 20 |
| `chunks_hybrid_rrf` | Source passages found by meaning and by words, fused. | search text | 20 |
| `chunk_neighbors` | The passages either side of one passage in its section. | chunk ID | 5 |
| `documents_about` | Documents that mention an entity, most mentions first. | entity ID | 50 |
| `pages_about` | Compiled pages that cite an entity. | entity ID | 50 |
| `relation_current` | Current relations of an entity. | entity ID | 50 |
| `observation_current` | Current observations about an entity. | entity ID | 50 |
| `identity_as_of` | How an entity's identity was decided, up to an instant. | entity ID, instant | 100 |
| `entity_timeline` | An entity's facts counted per day. | entity ID | 200 |
| `explain` | Why a fact is held: history, evidence, documents. | fact ID | 100 |
| `multi_hop_context` | Claims along a route between two entities that match a search. | deployment ID, from entity ID, to entity ID, search text | 100 |
| `changed_since` | What the memory learned after an instant. | instant | 100 |
| `graph_neighborhood` | Relations within two hops of an entity. | deployment ID, entity ID | graph budget |
| `graph_path` | Routes of up to four hops between two entities. | deployment ID, from entity ID, to entity ID | graph budget |
| `graph_citation_path` | Citation routes of up to six hops between two documents. | deployment ID, from document ID, to document ID | graph budget |

The graph queries take your deployment ID first. It is in every ingest
result (`deployment_id`) and every SQL result.

## Look at one

Before you rely on a saved query, read what it does:

```python
detail = client.describe_saved_query(namespace="examples", name="documents_about")
print(detail["status"], detail["version"])
print(detail["sql"])
print(detail["parameter_schema"])
print(detail["declared_interpretation"])
```

The description holds the SQL, the parameter and result schemas, the
declared interpretation, the default limits, the validation report, and who
wrote and approved the version. Pass `version=` to see an older one.

```bash
remember query describe-saved examples documents_about
```

## Run one

```python
dana = client.resolve_entity("Dana").entities[0]
result = client.run_saved_query(
    namespace="examples",
    name="documents_about",
    parameters=[str(dana.entity_id)],
    max_rows=20,
)
columns = [column["name"] for column in result["columns"]]
for row in result["rows"]:
    print(dict(zip(columns, row)))
```

`run_saved_query(namespace, name, parameters=(), version=None,
max_rows=None)` returns the same `QueryResult/v1` dict as a SQL query,
with a `saved_query` field naming the exact version that ran: `query_id`,
`namespace`, `name`, `version` and `query_hash`. Record it next to any
answer you keep.

The result is subject to the same [limits](sql.md#5-limits) as any SQL
query. A `max_rows` you pass overrides the saved query's own default; the
query's `LIMIT` still applies.

CLI and MCP:

```bash
remember query run-saved examples documents_about \
  --parameters '["0b6f2d8e-5c1a-4e3b-9d7f-1a2b3c4d5e6f"]' --max-rows 20
```

```json
{
  "name": "run_saved_query",
  "arguments": {
    "namespace": "examples",
    "name": "documents_about",
    "parameters": ["0b6f2d8e-5c1a-4e3b-9d7f-1a2b3c4d5e6f"],
    "max_rows": 20
  }
}
```

## Status: when a saved query runs

Every version has a status. Only `active` runs.

| Status | Meaning | Running it |
|---|---|---|
| `draft` | Written, not approved. Not listed by default. | Refused: `saved_query_disabled`. |
| `active` | Approved by an operator, validated against the current query space. | Runs. |
| `pending_revalidation` | The query space changed since it was validated. | Refused: `saved_query_revalidation_pending`. |
| `deprecated` | A newer version was activated. | Refused unless another version is active. |
| `disabled` | Switched off by an operator. | Refused: `saved_query_disabled`. |
| `broken` | Failed validation against the current query space. | Refused: `saved_query_disabled`. |

How a version moves:

- The author of a version cannot approve it. Activation is a separate act
  by someone with authority, and the version records both people.
- When the query space changes (an upgrade adds a column or a view), every
  active version moves to `pending_revalidation` in the same step. It runs
  again only after it is validated against the new query space.
- Activating a new version deprecates the one it replaces. Old versions are
  kept; a caller who pinned `version=2` either gets exactly version 2 or a
  refusal, never different SQL.

Errors when running:

| Code | HTTP | Meaning |
|---|---|---|
| `saved_query_not_found` | 404 | No such `namespace.name`, or no such version. |
| `saved_query_disabled` | 409 | The query or the requested version is not active. |
| `saved_query_revalidation_pending` | 409 | Validated against another version of the query space. |
| `saved_query_incompatible` | 409 | Written for a query space this deployment does not have. |

All the SQL error codes can also occur; see [Explore memory with
SQL](sql.md#8-errors).

## Change a shipped query

The shipped queries are starting points, not guarantees: the platform wrote
them honestly, but what they mean is up to you. To change one, copy its SQL
from `describe_saved_query`, edit the filters, and run your copy with
`open_query`. Your copy is yours; the shipped version does not change.

The HTTP API, the client and the CLI can list, describe and run saved
queries. They cannot create, approve or disable one yet.

## Next

- [Explore memory with SQL](sql.md)
- [Ask about the past](ask-about-the-past.md): `claims_as_of` and
  `changed_since` in use.
- [Cite the source of an answer](cite-sources.md): `explain` in use.
