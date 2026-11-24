# Archive and forget — design (D159)

**Status:** binding current state for removing a document's contribution to memory.
Decision [D159](../../decisions.md#d159-archive-and-forget-replace-delete-forget-hides-at-once-and-sweeps-until-clean);
reasoning and rejected alternatives in the [analysis](../analysis/archive_and_forget.md).
Replaces D135's public delete and D74's hard-forget workflow.

## 1. Two verbs and one mental model

| Verb | Meaning | Undo | Bytes kept |
|---|---|---|---|
| **Archive** | "This no longer holds." The memory stops believing the document; it stays readable as history. | `unarchive` restores exactly what the archive ended. | Yes |
| **Forget** | "This should not be here." The document and everything derived only from it are erased. | Only by adding the content again, deliberately (§3.6). | No |

There is no third verb. A connector that sees a file deleted at its source archives the
document (the file existed and said what it said). Both verbs act on a whole lineage (the
document and all its versions), need full write scope, and work for every document type and
versioning mode.

**The mental model of forget, in three sentences.** Forget first *hides* the document in one
database transaction; every read already filters hidden documents, so from that commit nothing
can show it. Then a background job *sweeps* everything derived from the document, keyed only by
its IDs and hashes, and every sweep is safe to repeat. The job sweeps again after a quiet period
and repeats until a sweep finds nothing left, so anything a slow worker wrote for the hidden
document in the meantime is invisible to reads and removed by the next sweep.

Forget adds no new lock, barrier, admission gate or startup step. It uses only the lineage row
lock that ingest already takes, and the single-committer rule the knowledge plane already has.
Nothing else in the deployment waits for a forget.

## 2. Archive

### 2.1 What archive does

`POST /documents/{doc_id}/archive` runs the existing lineage cascade (evidence lifecycle §8)
in one transaction under the lineage row lock and sets `documents.archived_at`:

- every version's claims stop being current testimony (currency reason `version_archived`,
  the renamed `version_deleted`);
- support is recounted; a fact also supported by another document just loses one supporter;
- a fact only this document supported is closed as a belief change: `invalidated_at` is set,
  its world-time window is left as the evidence stated it, and the closure is recorded as
  `retracted_source_removal` under the archive's reconciliation id;
- open `support_withdrawn` reviews on claims no current version carries close as
  `auto_resolved`; T4 anchors (D102) are cleared;
- the K citation path emits the usual evidence-changed delta, so compiled pages rebuild and
  authored pages citing the document get an ordinary review flag.

Answer: `DocumentArchive` (`doc_id`, `archived_at`, `claims_retired`, `relations_closed`,
`observations_closed`). A repeat answers the same record with zero counts. Archive starts no
pipeline work and is not spend-gated; entity profiles whose facts changed are refreshed after
commit, metered as `profile_archive`. Work still in flight for the document finishes and is
retired at reconcile exactly as D135 retired late work for a deleted document.

Archive takes effect when it is called; it never back-dates or schedules. Editions in force for
declared periods use D140 effective periods instead.

`archived_by` records who archived: `caller` or `source`. A caller's archive takes precedence:
connector events never undo it.

### 2.2 What stays visible

| Surface | Archived document |
|---|---|
| `GET /documents/{doc_id}`, versions, stored original, `source_open` | Readable, with `archived_at`. |
| `GET /documents`, search, the context operations | Hidden by default; `include_archived=true` includes it, each passage and claim marked archived. Its claims are never current fact support. |
| Facts | The facts it closed stay readable as history, with their closure reason; belief-as-of reads before the archive see them as they were. |
| SQL query space | An inventory view lists it with `archived_at`; current-testimony views exclude it. |
| Corpus filesystem (P3) | Removed, as normal removal does: the filesystem is the current corpus. |
| D140 references to it | `target_unavailable`, until it comes back. |

### 2.3 Undo and coming back

- **`POST /documents/{doc_id}/unarchive` is the exact undo of the archive.** In one
  transaction under the lineage row lock it clears `archived_at`, makes current again every
  claim whose testimony that archive ended (found through the archive's reconciliation id), and
  reopens every fact that archive closed (clears `invalidated_at`, records
  `reopened_unarchive`) when it has support again after the recount. A fact that something
  else has changed since (closed for another reason, superseded) is left as it is. Versions
  whose processing the archive interrupted are re-enqueued; completed work is reused (D56).
  Unarchive makes no model call except the profile refresh. Answer: `doc_id`,
  `claims_restored`, `facts_reopened`.
- **Identical bytes re-sent** to an archived lineage are a no-op (whatever is a no-op for a
  live document, D55); the document stays archived. A full resync never undoes an archive.
- **A changed version** of an archived document is ordinary ingest: the lineage is live again
  and the new version is processed with D56 reuse, which may draw on the archived versions. A
  reused claim that becomes current again reopens a fact the archive closed for it, by the
  same rule as unarchive. Rule 4 therefore holds as for any live edit.
- **A source archive is undone by the source.** When `archived_by = source` and the connector
  sees the file again, identical bytes act as unarchive and changed bytes as a changed version.
  Within one sync cycle a delete-and-recreate never archives at all (D135's cycle barrier).

### 2.4 Surfaces

HTTP `POST /documents/{doc_id}/archive` and `/unarchive`; `MemoryClient.archive_document` and
`unarchive_document`; CLI `remember documents archive|unarchive DOC_ID`; MCP tools
`archive_document` and `unarchive_document` (omitted by `--read-only`;
`destructiveHint: false`); `include_archived` on `GET /documents`, search and the context
operations. The TypeScript client exposes the same operations (D141 parity).

## 3. Forget

### 3.1 What forget guarantees

After a forget completes, no read surface (search, context, facts, graph, SQL, browse, corpus
filesystem, K) returns content unique to the document, and its IDs answer as IDs that never
existed (S55). Facts and entities that another surviving document supports remain, with any
wording that came from the forgotten document recomputed from what survives.

What remains is content-free: the forget record (§3.5), currency and closure rows stripped of
source text, aggregate counts, work-ledger rows (status and IDs), the cost ledger and metering
receipts.

The guarantee has stated limits (§3.9): PostgreSQL erasure is logical; K erasure follows
citations; an operator-managed FUSE mount or a remember.dev client clears what it has cached
only when its cache expires; and a write from a worker stalled for longer than the quiet period
is outside the guarantee.

### 3.2 Hide (the request)

`POST /documents/{doc_id}/forget` takes the lineage row lock (the same lock ingest takes, so a
concurrent ingest of this lineage either commits first or waits and then meets the guard) and
in one transaction:

1. runs archive's cascade (§2.1) if the document is live, or finishes a pending archive
   cascade (D135's finish-or-refuse rerun) if one is under way;
2. sets `documents.deleted_at` on the lineage and its versions, the tombstone every read
   filters, including reads that ask for archived material;
3. marks the document's pending and failed work items `skipped`;
4. inserts the forget record with status `erasing`, the document's version IDs, its content
   hashes and its source-identity hash, which also arms the re-ingest guard (§3.6);
5. enqueues the forget job, whose work item targets the forget record.

Answer: 202 `Forget` (`forget_id`, `doc_id`, `status`, `requested_at`). A repeat answers 200
with the same record; an unknown `doc_id` is 404 `document_not_found`. Forget is not
spend-gated; its provider calls (profile refresh, compiled K page rebuilds) are metered as
ordinary work.

### 3.3 The sweep

One sweep deletes or clears everything derived from the document. It is keyed only by the
record's `doc_id`, version IDs and content hashes, so it needs no list of object keys and
works the same after a restore. Every step deletes what exists and treats "already gone" as
success, so a sweep can be interrupted and run again at any point.

1. **PostgreSQL, one transaction.**
   - Delete every row of the document's subtree: chunks and their P1 search rows, claims,
     chunk-claim and evidence links, mentions, occurrences, Selection and extraction payloads,
     review payloads, sections, representations, D134 metadata rows, D156 tag assignments,
     D118 §7 mutable-fact payloads, versions and the document row. Children are deleted before
     parents; the foreign-key cascades that already exist inside the subtree do part of it.
   - Facts and entities: a relation or observation whose only evidence (current or archived)
     came from this document is deleted. An entity whose only evidence came from it is reduced
     to a content-free retired row (merge logs reference it). A surviving shared fact or entity
     has its wording, names, aliases, labels, vectors and profile recomputed from surviving
     evidence, or cleared until ordinary work recomputes them.
   - Clear content in rows that must stay: the document's current pointers and incoming D140
     references from other documents are unbound; metering receipts and cost rows keep their
     counts but lose their link to the erased version; entity-resolution audit text for
     entities this document touched is cleared; work-ledger error text of the document's work
     items is cleared.
2. **Objects.** Delete every object under the document's prefixes for its versions (originals,
   artifacts, media, source maps, checkpoints). A raw original whose content hash another
   surviving version still uses stays.
3. **Corpus filesystem (D155).** Delete every folder carrying the document's suffix and
   regenerate the affected `_index.md` files and root `llms.txt` from the database, under the
   existing corpus publication lock. Then tell controlled mount sessions to drop those paths
   (best effort; §3.7).
4. **Knowledge plane (K)**, as one ordinary K job (so the existing single-committer rule
   serializes it with other K commits): delete every compiled page that cites the document's
   evidence or a fact whose wording the sweep changed, rewrite the Git history of those paths
   and of every compiled path that ever cited it, expire reflogs and prune unreachable objects,
   and leave the deleted pages to be rebuilt by ordinary K work. Authored pages and curation
   sidecars that still cite the document are flagged and listed on the record (§3.4); their
   history is rewritten once the owner has edited them.

### 3.4 Sweep until clean

The job runs a sweep, waits a **quiet period**, and sweeps again. The quiet period is longer
than any worker can keep writing after the hide (the D145 work lease plus the bounded object
request time; starting value 15 minutes, to be measured). When a sweep after the quiet period
deletes nothing, the record becomes `complete`, or `waiting_for_redaction` when authored pages
or sidecars still cite the document. If a sweep does find something (a slow worker wrote late),
the job simply waits and sweeps again.

The job never waits on another job and holds no lock while it waits: it reschedules itself with
the work ledger's `not_before`. A failed sweep is retried by the ordinary ledger (attempts,
visible traceback, dead-letter queue); the document stays hidden throughout.

**The one wait: authored pages.** The engine never rewrites human or agent-authored prose
(D46). When the K sync sees that a listed page or sidecar no longer cites any of the
document's evidence, it rewrites that path's history and removes it from the record; when none
remain the record becomes `complete`. The sync runs whether or not compilation is switched on.
Removing quoted text, not only the citation, is the owner's responsibility; the review flag
says so.

### 3.5 The forget record

```sql
CREATE TYPE forget_status AS ENUM ('erasing', 'waiting_for_redaction', 'complete');

CREATE TABLE forgets (
  forget_id             uuid PRIMARY KEY,
  deployment_id         uuid NOT NULL REFERENCES deployments,
  doc_id                uuid NOT NULL,          -- no FK: the document row is erased
  version_ids           uuid[] NOT NULL,        -- every version of the lineage at hide
  content_hashes        text[] NOT NULL,        -- every version's raw content hash
  source_identity_hash  text,                   -- sha256(deployment_id, source_kind, source_ref); NULL for uploads
  blocking_k_paths      text[] NOT NULL DEFAULT '{}',  -- pending owner redaction; emptied on completion
  status                forget_status NOT NULL DEFAULT 'erasing',
  requested_at          timestamptz NOT NULL DEFAULT now(),
  completed_at          timestamptz,
  UNIQUE (deployment_id, doc_id),
  CHECK ((status = 'complete') = (completed_at IS NOT NULL))
);
CREATE INDEX ix_forgets_source ON forgets (deployment_id, source_identity_hash)
  WHERE source_identity_hash IS NOT NULL;
CREATE INDEX ix_forgets_content ON forgets USING gin (content_hashes);
```

The record holds IDs and hashes only, never text, names, titles or URIs. It is the only forget
state: there is no second store and nothing reads it at start. `GET /forgets` and
`GET /forgets/{forget_id}` return records; `remember forget list|status` shows them.

### 3.6 Re-ingest guard and adding a document back

Every ingest path (`ingest`, `ingest_observed`, `ingest_stored`, connectors) checks the forget
records for the request's source identity or content hash, before writing any bytes and again
at registration under the lineage row lock.

- **Default:** a match is refused with 409 `document_forgotten` (`forget_id`, `forgotten_at`).
  Connectors and syncs log it and skip the item.
- **`allow_forgotten=true` on that one request**, once the matching record is `complete`
  (before that, 409 `forget_in_progress`): the content is ingested as an ordinary ingest
  would, and the record for this request's lineage identity is deleted in the same
  transaction, so a later `forget-reapply` never erases the copy someone deliberately added
  back. Another forget that happened to share the bytes keeps its record. The flag exists only
  per request (HTTP, `MemoryClient`, `remember ingest --allow-forgotten`, MCP `ingest`, whose
  description tells the agent to set it only after the user confirms). There is no deployment
  setting.

### 3.7 Mounts

- **remember.dev mount sessions:** each sweep sends the session adapter the document's suffix;
  the adapter drops those paths and their cached bytes from open sessions without stopping
  them. A client that is offline catches up when it reconnects, and its cache expiry bounds
  what it can serve meanwhile. Delivery is best effort and never holds a forget open.
- **Operator-managed S3 FUSE mounts (self-host):** the objects are deleted; listings and new
  opens stop showing them when the tool's cache expires. The filesystem-view docs require
  bounded caches and state that a file already open in such a tool may keep serving what it
  read until it is closed.
- Bytes a client copied out of a mount are outside the engine's boundary.

### 3.8 After a restore

Restoring backups is the operator's (D60). The restore procedure ends, before the deployment
takes traffic, with:

```
remember ops forget-reapply [--from forgets.json]
```

It runs inside the engine container, before the API and workers start. For every forget record
in the restored database and in the file (`remember ops forget-export` writes it; the operator
keeps it outside the database backup), it hides the document again if the restored database
holds it, and runs the sweep. Because the sweep is keyed by IDs and hashes, it works whichever
stores were restored. A database restored from before a forget lacks that forget's record, so
the export is how such a forget is re-applied. Deliberately re-adding a forgotten document
deletes its record; a backup taken before that re-add brings back the old guard as well as the
old document, which `forget-reapply` then erases again, consistent with the restored state.
remember.dev keeps no database backups.

### 3.9 Stated limits

- **PostgreSQL erasure is logical.** No snapshot taken after the sweep commits returns the
  content; old row versions persist in table files and write-ahead log until overwritten.
  Forget does not run `VACUUM FULL` (it locks tables).
- **K erasure follows citations.** Compiled pages cite the evidence they use (D45/D50
  validation); forget rebuilds every page that cites forgotten evidence or a fact whose wording
  changed. Text a writer copied without citing it is outside the guarantee.
- **Caches outside the engine** (FUSE tools, remember.dev clients offline past their cache
  expiry, client downloads) are bounded by those clients, not by the engine.
- **A worker stalled for longer than the quiet period** after the hide could still write once.
  Workers lose their lease well within the quiet period, and every pipeline write already
  checks that its version is not tombstoned, so this needs a worker that keeps running without
  its lease or its database session.
- **A shared raw original** keeps the key it was first stored under, which can include the
  forgotten document's ID and file extension.

### 3.10 Surfaces and errors

HTTP `POST /documents/{doc_id}/forget`, `GET /forgets`, `GET /forgets/{forget_id}`;
`MemoryClient.forget_document`, `list_forgets`, `get_forget`; CLI
`remember documents forget DOC_ID` (asks for confirmation; `--yes` skips it) and
`remember forget list|status`; operator commands `remember ops forget-export` and
`remember ops forget-reapply`; MCP tool `forget_document` with a required `confirm: true` and a
description telling the agent to confirm with the user first (omitted by `--read-only`;
`destructiveHint: true`). TypeScript parity follows D141.

| Code | When |
|---|---|
| 404 `document_not_found` | unknown or forgotten `doc_id` on any document route |
| 409 `document_forgotten` | ingest of forgotten content without `allow_forgotten` |
| 409 `forget_in_progress` | `allow_forgotten` while the matching record is not yet `complete` |

## 4. Removed

- The public delete: `DELETE /documents/{doc_id}`, `MemoryClient.delete_document`,
  `remember documents delete`, the `delete_document` MCP tool, their TypeScript counterparts and
  `DocumentDeletion`. Removed outright, with no deprecation period.
- D74's machinery: `ForgetManifestPort` and its stores (`forget_manifest_root`,
  `forget_manifest_bucket_name`), the startup readiness replay, the deployment-wide admission
  barrier, drain and 503 `forget_in_progress`, the `preparing`/`accepted` statuses, the refusal
  while authored pages cite the document, the permanent ingest guard, and the stop-and-remount
  rule for mounts.

**Upgrade.** The migration turns each `forget_manifests` row into a `forgets` record: complete
ones as `complete`, unfinished ones as `erasing` with the hide transaction applied and the job
enqueued. Lineages deleted with D135's delete become caller archives, and lineages a connector
deleted become source archives; internal version tombstones stay. A record kept only in a D74
external manifest store is imported with `remember ops forget-import-d74 <root-or-bucket>`.

## 5. Acceptance tests

Real PostgreSQL and MinIO, deterministic providers, no hosted model calls.

1. **Canary.** Plant a unique token in one document (body, title, file name, metadata, claims,
   an entity name another document's fact also uses, a compiled K page, the corpus folder).
   Forget it. Then `pg_dump` the database, read every object in every bucket, walk the K
   repository's history and the corpus tree: the token is nowhere, and the other document's
   fact survives with clean wording.
2. **Schema coverage.** Every table and column that references a document, version,
   representation, section, chunk, claim or mention (real foreign keys, logical references by
   column name, and payload columns) is either deleted or cleared by the sweep, or on a reviewed
   allow-list of content-free rows. A new table fails CI until someone classifies it. Each
   foreign-key cascade in the subtree is exercised directly and through the sweep.
3. **Nothing extra.** A control document sharing facts, an entity and identical bytes keeps its
   facts, entity, raw object and corpus folder, with exact counts.
4. **Never existed.** Every public lookup of the forgotten IDs answers as for a random ID.
5. **No global stop.** While a forget sweeps and waits, ingest, extraction, search, SQL and K
   compilation for other documents proceed.
6. **Late writer.** A worker that writes rows, an artifact and a corpus file for the document
   after the hide: reads never show them, and the next sweep removes them before completion.
7. **Interrupted sweep.** Kill the job inside each step; the retry completes and the canary
   passes.
8. **Authored pages.** A page citing the document makes the forget wait; after the owner's edit
   the sync completes it, also with the facts layer switched off.
9. **Re-ingest.** Refused without the flag; refused while erasing; accepted with the flag (the
   record is deleted, and a later `forget-reapply` leaves the copy alone); a connector resync
   skips the forgotten file.
10. **Mounts.** A remember.dev session open during the forget stops listing the document
    without restarting; an S3 FUSE mount with bounded caches stops listing it within its bound.
11. **Restore.** Restore the pre-forget database and run `forget-reapply --from` the export;
    restore only the object store and run it; the canary passes both times.
12. **Archive and unarchive.** Archive closes exclusive facts and keeps shared ones; the
    document is hidden by default and readable with `include_archived`; identical bytes leave it
    archived; unarchive restores exactly the archived testimony and reopens exactly the facts the
    archive closed; a changed version after archive costs what the same edit to a live document
    costs (CLAUDE.md Rule 4 test, with its known-gap expected failures).
13. **Upgrade.** Upgrade from the previous release with a complete D74 forget, an unfinished one
    and a D135-deleted document.
14. **Large document.** A forget of a document with thousands of chunks completes while other
    reads stay within their normal latency; the duration is recorded.

## 6. Non-goals

- Forgetting one version, arbitrary text, or "everything about a person" by similarity (find
  the documents, then forget each).
- Back-dated or scheduled archive.
- Erasing backups, client copies or remote Git hosts (operator or client policy, D60).
- Machine redaction of authored prose (D46).
- A secure physical overwrite of PostgreSQL pages.
