---
title: Contradictions, corroboration and supersession
description: How RememberStack shows disagreement between sources instead of hiding it, how it counts independent support, and how it tells a change from a conflict.
applies_to: [remember.dev, self-hosted]
---

# Contradictions, corroboration and supersession

Sources disagree. The spec says the cutover is on June 8; a standup
transcript says June 15. A memory that quietly keeps one of them hands your
agent a confident answer that is wrong half the time, and nobody finds out
until the date arrives.

RememberStack does not pick a side for you. When facts conflict, every side
is returned together, each with its own evidence. When sources agree, it
tells you how many independent sources agree. And when something changed
rather than conflicted, it records the change instead of calling it a
disagreement.

## Contradictions

Two facts **contradict** when they cannot both be true: two different
cutover dates for the same cutover, two different owners of the same task at
the same time. During [adjudication](facts.md#how-a-claim-changes-the-facts)
a conflict is recorded in one of two ways:

- **Contradicting evidence on one fact.** The incoming claim disputes an
  existing fact directly ("Ravi did not approve the schema change"). The
  claim attaches to that fact as evidence with stance `contradicts`.
- **A contradiction group.** The incoming claim states a different,
  incompatible value. It becomes its own fact, and the facts are linked in a
  contradiction group. Each fact in the group is a **co-member** of the
  others.

### Every side, every time

A fact that belongs to a live contradiction group is never returned alone.
Its `FactResult` carries:

- `contradiction_group`: the group's ID,
- `contradiction`: a block with the other sides inline.

```json
"contradiction": {
  "group_id": "5b0e2f3c-7a41-4d8e-9c55-0f6a2d1e8b90",
  "co_members": [
    {
      "fact_id": "a3f1c9e2-1b7d-4c0a-8e6f-2d9b4c7a1e53",
      "label": "The billing migration cutover is scheduled for 2026-06-15.",
      "evidence_count": 1,
      "validity": {
        "valid_from": null,
        "valid_until": null,
        "valid_precision": "unknown",
        "ingested_at": "2026-04-28T09:12:44Z",
        "invalidated_at": null
      }
    }
  ],
  "returned": 1,
  "total": 1,
  "continuation": null
}
```

Up to 25 co-members come back inline. Beyond that, the block still carries
`group_id`, `returned` and `total`, so the agent knows how many sides exist.
Returning one side of a contradiction without the others is treated as a
bug, not a ranking choice.

What your agent does with it is its decision: prefer the side with more
independent evidence, prefer the more recent source, or tell the user the
sources disagree and cite both. See
[Handle unknowns and ambiguity](../guides/unknowns-and-ambiguity.md).

### Stance on evidence

Every link between a fact and a claim has a stance: `supports` or
`contradicts`. In a `facts_context` result:

- `fact_evidence` lists the sampled links, each with its `stance`,
- `evidence_totals` gives the exact total per fact **and per stance**, with
  how many were returned.

So an agent can see "3 documents support this, 1 disputes it" without
fetching every claim.

## Corroboration

**Corroboration** is independent sources saying the same thing. RememberStack
counts it by **distinct documents**, never by versions, claims or
repetitions:

- `evidence_count` on a fact is the number of distinct documents whose
  current testimony supports it.
- `corroboration_count` on a claim in a `claims_and_sources_context` result
  is the number of distinct documents that stated the same claim. Claims are
  grouped only when their normalized text, their said-on time, their
  world-time fields and their attribution all agree; `grouped_claim_ids`
  lists the claims folded into the one shown.

The rule protects the count from inflating by accident:

| Situation | Counts as |
|---|---|
| One transcript repeats a statement five times. | 1 |
| A spec has ten versions that all say it. | 1 |
| A newer extractor re-reads the same file. | 1 (the new claims replace the old ones as current testimony) |
| The spec and a separate meeting note both say it. | 2 |

A high count means many separate sources, not one source that talks a lot.

## Supersession is not contradiction

"Ravi works on the billing migration" (January) and "Ravi moved to the
search team" (June) do not conflict. Both were true, at different times.
Treating them as a contradiction would force a choice that should not be
made; treating them as unrelated would leave both "current".

RememberStack handles this as **supersession**: the later fact is the
successor, and the earlier fact's world-time window is closed at the
successor's start. The earlier fact stays in memory, true of its period,
and stops matching `current` queries. See [Time](time.md).

| | Contradiction | Supersession |
|---|---|---|
| What it means | Sources disagree about the same thing at the same time. | The world changed; both statements were true in turn. |
| What happens | Both facts stay current and are linked in a group, or the claim attaches with stance `contradicts`. | The earlier fact's `valid_until` is set to the successor's start. |
| What a `current` query returns | Every side, together. | Only the successor. |
| What a `history` query returns | Every side, together. | Both, each with its window. |

Corrections are a third case. "The cutover moved from June 8 to June 9" is
neither a new fact nor a conflict when the source is correcting the date of
the same cutover: the fact keeps its identity and its window is replaced.

## Where to go next

- [Facts](facts.md): the adjudication decisions behind all three cases.
- [Evidence](evidence.md): the claims on each side.
- [Reading a result](reading-results.md): `contradiction`,
  `evidence_totals` and `corroboration_count` in context.
