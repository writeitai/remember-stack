---
title: Search and adjacent chunks
description: Search claims and source chunks by meaning or by keyword, and fetch the chunks around one you found.
applies_to: [remember.dev, self-hosted]
---

# Search and adjacent chunks

Search returns evidence, not facts. A claim is what one source said; a chunk
is a passage of a source document. Neither is a statement of what the memory
holds true. For that, use [`facts_context`](../assured-operations.md#facts_context)
or the [lookup routes](entities-and-facts.md).

Each search exists in two forms. The `GET` form puts the query in the URL; the
`POST` form puts it in the body. A query is often a person's own words, and a
URL is written to access logs, kept by proxies and saved in browser history.
The `POST` form keeps the query text out of all three, so prefer it for
anything a person typed. The `GET` form stays for existing clients; both
return the same result and cost the same.

All six routes need the `read` scope. On remember.dev each carries a `search`
spend hold. Base URL, authentication and error shapes are described in
[HTTP API conventions](index.md).

## How search works

1. **Nominate.** The chosen channel ranks candidate ids: `semantic` embeds the
   query and compares vectors; `bm25` ranks by keyword. Only current testimony
   is nominated for claims.
2. **Confirm.** Each candidate is re-read from the database. Anything that no
   longer holds — a deleted version, a forgotten source — is dropped, and the
   number dropped is reported in `dropped_by_hydration`.

A search that finds nothing returns `200` with `negative.kind` `known_empty`.

## GET /search/claims

Search claims.

### Parameters

| Name | In | Type | Required | Default | Constraints |
|---|---|---|---|---|---|
| `query` | query | string | yes | | |
| `k` | query | integer | no | `10` | 1 to 400. |
| `channel` | query | `semantic` \| `bm25` | no | `semantic` | |

### Response

`200` with an [`Envelope`](../result-types.md#envelope) of grain `evidence`.
`evidence` holds up to `k` [`EvidenceResult`](../result-types.md#evidenceresult)
entries in rank order.

### Errors

| Status | `detail` | Cause |
|---|---|---|
| `422` | validation list | `query` missing, `k` out of range, or an unknown `channel`. |
| `500` | `Internal Server Error` | The embedding call failed (semantic channel). |

### Example

```bash
curl -s -G "$REMEMBER_API_URL/search/claims" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  --data-urlencode "query=billing migration cutover date" \
  --data-urlencode "k=20"
```

```python
from remember import Client

memory = Client()
envelope = memory.search_claims(query="billing migration cutover date", k=20)
for claim in envelope.evidence:
    print(claim.claim_text, claim.document_title)
```

## POST /search/claims

Search claims, with the query in the body.

### Request body

[`SearchRequest`](../result-types.md#searchrequest):

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `query` | string | yes | | 1 to 4,096 characters. |
| `k` | integer | no | `10` | 1 to 400. |
| `channel` | `semantic` \| `bm25` | no | `semantic` | |

Unknown fields are rejected.

### Response

As for [`GET /search/claims`](#get-searchclaims).

### Errors

As for `GET /search/claims`, plus `422` for an empty or over-long `query`
or an unknown field.

### Example

```bash
curl -s -X POST "$REMEMBER_API_URL/search/claims" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "billing migration cutover date", "k": 20, "channel": "bm25"}'
```

The `remember` Python client has no method for the `POST` form; its
`search_claims` uses `GET`.

## GET /search/chunks

Search source chunks: passages of the documents themselves.

### Parameters

| Name | In | Type | Required | Default | Constraints |
|---|---|---|---|---|---|
| `query` | query | string | yes | | |
| `k` | query | integer | no | `10` | 1 to 400. |
| `channel` | query | `semantic` \| `bm25` | no | `semantic` | |

### Response

`200` with an `Envelope` of grain `evidence`. `chunks` holds up to `k`
[`ChunkEvidenceResult`](../result-types.md#chunkevidenceresult) entries in
rank order, each with the chunk text, its offsets in the document's converted
Markdown, and the document it belongs to.

### Errors

As for [`GET /search/claims`](#get-searchclaims).

### Example

```bash
curl -s -G "$REMEMBER_API_URL/search/chunks" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  --data-urlencode "query=rollback plan" \
  --data-urlencode "channel=bm25"
```

```python
envelope = memory.search_chunks(query="rollback plan", channel="bm25")
for chunk in envelope.chunks:
    print(chunk.document_title, chunk.char_start, chunk.chunk_text[:80])
```

## POST /search/chunks

Search source chunks, with the query in the body.

### Request body

[`SearchRequest`](../result-types.md#searchrequest), as for
[`POST /search/claims`](#post-searchclaims).

### Response

As for [`GET /search/chunks`](#get-searchchunks).

### Errors

As for [`POST /search/claims`](#post-searchclaims).

### Example

```bash
curl -s -X POST "$REMEMBER_API_URL/search/chunks" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query": "rollback plan", "k": 10}'
```

The `remember` Python client has no method for the `POST` form.

## GET /chunks/{chunk_id}/adjacent

Return a chunk together with its neighbours in document order, so you can
read a search hit in context.

### Parameters

| Name | In | Type | Required | Default | Constraints |
|---|---|---|---|---|---|
| `chunk_id` | path | UUID | yes | | |
| `window` | query | integer | no | `1` | 1 or 2. How many chunks on each side. |

The neighbours come from the same version of the same document as the target
chunk.

### Response

`200` with an `Envelope` of grain `evidence`. `chunks` holds up to
`2 × window + 1` chunks in document order, the target included. An unknown or
no longer visible `chunk_id` gives `negative.kind` `unknown_entity`. If the
target exists but none of the chunks can be confirmed, `negative.kind` is
`known_empty`.

### Errors

| Status | `detail` | Cause |
|---|---|---|
| `422` | validation list | `chunk_id` is not a UUID, or `window` is not 1 or 2. |

### Example

```bash
curl -s "$REMEMBER_API_URL/chunks/$CHUNK_ID/adjacent?window=2" \
  -H "Authorization: Bearer $REMEMBER_API_KEY"
```

```python
envelope = memory.adjacent_chunks(chunk_id=chunk_id, window=2)
```

## POST /chunks/adjacent

Return a chunk and its neighbours, with the chunk id in the body.

### Request body

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `chunk_id` | UUID | yes | | |
| `window` | integer | no | `1` | 1 or 2. |

Unknown fields are rejected.

### Response

As for [`GET /chunks/{chunk_id}/adjacent`](#get-chunkschunk_idadjacent).

### Errors

`422` for a missing or malformed `chunk_id`, a `window` other than 1 or 2, or
an unknown field.

### Example

```bash
curl -s -X POST "$REMEMBER_API_URL/chunks/adjacent" \
  -H "Authorization: Bearer $REMEMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"chunk_id\": \"$CHUNK_ID\", \"window\": 1}"
```

The `remember` Python client's `adjacent_chunks` uses the `GET` form.
