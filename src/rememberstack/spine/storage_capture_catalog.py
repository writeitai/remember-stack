"""Fixed, private catalog reads from the original D152 PostgreSQL view.

These are native reference seeds, not classified storage members or proof of
store completeness. Locators and native content identities stay inside the
engine. The store adapter must resolve them against its qualified cut before
any content-free member or component amount can be published.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import SecretStr

from rememberstack.model.metering import ManagedDocumentVersionOutcome
from rememberstack.model.storage_capture import UnsignedDecimal
from rememberstack.spine.storage_capture_guard import NativeRelationGuardRefused

CATALOG_FUNCTION = "rememberstack_capture_internal.read_capture_catalog"
CATALOG_PAGE_LIMIT = 1024
_SIGNATURE = f"{CATALOG_FUNCTION}(text,text,text,integer)"


class _NativeRecord(BaseModel):
    """Keep native observations closed and suppress private validation inputs."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class NativeCatalogIdentity(_NativeRecord):
    """Actual session, virtual transaction and MVCC snapshot, without handoff."""

    session_login: str
    database_name: str
    database_oid: UnsignedDecimal
    postmaster_started_unix_us: UnsignedDecimal
    backend_pid: int = Field(gt=0)
    virtual_transaction_id: str
    snapshot_identity: str


class NativeStorageConfiguration(_NativeRecord):
    """Private native store roots; these do not repair an incomplete binding."""

    raw_bucket: SecretStr = Field(repr=False)
    artifacts_bucket: SecretStr = Field(repr=False)
    corpusfs_bucket: SecretStr = Field(repr=False)
    knowledge_repo_uri: SecretStr | None = Field(repr=False)


class NativeCatalogAcquisition(_NativeRecord):
    """Original session plus native scope materialised only after release."""

    identity: NativeCatalogIdentity
    deployment_id: UUID | None
    storage: NativeStorageConfiguration | None


class NativeStoreReference(_NativeRecord):
    """One native store locator or journal intent, including unfinished state.

    A tree/repository locator is a root to expand through qualified store
    evidence, never a fabricated object or a row-byte estimate. Only raw
    objects record a native length; missing length remains explicit. A journal
    intent carries the actual forget UUID, not an invented store path. Its
    qualified adapter derives the concrete journal key and proves presence.
    """

    native_class: str
    native_id: SecretStr = Field(repr=False)
    store_role: Literal["raw", "artifacts", "corpus", "forget", "knowledge"]
    locator: SecretStr = Field(repr=False)
    locator_kind: Literal["object", "tree", "repository_path", "journal_intent"]
    recorded_bytes: UnsignedDecimal | None
    native_state: str | None
    recorded_digest: SecretStr | None = Field(repr=False)


class NativeReferencePage(_NativeRecord):
    """A bounded, strictly ordered page from the same retained view."""

    identity: NativeCatalogIdentity
    deployment_id: UUID
    rows: tuple[NativeStoreReference, ...]


class NativeTerminalPage(_NativeRecord):
    """Existing v3 terminal bodies, with no processing-delivery filter."""

    identity: NativeCatalogIdentity
    deployment_id: UUID
    rows: tuple[ManagedDocumentVersionOutcome, ...]


_BODY = r"""
DECLARE
  native_scope uuid;
  native_identity jsonb;
  page jsonb;
BEGIN
  IF session_user <> __COLLECTOR__ THEN
    RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='native_catalog_login';
  END IF;
  IF current_setting('transaction_isolation') <> 'repeatable read'
     OR current_setting('transaction_read_only') <> 'on' THEN
    RAISE EXCEPTION USING ERRCODE='25001', MESSAGE='native_catalog_transaction';
  END IF;
  IF page_limit IS NULL OR page_limit < 1 OR page_limit > 1024
     OR after_class IS NULL OR after_id IS NULL THEN
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='native_catalog_page';
  END IF;
  IF section = 'acquire' THEN
    IF after_class <> '' OR after_id <> '' THEN
      RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='native_catalog_cursor';
    END IF;
    LOCK TABLE public.deployments, public.alembic_version,
      public.content_objects, public.document_representations,
      public.document_structure_generations, public.document_reference_generations,
      public.projection_snapshots, public.knowledge_artifacts,
      public.knowledge_compilations, public.knowledge_plan_runs,
      public.forget_manifests, public.managed_ingest_measurements,
      public.managed_ingest_outcomes IN ACCESS SHARE MODE NOWAIT;
  ELSE
    IF (SELECT count(*) FROM public.deployments) <> 1
       OR (SELECT count(*) FROM public.alembic_version) <> 1
       OR (SELECT version_num FROM public.alembic_version) <> 'p9_46_0067' THEN
      RAISE EXCEPTION USING ERRCODE='55000', MESSAGE='native_catalog_scope';
    END IF;
    SELECT deployment_id INTO STRICT native_scope FROM public.deployments;
  END IF;
  SELECT jsonb_build_object(
    'session_login', session_user,
    'database_name', current_database(),
    'database_oid', (SELECT oid::text FROM pg_database WHERE datname=current_database()),
    'postmaster_started_unix_us',
      floor(extract(epoch FROM pg_postmaster_start_time())*1000000)::text,
    'backend_pid', pg_backend_pid(),
    'virtual_transaction_id',
      (SELECT virtualtransaction FROM pg_locks WHERE pid=pg_backend_pid()
       AND locktype='virtualxid' AND mode='ExclusiveLock'),
    'snapshot_identity', pg_current_snapshot()::text) INTO native_identity;
  IF section = 'acquire' THEN
    RETURN jsonb_build_object('identity',native_identity,'deployment_id',NULL,'storage',NULL);
  ELSIF section = 'bind' THEN
    IF after_class <> '' OR after_id <> '' THEN
      RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='native_catalog_cursor';
    END IF;
    RETURN jsonb_build_object('identity',native_identity,'deployment_id',native_scope::text,'storage',
      (SELECT jsonb_build_object('raw_bucket',raw_bucket,
        'artifacts_bucket',artifacts_bucket,'corpusfs_bucket',corpusfs_bucket,
        'knowledge_repo_uri',knowledge_repo_uri) FROM public.deployments));
  ELSIF section = 'identity' THEN
    IF after_class <> '' OR after_id <> '' THEN
      RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='native_catalog_cursor';
    END IF;
    page := '[]'::jsonb;
  ELSIF section = 'references' THEN
    WITH native_references AS (
      SELECT 'content_objects.raw_uri'::text AS native_class,
        content_hash AS native_id,'raw'::text AS store_role,raw_uri AS locator,
        'object'::text AS locator_kind,byte_size::text AS recorded_bytes,
        NULL::text AS native_state,content_hash AS recorded_digest FROM public.content_objects
        WHERE deployment_id=native_scope
      UNION ALL
      SELECT 'document_representations.'||u.field,r.representation_id::text,
        'artifacts',u.locator,'object',NULL,r.status,
        CASE u.field WHEN 'conversion_uri' THEN r.manifest_hash
          WHEN 'markdown_uri' THEN r.markdown_hash
          WHEN 'pageindex_uri' THEN r.pageindex_hash ELSE NULL END
        FROM public.document_representations r
        CROSS JOIN LATERAL (VALUES ('markdown_uri',r.markdown_uri),
          ('pageindex_uri',r.pageindex_uri),('conversion_uri',r.conversion_uri),
          ('blocks_uri',r.blocks_uri),('meta_uri',r.meta_uri)) u(field,locator)
        WHERE r.deployment_id=native_scope AND u.locator IS NOT NULL
      UNION ALL
      SELECT 'document_structure_generations.pageindex_uri',
        structure_generation_id::text,'artifacts',pageindex_uri,'object',NULL,NULL,NULL
        FROM public.document_structure_generations
        WHERE deployment_id=native_scope AND pageindex_uri IS NOT NULL
      UNION ALL
      SELECT 'document_reference_generations.artifact_uri',generation_id::text,
        'artifacts',artifact_uri,'object',NULL,status,NULL
        FROM public.document_reference_generations
        WHERE deployment_id=native_scope AND artifact_uri IS NOT NULL
      UNION ALL
      SELECT CASE WHEN plane::text='P3_corpusfs' THEN 'projection_snapshots.gcs_uri'
          ELSE 'projection_snapshots.unsupported_origin' END,
        snapshot_id::text,'corpus',gcs_uri,
        'tree',NULL,status::text,NULL FROM public.projection_snapshots
        WHERE deployment_id=native_scope
      UNION ALL
      SELECT 'knowledge_artifacts.'||u.field,k.artifact_id::text,'knowledge',
        u.locator,'repository_path',NULL,k.status::text,NULL
        FROM public.knowledge_artifacts k
        CROSS JOIN LATERAL (VALUES ('git_path',k.git_path),
          ('curation_path',k.curation_path)) u(field,locator)
        WHERE k.deployment_id=native_scope AND u.locator IS NOT NULL
      UNION ALL
      SELECT 'knowledge_compilations.session_transcript_uri',compilation_id::text,
        'artifacts',session_transcript_uri,'object',NULL,NULL,NULL
        FROM public.knowledge_compilations
        WHERE deployment_id=native_scope AND session_transcript_uri IS NOT NULL
      UNION ALL
      SELECT 'knowledge_plan_runs.session_transcript_uri',run_id::text,
        'artifacts',session_transcript_uri,'object',NULL,status,NULL
        FROM public.knowledge_plan_runs WHERE deployment_id=native_scope
      UNION ALL
      SELECT 'forget_manifests.portable_intent',forget_id::text,'forget',
        forget_id::text,'journal_intent',NULL,status::text,NULL
        FROM public.forget_manifests
        WHERE deployment_id=native_scope
    ), bounded AS (
      SELECT * FROM native_references WHERE
        (native_class COLLATE "C",native_id COLLATE "C") >
        (after_class COLLATE "C",after_id COLLATE "C")
      ORDER BY native_class COLLATE "C",native_id COLLATE "C" LIMIT page_limit
    ) SELECT COALESCE(jsonb_agg(to_jsonb(bounded) ORDER BY
        native_class COLLATE "C",native_id COLLATE "C"),'[]'::jsonb)
        INTO page FROM bounded;
  ELSIF section = 'terminals' THEN
    IF after_class <> '' THEN
      RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='native_catalog_cursor';
    END IF;
    SELECT COALESCE(jsonb_agg(to_jsonb(bounded) ORDER BY measurement_id),'[]'::jsonb)
      INTO page FROM (
        SELECT o.measurement_id,o.document_version_id,o.outcome,o.completed_at,
          o.reason_code,o.profile_complete,o.version_commit_sequence
        FROM public.managed_ingest_outcomes o
        JOIN public.managed_ingest_measurements m USING (measurement_id)
        WHERE m.deployment_id=native_scope AND
          o.measurement_id::text COLLATE "C" > after_id COLLATE "C"
        ORDER BY o.measurement_id LIMIT page_limit) bounded;
  ELSE
    RAISE EXCEPTION USING ERRCODE='22023', MESSAGE='native_catalog_section';
  END IF;
  RETURN jsonb_build_object('identity',native_identity,
    'deployment_id',native_scope::text,'rows',page);
END
"""


def install_native_capture_catalog(
    *, connection: psycopg.Connection, definer_role: str, collector_role: str
) -> None:
    """Install fixed reads using the guard's existing roles and private schema.

    No role, membership, table SELECT grant or secret is created. This trusted
    operator operation is not called by a capture request or engine startup.
    """
    if connection.info.transaction_status != TransactionStatus.IDLE:
        raise NativeRelationGuardRefused("native_catalog_installer_transaction")
    with connection.transaction():
        boundary = connection.execute(
            "SELECT r.rolname=%s,has_function_privilege(%s,p.oid,'EXECUTE'),"
            "n.nspowner=p.proowner FROM pg_catalog.pg_proc p "
            "JOIN pg_catalog.pg_roles r ON r.oid=p.proowner "
            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
            "WHERE p.oid=to_regprocedure("
            "'rememberstack_capture_internal.guard_relation_allocation(bigint)')",
            (definer_role, collector_role),
        ).fetchone()
        if boundary != (True, True, True) or definer_role == collector_role:
            raise NativeRelationGuardRefused("native_catalog_installer_boundary")
        body = _BODY.replace(
            "__COLLECTOR__", sql.Literal(collector_role).as_string(connection)
        )
        function = sql.Identifier(
            "rememberstack_capture_internal", "read_capture_catalog"
        )
        connection.execute(
            sql.SQL(
                "CREATE OR REPLACE FUNCTION {}(section text,after_class text,"
                "after_id text,page_limit integer) RETURNS jsonb LANGUAGE plpgsql "
                "SECURITY DEFINER SET search_path=pg_catalog,pg_temp SET timezone='UTC' AS {}"
            ).format(function, sql.Literal(body))
        )
        connection.execute(
            sql.SQL("ALTER FUNCTION {}(text,text,text,integer) OWNER TO {}").format(
                function, sql.Identifier(definer_role)
            )
        )
        connection.execute(
            sql.SQL(
                "REVOKE ALL ON FUNCTION {}(text,text,text,integer) FROM PUBLIC"
            ).format(function)
        )
        old_grantees = connection.execute(
            "SELECT DISTINCT r.rolname FROM pg_catalog.pg_proc p "
            "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(p.proacl,"
            "pg_catalog.acldefault('f',p.proowner))) a "
            "JOIN pg_catalog.pg_roles r ON r.oid=a.grantee "
            "WHERE p.oid=to_regprocedure(%s) AND a.grantee<>p.proowner",
            (_SIGNATURE,),
        ).fetchall()
        for (old_grantee,) in old_grantees:
            connection.execute(
                sql.SQL(
                    "REVOKE ALL ON FUNCTION {}(text,text,text,integer) FROM {}"
                ).format(function, sql.Identifier(old_grantee))
            )
        connection.execute(
            sql.SQL(
                "GRANT EXECUTE ON FUNCTION {}(text,text,text,integer) TO {}"
            ).format(function, sql.Identifier(collector_role))
        )
