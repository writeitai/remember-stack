"""D140 reference generations: the writes behind supplied and extracted references.

A version's references are produced in *generations* (D140 §6.1): one
production of references for one source version and one origin. Rows in
``document_crossrefs`` are visible only while their generation is ``active``,
and each ``(version, origin)`` has at most one active generation, which a new
generation replaces atomically.

**Supplied references** (§6.3). ``PUT …/references`` records the caller's
NDJSON set as a generation; the latest PUT wins:

- each PUT locks the version's row and orders itself by the next per-version
  ``request_seq``; the version's *intent* is its newest supplied generation
  that is neither superseded nor rejected;
- a body equal to the intent's body is a retry and changes nothing;
- a body equal to the *active* generation's body while a newer one is pending
  cancels the pending one;
- any other body becomes a new ``pending`` generation and supersedes every
  older pending one.

The body is stored content-addressed beside the version's other artifacts
(``<doc_id>/<content_hash>/references/<input_hash>.ndjson``), so a set sent
again reuses the stored object. Rows are written only by the E0 ``crossref``
sub-worker (:meth:`ReferenceCatalog.materialize_supplied`), which validates
the set against the version's structure all-or-nothing and activates it in
the transaction that writes the rows, after re-checking under the version row
lock that the generation is still pending. When the version is already ready
the PUT validates synchronously too, so an unknown source section is reported
at once; otherwise the worker is enqueued when structure completes
(:func:`enqueue_pending_supplied_on`).

**Extracted references** (§6.4) are one generation per (version,
representation, crossreferencer version); they become active with the D65
representation swap (:func:`activate_extracted_on`) or immediately when the
representation is already current.

**Late binding.** A target is named by its source identity
(``source_kind``, ``source_ref``) and resolved to ``to_doc_id`` when the rows
are written; rows still unresolved are bound when a lineage with that
identity is first created (:func:`bind_pending_references_on`, one indexed
update on the ingest path). The two sides serialize on one advisory lock per
deployment and target source kind — shared on the ingest side, exclusive on
the writer side — so a lineage created while a generation is being written
can never miss that generation's rows.
"""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from typing import Final
from typing import Literal
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from remember.models import REFERENCE_ERRORS_MAX
from remember.models import ReferenceGeneration
from remember.models import ReferenceGenerations
from remember.models import ReferenceInput
from remember.models import ReferenceItemError
from remember.models import ReferenceKind
from remember.models import ReferencesSet
from rememberstack.core.storage_routing import storage_class_for_derived
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentVersionNotFoundError
from rememberstack.model import EnqueueWork
from rememberstack.model import ObjectAlreadyExistsError
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model import ReferenceBodyError
from rememberstack.ports.object_store import ObjectStorePort
from rememberstack.spine.work_ledger import enqueue_on

E0_CROSSREF_VERSION: Final = "e0-crossref-2026.10:d140-supplied"
"""The ``crossref`` stage's component version (the supplied-set materializer)."""

MaterializeOutcome = Literal["activated", "rejected", "discarded", "skipped"]


class ExtractedReference(BaseModel):
    """One reference the D36 extraction rungs found in a representation (§6.4).

    Extracted references are always ``floating``. The target is named by
    source identity when the extraction knows it; ``to_section_key`` comes
    from an anchor (``page#per-diem``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: ReferenceKind
    from_section_key: str | None = None
    from_char_start: int | None = Field(default=None, ge=0)
    from_char_end: int | None = Field(default=None, ge=0)
    to_source_kind: str | None = None
    to_source_ref: str | None = None
    to_section_key: str | None = None
    raw_citation: str | None = None
    context: str | None = None


def parse_reference_body(*, body: bytes) -> tuple[ReferenceInput, ...]:
    """Validate an NDJSON reference set: one :class:`ReferenceInput` per line.

    Blank lines are ignored. Raises :class:`ReferenceBodyError` naming the
    first line that is not UTF-8 JSON or not a valid reference.
    """
    items: list[ReferenceInput] = []
    for number, raw in enumerate(body.split(b"\n"), start=1):
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReferenceBodyError(
                line=number, reason=f"not a JSON object: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise ReferenceBodyError(line=number, reason="not a JSON object")
        try:
            items.append(ReferenceInput.model_validate(payload))
        except ValidationError as error:
            problems = "; ".join(
                f"{'.'.join(str(part) for part in problem['loc']) or 'item'}:"
                f" {problem['msg']}"
                for problem in error.errors(include_url=False)
            )
            raise ReferenceBodyError(line=number, reason=problems) from error
    return tuple(items)


def canonical_reference_body(*, references: Sequence[ReferenceInput]) -> bytes:
    """The stored form of a set: one sorted-key JSON object per line.

    Equal sets give equal bytes, so the hash of this body is the set's
    identity (the generation's ``input_hash``).
    """
    lines = [
        json.dumps(
            reference.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        for reference in references
    ]
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


class ReferenceCatalog:
    """Reference generations over an explicitly composed engine and artifact store."""

    def __init__(self, *, engine: Engine, artifact_store: ObjectStorePort) -> None:
        """Bind the catalog to the spine and the artifacts bucket."""
        self._engine = engine
        self._artifact_store = artifact_store

    def set_references(
        self, *, deployment_id: UUID, doc_id: UUID, version_id: UUID, body: bytes
    ) -> ReferencesSet:
        """Record an NDJSON ``body`` as the version's complete supplied set (§6.3).

        Raises ``ReferenceBodyError`` (nothing recorded) for a line that is
        not a valid reference, ``DocumentNotFoundError`` for an absent or
        deleted lineage and ``DocumentVersionNotFoundError`` when the version
        is not a live version of it.
        """
        references = parse_reference_body(body=body)
        body = canonical_reference_body(references=references)
        input_hash = hashlib.sha256(body).hexdigest()
        with self._engine.connect() as connection:
            located = _live_version(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                version_id=version_id,
                lock=False,
            )
        artifact_uri = (
            f"{doc_id}/{located['content_hash']}/references/{input_hash}.ndjson"
        )
        try:
            self._artifact_store.write_bytes(
                key=ObjectKey(artifact_uri),
                content=body,
                storage_class=storage_class_for_derived(),
            )
        except ObjectAlreadyExistsError:
            pass  # the same set was stored before: content addressed
        with self._engine.begin() as connection:
            version = _live_version(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                version_id=version_id,
                lock=True,
            )
            live = [
                _generation(row=row)
                for row in connection.execute(
                    _SELECT_SUPPLIED_INTENT,
                    {"deployment_id": deployment_id, "version_id": version_id},
                ).mappings()
            ]
            intent = live[0] if live else None
            active = next((row for row in live if row.status == "active"), None)
            if intent is not None and intent.input_hash == input_hash:
                return ReferencesSet(
                    doc_id=doc_id,
                    version_id=version_id,
                    outcome="unchanged",
                    generation=intent,
                )
            if active is not None and active.input_hash == input_hash:
                connection.execute(
                    _SUPERSEDE_PENDING_SUPPLIED,
                    {"deployment_id": deployment_id, "version_id": version_id},
                )
                return ReferencesSet(
                    doc_id=doc_id,
                    version_id=version_id,
                    outcome="pending_cancelled",
                    generation=active,
                )
            connection.execute(
                _SUPERSEDE_PENDING_SUPPLIED,
                {"deployment_id": deployment_id, "version_id": version_id},
            )
            request_seq = connection.execute(
                _NEXT_REQUEST_SEQ,
                {"deployment_id": deployment_id, "version_id": version_id},
            ).scalar_one()
            generation_id = uuid4()
            connection.execute(
                _INSERT_SUPPLIED_GENERATION,
                {
                    "generation_id": generation_id,
                    "deployment_id": deployment_id,
                    "doc_id": doc_id,
                    "version_id": version_id,
                    "input_hash": input_hash,
                    "request_seq": request_seq,
                    "artifact_uri": artifact_uri,
                    "item_count": len(references),
                },
            )
            structure_generation_id = version["structure_generation_id"]
            if structure_generation_id is not None:
                errors = section_key_errors_on(
                    connection=connection,
                    deployment_id=deployment_id,
                    structure_generation_id=structure_generation_id,
                    references=references,
                )
                if errors:
                    _reject_on(
                        connection=connection,
                        generation_id=generation_id,
                        errors=errors,
                    )
                else:
                    _enqueue_on(
                        connection=connection,
                        deployment_id=deployment_id,
                        doc_id=doc_id,
                        version_id=version_id,
                        generation_id=generation_id,
                        content_hash=version["content_hash"],
                    )
            created = _generation(
                row=connection.execute(
                    _SELECT_GENERATION, {"generation_id": generation_id}
                )
                .mappings()
                .one()
            )
            return ReferencesSet(
                doc_id=doc_id,
                version_id=version_id,
                outcome="created",
                generation=created,
            )

    def reference_generations(
        self, *, deployment_id: UUID, doc_id: UUID, version_id: UUID
    ) -> ReferenceGenerations:
        """The version's generations, newest first (at most 100, actives always).

        Raises ``DocumentNotFoundError`` / ``DocumentVersionNotFoundError``
        like :meth:`set_references`.
        """
        with self._engine.connect() as connection:
            _live_version(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                version_id=version_id,
                lock=False,
            )
            rows = connection.execute(
                _SELECT_VERSION_GENERATIONS,
                {"deployment_id": deployment_id, "version_id": version_id},
            ).mappings()
            return ReferenceGenerations(
                doc_id=doc_id,
                version_id=version_id,
                generations=tuple(_generation(row=row) for row in rows),
            )

    def materialize_supplied(
        self, *, deployment_id: UUID, generation_id: UUID
    ) -> MaterializeOutcome:
        """Validate a pending supplied generation and activate it (the E0 worker).

        Idempotent on ``generation_id``: a generation that is no longer
        pending (already active, rejected or superseded) is ``skipped``, and
        one superseded by a later PUT while this ran is ``discarded`` without
        writing a row. An unknown ``from_section_key`` rejects the whole set
        and leaves the previously active generation active.
        """
        with self._engine.connect() as connection:
            row = (
                connection.execute(_SELECT_GENERATION, {"generation_id": generation_id})
                .mappings()
                .one_or_none()
            )
        if (
            row is None
            or row["deployment_id"] != deployment_id
            or row["origin"] != "supplied"
            or row["status"] != "pending"
        ):
            return "skipped"
        references = parse_reference_body(
            body=self._artifact_store.read_bytes(key=ObjectKey(row["artifact_uri"]))
        )
        with self._engine.begin() as connection:
            try:
                version = _live_version(
                    connection=connection,
                    deployment_id=deployment_id,
                    doc_id=row["doc_id"],
                    version_id=row["version_id"],
                    lock=True,
                )
            except (DocumentNotFoundError, DocumentVersionNotFoundError):
                return "skipped"  # deleted: its references are never readable
            status = connection.execute(
                _LOCK_GENERATION_STATUS, {"generation_id": generation_id}
            ).scalar_one_or_none()
            if status != "pending":
                return "discarded"
            structure_generation_id = version["structure_generation_id"]
            if structure_generation_id is None:
                # enqueued again when the version's structure completes
                return "skipped"
            errors = section_key_errors_on(
                connection=connection,
                deployment_id=deployment_id,
                structure_generation_id=structure_generation_id,
                references=references,
            )
            if errors:
                _reject_on(
                    connection=connection, generation_id=generation_id, errors=errors
                )
                return "rejected"
            _insert_rows_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=row["doc_id"],
                version_id=row["version_id"],
                generation_id=generation_id,
                origin="supplied",
                rows=[_supplied_row(reference=reference) for reference in references],
            )
            connection.execute(
                _SUPERSEDE_ACTIVE,
                {
                    "deployment_id": deployment_id,
                    "version_id": row["version_id"],
                    "origin": "supplied",
                },
            )
            connection.execute(
                _ACTIVATE_GENERATION,
                {"generation_id": generation_id, "item_count": len(references)},
            )
            return "activated"

    def record_extracted(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        representation_id: UUID,
        crossref_version: str,
        references: Sequence[ExtractedReference],
    ) -> ReferenceGeneration:
        """Write one extracted generation (§6.4), idempotent on its identity.

        The generation is ``(version, representation, crossref_version)``. Its
        rows are written pending; it becomes active now when the
        representation is already the version's current one (a
        crossreferencer bump), otherwise in the transaction of the D65
        representation swap (:func:`activate_extracted_on`).
        """
        generation_id = extracted_generation_id(
            version_id=version_id,
            representation_id=representation_id,
            crossref_version=crossref_version,
        )
        with self._engine.begin() as connection:
            _live_version(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                version_id=version_id,
                lock=True,
            )
            inserted = connection.execute(
                _INSERT_EXTRACTED_GENERATION,
                {
                    "generation_id": generation_id,
                    "deployment_id": deployment_id,
                    "doc_id": doc_id,
                    "version_id": version_id,
                    "representation_id": representation_id,
                    "crossref_version": crossref_version,
                    "input_hash": hashlib.sha256(
                        f"{representation_id}:{crossref_version}".encode()
                    ).hexdigest(),
                    "item_count": len(references),
                },
            ).scalar_one_or_none()
            if inserted is not None:
                _insert_rows_on(
                    connection=connection,
                    deployment_id=deployment_id,
                    doc_id=doc_id,
                    version_id=version_id,
                    generation_id=generation_id,
                    origin="extracted",
                    rows=[
                        _extracted_row(
                            reference=reference, representation_id=representation_id
                        )
                        for reference in references
                    ],
                )
                activate_extracted_on(
                    connection=connection,
                    deployment_id=deployment_id,
                    version_id=version_id,
                    representation_id=representation_id,
                )
            return _generation(
                row=connection.execute(
                    _SELECT_GENERATION, {"generation_id": generation_id}
                )
                .mappings()
                .one()
            )


def extracted_generation_id(
    *, version_id: UUID, representation_id: UUID, crossref_version: str
) -> UUID:
    """The deterministic id of one extracted generation."""
    return uuid5(
        NAMESPACE_URL,
        f"rememberstack:crossref:{version_id}:{representation_id}:{crossref_version}",
    )


def section_key_errors_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    structure_generation_id: UUID,
    references: Sequence[ReferenceInput],
) -> tuple[ReferenceItemError, ...]:
    """Every item whose ``from_section_key`` the structure generation lacks.

    A generation whose sections are not indexed for keys yet (pre-D140,
    awaiting backfill) cannot tell a key exists, so every keyed item is an
    error there rather than broadening the reference to the whole document.
    """
    wanted = sorted(
        {r.from_section_key for r in references if r.from_section_key is not None}
    )
    if not wanted:
        return ()
    parameters = {
        "deployment_id": deployment_id,
        "structure_generation_id": structure_generation_id,
        "keys": wanted,
    }
    unindexed = connection.execute(_GENERATION_UNINDEXED, parameters).scalar_one()
    known = (
        frozenset()
        if unindexed
        else frozenset(connection.execute(_KNOWN_SECTION_KEYS, parameters).scalars())
    )
    errors: list[ReferenceItemError] = []
    for item, reference in enumerate(references, start=1):
        key = reference.from_section_key
        if key is None or key in known:
            continue
        errors.append(
            ReferenceItemError(
                item=item,
                field="from_section_key",
                reason=(
                    "the version's sections are not indexed for keys yet"
                    if unindexed
                    else f"the version has no section with key {key!r}"
                ),
            )
        )
        if len(errors) == REFERENCE_ERRORS_MAX:
            break
    return tuple(errors)


def enqueue_pending_supplied_on(
    *, connection: Connection, deployment_id: UUID, version_id: UUID
) -> int:
    """Enqueue the crossref worker for the version's pending supplied generation.

    Called in the transaction that makes the version ready (structure
    completion), after the version row is written: a PUT that committed
    earlier is visible here, and a later PUT sees the version ready and
    enqueues itself. Returns how many generations were enqueued.
    """
    rows = connection.execute(
        _SELECT_PENDING_SUPPLIED,
        {"deployment_id": deployment_id, "version_id": version_id},
    ).mappings()
    count = 0
    for row in rows:
        _enqueue_on(
            connection=connection,
            deployment_id=deployment_id,
            doc_id=row["doc_id"],
            version_id=version_id,
            generation_id=row["generation_id"],
            content_hash=row["content_hash"],
        )
        count += 1
    return count


def activate_extracted_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    version_id: UUID,
    representation_id: UUID,
) -> bool:
    """Activate the newest pending extracted generation of a current representation.

    Does nothing unless ``representation_id`` is the version's current
    representation, so stale coordinates are never visible: called in the
    D65 swap transaction and right after an extracted generation is written.
    The previously active extracted generation and older pending ones of the
    representation are superseded.
    """
    chosen = connection.execute(
        _SELECT_ACTIVATABLE_EXTRACTED,
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "representation_id": representation_id,
        },
    ).scalar_one_or_none()
    if chosen is None:
        return False
    connection.execute(
        _SUPERSEDE_ACTIVE,
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "origin": "extracted",
        },
    )
    connection.execute(
        _SUPERSEDE_OLDER_EXTRACTED,
        {
            "deployment_id": deployment_id,
            "version_id": version_id,
            "representation_id": representation_id,
            "generation_id": chosen,
        },
    )
    connection.execute(
        _ACTIVATE_GENERATION, {"generation_id": chosen, "item_count": None}
    )
    return True


def bind_pending_references_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    source_kind: str,
    source_ref: str,
) -> int:
    """Late binding: point unresolved references naming this identity at ``doc_id``.

    Called once, in the transaction that first creates the lineage. The
    shared advisory lock orders it against reference writers of the same
    target source kind (see the module docstring). Returns the rows bound.
    """
    connection.execute(
        _LOCK_BINDING_SHARED,
        {"deployment_id": deployment_id, "source_kind": source_kind},
    )
    return connection.execute(
        _BIND_PENDING,
        {
            "deployment_id": deployment_id,
            "doc_id": doc_id,
            "source_kind": source_kind,
            "source_ref": source_ref,
        },
    ).rowcount


def _live_version(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    version_id: UUID,
    lock: bool,
) -> RowMapping:
    """The live version of a live lineage, optionally locked; 404 errors otherwise."""
    row = (
        connection.execute(
            _LOCK_VERSION if lock else _SELECT_VERSION,
            {
                "deployment_id": deployment_id,
                "doc_id": doc_id,
                "version_id": version_id,
            },
        )
        .mappings()
        .one_or_none()
    )
    if row is not None:
        return row
    if (
        connection.execute(
            _SELECT_LIVE_LINEAGE, {"deployment_id": deployment_id, "doc_id": doc_id}
        ).one_or_none()
        is None
    ):
        raise DocumentNotFoundError(f"document {doc_id} does not exist")
    raise DocumentVersionNotFoundError(
        f"version {version_id} is not a live version of document {doc_id}"
    )


def _enqueue_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    version_id: UUID,
    generation_id: UUID,
    content_hash: str,
) -> None:
    """Enqueue the crossref sub-worker for one generation.

    The work is keyed on the generation (``processing_target`` has no
    reference-generation kind, so it is filed under the version's kind with
    the generation's id): every accepted PUT gets its own job, and a retried
    job finds the generation no longer pending and does nothing.
    """
    enqueue_on(
        connection=connection,
        work=EnqueueWork(
            deployment_id=deployment_id,
            target_kind=ProcessingTarget.DOCUMENT_VERSION,
            target_id=generation_id,
            stage=PipelineStage.CROSSREF,
            component_version=E0_CROSSREF_VERSION,
            content_hash=content_hash,
            lane=ProcessingLane.STEADY,
            payload={
                "doc_id": str(doc_id),
                "version_id": str(version_id),
                "generation_id": str(generation_id),
            },
        ),
    )


def _reject_on(
    *, connection: Connection, generation_id: UUID, errors: Sequence[ReferenceItemError]
) -> None:
    connection.execute(
        _REJECT_GENERATION,
        {
            "generation_id": generation_id,
            "errors": json.dumps([error.model_dump(mode="json") for error in errors]),
        },
    )


def _insert_rows_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    version_id: UUID,
    generation_id: UUID,
    origin: str,
    rows: Sequence[dict[str, object]],
) -> None:
    """Write a generation's rows, resolving each named target to its lineage.

    Takes the exclusive binding lock of every target source kind first (in
    sorted order), so a lineage created concurrently either is seen here or
    binds these rows when it commits.
    """
    if not rows:
        return
    for source_kind in sorted(
        {str(row["to_source_kind"]) for row in rows if row["to_source_kind"]}
    ):
        connection.execute(
            _LOCK_BINDING_EXCLUSIVE,
            {"deployment_id": deployment_id, "source_kind": source_kind},
        )
    connection.execute(
        _INSERT_CROSSREFS,
        {
            "deployment_id": deployment_id,
            "doc_id": doc_id,
            "version_id": version_id,
            "generation_id": generation_id,
            "origin": origin,
            "rows": json.dumps(
                [{"crossref_id": str(uuid4()), **row} for row in rows], default=str
            ),
        },
    )


def _supplied_row(*, reference: ReferenceInput) -> dict[str, object]:
    return {
        "from_section_key": reference.from_section_key,
        "from_representation_id": None,
        "from_char_start": None,
        "from_char_end": None,
        "kind": reference.kind,
        "source_label": reference.source_label,
        "to_source_kind": reference.target.source_kind,
        "to_source_ref": reference.target.source_ref,
        "to_version_key": reference.target.version_key,
        "to_section_key": reference.target.section_key,
        "binding": reference.binding,
        "change_effective_from": (
            None
            if reference.change_effective_from is None
            else reference.change_effective_from.isoformat()
        ),
        "change_date_known": reference.change_date_known,
        "raw_citation": None,
        "context": reference.context,
    }


def _extracted_row(
    *, reference: ExtractedReference, representation_id: UUID
) -> dict[str, object]:
    spanned = reference.from_char_start is not None
    return {
        "from_section_key": reference.from_section_key,
        "from_representation_id": str(representation_id) if spanned else None,
        "from_char_start": reference.from_char_start,
        "from_char_end": reference.from_char_end,
        "kind": reference.kind,
        "source_label": None,
        "to_source_kind": reference.to_source_kind,
        "to_source_ref": reference.to_source_ref,
        "to_version_key": None,
        "to_section_key": reference.to_section_key,
        "binding": "floating",
        "change_effective_from": None,
        "change_date_known": False if reference.kind == "amends" else None,
        "raw_citation": reference.raw_citation,
        "context": reference.context,
    }


def _generation(*, row: RowMapping) -> ReferenceGeneration:
    errors = row["errors"] or []
    return ReferenceGeneration(
        generation_id=row["generation_id"],
        doc_id=row["doc_id"],
        version_id=row["version_id"],
        origin=row["origin"],
        status=row["status"],
        request_seq=row["request_seq"],
        input_hash=row["input_hash"],
        item_count=row["item_count"],
        representation_id=row["representation_id"],
        crossref_version=row["crossref_version"],
        errors=tuple(ReferenceItemError.model_validate(error) for error in errors),
        created_at=row["created_at"],
        activated_at=row["activated_at"],
    )


_GENERATION_COLUMNS = """
    generation_id, deployment_id, doc_id, version_id, origin::text AS origin,
    status, request_seq, input_hash, item_count, representation_id,
    crossref_version, artifact_uri, errors, created_at, activated_at
"""

_VERSION_COLUMNS = """
    SELECT v.version_id, v.content_hash,
           CASE WHEN v.status::text = 'ready' AND r.status::text = 'ready'
                THEN r.current_structure_generation_id END
               AS structure_generation_id
    FROM document_versions v
    JOIN documents d
      ON d.deployment_id = v.deployment_id
     AND d.doc_id = v.doc_id
     AND d.deleted_at IS NULL
    LEFT JOIN document_representations r
      ON r.representation_id = v.current_representation_id
    WHERE v.deployment_id = :deployment_id
      AND v.doc_id = :doc_id
      AND v.version_id = :version_id
      AND v.deleted_at IS NULL
"""

_SELECT_VERSION = text(_VERSION_COLUMNS)

# The version row is the serialization point of a version's reference writes
# (§6.3): PUTs are ordered by it and the worker re-checks under it.
_LOCK_VERSION = text(_VERSION_COLUMNS + "    FOR UPDATE OF v\n")

_SELECT_LIVE_LINEAGE = text(
    """
    SELECT 1 FROM documents
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      AND deleted_at IS NULL
    """
)

_SELECT_GENERATION = text(
    f"SELECT {_GENERATION_COLUMNS} FROM document_reference_generations"
    " WHERE generation_id = :generation_id"
)

_LOCK_GENERATION_STATUS = text(
    """
    SELECT status FROM document_reference_generations
    WHERE generation_id = :generation_id
    FOR UPDATE
    """
)

_SELECT_SUPPLIED_INTENT = text(
    f"""
    SELECT {_GENERATION_COLUMNS}
    FROM document_reference_generations
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND origin = 'supplied'
      AND status IN ('pending', 'active')
    ORDER BY request_seq DESC
    """
)

_SELECT_VERSION_GENERATIONS = text(
    f"""
    SELECT {_GENERATION_COLUMNS}
    FROM document_reference_generations
    WHERE generation_id IN (
        (SELECT generation_id FROM document_reference_generations
         WHERE deployment_id = :deployment_id AND version_id = :version_id
         ORDER BY created_at DESC, generation_id DESC
         LIMIT 100)
        UNION
        SELECT generation_id FROM document_reference_generations
        WHERE deployment_id = :deployment_id AND version_id = :version_id
          AND status = 'active'
    )
    ORDER BY created_at DESC, generation_id DESC
    """
)

_SELECT_PENDING_SUPPLIED = text(
    """
    SELECT g.generation_id, g.doc_id, v.content_hash
    FROM document_reference_generations g
    JOIN document_versions v
      ON v.deployment_id = g.deployment_id AND v.version_id = g.version_id
    WHERE g.deployment_id = :deployment_id
      AND g.version_id = :version_id
      AND g.origin = 'supplied'
      AND g.status = 'pending'
    ORDER BY g.request_seq
    """
)

_NEXT_REQUEST_SEQ = text(
    """
    SELECT coalesce(max(request_seq), 0) + 1
    FROM document_reference_generations
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND origin = 'supplied'
    """
)

_SUPERSEDE_PENDING_SUPPLIED = text(
    """
    UPDATE document_reference_generations
    SET status = 'superseded'
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND origin = 'supplied'
      AND status = 'pending'
    """
)

_SUPERSEDE_ACTIVE = text(
    """
    UPDATE document_reference_generations
    SET status = 'superseded'
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND origin = CAST(:origin AS crossref_origin)
      AND status = 'active'
    """
)

_ACTIVATE_GENERATION = text(
    """
    UPDATE document_reference_generations
    SET status = 'active',
        activated_at = clock_timestamp(),
        item_count = coalesce(CAST(:item_count AS integer), item_count)
    WHERE generation_id = :generation_id
    """
)

_REJECT_GENERATION = text(
    """
    UPDATE document_reference_generations
    SET status = 'rejected', errors = CAST(:errors AS jsonb)
    WHERE generation_id = :generation_id
    """
)

_INSERT_SUPPLIED_GENERATION = text(
    """
    INSERT INTO document_reference_generations (
        generation_id, deployment_id, doc_id, version_id, origin, input_hash,
        request_seq, artifact_uri, item_count, status
    ) VALUES (
        :generation_id, :deployment_id, :doc_id, :version_id, 'supplied',
        :input_hash, :request_seq, :artifact_uri, :item_count, 'pending'
    )
    """
)

_INSERT_EXTRACTED_GENERATION = text(
    """
    INSERT INTO document_reference_generations (
        generation_id, deployment_id, doc_id, version_id, origin,
        representation_id, crossref_version, input_hash, item_count, status
    ) VALUES (
        :generation_id, :deployment_id, :doc_id, :version_id, 'extracted',
        :representation_id, :crossref_version, :input_hash, :item_count, 'pending'
    )
    ON CONFLICT (generation_id) DO NOTHING
    RETURNING generation_id
    """
)

_SELECT_ACTIVATABLE_EXTRACTED = text(
    """
    SELECT g.generation_id
    FROM document_reference_generations g
    JOIN document_versions v
      ON v.deployment_id = g.deployment_id
     AND v.version_id = g.version_id
     AND v.current_representation_id = g.representation_id
    WHERE g.deployment_id = :deployment_id
      AND g.version_id = :version_id
      AND g.representation_id = :representation_id
      AND g.origin = 'extracted'
      AND g.status = 'pending'
    ORDER BY g.created_at DESC, g.generation_id DESC
    LIMIT 1
    """
)

_SUPERSEDE_OLDER_EXTRACTED = text(
    """
    UPDATE document_reference_generations
    SET status = 'superseded'
    WHERE deployment_id = :deployment_id
      AND version_id = :version_id
      AND representation_id = :representation_id
      AND origin = 'extracted'
      AND status = 'pending'
      AND generation_id <> :generation_id
    """
)

_GENERATION_UNINDEXED = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM document_sections
        WHERE deployment_id = :deployment_id
          AND structure_generation_id = :structure_generation_id
          AND own_content_hash IS NULL
    )
    """
)

_KNOWN_SECTION_KEYS = text(
    """
    SELECT section_key FROM document_sections
    WHERE deployment_id = :deployment_id
      AND structure_generation_id = :structure_generation_id
      AND section_key = ANY(CAST(:keys AS text[]))
    """
)

_LOCK_BINDING_SHARED = text(
    """
    SELECT pg_advisory_xact_lock_shared(hashtextextended(
        'crossref-binding:' || CAST(:deployment_id AS text) || ':' || :source_kind, 0
    ))
    """
)

_LOCK_BINDING_EXCLUSIVE = text(
    """
    SELECT pg_advisory_xact_lock(hashtextextended(
        'crossref-binding:' || CAST(:deployment_id AS text) || ':' || :source_kind, 0
    ))
    """
)

_BIND_PENDING = text(
    """
    UPDATE document_crossrefs
    SET to_doc_id = :doc_id, resolved = true
    WHERE deployment_id = :deployment_id
      AND to_source_kind = :source_kind
      AND to_source_ref = :source_ref
      AND to_doc_id IS NULL
    """
)

# One statement per generation; a target is resolved by its source identity
# through the documents unique index (a tombstoned lineage binds too, so its
# revival resolves without re-binding, D140 §9).
_INSERT_CROSSREFS = text(
    """
    INSERT INTO document_crossrefs (
        crossref_id, deployment_id, from_doc_id, from_version_id, generation_id,
        from_section_key, from_representation_id, from_char_start, from_char_end,
        kind, origin, source_label, to_source_kind, to_source_ref, to_version_key,
        to_section_key, binding, change_effective_from, change_date_known,
        to_doc_id, resolved, raw_citation, context
    )
    SELECT r.crossref_id, :deployment_id, :doc_id, :version_id, :generation_id,
           r.from_section_key, r.from_representation_id, r.from_char_start,
           r.from_char_end, CAST(r.kind AS crossref_kind),
           CAST(:origin AS crossref_origin), r.source_label, r.to_source_kind,
           r.to_source_ref, r.to_version_key, r.to_section_key,
           CAST(r.binding AS crossref_binding), r.change_effective_from,
           r.change_date_known, d.doc_id, d.doc_id IS NOT NULL, r.raw_citation,
           r.context
    FROM jsonb_to_recordset(CAST(:rows AS jsonb)) AS r (
        crossref_id uuid,
        from_section_key text,
        from_representation_id uuid,
        from_char_start integer,
        from_char_end integer,
        kind text,
        source_label text,
        to_source_kind text,
        to_source_ref text,
        to_version_key text,
        to_section_key text,
        binding text,
        change_effective_from timestamptz,
        change_date_known boolean,
        raw_citation text,
        context text
    )
    LEFT JOIN documents d
      ON d.deployment_id = :deployment_id
     AND d.source_kind = r.to_source_kind
     AND d.source_ref = r.to_source_ref
    """
)
