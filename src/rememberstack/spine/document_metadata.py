"""D134 document metadata writes, run inside the caller's transaction.

Three moments write these rows:

- **ingest** — a new version gets its ``document_metadata`` row and its first
  ``document_names`` row in the same transaction as the version insert;
- **metadata observation** — identical bytes arriving for an existing lineage
  under a different file name, title or path create no version, but append a
  ``document_names`` row for the version they matched;
- **conversion** — what the converter read from the file is merged into the
  version's row, idempotently, in the representation's transaction.

The lineage title (``documents.title``) is never changed here: it stays
first-write-wins, so a rename never moves an extraction reuse key.
"""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Connection

from rememberstack.core.document_metadata import family_for_mime
from rememberstack.core.document_metadata import INGEST_METADATA_MAPPING_VERSION
from rememberstack.core.document_metadata import name_text
from rememberstack.core.document_metadata import normalize_address
from rememberstack.core.document_metadata import normalize_name
from rememberstack.model.document_metadata import DocumentMetadata
from rememberstack.model.documents import UploadRecord


def record_ingest_metadata_on(
    *,
    connection: Connection,
    record: UploadRecord,
    doc_id: UUID,
    version_id: UUID,
    mime: str,
) -> None:
    """Write a new version's metadata row and its first observed name.

    ``mime`` is the stored (effective) MIME, so the family always agrees with
    what conversion routes on. Every value here came with the ingest call, so
    its provenance is ``connector``.
    """
    provenance = {
        field: "connector"
        for field, value in (
            ("file_name", record.file_name),
            ("source_path", record.source_path),
            ("title", record.declared_title),
            ("modified_at", record.source_modified_at),
        )
        if value is not None
    }
    connection.execute(
        _INSERT_METADATA,
        {
            "deployment_id": record.deployment_id,
            "version_id": version_id,
            "doc_id": doc_id,
            "family": family_for_mime(mime=mime),
            "file_name": record.file_name,
            "source_path": record.source_path,
            "title": record.declared_title,
            "modified_at": record.source_modified_at,
            "provenance": json.dumps(provenance),
            "mapping_version": INGEST_METADATA_MAPPING_VERSION,
        },
    )
    _insert_name(
        connection=connection,
        deployment_id=record.deployment_id,
        version_id=version_id,
        file_name=record.file_name,
        title=record.declared_title,
        source_path=record.source_path,
    )


def observe_names_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    file_name: str | None,
    title: str | None,
    source_path: str | None,
) -> bool:
    """Append a name row when identical bytes arrive under a different name.

    A value the caller did not send (None) was not observed, so it never
    counts as a change: re-sending a file without a title is not a rename.
    Returns whether a row was appended. A version without a metadata row (a
    forgotten lineage's) records nothing. The version's metadata row is locked
    first, so concurrent name writers compare against each other's rows.
    """
    if not _lock_metadata(
        connection=connection, deployment_id=deployment_id, version_id=version_id
    ):
        return False
    latest = _latest_name(
        connection=connection, deployment_id=deployment_id, version_id=version_id
    )
    observed = {"file_name": file_name, "title": title, "source_path": source_path}
    if latest is not None and all(
        value is None or value == latest[field] for field, value in observed.items()
    ):
        return False
    return _insert_name(
        connection=connection,
        deployment_id=deployment_id,
        version_id=version_id,
        file_name=file_name,
        title=title,
        source_path=source_path,
    )


def merge_converter_metadata_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    metadata: DocumentMetadata,
    mapping_version: str,
) -> None:
    """Merge what the converter read from the file into the version's row.

    The file's own values win for dates, language and thread; the title is
    taken only when the ingest declared none (a caller-declared title stays).
    Authors and recipients replace the version's earlier ``source`` people.
    Running this twice with the same metadata leaves the same rows, so a
    replayed or repeated conversion is safe. The metadata row is locked
    first, so the name comparison sees any observation committed before it.
    """
    if not _lock_metadata(
        connection=connection, deployment_id=deployment_id, version_id=version_id
    ):
        return
    source_fields = {
        field: "source"
        for field, value in (
            ("created_at", metadata.created_at),
            ("modified_at", metadata.modified_at),
            ("language", metadata.language),
            ("thread_ref", metadata.thread_ref),
        )
        if value is not None
    }
    connection.execute(
        _MERGE_METADATA,
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "title": metadata.title,
            "created_at": metadata.created_at,
            "modified_at": metadata.modified_at,
            "language": metadata.language,
            "thread_ref": metadata.thread_ref,
            "extra": json.dumps(metadata.extra, default=str),
            "provenance": json.dumps(source_fields),
            "mapping_version": mapping_version,
        },
    )
    connection.execute(
        _DELETE_SOURCE_PEOPLE,
        {"deployment_id": deployment_id, "version_id": version_id},
    )
    people = [
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "role": role,
            "ordinal": ordinal,
            "display_name": person.name,
            "address": person.address,
            "normalized_name": normalize_name(value=person.name),
            "normalized_address": normalize_address(value=person.address),
        }
        for role, persons in (
            ("author", metadata.authors),
            ("recipient", metadata.recipients),
        )
        for ordinal, person in enumerate(persons)
    ]
    if people:
        connection.execute(_INSERT_SOURCE_PERSON, people)
    if metadata.title is None or not metadata.title.strip():
        return
    latest = _latest_name(
        connection=connection, deployment_id=deployment_id, version_id=version_id
    )
    if latest is not None and latest["title"] == metadata.title:
        return
    _insert_name(
        connection=connection,
        deployment_id=deployment_id,
        version_id=version_id,
        file_name=None if latest is None else latest["file_name"],
        title=metadata.title,
        source_path=None if latest is None else latest["source_path"],
    )


def refresh_family_on(
    *, connection: Connection, deployment_id: UUID, content_hash: str, mime: str
) -> None:
    """Re-derive the family of every version of bytes whose MIME was repaired.

    A MIME repair (D117: parked bytes re-sent with a routable type) changes
    the stored type for every lineage holding those bytes, so every such
    version's family changes with it, in the same transaction.
    """
    connection.execute(
        _REFRESH_FAMILY,
        {
            "deployment_id": deployment_id,
            "content_hash": content_hash,
            "family": family_for_mime(mime=mime),
        },
    )


def _lock_metadata(
    *, connection: Connection, deployment_id: UUID, version_id: UUID
) -> bool:
    """Lock the version's metadata row; False when it has none.

    Every writer of a version's names takes this lock before reading the
    latest name, so read-compare-insert never interleaves.
    """
    return (
        connection.execute(
            _LOCK_METADATA, {"deployment_id": deployment_id, "version_id": version_id}
        ).scalar_one_or_none()
        is not None
    )


def _latest_name(
    *, connection: Connection, deployment_id: UUID, version_id: UUID
) -> dict[str, str | None] | None:
    """The version's most recently observed name, if any."""
    row = (
        connection.execute(
            _SELECT_LATEST_NAME,
            {"deployment_id": deployment_id, "version_id": version_id},
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else dict(row)


def _insert_name(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    file_name: str | None,
    title: str | None,
    source_path: str | None,
) -> bool:
    """Append one observed name; nothing when every part is empty."""
    searchable = name_text(parts=(file_name, title, source_path))
    if not searchable:
        return False
    connection.execute(
        _INSERT_NAME,
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "file_name": file_name,
            "title": title,
            "source_path": source_path,
            "name_text": searchable,
        },
    )
    return True


_INSERT_METADATA = text(
    """
    INSERT INTO document_metadata (
        deployment_id, version_id, doc_id, family, file_name, source_path,
        title, modified_at, provenance, metadata_mapping_version
    ) VALUES (
        :deployment_id, :version_id, :doc_id, :family, :file_name,
        :source_path, :title, :modified_at, CAST(:provenance AS jsonb),
        :mapping_version
    )
    """
)

# clock_timestamp(), not now(): two names observed in one transaction must
# not collide on the (version, observed_at) key.
_INSERT_NAME = text(
    """
    INSERT INTO document_names (
        deployment_id, version_id, observed_at, file_name, title,
        source_path, name_text
    ) VALUES (
        :deployment_id, :version_id, clock_timestamp(), :file_name, :title,
        :source_path, :name_text
    )
    """
)

_REFRESH_FAMILY = text(
    """
    UPDATE document_metadata m
    SET family = :family
    FROM document_versions v
    WHERE v.deployment_id = m.deployment_id
      AND v.version_id = m.version_id
      AND v.deployment_id = :deployment_id
      AND v.content_hash = :content_hash
      AND m.family <> :family
    """
)

_LOCK_METADATA = text(
    """
    SELECT 1 FROM document_metadata
    WHERE deployment_id = :deployment_id AND version_id = :version_id
    FOR UPDATE
    """
)

_SELECT_LATEST_NAME = text(
    """
    SELECT file_name, title, source_path
    FROM document_names
    WHERE deployment_id = :deployment_id AND version_id = :version_id
    ORDER BY observed_at DESC
    LIMIT 1
    """
)

# The title is taken only when none was declared at ingest, or when the one
# present came from an earlier conversion (provenance 'source'). The SET
# expressions read the row's pre-update values, so the provenance update
# repeats the same condition.
_MERGE_METADATA = text(
    """
    UPDATE document_metadata
    SET title = CASE
            WHEN CAST(:title AS text) IS NOT NULL
             AND (title IS NULL OR provenance->>'title' = 'source')
            THEN CAST(:title AS text) ELSE title END,
        provenance = provenance || CAST(:provenance AS jsonb) || CASE
            WHEN CAST(:title AS text) IS NOT NULL
             AND (title IS NULL OR provenance->>'title' = 'source')
            THEN '{"title": "source"}'::jsonb ELSE '{}'::jsonb END,
        created_at = COALESCE(CAST(:created_at AS timestamptz), created_at),
        modified_at = COALESCE(CAST(:modified_at AS timestamptz), modified_at),
        language = COALESCE(CAST(:language AS text), language),
        thread_ref = COALESCE(CAST(:thread_ref AS text), thread_ref),
        extra = extra || CAST(:extra AS jsonb),
        metadata_mapping_version = :mapping_version
    WHERE deployment_id = :deployment_id AND version_id = :version_id
    """
)

_DELETE_SOURCE_PEOPLE = text(
    """
    DELETE FROM document_people
    WHERE deployment_id = :deployment_id AND version_id = :version_id
      AND provenance = 'source'
    """
)

_INSERT_SOURCE_PERSON = text(
    """
    INSERT INTO document_people (
        deployment_id, version_id, role, ordinal, display_name, address,
        normalized_name, normalized_address, provenance
    ) VALUES (
        :deployment_id, :version_id, :role, :ordinal, :display_name, :address,
        :normalized_name, :normalized_address, 'source'
    )
    """
)
