"""Fixed native relation exclusion and in-guard allocation sampling for D152.

This is a PG19 / p9_46_0067 primitive, not a complete capture, clock witness,
classification or billable quantity. Installation uses trusted operator
authority; the existing collector LOGIN receives only the fixed operation.
The caller retains this original transaction and rolls it back after its
independent original view and boundary checks. No owning DSN reaches that API.
"""

from __future__ import annotations

from typing import Annotated
from typing import Literal

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.model.storage_capture import UnsignedDecimal
from rememberstack.spine.catalog_contract import EXPECTED_RANGE_PARENTS
from rememberstack.spine.catalog_contract import EXPECTED_TABLES

_SCHEMA = "rememberstack_capture_internal"
_FUNCTION = "guard_relation_allocation"
_ROOTS = tuple(
    sorted(
        set(EXPECTED_TABLES)
        | {
            "alembic_version",
            "ingest_principals",
            "managed_ingest_measurements",
            "managed_ingest_outcomes",
            "part_config",
            "part_config_sub",
        }
        | {f"template_public_{name}" for name in EXPECTED_RANGE_PARENTS}
    )
)
_EXTENSIONS = (
    "btree_gist=1.9",
    "fuzzystrmatch=1.2",
    "pg_partman=5.5.0",
    "pg_textsearch=1.5.1",
    "pg_trgm=1.6",
    "pgcrypto=1.4",
    "plpgsql=1.0",
    "unaccent=1.1",
    "vector=0.8.6",
)
_CATALOGS = ("pg_class", "pg_namespace", "pg_proc", "pg_extension")
_POSITIVE = Annotated[int, Field(strict=True, gt=0)]


class _NativeRecord(BaseModel):
    """Keep private native observations immutable and reject implicit coercion."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class NativeRelationAllocation(_NativeRecord):
    """One actual relation/fork record, still requiring profile classification."""

    resource_kind: Literal["table", "index", "toast", "toast_index"]
    root_relation: str
    relation_oid: UnsignedDecimal
    main_bytes: UnsignedDecimal
    fsm_bytes: UnsignedDecimal
    vm_bytes: UnsignedDecimal
    init_bytes: UnsignedDecimal


class NativeRelationGuardSample(_NativeRecord):
    """A same-transaction sample, never a completed historic capture witness."""

    contract: Literal["rememberstack.native_relation_guard.v1"]
    deployment_id: str
    schema_head: Literal["p9_46_0067"]
    session_login: str
    definer_role: str
    backend_pid: _POSITIVE
    transaction_id: UnsignedDecimal
    guard_started_unix_us: UnsignedDecimal
    sample_before_unix_us: UnsignedDecimal
    sample_after_unix_us: UnsignedDecimal
    allocated_fork_bytes: UnsignedDecimal
    physical_table_total_bytes: UnsignedDecimal
    relations: tuple[NativeRelationAllocation, ...]


class NativeRelationGuardRefused(RuntimeError):
    """A non-secret refusal of incompatible installer or caller authority."""


_BODY = r"""
DECLARE
  expected_roots constant text[] := __ROOTS__;
  expected_extensions constant text[] := __EXTENSIONS__;
  started_us bigint := floor(extract(epoch FROM clock_timestamp())*1000000);
  before_us bigint;
  after_us bigint;
  actual_roots text[];
  actual_extensions text[];
  native_scope uuid;
  r record;
  option_value text;
  physical_oids oid[];
  samples jsonb;
  fork_total numeric;
  table_total numeric;
BEGIN
  IF current_setting('transaction_isolation') <> 'read committed'
     OR current_setting('transaction_read_only') <> 'off' THEN
    RAISE EXCEPTION USING ERRCODE='25001', MESSAGE='native_guard_transaction';
  END IF;
  IF deadline_unix_us IS NULL OR deadline_unix_us <= started_us
     OR deadline_unix_us-started_us > 10000000 THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='native_guard_deadline';
  END IF;
  IF current_setting('transaction_timeout')::interval <= interval '0 seconds'
     OR current_setting('transaction_timeout')::interval > interval '10 seconds'
     OR current_setting('statement_timeout')::interval <= interval '0 seconds'
     OR current_setting('statement_timeout')::interval > interval '10 seconds'
     OR current_setting('lock_timeout')::interval <= interval '0 seconds'
     OR current_setting('lock_timeout')::interval > interval '250 milliseconds' THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='native_guard_native_bounds';
  END IF;
  IF current_setting('server_version_num')::integer/10000 <> 19 THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_server';
  END IF;
  IF NOT pg_try_advisory_xact_lock(152, (SELECT oid::integer FROM pg_database
                                        WHERE datname=current_database())) THEN
    RAISE EXCEPTION USING ERRCODE='55P03', MESSAGE='native_guard_busy';
  END IF;
  LOCK TABLE pg_catalog.pg_class, pg_catalog.pg_namespace,
             pg_catalog.pg_proc, pg_catalog.pg_extension IN SHARE MODE NOWAIT;
  IF (SELECT count(*) FROM public.alembic_version) <> 1
     OR (SELECT version_num FROM public.alembic_version) <> 'p9_46_0067' THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_schema';
  END IF;
  SELECT array_agg(extname||'='||extversion ORDER BY extname)
    INTO actual_extensions FROM pg_extension;
  IF actual_extensions IS DISTINCT FROM expected_extensions THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_extensions';
  END IF;
  IF EXISTS(SELECT 1 FROM unnest(string_to_array(
       current_setting('shared_preload_libraries'), ',')) p(name)
       WHERE btrim(name) NOT IN ('', 'pg_textsearch', 'pg_partman_bgw')) THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_background';
  END IF;
  SELECT array_agg(c.relname::text ORDER BY c.relname::text)
    INTO actual_roots FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind IN ('r','p','m')
      AND NOT EXISTS(SELECT 1 FROM pg_inherits h WHERE h.inhrelid=c.oid);
  IF actual_roots IS DISTINCT FROM expected_roots THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_root_set';
  END IF;
  IF EXISTS(SELECT 1 FROM pg_inherits h
       JOIN pg_class p ON p.oid=h.inhparent
       JOIN pg_namespace pn ON pn.oid=p.relnamespace
       JOIN pg_class c ON c.oid=h.inhrelid
       JOIN pg_namespace cn ON cn.oid=c.relnamespace
       WHERE (pn.nspname='public' OR cn.nspname='public')
         AND (pn.nspname<>'public' OR cn.nspname<>'public'))
     OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname='public' AND c.relkind='f') THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_relation_scope';
  END IF;
  IF (SELECT count(*) FROM public.deployments) <> 1 THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_deployment';
  END IF;
  SELECT deployment_id INTO STRICT native_scope FROM public.deployments;

  FOR r IN SELECT c.oid,n.nspname,c.relname,c.relkind,c.reltoastrelid,
                  c.relpersistence,a.amname
           FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
           LEFT JOIN pg_am a ON a.oid=c.relam
           WHERE n.nspname='public' AND c.relkind IN ('r','p','m') ORDER BY c.oid
  LOOP
    IF floor(extract(epoch FROM clock_timestamp())*1000000) >= deadline_unix_us THEN
      RAISE EXCEPTION USING ERRCODE='57014', MESSAGE='native_guard_deadline';
    END IF;
    IF r.relpersistence <> 'p' OR (r.relkind <> 'p' AND r.amname <> 'heap') THEN
      RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_table_format';
    END IF;
    EXECUTE format('LOCK TABLE ONLY %I.%I IN SHARE MODE NOWAIT',r.nspname,r.relname);
    IF r.reltoastrelid <> 0 THEN
      SELECT o.option_value INTO option_value FROM pg_class t
        CROSS JOIN LATERAL pg_options_to_table(t.reloptions) o
        WHERE t.oid=r.reltoastrelid AND o.option_name='autovacuum_enabled';
      IF option_value IS NULL THEN
        EXECUTE format('ALTER TABLE ONLY %I.%I RESET(toast.autovacuum_enabled)',
                       r.nspname,r.relname);
      ELSE
        EXECUTE format('ALTER TABLE ONLY %I.%I SET(toast.autovacuum_enabled=%L)',
                       r.nspname,r.relname,option_value);
      END IF;
    END IF;
  END LOOP;

  FOR r IN SELECT x.oid,n.nspname,x.relname,a.amname,i.indisvalid,i.indisready,
                  ts.spcname
           FROM pg_index i JOIN pg_class x ON x.oid=i.indexrelid
           JOIN pg_namespace n ON n.oid=x.relnamespace
           JOIN pg_class t ON t.oid=i.indrelid
           JOIN pg_namespace tn ON tn.oid=t.relnamespace
           JOIN pg_am a ON a.oid=x.relam
           JOIN pg_database d ON d.datname=current_database()
           JOIN pg_tablespace ts ON ts.oid=COALESCE(NULLIF(x.reltablespace,0),
                                                   d.dattablespace)
           WHERE tn.nspname='public' AND x.relkind='i' ORDER BY x.oid
  LOOP
    IF floor(extract(epoch FROM clock_timestamp())*1000000) >= deadline_unix_us THEN
      RAISE EXCEPTION USING ERRCODE='57014', MESSAGE='native_guard_deadline';
    END IF;
    IF NOT r.indisvalid OR NOT r.indisready
       OR r.amname NOT IN ('btree','gin','gist','hnsw','bm25') THEN
      RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_index_format';
    END IF;
    EXECUTE format('ALTER INDEX %I.%I SET TABLESPACE %I',r.nspname,r.relname,r.spcname);
  END LOOP;

  SELECT array_agg(c.oid ORDER BY c.oid) INTO physical_oids
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relkind IN ('r','m');
  before_us := floor(extract(epoch FROM clock_timestamp())*1000000);
  WITH RECURSIVE tree AS (
    SELECT c.oid,c.relname::text AS root FROM pg_class c
      JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p','m')
        AND NOT EXISTS(SELECT 1 FROM pg_inherits h WHERE h.inhrelid=c.oid)
    UNION ALL
    SELECT c.oid,t.root FROM tree t JOIN pg_inherits h ON h.inhparent=t.oid
      JOIN pg_class c ON c.oid=h.inhrelid
  ), heaps AS (
    SELECT t.oid,t.root,c.reltoastrelid FROM tree t
      JOIN pg_class c ON c.oid=t.oid WHERE t.oid=ANY(physical_oids)
  ), resources AS (
    SELECT oid,root,'table'::text AS kind FROM heaps
    UNION ALL SELECT i.indexrelid,h.root,'index' FROM heaps h
      JOIN pg_index i ON i.indrelid=h.oid JOIN pg_class c ON c.oid=i.indexrelid
      WHERE c.relkind='i'
    UNION ALL SELECT reltoastrelid,root,'toast' FROM heaps WHERE reltoastrelid<>0
    UNION ALL SELECT i.indexrelid,h.root,'toast_index' FROM heaps h
      JOIN pg_index i ON i.indrelid=h.reltoastrelid WHERE h.reltoastrelid<>0
  ), sizes AS (
    SELECT r.*,pg_relation_size(oid,'main') AS main,
           pg_relation_size(oid,'fsm') AS fsm,pg_relation_size(oid,'vm') AS vm,
           pg_relation_size(oid,'init') AS init FROM resources r
  )
  SELECT jsonb_agg(jsonb_build_object('resource_kind',kind,'root_relation',root,
           'relation_oid',oid::text,'main_bytes',main::text,'fsm_bytes',fsm::text,
           'vm_bytes',vm::text,'init_bytes',init::text) ORDER BY root,kind,oid),
         sum(main::numeric+fsm+vm+init)
    INTO samples,fork_total FROM sizes;
  SELECT sum(pg_total_relation_size(oid)::numeric) INTO table_total
    FROM unnest(physical_oids) t(oid);
  after_us := floor(extract(epoch FROM clock_timestamp())*1000000);
  IF samples IS NULL OR fork_total IS DISTINCT FROM table_total THEN
    RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_guard_size_reconciliation';
  END IF;
  IF after_us >= deadline_unix_us THEN
    RAISE EXCEPTION USING ERRCODE='57014', MESSAGE='native_guard_deadline';
  END IF;
  RETURN jsonb_build_object('contract','rememberstack.native_relation_guard.v1',
    'deployment_id',native_scope::text,'schema_head','p9_46_0067',
    'session_login',session_user,'definer_role',current_user,
    'backend_pid',pg_backend_pid(),'transaction_id',pg_current_xact_id()::text,
    'guard_started_unix_us',started_us::text,'sample_before_unix_us',before_us::text,
    'sample_after_unix_us',after_us::text,'allocated_fork_bytes',fork_total::text,
    'physical_table_total_bytes',table_total::text,'relations',samples);
END
"""


def install_native_relation_guard(
    *, connection: psycopg.Connection, definer_role: str, collector_role: str
) -> None:
    """Install the fixed operation using existing, explicitly provisioned roles.

    No LOGIN, membership or secret is created. The definer must already inherit
    the actual engine owner's authority; the ordinary collector must not. This
    installer is never called by capture, API requests or normal engine setup.
    """
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise NativeRelationGuardRefused("native_guard_installer_transaction")
    if not definer_role or not collector_role or definer_role == collector_role:
        raise NativeRelationGuardRefused("native_guard_installer_roles")
    with connection.transaction():
        rows = connection.execute(
            "SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,"
            "rolreplication,rolbypassrls FROM pg_roles WHERE rolname=ANY(%s)",
            ([definer_role, collector_role],),
        ).fetchall()
        roles = {row[0]: row[1:] for row in rows}
        if roles.get(definer_role) != (False, False, False, False, False, False):
            raise NativeRelationGuardRefused("native_guard_definer_authority")
        if roles.get(collector_role) != (True, False, False, False, False, False):
            raise NativeRelationGuardRefused("native_guard_collector_authority")
        authority = connection.execute(
            "SELECT pg_has_role(%s,c.relowner,'USAGE'),"
            "pg_has_role(%s,c.relowner,'MEMBER'),pg_has_role(%s,%s,'MEMBER') "
            "FROM pg_class c WHERE c.oid='public.deployments'::regclass",
            (definer_role, collector_role, collector_role, definer_role),
        ).fetchone()
        if authority != (True, False, False):
            raise NativeRelationGuardRefused("native_guard_role_boundary")
        existing = connection.execute(
            "SELECT nspowner=(SELECT oid FROM pg_roles WHERE rolname=%s) "
            "FROM pg_namespace WHERE nspname=%s",
            (definer_role, _SCHEMA),
        ).fetchone()
        if existing is not None and existing != (True,):
            raise NativeRelationGuardRefused("native_guard_schema_owner")
        connection.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(
                sql.Identifier(_SCHEMA), sql.Identifier(definer_role)
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(
                sql.Identifier(_SCHEMA)
            )
        )
        connection.execute(
            sql.SQL("GRANT MAINTAIN ON {} TO {}").format(
                sql.SQL(",").join(
                    sql.Identifier("pg_catalog", name) for name in _CATALOGS
                ),
                sql.Identifier(definer_role),
            )
        )
        body = _BODY.replace(
            "__ROOTS__", sql.Literal(list(_ROOTS)).as_string(connection)
        ).replace(
            "__EXTENSIONS__", sql.Literal(list(_EXTENSIONS)).as_string(connection)
        )
        function = sql.Identifier(_SCHEMA, _FUNCTION)
        connection.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION {}(deadline_unix_us bigint) RETURNS jsonb "
                "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog AS {}"
            ).format(function, sql.Literal(body))
        )
        connection.execute(
            sql.SQL("ALTER FUNCTION {}(bigint) OWNER TO {}").format(
                function, sql.Identifier(definer_role)
            )
        )
        connection.execute(
            sql.SQL("REVOKE ALL ON FUNCTION {}(bigint) FROM PUBLIC").format(function)
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(_SCHEMA), sql.Identifier(collector_role)
            )
        )
        connection.execute(
            sql.SQL("GRANT EXECUTE ON FUNCTION {}(bigint) TO {}").format(
                function, sql.Identifier(collector_role)
            )
        )
