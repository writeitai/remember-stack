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
action. Version *content* is immutable in PostgreSQL; only lifecycle fields
may transition.
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
  'Immutable versions of a saved query. Content columns cannot change after insert; only lifecycle fields (status, validation, approver, assurance, supersession, pinned manifest hash) may transition. Each pins the surface_manifest_hash it was validated against.';

COMMENT ON COLUMN saved_query_versions.validated_surface_manifest_hash IS
  'The exact surface this version was validated against. A version whose hash is not the current one is not executable until it has been revalidated.';

COMMENT ON COLUMN saved_query_versions.assurance IS
  'Who stands behind the meaning of the query. customer_authored is the draft default; customer_reviewed is set on activation of customer SQL; shipped_example is platform-written starting points. None of these raise the result grade above exploratory_tabular, and none means platform fact assurance.';

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

-- Authoritative current surface hash for this deployment. Publication and the
-- active→pending_revalidation suspension write it in one transaction; a
-- revalidation CAS conditions its transition on this row still holding the
-- hash the validator observed when it started.
CREATE TABLE saved_query_registry_state (
  deployment_id           uuid PRIMARY KEY
    REFERENCES deployments(deployment_id) ON DELETE CASCADE,
  surface_manifest_hash   text NOT NULL,
  updated_at              timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE saved_query_registry_state IS
  'Per-deployment surface-manifest pin for saved-query revalidation CAS. publish_surface_hash writes the new hash and suspends active versions in the same transaction; revalidate succeeds only when this hash still equals the validator start hash.';

-- Non-content governance evidence. Hard-delete purges customer SQL text but
-- these rows remain: only IDs, hashes, actor, action, and timestamps.
CREATE TABLE saved_query_audit (
  audit_id      bigserial PRIMARY KEY,
  deployment_id uuid NOT NULL,
  query_id      uuid,
  version       integer,
  query_hash    text,
  actor         text NOT NULL,
  action        text NOT NULL,
  old_hash      text,
  new_hash      text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX saved_query_audit_by_deployment
  ON saved_query_audit (deployment_id, created_at);

COMMENT ON TABLE saved_query_audit IS
  'Non-reversible, non-content audit of saved-query governance transitions (activate, disable, purge, publish, revalidate). Survives hard deletion of registry content.';

-- Content columns are immutable. Lifecycle fields may transition.
CREATE FUNCTION saved_query_versions_reject_content_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.sql IS DISTINCT FROM OLD.sql
     OR NEW.query_hash IS DISTINCT FROM OLD.query_hash
     OR NEW.parameter_schema IS DISTINCT FROM OLD.parameter_schema
     OR NEW.declared_result_schema IS DISTINCT FROM OLD.declared_result_schema
     OR NEW.declared_interpretation IS DISTINCT FROM OLD.declared_interpretation
     OR NEW.query_space_major IS DISTINCT FROM OLD.query_space_major
     OR NEW.default_limits IS DISTINCT FROM OLD.default_limits
     OR NEW.author_principal IS DISTINCT FROM OLD.author_principal
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.deployment_id IS DISTINCT FROM OLD.deployment_id
     OR NEW.query_id IS DISTINCT FROM OLD.query_id
     OR NEW.version IS DISTINCT FROM OLD.version
  THEN
    RAISE EXCEPTION
      'saved_query_versions content is immutable after insert'
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER saved_query_versions_immutable_content
  BEFORE UPDATE ON saved_query_versions
  FOR EACH ROW
  EXECUTE FUNCTION saved_query_versions_reject_content_mutation();
"""

_DROP = """
DROP TRIGGER IF EXISTS saved_query_versions_immutable_content ON saved_query_versions;
DROP FUNCTION IF EXISTS saved_query_versions_reject_content_mutation();
DROP TABLE IF EXISTS saved_query_audit;
DROP TABLE IF EXISTS saved_query_registry_state;
ALTER TABLE saved_queries DROP CONSTRAINT IF EXISTS saved_queries_latest_version_exists;
DROP TABLE IF EXISTS saved_query_versions;
DROP TABLE IF EXISTS saved_queries;
DROP TYPE IF EXISTS saved_query_assurance;
DROP TYPE IF EXISTS saved_query_origin;
DROP TYPE IF EXISTS saved_query_status;
"""


def upgrade() -> None:
    """Create the registry tables, immutability trigger, state pin, and audit."""
    op.execute(_DDL)


def downgrade() -> None:
    """Drop the registry. Versions are data, so this is destructive."""
    op.execute(_DROP)
