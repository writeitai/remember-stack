# Open query space — Batch A implementation note

**Date:** 2026-08-04
**Binding design:** [`open_query_space_design.md`](../designs/open_query_space_design.md) §3.2, §3.3,
§6, §9.1–§9.4, §11.1
**Migration:** `p9_01_0022_memory_v1_query_space`
**Manifest:** `src/rememberstack/spine/query_space/memory_v1_manifest.json`
**`surface_manifest_hash`:** `01ecaf182c58ed7d…` (full value in the manifest)

Batch A is the schema contract: the `memory_v1` relation set, the machine-readable
manifest that describes it, the canonicalizer that turns that manifest into one stable
hash, and the executable proofs that the three row-level invariants really are compiled
into SQL rather than left to caller discipline. Nothing in this batch executes customer
SQL — roles, the parser, and the limits are Batch B — so the views are validated by
reading them directly with the migration role.

## 1. What is public, and what stays private

`memory_v1` contains exactly the 24 relations §3.2 enumerates, no more and no fewer.
Base tables, the projection views, and the operator schema stay in `public` and are not
part of the query space.

Three private helpers were added, and they are deliberately **not** in `memory_v1`:

- `public.v_memory_entity_survivor` resolves an entity id to the terminal survivor of
  its `merged_into` chain. It exists because a merge in this system is a *redirect*
  rather than a rewrite — the absorbed entity keeps its id, and `relations` keep their
  original endpoint ids — so every entity reference must be redirected before it is
  exposed or joined. Without it, a merged entity's facts, aliases, and mentions would
  either disappear or point at an identity that no longer resolves.
- `public.v_memory_mention_current_content` is the single definition of "this mention
  occurs in current content, and this is the survivor it resolves to". Both
  `mentions_live` and `entity_document_mentions` are projections of it.
- `public.v_memory_page_citation_visible` is the single definition of "this citation's
  target is still visible". Both the membership gate of `pages_live` and the per-link
  projection of `page_evidence_visible` read it.

The last two exist for one reason: a rule that two public relations state separately is
a rule that can drift, and both of the drifts it would allow were real. Stating the rule
once and projecting it twice makes the count equal to the transcript it counts, and
makes a page's membership equal to the links it can show, *by construction* rather than
by review. They are private subqueries in view form, which §3.3 explicitly permits for
helpers.

**The Batch B rule for the helpers.** None of the three is ever granted to a query role,
and none is ever placed on a query role's `search_path`. They are implementation detail
of the public relations, not a supported surface: a caller reaches their rows only
through `memory_v1`, whose gates the §9.2 matrix executes cell by cell. The survivor
helper in particular *must* keep resolving an entity whose provenance is gone, because
`entities_current` computes that provenance from it — which is exactly why it must never
be readable directly. Batch B's role work is where that rule is enforced in SQL; Batch A
enforces the half it can, by keeping the helpers out of `memory_v1` and granting nothing.

The existing catalog contract (`spine/catalog_contract.py`) locks the presence of all
three helper views alongside the `memory_v1` schema at head, and their absence after a
downgrade, so a leftover schema or a leftover helper cannot masquerade as the query
surface. A gate reads `pg_class.relacl` and `information_schema.role_table_grants` for
each helper and asserts there is no grant on any of them.

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
it, and a gate test greps every deployed definition for that shape. That grep reads
`pg_get_viewdef`, which is fine for a grep: it is only the *hash* that must never see
printer output (§5), and reading the deployed text is what makes the check about what is
actually running.

A few left joins do appear, and they are a different thing: they project *optional
columns of an already authorized row* and are themselves fully predicated, so they can
only ever null a column, never admit a row. They are `documents_live`'s current
version/representation coordinates, the mention helper's resolution and claim
coordinates (with `mentions_live`'s survivor gate on top of them), and `pages_live`'s
parent artifact, whose join carries the parent's own status and citation gates. In each
case the fully-predicated join is what makes the column null rather than dangling when
the referenced object stops being visible.

**Every coordinate of an association is bound, not just the one that looks primary.**
A mention names *two* coordinates — a chunk and a document lineage — and both are part
of the association. Authorizing only the chunk leaves a hole: a mention row whose own
`doc_id` names a forgotten lineage is still published, under the live lineage of its
chunk, which is precisely the identifier the forget was meant to remove. `mentions_live`
therefore joins `chunks_live` on `chunk_id` **and** `doc_id`, and the claim coordinate is
gated the same way — the claim must be a visible claim *of that lineage*. The rule
generalizes: when a row carries several coordinates of one association, each is a
separate authorization obligation.

**No dangling identifier, and no dangling description of one.** A recurring choice
throughout: an id column is exposed only when the object it names is itself visible.
`mentions_live.claim_id`, `testimony_currency_events_visible.from_version_id`,
`chunks_live.section_id`, and `pages_live.parent_artifact_id` are all gated this way.
`mentions_live` extends the rule from the identifier to everything that *describes* it:
`resolution_method`, `resolution_confidence`, `resolution_is_new_entity`, and
`resolved_at` are published together with `resolved_entity_id` or not at all. Publishing
the metadata while nulling the id would say "a tier-0 resolver decided this at 09:00 with
confidence 0.9" about an identity this schema will not show — a description of a decision
whose subject is invisible, which is the same leak in prose form. A retired, merged-away,
or provenance-free survivor therefore leaves the whole resolution null, and the mention
stays visible as unresolved, which is what §3.2 requires of it.

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

**`pages_live` gates on citation provenance as well as on status.** §3.3's general
sentence lists "K rows" among the rows that need an `EXISTS` through provenance to at
least one surviving lineage, and its boundary table adds the per-link rule *"expose links
only when each target is visible"*. Both apply: the artifact needs provenance, and each
link needs its own target. So membership in `pages_live` requires a non-tombstoned status
**and** at least one citation whose target still passes its own gate, and
`page_evidence_visible` publishes exactly those citations. A page whose every cited
source has been forgotten leaves with them, rather than surviving as compiled prose about
content this deployment can no longer show.

D46 (decisions.md, "both K page kinds carry citations") is what makes the fail-closed
reading the right one rather than an over-reach: a knowledge artifact with no citation at
all is not an ordinary uncited page, it is an anomaly — a compile that produced prose
without recording where it came from. Anomalies belong in the operator quarantine report,
which now counts them (`page_without_visible_citation`), not on the public surface.

> **Withdrawn.** An earlier version of this note argued the opposite: that the specific
> rule governed alone, because requiring a citation would hide an uncited authored page
> and because the D74 hard-forget purge already scrubs `page_summary` and marks compiled
> pages stale. That reasoning is incorrect and is withdrawn. D46 says both page kinds
> carry citations, so the "uncited authored page" it was protecting is an anomaly rather
> than a legitimate state; and the D74 purge closes the *prose* leak on the forget path,
> which is not the same as gating membership — it says nothing about an artifact whose
> citations were removed by any other route, and a surface must not depend on a writer
> elsewhere having done the scrubbing. Fail-closed membership is the binding reading.

**Merge resolution fails closed, because a merge chain is not guaranteed to terminate.**
`entities.merged_into` records a redirect, and nothing in the schema prevents two merges
being recorded in opposite directions (A into B, B into A), a chain longer than any
guard, or a redirect to a row that no longer exists. The walk in
`v_memory_entity_survivor` therefore keeps a depth bound *and* only emits a row when the
walk actually reaches an entity with `merged_into IS NULL`. Taking the furthest row
reached instead — which is what a `DISTINCT ON … ORDER BY depth DESC` does — is
fail-open: in a two-node cycle each entity becomes its own "survivor" and both are
republished as if no merge had happened, with a caller unable to tell which identity is
canonical. Fail-closed means the entity resolves to nothing: it is absent from
`entities_current` and from every relation that joins a survivor — including
`facts_visible_history`, so a fact with an unresolvable endpoint drops as a unit rather
than dangling — and the anomaly is counted in the operator quarantine report
(`entity_merge_chain_unresolved`) so the omission is visible to someone who can repair
it.

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
- **`entity_document_mentions`** counts exactly the mentions this deployment can still
  show: one for every row of `mentions_live` in that lineage whose resolution names that
  survivor, and nothing else. §3.2 binds it to an *exact live mention count*, and both
  halves of that phrase matter. A mention in a version that has been superseded is not
  live content — `mentions_live` does not publish it and `chunks_live` does not publish
  its chunk — so counting it would report a number the caller cannot reconcile with any
  relation in the schema. Counting through the shared helper rather than through a
  second, looser chain of its own is what makes the identity mechanical rather than
  reviewed: the count *is* the number of transcript rows, and a gate asserts that
  equality on every pair. `entities_current.live_mention_count` sums these counts, so it
  inherits the same meaning instead of a differently-derived one.
- **`entities_current`** requires surviving provenance — a mention in current content or
  a live document-entity bridge — so an entity whose every source was forgotten
  disappears instead of becoming an orphan. `graph_degree` is copied from the latest
  published graph snapshot and is documented as orientation that can lag.
  **One consequence is worth stating plainly**, because it follows from counting current
  content and is not obvious: an entity whose *only* mention sits in a superseded version
  of a live lineage is now absent, where a version-level gate would have kept it. That is
  the fail-closed direction — the entity has no place in current content to point at, and
  the alternative is a second, looser provenance chain beside the counting one, which is
  the drift this batch just removed. The quarantine probe
  `entity_without_surviving_provenance` was rewritten against the same helper for the
  same reason — an operator report that counted a *different* set from the one the view
  omits would be worse than no report — so such entities are omitted from the surface and
  countable in the report.
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
- **`v_memory_page_citation_visible` is written as two UNION arms rather than one scan
  with an `OR` of two gates**, and that is a planner fact worth knowing before someone
  "simplifies" it back. A disjunction of two `EXISTS` subqueries cannot be planned as two
  semi-joins: PostgreSQL falls back to evaluating each side as a materialized subplan, and
  because one side is `facts_visible_history` — which itself resolves survivors and
  aggregates `evidence_lineage` — reading `pages_live` on a four-artifact fixture took
  **7.9 seconds**. The same predicate split into one arm per target class, each with its
  own `EXISTS`, reads in **0.02 seconds**. Nothing about the semantics changed; only the
  shape the planner is given.
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
| mention placement and live resolution | `ix_mentions_doc`, `ix_chunks_doc`, `ix_resdec_mention`, `ix_resdec_live`, `entities_pkey` |
| support state | `ix_review_pending` |
| K citations and the page provenance gate | `ux_kae_link`, `ix_kae_claim_coordinate`, `ix_kae_relation`, `ix_kae_doc` |

**The measurement obligations this batch leaves open.** Two, both about shape rather
than about a missing index.

`v_memory_entity_survivor` is a recursive CTE over the whole entity registry, and
PostgreSQL has no index that helps a recursive walk, so a query joining it pays for a
full survivor resolution. At fixture and small-corpus scale this is invisible; at the
target scale it is a real cost on entity-bearing queries. It is a candidate for a
materialized survivor table maintained by the merge path — which would also make the
fail-closed rule a stored fact rather than a per-query walk.

`facts_visible_history` is the other. Its `CROSS JOIN LATERAL` over `evidence_lineage`
computes both D54 counts for every row, and a caller who only needs membership — an
`EXISTS`, a join for the fact id — still pays for them, because PostgreSQL will not skip
a lateral aggregate whose output is unused. That is what made the citation gate's
`OR`-shaped predicate collapse into seconds before it was split into arms (§3). Both are
measurements, not guesses: no number is committed here, and Batch B's plan work is the
natural place to take them.

## 5. The manifest, the canonicalizer, and the hash

`src/rememberstack/spine/query_space/` holds five pieces:

- **`canonical.py`** — a **restricted subset of RFC 8785** canonical JSON, and it is
  labelled as a subset rather than as the scheme. The admitted types are the ones the
  manifest contains: objects, arrays, strings, exactly-representable integers, booleans,
  and null. The RFC's number rule — the shortest ECMAScript round-trip form of an
  IEEE-754 double — is deliberately *not* implemented, and a non-integer float raises.
  Half-implementing that rule would produce bytes that agree with a conforming
  canonicalizer on the values we happen to test and disagree on some value we do not,
  which is the worst possible property for a hash; refusing the type means the manifest
  can never contain a value we would canonicalize differently from the RFC. Within the
  admitted types the output is conformant, so another language's conforming
  implementation reproduces these bytes. Python's `json.dumps(sort_keys=True)` is not
  used for hashed bytes: it sorts by Unicode code point rather than UTF-16 code unit, and
  those orders disagree for names outside the Basic Multilingual Plane; a test covers
  that case explicitly.
- **`ast_serializer.py`** — the pinned canonical AST serialization, version
  `memory_v1.pglast_ast/1`. **Raw SQL text is never a hash input, and neither is any
  rendering of it.** The input is the *authored* `CREATE VIEW` statement — the DDL string
  the migration executes — parsed by PostgreSQL's own parser through `pglast`, a binding
  to `libpg_query` (the real PostgreSQL grammar compiled as a library, pinned to the
  major that matches the declared server major). The resulting parse tree is
  canonicalized into JSON: `location` fields are dropped, because they are byte offsets
  into the input text that any added space or comment would move, and an enumerated
  field's numeric `value` is dropped in favour of its `name`, because the number is an
  ordinal in a C header rather than a meaning. Comments and whitespace never appear at
  all — the parser discards them before a tree exists. Checked-in golden vectors pin the
  exact output for eight representative statements, plus *equivalence* cases (layout,
  comments, keyword case, and `JOIN` versus `INNER JOIN` cannot change it) and
  *distinction* cases (`>` versus `>=`, `JOIN` versus `LEFT JOIN`, the legacy permissive
  orphan branch, quoted-identifier case, a reordered column list, and a renamed published
  column must change it).
- **`source_definitions.py`** — the authored DDL read as the canonical source: per public
  relation, the `CREATE VIEW` statement, its ordered output-column names, its relation
  comment, and each column's comment, all extracted from the migration's own strings.
- **`catalog.py`** — the declared half of the contract: column types, nullability, row
  key, join keys, grain phrase and grain tag, clock-semantics tag, bound vocabularies,
  index usage, and the two fixture case ids per view.
- **`manifest.py`** — the generator, which **takes no database connection at all**, plus
  the live comparison that does.

**Why the hash is computed from source, and what the database is then for.** The earlier
version of this batch hashed a serialization of `pg_get_viewdef()` output. That was
wrong twice over. It made the hash depend on PostgreSQL's *view printer* — an
implementation detail that has changed spelling across releases — when §6 requires the
hash to be independent of the server version; and it made the hash uncomputable without a
running server, so "two independent builds agree" could only ever be tested by opening
two connections to one database, which proves nothing about reproducibility. Now:

- **The manifest is built from the repository alone.** `build_manifest()` parses the
  authored DDL and reads the declared contract. Any checkout recomputes the same 64
  characters with no server running, and a database-free test asserts the checked-in hash
  equals that recomputation.
- **Two independent builds are two separate scratch databases.** The gate creates two
  throwaway databases, migrates each to head, reads each back from `pg_catalog`, and
  asserts the two deployed surfaces are byte-identical to each other and equal to what
  the manifest publishes. Nothing is shared but the repository, so object identifiers,
  creation order, and timing all differ and the comparison is about the contract.
- **Live introspection is the independent side of a comparison, not an input.**
  `live_schema_differences()` reads the deployed relation set, ordered columns,
  `format_type` type names, and every relation and column comment from `pg_catalog` and
  compares them to the checked-in manifest, reporting each disagreement. The live side
  reads no declaration, which is the point: a wrong declared type or a stale comment
  fails, instead of being compared with itself.

**Why column types and nullability are declared.** Only PostgreSQL can resolve the type
of a view's output expression, and the manifest must be computable without PostgreSQL —
so the types are declared in `catalog.py` in published column order and *proven* against
`pg_catalog.format_type` by the gate above. Nullability has no catalog source at all:
`pg_attribute.attnotnull` is `false` for every view column regardless of the expression
behind it, and `information_schema` reports `YES` unconditionally. It is therefore
declared and proven by *execution*: for every column declared non-null the gate asserts
zero nulls across the fixture corpus, having first asserted that each view has at least
one fixture row so the check cannot pass vacuously. Row keys are proven the same way, by
executing a uniqueness check per declared key.

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
closes that gap without weakening the surface. Twelve probes count rows that exist in the
base tables but can never reach `memory_v1`, each with the operator-facing meaning and
the repair it implies:

`claim_without_chunk`, `chunk_without_version`, `section_outside_current_generation`,
`mention_without_chunk`, `evidence_lineage_mismatch`,
`fact_without_surviving_provenance`, `entity_without_surviving_provenance`,
`entity_merge_chain_unresolved`, `crossref_without_live_endpoints`,
`knowledge_citation_without_visible_target`, `page_without_visible_citation`,
`currency_event_without_live_lineage`.

The last two of those are the counterpart of the two fail-closed rules this batch
tightened. A merge chain that never terminates and a knowledge page with no visible
citation are both states where the *right* behaviour is to publish nothing and the
*wrong* behaviour is to say nothing: without a probe, an operator would see a page or an
entity vanish from every query with no way to learn why. `page_without_visible_citation`
reads the same private helper `pages_live` gates on, so the count and the omission cannot
describe different sets.

The report is operator-only by construction: it is not part of `memory_v1`, it is not
reachable from any agent surface, and it emits counts and repair guidance — never corpus
content — so nothing in it can be joined back into a public result. Orphans stay omitted
from every public path until they are repaired; the report only makes them countable.

## 7. Verification results

All commands ran against PostgreSQL 16 in the repository's pinned image.

**§9.1 — DDL/manifest identity.** Live introspection from `pg_catalog` alone — relation
set, ordered columns, `format_type` type names, and every relation and column comment —
equals the checked-in manifest with zero differences, and the checked-in file is byte for
byte what the generator renders. The hash recomputes from source with no database at all.
Two separate scratch databases, each migrated to head independently, deploy byte-identical
surfaces that both equal the manifest. The golden vectors reproduce exactly, including
the equivalence and distinction cases. `memory_v1` has no ACL on the schema, no ACL on any
relation, and no `PUBLIC` grant; the three private helpers have no ACL either. All 24
declared row keys are unique on the corpus. No column declared non-null is ever null, and
every view has fixture rows. Every bound vocabulary covers the values that actually occur.
Every view comment is a complete sentence over 200 characters and every column comment is
a complete sentence. No definition contains the legacy orphan branch. Every declared join
key resolves to a real column on a real `memory_v1` relation. All 48 manifest fixture
cases (one positive and one negative per view) are executed and pass, and the gate asserts
the executed set equals the declared set exactly.

**§9.2 — D48 deletion matrix.** The artifact
`src/rememberstack/spine/query_space/d48_deletion_matrix.json` enumerates **9 targets ×
25 surfaces = 225 cells** — the 24 public relations plus the private survivor helper,
crossed with the six deletions this batch can perform and the three whose object class it
does not build. It is generated by `build_matrix()` and checked in; a test asserts the
file equals its generator verbatim. The split:

| Cell status | Count | What the gate proves, per cell |
|---|---|---|
| `applicable` | 56 | The forbidden identifiers **were reachable** through this relation before the mutation, and no column of any row carries one after it. |
| `not_applicable` / `no_identifier_of_this_class` | 88 | The reachable set really **is empty**, before and after — the declaration is checked, not trusted. |
| `not_applicable` / `not_caller_reachable` | 6 | The surface is outside `memory_v1` and carries **no grant at all**, which the gate reads from `pg_class.relacl` and `information_schema.role_table_grants`. |
| `deferred` | 75 | Recorded with the batch that will execute it (P1 candidate → C, P2 edge → D, corpus body → C), so the artifact's coverage claim states its own scope. |

150 executable cells, all passing. The change from the previous 144-cell artifact is the
point of the rework: a single global "no forbidden identifier is present afterwards"
assertion passes trivially wherever the identifier could never have been present, and the
old per-*target* non-vacuity check (`any()` across that target's row) hid that — one
reachable surface satisfied it for all 24. Per-cell reachability makes each cell state
and prove its own obligation, and the honest consequence is visible in the numbers: only
56 of 144 public cells were ever proving anything, and the other 88 now say so and prove
their emptiness instead. A separate fixture proves a composed fact → evidence → claim →
source path drops as a unit when any hop stops being visible.

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

**Coordinate binding and fail-closed resolution.** Seven further gates cover the boundary
cases the invariants turn on, each written as the adversarial move it defends against.
Repointing a mention's own `doc_id` at a tombstoned lineage removes the mention from
`mentions_live` and its contribution from `entity_document_mentions`, rather than
publishing it under the live lineage of its chunk. Retiring the entity a live resolution
names leaves the mention visible and nulls all five resolution columns together. Every
`entity_document_mentions` row's count equals the number of `mentions_live` rows for that
survivor and lineage, and the corpus is asserted to contain a mention of superseded
content so the current-content restriction is not tested vacuously. A two-node merge cycle
resolves to no survivor at all: both entities leave `entities_current`, the mention that
resolved to one of them goes unresolved, a fact with such an endpoint drops as a unit, and
the quarantine report counts two. A merge chain longer than the depth bound resolves to
nothing while a short chain still resolves. Forgetting the last lineage a page cites
removes the page and its links from the public surface, while a page that keeps one
visible citation stays; the uncited page exists and is active in the base tables, is absent
from `pages_live`, and is counted once by the quarantine report.

**Suite, types, lint, migration cycle.** The full `src/tests/` suite is green — **1,208
passed, 0 failed, 0 skipped** in 6 minutes, of which 38 are the §9 schema gates and 15 the
database-free canonicalizer and artifact proofs; `pyright src/ benchmarks/` reports 0
errors; `ruff check` and `ruff format --check` are clean; and the migration's up/down/up
cycle passes through the existing `test_migrations.py` lifecycle test, which asserts the
head revision is `p9_01_0022` and that a downgrade leaves no `memory_v1` schema behind.
The one new runtime dependency is `pglast` (pinned to major 6, the PostgreSQL 16
grammar), which is what makes the parse tree — rather than a printed rendering of it — the
hash input.

## 8. What Batch A deliberately does not contain

No roles and no grants, no SQL parser or allowlist, no executor, no limits, no
`QueryResult`, no set-returning function, no Cypher, no registry, and no API, SDK, CLI,
or MCP surface. `memory_v1` is not reachable by a customer until Batch F, which is why
this batch ships no website page: the documentation obligation for the schema contract is
this note.
