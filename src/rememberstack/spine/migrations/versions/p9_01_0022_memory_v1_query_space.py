"""Create the versioned `memory_v1` invariant-compiled query-space schema.

The open query space makes PostgreSQL the public data language: agents write
ordinary SQL against `memory_v1`, and the views below — not caller discipline
— compile the three row-level invariants that make an arbitrary query
trustworthy.

- **D48 (deletion is fail-closed).** Every lineage path is an INNER JOIN or
  `EXISTS` chain through a surviving `documents` row (and, for version-derived
  rows, a non-tombstoned `document_versions` row; and, for current-content
  rows, the lineage's current version and current ready representation). The
  legacy permissive form — `LEFT JOIN documents ... WHERE d.doc_id IS NULL OR
  d.deleted_at IS NULL`, which lets an orphan through — appears nowhere here.
- **D41 (two clocks, one instant).** `facts_current` and
  `graph_edges_current` evaluate exactly one `statement_timestamp()` and emit
  it as `evaluated_at`, so every reference to a current relation inside one
  SQL statement answers at the same instant. Fact world-validity is half-open
  `[valid_from, valid_until)`; claim validity stays immutable source testimony
  and never answers what currently holds.
- **D54 (counting has one meaning).** `evidence_lineage` is the sole public
  input for evidence counts: one row per fact × current-testimony document
  lineage × stance. Repetition inside one document, and re-extraction of the
  same document, therefore cannot inflate a count. `support_state` is derived
  at read time from the open `support_withdrawn` review row exactly as the
  shipped query engine derives it; there is deliberately no stored column that
  could drift from that queue.

Base tables stay private: the schema contains exactly the relations the
binding design enumerates, and nothing else. Three private helper views (in
`public`, never granted) carry the logic two public relations would otherwise
have to state twice: `v_memory_entity_survivor` resolves merge redirects,
because a merge is a redirect rather than a rewrite and relations are not
re-pointed in PostgreSQL; `v_memory_mention_current_content` is the one
definition of "this mention occurs in current content and resolves here", so a
mention count and the mention transcript cannot disagree; and
`v_memory_page_citation_visible` is the one definition of "this citation's
target is visible", so a page's membership gate and its per-link gate cannot
disagree.
"""

from collections.abc import Sequence

from alembic import op

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import apply_view_ddl
from rememberstack.spine.migrations._helpers import drop_views

revision: str = "p9_01_0022"
down_revision: str | None = "p8_01_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every `memory_v1` relation, in creation order (dependencies first).
MEMORY_V1_VIEWS: tuple[str, ...] = (
    "documents_live",
    "document_versions_visible",
    "sections_live",
    "chunks_live",
    "claims_visible_history",
    "claims_live",
    "claim_occurrences_live",
    "testimony_currency_events_visible",
    "entity_document_mentions",
    "entities_current",
    "entity_aliases_current",
    "mentions_live",
    "identity_events_visible",
    "fact_claim_evidence_live",
    "evidence_lineage",
    "facts_visible_history",
    "facts_current",
    "contradiction_members_current",
    "graph_edges_current",
    "graph_edges_visible_history",
    "document_crossrefs_live",
    "pages_live",
    "page_evidence_visible",
    "changes_visible",
)

#: The private helper views this migration creates in `public`. They are never
#: part of `memory_v1`, never granted, and (Batch B) never on a query role's
#: search_path; they exist so two public relations cannot state one rule twice
#: and drift.
PRIVATE_HELPER_VIEWS: tuple[str, ...] = (
    "v_memory_entity_survivor",
    "v_memory_mention_current_content",
    "v_memory_page_citation_visible",
)

#: Every view this migration creates, dependents first, for the downgrade. The
#: helpers are interleaved because two of them read `memory_v1` relations and
#: are read by others, so one reversed list per schema would not be safe.
_DROP_ORDER: tuple[str, ...] = (
    "memory_v1.changes_visible",
    "memory_v1.page_evidence_visible",
    "memory_v1.pages_live",
    "v_memory_page_citation_visible",
    "memory_v1.document_crossrefs_live",
    "memory_v1.graph_edges_visible_history",
    "memory_v1.graph_edges_current",
    "memory_v1.contradiction_members_current",
    "memory_v1.facts_current",
    "memory_v1.facts_visible_history",
    "memory_v1.evidence_lineage",
    "memory_v1.fact_claim_evidence_live",
    "memory_v1.identity_events_visible",
    "memory_v1.mentions_live",
    "memory_v1.entity_aliases_current",
    "memory_v1.entities_current",
    "memory_v1.entity_document_mentions",
    "v_memory_mention_current_content",
    "memory_v1.testimony_currency_events_visible",
    "memory_v1.claim_occurrences_live",
    "memory_v1.claims_live",
    "memory_v1.claims_visible_history",
    "memory_v1.chunks_live",
    "memory_v1.sections_live",
    "memory_v1.document_versions_visible",
    "memory_v1.documents_live",
    "v_memory_entity_survivor",
)

_SCHEMA_DDL = r"""CREATE SCHEMA memory_v1;
COMMENT ON SCHEMA memory_v1 IS
  'Version 1 of the public PostgreSQL query space. Every relation in this schema compiles the D48 surviving-lineage rule, the D41 two-clock rule, and the D54 distinct-lineage counting rule, so an arbitrary caller query inherits them. Base tables, projection tables, and operator schemas are never public; adding a nullable trailing column, a relation, or an invariant correction rolls the surface manifest hash, while removing, renaming, reordering, narrowing, or weakening anything requires memory_v2.';
"""

# ── private helper ────────────────────────────────────────────────────────
# A merge is a REDIRECT (entities.merged_into), never a rewrite, and relations
# keep their original endpoint ids, so every entity reference must be resolved
# to its terminal survivor before it is exposed or joined. This helper is NOT
# part of memory_v1: it stays in the private schema and is never granted.
#
# The walk is FAIL-CLOSED. Acyclicity is not schema-enforced, so a chain can be
# a cycle (A merged into B, B merged into A), can run longer than the depth
# bound, or can point at an entity row that no longer exists. In every one of
# those cases the walk never reaches a row with merged_into IS NULL, and the
# final join to that terminal row therefore emits NOTHING: the entity resolves
# to no survivor and disappears from entities_current and from every relation
# that joins a survivor. The alternative — taking the deepest row reached — is
# fail-open: a two-node cycle would make each entity its own "survivor" and
# both would be exposed as if the merge had never happened. The operator
# quarantine report counts entities in this state so the omission is visible.
_HELPER_DDL = r"""CREATE VIEW v_memory_entity_survivor (
  deployment_id,
  entity_id,
  survivor_entity_id
) AS
WITH RECURSIVE chain(deployment_id, entity_id, cur, depth) AS (
  SELECT deployment_id, entity_id, entity_id, 0 FROM entities
  UNION ALL
  SELECT c.deployment_id, c.entity_id, e.merged_into, c.depth + 1
  FROM chain AS c
  JOIN entities AS e
    ON e.deployment_id = c.deployment_id
   AND e.entity_id = c.cur
  WHERE e.merged_into IS NOT NULL AND c.depth < 64  -- cycle / runaway guard
)
SELECT c.deployment_id, c.entity_id, c.cur
FROM chain AS c
JOIN entities AS terminal
  ON terminal.deployment_id = c.deployment_id
 AND terminal.entity_id = c.cur
 AND terminal.merged_into IS NULL;  -- only a terminated chain resolves
COMMENT ON VIEW v_memory_entity_survivor IS
  'Private merge-redirect resolution: maps an entity id to the terminal survivor of its merged_into chain. Resolution is fail-closed because acyclicity is not schema-enforced: a chain that does not reach an unmerged entity within the depth bound — a cycle, an over-long chain, or a redirect to a missing row — yields no row at all, so the entity is absent from every survivor-joined relation rather than being exposed as its own survivor. Not part of memory_v1 and never granted to a query role.';
"""

# ── E0 content surface ────────────────────────────────────────────────────
_CONTENT_DDL = r"""CREATE VIEW memory_v1.documents_live (
  deployment_id,               -- The deployment that owns this document lineage; every join to another memory_v1 relation carries it.
  doc_id,                      -- Stable lineage identity, unique within the deployment and never reused.
  source_kind,                 -- The connector family that produced the lineage, such as google_drive or upload.
  source_ref,                  -- The connector-native stable identifier, null for one-shot sources that have none.
  source_uri,                  -- The original location of the source, null when the source has no addressable location.
  title,                       -- Best-effort human title of the lineage, which is orientation text rather than asserted evidence.
  versioning_mode,             -- The D55 currency mode of the lineage, either snapshot or living.
  origin,                      -- The D42 provenance stamp, either external or system_generated.
  first_seen_at,               -- The instant this deployment first observed the lineage.
  last_observed_at,            -- The instant the connector last observed the lineage, null when it has never been re-observed.
  current_version_id,          -- The lineage's current snapshot, null when no non-tombstoned current version exists.
  current_version_no,          -- The one-based ordinal of the current version within the lineage, null when there is no visible current version.
  current_version_status,      -- Processing status of the current version, null when there is no visible current version.
  current_representation_id,   -- The current ready reading of the current version, null when no ready representation exists.
  has_current_ready_content,   -- True only when the lineage has a ready current version and a ready current representation, which is the precondition every current-content relation joins on.
  source_modified_at,          -- When the source says the current snapshot was authored, which dates derived testimony.
  published_at,                -- The document's own publication date on the current version, null when unknown.
  language                     -- Detected primary language of the current version, null when undetected.
) AS
SELECT
  d.deployment_id,
  d.doc_id,
  d.source_kind,
  d.source_ref,
  d.source_uri,
  d.title,
  d.versioning_mode::text,
  d.origin::text,
  d.first_seen_at,
  d.last_observed_at,
  v.version_id,
  v.version_no,
  v.status::text,
  r.representation_id,
  (r.representation_id IS NOT NULL AND v.status = 'ready'),
  v.source_modified_at,
  v.published_at,
  v.language
FROM documents AS d
LEFT JOIN document_versions AS v
  ON v.deployment_id = d.deployment_id
 AND v.doc_id = d.doc_id
 AND v.version_id = d.current_version_id
 AND v.deleted_at IS NULL
LEFT JOIN document_representations AS r
  ON r.deployment_id = v.deployment_id
 AND r.version_id = v.version_id
 AND r.representation_id = v.current_representation_id
 AND r.status = 'ready'
WHERE d.deleted_at IS NULL;
COMMENT ON VIEW memory_v1.documents_live IS
  'One row per live document lineage, keyed by (deployment_id, doc_id). A tombstoned lineage is absent, and the current version and representation coordinates are produced only from a non-tombstoned version and a ready representation, so no column can name deleted state. The two optional joins project coordinates of an already authorized lineage and admit no row of their own. This is a live-content relation, not a fact or evidence relation: title and source metadata are orientation, never asserted evidence, and the view carries no counts and no clock semantics beyond the observation instants it names.';

CREATE VIEW memory_v1.document_versions_visible (
  deployment_id,               -- The deployment that owns the version.
  version_id,                  -- Stable identity of this observed snapshot of the lineage.
  doc_id,                      -- The lineage this version belongs to, joinable to documents_live.
  version_no,                  -- One-based ordinal of the version within its lineage.
  content_hash,                -- Hash of the immutable bytes this version observed, shared by lineages carrying identical content.
  source_version_ref,          -- The connector revision or etag for this snapshot, null when the source has none.
  status,                      -- Processing status of the version, such as ready or failed.
  current_representation_id,   -- The reading of this version that is currently live, null while no reading has completed.
  ingested_at,                 -- Transaction-time origin: when this deployment ingested the snapshot.
  source_modified_at,          -- When the source says this snapshot was authored, null when the source gives no date.
  published_at,                -- The document's own publication date for this snapshot, null when unknown.
  language,                    -- Detected primary language of this snapshot, null when undetected.
  superseded_at,               -- When a newer version became current, null while this version is still the lineage's current one.
  is_current_version           -- True only for the lineage's current snapshot.
) AS
SELECT
  v.deployment_id,
  v.version_id,
  v.doc_id,
  v.version_no,
  v.content_hash,
  v.source_version_ref,
  v.status::text,
  v.current_representation_id,
  v.ingested_at,
  v.source_modified_at,
  v.published_at,
  v.language,
  v.superseded_at,
  (v.version_id IS NOT DISTINCT FROM d.current_version_id)
FROM document_versions AS v
JOIN documents AS d
  ON d.deployment_id = v.deployment_id
 AND d.doc_id = v.doc_id
 AND d.deleted_at IS NULL
WHERE v.deleted_at IS NULL;
COMMENT ON VIEW memory_v1.document_versions_visible IS
  'One row per non-tombstoned version of a live document lineage, keyed by (deployment_id, version_id) and joined to documents_live on (deployment_id, doc_id). A tombstoned version and every version of a forgotten lineage are absent, which is why the whole schema authorizes version-derived rows through this relation rather than through document_versions directly. This is version history, not fact history: is_current_version says which snapshot the lineage currently points at, and no column here asserts what the system currently believes to be true. The view carries no counts.';

CREATE VIEW memory_v1.sections_live (
  deployment_id,               -- The deployment that owns the section.
  section_id,                  -- Stable identity of this section node.
  doc_id,                      -- The live lineage the section belongs to.
  version_id,                  -- The lineage's current version, whose bytes this section indexes.
  representation_id,           -- The current ready reading whose character offsets this section uses.
  structure_generation_id,     -- The D79 structure generation that produced this tree, always the representation's current generation.
  parent_section_id,           -- The parent node in the section tree, null for the root section.
  node_path,                   -- Materialized path such as 0.2.1, unique within the structure generation.
  heading_level,               -- Source heading depth from one to six, null when the section carries no heading.
  title,                       -- Section title as read from the source, null when the section has none.
  normalized_title,            -- Case-folded and trimmed title used for stable matching, empty when there is no title.
  role,                        -- Structural role of the section, such as body, references, or boilerplate.
  ordinal,                     -- Position of the section among its siblings.
  block_start,                 -- First block ordinal of the section on the deterministic block grid.
  block_end,                   -- Last block ordinal of the section, inclusive.
  char_start,                  -- Start character offset of the section within this representation's markdown.
  char_end,                    -- End character offset of the section within this representation's markdown.
  page_start,                  -- First source page of the section, null when the source is not paginated.
  page_end,                    -- Last source page of the section, null when the source is not paginated.
  summary                      -- Section summary generated for navigation and context; it is labeled orientation text and is never asserted evidence.
) AS
SELECT
  s.deployment_id,
  s.section_id,
  s.doc_id,
  s.version_id,
  s.representation_id,
  s.structure_generation_id,
  s.parent_section_id,
  s.node_path,
  s.heading_level,
  s.title,
  s.normalized_title,
  s.role::text,
  s.ordinal,
  s.block_start,
  s.block_end,
  s.char_start,
  s.char_end,
  s.page_start,
  s.page_end,
  s.summary
FROM document_sections AS s
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = s.deployment_id
 AND dl.doc_id = s.doc_id
 AND dl.current_version_id = s.version_id
 AND dl.current_representation_id = s.representation_id
 AND dl.has_current_ready_content
JOIN document_representations AS r
  ON r.deployment_id = s.deployment_id
 AND r.representation_id = s.representation_id
 AND r.current_structure_generation_id = s.structure_generation_id;
COMMENT ON VIEW memory_v1.sections_live IS
  'One row per section of the current ready representation of a live lineage, keyed by (deployment_id, section_id) and joined to documents_live on (deployment_id, doc_id). Sections of a superseded version, of a non-ready reading, of a superseded D79 structure generation, and of a forgotten lineage are all absent, so a node_path resolves to exactly one live tree. Character and block offsets are meaningful only against the named representation. The summary column is orientation text, not evidence, and the view carries no counts and no validity clocks.';

CREATE VIEW memory_v1.chunks_live (
  deployment_id,                   -- The deployment that owns the chunk.
  chunk_id,                        -- Stable identity of this retrieval unit within the current reading.
  doc_id,                          -- The live lineage the chunk belongs to.
  version_id,                      -- The lineage's current version the chunk was cut from.
  representation_id,               -- The current ready reading whose block grid and offsets the chunk uses.
  section_id,                      -- The section containing the chunk, null when the chunk has no section in the current structure generation.
  ordinal,                         -- Position of the chunk within the document.
  block_start,                     -- First block ordinal packed into the chunk.
  block_end,                       -- Last block ordinal packed into the chunk, inclusive.
  char_start,                      -- Start character offset of the chunk within this representation's markdown.
  char_end,                        -- End character offset of the chunk within this representation's markdown.
  token_count,                     -- Token length of the chunk, null when it was never measured.
  chunk_content_hash,              -- Hash of the chunk's ordered block hashes, which is its content identity.
  extraction_input_hash,           -- Hash of the stable extraction inputs, which is the reuse key that avoids re-extracting unchanged content.
  embedding_text_hash,             -- Hash of the exact text that was embedded under the D80 policy, null when the chunk has not been embedded.
  location_facts,                  -- The deterministic D80 location facts as structured data, null when no policy generation has stamped the chunk.
  location_header,                 -- The deterministic D80 location header prepended to the embedded text; it is generated orientation text and is never asserted evidence.
  embedding_input_policy_version,  -- The D80 embedding-input policy in force for this chunk, null when unstamped.
  policy_generation,               -- The generation label of that policy application, null when unstamped.
  embedder_generation,             -- The embedder generation that produced the chunk vector, null when the chunk has not been embedded.
  chunker_version,                 -- The chunker configuration that produced this cut, null on rows written before the stamp existed.
  prefixer_version,                -- The context-prefixer generation for this chunk, null when no prefix was generated.
  created_at                       -- When the chunk row was written, which is a processing instant rather than a world-time clock.
) AS
SELECT
  c.deployment_id,
  c.chunk_id,
  c.doc_id,
  c.version_id,
  c.representation_id,
  sec.section_id,
  c.ordinal,
  c.block_start,
  c.block_end,
  c.char_start,
  c.char_end,
  c.token_count,
  c.chunk_content_hash,
  c.extraction_input_hash,
  c.embedding_text_hash,
  c.location_facts_json,
  c.location_header,
  c.embedding_input_policy_version,
  c.policy_generation,
  c.embedding_version,
  c.chunker_version,
  c.prefixer_version,
  c.created_at
FROM chunks AS c
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = c.deployment_id
 AND dl.doc_id = c.doc_id
 AND dl.current_version_id = c.version_id
 AND dl.current_representation_id = c.representation_id
 AND dl.has_current_ready_content
JOIN document_representations AS r
  ON r.deployment_id = c.deployment_id
 AND r.representation_id = c.representation_id
LEFT JOIN document_sections AS sec
  ON sec.deployment_id = c.deployment_id
 AND sec.section_id = c.section_id
 AND sec.structure_generation_id = r.current_structure_generation_id;
COMMENT ON VIEW memory_v1.chunks_live IS
  'One row per chunk coordinate in the current ready representation of a live lineage, keyed by (deployment_id, chunk_id) and joined to documents_live on (deployment_id, doc_id) and to sections_live on (deployment_id, section_id). This relation is metadata only and deliberately carries no authoritative body column: chunk text is returned solely by the confirmed body-fetch path, which re-verifies the coordinate and the content and embedding hashes before any bytes leave the system. Chunks of superseded versions, non-ready readings, and forgotten lineages are absent, and a section_id is exposed only when that section belongs to the representation''s current structure generation. The location header is generated orientation text, never evidence; the view carries no counts and no validity clocks.';
"""

# ── E2 testimony surface ──────────────────────────────────────────────────
_TESTIMONY_DDL = r"""CREATE VIEW memory_v1.claims_visible_history (
  deployment_id,           -- The deployment that owns the claim.
  claim_id,                -- Stable identity of this immutable claim.
  doc_id,                  -- The live lineage that asserted the claim.
  version_id,              -- The non-tombstoned version the claim was extracted from.
  representation_id,       -- The reading whose character offsets the claim's anchors use.
  chunk_id,                -- The chunk the claim was extracted from.
  claim_text,              -- The standalone assertion as extracted, which is source testimony rather than adjudicated truth.
  source_span,             -- The verbatim slice of the source the claim derives from.
  char_start,              -- Start character offset of source_span within the named representation's markdown.
  char_end,                -- End character offset of source_span within the named representation's markdown.
  added_context,           -- The substrings decontextualization added, each with the bundle source it came from.
  temporal_class,          -- How the claim behaves over time, either static, dynamic, or atemporal; null when unclassified.
  is_attributed,           -- True when the claim preserves an attribution, so it entails that someone said it rather than that it holds.
  audit_status,            -- Result of the sampled independent grounding audit, defaulting to unaudited.
  kept_flagged,            -- True when selection kept the claim but marked it for review.
  extractor_version,       -- The extractor generation that produced the claim, which is part of the D54 extraction basis.
  asserted_at,             -- Assertion-event time: when the source asserted this, null when the source carries no date.
  claim_valid_from,        -- Immutable start of the world-time interval the SOURCE asserted, null for unbounded-before or unknown.
  claim_valid_until,       -- Immutable end of that interval, null for open-per-source or unknown as disambiguated by claim_valid_precision.
  claim_valid_precision,   -- Granularity of the asserted interval, from unknown through instant, day, month, quarter, and year to open.
  claim_valid_kind,        -- Which world-interval was asserted, such as event_time or measurement_period; null when unclassified.
  ingested_at,             -- Transaction-time: when this deployment extracted the claim.
  source_kind,             -- The connector family of the asserting lineage.
  source_handle,           -- Stable human-usable handle for the asserting lineage, formed from its connector-native identity.
  is_current_testimony     -- True while this claim is the current transcription of its chunk under D54; false once a newer extraction generation or a living-mode version move superseded it.
) AS
SELECT
  c.deployment_id,
  c.claim_id,
  c.doc_id,
  ch.version_id,
  ch.representation_id,
  c.chunk_id,
  c.claim_text,
  c.source_span,
  c.char_start,
  c.char_end,
  c.added_context,
  c.temporal_class::text,
  c.is_attributed,
  c.audit_status::text,
  c.kept_flagged,
  c.extractor_version,
  c.asserted_at,
  c.claim_valid_from,
  c.claim_valid_until,
  c.claim_valid_precision::text,
  c.claim_valid_kind::text,
  c.ingested_at,
  dl.source_kind,
  dl.source_kind || ':' || coalesce(dl.source_ref, dl.doc_id::text),
  c.is_current_testimony
FROM claims AS c
JOIN chunks AS ch
  ON ch.deployment_id = c.deployment_id
 AND ch.chunk_id = c.chunk_id
JOIN memory_v1.document_versions_visible AS vv
  ON vv.deployment_id = ch.deployment_id
 AND vv.version_id = ch.version_id
 AND vv.doc_id = c.doc_id
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = c.deployment_id
 AND dl.doc_id = c.doc_id;
COMMENT ON VIEW memory_v1.claims_visible_history IS
  'One row per claim whose source lineage is live and whose source version is not tombstoned, keyed by (deployment_id, claim_id) and joined to documents_live on (deployment_id, doc_id) and to document_versions_visible on (deployment_id, version_id). This relation is IMMUTABLE SOURCE TESTIMONY and never answers what currently holds: claim_valid_from and claim_valid_until say when a source asserted a proposition applied, not what the system believes now, and filtering them by an instant is the classic wrong current-truth query — use facts_current instead. Claim-evidence overlap is INCLUSIVE at both endpoints, because an instant-precision claim has equal endpoints that a half-open convention would make empty. A null claim_valid_from means unbounded-before or unknown and a null claim_valid_until means open-per-source or unknown, disambiguated by claim_valid_precision. Claims of forgotten lineages and tombstoned versions are absent; is_current_testimony is D54 bookkeeping and never validity. The view carries no counts.';

CREATE VIEW memory_v1.claims_live (
  deployment_id,           -- The deployment that owns the claim.
  claim_id,                -- Stable identity of this immutable claim.
  doc_id,                  -- The live lineage that asserted the claim.
  version_id,              -- The non-tombstoned version the claim was extracted from.
  representation_id,       -- The reading whose character offsets the claim's anchors use.
  chunk_id,                -- The chunk the claim was extracted from.
  claim_text,              -- The standalone assertion as extracted, which is source testimony rather than adjudicated truth.
  source_span,             -- The verbatim slice of the source the claim derives from.
  char_start,              -- Start character offset of source_span within the named representation's markdown.
  char_end,                -- End character offset of source_span within the named representation's markdown.
  added_context,           -- The substrings decontextualization added, each with the bundle source it came from.
  temporal_class,          -- How the claim behaves over time, either static, dynamic, or atemporal; null when unclassified.
  is_attributed,           -- True when the claim preserves an attribution, so it entails that someone said it rather than that it holds.
  audit_status,            -- Result of the sampled independent grounding audit, defaulting to unaudited.
  kept_flagged,            -- True when selection kept the claim but marked it for review.
  extractor_version,       -- The extractor generation that produced the claim, which is part of the D54 extraction basis.
  asserted_at,             -- Assertion-event time: when the source asserted this, null when the source carries no date.
  claim_valid_from,        -- Immutable start of the world-time interval the SOURCE asserted, null for unbounded-before or unknown.
  claim_valid_until,       -- Immutable end of that interval, null for open-per-source or unknown as disambiguated by claim_valid_precision.
  claim_valid_precision,   -- Granularity of the asserted interval, from unknown through instant, day, month, quarter, and year to open.
  claim_valid_kind,        -- Which world-interval was asserted, such as event_time or measurement_period; null when unclassified.
  ingested_at,             -- Transaction-time: when this deployment extracted the claim.
  source_kind,             -- The connector family of the asserting lineage.
  source_handle            -- Stable human-usable handle for the asserting lineage, formed from its connector-native identity.
) AS
SELECT
  h.deployment_id,
  h.claim_id,
  h.doc_id,
  h.version_id,
  h.representation_id,
  h.chunk_id,
  h.claim_text,
  h.source_span,
  h.char_start,
  h.char_end,
  h.added_context,
  h.temporal_class,
  h.is_attributed,
  h.audit_status,
  h.kept_flagged,
  h.extractor_version,
  h.asserted_at,
  h.claim_valid_from,
  h.claim_valid_until,
  h.claim_valid_precision,
  h.claim_valid_kind,
  h.ingested_at,
  h.source_kind,
  h.source_handle
FROM memory_v1.claims_visible_history AS h
WHERE h.is_current_testimony;
COMMENT ON VIEW memory_v1.claims_live IS
  'One row per current-testimony claim, keyed by (deployment_id, claim_id): the subset of claims_visible_history whose D54 currency flag is still set. Like every claim relation this is IMMUTABLE SOURCE TESTIMONY, and "live" here means current transcription of a live source, never current truth: a claim in this relation can be contradicted by the adjudicated worldview, and querying its validity window to answer what holds now is the wrong query — start from facts_current and follow fact_claim_evidence_live back to here. Claim-evidence overlap is inclusive at both endpoints; a null endpoint is unbounded or unknown per claim_valid_precision. Claims of forgotten lineages and tombstoned versions are absent, and this relation is the sole claim input to the D54 counting path. The view carries no counts.';

CREATE VIEW memory_v1.claim_occurrences_live (
  deployment_id,       -- The deployment that owns the occurrence.
  claim_id,            -- The claim carried by this chunk occurrence.
  chunk_id,            -- The current-content chunk that carries the claim.
  derivation_kind,     -- How this occurrence was derived from the source, such as passthrough, asr, or ocr; null when the reading recorded no label.
  doc_id,              -- The live lineage carrying the occurrence.
  version_id,          -- The lineage's current version carrying the occurrence.
  representation_id,   -- The current ready reading carrying the occurrence.
  section_id,          -- The section containing the carrying chunk, null when the chunk has no section in the current structure generation.
  evidence_mode,       -- How mediated this occurrence is, such as source_expression or model_observation; null when the reading recorded no mode.
  source_locators,     -- The resolved source locator set for this occurrence, null when the reading resolved none.
  attached_at          -- When this occurrence was first recorded, which is a processing instant rather than a world-time clock.
) AS
SELECT DISTINCT ON (cc.deployment_id, cc.claim_id, cc.chunk_id, cc.derivation_kind)
  cc.deployment_id,
  cc.claim_id,
  cc.chunk_id,
  cc.derivation_kind,
  cl.doc_id,
  cl.version_id,
  cl.representation_id,
  cl.section_id,
  cc.evidence_mode,
  cc.source_locators,
  cc.created_at
FROM chunk_claims AS cc
JOIN memory_v1.chunks_live AS cl
  ON cl.deployment_id = cc.deployment_id
 AND cl.chunk_id = cc.chunk_id
JOIN memory_v1.claims_visible_history AS ch
  ON ch.deployment_id = cc.deployment_id
 AND ch.claim_id = cc.claim_id
ORDER BY cc.deployment_id, cc.claim_id, cc.chunk_id, cc.derivation_kind,
         cc.created_at, cc.evidence_mode;
COMMENT ON VIEW memory_v1.claim_occurrences_live IS
  'One row per current claim occurrence, keyed by (deployment_id, claim_id, chunk_id, derivation_kind) with null derivation kinds treated as equal, and joined to claims_visible_history on (deployment_id, claim_id) and to chunks_live on (deployment_id, chunk_id). It is the explicit association answering which current chunk, version, representation, and section carry a claim, which a re-attachment of the same claim to the same chunk under the same derivation does not duplicate: repeated attachments collapse to the earliest, so attached_at is the first time the occurrence was recorded. Occurrences in superseded versions, non-ready readings, and forgotten lineages are absent. Source locators are provenance, not evidence text; the view carries no counts and no validity clocks.';

CREATE VIEW memory_v1.testimony_currency_events_visible (
  deployment_id,           -- The deployment that owns the transition.
  event_id,                -- Stable identity of this append-only transition record.
  claim_id,                -- The claim whose testimony currency changed.
  doc_id,                  -- The live lineage whose basis change drove the transition.
  reconciliation_id,       -- The single reconciliation run that emitted the transition, so a retried run is recognizable as one run.
  became_current,          -- True when the claim regained currency and false when it lost currency.
  reason,                  -- Why currency changed, one of reextracted, version_superseded, version_deleted, or review_restored.
  from_extractor_version,  -- The superseded extractor generation for a re-extraction, null for the other reasons.
  from_version_id,         -- The superseded document version, exposed only while that version is itself visible and null otherwise.
  occurred_at              -- When the transition occurred, which is a transaction-time instant and never a validity clock.
) AS
SELECT
  e.deployment_id,
  e.event_id,
  e.claim_id,
  e.doc_id,
  e.reconciliation_id,
  e.became_current,
  e.reason::text,
  e.from_extractor_version,
  fv.version_id,
  e.occurred_at
FROM testimony_currency_events AS e
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = e.deployment_id
 AND dl.doc_id = e.doc_id
JOIN memory_v1.claims_visible_history AS ch
  ON ch.deployment_id = e.deployment_id
 AND ch.claim_id = e.claim_id
LEFT JOIN memory_v1.document_versions_visible AS fv
  ON fv.deployment_id = e.deployment_id
 AND fv.version_id = e.from_version_id;
COMMENT ON VIEW memory_v1.testimony_currency_events_visible IS
  'One row per visible D54 testimony-currency transition, keyed by (deployment_id, event_id) and joined to claims_visible_history on (deployment_id, claim_id) and to documents_live on (deployment_id, doc_id). A currency transition is BOOKKEEPING and never validity: nothing about the claim changes and no fact is adjudicated by it. Transitions of forgotten lineages and of claims whose source version is tombstoned are absent, and from_version_id is null rather than dangling whenever the superseded version is itself no longer visible, so this relation cannot be read as a tombstone side channel. The occurrence instant is transaction time; the view carries no counts and no world-validity clocks.';
"""

# ── entity and identity surface ───────────────────────────────────────────
# The private mention helper is the SINGLE definition of "this mention occurs
# in current content, and this is the survivor it resolves to". Both the
# mention transcript (mentions_live) and the mention count
# (entity_document_mentions) are projections of it, so the count is exactly the
# number of transcript rows for that survivor and lineage and the two cannot
# drift. Every coordinate of the association is bound: the chunk must be a
# current-content chunk, the mention's own doc_id must be the lineage that
# chunk belongs to, and the claim coordinate must be a visible claim of that
# same lineage. Binding the lineage matters — without it, a mention row whose
# doc_id names a forgotten lineage would still be exposed through the live
# lineage of its chunk.
_ENTITY_DDL = r"""CREATE VIEW v_memory_mention_current_content (
  deployment_id,
  mention_id,
  doc_id,
  version_id,
  representation_id,
  chunk_id,
  section_id,
  claim_id,
  surface_form,
  normalized_lemma,
  canonical_name_form,
  emitted_type,
  type_confidence,
  language,
  char_start,
  char_end,
  created_at,
  survivor_entity_id,
  resolution_method,
  resolution_confidence,
  resolution_is_new_entity,
  resolved_at
) AS
SELECT
  m.deployment_id,
  m.mention_id,
  cl.doc_id,
  cl.version_id,
  cl.representation_id,
  cl.chunk_id,
  cl.section_id,
  mc.claim_id,
  m.surface_form,
  m.normalized_lemma,
  m.canonical_name_form,
  m.emitted_type,
  m.type_confidence,
  m.language,
  m.char_start,
  m.char_end,
  m.created_at,
  s.survivor_entity_id,
  live.method::text,
  live.confidence,
  live.is_new_entity,
  live.decided_at
FROM mentions AS m
JOIN memory_v1.chunks_live AS cl
  ON cl.deployment_id = m.deployment_id
 AND cl.chunk_id = m.chunk_id
 AND cl.doc_id = m.doc_id
LEFT JOIN memory_v1.claims_visible_history AS mc
  ON mc.deployment_id = m.deployment_id
 AND mc.claim_id = m.claim_id
 AND mc.doc_id = cl.doc_id
LEFT JOIN LATERAL (
  SELECT rd.entity_id, rd.method, rd.confidence, rd.is_new_entity, rd.decided_at
  FROM resolution_decisions AS rd
  WHERE rd.deployment_id = m.deployment_id
    AND rd.mention_id = m.mention_id
    AND rd.superseded_by IS NULL
  ORDER BY rd.decided_at DESC, rd.decision_id DESC
  LIMIT 1
) AS live ON true
LEFT JOIN v_memory_entity_survivor AS s
  ON s.deployment_id = m.deployment_id
 AND s.entity_id = live.entity_id;
COMMENT ON VIEW v_memory_mention_current_content IS
  'Private single definition of a mention in current content: exactly one row per mention whose chunk is a current-content chunk of the lineage the mention itself names, carrying the mention''s coordinates and the survivor of its one live, unsuperseded resolution decision. Both mentions_live and entity_document_mentions are projections of this relation, so a count and the transcript it counts cannot disagree. Not part of memory_v1 and never granted to a query role.';

CREATE VIEW memory_v1.entity_document_mentions (
  deployment_id,       -- The deployment that owns both the entity and the document.
  entity_id,           -- The survivor entity, with merge redirects already resolved.
  doc_id,              -- The live lineage the entity is mentioned in.
  mention_count,       -- Exact count of the mentions of this survivor in this lineage's current content.
  first_mentioned_at,  -- When the earliest counted mention was recorded.
  last_mentioned_at    -- When the latest counted mention was recorded.
) AS
SELECT
  h.deployment_id,
  h.survivor_entity_id,
  h.doc_id,
  count(*)::bigint,
  min(h.created_at),
  max(h.created_at)
FROM v_memory_mention_current_content AS h
JOIN entities AS e
  ON e.deployment_id = h.deployment_id
 AND e.entity_id = h.survivor_entity_id
 AND e.status = 'active'
GROUP BY h.deployment_id, h.survivor_entity_id, h.doc_id;
COMMENT ON VIEW memory_v1.entity_document_mentions IS
  'One row per survivor entity and live document lineage, keyed by (deployment_id, entity_id, doc_id) and joined to entities_current on (deployment_id, entity_id) and documents_live on (deployment_id, doc_id). The mention count is EXACT rather than sampled or capped, and it counts exactly the mentions this deployment can still show: one for every row of mentions_live in this lineage whose resolution names this survivor, and nothing else. Mentions of forgotten lineages, mentions of superseded versions and non-current readings, mentions with no chunk coordinate, mentions whose own lineage disagrees with their chunk''s, and mentions whose resolution has been superseded are therefore counted nowhere — a mention of superseded content is not live content and is not counted. Merge redirects are resolved before counting, so a merged entity contributes to its survivor and never appears on its own. The clocks are mention-recording instants, not world-validity, and this relation carries no evidence and no fact semantics.';

CREATE VIEW memory_v1.entities_current (
  deployment_id,        -- The deployment that owns the entity.
  entity_id,            -- Stable survivor identity, which is never reused and never rewritten by a merge.
  entity_type,          -- Canonical type of the entity as voted across its mentions.
  canonical_name,       -- Preferred display name of the entity.
  normalized_name,      -- Accent-folded lower-case form of the canonical name, used for matching.
  type_confidence,      -- Confidence in the type vote, null when never scored.
  profile_summary,      -- Registry-maintained blurb about the entity; it is labeled orientation text and is never asserted evidence.
  live_mention_count,   -- Exact count of the mentions of this entity in the CURRENT content of live lineages, which is zero when every mention of it survives only in a superseded version.
  live_document_count,  -- Exact count of the live document lineages whose CURRENT content mentions this entity, which is zero for the same reason.
  graph_degree,         -- Deprecated compatibility scalar fixed at zero after D98; consumers compute live relation degree from PostgreSQL adjacency.
  created_at,           -- When the entity was minted.
  updated_at            -- When the entity registry row was last maintained.
) AS
SELECT
  e.deployment_id,
  e.entity_id,
  e.type,
  e.canonical_name,
  e.normalized_name,
  e.type_confidence,
  e.profile_summary,
  live.mention_count,
  live.document_count,
  e.graph_degree::bigint,
  e.created_at,
  e.updated_at
FROM entities AS e
CROSS JOIN LATERAL (
  SELECT coalesce(sum(edm.mention_count), 0)::bigint AS mention_count,
         count(*)::bigint AS document_count
  FROM memory_v1.entity_document_mentions AS edm
  WHERE edm.deployment_id = e.deployment_id
    AND edm.entity_id = e.entity_id
) AS live
WHERE e.status = 'active'
  AND EXISTS (
    -- The D48 membership floor is an explicit association to at least one
    -- SURVIVING LINEAGE, which is a weaker requirement than the counting rule
    -- above: a mention in a non-tombstoned version of a live lineage is such an
    -- association even after a newer version superseded it. The counts stay
    -- current-content-only and may therefore both be zero on a published row.
    -- One arm per association class rather than an OR of two EXISTS: a
    -- disjunction of subqueries cannot be planned as a semi-join, while this
    -- shape can (the same reason the K citation helper is written as arms).
    SELECT 1
    FROM (
      SELECT m.deployment_id, s.survivor_entity_id AS entity_id
      FROM mentions AS m
      JOIN chunks AS ch
        ON ch.deployment_id = m.deployment_id
       AND ch.chunk_id = m.chunk_id
      JOIN memory_v1.document_versions_visible AS vv
        ON vv.deployment_id = ch.deployment_id
       AND vv.version_id = ch.version_id
       AND vv.doc_id = m.doc_id
      CROSS JOIN LATERAL (
        SELECT rd.entity_id
        FROM resolution_decisions AS rd
        WHERE rd.deployment_id = m.deployment_id
          AND rd.mention_id = m.mention_id
          AND rd.superseded_by IS NULL
        ORDER BY rd.decided_at DESC, rd.decision_id DESC
        LIMIT 1
      ) AS decided
      JOIN v_memory_entity_survivor AS s
        ON s.deployment_id = m.deployment_id
       AND s.entity_id = decided.entity_id
      UNION ALL
      SELECT d.deployment_id, s.survivor_entity_id
      FROM documents AS d
      JOIN v_memory_entity_survivor AS s
        ON s.deployment_id = d.deployment_id
       AND s.entity_id = d.document_entity_id
      WHERE d.deleted_at IS NULL
    ) AS provenance
    WHERE provenance.deployment_id = e.deployment_id
      AND provenance.entity_id = e.entity_id
  );
COMMENT ON VIEW memory_v1.entities_current IS
  'One row per externally visible survivor entity, keyed by (deployment_id, entity_id). Membership requires SURVIVING PROVENANCE, which is an explicit association to at least one live document lineage: a mention of this survivor in any non-tombstoned version of a live lineage, or a live document-entity bridge. An entity whose every source has been forgotten is therefore absent rather than orphaned, and merged entities are absent because a merge redirects to a survivor instead of rewriting history. MEMBERSHIP AND THE COUNTS ANSWER DIFFERENT QUESTIONS, and the difference is deliberate: the two counts are exact over CURRENT content only — they equal this entity''s rows in mentions_live and entity_document_mentions — so an entity whose only mention sits in a superseded version of a live lineage is published here with both counts at zero and has no row in entity_document_mentions at all. A zero count is not an absence of provenance. graph_degree is a deprecated compatibility scalar fixed at zero after D98; blast-radius consumers compute current degree directly from PostgreSQL relation adjacency. profile_summary is orientation text, never evidence; and the clocks are registry maintenance instants that carry no world-validity meaning.';

CREATE VIEW memory_v1.entity_aliases_current (
  deployment_id,        -- The deployment that owns the alias.
  alias_id,             -- Stable identity of this alias row.
  source_entity_id,     -- The entity the alias was originally recorded against, which may since have been merged away.
  entity_id,            -- The survivor entity the alias currently names, joinable to entities_current.
  alias_text,           -- The surface form as observed or as canonicalized.
  normalized_lemma,     -- Accent-folded lower-case match key for the alias.
  provenance,           -- Where the alias came from, either source when observed in a document or llm_canonical when emitted by the extractor.
  confidence,           -- Confidence that this surface really names the entity, null when never scored.
  first_seen,           -- When the alias was first recorded.
  last_seen             -- When the alias was last observed.
) AS
SELECT
  a.deployment_id,
  a.alias_id,
  a.entity_id,
  ec.entity_id,
  a.alias_text,
  a.normalized_lemma,
  a.provenance::text,
  a.confidence,
  a.first_seen,
  a.last_seen
FROM aliases AS a
JOIN v_memory_entity_survivor AS s
  ON s.deployment_id = a.deployment_id
 AND s.entity_id = a.entity_id
JOIN memory_v1.entities_current AS ec
  ON ec.deployment_id = s.deployment_id
 AND ec.entity_id = s.survivor_entity_id;
COMMENT ON VIEW memory_v1.entity_aliases_current IS
  'One row per current alias-to-survivor mapping, keyed by (deployment_id, alias_id) and joined to entities_current on (deployment_id, entity_id). Merge redirects are resolved, so an alias recorded against a since-merged entity now names the survivor while source_entity_id preserves where it was recorded. An alias whose survivor has no surviving provenance is absent, because membership is inherited from entities_current. The clocks are observation instants rather than validity, and the view carries no counts.';

CREATE VIEW memory_v1.mentions_live (
  deployment_id,           -- The deployment that owns the mention.
  mention_id,              -- Stable identity of this mention in the immutable mention transcript.
  doc_id,                  -- The live lineage the mention occurs in.
  version_id,              -- The lineage's current version the mention occurs in.
  representation_id,       -- The current ready reading whose offsets the mention anchors use.
  chunk_id,                -- The current-content chunk the mention occurs in.
  section_id,              -- The section containing the mention, null when the chunk has no section in the current structure generation.
  claim_id,                -- The claim the mention occurs in, exposed only while that claim is itself visible and null otherwise.
  surface_form,            -- The mention exactly as it appeared in the source.
  normalized_lemma,        -- Accent-folded lower-case form of the surface form.
  canonical_name_form,     -- The nominative or canonical form the extractor emitted, null when it emitted none.
  emitted_type,            -- The entity type the extractor emitted for this mention, null when it emitted none.
  type_confidence,         -- Extractor confidence in that emitted type, null when unscored.
  language,                -- Language of the mention, null when undetected.
  char_start,              -- Start character offset of the mention within the named representation's markdown, null when unrecorded.
  char_end,                -- End character offset of the mention within the named representation's markdown, null when unrecorded.
  created_at,              -- When the mention was recorded, which is a processing instant.
  resolved_entity_id,      -- The survivor entity this mention currently resolves to, null while the mention is unresolved or while the entity that decision names is not itself visible.
  resolution_method,       -- Which decision tier produced the live resolution, null exactly when resolved_entity_id is null.
  resolution_confidence,   -- Confidence of that live resolution, null exactly when resolved_entity_id is null.
  resolution_is_new_entity,-- True when the live resolution minted a new entity, null exactly when resolved_entity_id is null.
  resolved_at              -- When the live resolution was decided, null exactly when resolved_entity_id is null.
) AS
SELECT
  h.deployment_id,
  h.mention_id,
  h.doc_id,
  h.version_id,
  h.representation_id,
  h.chunk_id,
  h.section_id,
  h.claim_id,
  h.surface_form,
  h.normalized_lemma,
  h.canonical_name_form,
  h.emitted_type,
  h.type_confidence,
  h.language,
  h.char_start,
  h.char_end,
  h.created_at,
  ec.entity_id,
  CASE WHEN ec.entity_id IS NULL THEN NULL ELSE h.resolution_method END,
  CASE WHEN ec.entity_id IS NULL THEN NULL ELSE h.resolution_confidence END,
  CASE WHEN ec.entity_id IS NULL THEN NULL ELSE h.resolution_is_new_entity END,
  CASE WHEN ec.entity_id IS NULL THEN NULL ELSE h.resolved_at END
FROM v_memory_mention_current_content AS h
LEFT JOIN memory_v1.entities_current AS ec
  ON ec.deployment_id = h.deployment_id
 AND ec.entity_id = h.survivor_entity_id;
COMMENT ON VIEW memory_v1.mentions_live IS
  'One row per mention occurring in current content, keyed by (deployment_id, mention_id) and joined to chunks_live on (deployment_id, chunk_id), documents_live on (deployment_id, doc_id), and entities_current on (deployment_id, resolved_entity_id). Membership binds every coordinate of the mention: the chunk must be a current-content chunk and the mention''s own lineage must be that chunk''s lineage, so mentions in superseded versions, in non-ready readings, in forgotten lineages, and mentions whose recorded lineage disagrees with their chunk''s are all absent. Resolution is deliberately nullable and UNRESOLVED MENTIONS REMAIN VISIBLE: the five resolution columns are populated together or not at all, from the mention''s single live, unsuperseded decision and only when the survivor that decision names passes the entities_current provenance gate, so a decision pointing at a retired, merged-away, or provenance-free identity leaves the whole resolution null rather than describing a decision whose subject this schema will not show. The claim coordinate is gated the same way and is null unless that claim is itself a visible claim of this lineage. Merge redirects are resolved before exposure. This relation is source transcript, not evidence and not fact; it carries no counts and no validity clocks.';

CREATE VIEW memory_v1.identity_events_visible (
  deployment_id,       -- The deployment that owns the event.
  object_kind,         -- Which append-only log the event comes from, either resolution_decision or merge_event.
  event_id,            -- Identity of the event within its own log, unique together with object_kind.
  entity_id,           -- The survivor entity the event is about, joinable to entities_current.
  related_entity_id,   -- The counterpart entity of a merge or unmerge, null for a resolution event.
  mention_id,          -- The mention a resolution event decided, null for a merge event.
  outcome,             -- What the event did, one of linked, new_entity, merge, or unmerge.
  method,              -- Which mechanism produced the event, such as a resolution tier or merge_event.
  confidence,          -- Confidence recorded for the decision, null when the log records none.
  decided_by,          -- Whether the decision was automatic or human.
  decided_at,          -- When the decision was made, which is a transaction-time instant.
  is_superseded        -- True once a later decision replaced this one, or a later un-merge reversed it.
) AS
SELECT
  rd.deployment_id,
  'resolution_decision',
  rd.decision_id,
  ec.entity_id,
  NULL::uuid,
  rd.mention_id,
  CASE WHEN rd.is_new_entity THEN 'new_entity' ELSE 'linked' END,
  rd.method::text,
  rd.confidence,
  rd.decided_by::text,
  rd.decided_at,
  (rd.superseded_by IS NOT NULL)
FROM resolution_decisions AS rd
JOIN v_memory_entity_survivor AS s
  ON s.deployment_id = rd.deployment_id
 AND s.entity_id = rd.entity_id
JOIN memory_v1.entities_current AS ec
  ON ec.deployment_id = s.deployment_id
 AND ec.entity_id = s.survivor_entity_id
WHERE EXISTS (
  SELECT 1
  FROM mentions AS m
  JOIN chunks AS ch
    ON ch.deployment_id = m.deployment_id
   AND ch.chunk_id = m.chunk_id
  JOIN memory_v1.document_versions_visible AS vv
    ON vv.deployment_id = ch.deployment_id
   AND vv.version_id = ch.version_id
   AND vv.doc_id = m.doc_id
  WHERE m.deployment_id = rd.deployment_id
    AND m.mention_id = rd.mention_id
)
UNION ALL
SELECT
  me.deployment_id,
  'merge_event',
  me.merge_id,
  ec.entity_id,
  me.absorbed_id,
  NULL::uuid,
  CASE WHEN me.reversed_by IS NOT NULL THEN 'unmerge' ELSE 'merge' END,
  'merge_event',
  NULL::real,
  me.decided_by::text,
  me.decided_at,
  (me.reversed_by IS NOT NULL)
FROM merge_events AS me
JOIN v_memory_entity_survivor AS s
  ON s.deployment_id = me.deployment_id
 AND s.entity_id = me.survivor_id
JOIN memory_v1.entities_current AS ec
  ON ec.deployment_id = s.deployment_id
 AND ec.entity_id = s.survivor_entity_id;
COMMENT ON VIEW memory_v1.identity_events_visible IS
  'One row per visible identity event, keyed by the synthetic pair (deployment_id, object_kind, event_id): each union arm names its own log in object_kind and supplies that log''s own identifier, so the two identifier spaces cannot collide. Resolution events carry a mention and an outcome of linked or new_entity; merge events carry a counterpart entity and an outcome of merge or unmerge, where a split is recorded as the un-merge that reversed a merge. Source-only forgotten events are absent: a resolution event appears only while its mention still resolves to a non-tombstoned version of a live lineage, and every event requires its survivor entity to pass the entities_current provenance gate. Decision clocks are transaction time and carry no world-validity meaning; the view carries no counts and asserts no facts.';
"""

# ── facts layer ───────────────────────────────────────────────────────────
_FACTS_DDL = r"""CREATE VIEW memory_v1.fact_claim_evidence_live (
  deployment_id,           -- The deployment that owns the association.
  fact_kind,               -- Which fact layer the association points at, either relation or observation.
  fact_id,                 -- The adjudicated fact this claim supports or contradicts.
  claim_id,                -- The current-testimony claim on the other side of the bridge.
  stance,                  -- Exactly supports or contradicts, matching the shipped evidence stance vocabulary.
  doc_id,                  -- The live lineage that asserted the claim, which is the unit D54 counts.
  source_kind,             -- The connector family of that lineage.
  source_handle,           -- Stable human-usable handle for that lineage, formed from its connector-native identity.
  asserted_at,             -- When the source asserted the claim, null when the source carries no date.
  claim_valid_from,        -- Immutable start of the world-time interval the SOURCE asserted, null for unbounded-before or unknown.
  claim_valid_until,       -- Immutable end of that interval, null for open-per-source or unknown.
  claim_valid_precision,   -- Granularity of the asserted interval, from unknown through instant to open.
  claim_valid_kind,        -- Which world-interval was asserted, null when unclassified.
  linked_at                -- When the association was recorded, which is a processing instant rather than a validity clock.
) AS
SELECT
  re.deployment_id,
  'relation',
  re.relation_id,
  re.claim_id,
  re.stance::text,
  c.doc_id,
  c.source_kind,
  c.source_handle,
  c.asserted_at,
  c.claim_valid_from,
  c.claim_valid_until,
  c.claim_valid_precision,
  c.claim_valid_kind,
  re.created_at
FROM relation_evidence AS re
JOIN memory_v1.claims_live AS c
  ON c.deployment_id = re.deployment_id
 AND c.claim_id = re.claim_id
 AND c.doc_id = re.doc_id
UNION ALL
SELECT
  oe.deployment_id,
  'observation',
  oe.observation_id,
  oe.claim_id,
  oe.stance::text,
  c.doc_id,
  c.source_kind,
  c.source_handle,
  c.asserted_at,
  c.claim_valid_from,
  c.claim_valid_until,
  c.claim_valid_precision,
  c.claim_valid_kind,
  oe.created_at
FROM observation_evidence AS oe
JOIN memory_v1.claims_live AS c
  ON c.deployment_id = oe.deployment_id
 AND c.claim_id = oe.claim_id
 AND c.doc_id = oe.doc_id;
COMMENT ON VIEW memory_v1.fact_claim_evidence_live IS
  'One row per current claim-to-fact association, keyed by (deployment_id, fact_kind, fact_id, claim_id, stance) and joined to facts_current or facts_visible_history on (deployment_id, fact_kind, fact_id) and to claims_live on (deployment_id, claim_id). This is the AUDITABLE BRIDGE between the two truth layers: it records which immutable testimony supports or contradicts an adjudicated fact, and stance is exactly supports or contradicts. Only current testimony from live lineages appears, and an association whose denormalized lineage disagrees with its claim''s lineage is treated as mismatched state and dropped rather than exposed. The claim-validity columns are the SOURCE''s asserted interval, inclusive at both endpoints and never the fact''s validity; a null endpoint is unbounded or unknown. The view carries no counts: aggregate evidence_lineage instead.';

CREATE VIEW memory_v1.evidence_lineage (
  deployment_id,             -- The deployment that owns the evidence.
  fact_kind,                 -- Which fact layer the evidence points at, either relation or observation.
  fact_id,                   -- The adjudicated fact this lineage supports or contradicts.
  doc_id,                    -- The live document lineage that is the counted unit of corroboration.
  stance,                    -- Exactly supports or contradicts.
  source_kind,               -- The connector family of the lineage.
  source_handle,             -- Stable human-usable handle for the lineage, formed from its connector-native identity.
  claim_count,               -- How many current-testimony claims in this lineage take this stance, which is DESCRIPTIVE ONLY and is never an evidence count.
  representative_claim_id,   -- The most recently asserted claim of this lineage and stance, chosen deterministically as a readable exemplar.
  asserted_from,             -- Earliest assertion instant among those claims, null when none of them carries a date.
  asserted_to                -- Latest assertion instant among those claims, null when none of them carries a date.
) AS
SELECT
  e.deployment_id,
  e.fact_kind,
  e.fact_id,
  e.doc_id,
  e.stance,
  e.source_kind,
  e.source_handle,
  count(*)::bigint,
  (array_agg(e.claim_id ORDER BY e.asserted_at DESC NULLS LAST, e.claim_id))[1],
  min(e.asserted_at),
  max(e.asserted_at)
FROM memory_v1.fact_claim_evidence_live AS e
GROUP BY e.deployment_id, e.fact_kind, e.fact_id, e.doc_id, e.stance,
         e.source_kind, e.source_handle;
COMMENT ON VIEW memory_v1.evidence_lineage IS
  'One row per fact, current-testimony document lineage, and stance, keyed by (deployment_id, fact_kind, fact_id, doc_id, stance) and joined to the fact relations on (deployment_id, fact_kind, fact_id) and to documents_live on (deployment_id, doc_id). THIS RELATION IS THE SOLE PUBLIC INPUT FOR D54 EVIDENCE COUNTS: an evidence count is the number of rows here for a fact and stance, which is exactly the number of distinct current-testimony source lineages, so repeating an assertion inside one document and re-extracting the same document both leave every count unchanged while a genuinely independent second source moves it by one. claim_count is descriptive colour about how loudly one lineage says it and must never be summed into an evidence count. Evidence from forgotten lineages and from superseded testimony is absent. The assertion range is source-asserted event time, not fact validity.';

CREATE VIEW memory_v1.facts_visible_history (
  deployment_id,             -- The deployment that owns the fact.
  fact_kind,                 -- Which fact layer this row belongs to, either relation or observation.
  fact_id,                   -- Stable identity of the adjudicated fact.
  subject_entity_id,         -- Survivor identity of the subject entity, with merge redirects resolved.
  predicate,                 -- The governed predicate of a relation, null for an observation.
  object_entity_id,          -- Survivor identity of the object entity of a relation, null for an observation.
  statement,                 -- The canonical statement of an observation, null for a relation.
  fact_label,                -- Human-readable sentence for the fact, null when no label has been generated.
  valid_from,                -- Raw world-time start of the fact, null for unknown or always.
  valid_until,               -- Raw world-time end of the fact, null while the fact has not been capped; the interval is half-open so a fact does not hold at valid_until.
  ingested_at,               -- Raw transaction-time start: when the system first believed the fact.
  invalidated_at,            -- Raw transaction-time end: when the system learned the fact was superseded, null while it is still believed.
  contradiction_group,       -- Shared identifier of an unadjudicated contradiction, null when the fact is in no contradiction group.
  confidence,                -- Aggregate confidence over the fact's evidence, null when never scored.
  evidence_count_current,    -- LIVE count of distinct current-testimony lineages supporting the fact, read now and never a historical reconstruction.
  contradict_count_current,  -- LIVE count of distinct current-testimony lineages contradicting the fact, read now and never a historical reconstruction.
  support_state_current      -- LIVE support state, exactly current or withdrawn, derived now from the open review queue and never a stored column.
) AS
SELECT
  f.deployment_id,
  f.fact_kind,
  f.fact_id,
  f.subject_entity_id,
  f.predicate,
  f.object_entity_id,
  f.statement,
  f.fact_label,
  f.valid_from,
  f.valid_until,
  f.ingested_at,
  f.invalidated_at,
  f.contradiction_group,
  f.confidence,
  counts.supports,
  counts.contradicts,
  CASE WHEN EXISTS (
    SELECT 1
    FROM review_queue AS q
    WHERE q.deployment_id = f.deployment_id
      AND q.item_kind = 'support_withdrawn'
      AND q.status IN ('pending', 'deferred')
      AND q.candidate ->> 'fact_id' = f.fact_id::text
  ) THEN 'withdrawn' ELSE 'current' END
FROM (
  SELECT
    r.deployment_id, 'relation' AS fact_kind, r.relation_id AS fact_id,
    s1.survivor_entity_id AS subject_entity_id, r.predicate,
    s2.survivor_entity_id AS object_entity_id, NULL::text AS statement,
    r.fact_label, r.valid_from, r.valid_until, r.ingested_at, r.invalidated_at,
    r.contradiction_group, r.confidence
  FROM relations AS r
  JOIN v_memory_entity_survivor AS s1
    ON s1.deployment_id = r.deployment_id
   AND s1.entity_id = r.subject_entity_id
  JOIN v_memory_entity_survivor AS s2
    ON s2.deployment_id = r.deployment_id
   AND s2.entity_id = r.object_entity_id
  WHERE EXISTS (
    SELECT 1
    FROM relation_evidence AS re
    JOIN documents AS d
      ON d.deployment_id = re.deployment_id
     AND d.doc_id = re.doc_id
     AND d.deleted_at IS NULL
    WHERE re.deployment_id = r.deployment_id
      AND re.relation_id = r.relation_id
  )
  UNION ALL
  SELECT
    o.deployment_id, 'observation' AS fact_kind, o.observation_id AS fact_id,
    s1.survivor_entity_id AS subject_entity_id, NULL::text AS predicate,
    NULL::uuid AS object_entity_id, o.statement,
    o.obs_label, o.valid_from, o.valid_until, o.ingested_at, o.invalidated_at,
    o.contradiction_group, o.confidence
  FROM observations AS o
  JOIN v_memory_entity_survivor AS s1
    ON s1.deployment_id = o.deployment_id
   AND s1.entity_id = o.subject_entity_id
  WHERE EXISTS (
    SELECT 1
    FROM observation_evidence AS oe
    JOIN documents AS d
      ON d.deployment_id = oe.deployment_id
     AND d.doc_id = oe.doc_id
     AND d.deleted_at IS NULL
    WHERE oe.deployment_id = o.deployment_id
      AND oe.observation_id = o.observation_id
  )
) AS f
CROSS JOIN LATERAL (
  SELECT
    count(*) FILTER (WHERE el.stance = 'supports')::bigint AS supports,
    count(*) FILTER (WHERE el.stance = 'contradicts')::bigint AS contradicts
  FROM memory_v1.evidence_lineage AS el
  WHERE el.deployment_id = f.deployment_id
    AND el.fact_kind = f.fact_kind
    AND el.fact_id = f.fact_id
) AS counts;
COMMENT ON VIEW memory_v1.facts_visible_history IS
  'One row per historically visible relation or observation, keyed by (deployment_id, fact_kind, fact_id) and joined to fact_claim_evidence_live and evidence_lineage on the same triple. Membership requires SURVIVING HISTORICAL PROVENANCE rather than current support: a fact stays visible while at least one of its evidence lineages is still live, so a fact whose current testimony was processed away remains here with zero current support and a withdrawn support state, while a fact whose every source has been forgotten disappears. Both clocks are RAW: valid_from and valid_until are world time with a half-open interval, ingested_at and invalidated_at are transaction time, a null endpoint is unbounded or unknown, and membership here is not a claim that the fact currently holds. The three columns suffixed _current are LIVE CURRENT-TESTIMONY VALUES READ NOW and are explicitly not historical reconstructions: they never assert that those counts or that support state held at any historical instant. The counts are exact counts of distinct current-testimony lineages taken from evidence_lineage, and support_state_current is derived at read time from the open support_withdrawn review row rather than from any stored column. Entity endpoints are survivor identities.';

CREATE VIEW memory_v1.facts_current (
  deployment_id,         -- The deployment that owns the fact.
  fact_kind,             -- Which fact layer this row belongs to, either relation or observation.
  fact_id,               -- Stable identity of the adjudicated fact.
  subject_entity_id,     -- Survivor identity of the subject entity, with merge redirects resolved.
  predicate,             -- The governed predicate of a relation, null for an observation.
  object_entity_id,      -- Survivor identity of the object entity of a relation, null for an observation.
  statement,             -- The canonical statement of an observation, null for a relation.
  fact_label,            -- Human-readable sentence for the fact, null when no label has been generated.
  valid_from,            -- World-time start of the fact, null for unknown or always.
  valid_until,           -- World-time end of the fact, null while the fact is open; the interval is half-open, so a fact whose valid_until equals the evaluation instant is already excluded.
  ingested_at,           -- Transaction-time start: when the system first believed the fact.
  contradiction_group,   -- Shared identifier of an unadjudicated contradiction, null when the fact is in no contradiction group.
  confidence,            -- Aggregate confidence over the fact's evidence, null when never scored.
  evidence_count,        -- Exact count of distinct current-testimony lineages supporting the fact.
  contradict_count,      -- Exact count of distinct current-testimony lineages contradicting the fact.
  support_state,         -- Exactly current or withdrawn, derived at read time from the open review queue.
  evaluated_at           -- The single statement instant at which both clocks were applied, shared by every current relation referenced in the same statement.
) AS
SELECT
  h.deployment_id,
  h.fact_kind,
  h.fact_id,
  h.subject_entity_id,
  h.predicate,
  h.object_entity_id,
  h.statement,
  h.fact_label,
  h.valid_from,
  h.valid_until,
  h.ingested_at,
  h.contradiction_group,
  h.confidence,
  h.evidence_count_current,
  h.contradict_count_current,
  h.support_state_current,
  clock.evaluated_at
FROM (SELECT statement_timestamp() AS evaluated_at) AS clock
CROSS JOIN memory_v1.facts_visible_history AS h
WHERE h.ingested_at <= clock.evaluated_at
  AND h.invalidated_at IS NULL
  AND (h.valid_from IS NULL OR h.valid_from <= clock.evaluated_at)
  AND (h.valid_until IS NULL OR h.valid_until > clock.evaluated_at);
COMMENT ON VIEW memory_v1.facts_current IS
  'One row per currently valid relation or observation, keyed by (deployment_id, fact_kind, fact_id) and joined to fact_claim_evidence_live and evidence_lineage on the same triple. THIS IS THE ADJUDICATED CURRENT WORLDVIEW and the right starting point for any what-holds-now question; claim relations answer what a source said, never what the system believes. Both D41 clocks are applied at exactly one instant: the fact must have been believed by evaluated_at and not since invalidated, and its world-time interval must contain evaluated_at under a half-open convention where valid_from is inclusive and valid_until is exclusive, with a null endpoint meaning unbounded. Every reference to a current relation inside one SQL statement observes that same evaluated_at, which is emitted on every row. Membership additionally requires a surviving provenance lineage, so forgotten sources remove the fact rather than orphaning it. The counts are exact counts of distinct current-testimony lineages taken from evidence_lineage, and support_state is derived at read time from the open support_withdrawn review row: a zero count never manufactures a withdrawn state and deletion never infers one.';

CREATE VIEW memory_v1.contradiction_members_current (
  deployment_id,         -- The deployment that owns the fact.
  contradiction_group,   -- The shared identifier binding the members of one unadjudicated contradiction.
  fact_kind,             -- Which fact layer this member belongs to, either relation or observation.
  fact_id,               -- Stable identity of the member fact.
  fact_label,            -- Human-readable sentence for the member, null when no label has been generated.
  valid_from,            -- World-time start of the member, null for unknown or always.
  valid_until,           -- World-time end of the member, null while the member is open.
  ingested_at,           -- Transaction-time start: when the system first believed the member.
  evidence_count,        -- Exact count of distinct current-testimony lineages supporting the member.
  contradict_count,      -- Exact count of distinct current-testimony lineages contradicting the member.
  support_state,         -- Exactly current or withdrawn, derived at read time from the open review queue.
  evaluated_at           -- The single statement instant at which both clocks were applied, shared with every other current relation in the statement.
) AS
SELECT
  f.deployment_id,
  f.contradiction_group,
  f.fact_kind,
  f.fact_id,
  f.fact_label,
  f.valid_from,
  f.valid_until,
  f.ingested_at,
  f.evidence_count,
  f.contradict_count,
  f.support_state,
  f.evaluated_at
FROM memory_v1.facts_current AS f
WHERE f.contradiction_group IS NOT NULL;
COMMENT ON VIEW memory_v1.contradiction_members_current IS
  'One row per current member of a contradiction group, keyed by (deployment_id, contradiction_group, fact_kind, fact_id) and joined to facts_current on (deployment_id, fact_kind, fact_id). A contradiction group is the system declining to silently pick a winner, so both sides stand and are visible here with their own clocks, counts, and support state. Membership, clocks, and the shared evaluation instant are inherited unchanged from facts_current, including the half-open world-time interval and the surviving-provenance requirement. Because arbitrary SQL can still filter this relation down to one side, a result built from it carries no platform guarantee that co-members are complete: that guarantee belongs to the assured operations. The counts are exact counts of distinct current-testimony lineages, and support state is derived at read time.';

CREATE VIEW memory_v1.graph_edges_current (
  deployment_id,         -- The deployment that owns the edge.
  relation_id,           -- Stable identity of the relation this edge projects.
  subject_entity_id,     -- Survivor identity of the edge's source entity, guaranteed present in entities_current.
  object_entity_id,      -- Survivor identity of the edge's target entity, guaranteed present in entities_current.
  predicate,             -- The governed predicate carried by the edge.
  fact_label,            -- Human-readable sentence for the relation, null when no label has been generated.
  valid_from,            -- World-time start of the relation, null for unknown or always.
  valid_until,           -- World-time end of the relation, null while it is open; the interval is half-open.
  ingested_at,           -- Transaction-time start: when the system first believed the relation.
  contradiction_group,   -- Shared identifier of an unadjudicated contradiction, null when the edge is in no contradiction group.
  confidence,            -- Aggregate confidence over the relation's evidence, null when never scored.
  evidence_count,        -- Exact count of distinct current-testimony lineages supporting the relation.
  contradict_count,      -- Exact count of distinct current-testimony lineages contradicting the relation.
  support_state,         -- Exactly current or withdrawn, derived at read time from the open review queue.
  evaluated_at           -- The single statement instant at which both clocks were applied, shared with every other current relation in the statement.
) AS
SELECT
  f.deployment_id,
  f.fact_id,
  f.subject_entity_id,
  f.object_entity_id,
  f.predicate,
  f.fact_label,
  f.valid_from,
  f.valid_until,
  f.ingested_at,
  f.contradiction_group,
  f.confidence,
  f.evidence_count,
  f.contradict_count,
  f.support_state,
  f.evaluated_at
FROM memory_v1.facts_current AS f
JOIN memory_v1.entities_current AS subject
  ON subject.deployment_id = f.deployment_id
 AND subject.entity_id = f.subject_entity_id
JOIN memory_v1.entities_current AS object
  ON object.deployment_id = f.deployment_id
 AND object.entity_id = f.object_entity_id
WHERE f.fact_kind = 'relation';
COMMENT ON VIEW memory_v1.graph_edges_current IS
  'One row per current relation edge, keyed by (deployment_id, relation_id) and joined to entities_current on (deployment_id, subject_entity_id) and (deployment_id, object_entity_id). This is the LIVE graph surface, evaluated in PostgreSQL rather than read from a projection: it inherits the facts_current membership rule, the same half-open world-time interval, and the same shared evaluation instant, which is emitted on every row. Both endpoints are survivor identities and both are required to be visible entities, so an edge is dropped as a unit rather than dangling into an entity that has no surviving provenance. The counts are exact counts of distinct current-testimony lineages, and support state is derived at read time from the open review queue. Observations never project here, because they are entity-anchored facts rather than edges.';

CREATE VIEW memory_v1.graph_edges_visible_history (
  deployment_id,             -- The deployment that owns the edge.
  relation_id,               -- Stable identity of the relation this edge projects.
  subject_entity_id,         -- Survivor identity of the edge's source entity, guaranteed present in entities_current.
  object_entity_id,          -- Survivor identity of the edge's target entity, guaranteed present in entities_current.
  predicate,                 -- The governed predicate carried by the edge.
  fact_label,                -- Human-readable sentence for the relation, null when no label has been generated.
  valid_from,                -- Raw world-time start of the relation, null for unknown or always.
  valid_until,               -- Raw world-time end of the relation, null while it has not been capped; the interval is half-open.
  ingested_at,               -- Raw transaction-time start: when the system first believed the relation.
  invalidated_at,            -- Raw transaction-time end: when the system learned the relation was superseded, null while it is still believed.
  contradiction_group,       -- Shared identifier of an unadjudicated contradiction, null when the edge is in no contradiction group.
  confidence,                -- Aggregate confidence over the relation's evidence, null when never scored.
  evidence_count_current,    -- LIVE count of distinct current-testimony lineages supporting the relation, read now and never a historical reconstruction.
  contradict_count_current,  -- LIVE count of distinct current-testimony lineages contradicting the relation, read now and never a historical reconstruction.
  support_state_current      -- LIVE support state, exactly current or withdrawn, read now and never a historical reconstruction.
) AS
SELECT
  h.deployment_id,
  h.fact_id,
  h.subject_entity_id,
  h.object_entity_id,
  h.predicate,
  h.fact_label,
  h.valid_from,
  h.valid_until,
  h.ingested_at,
  h.invalidated_at,
  h.contradiction_group,
  h.confidence,
  h.evidence_count_current,
  h.contradict_count_current,
  h.support_state_current
FROM memory_v1.facts_visible_history AS h
JOIN memory_v1.entities_current AS subject
  ON subject.deployment_id = h.deployment_id
 AND subject.entity_id = h.subject_entity_id
JOIN memory_v1.entities_current AS object
  ON object.deployment_id = h.deployment_id
 AND object.entity_id = h.object_entity_id
WHERE h.fact_kind = 'relation';
COMMENT ON VIEW memory_v1.graph_edges_visible_history IS
  'One row per historically visible relation edge, keyed by (deployment_id, relation_id) and joined to entities_current on both endpoint columns. Membership requires surviving historical provenance and two visible survivor endpoints, so a relation whose sources have all been forgotten disappears and an edge is never left dangling. Both clocks are RAW: world time is half-open, transaction time is bounded by ingested_at and invalidated_at, a null endpoint is unbounded or unknown, and membership here is not a claim that the edge currently holds. The three columns suffixed _current are LIVE CURRENT-TESTIMONY VALUES READ NOW and never assert that those counts or that support state held at any historical instant; they come from evidence_lineage and from the open support_withdrawn review row respectively.';
"""

# ── cross-references, K plane, and the change feed ────────────────────────
_SURROUND_DDL = r"""CREATE VIEW memory_v1.document_crossrefs_live (
  deployment_id,   -- The deployment that owns both endpoint lineages.
  crossref_id,     -- Stable identity of this cross-reference.
  from_doc_id,     -- The live lineage that makes the reference.
  to_doc_id,       -- The live lineage that is referenced.
  kind,            -- What kind of reference this is, one of cites, links_to, attaches, or replies_to.
  context,         -- Bounded surrounding context of the reference, truncated to 500 characters and null when none was captured.
  created_at       -- When the reference was extracted, which is a processing instant.
) AS
SELECT
  x.deployment_id,
  x.crossref_id,
  source.doc_id,
  target.doc_id,
  x.kind::text,
  left(x.context, 500),
  x.created_at
FROM document_crossrefs AS x
JOIN memory_v1.documents_live AS source
  ON source.deployment_id = x.deployment_id
 AND source.doc_id = x.from_doc_id
JOIN memory_v1.documents_live AS target
  ON target.deployment_id = x.deployment_id
 AND target.doc_id = x.to_doc_id
WHERE x.resolved AND x.to_doc_id IS NOT NULL;
COMMENT ON VIEW memory_v1.document_crossrefs_live IS
  'One row per resolved cross-reference whose BOTH endpoint lineages are live, keyed by (deployment_id, crossref_id) and joined to documents_live on (deployment_id, from_doc_id) and (deployment_id, to_doc_id). An unresolved reference, a reference whose target was never ingested, or one whose source or target lineage has been forgotten is absent rather than half-resolved, so this relation never reveals that a document once existed. The raw citation text is deliberately not exposed, because it is retained even after a target is forgotten; the bounded context is truncated to 500 characters. The creation clock is a processing instant, and the view carries no counts and asserts no facts.';

CREATE VIEW v_memory_page_citation_visible (
  deployment_id,
  artifact_id,
  role,
  target_kind,
  target_id,
  claim_chunk_content_hash
) AS
-- one arm per target class, rather than one scan with an OR of two gates: an
-- OR across two subqueries forces the planner to materialize each gate in
-- full, while separate arms let each one be a semi-join against its own index
SELECT
  e.deployment_id,
  e.artifact_id,
  e.role::text,
  CASE WHEN e.claim_lineage_id IS NOT NULL THEN 'claim' ELSE 'document' END,
  coalesce(e.claim_lineage_id, e.doc_id),
  e.claim_chunk_content_hash
FROM knowledge_artifact_evidence AS e
WHERE (e.claim_lineage_id IS NOT NULL OR e.relation_id IS NULL)
  AND EXISTS (
    SELECT 1
    FROM memory_v1.documents_live AS d
    WHERE d.deployment_id = e.deployment_id
      AND d.doc_id = coalesce(e.claim_lineage_id, e.doc_id)
  )
UNION ALL
SELECT
  e.deployment_id,
  e.artifact_id,
  e.role::text,
  'relation',
  e.relation_id,
  e.claim_chunk_content_hash
FROM knowledge_artifact_evidence AS e
WHERE e.claim_lineage_id IS NULL
  AND e.relation_id IS NOT NULL
  AND EXISTS (
    SELECT 1
    FROM memory_v1.facts_visible_history AS f
    WHERE f.deployment_id = e.deployment_id
      AND f.fact_kind = 'relation'
      AND f.fact_id = e.relation_id
  );
COMMENT ON VIEW v_memory_page_citation_visible IS
  'Private single definition of a knowledge citation whose target is still visible: one row per citation link whose cited lineage is live or whose cited relation still has surviving provenance. Both the membership gate of pages_live and the per-link projection of page_evidence_visible read it, so a page cannot be published with provenance its links cannot show. Not part of memory_v1 and never granted to a query role.';

CREATE VIEW memory_v1.pages_live (
  deployment_id,        -- The deployment that owns the artifact.
  artifact_id,          -- Stable identity of this knowledge artifact.
  layer,                -- Content tier of the artifact, one of K1, K2, or K3.
  page_kind,            -- Ownership contract of the body, either compiled when machine-owned or authored when human-owned.
  git_path,             -- Path of the artifact's file in the knowledge repository.
  kind,                 -- Free-form editorial kind such as summary or profile, null when unset.
  parent_artifact_id,   -- Parent artifact in the compile tree, exposed only while that parent is itself visible and null otherwise.
  page_summary,         -- Writer-emitted abstract of the page; it is compiled orientation prose and is never asserted evidence.
  status,               -- Lifecycle status of the artifact, one of active, stale, or quarantined.
  last_compiled_at,     -- When the artifact was last compiled, null when it has never been compiled.
  is_stale,             -- True when a compiled artifact is known to lag its inputs, either by status or by an unprocessed refresh.
  open_review_flags,    -- Exact count of unprocessed authored-review flags on the artifact, always zero for a compiled page.
  redaction_required    -- True when an open authored-review flag asks the author to redact content.
) AS
SELECT
  a.deployment_id,
  a.artifact_id,
  a.layer::text,
  a.page_kind::text,
  a.git_path,
  a.kind,
  parent.artifact_id,
  a.page_summary,
  a.status::text,
  a.last_compiled_at,
  (a.page_kind = 'compiled' AND (
    a.status = 'stale'
    OR EXISTS (
      SELECT 1
      FROM knowledge_refresh_queue AS q
      WHERE q.deployment_id = a.deployment_id
        AND q.artifact_id = a.artifact_id
        AND q.processed_at IS NULL
    )
  )),
  flags.open_review_flags,
  flags.redaction_required
FROM knowledge_artifacts AS a
LEFT JOIN knowledge_artifacts AS parent
  ON parent.deployment_id = a.deployment_id
 AND parent.artifact_id = a.parent_artifact_id
 AND parent.status <> 'tombstoned'
 AND EXISTS (
   SELECT 1
   FROM v_memory_page_citation_visible AS pc
   WHERE pc.deployment_id = parent.deployment_id
     AND pc.artifact_id = parent.artifact_id
 )
CROSS JOIN LATERAL (
  -- an authored page carries review flags; the predicate on page_kind lives in
  -- the WHERE clause so a compiled page aggregates over an empty set
  SELECT
    count(*)::bigint AS open_review_flags,
    coalesce(bool_or(
      coalesce((q.payload ->> 'redaction_required')::boolean, false)
    ), false) AS redaction_required
  FROM knowledge_refresh_queue AS q
  WHERE q.deployment_id = a.deployment_id
    AND q.artifact_id = a.artifact_id
    AND q.trigger = 'authored_review'
    AND q.processed_at IS NULL
    AND a.page_kind = 'authored'
) AS flags
WHERE a.status <> 'tombstoned'
  AND EXISTS (
    SELECT 1
    FROM v_memory_page_citation_visible AS c
    WHERE c.deployment_id = a.deployment_id
      AND c.artifact_id = a.artifact_id
  );
COMMENT ON VIEW memory_v1.pages_live IS
  'One row per visible knowledge artifact, keyed by (deployment_id, artifact_id) and joined to page_evidence_visible on the same pair. Membership is FAIL-CLOSED ON PROVENANCE as well as on status: an artifact appears only while it is not tombstoned AND at least one of its citations still points at a visible target, so a page whose every cited source has been forgotten leaves with them instead of surviving as compiled prose about content this deployment can no longer show. Both page kinds carry citations, so an artifact with none is anomalous rather than ordinary: it is absent here and counted in the operator quarantine report, where it can be recompiled or retired. A tombstoned parent, and a parent that is itself absent for either reason, is reported as null rather than dangling. Everything textual here is COMPILED ORIENTATION PROSE AT COMPILED GRAIN: page_summary is a writer''s abstract of cited evidence and can never be promoted to a live fact, and the artifact body itself lives in the knowledge repository rather than in this schema. The review-flag count is exact over unprocessed flags, is_stale means the compiled page is known to lag its inputs, and last_compiled_at is a processing instant rather than a validity clock.';

CREATE VIEW memory_v1.page_evidence_visible (
  deployment_id,               -- The deployment that owns the association.
  artifact_id,                 -- The visible knowledge artifact that carries the citation.
  role,                        -- What the citation does, one of supports, contradicts, or cites.
  target_kind,                 -- What is cited, one of claim, relation, or document.
  target_id,                   -- The cited target: the asserting lineage for a claim citation, the relation for a relation citation, or the lineage for a document citation.
  claim_chunk_content_hashes,  -- Sorted chunk-content hashes locating the cited claims inside the lineage, null for non-claim targets; these are locators only and never authorize a read.
  link_count                   -- Exact number of underlying citation links collapsed into this association.
) AS
SELECT
  l.deployment_id,
  l.artifact_id,
  l.role,
  l.target_kind,
  l.target_id,
  array_agg(DISTINCT l.claim_chunk_content_hash ORDER BY l.claim_chunk_content_hash)
    FILTER (WHERE l.claim_chunk_content_hash IS NOT NULL),
  count(*)::bigint
FROM v_memory_page_citation_visible AS l
JOIN memory_v1.pages_live AS p
  ON p.deployment_id = l.deployment_id
 AND p.artifact_id = l.artifact_id
GROUP BY l.deployment_id, l.artifact_id, l.role, l.target_kind, l.target_id;
COMMENT ON VIEW memory_v1.page_evidence_visible IS
  'One row per visible artifact-to-target citation, keyed by (deployment_id, artifact_id, role, target_kind, target_id) and joined to pages_live on (deployment_id, artifact_id), documents_live on target_id for claim and document targets, and the fact relations on target_id for relation targets. EACH TARGET PASSES ITS OWN VISIBILITY GATE: a citation appears only while its cited lineage is live or its cited relation still has surviving provenance, so forgetting a source removes the link rather than leaving a reference to vanished content. The same gate decides membership in pages_live, so a visible page always has at least one row here and a link never outlives the page that carries it. A claim citation is a stable coordinate on the asserting LINEAGE, and its chunk content hashes are exposed only as locators inside that already authorized lineage: the hash never authorizes a read and cannot be used to bypass the lineage gate. Because several chunk coordinates in one lineage collapse into one association, link_count reports exactly how many underlying links were collapsed. The view carries no clocks.';

CREATE VIEW memory_v1.changes_visible (
  deployment_id,   -- The deployment that owns the change event.
  object_kind,     -- Which kind of change event this is, naming both the changed object and the transition.
  event_id,        -- Identity of the event within its own source, unique together with object_kind.
  object_id,       -- The object that changed, joinable to the relation named by object_kind.
  occurred_at,     -- When the change occurred, which is a transaction-time instant and never world validity.
  label            -- Short human-readable label for the changed object, drawn only from objects that are themselves visible.
) AS
SELECT h.deployment_id, 'relation_ingest', h.fact_id, h.fact_id, h.ingested_at,
       coalesce(h.fact_label, h.predicate)
FROM memory_v1.facts_visible_history AS h
WHERE h.fact_kind = 'relation'
UNION ALL
SELECT h.deployment_id, 'relation_invalidation', h.fact_id, h.fact_id,
       h.invalidated_at, coalesce(h.fact_label, h.predicate)
FROM memory_v1.facts_visible_history AS h
WHERE h.fact_kind = 'relation' AND h.invalidated_at IS NOT NULL
UNION ALL
SELECT ra.deployment_id, 'relation_supersession', ra.adjudication_id, h.fact_id,
       ra.decided_at, coalesce(h.fact_label, h.predicate)
FROM relation_adjudications AS ra
JOIN memory_v1.facts_visible_history AS h
  ON h.deployment_id = ra.deployment_id
 AND h.fact_kind = 'relation'
 AND h.fact_id = ra.relation_id
WHERE ra.outcome = 'supersede'
UNION ALL
SELECT h.deployment_id, 'observation_ingest', h.fact_id, h.fact_id, h.ingested_at,
       coalesce(h.fact_label, h.statement)
FROM memory_v1.facts_visible_history AS h
WHERE h.fact_kind = 'observation'
UNION ALL
SELECT h.deployment_id, 'observation_invalidation', h.fact_id, h.fact_id,
       h.invalidated_at, coalesce(h.fact_label, h.statement)
FROM memory_v1.facts_visible_history AS h
WHERE h.fact_kind = 'observation' AND h.invalidated_at IS NOT NULL
UNION ALL
SELECT oa.deployment_id, 'observation_supersession', oa.adjudication_id, h.fact_id,
       oa.decided_at, coalesce(h.fact_label, h.statement)
FROM observation_adjudications AS oa
JOIN memory_v1.facts_visible_history AS h
  ON h.deployment_id = oa.deployment_id
 AND h.fact_kind = 'observation'
 AND h.fact_id = oa.observation_id
WHERE oa.outcome = 'supersede'
UNION ALL
SELECT c.deployment_id, 'claim_ingest', c.claim_id, c.claim_id, c.ingested_at,
       left(c.claim_text, 120)
FROM memory_v1.claims_visible_history AS c
UNION ALL
SELECT kc.deployment_id, 'knowledge_page_compilation', kc.compilation_id,
       p.artifact_id, kc.compiled_at, p.git_path
FROM knowledge_compilations AS kc
JOIN memory_v1.pages_live AS p
  ON p.deployment_id = kc.deployment_id
 AND p.artifact_id = kc.artifact_id;
COMMENT ON VIEW memory_v1.changes_visible IS
  'One row per externally visible change event, keyed by the synthetic pair (deployment_id, object_kind, event_id): every union arm names its own transition in object_kind and supplies an identifier from its own source table, so the underlying identifier spaces cannot collide. Each arm reads an already invariant-bearing relation or joins one, so a change event appears only while its object is still visible. THERE IS DELIBERATELY NO DELETION ARM: forgetting a lineage, tombstoning a version, or retiring a page removes the affected events instead of announcing the removal, and labels are drawn only from visible objects, so neither the event set nor the label text can become a side channel for what was forgotten. The occurrence clock is transaction time and never world validity, the feed is uncapped at the relation level so a caller bounds it with an ordinary predicate, and the view carries no counts and asserts no facts.';
"""


#: The authored DDL of the query space, in creation order. These strings are
#: the CANONICAL SOURCE of the public surface: the migration executes exactly
#: them, and the manifest's canonical AST is PostgreSQL's own parse of exactly
#: them, so editing one here rolls `surface_manifest_hash` — while reformatting
#: or re-commenting one cannot, because a parse tree has neither.
MEMORY_V1_AUTHORED_DDL: tuple[str, ...] = (
    _SCHEMA_DDL,
    _HELPER_DDL,
    _CONTENT_DDL,
    _TESTIMONY_DDL,
    _ENTITY_DDL,
    _FACTS_DDL,
    _SURROUND_DDL,
)


def upgrade() -> None:
    """Create the memory_v1 query space and its private helper views."""
    apply_ddl(sql=_SCHEMA_DDL)
    apply_view_ddl(sql=_HELPER_DDL)
    apply_view_ddl(sql=_CONTENT_DDL)
    apply_view_ddl(sql=_TESTIMONY_DDL)
    apply_view_ddl(sql=_ENTITY_DDL)
    apply_view_ddl(sql=_FACTS_DDL)
    apply_view_ddl(sql=_SURROUND_DDL)


def downgrade() -> None:
    """Remove the whole query space and the helpers it depends on."""
    drop_views(view_names=_DROP_ORDER)
    op.execute("DROP SCHEMA IF EXISTS memory_v1")
