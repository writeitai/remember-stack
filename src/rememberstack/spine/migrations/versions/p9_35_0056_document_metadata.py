"""D134 general document metadata: per-version fields, people, and observed names.

Existing versions are backfilled from what the spine already records: the
family from the stored MIME (the same mapping as
``rememberstack.core.document_metadata.family_for_mime``) and the version's
language, thread key and source dates. What title the caller declared was
never recorded, so ``document_metadata.title`` stays NULL (a later
conversion can fill it); the lineage title becomes the version's first
``document_names`` row, so the document stays findable by it. File names and
source paths were never recorded either, so they stay NULL. Lineages with a
hard-forget manifest are skipped: their source-bearing rows were scrubbed and
must not be re-derived.

The backfill is one statement in the migration transaction and holds locks
on ``document_versions`` while it runs; a keyset backfill (as p9_16 does) is
the path if a deployment is too large for that.

The downgrade refuses to drop populated tables: metadata observed at ingest
(file names, paths, observed names) cannot be re-derived from anything else.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables

revision: str = "p9_35_0056"
down_revision: str | None = "p9_34_0055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL_MAPPING_VERSION = "backfill-p9_35"

_DDL = r"""
CREATE TABLE document_metadata (
  deployment_id   uuid NOT NULL,
  version_id      uuid NOT NULL,
  doc_id          uuid NOT NULL,               -- denormalized lineage for filters
  family          text NOT NULL,               -- D133 format family
  file_name       text,                        -- as observed for this version
  source_path     text,                        -- the source location as observed for this version (lineage source_uri is mutable)
  title           text,                        -- the title the document declares
  created_at      timestamptz,                 -- source-declared created/sent
  modified_at     timestamptz,                 -- source-declared last modified
  language        text,                        -- detected primary language
  thread_ref      text,                        -- opaque conversation/thread key
  provenance      jsonb NOT NULL DEFAULT '{}', -- field → source | connector
  extra           jsonb NOT NULL DEFAULT '{}', -- family-specific fields; returned, not a general filter
  metadata_mapping_version text NOT NULL,      -- the family's metadata mapping (converter/connector), distinct from the E2 extractor version
  PRIMARY KEY (deployment_id, version_id),
  FOREIGN KEY (deployment_id, version_id) REFERENCES document_versions (deployment_id, version_id) ON DELETE CASCADE
);
COMMENT ON TABLE document_metadata IS
  'D134 general document metadata: one row per version, the same fields for every format family. Hard forget deletes the lineage''s rows.';
CREATE INDEX ix_document_metadata_family  ON document_metadata (deployment_id, family, created_at DESC);
CREATE INDEX ix_document_metadata_thread  ON document_metadata (deployment_id, thread_ref) WHERE thread_ref IS NOT NULL;

CREATE TABLE document_names (
  deployment_id   uuid NOT NULL,
  version_id      uuid NOT NULL,
  observed_at     timestamptz NOT NULL,        -- when this name was observed for the version
  file_name       text,                        -- the file name observed
  title           text,                        -- the declared title observed
  source_path     text,                        -- the source location observed
  name_text       text NOT NULL,               -- file_name + title + source_path, space-joined; the indexed search text
  origin          text NOT NULL CHECK (origin IN ('ingest','observation','converter','backfill')), -- ingest | observation (same bytes, new name) | converter (declared by the file) | backfill (legacy lineage title)
  PRIMARY KEY (deployment_id, version_id, observed_at),
  FOREIGN KEY (deployment_id, version_id) REFERENCES document_metadata (deployment_id, version_id) ON DELETE CASCADE
);
COMMENT ON TABLE document_names IS
  'D134 every name a version was observed under: the name at ingest, then one row per metadata observation (identical bytes under a new file name, title or path). Hard forget deletes the lineage''s rows.';
CREATE INDEX ix_document_names_trgm ON document_names USING gin (name_text gin_trgm_ops);
CREATE INDEX ix_document_names_bm25 ON document_names USING bm25 (name_text) WITH (text_config='simple');

CREATE TABLE document_people (
  deployment_id   uuid NOT NULL,
  version_id      uuid NOT NULL,
  role            text NOT NULL CHECK (role IN ('author','recipient')), -- author or recipient
  ordinal         integer NOT NULL,            -- position within the role as the source lists it
  display_name    text,                        -- the display name as given
  address         text,                        -- email address or handle
  normalized_name text,                        -- lower case, unaccented, whitespace collapsed
  normalized_address text,                     -- lower case, unaccented, whitespace collapsed
  provenance      text NOT NULL CHECK (provenance IN ('source','connector')), -- source file or connector
  CHECK (display_name IS NOT NULL OR address IS NOT NULL),
  PRIMARY KEY (deployment_id, version_id, role, ordinal),
  FOREIGN KEY (deployment_id, version_id) REFERENCES document_metadata (deployment_id, version_id) ON DELETE CASCADE
);
COMMENT ON TABLE document_people IS
  'D134 the people in a version''s authors/recipients, one row each, for filtering by name or address. Hard forget deletes the lineage''s rows.';
CREATE INDEX ix_document_people_name    ON document_people USING gin (normalized_name gin_trgm_ops);
CREATE INDEX ix_document_people_address ON document_people (deployment_id, normalized_address);
"""

_BACKFILL_METADATA = f"""
INSERT INTO document_metadata (
  deployment_id, version_id, doc_id, family, file_name, source_path, title,
  created_at, modified_at, language, thread_ref, metadata_mapping_version
)
SELECT v.deployment_id, v.version_id, v.doc_id,
       CASE
         WHEN m.mime IN ('text/markdown', 'text/x-markdown') THEN 'markdown'
         WHEN m.mime IN ('text/html', 'application/xhtml+xml') THEN 'html'
         WHEN m.mime = 'application/pdf' THEN 'pdf'
         WHEN m.mime LIKE 'image/%' THEN 'image'
         WHEN m.mime LIKE 'audio/%' THEN 'audio'
         WHEN m.mime LIKE 'video/%' THEN 'video'
         WHEN m.mime IN ('application/msword', 'application/rtf')
           OR m.mime LIKE 'application/vnd.openxmlformats-officedocument.%'
           OR m.mime LIKE 'application/vnd.oasis.opendocument.%'
           OR m.mime LIKE 'application/vnd.ms-excel%'
           OR m.mime LIKE 'application/vnd.ms-powerpoint%' THEN 'office'
         WHEN m.mime LIKE 'text/%' THEN 'text'
         ELSE 'other'
       END,
       NULL, NULL, NULL,
       v.published_at, v.source_modified_at, v.language, v.thread_ref,
       '{BACKFILL_MAPPING_VERSION}'
FROM document_versions v
JOIN LATERAL (
  SELECT lower(btrim(split_part(c.mime, ';', 1))) AS mime
  FROM content_objects c
  WHERE c.deployment_id = v.deployment_id AND c.content_hash = v.content_hash
) m ON true
WHERE NOT EXISTS (
  SELECT 1 FROM forget_manifests f
  WHERE f.deployment_id = v.deployment_id AND f.doc_id = v.doc_id
)
ON CONFLICT (deployment_id, version_id) DO NOTHING
"""

_BACKFILL_NAMES = f"""
INSERT INTO document_names (
  deployment_id, version_id, observed_at, title, name_text, origin
)
SELECT md.deployment_id, md.version_id, v.ingested_at, d.title, btrim(d.title),
       'backfill'
FROM document_metadata md
JOIN document_versions v
  ON v.deployment_id = md.deployment_id AND v.version_id = md.version_id
JOIN documents d ON d.deployment_id = v.deployment_id AND d.doc_id = v.doc_id
WHERE md.metadata_mapping_version = '{BACKFILL_MAPPING_VERSION}'
  AND btrim(coalesce(d.title, '')) <> ''
ON CONFLICT (deployment_id, version_id, observed_at) DO NOTHING
"""

_TABLES = ("document_people", "document_names", "document_metadata")


def upgrade() -> None:
    """Create the D134 tables and backfill one row per existing version."""
    apply_ddl(sql=_DDL)
    op.execute(_BACKFILL_METADATA)
    op.execute(_BACKFILL_NAMES)


def downgrade() -> None:
    """Drop the D134 tables, refusing when any holds rows."""
    connection = op.get_bind()
    populated = [
        table
        for table in _TABLES
        if connection.execute(text(f"SELECT EXISTS (SELECT 1 FROM {table})")).scalar()
    ]
    if populated:
        raise RuntimeError(
            "D134 downgrade requires an explicitly reviewed restore/conversion"
            f" plan: {', '.join(populated)} hold document metadata (file names,"
            " paths, observed names) that cannot be re-derived"
        )
    drop_tables(table_names=_TABLES)
