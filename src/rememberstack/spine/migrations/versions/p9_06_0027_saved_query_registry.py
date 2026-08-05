"""Create the saved-query registry: identities and immutable versions (§5).

A saved query has a stable identity and an append-only history. Editing one
does not change what an earlier caller ran — it adds a version — because a
saved query is something other people's work depends on, and silently changing
what a name means is the failure this shape exists to prevent.

Every version pins the `surface_manifest_hash` it was validated against. When
that hash changes, active versions move to `pending_revalidation` in the SAME
transaction that publishes the new hash, so there is no instant at which a
version claims validation against a surface that no longer exists. That state
is deliberately non-executable: a query validated against a different surface
is a query nobody has checked.

Registry SQL can contain customer data — a WHERE clause naming a person is
customer data — so the tables carry no dependency that would prevent a hard
delete, and D74 purges the text while audit keeps only ids, hashes, actor, and
action.
"""

from alembic import op

revision: str = "p9_06_0027"
down_revision: str | None = "p9_05_0026"
branch_labels = None
depends_on = None

# §5 registry bounds. They are enforced in the application, where a violation
# can be reported as `quota_exceeded` with something useful to say; the CHECKs
# here are the backstop that keeps a bug from writing something the contract
# says cannot exist.
_SQL_BYTES_MAX = 64 * 1024
_VERSIONS_PER_IDENTITY_MAX = 50

_DDL = f"""
CREATE TYPE saved_query_status AS ENUM (
  'draft', 'pending_revalidation', 'active', 'deprecated', 'disabled', 'broken'
);

CREATE TYPE saved_query_origin AS ENUM (
  'human', 'agent', 'import', 'shipped_example'
);

CREATE TYPE saved_query_assurance AS ENUM (
  'customer_authored', 'customer_reviewed', 'shipped_example'
);

CREATE TABLE saved_queries (
  deployment_id   uuid NOT NULL REFERENCES deployments(deployment_id)
                    ON DELETE CASCADE,
  query_id        uuid NOT NULL,
  namespace       text NOT NULL,
  name            text NOT NULL,
  description     text,
  owner_principal text NOT NULL,
  origin          saved_query_origin NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  disabled_at     timestamptz,
  latest_version  integer,
  PRIMARY KEY (query_id),
  UNIQUE (deployment_id, query_id),
  -- One name means one thing inside a deployment.
  UNIQUE (deployment_id, namespace, name),
  CONSTRAINT saved_queries_namespace_shape
    CHECK (namespace ~ '^[a-z][a-z0-9_]*$'),
  CONSTRAINT saved_queries_name_shape
    CHECK (name ~ '^[a-z][a-z0-9_]*$')
);

COMMENT ON TABLE saved_queries IS
  'Stable identity of a saved query. Versions are append-only in saved_query_versions; editing a saved query adds a version rather than changing what an earlier caller ran.';

CREATE TABLE saved_query_versions (
  deployment_id                  uuid NOT NULL,
  query_id                       uuid NOT NULL,
  version                        integer NOT NULL,
  sql                            text NOT NULL,
  query_hash                     text NOT NULL,
  parameter_schema               jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  declared_result_schema         jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  declared_interpretation        text,
  query_space_major              text NOT NULL,
  validated_surface_manifest_hash text NOT NULL,
  default_limits                 jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  status                         saved_query_status NOT NULL,
  assurance                      saved_query_assurance,
  author_principal               text NOT NULL,
  approver_principal             text,
  validation_report              jsonb NOT NULL DEFAULT '{{}}'::jsonb,
  created_at                     timestamptz NOT NULL DEFAULT now(),
  superseded_at                  timestamptz,
  PRIMARY KEY (query_id, version),
  UNIQUE (deployment_id, query_id, version),
  FOREIGN KEY (deployment_id, query_id)
    REFERENCES saved_queries(deployment_id, query_id) ON DELETE CASCADE,
  CONSTRAINT saved_query_versions_positive CHECK (version >= 1),
  CONSTRAINT saved_query_versions_bounded
    CHECK (version <= {_VERSIONS_PER_IDENTITY_MAX}),
  CONSTRAINT saved_query_versions_sql_size
    CHECK (octet_length(sql) <= {_SQL_BYTES_MAX}),
  -- An activated version was approved by someone. Recording who is the whole
  -- point of separating activation from authorship.
  CONSTRAINT saved_query_versions_active_is_approved
    CHECK (status <> 'active' OR approver_principal IS NOT NULL)
);

COMMENT ON TABLE saved_query_versions IS
  'Immutable versions of a saved query. Each pins the surface_manifest_hash it was validated against; when that hash changes, active versions move to pending_revalidation in the same transaction that publishes the new hash, because a query validated against a surface that no longer exists is a query nobody has checked.';

COMMENT ON COLUMN saved_query_versions.validated_surface_manifest_hash IS
  'The exact surface this version was validated against. A version whose hash is not the current one is not executable until it has been revalidated.';

COMMENT ON COLUMN saved_query_versions.assurance IS
  'Who stands behind the meaning of the query. NULL means platform fact assurance; customer_authored and customer_reviewed do not raise the result grade above exploratory_tabular.';

CREATE INDEX saved_query_versions_by_status
  ON saved_query_versions (deployment_id, status);

-- Drafts are excluded from default discovery, and the draft quotas in §5 are
-- per principal, so both are counted by this index rather than by a scan.
CREATE INDEX saved_query_versions_drafts_by_author
  ON saved_query_versions (deployment_id, author_principal)
  WHERE status = 'draft';

ALTER TABLE saved_queries
  ADD CONSTRAINT saved_queries_latest_version_exists
  FOREIGN KEY (query_id, latest_version)
  REFERENCES saved_query_versions(query_id, version)
  DEFERRABLE INITIALLY DEFERRED;
"""

_DROP = """
ALTER TABLE saved_queries DROP CONSTRAINT saved_queries_latest_version_exists;
DROP TABLE saved_query_versions;
DROP TABLE saved_queries;
DROP TYPE saved_query_assurance;
DROP TYPE saved_query_origin;
DROP TYPE saved_query_status;
"""


def upgrade() -> None:
    """Create the registry tables, their enums, and their bounds."""
    op.execute(_DDL)


def downgrade() -> None:
    """Drop the registry. Versions are data, so this is destructive."""
    op.execute(_DROP)
