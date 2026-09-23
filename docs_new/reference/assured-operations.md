---
title: Assured operations
description: The four fixed retrievals RememberStack guarantees, with every parameter, limit, default and result contract.
applies_to: [remember.dev, self-hosted]
---

# Assured operations

An assured operation is a retrieval whose plan, parameters and result shape
are fixed by RememberStack and checked on every deployment. There are exactly
four:

| Operation | Version | Answers from | Result | Grain |
|---|---|---|---|---|
| [`resolve_entity`](#resolve_entity) | 1 | identity | `Envelope` | `fact` |
| [`claims_and_sources_context`](#claims_and_sources_context) | 2 | what sources said | `Envelope` | `evidence` |
| [`facts_context`](#facts_context) | 3 | what is held true | `Envelope` | `fact` |
| [`combined_context`](#combined_context) | 4 | both, side by side | `ContextBundle/v2` | none (two envelopes) |

None of them calls a language model. `claims_and_sources_context`,
`facts_context` and `combined_context` embed the query text to search by
meaning, and `resolve_entity` embeds the name only when no alias matches;
embedding is not generation.

The same four are offered everywhere: `POST /operations/{name}` over HTTP,
`Client.run_operation` and the named helpers in the `remember` package, and
one MCP tool each. Their descriptors (`GET /operations`) carry the exact JSON
Schema of the inputs, the result schema and an `implementation_plan_hash`, so
an agent can check it is calling the version it expects. The route is
described in [Assured operation routes](http-api/operations.md). For anything
these four do not cover, use the [direct routes](http-api/entities-and-facts.md),
[search](http-api/search.md), the [graph](http-api/graph.md) or
[SQL queries](http-api/query.md).

## Choosing one

| You want | Use |
|---|---|
| The entity ids behind a name ("Dana", "billing migration") | `resolve_entity` |
| Everything the sources said about a topic, with the passages, for high recall | `claims_and_sources_context` |
| What the memory holds true now, or at a time, with its evidence and contradictions | `facts_context` |
| Context for an agent turn: both of the above in one call | `combined_context` |

Resolve names first. The context operations take `entity_ids`, not names, so
the usual flow is `resolve_entity`, pick the candidates you mean, then pass
their ids. See [Give an agent context](../guides/agent-context.md).

## Arguments

Arguments are one JSON object. They are validated against the descriptor
before anything runs:

- unknown keys are refused;
- a missing required key is refused;
- strings must be strings; integers must be integers (`3.0` is accepted,
  `true` and `"3"` are not);
- `entity_ids` must be an array of UUID strings with no duplicates, and at
  least one item if present;
- lengths, item counts and ranges below are enforced.

A refused argument is HTTP `422` with
`{"detail": {"code": "invalid_parameter", "message": "…"}}`.

## The time object

`facts_context` and `combined_context` take an optional `time` object that
fixes which facts count. The default is `{"mode": "current"}`.

| Mode | Shape | Selects | Result `temporal_scope.mode` |
|---|---|---|---|
| `current` | `{"mode": "current"}` | Facts valid at the moment the call runs. | `current` |
| `at` | `{"mode": "at", "at": "2026-06-01T00:00:00Z"}` | Facts whose validity covers that instant. | `at` |
| `overlap` | `{"mode": "overlap", "from": "2026-04-01T00:00:00Z", "to": "2026-06-30T23:59:59Z"}` | Facts whose validity overlaps the interval, both ends included. `to` must not be before `from`. | `overlap` |
| `history` | `{"mode": "history"}` | Every currently believed validity interval that began by the moment the call runs. | `history` |

Rules for the timestamps:

- they must be full ISO 8601 date-times (`2026-06-01T00:00:00Z`), not dates;
- they must carry a time zone (`Z` or an offset); the engine converts them to
  UTC;
- the object takes no other keys.

In every mode the memory's knowledge is taken as it stands now: the result's
`believed_at` is the call's evaluation time. To read what the memory believed
at a past instant, use the [graph routes](http-api/graph.md) or the
[`facts_as_of`](query-space.md#facts_as_of) SQL function.

Each returned fact carries `temporal_match`: `confirmed` when its validity
certainly matches the requested time, `possible` when it may (for example, a
fact with no known start). See [Time](../concepts/time.md).

## resolve_entity

Resolve a name to the current entities it can mean, ranked, without guessing.

**Version** 1. **Answer intent** `identity`. **Result contract** `envelope`,
grain `fact`. **Plan** one step: `resolve_entity`.

### Parameters

| Name | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `name` | string | yes | | At least 1 character. |

### What it does

Matching runs in tiers and stops at the first that finds anything: exact
alias (`T0`, every entity with that alias, uncapped), trigram (`T1`),
phonetic (`T2`), then embedding similarity against entity profiles (`T3`).
The fuzzy and embedding tiers stop at the ingest pipeline's candidate width and
say so in `truncation` (`reason: "resolve_candidate_limit"`). Several
candidates mean the name is ambiguous; the operation never picks one for you.

### Result

`entities` holds the candidates, best first, each with `entity_id`,
`canonical_name` and `tier`. No match: `negative.kind` `unknown_entity`. The
embedding tier was needed but the entity index is not published:
`negative.kind` `boundary`.

The HTTP route [`GET /resolve`](http-api/entities-and-facts.md#get-resolve)
does the same and also accepts `context_entity_ids` to reorder candidates.

### Example

```bash
curl -s -X POST "$REMEMBER_API_URL/operations/resolve_entity" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"name": "billing migration"}'
```

```python
from remember import Client

memory = Client()
envelope = memory.resolve_entity("billing migration")
ids = [candidate.entity_id for candidate in envelope.entities]
```

## claims_and_sources_context

Return what the sources said about a query: claims and the source passages
around them, with high recall. This is testimony, not the memory's settled
view.

**Version** 2. **Answer intent** `claims_and_sources`. **Result contract**
`envelope`, grain `evidence`. **Plan** one step: `claims_and_sources_context`.

### Parameters

| Name | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | | 1 to 8,192 characters. |
| `entity_ids` | array of UUID | no | none | 1 to 20 unique ids. Restricts the search to claims and chunks about these entities. |
| `k` | integer | no | `50` | 1 to 100. The most claims, and separately the most chunks, returned. |
| `candidate_k` | integer | no | `200` | 1 to 400, and at least `k`. How many candidates each search channel nominates. |

### What it does

1. Claims and chunks are searched separately. Each is searched twice — by
   meaning (semantic) and by keyword (BM25) — with up to `candidate_k`
   candidates per channel. Only current testimony is searched.
2. The two rankings are fused by reciprocal rank (constant 60).
3. Every candidate is re-read from the database; what no longer holds is
   dropped and counted in `dropped_by_hydration`.
4. Claims with identical text are grouped: the first carries
   `corroboration_count` and the ids of the others in `grouped_claim_ids`.
5. The top `k` claims and the top `k` chunks are returned.

If any id in `entity_ids` is not a current entity, nothing is searched and
the result is `negative.kind` `unknown_entity`.

### Result

`evidence` holds up to `k` [`EvidenceResult`](result-types.md#evidenceresult)
entries and `chunks` up to `k`
[`ChunkEvidenceResult`](result-types.md#chunkevidenceresult) entries.
`temporal_scope.mode` is `current`. `truncation` is set when more results
existed than `k`, or when a channel returned its full `candidate_k` (then
`total_is_exact` is `false`). Nothing found: `negative.kind` `known_empty`.

### Example

```bash
curl -s -X POST "$REMEMBER_API_URL/operations/claims_and_sources_context" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "When is the billing migration cutover?", "k": 20}'
```

```python
envelope = memory.run_operation(
    name="claims_and_sources_context",
    arguments={"query": "When is the billing migration cutover?", "k": 20},
)
for claim in envelope.evidence:
    print(claim.asserted_at, claim.document_title, claim.claim_text)
```

## facts_context

Return the facts the memory holds true — relations and observations it has
adjudicated — for a query, under an explicit time scope, each with its
supporting and contradicting evidence.

**Version** 3. **Answer intent** `facts`. **Result contract** `envelope`,
grain `fact`. **Plan** two steps: `graph_neighborhood`, then `facts_context`.

### Parameters

| Name | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | | 1 to 8,192 characters. |
| `entity_ids` | array of UUID | no | none | 1 to 19 unique ids. The anchors. |
| `k` | integer | no | `15` | 1 to 30. The most facts returned. |
| `evidence_per_fact` | integer | no | `3` | 1 to 5. The most claims returned per fact and per stance (supporting, contradicting). |
| `hops` | integer | no | `1` | 1 to 2. How far the live graph expands from each anchor. |
| `predicate` | string | no | none | 1 to 200 characters. Only relations with this predicate are expanded and returned. |
| `time` | object | no | `{"mode": "current"}` | See [The time object](#the-time-object). |

### What it does

Without `entity_ids`, facts are searched across the whole deployment by
meaning. Entity profiles are searched too, and facts about the 20 best
matching entities are fused in, which helps list-shaped questions.

With `entity_ids`:

1. Every anchor must be a current entity. If one is not, the result is
   `negative.kind` `unknown_entity`.
2. For `current` and `at`, the live graph expands each anchor by `hops`
   (following only `predicate` edges when given), filling at most 20 entities
   in total, anchors included. For `overlap` and `history` there is no single
   instant to expand at, so only the anchors are used.
3. Facts are searched inside that set of entities.

Then, in every case: up to 200 candidates are nominated, confirmed against
the database in batches until `k` facts (plus one, to know whether there are
more) have been confirmed, and their evidence is attached — up to
`evidence_per_fact` claims per fact and stance, and at most 60 claims in the
whole result.

The operation has a 25-second database budget. If it runs out, or the live
graph or search index cannot answer, the result is `negative.kind`
`boundary` with an explanation, never a silently smaller answer.

### Result

| Field | Contents |
|---|---|
| `facts` | Up to `k` [`FactResult`](result-types.md#factresult) entries: `kind` (`relation` or `observation`), `label`, `validity`, `temporal_match`, `support`, and the other sides of any contradiction. |
| `evidence` | The claims attached to those facts, each once. |
| `fact_evidence` | Which claim supports or contradicts which fact (`stance`). |
| `evidence_totals` | For each fact and each stance, how many claims were `returned` and how many exist in `total`. |
| `nodes` | The neighbours the graph expansion added, when `entity_ids` were given. |
| `temporal_scope` | The time mode used, with `evaluated_at` and `believed_at`. |
| `truncation` | Whether more facts (or neighbours) existed. |
| `dropped_by_hydration` | Candidates that failed confirmation, plus neighbours that were no longer current. |
| `negative` | `known_empty` when no fact matches; `unknown_entity` or `boundary` as above. |

### Example

```bash
curl -s -X POST "$REMEMBER_API_URL/operations/facts_context" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"who owns what\", \"entity_ids\": [\"$BILLING_MIGRATION_ID\"],
       \"time\": {\"mode\": \"at\", \"at\": \"2026-06-01T00:00:00Z\"}}"
```

A trimmed response (empty arrays and some fields left out):

```json
{
  "grain": "fact",
  "temporal_scope": {
    "mode": "at",
    "at": "2026-06-01T00:00:00Z",
    "evaluated_at": "2026-09-23T10:00:00Z",
    "believed_at": "2026-09-23T10:00:00Z",
    "identity_regime": "current"
  },
  "facts": [
    {
      "fact_id": "c3d2…",
      "kind": "relation",
      "label": "Ravi owns the billing migration",
      "evidence_count": 4,
      "validity": {
        "valid_from": "2026-03-02T00:00:00Z",
        "valid_until": null,
        "valid_precision": "day",
        "ingested_at": "2026-03-03T08:12:40Z",
        "invalidated_at": null
      },
      "temporal_match": "confirmed",
      "contradiction_group": null,
      "contradiction": null,
      "support": "current"
    }
  ],
  "evidence": [
    {
      "claim_id": "e8f1…",
      "doc_id": "a1f3…",
      "chunk_id": "0b7d…",
      "claim_text": "Ravi is taking over the billing migration from 2 March 2026.",
      "source_span": "Ravi takes over billing migration from Monday",
      "char_start": 1204,
      "char_end": 1249,
      "is_attributed": true,
      "is_current_testimony": true,
      "asserted_at": "2026-02-27T15:00:00Z",
      "document_title": "Weekly sync 2026-02-27",
      "source_kind": "transcripts"
    }
  ],
  "fact_evidence": [
    {"fact_kind": "relation", "fact_id": "c3d2…", "claim_id": "e8f1…", "stance": "supports"}
  ],
  "evidence_totals": [
    {"fact_kind": "relation", "fact_id": "c3d2…", "stance": "supports", "returned": 1, "total": 4},
    {"fact_kind": "relation", "fact_id": "c3d2…", "stance": "contradicts", "returned": 0, "total": 0}
  ],
  "freshness": {"pg_live_ts": "2026-09-23T10:00:00Z", "p1_written_inline": true, "p1_believed_at_horizon": null, "k": null},
  "truncation": {"truncated": false, "returned": 3, "estimated_total": 3, "total_is_exact": true, "continuation": null, "reason": null},
  "dropped_by_hydration": 0,
  "excluded_unstamped": 0,
  "negative": null
}
```

```python
envelope = memory.facts_context(
    "who owns what",
    entity_ids=[billing_migration_id],
    time={"mode": "at", "at": "2026-06-01T00:00:00Z"},
)
```

## combined_context

Return `claims_and_sources_context` and `facts_context` for the same query in
one response, side by side and never blended.

**Version** 4. **Answer intent** `combined_context`. **Result contract**
`context_bundle_v2`; no single grain. **Plan** an operation bundle whose
children are `claims_and_sources_context` then `facts_context`; its
`implementation_plan_hash` covers both children's plan hashes.

### Parameters

| Name | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | | 1 to 8,192 characters. |
| `entity_ids` | array of UUID | no | none | 1 to 19 unique ids. Passed to both children. |
| `hops` | integer | no | `1` | 1 to 2. Passed to `facts_context`. |
| `predicate` | string | no | none | 1 to 200 characters. Passed to `facts_context`. |
| `time` | object | no | `{"mode": "current"}` | Passed to `facts_context`. |

The children's other parameters are fixed: `claims_and_sources_context` runs
with `k` 50 and `candidate_k` 200; `facts_context` with `k` 15 and
`evidence_per_fact` 3. Both run at the same evaluation instant.

### Result

A [`ContextBundle/v2`](result-types.md#contextbundlev2):

```json
{
  "contract": "ContextBundle/v2",
  "claims_and_sources": {"grain": "evidence", "...": "an Envelope"},
  "facts": {"grain": "fact", "...": "an Envelope"}
}
```

Each child is a complete envelope with its own `negative`, `truncation` and
`temporal_scope`. `claims_and_sources` is always current; `facts` follows
`time`. Read them separately: a claim in the first is testimony, a fact in the
second is the memory's view. See [Reading a result](../concepts/reading-results.md).

### Example

```bash
curl -s -X POST "$REMEMBER_API_URL/operations/combined_context" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "billing migration status and risks"}'
```

```python
bundle = memory.combined_context("billing migration status and risks")
said = bundle.claims_and_sources.evidence
held = bundle.facts.facts
```

## Descriptor reference

`GET /operations` returns one [`ToolDescriptor`](result-types.md#tooldescriptor)
per operation. The values that identify the shipped build:

| Operation | `version` | `result_contract` | `output_grain` | `answer_intent` | `implementation_plan_hash` |
|---|---|---|---|---|---|
| `resolve_entity` | 1 | `envelope` | `fact` | `identity` | `fcf8f8bd08efe28d62af572dbd4f81601b03f1744d993021225454b55748059a` |
| `claims_and_sources_context` | 2 | `envelope` | `evidence` | `claims_and_sources` | `cb897d871de908ee47b4bd80b313cf3b1df86c6e6d2a83882c3c3c3320ce4278` |
| `facts_context` | 3 | `envelope` | `fact` | `facts` | `88264ee8ce445d7145b6138e8ea97e9c91d441512816186d5d064519499e339f` |
| `combined_context` | 4 | `context_bundle_v2` | `null` | `combined_context` | `d9e9b28cfdf398658459e17839e8f732437c5514aa9927d4f622d9d5175d0f10` |

The hashes are those recorded in the `memory_v1` manifest for this release.
The registry refuses any stored operation that does not match its canonical
definition byte for byte.
