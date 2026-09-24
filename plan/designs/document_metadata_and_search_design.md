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
| `title` | text | The title the document declares |
| `authors` | people | Who produced it |
| `recipients` | people | Who it was addressed to |
| `created_at` | timestamp | When the source says it was created or sent |
| `modified_at` | timestamp | When the source says it was last modified |
| `language` | text | Detected primary language |
| `thread_ref` | text | The conversation or thread it belongs to, as an opaque key |
| `family` | text | The D133 format family |

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
names). Each field records its provenance, `source` or `connector`; when both
provide a field, the file's own value wins and the connector's is kept in
`extra`. Values are what the source *declares*, not verified facts: an
email's `From` can be forged, and results say where the value came from.

**Storage (D37).** These are compact, query-critical metadata, so they live
in PostgreSQL: `document_metadata` (one row per version) and
`document_people` (one row per person per role, indexed on the normalized
name and address). Schema: [`postgres_schema_design.md`](postgres_schema_design.md) §6.
Normal delete removes them with the version; hard forget scrubs them (D74).

## 3. `search_documents`

A direct retrieval primitive (bound in [`retrieval_design.md`](retrieval_design.md) §3):

```
search_documents(query?, filters?, k) → documents
```

- **`filters`** use the general fields: `family`, `authors`, `recipients`
  (each matches a name or an address), `created` and `modified` ranges,
  `language`, `thread_ref`, and an explicit `doc_ids` set.
- **`query`** is matched on two channels, fused by rank (the D9 fusion):
  - **names** — `file_name`, `title` and the last segment of the source
    path, for every live version, by trigram and BM25, so a partial or
    misspelled name still finds the file;
  - **content** — a per-document search row holding the profile overview
    (D133 §4.2) or the document's top-level summary (D39), plus the best
    matching chunk of the document from the existing chunk search.
- With filters only, results are ordered by `created_at`, newest first.
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
- **claims** — the claim's origin chunk's document version matches (the
  D80 rule: claims carry no copied filter values; they join through their
  chunk);
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
  carries the title; it gains the **file name**. Both are header facts, so
  the added words are recorded as `added_context` with
  `source_kind: header` and pass the existing D32 grounding check. Adding the
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
share a name. So E3 receives the document's own names (title, file name, and
file name without extension). A subject, object or context reference whose
normalized name equals one of them is **not minted or resolved**: the claim
is kept, searchable by its text, and the skipped reference is counted in
extraction diagnostics. This extends D96's eligibility rule ("do not mint
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
  profile overview; a non-self claim gets no name; the title is preferred
  over the file name; grounding accepts the header context.
- E3: a claim naming its own file mints no entity; two same-named files
  create no shared entity; a mention of another file resolves normally.
- Delete and hard forget remove metadata and people rows.
