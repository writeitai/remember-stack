---
title: Handle unknowns and ambiguity
description: Tell "nothing known" from "no such thing" from "could not look", handle names that match more than one entity, and brief your agent so it never fills a gap by guessing.
applies_to: [remember.dev, self-hosted]
---

# Handle unknowns and ambiguity

When a model does not find the answer, it tends to make one up. A memory
that returns an empty list gives it no reason not to. RememberStack says
why a result is empty, when a name could mean two people, when a list was
cut short and when two sources disagree. This page shows how to read each
of those signals and how to tell your agent what to do with them.

Setup is in the [Quickstart](../start/quickstart.md). Every field used here
is described in [Reading a result](../concepts/reading-results.md).

## Three kinds of "nothing"

A result with nothing to return carries a `negative` with a `kind`, an
`explanation` and sometimes a `workaround`. `NegativeKind` has three
values:

| `kind` | Meaning | What to do |
|---|---|---|
| `unknown_entity` | The name or ID does not resolve to any current entity. | Check the spelling, try another name, or search claims and passages. Do not say "X has no owner"; say the memory does not know X. |
| `known_empty` | The entity or query is understood, and nothing matches within the result's stated freshness. | Report that the memory holds nothing matching. Broaden the query or look at testimony if the question allows. |
| `boundary` | The question could not be answered as asked: a dependency was unavailable, a budget ran out, or the request crossed a limit. | Follow `workaround`: retry, use fewer anchors or hops, or ask differently. Never report it as "nothing found". |

```python
import remember
from remember.models import NegativeKind

client = remember.Client.from_env()
result = client.facts_context("Who approved the refund policy?")

if result.negative is None:
    ...  # use result.facts
elif result.negative.kind is NegativeKind.UNKNOWN_ENTITY:
    print("The memory does not know that:", result.negative.explanation)
elif result.negative.kind is NegativeKind.KNOWN_EMPTY:
    print("Nothing is recorded:", result.negative.explanation)
else:  # NegativeKind.BOUNDARY
    print("Could not answer:", result.negative.explanation, "→", result.negative.workaround)
```

`NegativeKind` is imported from `remember.models`.

Forgotten material looks exactly like material that never existed. That is
deliberate: a hard-forgotten document leaves no trace in answers.

## Names that match more than one entity

`resolve_entity` never picks a winner for you. When a name matches several
entities, all of them come back in `entities`, ranked:

```python
resolved = client.resolve_entity("Dana")

if resolved.negative is not None:
    print("No one called Dana:", resolved.negative.explanation)
elif len(resolved.entities) == 1:
    dana = resolved.entities[0]
else:
    for candidate in resolved.entities:
        print(candidate.entity_id, candidate.canonical_name, candidate.tier)
```

Each candidate has `entity_id`, `canonical_name`, `tier` and
`context_hits`. The tier says how it matched:

| `tier` | Match |
|---|---|
| `T0` | Exact match on a known name or alias. Every entity with that exact name is returned. |
| `T1` | Close spelling. |
| `T2` | Similar sound. |
| `T3` | Similar meaning, by embedding, used only when nothing matched by name. |

Fuzzy tiers stop at a fixed number of candidates; when that cap hid more,
`truncation.truncated` is `True` with reason `resolve_candidate_limit`.

What to do with more than one candidate:

- **Ask.** "Do you mean Dana, the product lead, or Dana in finance?"
  is almost always right for an interactive agent.
- **Use the conversation.** If the question already names a project or a
  colleague, resolve those too and pass their IDs to `resolve`: candidates
  connected to them move up (`context_hits` counts the connections).

  ```python
  billing = client.resolve_entity("billing migration").entities[0]
  ranked = client.resolve(name="Dana", context_entity_ids=(billing.entity_id,))
  ```

  `resolve` takes up to 8 context entities. It reorders; it never drops a
  candidate.
- **Pass them all.** `facts_context(..., entity_ids=[...])` accepts every
  candidate, up to 19 anchors, and the facts show which one the answer is
  about.

Over MCP, `resolve_entity` is a tool with a single `name` argument. The
context-ranked `resolve` is available from the Python client and `GET
/resolve` only; remember.dev's compatibility profile may not include it
(see [What remember.dev serves](../cloud/compatibility.md)).

## Lists that were cut short

`truncation` tells you whether you have everything:

| Field | Meaning |
|---|---|
| `truncated` | `True` when more matched than was returned. |
| `returned` | How many items this result holds. |
| `estimated_total` | How many matched, as far as the operation knows. |
| `total_is_exact` | `False` when `estimated_total` is a lower bound. |
| `reason` | Why it stopped, when known. |

A truncated result is not an exhaustive answer. "Ravi owns three
components" from a truncated list means "at least three". Raise `k`
([Give an agent context](agent-context.md)), narrow the query, or say the
count is partial.

Evidence has its own totals: `evidence_totals[]` gives, per fact and
stance, `returned` and the exact `total` of claims.

## Candidates dropped on confirmation

Search nominates candidates quickly from an index; the database then
confirms each one against current state. `dropped_by_hydration` counts
candidates that were nominated but failed confirmation: deleted since the
index was built, no longer current, or outside your time window or
anchors. A non-zero value is normal. A large one on a fresh ingest usually
means processing is still settling; wait for
[readiness](wait-for-readiness.md) and ask again.

## Sources that disagree

When two facts conflict and both still stand, they share a
`contradiction_group`, and each carries `contradiction` with the other side:

```python
for fact in result.facts:
    if fact.contradiction is None:
        continue
    print("Disputed:", fact.label)
    for rival in fact.contradiction.co_members:
        print("  versus:", rival.label, f"({rival.evidence_count} sources)")
    if fact.contradiction.returned < fact.contradiction.total:
        print(f"  and {fact.contradiction.total - fact.contradiction.returned} more")
```

Report both sides with their sources. Do not pick the one with more
evidence and present it as settled; `evidence_count` counts distinct
documents, not truth. The SQL view `contradiction_members_current` lists
every member of every live group.

A fact can also stand while the newest statement linked to it contradicts
it. That is worth a second look even without a contradiction group.
[Explore memory with
SQL](sql.md#facts-whose-newest-testimony-disagrees) has a query that finds
those facts.

Two related signals:

- **`support: withdrawn`** means the fact lost all its current supporting
  testimony when a document was re-read, and is waiting for review. It
  still stands, but treat it as shaky: say so, and check its evidence
  before relying on it.
- **`temporal_match: possible`** means the fact is relevant but not dated
  well enough to be sure it falls in your time window. Report it apart
  from `confirmed` matches.

## SQL results have no negatives

A SQL query that returns no rows says nothing about why. It is not
`known_empty`. Check `truncated`, `truncation_reason` and, for graph
functions, `graph_invocations` before you treat an empty result as proof of
absence. See [Explore memory with SQL](sql.md).

## Brief your agent

Put a paragraph like this in the system prompt of any agent that answers
from RememberStack:

```text
You answer from a memory service. Its results are the only source of truth
about the team's work; do not fill gaps from general knowledge.

- Facts are what the memory holds true now (or at the time asked). Claims
  and passages are what sources said. Keep them apart: never present a
  quote as an established fact.
- If a result says unknown_entity, say you do not know that name. If it
  says known_empty, say nothing is recorded. If it says boundary, say you
  could not look it up and why. Never turn any of these into "no" or
  "none".
- If a name matches more than one entity, ask which one is meant, or
  answer for each separately and label them.
- If a list is marked truncated, say the answer may be incomplete. Say "at
  least N", never "exactly N".
- If facts contradict each other, give both sides with their sources.
  If a fact's support is withdrawn, say it is unconfirmed.
- Dates: a fact's validity is when it was true; asserted_at is when a
  source said it; ingested_at is when the memory learned it. Do not mix
  them, and do not give a day when the precision is a month.
- Cite the source passage for every factual statement.
```

Adapt the wording, not the rules.

## Why is my answer empty or wrong?

Before you suspect the memory, check how the question was asked. In this
order:

1. **Is the document processed?** A question sent before
   [readiness](wait-for-readiness.md) reports the version ready gets an
   answer without it, and nothing in that answer says so.
2. **What does `negative` say?** `unknown_entity`, `known_empty` and
   `boundary` each need a different next step; see
   [Three kinds of "nothing"](#three-kinds-of-nothing).
3. **Which time did you ask about?** `facts_context` returns facts true
   now unless you pass `time`. A fact that ended in June is not in a
   `current` answer; ask with `history` or `at`. See
   [Time](../concepts/time.md#asking-about-time).
4. **Facts or claims?** `claims_and_sources_context` returns what sources
   said, including statements that were later replaced. `facts_context`
   returns what holds.
5. **Which entity?** A name that matches two people needs resolving first;
   see [Names that match more than one entity](#names-that-match-more-than-one-entity).
6. **Is the list complete?** Check `truncation` before you count.
7. **SQL:** an empty result has no `negative`. Check
   `termination_reason` and `error_code` first: a rejected statement also
   comes back with no rows.

If the question is right and the answer is still wrong, the cause is on
the deployment side. On a self-hosted deployment, work through
[Troubleshooting](../self-hosting/troubleshooting.md). On remember.dev,
send [Support](../cloud/support.md) the request, the full response, the
time it was sent, and the `version_id` of the document you expected to
see.

## Next

- [Cite the source of an answer](cite-sources.md)
- [Build a memory-backed agent](build-an-agent.md)
- [Contradictions, corroboration, supersession](../concepts/contradictions.md)
