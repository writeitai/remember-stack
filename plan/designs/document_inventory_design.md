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

## 3. The listing is a snapshot, and it has to be

Ordering by "newest activity" makes the sort key **mutable**: it is the newest
version's `ingested_at`, and re-ingesting a document moves it.

Left alone, that breaks paging in a way no reader can detect. A document
sitting below the cursor that gains a new version jumps *above* the cursor —
past a place the reader has already been — and is returned on no page at all.
They reach the end of the list believing they have seen everything.

So a listing is pinned to an instant. The cursor carries a **watermark** (the
moment the first page was requested) alongside the sort position, and every
page describes each document as of that instant. A document whose activity
postdates the watermark belongs to the *next* listing, not this one, and that
is a stated rule rather than an accident of ordering.

## 4. The sort key at scale

This system targets millions of documents, so the ordering has to be servable
by an index rather than by sorting the corpus on every page.

It is not enough to index the versions. "Each document's newest version,
ordered by that version's time" is a group-wise maximum followed by a sort,
and no single index over `document_versions` serves both halves: an index
ordered by time cannot deduplicate by lineage, and one ordered by lineage
cannot produce recency order.

**The lineage therefore carries the ordering key.** `documents` holds a
durable `latest_activity_at` — the `ingested_at` of its newest surviving
version — maintained transactionally wherever a version is written or
tombstoned, and indexed as
`(deployment_id, latest_activity_at DESC, doc_id DESC) WHERE deleted_at IS
NULL`. A page is then an index range scan whose cost is the page size, not the
corpus size.

The tension worth naming, because it is not obvious: a denormalised key holds
the *current* newest activity, while §3 asks for the newest activity **as of
the watermark**. These disagree for exactly the documents re-ingested during a
scroll. The resolution is that the watermark filters on the same indexed
column — a document whose `latest_activity_at` is newer than the watermark is
outside this listing pass entirely, and appears when the reader refreshes.
That is the snapshot rule of §3 applied consistently, rather than a second
mechanism: one column both orders and bounds the listing.

Maintenance is a spine responsibility, not a caller's. A denormalised column
that any write path can forget to update is a column that drifts, and a drifted
sort key reorders somebody's documents silently.

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
