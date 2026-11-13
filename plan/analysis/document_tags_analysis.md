# Document tags — analysis

**Date:** 2026-11-13. **Outcome:** D156,
[document tags design](../designs/document_tags_design.md).
This analysis is non-binding; it records why the design looks the way it
does and which alternatives were rejected.

## 1. The motivating use

An operator keeps **one memory for several products**: each product's
decision log, design documents and finished ticket outcomes go into the same
deployment. The point of one memory is that the products share people,
infrastructure, vendors and decisions, so the memory should connect them and
show when one product's records contradict another's.

The first concrete case is an agent ticket board whose agents work across
several products. A planning agent asks "has any product already decided how
to restart interrupted worker runs?" (whole memory). An implementing agent
asks "what has this product decided about worker restarts?" (one product).
A reviewer asks "only design decisions, not ticket chatter" (one kind of
record). None of the narrower questions can be expressed today.

## 2. What exists and why it does not answer this

| Existing mechanism | Why it does not answer "only this product" |
|---|---|
| D134 general metadata (`family`, `authors`, `recipients`, dates, `language`, `thread_ref`) | Describes what the **source declares** about itself. A ticket rendered to Markdown and a design document are both `family: markdown`; nothing says which product either belongs to. |
| `extra` | Family-specific source fields, deliberately not filterable (D134 §2). |
| `thread_ref` | One opaque conversation key per version. Reusing it as a product label breaks its meaning for real threads and allows only one value. |
| `doc_ids` filter | Works, but the caller must first know and send every document ID; it does not scale to "all of this product". |
| `source_kind` exact filter on claim search | One value per lineage, fixed at the lineage's first ingest, and only on claim search. |
| `source_ref` | The document's identity (D55). Renaming it makes a different document and forfeits D56 reuse; it cannot also carry labels. |
| The `documents` filter itself | Only on `search`. The context operations agents call most (`facts_context`, `claims_and_sources_context`, `combined_context`) take no document filter (the shipped docs' "not built yet" page says so). |

So two things are missing: a caller-owned label on documents, and a way for
the context operations to filter by documents at all. The second is needed
even without tags.

## 3. Requirements

1. The caller decides the labels; the engine does not infer them.
2. A document can carry several labels (`project:autoboard` and
   `kind:decision`) and can be relabelled at any time.
3. Relabelling is free: no reprocessing, no new version, no change to what
   the memory extracted or believes (the D56 rule).
4. Every read grain agents use can be limited by labels, including facts.
5. Filtering happens before ranking, so a narrow label still returns its
   best results (the D94/D142 rule).
6. A filtered read must not make the memory look more certain than it is:
   contradictions from outside the filter stay visible.

## 4. Where to store labels

**Chosen: on the document lineage only.** Claims, chunks and facts already
trace back to documents (occurrences, supporting claims), so they inherit
labels through joins they already make. Benefits:

- **One source of truth.** A relabel is one small write; nothing downstream
  has to be updated or can drift.
- **Free relabelling.** No claim, chunk, embedding or projection row changes.
- **It matches how knowledge is built.** Every derived item cites documents;
  inheriting their labels follows the same lineage the engine uses for
  evidence.
- **Shared support works naturally.** A fact supported by an Autoboard
  ticket and a Legalit design document matches either label without any
  merging rule.

Because a claim belongs to exactly one lineage (`claims.doc_id`; reuse never
crosses lineages), a claim's labels are simply its document's labels.

**Rejected: copy labels onto claims, chunks and facts.** Faster filters, but
every relabel fans out across every derived row of the document, facts would
need their labels recomputed whenever support changes, and D80 already forbids
copying filter values onto claims. A rebuildable accelerator may be added
later if measurement demands it, as a derivative of `document_tags`, never as
the truth.

**Rejected: labels on versions.** A label says what a document is for, which
an edit does not change. Version labels would make "which product is this"
depend on which version a read selected, and would force a choice of what a
new version inherits.

**Rejected: labels on entities.** Entities are shared across all work by
design; "Ravi" is the same person in two products. Labelling entities would
either split identities or give one entity conflicting labels.

## 5. What a label looks like

**Chosen: free strings, normalized, with a documented `key:value`
convention.** The engine parses nothing.

- *Structured `{key, value}` pairs* were considered. They allow "any
  `project:` value" queries and typed values, but add schema, validation and
  a second matching mode, while the questions in §1 are all exact-match.
  The convention gives most of the readability without the machinery.
- *Prefix or wildcard matching* (`project:*`, `repo:writeitai/*`) was
  considered and left out. `any` lists cover the observed questions, and
  prefix matching would make tag text a hierarchy the engine has to
  interpret. Adoption trigger: agents repeatedly need to select an open-ended
  family of tags that cannot be listed.
- *Case-sensitive tags* were rejected: `Project:Autoboard` vs
  `project:autoboard` is a common, silent mistake, and a label gains nothing
  from case.

## 6. Setting labels

**Ingest adds, never removes.** One lineage can be reached by several
callers; one-shot uploads are identified by their bytes, so the same PDF
uploaded for two products is one lineage. If ingest replaced the label set,
the second upload would silently remove the first product's label. Removal
and full replacement go through an explicit update operation
(`update_document_tags`), which a connector with an authoritative label set
uses with `set`.

A bulk "retag everything matching this filter" write was rejected: it
duplicates `search_documents` paging inside a write and makes one call able
to relabel the whole memory. A loop over `search_documents` results does the
same job visibly.

## 7. Filtering facts

The hard question is what a filtered fact read returns. Three rules were
considered.

**a. Matching evidence only (D134 §4's original rule for relations and
observations).** The fact is included when a matching document supports it,
and all returned evidence is limited to matching documents. Simple, but a
fact contested by another product's document looks uncontested inside the
filter. That misrepresents the memory, and the contradiction across products
is exactly what one shared memory is for.

**b. Chosen: support from the scope, contradictions from everywhere.**
Inclusion as in (a). Supporting evidence from matching documents only;
contradicting evidence and contradiction co-members from every document,
each marked in or out of the filter, with exact two-sided totals. The fact's
value and status are the deployment's one adjudicated answer.

**c. Re-adjudicate facts per filter.** Compute "what is true according to
these documents only". Rejected: it creates several truths in one memory,
adjudication is a write-time process that can call models (the read path
calls no model except the query embedding), and the cost would multiply
with every label combination.

Rule (b) is applied to every `documents` filter, not only to tags, so
filtered fact reads behave one way.

Testimony reads (`claims_and_sources_context`, claim and chunk `search`) stay
strictly filtered. They answer "what did these documents say"; there is no
verdict to contradict.

## 8. Name resolution stays unfiltered

`resolve_entity` takes no document filter. A filtered resolver would answer
"unknown" for an entity well known elsewhere in the memory, which is a
misleading negative. Agents resolve names against the whole memory and scope
the facts and claims they then read.

## 9. Empty results

Tags are free text, so a filter on a misspelled tag matches nothing. The
envelope says whether the filter matched any live document, so an agent can
tell "no document has `project:autobaord`" from "Autoboard's documents say
nothing about this".

## 10. Performance

The existing rule is that eligibility precedes ranking: the filter is a
nomination input, never applied to a finished top-k. The concern is
selectivity:

- A narrow tag (1% of documents) under global nomination plus confirmation
  can exhaust its candidate budget before finding enough matching items. The
  [search projection design](../designs/postgres_p1_search_projection_design.md)
  already records this failure for a 1% document filter. Tags use the
  exact-ID path instead: PostgreSQL enumerates eligible IDs from the tag
  index, and the projection scores only those.
- A broad tag (90%) is cheaper with ordinary nomination and per-candidate
  confirmation.

Choosing the path is a planner decision measured inside D153's shared
eligibility contract, not a public knob. Qualification must include a 1% and
a 90% tag on a large store, both returning complete results within the
operation deadline. Facts become eligible through "has a live supporting
claim in an eligible lineage", which composes with D153's source-qualified
fact helper.

## 11. Cost (D56)

Tags enter no keyed input: not the extraction header, `extraction_input_hash`,
`embedding_text_hash`, the D80 location header, or the Claimify window.
Tagging, retagging and clearing cause no model calls and create no versions.
This must be proven with the provider-call-counting reuse tests, because a
label added to the extraction header "for context" would silently re-extract
every relabelled document.

## 12. Alternatives that keep everything as it is

- **Separate deployments per product.** They remain right for bodies of work
  that must never share entities or facts. For the motivating case they lose
  the point: no shared entities, no facts that cross products, no
  contradictions across products, and one connection and key per product for
  every agent.
- **A `source_ref` prefix filter** (an earlier idea for this use case: encode
  `autoboard/tickets/123` and filter on `autoboard/`). Rejected: `source_ref`
  is identity and cannot change without creating a new document, it allows
  one hierarchy only, and connectors with native IDs (a Drive file ID) cannot
  use it for labels at all.
