# Public document deletion — analysis (D135)

**Status:** analysis behind D135. Non-binding; the binding contract is
[evidence_lifecycle_design.md §8](../designs/evidence_lifecycle_design.md#8-deletion--deletion-removes-the-documents-contribution-uniformly).
**Date:** 2026-09-23.

## 1. The problem

A person who ingests the wrong file, or a file they are no longer allowed to
keep, has had no way to take it back out of the memory. The engine has always
known how: D55 and the evidence-lifecycle design §8 define *deleting a
document* as removing its contribution — its claims stop counting as current
testimony, fact support is recounted, and facts that only this document
supported are closed with a recorded retraction. `LifecycleCatalog` and
`DeletionService` implement exactly that, and the hard-forget worker (D74)
reuses it as its first step. But nothing outside the engine could call it: the
HTTP API, SDK, CLI and MCP servers had no delete, and the docs listed deletion
under "not built yet".

Example. Dana uploads `billing-migration-draft.md`, which says "The billing
migration finishes in March." Ravi's later status note says the same thing.
A week later Dana realises the draft was never approved and wants it gone.
After deletion the memory should still believe "finishes in March" (Ravi's note
supports it), but only on one supporter; facts that came *only* from the draft
should end, with a record saying why.

Exposing the cascade is the easy part. A caller-facing delete also has to
answer questions the internal service never had to:

1. What does a second delete of the same document do?
2. What happens when a delete is interrupted half-way?
3. What happens to pipeline work still running for the document?
4. What happens when the same file is ingested again later?
5. Who may call it, and is it charged?
6. Which grains are exposed: lineage, version, hard-forget?

## 2. What the existing code did (inspected on `main`, 2026-09-23)

- `LifecycleCatalog.delete_lineage` set `documents.deleted_at` and cleared the
  document's T4 anchors (D102). It left every `document_versions` row live.
- `DeletionService.delete_lineage` then ran the cascade under a stable
  reconciliation id (`delete-lineage:<doc_id>`), so a rerun replays the ledger
  rather than duplicating it.
- Every read already hides a tombstoned lineage: the `memory_v1` views join
  through `documents.deleted_at IS NULL` (and `document_versions.deleted_at IS
  NULL` for version-derived rows), P1 search joins `documents_live`, and the
  inventory filters tombstones. So the tombstone alone removes a document
  from reads; the cascade is what fixes fact support.
- **Re-ingest after delete was broken.** E0 resurrects a tombstoned lineage
  when it sees the same identity again (designed for a watched file that is
  deleted and recreated). For an upload, identity *is* the content hash. So
  re-uploading a deleted file resurrected the lineage, found the latest
  version had the same bytes, and returned it as a no-op: the document came
  back live in the inventory while contributing nothing, because its claims
  had lost currency and nothing would run again.
- **Claim reuse ignored deletion.** D56 reuse attaches an earlier version's
  claims to a new version's identical chunk. A deleted version's claims are
  non-current, so reusing them makes a new version silently testify nothing.
- **In-flight work was not considered.** Deleting a lineage while its version
  is still extracting leaves the pipeline running; claims and fact evidence it
  writes afterwards are current, so facts could regain support from a deleted
  document. Reads stay clean (they filter the tombstone), but fact counts and
  closure do not.

## 3. Options and choices

### 3.1 Repeat and interrupted deletes

- **(a) Idempotent 200.** Every delete of a deleted document answers 200.
  Simple for retries, but it cannot tell a caller the id was wrong: a typo and
  an earlier success look the same.
- **(b) 404 for anything absent.** Clear, and matches `GET /documents` (a
  deleted document is not listed). But if the first attempt committed the
  tombstone and crashed before the cascade, the retry would answer 404 and the
  facts would never be fixed.
- **(c) Chosen: finish-or-refuse.** A delete of a tombstoned lineage reruns
  the cascade under the same stable id. If the rerun changes anything (a new
  currency event, a moved count, a newly closed fact), the earlier attempt was
  incomplete and the call answers 200 with what it finished. If nothing
  changes, the document was already gone and the call answers 404
  `document_not_found`. The rerun is database-only and bounded by the
  document's own claims.

### 3.2 Work still running for the document

- **Refuse to delete while work is pending (409).** Honest, but a document
  stuck in retries or parked with no conversion route could never be deleted,
  and the caller has nothing to do but poll.
- **Cancel pending work.** The work ledger has no cancel state; adding one
  touches every stage's claim path.
- **Chosen: let it finish and retire it at reconcile.** Reconcile is the stage
  every version's chain passes through after facts are applied. When it finds
  its version's lineage (or the version itself) deleted, it runs the same §8
  cascade over whatever that deleted input still holds current, clears any T4
  anchors late work re-created, and ends the chain (no labelling of facts from
  deleted input). Reads never show the late work, because they filter the
  tombstone. The cost is that already-queued stages still run once.

  Verified in CI while building this: claim extraction already fences
  publication on a tombstoned lineage (`selection_catalog` refuses a chunk
  whose document is deleted), so a document deleted before extraction never
  gets claims at all. The window reconcile covers is narrower: claims
  extracted just before the tombstone, then fact application after it. Fact
  application counts only current testimony, so it can leave a fact open with
  zero support; reconcile (and a repeated delete) therefore recount and close
  every fact that *any* of the deleted lineage's claims touches, not only the
  claims whose currency changes in that run.

### 3.3 Adding the document back

- **Refuse re-ingest of a deleted document.** Breaks the obvious "undo" flow
  (delete, then upload the file again) and the connector delete-and-recreate
  case.
- **Undelete: restore the old claims and reopen closed facts.** Needs a
  reopening outcome for `retracted_source_removal` closures that D118's fact
  windows do not have, and would resurrect testimony the person deliberately
  removed.
- **Chosen: re-adding is a new observation.** Lineage deletion tombstones
  every version with the lineage. E0 treats a deleted latest version as never
  matching new bytes, so returning bytes create a new version and run the full
  pipeline; claim reuse never draws on a deleted version. The re-added
  document's claims are fresh, current testimony, and E3 applies them to live
  facts as for any new document (a closed fact is not reopened; an equivalent
  live fact is found or created).

### 3.4 Authority and cost

- Deletion changes memory, so it requires full **write** scope. The route
  table's default already makes an unlisted route `WRITE`; a narrow browser
  `ingest` credential and a `read` credential are refused.
- It runs under the D74 admission barrier like every route: while a
  hard-forget is in progress it answers 503 `forget_in_progress`.
- It is **not** spend-gated: it starts no pipeline work. Its only possible
  provider call is re-embedding the entity profiles whose facts changed
  (profiles are the disposable orientation text used by resolution). That is
  recorded on the surface cost ledger under call site `profile_delete`, as a
  review verdict's refresh is. A provider failure there is logged and does
  not fail the committed deletion; those profiles refresh on the entity's next
  evidence change.

### 3.5 Grains

- **Lineage (exposed).** "Remove this document" is what callers mean, and it
  is the grain inventory, search results and citations name (`doc_id`).
- **Version (not exposed).** The engine grain exists (`delete_version`,
  repointing currency to the predecessor), but a public version delete needs a
  contract for deleting the last live version and for how the inventory shows
  a lineage with none; that is a separate decision.
- **Hard-forget (not exposed).** D74's erasure is a different operation: it
  closes admission for the whole deployment while it runs, purges stored
  bytes, projections and knowledge-page history, and requires authored pages
  to be redacted first. Putting it behind the same verb would make an
  irreversible, deployment-wide outage one flag away from an ordinary delete.
  It stays an operator operation.

## 4. Consequences

- Deletion is auditable: currency events (`version_deleted`) and fact
  retractions (`retracted_source_removal`) name the stable run id; claims and
  stored originals remain. It is *not* erasure; D74 remains the only erasure.
- Watched-source deletion (`sync.py`) still tombstones only the lineage and
  keeps its own resurrection semantics; bringing it onto the version tombstone
  is outside this decision.
- A deleted document's T4 anchors are cleared at delete time and again if late
  work re-creates them.
