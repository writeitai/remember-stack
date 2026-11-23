# Archive and forget — design (D159)

**Status:** binding current state for removing a document's contribution to memory.
Decision [D159](../../decisions.md#d159-archive-and-forget-replace-delete-forget-erases-one-document-without-stopping-the-deployment);
reasoning and rejected alternatives in the [analysis](../analysis/archive_and_forget.md).
Replaces D135's public delete and D74's hard-forget workflow (manifest store, global
barrier, startup replay, permanent ingest guard). The lineage cascade of
[evidence lifecycle §8](evidence_lifecycle_design.md#8-deletion--deletion-removes-the-documents-contribution-uniformly)
and the D155 per-document corpus erasure of
[E0 §6.7](e0_files_design.md#67-forget-erase-the-document-regenerate-indexes) are the
building blocks this design composes.

## 1. Two verbs

| Verb | Meaning | Reversible | Bytes kept |
|---|---|---|---|
| **Archive** | "This no longer holds." The memory stops believing the document from now on; the document stays readable as history. | Yes: un-archive, or a new version arriving, brings it back. | Yes |
| **Forget** | "This should not be here." The document and everything derived only from it are erased from every engine-controlled store. | Only by adding the content again, deliberately (§3.6). | No |

There is no third verb. A connector that observes a file deleted at its source archives the
document (the file existed and said what it said). Both verbs act on a whole lineage (the
document and all its versions); the version grain stays an internal operation.

Both verbs need full write scope and work for every document type and versioning mode.
Archive, un-archive, forget and ingest of the same lineage serialize on the lineage row lock
E0 already takes, so of two concurrent requests one sees the other's committed result.

## 2. Archive

### 2.1 State

A lineage is in one of three states:

- **live** — `archived_at` and `deleted_at` both null;
- **archived** — `documents.archived_at` set; the lineage and its versions are *not*
  tombstoned, so they stay readable as history;
- **being forgotten / forgotten** — `documents.deleted_at` set (the tombstone every read
  filters) until the row is erased.

A version carries **current testimony** only while its lineage is live and the version itself
is not deleted. Everything that decides currency, support counting and claim publication uses
that rule; everything that decides whether a row may be *shown* uses the visibility rules in
§2.3.

### 2.2 What archive does

`POST /documents/{doc_id}/archive` sets `archived_at` and runs the lineage cascade of
evidence lifecycle §8 in the same transaction, with "live version" read as "version carrying
current testimony":

- every version's testimony ends (currency reason `version_archived`, the renamed
  `version_deleted`);
- support is recounted; a fact also supported by another document loses one supporter;
- facts that only this document supported close with the recorded reason
  `retracted_source_removal`;
- open `support_withdrawn` reviews on claims no current version carries close as
  `auto_resolved`;
- the lineage's T4 anchors (D102) are cleared;
- the K citation path emits the evidence-changed delta, so compiled pages rebuild and
  authored pages that cite the document get an ordinary review flag.

The answer is `DocumentArchive`: `doc_id`, `archived_at`, `claims_retired`,
`relations_closed`, `observations_closed`. D135's episode identity, finish-or-refuse rerun and
in-flight retirement at reconcile are unchanged: a repeat reuses the episode's
reconciliation id, and a repeat that changes nothing answers the same `DocumentArchive` with
zero counts (the document exists, so it is not 404). A connector-observed source deletion is
an archive episode with D135's per-episode finalization and sync-cycle barrier, so a file
deleted and recreated within one sync pass still resolves as a support swap. Entity profiles
whose facts changed are refreshed best-effort after commit, metered as `profile_archive`.

The archive request itself starts no pipeline work and is not spend-gated; work the cascade
queues (D140 window review, corpus folder removal) follows its own ordinary rules.

Archive takes effect when it is called. It never back-dates or schedules: editions in force
for declared periods use D140 effective periods.

### 2.3 What stays visible

| Surface | Archived document |
|---|---|
| `GET /documents/{doc_id}`, versions, stored original, `source_open` | Readable, with `archived_at`. |
| `GET /documents` (inventory, tags, metadata filters) | Hidden by default; `include_archived=true` lists it with `archived_at`. |
| Search and the context operations | Hidden by default; `include_archived=true` includes its passages, each marked archived. Its claims carry no current testimony, so `facts_context` never answers from them. |
| Facts | The facts it closed stay readable as history with their closure reason. |
| SQL query space | `documents` lists it with `archived_at`; the current-testimony views exclude it. |
| Corpus filesystem (P3) | Removed, as D155's normal removal does: the filesystem is the served current corpus. |
| Tags | Kept; it matches tag filters when archived material is requested. |

### 2.4 Coming back

- **Whatever would be a no-op for a live document is a no-op for an archived one** (D55:
  identical bytes on the latest version with the same version metadata, including D140
  version key and effective periods). It answers `created=false` with `archived_at`, and
  the document stays archived. A full resync therefore never undoes an archive.
- **Anything that would record a new version** (changed bytes, or a new D140 edition) records
  it and clears `archived_at` in the same transaction; the document is live again.
- **`POST /documents/{doc_id}/unarchive`** records a new version from the latest stored
  original and clears `archived_at`. It is spend-gated like an ingest. Answer: `doc_id`,
  `version_id` and the ordinary ingest readiness fields.

**Rule 4 holds.** The returning version is processed through the ordinary pipeline, and D56
reuse may draw on archived versions: unchanged chunks reuse their embeddings (D80) and their
Selection and Claimify results, so un-archiving identical bytes makes no extraction or
embedding call, and a one-section edit pays only for that section. A reused claim whose
testimony had ended becomes current again through a new currency transition
(`version_restored`) and is applied to facts like a new claim: E3 finds or creates a live
fact for it. Facts the archive closed stay closed as history; nothing is reopened. Forgotten
versions are never reuse candidates, because their rows are erased.

### 2.5 Surfaces

HTTP `POST /documents/{doc_id}/archive` and `/unarchive`; `MemoryClient.archive_document`
and `unarchive_document`; CLI `remember documents archive|unarchive DOC_ID`; MCP tools
`archive_document` and `unarchive_document` (omitted by `--read-only`;
`destructiveHint: false`). `include_archived` is added to `GET /documents`, search and the
context operations. The TypeScript client exposes the same operations, parameters, answers
and errors (D141 parity).

## 3. Forget

### 3.1 Contract

After a forget completes, no read surface (search, context, facts, graph, SQL, browse,
corpus filesystem, K) can return content unique to the document, and looking up its IDs
answers exactly as for an ID that never existed (S55). Facts and entities independently
supported by another document that is not being forgotten survive, with any wording they took
from the forgotten document replaced (§3.4 step 2); forget removes a source's contribution,
not every independently obtained copy of a fact. PostgreSQL erasure is logical (§3.8).

What remains: the content-free forget record (§3.5), currency-transition and fact-closure
rows stripped of source text, aggregate counts, work-ledger rows (status and IDs only), the
cost ledger, and metering receipts detached from the erased version.

### 3.2 The request: hide at once

`POST /documents/{doc_id}/forget` takes the content-hash and source-identity locks (§3.6) and
the lineage row lock, then in one transaction:

1. runs the lineage cascade of §2.2 if the document is live (an archived document already
   has);
2. sets `documents.deleted_at` on the lineage and its versions (the tombstone every read
   filters, including reads that ask for archived material);
3. marks every `pending` or `failed` work item that targets the document, one of its
   versions, representations, chunks or claims as `skipped` (rows and costs are kept;
   claimable work never includes a tombstoned document);
4. inserts the forget record with status `erasing`, the source-identity hash and every
   version's content hash, so the re-ingest guard (§3.6) is active from this commit;
5. enqueues one `forget` work item for the document.

After commit, synchronously (as D155's normal removal does): remove the document's recorded
corpus folders, regenerate the affected `_index.md` and root `llms.txt`, and send controlled
mount sessions the invalidation for those paths (§3.7). A failure here is reported in the
answer and retried by the background job; the document is already hidden.

Answer: 202 `Forget` (`forget_id`, `doc_id`, `status`, `requested_at`). A repeated request
for a document that already has a forget record answers 200 with that record. An unknown
`doc_id` is 404 `document_not_found`. Forget is not spend-gated; its provider calls (entity
profile refresh, compiled K page rebuilds) are metered as `profile_forget` and as ordinary K
work.

Nothing else in the deployment waits: other documents keep being read, ingested and
processed throughout.

### 3.3 The per-document fence

Forget needs the document's data to stop changing before it inventories and deletes it. It
gets that for the one document, never for the deployment:

- **Queued work is retired** (§3.2 step 3), and claim queries never hand out work whose
  document is tombstoned.
- **Running work finishes first.** The forget job waits until no work item for the document
  other than itself is `running`. Work items for other documents are never waited for.
- **Database writers re-check.** Every handler that writes document-derived rows takes a
  share lock on its version row (`FOR SHARE`, as claim publication and Selection already do)
  in its writing transaction and writes nothing when the version is gone or tombstoned.
  Forget's delete transaction locks those rows `FOR UPDATE`, so a writer either commits
  first (and its rows are in the inventory) or finds nothing to write to.
- **Object writers hold a per-document lock.** Every handler that writes objects for a
  document (E0 artifacts, media, source maps, checkpoints, corpus folders) holds a shared
  advisory lock keyed by the document on its own database session for the whole object IO,
  beside the locks it already holds. Forget's object and corpus steps take that lock
  exclusively, so every issued write has finished. Because a session can die while its
  process still has one request in flight, forget then waits the configured maximum object
  request duration (E0's bounded S3 timeouts with no automatic retries) before it verifies.
- **Derived text built from many documents is fenced by generation.** Entity profiles,
  compiled K pages and planner transcripts are published only if the input generation they
  were prepared from is still current; forget advances the generation of every entity and K
  scope it touches, so a computation prepared before the forget is discarded at publication.
- **Shared rows** are recounted under the entity and fact locks ordinary fact work takes, in
  its order (entities, then facts).

### 3.4 The erasure job

One `forget` work item per document runs these steps. Each is idempotent; the work ledger
provides attempts, visible tracebacks, retries and the dead-letter queue. A failure leaves
the document hidden and the record `erasing`; nothing reopens.

1. **Wait for the fence** (§3.3).
2. **PostgreSQL, one transaction.**
   - *Inventory.* Record the content-free inventory (IDs, hashes, object keys and artifact
     prefixes) on the forget record.
   - *Detach what must survive.* Clear the document's current-version and
     current-representation pointers; unbind incoming D140 cross-references from other
     documents (the citing document keeps its text; the reference resolves as one to a
     never-ingested document); detach metering receipts (`managed_ingest_measurements`) and
     cost rows from the version, keeping their counts and delivery state.
   - *Delete.* Exclusively owned children go by the foreign-key cascades that exist today
     inside the document subtree; rows linked by logical references (columns PostgreSQL
     does not enforce, used on large partitioned tables) are deleted explicitly, children
     before parents: P1 search rows (`chunk_search`), chunk-claim links, evidence links,
     mentions, occurrences, claims, Selection and extraction payloads, review payloads,
     sections, chunks, D134 metadata rows, D156 tag assignments, D118 §7 mutable-fact
     payloads, versions, the document row. The schema-coverage test (§5) proves every table
     is reached.
   - *Facts and entities.* Delete relations and observations whose only support was this
     document (their statements and payloads with them). For surviving shared facts and
     entities, recompute stored wording, canonical names, aliases and validity from the
     surviving provenance (or clear them until recomputed), invalidate entity profile caches
     (D95–D96) and clear derived embeddings whose input text changed; ordinary work
     recomputes them. Delete entities with no surviving evidence. Content-clear
     entity-resolution audit payloads for entities resolved from the document.
   - *Ledger and deployment-wide derived data.* Clear `last_error` on the document's work
     items (tracebacks may quote content); payloads carry IDs only. Clear, deployment-wide,
     planner audit transcripts and legacy eval fixtures, which carry no reliable document
     provenance.
3. **Objects.** Under the content-hash lock, decide raw-object ownership: a content object
   stays when another lineage not being forgotten (live or archived) references it; the guard
   keeps any new lineage from adopting it meanwhile. Delete every other raw object, every key
   under the versions' artifact prefixes (which catches copies written before a crash left
   them unregistered), assets and transcripts. Missing keys count as success. Retained object
   versions and unfinished multipart uploads follow E0 §5 and §6.7 (versioning off, or
   enumerate and purge every version).
4. **Corpus filesystem (D155).** Under the publication lock and the per-document lock, scan
   corpus keys for every folder with the document's suffix (including former moved paths and
   interrupted copies), delete them, regenerate affected indexes from the scrubbed database,
   invalidate the deleted and changed paths in controlled mount sessions (§3.7), and verify.
5. **Knowledge plane (K).** Holding the single-committer lock for the whole step:
   - rebuild compiled pages without the document; where compilation cannot run (K or the
     facts layer switched off), delete compiled pages that cited it;
   - determine **blocking** paths: authored pages and curation sidecars whose *current* file
     still cites an ID in the inventory. They get the `redaction_required` review flag and
     are listed on the record;
   - erase every other registered K path from all reachable Git history and re-add its
     current file, including authored and sidecar paths that no longer cite the document but
     may have in older history (D74's conservative rule: current citation bindings cannot
     prove a page never cited it), and archived planner and writer transcripts;
   - publish once, then verify the rewritten history.
6. **Verify.** Mechanically check every inventory ID, hash, object key and prefix, every
   corpus copy and index entry, controlled-session acknowledgements, and every non-blocking
   K path are absent from their stores. Set `erased_at`. With no blocking paths set status
   `complete`; otherwise `waiting_for_redaction`.

### 3.5 The forget record

```sql
CREATE TYPE forget_status AS ENUM ('erasing', 'waiting_for_redaction', 'complete');

CREATE TABLE forgets (
  forget_id             uuid PRIMARY KEY,
  deployment_id         uuid NOT NULL REFERENCES deployments,
  doc_id                uuid NOT NULL,          -- no FK: the document row is erased
  source_identity_hash  text,                   -- sha256(deployment_id, source_kind, source_ref); NULL for uploads
  content_hashes        text[] NOT NULL,        -- every version's raw content hash
  inventory             jsonb,                  -- content-free IDs, object keys and prefixes; set by step 2
  blocking_k_paths      text[] NOT NULL DEFAULT '{}',
  status                forget_status NOT NULL DEFAULT 'erasing',
  requested_at          timestamptz NOT NULL DEFAULT now(),
  erased_at             timestamptz,            -- every engine-owned store verified clean
  completed_at          timestamptz,            -- blocking pages redacted and erased too
  UNIQUE (deployment_id, doc_id),
  UNIQUE (deployment_id, forget_id),
  CHECK ((status = 'complete') = (completed_at IS NOT NULL)),
  CHECK (status = 'erasing' OR (erased_at IS NOT NULL AND inventory IS NOT NULL))
);
CREATE INDEX ix_forgets_source ON forgets (deployment_id, source_identity_hash)
  WHERE source_identity_hash IS NOT NULL;
CREATE INDEX ix_forgets_content ON forgets USING gin (content_hashes);
```

The record holds no text, names, titles, title-derived paths, URIs or prompts. It is the
only forget state; there is no second store and nothing reads it at start. `GET /forgets` and
`GET /forgets/{forget_id}` return records without the inventory; the CLI is
`remember forget list [--json]` and `remember forget status FORGET_ID`.

### 3.6 Re-ingest guard and adding a document back

**One gate for every ingest path.** `ingest`, `ingest_observed`, `ingest_stored`, connectors
and `unarchive` take transaction-scoped advisory locks keyed by the content hash and, when
there is one, the source identity, before checking the guard, and hold them through
registration. The forget request (§3.2) and step 3 take the same locks for every hash on the
record. So no ingest can register forgotten content between the guard check and its commit,
and no lineage can adopt a raw object forget is deciding about. Raw bytes E0 wrote before
taking the locks are deleted on refusal, as for any refusal.

- **Default:** a match against any record is refused with 409 `document_forgotten`
  (`forget_id`, `forgotten_at`). Connectors and syncs log it and skip the item. A tombstoned
  lineage is never resurrected by an ingest.
- **`allow_forgotten=true` on that one request:** refused with 409 `forget_in_progress` while
  any matching record is `erasing`. Otherwise the content is registered as a new document
  with a new `doc_id`, and every matching record is deleted in the registration transaction
  (only if registration commits), so `reapply` can never erase the copy someone deliberately
  added back. A pending authored-page wait for a deleted record ends; those pages keep their
  review flag. The person chose to have the content back, so its history in those pages is
  no longer an erasure obligation.

The flag exists only per request: on the HTTP ingest routes, the `MemoryClient` ingest
methods, `remember ingest --allow-forgotten` (one file or a folder) and the MCP `ingest`
tool, whose description tells the agent to set it only after the user confirms. There is no
deployment or project setting.

### 3.7 Mounts

- **Controlled sessions** (remember.dev's `remember mount`, through the cloud's mount
  adapter): the corpus purge port gains `invalidate(doc_id, paths)` for deleted and
  regenerated paths. The adapter must remove those entries from every open session's
  listings, drop cached data for them, make later reads on already-open handles of erased
  paths fail, and acknowledge; it never stops a session. Step 6 requires the
  acknowledgement. The cloud repository implements the adapter.
- **Operator-managed S3 FUSE mounts** (self-host): the objects are deleted, and new listings
  and opens stop showing them when the tool's metadata cache expires; the filesystem-view
  docs require bounded stat, listing and data caches, and that bound is the guarantee. No
  remount. The engine does not control those tools: a process that already opened the file
  may keep the bytes it read, and some tools keep serving an open handle until it is closed.
  The docs say so.
- Bytes a client copied out of a mount are outside the engine's boundary.

### 3.8 PostgreSQL physical residue

Deleted rows are invisible to every snapshot taken after the delete commits, so later queries,
exports and dumps cannot return them; a dump already running keeps its earlier snapshot.
Their old row versions remain in table files until autovacuum marks the space reusable, and in
write-ahead-log segments until they are recycled; neither is a secure overwrite. Forget does
not run `VACUUM FULL` (it locks the table exclusively). The public docs state this.

### 3.9 Authored pages: the one wait

The K sync, which runs under the single-committer lock and already records authored-page
citations, checks each `waiting_for_redaction` record whenever a listed path changes. When
that path's current file no longer cites any inventory ID, the sync erases the path from all
reachable Git history, re-adds the current file, verifies the rewritten history, and removes
the path from `blocking_k_paths`; when none remain the record becomes `complete`. This runs
in the sync itself, not as a re-enqueued work item. The engine never edits authored prose;
removing the quoted text, not only the citation, is the owner's responsibility, and the
review flag says so.

### 3.10 After a restore

Restoring backups is the operator's (D60). The restore procedure ends with:

```
remember forget reapply [--from forgets.json]
```

`reapply` takes the records in the database plus the records in the file. A file record is
skipped when the database holds a document matching its hashes that was created after the
record's `requested_at` (someone deliberately added it back). For each remaining record:

- if the restored database holds the document (by `doc_id` or matching hashes), reapply hides
  it and runs the whole erasure (§3.2 steps 1–5 and §3.4) under that record, ignoring the
  request's idempotent short-circuit;
- otherwise it re-runs §3.4 steps 3–6 from the record's inventory, which also re-scrubs a
  separately restored object store, corpus bucket or K repository.

It prints what it erased. The operator runs it before the restored deployment takes traffic;
the engine does not run it at start and never refuses to start because of it.

A database restored from before a forget no longer contains that forget's record, so the
operator keeps `remember forget list --json` exports outside the database backup (the
erasure log any controller keeps) and passes the latest with `--from`. Without that export,
S55 does not survive such a restore; this is the accepted limitation. remember.dev keeps no
database backups.

### 3.11 Surfaces and errors

HTTP `POST /documents/{doc_id}/forget`, `GET /forgets`, `GET /forgets/{forget_id}`;
`MemoryClient.forget_document`, `list_forgets`, `get_forget`; CLI
`remember documents forget DOC_ID` (asks for confirmation; `--yes` skips it),
`remember forget list|status|reapply`; MCP tool `forget_document` with a required
`confirm: true` argument and a description telling the agent to confirm with the user first
(omitted by `--read-only`; `destructiveHint: true`). The TypeScript client exposes the same
operations, parameters, answers and errors (D141 parity); `reapply` is CLI-only.

| Code | When |
|---|---|
| 404 `document_not_found` | unknown or forgotten `doc_id` on any document route |
| 409 `document_forgotten` | ingest of forgotten content without `allow_forgotten` |
| 409 `forget_in_progress` | `allow_forgotten` while a matching record is still `erasing` |

## 4. Removed

- The public delete: `DELETE /documents/{doc_id}`, `MemoryClient.delete_document`,
  `remember documents delete`, the `delete_document` MCP tool, their TypeScript counterparts
  and `DocumentDeletion`. Removed outright; there is no deprecation period.
- `ForgetManifestPort`, its local-root and object-store implementations,
  `forget_manifest_root`, `forget_manifest_bucket_name` and the operator duty to transfer a
  manifest root.
- The startup readiness replay (`HardForgetReadiness` in the API and `project --plane p3`).
- The deployment-wide admission barrier, the shared advisory fence ordinary work takes for it,
  the global drain, and 503 `forget_in_progress` for ordinary traffic.
- The `preparing`/`accepted` statuses and the frozen repeatable-read acceptance cut.
- The refusal of a forget while authored pages cite the document
  (`ForgetRedactionRequiredError`).
- The permanent re-ingest guard.
- The stop-and-remount rule for mounts.

The `forget_manifests` table becomes `forgets` by migration. A record that exists only in a
deployment's old external manifest store is imported with `remember forget reapply --from`
using that store's export.

## 5. Acceptance tests

Run against real PostgreSQL and MinIO with deterministic providers and no hosted model calls.

1. **Whole-database canary.** Plant a unique token in one document (body, title, file name,
   metadata, tags, extracted claims, a fact statement and entity alias it alone contributed,
   a compiled K page, the corpus folder); forget it; `pg_dump` the whole database, read
   every object and object version in every bucket, walk the K repository's reachable
   history and the corpus tree, and find the token nowhere.
2. **Schema coverage.** From the live catalog, enumerate every table with a column that
   references a document, version, representation, section, chunk, claim or mention: real
   foreign keys, logical references found by column name (`doc_id`, `version_id`,
   `representation_id`, `section_id`, `chunk_id`, `claim_id`, `mention_id`, and their
   arrays), and every `jsonb` or free-text payload column. The test fails when one is not
   deleted (by cascade or explicitly), detached or cleared by step 2, and is not on a
   reviewed allow-list of content-free rows. A new table therefore fails CI until someone
   decides how forget treats it.
3. **Every table seeded.** One fixture puts a row in every table the coverage test lists for
   the forgotten document, including metering receipts, incoming D140 references, current
   pointers, cascaded children and pending deliveries, and runs step 2 as one transaction:
   it commits, the cascades remove exactly the owned children, and cost rows, metering
   receipts and their work-ledger parents survive.
4. **Nothing extra removed.** A control document shares facts, an entity and identical bytes
   with the forgotten one; afterwards its facts, entity, raw object and corpus folder are
   intact, its fact counts are exact, and shared facts no longer carry the forgotten wording.
5. **No orphans.** No row references a deleted parent except the allow-listed content-free
   ledger and accounting identifiers; no object or corpus folder is left without its row.
6. **Never existed.** Every public lookup of the forgotten IDs answers exactly as for a random
   ID.
7. **No global stop.** While a forget waits on a running work item, ingest, extraction, search
   and SQL for other documents proceed and complete.
8. **Kill, resume and late writers.** Kill the forget item after each step; the retry
   completes and the canary passes. Work of every stage queued for the document before the
   forget is never claimed. A corpus PUT and an artifact PUT held past their session's death
   land before verification and are found; the run does not complete until they are erased.
   A profile and a compiled page prepared before the forget are discarded at publication.
9. **Concurrent forgets.** Two forgets of documents sharing a fact race with a third document
   adding support to it; the fact ends with exactly the third document's support and wording.
10. **Authored pages.** An authored page citing the document: forget erases everything else
    and reports `waiting_for_redaction` with the path; after the owner's edit the sync
    completes it and the old page text is absent from Git history. An authored page that
    cited it only in older history is rewritten immediately.
11. **Re-ingest.** Refused without the flag (`document_forgotten`) for an upload without a
    source identity, a stored-object ingest (refused object deleted) and a connector resync;
    refused while erasing (`forget_in_progress`); with the flag, accepted as a new `doc_id`,
    the record deleted, and a later `reapply` leaves the copy alone.
12. **Mounts.** A controlled session open during the forget stops listing the document, and a
    handle opened before the forget fails its next read, without the session restarting;
    changed index files show the regenerated content. An S3 FUSE mount with bounded caches
    stops listing it within its cache bound.
13. **Restore.** Restore the pre-forget database dump and run `reapply --from` the export: the
    canary passes. Restore only the corpus bucket and run `reapply`: it passes. A stale export
    does not erase a document deliberately added back after it.
14. **Archive.** Archive closes exclusive facts and keeps shared ones; the document is hidden
    by default and readable with `include_archived`; identical bytes re-sent leave it
    archived; a source delete-and-recreate within one sync pass is a support swap.
15. **Rule 4.** Provider calls are counted: un-archiving identical bytes makes no Selection,
    Claimify or embedding call; a changed version of an archived document with one edited
    section pays only for that section, per the CLAUDE.md Rule 4 test.
16. **Large document.** Forget of a document with thousands of chunks completes while other
    documents' reads stay within their normal latency; the duration is recorded.

## 6. Non-goals

- Forgetting one version, arbitrary text, or "everything about a person" by similarity. A
  person-wide request is answered by finding the documents (a human or agent decision) and
  forgetting each.
- Back-dated or scheduled archive (D140 effective periods cover dated editions).
- Erasing backups, client copies of mounted files, or remote Git hosts the operator
  configured; those are operator or client policy (D60).
- Machine redaction of authored prose (D46).
- A secure physical overwrite of PostgreSQL pages.
