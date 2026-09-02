# Document inventory — what a customer's memory actually holds

The counts on a dashboard — how much arrived, how much was a duplicate, how
much failed — can be answered from billing metering, which is deliberately
content-free. **Which** document, under what name, ingested when, and where it
got to cannot. Only the deployment holds the names, so the inventory is a
deployment surface (`GET /documents`), served from the deployment's own
hostname, and the control plane never proxies it (D33).

## 1. Newest observed, not current

A lineage's `current_version_id` points at the snapshot that finished
processing and is being served. It is **not** the newest snapshot, and the two
differ exactly when something is unfinished or broken:

- a document uploaded a minute ago and still converting has **no** current
  version at all;
- a document whose newest upload failed still points at the older, working
  one.

A listing keyed on the current pointer therefore hides precisely the documents
somebody opens the screen to find. Worse, an empty screen straight after an
upload is indistinguishable from the upload having been lost. So a row reports
the highest `version_no` in the lineage.

`serving` is the separate question — whether *any* ready snapshot exists to
answer queries from. Keeping the two apart is what lets the app distinguish
**"the newest upload failed"** from **"there is nothing here to search"**.
They are different problems with different fixes, and one flag collapsing them
misleads in both directions.

## 2. Tombstones

Deleting a version sets `deleted_at` and leaves `status` alone, so a
tombstoned snapshot still reads `ready`. The newest version is therefore the
newest **surviving** one: reporting a tombstone would show somebody a document
they had asked to have removed, labelled as fine. A lineage with no surviving
version is not listed; a lineage the customer asked to forget is not listed,
though its tombstone stays in the spine so an audit can tell "forgotten" from
"never existed".

## 3. The order must be immutable

The tempting order is "most recently touched" — newest activity first. It is
the wrong one, and the reason is worth stating carefully because the failure
is invisible to the reader.

That key **moves**. Re-ingesting a document raises it; tombstoning its newest
version lowers it again. Paging with a keyset cursor against a moving key
fails in both directions:

- a document below the cursor that gains a version jumps **above** it, past a
  place the reader has already been, and is returned on no page at all — they
  reach the end believing they saw everything;
- a document already returned whose newest version is deleted falls **below**
  the cursor and is returned a second time, so the same document appears twice
  in one scroll.

Neither announces itself. Both were reproduced against real PostgreSQL.

Pinning the listing to an instant — carrying a watermark in the cursor and
describing every document as of that moment — looks like the fix and is not.
It stops the first failure and not the second, because a tombstone is not a
new version; it makes `serving` disagree with `latest` unless it is threaded
through every subquery; and it silently excludes documents first ingested
after the listing began.

**So the order is `documents.first_seen_at`**, stamped once when the lineage
is created and never updated, with `doc_id` breaking ties. A newly ingested
document is still at the top, because its lineage is new. A *re-ingested* one
keeps its place — which is the truthful answer for an inventory: it is the
same document, and it was first seen when it was first seen. Immutability
removes both failures rather than compensating for them, and `latest` and
`serving` are read in one statement, so a row describes one coherent instant
without any extra machinery.

## 4. The sort key at scale

This system targets millions of documents, so the ordering has to be servable
by an index rather than by sorting the corpus on every page.

An immutable key on the lineage is what makes that possible. `first_seen_at`
already lives on `documents`, so the covering order is an index and nothing
else:

```
CREATE INDEX ix_documents_inventory_order
  ON documents (deployment_id, first_seen_at DESC, doc_id DESC)
  WHERE deleted_at IS NULL;
```

A page is then an index-only scan whose cost is the page size. The alternative
— ordering on the newest version's ingest time — cannot be indexed at all:
"each document's newest version, ordered by that version's time" is a
group-wise maximum followed by a sort, and no index over `document_versions`
serves both halves. An index ordered by time cannot deduplicate by lineage,
and one ordered by lineage cannot produce recency order. Denormalising that
derived value onto the lineage would restore an index and reintroduce a
mutable key, which is where §3 started.

## 5. What this surface deliberately does not report

**Per-stage skips.** A processing status of `skipped` exists in the schema and
readiness treats it as terminal, but nothing in the pipeline writes one, and
claim extraction is enqueued per *chunk* rather than per document version. A
field reporting skips would therefore be empty for every document — which
reads as "nothing was skipped" and is indistinguishable from "we cannot tell".

Reporting skips needs a decision this system has not made: what it means for a
stage to decline work, at which grain that is recorded, and which component
writes it. Until that exists there is nothing truthful to show, and showing an
always-empty field would be a reassurance the data does not support.

**Cursor authenticity.** The cursor is verified to *parse*, not to have been
issued. It is not a capability: the query is scoped to the caller's own
deployment either way, so a hand-written but well-formed cursor can only start
somebody at an odd place in their own corpus. A malformed one is refused
rather than treated as "start again", because restarting would re-serve a page
already seen and read as duplicated documents.
