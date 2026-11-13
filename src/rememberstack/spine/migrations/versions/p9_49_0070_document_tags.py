"""D156 document tags: the tag registry and per-lineage assignments.

``tags`` is the deployment's declared vocabulary; ``document_tags`` assigns
registered tags to document lineages. Assignments are stored only here, are
not versioned, and enter no reuse key, so a tag write causes no processing.

The downgrade refuses to drop populated tables: a registry and its
assignments are caller decisions that cannot be re-derived from anything else.
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_ddl

revision: str = "p9_49_0070"
down_revision: str | None = "p9_48_0069"
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

_TABLES = ("document_tags", "tags")


def upgrade() -> None:
    """Create the registry and the assignment table."""
    apply_ddl(sql=_DDL)


def downgrade() -> None:
    """Drop both tables; refuse when any tag was registered."""
    connection = op.get_bind()
    if connection.execute(text("SELECT EXISTS (SELECT 1 FROM tags)")).scalar_one():
        raise RuntimeError(
            "D156 downgrade would drop registered tags and their assignments,"
            " which cannot be re-derived; delete them first"
        )
    for table in _TABLES:
        op.execute(f"DROP TABLE {table}")
