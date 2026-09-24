# Document metadata and document search (Design)

**Status:** D134, accepted 2026-09-24; binding when merged.
**Analysis:** [format coverage and the conversion architecture](../analysis/format_coverage_and_conversion_architecture.md) §5.
**Refines:** D80 (document filters join typed authority), D96 entity
eligibility (a document's own name is not minted), and the E2 Claimify
instructions (self-references name the document). **Composes with:** D133
([format conversion](format_conversion_design.md)).
**Rejected alternative kept as a proposal:** [document subject entities](../proposals/document_subject_entities.md).

## 1. The problem

Agents ask three kinds of questions about files that the engine cannot answer
today:

- **Find a file:** "find Q3_sales_2025.xlsx", "the audit report from last
  spring".
- **Find files by who and when:** "documents Alice wrote last month", "emails
  from Alice".
- **Find information, limited to certain files:** "everything about Project X
  from emails from Alice".

The data mostly exists. `documents` holds each lineage's title and location,
and every chunk, claim and fact links back to its document. What is missing:

1. **Structured facts about each document** — who wrote it, to whom, when,
   under what title — stored so they can be filtered, not only as text
   inside `document.md`. `GET /documents` pages by status only; retrieval's
   `search` has no document target and no document filters.
2. **A way to search for documents** by name, metadata and content.
3. **Claims about a document that name it.** "This report summarizes the 2025
   audit" cannot be found by the report's name and does not say which report.

## 2. General document metadata

Every document version gets the same **general metadata fields**, whatever
its format. Each format family maps its own native fields onto them, so one
filter ("authors include Alice") works across emails, Word files, PDFs and
chat exports.

| Field | Type | Meaning |
|---|---|---|
| `file_name` | text | The file's name as observed for this version (upload name, connector name, archive member name) |
| `source_path` | text | The source location as observed for this version (the lineage's `source_uri` changes on moves; this keeps each version's) |
| `title` | text | The title the document declares |
| `authors` | people | Who produced it |
| `recipients` | people | Who it was addressed to |
| `created_at` | timestamp | When the source says it was created or sent |
| `modified_at` | timestamp | When the source says it was last modified |
| `language` | text | Detected primary language |
| `thread_ref` | text | The conversation or thread it belongs to, as an opaque key |
| `family` | text | The D133 format family (until the D133 registry ships, a coarse class derived from the stored MIME: `text`, `markdown`, `html`, `pdf`, `image`, `audio`, `video`, `office`, `other`) |

A **people** value is a list of `{name, address}`: `name` is a display name
("Alice Novak"), `address` is an email address or handle
(`alice@acme.com`, `@alice`). Either may be absent. Both are stored as given
and in a normalized form (lower case, accents removed, whitespace collapsed;
addresses also lower-cased) for matching.

**How families map onto the fields.** Each family design (D133 §10.1) states
its mapping; the shipped mappings start from:

| General field | Email | Office / PDF | Chat export | Data file |
|---|---|---|---|---|
| `title` | Subject | title property | conversation title | title property, else none |
| `authors` | From | author / creator | participants who posted | creator property |
| `recipients` | To, Cc, Bcc | — | other participants | — |
| `created_at` | Date sent | created property | first message | created property |
| `modified_at` | — | last-modified property | last message | modified property |
| `thread_ref` | Message-ID and References root | — | conversation ID | — |

A family may record further fields of its own in `extra` (for example an
email's `Reply-To`, a spreadsheet's sheet names). `extra` is returned with
results but is not a general filter; a field that proves useful across
families is promoted to a general field by a design change.

**Where values come from.** The converter reads them from the file and
returns them with its result (`ConversionResult.metadata`); a connector may
add what its source knows (a Drive file's owner, a Slack workspace's user
names). Each field records its provenance, `source` or `connector`. A title
the caller declares at ingest is kept as the version's `title`; a title the
file itself declares fills `title` only when the caller gave none, and is
always recorded as a `document_names` row, so it stays searchable either way.
A field the caller leaves out is not a change: re-sending a file without a
name records no new name. Values are what the source *declares*, not verified facts: an
email's `From` can be forged, and results say where the value came from.

**Storage (D37).** These are compact, query-critical metadata, so they live
in PostgreSQL: `document_metadata` (one row per version), `document_people`
(one row per person per role, indexed on the normalized name and address)
and `document_names` (every name a version was observed under, with the
trigram and BM25 indexes the name channel uses). Schema:
[`postgres_schema_design.md`](postgres_schema_design.md) §6. The mapping
that produced them is versioned separately (`metadata_mapping_version`) from
the E2 extractor.

**Renames.** Identical bytes arriving under a new file name, title or path
create no version (E0's metadata observation). They append a row to
`document_names` for the current version, which `search_documents` reads.
Nothing else changes: the lineage's `title` stays first-write-wins, so a
rename never changes an extraction reuse key, and the version's
`document_metadata` keeps what was observed when it was ingested.

**Deletion.** Normal delete soft-tombstones versions and keeps their rows for
audit (schema §13.1), so the metadata rows stay too; every read in §3 and §4
considers only **live** versions of live lineages, so a deleted document
never matches. Hard forget deletes the lineage's rows from all three tables
(D74) — `document_names` holds file names and paths, so it is source-bearing
like the others.

## 3. `search_documents`

A direct retrieval primitive (bound in [`retrieval_design.md`](retrieval_design.md) §3):

```
search_documents(query?, filters?, k) → documents
```

- **`filters`** use the general fields: `family`, `authors`, `recipients`
  (each matches a name or an address), `created` and `modified` ranges,
  `language`, `thread_ref`, and an explicit `doc_ids` set.
- **Results are documents (lineages), judged by one version.** By default
  each lineage is judged by its **current** version: filters and name
  matching use that version's metadata, and the result returns it. With
  `versions: all`, a lineage matches when **any** live version matches; the
  result returns the newest matching version and lists the other matching
  version IDs. Either way the returned metadata is that of the returned
  version, so a result never shows metadata that did not match.
- **`query`** is matched on two channels, fused by rank (the D9 fusion):
  - **names** — every name the judged version(s) were observed under
    (`document_names`: the name at conversion plus each later metadata
    observation), by trigram and BM25, so a renamed file is found by its new
    and old names, and a partial or misspelled name still finds it;
  - **content** — the existing `chunk_search` (D94), grouped by document:
    each document scores by its best-ranked chunk in the judged version.
    A profiled file's overview and a document's top-level text are ordinary
    chunks, so no second search index is needed.
- With filters only, results are ordered by the judged version's
  `created_at` descending, documents lacking a date last, then by `doc_id`.
  That key moves when a new version becomes current, which would drop or
  repeat rows under a keyset cursor (the `GET /documents` lesson), so the
  cursor also pins the first call's **as-of instant**: every page judges
  versions as they were at that instant, and versions arriving later are not
  considered until a new search starts.
- **Each result** carries the document and version, `file_name`, `title`,
  family and posture, processing status, the general metadata, the overview
  or summary, and how to reach it: its P3 path, `source_open`, and
  `data_query` when it has query tables.
- **People matching is disclosed.** When `authors: ["Alice"]` matches several
  distinct people ("Alice Novak <alice@acme.com>", "Alice Chen
  <achen@x.io>"), the result lists each matched person with a count, so the
  agent can narrow the filter instead of the engine guessing.

It is exposed on the HTTP API, SDK, CLI and MCP: "find the file" is an
intent an agent must be able to discover, like `source_open` (D115) and
`data_query` (D133).

## 4. Document filters on `search`

`search` (and the recipes built on it) accepts a `documents` filter with the
same fields as §3. It restricts results to evidence from matching documents:

- **chunks** — the chunk's document version matches;
- **claims** — at least one live **occurrence** of the claim (a
  `chunk_claims` row) is in a chunk whose document version matches. A claim
  reused across versions (D56) has one occurrence per version, so each
  version's metadata is tested on its own occurrence; the returned evidence
  names the matching occurrence. Claims still carry no copied filter values
  (D80); this refines D80's "join through the origin chunk" to "join through
  occurrences" for document filters;
- **relations and observations** — the fact has at least one live
  supporting claim from a matching document; the returned evidence is limited
  to those claims.

The filter is applied inside the ranked statement, before the top results
are cut (the existing D94 rule), never by filtering a finished top-k.

**Worked example.** "Everything about Project X from emails from Alice":

1. `search_documents(filters: {family: email, authors: ["Alice"]})` — or
   directly the filter below; if several Alices match, the agent narrows.
2. `search(target: claims | chunks | relations, query: "Project X",
   documents: {family: email, authors: ["alice@acme.com"]})`.
3. Every result cites the email it came from.

## 5. Claims that talk about their own document name it

When a passage refers to **its own document** — "this report", "the attached
spreadsheet", "this document", or a data file's profile overview — Claimify
writes the document's name into the claim instead of the bare reference:

> source: "This report summarizes the 2025 audit findings."
> claim: "The report Audit_2025.pdf summarizes the 2025 audit findings."

- **The name used** is the document's title when it has one, otherwise its
  file name, as observed for that version. The extraction header already
  carries the title; it gains the **file name**. The added words pass the
  existing D32 layer-2 check because their tokens appear in the header (the
  check is token membership in the grounding context; the
  `added_context.source_kind: header` tag Claimify records is advisory).
- **The self-reference is marked at the exact words, not inferred later.**
  Claimify returns a new output field, `own_document_name`: the exact text it
  wrote in place of the self-reference (e.g. `Audit_2025.pdf`). The grounding
  gate keeps it only when that text equals one of the document's names, occurs
  **exactly once** in the claim, and does **not** occur as a whole in the
  claim's source span, so the name came from the header rather than the
  passage. Individual words may overlap: in a document titled "Annual
  Report", "this report" shares the word *report* and is still accepted; it then persists the character span as
  `claims.own_document_name_span`. Otherwise the field is dropped with a
  diagnostic and the claim is kept. The span is part of the claim and
  survives D56 reuse with it.
- **Renames re-extract.** The file name is part of the extraction reuse key
  (`e1_chunks_design.md` §7), so a new version with a different file name
  never reuses claims naming the old one. Adding the
  file name changes the header, a stable extraction input (D56), so it and
  the prompt change bump the extractor version: affected documents are
  re-extracted once.
- **Only self-references.** Ordinary claims ("Acme's Q3 revenue was
  €4.2M") never get the file name appended: provenance already links every
  claim to its file, and an identical suffix on every claim degrades
  embeddings (the D129 lesson).
- **Claims are immutable.** A later rename does not rewrite them;
  `search_documents` finds the file by its current name and every version's
  name.
- **Profiles and file cards produce claims this way.** A profile's overview
  becomes "The workbook Q3_sales_2025.xlsx covers EU revenue by region for
  2025", searchable by name. Structure sections still produce none (D133
  §4.5).

## 6. A document's own name is not an entity

Left alone, E3 would treat "Audit_2025.pdf" in such a claim as a name and
mint or resolve an entity for it — and could merge two different files that
share a name. So E3 uses the claim's `own_document_name_span`. A reference whose surface
text is exactly the text at that span is **not minted or resolved**; the
claim is kept, searchable by its text, and the skipped reference is counted
in extraction diagnostics. Only that one reference is skipped:
- a claim without the span is unaffected, so in a document titled "Alice" a
  claim about Alice the person resolves normally;
- if E3 emits more than one reference with that same surface text (e.g.
  "Alice wrote the report Alice"), it cannot tell which one is the document,
  so it skips none, resolves all normally and records a diagnostic — the
  rare cost is a name entity for that file, never a lost person;
- a mention of a different file that shares this file's name has no span
  and resolves normally. This extends D96's eligibility rule ("do not mint
filler nouns", `entity_identity_and_retrieval_design.md` §4.3). Mentions of
*other* files by name are unaffected and resolve as ordinary names.

## 7. Alternatives

| Alternative | Why not |
|---|---|
| Make documents entities, bound to their lineage (the first D134 draft) | Schema, resolution, merge-guard and forget machinery to answer questions metadata filters and document search answer directly. Recorded as a [proposal](../proposals/document_subject_entities.md) with its adoption trigger. |
| Per-family metadata only (email `from`, DOCX `author`, …) | Every query would need to know each family's field names; "documents Alice wrote" would be one query per family. |
| Metadata only as text in `document.md` | Searchable but not filterable; "emails from Alice" would become a text match that also hits every email *mentioning* Alice. |
| Append the file name to every claim | Degrades embeddings and duplicates provenance (§5). |
| Let E3 resolve file names as entities | Same-named files would merge (§6). |

**Cleanup, not a precondition.** The D18-era `documents.document_entity_id`
column is unused by any writer and contradicts this decision; it is removed
in a separate cleanup (its views and graph function are redefined there).
Nothing in this design reads or writes it.

## 8. Tests

- Each shipped family's metadata mapping, from its fixture corpus (per D133
  §10.3), including missing and malformed fields.
- People normalization and matching: display name only, address only, both;
  several people matching one name.
- `search_documents`: exact, partial and misspelled names; filters only;
  names from older versions; people disambiguation in the result.
- Document filters on each `search` target, applied before the top-k cut
  (a matching document ranked below the unfiltered top-k is still returned).
- Self-reference naming: "this report", "the attached spreadsheet", a
  profile overview; a non-self claim gets no name; `own_document_name` is
  dropped when it is not one of the document's names, occurs twice, or the
  whole name is already in the source span (a shared word such as "report"
  in "Annual Report" is accepted); a renamed new version does not reuse old
  self-referencing claims; the title is preferred
  over the file name; grounding accepts the header context.
- E3: a claim naming its own file mints no entity; two same-named files
  create no shared entity; a mention of another file resolves normally; a
  person whose name equals the document's title still resolves; "Alice wrote
  the report Alice" skips nothing.
- A same-byte rename is found by `search_documents` under the new name.
- Deleted versions and lineages never match `search_documents` or
  document filters; hard forget removes metadata and people rows.
- `versions: all` returns the matching version's metadata, never the current
  version's when only an older version matched.
