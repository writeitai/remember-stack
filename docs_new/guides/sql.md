---
title: Explore memory with SQL
description: Discover the query space, write your first SQL query over current facts, combine search with joins, and read the limits and errors.
applies_to: [remember.dev, self-hosted]
---

# Explore memory with SQL

The assured operations answer the common questions in one call. Some
questions do not fit them: "which documents mention both Dana and the
invoice exporter?", "how many facts about the billing migration changed
this month?", "list every contradiction". For those you write SQL queries.

SQL queries run over **the query space**, `memory_v1`: a fixed set of
prepared, read-only views and functions over the memory. It is not access
to the database. Every statement is parsed and checked against the query
space before it runs: one `SELECT`, only the published views, functions,
operators and casts. Anything else is rejected with an error code and
never reaches the database.

Setup is in the [Quickstart](../start/quickstart.md). The full list of
views and columns is in [Query space memory_v1](../reference/query-space.md).

## 1. Discover the query space

Ask the deployment what it has:

```python
import remember

client = remember.Client.from_env()
space = client.describe_query_space(pattern="facts_*")

for view in space["views"]:
    print(view["name"], "—", view["comment"])
    for name, sql_type, nullable in view["columns"]:
        print(f"  {name} {sql_type}{'' if nullable else ' not null'}")
```

`describe_query_space(pattern=None, include_examples=False)` returns the
schema name and version, a hash of the published surface, the views (each
with `name`, `grain`, `row_key`, `comment`, `columns`), the function names,
the limits, the rules for reading results (`honesty_warnings`) and worked
examples. `pattern` is a shell-style filter over view names.
`include_examples=True` adds the names of the shipped [saved
queries](saved-queries.md).

To find where something lives, search the query space's descriptions:

```python
for hit in client.search_query_space(query="contradiction", k=5):
    print(hit["kind"], hit["name"], "—", hit["purpose"])
```

`k` is 1 to 25, default 10. Hits are views, functions, operations or
examples.

CLI:

```bash
remember query space --pattern 'facts_*'
remember query space --include-examples
remember query search-space "contradiction" --k 5
```

## 2. Your first query

`facts_current` holds every fact the memory holds true now, one row per
fact:

```python
result = client.open_query(
    "SELECT fact_kind, fact_label, valid_from, valid_until, evidence_count"
    " FROM facts_current"
    " WHERE fact_label ~~* $1"
    " ORDER BY evidence_count DESC"
    " LIMIT 20",
    parameters=["%invoice exporter%"],
)

names = [column["name"] for column in result.columns]
for row in result.rows:
    print(dict(zip(names, row)))
print("truncated:", result.truncated)
```

- Write parameters as `$1`, `$2`, … and pass them in `parameters`. Never
  paste values into the SQL text.
- Cast a parameter when its type matters: `$1::uuid`, `$2::timestamptz`.
- `~~*` is `ILIKE`; either spelling works.
- Every view is already scoped to your deployment. You do not filter on
  `deployment_id`.

`open_query(sql, *, parameters=(), max_rows=None)` returns a
`QueryResultDict`: a dict of the full result with `.rows`, `.columns` and
`.truncated` as shortcuts. Each row is a list of values in column order,
and each column is a dict with `name`, `type` and `nullable`.
`query_sql(sql=..., parameters=..., max_rows=...)` returns the same result
as a plain dict.

CLI (prints the result as JSON):

```bash
remember query sql \
  "SELECT fact_label, evidence_count FROM facts_current WHERE fact_label ~~* \$1 LIMIT 20" \
  --parameters '["%invoice exporter%"]'
```

MCP, for an agent:

```json
{
  "name": "query_sql",
  "arguments": {
    "sql": "SELECT fact_label, evidence_count FROM facts_current WHERE fact_label ~~* $1 LIMIT 20",
    "parameters": ["%invoice exporter%"]
  }
}
```

The MCP server offers `query_sql`, `explain_sql`, `describe_query_space`,
`search_query_space`, `list_saved_queries`, `describe_saved_query` and
`run_saved_query` when the deployment serves SQL queries.

## 3. The views you will use most

| View | One row per |
|---|---|
| `facts_current` | fact held true now |
| `facts_visible_history` | fact, including ended and no-longer-believed ones |
| `fact_claim_evidence_live` | link between a fact and a claim, with stance |
| `claims_live` | claim that is current testimony |
| `claims_visible_history` | claim, including superseded testimony |
| `documents_live` | document, with its `source_kind`, `source_ref` and current version |
| `document_versions_visible` | version of a document |
| `entities_current` | entity |
| `entity_aliases_current` | name an entity is known by |
| `entity_document_mentions` | entity and document it is mentioned in |
| `contradiction_members_current` | fact in a live contradiction group |
| `changes_visible` | change the memory recorded, with `occurred_at` |

Keep the layers apart in your joins. Claims are testimony: a claim that
says "the migration is in June" does not make it true now. Answer "is it
true" questions from `facts_current`, and join to
`fact_claim_evidence_live` and `claims_live` to show why.

### Wrong and right: "who owns the invoice exporter now?"

**Wrong.** This reads testimony and takes the newest statement as the
answer:

```sql
SELECT claim_text, source_handle, asserted_at
FROM claims_live
WHERE claim_text ~~* $1
ORDER BY asserted_at DESC
LIMIT 20
```

`claims_live` holds every statement that is still current testimony.
"Ravi owns the invoice exporter" from a May spec and "Dana took over the
exporter" from a September retro are both in it, and the newest one is not
necessarily what the memory holds true: it may be one side of a
disagreement, or a statement that did not change the facts at all. The
claims' `claim_valid_from` and `claim_valid_until` do not help either:
they are what the source said about time, not the memory's verdict.

**Right.** Start from the facts and join to the testimony behind each one:

```sql
SELECT f.fact_label, f.valid_from, f.evidence_count, f.contradiction_group,
       e.stance, e.source_handle, e.asserted_at
FROM facts_current AS f
JOIN fact_claim_evidence_live AS e
  ON e.fact_kind = f.fact_kind AND e.fact_id = f.fact_id
WHERE f.fact_label ~~* $1
ORDER BY f.evidence_count DESC, f.fact_id, e.stance, e.asserted_at DESC
LIMIT 50
```

Run both with `parameters=["%invoice exporter%"]`. The second answers with
what the memory holds true now, shows each fact's supporting and
contradicting claims, and a non-null `contradiction_group` tells you the
owner is disputed.

### Facts whose newest testimony disagrees

A fact can stand while the latest thing any source said about it
contradicts it: the October plan is still the fact, but yesterday's
standup note says the date is slipping again. This query finds those
facts, with the contradicting statement:

```sql
WITH ranked AS (
  SELECT e.fact_kind, e.fact_id, e.claim_id, e.stance,
         c.claim_text, c.source_handle, c.asserted_at,
         row_number() OVER (
           PARTITION BY e.fact_kind, e.fact_id
           ORDER BY c.asserted_at DESC NULLS LAST, c.claim_id
         ) AS testimony_rank
  FROM fact_claim_evidence_live AS e
  JOIN claims_live AS c ON c.claim_id = e.claim_id
)
SELECT f.fact_label, f.evidence_count, f.contradict_count,
       r.claim_text AS newest_claim, r.source_handle, r.asserted_at
FROM facts_current AS f
JOIN ranked AS r ON r.fact_kind = f.fact_kind AND r.fact_id = f.fact_id
WHERE r.testimony_rank = 1
  AND r.stance = 'contradicts'
ORDER BY r.asserted_at DESC
LIMIT 50
```

Each row is a fact worth a second look: report it together with the newer
statement, not as settled. `describe_query_space` returns this pattern and
the wrong/right pair above in its `worked_examples`.

## 4. Functions

Functions go in `FROM`, like a table:

| Function | What it returns |
|---|---|
| `semantic_facts(query, k, filters)` | facts ranked by meaning |
| `semantic_claims(query, k, filters)`, `lexical_claims(query, k, filters)` | claims ranked by meaning or by words |
| `semantic_chunks(query, k, filters)`, `lexical_chunks(query, k, filters)` | source passages ranked by meaning or by words |
| `semantic_entities(query, k, filters)` | entities ranked by meaning |
| `fetch_chunk_bodies(chunk_ids)` | the text of up to 50 chunks |
| `facts_as_of(valid_at, believed_at, max_rows)` | facts as held at one world time and one belief time |
| `canonical_bounds(valid_from, valid_until, valid_precision)` | a claim's time window made comparable |
| `graph_neighborhood(deployment_id, entity_id, …)` | relations within N hops |
| `graph_path(deployment_id, from_entity_id, to_entity_id, …)` | shortest routes between two entities |
| `graph_citation_path(deployment_id, from_doc_id, to_doc_id, …)` | citation routes between two documents |

A statement may call at most 3 functions of each category (search, graph,
body fetch, time). Search functions nominate candidates from the index; the database
confirms them against current state, and `semantic_invocations` in the
result says how many were nominated, confirmed and dropped.

Search joined to current state:

```python
result = client.open_query(
    "SELECT s.rank, c.claim_text, c.source_handle, c.asserted_at"
    " FROM semantic_claims($1, 20) AS s"
    " JOIN claims_live AS c ON c.claim_id = s.claim_id"
    " ORDER BY s.rank"
    " LIMIT 20",
    parameters=["why did the billing migration slip"],
)
```

The graph functions take your deployment ID as their first argument. Every
result carries it:

```python
deployment_id = client.open_query("SELECT count(*) FROM documents_live")["deployment_id"]
neighbours = client.open_query(
    "SELECT hops, relation_ids, node_ids FROM graph_neighborhood($1::uuid, $2::uuid, 2)"
    " ORDER BY hops",
    parameters=[deployment_id, str(ravi_id)],
)
```

The full argument lists are in [Query space
memory_v1](../reference/query-space.md).

## 5. Limits

Queries sent over the API run in the interactive tier:

| Limit | Value |
|---|---|
| Rows returned | 200 by default; `max_rows` raises it to at most 1,000 |
| Bytes returned | 1 MiB by default, 8 MiB at most |
| Statement time | 5 seconds |
| SQL text | 64 KiB |
| Parameters | 64, 256 KiB in total |
| Recursive CTEs | 1, depth at most 6 |
| Concurrent queries | 2 per caller, 8 per deployment |
| Statement time per minute | 30 seconds per caller, 120 per deployment |

A `max_rows` above the cap is lowered to the cap, not refused. When rows
were cut, `truncated` is `True` and `truncation_reason` says why. The
`limits` field of every result states the caps it ran under.

A second tier (analytical: 10,000 rows, 60 seconds) exists for operators;
the API does not select it.

## 6. Read the whole result

Every result is `QueryResult/v1`. Beyond `columns` and `rows`, check:

- `truncated`, `truncation_reason`: rows were cut.
- `warnings`: anything the query space wants you to know.
- `semantic_invocations`: per search function, how many candidates were
  nominated, confirmed and dropped.
- `graph_invocations`: per graph function, whether it stopped on a budget
  (`truncation_reason` such as `depth_budget` or `time_budget`).
- `referenced_views`, `referenced_functions`, `source_grain_tags`: what the
  query touched.

SQL results are `exploratory_tabular`: they carry no negatives, no
guaranteed order unless you `ORDER BY`, and no guarantee that a join kept
the meaning of the views it combined. An empty result is not proof that
nothing exists; see [Handle unknowns and
ambiguity](unknowns-and-ambiguity.md).

## 7. Check a query without running it

```python
plan = client.explain_query(
    "SELECT fact_label FROM facts_current WHERE fact_label ~~* $1",
    parameters=["%exporter%"],
)
```

`explain_query` (and `remember query explain-sql`) validates the statement
and returns the database plan without executing it.

## 8. Errors

A statement that is rejected or fails still returns a result (HTTP 200).
Check `termination_reason`: it is `completed`, `rejected` (the statement or
its limits were refused before running) or `failed` (it stopped while
running). When it is not `completed`, `error_code` and `error_message` say
why. Saved-query refusals and malformed requests are different: they raise
`remember.MemoryApiError`, with the HTTP status shown below.

The error codes:

| Code | HTTP status when raised | Meaning |
|---|---|---|
| `parse_error` | 422 | Not valid SQL. |
| `multiple_statements` | 422 | More than one statement. Send one. |
| `statement_not_allowed` | 422 | Not a `SELECT`. |
| `relation_not_allowed` | 422 | A table or view outside the query space. |
| `function_not_allowed` | 422 | A function outside the allowed list. |
| `function_placement_not_allowed` | 422 | A query-space function used outside `FROM`. |
| `operator_not_allowed` | 422 | An operator outside the allowed list. |
| `invalid_parameter` | 422 | A parameter is missing, of the wrong type or too large. |
| `unbounded_recursion` | 422 | A recursive query without a depth bound. |
| `schema_version_mismatch` | 409 | The query targets another query-space version. |
| `quota_exceeded`, `concurrency_exceeded` | 409 | Too many queries or too much statement time. Wait and retry. |
| `statement_timeout`, `lock_timeout`, `cancelled`, `resource_limit` | 500 | Hit a time or resource cap. Narrow the query. |
| `execution_error`, `confirmation_failed` | 500 | Failed while running. |
| `pg_unavailable`, `p1_unavailable`, `graph_unavailable`, `corpus_body_unavailable`, `generation_unavailable` | 503 | A store the query needs is not available. Retry later. |

```python
result = client.open_query("DELETE FROM facts_current")
if result["termination_reason"] != "completed":
    print(result["termination_reason"], result["error_code"], result["error_message"])
    # rejected statement_not_allowed …
```

## Next

- [Saved queries](saved-queries.md): the 18 shipped queries to start from.
- [Query space memory_v1](../reference/query-space.md)
- [SQL query routes in the HTTP API](../reference/http-api/query.md)
