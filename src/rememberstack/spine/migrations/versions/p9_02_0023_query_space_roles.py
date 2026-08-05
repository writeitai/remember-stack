"""Create the query-space role split and grants (design §4.2 as amended).

Tenancy is physical (D68) plus grants — row-level security and
`security_barrier` are deliberately absent (operator decision 2026-08-04:
measured performance degradation and maintenance burden; the connection IS the
deployment, so per-row policies are redundant complexity). This migration
binds the role split:

- ``rememberstack_view_owner`` (NOLOGIN) owns every ``memory_v1`` view and
  holds the minimum base-table ``SELECT`` — the only path from public views to
  private tables.
- ``rememberstack_query`` (LOGIN, NOINHERIT) is the deployment-scoped agent
  role: ``USAGE`` on ``memory_v1``, ``SELECT`` on exactly the public views,
  a ``search_path`` of ``memory_v1, pg_catalog``, and nothing else — no base
  schema, no private helpers, no other deployment's objects (those live in
  other databases entirely). Its password is set at deploy time, never here.

Roles are cluster-global while migrations are per-database, so creation is
idempotent (guarded on ``pg_roles``) and the downgrade revokes grants and
returns view ownership but deliberately leaves the roles in place — another
database on the same cluster may still bind them; dropping them is an
operator action, recorded in the implementation note.
"""

from alembic import op

revision: str = "p9_02_0023"
down_revision: str | None = "p9_01_0022"
branch_labels = None
depends_on = None

_VIEW_OWNER = "rememberstack_view_owner"
# The query login is PER DEPLOYMENT, not per cluster. Roles are cluster-global
# objects while D68 gives each deployment its own database, so one shared login
# would hold whatever grants every deployment's migration gave it — and
# PostgreSQL's default PUBLIC CONNECT would let it reach the others' databases.
# Deriving the name from the database keeps the login inside its deployment.
_QUERY_ROLE_PREFIX = "rememberstack_query"


def query_role_name(database: str) -> str:
    """The deployment-scoped login name for one database."""
    return f"{_QUERY_ROLE_PREFIX}_{database}"


# The exhaustive public view list is read from the checked-in catalog at
# runtime so this migration can never drift from the schema contract.
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS_BY_NAME  # noqa: E402

_PRIVATE_HELPERS = (
    "v_memory_entity_survivor",
    "v_memory_mention_current_content",
    "v_memory_page_citation_visible",
)


def _create_role_if_absent(name: str, options: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{name}') THEN
                CREATE ROLE {name} {options};
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    database = op.get_bind().exec_driver_sql("SELECT current_database()").scalar_one()
    query_role = query_role_name(str(database))
    _create_role_if_absent(_VIEW_OWNER, "NOLOGIN")
    _create_role_if_absent(query_role, "LOGIN NOINHERIT")

    # The view owner needs base-table read to serve owner-evaluated views.
    op.execute(f"GRANT USAGE ON SCHEMA public TO {_VIEW_OWNER}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {_VIEW_OWNER}")
    op.execute(f"GRANT USAGE ON SCHEMA memory_v1 TO {_VIEW_OWNER}")

    # PostgreSQL grants several capabilities to PUBLIC by default, and every
    # role inherits them — so "the query role was granted only SELECT on the
    # views" is not the same as "the query role can only select the views".
    # Within this deployment's database those defaults are withdrawn: no
    # schema usage, no temporary objects, no executing the spine's own
    # functions. (Per-deployment routing, D68, makes this database-local
    # revocation both safe and complete.)
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    # The database name is not a parameterizable identifier, so it is read
    # from the connection and quoted.
    op.execute(
        f"""
        DO $$
        BEGIN
            EXECUTE format(
                'REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()
            );
            -- CONNECT goes back to exactly the role that must have it, so
            -- the withdrawal is a narrowing rather than a lockout.
            EXECUTE format(
                'GRANT CONNECT ON DATABASE %I TO {query_role}',
                current_database()
            );
        END $$;
        """
    )
    op.execute("REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC")
    op.execute("REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA public FROM PUBLIC")
    # Large-object APIs live in pg_catalog and are granted to PUBLIC, so they
    # survive a schema-level revoke; they are server-side file handles and
    # have no place on a read-only query surface.
    for large_object_api in (
        "lo_import(text)",
        "lo_import(text, oid)",
        "lo_export(oid, text)",
        "lo_create(oid)",
        "lo_unlink(oid)",
        "lo_from_bytea(oid, bytea)",
        "lo_put(oid, bigint, bytea)",
        "loread(integer, integer)",
        "lowrite(integer, bytea)",
    ):
        op.execute(f"REVOKE EXECUTE ON FUNCTION {large_object_api} FROM PUBLIC")
    # Anything the spine creates later inherits the same posture.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
        " REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
    )

    for view_name in sorted(VIEW_CONTRACTS_BY_NAME):
        op.execute(f"ALTER VIEW memory_v1.{view_name} OWNER TO {_VIEW_OWNER}")

    op.execute(f"GRANT USAGE ON SCHEMA memory_v1 TO {query_role}")
    for view_name in sorted(VIEW_CONTRACTS_BY_NAME):
        op.execute(f"GRANT SELECT ON memory_v1.{view_name} TO {query_role}")
    # Durable role settings: the query role may not set superuser-only GUCs
    # per request, so the caps that require privilege are pinned here by the
    # migration (which runs as owner). The executor re-applies the
    # per-request, non-privileged ones with SET LOCAL.
    op.execute(f"ALTER ROLE {query_role} SET search_path = memory_v1, pg_catalog")
    op.execute(f"ALTER ROLE {query_role} SET temp_file_limit = '65536kB'")
    op.execute(f"ALTER ROLE {query_role} SET max_parallel_workers_per_gather = 0")
    op.execute(f"ALTER ROLE {query_role} SET default_transaction_read_only = on")
    op.execute(
        f"ALTER ROLE {query_role} SET idle_in_transaction_session_timeout = 5000"
    )

    # The private helpers are never public: no PUBLIC grants, no query-role
    # grants, and the view owner reaches them only through ownership of the
    # public views' definitions.
    for helper in _PRIVATE_HELPERS:
        op.execute(f"REVOKE ALL ON public.{helper} FROM PUBLIC")
        op.execute(f"REVOKE ALL ON public.{helper} FROM {query_role}")
        op.execute(f"ALTER VIEW public.{helper} OWNER TO {_VIEW_OWNER}")


def downgrade() -> None:
    database = op.get_bind().exec_driver_sql("SELECT current_database()").scalar_one()
    query_role = query_role_name(str(database))
    for helper in _PRIVATE_HELPERS:
        op.execute(f"ALTER VIEW public.{helper} OWNER TO CURRENT_USER")
    op.execute(f"ALTER ROLE {query_role} RESET ALL")
    for view_name in sorted(VIEW_CONTRACTS_BY_NAME):
        op.execute(f"REVOKE ALL ON memory_v1.{view_name} FROM {query_role}")
        op.execute(f"ALTER VIEW memory_v1.{view_name} OWNER TO CURRENT_USER")
    op.execute(f"REVOKE USAGE ON SCHEMA memory_v1 FROM {query_role}")
    op.execute(f"REVOKE USAGE ON SCHEMA memory_v1 FROM {_VIEW_OWNER}")
    op.execute(f"REVOKE SELECT ON ALL TABLES IN SCHEMA public FROM {_VIEW_OWNER}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {_VIEW_OWNER}")
    # Roles stay: they are cluster-global and possibly bound by sibling
    # databases; dropping them is an operator action.
