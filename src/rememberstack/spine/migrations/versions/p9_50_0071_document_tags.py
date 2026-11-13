"""D156 document tags: the tag registry, per-lineage assignments and their views.

``tags`` is the deployment's declared vocabulary; ``document_tags`` assigns
registered tags to document lineages. Assignments are stored only here, are
not versioned, and enter no reuse key, so a tag write causes no processing.
The query space exposes both as ``memory_v1.tags_current`` and
``memory_v1.document_tags_live``, so saved queries can join on tags like any
other document coordinate.

The downgrade refuses to drop populated tables: a registry and its
assignments are caller decisions that cannot be re-derived from anything else.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import apply_view_ddl

revision: str = "p9_50_0071"
down_revision: str | None = "p9_49_0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DDL = r"""
CREATE TABLE tags (
  deployment_id uuid NOT NULL,
  tag_id        uuid NOT NULL,
  name          text NOT NULL,               -- NFC, 1-128 characters, no whitespace or control characters; the spelling it was declared with
  description   text NOT NULL,               -- what the tag means and when to apply it, written for agents; up to 2,000 characters
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, tag_id),
  CHECK (char_length(name) BETWEEN 1 AND 128),
  CHECK (char_length(description) <= 2000)
);
COMMENT ON TABLE tags IS
  'D156 the deployment''s tag registry: a tag is created before it is used. Names keep their declared spelling and are unique case-insensitively. Hard forget of a document leaves the registry unchanged.';
CREATE UNIQUE INDEX ux_tags_name ON tags (deployment_id, lower(name));

CREATE TABLE document_tags (
  deployment_id uuid NOT NULL,
  doc_id        uuid NOT NULL,
  tag_id        uuid NOT NULL,
  assigned_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (deployment_id, doc_id, tag_id),
  FOREIGN KEY (deployment_id, doc_id) REFERENCES documents (deployment_id, doc_id) ON DELETE CASCADE,
  FOREIGN KEY (deployment_id, tag_id) REFERENCES tags (deployment_id, tag_id) ON DELETE CASCADE
);
COMMENT ON TABLE document_tags IS
  'D156 tag assignments on a document lineage, stored only here: chunks, claims and facts inherit through their documents. Not versioned and never part of a reuse key, so a tag write causes no processing. Deletion keeps the rows; hard forget deletes the lineage''s rows.';
CREATE INDEX ix_document_tags_tag ON document_tags (deployment_id, tag_id, doc_id);
"""

MEMORY_V1_TAG_VIEW_DDL = r"""
CREATE VIEW memory_v1.tags_current (
  deployment_id,   -- The deployment that registered the tag.
  tag_id,          -- Stable identity of the tag; assignments point at it, so a rename changes no assignment.
  name,            -- The tag's current name with the spelling it was declared with, unique within the deployment ignoring letter case.
  description,     -- What the tag means and when to apply it, written by the caller for agents.
  created_at,      -- When the tag was registered, a registry instant rather than a world-time clock.
  updated_at       -- When the tag's name or description last changed, a registry instant rather than a world-time clock.
) AS
SELECT
  t.deployment_id,
  t.tag_id,
  t.name,
  t.description,
  t.created_at,
  t.updated_at
FROM tags AS t;
COMMENT ON VIEW memory_v1.tags_current IS
  'One row per registered document tag, keyed by (deployment_id, tag_id). A tag is a caller-declared label with a description, created before any document carries it; its name is unique within the deployment ignoring letter case and is matched that way, so compare names with lower() on both sides. A renamed tag shows only its current name, because there are no aliases, and a deleted tag is absent. Tags are the deployment''s vocabulary rather than content from any source, so forgetting a document does not change this relation. The view carries no counts: join document_tags_live to count the live documents that carry a tag.';

CREATE VIEW memory_v1.document_tags_live (
  deployment_id,   -- The deployment that owns the assignment.
  doc_id,          -- The live lineage carrying the tag, joinable to documents_live.
  tag_id,          -- The registered tag assigned to the lineage, joinable to tags_current.
  name,            -- The tag's current name, so a renamed tag shows its new name on every lineage that carries it.
  assigned_at      -- When the tag was first assigned to the lineage, a registry instant rather than a world-time clock.
) AS
SELECT
  a.deployment_id,
  a.doc_id,
  a.tag_id,
  t.name,
  a.assigned_at
FROM document_tags AS a
JOIN tags AS t
  ON t.deployment_id = a.deployment_id
 AND t.tag_id = a.tag_id
JOIN memory_v1.documents_live AS d
  ON d.deployment_id = a.deployment_id
 AND d.doc_id = a.doc_id;
COMMENT ON VIEW memory_v1.document_tags_live IS
  'One row per tag assignment on a live document lineage, keyed by (deployment_id, doc_id, tag_id) and joined to documents_live on (deployment_id, doc_id) and to tags_current on (deployment_id, tag_id). Tags belong to the lineage, not to a version, so every version of the document carries them, and they are never part of what the memory extracts or believes: a chunk inherits its document''s tags through doc_id, a claim through its occurrences, and a fact through its supporting claims in fact_claim_evidence_live. A deleted or forgotten lineage has no rows, and neither does a deleted tag. The view carries no counts.';
"""
"""The authored D156 ``memory_v1`` views, also read by the offline manifest
builder."""

_TABLES = ("document_tags", "tags")
_VIEWS = ("document_tags_live", "tags_current")
_VIEW_OWNER = "rememberstack_view_owner"

QUERY_ROLE_GRANTS = r"""
DO $do$
BEGIN
  EXECUTE format(
    'GRANT SELECT ON memory_v1.tags_current, memory_v1.document_tags_live TO %I',
    'rememberstack_query_' || current_database()
  );
END
$do$;
"""
"""The query role reads the two views; the tables stay private."""


def upgrade() -> None:
    """Create the registry, the assignment table and their two views."""
    apply_ddl(sql=_DDL)
    for table in _TABLES:
        # The view owner's schema-wide SELECT was granted before they existed.
        op.execute(f"GRANT SELECT ON {table} TO {_VIEW_OWNER}")
    apply_view_ddl(sql=MEMORY_V1_TAG_VIEW_DDL)
    for view in _VIEWS:
        op.execute(f"ALTER VIEW memory_v1.{view} OWNER TO {_VIEW_OWNER}")
    op.execute(QUERY_ROLE_GRANTS)


def downgrade() -> None:
    """Drop the views and both tables; refuse when any tag was registered."""
    connection = op.get_bind()
    if connection.execute(text("SELECT EXISTS (SELECT 1 FROM tags)")).scalar_one():
        raise RuntimeError(
            "D156 downgrade would drop registered tags and their assignments,"
            " which cannot be re-derived; delete them first"
        )
    for view in _VIEWS:
        op.execute(f"DROP VIEW memory_v1.{view}")
    for table in _TABLES:
        op.execute(f"DROP TABLE {table}")
