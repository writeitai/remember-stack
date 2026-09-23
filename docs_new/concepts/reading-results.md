---
title: Reading a result
description: The envelope every RememberStack read returns, field by field, with an annotated example, and the ContextBundle/v2 wrapper.
applies_to: [remember.dev, self-hosted]
---

# Reading a result

A list of text snippets tells your agent nothing about itself. Is this
everything, or the top ten of thousands? Is it current? Did the search find
nothing, or does the entity not exist? Are these facts or somebody's
opinion? An agent that cannot tell guesses, and guesses sound exactly like
answers.

Every RememberStack read returns an **envelope**: the results plus an
account of what they are. The envelope says what kind of truth it holds,
which time it describes, whether it was cut short, what was dropped and why,
and, when the answer is "no", which kind of "no".

## The envelope at a glance

| Field | What it tells you |
|---|---|
| `grain` | What kind of truth the results are. |
| `temporal_scope` | Which time the answer describes, and when it was evaluated. |
| `entities` | Entity candidates (from resolving a name). |
| `facts` | Relations and observations. |
| `evidence` | Claims, each with its provenance. |
| `fact_evidence` | Which claim backs which fact, and with which stance. |
| `evidence_totals` | Exact evidence counts per fact and stance. |
| `chunks` | Source passages. |
| `sources` | Source documents. |
| `transcript` | Decision history. |
| `nodes`, `edges`, `paths` | Graph results. |
| `ranking` | The order results were ranked in, with each score. |
| `changes`, `aggregate`, `pages` | Change feeds, counts and compiled pages (used by specialised reads). |
| `freshness` | How current the data behind the answer is. |
| `truncation` | Whether the results were capped, and how many exist. |
| `dropped_by_hydration` | How many candidates were found but failed confirmation. |
| `excluded_unstamped` | How many undated claims a time filter had to leave out. |
| `negative` | Why the answer is empty, as a typed reason. |

Lists a read does not fill are empty (`[]`); single values it does not fill
are `null`.

## An annotated example

Ravi's `entity_id` was found with `resolve_entity`. This asks what he is
working on now:

```python
import remember

with remember.Client() as memory:
    result = memory.facts_context(
        "what is Ravi working on",
        entity_ids=["0b6c4f7e-3d2a-4e91-8c1f-5a7d9e2b4c60"],
    )
    print(result.model_dump_json(indent=2, by_alias=True))
```

```json
{
  "grain": "fact",
  "temporal_scope": {
    "mode": "current",
    "evaluated_at": "2026-09-23T08:15:02.113000Z",
    "believed_at": "2026-09-23T08:15:02.113000Z",
    "identity_regime": "current"
  },
  "entities": [],
  "facts": [
    {
      "fact_id": "7d2e9a14-58c3-4f0b-a6e1-3c9b2d7f8e05",
      "kind": "relation",
      "label": "Ravi works on search team",
      "evidence_count": 2,
      "validity": {
        "valid_from": "2026-06-01T00:00:00Z",
        "valid_until": null,
        "valid_precision": "open",
        "ingested_at": "2026-06-12T14:11:37Z",
        "invalidated_at": null
      },
      "temporal_match": "confirmed",
      "contradiction_group": null,
      "contradiction": null,
      "support": "current"
    },
    {
      "fact_id": "c41f0b8e-92a7-4d63-b5e2-8f1a6c3d9e27",
      "kind": "observation",
      "label": "Ravi is on call for billing incidents.",
      "evidence_count": 1,
      "validity": {
        "valid_from": null,
        "valid_until": null,
        "valid_precision": "unknown",
        "ingested_at": "2026-02-02T09:30:12Z",
        "invalidated_at": null
      },
      "temporal_match": "possible",
      "contradiction_group": null,
      "contradiction": null,
      "support": "current"
    }
  ],
  "evidence": [
    {
      "claim_id": "e5a8c2d1-6f3b-4a97-8d0e-1b4c7f2a9e36",
      "doc_id": "3f9d1c6a-2b8e-5c47-9a1d-6e0f3b8c2d74",
      "chunk_id": "91c7e4b2-0d5a-4f86-b3e9-7a2c1d8f6e40",
      "claim_text": "Ravi moved from the billing migration to the search team on 2026-06-01.",
      "source_span": "Ravi moved from the billing migration to the search team on 1 June.",
      "char_start": 412,
      "char_end": 479,
      "evidence_spans": [{"char_start": 412, "char_end": 479}],
      "is_attributed": false,
      "is_current_testimony": true,
      "asserted_at": "2026-06-12T14:00:00Z",
      "claim_valid_from": "2026-06-01T00:00:00Z",
      "claim_valid_until": "2026-06-01T00:00:00Z",
      "claim_valid_precision": "day",
      "claim_valid_kind": "event_time",
      "document_title": "2026-06-12-retro",
      "source_kind": "notes",
      "corroboration_count": null,
      "grouped_claim_ids": []
    },
    {
      "claim_id": "2b7f9d3e-4c1a-4e58-a0d6-9f3e2c7b1a85",
      "doc_id": "8a2e6d4f-1c9b-5f30-8e7a-2d5c9b1f4e63",
      "chunk_id": "4e8a1f6c-7b3d-4c29-9e05-3a6d8f2c1b97",
      "claim_text": "Ravi is on call for billing incidents.",
      "source_span": "Ravi is on call for billing incidents.",
      "char_start": 96,
      "char_end": 134,
      "evidence_spans": [{"char_start": 96, "char_end": 134}],
      "is_attributed": false,
      "is_current_testimony": true,
      "asserted_at": "2026-02-02T09:00:00Z",
      "claim_valid_from": null,
      "claim_valid_until": null,
      "claim_valid_precision": "unknown",
      "claim_valid_kind": null,
      "document_title": "oncall-rota",
      "source_kind": "notes",
      "corroboration_count": null,
      "grouped_claim_ids": []
    }
  ],
  "fact_evidence": [
    {
      "fact_kind": "relation",
      "fact_id": "7d2e9a14-58c3-4f0b-a6e1-3c9b2d7f8e05",
      "claim_id": "e5a8c2d1-6f3b-4a97-8d0e-1b4c7f2a9e36",
      "stance": "supports"
    },
    {
      "fact_kind": "observation",
      "fact_id": "c41f0b8e-92a7-4d63-b5e2-8f1a6c3d9e27",
      "claim_id": "2b7f9d3e-4c1a-4e58-a0d6-9f3e2c7b1a85",
      "stance": "supports"
    }
  ],
  "evidence_totals": [
    {"fact_kind": "relation", "fact_id": "7d2e9a14-58c3-4f0b-a6e1-3c9b2d7f8e05", "stance": "supports", "returned": 1, "total": 2},
    {"fact_kind": "relation", "fact_id": "7d2e9a14-58c3-4f0b-a6e1-3c9b2d7f8e05", "stance": "contradicts", "returned": 0, "total": 0},
    {"fact_kind": "observation", "fact_id": "c41f0b8e-92a7-4d63-b5e2-8f1a6c3d9e27", "stance": "supports", "returned": 1, "total": 1},
    {"fact_kind": "observation", "fact_id": "c41f0b8e-92a7-4d63-b5e2-8f1a6c3d9e27", "stance": "contradicts", "returned": 0, "total": 0}
  ],
  "chunks": [],
  "sources": [],
  "transcript": [],
  "nodes": [
    {"entity_id": "5e1b8c3a-9d7f-4a26-b0c4-6f2e8d1a3b59", "name": "search team", "hops": 1}
  ],
  "paths": [],
  "edges": [],
  "ranking": [],
  "changes": [],
  "aggregate": null,
  "pages": [],
  "freshness": {
    "pg_live_ts": "2026-09-23T08:15:02.113000Z",
    "p1_written_inline": true,
    "p1_believed_at_horizon": null,
    "k": null
  },
  "truncation": {
    "truncated": false,
    "returned": 2,
    "estimated_total": 2,
    "total_is_exact": true,
    "continuation": null,
    "reason": null
  },
  "dropped_by_hydration": 0,
  "excluded_unstamped": 0,
  "negative": null
}
```

Reading it top to bottom:

- **`grain: "fact"`**: these are adjudicated facts, not raw testimony.
- **`temporal_scope`**: the answer describes the world *now*
  (`mode: "current"`), as of 2026-09-23 08:15 UTC, using today's identities.
- **The first fact** is a relation, true since 2026-06-01 and still open,
  supported by 2 distinct documents. Its window is complete, so its time
  match is `confirmed`. Ravi's earlier billing migration work is not here:
  it ended on 2026-06-01, and this is a `current` read.
- **The second fact** has no date. It is returned because nothing rules it
  out, and marked `possible` so the agent does not mistake it for a
  confirmed current fact.
- **`evidence` and `fact_evidence`**: one claim per fact was returned; each
  link says which fact it backs and that it supports it. The first claim
  shows the relative-date resolution: the retro said "on 1 June", the claim
  says 2026-06-01.
- **`evidence_totals`**: the relation has 2 supporting documents and only
  1 claim was returned, so there is more evidence to fetch
  (`hydrate_relation`). Nothing contradicts either fact.
- **`nodes`**: the graph expansion from Ravi reached the search team, one
  hop away.
- **`truncation`**: both matching facts were returned; the total is exact.
- **`negative: null`**: the answer is not empty.

## Field reference

### `grain`

What kind of truth the result holds. Never mixed within one envelope.

| Value | Meaning | Returned by |
|---|---|---|
| `fact` | Adjudicated facts and entity candidates. | `facts_context`, `resolve_entity`, lookups |
| `evidence` | Claims and source passages: what sources said. | `claims_and_sources_context`, search |
| `composite` | A fact together with its evidence, sources or history. | `hydrate_relation`, `transcript_relation` |
| `compiled` | Compiled knowledge pages. | Not served by the default routes. |

### `temporal_scope`

Always present. Its `mode` is one of `current`, `at` (with `at`),
`overlap` (with `from` and `to`), `history` or `as_of` (with `valid_at`).
Every mode carries `evaluated_at` (the instant the read ran), `believed_at`
(the belief-time instant it read) and `identity_regime`: `current` means
today's aliases and merges were used, even for a past time. See
[Time](time.md#asking-about-time).

### `entities`

`EntityCandidate` objects from name resolution: `entity_id`,
`canonical_name`, `tier` (`T0` exact alias, `T1` similar spelling, `T2`
similar sound, `T3` profile embedding) and `context_hits` (how many current
relations connect it to the entities you said were in focus). More than one
candidate means ambiguity. See [Entities](entities.md).

### `facts`

`FactResult` objects:

| Field | Meaning |
|---|---|
| `fact_id` | The relation or observation ID. |
| `kind` | `relation` or `observation`. |
| `label` | The fact as a readable sentence, without dates. |
| `evidence_count` | Distinct documents whose current testimony supports it. |
| `validity` | `valid_from`, `valid_until` (exclusive), `valid_precision`, `ingested_at`, `invalidated_at`. See [Time](time.md). |
| `temporal_match` | `confirmed` or `possible`. |
| `contradiction_group` | The contradiction group's ID, or `null`. |
| `contradiction` | The other sides of the contradiction, inline. See [Contradictions](contradictions.md). |
| `support` | `current`, or `withdrawn` when its only support was lost to a processing change. See [Facts](facts.md#support-withdrawn). |

### `evidence`

`EvidenceResult` objects, one per claim:

| Field | Meaning |
|---|---|
| `claim_id`, `doc_id`, `chunk_id` | Where the claim lives. |
| `claim_text` | The standalone claim. |
| `source_span`, `char_start`, `char_end` | The origin passage and its position in the version's converted text. |
| `evidence_spans` | Every supporting range, origin first. |
| `is_attributed` | Whether it records someone's statement or stance. |
| `is_current_testimony` | Whether it still counts as what its source says. |
| `asserted_at` | When the source said it. |
| `claim_valid_from`, `claim_valid_until`, `claim_valid_precision`, `claim_valid_kind` | When the claim says it happened or was true (inclusive end). |
| `document_title`, `source_kind` | Which document. |
| `corroboration_count` | Distinct documents that stated the same claim (set by `claims_and_sources_context`). |
| `grouped_claim_ids` | The claims folded into this one by that grouping. |

See [Claims](claims.md) and [Evidence](evidence.md).

### `fact_evidence` and `evidence_totals`

`fact_evidence` links facts to the claims in `evidence`: `fact_kind`,
`fact_id`, `claim_id`, `stance` (`supports` or `contradicts`).
`evidence_totals` has one entry per fact and stance with `returned` (links in
this envelope) and `total` (links that exist). When `returned` is less than
`total`, you are seeing a sample.

### `chunks`

`ChunkEvidenceResult` objects, source passages: `chunk_id`, `doc_id`,
`version_id`, `representation_id`, `chunk_text`, `context_prefix`,
`char_start`, `char_end`, `section_role`, `document_title`, `source_kind`,
`source_modified_at`, `published_at`. Passages are kept separate from claims:
a passage is raw source text, a claim is an extracted statement.

### `sources`

`SourceRecord` objects: `doc_id`, `title`, `source_kind`, `markdown_uri`
(the converted text), and, where the read computes them, `mention_count`,
`first_mentioned_at` and `last_mentioned_at`.

### `transcript`

`TranscriptEntry` objects: the decision history. See
[Evidence](evidence.md#why-do-we-believe-this).

### `nodes`, `edges`, `paths`

Graph results. A `GraphNode` has `entity_id`, `name` and `hops` (distance
from the start). A `GraphEdge` is a relation: `relation_id`, `subject_id`,
`object_id`, `predicate`, `fact` (its label), `evidence_count`, the window
and belief fields, and `support`. A `GraphPath` has `length`, `nodes` and
`edges`, and is returned whole or not at all.

### `ranking`

`RankedItem` objects in rank order: `item_id`, `score` and `signals`. For a
fused search the score is the RRF sum, and `signals` holds each channel's
contribution (`channel_0` semantic, `channel_1` BM25). See
[Retrieval](retrieval.md#how-hybrid-retrieval-works).

### `freshness`

`pg_live_ts` is the database instant the answer was read at. The other
fields (`p1_written_inline`, `p1_believed_at_horizon`, `k`) describe search
index lag and compiled pages; in current deployments search indexes are
written with the data (`true`) and have no belief-time horizon (`null`).

### `truncation`

`truncated` says whether more results exist than were returned. `returned`
is how many came back, `estimated_total` how many were found, and
`total_is_exact` whether that total is exact. `continuation` is an opaque
cursor for reads that can page (the graph neighbourhood). A capped answer
always says so.

### `dropped_by_hydration`

How many candidates the search found but that failed confirmation against
live data: no longer current, retracted, outside the time scope, or merged
away. A non-zero value is normal and means stale index entries were filtered
out, not that something is broken.

### `excluded_unstamped`

For reads that filter claims by their stated time: how many claims were left
out because they carry no date. None of the assured operations or HTTP
routes filters claims this way today, so it is `0`.

### `negative`

When the answer is empty, `negative` says why, and each kind calls for a
different reaction:

| `kind` | Meaning | What your agent should do |
|---|---|---|
| `unknown_entity` | The name or ID does not match any entity in memory. | Check the spelling, try another name, or say memory has nothing on it. |
| `known_empty` | The entity or query is valid, but nothing matches. | Answer "nothing recorded", or broaden the query. |
| `boundary` | The read cannot answer this shape (a limit of the capability, such as an unavailable graph). | Use the `workaround` the negative names. |

Each negative has an `explanation` and, where one exists, a `workaround`.
Forgotten content is indistinguishable from content that never existed; there
is no separate "deleted" kind. See
[Handle unknowns and ambiguity](../guides/unknowns-and-ambiguity.md).

## ContextBundle/v2

`combined_context` returns two envelopes side by side instead of one:

```json
{
  "contract": "ContextBundle/v2",
  "claims_and_sources": {"grain": "evidence", "...": "..."},
  "facts": {"grain": "fact", "...": "..."}
}
```

`claims_and_sources` is exactly a `claims_and_sources_context` envelope
(grain `evidence`) and `facts` is exactly a `facts_context` envelope (grain
`fact`). They are never merged, so testimony and belief stay distinguishable,
and each keeps its own truncation, drops and negative. In Python it is a
`remember.ContextBundleV2`:

```python
with remember.Client() as memory:
    bundle = memory.combined_context("billing migration cutover")
    for fact in bundle.facts.facts:
        print("fact:", fact.label, fact.evidence_count)
    for claim in bundle.claims_and_sources.evidence:
        print("said:", claim.claim_text, claim.asserted_at)
```

## Where to go next

- [Result types](../reference/result-types.md): the complete schemas.
- [Handle unknowns and ambiguity](../guides/unknowns-and-ambiguity.md).
- [Cite the source of an answer](../guides/cite-sources.md).
