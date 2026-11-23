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
- **archived** — `documents.archived_at` set, with `archived_by` = `caller` or `source`
  (a connector observed the source deletion). A caller's archive takes precedence: archiving
  a source-archived document sets `archived_by = caller` under the lineage lock (keeping the
  episode), and connector events never downgrade `caller` to `source`. Every version that existed at that moment gets
  `document_versions.archived_at` too, a durable cutoff: those versions never carry current
  testimony again, even after the lineage comes back. Nothing is tombstoned, so the lineage
  and its versions stay readable as history;
- **being forgotten / forgotten** — `documents.deleted_at` set (the tombstone every read
  filters) until the row is erased.

Every archive and un-archive is also appended, with its belief time, to a small ledger
(`version_archive_events`: version, `archived_at`, `restored_at`), so reads of belief as of a
past instant (D140 pinned selection and support) see each version as archived exactly during
the intervals it was. A version carries **current testimony** only while its lineage is live
and the version itself is neither archived nor deleted. Coming back (§2.4) clears the lineage's `archived_at` and
either restores the latest version (its cutoff is lifted) or adds a new version; every earlier
version keeps its cutoff. D135's late retirement at reconcile keys on the version's own state,
so work finishing late for an archived version still retires. Everything that decides
currency, support counting and claim publication uses that rule; everything that decides whether a row may be *shown* uses the visibility rules in
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

Layer gaps (D143: work a disabled layer has not yet done for a version) are parked, not
deleted, while the document is archived, and re-armed on un-archive, so enabling a layer later
still reaches a version that finished while it was off.

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
| D140 cross-references to it | Return `target_unavailable`, as for an unavailable target; they resolve again without re-binding when it comes back. |

### 2.4 Coming back

- **Whatever would be a no-op for a live document is a no-op for an archived one** (D55:
  identical bytes on the latest version with the same version metadata, including D140
  version key; a D140 correction of effective periods alone is metadata and also leaves it
  archived). It answers `created=false` with `archived_at`, and the document stays archived.
  A full resync therefore never undoes a caller's archive.
- **A source reappearing undoes a source archive.** When `archived_by = source`, a connector
  observing the file again un-archives it: identical bytes restore the latest version as
  `unarchive` does, changed bytes record a new version. The connector's unchanged-revision
  shortcut does not apply to a source-archived lineage, so a reappearance with an unchanged
  revision still reaches this rule. Within one sync cycle a delete-and-recreate never
  archives at all: D135's cycle barrier resolves it as a support swap.
- **Anything that would record a new version** (changed bytes, or a new D140 edition) records
  it and clears `archived_at` in the same transaction; the document is live again.
- **`POST /documents/{doc_id}/unarchive`** restores the latest version: it clears the
  lineage's `archived_at` and that version's cutoff in one transaction, so no new version and
  no D140 edition key is involved. It is a state change on this lineage, not an ingest: it
  checks only this lineage's own state under its lock, so a forget of a different lineage that
  shared these bytes does not block it. If the archive landed while the version was still
  being processed (its claim publication was then refused), unarchive re-enqueues the
  version's pipeline from the first stage that did not complete; D56 reuse means completed
  paid work is not repeated. The version's D140 declared periods and
  edition key take part in interval derivation again, and pinned references to it resolve
  again. Earlier versions stay cut off. The fact application it queues for the restored
  claims is ordinary metered work. Answer: `doc_id`, `version_id`, and how many claims were
  restored.

**Restored testimony is a new testimony episode.** When a claim's testimony ends and later
becomes current again (un-archive, or D56 reuse of an archived version's claim by a new
version), it starts a new **testimony episode**: a currency transition `version_restored`
with a new episode number on the claim. Support links between claims and facts carry the
episode that created them, and current support counts only links from a claim's current
episode; links from ended episodes stay as history. The restored claim is applied to facts
under a fresh application identity (claim plus episode), so the work ledger and fact
adjudication (including D118's application key) treat it as new rather than returning the old
answer. Application reuses the stored normalization (no paid re-normalization) and keeps the
source dates and names that D140 text origin keys on. E3 finds or creates a live fact for it;
facts the archive closed stay closed as history, and a restored claim never supports both
its old and its new fact.

**Rule 4 holds.** Un-archiving a fully processed version makes no Selection, Claimify or
embedding call (it re-reads nothing); an interrupted one resumes only its missing work. A changed version of an archived document goes through the ordinary pipeline, and D56
reuse may draw on archived versions: unchanged chunks reuse their embeddings (D80) and
Selection and Claimify results, so a one-section edit pays only for that section; reused
claims start a new episode as above. A tombstoned version (one being forgotten, or deleted at
the internal version grain) is never a reuse candidate; forgotten versions' rows are erased
anyway.

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
cost ledger, and metering receipts detached from the erased version. Cost-ledger call keys
are opaque (a hash of their inputs) on every write path; a migration replaces existing keys
that embed source text (reclustering put the normalized alias into `call_key`), keeping
every receipt and total.

### 3.2 The request: hide at once

`POST /documents/{doc_id}/forget` takes its locks in the common order of §3.6 (the lineage
identity, then the content hashes of the versions it then enumerates, sorted; holding the
lineage identity means no version can be added meanwhile), then in one transaction:

1. runs the lineage cascade of §2.2, or finishes it idempotently through D135's
   finish-or-refuse rerun when an archive (a source archive awaiting finalization, say) has
   already started it;
2. sets `documents.deleted_at` on the lineage and its versions (the tombstone every read
   filters, including reads that ask for archived material);
3. marks every `pending` or `failed` work item that targets the document, one of its
   versions, representations, chunks or claims as `skipped` (rows and costs are kept;
   claimable work never includes a tombstoned document);
4. inserts the forget record with status `erasing`, the lineage's current re-add generation
   (so purge, mount invalidation and `forget-reapply` target the exact `-r<n>` suffix and
   prefix after the document row is gone), the source-identity hash and every version's
   content hash, so the re-ingest guard (§3.6) is active from this commit;
5. enqueues one `forget` work item whose target is the forget record (`forget_id`), not the
   document, so a later forget of a re-added lineage that reuses the deterministic `doc_id`
   never collides with an earlier forget's finished item. The job re-reads its record and
   checks its attempt is current before every destructive step.

After commit, synchronously (as D155's normal removal does): remove the document's recorded
corpus folders, regenerate the affected `_index.md` and root `llms.txt`, and send controlled
mount sessions the invalidation for the document's suffix (§3.7). A failure here is reported in the
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
- **Running work finishes or expires first.** At hide, lease renewal (D145) is refused for
  running items of the document, so a hung handler cannot hold the forget forever. A lease
  ending does not by itself release PostgreSQL session locks, so the dedicated lock sessions
  are bounded too: a worker terminates its own lock sessions when its lease lapses, and the
  janitor terminates (`pg_terminate_backend`) any lock session whose attempt's lease has
  expired. Work that belongs to the document through its payload rather than its target (a
  corpus publication event, whose target is an event ID and whose `doc_id` is in the payload)
  is included in the skip at hide and in the claim and renewal refusals. Forget never blocks on a lock: it tries it and, on contention, reschedules
  itself; the job
  proceeds once each such item has finished or its lease has expired (an expired attempt's
  token is stale and cannot publish). The forget job never blocks a worker slot while it
  waits: it releases its locks and reschedules itself with the ledger's `not_before`, as for
  every other wait in this design (the delayed re-verification included). The forget job waits until no work item for the
  document other than itself is `running`. "For the document" means its target is the document, one
  of its versions, representations, chunks or claims, or an entity whose pending work unit
  (an E3 flush unit) names one of its versions through its membership row. Work items for
  other documents are never waited for.
- **Database writers re-check their inputs.** Every handler that writes document-derived
  rows, or rows derived from another document's claims (fact application and adjudication
  over a shared fact, entity flush units), takes a share lock (`FOR SHARE`, as claim
  publication and Selection already do) on the version row of every input it consumed, checks
  that its attempt is still current, and writes nothing when any input version is gone or
  tombstoned.
  Forget's delete transaction locks those rows `FOR UPDATE`, so a writer either commits
  first (and its rows are in the inventory) or finds nothing to write to.
- **Object writers hold two independent per-document locks.** Every handler that writes
  objects for a document (E0 originals, artifacts, media, source maps, checkpoints, corpus
  folders) holds two shared advisory locks with distinct keys (`object-a:<doc_id>` and
  `object-b:<doc_id>`), each on its own database session, acquired in that order, for the
  whole object IO; forget acquires them exclusively in the same order, so neither side can
  queue behind the other on the second lock while holding the first against it. After acquiring
  them, and before issuing any write, the writer checks that its work attempt is still
  current and its input version is still live (not tombstoned), so an expired worker that
  acquires the locks after an erasure writes nothing. The writer checks both locks before
  every request and releases them only after each request has a terminal result (success, failure, or a confirmed abort
  of a multipart upload). This is D155's two-session publication fence, scoped to one
  document instead of the deployment. Forget's object and corpus steps take both
  exclusively, so every write issued while either session lived has finished. A writer that
  loses both sessions issues nothing further and waits for the terminal result of the request
  it has in flight. If it succeeded, the writer cleans up only what it owns: an upload it
  started (aborted by ID) or an object version it created; for a shared mutable key such as a
  navigation index, it never deletes, but reacquires the fences and regenerates the file from
  current authority, so it cannot remove an index forget has just regenerated. If the writer's process
  dies instead, its connection closes: a provider stores a PUT only once it has received the
  whole body, and that happens while the server is still handling the request. The job
  therefore verifies once more after a delay (starting value 15 minutes, to be measured) and
  the record reaches `erased_at` only after that pass is clean. The residual, a provider that
  stores a fully received PUT later than that delay, is accepted and stated in the public
  docs; S3-compatible providers give no enforceable server-side completion bound to do
  better.
- **Shared navigation files have their own fence.** Corpus index files (`_index.md`,
  `llms.txt`) list many documents, so a publisher for document B can render an index that
  still lists A. Index writes therefore hold the deployment's navigation lock on a second,
  independent session (beside the publication lock), with the same two-session rule; forget's
  corpus step takes both exclusively, regenerates the indexes from the scrubbed database, and
  the delayed re-verification re-checks them.
- **Derived text built from many documents is fenced by its inputs.** Entity profiles, P1
  fact labels and their vectors, entity-resolution decisions and their model rationales (whose
  inputs include every supplied candidate's profile, not only the selected entity's), compiled
  K pages and planner transcripts are published only if the exact inputs they were prepared
  from are unchanged, rechecked under the write lock
  (an input hash, or the generation of every entity and K scope involved; forget advances
  those generations). A computation prepared before the forget is discarded at publication.
- **Shared rows** are recounted under the entity and fact locks ordinary fact work takes, in
  its order (entities, then facts).

### 3.4 The erasure job

One `forget` work item per document runs these steps. Each is idempotent; the work ledger
provides attempts, visible tracebacks, retries and the dead-letter queue. A failure leaves
the document hidden and the record `erasing`; nothing reopens.

1. **Wait for the fence** (§3.3).
2. **PostgreSQL, one transaction.**
   - *Inventory.* Record the content-free inventory on the forget record before anything is
     deleted: row IDs, content hashes, and content-free object **locators** rather than full
     keys (a raw key ends with the caller's file extension; exact keys are discovered by
     listing during the purge): the engine prefix `{doc_id}/` (with its re-add generation,
     §3.6), the opaque key of every original kept outside that prefix (a registered
     `uploads/<id>` original, D147), the owning prefix plus content hash of a deduplicated
     original stored under another lineage, and staged-object IDs; and the K artifact IDs
     (not their paths) that cite the
     document's evidence (read from the citation bindings, which the deletes below remove).
     The inventory separates rows to **delete** from rows to **scrub** (kept as content-free
     retired rows, with the fields to clear named), so verification checks each correctly. On
     `reapply`, shared ownership is recomputed from the restored provenance before anything
     is deleted.
   - *Detach what must survive.* Clear the document's current-version and
     current-representation pointers; unbind incoming D140 cross-references from other
     documents (the citing document keeps its text; the reference resolves as one to a
     never-ingested document); detach metering receipts (`managed_ingest_measurements`) and
     cost rows from the version: unsent measurements are cancelled, and accepted or in-flight
     approvals keep the fields their terminal accounting needs on their own rows, so the
     outbox no longer joins the erased version.
   - *Delete.* Exclusively owned children go by the foreign-key cascades that exist today
     inside the document subtree; rows linked by logical references (columns PostgreSQL
     does not enforce, used on large partitioned tables) are deleted explicitly, children
     before parents: P1 search rows (`chunk_search`), chunk-claim links, evidence links,
     mentions, occurrences, claims, Selection and extraction payloads, review payloads,
     sections, chunks, D134 metadata rows, D156 tag assignments, D118 §7 mutable-fact
     payloads, versions, the document row. The schema-coverage test (§5) proves every table
     is reached.
   - *Facts and entities.* Ownership is classified from all surviving provenance, current or
     historical (a fact also evidenced by an archived document is shared, not exclusive).
     Relations and observations whose only provenance was this document are deleted, after
     detaching the audit rows that reference them; a predicate the source itself emitted
     (an `other:` registry entry) that nothing references afterwards is deleted too.
     Entities whose only provenance was this document become content-free retired rows (IDs
     and structural links, as D74's scrub did, because merge logs and exclusions reference
     them by restrictive foreign keys); their names, aliases and profile text are deleted. For surviving shared facts and
     entities, recompute stored wording, canonical names, aliases and validity from the
     surviving provenance (or clear them until recomputed). Then clear every persisted
     dependant of a changed name or fact: entity profile caches (D95–D96), the stored labels
     and vectors of every fact whose own wording or endpoint names changed (a relation
     supported only by another document still carries the forgotten entity name in its label),
     and add the K pages that cite those facts to the inventory's K list; ordinary work
     recomputes the labels, vectors and profiles. Content-clear entity-resolution audit payloads
     and rationales for entities resolved from the document and for decisions in which a
     changed entity was a supplied candidate.
   - *Ledger and deployment-wide derived data.* Clear `last_error` on the document's work
     items (tracebacks may quote content); payloads carry IDs only. Clear, deployment-wide,
     planner audit transcripts and legacy eval fixtures, which carry no reliable document
     provenance, and record that step 5 must sweep the K session-transcript prefixes.
3. **Objects.** Under the content-hash lock, decide raw-object ownership by physical
   reference: a content object stays while any version row, of any lineage, still references
   it. When two forgets share a hash, whichever deletes the last referencing version deletes
   the object; the other finds a reference and leaves it. The guard keeps any new lineage from
   adopting it meanwhile. Otherwise delete the object and its `content_objects` row together (so a later deliberate re-add writes a fresh original
   instead of pointing at a purged key). Delete every key under the document's prefix
   (`{doc_id}/…`: originals, artifacts, media, source maps, checkpoints) except a shared
   original another lineage still uses, the lineage's own staged objects and uploads, unadopted
   raw originals carrying a forgotten hash (aborting tracked multipart uploads by ID), assets and transcripts. Missing keys count as success.
   Retained object versions follow E0 §6.7 (versioning off, or enumerate and purge every
   version). Forget does not depend on listing a provider's unfinished uploads: every
   multipart upload the engine starts is tracked (§3.6), so it is aborted by ID.
4. **Corpus filesystem (D155).** Scan the corpus keys for every folder with the forgotten
   generation's suffix (including former moved paths and interrupted copies) outside the
   shared publication lock. Then delete them and regenerate the affected indexes from the
   scrubbed database in bounded batches, each under the publication lock and the
   per-document lock taken briefly with try-and-reschedule; invalidate the deleted and
   changed paths in controlled mount sessions (§3.7); and verify. Forget therefore never holds
   the deployment-wide lock across a scan, and a stalled full reconcile (whose lock session is
   bounded by its lease like every other) cannot stop it for long.
5. **Knowledge plane (K).** Every K publication, ordinary or forget's, is a conditional ref
   update: it succeeds only if the repository head is still the head its checkout started
   from, and every purge advances that head. So a stale publisher whose single-committer
   session was lost cannot restore purged history. Holding the single-committer lock for the
   whole step:
   - first determine **blocking** paths: authored pages and curation sidecars whose *current*
     file still cites an ID in the inventory. They get the `redaction_required` review flag,
     are listed on the record, and are treated as absent compile inputs until redacted (also
     hidden from the agent writer's checkout during rebuilds), so no compile can copy their
     text into a compiled page;
   - delete the compiled body, cached model summaries and kept previous Markdown of every
     page in the inventory's K artifact list, of every compiled page whose recorded inputs
     (its bundle, not only its citations: a writer can copy uncited bundle text) contained any
     of the document's evidence, of every compiled page that read a blocking sidecar or
     authored page as input (its body may already hold that prose even if it cites
     only unrelated facts), and of every page that depends on one of them through the
     compiler's actual inputs, followed transitively: same-scope summaries,
     previous Markdown, and child summaries reached through `parent_artifact_id` links across
     scopes. A bundle-mode writer runs in a sandbox that holds only its prepared working
     directory (no memory CLI credentials, no mounts, no Git history), so its recorded
     bundle is exactly what it read. A page compiled in agent mode (the writer explored the
     memory freely, so its inputs are not established) counts as dependent if it was compiled
     after the forgotten document was first ingested. Those bodies are gone before
     the history rewrite re-adds current files, so no contaminated current file is
     re-added;
   - erase every other registered K path from all reachable Git history and re-add its
     current file, including authored and sidecar paths that no longer cite the document but
     may have in older history (D74's conservative rule: current citation bindings cannot
     prove a page never cited it);
   - delete every K session transcript (planner and writer) in the deployment by sweeping
     their dedicated object prefixes, which needs no per-row locator. Transcript uploads hold
     the deployment's two-session K transcript fence (the same rule as the per-document object
     fence, with its own keys), which the sweep takes exclusively, and the delayed
     re-verification re-checks those prefixes, so a transcript PUT already in flight cannot
     survive the sweep;
   - retire or recompute every planner-generated registration (a page path, a routing rule)
     whose text came from a forgotten or renamed entity or from the forgotten document's own
     title or names, before anything is rebuilt, so the
     next compile cannot publish the forgotten name again;
   - publish once, verify the rewritten history, and leave the deleted compiled pages to be
     rebuilt by ordinary K work from clean inputs (when K or the facts layer is switched off,
     they stay absent).
6. **Verify.** Mechanically check that every inventory row marked delete is absent from
   PostgreSQL and every row marked scrub has its named fields cleared; that every cleared
   derived cache (a profile, a fact label or vector) is empty or recomputed from surviving
   inputs, with its refresh queued;
   every object selected for deletion in step 3 (not a shared original that stays) is absent,
   with no retained version or open multipart upload; every corpus copy and stale index entry
   is gone; every controlled session acknowledged; and every retained K path's reachable
   history contains no reference to an inventory ID and no deleted compiled body. Set `erased_at`. With no blocking paths set status
   `complete`; otherwise `waiting_for_redaction`.

### 3.5 The forget record

```sql
CREATE TYPE forget_status AS ENUM ('erasing', 'waiting_for_redaction', 'complete');

CREATE TABLE forgets (
  forget_id             uuid PRIMARY KEY,
  deployment_id         uuid NOT NULL REFERENCES deployments,
  doc_id                uuid NOT NULL,          -- no FK: the document row is erased
  generation            smallint NOT NULL DEFAULT 0, -- the lineage's re-add generation at hide (selects the -r<n> suffix and prefix)
  source_identity_hash  text,                   -- sha256(deployment_id, source_kind, source_ref); NULL for uploads
  content_hashes        text[] NOT NULL,        -- every version's raw content hash
  inventory             jsonb,                  -- content-free row IDs (delete vs scrub), content hashes, object locators and K artifact IDs; set by step 2
  blocking_k_paths      text[] NOT NULL DEFAULT '{}',
  status                forget_status NOT NULL DEFAULT 'erasing',
  requested_at          timestamptz NOT NULL DEFAULT now(),
  erased_at             timestamptz,            -- every engine-owned store verified clean
  completed_at          timestamptz,            -- blocking pages redacted and erased too
  UNIQUE (deployment_id, doc_id, generation),
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
`remember forget list` and
`remember forget status FORGET_ID`.

### 3.6 Re-ingest guard and adding a document back

**Staged objects.** Every engine write path that starts a multipart upload (originals,
artifacts, corpus copies) records the key and upload ID in `staged_objects` before sending the
first part, together with the store and the owning lineage. Forget aborts and deletes the
staged uploads and objects of its own lineage (derivatives and corpus copies are always swept
by lineage) and verifies the abort; it never relies on a provider listing unfinished uploads
or on its expiry policy. Only an unadopted raw original, which no lineage owns yet, is swept
by its content hash, so a forget never touches another lineage's copy of the same bytes. E0 writes an original before
registering it. It first records the key and content hash in `staged_objects` in the
transaction that checks the guard (below), then writes the bytes, then registers. The staged
row is deleted only when its key has been adopted as the version's original or the object has
been deleted and its absence verified (a duplicate of bytes already stored is deleted after
registration, and its staged row with it). A crash at any point leaves a staged row, never an
untracked object: forget's step 3 deletes staged objects carrying a forgotten hash, and the
janitor deletes staged objects older than the request bound. Client uploads to
`uploads/<id>` (D147) are new data a client sent, not a copy of a forgotten document; they are
checked by the guard at registration and swept by the existing unregistered-upload janitor.

**One gate for every ingest path.** `ingest`, `ingest_observed`, `ingest_stored`, connectors
take session-scoped advisory locks, held across the staging, write and registration
transactions and released after registration or refusal cleanup, in one common order: first
the lineage identity (keyed by the deterministic `doc_id` E0 derives from the source identity,
or from the content for an upload without one, so the lock exists before the row does), then
the content hashes in sorted order. Row locks are taken only inside transactions, after
those. They check the guard before E0 writes any bytes and again at registration. The forget request (§3.2) and step 3 take the same locks for every hash on the
record, in the same order. So no ingest can register forgotten content between the guard
check and its commit, no lineage can adopt a raw object forget is deciding about, and bytes
written before a refusal are deleted through their staged row.

- **Default:** a match against any record is refused with 409 `document_forgotten`
  (`forget_id`, `forgotten_at`). Connectors and syncs log it and skip the item. A tombstoned
  lineage is never resurrected by an ingest.
- **`allow_forgotten=true` on that one request:** refused with 409 `forget_in_progress` while
  any matching record is `erasing`. Otherwise the content is registered exactly as an ordinary
  ingest would register it: a new lineage, or a new version of a live lineage that happens to
  carry the same bytes, whose history is untouched. The request releases exactly one record:
  the one whose lineage identity is the request's (the same source identity, or, for an
  upload without one, the same content identity). That record is deleted in the registration
  transaction, only if it commits. Other matching records (a different forgotten lineage that
  shared these bytes) are passed over for this request but stay, and keep guarding their own
  identities and hashes. A pending authored-page wait for a deleted record ends; those pages
  keep their review flag. A re-added lineage keeps its deterministic `doc_id` but writes its
  objects under a new generation prefix (`{doc_id}/r<n>/…`, `n` stored on the documents row;
  a stored-object re-add copies the client's `uploads/<id>` object into that prefix and
  deletes the upload rather than keeping the upload key as its original),
  and its corpus folders under a generation-specific suffix (`corpus_suffix(doc_id)` followed
  by `-r<n>`, E0 §6), so a delete issued by a stalled eraser of the earlier forget, which only
  ever targets the forgotten generation's locators and suffix, can never remove the re-added
  copy. Erasers also take the
  lineage-identity lock (shared with ingest and re-add) around each mutation batch and
  re-check their record and attempt under it. The person chose to have the content back, so its history in those pages is
  no longer an erasure obligation.

The flag exists only per request: on the HTTP ingest routes, the `MemoryClient` ingest
methods, `remember ingest --allow-forgotten` (one file or a folder) and the MCP `ingest`
tool, whose description tells the agent to set it only after the user confirms. There is no
deployment or project setting.

### 3.7 Mounts

- **Controlled sessions** (remember.dev's `remember mount`, through the cloud's mount
  adapter): the corpus purge port gains `invalidate(doc_suffix)`: every path carrying the
  document's folder suffix (derived from `doc_id`, so it survives the loss of the recorded
  path set and holds no title) and every cached navigation file (`_index.md`, `llms.txt`)
  in the session, which the adapter can find by name without knowing which ones changed. The
  call is retryable after a crash at any point, and forget releases the publication and
  navigation locks before waiting for the acknowledgement. The adapter must remove those entries from every open session's
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
Their old row versions remain in table files until later writes overwrite the space vacuum
made reusable, and in write-ahead-log segments until they are recycled; physical remnants
may persist after both, and neither is a secure overwrite. Forget does
not run `VACUUM FULL` (it locks the table exclusively). The public docs state this.

### 3.9 Authored pages: the one wait

The K sync runs under the single-committer lock. On every sync it rechecks every path of
every `waiting_for_redaction` record, changed or not (so an edit that landed while the record
was being written is not missed), including curation sidecars and deleted files (a deleted
file cites nothing). When a path's current file no longer cites any inventory ID, the sync erases the path from all
reachable Git history, re-adds the current file, verifies the rewritten history, and removes
the path from `blocking_k_paths`; when none remain the record becomes `complete`. Blocking
paths can carry page titles, so the record holds them only while redaction is pending. This runs
in the sync itself, not as a re-enqueued work item. The authored-page sync and this
redaction check keep running when compilation does not (K or the facts layer switched off),
so a redaction is always finalized. The engine never edits authored prose;
removing the quoted text, not only the citation, is the owner's responsibility, and the
review flag says so.

### 3.10 After a restore

Restoring backups is the operator's (D60). The restore procedure ends with:

```
remember ops forget-reapply [--from forgets.json]
```

`reapply` works from one record set: the latest export when `--from` is given (it replaces
the restored database's records, so a forget made after the backup is restored and a record a
deliberate re-add released after the backup stays released), otherwise the database's
records. For each record:

- if the restored database holds that exact lineage (by `doc_id`; never by hash, so another
  lineage that owns identical bytes is untouched), reapply runs the same hide and scrub
  operations the request and job use (§3.2 steps 1–4 and §3.4) directly under that record, not
  through the request, and marks any restored `forget` work item for the record as settled;
- otherwise it re-runs §3.4 steps 3–6.

Compiled K pages are regenerable (D46), so `reapply` does not trust a restored K tree's compiled
pages to match the inventory (a restored page may predate the compile that dropped its
citation): it discards every compiled body, summary and previous Markdown in the restored
repository, erases their history, and lets ordinary K work rebuild them. It identifies
compiled files from the restored revision itself: every page carries its ownership
(`page_kind` and its artifact ID) in its own frontmatter, committed together with every
ownership change (a compiled page adopted as authored is re-committed with
`page_kind: authored`), so a compiled page restored under a path it has since moved from is
still recognized and discarded, and an adopted page keeps its authored edits. A restored file
with no ownership marker (a revision from before markers existed) is neither discarded nor
served: it is moved out of the served tree and flagged for owner review. Authored pages and
sidecars are checked against every record's inventory as in step 5.

Either way it erases the union of the exported inventory and whatever the restored database
still holds for the lineage, because a store restored from a different moment than the
database can hold objects or paths only one of the two knows. A record exported while it
was still `erasing` has no inventory yet, and an older database cannot stand in for it (a
newer store can hold keys that database never knew), so reapply reports every such record as
unverifiable and exits with an error rather than certify an incomplete replay; the operator
takes a new full export once the forget has erased.

Store steps are batched across the whole record set: reapply collects every record's
corpus suffixes, locators and K work first, then scans the corpus once, sweeps objects once
and rewrites K history once, rather than repeating deployment-wide work per record. It prints
what it erased. The operator runs it before the restored deployment takes traffic;
the engine does not run it at start and never refuses to start because of it.

A database restored from before a forget no longer contains that forget's record, so the
operator keeps `remember ops forget-export` exports, which include each record's full
content-free inventory, outside the database backup (the erasure log any controller keeps)
and passes the latest full export (every record, not only those newer than the backup) with
`--from`. An older export would re-impose a guard a deliberate
re-add has since released and could erase that re-add, so it must be the latest. Without that export,
S55 does not survive such a restore; this is the accepted limitation. Likewise, deliberately
re-adding a forgotten document releases its record, so a backup taken before that forget can
bring the forgotten version back after a restore: after any release, the operator takes a new
backup, and that backup replaces the pre-release ones for recovery; the older backups stay
outside the erasure guarantee. remember.dev keeps no
database backups.

### 3.11 Surfaces and errors

HTTP `POST /documents/{doc_id}/forget`, `GET /forgets`, `GET /forgets/{forget_id}`;
`MemoryClient.forget_document`, `list_forgets`, `get_forget`; CLI
`remember documents forget DOC_ID` (asks for confirmation; `--yes` skips it),
`remember forget list|status`; the operator commands `remember ops forget-export` (every
record with its inventory) and `remember ops forget-reapply`, which run inside the engine
container against the database, so they work before the API and workers start; MCP tool
`forget_document` with a required
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
  manifest root. D152's managed storage-capture binding loses its forget role filesystem
  (three roles remain: raw, artifacts, corpus); the cloud capture adapter changes with it.
- The startup readiness replay (`HardForgetReadiness` in the API and `project --plane p3`).
- The deployment-wide admission barrier, the shared advisory fence ordinary work takes for it,
  the global drain, and 503 `forget_in_progress` for ordinary traffic.
- The `preparing`/`accepted` statuses and the frozen repeatable-read acceptance cut.
- The refusal of a forget while authored pages cite the document
  (`ForgetRedactionRequiredError`).
- The permanent re-ingest guard.
- The stop-and-remount rule for mounts.

The migration from the D135/D74 release also converts existing tombstones: a lineage a caller
deleted becomes a caller archive and one a connector observed deleted becomes a source archive
(versions get the archive cutoff, `deleted_at` is cleared); internal version tombstones stay
as they are. So no lineage is left "being forgotten" without a forget record. The
`forget_manifests` table becomes `forgets` by migration. A record that exists only in a
deployment's old external manifest store is imported with `remember ops forget-reapply --from`
using that store's export.

## 5. Acceptance tests

Run against real PostgreSQL and MinIO with deterministic providers and no hosted model calls.

1. **Whole-database canary.** Plant a unique token in one document A (body, title, file name,
   metadata, extracted claims (and uncited bundle text a writer copied), a planner-generated
   page path and routing rule, a fact statement and entity alias it alone contributed, the
   name of an entity that another document B also relates to, a reclustering cost-ledger
   call key,
   a compiled K page, the corpus folder); forget it; `pg_dump` the whole database, read
   every object and object version in every bucket, walk the K repository's reachable
   history and the corpus tree, and find the token nowhere. A tag assigned to the document
   loses the assignment; the tag's registry entry, which is deployment vocabulary (D156),
   survives by design. B's independently supported relation survives with clean text.
2. **Schema coverage.** From the live catalog, enumerate every table with a column that
   references a document, version, representation, section, chunk, claim or mention: real
   foreign keys, logical references found by column name (`doc_id`, `version_id`,
   `representation_id`, `section_id`, `chunk_id`, `claim_id`, `mention_id`, and their
   arrays), every `jsonb` or free-text payload column, and registry rows a source can emit
   (`other:` predicates). The test fails when one is not
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
7. **No global stop.** With a single non-convert worker slot, while a forget waits on a
   running work item, ingest, extraction, search and SQL for other documents proceed and
   complete.
8. **Kill, resume and late writers.** Kill the forget item after each step; the retry
   completes and the canary passes. Work of every stage queued for the document before the
   forget is never claimed. A corpus PUT and an artifact PUT held past their session's death
   land before verification and are found; the run does not complete until they are erased.
   A profile and a compiled page prepared before the forget are discarded at publication. A
   hung handler stops renewing at hide and the forget proceeds after its lease. A fact
   application for another document that consumed the forgotten claims writes nothing. An E0
   crash between writing an original and registering it leaves a staged row that forget
   erases.
9. **Concurrent forgets.** Two forgets of documents sharing a fact race with a third document
   adding support to it; the fact ends with exactly the third document's support and wording.
   Two forgets of lineages with identical bytes, in both job orders and with an archived third
   owner: the object goes exactly when its last version reference does. A new version arriving
   while a forget enumerates versions waits on the lineage row and is then refused. B's index
   PUT paused across A's purge, with B's document locks held, is caught. A paused P1 label job
   on a shared fact discards its stale label. A forget racing a full corpus reconcile completes
   without deadlock. A resolver decision whose candidates included the forgotten entity
   publishes no stale rationale. A stalled eraser's delete issued after a deliberate re-add
   leaves the re-added copy intact, in object storage and in the corpus. A corpus publisher
   that stalls mid-PUT while its heartbeat still runs is refused renewal and the forget
   proceeds. Restore only the artifact bucket against the post-forget database: K session
   transcripts are swept; a transcript PUT that lands after the first sweep is caught by the
   delayed pass. A stored-object re-add whose earlier upload key receives a late DELETE after
   registration keeps its copy. Two lineages staging the same bytes: forgetting one never
   aborts the other's upload. A stalled full reconcile with a running heartbeat does not stop a
   forget. Restore only the corpus bucket from between a re-add and its second forget: the
   second forget's generation is erased. Upgrade from the D135 release: deleted lineages
   become archives. A writer that stalls while holding both object locks
   loses them when its lease lapses, and the forget proceeds without occupying a worker slot.
   A bundle-mode writer cannot reach the memory CLI or mounts. An edited version whose flush
   unit holds both fresh claims (episode 1) and reused claims (episode 2) applies each
   against its own episode. A writer whose sessions died while forget regenerated an
   index does not delete the new index.
10. **Authored pages.** An authored page citing the document: forget erases everything else
    and reports `waiting_for_redaction` with the path; after the owner's edit the sync
    completes it and the old page text is absent from Git history. An authored page that
    cited it only in older history is rewritten immediately. A blocking sidecar is never
   copied into a rebuilt compiled page. An owner edit that lands while the record is being
   written, and a sidecar deleted by its owner, both complete the forget on the next sync.
11. **Re-ingest.** Refused without the flag (`document_forgotten`) for an upload without a
    source identity, a stored-object ingest (refused object deleted) and a connector resync;
    refused while erasing (`forget_in_progress`); with the flag, accepted under the same
    deterministic `doc_id`, the identity-matched record deleted, and a later `reapply` with
    the latest export leaves the copy alone. Archive B, forget A with identical bytes, then
    unarchive B: it succeeds. Archive while extraction is in flight, then unarchive: the
    unfinished stages resume and completed paid work is reused. A second forget of a re-added
    document while a delayed delivery of the first forget's item arrives: the stale item does
    nothing and the second forget completes.
12. **Mounts.** A controlled session open during the forget stops listing the document, and a
    handle opened before the forget fails its next read, without the session restarting;
    changed index files show the regenerated content. A crash after the corpus deletion and
    before the acknowledgement is retried by suffix and still invalidates a warmed handle and
    a warmed, since-pruned index file. A multipart upload interrupted mid-write for the
    document is aborted by its tracked ID even where the provider cannot list uploads. An S3 FUSE mount with bounded caches
    stops listing it within its cache bound, and a fresh open fails after the bound; the test
    records the chosen tool's behaviour for already-open handles. A PUT that commits after its
    writer's sessions died and its client timed out is caught by the delayed pass.
13. **Restore.** Restore the pre-forget database dump and run `forget-reapply --from` the
    export: the canary passes. Restore only the raw bucket against the post-forget database:
    a registered `uploads/<id>` original and a deduplicated original are erased from their
    locators. Restore only the K repository from before a page moved: the compiled page under
    its old path is discarded, not adopted; restore it from after a compiled page was adopted
    as authored: the authored edits are kept; a markerless legacy file is quarantined. Restore only the K repository from before a compile that dropped the
    citation: `reapply` discards the restored compiled pages and the canary passes. Restore only the corpus bucket and run `reapply`: it passes. A stale export
    does not erase another lineage that owns identical bytes. A restore spanning a deliberate
    re-add, with the latest export, neither erases the re-add nor revives its guard. A
    connector document and an upload forgotten with identical bytes: re-adding the connector
    document releases only its record, and a later unflagged upload is still refused.
14. **Archive.** Archive closes exclusive facts and keeps shared ones; the document is hidden
    by default and readable with `include_archived`; identical bytes re-sent leave it
    archived; a source delete-and-recreate within one sync pass is a support swap; a source
    file reappearing in a later cycle revives a source archive; a caller's archive of a
    source-archived document is not undone by a resync; after un-archive the latest version
    testifies again and older editions stay cut off, and a belief-as-of read inside the former
    archive interval still sees them archived. A version that finished with facts disabled and
    was archived is reached when facts are enabled after un-archive. Un-archive of a keyed, periodised D140 document restores
    its edition and periods and its pinned references resolve again. A restored claim whose old
    fact survived through another document and whose new application lands elsewhere supports
    only the new fact. Metering: an unsent measurement for a forgotten version is
    cancelled and an approval racing the purge keeps its terminal accounting.
15. **Rule 4.** Provider calls are counted: un-archiving a fully processed version makes no
    Selection, Claimify or embedding call; a changed version of an archived document with one edited
    section pays only for that section, per the CLAUDE.md Rule 4 test. Restored claims get a
    fresh fact application: the old fact stays closed and the new support is visible.
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
