# Effective time, section keys, and section-level references (D140)

**Status:** binding design. **Decision:** [D140](../../decisions.md#d140-declared-effective-periods-section-keys-and-version-aware-references).
**Analysis:** [version effective time and section references](../analysis/version_effective_time_and_section_references.md).
**Delivery order:** [effective time delivery plan](../plans/effective_time_and_section_references.md).

This design gives the engine five general capabilities:

1. a caller can declare **when each version of a document is in force** (its *effective
   periods*);
2. retrieval can read document text **as of a date**, using the time scope that facts
   already use;
3. a section can carry a **stable key** that identifies it across versions;
4. a new version **reuses the processing of its unchanged chunks** even when its source
   timestamp changed;
5. **cross-references** can run from a section of one version to a section of another
   document, pinned to a target version or floating to whichever version is in force, and a
   caller can supply them already resolved.

A running example that has nothing to do with law: *the Travel Policy* of a company.
Edition 1 is in force from 2024-01-01. Edition 2, published on 2025-11-15, is in force from
2026-01-01. Its section 4 `{#per-diem}` changes the daily allowance; section 7
`{#approvals}` says "approvals follow section 3 of the Expense Policy". The Czech statute
book is the corpus that motivated the work (see the analysis §2); it uses exactly the same
mechanisms.

## 1. Concepts

- **Lineage and version** — unchanged from D55: a lineage (`doc_id`) is the logical document
  over time; a version is one immutable snapshot of its bytes.
- **Effective period** — a half-open interval `[effective_from, effective_until)` during
  which a version's text is *in force* according to its publisher. It is **declared by the
  caller**; the engine never infers it. It is distinct from three other times the engine
  already keeps:

  | Time | Meaning | Where |
  | --- | --- | --- |
  | ingest (system) time | when the engine received the version | `document_versions.ingested_at` |
  | source time | when the source says the snapshot was authored or modified | `source_modified_at` → claim `asserted_at` (D41/D55) |
  | claim validity | the period a sentence says something holds for ("in 2024 …") | `claims.claim_valid_*` (D41) |
  | fact window | the adjudicated period a fact holds | `valid_from`/`valid_until` (D118) |
  | **effective period** | when the publisher says the version's text is the one in force | new (§2) |

  Example: edition 2 of the Travel Policy was *modified* on 2025-11-10, *ingested* on
  2025-11-15, and is *in force* from 2026-01-01.
- **Periodised lineage** — a lineage with at least one live declared period. Only
  `snapshot` lineages can be periodised (§2.4).
- **Section key** — a caller- or source-chosen identifier for a section, stable across
  versions (`per-diem`, `approvals`, `par_5`).
- **Text origin time** — for a chunk, the source time of the earliest live version of the
  same lineage in which the identical chunk (same text and same neighbours) appeared (§5).

## 2. Effective periods

### 2.1 Data model

```sql
CREATE TABLE document_effective_periods (
  period_id       uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL,
  doc_id          uuid NOT NULL,          -- lineage, denormalized for per-lineage ordering
  version_id      uuid NOT NULL,          -- the version whose text is in force
  effective_from  timestamptz NOT NULL,   -- start, inclusive
  effective_until timestamptz,            -- explicit end, exclusive; NULL = "until the next declared start" (§2.2)
  declared_at     timestamptz NOT NULL DEFAULT now(),
  declared_by     text NOT NULL,          -- 'ingest' | 'period_api' (which write path)
  retracted_at    timestamptz,            -- set when a correction replaces this declaration
  retracted_by_period_id uuid,            -- the replacing declaration, if any
  CHECK (effective_until IS NULL OR effective_until > effective_from),
  FOREIGN KEY (deployment_id, version_id) REFERENCES document_versions (deployment_id, version_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, doc_id)     REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE
);
-- one live declaration per start instant per lineage
CREATE UNIQUE INDEX ux_effective_periods_live_start
  ON document_effective_periods (deployment_id, doc_id, effective_from) WHERE retracted_at IS NULL;
CREATE INDEX ix_effective_periods_version
  ON document_effective_periods (deployment_id, version_id) WHERE retracted_at IS NULL;
```

Rows are append-only in meaning: a correction sets `retracted_at` on the old row and inserts
a new one in the same transaction (the ledger pattern of D33/D54 — every past declaration
stays readable). A version can have **several** live periods: a text that was replaced and
later restored (a reverted policy clause, a statute provision restored by a court) is one
version in force in two periods, because identical bytes in one lineage are one version
(D55 `UNIQUE (doc_id, content_hash)`).

### 2.2 The derived in-force interval

The engine never stores a derived end. The **in-force interval** of a live period `p` is:

- start = `p.effective_from`;
- end = `p.effective_until` if declared; otherwise the smallest `effective_from` of any
  other live period of the same lineage that is greater than `p.effective_from`; otherwise
  open (`NULL`).

A read-only view computes it with one window function over the lineage's live periods
(ordered by `effective_from`, `lead(effective_from)`), restricted to live, ready,
non-deleted versions:

```sql
CREATE VIEW memory_v1.document_effective_periods_live AS
SELECT p.deployment_id, p.doc_id, p.version_id, p.period_id,
       p.effective_from,
       COALESCE(p.effective_until,
                lead(p.effective_from) OVER (PARTITION BY p.deployment_id, p.doc_id
                                             ORDER BY p.effective_from)) AS effective_until,
       (p.effective_until IS NOT NULL) AS until_declared
FROM document_effective_periods p
JOIN memory_v1.documents_live d ON (d.deployment_id, d.doc_id) = (p.deployment_id, p.doc_id)
JOIN document_versions v ON (v.deployment_id, v.version_id) = (p.deployment_id, p.version_id)
WHERE p.retracted_at IS NULL AND v.deleted_at IS NULL;
```

Consequences a reader should check against the example:

- Declaring edition 2 from 2026-01-01 needs **no write to edition 1**: edition 1's interval
  becomes `[2024-01-01, 2026-01-01)` automatically.
- Versions may be declared in any order (archives are back-filled out of order).
- A **gap** (the policy withdrawn on 2027-06-30 with no successor) is an explicit
  `effective_until` on the last period. A later re-issue simply declares a new period.
- An explicit end is never overridden by a later start; if a declared end lies *after* the
  next declared start, the two periods overlap and both are in force in the overlap (a
  transition period where both editions apply). Overlap is legal; an exact duplicate start
  is not (the unique index).
- Deleting a version (D135) removes its periods; neighbours' derived ends recompute.

### 2.3 Writing periods

- **At ingest.** `POST /ingest` (and SDK `ingest`, MCP `ingest`) gain optional
  `effective_from` and `effective_until` (timezone-aware; stored in UTC). They require
  `source_kind`/`source_ref` (a lineage) and `versioning_mode = snapshot`.
  - A new version: the period is inserted in the version's creating transaction.
  - Identical bytes (D55 no-op; no new version): the declaration is recorded **on the existing
    version** — an identical text coming into force again adds a period. This refines D55's
    metadata-no-op clarification: a period is not snapshot metadata and is not extraction
    input, so adding one never rewrites anything that derived text depends on.
  - A declaration identical to a live one (same version, same start, same end) is a no-op.
    A declaration with the same start as a live period of **another** version of the lineage
    is rejected with `409 Conflict` and nothing is written.
- **After ingest.** `PUT /documents/{doc_id}/versions/{version_id}/effective-periods` replaces
  the live period set of one version with the given set (each `{effective_from,
  effective_until?}`), retracting removed declarations and inserting new ones atomically;
  the SDK method is `set_effective_periods`. This is how a repeal, an expiry or a corrected
  date is recorded. It requires `memory:write`.
- Periods never trigger reprocessing: they are not part of any extraction, embedding or
  structure input (§5 keeps them out of every key).

### 2.4 Interaction with versioning modes, currency and the current pointer

- **Only `snapshot` lineages accept periods.** `living` means "the newest version is the
  standing statement" (D55); a declared period would be a second authority over the same
  question. A request declaring a period on a `living` lineage is rejected (`422`). A
  `snapshot` lineage that is already periodised keeps accepting ingests without a period;
  such a version has no in-force interval until one is declared (§3 explains that it is then
  not selected by time scopes).
- **Testimony currency is unchanged.** In `snapshot` mode every version's claims stay current
  testimony (D54); effective periods do not flip currency. Currency answers "should this
  claim count as evidence"; the period answers "was this text in force at T". Evidence
  counts stay per distinct lineage, so a statute's many versions still count once.
- **`current_version_id` keeps its D55 meaning** (the newest observed version) for every
  surface that has no time scope (P3 paths, lineage listings, the document's `title`). Time-
  scoped reads (§3) select versions through periods instead of the pointer.
- **Future-dated versions.** A version whose periods all start after now is stored,
  processed and searchable with `at`/`overlap` scopes at future instants, but invisible to
  `current` until its start passes. No scheduler is needed: selection is evaluated at read
  time.

## 3. Time-scoped retrieval

### 3.1 One time vocabulary

Every read operation that returns document text or text-derived claims gains the optional
`time` argument with the existing modes (`src/remember/models.py` `TemporalScope`, MCP
`_TIME_SCHEMA`): `current` (default), `at`, `overlap`, `history`. The operations:

| Surface | Change |
| --- | --- |
| chunk and claim search (HTTP `POST /search/chunks`, `POST /search/claims`; SDK `search_chunks`, `search_claims`) | `SearchRequest.time` |
| `claims_and_sources_context` (MCP, HTTP, SDK) | `time` argument |
| `search_documents` (MCP, HTTP, SDK) | `time` argument (§3.4) |
| `adjacent_chunks` and source-passage hydration | accept any live, ready version's chunk (§3.3) |
| `document_references`, `section_history` (new, §6) | `time` argument |

### 3.2 Which versions a scope selects

For a **periodised lineage**, a version is selected when one of its live in-force intervals
`[s, e)` satisfies (NULL `e` = open):

| Mode | Condition |
| --- | --- |
| `current` (evaluation instant `now`) | `s <= now AND (e IS NULL OR e > now)` |
| `at T` | `s <= T AND (e IS NULL OR e > T)` |
| `overlap [a, b]` | `s <= b AND (e IS NULL OR e > a)` |
| `history` | `s <= now` |

These are exactly the predicates `facts_context` applies to fact windows
(`query_engine.py` `_FACTS_CONTEXT_TIME_PREDICATE`), so the same words mean the same thing on
facts and on text.

For a lineage **without live periods**, every mode selects the current version — today's
behaviour, byte for byte. Existing corpora therefore see no change.

Derived ends are computed over every live declared period, including periods of versions
that are still processing: whether a version is in force is the publisher's statement, not
a processing state. Selection then keeps only versions that are `ready` with a ready current
representation. So while the version in force at the instant is still processing, its
lineage contributes nothing to that scope rather than serving the text it replaced; the
response's freshness block names the lineage as pending, the same way pipeline readiness
reports unfinished versions today.

Consequences: under `current`, a withdrawn policy (no interval contains now) and an edition
not yet in force contribute nothing — the intended answer to "what applies now". Under
`history`, several versions of one lineage can match; identical chunks (same
`chunk_content_hash`) from different selected versions of one lineage are collapsed into one
result that lists every matching version and its intervals, so unchanged paragraphs do not
repeat.

The selection is one set-returning SQL function shared by every read path,
`memory_v1.versions_in_scope(deployment_id, mode, at, from, to, evaluated_at)` → `(doc_id,
version_id)`, so the rule exists once. It is also published in the open query space
(§8).

### 3.3 Reading non-current versions

Today every chunk read path assumes the current version (`memory_v1.chunks_live` joins
`current_version_id`). With time scopes, a returned chunk may belong to an older or a
future version. **Selection** is scoped; **dereferencing** is not: `adjacent_chunks` and
passage hydration accept a chunk of any live, ready version whose
representation is that version's current representation (the D65 rule), and stay inside
that version (neighbours are never mixed across versions). `memory_v1.chunks_live` keeps
its meaning (current-version chunks) for compatibility; a companion view
`memory_v1.chunks_all_versions_live` exposes every live version's current-representation
chunks for scoped reads.

### 3.4 `search_documents`

`search_documents` keeps `versions: current | all` for lineages without periods. For
periodised lineages the `time` scope decides which versions are judged (`versions = all`
still means every version). Each result that comes from a periodised lineage carries its
in-force intervals.

### 3.5 Claims under a time scope

A claim is selected for a periodised lineage when it has an occurrence (`chunk_claims`) in a
selected version. Because D56 attaches one immutable claim to every version-chunk that
carried it, a claim about an unchanged paragraph is found under any date at which some
version containing it was in force. Currency filtering applies unchanged to lineages
without periods.

### 3.6 Results carry their period

Every chunk, passage and claim result from a periodised lineage includes
`effective: [{from, until, until_declared}]` — the intervals of the selected version(s). An
agent can then say "this is the text in force from 2026-01-01" without a second call.

## 4. Section keys

### 4.1 Source syntax

A heading may end with an attribute block in the Pandoc/kramdown convention:

```markdown
## 4. Per-diem allowance {#per-diem}
### § 5 Scope {#par_5 .provision}
```

The D79 heading parser recognizes a **trailing** `{…}` block on ATX and setext headings,
takes its `#identifier` as the section key and strips the whole block from the stored title
(classes and key=value pairs are ignored). Key syntax: 1–200 characters from
`[A-Za-z0-9_.:/-]`; anything else leaves the heading untouched (the braces stay in the
title, as today). Converters that see heading ids in HTML (`<h2 id="per-diem">`) emit the
same attribute. Keys are never produced by a model; fallback (model-anchored) sections have
no key.

### 4.2 Storage and uniqueness

`document_sections` gains:

- `section_key text` — `NULL` when the heading has none;
- `section_content_hash text NOT NULL` — the hash of the section's ordered block hashes
  (its own blocks only, children excluded), deterministic from the D57 block grid.

A key is unique within a version (`UNIQUE (version_id, section_key)` where not null). A
duplicate key in one version keeps the first occurrence and records the others as a
structure warning (`conversion.json` warnings); the later headings get no key.

### 4.3 Using keys

- **Section history** (§6.2): all versions of a lineage that contain a key, with each
  version's periods, the section content hash and whether it changed from the previous
  version.
- **Reference endpoints** (§6.1) name sections by key.
- The parser change is a new parser generation (`SKELETON_PARSER_VERSION`); documents
  without attribute blocks produce identical sections.

## 5. Reuse across versions: text origin time

D56 keys extraction reuse on stable inputs. On `main`, the "stable header facts" in
`extraction_input_hash` include the version's `source_modified_at`/`published_at`, because
the E2 header shows that date to the model and it becomes `asserted_at`. Since nearly every
new version has a new modification time, no chunk of a new version matches the previous
version, and D56's "cost proportional to the edit" does not hold (analysis §3.5).

**Rule.** When E1 builds the chunk records of a new version of lineage `L`:

1. compute the chunk's **date-free reuse identity**: own block hashes, neighbour block
   hashes, and the header facts *other than* the date (title, file name, source kind,
   language, extraction-eligibility policy);
2. look up the earliest live, non-deleted version of `L` that has a chunk with the same
   date-free identity; if found, the chunk's **text origin time** is that chunk's text origin
   time (so the value propagates unchanged along a chain of versions); otherwise it is the
   new version's `source_modified_at` (or `published_at`);
3. store `text_origin_at` on the chunk, use it as the date in the E2 header, and put it (not
   the version date) into `extraction_input_hash`.

The result: an unchanged chunk has the same key as before, so Selection, Claimify, claims
and embeddings are reused; its relative expressions stay resolved against the time the words
were written, which is also the `asserted_at` of the reused claim. A changed chunk, or one
whose neighbours changed, has no match and gets the new version's date — identical to
today's behaviour. Effective periods are never part of any key.

Deleted versions (D135) are skipped by the lookup, so reuse never draws on retired
testimony (D55's refinement). `text_origin_at` is a deterministic function of stored hashes
and immutable version source times, so replay (D7) reproduces it.

## 6. Cross-references

### 6.1 Data model

`document_crossrefs` (D36) is extended in place. A row is **one reference made by one source
version**:

```sql
CREATE TYPE crossref_kind AS ENUM
  ('cites', 'links_to', 'attaches', 'replies_to', 'refers_to', 'amends', 'implements');
CREATE TYPE crossref_binding AS ENUM ('floating', 'pinned');
CREATE TYPE crossref_origin  AS ENUM ('extracted', 'supplied');

CREATE TABLE document_crossrefs (
  crossref_id        uuid PRIMARY KEY,
  deployment_id      uuid NOT NULL,
  from_doc_id        uuid NOT NULL,        -- source lineage (denormalized)
  from_version_id    uuid NOT NULL,        -- the version that makes the reference
  from_section_key   text,                 -- source section, when known
  from_char_start    integer,              -- source span in the representation's document.md, when known (extracted rows)
  from_char_end      integer,
  kind               crossref_kind NOT NULL,
  origin             crossref_origin NOT NULL,
  reference_set_id   uuid,                 -- supplied rows: the generation that wrote them (§6.3)
  source_label       text,                 -- caller's own type code, opaque, returned verbatim
  -- target, as named by the source:
  to_source_kind     text,                 -- target lineage identity (supplied rows; extracted rows when an exact key matched)
  to_source_ref      text,
  to_source_version_ref text,              -- pinned target version, by the target's source_version_ref
  to_section_key     text,                 -- target section key; NULL = whole document
  binding            crossref_binding NOT NULL DEFAULT 'floating',
  change_effective_from timestamptz,       -- kind = amends: when the change takes effect
  -- resolution:
  to_doc_id          uuid,                 -- resolved target lineage; NULL = not (yet) ingested
  to_version_id      uuid,                 -- resolved target version for pinned rows
  resolved           boolean NOT NULL DEFAULT false,
  raw_citation       text,                 -- extracted rows: the citation text as found (retained after resolution, §13 of the schema design)
  context            text,                 -- bounded surrounding text
  crossref_version   text,                 -- the crossreferencer generation (D7 replay)
  created_at         timestamptz NOT NULL DEFAULT now(),
  CHECK (binding = 'floating' OR to_source_version_ref IS NOT NULL OR to_version_id IS NOT NULL),
  CHECK (kind = 'amends' OR change_effective_from IS NULL),
  CHECK (origin = 'extracted' OR reference_set_id IS NOT NULL),
  FOREIGN KEY (deployment_id, from_doc_id)     REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, from_version_id) REFERENCES document_versions (deployment_id, version_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, to_doc_id)       REFERENCES documents (deployment_id, doc_id) ON DELETE SET NULL (to_doc_id),
  FOREIGN KEY (deployment_id, to_version_id)   REFERENCES document_versions (deployment_id, version_id) ON DELETE SET NULL (to_version_id)
);
```

Indexes: by `(from_version_id)`, by `(to_doc_id, to_section_key)` for incoming lookups, and
by `(deployment_id, to_source_kind, to_source_ref) WHERE to_doc_id IS NULL` for late binding.

**Kinds** (each general; the analysis §4.4 gives the reasoning):

| Kind | Meaning | Examples |
| --- | --- | --- |
| `cites` | bibliographic citation from a reference list | a paper's DOI in its bibliography |
| `links_to` | a hyperlink found in the text | a URL in a wiki page |
| `attaches` | container membership | an e-mail attachment |
| `replies_to` | conversation threading | `In-Reply-To` |
| `refers_to` | an in-text pointer to another passage or document | "see section 4.2", `page#anchor`, "as defined in § 3" |
| `amends` | the source changes the target's text or force from `change_effective_from` | contract amendment 2 changes clause 7; RFC "Updates: 7231"; an amending act |
| `implements` | the source elaborates or executes the target | an implementing regulation; a procedure implementing a policy; a profile of a standard |

**Binding.** A `floating` reference resolves at read time to the target version in force at
the reading instant (periodised targets) or to the target's current version (other targets).
A `pinned` reference names one target version, matched through the target's
`source_version_ref`; it resolves to that version whenever it is read. Section keys resolve
at read time inside whichever version was chosen.

### 6.2 Reading references

Two operations, on HTTP, SDK and the MCP catalogue (D136 parity):

- **`document_references`** — input: either `chunk_id`, or `doc_id` with an optional
  `section_key`; `direction` (`outgoing` | `incoming` | `both`, default `both`); optional
  `kinds`; `time` (§3); `k` (default 50, max 200). Outgoing rows are references made by the
  selected version(s) of the source, restricted to the chunk's section (or the given section
  and its descendants). Incoming rows are references whose target is this lineage (and
  section key, when given), made by versions selected by `time` in their own lineages. Each
  row returns: kind, `source_label`, context, the source (doc, version, section key and
  title), and the target resolution — resolved version, its in-force intervals, the target
  section's title and first chunk ids (so an agent can read it with the passage tools) — or
  an explicit status: `target_not_ingested`, `target_not_in_force` (floating target with no
  version in force at the instant), `section_not_in_version` (the key is absent from the
  chosen target version), `pinned_version_missing`. Nothing is silently dropped.
- **`section_history`** — input: `doc_id`, `section_key`, `time` (default `history`). Output:
  one row per selected version containing the key: version id and number, in-force
  intervals, section title, section content hash, `changed` (content hash differs from the
  previous row), first chunk ids, and the incoming `amends` references whose target is this
  section with their `change_effective_from`.

The existing directed `graph_citation_path` keeps lineage grain (§7).

### 6.3 Caller-supplied references

`PUT /documents/{doc_id}/versions/{version_id}/references` (SDK
`set_references`) with a JSON body `{"references": [...]}` (bounded; 10 000 rows per call as a
starting point, to be measured). Each item: `kind`, `from_section_key?`,
`target: {source_kind, source_ref, source_version_ref?, section_key?}`, `binding`,
`change_effective_from?`, `source_label?`, `context?`.

- The body is stored as a content-addressed **reference-set artifact** of the version
  (`…/<doc_id>/<content_hash>/references/<sha256>.json`) and a `document_reference_sets` row
  (`reference_set_id`, version, content hash of the set, `created_at`,
  `superseded_at`). Identical sets are no-ops (idempotent on the set hash).
- The E0 `crossref` sub-worker (D36) materializes the set into `document_crossrefs` rows with
  `origin = supplied` after the version's structure exists, and in the same transaction marks
  the previous supplied set's rows non-live (`reference_set_id` of a superseded set). This is
  how supplied references **write through E0** (Rule 3): the HTTP call records input; the
  pipeline writes rows.
- Validation happens at materialization: an unknown `from_section_key` keeps the row at
  document grain and records a warning; a malformed item rejects the whole set before storage
  (`422`).
- **Late binding**: resolution of `to_source_kind`/`to_source_ref` to `to_doc_id` happens at
  materialization and, for rows still unresolved, when a lineage with that identity is first
  ingested (one indexed lookup on the ingest path, as D36 §4A already specifies). Pinned rows
  resolve `to_version_id` when a version with the named `source_version_ref` exists, at
  materialization or when that version is ingested.

### 6.4 Extracted references

The D36 extraction rungs (URLs → `links_to`, containers → `attaches`, thread headers →
`replies_to`, reference-list mining and per-deployment citation grammars → `cites`, cheap-first
resolution with the small-model residue rung) write the same table with `origin = extracted`,
`from_version_id` set, spans filled, and — where the matched key includes an anchor
(`page#per-diem`) — `to_section_key` filled and kind `refers_to`. Extracted rows are keyed on
(version content, crossreferencer version) as D36 requires. Supplied and extracted rows
coexist; a deployment that supplies references for a lineage can disable extraction for it
by source kind (configuration, not a domain flag).

## 7. Graph and open query space

- **Graph edge.** The `document_crossref` edge stays lineage to lineage. Its source view
  deduplicates live rows to one edge per `(from_doc_id, to_doc_id, kind)` among references
  made by the versions selected under `current` (in force now, or the current pointer for
  lineages without periods). `graph_citation_path` keeps its current, structural semantics;
  time-scoped reference questions go through `document_references`. Sections are not graph
  vertices (bounded fan-out; the graph's purpose is entity relations).
- **Public views.** `memory_v1.document_effective_periods_live` (§2.2); `sections_live` gains
  `section_key` and `section_content_hash`; `chunks_live` gains `text_origin_at`;
  `chunks_all_versions_live` (§3.3); `document_crossrefs_live` gains `from_version_id`,
  `from_section_key`, `to_section_key`, `binding`, `to_version_id`, `origin`,
  `source_label`, `change_effective_from` and keeps its rule that only rows whose target lineage
  is live and resolved appear (target identity text is never exposed); and the function
  `memory_v1.versions_in_scope(…)` (§3.2) is allowlisted.

## 8. Facts from periodised versions

A claim extracted from a version of a periodised lineage is evidence that the document stated
it while that version was in force. The claim itself does not change (D41): its occurrence's
**in-force interval set** — the union of the intervals of the versions it occurs in — is an
additional **adjudication input** for E3, shown beside the claim's own validity. D118's single
fact window stays the only fact-time authority: adjudication may use the interval to propose
or bound a window ("the allowance was €45 per day from 2024-01-01 until 2026-01-01"), exactly as
it uses a claim's own dates.

When a lineage's periods change (a new start, a declared end, a correction, a version deleted),
the engine enqueues ordinary D118 re-adjudication for facts supported by claims that occur in
the affected versions. Windows then change through the normal mutable-window path, so the rule
of open PR #486 (belief-time reads use a fact's current window, the transcript keeps the
earlier one) applies without special cases.

## 9. Deletion and forgetting

- Deleting a version or lineage (D135) cascades to its periods, section keys (rows), reference
  sets and the references it made. Derived ends of the remaining periods recompute.
- References *to* a deleted or forgotten target keep the source's own target identity (it is
  the source's content, like `raw_citation`); `to_doc_id`/`to_version_id` clear; public views
  hide the row until the target is live again. Hard forget (D74) of the *source* erases its
  reference-set artifacts and rows with its other derived text.

## 10. Non-goals and scope boundaries

- **Amendment replay.** The engine stores and serves the texts a publisher releases; it does
  not reconstruct a text by applying edit instructions. Sources that publish only base text
  plus amendments need a client-side consolidator.
- **Inferred periods or keys.** Periods come only from declarations; keys only from the source
  text. A model never produces either.
- **Periods on `living` lineages** (§2.4).
- **Section vertices in the graph** (§7).
- **Domain vocabulary.** No enum value, flag or field is specific to legislation; callers keep
  their own taxonomies in `source_label`.

## 11. Worked example (Travel Policy)

1. `ingest(travel_v1.md, source_kind="intranet", source_ref="policy/travel",
   effective_from=2024-01-01)` → version 1, period `[2024-01-01, open)`.
2. `ingest(travel_v2.md, …, effective_from=2026-01-01)` on 2025-11-15 → version 2. Version 1's
   interval is now `[2024-01-01, 2026-01-01)`. Chunks unchanged between editions keep their text
   origin time (2023-12-10, edition 1's modification time), match their old keys and are not
   re-extracted; only section 4 and its neighbours are.
3. On 2025-12-01, `search_chunks("daily allowance")` (default `current`) returns edition 1's section 4:
   edition 2 is not in force yet. `search_chunks(…, time={"mode":"at","at":"2026-02-01"})` returns
   edition 2's section 4 with `effective: [{from: 2026-01-01, until: null}]`.
4. `set_references(doc, v2, [{kind: "refers_to", from_section_key: "approvals", target:
   {source_kind: "intranet", source_ref: "policy/expense", section_key: "approvals-process"},
   binding: "floating"}])`. The Expense Policy is not ingested yet → `target_not_ingested`. When
   it is ingested, late binding fills `to_doc_id`; `document_references(doc_id, "approvals")`
   now returns the Expense Policy section that is in force at the reading instant.
5. On 2027-06-30 the company withdraws the policy: `set_effective_periods(doc, v2,
   [{from: 2026-01-01, until: 2027-06-30}])`. From then on `current` returns nothing from this
   lineage; `at 2026-05-01` still returns edition 2; `section_history(doc, "per-diem")` lists both
   editions and marks edition 2's section as changed.
