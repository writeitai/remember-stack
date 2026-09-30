# Effective time, section keys, and section-level references (D140)

**Status:** binding design. **Decision:** [D140](../../decisions.md#d140-declared-effective-periods-section-keys-and-version-aware-references).
**Analysis:** [version effective time and section references](../analysis/version_effective_time_and_section_references.md).
**Delivery order:** [effective time delivery plan](../plans/effective_time_and_section_references.md).

This design gives the engine six general capabilities:

1. a caller can declare **when each version of a document is in force** (its *effective
   periods*);
2. retrieval can read document text **as of a date**, using the time scope that facts
   already use, and every returned passage, claim and handle addresses the version that was
   selected;
3. **fact reads never present text that is not in force** as their only support (a
   deterministic evidence gate; D118 stays the fact-window authority);
4. a section can carry a **stable key** that identifies it across versions;
5. a new version **reuses the processing of its unchanged chunks** even when its source
   timestamp changed;
6. **cross-references** can run from a section of one version to a section of another
   document, pinned to a named target version or floating to whichever versions are in
   force, and a caller can supply them already resolved.

A running example that has nothing to do with law: *the Travel Policy* of a company.
Edition 1 is in force from 2024-01-01. Edition 2, published on 2025-11-15, is in force from
2026-01-01. Its section 4 `{#per-diem}` changes the daily allowance; section 7
`{#approvals}` says "approvals follow section 3 of the Expense Policy". The Czech statute
book is the corpus that motivated the work (see the analysis §2); it uses exactly the same
mechanisms.

## 1. Concepts

- **Lineage and version** — D55: a lineage (`doc_id`) is the logical document over time; a
  version is one immutable *observation* of its bytes. Identical bytes to the lineage's
  **latest** version are a no-op; bytes identical to an **older** version (A→B→A) are a new
  observation and create a new version that shares the content object
  (`src/rememberstack/spine/document_catalog.py:71-82`; the per-lineage content uniqueness
  constraint was dropped by migration `p3_01_0008`).
- **Served version** — `documents.current_version_id`: the newest version whose
  representation has completed processing and that is not deleted
  (`document_catalog.py:642-666`, `lifecycle.py` repoint after deletion). It is *not* the
  newest observed version while a newer one is still processing.
- **Version key** — an optional caller-chosen, immutable identifier of a version, unique
  within its lineage (`edition-2`, a publisher's consolidated-version id). It is the stable
  address used by pinned references (§6.1). It is distinct from `source_version_ref`, which
  stays D55's mutable connector cursor.
- **Effective period** — a half-open interval `[effective_from, effective_until)` during
  which a version's text is *in force* according to its publisher. It is **declared by the
  caller**; the engine never infers it. It is distinct from the other times the engine keeps:

  | Time | Meaning | Where |
  | --- | --- | --- |
  | ingest (system) time | when the engine received the version | `document_versions.ingested_at` |
  | source time | when the source says the snapshot was authored or modified | `source_modified_at` (D55) |
  | text origin time | when the words of a chunk were first written in this lineage | `chunks.text_origin_at` → claim `asserted_at` (§5) |
  | claim validity | the period a sentence says something holds for ("in 2024 …") | `claims.claim_valid_*` (D41) |
  | fact window | the adjudicated period a fact holds | `valid_from`/`valid_until` (D118) |
  | **effective period** | when the publisher says the version's text is the one in force | new (§2) |

  Example: edition 2 of the Travel Policy was *modified* on 2025-11-10, *ingested* on
  2025-11-15, and is *in force* from 2026-01-01.
- **Periodised lineage** — a lineage whose `documents.effective_time` is `declared` (§2.4).
  The state is set by the first declaration and does not revert when declarations are
  retracted. Only `snapshot` lineages can be periodised.
- **Section key** — a source-chosen identifier for a section, stable across versions
  (`per-diem`, `approvals`, `par_5`).

## 2. Effective periods

### 2.1 Data model

```sql
ALTER TABLE documents
  ADD COLUMN effective_time text NOT NULL DEFAULT 'undeclared'
    CHECK (effective_time IN ('undeclared', 'declared'));   -- §2.4

ALTER TABLE document_versions
  ADD COLUMN version_key text;                              -- immutable once set; §1
CREATE UNIQUE INDEX ux_docversions_version_key
  ON document_versions (deployment_id, doc_id, version_key) WHERE version_key IS NOT NULL;

CREATE TABLE document_effective_periods (
  period_id       uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL,
  doc_id          uuid NOT NULL,
  version_id      uuid NOT NULL,
  effective_from  timestamptz NOT NULL,   -- inclusive
  effective_until timestamptz,            -- exclusive; NULL = until the next declared start (§2.2)
  declared_at     timestamptz NOT NULL DEFAULT now(),
  declared_by     text NOT NULL CHECK (declared_by IN ('ingest', 'period_api')),
  retracted_at    timestamptz,            -- a correction replaced or removed this declaration
  retracted_by_period_id uuid,
  CHECK (effective_until IS NULL OR effective_until > effective_from),
  CHECK (retracted_at IS NULL OR retracted_at >= declared_at),
  -- ownership: the version belongs to this lineage in this deployment
  FOREIGN KEY (deployment_id, doc_id, version_id)
    REFERENCES document_versions (deployment_id, doc_id, version_id)
);
CREATE UNIQUE INDEX ux_effective_periods_live_start
  ON document_effective_periods (deployment_id, doc_id, effective_from) WHERE retracted_at IS NULL;
CREATE INDEX ix_effective_periods_lineage
  ON document_effective_periods (deployment_id, doc_id, effective_from);
```

The composite foreign key uses the existing `UNIQUE (deployment_id, doc_id, version_id)` on
`document_versions`, so a period for lineage A can never name a version of lineage B. There
is no `ON DELETE` action: versions and lineages are soft-deleted (D135, §9), and periods are
removed only by hard forget (D74), which deletes them explicitly.

Rows are a ledger: a correction sets `retracted_at` on the old row and inserts the new one in
the same transaction. Every declaration ever made for a live version remains readable, with
the instant it was made and the instant it was retracted; this is what lets a paged read pin
its view of declarations (§3.6). A version may hold **several** live periods (a text that was
withdrawn and later put back into force by declaration rather than re-ingest).

### 2.2 The derived in-force interval

The engine never stores a derived end. Declarations are evaluated **as known at a belief
instant `b`** (by default the request's evaluation instant): a declaration is *known at `b`*
when `declared_at <= b AND (retracted_at IS NULL OR retracted_at > b)`. Among the declarations
of one lineage known at `b` and belonging to non-deleted versions, the **in-force interval** of
a declaration `p` is:

- start = `p.effective_from`;
- end = `p.effective_until` if declared; otherwise the smallest `effective_from` of any other
  such declaration that is greater than `p.effective_from`; otherwise open (`NULL`).

The derivation deliberately **ignores processing status**: whether a version is in force is
the publisher's statement, not a pipeline state. A version that is still converting still ends
its predecessor's interval.

This is the set-returning function `memory_v1.effective_intervals(deployment_id, believed_at
DEFAULT now())` → `(doc_id, version_id, period_id, effective_from, effective_until,
until_declared)`, one window function (`lead(effective_from) OVER (PARTITION BY doc_id ORDER
BY effective_from)`) over the known declarations of live lineages and non-deleted versions.
The public view `memory_v1.document_effective_periods_live` is that function at `now()`.

Consequences a reader should check against the example:

- Declaring edition 2 from 2026-01-01 needs **no write to edition 1**: edition 1's interval
  becomes `[2024-01-01, 2026-01-01)` automatically.
- Versions may be declared in any order (archives are back-filled out of order).
- A **gap** (the policy withdrawn on 2027-06-30 with no successor) is an explicit
  `effective_until` on the last period. A later re-issue declares a new period.
- An explicit end is never overridden by a later start; if a declared end lies after the next
  declared start, the two versions are both in force in the overlap (a transition period).
  Overlap is legal; two live declarations of one lineage with the same start are not (the
  unique index). §3.2 and §6.2 say how readers see the overlap.
- Soft-deleting a version (D135) removes its declarations from evaluation; its neighbours'
  derived ends recompute from the remaining declarations.

### 2.3 Writing periods

- **At ingest.** `POST /ingest` (and SDK `ingest`, MCP `ingest`) gain optional
  `effective_from`, `effective_until` and `version_key` (timezone-aware instants stored in
  UTC). All three require `source_kind`/`source_ref`; periods also require
  `versioning_mode = snapshot`.
  - `version_key` already used in the lineage: if it names a version with the same
    `content_hash`, the call is an idempotent observation of that version (its cursor may
    advance, D55); with other content it is rejected with `409 Conflict`. A key is set only
    when the version is created and never changes.
  - A new version: the declaration is inserted in the version's creating transaction and the
    lineage becomes `declared`.
  - A D55 no-op (bytes identical to the latest version): the declaration is recorded on that
    existing version. A period is neither snapshot metadata nor extraction input, so this
    never rewrites anything derived text depends on.
  - A declaration identical to a known one (same version, same start, same end) is a no-op. A
    declaration whose start equals a live declaration of **another** version of the lineage is
    rejected with `409 Conflict` and nothing is written.
- **After ingest.** `PUT /documents/{doc_id}/versions/{version_id}/effective-periods` replaces
  the live declaration set of one version with the given set (each `{effective_from,
  effective_until?}`), retracting removed declarations and inserting new ones atomically; SDK
  `set_effective_periods`. An empty set is allowed: the version then has no in-force interval,
  and the lineage **stays `declared`** (§2.4). The version must belong to the lineage in the
  deployment (checked in the same statement that locks it); it requires `memory:write`.
- **Leaving effective time.** `DELETE /documents/{doc_id}/effective-periods` (SDK
  `clear_effective_time`) retracts every live declaration of the lineage and sets
  `effective_time = 'undeclared'`, returning the lineage to served-version semantics. It is the
  only way back, so reverting to "the newest version is current" is always an explicit act.
- Periods never trigger reprocessing: they are not part of any extraction, embedding or
  structure input (§5 keeps them out of every key). They do enqueue fact re-adjudication
  (§8).

### 2.4 Interaction with versioning modes, currency and the served version

- **Only `snapshot` lineages accept periods.** `living` means "the newest version is the
  standing statement" (D55); a declared period would be a second authority over the same
  question. A request declaring a period on a `living` lineage is rejected (`422`).
- **`effective_time` is sticky.** A lineage becomes `declared` with its first declaration and
  stays `declared` when declarations are retracted. A `declared` lineage with no interval
  containing the instant contributes nothing to that scope — retracting the last period of a
  withdrawn edition therefore never resurrects it as "current". Only `clear_effective_time`
  returns the lineage to `undeclared`.
- **Testimony currency is unchanged.** In `snapshot` mode every version's claims stay current
  testimony (D54); effective periods do not flip currency. Currency answers "should this
  claim count as evidence"; the period answers "was this text in force at T". Evidence counts
  stay per distinct lineage. Which evidence a fact read may *show* is decided by the gate in
  §8, not by currency.
- **The served version keeps its D55/D65 meaning** for every surface that has no time scope
  (lineage listings, the lineage title, P3's lineage path). Time-scoped reads select versions
  through effective intervals (§3).
- **Future-dated versions.** A version whose intervals all start after now is stored,
  processed and readable under `at`/`overlap` scopes at future instants, but invisible to
  `current` until its start passes. Selection is evaluated at read time; no scheduler exists.

## 3. Time-scoped retrieval

### 3.1 One time vocabulary

Every read operation that returns document text, text-derived claims or references gains the
optional `time` argument with the existing modes (`src/remember/models.py` `TemporalScope`,
MCP `_TIME_SCHEMA`): `current` (default), `at`, `overlap`, `history`.

| Surface | Change |
| --- | --- |
| chunk and claim search (HTTP `POST /search/chunks`, `POST /search/claims`; SDK `search_chunks`, `search_claims`) | `SearchRequest.time` |
| `claims_and_sources_context` (MCP, HTTP, SDK) | `time` argument |
| `search_documents` (MCP, HTTP, SDK) | `time` argument (§3.5) |
| `facts_context`, `combined_context`, relation/observation lookups, graph neighbourhood/path | existing `time` also drives the §8 evidence gate |
| `adjacent_chunks` and source-passage hydration | accept a chunk of any non-deleted ready version (§3.3) |
| `document_references`, `section_history` (new, §6.2) | `time` argument |

### 3.2 Which versions a scope selects

Let `b` be the belief instant (§2.2) and `now` the evaluation instant. For a **periodised
lineage**, a version is selected when one of its in-force intervals `[s, e)` known at `b`
satisfies (NULL `e` = open):

| Mode | Condition |
| --- | --- |
| `current` | `s <= now AND (e IS NULL OR e > now)` |
| `at T` | `s <= T AND (e IS NULL OR e > T)` |
| `overlap [a, b']` | `s <= b' AND (e IS NULL OR e > a)` |
| `history` | `s <= now` |

These are exactly the predicates `facts_context` applies to fact windows
(`query_engine.py` `_FACTS_CONTEXT_TIME_PREDICATE`), so the same words mean the same thing on
facts and on text.

Selection additionally requires the version to be non-deleted and `ready` with a ready current
representation. While the version in force at an instant is still processing, its lineage
contributes nothing to that scope — it never serves the text that version replaced.

For an **undeclared** lineage, every mode selects the served version — today's behaviour,
byte for byte. Existing corpora therefore see no change.

Under `current`, a withdrawn edition and an edition not yet in force contribute nothing.
Where two versions of one lineage are both in force (overlapping declarations), both are
selected and results identify each; readers are never given one silently.

The rule exists once, as `memory_v1.versions_in_scope(deployment_id, mode, at, range_start,
range_end, evaluated_at, believed_at)` → `(doc_id, version_id, representation_id,
effective_from, effective_until)`, used by every read path and allowlisted in the open query
space.

### 3.3 Reading and addressing the selected version

Selection is scoped; addressing is exact. Every scoped result names the version it came from,
and every handle it carries opens that version:

- **Chunks and passages** carry `doc_id`, `version_id`, `representation_id` and their spans in
  that representation. `adjacent_chunks` and passage hydration accept a chunk of any
  non-deleted ready version whose representation is that version's current representation (the
  D65 rule), and never mix versions. `memory_v1.chunks_live` keeps its meaning (served-version
  chunks); the new `memory_v1.chunks_all_versions_live` exposes the current-representation
  chunks of every non-deleted ready version for scoped reads.
- **Source handles** use `source_open(version_id, representation_id)` (D115 already takes a
  version). A **P3 path** names the lineage and opens its *served* version, so it is returned
  only when the selected version is the served version; otherwise the result sets
  `p3_path = null` and `served_version = false`. `DocumentSearchResult.p3_path` becomes
  optional accordingly. An agent can never open a different edition than the one it was shown.

### 3.4 Claims under a time scope

D56 attaches one immutable claim to every version-chunk that carried it (`chunk_claims`
occurrences, each with its own `evidence_spans` and locators). Under a scope:

- A claim is selected for a periodised lineage when it has an occurrence in a selected
  version. For undeclared lineages today's currency filter applies unchanged.
- **The returned evidence is the occurrence in the selected version**, not the claim's origin:
  its `chunk_id`, `version_id`, `representation_id`, its per-occurrence `evidence_spans` and
  locators. If several selected versions carry the claim (history, overlap, or overlapping
  declarations), the claim is returned once with one occurrence per selected version, ordered
  by effective start. `effective` (§3.7) describes those occurrences.
- The claim's immutable fields (`claim_text`, `asserted_at`, claim validity) are unchanged.
  An occurrence is always readable even when the claim's origin version was later deleted,
  because hydration never goes through the origin.

This refines D134 §4's "the returned evidence is the claim's origin" for scoped reads of
periodised lineages; unscoped reads and undeclared lineages keep the origin rule.

### 3.5 `search_documents`

For periodised lineages the `time` scope decides which versions are judged:

- `versions: current` (default) judges each selected version; `versions: all` judges **every
  version the scope selects** (for periodised lineages) or every live version (undeclared
  lineages, as today). There is no option that ignores the scope for periodised lineages: an
  audit over all editions uses `time: history`, and future editions use `overlap` with a
  future range.
- Filter-only paging pins the first call's `as_of` instant (D134) **and** uses it as the belief
  instant `b` for declarations, so a period correction made between pages cannot move a row
  across pages.
- Each result from a periodised lineage carries its `effective` intervals and the §3.3 handles.

### 3.6 Paging and belief instant

Every scoped operation that pages (filter-only `search_documents`, `document_references`,
`section_history`) records `evaluated_at` and `believed_at` in its cursor and reuses them for
later pages. Ranked single-page searches evaluate both at the request instant.

### 3.7 Results carry their period and pending lineages

- Every chunk, passage, claim occurrence and document result from a periodised lineage
  includes `effective: [{from, until, until_declared}]` for its version.
- Scoped responses report lineages whose in-force version for the scope is not ready in a new
  envelope field `Freshness.scope_pending`: `{doc_ids: [...] (at most 50), count}`. It is
  computed in the same statement from `effective_intervals` joined to version status, so an
  agent can tell "nothing is in force" from "the in-force text is still processing".

## 4. Section keys

### 4.1 Source syntax

A heading may end with an attribute block in the Pandoc/kramdown convention:

```markdown
## 4. Per-diem allowance {#per-diem}
### § 5 Scope {#par_5 .provision}
```

The D79 heading parser recognizes a **trailing** `{…}` block on ATX and setext headings, takes
its `#identifier` as the section key and strips the whole block from the stored title (classes
and key=value pairs are ignored). Key syntax: 1–200 characters from `[A-Za-z0-9_.:/-]`;
anything else leaves the heading untouched. Converters that see heading ids in HTML
(`<h2 id="per-diem">`) emit the same attribute. Keys are never produced by a model;
model-anchored fallback sections have no key.

### 4.2 Storage and uniqueness

`document_sections` gains:

- `section_key text` — `NULL` when the heading has none;
- `own_content_hash text` — hash of the section's own ordered block hashes (children
  excluded);
- `subtree_content_hash text` — hash of the ordered block hashes of the section's whole span,
  children included (the parser's section span already includes its children,
  `structure_skeleton.py:111-118`).

Both hashes are deterministic from the D57 block grid. A key is unique within a version
(`UNIQUE (version_id, section_key)` where not null). A duplicate key in one version keeps the
first occurrence and records a structure warning; the later headings get no key.

The hash columns are nullable at the schema level: sections created before D140 are backfilled
by a maintenance job that recomputes them from each representation's stored `blocks.json` and
section block ranges (no model, no reprocessing). Readers treat a missing hash as "unknown"
(`changed = null`), never as "unchanged".

### 4.3 Using keys

- **Section history** (§6.2) lists a key across versions, ordered by effective start
  (periodised lineages) or by `version_no` (undeclared lineages), with `changed` computed from
  the subtree hash and `own_changed` from the own-block hash, and explicit **absence rows**
  where a selected version lacks the key.
- **Reference endpoints** (§6.1) name sections by key.
- Parsing attribute blocks is a new parser generation (`SKELETON_PARSER_VERSION`). Because
  `structurer_version` is part of the D56 extraction key, the first new version of each
  existing lineage after deployment misses reuse once (a one-time extraction-basis rollover,
  D56/D65). Existing versions are not re-structured; their sections get keys only if they are
  re-structured for another reason.

## 5. Reuse across versions: text origin time

D56 keys extraction reuse on stable inputs. On `main`, the "stable header facts" in
`extraction_input_hash` include both `source_modified_at` and `published_at` of the version
(`e1.py:629-635`), the E2 header shows `source_modified_at or published_at` (`e2.py:1741-1749`)
and claims take the same value as `asserted_at` (`e2.py:1293-1296`). Since nearly every new
version has a new modification time, no chunk of a new version matches the previous version,
and D56's "cost proportional to the edit" does not hold.

**One per-chunk date.** Each chunk gets `text_origin_at`, and it replaces the version date in
all three places: the reuse key (replacing *both* `source_modified_at` and `published_at`
header facts), the E2 header, and the `asserted_at` of claims freshly extracted from the chunk.
The version's own date remains on the version for everything else.

**Reuse identity.** Each chunk also stores `reuse_identity_hash` — the date-free identity:
own block hashes, neighbour block hashes, the header facts other than dates (title, file name,
source kind, language, the extraction-eligibility policy marker), `blockizer_version`,
`structurer_version` and `extractor_version`. Indexed on `(deployment_id, doc_id,
reuse_identity_hash)`.

**Rule, at chunk creation.** When E1 creates the chunks of a new version of lineage `L`:

1. compute `reuse_identity_hash`;
2. among chunks of `L` with the same `reuse_identity_hash` that belong to **non-deleted**
   versions at this moment, take the one with the smallest `(text_origin_at, version_no,
   ordinal)`; the new chunk's `text_origin_at` is that chunk's `text_origin_at`;
3. with no match, `text_origin_at = source_modified_at or published_at` of the new version
   (the value E2 uses today); with neither, `NULL` (as today's header "date unknown").

`text_origin_at` is **recorded once and immutable**, like the `asserted_at` it feeds. It is
extraction input: replay (D7) uses the recorded value and never recomputes it. Deleting the
origin version later does not change it on descendant chunks or on reused claims — those
claims were already stamped with it and D135 retires testimony, not timestamps. Chunks created
*after* a deletion never draw from deleted versions (step 2), so reuse never draws on retired
testimony (D55's refinement).

Because the parser, blockizer, structurer and extractor versions are part of the identity, a
toolchain change is a reuse boundary exactly as D56/D65 define: a chunk under a new toolchain
matches nothing and takes its own version's date.

**Why this is correct.** An unchanged chunk has the same key as before, so Selection, Claimify,
claims and embeddings are reused; relative expressions in it stay resolved against the time
the words were written, which is also the `asserted_at` of the claims it carries. A changed
chunk, or one whose neighbours changed, matches nothing and takes the new version's date —
today's behaviour. Effective periods are never part of any key.

This refines D55's rule that claims' `asserted_at` comes from the version's source time: it
now comes from the source time of the version where the text first appeared in the lineage,
which for new text is the same value.

## 6. Cross-references

### 6.1 Data model

`document_crossrefs` (D36) is extended in place. A row is **one reference made by one source
version**:

```sql
CREATE TYPE crossref_kind    AS ENUM
  ('cites', 'links_to', 'attaches', 'replies_to', 'refers_to', 'amends', 'implements');
CREATE TYPE crossref_binding AS ENUM ('floating', 'pinned');
CREATE TYPE crossref_origin  AS ENUM ('extracted', 'supplied');

CREATE TABLE document_reference_sets (
  reference_set_id uuid PRIMARY KEY,
  deployment_id    uuid NOT NULL,
  doc_id           uuid NOT NULL,
  version_id       uuid NOT NULL,
  set_hash         text NOT NULL,          -- sha256 of the canonical body — idempotency key
  artifact_uri     text NOT NULL,          -- …/<doc_id>/<content_hash>/references/<set_hash>.ndjson
  item_count       integer NOT NULL,
  status           text NOT NULL CHECK (status IN ('pending', 'materialized', 'rejected', 'superseded')),
  errors           jsonb,                  -- rejected: [{item, field, reason}], bounded
  created_at       timestamptz NOT NULL DEFAULT now(),
  materialized_at  timestamptz,
  UNIQUE (deployment_id, version_id, set_hash),
  UNIQUE (deployment_id, version_id, reference_set_id),
  FOREIGN KEY (deployment_id, doc_id, version_id)
    REFERENCES document_versions (deployment_id, doc_id, version_id)
);

CREATE TABLE document_crossrefs (
  crossref_id        uuid PRIMARY KEY,
  deployment_id      uuid NOT NULL,
  from_doc_id        uuid NOT NULL,
  from_version_id    uuid NOT NULL,
  from_section_key   text,                 -- source section, when known
  from_char_start    integer,              -- extracted rows: span in the version's current representation
  from_char_end      integer,
  kind               crossref_kind NOT NULL,
  origin             crossref_origin NOT NULL,
  reference_set_id   uuid,                 -- supplied rows
  source_label       text,                 -- caller's own type code, opaque, returned verbatim
  -- target as named by the source (source content; §6.5 governs exposure):
  to_source_kind     text,
  to_source_ref      text,
  to_version_key     text,                 -- pinned: the target's immutable version key (§1)
  to_section_key     text,                 -- NULL = whole document
  binding            crossref_binding NOT NULL DEFAULT 'floating',
  change_effective_from timestamptz,       -- kind = amends: when the change takes effect
  change_date_known  boolean,              -- kind = amends: false = the source states no date
  -- resolution:
  to_doc_id          uuid,                 -- resolved target lineage; NULL = not (yet) matched
  resolved           boolean NOT NULL DEFAULT false,
  raw_citation       text,                 -- extracted rows: the citation text as found
  context            text,                 -- bounded surrounding text
  crossref_version   text,                 -- crossreferencer generation (D7)
  created_at         timestamptz NOT NULL DEFAULT now(),
  CHECK (binding = 'floating' OR to_version_key IS NOT NULL),
  CHECK ((kind = 'amends') = (change_date_known IS NOT NULL)),
  CHECK (change_date_known IS NOT TRUE OR change_effective_from IS NOT NULL),
  CHECK (change_date_known IS NOT FALSE OR change_effective_from IS NULL),
  CHECK ((origin = 'supplied') = (reference_set_id IS NOT NULL)),
  FOREIGN KEY (deployment_id, from_doc_id, from_version_id)
    REFERENCES document_versions (deployment_id, doc_id, version_id),
  FOREIGN KEY (deployment_id, from_version_id, reference_set_id)
    REFERENCES document_reference_sets (deployment_id, version_id, reference_set_id),
  FOREIGN KEY (deployment_id, to_doc_id) REFERENCES documents (deployment_id, doc_id)
);
CREATE INDEX ix_crossrefs_from     ON document_crossrefs (deployment_id, from_version_id, from_section_key, crossref_id);
CREATE INDEX ix_crossrefs_incoming ON document_crossrefs (deployment_id, to_doc_id, to_section_key, from_doc_id, from_version_id, crossref_id)
  WHERE to_doc_id IS NOT NULL;
CREATE INDEX ix_crossrefs_pending  ON document_crossrefs (deployment_id, to_source_kind, to_source_ref) WHERE to_doc_id IS NULL;
```

The composite foreign keys make it impossible for a row to name a version of another lineage
or a reference set of another version. There are no cascading delete actions (§9).

**Kinds** (each general; the analysis §4.4 gives the reasoning):

| Kind | Meaning | Examples |
| --- | --- | --- |
| `cites` | bibliographic citation from a reference list | a paper's DOI in its bibliography |
| `links_to` | a hyperlink found in the text | a URL in a wiki page |
| `attaches` | container membership | an e-mail attachment |
| `replies_to` | conversation threading | `In-Reply-To` |
| `refers_to` | an in-text pointer to another passage or document | "see section 4.2", `page#anchor`, "as defined in § 3" |
| `amends` | the source changes the target's text or force | contract amendment 2 changes clause 7 from 1 July; RFC "Updates: 7231"; an amending act |
| `implements` | the source elaborates or executes the target | an implementing regulation; a procedure implementing a policy; a profile of a standard |

An `amends` row always says whether its date is known: `change_date_known = true` with
`change_effective_from`, or `false` when the source states none (an erratum without a date).
Readers never present an unknown date as a timeline point.

**Binding.** A `floating` reference points at whichever target versions are in force when the
reference is read (§6.2). A `pinned` reference names one target version by its immutable
`version_key`; the mutable `source_version_ref` cursor is never used as an address. A pinned
reference to a target version that has no key cannot be expressed; callers who need pinning
assign version keys at ingest.

### 6.2 Reading references

Two operations, on HTTP, SDK and the MCP catalogue (D136 parity). Both read the private tables
through a fixed, authorization-checked operation query (`memory:read`), not through the open
query space.

**`document_references`** — input: either `chunk_id`, or `doc_id` with an optional
`section_key`; `direction` (`outgoing` | `incoming` | `both`, default `both`); optional `kinds`;
`time`; `k` (default 50, max 200); `cursor`.

*Source side.* With `chunk_id`, the source version is the chunk's version (a chunk id pins its
version) and the source section is the chunk's section; with `doc_id`, the source versions are
those `time` selects. Outgoing rows are references made by those versions from the section and
its descendants (descendant keys are resolved inside the version; at most 1,000 descendant keys
per call as a starting point, beyond which the call returns a `too_broad` negative asking for a
narrower section).

*Time window.* Each source version contributes its **source window**: its in-force intervals
(undeclared lineages: unbounded) intersected with the query window — the instant for
`current`/`at`, `[from, to]` for `overlap`, `(-∞, now]` for `history`. With
`chunk_id` and no `time`, the query window is `(-∞, now]` intersected with the chunk version's
intervals, i.e. "what did this passage point to while it was in force"; for an undeclared
lineage it is the instant `now`.

*Target resolution — a temporal join.* For each reference and each source window `W`:

- **floating, periodised target:** one result row per target version whose in-force interval
  intersects `W`, with `applies_during = interval ∩ W`. Several rows appear when the target was
  amended inside `W` or when target declarations overlap; rows whose `applies_during` overlap
  each other are marked `concurrent = true`. A point query (`current`, `at`) that finds two
  target versions in force returns both, marked concurrent — never one chosen silently.
- **floating, undeclared target:** one row for the served version, `applies_during = W`.
- **pinned:** one row for the version with the named `version_key`, `applies_during = W`, and
  the target version's own intervals shown beside it (a pinned target need not be in force).

Each result row returns: kind, `source_label`, `binding`, context, the source (doc, version,
section key and title, source window), the target (doc, version, representation, section key,
section title, first chunk ids, `applies_during`, `concurrent`) or a **status**:

| Status | Meaning |
| --- | --- |
| `resolved` | target version and (if named) section found |
| `target_unavailable` | no live lineage with the named identity — never ingested, deleted, or forgotten (one status for all three, so deletion is not revealed) |
| `target_not_in_force` | floating: the target lineage is live but no version is in force anywhere in `W` |
| `section_not_in_version` | the target version exists but lacks the named key |
| `pinned_version_unavailable` | pinned: no live version with the named key |

For every status the row includes the target **as named by the source** (`to_source_kind`,
`to_source_ref`, `to_version_key`, `to_section_key`): it is content of the live source document,
like the passage text that contains the reference. Nothing about an unavailable target beyond
what the source itself says is returned.

*Incoming side.* References whose `to_doc_id` is this lineage (and `to_section_key` is the
given key or one of its descendants' keys in any version of this lineage), made by source
versions that `time` selects in their own lineages; target resolution as above, restricted to
this lineage.

*Order and paging.* Rows are ordered by `(direction, source doc_id, source version_id,
crossref_id, applies_during start, target version_id)` and paged by an opaque keyset cursor
that also pins `evaluated_at` and `believed_at` (§3.6). The incoming query is an index range
scan on `ix_crossrefs_incoming`; the outgoing query on `ix_crossrefs_from`.

**`section_history`** — input: `doc_id`, `section_key`, `time` (default `history`), `k`,
`cursor`. Output, ordered by effective start (periodised) or `version_no` (undeclared): one row
per selected version — version id and number, `version_key`, in-force intervals, and either the
section (title, `own_content_hash`, `subtree_content_hash`, `changed` and `own_changed` relative
to the previous row that contained the key, first chunk ids) or `status = absent` when the
version lacks the key (a removed or repealed section). A version whose hashes are unknown
(pre-D140, not yet backfilled) returns `changed = null`. Incoming `amends` references that
target the section are listed with their `change_effective_from` and `change_date_known`.

The existing directed `graph_citation_path` keeps lineage grain (§7).

### 6.3 Caller-supplied references

`PUT /documents/{doc_id}/versions/{version_id}/references` (SDK `set_references`) with an
NDJSON body, one reference per line, **replacing the version's complete supplied set**. The
body is bounded by size (64 MiB as a starting point, to be measured), not by item count; a
larger set is rejected with `413`. Each item: `kind`, `from_section_key?`, `target:
{source_kind, source_ref, version_key?, section_key?}`, `binding`, `change_effective_from?`,
`change_date_known?` (required for `amends`), `source_label?`, `context?`.

- The body is stored as a content-addressed artifact of the version and a
  `document_reference_sets` row with `status = pending`. An identical set (same hash) is a
  no-op.
- **Validation is all-or-nothing.** Items are validated as JSON on receipt (malformed → `422`
  naming the line). Section keys are validated against the version's structure: synchronously
  when the version is ready, otherwise by the crossref sub-worker once structure exists. An
  unknown `from_section_key` rejects the **whole set** with `{item, field, reason}` errors; the
  set's status becomes `rejected` and the previously materialized set stays live. The source
  scope of a reference is never broadened.
- The E0 `crossref` sub-worker (D36) materializes a valid set into `document_crossrefs` rows
  with `origin = supplied` and, in the same transaction, marks the previous set `superseded`.
  Readers see only rows of the version's `materialized` set. This is how supplied references
  **write through E0** (Rule 3): the HTTP call records input; the pipeline writes rows.
- `GET /documents/{doc_id}/versions/{version_id}/references` returns the sets' statuses and
  errors.
- **Late binding**: `to_source_kind`/`to_source_ref` resolve to `to_doc_id` at materialization
  and, for rows still unresolved, when a lineage with that identity is first ingested (one
  indexed lookup on the ingest path, as D36 §4A specifies). Pinned version keys and section keys
  resolve at read time, so they need no binding step.

### 6.4 Extracted references

The D36 extraction rungs (URLs → `links_to`, containers → `attaches`, thread headers →
`replies_to`, reference-list mining and per-deployment citation grammars → `cites`, cheap-first
resolution with the small-model residue rung) write the same table with `origin = extracted`,
`from_version_id` set, spans filled, and — where a matched link carries an anchor
(`page#per-diem`) — `to_section_key` filled and kind `refers_to`. Extracted rows are keyed on
(version content, crossreferencer version) as D36 requires. Extracted rows are always
`floating` (extraction cannot know a version key). A deployment that supplies references for a
source kind may disable extraction for it by configuration.

### 6.5 Visibility

- The operations of §6.2 are the only surface that returns unresolved rows or a target as
  named by the source, and only for a live source version.
- The public view `document_crossrefs_live` keeps its rule: only rows whose source version and
  target lineage are live and resolved; it never exposes `to_source_*`, `to_version_key` or
  `raw_citation`.

## 7. Graph and open query space

- **Graph edge.** The `document_crossref` edge stays lineage to lineage. Its source view
  deduplicates to one edge per `(from_doc_id, to_doc_id, kind)` among resolved references made
  by the versions `versions_in_scope` selects under `current`; `crossref_id` is the smallest
  contributing row id. `graph_citation_path` keeps its current, structural semantics;
  time-scoped and section-level questions go through `document_references`.
- **Why sections are not graph vertices.** Section references are read one source or target at
  a time with keyset paging (§6.2); nothing needs multi-hop traversal at section grain, and the
  graph's purpose is entity relations. Adding millions of section vertices would cost memory and
  maintenance without a query that needs them.
- **Public relations and functions.** `memory_v1.document_effective_periods_live` and the
  function `memory_v1.effective_intervals(deployment_id, believed_at)` (§2.2);
  `memory_v1.versions_in_scope(…)` (§3.2); `sections_live` gains `section_key`,
  `own_content_hash`, `subtree_content_hash`; `chunks_live` gains `text_origin_at`;
  `chunks_all_versions_live` (§3.3); `document_crossrefs_live` gains `from_version_id`,
  `from_section_key`, `to_section_key`, `binding`, `origin`, `source_label`,
  `change_effective_from`, `change_date_known`; `memory_v1.fact_in_scope_support(…)` (§8).
  SQL functions name range bounds `range_start`/`range_end` (`from` is a SQL keyword); the
  wire time scope keeps its existing `from`/`to` fields.

## 8. Facts from periodised versions

### 8.1 The evidence gate (deterministic, read time)

Without a gate, a fact supported only by a claim from an edition not yet in force (or already
withdrawn) would be returned by `facts_context` as current, while chunk search correctly
excludes that edition. The gate closes this without touching fact windows:

- A supporting claim is **in scope** for a time scope when it has an occurrence in a version
  that the scope selects (§3.2) **or** an occurrence in a version of an undeclared lineage
  (whose evidence is not time-restricted by D140).
- Fact reads under a time scope — `facts_context`, `combined_context`, relation/observation
  lookups, graph neighbourhood and path expansion — return a fact only if it passes its own
  D118 window predicate **and** has at least one in-scope supporting claim. The returned
  evidence is limited to in-scope claims, shown through their in-scope occurrences (§3.4).
- The gate never changes stored counts (D54), windows (D118) or currency; it is a visibility
  rule on evidence, computed in the same statement as the fact read, over the result's bounded
  candidate set.
- **Conflicting dates.** The gate and the fact window are independent conditions and both
  must hold. If a claim's own validity says "from 2025" but its only occurrences are in an
  edition in force from 2026, a `current` read on 2025-06-01 does not return the fact (no
  in-scope evidence), even though its window contains the instant; an `at 2026-02-01` read
  returns it. Reconciling the window with the edition dates is adjudication's job (§8.2); the
  gate only guarantees that no answer rests solely on text that is not in force at the scope.
- The same predicate is published as `memory_v1.fact_in_scope_support(deployment_id, fact_kind,
  fact_id, mode, at, range_start, range_end, evaluated_at)` for open-query callers; the raw
  `facts_current` view is unchanged and its manifest comment says it does not apply the gate.

### 8.2 Adjudication input

A claim's occurrence **in-force interval set** — the union of the intervals of the versions it
occurs in — is an additional **adjudication input** for E3, shown beside the claim's own
validity. D118's single fact window stays the only fact-time authority: adjudication may use
the interval to propose or bound a window, exactly as it uses a claim's own dates. When a
lineage's declarations change (a new start, a declared end, a correction, a version deleted),
the engine enqueues ordinary D118 re-adjudication for facts supported by claims that occur in
the affected versions; windows then change through the normal mutable-window path, so open PR
#486's belief-time rule applies without special cases.

## 9. Deletion and forgetting

D135 deletes by soft tombstone (`document_versions.deleted_at`, `documents.deleted_at`,
`lifecycle.py` `_TOMBSTONE_VERSION` / `_TOMBSTONE_LINEAGE_BY_ID`); rows stay. D140 therefore
specifies visibility, not cascades:

- **Version deleted.** Its declarations stop taking part in interval derivation (§2.2 joins
  non-deleted versions); neighbours' derived ends recompute. Its chunks, claim occurrences,
  reference sets and outgoing references stop being readable (every read joins non-deleted
  versions). Incoming references whose pinned `version_key` named it return
  `pinned_version_unavailable`; floating references simply stop selecting it. The declaration
  ledger rows remain as history, as D135 retains all version rows.
- **Lineage deleted.** Everything above for all its versions; incoming references return
  `target_unavailable`. `to_doc_id` is kept: if the lineage is revived (D55/D135 resurrection of
  a tombstoned lineage), resolution works again without re-binding.
- **Hard forget (D74).** The forget worker explicitly deletes the lineage's period rows,
  reference-set rows and artifacts, and the references it made; for references *to* it made by
  other lineages it sets `to_doc_id = NULL, resolved = false` and keeps the source-named target
  (it is the source's content, like `raw_citation`), so a re-ingest can re-bind it. Deleting
  the chunk and section rows follows D74 unchanged; `text_origin_at` values on other lineages
  are unaffected (they never cross lineages).

## 10. Non-goals and scope boundaries

- **Amendment replay.** The engine stores and serves the texts a publisher releases; it does
  not reconstruct a text by applying edit instructions. Sources that publish only base text
  plus amendments need a client-side consolidator.
- **Inferred periods, keys or version keys.** Periods and version keys come only from callers;
  section keys only from the source text. A model never produces them.
- **Periods on `living` lineages** (§2.4).
- **Section vertices in the graph** (§7).
- **Domain vocabulary.** No enum value, flag or field is specific to legislation; callers keep
  their own taxonomies in `source_label`.

## 11. Worked example (Travel Policy)

1. `ingest(travel_v1.md, source_kind="intranet", source_ref="policy/travel",
   version_key="edition-1", effective_from=2024-01-01)` → version 1, interval
   `[2024-01-01, open)`.
2. `ingest(travel_v2.md, …, version_key="edition-2", effective_from=2026-01-01)` on 2025-11-15 →
   version 2. Version 1's interval is now `[2024-01-01, 2026-01-01)`. Chunks unchanged between
   editions match an edition-1 chunk's reuse identity, keep its text origin time (2023-12-10)
   and its extraction key, and are not re-extracted; only section 4 and its neighbours are.
   Version 2 becomes the served version once processed.
3. On 2025-12-01, `search_chunks("daily allowance")` (default `current`) returns edition 1's
   section 4 with `effective: [{from: 2024-01-01, until: 2026-01-01}]`, its version id, and
   `p3_path = null, served_version = false` (P3 now serves edition 2). `facts_context("per diem
   allowance")` returns the edition-1 allowance fact; the edition-2 allowance fact is withheld
   by the evidence gate even though edition 2 is served. With `time={"mode":"at",
   "at":"2026-02-01"}` both searches return edition 2.
4. `set_references(doc, v2, …)` with one NDJSON line `{"kind": "refers_to", "from_section_key":
   "approvals", "target": {"source_kind": "intranet", "source_ref": "policy/expense",
   "section_key": "approvals-process"}, "binding": "floating"}`. The Expense Policy is not
   ingested yet → `target_unavailable` with the target as named. When it is ingested, late
   binding fills `to_doc_id`; `document_references(doc_id, "approvals", time={"mode":
   "overlap", "from": "2026-01-01", "to": "2026-12-31"})` returns one row per
   Expense Policy edition in force during 2026, each with its `applies_during`.
5. On 2027-06-30 the company withdraws the policy: `set_effective_periods(doc, v2, [{from:
   2026-01-01, until: 2027-06-30}])`. From then on `current` returns nothing from this lineage
   and its facts lose their only in-scope evidence for `current`; `at 2026-05-01` still returns
   edition 2; `section_history(doc, "per-diem")` lists both editions and marks edition 2's
   section `changed = true`.
