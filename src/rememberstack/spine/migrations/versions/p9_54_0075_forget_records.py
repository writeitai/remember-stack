"""D159 forget records: hide at once, sweep until clean.

``forget_manifests`` (D74) becomes ``forgets``: one immutable, content-free
record per forgotten lineage, and the only forget state. It holds the
document's ID, its version IDs, content hashes and source-identity hash, the
content-free IDs a sweep collects, and the sweep's visible progress. The
``hard_forget`` stage becomes ``forget`` and its work item targets the record
(``processing_target`` gains ``forget``).

Upgrade: a complete D74 forget becomes a ``complete`` record. An unfinished
one (``preparing`` or ``accepted``) becomes ``erasing`` with the hide applied
(the lineage and its versions tombstoned) and a forget job enqueued; the job
finishes the lineage cascade before its first sweep. Old ``hard_forget`` work
rows still queued are skipped. Metering receipts lose their foreign key to the
version, so a sweep can delete the version while the receipt keeps its counts
and the version's UUID as billing metadata.
"""

from collections.abc import Sequence
import hashlib
import json
import re
from uuid import uuid4

from alembic import op
from sqlalchemy import text

from rememberstack.spine.migrations._helpers import apply_ddl
from rememberstack.spine.migrations._helpers import drop_tables
from rememberstack.spine.migrations._helpers import drop_types

revision: str = "p9_54_0075"
down_revision: str | None = "p9_53_0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FORGET_COMPONENT_VERSION = "forget-v1"
"""The sweep generation recorded on every forget work row (D159)."""

_DDL = r"""
CREATE TYPE forget_status AS ENUM ('erasing', 'waiting_for_redaction', 'complete');

CREATE TABLE forgets (
  forget_id             uuid PRIMARY KEY,
  deployment_id         uuid NOT NULL REFERENCES deployments,
  doc_id                uuid NOT NULL,               -- the forgotten lineage; no FK, the document row is erased
  version_ids           uuid[] NOT NULL,             -- every version of the lineage at hide
  content_hashes        text[] NOT NULL,             -- every version's raw content hash; arms the re-ingest guard
  source_identity_hash  text,                        -- sha256 of (deployment_id, source_kind, source_ref); NULL for uploads
  collected             jsonb,                       -- content-free IDs and opaque keys every sweep adds: shared facts and entities, K artifacts, original object keys
  blocking_k_paths      text[] NOT NULL DEFAULT '{}', -- authored K paths still citing the document; emptied on completion
  released_hashes       text[] NOT NULL DEFAULT '{}', -- guard entries released by a deliberate re-add; never removes sweep keys
  identity_released     boolean NOT NULL DEFAULT false, -- the source identity was released by a deliberate re-add
  last_error            text,                        -- content-free code of the last failed sweep, for visibility
  status                forget_status NOT NULL DEFAULT 'erasing',
  requested_at          timestamptz NOT NULL DEFAULT now(),
  completed_at          timestamptz,
  UNIQUE (deployment_id, doc_id),
  CHECK ((status = 'complete') = (completed_at IS NOT NULL))
);
COMMENT ON TABLE forgets IS
  'D159 forget records: one per forgotten lineage, IDs and hashes only, never text. The only forget state: the re-ingest guard reads it, the sweep is keyed by it, and forget-reapply re-runs the sweep from it.';
CREATE INDEX ix_forgets_source ON forgets (deployment_id, source_identity_hash)
  WHERE source_identity_hash IS NOT NULL;
CREATE INDEX ix_forgets_content ON forgets USING gin (content_hashes);
"""


def upgrade() -> None:
    """Create the record table, convert D74 manifests and drop D74's state."""
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE processing_target ADD VALUE IF NOT EXISTS 'forget'")
    op.execute("ALTER TYPE pipeline_stage RENAME VALUE 'hard_forget' TO 'forget'")
    apply_ddl(sql=_DDL)
    op.execute("""
        ALTER TABLE managed_ingest_measurements
        DROP CONSTRAINT IF EXISTS managed_ingest_measurements_deployment_id_doc_id_version_i_fkey
    """)
    connection = op.get_bind()
    for row in connection.execute(
        text("""
        SELECT m.forget_id, m.deployment_id, m.doc_id, m.manifest,
               m.source_identity_hash, m.content_hashes, m.status::text AS status,
               m.prepared_at, m.completed_at, d.source_kind, d.source_ref
        FROM forget_manifests m
        LEFT JOIN documents d
          ON d.deployment_id = m.deployment_id AND d.doc_id = m.doc_id
        ORDER BY m.prepared_at, m.forget_id
        """)
    ).mappings():
        _convert(connection=connection, row=row)
    op.execute("""
        UPDATE processing_state
        SET status = 'skipped', defer_reason = NULL, lease_until = NULL,
            finished_at = now()
        WHERE stage = 'forget' AND target_kind <> 'forget'
          AND status IN ('pending', 'failed', 'running')
    """)
    drop_tables(table_names=("forget_manifests",))
    drop_types(type_names=("forget_manifest_status",))


def downgrade() -> None:
    """Refuse while records exist: D74 manifests cannot be re-derived from them."""
    connection = op.get_bind()
    if connection.execute(text("SELECT EXISTS (SELECT 1 FROM forgets)")).scalar():
        raise RuntimeError(
            "D159 downgrade requires an explicitly reviewed plan: forgets holds"
            " forget records that D74 manifests cannot represent"
        )
    drop_tables(table_names=("forgets",))
    drop_types(type_names=("forget_status",))
    op.execute("ALTER TYPE pipeline_stage RENAME VALUE 'forget' TO 'hard_forget'")
    op.execute("""
        CREATE TYPE forget_manifest_status AS ENUM ('preparing','accepted','complete');
        CREATE TABLE forget_manifests (
          forget_id uuid PRIMARY KEY,
          deployment_id uuid NOT NULL REFERENCES deployments,
          doc_id uuid NOT NULL,
          schema_version smallint NOT NULL,
          manifest_hash text,
          manifest jsonb,
          source_identity_hash text,
          content_hashes text[] NOT NULL DEFAULT '{}',
          status forget_manifest_status NOT NULL DEFAULT 'preparing',
          prepared_at timestamptz NOT NULL DEFAULT now(),
          accepted_at timestamptz,
          completed_at timestamptz,
          last_verified_at timestamptz,
          UNIQUE (deployment_id, doc_id),
          UNIQUE (deployment_id, forget_id),
          CHECK ((status = 'preparing') OR
                 (manifest_hash IS NOT NULL AND manifest IS NOT NULL
                  AND accepted_at IS NOT NULL)),
          CHECK ((status = 'complete') = (completed_at IS NOT NULL))
        );
        CREATE INDEX ix_forget_source_guard
          ON forget_manifests (deployment_id, source_identity_hash)
          WHERE source_identity_hash IS NOT NULL;
        CREATE INDEX ix_forget_content_guard
          ON forget_manifests USING gin (content_hashes);
        ALTER TABLE managed_ingest_measurements
          ADD CONSTRAINT managed_ingest_measurements_deployment_id_doc_id_version_i_fkey
          FOREIGN KEY (deployment_id, doc_id, version_id)
          REFERENCES document_versions (deployment_id, doc_id, version_id)
          ON DELETE RESTRICT NOT VALID;
    """)


_LINEAGE_ORIGINAL = re.compile(r"(?P<owner>[0-9a-f-]{36})/(?P<hash>[0-9a-f]{64})/.+")


def _locator(*, key: str, doc_id: str) -> str | None:
    """The record's content-free locator for a D74 object key (as live)."""
    if key.startswith(f"{doc_id}/"):
        return None
    if match := _LINEAGE_ORIGINAL.fullmatch(key):
        return f"{match['owner']}/{match['hash']}/"
    return key


def _convert(*, connection, row) -> None:  # noqa: ANN001
    """Turn one D74 manifest row into a D159 record (design §4 Upgrade)."""
    manifest = row["manifest"] or {}
    params = {"d": row["deployment_id"], "doc": row["doc_id"]}
    version_ids = list(
        connection.execute(
            text("""SELECT version_id FROM document_versions
            WHERE deployment_id = :d AND doc_id = :doc ORDER BY version_id"""),
            params,
        ).scalars()
    )
    hashes = set(row["content_hashes"] or ()) | set(
        connection.execute(
            text("""SELECT content_hash FROM document_versions
            WHERE deployment_id = :d AND doc_id = :doc"""),
            params,
        ).scalars()
    )
    identity = row["source_identity_hash"]
    # The live rule: a one-shot upload's lineage is its bytes (source_ref is
    # one of its content hashes); any other source_ref is an identity.
    one_shot = row["source_kind"] == "upload" and row["source_ref"] in hashes
    if identity is None and row["source_ref"] is not None and not one_shot:
        # The D74/D159 source-identity hash, frozen here so this migration
        # never changes with application code.
        identity = hashlib.sha256(
            json.dumps(
                [str(row["deployment_id"]), row["source_kind"], row["source_ref"]],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
    collected = {
        "fact_ids": sorted(set(manifest.get("fact_ids", ()))),
        "entity_ids": sorted(
            set(manifest.get("entity_ids", ()))
            | set(manifest.get("resolved_entity_ids", ()))
        ),
        "k_artifact_ids": sorted(manifest.get("k_artifact_ids", ())),
        "object_keys": sorted(
            {
                locator
                for key in manifest.get("object_keys", ())
                if (locator := _locator(key=str(key), doc_id=str(row["doc_id"])))
            }
        ),
    }
    # D74 named every K artifact of the deployment; keep only compiled pages
    # (authored pages are their owners' and never purged on the record's say).
    collected["k_artifact_ids"] = sorted(
        str(value)
        for value in connection.execute(
            text("""SELECT artifact_id FROM knowledge_artifacts
            WHERE deployment_id = :d AND page_kind = 'compiled'
              AND artifact_id = ANY(CAST(:ids AS uuid[]))"""),
            {**params, "ids": collected["k_artifact_ids"]},
        ).scalars()
    )
    complete = row["status"] == "complete"
    if not complete:
        # D74 may have scrubbed the citations before it rebuilt the pages or
        # erased their history: the first sweeps rebuild every one it named.
        collected["k_pending"] = collected["k_artifact_ids"]
    requested_at = manifest.get("requested_at") or row["prepared_at"]
    connection.execute(
        text("""
        INSERT INTO forgets (
          forget_id, deployment_id, doc_id, version_ids, content_hashes,
          source_identity_hash, collected, status, requested_at, completed_at
        ) VALUES (
          :forget_id, :d, :doc, CAST(:version_ids AS uuid[]),
          CAST(:hashes AS text[]), :identity, CAST(:collected AS jsonb),
          CAST(:status AS forget_status), CAST(:requested_at AS timestamptz),
          :completed_at
        )
        """),
        {
            **params,
            "forget_id": row["forget_id"],
            "version_ids": version_ids,
            "hashes": sorted(hashes),
            "identity": identity,
            "collected": json.dumps(collected, sort_keys=True),
            "status": "complete" if complete else "erasing",
            "requested_at": requested_at,
            "completed_at": (row["completed_at"] or row["prepared_at"])
            if complete
            else None,
        },
    )
    if complete:
        return
    # The hide: tombstone the lineage and every version. The job's first
    # sweep finishes the lineage cascade under the lineage row lock.
    connection.execute(
        text("""UPDATE documents SET deleted_at = COALESCE(deleted_at, now())
        WHERE deployment_id = :d AND doc_id = :doc"""),
        params,
    )
    connection.execute(
        text("""UPDATE document_versions SET deleted_at = COALESCE(deleted_at, now())
        WHERE deployment_id = :d AND doc_id = :doc"""),
        params,
    )
    connection.execute(
        text("""
        INSERT INTO processing_state (
          processing_id, deployment_id, target_kind, target_id, stage,
          component_version, content_hash, lane, payload, max_attempts
        ) VALUES (
          :processing_id, :d, 'forget', :forget_id, 'forget', :version,
          :content_hash, NULL, CAST(:payload AS jsonb), 32767
        ) ON CONFLICT DO NOTHING
        """),
        {
            **params,
            "processing_id": uuid4(),
            "forget_id": row["forget_id"],
            "version": f"{FORGET_COMPONENT_VERSION}/1",
            "content_hash": str(row["forget_id"]),
            "payload": json.dumps(
                {
                    "forget_id": str(row["forget_id"]),
                    "sweep": 1,
                    "failures": 0,
                    "first": True,
                    "reapply": False,
                    "series": "",
                }
            ),
        },
    )
