# Documents as the subject of claims (Design)

**Status:** D134, accepted 2026-09-23; binding when merged.
**Analysis:** [format coverage and the conversion architecture](../analysis/format_coverage_and_conversion_architecture.md) §5.
**Amends:** D122 (source reference cards: one card binds without resolution)
and D96/D102 entity identity (one entity origin that is not a name
cascade). **Motivated by:** D133 profiles and file cards
([format conversion design](format_conversion_design.md)).

## Problem

Some claims are about a file, not about the world the file describes:

- a spreadsheet profile: "The workbook Q3_sales_2025.xlsx covers EU revenue
  by region for 2025";
- a file card: "The font file Inter.ttf declares the family Inter";
- ordinary prose: "This report summarizes the 2025 audit findings".

Provenance already links every claim to the document version it came from
(claim → source span → version → document). That answers *where did this come
from*. It does not answer *who or what is this claim about*: the entity layer
has no entity for the document, so the claim's subject is either an invented
name entity ("Q3 sales workbook") that the resolver may confuse with other
workbooks, or nothing at all.

A file name alone is not an identity. Names collide (`Book1.xlsx`,
`export (3).csv`), change on rename, and the same name in two folders is two
files.

## Decision

A document can be the subject of a claim. When it is, the claim binds to a
**document entity**: an ordinary entity (D96 — no types) whose identity comes
from the document's lineage (`doc_id`), not from name resolution.

### 1. Minting — when, and only when, a claim needs it

A document entity is minted the first time a claim from that document takes
the document itself as its subject. Documents nobody makes claims about never
get one, so millions of ingested documents do not add millions of entities to
entity search and resolution candidate lists.

A **document-subject binding** records `(entity_id, doc_id)`, one-to-one.
It is the only thing that makes an entity a document entity; there is no
entity type.

### 2. Aliases — the names the document goes by

On mint, and whenever a new version or rename is observed, E0 writes aliases
with provenance `document_metadata`:

- the file name, with and without its extension;
- the document title, where the format declares one or the profile/card
  heading names one;
- the last segment of the source path, where the connector provides one.

Renaming a file adds an alias; old aliases remain (the file was known by
them). Aliases feed search and candidate generation like any other alias.

### 3. The self card — how a claim takes the document as subject

D122 gives Claimify a bounded set of **source reference cards**: things the
source introduces, with passages supporting them. Every Selection request
additionally receives one **self card** for the document being processed.
Its label is the document's current name (title if present, else file name)
and its support is the document header Selection already sees. It does not
count against D122's per-request card cap.

When a proposition's subject is the document itself — a profile's
overview, a file card, or prose that refers to itself ("this report", "the
attached spreadsheet" inside the spreadsheet's own profile) — Claimify
writes a self-contained claim naming the document and cites the self card.
The claim text is grounded because the name appears in the header or the
profile heading (D32 layer-2 token check unchanged).

E3 carries the self-card citation into the `EntityRef` as a structured
**document-self** marker (not inferred from text). The resolver binds a
document-self reference directly to the document entity — minting it if
absent — without running the T0–T4 cascade. This is the one amendment to
D122's rule that choosing a card never bypasses resolution: the self card's
identity is known from provenance, so there is nothing to resolve.

### 4. Mentions from other documents go through normal resolution

A different document that mentions the file ("see Q3_sales_2025.xlsx for
the numbers") does not get the self card for it. Its mention is an ordinary
name that runs the identity cascade. The document entity's file-name
aliases make it a T0 candidate; T3/T4 decide as for any entity, and globally
T0 still never auto-accepts (D95/D100). Two files with the same name remain
two candidates.

### 5. Claim text is immutable; the entity is the stable link

A stored claim names the document the way that version named itself. A later
rename does not rewrite old claims — claims are immutable evidence — but
adds an alias to the entity, so search on the new name still reaches the
entity and, through it, every claim about the document.

### 6. What an agent can do with it

`resolve_entity("Q3_sales_2025.xlsx")` returns the document entity with its
observations (the claims about the file) and its document binding. From the
binding the agent reaches the document: P3 stub, `source_open`, or
`data_query` for a profiled data file. The entity is the handle that turns
"memory knows this file exists" into "the agent can open or query it".

### 7. Lifecycle

- **New versions** keep the same document entity (the lineage is the
  identity). Claims from each version bind to it as usual, and the lifecycle
  rules for superseded versions apply to those claims unchanged.
- **Forgetting the document** retires the document-subject binding and the
  document's own claims through the existing cascade. The entity remains if
  claims from other documents still reference it, like any entity whose
  source of introduction was forgotten.
- **Container children** (D133 §5) are documents; each child can have its
  own document entity. A parent's listing claim ("the archive contains 40
  invoices") binds to the parent's entity.

## Alternatives

| Alternative | Why not |
|---|---|
| File name in claim text only | A string is not an identity: collisions and renames break it, nothing binds the claim to the document. |
| Provenance only | Answers where a claim came from, not what it is about; document-subject claims would float without a holder. |
| Mint a document entity for every document at ingest | Correct identity, but floods entity search and T0 candidate lists at millions of documents with entities nobody talks about. |
| Let the name cascade resolve self-references | Guesses an identity already known from provenance, and invites merging two same-named files. |

## Tests

Self-reference in a profile, a file card and prose; two same-named files in
different folders stay two entities; rename adds an alias and old claims
still reach the entity; a mention from another document is a candidate but
never auto-accepted; a document with no self-referencing claims mints no
entity; forget of the document retires the binding while another document's
claim about it keeps the entity; version reuse (D56) preserves the binding
without a second mint.
