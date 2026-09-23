---
title: A five-minute tour
description: What happens to a document from the moment you send it to the moment your agent asks about it.
applies_to: [remember.dev, self-hosted]
---

# A five-minute tour

This page follows one document through RememberStack. The team in the
example is working on a billing migration. Dana is the product lead and
Ravi is an engineer. The document is the notes from Thursday's stand-up.

## 1. You send the document

```python
from datetime import UTC, datetime

import remember

client = remember.Client.from_env()
version = client.ingest(
    "notes/2026-09-17-standup.md",
    mime="text/markdown",
    source_kind="file",
    source_ref="notes/2026-09-17-standup.md",
    source_modified_at=datetime(2026, 9, 17, 9, 30, tzinfo=UTC),
)
```

RememberStack stores the file straight away and returns a `version_id`. The
`source_kind` and `source_ref` pair names where the file lives, so a later
edit of the same file becomes a new version of the same document rather than
an unrelated one. `source_modified_at` tells RememberStack when the source
said what it says.

Sending the same bytes again stores nothing new and returns `created: false`.
See [Documents, versions and sources](../concepts/documents-and-sources.md).

## 2. RememberStack reads it

The document now goes through a pipeline of stages that run in the
background.

1. **Convert.** The file becomes Markdown. Markdown and plain text pass
   through unchanged; other formats need a converter.
2. **Structure.** RememberStack finds the document's sections and what each
   is for, such as body, appendix or references.
3. **Chunk.** Each section is cut into passages along paragraph boundaries.
4. **Select and extract claims.** RememberStack picks out the statements
   worth keeping and drops opinions, advice and hypotheticals. It records
   why each dropped statement was dropped. Each kept statement becomes a
   *claim*, rewritten to stand on its own ("Ravi said the migration moves to
   October" rather than "he said it moves"), and tied to the exact
   characters it came from.
5. **Check grounding.** A deterministic check rejects any claim that uses
   words the source does not contain. This is where invented detail is
   caught.

## 3. RememberStack connects it

1. **Resolve entities.** Names become *entities*. "Ravi", "Ravi K." and
   "the backend engineer on billing" can resolve to one person. A merge can
   be undone.
2. **Form facts.** Claims become *facts*: relations between two entities
   ("Ravi owns the invoice exporter") or observations about one ("the
   billing migration targets October").
3. **Adjudicate.** Each new claim is weighed against what the memory
   already holds. It either confirms an existing fact, adjusts the period in
   which that fact held, supersedes it, or is marked as contradicting it.
   Nothing is silently overwritten, and every decision is recorded.

If last week's planning doc said the migration targets June, the June fact
now has an end date, the October fact starts, and both keep their evidence.

## 4. The document becomes queryable

This takes minutes, not milliseconds, because every stage above does real
work. You can ask when a document is ready:

```python
client.wait_for_readiness([version.version_id], timeout=1800, poll_interval=15)
```

See [Wait until a document is queryable](../guides/wait-for-readiness.md).

## 5. Your agent asks

```python
result = client.facts_context("When does the billing migration ship?")
```

`facts_context` finds the entities in the question, walks the graph around
them, and ranks the facts it finds there. The result contains:

- the current fact: the migration targets October;
- its time window: held since 17 September 2026;
- its evidence: the passage in Thursday's stand-up notes, with character
  positions;
- the fact it replaced, if you ask for history;
- anything contradicting it.

No language model writes this answer; the question is only embedded for
the semantic part of the search. The same question returns the same answer
until the memory changes.

## 6. You check the answer

Every fact links to the claims behind it. Every claim links to a passage,
and every passage to a document version. Your agent can quote the stand-up
notes word for word. You can open the file and find the sentence.

## Where to go next

- [Quickstart](quickstart.md): do this yourself.
- [Claims](../concepts/claims.md), [Facts](../concepts/facts.md) and
  [Time](../concepts/time.md): the ideas behind each step.
- [The pipeline and readiness](../concepts/pipeline.md): every stage in
  detail.
