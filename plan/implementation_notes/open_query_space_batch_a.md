# Open query space — Batch A implementation note

**Date:** 2026-08-04
**Binding design:** [`open_query_space_design.md`](../designs/open_query_space_design.md) §3.2, §3.3,
§6, §9.1–§9.4, §11.1
**Migration:** `p9_01_0022_memory_v1_query_space`
**Manifest:** `src/rememberstack/spine/query_space/memory_v1_manifest.json`
**`surface_manifest_hash`:** `fb9fd229064f5cb4…` (full value in the manifest)

Batch A is the schema contract: the `memory_v1` relation set, the machine-readable
manifest that describes it, the canonicalizer that turns that manifest into one stable
hash, and the executable proofs that the three row-level invariants really are compiled
into SQL rather than left to caller discipline. Nothing in this batch executes customer
SQL — roles, RLS, the parser, and the limits are Batch B — so the views are validated by
reading them directly with the migration role.

## 1. What is public, and what stays private

`memory_v1` contains exactly the 24 relations §3.2 enumerates, no more and no fewer.
Base tables, the projection views, and the operator schema stay in `public` and are not
part of the query space.

One private helper was added, and it is deliberately **not** in `memory_v1`:
`public.v_memory_entity_survivor` resolves an entity id to the terminal survivor of its
`merged_into` chain, with a depth guard because acyclicity is not schema-enforced. It
exists because a merge in this system is a *redirect* rather than a rewrite — the
absorbed entity keeps its id, and `relations` keep their original endpoint ids — so
every entity reference must be redirected before it is exposed or joined. Without it, a
merged entity's facts, aliases, and mentions would either disappear or point at an
identity that no longer resolves. It is a private subquery in view form, which §3.3
explicitly permits for helpers.

The existing catalog contract (`spine/catalog_contract.py`) now also locks the presence
of the `memory_v1` schema at head and its absence after a downgrade, so a leftover
schema cannot masquerade as the query surface.

## 2. How the invariants are compiled

### D48 — the authorization chain, and the branch that is forbidden

Every relation reaches a surviving `documents` row through an INNER JOIN or an `EXISTS`,
specialized to its own coordinates:

| Row kind | Chain |
|---|---|
| Lineage rows | `documents` with `deleted_at IS NULL` |
| Version-derived rows | additionally a `document_versions` row with `deleted_at IS NULL`, reached through `document_versions_visible` |
| Current-content rows | additionally equality with the lineage's current version and current ready representation, reached through `documents_live.has_current_ready_content` |
| Fact, entity, count, change, and K rows | `EXISTS` through their explicit provenance association to at least one surviving lineage |

The shipped legacy recipes contain the permissive form this design forbids —
`LEFT JOIN documents d … WHERE (d.doc_id IS NULL OR d.deleted_at IS NULL)` — whose
*absence* of a matching document **admits** the row. No `memory_v1` definition contains
it, and a gate test greps every deparsed definition for that shape.

Three left joins do appear, and they are a different thing: they project *optional
columns of an already authorized row* and are themselves fully predicated, so they can
only ever null a column, never admit a row. They are `documents_live`'s current
version/representation coordinates, `mentions_live`'s resolution and claim coordinates,
and `pages_live`'s parent artifact. In each case the fully-predicated join is what makes
the column null rather than dangling when the referenced object stops being visible.

**No dangling identifier.** A recurring choice throughout: an id column is exposed only
when the object it names is itself visible. `mentions_live.claim_id`,
`testimony_currency_events_visible.from_version_id`, `chunks_live.section_id`, and
`pages_live.parent_artifact_id` are all gated this way. The alternative — exposing the
raw column — leaks the identifier of a tombstoned object, and the §9.2 matrix catches
exactly that.

**Two views are fail-closed beyond the letter of the design, deliberately.**
`testimony_currency_events_visible` requires its claim to be visible, not merely its
lineage: after a version is tombstoned, a transition record about that version's claims
is version-derived state, and D48 says version-derived state is absent. The consequence
is that a `version_deleted` transition disappears together with the version it describes,
which is the fail-closed reading. `graph_edges_current` and
`graph_edges_visible_history` require *both* survivor endpoints to be present in
`entities_current`, so an edge drops as a unit rather than dangling into an entity with
no surviving provenance; this can make the graph projection slightly narrower than
`facts_current` filtered to relations, which is the intended direction.

**`pages_live` gates on visibility, not on citation provenance.** §3.3's general
sentence lists "K rows" among the rows that need an `EXISTS` through provenance, while
its boundary table states the specific K rule as *"expose links only when each target is
visible"*. The specific rule governs, for two reasons. First, requiring a live citation
would hide an uncited authored page, which D46 explicitly allows to exist. Second, the
leak the general rule guards against — compiled prose about forgotten content — is
already closed upstream: the D74 hard-forget purge nulls `page_summary`, `content_hash`,
`inputs_hash`, and `last_compiled_at` on every affected artifact and marks compiled pages
stale, in the same transaction that deletes the citations. So the artifact is gated on
visibility and every *link* is gated on its target, which is exactly what the boundary
table says.

### D41 — one predicate, one instant

`facts_current` is literally the §3.3 predicate applied to `facts_visible_history`:

```sql
FROM (SELECT statement_timestamp() AS evaluated_at) AS clock
CROSS JOIN memory_v1.facts_visible_history AS h
WHERE h.ingested_at <= clock.evaluated_at
  AND h.invalidated_at IS NULL
  AND (h.valid_from  IS NULL OR h.valid_from  <= clock.evaluated_at)
  AND (h.valid_until IS NULL OR h.valid_until >  clock.evaluated_at)
```

`statement_timestamp()` is fixed for the duration of one SQL statement, so every
reference to a current relation inside one statement — `facts_current`,
`contradiction_members_current`, and `graph_edges_current`, the latter two defined over
the first — observes the same `evaluated_at`, and that value is emitted on every row. A
gate test reads all three in one statement and asserts the instants are equal, and reads
`facts_current` in a second statement and asserts the instant advanced.

World validity is half-open, `[valid_from, valid_until)`. Claim validity is a different
relation with a different rule: it is immutable source testimony, and the shipped
inclusive overlap predicate stays inclusive because an `instant`-precision claim has
`claim_valid_from = claim_valid_until`, which a half-open convention would make empty.
The two conventions live in different views and both are stated in the view comments.

### D54 — counting, and the state that is never stored

`evidence_lineage` is one row per fact × current-testimony document lineage × stance,
aggregated from `fact_claim_evidence_live`, which in turn joins `claims_live`. An
evidence count is therefore the number of rows here, which is by construction the number
of distinct current-testimony source lineages. Repetition inside one document and
re-extraction of one document both leave the counts unchanged; a genuinely independent
second source moves the matching count by one. `claim_count` on `evidence_lineage` is
descriptive colour about how loudly one lineage says it and is documented as never
summable into an evidence count.

`support_state` is derived at read time from the open review-queue row, exactly as the
shipped query engine derives it:

```sql
EXISTS (SELECT 1 FROM review_queue q
        WHERE q.deployment_id = f.deployment_id
          AND q.item_kind = 'support_withdrawn'
          AND q.status IN ('pending', 'deferred')
          AND q.candidate ->> 'fact_id' = f.fact_id::text)
```

There is no stored support-state column anywhere in the query space, so nothing can
drift from that queue. The cached `relations.evidence_count` column is *not* read by any
view: the counts come from `evidence_lineage`. A gate test flags a fact, observes
`withdrawn` in `facts_current`, `facts_visible_history`, and `graph_edges_current`,
observes that `relations.evidence_count` is untouched, closes the queue row, and observes
`current` again.

`facts_visible_history` requires surviving *historical* provenance rather than current
support, so a processing-withdrawn fact remains visible with zero current support — a
case the gate exercises directly.

**Naming.** Current surfaces use `evidence_count` / `contradict_count` /
`support_state`; history and as-of surfaces use the `_current` suffix, and their comments
state in full sentences that those are live current-testimony values read now and never
historical reconstructions.

## 3. Per-view notes worth reading before changing anything

- **`documents_live`** is the entry point every current-content relation joins:
  `has_current_ready_content` is the single boolean that means "ready current version and
  ready current representation", so the current-content rule is written once.
- **`sections_live`** and **`chunks_live`** additionally require the D79 *current
  structure generation*. Without that, a representation with two generations would
  produce two trees and `node_path` would no longer identify one section. A chunk's
  `section_id` is nulled rather than dangling when the section belongs to a superseded
  generation.
- **`claims_live`** is defined as `claims_visible_history` filtered by
  `is_current_testimony`, so the two relations cannot drift.
- **`claim_occurrences_live`** collapses repeated attachments of the same claim to the
  same chunk under the same derivation with `DISTINCT ON`, keeping the earliest
  `attached_at`. The base table's primary key includes `created_at`, so two attachments
  are representable; without the collapse the declared key would not be unique. Null
  derivation kinds are treated as equal, which the view comment states.
- **`entity_document_mentions`** counts only mentions that are themselves visible: the
  mention's chunk must resolve to a non-tombstoned version of a live lineage. That is
  stricter than a lineage-only gate and is what makes the version cell of the deletion
  matrix pass rather than counting mentions of a tombstoned version.
- **`entities_current`** requires surviving provenance — a live mention or a live
  document-entity bridge — so an entity whose every source was forgotten disappears
  instead of becoming an orphan. `graph_degree` is copied from the latest published graph
  snapshot and is documented as orientation that can lag.
- **`identity_events_visible`** and **`changes_visible`** are UNION views whose declared
  key is `(deployment_id, object_kind, event_id)`. Uniqueness across the underlying id
  spaces is proven by construction: **every arm emits a distinct `object_kind` literal
  and supplies an identifier from its own source table**, so two arms can never collide
  even when their id spaces overlap. `changes_visible` has eight arms
  (`relation_ingest`, `relation_invalidation`, `relation_supersession`, the three
  observation equivalents, `claim_ingest`, `knowledge_page_compilation`); the two
  invalidation arms and the two ingest arms share a fact id space and are separated by
  the literal alone. This is the manifest obligation §3.2 assigns to Batch A.
- **`changes_visible` has no deletion arm at all.** Forgetting a lineage, tombstoning a
  version, or retiring a page *removes* the affected events; it never announces the
  removal. Labels are drawn only from objects that pass their own gate, so neither the
  event set nor the label text can become a tombstone side channel.
- **`page_evidence_visible`** required the one genuine design-to-schema reconciliation in
  this batch. The declared key is
  `(deployment_id, artifact_id, role, target_kind, target_id)`, but a K claim citation is
  stored as a *coordinate* — `(claim_lineage_id, claim_chunk_content_hash)` — and one page
  can legitimately cite two chunk coordinates inside one lineage under one role. Exposing
  one row per underlying link would make the declared key non-unique. The compilation
  follows the design's own words — *"target passes its own visibility gate; chunk hash is
  a locator, not an authorization bypass"* — literally: the **lineage** is the target and
  the authorization gate, the chunk hashes are exposed as a sorted `text[]` **locator**
  array inside that already-authorized lineage, and `link_count` reports how many
  underlying links were collapsed. The hash never authorizes anything on its own, which
  is the property the design names.
- **`document_crossrefs_live`** exposes a `context` bounded to 500 characters and
  deliberately omits `raw_citation`, which is retained even after a target is forgotten
  and would therefore be a leak path.

## 4. Index usage

No index was added by this batch. Every authorization chain is an equality lookup on an
existing key or a covered partial index; the manifest records, per view, which indexes
its chain relies on (in the non-hashed `annotations` section, since physical indexes are
explicitly excluded from the hash). The main ones:

| Chain | Index |
|---|---|
| lineage authorization | `documents_pkey`, `ix_documents_live` |
| version authorization | `document_versions_pkey` |
| current-content coordinates | `ix_chunks_doc`, `ix_chunks_version`, `ix_chunks_section`, `ix_sections_doc`, `uq_sections_generation_path` |
| claim authorization | `ix_claims_doc`, `ix_claims_chunk`, `ix_claims_current` |
| evidence aggregation | `relation_evidence_pkey`, `observation_evidence_pkey` (hash-partitioned on the fact id, so one fact prunes to one partition) |
| live resolution | `ix_resdec_live`, `ix_resdec_entity_live` |
| support state | `ix_review_pending` |
| K citations | `ux_kae_link`, `ix_kae_claim_coordinate`, `ix_kae_relation`, `ix_kae_doc` |

**The one measurement obligation this batch leaves open** is
`v_memory_entity_survivor`. It is a recursive CTE over the whole entity registry, and
PostgreSQL has no index that helps a recursive walk, so a query joining it pays for a
full survivor resolution. At fixture and small-corpus scale this is invisible; at the
target scale it is a real cost on entity-bearing queries. It is a candidate for a
materialized survivor table maintained by the merge path. That is a measurement, not a
guess — no number is committed here, and Batch B's plan work is the natural place to take
it.

## 5. The manifest, the canonicalizer, and the hash

`src/rememberstack/spine/query_space/` holds four pieces:

- **`canonical.py`** — RFC 8785 canonical JSON. Python's `json.dumps(sort_keys=True)` is
  deliberately not used for hashed bytes: it sorts by Unicode code point rather than
  UTF-16 code unit, and those orders disagree for names outside the Basic Multilingual
  Plane. A non-integer float raises rather than being rounded, because the RFC's number
  rule is the shortest ECMAScript round-trip form and approximating it would make the
  hash a guess. A test covers the code-unit ordering case explicitly.
- **`ast_serializer.py`** — the pinned canonical AST serialization, version
  `memory_v1.ast/1`. **Raw SQL text is never a hash input.** PostgreSQL does not store
  the SQL a migration typed; it stores the parse tree, and `pg_get_viewdef` prints that
  tree back — comments gone, whitespace normalized, implicit casts made explicit, type
  names already canonical `pg_catalog.format_type` output. That printed parse tree is the
  serializer's input. It is then lexed into a token tree and written as one s-expression
  (`(w:select w:f o:. w:fact_id …)`), which makes the result insensitive to line breaks,
  indentation, keyword case, and comments, and sensitive to every semantic difference.
  Checked-in golden vectors pin the exact output, plus *equivalence* cases (formatting and
  comments cannot change it) and *distinction* cases (`>` versus `>=`, `JOIN` versus
  `LEFT JOIN`, quoted-identifier case must change it).
- **`catalog.py`** — the declared half of the contract: nullability, row key, join keys,
  grain phrase and grain tag, clock-semantics tag, bound vocabularies, index usage, and
  the two fixture case ids per view.
- **`manifest.py`** — the generator. Everything it can read from the database it reads
  from the database: ordered columns, `format_type` type names, view and column comments,
  and the definition AST. Everything PostgreSQL cannot report comes from `catalog.py`.

**Why nullability is declared rather than introspected.** PostgreSQL does not track
nullability for view columns — `pg_attribute.attnotnull` is `false` for every view column
regardless of the expression behind it, and `information_schema` reports `YES`
unconditionally. There is no catalog source to compare against. The contract therefore
*declares* nullability and the gate *executes* it: for every column declared non-null,
the gate asserts zero nulls across the fixture corpus, and it first asserts that each view
has at least one fixture row so the check cannot pass vacuously. The remaining halves of
§9.1 — ordered columns, canonical types, comments, and the definition AST — are compared
against live introspection exactly.

**The hashed document** is exactly the four §6 members. `views_schema` is populated;
`function_signatures`, `core_operation_descriptors`, and `limits` carry their bound
structure with no entries, because the SQL-callable functions, the assured-operation
descriptors, and the grammar and resource limits are contributed by later work. Binding
the shape now means those additions *fill* the hashed document rather than reshape it.
Index usage and fixtures live in a separate `annotations` section that is not hashed, so
adding an index cannot roll the hash.

## 6. The legacy/orphan quarantine report

The public views are fail-closed, which is right for a caller and wrong for an operator:
absence in a query surface looks exactly like absence in the corpus. `quarantine.py`
closes that gap without weakening the surface. Ten probes count rows that exist in the
base tables but can never reach `memory_v1`, each with the operator-facing meaning and
the repair it implies:

`claim_without_chunk`, `chunk_without_version`, `section_outside_current_generation`,
`mention_without_chunk`, `evidence_lineage_mismatch`,
`fact_without_surviving_provenance`, `entity_without_surviving_provenance`,
`crossref_without_live_endpoints`, `knowledge_citation_without_visible_target`,
`currency_event_without_live_lineage`.

The report is operator-only by construction: it is not part of `memory_v1`, it is not
reachable from any agent surface, and it emits counts and repair guidance — never corpus
content — so nothing in it can be joined back into a public result. Orphans stay omitted
from every public path until they are repaired; the report only makes them countable.

## 7. Verification results

All commands ran against PostgreSQL 16 in the repository's pinned image.

**§9.1 — DDL/manifest identity.** Runtime introspection equals the checked-in manifest
byte for byte, including the rendered file. Two independent generator runs produce the
same hash and the same rendering. `memory_v1` has no ACL on the schema, no ACL on any
relation, and no `PUBLIC` grant. All 24 declared row keys are unique on the corpus. No
column declared non-null is ever null, and every view has fixture rows. Every bound
vocabulary covers the values that actually occur. Every view comment is a complete
sentence over 200 characters and every column comment is a complete sentence. No
definition contains the legacy orphan branch. Every declared join key resolves to a real
column on a real `memory_v1` relation. All 48 manifest fixture cases (one positive and
one negative per view) are executed and pass, and the gate asserts the executed set
equals the declared set exactly.

**§9.2 — D48 deletion matrix.** The artifact
`src/rememberstack/spine/query_space/d48_deletion_matrix.json` enumerates 6 targets ×
24 surfaces = **144 cells**, is generated by `build_matrix()`, and is checked in; a test
asserts the file equals its generator verbatim. Every cell is executed: the target's
mutation is applied inside a transaction, every row of every column of the surface is
scanned for any identifier in that target's forbidden set, and the result must be empty.
The run additionally asserts that each target's forbidden identifiers *were* reachable
before the mutation, so a cell cannot pass vacuously. 144/144 pass — no implicit or
sampled cells. A separate fixture proves a composed fact → evidence → claim → source
path drops as a unit when any hop stops being visible.

**§9.3 — D41 clocks.** `facts_current` is proven to equal the hand-written §3.3 predicate
evaluated at `statement_timestamp()` within one statement. Boundary equality at
`valid_from` is inclusive and `valid_until` is exclusive, checked at the exact
microsecond and one microsecond inside. Null endpoints are unbounded on both sides.
Future ingestion is not yet believed. Invalidation excludes at equality, not after it.
Distinct `valid_at` and `believed_at` select different membership. One statement observes
one shared instant across `facts_current`, `graph_edges_current`, and
`contradiction_members_current`, and a later statement advances it. No claim id is ever
a current fact id, and `claims_live` exposes no `evaluated_at`, `support_state`, or
`evidence_count` column. Claim-evidence overlap is inclusive at both endpoints, and an
`instant` claim with equal endpoints still matches.

**§9.4 — D54 lifecycle.** Repetition inside one source leaves both counts unchanged.
Re-extracting one lineage leaves both counts unchanged. A second lineage moves the
matching count by one and leaves the other alone. Both counts equal the distinct-lineage
counts from `evidence_lineage`, and the two stances stay distinct. Processing loss alone
opens `withdrawn` across the current, history, and graph surfaces without touching the
cached column, and closing the queue row restores `current`. A `deferred` row still counts
as open. Source deletion reduces counts and never opens the flag, and a zero count never
manufactures a withdrawal. A processing-withdrawn fact stays visible in history with zero
current support.

**Suite, types, lint, migration cycle.** The full `src/tests/` suite is green with no new
skips; `pyright src/ benchmarks/` reports 0 errors; `ruff check` and `ruff format --check`
are clean; and the migration's up/down/up cycle passes through the existing
`test_migrations.py` lifecycle test, which now asserts the head revision is `p9_01_0022`
and that a downgrade leaves no `memory_v1` schema behind.

## 8. What Batch A deliberately does not contain

No roles, no RLS policies, no SQL parser or allowlist, no executor, no limits, no
`QueryResult`, no set-returning function, no Cypher, no registry, and no API, SDK, CLI,
or MCP surface. `memory_v1` is not reachable by a customer until Batch F, which is why
this batch ships no website page: the documentation obligation for the schema contract is
this note.
