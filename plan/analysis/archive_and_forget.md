# Archive and forget — analysis (D159)

**Status:** analysis for [D159](../../decisions.md#d159-archive-and-forget-replace-delete-forget-erases-one-document-without-stopping-the-deployment)
and the binding [archive and forget design](../designs/archive_and_forget_design.md).
Non-binding; it records why the design looks the way it does. Date: 2026-11-23.

## 1. The problem

Before D159 a caller had one way to remove a document and the operator had another:

- **Delete (D135)** — `DELETE /documents/{doc_id}`, the `delete_document` MCP tool and
  `remember documents delete`. It hides the document from every read, ends its claims'
  currency and closes the facts only it supported. Nothing is erased: the stored original,
  the chunks and the retired claims stay as audit history.
- **Hard-forget (D74)** — real erasure, reachable only through the self-host profile
  (`SelfHostHardForget.request`). No API, CLI or MCP route starts it, so remember.dev
  documents "no erasure of a single document" and answers erasure requests by email.

Three problems with that:

1. **Delete is neither archive nor erasure.** A person who says "that plan is dead" wants
   the memory to stop believing it but keep it as history. A person who says "that file
   should never have been here" (a leaked secret, a wrong upload, a privacy request)
   wants it gone. Delete does half of each: it hides the document from lists and search
   (so it is not usable as history) and keeps every byte (so it is not erasure).
2. **Erasure is not usable.** Hard-forget closes the whole deployment while it runs: every
   read, ingest and ordinary work claim answers 503 `forget_in_progress` until the purge
   and its verification finish. It refuses to start while any authored knowledge page
   still cites the document. Every API start replays every forget ever made, and a
   damaged forget-record store keeps the deployment closed.
3. **The re-ingest guard is permanent.** A forgotten source identity or content hash can
   never be ingested again in that deployment, even when the person who forgot it made a
   mistake and wants it back.

The owner's direction (2026-11-19 to 2026-11-23):

- Only two verbs, **archive** and **forget**.
- Forget must also erase the filesystem view, including mounts that are live while it
  runs.
- The restore-replay machinery overcomplicates things: a restore from backup is an extreme
  outlier, and a replay that can keep the deployment closed is inefficient and
  error-prone.
- One document's erasure must not stop everything else.
- A person may add a forgotten document back, but only by an explicit, per-request choice.
- Test the cascade properly (foreign keys, ordered deletes, nothing left behind, nothing
  extra removed).
- Pre-launch: no deprecation period; the delete surface is simply removed.

## 2. Why D74 looks the way it does

D74 was written to one guarantee, S55: once forgotten, content can never be served again,
**including after a restore from backup**. Data-protection guidance does expect erasure to
be re-applied if a backup taken before it is ever restored. Three constraints then forced
the machinery:

1. **The engine cannot detect a restore.** Backups and restores belong to the operator
   (D60). A restore can roll back PostgreSQL, the object store, the corpus bucket or the
   knowledge repository independently, with no signal to the engine. The only way to "erase
   again after a restore" without a signal is to erase again on every start.
2. **The record cannot live in the database.** A restored database rolls back the record of
   its own erasure. So the record moved to a separate store (`ForgetManifestPort`, the D147
   manifest bucket), and that store became a dependency of starting at all.
3. **Proof was preferred to availability.** D74's rejected-alternatives paragraph says it
   directly: the global barrier is "intentionally less available but much smaller and
   easier to prove". With every other write stopped, the inventory of what to erase cannot
   move under the purge.

Each step follows from the one before; the cost is out of proportion to the risk it
covers:

- the replay grows with every forget ever made and runs on every start;
- a missing or damaged manifest store stops the deployment from starting;
- every forget is a deployment-wide outage of unbounded length (it waits for all running
  work to drain, then for the purge, the knowledge-history rewrite and verification);
- operator-managed mounts must be stopped and remounted by hand;
- remember.dev keeps no backups, so for the hosted product the replay protects against
  nothing.

## 3. Options and choices

### 3.1 Verbs

- **Keep delete beside archive and forget.** Three overlapping verbs: agents and people
  would have to learn a distinction ("hidden but kept" vs "kept as history" vs "erased")
  that delete never served well. Rejected.
- **Chosen: archive and forget only.** "No longer holds" is archive; "should not be here"
  is forget. Delete's engine machinery (the lineage cascade that ends currency and closes
  facts, the episode identity, the in-flight retirement at reconcile) is not thrown away:
  it becomes archive's cascade and forget's first step. A source-observed deletion (a
  connector sees the file removed at its source) becomes an archive, because the file
  existed and said what it said; it is history, not a mistake.

### 3.2 What archive means

- **Archive as a label (`status:archived` tag).** Changes nothing the memory believes.
  Rejected.
- **Archive as a read-time filter only** (claims stay current, reads hide them). Cheap to
  undo, but a fact supported only by archived documents would stay open with full support,
  so "what is true now?" keeps answering from a dead plan. Rejected.
- **Chosen: archive runs the lineage cascade and keeps the document readable.** Claims stop
  being current testimony and facts only they supported close with the recorded reason
  `retracted_source_removal`, exactly as delete did. Unlike delete, the document stays
  listed and readable when a caller asks for archived material, and its stored original
  stays so it can come back.
- **Archive "as of a past or future date".** A past date would rewrite what the memory
  believed between that date and now; a future date needs a scheduler. Neither is needed:
  archive takes effect when it is called, and documents whose versions are in force for
  declared periods already have D140 effective periods for "no longer in force from date
  X". Not adopted.

### 3.3 Un-archive

- **Reopen the old claims and facts.** Needs a reopening outcome for
  `retracted_source_removal` closures that D118's fact windows do not have (the same reason
  D135 rejected undelete). Rejected.
- **Coming back as a full re-read** (D135's re-add, where reuse never drew on a deleted
  version). Simple, but it breaks Rule 4: un-archiving identical bytes, or a one-section
  edit to an archived document, would pay to extract and embed the whole document again.
  Rejected in review round 1.
- **Chosen: coming back is a new observation that reuses paid work.** Un-archiving records a
  new version from the latest stored original. D56 reuse may draw on archived versions, so
  unchanged chunks reuse their embeddings and Selection/Claimify results. D135 forbade this
  because a reused claim kept its ended testimony and silently supported nothing; the fix
  is a `version_restored` currency transition that makes the reused claim current again,
  after which ordinary fact application finds or creates a live fact for it. Closed facts
  stay closed as history, so no reopening outcome is needed.
- **Identical bytes re-sent to an archived document.** Under D135 a re-sent identical
  upload resurrected a deleted lineage. For archive that would let a full resync silently
  undo every archive and pay to reprocess it. Chosen: identical bytes to the archived
  latest version are a no-op that leaves it archived; changed bytes are a new version and
  bring the document back.

### 3.4 How forget stays available

- **Keep the global barrier.** Rejected by the owner and by the analysis above.
- **Chosen: hide first, fence one document, erase in the background.**
  1. The request runs the lineage cascade at once (the document disappears from every read
     in the same transaction) and records the forget.
  2. Only work for that document is held: its running work items are allowed to finish (a
     work item's lease bounds the wait), its queued ones find nothing left when they run,
     and a late writer cannot attach rows because every writer re-checks its version under
     a share lock that forget's delete transaction excludes. Object writers hold a
     per-document advisory lock for their IO, and forget waits the maximum request duration
     after taking it, which covers a request still in flight from a dead session (the same
     reasoning D155 used for its two publication locks). Foreign keys cannot do this job:
     most document-derived tables use logical references PostgreSQL does not enforce. Forget
     therefore keeps the existing cascades for children the document exclusively owns,
     deletes logically referenced rows explicitly, and relies on a schema-coverage test plus
     a fixture that seeds every table. Turning the cascades off was considered and dropped
     in review: it adds schema churn without proving more than the seeded test does.
  3. The erasure runs as one ordinary resumable work item. Everything else keeps serving
     and processing.

  What the global barrier bought was a frozen inventory. The per-document fence buys the
  same thing for the one document that matters: after the hide, no new testimony can
  attach to it (claim publication already refuses a tombstoned lineage), and the erasure
  takes its inventory inside the transaction that deletes it. Shared rows (a fact also
  supported by another document, an entity mentioned elsewhere) are recounted under the
  same row locks ordinary fact work takes, as archive and delete already do.

### 3.5 Where the forget record lives and what happens after a restore

- **Keep the separate manifest store and replay on every start.** Rejected (§2).
- **Keep a separate copy but replay only on demand.** Still a second store to provision and
  keep healthy for a case remember.dev does not have. Rejected.
- **Chosen: one PostgreSQL row per forget, plus an operator command.** The row carries the
  content-free inventory (IDs, hashes, object keys) and drives the background job, the
  re-ingest guard and verification. `remember forget list --json` exports the records, and
  `remember forget reapply` re-runs erasure for every record (and for records from an
  exported file). The documented restore procedure runs `reapply` before the restored
  deployment takes traffic.

  The honest limitation: a database restored from before a forget no longer contains that
  forget's row. The operator re-applies it from an export kept outside the database
  backup, as with any other erasure log a controller keeps. Restore is an operator action
  (D60), so the operator is present to do it; the engine never blocks starting on it.
  remember.dev keeps no database backups, so it has no such case.

### 3.6 Adding a forgotten document back

- **Permanent guard (D74).** Too strict: a person who forgot the wrong file can never get it
  back in that deployment.
- **No guard.** Then forgetting a file that a sync still sees is pointless: the next sync
  brings it back. Real for repository and mailbox syncs.
- **A deployment-wide "allow re-ingest" setting.** Once switched on, every sync can return
  every forgotten document; people switch it on to get past one error and leave it on.
  Rejected.
- **Chosen: refuse by default, allow per request.** Ingest of a forgotten source identity or
  content hash is refused with `document_forgotten` (409), naming when it was forgotten. A
  request that sets `allow_forgotten=true` is ingested as an ordinary ingest would be, and
  the matching record loses the hash and source identity that request matched (deleted when
  nothing is left), so a later `reapply` cannot erase the copy someone deliberately added
  back, while a different forget that shared one hash keeps the rest of its guard. Syncs never set the flag. Erasure law concerns what is stored now; storing
  the data again later is a new, deliberate decision, which the flag makes explicit.

### 3.7 Authored knowledge pages

D46 splits knowledge pages into compiled pages (the engine writes them from facts and
rebuilds them) and authored pages (a person or agent wrote them; the engine never rewrites
their prose). Compiled-page curation sidecars (human pins, exclusions, corrections) count
as authored text. Example: an authored page "WriteIt.ai pricing decision" quotes a meeting
note, "Customer X said they would pay $40/month". Forgetting the meeting note can remove
the citation link, but only a person can remove the quoted sentence.

- **D74: refuse the forget until the owner redacts.** Nothing happens to the document
  meanwhile, and a forget requested by an agent over MCP simply fails. Rejected.
- **Machine-redact the prose.** Violates D46 (the engine never rewrites human words).
  Rejected.
- **Chosen: everything else completes; only those pages wait.** The document is hidden and
  every engine-owned store is erased and verified at once. Pages that still cite it get the
  existing `redaction_required` review flag, and the forget stays `waiting_for_redaction`
  listing their paths. When the owner's edit removes the citation, the forget erases those
  pages' history and completes. In the usual case no authored page cites the document and
  the forget simply completes.

### 3.8 Mounts

D155 publishes the corpus as document folders in object storage; people mount it read-only
with an S3 FUSE tool (self-host) or `remember mount` (remember.dev, through mountable
sessions the cloud controls). D74 stopped controlled sessions and required operator mounts
to be remounted.

- **Chosen:** the forget request removes the document's recorded folders and rewrites the
  affected indexes synchronously, as D155's normal removal already does. The background job
  then scans for every copy carrying the document's suffix. Controlled sessions receive an
  invalidation for the document's paths through the existing corpus purge port and must drop
  them (and their cached bytes) without being stopped. An operator-managed FUSE mount sees
  the deletion when its metadata cache expires; the filesystem-view docs already require
  bounded caches, and that bound is the stated guarantee. A process that already opened and
  read a file through such a tool may keep those bytes, and some tools serve an open handle
  until it closes; the engine cannot revoke them, so the docs say so rather than promise
  more. Controlled sessions are held to the stronger rule: reads on already-open handles of
  erased paths fail. Bytes a client copied out of a mount are outside the engine's
  boundary, as downloads always were.

### 3.9 Physical residue in PostgreSQL

A deleted PostgreSQL row is invisible to every snapshot taken after the delete commits (a
dump already running keeps its earlier snapshot), but its old row version stays in the table file until vacuum marks the space reusable and later writes
overwrite it, and it stays in write-ahead-log segments until they are recycled. Only
`VACUUM FULL` rewrites a table, and it takes an exclusive lock that would stop the
deployment, which is the cost this design removes. The design therefore promises logical
erasure (no later query, export or dump can return the content), states that autovacuum
and WAL recycling are not a secure overwrite, and says so in the public docs rather than
implying more.

## 4. Rule 4 (a new version costs only its edit)

Forget and archive do not change any reuse key. Two declared costs:

- Un-archive, and a new version of an archived document, reuse unchanged embeddings and
  Selection/Claimify results from archived versions (§3.3); only forgotten versions, whose
  rows are erased, are never reuse candidates.
- Forget clears derived embeddings on surviving shared rows whose input text changed, as
  D74 already did; they are recomputed by ordinary work.

## 5. What is removed

The separate manifest store (`ForgetManifestPort`, `forget_manifest_root`,
`forget_manifest_bucket_name`), the startup readiness replay (API and `project --plane p3`),
the global admission barrier and its 503 `forget_in_progress`, the global drain, the
refusal for unredacted authored pages, the permanent ingest guard, the
`SelfHostHardForget`-only entry point, the stop-and-remount rule for mounts, and the whole
public delete surface (`DELETE /documents/{doc_id}`, `MemoryClient.delete_document`,
`remember documents delete`, the `delete_document` MCP tool and their TypeScript
counterparts).
