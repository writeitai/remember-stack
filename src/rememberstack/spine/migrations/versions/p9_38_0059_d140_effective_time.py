"""D140 effective periods, section keys, text origin, and version-grain references.

Every schema object of the D140 design (effective time and section references)
lands in this one revision:

- **Effective time.** ``document_versions.version_key`` (a caller-chosen,
  immutable version address, unique per lineage), the declaration ledger
  ``document_effective_periods``, the lineage mode ledger
  ``document_effective_time_events`` and the current-belief selection
  projection ``document_version_scope``. The projection is maintained by
  database triggers in the transaction of every write that can change it (a
  declaration or retraction, a mode event, a version becoming ready or current,
  a version or lineage deletion) through ``refresh_document_version_scope``,
  which rewrites one lineage's rows. The triggers are constraint triggers
  deferred to commit: the rewrite then sees the transaction's final state
  once, and its per-lineage lock is taken after every row lock the writer
  holds, so it cannot join a writer's lock order (an ingest holding the
  lineage row while a version finishes processing would otherwise deadlock). It is filled here for every existing
  lineage: all are undeclared, so each served version gets an unbounded range
  and every other version an empty one.
- **Public time-scope surface.** ``memory_v1.effective_intervals``,
  ``memory_v1.versions_in_scope`` and ``memory_v1.fact_in_scope_support``
  (``SECURITY DEFINER`` owned by the view owner, since they read private
  ledgers), the views ``memory_v1.document_effective_periods_live`` and
  ``memory_v1.chunks_all_versions_live``, and the new trailing columns of
  ``sections_live`` (section key and content hashes) and ``chunks_live``
  (text origin time).
- **Sections and chunks.** Nullable ``section_key``, ``own_content_hash`` and
  ``subtree_content_hash`` on ``document_sections`` (a key is unique within a
  structure generation) and nullable ``text_origin_at`` and
  ``reuse_identity_hash`` on ``chunks``. Existing rows keep NULL: sections are
  backfilled by a maintenance job, and pre-D140 chunks are never matched by
  the text-origin lookup.
- **References.** ``crossref_kind`` gains ``refers_to``, ``amends`` and
  ``implements``; ``crossref_binding`` and ``crossref_origin`` are new;
  ``document_reference_generations`` is new; ``document_crossrefs`` is
  recreated at version grain. No code on the prior revision writes that table,
  so the migration refuses a store in which it holds rows rather than guessing
  a version and generation for them. The enum is recreated rather than
  extended because the new table's CHECK constraints compare with ``amends``,
  and PostgreSQL forbids using an enum value added in the same transaction.
  The public view, the private graph source view (now one edge per lineage
  pair and kind among the references of the versions in force now) and the
  live property graph are recreated in the same transaction, with their grants.

The downgrade refuses when any D140 data exists — a declaration or mode event
(live or retracted), a version key, or a reference generation — because none
of it can be re-derived. Otherwise it restores the prior objects exactly.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import apply_view_ddl
from rememberstack.spine.migrations._helpers import view_column_comments
from rememberstack.spine.migrations.versions.p9_17_0038_postgres19_live_graph import (
    _CURRENT_GRAPH,
)

revision: str = "p9_38_0059"
down_revision: str | None = "p9_37_0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VIEW_OWNER = "rememberstack_view_owner"

_EFFECTIVE_TIME_DDL = r"""
ALTER TABLE document_versions ADD COLUMN version_key text;
COMMENT ON COLUMN document_versions.version_key IS
  'D140: optional caller-chosen version address, assigned only when the version is created (a key new to the lineage always creates a version; an existing key on any other observation is rejected). Unique per lineage and immutable; the address pinned references use. Not the mutable source_version_ref cursor.';
CREATE UNIQUE INDEX ux_docversions_version_key
  ON document_versions (deployment_id, doc_id, version_key) WHERE version_key IS NOT NULL;

CREATE TABLE document_effective_time_events (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  doc_id          uuid NOT NULL,               -- the lineage whose effective-time mode changed
  event_at        timestamptz NOT NULL DEFAULT now(), -- when the mode changed; the lineage is periodised at a belief instant when its latest event at or before it is declared
  event           text NOT NULL CHECK (event IN ('declared','cleared')), -- declared: the first declaration of an undeclared lineage; cleared: clear_effective_time
  PRIMARY KEY (deployment_id, doc_id, event_at),
  FOREIGN KEY (deployment_id, doc_id) REFERENCES documents (deployment_id, doc_id)
);
COMMENT ON TABLE document_effective_time_events IS
  'D140 lineage effective-time mode transitions, a ledger evaluated at a belief instant. Periodisation is sticky: retracting declarations never writes an event; only clear_effective_time records cleared. Hard forget deletes the lineage''s rows.';

CREATE TABLE document_effective_periods (
  period_id       uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  doc_id          uuid NOT NULL,               -- the lineage; the composite FK binds the version to it
  version_id      uuid NOT NULL,               -- the version declared in force
  effective_from  timestamptz NOT NULL,        -- inclusive start of the declared period
  effective_until timestamptz,                 -- exclusive declared end; NULL = until the next declared start in the lineage, derived at read time
  declared_at     timestamptz NOT NULL DEFAULT now(), -- when the declaration became known (belief axis)
  declared_by     text NOT NULL CHECK (declared_by IN ('ingest','period_api')), -- ingest (POST /ingest) or period_api (PUT effective-periods)
  retracted_at    timestamptz,                 -- when a correction replaced or removed the declaration; NULL while live
  retracted_by_period_id uuid,                 -- the declaration that replaced this one, NULL when it was only removed
  CHECK (effective_until IS NULL OR effective_until > effective_from),
  CHECK (retracted_at IS NULL OR retracted_at >= declared_at),
  FOREIGN KEY (deployment_id, doc_id, version_id)
    REFERENCES document_versions (deployment_id, doc_id, version_id)
);
COMMENT ON TABLE document_effective_periods IS
  'D140 declared effective periods: when a snapshot-lineage version''s text is in force per its publisher. A ledger evaluated as known at a belief instant; not extraction input; never inferred. Time-scoped reads select versions through memory_v1.versions_in_scope.';
CREATE UNIQUE INDEX ux_effective_periods_live_start
  ON document_effective_periods (deployment_id, doc_id, effective_from) WHERE retracted_at IS NULL;
CREATE INDEX ix_effective_periods_lineage
  ON document_effective_periods (deployment_id, doc_id, effective_from);

CREATE TABLE document_version_scope (
  deployment_id   uuid NOT NULL REFERENCES deployments,
  doc_id          uuid NOT NULL,               -- the live lineage the version belongs to
  version_id      uuid NOT NULL,               -- one non-deleted version of that lineage
  in_force        tstzmultirange NOT NULL,     -- periodised: the derived in-force intervals as currently believed; undeclared: unbounded for the served version, empty otherwise
  periodised      boolean NOT NULL,            -- the lineage's latest effective-time event is declared
  selectable      boolean NOT NULL,            -- the version is ready with a ready current representation
  PRIMARY KEY (deployment_id, version_id),
  FOREIGN KEY (deployment_id, doc_id, version_id) REFERENCES document_versions (deployment_id, doc_id, version_id) ON DELETE CASCADE
);
COMMENT ON TABLE document_version_scope IS
  'D140 current-belief selection projection: one row per non-deleted version of every live lineage, rewritten per lineage by refresh_document_version_scope in the transaction of every write that changes it (declaration, retraction, mode event, readiness or current-pointer move, deletion). Rebuildable from the ledgers and version rows, so a hard-deleted version row takes its projection row with it (ON DELETE CASCADE); the ledgers keep no-cascade foreign keys.';
CREATE INDEX ix_version_scope_in_force ON document_version_scope USING gist (deployment_id, in_force) WHERE selectable;
CREATE INDEX ix_version_scope_lineage ON document_version_scope (deployment_id, doc_id);
CREATE INDEX ix_effective_periods_declared_at ON document_effective_periods (deployment_id, declared_at);
CREATE INDEX ix_effective_periods_retracted_at ON document_effective_periods (deployment_id, retracted_at) WHERE retracted_at IS NOT NULL;
CREATE INDEX ix_effective_time_events_at ON document_effective_time_events (deployment_id, event_at);
CREATE INDEX ix_version_scope_pending ON document_version_scope USING gist (deployment_id, in_force) WHERE NOT selectable;
"""

_SCOPE_FUNCTIONS_DDL = r"""
CREATE FUNCTION document_effective_intervals_at(
  p_deployment_id uuid,
  p_doc_ids uuid[],
  p_believed_at timestamptz
)
RETURNS TABLE (
  doc_id uuid,
  version_id uuid,
  period_id uuid,
  effective_from timestamptz,
  effective_until timestamptz,
  until_declared boolean
)
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT
    p.doc_id,
    p.version_id,
    p.period_id,
    p.effective_from,
    coalesce(
      p.effective_until,
      lead(p.effective_from) OVER (PARTITION BY p.doc_id ORDER BY p.effective_from)
    ),
    p.effective_until IS NOT NULL
  FROM public.document_effective_periods AS p
  JOIN public.documents AS d
    ON d.deployment_id = p.deployment_id
   AND d.doc_id = p.doc_id
   AND d.deleted_at IS NULL
  JOIN public.document_versions AS v
    ON v.deployment_id = p.deployment_id
   AND v.doc_id = p.doc_id
   AND v.version_id = p.version_id
   AND v.deleted_at IS NULL
  WHERE p.deployment_id = p_deployment_id
    AND p.doc_id = ANY(p_doc_ids)
    AND p.declared_at <= p_believed_at
    AND (p.retracted_at IS NULL OR p.retracted_at > p_believed_at)
$$;
COMMENT ON FUNCTION document_effective_intervals_at(uuid, uuid[], timestamptz) IS
  'D140 section 2.2: the in-force interval of every declaration known at the belief instant (declared at or before it and not retracted by then) of the non-deleted versions of the given live lineages. The end is the declared end, else the next known declared start in the lineage, else open. Processing status is deliberately ignored.';

CREATE FUNCTION document_periodised_at(
  p_deployment_id uuid,
  p_doc_id uuid,
  p_believed_at timestamptz
)
RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT coalesce(
    (
      SELECT e.event = 'declared'
      FROM public.document_effective_time_events AS e
      WHERE e.deployment_id = p_deployment_id
        AND e.doc_id = p_doc_id
        AND e.event_at <= p_believed_at
      ORDER BY e.event_at DESC
      LIMIT 1
    ),
    false
  )
$$;
COMMENT ON FUNCTION document_periodised_at(uuid, uuid, timestamptz) IS
  'D140: whether a lineage is periodised at a belief instant, i.e. its latest effective-time event at or before that instant is declared.';

CREATE FUNCTION refresh_document_version_scope(p_deployment_id uuid, p_doc_id uuid)
RETURNS void
LANGUAGE plpgsql
VOLATILE
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  -- Current belief is every live declaration and the latest mode event,
  -- evaluated at infinity: writers stamp rows with the instant they took
  -- after locking the lineage, which is later than this transaction's now().
  -- One rewrite of a lineage at a time: concurrent writers of the same lineage
  -- would otherwise each delete the rows they can see and collide on insert.
  -- The triggers are deferred to commit, so this lock is taken after the
  -- transaction's own row locks and never inside another writer's lock order.
  PERFORM pg_advisory_xact_lock(
    hashtextextended(
      'document_version_scope:' || p_deployment_id::text || ':' || p_doc_id::text, 0
    )
  );
  DELETE FROM public.document_version_scope
  WHERE deployment_id = p_deployment_id AND doc_id = p_doc_id;
  INSERT INTO public.document_version_scope (
    deployment_id, doc_id, version_id, in_force, periodised, selectable
  )
  WITH lineage AS (
    SELECT d.deployment_id, d.doc_id, d.current_version_id,
           public.document_periodised_at(
             d.deployment_id, d.doc_id, 'infinity'
           ) AS periodised
    FROM public.documents AS d
    WHERE d.deployment_id = p_deployment_id
      AND d.doc_id = p_doc_id
      AND d.deleted_at IS NULL
  ),
  intervals AS (
    SELECT i.version_id,
           range_agg(tstzrange(i.effective_from, i.effective_until, '[)')) AS in_force
    FROM public.document_effective_intervals_at(
      p_deployment_id, ARRAY[p_doc_id], 'infinity'
    ) AS i
    GROUP BY i.version_id
  )
  SELECT
    v.deployment_id,
    v.doc_id,
    v.version_id,
    CASE
      WHEN l.periodised THEN coalesce(i.in_force, '{}'::tstzmultirange)
      WHEN v.version_id = l.current_version_id THEN '{(,)}'::tstzmultirange
      ELSE '{}'::tstzmultirange
    END,
    l.periodised,
    v.status = 'ready' AND r.representation_id IS NOT NULL
  FROM lineage AS l
  JOIN public.document_versions AS v
    ON v.deployment_id = l.deployment_id
   AND v.doc_id = l.doc_id
   AND v.deleted_at IS NULL
  LEFT JOIN intervals AS i ON i.version_id = v.version_id
  LEFT JOIN public.document_representations AS r
    ON r.deployment_id = v.deployment_id
   AND r.version_id = v.version_id
   AND r.representation_id = v.current_representation_id
   AND r.status = 'ready';
END
$$;
COMMENT ON FUNCTION refresh_document_version_scope(uuid, uuid) IS
  'D140: rewrite one lineage''s document_version_scope rows from the declaration and mode ledgers and the version rows, as currently believed. Called by the lineage-write triggers; idempotent, so a rebuild equals the maintained projection.';

CREATE FUNCTION document_version_scope_lineage_write()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM public.refresh_document_version_scope(OLD.deployment_id, OLD.doc_id);
    RETURN OLD;
  END IF;
  PERFORM public.refresh_document_version_scope(NEW.deployment_id, NEW.doc_id);
  RETURN NEW;
END
$$;
COMMENT ON FUNCTION document_version_scope_lineage_write() IS
  'D140 trigger: refresh the written row''s lineage in document_version_scope. Used on tables that carry (deployment_id, doc_id).';

CREATE FUNCTION document_version_scope_representation_write()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
  lineage uuid;
BEGIN
  SELECT v.doc_id INTO lineage
  FROM public.document_versions AS v
  WHERE v.deployment_id = NEW.deployment_id AND v.version_id = NEW.version_id;
  IF lineage IS NOT NULL THEN
    PERFORM public.refresh_document_version_scope(NEW.deployment_id, lineage);
  END IF;
  RETURN NEW;
END
$$;
COMMENT ON FUNCTION document_version_scope_representation_write() IS
  'D140 trigger: a representation became (or stopped being) ready; refresh its version''s lineage in document_version_scope.';

CREATE CONSTRAINT TRIGGER tr_effective_periods_scope
  AFTER INSERT OR UPDATE OR DELETE ON document_effective_periods
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION document_version_scope_lineage_write();
CREATE CONSTRAINT TRIGGER tr_effective_time_events_scope
  AFTER INSERT OR DELETE ON document_effective_time_events
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION document_version_scope_lineage_write();
CREATE CONSTRAINT TRIGGER tr_document_versions_scope_insert
  AFTER INSERT ON document_versions
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION document_version_scope_lineage_write();
CREATE CONSTRAINT TRIGGER tr_document_versions_scope_update
  AFTER UPDATE OF status, deleted_at, current_representation_id ON document_versions
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  WHEN (
    OLD.status IS DISTINCT FROM NEW.status
    OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
    OR OLD.current_representation_id IS DISTINCT FROM NEW.current_representation_id
  )
  EXECUTE FUNCTION document_version_scope_lineage_write();
CREATE CONSTRAINT TRIGGER tr_documents_scope_update
  AFTER UPDATE OF current_version_id, deleted_at ON documents
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  WHEN (
    OLD.current_version_id IS DISTINCT FROM NEW.current_version_id
    OR OLD.deleted_at IS DISTINCT FROM NEW.deleted_at
  )
  EXECUTE FUNCTION document_version_scope_lineage_write();
CREATE CONSTRAINT TRIGGER tr_document_representations_scope_insert
  AFTER INSERT ON document_representations
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION document_version_scope_representation_write();
CREATE CONSTRAINT TRIGGER tr_document_representations_scope_update
  AFTER UPDATE OF status ON document_representations
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  WHEN (OLD.status IS DISTINCT FROM NEW.status)
  EXECUTE FUNCTION document_version_scope_representation_write();
"""

# Every existing lineage is undeclared: one statement fills the projection.
_FILL_VERSION_SCOPE = r"""
INSERT INTO document_version_scope (
  deployment_id, doc_id, version_id, in_force, periodised, selectable
)
SELECT
  v.deployment_id,
  v.doc_id,
  v.version_id,
  CASE WHEN v.version_id = d.current_version_id
       THEN '{(,)}'::tstzmultirange ELSE '{}'::tstzmultirange END,
  false,
  v.status = 'ready' AND r.representation_id IS NOT NULL
FROM documents AS d
JOIN document_versions AS v
  ON v.deployment_id = d.deployment_id
 AND v.doc_id = d.doc_id
 AND v.deleted_at IS NULL
LEFT JOIN document_representations AS r
  ON r.deployment_id = v.deployment_id
 AND r.version_id = v.version_id
 AND r.representation_id = v.current_representation_id
 AND r.status = 'ready'
WHERE d.deleted_at IS NULL
"""

_SECTION_CHUNK_DDL = r"""
ALTER TABLE document_sections
  ADD COLUMN section_key text,
  ADD COLUMN own_content_hash text,
  ADD COLUMN subtree_content_hash text;
COMMENT ON COLUMN document_sections.section_key IS
  'D140: stable key from a trailing heading attribute {#key}; NULL when the heading has none (never model-produced). Unique within a structure generation.';
COMMENT ON COLUMN document_sections.own_content_hash IS
  'D140: hash of the section''s own ordered block hashes, children excluded; NULL = pre-D140, awaiting backfill.';
COMMENT ON COLUMN document_sections.subtree_content_hash IS
  'D140: hash of every block hash in the section span, children included ("did this section change"); NULL = pre-D140, awaiting backfill.';
CREATE UNIQUE INDEX ux_sections_key
  ON document_sections (deployment_id, structure_generation_id, section_key) WHERE section_key IS NOT NULL;
CREATE INDEX ix_sections_doc_key
  ON document_sections (deployment_id, doc_id, section_key) WHERE section_key IS NOT NULL;

ALTER TABLE chunks
  ADD COLUMN text_origin_at timestamptz,
  ADD COLUMN reuse_identity_hash text;
COMMENT ON COLUMN chunks.text_origin_at IS
  'D140: recorded once at creation: the text origin time of the earliest eligible matching chunk (same reuse_identity_hash, non-deleted version, dated no later than this version) in the lineage, else this version''s source_modified_at or published_at. The E2 header date, the reuse-key date and fresh claims'' asserted_at. NULL on chunks cut before D140.';
COMMENT ON COLUMN chunks.reuse_identity_hash IS
  'D140: date-free chunk identity (own and neighbour block hashes, non-date header facts, blockizer/structurer/extractor versions) for the text-origin lookup; NULL on chunks cut before D140.';
CREATE INDEX ix_chunks_reuse_identity
  ON chunks (deployment_id, doc_id, reuse_identity_hash) WHERE reuse_identity_hash IS NOT NULL;
"""

_REFERENCE_DDL = r"""
CREATE TYPE crossref_kind AS ENUM ('cites','links_to','attaches','replies_to','refers_to','amends','implements');
CREATE TYPE crossref_binding AS ENUM ('floating','pinned');
CREATE TYPE crossref_origin AS ENUM ('extracted','supplied');

CREATE TABLE document_reference_generations (
  generation_id   uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  doc_id          uuid NOT NULL,               -- the source lineage
  version_id      uuid NOT NULL,               -- the source version whose references this generation holds
  origin          crossref_origin NOT NULL,    -- extracted (E0 extraction rungs) or supplied (caller NDJSON)
  representation_id uuid,                      -- extracted only: the representation the rows' spans index
  crossref_version text,                       -- extracted only: the crossreferencer generation
  input_hash      text NOT NULL,               -- supplied: sha256 of the canonical NDJSON body; extracted: hash(representation_id, crossref_version)
  request_seq     bigint,                      -- supplied only: per-version PUT order; the newest non-superseded, non-rejected generation is the caller's intent
  artifact_uri    text,                        -- supplied only: the content-addressed stored NDJSON body
  item_count      integer,                     -- number of references in the generation
  status          text NOT NULL CHECK (status IN ('pending','active','rejected','superseded')), -- rows are visible only while active
  errors          jsonb,                       -- rejected: bounded [{item, field, reason}]
  created_at      timestamptz NOT NULL DEFAULT now(), -- when the generation was recorded
  activated_at    timestamptz,                 -- when the generation became active
  CHECK ((origin = 'extracted') = (representation_id IS NOT NULL AND crossref_version IS NOT NULL)),
  CHECK ((origin = 'supplied') = (artifact_uri IS NOT NULL)),
  CHECK ((origin = 'supplied') = (request_seq IS NOT NULL)),
  UNIQUE (deployment_id, version_id, generation_id),
  UNIQUE (deployment_id, version_id, origin, request_seq),
  FOREIGN KEY (deployment_id, doc_id, version_id)
    REFERENCES document_versions (deployment_id, doc_id, version_id)
);
COMMENT ON TABLE document_reference_generations IS
  'D140: one production of references for one source version and origin. Rows in document_crossrefs are visible only while their generation is active; at most one active generation per (version, origin), and a new generation replaces the active one atomically. Supplied: each PUT whose body differs from the intent; extracted: one per (version, representation, crossref_version), activated with the D65 representation swap.';
CREATE UNIQUE INDEX ux_reference_generations_active
  ON document_reference_generations (deployment_id, version_id, origin) WHERE status = 'active';

CREATE TABLE document_crossrefs (
  crossref_id     uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  from_doc_id     uuid NOT NULL,               -- the source lineage
  from_version_id uuid NOT NULL,               -- the version that makes the reference
  generation_id   uuid NOT NULL,               -- the generation that wrote the row; visible only while it is active
  from_section_key text,                       -- the source section, when known
  from_representation_id uuid,                 -- extracted rows: the representation the span indexes
  from_char_start integer,                     -- extracted rows: span start in that representation
  from_char_end   integer,                     -- extracted rows: span end in that representation
  kind            crossref_kind NOT NULL,      -- cites | links_to | attaches | replies_to | refers_to | amends | implements
  origin          crossref_origin NOT NULL,    -- extracted | supplied
  source_label    text,                        -- the caller's own type code, opaque, returned verbatim
  to_source_kind  text,                        -- target as named by the source; exposed only by document_references for a live source
  to_source_ref   text,                        -- target as named by the source
  to_version_key  text,                        -- pinned: the target version's immutable version_key
  to_section_key  text,                        -- the target section; NULL = the whole document
  binding         crossref_binding NOT NULL DEFAULT 'floating', -- floating: versions in force at read time; pinned: the named version key
  change_effective_from timestamptz,           -- kind = amends with a known date: when the change takes effect
  change_date_known boolean,                   -- kind = amends: false = the source states no date
  to_doc_id       uuid,                        -- resolved target lineage; NULL = not (yet) matched
  resolved        boolean NOT NULL DEFAULT false, -- whether to_doc_id was matched
  raw_citation    text,                        -- extracted rows: the citation text as found; retained even when resolved
  context         text,                        -- bounded surrounding text
  created_at      timestamptz NOT NULL DEFAULT now(), -- when the row was written
  CHECK (binding = 'floating' OR to_version_key IS NOT NULL),
  CHECK ((kind = 'amends') = (change_date_known IS NOT NULL)),
  CHECK (kind = 'amends' OR change_effective_from IS NULL),
  CHECK (change_date_known IS NOT TRUE OR change_effective_from IS NOT NULL),
  CHECK (change_date_known IS NOT FALSE OR change_effective_from IS NULL),
  CHECK (from_char_start IS NULL OR from_representation_id IS NOT NULL),
  FOREIGN KEY (deployment_id, from_doc_id, from_version_id)
    REFERENCES document_versions (deployment_id, doc_id, version_id),
  FOREIGN KEY (deployment_id, from_version_id, generation_id)
    REFERENCES document_reference_generations (deployment_id, version_id, generation_id),
  FOREIGN KEY (deployment_id, to_doc_id) REFERENCES documents (deployment_id, doc_id)
);
COMMENT ON TABLE document_crossrefs IS
  'References made by one source version (D36/D140), written by one extracted or supplied generation; document- or section-grain, floating or pinned. Projected (deduplicated per lineage pair and kind, active generations only) to graph document_crossref edges; raw_citation and the source-named target are retained so a forgotten/re-ingested target can be re-resolved, and are never exposed through memory_v1.';
CREATE INDEX ix_crossrefs_from     ON document_crossrefs (deployment_id, from_version_id, generation_id, from_section_key, crossref_id);
CREATE INDEX ix_crossrefs_incoming ON document_crossrefs (deployment_id, to_doc_id, to_section_key, from_doc_id, from_version_id, crossref_id) WHERE to_doc_id IS NOT NULL;
CREATE INDEX ix_crossrefs_pending  ON document_crossrefs (deployment_id, to_source_kind, to_source_ref) WHERE to_doc_id IS NULL;
"""

GRAPH_CROSSREFS_SOURCE_DDL = r"""
CREATE VIEW rememberstack_graph_internal.crossrefs_live AS
SELECT DISTINCT ON (x.deployment_id, x.from_doc_id, x.to_doc_id, x.kind)
       x.deployment_id, x.crossref_id, x.from_doc_id, x.to_doc_id,
       x.kind::text AS kind, x.context, x.created_at
FROM document_crossrefs AS x
JOIN document_reference_generations AS generation
  ON generation.deployment_id = x.deployment_id
 AND generation.version_id = x.from_version_id
 AND generation.generation_id = x.generation_id
 AND generation.status = 'active'
JOIN document_version_scope AS scope
  ON scope.deployment_id = x.deployment_id
 AND scope.version_id = x.from_version_id
 AND scope.doc_id = x.from_doc_id
 AND scope.selectable
 AND scope.in_force @> statement_timestamp()
JOIN documents AS target
  ON target.deployment_id = x.deployment_id
 AND target.doc_id = x.to_doc_id
 AND target.deleted_at IS NULL
WHERE x.resolved AND x.to_doc_id IS NOT NULL
ORDER BY x.deployment_id, x.from_doc_id, x.to_doc_id, x.kind, x.crossref_id;
"""
"""One lineage-to-lineage edge per (from_doc_id, to_doc_id, kind) among the
resolved references of the versions in force now (the ``current`` scope of
``versions_in_scope``: the in-force version of a periodised lineage, the
served version otherwise); ``crossref_id`` is the smallest contributing row.
The projection holds only live lineages, so the source lineage needs no
separate liveness join."""

MEMORY_V1_D140_VIEW_DDL = r"""
CREATE OR REPLACE VIEW memory_v1.sections_live (
  deployment_id,
  section_id,
  doc_id,
  version_id,
  representation_id,
  structure_generation_id,
  parent_section_id,
  node_path,
  heading_level,
  title,
  normalized_title,
  role,
  ordinal,
  block_start,
  block_end,
  char_start,
  char_end,
  page_start,
  page_end,
  summary,
  section_key,             -- Stable source-chosen key of the section from a trailing heading attribute, unique within the structure generation, null when the heading declares none.
  own_content_hash,        -- Hash of the section's own ordered block hashes with its children excluded, null until the section has been indexed.
  subtree_content_hash     -- Hash of every block hash in the section span including its children, null until the section has been indexed.
) AS
SELECT
  s.deployment_id,
  s.section_id,
  s.doc_id,
  s.version_id,
  s.representation_id,
  s.structure_generation_id,
  parent.section_id,
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
  s.summary,
  s.section_key,
  s.own_content_hash,
  s.subtree_content_hash
FROM document_sections AS s
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = s.deployment_id
 AND dl.doc_id = s.doc_id
 AND dl.current_version_id = s.version_id
 AND dl.current_representation_id = s.representation_id
 AND dl.has_current_ready_content
JOIN document_representations AS r
  ON r.deployment_id = s.deployment_id
 AND r.version_id = s.version_id
 AND r.representation_id = s.representation_id
 AND r.current_structure_generation_id = s.structure_generation_id
LEFT JOIN document_sections AS parent
  ON parent.deployment_id = s.deployment_id
 AND parent.section_id = s.parent_section_id
 AND parent.doc_id = s.doc_id
 AND parent.version_id = s.version_id
 AND parent.representation_id = s.representation_id
 AND parent.structure_generation_id = s.structure_generation_id;

CREATE OR REPLACE VIEW memory_v1.chunks_live (
  deployment_id,
  chunk_id,
  doc_id,
  version_id,
  representation_id,
  section_id,
  ordinal,
  block_start,
  block_end,
  char_start,
  char_end,
  token_count,
  chunk_content_hash,
  extraction_input_hash,
  embedding_text_hash,
  location_facts,
  location_header,
  embedding_input_policy_version,
  policy_generation,
  embedder_generation,
  chunker_version,
  prefixer_version,
  created_at,
  text_origin_at                   -- When the words of the chunk were first written in this lineage as far as the engine knows, which dates its fresh claims; null on chunks cut before the time was recorded or when the version has no date.
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
  c.created_at,
  c.text_origin_at
FROM chunks AS c
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = c.deployment_id
 AND dl.doc_id = c.doc_id
 AND dl.current_version_id = c.version_id
 AND dl.current_representation_id = c.representation_id
 AND dl.has_current_ready_content
LEFT JOIN memory_v1.sections_live AS sec
  ON sec.deployment_id = c.deployment_id
 AND sec.section_id = c.section_id
 AND sec.doc_id = c.doc_id
 AND sec.version_id = c.version_id
 AND sec.representation_id = c.representation_id;

CREATE VIEW memory_v1.chunks_all_versions_live (
  deployment_id,                   -- The deployment that owns the chunk.
  chunk_id,                        -- Stable identity of this retrieval unit within its version's current reading.
  doc_id,                          -- The live lineage the chunk belongs to.
  version_id,                      -- The non-deleted ready version the chunk was cut from, which need not be the lineage's served version.
  representation_id,               -- The version's current ready reading whose block grid and offsets the chunk uses.
  section_id,                      -- The section containing the chunk, null when the chunk has no section in the reading's current structure generation.
  ordinal,                         -- Position of the chunk within its version.
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
  created_at,                      -- When the chunk row was written, which is a processing instant rather than a world-time clock.
  text_origin_at                   -- When the words of the chunk were first written in this lineage as far as the engine knows, which dates its fresh claims; null on chunks cut before the time was recorded or when the version has no date.
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
  c.created_at,
  c.text_origin_at
FROM chunks AS c
JOIN memory_v1.document_versions_visible AS v
  ON v.deployment_id = c.deployment_id
 AND v.version_id = c.version_id
 AND v.doc_id = c.doc_id
 AND v.status = 'ready'
 AND v.current_representation_id = c.representation_id
JOIN document_representations AS r
  ON r.deployment_id = c.deployment_id
 AND r.version_id = c.version_id
 AND r.representation_id = c.representation_id
 AND r.status = 'ready'
LEFT JOIN document_sections AS sec
  ON sec.deployment_id = c.deployment_id
 AND sec.section_id = c.section_id
 AND sec.doc_id = c.doc_id
 AND sec.version_id = c.version_id
 AND sec.representation_id = c.representation_id
 AND sec.structure_generation_id = r.current_structure_generation_id;
COMMENT ON VIEW memory_v1.chunks_all_versions_live IS
  'One row per chunk coordinate in the current ready representation of any non-deleted ready version of a live lineage, keyed by (deployment_id, chunk_id) and joined to document_versions_visible on (deployment_id, version_id). It is the relation time-scoped reads select from through versions_in_scope, so it includes versions that are no longer, or not yet, the lineage''s served version; chunks_live keeps meaning served-version chunks. Like chunks_live it is metadata only and carries no authoritative body column. Chunks of deleted versions, of non-ready readings and of forgotten lineages are absent, and a section_id is exposed only when that section belongs to the reading''s current structure generation. The view carries no counts and no validity clocks.';

CREATE VIEW memory_v1.document_effective_periods_live (
  deployment_id,       -- The deployment that owns the declaration.
  period_id,           -- Stable identity of this declared effective period.
  doc_id,              -- The live lineage whose version the period belongs to.
  version_id,          -- The non-deleted version declared in force, of any processing status.
  effective_from,      -- Inclusive start of the period, as declared by the caller.
  effective_until,     -- Exclusive end of the period: the declared end, else the next declared start in the lineage, null when the period is open.
  until_declared,      -- True when effective_until was declared rather than derived from the next declared start.
  declared_at          -- When the declaration became known, which is a belief-axis instant rather than a world-time clock.
) AS
SELECT
  p.deployment_id,
  p.period_id,
  p.doc_id,
  p.version_id,
  p.effective_from,
  coalesce(
    p.effective_until,
    lead(p.effective_from) OVER (
      PARTITION BY p.deployment_id, p.doc_id ORDER BY p.effective_from
    )
  ),
  p.effective_until IS NOT NULL,
  p.declared_at
FROM document_effective_periods AS p
JOIN memory_v1.document_versions_visible AS v
  ON v.deployment_id = p.deployment_id
 AND v.version_id = p.version_id
 AND v.doc_id = p.doc_id
WHERE p.retracted_at IS NULL;
COMMENT ON VIEW memory_v1.document_effective_periods_live IS
  'One row per currently known declared effective period, keyed by (deployment_id, period_id) and joined to document_versions_visible on (deployment_id, version_id). A period says when the publisher holds the version''s text to be in force; it is declared by a caller and never inferred. The end is derived from the next declared start of a non-deleted version of the lineage when no end was declared, so declaring a new edition shortens its predecessor without a write to it. Retracted declarations, declarations of deleted versions and every declaration of a forgotten lineage are absent. The view carries no counts; fact validity windows are a separate clock.';

CREATE VIEW memory_v1.document_crossrefs_live (
  deployment_id,           -- The deployment that owns the reference.
  crossref_id,             -- Stable identity of this reference made by one source version.
  from_doc_id,             -- The live lineage whose version makes the reference.
  to_doc_id,               -- The live lineage the reference resolves to.
  kind,                    -- What the reference does, such as cites, links_to, refers_to, amends, or implements.
  context,                 -- Bounded surrounding text of the reference, truncated to 500 characters.
  created_at,              -- When the reference row was written, which is a processing instant rather than a world-time clock.
  from_version_id,         -- The non-deleted version that makes the reference.
  from_section_key,        -- The section key of the source section, null when the reference is not attributed to a keyed section.
  to_section_key,          -- The section key the reference names in the target, null when it names the whole document.
  binding,                 -- Either floating, meaning whichever target versions are in force when read, or pinned, meaning one named target version.
  origin,                  -- Either extracted by the engine or supplied by a caller.
  source_label,            -- The caller's own opaque type code for the reference, returned verbatim and null when none was supplied.
  change_effective_from,   -- For an amends reference with a known date, when the change takes effect; null otherwise.
  change_date_known        -- For an amends reference, whether the source states when the change takes effect; null for every other kind.
) AS
SELECT
  x.deployment_id,
  x.crossref_id,
  source.doc_id,
  target.doc_id,
  x.kind::text,
  left(x.context, 500),
  x.created_at,
  x.from_version_id,
  x.from_section_key,
  x.to_section_key,
  x.binding::text,
  x.origin::text,
  x.source_label,
  x.change_effective_from,
  x.change_date_known
FROM document_crossrefs AS x
JOIN document_reference_generations AS generation
  ON generation.deployment_id = x.deployment_id
 AND generation.version_id = x.from_version_id
 AND generation.generation_id = x.generation_id
 AND generation.status = 'active'
JOIN memory_v1.document_versions_visible AS source
  ON source.deployment_id = x.deployment_id
 AND source.version_id = x.from_version_id
 AND source.doc_id = x.from_doc_id
JOIN memory_v1.documents_live AS target
  ON target.deployment_id = x.deployment_id
 AND target.doc_id = x.to_doc_id
WHERE x.resolved AND x.to_doc_id IS NOT NULL;
COMMENT ON VIEW memory_v1.document_crossrefs_live IS
  'One row per resolved reference made by a live source version to a live target lineage, keyed by (deployment_id, crossref_id) and joined to document_versions_visible on (deployment_id, from_version_id) and to documents_live on (deployment_id, to_doc_id). Only references of the active generation of their source version and origin appear, so a replaced reference set never shows through. An unresolved reference, a reference whose target was never ingested, and one whose source version or either lineage has been deleted or forgotten are absent rather than half-resolved, so this relation never reveals that a document once existed. The raw citation text and the target as named by the source are deliberately not exposed, because they are retained even after a target is forgotten; the bounded context is truncated to 500 characters. The creation clock is a processing instant, and the view carries no counts and asserts no facts.';
"""
"""The latest authored D140 ``memory_v1`` view definitions, also read by the
offline manifest builder. ``sections_live`` and ``chunks_live`` only gain
trailing columns, so they are replaced in place; their existing columns keep
the documentation the original migration authored."""

_NULL_TRAILING_CONTENT_VIEW_DDL = (
    MEMORY_V1_D140_VIEW_DDL[
        : MEMORY_V1_D140_VIEW_DDL.index(
            "CREATE VIEW memory_v1.chunks_all_versions_live"
        )
    ]
    .replace(
        "  s.section_key,\n  s.own_content_hash,\n  s.subtree_content_hash\n",
        "  NULL::text,\n  NULL::text,\n  NULL::text\n",
    )
    .replace(
        "  c.created_at,\n  c.text_origin_at\n",
        "  c.created_at,\n  NULL::timestamptz\n",
    )
)
"""The downgrade's ``sections_live`` and ``chunks_live``: the same columns, the
D140 ones as typed NULLs, so the table columns they read can be dropped."""

MEMORY_V1_D140_FUNCTION_DDL = r"""
CREATE FUNCTION memory_v1.effective_intervals(
  deployment_id uuid,
  doc_ids uuid[],
  believed_at timestamptz DEFAULT now()
)
RETURNS TABLE (
  doc_id uuid,
  version_id uuid,
  period_id uuid,
  effective_from timestamptz,
  effective_until timestamptz,
  until_declared boolean
)
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT i.doc_id, i.version_id, i.period_id, i.effective_from,
         i.effective_until, i.until_declared
  FROM public.document_effective_intervals_at(
    effective_intervals.deployment_id,
    effective_intervals.doc_ids,
    coalesce(effective_intervals.believed_at, now())
  ) AS i
  ORDER BY i.doc_id, i.effective_from, i.period_id
$$;

CREATE FUNCTION memory_v1.versions_in_scope(
  deployment_id uuid,
  mode text,
  at timestamptz DEFAULT NULL,
  range_start timestamptz DEFAULT NULL,
  range_end timestamptz DEFAULT NULL,
  evaluated_at timestamptz DEFAULT now(),
  believed_at timestamptz DEFAULT NULL,
  doc_ids uuid[] DEFAULT NULL
)
RETURNS TABLE (
  doc_id uuid,
  version_id uuid,
  representation_id uuid,
  effective_from timestamptz,
  effective_until timestamptz
)
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  p_deployment uuid := versions_in_scope.deployment_id;
  p_evaluated timestamptz := coalesce(versions_in_scope.evaluated_at, now());
  p_at timestamptz := versions_in_scope.at;
  p_believed timestamptz := versions_in_scope.believed_at;
  p_doc_ids uuid[] := versions_in_scope.doc_ids;
  p_window tstzrange;
BEGIN
  IF versions_in_scope.mode = 'current' THEN
    p_window := tstzrange(p_evaluated, p_evaluated, '[]');
  ELSIF versions_in_scope.mode = 'at' THEN
    IF p_at IS NULL THEN
      RAISE EXCEPTION 'versions_in_scope mode at requires at'
        USING ERRCODE = 'invalid_parameter_value';
    END IF;
    p_window := tstzrange(p_at, p_at, '[]');
  ELSIF versions_in_scope.mode = 'overlap' THEN
    IF versions_in_scope.range_start IS NULL OR versions_in_scope.range_end IS NULL THEN
      RAISE EXCEPTION 'versions_in_scope mode overlap requires range_start and range_end'
        USING ERRCODE = 'invalid_parameter_value';
    END IF;
    IF versions_in_scope.range_end < versions_in_scope.range_start THEN
      RAISE EXCEPTION 'versions_in_scope range_end precedes range_start'
        USING ERRCODE = 'invalid_parameter_value';
    END IF;
    p_window := tstzrange(versions_in_scope.range_start, versions_in_scope.range_end, '[]');
  ELSIF versions_in_scope.mode = 'history' THEN
    -- every interval that started at or before the evaluation instant
    p_window := tstzrange(NULL, p_evaluated, '[]');
  ELSE
    RAISE EXCEPTION 'versions_in_scope mode must be current, at, overlap or history'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  IF p_believed IS NULL THEN
    RETURN QUERY
    SELECT s.doc_id, s.version_id, v.current_representation_id,
           lower(interval_range), upper(interval_range)
    FROM public.document_version_scope AS s
    JOIN public.document_versions AS v
      ON v.deployment_id = s.deployment_id
     AND v.version_id = s.version_id
    CROSS JOIN LATERAL unnest(s.in_force) AS interval_range
    WHERE s.deployment_id = p_deployment
      AND s.selectable
      AND s.in_force && p_window
      AND interval_range && p_window
      AND (p_doc_ids IS NULL OR s.doc_id = ANY(p_doc_ids))
    ORDER BY s.doc_id, lower(interval_range) NULLS FIRST, s.version_id;
    RETURN;
  END IF;

  IF p_doc_ids IS NULL THEN
    RAISE EXCEPTION 'versions_in_scope with believed_at requires doc_ids'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  RETURN QUERY
  WITH lineage AS (
    SELECT d.doc_id, d.current_version_id,
           public.document_periodised_at(d.deployment_id, d.doc_id, p_believed) AS periodised
    FROM public.documents AS d
    WHERE d.deployment_id = p_deployment
      AND d.doc_id = ANY(p_doc_ids)
      AND d.deleted_at IS NULL
  ),
  candidate AS (
    SELECT i.doc_id, i.version_id,
           tstzrange(i.effective_from, i.effective_until, '[)') AS interval_range
    FROM public.document_effective_intervals_at(p_deployment, p_doc_ids, p_believed) AS i
    JOIN lineage AS l ON l.doc_id = i.doc_id AND l.periodised
    UNION ALL
    SELECT l.doc_id, l.current_version_id, '(,)'::tstzrange
    FROM lineage AS l
    WHERE NOT l.periodised AND l.current_version_id IS NOT NULL
  )
  SELECT c.doc_id, c.version_id, v.current_representation_id,
         lower(c.interval_range), upper(c.interval_range)
  FROM candidate AS c
  JOIN public.document_version_scope AS s
    ON s.deployment_id = p_deployment
   AND s.version_id = c.version_id
   AND s.selectable
  JOIN public.document_versions AS v
    ON v.deployment_id = p_deployment
   AND v.version_id = c.version_id
  WHERE c.interval_range && p_window
  ORDER BY c.doc_id, lower(c.interval_range) NULLS FIRST, c.version_id;
END
$$;

CREATE FUNCTION memory_v1.fact_in_scope_support(
  deployment_id uuid,
  fact_kind text,
  fact_id uuid,
  mode text,
  at timestamptz DEFAULT NULL,
  range_start timestamptz DEFAULT NULL,
  range_end timestamptz DEFAULT NULL,
  evaluated_at timestamptz DEFAULT now(),
  believed_at timestamptz DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
STABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
#variable_conflict use_column
DECLARE
  p_deployment uuid := fact_in_scope_support.deployment_id;
  p_evaluated timestamptz := coalesce(fact_in_scope_support.evaluated_at, now());
  p_believed timestamptz := coalesce(fact_in_scope_support.believed_at, p_evaluated);
  occurrence_docs uuid[];
  occurrence_versions uuid[];
  has_support boolean;
BEGIN
  IF fact_in_scope_support.fact_kind NOT IN ('relation', 'observation') THEN
    RAISE EXCEPTION 'fact_in_scope_support fact_kind must be relation or observation'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  has_support := EXISTS (
    SELECT 1 FROM public.relation_evidence AS e
    WHERE fact_in_scope_support.fact_kind = 'relation'
      AND e.deployment_id = p_deployment
      AND e.relation_id = fact_in_scope_support.fact_id
      AND e.stance = 'supports'
    UNION ALL
    SELECT 1 FROM public.observation_evidence AS e
    WHERE fact_in_scope_support.fact_kind = 'observation'
      AND e.deployment_id = p_deployment
      AND e.observation_id = fact_in_scope_support.fact_id
      AND e.stance = 'supports'
  );

  -- the non-deleted versions of live lineages whose current reading (D65)
  -- carries a supporting claim; an occurrence left in a replaced
  -- representation does not count
  SELECT array_agg(DISTINCT c.doc_id), array_agg(DISTINCT c.version_id)
  INTO occurrence_docs, occurrence_versions
  FROM (
    -- supporting evidence; a fact with none at all (D54 zero-support,
    -- contradiction-only) is judged by its evidence of either stance
    SELECT e.claim_id FROM public.relation_evidence AS e
    WHERE fact_in_scope_support.fact_kind = 'relation'
      AND e.deployment_id = p_deployment
      AND e.relation_id = fact_in_scope_support.fact_id
      AND (e.stance = 'supports' OR NOT has_support)
    UNION
    SELECT e.claim_id FROM public.observation_evidence AS e
    WHERE fact_in_scope_support.fact_kind = 'observation'
      AND e.deployment_id = p_deployment
      AND e.observation_id = fact_in_scope_support.fact_id
      AND (e.stance = 'supports' OR NOT has_support)
  ) AS support
  JOIN public.chunk_claims AS cc
    ON cc.deployment_id = p_deployment
   AND cc.claim_id = support.claim_id
  JOIN public.chunks AS c
    ON c.deployment_id = p_deployment
   AND c.chunk_id = cc.chunk_id
  JOIN public.document_versions AS v
    ON v.deployment_id = p_deployment
   AND v.version_id = c.version_id
   AND v.doc_id = c.doc_id
   AND v.deleted_at IS NULL
   AND v.current_representation_id = c.representation_id
  JOIN public.documents AS d
    ON d.deployment_id = p_deployment
   AND d.doc_id = c.doc_id
   AND d.deleted_at IS NULL;

  IF occurrence_docs IS NULL THEN
    RETURN false;
  END IF;

  -- evidence of a lineage without declared periods is not time-restricted
  IF EXISTS (
    SELECT 1 FROM unnest(occurrence_docs) AS lineage(doc_id)
    WHERE NOT public.document_periodised_at(p_deployment, lineage.doc_id, p_believed)
  ) THEN
    RETURN true;
  END IF;

  RETURN EXISTS (
    SELECT 1
    FROM memory_v1.versions_in_scope(
      p_deployment,
      fact_in_scope_support.mode,
      fact_in_scope_support.at,
      fact_in_scope_support.range_start,
      fact_in_scope_support.range_end,
      p_evaluated,
      p_believed,
      occurrence_docs
    ) AS selected
    WHERE selected.version_id = ANY(occurrence_versions)
  );
END
$$;
"""
"""The three D140 public time-scope functions. They are ``SECURITY DEFINER``
owned by the view owner because the ledgers and the projection they read are
private tables the query role cannot see."""

_CARRIED_CLAIMS_DDL = r"""
CREATE VIEW v_memory_claim_carried_periodised (
  deployment_id,
  claim_id,
  doc_id,
  source_kind,
  source_handle,
  asserted_at,
  claim_valid_from,
  claim_valid_until,
  claim_valid_precision,
  claim_valid_kind
) AS
SELECT
  c.deployment_id,
  c.claim_id,
  c.doc_id,
  dl.source_kind,
  dl.source_kind || ':' || coalesce(dl.source_ref, dl.doc_id::text),
  c.asserted_at,
  c.claim_valid_from,
  c.claim_valid_until,
  c.claim_valid_precision::text,
  c.claim_valid_kind::text
FROM claims AS c
JOIN memory_v1.documents_live AS dl
  ON dl.deployment_id = c.deployment_id
 AND dl.doc_id = c.doc_id
WHERE c.is_current_testimony
  AND EXISTS (
    SELECT 1 FROM document_version_scope AS mode_row
    WHERE mode_row.deployment_id = c.deployment_id
      AND mode_row.doc_id = c.doc_id
      AND mode_row.periodised
  )
  AND NOT EXISTS (
    SELECT 1 FROM memory_v1.claims_visible_history AS origin
    WHERE origin.deployment_id = c.deployment_id
      AND origin.claim_id = c.claim_id
  )
  AND EXISTS (
    SELECT 1
    FROM chunk_claims AS occurrence
    JOIN chunks AS ch
      ON ch.deployment_id = occurrence.deployment_id
     AND ch.chunk_id = occurrence.chunk_id
     AND ch.doc_id = c.doc_id
    JOIN document_versions AS v
      ON v.deployment_id = ch.deployment_id
     AND v.version_id = ch.version_id
     AND v.current_representation_id = ch.representation_id
     AND v.deleted_at IS NULL
    JOIN document_representations AS representation
      ON representation.deployment_id = ch.deployment_id
     AND representation.representation_id = ch.representation_id
     AND representation.status = 'ready'
    WHERE occurrence.deployment_id = c.deployment_id
      AND occurrence.claim_id = c.claim_id
  );
COMMENT ON VIEW v_memory_claim_carried_periodised IS
  'D140 §3.4: current-testimony claims of periodised lineages whose origin version is no longer visible but which a non-deleted version still carries in its current reading (a D56 reuse occurrence). D135 keeps such a claim current; this view lets the fact authority views count and show it through that occurrence. Claims whose origin is visible are absent (claims_visible_history already has them). Not part of memory_v1 and never granted to a query role.';
"""
"""D140 §3.4/§8.1: evidence of a periodised lineage survives deletion of the
claim's origin version while another version carries the claim. The two
private fact-authority views union this view beside the origin-based claim
relation; every public fact relation and D54 count inherits it."""

_LIVE_CLAIM_SOURCE = (
    "(SELECT deployment_id, claim_id, doc_id, source_kind, source_handle,"
    " asserted_at, claim_valid_from, claim_valid_until, claim_valid_precision,"
    " claim_valid_kind FROM memory_v1.claims_live"
    " UNION ALL"
    " SELECT deployment_id, claim_id, doc_id, source_kind, source_handle,"
    " asserted_at, claim_valid_from, claim_valid_until, claim_valid_precision,"
    " claim_valid_kind FROM v_memory_claim_carried_periodised)"
)
_VISIBLE_CLAIM_SOURCE = (
    "(SELECT deployment_id, claim_id, doc_id FROM memory_v1.claims_visible_history"
    " UNION ALL"
    " SELECT deployment_id, claim_id, doc_id FROM v_memory_claim_carried_periodised)"
)


def _authored_view(*, ddl: str, name: str) -> str:
    """The authored ``CREATE VIEW name`` statement, as CREATE OR REPLACE."""
    from rememberstack.spine.migrations._helpers import _split_sql

    for statement in _split_sql(sql=ddl):
        if statement.startswith(f"CREATE VIEW {name} ("):
            return statement.replace("CREATE VIEW ", "CREATE OR REPLACE VIEW ", 1)
    raise RuntimeError(f"authored definition of {name} not found")


def _fact_authority_views() -> tuple[tuple[str, str], ...]:
    """(prior, D140) definitions of the two views whose claim source widens."""
    from rememberstack.spine.migrations.versions.p9_04_0025_coordinate_binding import (
        MEMORY_V1_CORRECTION_DDL,
    )
    from rememberstack.spine.migrations.versions.p9_09_0030_fact_authority_performance import (
        FACT_AUTHORITY_DDL,
    )

    claim_live = _authored_view(ddl=FACT_AUTHORITY_DDL, name="v_memory_fact_claim_live")
    fact_visible = _authored_view(
        ddl=MEMORY_V1_CORRECTION_DDL, name="v_memory_fact_visible"
    )
    pairs = []
    for prior, old, new in (
        (
            claim_live,
            "JOIN memory_v1.claims_live AS claim",
            f"JOIN {_LIVE_CLAIM_SOURCE} AS claim",
        ),
        (
            fact_visible,
            "JOIN memory_v1.claims_visible_history AS claim",
            f"JOIN {_VISIBLE_CLAIM_SOURCE} AS claim",
        ),
    ):
        if prior.count(old) != 2:
            raise RuntimeError(f"unexpected authored view shape: {old!r}")
        pairs.append((prior, prior.replace(old, new)))
    return tuple(pairs)


GRAPH_GATE_DDL = r"""
CREATE FUNCTION rememberstack_graph_internal.relation_evidence_in_scope(
  deployment_id uuid,
  relation_id uuid,
  valid_at timestamptz,
  believed_at timestamptz,
  evaluated_at timestamptz
)
RETURNS boolean
LANGUAGE sql
STABLE
PARALLEL SAFE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
  SELECT CASE
    -- a deployment that never declared a period has nothing to time-restrict:
    -- its traversal keeps the graph view's surviving-provenance rule
    WHEN NOT EXISTS (
      SELECT 1 FROM public.document_effective_periods AS p WHERE p.deployment_id = $1
    ) THEN true
    WHEN $4 IS NOT NULL AND $4 < $5 AND (
      EXISTS (
        SELECT 1 FROM public.document_effective_periods AS p
        WHERE p.deployment_id = $1 AND p.declared_at > $4
      )
      OR EXISTS (
        SELECT 1 FROM public.document_effective_periods AS p
        WHERE p.deployment_id = $1 AND p.retracted_at > $4
      )
      OR EXISTS (
        SELECT 1 FROM public.document_effective_time_events AS m
        WHERE m.deployment_id = $1 AND m.event_at > $4
      )
    ) THEN
      -- a past belief instant the ledgers have moved on from: read them, as
      -- the query space does; otherwise the current projection is exactly
      -- the belief at that instant and answers by primary keys
      memory_v1.fact_in_scope_support(
        $1, 'relation', $2, 'at', coalesce($3, $5), NULL, NULL, $5, $4
      )
    ELSE (
      EXISTS (
        SELECT 1
        FROM public.relation_evidence AS e
        JOIN public.chunk_claims AS cc
          ON cc.deployment_id = e.deployment_id AND cc.claim_id = e.claim_id
        JOIN public.chunks AS ch
          ON ch.deployment_id = cc.deployment_id AND ch.chunk_id = cc.chunk_id
        JOIN public.document_versions AS v
          ON v.deployment_id = ch.deployment_id
         AND v.version_id = ch.version_id
         AND v.current_representation_id = ch.representation_id
        JOIN public.document_version_scope AS s
          ON s.deployment_id = ch.deployment_id AND s.version_id = ch.version_id
        WHERE e.deployment_id = $1
          AND e.relation_id = $2
          AND e.stance = 'supports'
          AND (NOT s.periodised
               OR (s.selectable
                   AND s.in_force && tstzrange(coalesce($3, $5), coalesce($3, $5), '[]')))
      )
      OR (
        NOT EXISTS (
          SELECT 1 FROM public.relation_evidence AS e
          WHERE e.deployment_id = $1 AND e.relation_id = $2 AND e.stance = 'supports'
        )
        AND EXISTS (
          SELECT 1
          FROM public.relation_evidence AS e
          JOIN public.chunk_claims AS cc
            ON cc.deployment_id = e.deployment_id AND cc.claim_id = e.claim_id
          JOIN public.chunks AS ch
            ON ch.deployment_id = cc.deployment_id AND ch.chunk_id = cc.chunk_id
          JOIN public.document_versions AS v
            ON v.deployment_id = ch.deployment_id
           AND v.version_id = ch.version_id
           AND v.current_representation_id = ch.representation_id
          JOIN public.document_version_scope AS s
            ON s.deployment_id = ch.deployment_id AND s.version_id = ch.version_id
          WHERE e.deployment_id = $1
            AND e.relation_id = $2
            AND (NOT s.periodised
                 OR (s.selectable
                     AND s.in_force && tstzrange(coalesce($3, $5), coalesce($3, $5), '[]')))
        )
      )
    )
  END
$$;
COMMENT ON FUNCTION rememberstack_graph_internal.relation_evidence_in_scope(
  uuid, uuid, timestamptz, timestamptz, timestamptz
) IS
  'D140 §8.1 evidence gate for one relation edge, at the valid instant: true when a supporting occurrence (or, for a relation with no supporting evidence at all, an occurrence of either stance) lies in a selected version or a live undeclared version, in that version''s current reading. A belief instant that some later declaration, retraction or mode event has moved past reads the declaration ledgers (memory_v1.fact_in_scope_support); otherwise the current projection answers. SECURITY DEFINER over private evidence; EXECUTE for the graph and query roles, whose traversal helpers call it on every candidate edge before expansion and limits.';
"""
"""The graph traversal's evidence gate (§7/§8.1). The graph role cannot read
evidence, so this one narrow, deployment-bound predicate is the only private
read it is granted; the traversal functions and the one-hop PGQ statement
call it on every candidate edge, inside the traversal's own snapshot."""

_GRAPH_LEVEL_SELECT = (
    "    FOR edge_record IN\n"
    "      SELECT path.current_path, path.path_ordinal, head.head_id, edge.*\n"
)
_GRAPH_LEVEL_LIMIT = (
    "      ORDER BY path.path_ordinal, edge.relation_id\n"
    "      LIMIT greatest(expansion_cap - examined + 1, 1)\n"
)


_GATE_SIGNATURE = (
    "rememberstack_graph_internal.relation_evidence_in_scope("
    "uuid, uuid, timestamptz, timestamptz, timestamptz)"
)

GRAPH_GATE_GRANTS = f"""
REVOKE ALL ON FUNCTION {_GATE_SIGNATURE} FROM PUBLIC;
ALTER FUNCTION {_GATE_SIGNATURE} OWNER TO {_VIEW_OWNER};
DO $do$
BEGIN
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION {_GATE_SIGNATURE} TO %I, %I',
    'rememberstack_graph_' || current_database(),
    'rememberstack_query_' || current_database()
  );
END
$do$;
"""
"""The gate runs inside the public traversal helpers, which both the graph role
and the query role execute (SECURITY INVOKER), so both need EXECUTE on it. It
answers one boolean per (deployment, relation) and reveals nothing the query
role cannot already read through ``memory_v1.fact_in_scope_support``."""


def gated_graph_helper(*, sql: str, function: str) -> str:
    """One traversal helper with the §8.1 gate applied inside each BFS level.

    Each level's adjacency statement keeps its order and becomes a subquery
    (``OFFSET 0`` keeps the planner from pushing the gate below the sort); the
    gate filters the ordered candidates and the level's expansion limit counts
    only the edges that pass. The gate is therefore applied before expansion
    and the limit, but evaluated lazily: only until the level's budget is
    filled, never across a whole dense hub up front.
    """
    if sql.count(_GRAPH_LEVEL_SELECT) != 1 or sql.count(_GRAPH_LEVEL_LIMIT) != 1:
        raise RuntimeError(f"unexpected {function} shape")
    return sql.replace(
        _GRAPH_LEVEL_SELECT,
        "    FOR edge_record IN\n"
        "      SELECT ordered.* FROM (\n"
        "      SELECT path.current_path, path.path_ordinal, head.head_id, edge.*\n",
    ).replace(
        _GRAPH_LEVEL_LIMIT,
        "      ORDER BY path.path_ordinal, edge.relation_id\n"
        "      OFFSET 0\n"
        "      ) AS ordered\n"
        "      WHERE rememberstack_graph_internal.relation_evidence_in_scope(\n"
        f"        {function}.deployment_id, ordered.relation_id, clock_valid,\n"
        "        clock_believed, statement_timestamp())\n"
        "      ORDER BY ordered.path_ordinal, ordered.relation_id\n"
        "      LIMIT greatest(expansion_cap - examined + 1, 1)\n",
    )


def _restore_helper_settings() -> None:
    """Reapply p9_19's per-helper planner settings a CREATE OR REPLACE dropped."""
    from rememberstack.spine.migrations.versions.p9_19_0040_graph_tenant_planner_settings import (
        _GRAPH_HELPER_INDEX_SETTINGS,
    )

    apply_ddl(sql=_GRAPH_HELPER_INDEX_SETTINGS)


def _gated_graph_helpers() -> tuple[tuple[str, str], ...]:
    """(prior chosen-window, D140 gated) definitions of the traversal helpers."""
    from rememberstack.spine.fact_graph_contract import chosen_window_helpers

    pairs = []
    for sql, function in zip(
        chosen_window_helpers(), ("graph_neighborhood", "graph_path"), strict=True
    ):
        prior = sql.replace(
            "CREATE FUNCTION memory_v1.", "CREATE OR REPLACE FUNCTION memory_v1.", 1
        )
        pairs.append((prior, gated_graph_helper(sql=prior, function=function)))
    return tuple(pairs)


_FUNCTION_SIGNATURES = (
    "memory_v1.effective_intervals(uuid, uuid[], timestamptz)",
    "memory_v1.versions_in_scope(uuid, text, timestamptz, timestamptz,"
    " timestamptz, timestamptz, timestamptz, uuid[])",
    "memory_v1.fact_in_scope_support(uuid, text, uuid, text, timestamptz,"
    " timestamptz, timestamptz, timestamptz, timestamptz)",
)

_FUNCTION_COMMENTS = {
    _FUNCTION_SIGNATURES[0]: (
        "D140 section 2.2: the in-force interval of every declared effective"
        " period known at believed_at (default now) of the non-deleted versions"
        " of the given live lineages, whatever their processing status. The end"
        " is the declared end, else the next known declared start in the"
        " lineage, else open (until_declared says which). Bounded by the"
        " caller's lineage list."
    ),
    _FUNCTION_SIGNATURES[1]: (
        "D140 section 3.2: the non-deleted ready versions (with a ready current"
        " representation) a time scope selects, one row per selecting in-force"
        " interval. mode is current, at (requires at), overlap (requires"
        " range_start and range_end, both inclusive) or history. A lineage with"
        " declared effective periods contributes the versions in force; every"
        " other live lineage contributes its served version with null bounds."
        " Without believed_at it reads the current-belief projection and may"
        " cover the whole deployment or the given doc_ids; with believed_at,"
        " doc_ids is required and the declaration and mode ledgers are"
        " evaluated as known then."
    ),
    _FUNCTION_SIGNATURES[2]: (
        "D140 section 8.1: the evidence gate. True when at least one claim"
        " supporting the relation or observation occurs in a version the time"
        " scope selects, or in a non-deleted version of a lineage that has no"
        " declared effective periods at the belief instant. believed_at"
        " defaults to evaluated_at. It is a visibility rule on evidence and"
        " never changes counts, windows or currency."
    ),
}

_PRIVATE_FUNCTIONS_READ_BY_OWNER = (
    "document_effective_intervals_at(uuid, uuid[], timestamptz)",
    "document_periodised_at(uuid, uuid, timestamptz)",
)

_OWNER_TABLES = (
    "document_effective_periods",
    "document_effective_time_events",
    "document_version_scope",
    "document_reference_generations",
    "document_crossrefs",
)
"""Tables created here that owner-evaluated ``memory_v1`` objects read. The
view owner's schema-wide SELECT was granted before they existed."""

_NEW_PUBLIC_VIEWS = ("chunks_all_versions_live", "document_effective_periods_live")

QUERY_ROLE_GRANTS = r"""
DO $do$
DECLARE
  query_role text := 'rememberstack_query_' || current_database();
  graph_role text := 'rememberstack_graph_' || current_database();
BEGIN
  EXECUTE format(
    'GRANT SELECT ON memory_v1.chunks_all_versions_live,'
    ' memory_v1.document_effective_periods_live,'
    ' memory_v1.document_crossrefs_live TO %I',
    query_role
  );
  EXECUTE format(
    'GRANT EXECUTE ON FUNCTION'
    ' memory_v1.effective_intervals(uuid, uuid[], timestamptz),'
    ' memory_v1.versions_in_scope(uuid, text, timestamptz, timestamptz,'
    ' timestamptz, timestamptz, timestamptz, uuid[]),'
    ' memory_v1.fact_in_scope_support(uuid, text, uuid, text, timestamptz,'
    ' timestamptz, timestamptz, timestamptz, timestamptz) TO %I',
    query_role
  );
  EXECUTE format(
    'GRANT SELECT ON rememberstack_graph_internal.crossrefs_live TO %I', query_role
  );
  EXECUTE format(
    'GRANT SELECT ON PROPERTY GRAPH memory_v1.memory_current TO %I', query_role
  );
  EXECUTE format(
    'GRANT SELECT ON rememberstack_graph_internal.crossrefs_live TO %I', graph_role
  );
  EXECUTE format(
    'GRANT SELECT ON memory_v1.document_crossrefs_live TO %I', graph_role
  );
  EXECUTE format(
    'GRANT SELECT ON PROPERTY GRAPH memory_v1.memory_current TO %I', graph_role
  );
END
$do$;
"""
"""Grants the recreated and new objects need; dropping an object drops its
grants, so the recreated crossref views and graph are granted again exactly as
p9_17_0038 granted them."""

_D140_DATA_EXISTS = """
SELECT EXISTS (SELECT 1 FROM document_effective_periods)
    OR EXISTS (SELECT 1 FROM document_effective_time_events)
    OR EXISTS (SELECT 1 FROM document_versions WHERE version_key IS NOT NULL)
    OR EXISTS (SELECT 1 FROM document_reference_generations)
    OR EXISTS (
        SELECT 1 FROM document_sections
        WHERE section_key IS NOT NULL
           OR own_content_hash IS NOT NULL
           OR subtree_content_hash IS NOT NULL
    )
    OR EXISTS (
        SELECT 1 FROM chunks
        WHERE text_origin_at IS NOT NULL OR reuse_identity_hash IS NOT NULL
    )
"""
"""Rows only D140 can have produced: a declaration or mode event (live or
retracted), a version key, a reference generation (every version-grain
reference belongs to one), a section key or section hash, or a chunk's text
origin time or reuse identity. Text origin time is recorded once and never
recomputed, so dropping it would silently lose it; the section values are
refused too so that no downgrade discards D140 data without a reviewed plan."""


def upgrade() -> None:
    """Create every D140 object and fill the projection for existing lineages."""
    connection = op.get_bind()
    op.execute("LOCK TABLE document_crossrefs IN ACCESS EXCLUSIVE MODE")
    if connection.execute(
        text("SELECT EXISTS (SELECT 1 FROM document_crossrefs)")
    ).scalar_one():
        raise RuntimeError(
            "D140 does not convert a store whose document_crossrefs holds rows:"
            " a lineage-grain reference names no source version or generation."
            " Delete the rows (no code on the prior revision writes them) and"
            " run the migration again"
        )

    apply_ddl(sql=_EFFECTIVE_TIME_DDL)
    apply_ddl(sql=_SCOPE_FUNCTIONS_DDL)
    op.execute(_FILL_VERSION_SCOPE)
    apply_ddl(sql=_SECTION_CHUNK_DDL)

    op.execute("DROP PROPERTY GRAPH memory_v1.memory_current")
    op.execute("DROP VIEW rememberstack_graph_internal.crossrefs_live")
    op.execute("DROP VIEW memory_v1.document_crossrefs_live")
    op.execute("DROP TABLE document_crossrefs")
    op.execute("DROP TYPE crossref_kind")
    apply_ddl(sql=_REFERENCE_DDL)

    for table in _OWNER_TABLES:
        op.execute(f"GRANT SELECT ON {table} TO {_VIEW_OWNER}")
    for function in _PRIVATE_FUNCTIONS_READ_BY_OWNER:
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO {_VIEW_OWNER}")
    for function in (
        "refresh_document_version_scope(uuid, uuid)",
        "document_version_scope_lineage_write()",
        "document_version_scope_representation_write()",
    ):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")

    apply_ddl(sql=GRAPH_CROSSREFS_SOURCE_DDL)
    apply_view_ddl(sql=MEMORY_V1_D140_VIEW_DDL)
    for view in ("document_crossrefs_live", *_NEW_PUBLIC_VIEWS):
        op.execute(f"ALTER VIEW memory_v1.{view} OWNER TO {_VIEW_OWNER}")
    op.execute(_CURRENT_GRAPH)

    apply_ddl(sql=MEMORY_V1_D140_FUNCTION_DDL)
    for signature in _FUNCTION_SIGNATURES:
        comment = _FUNCTION_COMMENTS[signature].replace("'", "''")
        op.execute(f"COMMENT ON FUNCTION {signature} IS '{comment}'")
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
        op.execute(f"ALTER FUNCTION {signature} OWNER TO {_VIEW_OWNER}")
    op.execute(QUERY_ROLE_GRANTS)

    apply_ddl(sql=_CARRIED_CLAIMS_DDL)
    op.execute("REVOKE ALL ON v_memory_claim_carried_periodised FROM PUBLIC")
    op.execute(
        """
        DO $do$
        BEGIN
          EXECUTE format(
            'REVOKE ALL ON v_memory_claim_carried_periodised FROM %I',
            'rememberstack_query_' || current_database()
          );
        END
        $do$;
        """
    )
    op.execute(f"ALTER VIEW v_memory_claim_carried_periodised OWNER TO {_VIEW_OWNER}")
    for _, widened in _fact_authority_views():
        op.execute(widened)

    apply_ddl(sql=GRAPH_GATE_DDL)
    apply_ddl(sql=GRAPH_GATE_GRANTS)
    for _, gated in _gated_graph_helpers():
        op.execute(gated)
    _restore_helper_settings()


def downgrade() -> None:
    """Restore the prior schema; refuse when any D140 data exists."""
    from rememberstack.spine.migrations.versions.p0_02_0003_entities_evaluation_e0_e1 import (
        _DDL as _LEGACY_E0_DDL,
    )
    from rememberstack.spine.migrations.versions.p9_01_0022_memory_v1_query_space import (
        _SURROUND_DDL,
    )
    from rememberstack.spine.migrations.versions.p9_17_0038_postgres19_live_graph import (
        _ALIGN_PUBLIC_CROSSREF_VIEW,
    )
    from rememberstack.spine.migrations.versions.p9_17_0038_postgres19_live_graph import (
        _GRAPH_SOURCES,
    )

    connection = op.get_bind()
    if connection.execute(text(_D140_DATA_EXISTS)).scalar_one():
        raise RuntimeError(
            "D140 downgrade requires an explicitly reviewed restore/conversion"
            " plan: declared effective periods, effective-time events, version"
            " keys, reference generations, section keys or hashes, or chunk"
            " text-origin data exist and would be lost"
        )

    for prior, _ in _gated_graph_helpers():
        op.execute(prior)
    _restore_helper_settings()
    op.execute(f"DROP FUNCTION {_GATE_SIGNATURE}")
    for prior, _ in _fact_authority_views():
        op.execute(prior)
    op.execute("DROP VIEW v_memory_claim_carried_periodised")

    op.execute("DROP FUNCTION " + _FUNCTION_SIGNATURES[2])
    op.execute("DROP FUNCTION " + _FUNCTION_SIGNATURES[1])
    op.execute("DROP FUNCTION " + _FUNCTION_SIGNATURES[0])

    op.execute("DROP PROPERTY GRAPH memory_v1.memory_current")
    op.execute("DROP VIEW rememberstack_graph_internal.crossrefs_live")
    op.execute("DROP VIEW memory_v1.document_crossrefs_live")
    op.execute("DROP VIEW memory_v1.document_effective_periods_live")
    op.execute("DROP VIEW memory_v1.chunks_all_versions_live")
    op.execute("DROP TABLE document_crossrefs")
    op.execute("DROP TABLE document_reference_generations")
    op.execute("DROP TYPE crossref_origin")
    op.execute("DROP TYPE crossref_binding")
    op.execute("DROP TYPE crossref_kind")
    op.execute(
        "CREATE TYPE crossref_kind AS ENUM ('cites','links_to','attaches','replies_to')"
    )
    apply_ddl(
        sql="\n".join(
            _legacy_statements(
                sql=_LEGACY_E0_DDL, first="CREATE TABLE document_crossrefs ("
            )
        )
    )
    # A view cannot lose columns in place, and the served-content views have
    # many dependents; their D140 trailing columns stay, as typed NULLs, so
    # the section and chunk columns they read can be dropped.
    apply_ddl(sql=_NULL_TRAILING_CONTENT_VIEW_DDL)

    apply_ddl(sql=_ALIGN_PUBLIC_CROSSREF_VIEW)
    for view, column, comment in view_column_comments(sql=_SURROUND_DDL):
        if view == "memory_v1.document_crossrefs_live":
            escaped = comment.replace("'", "''")
            op.execute(f"COMMENT ON COLUMN {view}.{column} IS '{escaped}'")
    op.execute(f"ALTER VIEW memory_v1.document_crossrefs_live OWNER TO {_VIEW_OWNER}")
    for statement in _legacy_statements(
        sql=_GRAPH_SOURCES,
        first="CREATE VIEW rememberstack_graph_internal.crossrefs_live AS",
    ):
        op.execute(statement)
    op.execute(_CURRENT_GRAPH)
    op.execute(
        r"""
        DO $do$
        DECLARE
          query_role text := 'rememberstack_query_' || current_database();
          graph_role text := 'rememberstack_graph_' || current_database();
        BEGIN
          EXECUTE format(
            'GRANT SELECT ON memory_v1.document_crossrefs_live TO %I', query_role
          );
          EXECUTE format(
            'GRANT SELECT ON rememberstack_graph_internal.crossrefs_live TO %I',
            query_role
          );
          EXECUTE format(
            'GRANT SELECT ON PROPERTY GRAPH memory_v1.memory_current TO %I',
            query_role
          );
          EXECUTE format(
            'GRANT SELECT ON rememberstack_graph_internal.crossrefs_live TO %I',
            graph_role
          );
          EXECUTE format(
            'GRANT SELECT ON memory_v1.document_crossrefs_live TO %I', graph_role
          );
          EXECUTE format(
            'GRANT SELECT ON PROPERTY GRAPH memory_v1.memory_current TO %I',
            graph_role
          );
        END
        $do$;
        """
    )
    op.execute(f"GRANT SELECT ON document_crossrefs TO {_VIEW_OWNER}")

    op.execute("DROP INDEX ix_chunks_reuse_identity")
    op.execute(
        "ALTER TABLE chunks DROP COLUMN reuse_identity_hash, DROP COLUMN text_origin_at"
    )
    op.execute("DROP INDEX ix_sections_doc_key")
    op.execute("DROP INDEX ux_sections_key")
    op.execute(
        "ALTER TABLE document_sections DROP COLUMN subtree_content_hash,"
        " DROP COLUMN own_content_hash, DROP COLUMN section_key"
    )

    for trigger, table in (
        ("tr_document_representations_scope_update", "document_representations"),
        ("tr_document_representations_scope_insert", "document_representations"),
        ("tr_documents_scope_update", "documents"),
        ("tr_document_versions_scope_update", "document_versions"),
        ("tr_document_versions_scope_insert", "document_versions"),
        ("tr_effective_time_events_scope", "document_effective_time_events"),
        ("tr_effective_periods_scope", "document_effective_periods"),
    ):
        op.execute(f"DROP TRIGGER {trigger} ON {table}")
    op.execute("DROP FUNCTION document_version_scope_representation_write()")
    op.execute("DROP FUNCTION document_version_scope_lineage_write()")
    op.execute("DROP FUNCTION refresh_document_version_scope(uuid, uuid)")
    op.execute("DROP FUNCTION document_periodised_at(uuid, uuid, timestamptz)")
    op.execute(
        "DROP FUNCTION document_effective_intervals_at(uuid, uuid[], timestamptz)"
    )
    op.execute("DROP TABLE document_version_scope")
    op.execute("DROP TABLE document_effective_periods")
    op.execute("DROP TABLE document_effective_time_events")
    op.execute("DROP INDEX ux_docversions_version_key")
    op.execute("ALTER TABLE document_versions DROP COLUMN version_key")


def _legacy_statements(*, sql: str, first: str) -> tuple[str, ...]:
    """The statements of one legacy object: its CREATE and what follows it.

    Collects from the statement starting with ``first`` up to (excluding) the
    next ``CREATE TABLE``/``CREATE VIEW``, so a table keeps its comment and
    indexes and a view its comment.
    """
    from rememberstack.spine.migrations._helpers import _split_sql

    collected: list[str] = []
    for statement in _split_sql(sql=sql):
        body = "\n".join(
            line for line in statement.splitlines() if not line.startswith("--")
        ).strip()
        if collected:
            if body.startswith(("CREATE TABLE", "CREATE VIEW", "CREATE PROPERTY")):
                break
            collected.append(statement)
        elif body.startswith(first):
            collected.append(statement)
    if not collected:
        raise RuntimeError(f"legacy DDL has no statement starting {first!r}")
    return tuple(collected)
