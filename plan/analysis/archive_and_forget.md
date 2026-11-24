# Archive and forget — analysis (D159)

**Status:** analysis for [D159](../../decisions.md#d159-archive-and-forget-replace-delete-forget-hides-at-once-and-sweeps-until-clean)
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

- **Coming back as a full re-read** (D135's re-add, where reuse never drew on a deleted
  version). Breaks Rule 4: un-archiving, or a one-section edit to an archived document, would
  pay to extract and embed the whole document again.
- **A new testimony episode per return** (review rounds 1–14 explored this). Restored claims
  would start a fresh episode with episode-scoped support links and a fresh fact application,
  so old closed facts stay closed and new ones are created. It works, but every evidence key,
  application key, flush unit and time-scoped read has to carry the episode, which is exactly
  the kind of pervasive, hard-to-explain machinery the owner rejected (2026-11-24).
- **Chosen: unarchive is the exact undo of the archive.** The archive's reconciliation id
  already records which claims it ended and which facts it closed. Unarchive makes those claims
  current again and reopens those facts (a new recorded outcome, `reopened_unarchive`) when they
  have support again, leaving alone anything something else has changed since. No model call,
  no new version, no episode. A changed version of an archived document is ordinary ingest whose
  D56 reuse may draw on archived versions; a reused claim becoming current reopens a fact the
  archive closed for it by the same rule. D135 rejected undelete because D118 had no reopening
  outcome and because it revived testimony a person deliberately removed; for archive, reviving
  is the point, and one recorded outcome is far simpler than the alternatives.
- **Identical bytes re-sent to an archived document.** Under D135 a re-sent identical upload
  resurrected a deleted lineage, so a full resync would silently undo every archive. Chosen:
  whatever is a no-op for a live document is a no-op for an archived one; it stays archived.

### 3.4 How forget stays available, without locks

- **Keep the global barrier.** Rejected by the owner and by §2.
- **Fence one document** (review rounds 1–14). Hide, then keep every writer of that document
  out while erasing: share locks in every writer, two-session advisory fences around object
  writes, a navigation fence, lineage-identity locks across ingest, staged-object tracking,
  re-add generation prefixes, an erasure generation for K sessions, sandboxed K writers.
  Each review round found another writer the fence missed or another way a lock could be held
  too long, and each fix added a lock or a gate that could itself fail. The owner rejected
  this on 2026-11-24: no locks or gates that can break things, a mental model people can
  follow, nothing finicky.
- **Chosen: hide, then sweep until clean.** Correctness rests on three properties that do not
  depend on timing:
  1. The hide is one transaction, and every read already filters tombstoned documents, so
     from that commit nothing can show the document, whatever is still running.
  2. The sweep is keyed by IDs and hashes and treats "already gone" as success, so it can run
     at any time, any number of times, and be interrupted anywhere.
  3. The job sweeps, waits a quiet period longer than any worker can keep writing, and sweeps
     again until a sweep finds nothing. Whatever a slow worker wrote in between was invisible
     (property 1) and is removed (property 2).

  Writers keep the checks they already have (claim publication refuses a tombstoned version);
  those checks reduce late writes but correctness does not depend on them. The only lock forget
  takes is the lineage row lock ingest already takes, which makes the re-ingest guard exact.
  The cost is a bounded delay before `complete` (two sweeps and a quiet period) and a stated
  residual: a worker that keeps writing for longer than the quiet period without its lease or
  database session.

### 3.5 Where the forget record lives and what happens after a restore

- **Keep the separate manifest store and replay on every start.** Rejected (§2).
- **Keep a separate copy but replay only on demand.** Still a second store to provision and
  keep healthy for a case remember.dev does not have. Rejected.
- **Chosen: one PostgreSQL row per forget, plus an operator command.** The row holds only the
  document's ID, version IDs, content hashes and source-identity hash. Because the sweep is
  keyed by exactly those, the row is all `remember ops forget-reapply` needs to erase again
  after a restore, whichever stores were restored; no object-key inventory is kept.
  `remember ops forget-export` writes the records for the operator to keep outside the
  database backup.

  The honest limitation: a database restored from before a forget no longer contains that
  forget's row; the operator re-applies it from the export, as with any erasure log a
  controller keeps. Restore is an operator action (D60), so the operator is present; the
  engine never blocks starting on it. remember.dev keeps no database backups.

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
  request that sets `allow_forgotten=true` is ingested as an ordinary ingest would be, and on
  commit the one record whose lineage identity matches the request is deleted, so a later
  `reapply` with the latest export cannot erase the copy someone deliberately added back,
  while a different forget that shared the bytes keeps its whole guard. Syncs never set the flag. Erasure law concerns what is stored now; storing
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

- **Chosen:** each sweep deletes the document's corpus folders and regenerates the indexes, and
  tells remember.dev mount sessions to drop those paths, best effort. A disconnected client
  catches up on reconnect and its cache expiry bounds what it can serve meanwhile; delivery
  never holds a forget open. An operator-managed FUSE mount sees the deletion when its cache
  expires; the filesystem-view docs require bounded caches and state that a file already open
  in such a tool may keep serving what it read. Requiring acknowledgements was considered and
  dropped: a client that never reconnects would hold the forget open forever.

### 3.9 Physical residue in PostgreSQL

A deleted PostgreSQL row is invisible to every snapshot taken after the delete commits (a
dump already running keeps its earlier snapshot), but its old row version stays in the table file until vacuum marks the space reusable and later writes
overwrite it, and it stays in write-ahead-log segments until they are recycled. Rewriting
a table (`VACUUM FULL`, `CLUSTER`) takes an exclusive lock that would stop work on that
table, which is the cost this design removes; online rewrite tools still leave the old files
to the filesystem, so none of them is a secure overwrite either. The design therefore promises logical
erasure (no later query, export or dump can return the content), states that autovacuum
and WAL recycling are not a secure overwrite, and says so in the public docs rather than
implying more.

## 4. Rule 4 (a new version costs only its edit)

No reuse key changes. Unarchive makes no extraction or embedding call (it undoes the archive).
A changed version of an archived document costs what the same edit to a live document costs:
D56 reuse may draw on archived versions, and only tombstoned (forgotten) versions are never
reuse candidates. Forget clears derived embeddings of surviving shared rows whose text changed;
ordinary work recomputes them.

## 4a. K erasure follows citations

Compiled pages cite the evidence they use (D45/D50). The sweep rebuilds every compiled page
that cites forgotten evidence or a fact whose wording changed, and rewrites those paths'
history. Proving that no writer ever copied uncited text would need sandboxed writers with
recorded inputs and provenance through every cached summary (explored in review); the owner's
simplicity rule puts that outside the guarantee as a stated limit instead.

## 5. What is removed

The separate manifest store (`ForgetManifestPort`, `forget_manifest_root`,
`forget_manifest_bucket_name`), the startup readiness replay (API and `project --plane p3`),
the global admission barrier and its 503 `forget_in_progress`, the global drain, the
refusal for unredacted authored pages, the permanent ingest guard, the
`SelfHostHardForget`-only entry point, the stop-and-remount rule for mounts, and the whole
public delete surface (`DELETE /documents/{doc_id}`, `MemoryClient.delete_document`,
`remember documents delete`, the `delete_document` MCP tool and their TypeScript
counterparts).
