---
title: Give an agent context
description: Pick the assured operation that fits the question, size it to your token budget, and put its result into a prompt without losing what it tells you.
applies_to: [remember.dev, self-hosted]
---

# Give an agent context

The next session opens on an empty window. The agent does not know which
decision still holds or which file said it, so you give it context from
memory before it answers. This page shows which call to make for which kind
of question, how big to make it, and how to put the result into a prompt so
the model keeps facts, testimony and gaps apart.

Setup is in the [Quickstart](../start/quickstart.md). The four operations
are defined in [Assured operations](../reference/assured-operations.md).

## Choose the operation

RememberStack has four assured operations. Each returns a typed result with
explicit guarantees about time, truncation and absence.

| The question | Operation | Returns |
|---|---|---|
| "Who is Ravi?" "Which 'billing' do you mean?" Any name you will use as an anchor. | `resolve_entity` | Ranked entity candidates. Never a silent guess. |
| "Is the migration still planned for October?" "Who owns the invoice exporter?" What is true now, or at a given time. | `facts_context` | Adjudicated facts (relations and observations) with their validity and supporting evidence. |
| "What did the team say about the exporter?" "Quote the spec." What sources said, word for word. | `claims_and_sources_context` | Current claims and source passages. Testimony, not verdicts. |
| A general question where you want both what is held true and what was said. | `combined_context` | Both results side by side, each complete and labelled. |

Default to `facts_context` for "is it true" questions and fall back to
claims only when facts are missing or you need a verbatim quote. Use
`combined_context` when you cannot tell in advance which the model needs.

## Parameters and limits

| Operation | Parameter | Default | Allowed |
|---|---|---|---|
| `resolve_entity` | `name` | required | non-empty string |
| `facts_context` | `query` | required | 1 to 8,192 characters |
| | `k` (facts returned) | 15 | 1 to 30 |
| | `evidence_per_fact` (claims per fact per stance) | 3 | 1 to 5 |
| | `hops` (graph expansion around anchors) | 1 | 1 to 2 |
| | `predicate` | none | 1 to 200 characters |
| | `entity_ids` (anchors) | none | 1 to 19 unique UUIDs |
| | `time` | `{"mode": "current"}` | see [Ask about the past](ask-about-the-past.md) |
| `claims_and_sources_context` | `query` | required | 1 to 8,192 characters |
| | `k` (results) | 50 | 1 to 100 |
| | `candidate_k` (nominations per channel) | 200 | 1 to 400, at least `k` |
| | `entity_ids` | none | 1 to 20 unique UUIDs |
| `combined_context` | `query`, `hops`, `predicate`, `entity_ids`, `time` | as `facts_context` | as `facts_context` |

`combined_context` always runs its halves at their defaults: 50 claims and
passages, 15 facts with 3 claims each.

One fact-context result carries at most 60 evidence links in total, however
you set `k` and `evidence_per_fact`. Fact retrieval has a 25-second
database budget; if the database cannot answer inside it you get a
`boundary` result, not a partial one.

## Call it from Python

The client has a method per operation:

```python
import remember

client = remember.Client.from_env()

candidates = client.resolve_entity("Ravi")
facts = client.facts_context("Who owns the invoice exporter?")
said = client.claims_and_sources_context("invoice exporter rewrite")
both = client.combined_context("What changed in the billing migration plan?")
```

`facts_context` accepts `time`, `hops`, `predicate` and `entity_ids`;
`combined_context` accepts `time`; `claims_and_sources_context` and
`resolve_entity` accept only the query or name. For every other parameter,
including `k` and `evidence_per_fact`, use `run_operation`:

```python
facts = client.run_operation(
    name="facts_context",
    arguments={
        "query": "invoice exporter owner",
        "k": 10,
        "evidence_per_fact": 2,
        "entity_ids": [str(ravi_id)],
    },
)
```

`run_operation` returns an `Envelope`, or a `ContextBundleV2` for
`combined_context`.

## Anchor on entities

Resolve the names in the question first, then pass the chosen entity IDs
as `entity_ids`. Facts are then searched in the anchors and their graph
neighbours (one hop by default), which keeps "the exporter Ravi owns" from
matching every exporter in the memory.

```python
resolved = client.resolve_entity("Ravi")
if resolved.negative is None and len(resolved.entities) == 1:
    ravi_id = resolved.entities[0].entity_id
    facts = client.facts_context("What does Ravi own?", entity_ids=[ravi_id])
```

When a name resolves to more than one candidate, decide which one you mean
(or pass all of them); see [Handle unknowns and
ambiguity](unknowns-and-ambiguity.md). An anchor that is not a current
entity makes the whole call return `unknown_entity`.

## The same from the CLI and MCP

```bash
remember query "Who owns the invoice exporter?"            # facts_context
remember query text "Who owns the invoice exporter?" --combined

remember operations run facts_context \
  --arg query="invoice exporter owner" --arg k=10 --arg evidence_per_fact=2
remember operations run claims_and_sources_context \
  --arg query="invoice exporter rewrite" --arg k=20
remember operations run resolve_entity --arg name=Ravi
```

Each `--arg` value is parsed as JSON when it can be, so `k=10` is a number
and `entity_ids='["…"]'` is a list; anything else is a string.

An agent connected over MCP sees the four operations as tools with the same
names and arguments:

```json
{"name": "facts_context", "arguments": {"query": "invoice exporter owner", "k": 10}}
```

## Put the result into a prompt

Do not paste the raw JSON unless the model is good at reading it and you
have the room. Render the parts that matter, and keep facts and testimony
under separate headings so the model does not mistake a quote for a
verdict.

```python
from remember import Envelope


def render_facts(envelope: Envelope) -> str:
    if envelope.negative is not None:
        return f"No facts: {envelope.negative.kind}. {envelope.negative.explanation}"
    claims = {claim.claim_id: claim for claim in envelope.evidence}
    lines = []
    for fact in envelope.facts:
        v = fact.validity
        when = f"valid {v.valid_from:%Y-%m-%d}" if v.valid_from else "validity unknown"
        if v.valid_until:
            when += f" until {v.valid_until:%Y-%m-%d}"
        notes = []
        if fact.support.value == "withdrawn":
            notes.append("support withdrawn, verify before relying on it")
        if fact.contradiction is not None:
            rivals = "; ".join(member.label for member in fact.contradiction.co_members)
            notes.append(f"contradicted by: {rivals}")
        suffix = f" [{'; '.join(notes)}]" if notes else ""
        lines.append(f"- {fact.label} ({when}; {fact.evidence_count} sources){suffix}")
        for link in envelope.fact_evidence:
            if link.fact_id == fact.fact_id and link.claim_id in claims:
                claim = claims[link.claim_id]
                title = claim.document_title or claim.doc_id
                lines.append(f'    {link.stance}: "{claim.source_span}" ({title})')
    if envelope.truncation is not None and envelope.truncation.truncated:
        lines.append(
            f"(Showing {envelope.truncation.returned} of about "
            f"{envelope.truncation.estimated_total}; this list is not complete.)"
        )
    return "\n".join(lines)


def render_claims(envelope: Envelope) -> str:
    if envelope.negative is not None:
        return f"No source passages: {envelope.negative.explanation}"
    lines = []
    for claim in envelope.evidence:
        said_at = f"{claim.asserted_at:%Y-%m-%d}" if claim.asserted_at else "undated"
        lines.append(f"- {claim.claim_text} ({claim.document_title or claim.doc_id}, {said_at})")
    return "\n".join(lines)


bundle = client.combined_context("What changed in the billing migration plan?")
context = (
    "## What the memory holds true\n"
    + render_facts(bundle.facts)
    + "\n\n## What sources said\n"
    + render_claims(bundle.claims_and_sources)
)
```

Put `context` in the system prompt or in a clearly marked block before the
user's question, together with instructions on how to use it. [Handle
unknowns and ambiguity](unknowns-and-ambiguity.md) has a system-prompt
paragraph you can copy.

## Size it to your token budget

The result size is set almost entirely by these numbers:

- **`facts_context`**: `k` facts, each with up to `evidence_per_fact`
  supporting claims and as many contradicting ones, capped at 60 evidence
  links. Each claim adds its text and its quoted passage. For a tight
  budget, `k=8, evidence_per_fact=1` keeps one quote per fact.
- **`claims_and_sources_context`**: up to `k` claims and up to `k`
  passages, default 50 each.
  Passages are whole chunks of source text and are the largest part of any
  result. Lower `k` first.
- **`combined_context`**: fixed at both defaults. When it is too large,
  call `facts_context` and `claims_and_sources_context` separately with
  smaller `k`.

Render, count, and trim from the bottom of each list: results come ranked.
If you trim, tell the model the list is partial, the same way the
truncation line above does.

## Mistakes to avoid

Each of these turns a correct result into a wrong answer. Put the ones
your agent is prone to into its instructions.

| Don't | Do |
|---|---|
| Answer "is it true now?" from `claims_and_sources_context`. A claim is what one source said, possibly months ago. | Answer from `facts_context`. Use claims to quote and cite. |
| Read a claim's `claim_valid_from`/`claim_valid_until` as proof that something held at a date. That window is the source's statement. | Ask `facts_context` with `time` set, and read the fact's `validity`. See [Ask about the past](ask-about-the-past.md). |
| Read `asserted_at` or `ingested_at` as when something happened. | Use the fact's `valid_from`/`valid_until`; `asserted_at` is when a source said it, `ingested_at` when the memory learned it. |
| Treat an empty result as "no" or "unknown name". | Read `negative.kind`: `unknown_entity`, `known_empty` and `boundary` need three different answers. |
| Count a truncated list as complete. | When `truncation.truncated` is `true`, say "at least N", raise `k`, or narrow the query. |
| Report one side of a contradiction, or pick the side with more sources. | Give every side in `contradiction.co_members` with its sources, and say when `returned` is less than `total`. |
| Take the first of several `resolve_entity` candidates. | Ask which one is meant, rank them with context, or pass them all as `entity_ids`. See [Handle unknowns and ambiguity](unknowns-and-ambiguity.md#names-that-match-more-than-one-entity). |
| Count `temporal_match: possible` facts as matches. | Report them apart from `confirmed` ones. |
| Present a fact with `support: withdrawn` as settled. | Say it is unconfirmed and check its evidence. |
| Ask about a document right after ingesting it and conclude it says nothing. | Wait until [readiness](wait-for-readiness.md) reports `ready`. |

## Next

- [Ask about the past](ask-about-the-past.md)
- [Cite the source of an answer](cite-sources.md)
- [Handle unknowns and ambiguity](unknowns-and-ambiguity.md)
- [Reading a result](../concepts/reading-results.md)
