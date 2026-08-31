"""Record the typed principal that created each document version.

revision: p9_21_0042

Ingest attribution is a *typed principal at version grain*: the engine records
WHO created a version without becoming a per-user authorization system (D50
keeps content-level authorization a non-goal; this adds attribution only).

Three kinds are distinguished because they are genuinely different referents:
``user`` is a person, ``api_credential`` is a machine credential that a person
once minted, and ``service`` is the deployment's own automation. Attributing
credential activity to the human who minted the token would be false
attribution, so the kind is stored, never inferred away.

Attribution is creation-scoped and immutable: the column is set when a version
row is inserted and never updated. Under D55 identical bytes return the
existing version with ``created=False``, so a later submitter of the same bytes
changes nothing here — that attempt is an operator/control-plane audit event,
not a second attribution.

``external_ref`` is opaque to the engine (the caller's stable id for the
principal). It is treated as erasable PII: a person-grain forget must be able
to enumerate and scrub principal rows and the bindings that reference them.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "p9_21_0042"
down_revision: str | None = "p9_20_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATE_KIND = (
    "CREATE TYPE ingest_principal_kind AS ENUM ('user','api_credential','service')"
)

_CREATE_TABLE = """
CREATE TABLE ingest_principals (
  principal_id    uuid PRIMARY KEY,
  deployment_id   uuid NOT NULL REFERENCES deployments,
  kind            ingest_principal_kind NOT NULL,
  external_ref    text NOT NULL,          -- opaque caller id; erasable PII
  first_seen_at   timestamptz NOT NULL DEFAULT now(),
  last_seen_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (deployment_id, kind, external_ref),
  UNIQUE (deployment_id, principal_id)    -- composite-FK target (tenancy, §0)
);
COMMENT ON TABLE ingest_principals IS
  'Typed ingest actors (user | api_credential | service). external_ref is the
   caller''s opaque stable id and is erasable PII. A credential is never
   collapsed into the person who minted it.';
"""
# No separate lookup index: UNIQUE (deployment_id, kind, external_ref) already
# provides one. A duplicate would double write cost and, worse, keep a second
# physical copy of erasable PII.

# ONE transaction, deliberately.
#
# An earlier revision built the index CONCURRENTLY inside an autocommit
# block. That is gentler on a huge table, but Alembic commits everything
# before entering the block, so an interruption there left the type, table,
# column and FK committed with the revision unstamped — and the rerun then
# died on `type "ingest_principal_kind" already exists`. A migration that
# cannot be resumed is a worse failure than a lock.
#
# Atomicity wins here: this either commits whole or rolls back whole. The
# index build takes a SHARE lock on `document_versions` for its duration
# (readers unaffected, writers wait). On a deployment large enough for that
# to matter, build it by hand first —
#   CREATE INDEX CONCURRENTLY ix_docversions_principal
#     ON document_versions (deployment_id, ingested_by_principal_id)
#     WHERE ingested_by_principal_id IS NOT NULL;
# — and the IF NOT EXISTS below makes this step a no-op.
_ADD_COLUMN = """
ALTER TABLE document_versions
  ADD COLUMN ingested_by_principal_id uuid;
COMMENT ON COLUMN document_versions.ingested_by_principal_id IS
  'The principal that CREATED this version (immutable, creation-scoped). NULL
   for versions ingested without attribution, including every pre-migration
   row. ON DELETE SET NULL keeps a principal erasure from destroying the
   version itself.';
"""

_ADD_FK = """
ALTER TABLE document_versions
  ADD CONSTRAINT fk_document_versions_ingest_principal
    FOREIGN KEY (deployment_id, ingested_by_principal_id)
    REFERENCES ingest_principals (deployment_id, principal_id)
    ON DELETE SET NULL (ingested_by_principal_id);
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS ix_docversions_principal
  ON document_versions (deployment_id, ingested_by_principal_id)
  WHERE ingested_by_principal_id IS NOT NULL;
"""


def upgrade() -> None:
    """Add the principal registry and attribution column in one transaction."""
    op.execute(_CREATE_KIND)
    op.execute(_CREATE_TABLE)
    op.execute(_ADD_COLUMN)
    op.execute(_ADD_FK)
    op.execute(_CREATE_INDEX)


def downgrade() -> None:
    """Drop attribution; existing versions lose their principal reference."""
    op.execute("DROP INDEX IF EXISTS ix_docversions_principal")
    op.execute(
        "ALTER TABLE document_versions "
        "DROP CONSTRAINT IF EXISTS fk_document_versions_ingest_principal, "
        "DROP COLUMN IF EXISTS ingested_by_principal_id"
    )
    op.execute("DROP TABLE IF EXISTS ingest_principals")
    op.execute("DROP TYPE IF EXISTS ingest_principal_kind")
