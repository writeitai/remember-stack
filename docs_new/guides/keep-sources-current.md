---
title: Keep a source up to date
description: Re-send an edited file as a new version of the same document, and choose what happens to facts that the new version no longer supports.
applies_to: [remember.dev, self-hosted]
---

# Keep a source up to date

Specs get edited. The billing migration plan said June; now it says
October. If you send the edited file as a new, unrelated document, the
memory holds both plans and cannot tell which one the team still stands
behind. This page shows how to send the edit as a new version of the same
document, and how to tell RememberStack whether the newest version replaces
what the old one said.

Setup is in the [Quickstart](../start/quickstart.md). The model behind this
page is in [Updating a source: snapshot and
living](../concepts/updating-sources.md).

## Re-send with the same source pair

A document is identified by `source_kind` + `source_ref`. Send the edited
file with the same pair:

```python
from datetime import UTC, datetime

import remember

client = remember.Client.from_env()

v2 = client.ingest(
    "specs/billing-migration-plan.md",
    mime="text/markdown",
    source_kind="file",
    source_ref="specs/billing-migration-plan.md",
    source_modified_at=datetime(2026, 9, 17, 14, 0, tzinfo=UTC),
    versioning_mode="living",
    source_version_ref="git:4f2c9e1",
)
print(v2.doc_id, v2.version_id, v2.created)
```

What happens:

- **Changed bytes** create a new version of the same document: the same
  `doc_id`, a new `version_id`, `created=True`. Wait on the new
  `version_id` before you query ([Wait until a document is
  queryable](wait-for-readiness.md)).
- **Identical bytes** (the file's latest version already has this content
  hash) store nothing and return the existing version with
  `created=False`. Nothing is reprocessed.
- **Bytes equal to an older version** (you reverted an edit) are a new
  observation and become a new version. The document moves forward; it
  never silently falls back to an old version.

Only the edited passages are extracted again. Passages whose text and
neighbours did not change reuse the claims they already had.

The same with the CLI:

```bash
remember ingest specs/billing-migration-plan.md \
  --mime text/markdown \
  --source-kind file --source-ref specs/billing-migration-plan.md \
  --source-modified-at 2026-09-17T14:00:00+00:00 \
  --versioning-mode living \
  --source-version-ref git:4f2c9e1
```

## Choose snapshot or living

`versioning_mode` says what an edit means.

| Mode | Use it for | What a new version does |
|---|---|---|
| `snapshot` (default) | Minutes, reports, dated notes, anything where each version is a record of its moment. | Every version stays dated testimony forever. An old version's claims keep counting. |
| `living` | Specs, plans, wikis, a README: documents whose latest version is what the author currently stands behind. | The latest version is the document's standing statement. Claims whose passages left it stop counting as current testimony. |

The mode belongs to the document and is set by its first ingest. Later
calls with a different `versioning_mode` for the same source pair are
accepted but do not change it, and neither does a later `title`. Decide
before the first send; if you got it wrong, use a new `source_ref`.

`versioning_mode="living"` and `source_version_ref` require the source
pair; without it the client raises `ValueError` and the API answers 422.

## What happens to facts

Facts are what the memory holds true, each backed by claims from one or
more documents. When a new version arrives:

**Changed statements are new testimony.** The October date in version 2 is
a new claim. It goes through adjudication like any other: it can supersede
the June fact, contradict it, or corroborate something else. The June
statement is not deleted; it becomes history with an end date.

**In `living` mode, removal retracts.** If a fact's only current support
was a passage that is gone from the latest version, the fact is closed:

- a relation or a state (Ravi owns the invoice exporter) gets
  `valid_until` set to the new version's `source_modified_at`;
- a measurement for a fixed period (Q3 invoices: 4,120) keeps its validity
  and is marked no longer believed (`invalidated_at`), because the figure
  was true of its period; what ended is the belief.

If other documents still support the fact, it only loses this document's
support; its `evidence_count` goes down by one and it stays current.

**In `snapshot` mode, nothing is retracted by a new version.** Removing a
sentence from version 3 does not unsay what version 2 said.

Retraction is visible, never silent. A closed relation or state keeps its
`valid_until` and is still returned by a fact query in [history
mode](ask-about-the-past.md). A withdrawn measurement is no longer
believed, so fact operations stop returning it; the SQL view
`facts_visible_history` still shows it with its `invalidated_at`.

## `source_modified_at` and `source_version_ref`

- `source_modified_at` is when the source last changed, as a
  timezone-aware UTC `datetime`. Claims extracted from the version get it
  as `asserted_at`, and in living mode it is the time a retracted fact
  stops holding. Send the source's own modification time, not the time you
  uploaded. It is fixed once the version exists; re-sending identical bytes
  with a different `source_modified_at` does not change it.
- `source_version_ref` is your label for the upstream revision: a git
  commit, a Drive revision ID, an ETag. It is stored with the version.
  When you re-send identical bytes with a new `source_version_ref`, no
  version is created but the stored label moves to the new value, so a
  sync job can remember how far it got.

## A sync loop for a folder of specs

```python
from datetime import UTC, datetime
from pathlib import Path

import remember

client = remember.Client.from_env()
root = Path("specs")
new_versions = []

for path in sorted(root.glob("*.md")):
    version = client.ingest(
        path,
        mime="text/markdown",
        source_kind="file",
        source_ref=f"specs/{path.name}",
        source_modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        versioning_mode="living",
    )
    if version.created:
        new_versions.append(version.version_id)

if new_versions:
    client.wait_for_readiness(new_versions, timeout=1800, poll_interval=30)
```

Run it on a schedule. Unchanged files are no-ops; edited files become new
versions; the loop waits only for what changed.

## Removing a file

Not sending a file does not remove its document: RememberStack has no
signal that the file is gone, and its claims keep counting. The HTTP API,
the client and the CLI have no delete call yet. To stop a living document
from supporting anything, send a version with the passages removed. See
[What is not built yet](../project/not-built-yet.md).

## Next

- [Wait until a document is queryable](wait-for-readiness.md)
- [Ask about the past](ask-about-the-past.md): see the June plan and the
  October plan side by side in time.
- [Contradictions, corroboration, supersession](../concepts/contradictions.md)
