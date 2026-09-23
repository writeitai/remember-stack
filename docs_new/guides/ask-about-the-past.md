---
title: Ask about the past
description: Ask what was true at a date, during a period, or ever, what sources said at the time, and what the memory believed on an earlier day.
applies_to: [remember.dev, self-hosted]
---

# Ask about the past

"When was the billing migration supposed to go live, back in June?" is not
the same question as "when is it going live?". The first needs the plan as
it stood in June; the second needs today's. RememberStack keeps both,
because every fact records when it held in the world and when the memory
learned it. This page shows how to ask each kind of past question.

Setup is in the [Quickstart](../start/quickstart.md). The two clocks are
explained in [Time](../concepts/time.md).

## The time modes

`facts_context` and `combined_context` take a `time` argument. It selects
facts by when they were true in the world (their validity), always as the
memory believes them now.

| Mode | Argument | Returns facts that |
|---|---|---|
| current (default) | `{"mode": "current"}` | hold now. |
| at | `{"mode": "at", "at": "<timestamp>"}` | held at that instant. |
| overlap | `{"mode": "overlap", "from": "<timestamp>", "to": "<timestamp>"}` | held at any point in the window, bounds included. |
| history | `{"mode": "history"}` | ever held, including ones that have ended, as long as they began by now. |

Timestamps are ISO 8601 with a time zone; they are converted to UTC. `to`
must not be before `from`. The result's `temporal_scope` echoes the mode
you asked for, with the instant it was evaluated.

## Choose a time mode

| The question | Mode |
|---|---|
| "Who owns the invoice exporter?" "Is the migration still planned for October?" | `current` |
| "Who owned the exporter on 1 July?" | `at` |
| "Who worked on the migration during Q3?" | `overlap` |
| "Which teams has Ravi been on?" "Has the date ever changed?" Biographies, achievements, timelines, anything with "ever". | `history` |

A `current` read leaves out everything that has ended, so it is the wrong
mode for "what has Ravi done": his finished work is exactly what you want.
Use `history` for those.

**Counting.** "How many times did the go-live date move?" Count only facts
whose `temporal_match` is `confirmed`. If any returned fact is `possible`,
or the result is truncated, you cannot state an exact count: say "at least
N", and list the possible ones separately.

**Two steps for "when X happened".** "Who owned the exporter when the
migration went live?" names a time by an event. Ask for the event first,
read its date, then ask the real question at that date:

```python
import remember

client = remember.Client.from_env()
event = client.facts_context("billing migration went live", time={"mode": "history"})
dated = [f for f in event.facts if f.validity.valid_from is not None]

if dated:
    went_live = dated[0].validity
    owners = client.facts_context(
        "Who owns the invoice exporter?",
        time={"mode": "at", "at": went_live.valid_from.isoformat()},
    )
```

Check the event's `valid_precision` before you use its date as an instant.
If it is `month` or coarser, ask with `overlap` over that month instead of
`at` its first day. If the event has no date at all, say so rather than
guessing one.

## What holds now

The examples below use a memory that holds the team's notes from May to
September 2026: the migration was planned for June, then moved to October
on 17 September.

```python
import remember

client = remember.Client.from_env()
now = client.facts_context("When does the billing migration go live?")
for fact in now.facts:
    print(fact.label, fact.validity.valid_from, fact.validity.valid_until)
```

This is the default; it returns the October plan.

## What held at a date

"What was the go-live date on 1 July?"

```python
july = client.facts_context(
    "When does the billing migration go live?",
    time={"mode": "at", "at": "2026-07-01T00:00:00Z"},
)
```

This returns the June plan, which held on 1 July, and not the October plan,
which only began to hold on 17 September.

CLI:

```bash
remember operations run facts_context \
  --arg query="When does the billing migration go live?" \
  --arg time='{"mode": "at", "at": "2026-07-01T00:00:00Z"}'
```

MCP:

```json
{
  "name": "facts_context",
  "arguments": {
    "query": "When does the billing migration go live?",
    "time": {"mode": "at", "at": "2026-07-01T00:00:00Z"}
  }
}
```

## What held during a period

"Who owned the invoice exporter during Q3?"

```python
q3 = client.facts_context(
    "Who owns the invoice exporter?",
    time={"mode": "overlap", "from": "2026-07-01T00:00:00Z", "to": "2026-09-30T23:59:59Z"},
)
```

Every fact that held at any moment in the window comes back, so a hand-over
in August returns both owners. Read their `validity` to order them.

## Everything that ever held

"Has the migration date ever changed?" "Which owners has the exporter had?"

```python
ever = client.facts_context(
    "billing migration go-live date",
    time={"mode": "history"},
)
for fact in sorted(ever.facts, key=lambda f: f.validity.valid_from or f.validity.ingested_at):
    v = fact.validity
    print(f"{fact.label}: {v.valid_from} → {v.valid_until or 'still holds'}")
```

Use history mode for "ever", "has … changed", biographies and timelines. It
includes facts whose validity has ended.

With `entity_ids`, the `overlap` and `history` modes search only the anchor
entities themselves. The `current` and `at` modes also search their graph
neighbours, because the neighbourhood is taken at a single instant.

## Read how sure the dates are

Each fact carries:

- `validity.valid_from` / `valid_until`: when it held in the world. A
  missing end with `valid_precision` `open` means it is ongoing; a missing
  value otherwise means unknown.
- `validity.valid_precision`: `instant`, `day`, `month`, `quarter`, `year`,
  `open` or `unknown`. "Planned for June" is `month` precision; do not
  answer with a day.
- `temporal_match`: `confirmed` when the fact's dates prove it matches your
  time window, `possible` when it is relevant but not dated well enough to
  be sure. Report possible matches separately; never count them as
  confirmed.
- `validity.ingested_at`: when the memory learned it. That is not when it
  happened.

## What sources said at the time

Facts are the memory's verdict. Sometimes you want the testimony: "what did
the June meetings say about the go-live?". Claims carry two source times:

- `asserted_at`: when the source made the statement, from the document's
  `source_modified_at`.
- `claim_valid_from` / `claim_valid_until`: when the claim says the thing
  happened or was true.

The shipped saved query `examples.claims_as_of` returns claims whose stated
time overlaps a window:

```python
june_claims = client.run_saved_query(
    namespace="examples",
    name="claims_as_of",
    parameters=["2026-06-01T00:00:00Z", "2026-06-30T23:59:59Z"],
)
columns = [column["name"] for column in june_claims["columns"]]
for row in june_claims["rows"]:
    print(dict(zip(columns, row)))
```

Claims whose time is `unknown` have no window and are left out; the
`unknown_precision_excluded` column counts them. To filter by when things
were said rather than when they happened, write a SQL query on
`claims_live` with `asserted_at`; see [Explore memory with SQL](sql.md).

## What the memory believed on an earlier day

The time modes answer "what was true then, as we know it now". To ask
"what did the memory believe on 1 August about 1 July?", before the
17 September correction arrived, use the `facts_as_of` function in a SQL
query. SQL queries run over the query space, a set of prepared read-only
views and functions; every statement is checked against it before it runs
([Explore memory with SQL](sql.md)). `facts_as_of` takes a world-time
instant and a belief instant:

```python
believed = client.open_query(
    "SELECT fact_label, valid_from, valid_until, temporal_match"
    " FROM facts_as_of($1::timestamptz, $2::timestamptz)"
    " WHERE fact_label ~~* $3"
    " ORDER BY valid_from",
    parameters=["2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z", "%migration%"],
)
for row in believed.rows:
    print(row)
```

It returns at most 200 rows by default (its third argument, up to 1,000).
The graph calls `graph_neighborhood` and `graph_path` take the same two
clocks as `valid_at` and `believed_at`.

## What changed since a date

"What has the memory learned since Friday?"

```python
changes = client.run_saved_query(
    namespace="examples",
    name="changed_since",
    parameters=["2026-09-18T00:00:00Z"],
)
```

It lists up to 100 changes, newest first, with the kind of object, its ID,
when it changed and a label.

## Next

- [Cite the source of an answer](cite-sources.md)
- [Saved queries](saved-queries.md)
- [Time](../concepts/time.md)
