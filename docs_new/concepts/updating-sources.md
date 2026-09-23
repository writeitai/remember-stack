---
title: "Updating a source: snapshot and living"
description: What a new version of a document means to RememberStack, and how a living source retracts facts when its content is removed.
applies_to: [remember.dev, self-hosted]
---

# Updating a source: snapshot and living

Documents change. A spec is edited every week; a status page is rewritten;
a meeting note is corrected the next morning. When a line disappears from
the spec, did the team stop believing it, or did someone tidy the page?
The answer depends on what kind of source it is, and a memory that guesses
wrong either keeps serving facts nobody stands behind any more, or forgets
things that were only moved.

RememberStack asks you to say what an edit means, once per document, with
its **versioning mode**.

## Two modes

| Mode | An edit means… | Right for |
|---|---|---|
| `snapshot` (default) | Another dated statement. Every version stays standing testimony, forever. | Archives and anything whose versions are separate statements: meeting notes, dated reports, transcripts, exported chat logs, rolling logs. |
| `living` | The source's current statement. The newest version replaces what the older ones said. | Documents that are kept up to date in place: a spec, a roadmap, a README, a status page, an agent's own running notes. |

You choose the mode when you ingest, and it needs a source
(`source_kind` and `source_ref`):

```python
import remember

with remember.Client() as memory:
    version = memory.ingest(
        "specs/billing-migration.md",
        source_kind="notes",
        source_ref="specs/billing-migration.md",
        versioning_mode="living",
    )
```

`snapshot` is the default because it is the safe one: it never removes
anything. Use `living` when "the newest version is what we mean now" is
true of the source.

Ask what a missing line means before you pick `living`. A rolling log that
keeps only its last thousand lines, or a chat export that holds the last 30
days, drops old lines because they are old, not because anyone took them
back. Ingest those as `snapshot`. As `living`, every line that scrolled off
would retract the facts it alone supported.

## What happens on a new version

In both modes a new version is processed the same way: it is converted,
chunked and read. Passages that did not change keep their existing claims
(same `claim_id`) without being extracted again; changed passages produce new
claims. New claims go through ordinary fact adjudication, so a changed value
updates, supersedes or contradicts facts like any other new testimony.

The modes differ in what happens to the **old** claims.

### Snapshot

Nothing happens to them. The claims of every version stay current
testimony. If version 1 said the cutover is June 8 and version 2 says June
15, both statements stand, each dated by its own `asserted_at`, and the
facts reflect both, as a correction, a successor or a contradiction.

### Living

When the new version has finished processing, RememberStack compares it with
the previous one. Claims that no current passage carries any more stop being
current testimony (reason `version_superseded`). A claim that moved to
another passage of the same document is still carried, so it stays current.

Then every fact those claims supported is recounted:

- **Other current support remains**: the fact's `evidence_count` goes down.
  Nothing else changes.
- **The removed claims were its only support**: the fact is **retracted**.
  Its `invalidated_at` is set, so it stops appearing in queries that read
  current belief. The retraction is written to the fact's decision
  transcript with the outcome `retracted_source_removal`.

Retraction ends belief; it does not invent a world-time end. The fact's
`valid_until` is left as it was, because a removed line says nothing about
when the thing stopped being true.

For documents ingested directly (`POST /ingest`, `memory.ingest`), the check
runs as soon as the new version is processed. For documents fed by a
connector sync, it waits until the whole sync cycle has finished, so a
passage that moved from one file to another within one sync counts as a
change of support, never as a retraction followed by a re-assertion.

This is how a living source takes something back: by no longer saying it.
Removing "Ravi owns the schema change" from the spec retracts that fact if
no other document supports it.

## Retraction is recorded, not deleted

A retraction never deletes anything:

- The claims remain, marked non-current with the reason, and readable for
  audit (`claims_visible_history`, `testimony_currency_events_visible`).
- The fact remains, with the instant it was retracted in `invalidated_at`.
  `hydrate_relation` still returns it and shows its evidence.
- The transcript says what happened and in which reconciliation.

If the content comes back in a later version, it is new testimony and goes
through ordinary processing again.

## Two different problems

A document's claims can change for two unrelated reasons, and RememberStack
keeps them apart:

| What changed | Example | A fact that loses its only support |
|---|---|---|
| **The source itself** | The spec no longer says "Ravi owns the schema change". | Is retracted in a `living` document (`invalidated_at` set, recorded as `retracted_source_removal`). In a `snapshot` document the old version still supports it, so it does not lose support. |
| **Only the reading of it** | A new release re-reads an unchanged file with a newer extractor, converter or chunker, and does not find the claim again. | Is not retracted. It is marked `support: "withdrawn"`, flagged for review and still returned. |

The first is the source speaking: it stopped saying something. The second
is RememberStack reading the same bytes differently, and it cannot tell
whether the old reading or the new one is right. So a fact is taken back
only when the source acts, or when its document is deleted. See [Facts](facts.md#support-withdrawn).

## Unchanged bytes and `source_version_ref`

Sending bytes identical to the document's latest version creates nothing
(`created=false`), in both modes. See
[Documents, versions and sources](documents-and-sources.md#re-ingesting-the-same-bytes-createdfalse).

`source_version_ref` is an optional revision marker from the source system:
an ETag, a Drive revision ID, a commit SHA. RememberStack stores it on the
version. When a sync sees a new revision marker but identical bytes, the
existing version's marker advances, so the sync does not fetch that revision
again. The version's `source_modified_at` does not change.

## Deleting a document

Deleting a document or a version ends the currency of its claims (reason
`version_deleted`) and retracts facts that only it supported, the same way
as a living removal. Deletion is not exposed through the HTTP API, the
`remember` package or the CLI yet.

## Where to go next

- [Keep a source up to date](../guides/keep-sources-current.md): a working
  sync loop.
- [Claims](claims.md#current-and-superseded-testimony): testimony currency.
- [Contradictions](contradictions.md): what a changed value does to facts.
