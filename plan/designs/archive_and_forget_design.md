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
| **Archive** | "This no longer holds." The memory stops believing the document from now on; the document stays readable as history. | Yes: un-archive, or a changed version arriving, brings it back as new testimony. | Yes |
| **Forget** | "This should not be here." The document and everything derived only from it are erased from every engine-controlled store. | Only by adding the content again, deliberately (§3.6). | No |

There is no third verb. A connector that observes a file deleted at its source archives the
document (the file existed and said what it said). Both verbs act on a whole lineage (the
document and all its versions); the version grain stays an internal operation.

Both verbs need full write scope and work on every document type and versioning mode.

## 2. Archive

### 2.1 What it does

`POST /documents/{doc_id}/archive` runs the lineage cascade of evidence lifecycle §8 in one
transaction, exactly as D135's delete did:

- every version's testimony ends (currency reason `version_archived`, the renamed
  `version_deleted`);
- support is recounted; facts also supported by another live document lose one supporter;
- facts that only this document supported close with the recorded reason
  `retracted_source_removal`;
- open `support_withdrawn` reviews on claims no live version carries are closed as
  `auto_resolved`;
- T4 anchors (D102) for the lineage are cleared;
- the K citation path emits the existing evidence-changed delta, so compiled pages rebuild
  and authored pages that cite it get a review flag (not a redaction flag);
- `documents.archived_at` is set to the transaction time.

The answer is `DocumentArchive`: `doc_id`, `archived_at`, `claims_retired`,
`relations_closed`, `observations_closed`. The episode identity, finish-or-refuse rerun and
in-flight-work retirement at reconcile are D135's, unchanged: a repeat of the same archive
reuses its reconciliation id; if the rerun changes nothing the answer is the same
`DocumentArchive` with zero counts (the document exists, so it is not 404). Entity profiles
whose facts changed are refreshed best-effort after commit and metered as `profile_archive`.
Archive starts no pipeline work and is not spend-gated.

Archive takes effect when it is called. It never back-dates or schedules: for editions in
force for declared periods use D140 effective periods.

### 2.2 What stays visible

| Surface | Archived document |
|---|---|
| `GET /documents/{doc_id}`, versions, stored original, `source_open` | Readable, with `archived_at`. |
| `GET /documents` (inventory, tags, metadata filters) | Hidden by default; `include_archived=true` lists it with `archived_at`. |
| Search and the context operations | Hidden by default; `include_archived=true` includes its passages, each marked archived. Its claims are not current, so `facts_context` never answers from it. |
| Facts | The facts it closed stay readable as history with their closure reason. |
| SQL query space | `documents` lists it with `archived_at`; the current-testimony views exclude it as they excluded deleted documents. |
| Corpus filesystem (P3) | Removed, as normal removal did: the filesystem is the served current corpus (D155). |
| Tags | Kept; the archived document matches tag filters when archived material is requested. |

### 2.3 Coming back

- **Identical bytes re-sent** to an archived lineage (same source identity, or an upload
  whose content hash equals the archived latest version's): a no-op answering
  `created=false` with `archived_at`; the document stays archived. A full resync therefore
  never undoes an archive or pays to reprocess it.
- **A changed version** (new bytes under the same source identity): a new version, processed
  in full; `archived_at` is cleared in the transaction that records it.
- **`POST /documents/{doc_id}/unarchive`**: records a new version from the latest stored
  original and processes it in full; `archived_at` is cleared. It is spend-gated like an
  ingest. Answer: `doc_id`, `version_id`, and the ordinary ingest readiness fields.

Returning testimony is new testimony: closed facts are not reopened, and D56 reuse never
draws on a version whose testimony was ended (D135's rule). This full read is a declared
Rule 4 cost of un-archiving.

### 2.4 Surfaces

HTTP `POST /documents/{doc_id}/archive` and `/unarchive`; `MemoryClient.archive_document`
and `unarchive_document`; CLI `remember documents archive|unarchive DOC_ID`; MCP tools
`archive_document` and `unarchive_document` (omitted by `--read-only`;
`destructiveHint: false`). `include_archived` is added to `GET /documents`, search and the
context operations, in the SDK, CLI, MCP tools and the TypeScript client.

## 3. Forget

### 3.1 Contract

After a forget completes, no read surface (search, context, facts, graph, SQL, browse,
corpus filesystem, K) can return content unique to the document, and looking up its IDs
answers exactly as for an ID that never existed (S55). Facts and entities independently
supported by another live document survive; forget removes a source's contribution, not
every independently obtained copy of a fact. Erasure is logical in PostgreSQL (§3.8).

What remains: the content-free forget record (§3.5), currency-transition and fact-closure
rows stripped of source text, aggregate counts, work-ledger rows with their payloads and
errors cleared (the cost ledger references them), and cost-ledger rows.

### 3.2 The request: hide at once

`POST /documents/{doc_id}/forget` takes the document's lineage row lock and, in one
transaction:

1. runs the lineage cascade of §2.1 if the document is live (an archived document has
   already run it);
2. sets `documents.deleted_at` (the tombstone every read filters, including reads that ask
   for archived material);
3. inserts the forget record with status `erasing`, the source-identity hash and every
   version's content hash, so the re-ingest guard (§3.6) is active from this commit;
4. enqueues one `forget` work item for the document.

After commit, synchronously (as D155's normal removal does): remove the document's
recorded corpus folders, regenerate the affected `_index.md` and root `llms.txt`, and send
controlled mount sessions the invalidation for those paths (§3.7). A failure here is
reported in the answer and retried by the background job; the document is already hidden.

Answer: 202 `Forget` (`forget_id`, `doc_id`, `status`, `requested_at`). A repeated request
for a document that already has a forget record answers 200 with that record. An unknown
`doc_id` is 404 `document_not_found`. Forget is not spend-gated; its provider calls (entity
profile refresh, compiled K page rebuilds) are metered as `profile_forget` and as ordinary K
work.

Nothing else in the deployment waits: other documents keep being read, ingested and
processed throughout.

### 3.3 The per-document fence

Forget needs the document's rows to stop changing before it inventories and deletes them.
It gets that per document, not per deployment:

- **No new testimony.** Claim publication refuses a tombstoned lineage (D135), and the
  tombstone is committed in §3.2.
- **Running work finishes first.** The forget job waits until no `processing_state` row
  that targets the document, one of its versions, chunks or claims is `running`. A worker's
  lease (D145) bounds the wait. Work items for other documents are never waited for.
- **Queued work becomes a no-op.** Queued items for the document stay in the ledger with
  their payloads cleared; when claimed they find their target gone and finish without
  writing. Every handler treats a missing target that way.
- **Late writes cannot attach.** Many document-derived tables reference their parent by a
  logical reference (a column PostgreSQL does not enforce, used on large partitioned
  tables), so the fence cannot rely on foreign keys. Instead every handler that writes
  document-derived rows takes a share lock on its version row (`FOR SHARE`, as claim
  publication and Selection already do) in its writing transaction and writes nothing when
  the version is gone or tombstoned. Forget's delete transaction locks those version rows
  `FOR UPDATE`, so a writer either commits first (and its rows are in forget's inventory)
  or runs after (and finds nothing to write to). The concurrency test (§5) claims queued
  work of every stage after a forget.
- **Late object writes are caught.** After the erasure, the job waits one worker-lease
  period and re-verifies object and corpus absence, so a write issued by a stalled worker
  before the delete and landing after it is found and removed.
- **Shared rows** (a fact supported elsewhere, an entity mentioned elsewhere) are recounted
  under the same row locks ordinary fact work takes, as archive does.

### 3.4 The erasure job

One `forget` work item per document runs these steps. Each is idempotent; the work ledger
provides attempts, visible tracebacks, retries and the dead-letter queue. A failure leaves
the document hidden and the record `erasing`; nothing reopens.

1. **Wait for the fence** (§3.3).
2. **PostgreSQL, one transaction.** Take the inventory (content-free IDs, hashes and object
   keys) and store it on the forget record, then delete in explicit dependency order,
   children before parents: P1 search rows (`chunk_search`), chunk-claim links, evidence
   links, mentions, occurrences, claims, Selection and extraction payloads, review
   payloads, sections, chunks, D134 metadata rows (`document_metadata`, `document_people`,
   `document_names`), D156 tag assignments, versions, the document row. Forget never relies
   on a cascade: the real foreign keys inside the document subtree, several of which are
   `ON DELETE CASCADE` today, become `NO ACTION`, so a child table the order missed makes
   the delete fail loudly instead of disappearing silently (or taking rows it should not).
   Logical references are covered by the schema-coverage test (§5). In the same
   transaction: recount shared facts; close facts with no
   remaining support; remove observations and entities with no surviving evidence (their
   names, aliases and profile text go with them); invalidate the profile cache of shared
   surviving entities (D95–D96) and clear derived embeddings on surviving rows whose input
   text changed; content-clear entity-resolution audit payloads for entities resolved from
   the document; clear the work-ledger payloads and errors of the document's work items;
   clear, deployment-wide, the planner audit transcripts and legacy eval fixtures that carry
   no reliable document provenance. D118 §7's mutable-fact payloads are part of this
   inventory.
3. **Objects.** Delete every raw, artifact, asset and transcript object in the inventory. A
   content object shared by identical bytes of another live lineage stays; missing keys
   count as success. Unfinished multipart uploads follow E0 §5's provider rule.
4. **Corpus filesystem (D155).** Under the publication lock, scan corpus keys for every
   folder with the document's suffix (including former moved paths and interrupted
   copies), delete them, regenerate affected indexes from the scrubbed database, invalidate
   the paths in controlled mount sessions (§3.7), and verify absence.
5. **Knowledge plane (K).** Under the single-committer lock, rebuild compiled pages without
   the document, then erase the affected compiled pages' paths from all reachable Git
   history and re-add their current files (D74's conservative rule stays: every registered
   compiled artifact's path is nominated, because current citation bindings cannot prove a
   page never cited the document in older history). Authored pages and curation sidecars
   whose current file still cites any ID in the inventory are **blocking**: they get the
   `redaction_required` review flag and are listed on the record; their history is not
   rewritten yet, because rewriting would re-add the unredacted current file.
6. **Verify.** Mechanically check every inventory ID, hash and object key, every corpus copy
   and index entry, and every non-blocking K path is absent from its store; wait one lease
   period and check objects and corpus again (§3.3). Set `erased_at`. If there are no
   blocking pages, set status `complete`; otherwise `waiting_for_redaction`.

### 3.5 The forget record

```sql
CREATE TYPE forget_status AS ENUM ('erasing', 'waiting_for_redaction', 'complete');

CREATE TABLE forgets (
  forget_id             uuid PRIMARY KEY,
  deployment_id         uuid NOT NULL REFERENCES deployments,
  doc_id                uuid NOT NULL,          -- no FK: the document row is erased
  source_identity_hash  text,                   -- sha256(deployment_id, source_kind, source_ref); NULL for uploads
  content_hashes        text[] NOT NULL,        -- every version's raw content hash
  inventory             jsonb,                  -- content-free IDs and object keys; set by step 2
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

The record holds no text, names, titles, paths derived from titles, URIs or prompts. It is
the only forget state; there is no second store. `GET /forgets` and
`GET /forgets/{forget_id}` return records (without the inventory); the CLI is
`remember forget list [--json]` and `remember forget status FORGET_ID`.

### 3.6 Re-ingest guard and adding a document back

Every ingest path (`ingest`, `ingest_observed`, `ingest_stored`, connectors) checks, under
the lineage lock it already takes, whether the source-identity hash or the content hash
matches a forget record:

- **Default:** refused with 409 `document_forgotten` (`forget_id`, `forgotten_at`). A
  stored-object ingest deletes the refused upload as for any refusal. Connectors and syncs
  log the refusal and skip the item.
- **`allow_forgotten=true` on that one request:** if the record is `erasing`, refused with
  409 `forget_in_progress` (retry when erased). Otherwise the content is ingested as a new
  document and the matching forget record is deleted in the same transaction, so `reapply`
  can never erase the copy someone deliberately added back, and any pending authored-page
  wait for it ends (the pages keep their review flag).

The flag exists only per request: on the HTTP ingest routes, `MemoryClient` ingest methods,
`remember ingest --allow-forgotten` (one file or a folder) and the MCP `ingest` tool, whose
description tells the agent to set it only after the user confirms. There is no
deployment or project setting.

### 3.7 Mounts

- **Controlled sessions** (remember.dev's `remember mount`): the corpus purge port gains
  `invalidate(doc_id, paths)`. The host adapter must make those paths disappear from every
  open session and drop any bytes it cached for them, without stopping the session, and
  acknowledge. The cloud repository implements it; verification (step 6) requires the
  acknowledgement.
- **Operator-managed S3 FUSE mounts** (self-host): the objects are deleted; the mount stops
  showing them when its metadata cache expires. The filesystem-view docs require bounded
  stat, listing and data caches, and that bound is the guarantee. No remount.
- Bytes a client copied out of a mount are outside the engine's boundary.

### 3.8 PostgreSQL physical residue

Deleted rows are gone from every query, export and dump when the transaction commits. Their
old row versions remain in table files until autovacuum marks the space reusable and later
writes overwrite it, and in write-ahead-log segments until they are recycled. Forget does
not run `VACUUM FULL` (it locks the table exclusively). The public docs state this.

### 3.9 Authored pages: the one wait

When an authored page or curation sidecar is edited so that its current file no longer cites
any ID in the record's inventory, the K sync that records the change re-enqueues the forget
item for each `waiting_for_redaction` record listing that path. The item erases the now-clean
paths from all reachable Git history, re-adds their current files, removes them from
`blocking_k_paths` and, when none remain, sets `complete`. The engine never edits authored
prose. Removing the quoted text, not only the citation, is the owner's responsibility; the
review flag says so.

### 3.10 After a restore

Restoring backups is the operator's (D60). The restore procedure ends with:

```
remember forget reapply [--from forgets.json]
```

For each record in the database, plus each record in the file that the database lacks
(inserted first): if the restored database holds the document again, run the whole forget
(§3.2–3.4) for it; otherwise re-run steps 3–6 from the record's inventory. It prints what it
erased. The operator runs it before the restored deployment takes traffic; the engine does
not run it at start and never refuses to start because of it.

A database restored from before a forget no longer contains that forget's record, so the
operator keeps `remember forget list --json` exports outside the database backup (the
erasure log any controller keeps) and passes the export with `--from`. remember.dev keeps no
database backups.

### 3.11 Surfaces and errors

HTTP `POST /documents/{doc_id}/forget`, `GET /forgets`, `GET /forgets/{forget_id}`;
`MemoryClient.forget_document`, `list_forgets`, `get_forget`; CLI
`remember documents forget DOC_ID` (asks for confirmation; `--yes` skips it),
`remember forget list|status|reapply`; MCP tool `forget_document` with a required
`confirm: true` argument and a description telling the agent to confirm with the user
first (omitted by `--read-only`; `destructiveHint: true`). TypeScript client parity follows.

| Code | When |
|---|---|
| 404 `document_not_found` | unknown or forgotten `doc_id` on any document route |
| 409 `document_forgotten` | ingest of forgotten content without `allow_forgotten` |
| 409 `forget_in_progress` | `allow_forgotten` while that record is still `erasing` |

## 4. Removed

- The public delete: `DELETE /documents/{doc_id}`, `MemoryClient.delete_document`,
  `remember documents delete`, the `delete_document` MCP tool, their TypeScript
  counterparts and `DocumentDeletion`. Removed outright; there is no deprecation period.
- `ForgetManifestPort`, its local-root and object-store implementations,
  `forget_manifest_root`, `forget_manifest_bucket_name` and the operator duty to transfer a
  manifest root.
- The startup readiness replay (`HardForgetReadiness` in the API and `project --plane p3`).
- The deployment-wide admission barrier, the shared advisory fence ordinary work takes for
  it, the global drain, and 503 `forget_in_progress`.
- The `preparing`/`accepted` statuses and the frozen repeatable-read acceptance cut.
- The refusal of a forget while authored pages cite the document
  (`ForgetRedactionRequiredError`).
- The permanent re-ingest guard.
- The stop-and-remount rule for mounts.

The `forget_manifests` table becomes `forgets` by migration. An existing record in the
external manifest store that the table lacks is inserted by `remember forget reapply --from`
with the old store's export.

## 5. Acceptance tests

Run against real PostgreSQL and MinIO with deterministic providers, no hosted model calls.

1. **Whole-database canary.** Plant a unique token in one document (body, title, file name,
   metadata, tags, extracted claims, entity names, a compiled K page, the corpus folder);
   forget it; then `pg_dump` the whole database, list and read every object in every bucket,
   walk the K repository's reachable history and the corpus tree, and find the token
   nowhere.
2. **Schema coverage.** From the live catalog, enumerate every table with a column that
   references a document, version, representation, section, chunk, claim or mention: real
   foreign keys, logical references found by column name (`doc_id`, `version_id`,
   `chunk_id`, `claim_id`, `mention_id`, and their arrays), and every `jsonb` or free-text
   payload column. The test fails when one is not deleted by step 2, cleared by it, or on
   a reviewed allow-list of content-free rows, and when a foreign key inside the document
   subtree cascades. A new table therefore fails CI until someone decides how forget
   treats it.
3. **Nothing extra removed.** A control document shares facts, an entity and identical
   bytes with the forgotten one; after forget its facts, entity, raw object and corpus
   folder are intact and its fact counts are exact.
4. **No orphans.** No row references a deleted parent; no object or corpus folder is left
   without its row.
5. **Never existed.** Every public lookup of the forgotten IDs answers exactly as for a
   random ID.
6. **No global stop.** While a forget waits on a running work item, ingest, extraction,
   search and SQL for other documents proceed and complete.
7. **Kill, resume and late writers.** Kill the forget item after each step; the retry
   completes and the canary passes. Queued work of every stage for the document, claimed
   after the forget, writes nothing. A stalled worker writes an object after step 3; the
   step 6 re-check finds and removes it.
8. **Authored pages.** An authored page citing the document: forget erases everything else,
   reports `waiting_for_redaction` with the path; after the owner's edit it completes and
   the old page text is absent from Git history.
9. **Re-ingest.** Refused without the flag (`document_forgotten`), refused while erasing
   (`forget_in_progress`), accepted with the flag (record deleted; `reapply` leaves the new
   copy alone); a connector resync skips the forgotten file.
10. **Mounts.** A controlled session open during the forget stops listing and serving the
    document's paths without being restarted; an S3 FUSE mount with bounded caches stops
    showing it within its cache bound.
11. **Restore.** Restore the pre-forget database dump, run `reapply --from` the export, and
    the canary passes; restore only the corpus bucket and run `reapply`, and it passes.
12. **Archive.** Archive closes exclusive facts and keeps shared ones; the document is
    hidden by default and readable with `include_archived`; identical bytes re-sent leave it
    archived; a changed version and `unarchive` bring it back as new testimony.
13. **Large document.** Forget of a large document (thousands of chunks) completes, and
    other documents' reads stay within their normal latency while it runs; the duration is
    recorded.

## 6. Non-goals

- Forgetting one version, or arbitrary text, or "everything about a person" by similarity.
  A person-wide request is answered by finding the documents (a human or agent decision) and
  forgetting each.
- Back-dated or scheduled archive (use D140 effective periods).
- Erasing backups, client downloads, or remote Git hosts the operator configured; those are
  operator or client policy (D60).
- Machine redaction of authored prose (D46).
