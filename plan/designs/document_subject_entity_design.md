# Documents as the subject of claims (Design)

**Status:** D134, accepted 2026-09-23; binding when merged.
**Analysis:** [format coverage and the conversion architecture](../analysis/format_coverage_and_conversion_architecture.md) §5.
**Amends:** D122 (one engine-supplied passage and card that binds without
the resolution cascade), the D18-era `documents.document_entity_id` bridge
(`postgres_schema_design.md`), and D96/D102 entity identity (one entity
origin that is not the name cascade). **Motivated by:** D133 profiles and
file cards ([format conversion design](format_conversion_design.md)).

## Problem

Some claims are about a file, not about the world the file describes:

- a spreadsheet profile: "The workbook Q3_sales_2025.xlsx covers EU revenue
  by region for 2025";
- a file card: "The font file Inter.ttf declares the family Inter";
- ordinary prose: "This report summarizes the 2025 audit findings".

Provenance already links every claim to the document version it came from
(claim → source span → version → document). That answers *where did this come
from*. It does not answer *what is this claim about*: the entity layer has no
entity for the document, so the claim's subject is either an invented name
entity ("Q3 sales workbook") that the resolver may confuse with other
workbooks, or nothing.

A file name alone is not an identity. Names collide (`Book1.xlsx`,
`export (3).csv`), change on rename, and the same name in two folders is two
files.

## Decision

A document can be the subject of a claim. When it is, the claim binds to a
**document entity**: an ordinary entity (D96 — no types) whose identity comes
from the document's lineage, not from name resolution.

### 1. The binding and minting

The existing `documents.document_entity_id` column becomes the
**document-subject binding**. It was a nullable bridge to a D18
"Document-typed" entity; types no longer exist (D96), and the column now
means exactly: *this entity is this document*. It gains a uniqueness
constraint (`UNIQUE (deployment_id, document_entity_id)`), so the binding is
one-to-one.

A document entity is minted the first time a claim from that document takes
the document as its subject (§3). Documents nobody makes claims about never
get one, so millions of ingested documents do not add millions of entities
to entity search and resolution candidate lists.

**Minting is atomic.** In one transaction the resolver locks the `documents`
row (`SELECT … FOR UPDATE`), reads `document_entity_id`, and either reuses
the bound entity or inserts a new entity with its aliases (§2) and sets the
column. A concurrent minter blocks on the row lock and then reuses the
winner's entity. The decision is recorded in `resolution_decisions` with
tier `document_self`.

**Merge guards.** Two entities bound to different documents never merge:
T3/T4 exclude a candidate pair where both are bound, and review tooling
refuses such a merge. A bound entity may absorb an unbound one (the bound
entity survives and keeps the binding); unmerge restores both as D21
defines.

This is distinct from D102's `document_entity_bindings`, which records
per-document name anchors for resolution replay and is unchanged.

### 2. Aliases — the names the document goes by

On mint, E0 writes the document's names as aliases with a new provenance
value, `document_metadata`. Each name is recorded as an **alias
contribution** — (entity, normalized name, provenance, contributing
`doc_id`) — and the searchable `aliases` row exists while at least one
contribution for it survives. Contributions keep sources apart even when two
documents give a merged entity the same name, so forgetting one document
removes only its contribution (§8). The names:

- the file name, with and without its extension;
- the document title, where the format declares one or the profile/card
  heading names one;
- the last segment of the source path, where the connector provides one.

**Renames.** Today E0 treats an upload with identical bytes as a no-op. It
gains a **metadata observation**: identical bytes arriving with a different
name, title or path update the lineage's `title`/`source_uri` without
creating a version, and — when the document has an entity — add the new
names as `document_metadata` aliases. Old aliases remain (the file was known
by them) with their `last_seen` unchanged.

### 3. The self passage — how a claim takes the document as subject

Claimify cites engine-supplied **source passages** by label
(`CandidateClaim.source_refs`), and D122 adds reference cards built from
those passages. Each Selection and Claimify request additionally receives
one engine-supplied **self passage**, labeled `DOCUMENT`, whose text is the
document's names from lineage metadata (title if present, then file name),
and a **self card** pointing at it. Neither counts against D122's card or
passage caps.

- The `DOCUMENT` passage is **metadata, not body**. It may be cited only as
  a supporting reference, never as the origin, and it produces no evidence
  span in `document.md`. Its tokens count as grounded context for the D32
  layer-2 check — they are system-supplied, not model-invented — which is
  the exact grounding exception this design adds.
- When a proposition's subject is the document itself — a profile's
  overview, a file card, or prose referring to itself ("this report") —
  Claimify writes a self-contained claim that names the document as its
  grammatical subject, cites `DOCUMENT`, and sets a new output field,
  **`document_is_subject: true`**. Citing `DOCUMENT` without that field is
  allowed and means only that the document's name gave context (for example
  as an object: "Alice authored Q3_sales.xlsx").
- The grounding gate accepts `document_is_subject: true` only when the claim
  also cites `DOCUMENT` and the claim text contains one of the `DOCUMENT`
  passage's names (after the same normalization aliases use). The gate does
  not parse grammar; which reference is the subject is decided structurally
  in E3 (§4). Otherwise the field is dropped and recorded as
  a grounding diagnostic; the claim itself is kept. The accepted value is
  persisted as **`subject_is_document`** on the claim and survives D56 reuse
  with it.

### 4. Binding in E3

E3's `EntityRef` does not change. When a claim has
`subject_is_document=true`, the resolver considers **only the subject
position**: the `subject` of each relation the claim yields and the entity of
each observation it yields. A subject reference whose normalized `name` or
`surface` equals one of the `DOCUMENT` passage's names binds to the document
entity through §1, without the T0–T4 cascade. Objects and context references
never bind this way, even when they match. If no subject matches, or several
distinct subject references match, nothing binds specially: the references
resolve through the normal cascade and a diagnostic records why.

This is the one amendment to D122's rule that choosing a card never bypasses
resolution: the self card's identity is known from provenance, so there is
nothing to resolve.

### 5. Mentions from other documents go through normal resolution

A different document that mentions the file ("see Q3_sales_2025.xlsx for
the numbers") does not get that file's self passage. Its mention is an
ordinary name that runs the identity cascade. The document entity's
`document_metadata` aliases make it a T0 candidate; T3/T4 decide as for any
entity, and globally T0 still never auto-accepts (D95/D100). Two files with
the same name remain two candidates.

### 6. Claim text is immutable; the entity is the stable link

A stored claim names the document the way that version named itself. A
rename does not rewrite old claims — claims are immutable evidence — but
adds an alias (§2), so a search on the new name reaches the entity and,
through it, every claim about the document.

### 7. What an agent can do with it

`resolve("Q3_sales_2025.xlsx")` returns the document entity with its
observations (the claims about the file). `lookup entity(id)` includes the
document binding, from which the agent reaches the document: P3 stub,
`source_open`, or `data_query` for a profiled data file. The entity turns
"memory knows this file exists" into "the agent can open or query it".

### 8. Lifecycle and forgetting

- **New versions** keep the same document entity (the lineage is the
  identity). Claims from each version bind to it, and the lifecycle rules
  for superseded versions apply to those claims unchanged.
- **Normal deletion** of the document clears the binding with the lineage
  tombstone; its own claims stop being current testimony through the
  existing cascade.
- **Hard forget (D74)** deletes the binding, every alias contribution from
  the forgotten document (and each `aliases` row left with none), and the
  document's claims. If the
  entity is still referenced by claims from other lineages, it survives with
  only the aliases those lineages contributed; its canonical name is
  recomputed from them and its profile cache recomputed from remaining
  evidence (the existing D74 shared-entity rule). If no alias remains, the
  entity is retired. Nothing unique to the forgotten file — its name, title,
  path or profile — survives on the entity.
- **Container children** (D133 §5) are documents; each child can have its
  own document entity. A parent's listing claim ("the archive contains 40
  invoices") binds to the parent's entity.

## Schema changes

Reconciled into [`postgres_schema_design.md`](postgres_schema_design.md):

- `documents.document_entity_id`: now the one-to-one document-subject
  binding, `UNIQUE (deployment_id, document_entity_id)`.
- `alias_provenance` gains `document_metadata`; a new `alias_contributions`
  table records each (entity, normalized name, provenance, contributing
  `doc_id`) for `document_metadata` aliases.
- `resolution_tier` gains `document_self`.
- `claims` gains `subject_is_document boolean NOT NULL DEFAULT false`;
  Claimify's `CandidateClaim` gains the optional `document_is_subject` field.

## Alternatives

| Alternative | Why not |
|---|---|
| File name in claim text only | A string is not an identity: collisions and renames break it; nothing binds the claim to the document. |
| Provenance only | Answers where a claim came from, not what it is about; document-subject claims would float without a holder. |
| Mint a document entity for every document at ingest | Correct identity, but floods entity search and T0 candidate lists at millions of documents with entities nobody talks about. |
| Let the name cascade resolve self-references | Guesses an identity already known from provenance, and invites merging two same-named files. |
| Bind on a `DOCUMENT` citation alone | A citation shows the name gave context, not that the document is the subject: "Alice authored Q3_sales.xlsx" would bind the object. Claimify's `document_is_subject` plus the E3 subject-position rule separates the two. |
| Have the E3 normalizer mark document-self references | A second model judgment; Claimify already decides what the claim is about when it writes it, and E3 only has to read the subject position. |

## Tests

Self-reference in a profile, a file card and prose; `DOCUMENT` cited as an
origin is rejected; a document cited only as an object ("Alice authored
Q3_sales.xlsx") never binds; `document_is_subject` without the document's name in
the claim text is dropped; two same-named files stay two entities and never merge;
concurrent first claims mint one entity; rename through a metadata
observation adds an alias and old claims still reach the entity; a mention
from another document is a candidate but never auto-accepted; a document with
no self-referencing claims mints no entity; hard forget removes the
document's aliases and binding while another lineage's claim keeps a renamed,
scrubbed entity; D56 reuse preserves `subject_is_document` without a second
mint.
