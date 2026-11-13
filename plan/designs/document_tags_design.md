# Document tags (Design)

**Status:** D156, proposed 2026-11-13; accepted and binding when its PR merges.
Accepting the design does not mean it ships; the
[delivery plan](../plans/document_tags.md) says what to build in which order.
**Analysis:** [document tags](../analysis/document_tags_analysis.md).
**Unchosen extension kept as a proposal:** [tag classification rules](../proposals/tag_classification_rules.md).
**Refines:** D134 ([document metadata and search](document_metadata_and_search_design.md))
— adds caller tags to the `documents` filter, lets that filter reach the
context operations, and changes which fact evidence a filtered read returns.
**Amends:** the [open query space](open_query_space_design.md) §3.1 input
schemas and operation versions, and §3.2's view catalog; the
[MCP catalogue](one_key_client_surfaces_design.md) tool list.
**Composes with:** D140 time scope, D142/D153 eligibility before ranking, D54
counting by lineage, D56 reuse, D135 deletion and D74 hard forget.

## 1. The problem

One deployment often holds the documents of several bodies of work: an
operator's decision records, design documents and ticket outcomes for
several products, or a team's specs, meeting notes and support threads. The
memory is shared on purpose. The same people, systems and decisions recur
across that work, and a fact recorded in one place can be contradicted in
another.

Agents still need to ask narrower questions: "what has the Autoboard
project decided about worker restarts?", "only design decisions, not ticket
chatter", "everything except the CI smoke-test project". The engine cannot
express these today:

- D134 metadata is what a **source declares** about itself (authors,
  recipients, dates, language, thread, format family). A ticket rendered as
  Markdown and a design document are both `family: markdown`; nothing says
  which product either belongs to.
- `source_kind` is an exact claim-search filter only, and `source_ref` is a
  document's identity, not a label. Neither can be changed without making a
  new document, and a document can have only one of each.
- The `documents` filter exists only on `search`. The operations agents use
  most (`claims_and_sources_context`, `facts_context`, `combined_context`)
  take no document filter at all, so an agent cannot scope its usual reads.

## 2. The idea in one paragraph

The deployment keeps a **tag registry**: a declared list of tags, each with a
name such as `project:autoboard` and a description of what it means. The
caller attaches registered tags to a **document lineage** (the document over
time, D55); a document can carry several. Assignments are stored in one
place, on the lineage, and nowhere else. Every grain that already traces
back to documents inherits them through that trace: a chunk through its
document, a claim through its occurrences, a fact through its supporting
claims. The existing `documents` filter gains a `tags` selector and is
accepted by the context operations as well as by `search` and
`search_documents`. Tags only select what a read returns. They never change
what the memory extracts, resolves or believes, and changing them costs no
processing.

## 3. Tags

### 3.1 The tag registry

A tag must be **created before it is used**. Each registered tag has:

| Field | Meaning |
|---|---|
| `tag_id` | Stable identity. Assignments point at it, so a rename changes one row. |
| `name` | The label callers and agents use, e.g. `project:autoboard`. |
| `description` | What the tag means and when to apply it, written for agents: "Documents about the Autoboard ticket board product, including its tickets, designs and decisions." |
| `created_at`, `updated_at` | Clocks. |

**Names.** A name is Unicode NFC-normalized, 1–128 characters, with no
whitespace or control characters. It keeps the spelling it was declared
with, and names are unique and matched **case-insensitively**:
`Project:Autoboard` finds the tag declared as `project:autoboard`, and
declaring both is refused as a duplicate. The engine gives a name no
structure; callers are encouraged to use a `key:value` convention because it
keeps a shared memory readable:

| Tag | Description (example) |
|---|---|
| `project:autoboard` | belongs to the Autoboard product |
| `kind:decision` | an accepted decision record, not a proposal |
| `source:ticket` | rendered from a board ticket |

There are no tag groups: the engine does not know that `project:autoboard`
and `project:legalit` are alternatives, and a document may carry both.

**Why a registry rather than free text.** An agent can only filter by tags
it knows exist. The registry is discoverable (`list_tags`, §6), so an agent
reads the deployment's tags and their descriptions instead of guessing
`project:autoboard` versus `proj:autoboard`. Writes and filters that name an
unregistered tag are refused, so a typo is an error instead of a silently
untagged document or an empty answer. A rename is one write and every
document follows it.

**Registry operations.**

- `create_tag(name, description)` declares a tag. A duplicate name
  (case-insensitive) is refused with `conflict`.
- `update_tag(tag, name?, description?)` renames or re-describes a tag.
  Assignments are unaffected. The old name stops resolving at once; there
  are no aliases, so filters and saved queries that use the old name fail
  with `unknown_tag` until they are updated.
- `delete_tag(tag)` removes the tag and all its assignments in one write.
- `list_tags(query?)` returns every tag with its `tag_id`, name and
  description, optionally narrowed by a substring of the name or
  description. It returns no document counts: discovery needs the
  vocabulary, and an unused tag is reported by the filtered read itself
  (§4.5).

Wherever an operation takes a `tag`, it accepts the name or the `tag_id`.

**Limits** (starting points, to be measured): 10,000 tags per deployment;
descriptions up to 2,000 characters; 64 tags per lineage; 32 tags in each
list of a filter (§4.1).

### 3.2 Assignments belong to the lineage, not to a version

Tags describe what a document *is for*, which does not change when its text
is edited. Assignments are stored per lineage (`doc_id`), so every version
of the document carries the lineage's current tags, including old versions
reached through a D140 time scope. Assignments have no history of their own:
a filter always uses the tags the lineage has when the read runs. A read
that asks about March uses today's tags to choose which documents to look
in.

Because claims are owned by one lineage (`claims.doc_id`; D56 reuse never
crosses lineages), every occurrence of a claim lies in the same lineage. A
claim therefore has exactly one tag set: its document's. The occurrence
subtleties of D134's version-level filters (a claim reused across versions
whose metadata differ) cannot arise for tags.

Every assignment comes from a caller (a person, connector or agent calling
ingest or `update_document_tags`). Assignments record no origin; the
[classification rules proposal](../proposals/tag_classification_rules.md)
adds one if rule-based assignment is ever accepted.

### 3.3 Setting and changing tags

Tags are assigned in two ways. Both refuse the whole request with
`unknown_tag`, naming every unregistered tag, before anything is stored.

**At ingest.** `ingest` takes an optional `tags` list. The listed tags are
**added** to the lineage's tags; ingest never removes a tag. Omitting `tags`
changes nothing. Adding is the safe meaning because one lineage can be
reached by several callers: a one-shot upload is identified by its bytes,
so two callers who upload the same file for different projects reach the
same lineage. If ingest replaced the tag set, the second upload would
silently remove the first caller's `project:` tag. With adding, the document
ends up tagged for both.

Tags never cause a version. An ingest that would otherwise be a no-op
resubmission of the lineage's latest live version (D134's
metadata-observation rule) stays one when it carries tags: the tags are
added, nothing is processed, and the existing version is returned with
`created=False`. Whether an ingest creates a version is decided exactly as
today (bytes, D140 `version_key`, reversion and D135 deletion rules); tags
take no part in it.

**By update.** `update_document_tags(doc_id, add?, remove?)` adds and
removes tags on one live lineage in one atomic write, or
`update_document_tags(doc_id, set)` replaces the lineage's whole set (`set`
excludes `add`/`remove`; an empty `set` clears its tags). Removing
a tag the lineage does not have is not an error. The result returns the
lineage's tags after the write. A connector that knows the authoritative
tag set of a source uses `set`; an agent that classifies a document uses
`add`.

Retagging many documents is a loop: find them with `search_documents` (any
filter, paged) and update each. There is no bulk retag-by-filter operation;
it would duplicate `search_documents` paging inside a write.

Tags on a deleted lineage cannot be changed (`not_found`).

### 3.4 Tags never reach processing

This is the D56 rule ("a new version costs only its edit") applied to
tags. Neither tag names, descriptions nor assignments are part of any keyed
input: not the extraction header, not `extraction_input_hash`, not
`embedding_text_hash`, not the D80 location header, not the Claimify
reference window. Creating, renaming, describing, assigning, removing or
deleting tags therefore causes no conversion, embedding, extraction,
resolution or adjudication work, and never creates a version. Tag writes
take effect for the next read.

The same separation keeps the memory's content independent of how it is
labelled. Tagging a document `project:legalit` cannot change a claim
extracted from it, an entity it mentions, or a fact it supports.

### 3.5 Deletion and hard forget

Normal deletion (D135) keeps a lineage's assignment rows for audit, like its
D134 metadata rows; a deleted lineage never matches a filter and its tags
are never returned. D74 hard forget deletes the
lineage's assignment rows. The registry itself is the deployment's
vocabulary, not content from any source, and is unaffected by forgetting a
document; a tag whose name or description must go is removed with
`delete_tag`.

## 4. Filtering by tags

### 4.1 The `tags` selector

The D134 `documents` filter object gains one field:

```text
documents: {
  ...D134 fields (family, authors, recipients, created/modified ranges,
               language, thread_ref, doc_ids),
  tags: {
    all?:  [tag, ...]   // the lineage has every one of these
    any?:  [tag, ...]   // the lineage has at least one of these
    none?: [tag, ...]   // the lineage has none of these
  }
}
```

A tag is given by name (matched case-insensitively) or `tag_id`. Any
unregistered tag fails the request with `unknown_tag`, naming it, before
retrieval. At least one list must be present and non-empty when `tags` is
given, and duplicates within a list are refused. Lists combine with AND,
and `tags` combines with the other D134 fields with AND, exactly as those
fields combine with each other. Examples:

| Question | Filter |
|---|---|
| Autoboard only | `tags: {all: ["project:autoboard"]}` |
| Decisions from Autoboard or Legalit | `tags: {all: ["kind:decision"], any: ["project:autoboard", "project:legalit"]}` |
| Everything except the smoke-test project | `tags: {none: ["project:ci-smoke"]}` |
| Autoboard emails from Alice | `family: ["email"], authors: ["alice@example.com"], tags: {all: ["project:autoboard"]}` |

A tag is tested on the lineage; D134's version-level fields are still tested
on the version, as before. Untagged lineages match `none` lists and fail
`all` and `any` lists. There is no wildcard or prefix matching on tags (see
the analysis for why).

### 4.2 Where the filter is accepted

The same `documents` filter, with `tags`, is accepted by:

- `search_documents` (D134 §3), through its existing filter shape: the
  SDK/HTTP `filters` object and the MCP tool's flat filter fields each gain
  `tags`. Its results also return each lineage's tags;
- `search` for chunks, claims, relations and observations (D134 §4);
- the context operations `claims_and_sources_context`, `facts_context` and
  `combined_context` ([open query space §3.1](open_query_space_design.md#31-surface-entry-points-and-assured-core)),
  which gain an optional `documents` argument (the same object `search`
  takes). `combined_context` passes it unchanged to both children.

`resolve_entity` does not take a document filter. The deployment has one
entity space; a name that is known in the memory resolves the same way
whatever the question's scope. Scoping name resolution would turn "Ravi is
known, but not in Autoboard's documents" into a misleading
`unknown_entity`. An agent resolves the name once and then scopes the
facts or claims it reads about that entity.

When `entity_ids` and `documents` are both given, an item must satisfy both:
associated with a supplied entity, **and** coming from a matching document.

### 4.3 How each grain inherits the filter

| Grain | Included when |
|---|---|
| Chunk | its document version is live and matches (tags tested on the lineage) |
| Claim | it has a live occurrence in a matching document version |
| Relation or observation (fact) | at least one live **supporting** claim comes from a matching document |

When a D140 time scope is also given, both conditions hold on the **same**
occurrence: a claim qualifies through an occurrence in a version that both
matches the filter and is selected by the time scope, and a fact qualifies
through a supporting claim that does. Independent tests (a matching
document in one version, an in-force version elsewhere) are not enough. The
time scope keeps applying to out-of-filter evidence (§4.4) as it does today.

The filter decides which items are **included**. It never re-decides
anything: a fact's value, validity window, support state and contradiction
status are the deployment's single adjudicated answer, the same as in an
unfiltered read. A filter selects which parts of the memory you look at; it
does not create a second version of the truth.

### 4.4 Fact evidence under a filter: support from the scope, contradictions from everywhere

A filtered fact read must answer "what do these documents say is true?"
without hiding that the rest of the memory disagrees. The rule, for
`facts_context`, the facts member of `combined_context`, and `search` over
relations and observations:

- **Supporting evidence** is drawn only from matching documents. That is
  what the scope asks for.
- **Contradicting evidence** is drawn from **every** document the read's
  time scope selects. Matching
  documents are allocated first within the existing evidence budget, then
  the rest. Each evidence association states whether its document matches
  the filter (`in_documents_filter: true | false`).
- **Contradiction co-members** (the competing facts of a contested fact)
  are returned even when none of their support matches the filter, marked
  the same way.
- **Totals stay exact and become two-sided.** Each `evidence_totals[]` row
  keeps its deployment-wide count of supporting or contradicting lineages
  (D54: distinct lineages) and adds the count that matches the filter.

Example. Autoboard's decision log says "worker runs restart after 12 hours";
a later Legalit operations note says "worker runs restart after 6 hours". A
read filtered to `project:autoboard` returns the 12-hour fact with its
Autoboard support, marked contested, with the Legalit note as an
out-of-filter contradiction and the 6-hour fact as an out-of-filter
co-member. An agent working on Autoboard learns, in the same answer, that
another part of the memory disagrees. Hiding that would make the filtered
answer look more certain than the memory is.

This replaces D134 §4's former rule that a filtered fact's returned evidence
is limited to matching claims; the rule above applies to every `documents`
filter, with or without tags.

Testimony reads are different. `claims_and_sources_context` and `search` over
claims and chunks return what matching documents **said**, so their results
are strictly limited to matching documents. Claims are testimony, not
verdicts; there is no "contradiction" of a claim to disclose.

### 4.5 Saying what the filter matched

Because every tag in a filter must be registered, a misspelled tag fails
with `unknown_tag` instead of returning nothing. A registered tag can still
legitimately match no live document (it was just created, or its documents
were deleted), and D134's other fields can match nothing too. The envelope of
every filtered read echoes the validated filter and states whether at least
one live document matched it (`documents_filter: {filter, matched_any_document}`).
An empty result therefore distinguishes "no document matches this filter"
from "the matching documents say nothing about this". Either may return
`known_empty`, with an explanation naming which case occurred, only when
the read completed. A read stopped by a D142/D153 work bound, a D143 layer
exclusion or an unfinished page keeps its existing incomplete-result
semantics (`boundary`, or a continuation), whatever the filter matched.

### 4.6 Eligibility before ranking

D134 and the open query space already forbid filtering a finished top-k.
Tags follow the same rule through the same execution path: the filter is an
eligibility input to nomination, never a filter on ranked output (D142's
nominate–confirm–continue loop, with D153's shared eligibility helpers).

The eligible document set comes from one indexed table (§5). For a narrow
filter (a tag on 1% of documents), PostgreSQL enumerates the eligible
claim, chunk or fact IDs and the projection scores only those, the pattern
§3.1 already uses for supplied entity IDs. For a broad filter, ordinary
nomination with per-candidate confirmation is cheaper. Choosing between the
two physical paths is a measured planner decision inside D153's contract,
not a public parameter. Fact eligibility under a filter is "has a live
supporting claim from an eligible lineage", evaluated through D153's
source-qualified fact helper. Tags are not copied into P1 projections or
onto claims and facts; a measured accelerator may be added later only as a
rebuildable derivative of `document_tags`, never as a second source of
truth.

## 5. Storage

```sql
-- D156: the deployment's tag registry. Names keep their declared spelling and are unique
-- case-insensitively.
CREATE TABLE tags (
  deployment_id uuid NOT NULL,
  tag_id        uuid NOT NULL,
  name          text NOT NULL,              -- NFC, 1–128 chars, no whitespace/control
  description   text NOT NULL,              -- up to 2,000 chars; written for agents
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, tag_id),
  CHECK (char_length(name) BETWEEN 1 AND 128),
  CHECK (char_length(description) <= 2000)
);
CREATE UNIQUE INDEX ux_tags_name ON tags (deployment_id, lower(name));

-- D156: tag assignments on a document lineage. Stored only here; chunks, claims and facts
-- inherit through their documents. Not versioned and never part of a reuse key, so a tag
-- write causes no processing.
CREATE TABLE document_tags (
  deployment_id uuid NOT NULL,
  doc_id        uuid NOT NULL,
  tag_id        uuid NOT NULL,
  assigned_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, doc_id, tag_id),
  FOREIGN KEY (deployment_id, doc_id) REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, tag_id) REFERENCES tags (deployment_id, tag_id) ON DELETE CASCADE
);
CREATE INDEX ix_document_tags_tag ON document_tags (deployment_id, tag_id, doc_id);
```

The registry's unique index makes case-insensitive names a database rule,
not only an API check. The assignment primary key answers "which tags does
this document have"; the tag index answers "which documents have this tag",
the side every filter starts from. Deleting a tag cascades to its
assignments. The per-lineage and per-deployment limits are enforced by the
write path.

The SQL query facade exposes both as views
([open query space §3.2](open_query_space_design.md#32-memory_v1-view-catalog)):
`memory_v1.tags_current` (one row per registered tag) and
`memory_v1.document_tags_live` (one row per assignment on a live lineage,
carrying `tag_id` and the current `name`), so saved
queries and `query_sql` can join on tags like any other document
coordinate.

## 6. Surfaces

| Surface | Change |
|---|---|
| HTTP | `GET /tags` (list, optional `query`); `POST /tags` (create); `PATCH /tags/{tag}` (rename/describe); `DELETE /tags/{tag}`. `POST /ingest` accepts `tags`. `POST /documents/{doc_id}/tags` with `{add?, remove?}` or `{set}`; returns `{doc_id, tags}`. `GET /documents` page items and `search_documents` results return `tags`. Every `documents` filter accepts `tags`. |
| Python SDK | `client.list_tags()`, `create_tag()`, `update_tag()`, `delete_tag()`; `client.ingest(..., tags=[...])`; `client.update_document_tags(doc_id, add=..., remove=...)` or `set=...`; `filters=DocumentSearchFilters(tags=...)` on `search_documents`; `documents={"tags": {"all": [...]}}` on `search` and the context methods. |
| CLI | `remember tags list`, `remember tags create NAME --description TEXT`, `remember tags update TAG [--name] [--description]`, `remember tags delete TAG`; `remember ingest FILE --tag T` (repeatable); `remember documents tag DOC_ID --add T --remove T` or `--set T`; `--tag T` (all), `--any-tag T` and `--not-tag T` on query commands. |
| MCP | Read tool `list_tags`; write tools `create_tag`, `update_tag`, `delete_tag` and `update_document_tags`; `ingest` gains `tags`; `search_documents` gains a flat `tags` field beside its other filter fields; `claims_and_sources_context`, `facts_context` and `combined_context` gain the `documents` argument. Each changed tool raises its `tool_version`. The context tools' descriptions tell agents to call `list_tags` before filtering by tag. |
| Operations | `claims_and_sources_context@5`, `facts_context@5`, `combined_context@6` (new optional input and the §4.4/§4.5 response fields); `surface_manifest_hash` rolls. |

## 7. What tags are not

- **Not entities or facts.** A tag is never extracted, resolved or turned
  into a graph node. The memory does not "know" that `project:autoboard` is
  the Autoboard product; the tag's description says so for agents.
- **Not a way to split the memory.** Entities, claims, facts and
  adjudication remain shared across every tag. Bodies of work that must
  never share entities or facts belong in separate deployments, as before.
- **Not metadata about the source.** D134 fields say what a source declares
  (who wrote it, when); tags say how the caller organizes it. Both filter
  through the same object, and neither overwrites the other.
- **Not assigned by the engine.** This design has no automatic tagging;
  every assignment comes from a caller. Rule-based assignment is the
  [classification rules proposal](../proposals/tag_classification_rules.md).

## 8. Tests

- Registry: create, rename, re-describe, delete; duplicate names refused
  case-insensitively; declared spelling returned; invalid names (whitespace,
  control characters, empty, over-long) and limits refused; a rename keeps
  every assignment and makes the old name `unknown_tag`; delete removes all
  assignments.
- Strictness: ingest, `update_document_tags` and every filter naming an
  unregistered tag fail with `unknown_tag` listing it, and ingest stores
  nothing.
- Ingest adds and never removes; a no-op resubmission with new tags creates
  no version and returns `created=False`, while a new `version_key` or a
  resubmission after deletion still versions exactly as without tags; two
  one-shot uploads of the same bytes with different tags leave both tags on
  the lineage.
- `update_document_tags`: add/remove atomically; `set` replaces; empty `set`
  clears; removing an absent tag succeeds; deleted lineage is `not_found`.
- **D56 reuse:** creating, renaming and deleting tags, and tagging,
  retagging and clearing a lineage with ready versions make zero conversion,
  embedding, Selection and Claimify calls and create no version
  (provider-call counts per stage, as in `test_reuse_lifecycle.py`). Two
  versions ingested with different tags reuse exactly what they would reuse
  with no tags.
- Each `all` / `any` / `none` combination, by name and by `tag_id`, combined
  with D134 fields and with `entity_ids`.
- Each grain of §4.3, on every surface that accepts the filter, applied
  before the cut: a matching item ranked below the unfiltered top-k is
  returned; a filter matching 1% and one matching 90% of a large store both
  return complete results within the operation deadline.
- §4.4: supporting evidence only from matching documents; out-of-filter
  contradictions and co-members returned and marked; two-sided totals equal
  hand-counted distinct lineages; the fact's value and support state are
  byte-identical to an unfiltered read.
- §4.5: a registered tag on no live document and a filter matching documents
  with no relevant content return distinguishable `known_empty`
  explanations.
- D140: a time-scoped read selects old versions of a tagged lineage using
  the lineage's current tags.
- D140 with tags on the same occurrence: a fact whose tagged support is out
  of force and whose in-force support is untagged is not returned.
- A tag-filtered read stopped by a work bound reports `boundary`, not
  `known_empty`.
- Deletion keeps assignment rows but never matches or returns them;
  hard forget removes them and leaves the registry unchanged.
- `resolve_entity` results are unaffected by any tag state.
