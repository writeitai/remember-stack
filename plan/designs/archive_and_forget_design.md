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
database transaction: from that commit the document, its versions, passages and claims are gone
from every engine read, and its running work loses its lease. Then a background job *sweeps*
everything derived from the document, keyed only by its IDs and hashes, including the copies
that were published elsewhere (corpus files, compiled K pages, the wording of shared facts), and
every sweep is safe to repeat. The job sweeps again after a quiet period and repeats until a
sweep finds nothing left, so anything a slow worker wrote in the meantime is removed by the next
sweep.

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
- open `support_withdrawn` reviews are left as they are (they are moot while the document is
  archived and apply again if it comes back); T4 anchors (D102) are cleared;
- the K citation path emits the usual evidence-changed delta, so compiled pages rebuild and
  authored pages citing the document get an ordinary review flag.

Every transition and closure the archive causes, including late work retired at reconcile
afterwards, is recorded under one **archive reconciliation id** derived from `doc_id` and
`archived_at`, so the undo (§2.3) can find all of them. A late retirement checks, in its own
writing transaction under the lineage row lock, that the lineage is still archived under that
id; after an unarchive it writes nothing. Claim publication treats an archived
lineage as it treats a deleted one: new claims for it are not published as current testimony.

Answer: `DocumentArchive` (`doc_id`, `archived_at`, `claims_retired`, `relations_closed`,
`observations_closed`). A repeat answers the same record with zero counts. Archive starts no
pipeline work and is not spend-gated; entity profiles whose facts changed are refreshed after
commit, metered as `profile_archive`. Work still in flight for the document finishes and is
retired at reconcile exactly as D135 retired late work for a deleted document.

Archive takes effect when it is called; it never back-dates or schedules. Editions in force for
declared periods use D140 effective periods instead.

`archived_by` records who archived: `caller` or `source`. Identical bytes from a connector never
undo a caller's archive; a changed version does (§2.3).

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

- **`POST /documents/{doc_id}/unarchive` undoes the archive.** In one
  transaction under the lineage row lock it clears `archived_at`, restores still-carried
  testimony that archive ended (found through the archive reconciliation id), and
  reopens every fact whose current closure came from an archive (any archive) when the
  returning testimony gives it support again, or when it was held open by a
  `support_withdrawn` review that still applies. Reopening is a new recorded event (`reopened_unarchive`, with its
  time); belief-as-of reads take a fact's belief intervals from these close and reopen events,
  so a read inside the archive interval still sees the fact closed. A fact that something else
  has changed since (closed for another reason, superseded) is left as it is. For versions
  whose processing the archive cut short, it enqueues the next stage that did not run;
  completed work is reused (D56).
  Unarchive makes no model call except the profile refresh. Answer: `doc_id`,
  `claims_restored`, `facts_reopened`.
- **Identical bytes re-sent** to a caller-archived lineage are a no-op (whatever is a no-op for
  a live document, D55); the document stays archived. A full resync never undoes an archive.
- **A changed version** of an archived document (caller or source archive alike) is ordinary
  ingest: the lineage is live again
  and the new version is processed with D56 reuse, which may draw on the archived versions. A
  reused claim that becomes current again reopens a fact the archive closed for it, by the
  same rule as unarchive. Rule 4 therefore holds as for any live edit.
- **A source archive is undone by the source.** When `archived_by = source` and the connector
  sees the file again, identical bytes act as unarchive and changed bytes as a changed version.
  Within one sync cycle a delete-and-recreate never archives at all (D135's cycle barrier).

Under [proposed D162](split_oversized_chunks_design.md#publication), automatic restoration
uses eligible occurrences in the selected completed claims basis. This also applies when
an unarchive restores a living lineage whose head was removed during the archive, or when
a changed version returns with reused claims. A suspended D54 `restore_support` verdict
resumes when its reviewed source remains eligible.

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

At 202 the document and its testimony are hidden from every engine read; copies published
elsewhere (corpus files, compiled K pages, shared fact wording) are erased by the sweep, which
starts at once. The guarantee has stated limits (§3.9): PostgreSQL erasure is logical; K erasure follows
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
3. inserts the forget record with status `erasing`, the document's version IDs, its content
   hashes and its source-identity hash, which also arms the re-ingest guard (§3.6);
4. enqueues the forget job, whose work item targets the forget record.

The hide touches only the lineage, its versions and the new record. The document's work items
are dealt with by the tombstone itself: claiming or renewing work checks it (D145 leases), so
queued work is never claimed and running work cannot renew its lease and stops; the sweep then
marks those work items `skipped`. Nothing waits for them.

Answer: 202 `Forget` (`forget_id`, `doc_id`, `status`, `requested_at`). A repeat answers 200
with the same record; an unknown `doc_id` is 404 `document_not_found`. Forget is not
spend-gated; its provider calls (profile refresh, compiled K page rebuilds) are metered as
ordinary work.

### 3.3 The sweep

One sweep deletes or clears everything derived from the document. It is keyed by the record
(§3.5): the document's ID, its version IDs and content hashes, plus a few content-free IDs the
first sweep collects before it deletes anything, so later sweeps can still find what the
deletes removed the links to. Every step deletes what exists and treats "already gone" as
success, so a sweep can be interrupted and run again at any point.

0. **Collect (every sweep, inside step 1's transaction, before its deletes).** Add to the
   record: the IDs of every fact and entity the document's evidence touches (exclusive
   and shared), so later sweeps and authored-page checks can still recognize citations to
   them; the K pages, authored pages, sidecars, compilation summaries and transcripts that cite
   the document's evidence or those facts (including fact references a fact-sheet page renders
   without recording them as citations); and the opaque locators of originals the document
   owns (an `uploads/<id>` key, D147). All are IDs or opaque keys, never text. Because each
   sweep collects in the same transaction that removes the links, something a late writer
   added after an earlier sweep is still found.
1. **PostgreSQL, one transaction.**
   - Delete every row of the document's subtree: chunks and their P1 search rows, claims,
     chunk-claim and evidence links, mentions, occurrences, Selection and extraction payloads,
     review payloads, sections, representations, D134 metadata rows, D156 tag assignments,
     D118 §7 mutable-fact payloads, versions and the document row. Children are deleted before
     parents; the foreign-key cascades that already exist inside the subtree do part of it.
   - Facts and entities: a relation or observation whose only evidence (current or archived)
     came from this document is deleted with its adjudication rows, and a source-generated
     `other:` predicate that no remaining relation uses is deleted from the registry (E3 records
     generated predicates with the relation that uses them). An entity whose only
     evidence came from it is reduced to a content-free retired row (merge logs reference it).
     Every collected shared fact or entity has its wording, names, aliases, labels, vectors and
     profile recomputed from surviving evidence, or cleared until ordinary work recomputes
     them; adjudication rows of surviving facts lose payload text from the document.
   - Raw originals: for each content hash, if no surviving version of any lineage uses it, the
     `content_objects` row is marked retired in this transaction and its object key is kept on
     the record; an ingest that attaches to a content row locks that existing row, so it either
     attached before (and the hash is still in use) or sees it retired and stores a fresh
     original under its own key. Retired objects are purged in step 2.
   - Clear content in rows that must stay: the document's current pointers and incoming D140
     references from other documents are unbound; metering receipts and cost rows keep their
     counts, their foreign keys to the erased document and versions are dropped before the
     versions are deleted, and a receipt keeps the erased version's UUID only as billing
     metadata so outstanding receipts are still delivered; entity-resolution audit text for entities this document touched is cleared;
     work-ledger error text of the document's work items is cleared.
2. **Objects.** Delete every object (and every retained object version) under the document's
   prefix that no surviving version references (this also catches an original an ingest wrote
   but never registered), and the retired originals kept on the record or collected as opaque
   locators. An original another lineage still uses is never touched.
3. **Corpus filesystem (D155).** Delete the folders carrying the document's suffix and
   regenerate the navigation files that listed it (`_index.md` up the tree, root `llms.txt`)
   from the database, including retained object versions, then check that no navigation file
   still lists it. Then tell controlled mount sessions to drop the document's paths and the
   rewritten navigation files (best effort; §3.7). None of this takes the corpus publication
   lock.
4. **Knowledge plane (K).** Compiled pages are a replaceable snapshot kept outside the authored
   pages' Git history (each publication replaces the previous snapshot; a one-time cleanup
   strips their legacy history, and `forget-reapply` runs that cleanup again on a restored
   repository),
   so forget never rewrites compiled history: it deletes the collected compiled pages, their
   compilation summaries and transcripts, and ordinary K work rebuilds them. Authored pages and
   sidecars whose current file cites the document's evidence or any collected ID are flagged
   and listed on the record (§3.4). Those whose current file is clean but whose history cites a
   collected ID have that history rewritten now. The K lease is held only while publishing, for
   forget and ordinary compilation alike: both prepare their work outside it.

### 3.4 Sweep until clean

The job runs a sweep, waits a **quiet period**, and sweeps again. The quiet period is longer
than any worker can keep writing after the hide (the D145 work lease plus the bounded object
request time; starting value 15 minutes, to be measured). When a sweep after the quiet period
finds nothing to delete or recompute, the record becomes `complete`, or
`waiting_for_redaction` when authored pages or sidecars still cite the document. If a sweep
does find something (a slow worker wrote late, or a stale label job wrote old wording back), the
job simply waits and sweeps again.

Writers help but are not relied on: revoked work is skipped, every handler checks that its
attempt is still current before each write, and shared publishers (corpus repair, label and
profile jobs, K compilation) revalidate the inputs they rendered from at publication and skip a
hidden document. Whatever slips through is found by the next sweep. A sweep "finds something"
only when it removes forgotten residue; an ordinary rebuild of a clean page or label is not
residue.
Under [proposed D162](split_oversized_chunks_design.md#passage-cutover-and-reads),
forget can invalidate a passage projection build and checks every physical
projection and private staging store for residue.

The job never waits on another job and holds no lock while it waits: it reschedules itself with
the work ledger's `not_before`. Its work item has no attempt cap: a failed sweep, or a crashed
worker whose lease expired, is rescheduled with backoff for as long as it takes, so an outage
never leaves a forget stuck. The last error is visible on the record; the document stays hidden
throughout.

**The one wait: authored pages.** The engine never rewrites human or agent-authored prose
(D46). When the K sync sees that a listed page or sidecar no longer cites the document's
evidence or a collected fact, it rewrites that path's history and removes it from the record;
when none remain the record becomes `complete`. The sync runs whether or not compilation is
switched on. Removing quoted text, not only the citation, is the owner's responsibility; the
review flag says so.

### 3.5 The forget record

```sql
CREATE TYPE forget_status AS ENUM ('erasing', 'waiting_for_redaction', 'complete');

CREATE TABLE forgets (
  forget_id             uuid PRIMARY KEY,
  deployment_id         uuid NOT NULL REFERENCES deployments,
  doc_id                uuid NOT NULL,          -- no FK: the document row is erased
  version_ids           uuid[] NOT NULL,        -- every version of the lineage at hide
  content_hashes        text[] NOT NULL,        -- every version's raw content hash
  source_identity_hash  text,                   -- sha256(deployment_id, source_kind, source_ref), uploads included; NULL only for records upgraded from D74
  collected             jsonb,                  -- step 0: shared fact/entity IDs, K page/summary/transcript IDs, opaque original locators (IDs and keys only)
  blocking_k_paths      text[] NOT NULL DEFAULT '{}',  -- pending owner redaction; emptied on completion
  released_hashes       text[] NOT NULL DEFAULT '{}',  -- guard entries released by a flagged re-add (§3.6); never removes sweep keys
  identity_released     boolean NOT NULL DEFAULT false,
  last_error            text,                   -- content-free code of the last failed sweep, for visibility
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
- **`allow_forgotten=true` on that one request**, once the matching record is no longer
  `erasing` (during `erasing`, 409 `forget_not_complete`): the content is ingested as an ordinary ingest
  would, and in the same transaction the record notes, in its separate `released_hashes` and
  `identity_released` fields, only what this request matched: the content hash it carries (and
  the source identity, if the request carries it). The record itself, and the IDs and hashes
  the sweep uses, never change and are never deleted, so a pending authored-page wait and a
  later `forget-reapply` work as before. Adding back one forgotten version leaves the lineage's
  other forgotten versions guarded, and another forget that happened to share the bytes keeps
  its guard.
- **A re-added lineage gets a fresh `doc_id`.** E0 derives a lineage's ID from its identity;
  when it creates a replacement lineage for an identity that has forget records, the
  derivation also includes the number of those records (which never change, so the count is
  stable). Everything keyed by the document's ID (rows, object prefixes, corpus
  suffix, K citations) is therefore disjoint from the forgotten lineage's, so neither a later
  `forget-reapply` of the old record nor a second forget can touch the copy added back. The flag exists only
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

Forget erases the active stores; backups follow the operator's own policy (D60). After
restoring object storage, the corpus bucket or the K repository from a backup, the operator runs
`remember ops forget-reapply`, which re-runs the sweep for every forget record in the database
(hiding a document again first if a restored database holds it). It is an ordinary command, not
a startup step, and nothing waits for it. A database restored from before a forget no longer has
that forget's record; the operator forgets the document again. remember.dev keeps no database
backups.

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
  The hide revokes the document's leases and every pipeline write already checks its version,
  so this needs a worker that keeps running without its lease or its database session.
- **A backup restored from before a forget** brings the document back until the operator
  forgets it again (`forget-reapply` re-applies only records the database still has).
- **A shared raw original** keeps the key it was first stored under, which can include the
  forgotten document's ID and file extension.

### 3.10 Surfaces and errors

HTTP `POST /documents/{doc_id}/forget`, `GET /forgets`, `GET /forgets/{forget_id}`;
`MemoryClient.forget_document`, `list_forgets`, `get_forget`; CLI
`remember documents forget DOC_ID` (asks for confirmation; `--yes` skips it) and
`remember forget list|status`; the operator command `remember ops forget-reapply`; MCP tool `forget_document` with a required `confirm: true` and a
description telling the agent to confirm with the user first (omitted by `--read-only`;
`destructiveHint: true`). TypeScript parity follows D141.

| Code | When |
|---|---|
| 404 `document_not_found` | unknown or forgotten `doc_id` on any document route |
| 409 `document_forgotten` | ingest of forgotten content without `allow_forgotten` |
| 409 `forget_not_complete` | `allow_forgotten` while the matching record is still `erasing` |

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
deleted become source archives, with `archived_at` set to their deletion time; the migration
re-labels the currency events and closures of the lineage's current deletion episode (the
caller's delete or connector finalization, plus the late reconcile runs of that episode; an
earlier, already-returned deletion is left alone) with the one archive reconciliation id, so unarchive can undo them, and clears only the version tombstones
that lineage deletion set; internal version tombstones stay. A record kept only in a D74
external manifest store (not in the table) is outside the upgrade; the operator forgets that
document again if a restore brings it back.

## 5. Acceptance tests

Real PostgreSQL and MinIO, deterministic providers, no hosted model calls.

1. **Canary.** Plant a unique token in one metered document (body, title, file name, metadata,
   claims, an entity name another document's fact also uses, a compiled K page, the corpus
   folder, a source-generated predicate, a fact-sheet page band). Public `/readiness` for its
   work items answers as for an unknown document. Right after the hide, the lineage, versions, passages
   and testimony are hidden from every engine read. After completion, `pg_dump` the
   database, read every object and object version in every bucket, walk the K repository's
   history and the corpus tree: the token is nowhere (copied wording included), and the other
   document's fact survives with clean wording. An old authored revision citing only an
   exclusive relation of the document is rewritten.
2. **Schema coverage.** Every table and column that references a document, version,
   representation, section, chunk, claim or mention (real foreign keys, logical references by
   column name, and payload columns) is either deleted or cleared by the sweep, or on a reviewed
   allow-list of content-free rows. A new table fails CI until someone classifies it. The
   existing foreign-key cascades are exercised through the sweep.
3. **Nothing extra.** A control document sharing facts, an entity and identical bytes keeps its
   facts, entity, raw object and corpus folder, with exact counts.
4. **Never existed.** Every public lookup of the forgotten IDs answers as for a random ID.
5. **No global stop.** While a forget of a document with thousands of chunks sweeps and waits,
   ingest, extraction, search, SQL and K compilation for other documents proceed; the forget's
   duration and other reads' latency are recorded, not asserted against a wall clock.
6. **Late writer.** A worker that writes rows, an artifact and a corpus file for the document
   after the hide is removed by the next sweep before completion. A real writer held past the
   quiet period writes nothing when it returns. A stale label job that writes a shared fact's
   old wording after the first sweep is undone by the next.
7. **Interrupted sweep.** Kill the job inside each step, including between the SQL step and the
   K step and on its last ordinary attempt; the retry completes and the canary passes.
8. **Authored pages.** A page citing the document makes the forget wait; after the owner's edit
   the sync completes it, also with the facts layer switched off.
9. **Re-ingest.** Refused without the flag; refused while erasing; accepted with the flag under a
   fresh `doc_id`, and for a lineage that had versions A and B, adding back A leaves B guarded;
   then a second forget of the re-added lineage and a `forget-reapply` of both records each
   erase only their own copy, including a reapply of the first record while the replacement is
   still live; a connector resync skips the forgotten file; an
   ingest racing the sweep on another lineage with the same bytes keeps its own original.
10. **Mounts.** A remember.dev session open during the forget stops listing the document
    without restarting; an S3 FUSE mount with bounded caches stops listing it within its bound;
    opening the old path fails and the parent index no longer lists it.
11. **Restore.** Restore only the object store (with an `ingest_stored` original) or the K
    repository from before a forget and run `forget-reapply`: the canary passes.
12. **Archive and unarchive.** Archive closes exclusive facts and keeps shared ones; the
    document is hidden by default and readable with `include_archived`; identical bytes leave it
    archived; unarchive restores exactly the archived testimony (including late work retired
    after the archive) and reopens exactly the facts the archive closed, and belief-as-of reads
    before, during and after the archive answer correctly; a changed version after archive costs what the same edit to a live document
    costs (CLAUDE.md Rule 4 test, with its known-gap expected failures).
13. **Upgrade.** Upgrade from the previous release with a complete D74 forget, an unfinished one
    and a D135-deleted document, and unarchive the last one.

## 6. Non-goals

- Forgetting one version, arbitrary text, or "everything about a person" by similarity (find
  the documents, then forget each).
- Back-dated or scheduled archive.
- Erasing backups, client copies or remote Git hosts (operator or client policy, D60).
- Machine redaction of authored prose (D46).
- A secure physical overwrite of PostgreSQL pages.
