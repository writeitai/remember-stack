"""PostgreSQL materialization, admission, and inventory for D74."""

from datetime import datetime
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy import TextClause
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core import source_identity_hash
from rememberstack.model import ComponentVersionConflictError
from rememberstack.model import EnqueueOutcome
from rememberstack.model import EnqueueWork
from rememberstack.model import ForgetInProgressError
from rememberstack.model import ForgetManifest
from rememberstack.model import ForgetManifestConflictError
from rememberstack.model import ForgetManifestNotFoundError
from rememberstack.model import ForgetManifestRecord
from rememberstack.model import ForgetManifestStatus
from rememberstack.model import ForgetTargetNotFoundError
from rememberstack.model import ForgottenSourceError
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.spine.admission import active_forget_id_on
from rememberstack.spine.work_ledger import enqueue_on

HARD_FORGET_COMPONENT_VERSION = "hard-forget-v1"
"""The deterministic D74 coordinator generation recorded on its one work row."""


class ForgetCatalog:
    """Own local forget admission, inventory, progress, and exact ingest guards."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the guard catalog to the authoritative spine database."""
        self._engine = engine

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Refuse traffic while a preparing or accepted forget is incomplete."""
        with self._engine.connect() as connection:
            forget_id = active_forget_id_on(
                connection=connection, deployment_id=deployment_id
            )
        if forget_id is not None:
            raise ForgetInProgressError(
                f"deployment {deployment_id} is honoring forget_id {forget_id}"
            )

    def prepare(
        self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID
    ) -> ForgetManifestRecord:
        """Establish one deployment-wide preparing barrier idempotently."""
        with self._engine.begin() as connection:
            connection.execute(_LOCK_DEPLOYMENT, {"deployment_id": deployment_id})
            active_id = active_forget_id_on(
                connection=connection, deployment_id=deployment_id
            )
            if active_id is not None:
                active = (
                    connection.execute(
                        _SELECT_BY_ID,
                        {"deployment_id": deployment_id, "forget_id": active_id},
                    )
                    .mappings()
                    .one()
                )
                if active["forget_id"] == forget_id and active["doc_id"] == doc_id:
                    return _record(row=active)
                raise ForgetInProgressError(
                    f"deployment {deployment_id} is already honoring forget_id"
                    f" {active_id}"
                )
            existing = (
                connection.execute(
                    _SELECT_BY_DOC, {"deployment_id": deployment_id, "doc_id": doc_id}
                )
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["forget_id"] != forget_id:
                    raise ForgetManifestConflictError(
                        f"document {doc_id} already belongs to forget_id"
                        f" {existing['forget_id']}"
                    )
                return _record(row=existing)
            owned = (
                connection.execute(
                    _SELECT_DOCUMENT, {"deployment_id": deployment_id, "doc_id": doc_id}
                )
                .mappings()
                .first()
            )
            if owned is None:
                raise ForgetTargetNotFoundError(
                    f"deployment {deployment_id} does not own document {doc_id}"
                )
            row = (
                connection.execute(
                    _INSERT_PREPARING,
                    {
                        "forget_id": forget_id,
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                    },
                )
                .mappings()
                .one()
            )
            return _record(row=row)

    def ordinary_work_is_drained(self, *, deployment_id: UUID) -> bool:
        """Return whether every already-running non-forget handler has finished."""
        with self._engine.connect() as connection:
            return bool(
                connection.execute(
                    _ORDINARY_WORK_DRAINED, {"deployment_id": deployment_id}
                ).scalar_one()
            )

    def record_for_doc(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> ForgetManifestRecord | None:
        """Return existing local progress for an idempotent request retry."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _SELECT_BY_DOC, {"deployment_id": deployment_id, "doc_id": doc_id}
                )
                .mappings()
                .first()
            )
        return _record(row=row) if row is not None else None

    def preparing_record(self, *, deployment_id: UUID) -> ForgetManifestRecord | None:
        """Return the single crash-recoverable preparing request, when present."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(_SELECT_PREPARING, {"deployment_id": deployment_id})
                .mappings()
                .one_or_none()
            )
        return _record(row=row) if row is not None else None

    def materialize_portable(self, *, manifest: ForgetManifest) -> None:
        """Recreate or validate the local barrier from portable restore intent."""
        parameters = {
            "forget_id": manifest.forget_id,
            "deployment_id": manifest.deployment_id,
            "doc_id": manifest.doc_id,
            "schema_version": manifest.schema_version,
            "manifest_hash": manifest.sha256(),
            "manifest": manifest.canonical_bytes().decode("utf-8"),
            "source_identity_hash": manifest.source_identity_hash,
            "content_hashes": list(manifest.content_hashes),
        }
        with self._engine.begin() as connection:
            connection.execute(
                _LOCK_DEPLOYMENT, {"deployment_id": manifest.deployment_id}
            )
            by_document = (
                connection.execute(
                    _SELECT_BY_DOC,
                    {
                        "deployment_id": manifest.deployment_id,
                        "doc_id": manifest.doc_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if (
                by_document is not None
                and by_document["forget_id"] != manifest.forget_id
            ):
                raise ForgetManifestConflictError(
                    f"document {manifest.doc_id} already belongs to forget_id"
                    f" {by_document['forget_id']}"
                )
            row = (
                connection.execute(
                    _SELECT_BY_ID_FOR_UPDATE,
                    {
                        "deployment_id": manifest.deployment_id,
                        "forget_id": manifest.forget_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                connection.execute(_INSERT_PORTABLE, parameters)
                return
            if row["doc_id"] != manifest.doc_id:
                raise ForgetManifestConflictError(
                    f"forget_id {manifest.forget_id} targets a different document"
                )
            if row["manifest"] is not None:
                _require_same_manifest(row=row, manifest=manifest)
                return
            connection.execute(_MATERIALIZE_PREPARING, parameters)

    def blocking_k_paths(self, *, deployment_id: UUID, doc_id: UUID) -> tuple[str, ...]:
        """Return owner-controlled paths whose synced citations still reach a lineage."""
        with self._engine.connect() as connection:
            values = connection.execute(
                _BLOCKING_K_PATHS, {"deployment_id": deployment_id, "doc_id": doc_id}
            ).scalars()
            return tuple(sorted({str(value) for value in values}))

    def k_paths_for_artifacts(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> tuple[str, ...]:
        """Return exact body and curation paths nominated for Git history purge."""
        if not artifact_ids:
            return ()
        with self._engine.connect() as connection:
            values = connection.execute(
                _K_PATHS_FOR_ARTIFACTS,
                {"deployment_id": deployment_id, "artifact_ids": list(artifact_ids)},
            ).scalars()
            return tuple(sorted({str(value) for value in values if value is not None}))

    def inventory_and_store_manifest(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        forget_id: UUID,
        requested_at: datetime,
    ) -> ForgetManifest:
        """Freeze one repeatable-read inventory and commit it before portable append."""
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                row = (
                    connection.execute(
                        _SELECT_BY_ID_FOR_UPDATE,
                        {"deployment_id": deployment_id, "forget_id": forget_id},
                    )
                    .mappings()
                    .first()
                )
                if row is None or row["doc_id"] != doc_id:
                    raise ForgetManifestNotFoundError(
                        f"preparing forget_id {forget_id} does not exist"
                    )
                if row["manifest"] is not None:
                    return _manifest(row=row)
                document = (
                    connection.execute(
                        _SELECT_DOCUMENT,
                        {"deployment_id": deployment_id, "doc_id": doc_id},
                    )
                    .mappings()
                    .first()
                )
                if document is None:
                    raise ForgetTargetNotFoundError(
                        f"deployment {deployment_id} does not own document {doc_id}"
                    )
                manifest = ForgetManifest(
                    forget_id=forget_id,
                    deployment_id=deployment_id,
                    doc_id=doc_id,
                    requested_at=requested_at,
                    source_identity_hash=(
                        source_identity_hash(
                            deployment_id=deployment_id,
                            source_kind=str(document["source_kind"]),
                            source_ref=str(document["source_ref"]),
                        )
                        if document["source_ref"] is not None
                        else None
                    ),
                    content_hashes=_text_values(
                        connection=connection,
                        statement=_CONTENT_HASHES,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                    ),
                    chunk_ids=_uuid_values(
                        connection=connection,
                        statement=_CHUNK_IDS,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                    ),
                    claim_ids=_uuid_values(
                        connection=connection,
                        statement=_CLAIM_IDS,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                    ),
                    mention_ids=_uuid_values(
                        connection=connection,
                        statement=_MENTION_IDS,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                    ),
                    resolved_entity_ids=_uuid_values(
                        connection=connection,
                        statement=_RESOLVED_ENTITY_IDS,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                    ),
                    fact_ids=_uuid_values(
                        connection=connection,
                        statement=_EXCLUSIVE_FACT_IDS,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                    ),
                    entity_ids=_uuid_values(
                        connection=connection,
                        statement=_EXCLUSIVE_ENTITY_IDS,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                    ),
                    object_keys=tuple(
                        ObjectKey(value)
                        for value in _text_values(
                            connection=connection,
                            statement=_OBJECT_KEYS,
                            deployment_id=deployment_id,
                            doc_id=doc_id,
                        )
                    ),
                    projection_prefixes=tuple(
                        ObjectKey(value)
                        for value in _text_values(
                            connection=connection,
                            statement=_PROJECTION_PREFIXES,
                            deployment_id=deployment_id,
                            doc_id=doc_id,
                        )
                    ),
                    k_artifact_ids=_uuid_values(
                        connection=connection,
                        statement=_K_ARTIFACT_IDS,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                    ),
                )
                connection.execute(
                    _STORE_MANIFEST,
                    {
                        "deployment_id": deployment_id,
                        "forget_id": forget_id,
                        "schema_version": manifest.schema_version,
                        "manifest_hash": manifest.sha256(),
                        "manifest": manifest.canonical_bytes().decode("utf-8"),
                        "source_identity_hash": manifest.source_identity_hash,
                        "content_hashes": list(manifest.content_hashes),
                    },
                )
                return manifest

    def cancel_unstored_preparation(
        self, *, deployment_id: UUID, forget_id: UUID
    ) -> bool:
        """Reopen admission only before immutable manifest bytes have been stored."""
        with self._engine.begin() as connection:
            return (
                connection.execute(
                    _DELETE_UNSTORED_PREPARING,
                    {"deployment_id": deployment_id, "forget_id": forget_id},
                ).rowcount
                == 1
            )

    def accept_and_enqueue(self, *, manifest: ForgetManifest) -> EnqueueOutcome:
        """Acknowledge portable append and atomically create the one worker row."""
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    _SELECT_BY_ID_FOR_UPDATE,
                    {
                        "deployment_id": manifest.deployment_id,
                        "forget_id": manifest.forget_id,
                    },
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ForgetManifestNotFoundError(
                    f"stored forget_id {manifest.forget_id} does not exist"
                )
            _require_same_manifest(row=row, manifest=manifest)
            connection.execute(
                _REGISTER_FORGETTER,
                {
                    "deployment_id": manifest.deployment_id,
                    "version": HARD_FORGET_COMPONENT_VERSION,
                },
            )
            definition_matches = bool(
                connection.execute(
                    _FORGETTER_DEFINITION_MATCHES,
                    {
                        "deployment_id": manifest.deployment_id,
                        "version": HARD_FORGET_COMPONENT_VERSION,
                    },
                ).scalar_one()
            )
            if not definition_matches:
                raise ComponentVersionConflictError(
                    "hard-forget component version has a conflicting definition"
                )
            connection.execute(
                _MARK_ACCEPTED,
                {
                    "deployment_id": manifest.deployment_id,
                    "forget_id": manifest.forget_id,
                },
            )
            return enqueue_on(
                connection=connection,
                work=EnqueueWork(
                    deployment_id=manifest.deployment_id,
                    target_kind=ProcessingTarget.DOCUMENT,
                    target_id=manifest.doc_id,
                    stage=PipelineStage.HARD_FORGET,
                    component_version=HARD_FORGET_COMPONENT_VERSION,
                    content_hash=manifest.sha256(),
                    lane=None,
                    payload={"forget_id": str(manifest.forget_id)},
                ),
            )

    def manifest_for(self, *, deployment_id: UUID, forget_id: UUID) -> ForgetManifest:
        """Return one accepted worker's exact immutable local manifest."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _SELECT_BY_ID,
                    {"deployment_id": deployment_id, "forget_id": forget_id},
                )
                .mappings()
                .first()
            )
        if row is None or row["manifest"] is None:
            raise ForgetManifestNotFoundError(
                f"accepted forget_id {forget_id} does not exist"
            )
        return _manifest(row=row)

    def scrub_postgres(self, *, manifest: ForgetManifest) -> None:
        """Idempotently remove lineage-owned source-bearing PostgreSQL payloads."""
        parameters = {
            "deployment_id": manifest.deployment_id,
            "doc_id": manifest.doc_id,
            "forget_id": manifest.forget_id,
            "chunk_ids": list(manifest.chunk_ids),
            "claim_ids": list(manifest.claim_ids),
            "mention_ids": list(manifest.mention_ids),
            "resolved_entity_ids": list(manifest.resolved_entity_ids),
            "fact_ids": list(manifest.fact_ids),
            "entity_ids": list(manifest.entity_ids),
            "k_artifact_ids": list(manifest.k_artifact_ids),
            "content_hashes": list(manifest.content_hashes),
        }
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    _SELECT_BY_ID_FOR_UPDATE,
                    {
                        "deployment_id": manifest.deployment_id,
                        "forget_id": manifest.forget_id,
                    },
                )
                .mappings()
                .first()
            )
            if row is None:
                raise ForgetManifestNotFoundError(
                    f"forget_id {manifest.forget_id} does not exist"
                )
            _require_same_manifest(row=row, manifest=manifest)
            for statement in _POSTGRES_SCRUB:
                connection.execute(statement, parameters)

    def verify_postgres_scrubbed(self, *, manifest: ForgetManifest) -> None:
        """Raise visibly if any nominated source-bearing PostgreSQL row remains."""
        with self._engine.connect() as connection:
            remaining = int(
                connection.execute(
                    _VERIFY_POSTGRES_SCRUB,
                    {
                        "deployment_id": manifest.deployment_id,
                        "doc_id": manifest.doc_id,
                        "chunk_ids": list(manifest.chunk_ids),
                        "claim_ids": list(manifest.claim_ids),
                        "mention_ids": list(manifest.mention_ids),
                        "resolved_entity_ids": list(manifest.resolved_entity_ids),
                        "fact_ids": list(manifest.fact_ids),
                        "entity_ids": list(manifest.entity_ids),
                        "k_artifact_ids": list(manifest.k_artifact_ids),
                        "content_hashes": list(manifest.content_hashes),
                    },
                ).scalar_one()
            )
        if remaining:
            raise RuntimeError(
                f"forget_id {manifest.forget_id} PostgreSQL verification found"
                f" {remaining} source-bearing row groups"
            )

    def mark_complete(self, *, manifest: ForgetManifest) -> None:
        """Open admission only after every store and PostgreSQL verification succeeds."""
        with self._engine.begin() as connection:
            updated = connection.execute(
                _MARK_COMPLETE,
                {
                    "deployment_id": manifest.deployment_id,
                    "forget_id": manifest.forget_id,
                    "manifest_hash": manifest.sha256(),
                },
            ).rowcount
        if updated != 1:
            raise ForgetManifestConflictError(
                f"forget_id {manifest.forget_id} was not accepted with these bytes"
            )

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
    ) -> None:
        """Refuse active traffic and any completed manifest's exact identity."""
        self.assert_available(deployment_id=deployment_id)
        identity_hash = source_identity_hash(
            deployment_id=deployment_id, source_kind=source_kind, source_ref=source_ref
        )
        with self._engine.connect() as connection:
            forget_id = connection.execute(
                _FORGOTTEN_INGEST,
                {
                    "deployment_id": deployment_id,
                    "source_identity_hash": identity_hash,
                    "content_hash": content_hash,
                },
            ).scalar_one_or_none()
        if forget_id is not None:
            raise ForgottenSourceError(
                f"ingest matches irreversible forget_id {forget_id}"
            )


def _record(*, row: RowMapping) -> ForgetManifestRecord:
    """Validate one database row as the typed progress boundary."""
    return ForgetManifestRecord(
        forget_id=row["forget_id"],
        deployment_id=row["deployment_id"],
        doc_id=row["doc_id"],
        manifest=_manifest(row=row) if row["manifest"] is not None else None,
        manifest_hash=row["manifest_hash"],
        status=ForgetManifestStatus(str(row["status"])),
        prepared_at=row["prepared_at"],
        accepted_at=row["accepted_at"],
        completed_at=row["completed_at"],
        last_verified_at=row["last_verified_at"],
    )


def _manifest(*, row: RowMapping) -> ForgetManifest:
    """Validate the immutable JSON materialization from one progress row."""
    return ForgetManifest.model_validate_json(
        json.dumps(row["manifest"], ensure_ascii=False, separators=(",", ":"))
    )


def _require_same_manifest(*, row: RowMapping, manifest: ForgetManifest) -> None:
    """Reject local identity reuse with bytes unlike the portable append."""
    stored = _manifest(row=row)
    if stored.canonical_bytes() != manifest.canonical_bytes():
        raise ForgetManifestConflictError(
            f"forget_id {manifest.forget_id} has different local manifest bytes"
        )


def _uuid_values(
    *, connection: Connection, statement: TextClause, deployment_id: UUID, doc_id: UUID
) -> tuple[UUID, ...]:
    """Return one query's distinct UUID column in canonical lexical order."""
    values = connection.execute(
        statement, {"deployment_id": deployment_id, "doc_id": doc_id}
    ).scalars()
    return tuple(sorted({UUID(str(value)) for value in values}, key=str))


def _text_values(
    *, connection: Connection, statement: TextClause, deployment_id: UUID, doc_id: UUID
) -> tuple[str, ...]:
    """Return one query's non-null text column sorted and duplicate-free."""
    values = connection.execute(
        statement, {"deployment_id": deployment_id, "doc_id": doc_id}
    ).scalars()
    return tuple(sorted({str(value) for value in values if value is not None}))


_LOCK_DEPLOYMENT = text(
    "SELECT pg_advisory_xact_lock(hashtextextended('hard-forget:' ||"
    " CAST(:deployment_id AS text), 0))"
)

_SELECT_DOCUMENT = text(
    """
    SELECT doc_id, source_kind, source_ref
    FROM documents
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_INSERT_PREPARING = text(
    """
    INSERT INTO forget_manifests (
        forget_id, deployment_id, doc_id, schema_version, status
    ) VALUES (:forget_id, :deployment_id, :doc_id, 1, 'preparing')
    RETURNING forget_id, deployment_id, doc_id, manifest_hash, manifest,
              status, prepared_at, accepted_at, completed_at, last_verified_at
    """
)

_SELECT_BY_ID = text(
    """
    SELECT forget_id, deployment_id, doc_id, manifest_hash, manifest,
           status, prepared_at, accepted_at, completed_at, last_verified_at
    FROM forget_manifests
    WHERE deployment_id = :deployment_id AND forget_id = :forget_id
    """
)

_SELECT_BY_ID_FOR_UPDATE = text(
    """
    SELECT forget_id, deployment_id, doc_id, manifest_hash, manifest,
           status, prepared_at, accepted_at, completed_at, last_verified_at
    FROM forget_manifests
    WHERE deployment_id = :deployment_id AND forget_id = :forget_id
    FOR UPDATE
    """
)

_SELECT_BY_DOC = text(
    """
    SELECT forget_id, deployment_id, doc_id, manifest_hash, manifest,
           status, prepared_at, accepted_at, completed_at, last_verified_at
    FROM forget_manifests
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_SELECT_PREPARING = text(
    """
    SELECT forget_id, deployment_id, doc_id, manifest_hash, manifest,
           status, prepared_at, accepted_at, completed_at, last_verified_at
    FROM forget_manifests
    WHERE deployment_id = :deployment_id AND status = 'preparing'
    """
)

_INSERT_PORTABLE = text(
    """
    INSERT INTO forget_manifests (
        forget_id, deployment_id, doc_id, schema_version, manifest_hash, manifest,
        source_identity_hash, content_hashes, status, accepted_at
    ) VALUES (
        :forget_id, :deployment_id, :doc_id, :schema_version, :manifest_hash,
        CAST(:manifest AS jsonb), :source_identity_hash, :content_hashes,
        'accepted', now()
    )
    """
)

_MATERIALIZE_PREPARING = text(
    """
    UPDATE forget_manifests
    SET schema_version = :schema_version,
        manifest_hash = :manifest_hash,
        manifest = CAST(:manifest AS jsonb),
        source_identity_hash = :source_identity_hash,
        content_hashes = :content_hashes,
        status = 'accepted',
        accepted_at = COALESCE(accepted_at, now())
    WHERE deployment_id = :deployment_id
      AND forget_id = :forget_id
      AND doc_id = :doc_id
      AND status = 'preparing'
      AND manifest IS NULL
    """
)

_ORDINARY_WORK_DRAINED = text(
    """
    SELECT NOT EXISTS (
        SELECT 1
        FROM processing_state
        WHERE deployment_id = :deployment_id
          AND status = 'running'
          AND stage <> 'hard_forget'
    )
    """
)

_CONTENT_HASHES = text(
    """
    SELECT DISTINCT content_hash
    FROM document_versions
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    ORDER BY content_hash
    """
)

_CHUNK_IDS = text(
    """
    SELECT DISTINCT chunk_id
    FROM chunks
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    ORDER BY chunk_id
    """
)

_CLAIM_IDS = text(
    """
    SELECT DISTINCT claim_id
    FROM claims
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    ORDER BY claim_id
    """
)

_MENTION_IDS = text(
    """
    SELECT DISTINCT mention_id
    FROM mentions
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    ORDER BY mention_id
    """
)

_RESOLVED_ENTITY_IDS = text(
    """
    SELECT DISTINCT decision.entity_id
    FROM resolution_decisions decision
    JOIN mentions mention ON mention.mention_id = decision.mention_id
    WHERE decision.deployment_id = :deployment_id
      AND mention.deployment_id = :deployment_id
      AND mention.doc_id = :doc_id
    ORDER BY decision.entity_id
    """
)

_EXCLUSIVE_FACT_IDS = text(
    """
    SELECT fact_id
    FROM (
        SELECT relation_id AS fact_id
        FROM relations relation
        WHERE relation.deployment_id = :deployment_id
          AND EXISTS (
              SELECT 1 FROM relation_evidence target
              WHERE target.deployment_id = :deployment_id
                AND target.relation_id = relation.relation_id
                AND target.doc_id = :doc_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM relation_evidence other
              JOIN claims claim
                ON claim.deployment_id = other.deployment_id
               AND claim.claim_id = other.claim_id
              JOIN documents document
                ON document.deployment_id = other.deployment_id
               AND document.doc_id = other.doc_id
              WHERE other.deployment_id = :deployment_id
                AND other.relation_id = relation.relation_id
                AND other.doc_id <> :doc_id
                AND claim.is_current_testimony
                AND document.deleted_at IS NULL
          )
        UNION
        SELECT observation_id AS fact_id
        FROM observations observation
        WHERE observation.deployment_id = :deployment_id
          AND EXISTS (
              SELECT 1 FROM observation_evidence target
              WHERE target.deployment_id = :deployment_id
                AND target.observation_id = observation.observation_id
                AND target.doc_id = :doc_id
          )
          AND NOT EXISTS (
              SELECT 1
              FROM observation_evidence other
              JOIN claims claim
                ON claim.deployment_id = other.deployment_id
               AND claim.claim_id = other.claim_id
              JOIN documents document
                ON document.deployment_id = other.deployment_id
               AND document.doc_id = other.doc_id
              WHERE other.deployment_id = :deployment_id
                AND other.observation_id = observation.observation_id
                AND other.doc_id <> :doc_id
                AND claim.is_current_testimony
                AND document.deleted_at IS NULL
          )
    ) exclusive
    ORDER BY fact_id
    """
)

_EXCLUSIVE_ENTITY_IDS = text(
    """
    SELECT DISTINCT target.entity_id
    FROM resolution_decisions target
    JOIN mentions mention ON mention.mention_id = target.mention_id
    WHERE target.deployment_id = :deployment_id
      AND mention.deployment_id = :deployment_id
      AND mention.doc_id = :doc_id
      AND target.superseded_by IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM resolution_decisions other
          JOIN mentions other_mention ON other_mention.mention_id = other.mention_id
          JOIN claims other_claim
            ON other_claim.deployment_id = other_mention.deployment_id
           AND other_claim.claim_id = other_mention.claim_id
          JOIN documents other_document
            ON other_document.deployment_id = other_mention.deployment_id
           AND other_document.doc_id = other_mention.doc_id
          WHERE other.deployment_id = :deployment_id
            AND other.entity_id = target.entity_id
            AND other.superseded_by IS NULL
            AND other_mention.doc_id <> :doc_id
            AND other_claim.is_current_testimony
            AND other_document.deleted_at IS NULL
      )
    ORDER BY target.entity_id
    """
)

_K_ARTIFACT_IDS = text(
    """
    SELECT artifact_id
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
    ORDER BY artifact_id::text
    """
)

_BLOCKING_K_PATHS = text(
    """
    WITH target_relations AS (
        SELECT DISTINCT relation_id
        FROM relation_evidence
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    ), affected AS (
        SELECT DISTINCT artifact_id
        FROM knowledge_artifact_evidence
        WHERE deployment_id = :deployment_id
          AND (
              doc_id = :doc_id
              OR claim_lineage_id = :doc_id
              OR relation_id IN (SELECT relation_id FROM target_relations)
          )
    )
    SELECT path
    FROM (
        SELECT artifact.git_path AS path
        FROM knowledge_artifacts artifact
        WHERE artifact.deployment_id = :deployment_id
          AND artifact.artifact_id IN (SELECT artifact_id FROM affected)
          AND artifact.page_kind = 'authored'
        UNION
        SELECT artifact.curation_path AS path
        FROM knowledge_artifacts artifact
        WHERE artifact.deployment_id = :deployment_id
          AND artifact.artifact_id IN (SELECT artifact_id FROM affected)
          AND artifact.page_kind = 'compiled'
          AND artifact.curation_path IS NOT NULL
    ) blocking
    ORDER BY path
    """
)

_K_PATHS_FOR_ARTIFACTS = text(
    """
    SELECT path
    FROM (
        SELECT git_path AS path
        FROM knowledge_artifacts
        WHERE deployment_id = :deployment_id
          AND artifact_id = ANY(:artifact_ids)
        UNION
        SELECT curation_path AS path
        FROM knowledge_artifacts
        WHERE deployment_id = :deployment_id
          AND artifact_id = ANY(:artifact_ids)
          AND curation_path IS NOT NULL
    ) paths
    ORDER BY path
    """
)

_OBJECT_KEYS = text(
    """
    WITH target_artifacts AS (
        SELECT artifact_id
        FROM knowledge_artifacts
        WHERE deployment_id = :deployment_id
    ), keys AS (
        SELECT object.raw_uri AS object_key
        FROM document_versions version
        JOIN content_objects object
          ON object.deployment_id = version.deployment_id
         AND object.content_hash = version.content_hash
        WHERE version.deployment_id = :deployment_id
          AND version.doc_id = :doc_id
          AND NOT EXISTS (
              SELECT 1
              FROM document_versions other_version
              JOIN documents other_document
                ON other_document.deployment_id = other_version.deployment_id
               AND other_document.doc_id = other_version.doc_id
              WHERE other_version.deployment_id = version.deployment_id
                AND other_version.content_hash = version.content_hash
                AND other_version.doc_id <> :doc_id
                AND other_version.deleted_at IS NULL
                AND other_document.deleted_at IS NULL
          )
        UNION ALL
        SELECT uri.object_key
        FROM document_representations representation
        JOIN document_versions version
          ON version.deployment_id = representation.deployment_id
         AND version.version_id = representation.version_id
        CROSS JOIN LATERAL (
            VALUES (representation.markdown_uri), (representation.pageindex_uri),
                   (representation.conversion_uri), (representation.blocks_uri),
                   (representation.meta_uri)
        ) uri(object_key)
        WHERE version.deployment_id = :deployment_id AND version.doc_id = :doc_id
        UNION ALL
        SELECT generation.pageindex_uri
        FROM document_structure_generations generation
        WHERE generation.deployment_id = :deployment_id
          AND generation.doc_id = :doc_id
        UNION ALL
        SELECT compilation.session_transcript_uri
        FROM knowledge_compilations compilation
        WHERE compilation.deployment_id = :deployment_id
          AND compilation.artifact_id IN (SELECT artifact_id FROM target_artifacts)
        UNION ALL
        SELECT run.session_transcript_uri
        FROM knowledge_plan_runs run
        WHERE run.deployment_id = :deployment_id
    )
    SELECT DISTINCT object_key
    FROM keys
    WHERE object_key IS NOT NULL
    ORDER BY object_key
    """
)

_PROJECTION_PREFIXES = text(
    """
    SELECT DISTINCT gcs_uri
    FROM projection_snapshots
    WHERE deployment_id = :deployment_id
      AND plane = 'P3_corpusfs'
    ORDER BY gcs_uri
    """
)

_STORE_MANIFEST = text(
    """
    UPDATE forget_manifests
    SET schema_version = :schema_version,
        manifest_hash = :manifest_hash,
        manifest = CAST(:manifest AS jsonb),
        source_identity_hash = :source_identity_hash,
        content_hashes = :content_hashes
    WHERE deployment_id = :deployment_id
      AND forget_id = :forget_id
      AND status = 'preparing'
      AND manifest IS NULL
    """
)

_DELETE_UNSTORED_PREPARING = text(
    """
    DELETE FROM forget_manifests
    WHERE deployment_id = :deployment_id
      AND forget_id = :forget_id
      AND status = 'preparing'
      AND manifest IS NULL
    """
)

_REGISTER_FORGETTER = text(
    """
    INSERT INTO pipeline_component_versions (
        deployment_id, component, version, params, notes
    ) VALUES (
        :deployment_id, 'forgetter', :version, '{"schema_version": 1}'::jsonb,
        'D74 deterministic hard-forget coordinator'
    )
    ON CONFLICT (deployment_id, component, version) DO NOTHING
    """
)

_FORGETTER_DEFINITION_MATCHES = text(
    """
    SELECT params = '{"schema_version": 1}'::jsonb
       AND model_name IS NULL
       AND prompt_hash IS NULL
       AND embedding_dim IS NULL
       AND notes = 'D74 deterministic hard-forget coordinator'
    FROM pipeline_component_versions
    WHERE deployment_id = :deployment_id
      AND component = 'forgetter'
      AND version = :version
    """
)

_MARK_ACCEPTED = text(
    """
    UPDATE forget_manifests
    SET status = CASE WHEN status = 'complete' THEN status ELSE 'accepted' END,
        accepted_at = COALESCE(accepted_at, now())
    WHERE deployment_id = :deployment_id AND forget_id = :forget_id
    """
)

_POSTGRES_SCRUB = (
    text(
        """
        UPDATE documents
        SET current_version_id = NULL,
            document_entity_id = NULL,
            source_ref = NULL,
            source_uri = NULL,
            title = NULL,
            deleted_at = COALESCE(deleted_at, now())
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    text(
        """
        UPDATE document_versions
        SET current_representation_id = NULL,
            source_version_ref = NULL,
            source_modified_at = NULL,
            published_at = NULL,
            language = NULL,
            status = 'deleted',
            error = NULL,
            superseded_at = NULL,
            deleted_at = COALESCE(deleted_at, now())
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    # The eval schema predates D74 and has no lineage-provenance column. A rare
    # hard-forget therefore drops deployment-scoped free-text eval fixtures
    # instead of guessing semantically which prose came from the lineage.
    text(
        """
        DELETE FROM eval_runs WHERE deployment_id = :deployment_id
        """
    ),
    text(
        """
        DELETE FROM canary_cases WHERE deployment_id = :deployment_id
        """
    ),
    text(
        """
        DELETE FROM golden_claim_labels WHERE deployment_id = :deployment_id
        """
    ),
    text(
        """
        DELETE FROM golden_pairs WHERE deployment_id = :deployment_id
        """
    ),
    text(
        """
        DELETE FROM knowledge_plan_decisions decision
        WHERE decision.deployment_id = :deployment_id
        """
    ),
    text(
        """
        DELETE FROM knowledge_plan_runs run
        WHERE run.deployment_id = :deployment_id
        """
    ),
    text(
        """
        DELETE FROM knowledge_refresh_queue refresh
        WHERE refresh.deployment_id = :deployment_id
          AND (
              refresh.artifact_id = ANY(:k_artifact_ids)
              OR refresh.scope_id IN (
                  SELECT artifact.scope_id
                  FROM knowledge_artifacts artifact
                  WHERE artifact.deployment_id = :deployment_id
                    AND artifact.artifact_id = ANY(:k_artifact_ids)
                    AND artifact.scope_id IS NOT NULL
              )
              OR refresh.payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
              OR EXISTS (
                  SELECT 1
                  FROM unnest(
                      CAST(:claim_ids AS uuid[]) || CAST(:mention_ids AS uuid[])
                      || CAST(:fact_ids AS uuid[])
                      || CAST(:resolved_entity_ids AS uuid[])
                      || CAST(:k_artifact_ids AS uuid[])
                  ) AS ids(value)
                  WHERE refresh.payload::text LIKE '%'
                        || CAST(ids.value AS text) || '%'
              )
          )
        """
    ),
    text(
        """
        DELETE FROM knowledge_dispatches dispatch
        WHERE dispatch.deployment_id = :deployment_id
          AND (
              dispatch.payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
              OR EXISTS (
                  SELECT 1
                  FROM unnest(
                      CAST(:claim_ids AS uuid[]) || CAST(:mention_ids AS uuid[])
                      || CAST(:fact_ids AS uuid[])
                      || CAST(:resolved_entity_ids AS uuid[])
                      || CAST(:k_artifact_ids AS uuid[])
                  ) AS ids(value)
                  WHERE dispatch.payload::text LIKE '%'
                        || CAST(ids.value AS text) || '%'
              )
          )
        """
    ),
    text(
        """
        DELETE FROM knowledge_artifact_evidence
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR claim_lineage_id = :doc_id
               OR relation_id = ANY(:fact_ids))
        """
    ),
    text(
        """
        DELETE FROM knowledge_compilations
        WHERE deployment_id = :deployment_id
          AND artifact_id = ANY(:k_artifact_ids)
        """
    ),
    text(
        """
        UPDATE knowledge_artifacts
        SET page_summary = NULL,
            content_hash = NULL,
            inputs_hash = NULL,
            last_compiled_at = NULL,
            status = CASE WHEN page_kind = 'compiled' THEN 'stale' ELSE status END
        WHERE deployment_id = :deployment_id
          AND artifact_id = ANY(:k_artifact_ids)
        """
    ),
    text(
        """
        UPDATE relation_adjudications
        SET triggering_claim_id = NULL, features = NULL
        WHERE deployment_id = :deployment_id
          AND triggering_claim_id = ANY(:claim_ids)
        """
    ),
    text(
        """
        UPDATE observation_adjudications
        SET triggering_claim_id = NULL, features = NULL
        WHERE deployment_id = :deployment_id
          AND triggering_claim_id = ANY(:claim_ids)
        """
    ),
    text(
        """
        UPDATE relation_adjudications
        SET superseded_by = NULL
        WHERE superseded_by IN (
            SELECT adjudication_id FROM relation_adjudications
            WHERE deployment_id = :deployment_id
              AND (relation_id = ANY(:fact_ids)
                   OR related_relation_id = ANY(:fact_ids))
        )
        """
    ),
    text(
        """
        DELETE FROM relation_adjudications
        WHERE deployment_id = :deployment_id
          AND (relation_id = ANY(:fact_ids)
               OR related_relation_id = ANY(:fact_ids))
        """
    ),
    text(
        """
        UPDATE observation_adjudications
        SET superseded_by = NULL
        WHERE superseded_by IN (
            SELECT adjudication_id FROM observation_adjudications
            WHERE deployment_id = :deployment_id
              AND (observation_id = ANY(:fact_ids)
                   OR related_observation_id = ANY(:fact_ids))
        )
        """
    ),
    text(
        """
        DELETE FROM observation_adjudications
        WHERE deployment_id = :deployment_id
          AND (observation_id = ANY(:fact_ids)
               OR related_observation_id = ANY(:fact_ids))
        """
    ),
    text(
        """
        DELETE FROM relation_evidence
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR relation_id = ANY(:fact_ids))
        """
    ),
    text(
        """
        DELETE FROM observation_evidence
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR observation_id = ANY(:fact_ids))
        """
    ),
    text(
        """
        DELETE FROM normalize_observation_staging
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    text(
        """
        DELETE FROM obs_flush_entity_units
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR version_id IN (
            SELECT version_id FROM document_versions
            WHERE deployment_id = :deployment_id AND doc_id = :doc_id
          ))
        """
    ),
    text(
        """
        DELETE FROM obs_flush_version_state
        WHERE deployment_id = :deployment_id
          AND version_id IN (
            SELECT version_id FROM document_versions
            WHERE deployment_id = :deployment_id AND doc_id = :doc_id
          )
        """
    ),
    text(
        """
        DELETE FROM relations
        WHERE deployment_id = :deployment_id AND relation_id = ANY(:fact_ids)
        """
    ),
    text(
        """
        DELETE FROM observations
        WHERE deployment_id = :deployment_id AND observation_id = ANY(:fact_ids)
        """
    ),
    text(
        """
        DELETE FROM aliases
        WHERE deployment_id = :deployment_id AND entity_id = ANY(:entity_ids)
        """
    ),
    # Generic-identifier guards are derived from source lemmas but carry no
    # lineage provenance. Rebuilding this small cache is safer than retaining
    # an unprovable surface form after an irreversible forget.
    text(
        """
        DELETE FROM generic_identifier_guard
        WHERE deployment_id = :deployment_id
        """
    ),
    text(
        """
        UPDATE merge_events
        SET trigger_lemmas = '{}',
            evidence = NULL,
            pre_merge_membership_snapshot = '{}'::jsonb
        WHERE deployment_id = :deployment_id
          AND (
              survivor_id = ANY(:resolved_entity_ids)
              OR absorbed_id = ANY(:resolved_entity_ids)
              OR EXISTS (
                  SELECT 1 FROM unnest(CAST(:mention_ids AS uuid[])) AS ids(value)
                  WHERE pre_merge_membership_snapshot::text LIKE '%'
                        || CAST(ids.value AS text) || '%'
              )
          )
        """
    ),
    text(
        """
        UPDATE resolution_exclusions
        SET reason = NULL
        WHERE deployment_id = :deployment_id
          AND (
              entity_id_low = ANY(:resolved_entity_ids)
              OR entity_id_high = ANY(:resolved_entity_ids)
          )
        """
    ),
    text(
        """
        UPDATE entities
        SET canonical_name = '',
            normalized_name = '',
            status = 'retired',
            merged_into = NULL,
            profile_summary = NULL,
            embedding = NULL,
            embedding_model = NULL,
            embedding_input_policy_version = NULL,
            embedding_text_hash = NULL,
            mention_count = 0,
            graph_degree = 0,
            updated_at = now()
        WHERE deployment_id = :deployment_id AND entity_id = ANY(:entity_ids)
        """
    ),
    text(
        """
        DELETE FROM document_entity_bindings
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    text(
        """
        DELETE FROM resolution_decisions
        WHERE deployment_id = :deployment_id
          AND mention_id = ANY(:mention_ids)
        """
    ),
    text(
        """
        DELETE FROM mentions
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR mention_id = ANY(:mention_ids))
        """
    ),
    text(
        """
        DELETE FROM grounding_audits
        WHERE deployment_id = :deployment_id AND claim_id = ANY(:claim_ids)
        """
    ),
    text(
        """
        DELETE FROM claim_extraction_decisions
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    text(
        """
        DELETE FROM chunk_claims
        WHERE deployment_id = :deployment_id
          AND (chunk_id = ANY(:chunk_ids) OR claim_id = ANY(:claim_ids))
        """
    ),
    text(
        """
        DELETE FROM claims
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    text(
        """
        DELETE FROM chunk_search
        WHERE deployment_id = :deployment_id AND chunk_id = ANY(:chunk_ids)
        """
    ),
    text(
        """
        DELETE FROM chunks
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    text(
        """
        DELETE FROM document_sections
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    # D79 audit tables are erased EXPLICITLY, never via implied cascade:
    # generations first (their selecting_check_id references checks with
    # default NO ACTION), then checks, then representations. A future FK
    # edit must not silently strand titles/stats/failure envelopes of a
    # forgotten document.
    text(
        """
        DELETE FROM document_structure_generations
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    text(
        """
        DELETE FROM document_skeleton_checks
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        """
    ),
    text(
        """
        DELETE FROM document_representations
        WHERE deployment_id = :deployment_id
          AND version_id IN (
              SELECT version_id FROM document_versions
              WHERE deployment_id = :deployment_id AND doc_id = :doc_id
          )
        """
    ),
    text(
        """
        DELETE FROM document_crossrefs
        WHERE deployment_id = :deployment_id AND from_doc_id = :doc_id
        """
    ),
    text(
        """
        DELETE FROM document_crossrefs
        WHERE deployment_id = :deployment_id AND to_doc_id = :doc_id
        """
    ),
    text(
        """
        UPDATE content_objects object
        SET mime = 'application/x-forgotten',
            byte_size = NULL,
            raw_uri = 'forgotten/' || object.content_hash,
            purged_at = COALESCE(object.purged_at, now())
        WHERE object.deployment_id = :deployment_id
          AND object.content_hash = ANY(:content_hashes)
          AND NOT EXISTS (
              SELECT 1
              FROM document_versions other_version
              JOIN documents other_document
                ON other_document.deployment_id = other_version.deployment_id
               AND other_document.doc_id = other_version.doc_id
              WHERE other_version.deployment_id = object.deployment_id
                AND other_version.content_hash = object.content_hash
                AND other_version.doc_id <> :doc_id
                AND other_version.deleted_at IS NULL
                AND other_document.deleted_at IS NULL
          )
        """
    ),
    text(
        """
        UPDATE processing_state
        SET payload = NULL, last_error = NULL
        WHERE deployment_id = :deployment_id
          AND stage <> 'hard_forget'
          AND (
              target_id = :doc_id
              OR target_id = ANY(:chunk_ids)
              OR target_id = ANY(:claim_ids)
              OR target_id = ANY(:mention_ids)
              OR target_id = ANY(:fact_ids)
              OR target_id = ANY(:entity_ids)
              OR target_id = ANY(:resolved_entity_ids)
              OR content_hash = ANY(:content_hashes)
              OR payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
              OR EXISTS (
                  SELECT 1
                  FROM unnest(
                      CAST(:chunk_ids AS uuid[]) || CAST(:claim_ids AS uuid[])
                      || CAST(:mention_ids AS uuid[]) || CAST(:fact_ids AS uuid[])
                      || CAST(:resolved_entity_ids AS uuid[])
                      || CAST(:k_artifact_ids AS uuid[])
                  ) AS ids(value)
                  WHERE payload::text LIKE '%' || CAST(ids.value AS text) || '%'
              )
          )
        """
    ),
    text(
        """
        DELETE FROM review_queue review
        WHERE review.deployment_id = :deployment_id
          AND (
              review.candidate::text LIKE '%' || CAST(:doc_id AS text) || '%'
              OR EXISTS (
                  SELECT 1
                  FROM unnest(
                      CAST(:chunk_ids AS uuid[]) || CAST(:claim_ids AS uuid[])
                      || CAST(:mention_ids AS uuid[]) || CAST(:fact_ids AS uuid[])
                      || CAST(:resolved_entity_ids AS uuid[])
                      || CAST(:k_artifact_ids AS uuid[])
                  ) AS ids(value)
                  WHERE review.candidate::text LIKE '%'
                        || CAST(ids.value AS text) || '%'
              )
          )
        """
    ),
)

_VERIFY_POSTGRES_SCRUB = text(
    """
    SELECT count(*)
    FROM (
        SELECT 1 FROM documents
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
          AND (source_ref IS NOT NULL OR source_uri IS NOT NULL OR title IS NOT NULL
               OR current_version_id IS NOT NULL OR document_entity_id IS NOT NULL
               OR deleted_at IS NULL)
        UNION ALL
        SELECT 1 FROM document_representations representation
        JOIN document_versions version
          ON version.deployment_id = representation.deployment_id
         AND version.version_id = representation.version_id
        WHERE version.deployment_id = :deployment_id AND version.doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM document_versions
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
          AND (current_representation_id IS NOT NULL
               OR source_version_ref IS NOT NULL
               OR source_modified_at IS NOT NULL
               OR published_at IS NOT NULL
               OR language IS NOT NULL
               OR status <> 'deleted'
               OR error IS NOT NULL
               OR superseded_at IS NOT NULL
               OR deleted_at IS NULL)
        UNION ALL
        SELECT 1 FROM document_sections
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM document_structure_generations
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM document_skeleton_checks
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM chunks
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM chunk_search
        WHERE deployment_id = :deployment_id AND chunk_id = ANY(:chunk_ids)
        UNION ALL
        SELECT 1 FROM chunk_claims
        WHERE deployment_id = :deployment_id
          AND (chunk_id = ANY(:chunk_ids) OR claim_id = ANY(:claim_ids))
        UNION ALL
        SELECT 1 FROM claims
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM mentions
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR mention_id = ANY(:mention_ids))
        UNION ALL
        SELECT 1 FROM document_entity_bindings
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM resolution_decisions
        WHERE deployment_id = :deployment_id AND mention_id = ANY(:mention_ids)
        UNION ALL
        SELECT 1 FROM claim_extraction_decisions
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM grounding_audits
        WHERE deployment_id = :deployment_id AND claim_id = ANY(:claim_ids)
        UNION ALL
        SELECT 1 FROM relation_evidence
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR relation_id = ANY(:fact_ids))
        UNION ALL
        SELECT 1 FROM observation_evidence
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR observation_id = ANY(:fact_ids))
        UNION ALL
        SELECT 1 FROM normalize_observation_staging
        WHERE deployment_id = :deployment_id AND doc_id = :doc_id
        UNION ALL
        SELECT 1 FROM relations
        WHERE deployment_id = :deployment_id AND relation_id = ANY(:fact_ids)
        UNION ALL
        SELECT 1 FROM observations
        WHERE deployment_id = :deployment_id AND observation_id = ANY(:fact_ids)
        UNION ALL
        SELECT 1 FROM aliases
        WHERE deployment_id = :deployment_id AND entity_id = ANY(:entity_ids)
        UNION ALL
        SELECT 1 FROM generic_identifier_guard
        WHERE deployment_id = :deployment_id
        UNION ALL
        SELECT 1 FROM merge_events
        WHERE deployment_id = :deployment_id
          AND (
              survivor_id = ANY(:resolved_entity_ids)
              OR absorbed_id = ANY(:resolved_entity_ids)
              OR EXISTS (
                  SELECT 1 FROM unnest(CAST(:mention_ids AS uuid[])) AS ids(value)
                  WHERE pre_merge_membership_snapshot::text LIKE '%'
                        || CAST(ids.value AS text) || '%'
              )
          )
          AND (
              cardinality(trigger_lemmas) > 0
              OR evidence IS NOT NULL
              OR pre_merge_membership_snapshot <> '{}'::jsonb
          )
        UNION ALL
        SELECT 1 FROM resolution_exclusions
        WHERE deployment_id = :deployment_id
          AND reason IS NOT NULL
          AND (
              entity_id_low = ANY(:resolved_entity_ids)
              OR entity_id_high = ANY(:resolved_entity_ids)
          )
        UNION ALL
        SELECT 1 FROM entities
        WHERE deployment_id = :deployment_id AND entity_id = ANY(:entity_ids)
          AND (status <> 'retired' OR canonical_name <> '' OR normalized_name <> ''
               OR profile_summary IS NOT NULL
               OR embedding IS NOT NULL OR embedding_model IS NOT NULL
               OR embedding_input_policy_version IS NOT NULL
               OR embedding_text_hash IS NOT NULL)
        UNION ALL
        SELECT 1 FROM knowledge_artifact_evidence
        WHERE deployment_id = :deployment_id
          AND (doc_id = :doc_id OR claim_lineage_id = :doc_id
               OR relation_id = ANY(:fact_ids))
        UNION ALL
        SELECT 1 FROM knowledge_compilations
        WHERE deployment_id = :deployment_id
          AND artifact_id = ANY(:k_artifact_ids)
        UNION ALL
        SELECT 1 FROM knowledge_artifacts
        WHERE deployment_id = :deployment_id
          AND artifact_id = ANY(:k_artifact_ids)
          AND (
              page_summary IS NOT NULL OR content_hash IS NOT NULL
              OR inputs_hash IS NOT NULL OR last_compiled_at IS NOT NULL
              OR (page_kind = 'compiled' AND status <> 'stale')
          )
        UNION ALL
        SELECT 1 FROM document_crossrefs
        WHERE deployment_id = :deployment_id
          AND (from_doc_id = :doc_id OR to_doc_id = :doc_id)
        UNION ALL
        SELECT 1 FROM relation_adjudications
        WHERE deployment_id = :deployment_id
          AND (
              triggering_claim_id = ANY(:claim_ids)
              OR relation_id = ANY(:fact_ids)
              OR related_relation_id = ANY(:fact_ids)
          )
        UNION ALL
        SELECT 1 FROM observation_adjudications
        WHERE deployment_id = :deployment_id
          AND (
              triggering_claim_id = ANY(:claim_ids)
              OR observation_id = ANY(:fact_ids)
              OR related_observation_id = ANY(:fact_ids)
          )
        UNION ALL
        SELECT 1 FROM knowledge_plan_decisions decision
        WHERE decision.deployment_id = :deployment_id
        UNION ALL
        SELECT 1 FROM knowledge_plan_runs run
        WHERE run.deployment_id = :deployment_id
        UNION ALL
        SELECT 1 FROM knowledge_refresh_queue refresh
        WHERE refresh.deployment_id = :deployment_id
          AND (
              refresh.artifact_id = ANY(:k_artifact_ids)
              OR refresh.scope_id IN (
                  SELECT artifact.scope_id
                  FROM knowledge_artifacts artifact
                  WHERE artifact.deployment_id = :deployment_id
                    AND artifact.artifact_id = ANY(:k_artifact_ids)
                    AND artifact.scope_id IS NOT NULL
              )
              OR refresh.payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
              OR EXISTS (
                  SELECT 1
                  FROM unnest(
                      CAST(:claim_ids AS uuid[]) || CAST(:mention_ids AS uuid[])
                      || CAST(:fact_ids AS uuid[])
                      || CAST(:resolved_entity_ids AS uuid[])
                      || CAST(:k_artifact_ids AS uuid[])
                  ) AS ids(value)
                  WHERE refresh.payload::text LIKE '%'
                        || CAST(ids.value AS text) || '%'
              )
          )
        UNION ALL
        SELECT 1 FROM knowledge_dispatches dispatch
        WHERE dispatch.deployment_id = :deployment_id
          AND (
              dispatch.payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
              OR EXISTS (
                  SELECT 1
                  FROM unnest(
                      CAST(:claim_ids AS uuid[]) || CAST(:mention_ids AS uuid[])
                      || CAST(:fact_ids AS uuid[])
                      || CAST(:resolved_entity_ids AS uuid[])
                      || CAST(:k_artifact_ids AS uuid[])
                  ) AS ids(value)
                  WHERE dispatch.payload::text LIKE '%'
                        || CAST(ids.value AS text) || '%'
              )
          )
        UNION ALL
        SELECT 1 FROM eval_runs WHERE deployment_id = :deployment_id
        UNION ALL
        SELECT 1 FROM canary_cases WHERE deployment_id = :deployment_id
        UNION ALL
        SELECT 1 FROM golden_claim_labels WHERE deployment_id = :deployment_id
        UNION ALL
        SELECT 1 FROM golden_pairs WHERE deployment_id = :deployment_id
        UNION ALL
        SELECT 1 FROM processing_state
        WHERE deployment_id = :deployment_id
          AND stage <> 'hard_forget'
          AND (payload IS NOT NULL OR last_error IS NOT NULL)
          AND (
              target_id = :doc_id
              OR target_id = ANY(:chunk_ids)
              OR target_id = ANY(:claim_ids)
              OR target_id = ANY(:mention_ids)
              OR target_id = ANY(:fact_ids)
              OR target_id = ANY(:entity_ids)
              OR target_id = ANY(:resolved_entity_ids)
              OR content_hash = ANY(:content_hashes)
              OR payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
              OR EXISTS (
                  SELECT 1
                  FROM unnest(
                      CAST(:chunk_ids AS uuid[]) || CAST(:claim_ids AS uuid[])
                      || CAST(:mention_ids AS uuid[]) || CAST(:fact_ids AS uuid[])
                      || CAST(:resolved_entity_ids AS uuid[])
                      || CAST(:k_artifact_ids AS uuid[])
                  ) AS ids(value)
                  WHERE payload::text LIKE '%' || CAST(ids.value AS text) || '%'
              )
          )
        UNION ALL
        SELECT 1 FROM review_queue review
        WHERE review.deployment_id = :deployment_id
          AND (
              review.candidate::text LIKE '%' || CAST(:doc_id AS text) || '%'
              OR EXISTS (
                  SELECT 1
                  FROM unnest(
                      CAST(:chunk_ids AS uuid[]) || CAST(:claim_ids AS uuid[])
                      || CAST(:mention_ids AS uuid[]) || CAST(:fact_ids AS uuid[])
                      || CAST(:resolved_entity_ids AS uuid[])
                      || CAST(:k_artifact_ids AS uuid[])
                  ) AS ids(value)
                  WHERE review.candidate::text LIKE '%'
                        || CAST(ids.value AS text) || '%'
              )
          )
        UNION ALL
        SELECT 1 FROM content_objects object
        WHERE object.deployment_id = :deployment_id
          AND object.content_hash = ANY(:content_hashes)
          AND NOT EXISTS (
              SELECT 1
              FROM document_versions other_version
              JOIN documents other_document
                ON other_document.deployment_id = other_version.deployment_id
               AND other_document.doc_id = other_version.doc_id
              WHERE other_version.deployment_id = object.deployment_id
                AND other_version.content_hash = object.content_hash
                AND other_version.doc_id <> :doc_id
                AND other_version.deleted_at IS NULL
                AND other_document.deleted_at IS NULL
          )
          AND (object.mime <> 'application/x-forgotten'
               OR object.byte_size IS NOT NULL
               OR object.raw_uri <> 'forgotten/' || object.content_hash
               OR object.purged_at IS NULL)
    ) residual
    """
)

_MARK_COMPLETE = text(
    """
    UPDATE forget_manifests
    SET status = 'complete', completed_at = COALESCE(completed_at, now()),
        last_verified_at = now()
    WHERE deployment_id = :deployment_id
      AND forget_id = :forget_id
      AND manifest_hash = :manifest_hash
      AND status IN ('accepted', 'complete')
    """
)


_FORGOTTEN_INGEST = text(
    """
    SELECT forget_id
    FROM forget_manifests
    WHERE deployment_id = :deployment_id
      AND status = 'complete'
      AND (
        source_identity_hash = :source_identity_hash
        OR :content_hash = ANY(content_hashes)
      )
    ORDER BY completed_at, forget_id
    LIMIT 1
    """
)
