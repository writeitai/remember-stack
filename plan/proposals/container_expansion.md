# Proposal: container expansion into child documents

**Status:** not chosen (2026-09-27, D138). Designed as D133's `expand` posture (§5) and reviewed
over several rounds; removed from the binding design when D138 settled that coding agents open
archives and attachments with their own tools. Archives now get a card listing their members.

## Adoption trigger

Adopt when members inside containers must be **searchable as documents in their own right** —
for example mailbox exports or chat exports whose messages are the knowledge, or archives whose
members agents repeatedly fail to find from the member-listing card — and a member-listing card
demonstrably is not enough.

## The design as reviewed

The text below is the D133 design as it stood when removed, including its schema
(`document_members`, `counting_lineage_id`, `document_versions.expansion_status`), its counting
rule (a container and its members count as one D54 source), and its delete and forget rules
(deleting or forgetting an upload covers every expanded member; a member is never deleted or
forgotten on its own). Section numbers refer to D133.

## The expand posture — child documents

A container's members become **child documents**: ordinary E0 documents,
written through the normal ingest path (ingestion always writes through E0,
D60/D61), detected from their own bytes, routed by the registry, and
processed like any upload.

### 5.1 The expand stage

Expansion is an E0 sub-worker, `expand`, after `convert`:

```
ingest ──► convert ──► expand ──► structure ──► crossref
```

- `convert` returns the parent's own reading plus **member descriptors**
  (`ConversionResult.members`): for each member its member key (§5.2),
  member path, relation (`archive_member`, `attachment`, `message`,
  `conversation`, `embedded_image`), its locators in the parent (one per
  occurrence), its own
  timestamp when the container records one, and its bytes staged in the private store (§4.6) keyed by the parent's
  content hash and the member key.
- `expand` reads the descriptors and, for each member, performs one E0
  ingest write with the child's lineage identity. It is idempotent: the
  member record's primary key and E0's content-hash no-op make a replay a
  no-op.
- **Member records** (schema: `document_members`) link parent version →
  child document version: `(parent_version_id, member_key)` primary key,
  member path, relation, child `doc_id` and `version_id`, its locators in
  the parent (a list: a repeated figure has one per occurrence),
  and a per-member status (`ingested`, `skipped` with reason, `failed`).
- **Two records, two lifetimes.** The parent's representation is immutable
  once written (D65). Its `coverage.gaps` names only what *conversion* left
  out, which the bytes fix: members beyond the expansion bounds, unsafe
  paths, decorative images below the floor. What happens to each member
  afterwards is mutable state in `document_members`: `ingested`, `skipped`
  (refused by detection or over its family's limit) or
  `failed`, with the reason. Other members proceed when one fails.
- **Readiness:** the parent's representation becomes current when its own
  reading completes; it does not wait for its children. The parent version
  carries a separate expansion status (`pending`, `complete`, `partial`)
  — a scoped readiness fact in the sense of `media_design.md` §4b. Each
  child becomes ready on its own schedule.
- The parent's `document.md` lists its members by name and relation, each
  with a stable **member handle** (`member:<member_key>`) rather than a path
  to a child that may not exist yet. P3 and retrieval resolve a handle through
  `document_members` when they render, so links appear once a child is
  ingested and the parent is never rewritten. The listing does not inline
  member content, so the same text is never extracted twice.

### 5.2 Member identity

A child lineage's identity is `source_kind="container_member"`,
`source_ref="<parent doc_id>:<member key>"` (D55). The member key must be
unique within one parent version and stable across parent versions where
the member is "the same thing":

| Container | Member key |
|---|---|
| Archive | `<normalized path>@sha256:<member hash>`, always |
| Email attachment | `<attachment file name>@sha256:<attachment hash>`, always |
| Mailbox | `sha256:<message bytes hash>`, always (a mailbox message's bytes do not change) |
| Message export | The shape's native conversation identifier, always (each shipped shape has one: ChatGPT conversation `id`, Slack channel ID, and the fixed key `chat` for a single-conversation WhatsApp file) |
| Embedded image | `sha256:<image bytes hash>`, always, with every occurrence's locator on its member record |

Every key has the same form whatever else the container holds: no key uses a
position or ordinal, and none changes shape when a duplicate appears.
Byte-identical repeats collapse to one member because they have the same
key.

Consequences, stated so they are not surprises: renaming **or editing** a
file inside an archive, or an attachment, makes a new child lineage and
retires the old one as absent — archive and attachment members have no
version history of their own, because a key that followed edits would need
the path alone, and a path alone is not unique inside an archive. A growing
chat conversation keeps its lineage and gains versions. Adding a member never
changes another member's key, and an unchanged figure in an edited document
keeps its child lineage.

A child's bytes are the member's exact bytes where the container delimits
them. Where it does not (one conversation inside a chat-export JSON), the
bytes are a canonical serialization of that member, and the member record
says so. A child inherits the parent's `versioning_mode`; its
`source_modified_at` is the member's own timestamp (ZIP entry time, email
`Date`, message timestamp) when the container records one, else the
parent's.

### 5.3 Counting and versions

- **Counting (D54).** A child and its parent are one source for confirmation
  counting. `documents.counting_lineage_id` is written once at lineage
  creation: the lineage's own `doc_id` for a root, the root container's
  `counting_lineage_id` for a child. Evidence rows denormalize it write-once
  like `doc_id`, and D54 counts `COUNT(DISTINCT counting_lineage_id)`
  (`postgres_schema_design.md` §13.1). Relation and observation counts,
  confirmation, reconciliation and projections all use it.
- **Versions (D55/D56).** A new parent version re-expands. A member with the
  same key reuses the existing child lineage and, when its content hash is
  unchanged, the existing child version. Only message-export conversations
  can gain a new child version, because only their keys (native IDs) survive
  a content change; an edited archive member or attachment has a new key and
  is a new child lineage (§5.2). A member absent from the new parent version
  has its child lineage retired as absent.

### 5.4 Deleting and forgetting

**Delete and hard forget act on the uploaded document; everything expanded
from it is part of it.** Deleting or forgetting a container covers every
lineage reachable from it through `document_members`, in the same
operation, exactly as chunks and claims go with their document. A member is
not deleted or forgotten on its own: a request naming a member is refused
with an error naming the upload it came from. (A container's original bytes
contain every member, so a single member could not be erased anyway while
its container survives.)

Staged member bytes are temporary: `expand` deletes a member's staged copy
from the private store once the member is ingested, skipped or failed.

### 5.5 Bounds

Expansion is bounded against hostile or accidental blow-up (zip bombs,
recursive archives). Bounds apply to the **whole tree under one root**,
not per level: nesting depth at most 4; at most 10,000 members in total;
total expanded bytes at most 10× the root's size and never more than
50 GB. Decompression is streamed and stops when a bound is reached. Member
paths are normalized and rejected when absolute or escaping (`..`).
Anything skipped is named in the relevant parent's `coverage.gaps`.

### 5.6 Embedded images

Images embedded in documents (PDF figures, DOCX/PPTX/EPUB images, notebook
outputs) become `embedded_image` children and run the image route (D115),
so a chart's labels and a diagram's structure become searchable text with a
locator back to the parent's page and region. Decorative images are skipped
below a size floor (starting value: under 64 × 64 pixels or under 4 KiB),
recorded in the parent's coverage. The parent keeps its `media/` copy and
link for display.

### 5.7 Message exports and mailboxes

A mailbox expands into one child per message; a chat export into one child
per conversation. A conversation child converts to a **dialogue
transcript** — speaker turns with timestamps — the same shape
conversational documents already have, so D131's cross-turn extraction
applies unchanged. There is no separate ingestion path for conversations.

