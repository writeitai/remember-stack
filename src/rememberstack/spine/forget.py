"""D159 forget: the record, the re-ingest guard and the PostgreSQL sweep.

The forget record (``forgets``) is the only forget state. The hide (in
``workers.forget``) tombstones the lineage and inserts the record in one
transaction; this module owns the record and the sweep's PostgreSQL step:
collect content-free IDs, then delete or clear everything derived from the
document, in one transaction, treating "already gone" as success.
"""

from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
import re
from typing import Any
from typing import Final
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core import source_identity_hash
from rememberstack.core.fact_label import deterministic_fact_label
from rememberstack.model import DocumentForgottenError
from rememberstack.model import EnqueueWork
from rememberstack.model import Forget
from rememberstack.model import ForgetNotCompleteError
from rememberstack.model import ForgetNotFoundError
from rememberstack.model import ForgetStatus
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.spine.work_ledger import enqueue_on
from rememberstack.spine.work_ledger import FORGOTTEN_WORK

FORGET_COMPONENT_VERSION: Final = "forget-v1"
"""The sweep generation; each sweep's work row is ``forget-v1/<sweep>``."""

_CLEARED_RATIONALE: Final = "Rationale cleared by a forget."

MAX_SWEEP_ATTEMPTS: Final = 32_767
"""A sweep row's attempt ceiling: a crashed worker's sweep is simply retried."""


_REPLACEMENT_LINEAGE: Final = uuid5(NAMESPACE_URL, "rememberstack:replacement-lineage")
"""The namespace of re-added lineages' IDs, disjoint from identity base IDs."""


@dataclass(frozen=True)
class SweepFindings:
    """What one sweep's PostgreSQL step found and what the later steps act on."""

    removed: bool
    """The step deleted or cleared residue; the job sweeps again after a quiet period."""
    object_keys: tuple[ObjectKey, ...]
    """Opaque original keys to purge (retired raw originals, D147 locators)."""
    compiled_artifact_ids: tuple[UUID, ...]
    """Compiled K pages marked for deletion and rebuild, until step 4 drops them."""
    profile_entity_ids: tuple[UUID, ...]
    """Surviving entities whose profile this step cleared, for a refresh."""
    fixture_keys: tuple[ObjectKey, ...] = ()
    """Plan-run transcripts deleted with the deployment's plan runs: purged,
    but never counted as the document's residue."""


def sweep_work(
    *,
    deployment_id: UUID,
    forget_id: UUID,
    sweep: int,
    failures: int,
    not_before: datetime | None,
    first: bool = False,
    reapply: bool = False,
    series: str = "",
) -> EnqueueWork:
    """One sweep's work row: it targets the record, one row per sweep (D159 §3.4).

    ``first`` marks the sweep right after a hide or a reapply: it is never the
    last one, because it does not follow a quiet period. ``reapply`` marks a
    series ``forget-reapply`` started after a restore. ``series`` names the
    current series of sweeps (empty for the one the hide starts); a sweep of an
    older series does nothing. The row has no practical attempt cap.
    """
    return EnqueueWork(
        deployment_id=deployment_id,
        target_kind=ProcessingTarget.FORGET,
        target_id=forget_id,
        stage=PipelineStage.FORGET,
        component_version=(
            f"{FORGET_COMPONENT_VERSION}/{series}/{sweep}"
            if series
            else f"{FORGET_COMPONENT_VERSION}/{sweep}"
        ),
        content_hash=str(forget_id),
        lane=None,
        payload={
            "forget_id": str(forget_id),
            "sweep": sweep,
            "failures": failures,
            "first": first,
            "reapply": reapply,
            "series": series,
        },
        not_before=not_before,
        max_attempts=MAX_SWEEP_ATTEMPTS,
    )


class ForgetCatalog:
    """Own forget records, the re-ingest guard and the PostgreSQL sweep step."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the authoritative spine database."""
        self._engine = engine

    def record(self, *, deployment_id: UUID, forget_id: UUID) -> Forget:
        """Return one record, or raise ``ForgetNotFoundError``."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(_SELECT + " WHERE deployment_id = :d AND forget_id = :f"),
                    {"d": deployment_id, "f": forget_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ForgetNotFoundError(str(forget_id))
        return _forget(row=row)

    def records(self, *, deployment_id: UUID) -> tuple[Forget, ...]:
        """Every record of a deployment, oldest first."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    _SELECT
                    + " WHERE deployment_id = :d ORDER BY requested_at, forget_id"
                ),
                {"d": deployment_id},
            ).mappings()
            return tuple(_forget(row=row) for row in rows)

    def record_for_doc_on(
        self, *, connection: Connection, deployment_id: UUID, doc_id: UUID
    ) -> Forget | None:
        """The lineage's record, read in the caller's transaction."""
        row = (
            connection.execute(
                text(_SELECT + " WHERE deployment_id = :d AND doc_id = :doc"),
                {"d": deployment_id, "doc": doc_id},
            )
            .mappings()
            .one_or_none()
        )
        return _forget(row=row) if row is not None else None

    def insert_on(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        doc_id: UUID,
        forget_id: UUID,
    ) -> tuple[Forget, bool]:
        """Insert the record under the lineage row lock the caller holds.

        Returns the record and whether this call created it; a concurrent
        forget that committed first wins and its record is returned.
        """
        document = (
            connection.execute(
                text("""SELECT source_kind, source_ref FROM documents
                WHERE deployment_id = :d AND doc_id = :doc"""),
                {"d": deployment_id, "doc": doc_id},
            )
            .mappings()
            .one()
        )
        # Every lineage's identity is guarded, a one-shot upload's included
        # (its source_ref is its content hash): an upload lineage may also
        # carry versions a caller sent under the same source_ref, and none of
        # them may come back unguarded (D159 §3.6).
        identity = (
            None
            if document["source_ref"] is None
            else source_identity_hash(
                deployment_id=deployment_id,
                source_kind=str(document["source_kind"]),
                source_ref=str(document["source_ref"]),
            )
        )
        row = (
            connection.execute(
                _INSERT_RECORD,
                {
                    "forget_id": forget_id,
                    "d": deployment_id,
                    "doc": doc_id,
                    "identity": identity,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            return _forget(row=row), True
        existing = self.record_for_doc_on(
            connection=connection, deployment_id=deployment_id, doc_id=doc_id
        )
        if existing is None:
            raise RuntimeError(f"forget record for {doc_id} vanished on conflict")
        return existing, False

    def rearm_on(self, *, connection: Connection, forget: Forget) -> str:
        """``forget-reapply``: erasing again under a new series of sweeps.

        Returns the new series; sweeps of an older series do nothing, so one
        still waiting out its quiet period cannot complete the record early.
        """
        series = uuid4().hex
        connection.execute(
            text("""UPDATE forgets SET status = 'erasing', completed_at = NULL,
            collected = jsonb_set(COALESCE(collected, '{}'::jsonb), '{series}',
                                  to_jsonb(CAST(:series AS text)))
            WHERE deployment_id = :d AND forget_id = :f"""),
            {"d": forget.deployment_id, "f": forget.forget_id, "series": series},
        )
        return series

    def current_series(self, *, forget: Forget) -> str:
        """The series of sweeps currently allowed to finish the record."""
        with self._engine.connect() as connection:
            return str(
                connection.execute(
                    text("""SELECT COALESCE(collected->>'series', '') FROM forgets
                    WHERE deployment_id = :d AND forget_id = :f"""),
                    {"d": forget.deployment_id, "f": forget.forget_id},
                ).scalar_one()
            )

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
        allow_forgotten: bool = False,
    ) -> None:
        """Refuse forgotten content before any bytes are written (D159 §3.6)."""
        with self._engine.connect() as connection:
            guard_forgotten_on(
                connection=connection,
                deployment_id=deployment_id,
                source_kind=source_kind,
                source_ref=source_ref,
                content_hash=content_hash,
                allow_forgotten=allow_forgotten,
            )

    def lineage_doc_id(
        self, *, deployment_id: UUID, source_kind: str, source_ref: str
    ) -> UUID:
        """The ``doc_id`` E0 gives this identity's lineage (D159 §3.6)."""
        with self._engine.connect() as connection:
            return lineage_doc_id_on(
                connection=connection,
                deployment_id=deployment_id,
                source_kind=source_kind,
                source_ref=source_ref,
            )

    def sweep_postgres(self, *, forget: Forget) -> SweepFindings:
        """Collect, then delete or clear, in one transaction (D159 §3.3 steps 0-1)."""
        with self._engine.begin() as connection:
            return _Sweep(connection=connection, forget=forget).run()

    def accepted_hashes(
        self, *, deployment_id: UUID, artifact_ids: frozenset[UUID]
    ) -> dict[UUID, str | None]:
        """Each compiled page's accepted body hash (None while it awaits a rebuild)."""
        with self._engine.connect() as connection:
            return {
                row.artifact_id: row.content_hash
                for row in connection.execute(
                    text("""SELECT artifact_id, content_hash FROM knowledge_artifacts
                    WHERE deployment_id = :d
                      AND artifact_id = ANY(CAST(:ids AS uuid[]))"""),
                    {"d": deployment_id, "ids": sorted(artifact_ids, key=str)},
                )
            }

    def finish(
        self, *, forget: Forget, blocking_k_paths: tuple[str, ...], series: str
    ) -> ForgetStatus:
        """A clean sweep after the quiet period: complete, or wait for redaction."""
        status = (
            ForgetStatus.WAITING_FOR_REDACTION
            if blocking_k_paths
            else ForgetStatus.COMPLETE
        )
        with self._engine.begin() as connection:
            connection.execute(
                text("""UPDATE forgets SET status = CAST(:status AS forget_status),
                blocking_k_paths = CAST(:paths AS text[]), last_error = NULL,
                completed_at = CASE WHEN :status = 'complete'
                                    THEN COALESCE(completed_at, now()) END
                WHERE deployment_id = :d AND forget_id = :f
                  AND COALESCE(collected->>'series', '') = :series"""),
                {
                    "d": forget.deployment_id,
                    "f": forget.forget_id,
                    "status": status.value,
                    "paths": sorted(blocking_k_paths),
                    "series": series,
                },
            )
        return status

    def live_originals(self, *, forget: Forget) -> frozenset[str]:
        """Original keys under the document's prefix another lineage still uses."""
        with self._engine.connect() as connection:
            return frozenset(
                str(value)
                for value in connection.execute(
                    text("""SELECT raw_uri FROM content_objects
                    WHERE deployment_id = :d AND purged_at IS NULL
                      AND (raw_uri LIKE CAST(:doc AS text) || '/%'
                           OR raw_uri LIKE 'uploads/%')
                      AND content_hash = ANY(CAST(:hashes AS text[]))"""),
                    {
                        "d": forget.deployment_id,
                        "doc": forget.doc_id,
                        "hashes": list(forget.content_hashes),
                    },
                ).scalars()
            )

    def clear_k_pending(
        self, *, forget: Forget, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """The rebuild and history erasure of these pages succeeded."""
        done = {str(value) for value in artifact_ids}
        with self._engine.begin() as connection:
            collected = _collected(
                connection=connection,
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
                lock=True,
            )
            collected["k_pending"] = [
                value for value in collected["k_pending"] if value not in done
            ]
            _store_collected(connection=connection, forget=forget, collected=collected)

    def mark_stale(
        self, *, forget: Forget, artifact_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        """Reapply: recorded pages that are still compiled are rebuilt.

        Returns the pages it marked; a retired page stays retired (its
        restored copy is still dropped). A page adopted as authored since
        keeps its body and history: only its owner edits it.
        """
        with self._engine.begin() as connection:
            return tuple(
                sorted(
                    connection.execute(
                        text("""UPDATE knowledge_artifacts
                        SET page_summary=NULL, content_hash=NULL, inputs_hash=NULL,
                            last_compiled_at=NULL,
                            status=CASE WHEN status='tombstoned' THEN status
                                        ELSE 'stale' END
                        WHERE deployment_id=:deployment_id
                          AND artifact_id=ANY(CAST(:ids AS uuid[]))
                          AND page_kind='compiled'
                        RETURNING artifact_id"""),
                        {
                            "deployment_id": forget.deployment_id,
                            "ids": list(artifact_ids),
                        },
                    ).scalars(),
                    key=str,
                )
            )

    def hashes_in_use(
        self, *, forget: Forget, hashes: tuple[str, ...]
    ) -> frozenset[str]:
        """Content hashes a surviving version of any lineage still uses.

        A version row of a lineage that has a forget record does not count;
        an archived or D135-deleted lineage's version does.
        """
        if not hashes:
            return frozenset()
        with self._engine.connect() as connection:
            return frozenset(
                str(value)
                for value in connection.execute(
                    text("""SELECT DISTINCT v.content_hash FROM document_versions v
                    WHERE v.deployment_id = :d
                      AND v.content_hash = ANY(CAST(:hashes AS text[]))
                      AND NOT EXISTS (SELECT 1 FROM forgets f
                                      WHERE f.deployment_id = v.deployment_id
                                        AND f.doc_id = v.doc_id)"""),
                    {"d": forget.deployment_id, "hashes": list(hashes)},
                ).scalars()
            )

    def collected_artifact_ids(self, *, forget: Forget) -> tuple[UUID, ...]:
        """The compiled K artifacts the record names, for a reapply."""
        with self._engine.connect() as connection:
            collected = _collected(
                connection=connection,
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
            )
        return tuple(UUID(value) for value in collected["k_artifact_ids"])

    def needles(self, *, forget: Forget) -> tuple[str, ...]:
        """The IDs whose presence in a K file means it still cites the document.

        The document, its versions and every collected fact: citations name
        a document (claim citations carry its lineage) or a fact. Each ID in
        both spellings a citation may use: hyphenated and 32 hex digits.
        """
        with self._engine.connect() as connection:
            collected = _collected(
                connection=connection,
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
            )
        ids = {
            forget.doc_id,
            *forget.version_ids,
            *(UUID(value) for value in collected["fact_ids"]),
        }
        return tuple(sorted({*map(str, ids), *(value.hex for value in ids)}))

    def page_needles(self, *, forget: Forget) -> tuple[str, ...]:
        """The needles of IDs that no longer exist: the document, its versions
        and its exclusive facts. A compiled page naming one is forgotten text;
        a shared fact a rebuilt page renders is not."""
        with self._engine.connect() as connection:
            collected = _collected(
                connection=connection,
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
            )
            surviving = {
                str(value)
                for value in connection.execute(
                    text("""SELECT relation_id FROM relations
                    WHERE deployment_id = :d AND relation_id = ANY(CAST(:ids AS uuid[]))
                    UNION ALL SELECT observation_id FROM observations
                    WHERE deployment_id = :d
                      AND observation_id = ANY(CAST(:ids AS uuid[]))"""),
                    {"d": forget.deployment_id, "ids": collected["fact_ids"]},
                ).scalars()
            }
        ids = {
            forget.doc_id,
            *forget.version_ids,
            *(UUID(value) for value in collected["fact_ids"] if value not in surviving),
        }
        return tuple(sorted({*map(str, ids), *(value.hex for value in ids)}))

    def pages_dropped(self, *, forget: Forget, artifact_ids: frozenset[UUID]) -> None:
        """Compiled pages the K step dropped because their body named the document.

        They are marked stale at once (called under the K lease) and the next
        sweep's PostgreSQL step treats them as collected pages (summaries and
        transcripts erased).
        """
        with self._engine.begin() as connection:
            connection.execute(
                _COMPILED_ARTIFACT_STATEMENTS[1],
                {
                    "deployment_id": forget.deployment_id,
                    "compiled_artifact_ids": sorted(artifact_ids, key=str),
                },
            )
            collected = _collected(
                connection=connection,
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
                lock=True,
            )
            collected["k_dropped"] = sorted(
                {*collected["k_dropped"], *map(str, artifact_ids)}
            )
            _store_collected(connection=connection, forget=forget, collected=collected)

    def waiting(self, *, deployment_id: UUID) -> tuple[Forget, ...]:
        """Records waiting for an owner to redact authored pages or sidecars."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                text(
                    _SELECT
                    + " WHERE deployment_id = :d"
                    + " AND status = 'waiting_for_redaction'"
                    + " ORDER BY requested_at, forget_id"
                ),
                {"d": deployment_id},
            ).mappings()
            return tuple(_forget(row=row) for row in rows)

    def redaction_progress(
        self, *, forget: Forget, blocking_k_paths: tuple[str, ...]
    ) -> ForgetStatus:
        """The K sync's check of a waiting record: complete once nothing cites.

        A record a ``forget-reapply`` re-armed meanwhile is left to its sweeps.
        """
        status = (
            ForgetStatus.WAITING_FOR_REDACTION
            if blocking_k_paths
            else ForgetStatus.COMPLETE
        )
        with self._engine.begin() as connection:
            connection.execute(
                text("""UPDATE forgets SET status = CAST(:status AS forget_status),
                blocking_k_paths = CAST(:paths AS text[]), last_error = NULL,
                completed_at = CASE WHEN :status = 'complete' THEN now() END
                WHERE deployment_id = :d AND forget_id = :f
                  AND status = 'waiting_for_redaction'"""),
                {
                    "d": forget.deployment_id,
                    "f": forget.forget_id,
                    "status": status.value,
                    "paths": sorted(blocking_k_paths),
                },
            )
        return status

    def schedule(self, *, work: EnqueueWork) -> None:
        """Enqueue one sweep outside a handler's completion transaction."""
        with self._engine.begin() as connection:
            enqueue_on(connection=connection, work=work)

    def record_error(self, *, forget: Forget, code: str) -> None:
        """Keep the content-free code of a failed sweep visible on the record."""
        with self._engine.begin() as connection:
            connection.execute(
                text("""UPDATE forgets SET last_error = :code
                WHERE deployment_id = :d AND forget_id = :f"""),
                {"d": forget.deployment_id, "f": forget.forget_id, "code": code},
            )


def guard_forgotten_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    source_kind: str,
    source_ref: str,
    content_hash: str,
    allow_forgotten: bool = False,
) -> tuple[UUID, ...]:
    """Check the forget records that guard this identity or hash (D159 §3.6).

    Called before bytes are written and again at registration under the
    lineage row lock. A record's released hash or identity no longer guards
    (a deliberate re-add); the sweep keys never change.

    Without ``allow_forgotten`` a match raises ``DocumentForgottenError``.
    With it, a match still ``erasing`` raises ``ForgetNotCompleteError``;
    otherwise the matching records' IDs are returned for
    :func:`release_forgotten_on`.
    """
    rows = (
        connection.execute(
            _FORGOTTEN_INGEST,
            {
                "d": deployment_id,
                "identity": _identity(
                    deployment_id=deployment_id,
                    source_kind=source_kind,
                    source_ref=source_ref,
                ),
                "content_hash": content_hash,
            },
        )
        .mappings()
        .all()
    )
    if not rows:
        return ()
    if not allow_forgotten:
        raise DocumentForgottenError(
            forget_id=rows[0]["forget_id"], forgotten_at=rows[0]["requested_at"]
        )
    for row in rows:
        if row["status"] == ForgetStatus.ERASING.value:
            raise ForgetNotCompleteError(forget_id=row["forget_id"])
    return tuple(row["forget_id"] for row in rows)


def release_forgotten_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    forget_ids: tuple[UUID, ...],
    source_kind: str,
    source_ref: str,
    content_hash: str,
) -> None:
    """Note what this re-add matched on the records of its own lineage (D159 §3.6).

    Of the matched records, only those of the lineage being added back (the
    lineages E0 derived for this identity) are touched: the content hash goes
    into ``released_hashes`` where the record holds it, and
    ``identity_released`` is set where the identity matched. Another forget
    that happens to share the bytes keeps its guard. The records' sweep keys
    (``version_ids``, ``content_hashes``, the identity) never change, so a
    later sweep or ``forget-reapply`` works as before.
    """
    if not forget_ids:
        return
    forgotten = _lineage_ids(
        connection=connection,
        deployment_id=deployment_id,
        source_kind=source_kind,
        source_ref=source_ref,
    )[:-1]
    if forgotten:
        connection.execute(
            _RELEASE_FORGOTTEN,
            {
                "d": deployment_id,
                "ids": list(forget_ids),
                "lineages": list(forgotten),
                "identity": _identity(
                    deployment_id=deployment_id,
                    source_kind=source_kind,
                    source_ref=source_ref,
                ),
                "content_hash": content_hash,
            },
        )


def lineage_doc_id_on(
    *, connection: Connection, deployment_id: UUID, source_kind: str, source_ref: str
) -> UUID:
    """The ``doc_id`` E0 derives for an identity's lineage (D159 §3.6).

    Until a lineage of the identity is forgotten it is the identity's base
    ID. A replacement lineage's ID is derived from the base ID and the number
    of forget records of the identity's lineages, in a namespace of its own
    so it never equals another identity's base ID. It therefore shares
    nothing keyed by the ID (rows, object prefix, corpus suffix, K
    citations) with a forgotten lineage. Records are never deleted, so the
    count, and the ID, are stable.
    """
    return _lineage_ids(
        connection=connection,
        deployment_id=deployment_id,
        source_kind=source_kind,
        source_ref=source_ref,
    )[-1]


def _lineage_ids(
    *, connection: Connection, deployment_id: UUID, source_kind: str, source_ref: str
) -> tuple[UUID, ...]:
    """The identity's forgotten lineage IDs, oldest first, then its current one.

    Each forgotten lineage was itself derived this way, so walking the
    derivation finds exactly the identity's forget records.
    """
    base = uuid5(
        NAMESPACE_URL, f"rememberstack:{source_kind}:{deployment_id}:{source_ref}"
    )
    lineages = [base]
    while (
        connection.execute(
            text("SELECT 1 FROM forgets WHERE deployment_id = :d AND doc_id = :doc"),
            {"d": deployment_id, "doc": lineages[-1]},
        ).first()
        is not None
    ):
        lineages.append(uuid5(_REPLACEMENT_LINEAGE, f"{base}:{len(lineages)}"))
    return tuple(lineages)


def _identity(*, deployment_id: UUID, source_kind: str, source_ref: str) -> str:
    """The request's source-identity hash, a one-shot upload's included."""
    return source_identity_hash(
        deployment_id=deployment_id, source_kind=source_kind, source_ref=source_ref
    )


_LINEAGE_ORIGINAL = re.compile(r"(?P<owner>[0-9a-f-]{36})/(?P<hash>[0-9a-f]{64})/.+")


def locator_hash(*, locator: str) -> str | None:
    """The content hash of an ``<owner>/<hash>/`` locator, else ``None``."""
    match = _LINEAGE_ORIGINAL.fullmatch(locator + "x")
    return match["hash"] if match is not None and locator.endswith("/") else None


def original_locator(*, key: str, doc_id: UUID) -> str | None:
    """The content-free locator a record keeps for one object key.

    A key under the document's own prefix needs none (the prefix purge covers
    it). An original under another lineage's prefix is kept as that
    ``<owner>/<hash>/`` prefix, so the file name it was stored with (its
    extension) never lands on the record. Other keys (D147 ``uploads/<id>``,
    transcripts) are opaque and kept as they are. A locator ending in ``/``
    is purged as a prefix.
    """
    if key.startswith(f"{doc_id}/"):
        return None
    if match := _LINEAGE_ORIGINAL.fullmatch(key):
        return f"{match['owner']}/{match['hash']}/"
    return key


def retry_delay(*, quiet_period_s: float, failures: int) -> timedelta:
    """The quiet period, doubled per consecutive failed sweep, capped at 64x."""
    return timedelta(seconds=quiet_period_s * 2 ** min(failures, 6))


def utc_now() -> datetime:
    """The application clock used to schedule the next sweep."""
    return datetime.now(timezone.utc)


class _Sweep:
    """One sweep's PostgreSQL step inside one transaction."""

    def __init__(self, *, connection: Connection, forget: Forget) -> None:
        self._c = connection
        self._forget = forget
        self._removed = 0

    def run(self) -> SweepFindings:
        forget = self._forget
        self._c.execute(
            text("""SELECT 1 FROM forgets WHERE deployment_id = :d AND forget_id = :f
            FOR UPDATE"""),
            {"d": forget.deployment_id, "f": forget.forget_id},
        )
        collected = _collected(
            connection=self._c,
            deployment_id=forget.deployment_id,
            forget_id=forget.forget_id,
        )
        p = self._collect(collected=collected)
        self._c.execute(
            text("""UPDATE forgets SET collected = CAST(:collected AS jsonb)
            WHERE deployment_id = :d AND forget_id = :f"""),
            {
                "d": forget.deployment_id,
                "f": forget.forget_id,
                "collected": json.dumps(collected, sort_keys=True),
            },
        )
        self._exec(_SKIP_DOCUMENT_WORK, p, residue=False)
        # Error text and payloads of every owned work row, finished ones too,
        # while the rows that tie the work to the document still exist.
        self._exec(_SCRUB_DOCUMENT_WORK, p)
        profile_entities = self._shared_wording(p=p)
        self._exclusive_facts(p=p)
        self._entities(p=p)
        self._mark_compiled(ids=p["compiled_artifact_ids"], p=p, collected=collected)
        for statement in _SUBTREE:
            self._exec(statement, p)
        self._retire_originals(p=p, collected=collected)
        # Kept on the record in this transaction: a retry still finds them.
        collected["fixture_keys"] = sorted(
            {
                *collected["fixture_keys"],
                *(str(key) for key in self._c.execute(_PLAN_TRANSCRIPTS, p).scalars()),
            }
            - {"None", ""}
        )
        for statement in _DEPLOYMENT_FIXTURES:
            self._exec(statement, {**p, "cleared": _CLEARED_RATIONALE}, residue=False)
        self._stale_labels(p=p, collected=collected)
        self._c.execute(
            text("""UPDATE forgets SET collected = CAST(:collected AS jsonb)
            WHERE deployment_id = :d AND forget_id = :f"""),
            {
                "d": forget.deployment_id,
                "f": forget.forget_id,
                "collected": json.dumps(collected, sort_keys=True),
            },
        )
        return SweepFindings(
            removed=self._removed > 0,
            object_keys=tuple(ObjectKey(key) for key in collected["object_keys"]),
            compiled_artifact_ids=tuple(
                UUID(value) for value in collected["k_pending"]
            ),
            profile_entity_ids=profile_entities,
            fixture_keys=tuple(ObjectKey(key) for key in collected["fixture_keys"]),
        )

    def _exec(
        self, statement: Any, params: dict[str, Any], residue: bool = True
    ) -> int:  # noqa: ANN401
        count = self._c.execute(statement, params).rowcount
        if residue and count > 0:
            self._removed += count
        return count

    def _ids(self, statement: str, params: dict[str, Any]) -> list[UUID]:
        return sorted(
            {
                UUID(str(value))
                for value in self._c.execute(text(statement), params).scalars()
            },
            key=str,
        )

    def _collect(self, *, collected: dict[str, Any]) -> dict[str, Any]:
        """Step 0: IDs only, gathered before the deletes remove their links."""
        forget = self._forget
        p: dict[str, Any] = {
            "deployment_id": forget.deployment_id,
            "doc_id": forget.doc_id,
            "version_ids": list(forget.version_ids),
            "content_hashes": list(forget.content_hashes),
        }
        p["version_ids"] = sorted(
            set(p["version_ids"])
            | set(
                self._ids(
                    "SELECT version_id FROM document_versions"
                    " WHERE deployment_id=:deployment_id AND doc_id=:doc_id",
                    p,
                )
            ),
            key=str,
        )
        p["claim_ids"] = self._ids(
            "SELECT claim_id FROM claims WHERE deployment_id=:deployment_id AND doc_id=:doc_id",
            p,
        )
        # Chunk IDs are kept on the record: a late P1 write keyed only by a
        # chunk ID is still found after the chunk rows are gone.
        p["chunk_ids"] = sorted(
            set(
                self._ids(
                    "SELECT chunk_id FROM chunks"
                    " WHERE deployment_id=:deployment_id AND doc_id=:doc_id",
                    p,
                )
            )
            | {UUID(value) for value in collected["chunk_ids"]},
            key=str,
        )
        collected["chunk_ids"] = sorted(map(str, p["chunk_ids"]))
        p["mention_ids"] = self._ids(
            "SELECT mention_id FROM mentions"
            " WHERE deployment_id=:deployment_id AND doc_id=:doc_id",
            p,
        )
        p["forgotten_doc_ids"] = self._ids(
            "SELECT doc_id FROM forgets WHERE deployment_id=:deployment_id", p
        )
        linked_relations = self._ids(_LINKED_RELATIONS, p)
        linked_observations = self._ids(_LINKED_OBSERVATIONS, p)
        p["linked_fact_ids"] = sorted(
            set(linked_relations) | set(linked_observations), key=str
        )
        p["fact_ids"] = sorted(
            set(p["linked_fact_ids"])
            | {UUID(value) for value in collected["fact_ids"]},
            key=str,
        )
        p["exclusive_fact_ids"] = self._ids(_EXCLUSIVE_FACTS, p)
        p["shared_fact_ids"] = sorted(
            set(p["linked_fact_ids"]) - set(p["exclusive_fact_ids"]), key=str
        )
        resolved = self._ids(_RESOLVED_ENTITIES, p)
        endpoints = self._ids(_FACT_ENDPOINTS, p)
        p["resolved_entity_ids"] = sorted(set(resolved) | set(endpoints), key=str)
        p["entity_ids"] = sorted(
            set(p["resolved_entity_ids"])
            | {UUID(value) for value in collected["entity_ids"]},
            key=str,
        )
        p["exclusive_entity_ids"] = self._ids(_EXCLUSIVE_ENTITIES, p)
        # A surviving decision may cite an entity merged into one this
        # document touched; the redirects are read before any entity is
        # retired and kept on the record for later sweeps.
        p["rationale_entity_ids"] = sorted(
            set(self._ids(_REDIRECT_ANCESTORS, p))
            | {UUID(value) for value in collected["redirect_entity_ids"]},
            key=str,
        )
        collected["redirect_entity_ids"] = sorted(map(str, p["rationale_entity_ids"]))
        compiled = self._ids(
            _CITING_ARTIFACTS.format(facts="linked_fact_ids")
            + " AND a.page_kind = 'compiled'",
            p,
        )
        # A cycle that published a page but has not finalized it yet holds
        # the page's citations only in its pending compilation.
        compiled = sorted(set(compiled) | set(self._ids(_PENDING_CITING, p)), key=str)
        # Superseded compilations of pages that no longer cite the document
        # still hold its summary and writer transcript.
        collected["object_keys"] = sorted(
            {
                *collected["object_keys"],
                *(
                    str(key)
                    for key in self._c.execute(
                        _HISTORICAL_CITING_TRANSCRIPTS, p
                    ).scalars()
                    if key
                ),
            }
        )
        self._exec(_CLEAR_HISTORICAL_CITING, p)
        # Pages the last K step dropped for naming the document: once.
        compiled = sorted(
            set(compiled) | {UUID(value) for value in collected["k_dropped"]}, key=str
        )
        collected["k_dropped"] = []
        if p["linked_fact_ids"]:
            # A fact sheet renders facts without recording citations; it is
            # deterministic and cheap to rebuild, so every one is rebuilt.
            compiled = sorted(set(compiled) | set(self._ids(_FACT_SHEETS, p)), key=str)
        p["compiled_artifact_ids"] = compiled
        p["k_artifact_ids"] = sorted(
            set(compiled) | {UUID(value) for value in collected["k_artifact_ids"]},
            key=str,
        )
        object_keys = {
            locator
            for statement in (_OWNED_ORIGINALS, _TRANSCRIPTS)
            for value in self._c.execute(statement, p).scalars()
            if value is not None
            and (
                locator := original_locator(key=str(value), doc_id=self._forget.doc_id)
            )
        }
        collected["fact_ids"] = sorted(
            {*collected["fact_ids"], *map(str, p["fact_ids"])}
        )
        collected["entity_ids"] = sorted(
            {*collected["entity_ids"], *map(str, p["entity_ids"])}
        )
        collected["k_artifact_ids"] = sorted(
            {*collected["k_artifact_ids"], *map(str, p["k_artifact_ids"])}
        )
        collected["object_keys"] = sorted({*collected["object_keys"], *object_keys})
        return p

    def _shared_wording(self, *, p: dict[str, Any]) -> tuple[UUID, ...]:
        """Recompute or clear wording that came from the document (D159 §3.3)."""
        self._exec(_CLEAR_APPLICATION_INPUTS, p)
        self._exec(_REPLACE_SHARED_STATEMENTS, p)
        for kind, table in (("relation", "relations"), ("observation", "observations")):
            label_clear = (
                "fact_label=NULL,fact_label_version=NULL,"
                if kind == "relation"
                else "obs_label=NULL,"
            )
            self._exec(
                text(f"""UPDATE {table} SET
                  valid_from=NULL,valid_until=NULL,valid_precision='unknown',
                  window_claim_ids='{{}}'
                  WHERE deployment_id=:deployment_id
                    AND window_claim_ids && CAST(:claim_ids AS uuid[])"""),
                p,
            )
            self._exec(
                text(f"""UPDATE {table} SET {label_clear}
                  embedding=NULL,embedding_model=NULL,embedding_input_policy_version=NULL,
                  embedding_text_hash=NULL,period_basis=NULL,updated_at=now()
                  WHERE deployment_id=:deployment_id
                    AND {kind}_id=ANY(CAST(:shared_fact_ids AS uuid[]))"""),
                p,
            )
            self._exec(
                text("""DELETE FROM fact_window_review_attempts
                  WHERE deployment_id=:deployment_id AND fact_kind=:kind
                    AND fact_id=ANY(CAST(:shared_fact_ids AS uuid[]))"""),
                {**p, "kind": kind},
            )
            self._exec(
                text(f"""UPDATE {kind}_adjudications SET
                  triggering_claim_id=NULL, features=NULL, consumed_claim_ids='{{}}'
                  WHERE deployment_id=:deployment_id AND
                    (consumed_claim_ids && CAST(:claim_ids AS uuid[])
                     OR triggering_claim_id=ANY(CAST(:claim_ids AS uuid[])))"""),
                p,
            )
        cleared = self._c.execute(_CLEAR_PROFILES, p).scalars().all()
        self._removed += len(cleared)
        return tuple(sorted({UUID(str(value)) for value in cleared}, key=str))

    def _exclusive_facts(self, *, p: dict[str, Any]) -> None:
        """Delete facts only this document evidenced, with their adjudications."""
        # Candidates: predicates of the relations about to go, and every
        # predicate the document's normalization outputs named (an attempt
        # can register a generated predicate and fail before its relation).
        p["exclusive_predicates"] = list(
            self._c.execute(
                text("""SELECT predicate FROM relations
                WHERE deployment_id=:deployment_id
                  AND relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
                UNION
                SELECT r->>'predicate' FROM normalization_outputs n,
                  jsonb_array_elements(COALESCE(n.output->'relations', '[]'::jsonb)) r
                WHERE n.deployment_id=:deployment_id
                  AND n.claim_id=ANY(CAST(:claim_ids AS uuid[]))
                  AND jsonb_typeof(n.output->'relations') = 'array'"""),
                p,
            ).scalars()
        )
        for statement in _EXCLUSIVE_FACT_DELETES:
            self._exec(statement, p)
        self._exec(_PRUNE_PREDICATES, p)

    def _entities(self, *, p: dict[str, Any]) -> None:
        """Retire exclusive entities content-free; repair shared names and aliases."""
        p["doc_lemmas"] = list(
            self._c.execute(
                text("""SELECT DISTINCT lemma FROM mentions m
                CROSS JOIN LATERAL (VALUES (m.normalized_lemma),
                  (lower(m.canonical_name_form))) v(lemma)
                WHERE m.deployment_id=:deployment_id AND m.doc_id=:doc_id
                  AND v.lemma IS NOT NULL"""),
                p,
            ).scalars()
        )
        p["doc_forms"] = list(
            self._c.execute(
                text("""SELECT DISTINCT form FROM mentions m
                CROSS JOIN LATERAL (VALUES (m.surface_form),
                  (m.canonical_name_form)) v(form)
                WHERE m.deployment_id=:deployment_id AND m.doc_id=:doc_id
                  AND v.form IS NOT NULL"""),
                p,
            ).scalars()
        )
        for statement in _ENTITY_STATEMENTS:
            self._exec(statement, p)

    def _mark_compiled(
        self, *, ids: list[UUID], p: dict[str, Any], collected: dict[str, Any]
    ) -> None:
        """Mark compiled pages stale; they stay pending until step 4 drops them.

        The pending list lives on the record, so a sweep interrupted after this
        transaction still deletes their files (step 4) before the forget
        completes; ordinary K work rebuilds them.
        """
        if not ids:
            # Review flags and dispatches that name the document go anyway.
            for statement in _COMPILED_ARTIFACT_STATEMENTS[2:]:
                self._exec(statement, {**p, "compiled_artifact_ids": []})
            return
        params = {**p, "compiled_artifact_ids": ids}
        transcripts = self._c.execute(_TRANSCRIPTS, params).scalars()
        collected["object_keys"] = sorted(
            {*collected["object_keys"], *(str(key) for key in transcripts if key)}
        )
        self._exec(_DELETE_COMPILED_CITATIONS, params)
        for statement in _COMPILED_ARTIFACT_STATEMENTS:
            self._exec(statement, params)
        collected["k_pending"] = sorted({*collected["k_pending"], *map(str, ids)})
        collected["k_artifact_ids"] = sorted(
            {*collected["k_artifact_ids"], *map(str, ids)}
        )

    def _retire_originals(
        self, *, p: dict[str, Any], collected: dict[str, Any]
    ) -> None:
        """Retire raw originals no surviving version uses; keep their keys."""
        hashes = [value for value in p["content_hashes"] if value]
        if not hashes:
            return
        # Lock the content rows first, then re-check in a new statement: an
        # ingest attaching to a row locks it too, so it either registered its
        # version before (the hash stays in use) or sees the row retired.
        self._c.execute(
            text("""SELECT content_hash FROM content_objects
            WHERE deployment_id=:deployment_id
              AND content_hash=ANY(CAST(:hashes AS text[]))
            ORDER BY content_hash FOR UPDATE"""),
            {**p, "hashes": hashes},
        )
        retired = (
            self._c.execute(_RETIRE_ORIGINALS, {**p, "hashes": hashes}).scalars().all()
        )
        self._removed += len(retired)
        collected["object_keys"] = sorted(
            {
                *collected["object_keys"],
                *(
                    locator
                    for key in retired
                    if key
                    and (
                        locator := original_locator(
                            key=str(key), doc_id=self._forget.doc_id
                        )
                    )
                ),
            }
        )

    def _stale_labels(self, *, p: dict[str, Any], collected: dict[str, Any]) -> None:
        """Clear a collected relation label that no longer matches its parts.

        A label job started before the hide can write the old wording back
        after a sweep. Labels are deterministic, so a label that differs from
        the one its current names would give is residue; an ordinary relabel
        is not.
        """
        rows = self._c.execute(
            text("""SELECT r.relation_id, r.fact_label, r.predicate,
                   s.canonical_name AS subject, o.canonical_name AS object
            FROM relations r
            JOIN entities s ON s.entity_id = r.subject_entity_id
            JOIN entities o ON o.entity_id = r.object_entity_id
            WHERE r.deployment_id=:d AND r.fact_label IS NOT NULL
              AND (r.relation_id=ANY(CAST(:ids AS uuid[]))
                   OR r.subject_entity_id=ANY(CAST(:entities AS uuid[]))
                   OR r.object_entity_id=ANY(CAST(:entities AS uuid[])))"""),
            {
                "d": self._forget.deployment_id,
                "ids": collected["fact_ids"],
                "entities": collected["entity_ids"],
            },
        ).mappings()
        stale = [
            row["relation_id"]
            for row in rows
            if row["fact_label"]
            != deterministic_fact_label(
                subject=row["subject"],
                predicate=row["predicate"],
                object_name=row["object"],
            )
        ]
        if stale:
            self._exec(
                text("""UPDATE relations SET fact_label=NULL, fact_label_version=NULL,
                  embedding=NULL, embedding_model=NULL,
                  embedding_input_policy_version=NULL, embedding_text_hash=NULL,
                  updated_at=now()
                WHERE deployment_id=:deployment_id
                  AND relation_id=ANY(CAST(:ids AS uuid[]))"""),
                {"deployment_id": self._forget.deployment_id, "ids": stale},
            )
            # Compiled pages that rendered the old wording are rebuilt too.
            pages = self._ids(
                _CITING_ARTIFACTS.format(facts="stale_ids")
                + " AND a.page_kind = 'compiled'",
                {**p, "stale_ids": stale},
            )
            self._mark_compiled(
                ids=sorted(set(pages) | set(self._ids(_FACT_SHEETS, p)), key=str),
                p=p,
                collected=collected,
            )


def _collected(
    *, connection: Connection, deployment_id: UUID, forget_id: UUID, lock: bool = False
) -> dict[str, Any]:
    """The record's collected IDs, with every key present."""
    value = connection.execute(
        text(
            "SELECT collected FROM forgets WHERE deployment_id=:d AND forget_id=:f"
            + (" FOR UPDATE" if lock else "")
        ),
        {"d": deployment_id, "f": forget_id},
    ).scalar_one()
    collected: dict[str, Any] = dict(value or {})
    for key in (
        "fact_ids",
        "entity_ids",
        "redirect_entity_ids",
        "chunk_ids",
        "k_artifact_ids",
        "k_pending",
        "k_dropped",
        "object_keys",
        "fixture_keys",
    ):
        collected[key] = sorted(collected.get(key) or ())
    return collected


def _store_collected(
    *, connection: Connection, forget: Forget, collected: dict[str, Any]
) -> None:
    connection.execute(
        text("""UPDATE forgets SET collected = CAST(:collected AS jsonb)
        WHERE deployment_id = :d AND forget_id = :f"""),
        {
            "d": forget.deployment_id,
            "f": forget.forget_id,
            "collected": json.dumps(collected, sort_keys=True),
        },
    )


def _forget(*, row: RowMapping) -> Forget:
    """Validate one database row as the typed record."""
    return Forget(
        forget_id=row["forget_id"],
        deployment_id=row["deployment_id"],
        doc_id=row["doc_id"],
        version_ids=tuple(row["version_ids"]),
        content_hashes=tuple(row["content_hashes"]),
        source_identity_hash=row["source_identity_hash"],
        status=ForgetStatus(str(row["status"])),
        blocking_k_paths=tuple(row["blocking_k_paths"]),
        last_error=row["last_error"],
        requested_at=row["requested_at"],
        completed_at=row["completed_at"],
    )


_SELECT = """
    SELECT forget_id, deployment_id, doc_id, version_ids, content_hashes,
           source_identity_hash, status::text AS status, blocking_k_paths,
           last_error, requested_at, completed_at
    FROM forgets
"""

_INSERT_RECORD = text(
    """
    INSERT INTO forgets (
        forget_id, deployment_id, doc_id, version_ids, content_hashes,
        source_identity_hash
    )
    SELECT :forget_id, :d, :doc,
           ARRAY(SELECT version_id FROM document_versions
                 WHERE deployment_id = :d AND doc_id = :doc ORDER BY version_id),
           ARRAY(SELECT DISTINCT content_hash FROM document_versions
                 WHERE deployment_id = :d AND doc_id = :doc ORDER BY content_hash),
           :identity
    ON CONFLICT (deployment_id, doc_id) DO NOTHING
    RETURNING forget_id, deployment_id, doc_id, version_ids, content_hashes,
              source_identity_hash, status::text AS status, blocking_k_paths,
              last_error, requested_at, completed_at
    """
)

_FORGOTTEN_INGEST = text(
    """
    SELECT forget_id, requested_at, status::text AS status
    FROM forgets
    WHERE deployment_id = :d
      AND (
        (CAST(:identity AS text) IS NOT NULL
         AND source_identity_hash = :identity AND NOT identity_released)
        OR (:content_hash = ANY(content_hashes)
            AND NOT :content_hash = ANY(released_hashes))
      )
    ORDER BY requested_at, forget_id
    """
)

_RELEASE_FORGOTTEN = text(
    """
    UPDATE forgets
    SET released_hashes = CASE
            WHEN :content_hash = ANY(content_hashes)
                 AND NOT :content_hash = ANY(released_hashes)
            THEN array_append(released_hashes, CAST(:content_hash AS text))
            ELSE released_hashes END,
        identity_released = identity_released
            OR (CAST(:identity AS text) IS NOT NULL
                AND source_identity_hash IS NOT DISTINCT FROM :identity)
    WHERE deployment_id = :d AND forget_id = ANY(CAST(:ids AS uuid[]))
      AND doc_id = ANY(CAST(:lineages AS uuid[]))
    """
)

_PENDING_CITING = """
    SELECT DISTINCT c.artifact_id FROM knowledge_compilations c
    CROSS JOIN LATERAL jsonb_array_elements(c.citations) citation
    WHERE c.deployment_id=:deployment_id AND c.git_commit IS NULL
      AND c.failed_at IS NULL AND jsonb_typeof(c.citations) = 'array'
      AND (citation->>'doc_id' = CAST(:doc_id AS text)
           OR citation->>'claim_lineage_id' = CAST(:doc_id AS text)
           OR citation->>'relation_id'
              = ANY(CAST(CAST(:linked_fact_ids AS uuid[]) AS text[])))
"""

_CITES_FORGOTTEN = """
    jsonb_typeof(c.citations) = 'array' AND EXISTS (
      SELECT 1 FROM jsonb_array_elements(c.citations) citation
      WHERE citation->>'doc_id' = CAST(:doc_id AS text)
         OR citation->>'claim_lineage_id' = CAST(:doc_id AS text)
         OR citation->>'relation_id'
            = ANY(CAST(CAST(:linked_fact_ids AS uuid[]) AS text[])))
"""

_HISTORICAL_CITING_TRANSCRIPTS = text(
    "SELECT c.session_transcript_uri FROM knowledge_compilations c"
    " WHERE c.deployment_id=:deployment_id AND " + _CITES_FORGOTTEN
)

_CLEAR_HISTORICAL_CITING = text(
    "UPDATE knowledge_compilations c SET page_summary=NULL,"
    " suggestions='[]'::jsonb"
    " WHERE c.deployment_id=:deployment_id"
    " AND (c.page_summary IS NOT NULL OR c.suggestions <> '[]'::jsonb)"
    " AND " + _CITES_FORGOTTEN
)

_FACT_SHEETS = """
    SELECT artifact_id FROM knowledge_artifacts
    WHERE deployment_id=:deployment_id AND page_kind='compiled'
      AND kind='fact_sheet' AND status <> 'tombstoned'
"""

# Transcript objects of the K runs the sweep deletes (compilations of the
# pages it rebuilds, and every plan run): collected before their rows go.
_TRANSCRIPTS = text(
    """
    SELECT session_transcript_uri FROM knowledge_compilations
    WHERE deployment_id=:deployment_id
      AND artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
    """
)

# Plan-run transcripts go with the deployment's plan runs (below); they are
# not the document's residue, so purging them never keeps a forget erasing.
_PLAN_TRANSCRIPTS = text(
    """
    SELECT session_transcript_uri FROM knowledge_plan_runs
    WHERE deployment_id=:deployment_id AND session_transcript_uri IS NOT NULL
    """
)

_SKIP_DOCUMENT_WORK = text(
    """
    UPDATE processing_state
    SET status = 'skipped', defer_reason = NULL, lease_until = NULL,
        finished_at = now(), payload = NULL, last_error = NULL
    WHERE deployment_id = :deployment_id
      AND stage <> 'forget'
      AND status IN ('pending', 'failed', 'running')
      AND """
    + FORGOTTEN_WORK.format(work="processing_state")
)

_SCRUB_DOCUMENT_WORK = text(
    """
    UPDATE processing_state SET payload = NULL, last_error = NULL
    WHERE deployment_id = :deployment_id
      AND stage <> 'forget'
      AND (payload IS NOT NULL OR last_error IS NOT NULL)
      AND """
    + FORGOTTEN_WORK.format(work="processing_state")
)

# Facts the document's evidence or its claims' windows touch.
_LINKED_RELATIONS = """
    SELECT relation_id FROM relation_evidence
    WHERE deployment_id=:deployment_id
      AND (doc_id=:doc_id OR claim_id=ANY(CAST(:claim_ids AS uuid[])))
    UNION
    SELECT relation_id FROM relations
    WHERE deployment_id=:deployment_id AND window_claim_ids && CAST(:claim_ids AS uuid[])
"""

_LINKED_OBSERVATIONS = """
    SELECT observation_id FROM observation_evidence
    WHERE deployment_id=:deployment_id
      AND (doc_id=:doc_id OR claim_id=ANY(CAST(:claim_ids AS uuid[])))
    UNION
    SELECT observation_id FROM observations
    WHERE deployment_id=:deployment_id AND window_claim_ids && CAST(:claim_ids AS uuid[])
"""

# A fact is exclusive when no other, not forgotten document supports it
# (current or archived testimony alike). Counterevidence alone cannot keep
# the forgotten assertion's wording.
_EXCLUSIVE_FACTS = """
    SELECT r.relation_id FROM relations r
    WHERE r.deployment_id=:deployment_id
      AND r.relation_id=ANY(CAST(:fact_ids AS uuid[]))
      AND NOT EXISTS (
        SELECT 1 FROM relation_evidence e
        WHERE e.deployment_id=r.deployment_id AND e.relation_id=r.relation_id
          AND e.stance='supports' AND e.doc_id<>:doc_id
          AND NOT e.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[]))
          AND NOT e.claim_id=ANY(CAST(:claim_ids AS uuid[])))
    UNION
    SELECT o.observation_id FROM observations o
    WHERE o.deployment_id=:deployment_id
      AND o.observation_id=ANY(CAST(:fact_ids AS uuid[]))
      AND NOT EXISTS (
        SELECT 1 FROM observation_evidence e
        WHERE e.deployment_id=o.deployment_id AND e.observation_id=o.observation_id
          AND e.stance='supports' AND e.doc_id<>:doc_id
          AND NOT e.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[]))
          AND NOT e.claim_id=ANY(CAST(:claim_ids AS uuid[])))
"""

_RESOLVED_ENTITIES = """
    SELECT DISTINCT d.entity_id FROM resolution_decisions d
    WHERE d.deployment_id=:deployment_id
      AND d.mention_id=ANY(CAST(:mention_ids AS uuid[]))
"""

_FACT_ENDPOINTS = """
    SELECT subject_entity_id FROM relations
    WHERE deployment_id=:deployment_id AND relation_id=ANY(CAST(:linked_fact_ids AS uuid[]))
    UNION
    SELECT object_entity_id FROM relations
    WHERE deployment_id=:deployment_id AND relation_id=ANY(CAST(:linked_fact_ids AS uuid[]))
    UNION
    SELECT subject_entity_id FROM observations
    WHERE deployment_id=:deployment_id
      AND observation_id=ANY(CAST(:linked_fact_ids AS uuid[]))
"""

# An entity is exclusive when it was resolved from this document's mentions
# and from no mention of another, not forgotten document, and no surviving
# fact uses it.
_REDIRECT_ANCESTORS = """
    WITH RECURSIVE closure(entity_id) AS (
      SELECT CAST(value AS uuid) FROM unnest(CAST(:entity_ids AS uuid[])) value
      UNION
      SELECT x.entity_id FROM entities x JOIN closure c ON x.merged_into=c.entity_id
      WHERE x.deployment_id=:deployment_id
    )
    SELECT entity_id FROM closure
"""
"""Every entity the document touched and every entity redirected into one."""

_EXCLUSIVE_ENTITIES = """
    SELECT e.entity_id FROM entities e
    WHERE e.deployment_id=:deployment_id
      AND e.entity_id=ANY(CAST(:entity_ids AS uuid[]))
      AND e.status <> 'retired'
      AND EXISTS (
        SELECT 1 FROM resolution_decisions d JOIN mentions m
          ON m.deployment_id=d.deployment_id AND m.mention_id=d.mention_id
        WHERE d.deployment_id=e.deployment_id AND d.entity_id=e.entity_id
          AND m.doc_id=:doc_id)
      AND NOT EXISTS (
        -- surviving provenance anywhere in the merge closure that resolves
        -- to this entity (itself and every entity redirected into it)
        WITH RECURSIVE closure(entity_id) AS (
          SELECT e.entity_id
          UNION
          SELECT x.entity_id FROM entities x JOIN closure c ON x.merged_into=c.entity_id
          WHERE x.deployment_id=e.deployment_id
        )
        SELECT 1 FROM resolution_decisions d JOIN mentions m
          ON m.deployment_id=d.deployment_id AND m.mention_id=d.mention_id
        WHERE d.deployment_id=e.deployment_id
          AND d.entity_id IN (SELECT entity_id FROM closure)
          AND d.superseded_by IS NULL AND m.doc_id<>:doc_id
          AND NOT m.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[])))
      AND NOT EXISTS (
        SELECT 1 FROM relations r
        WHERE r.deployment_id=e.deployment_id
          AND (r.subject_entity_id=e.entity_id OR r.object_entity_id=e.entity_id)
          AND NOT r.relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
      AND NOT EXISTS (
        SELECT 1 FROM observations o
        WHERE o.deployment_id=e.deployment_id AND o.subject_entity_id=e.entity_id
          AND NOT o.observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
"""

# K pages citing the document's evidence or a fact it touched. Compiled pages
# are deleted and rebuilt when a sweep finds such a link; authored pages and
# sidecars are inspected in the K repository itself (step 4).
_CITING_ARTIFACTS = """
    SELECT DISTINCT a.artifact_id FROM knowledge_artifacts a
    JOIN knowledge_artifact_evidence e
      ON e.deployment_id=a.deployment_id AND e.artifact_id=a.artifact_id
    WHERE a.deployment_id=:deployment_id
      AND (e.doc_id=:doc_id OR e.claim_lineage_id=:doc_id
           OR e.relation_id=ANY(CAST(:{facts} AS uuid[])))
"""

# Opaque keys of originals the document owns: its own prefix or a D147
# direct-upload locator. A shared original keeps the key it was first stored
# under and is never touched while another lineage uses it.
_OWNED_ORIGINALS = text(
    """
    SELECT raw_uri FROM content_objects
    WHERE deployment_id=:deployment_id
      AND content_hash=ANY(CAST(:content_hashes AS text[]))
      AND (raw_uri LIKE CAST(:doc_id AS text) || '/%' OR raw_uri LIKE 'uploads/%')
    """
)

_CLEAR_APPLICATION_INPUTS = text(
    """
    UPDATE fact_applications SET
      prepared=NULL, decision=NULL, attempt_id=NULL, input_hash=NULL,
      input_claim_ids=ARRAY(SELECT id FROM unnest(input_claim_ids) id
                            WHERE NOT id=ANY(CAST(:claim_ids AS uuid[])) ORDER BY id)
    WHERE deployment_id=:deployment_id
      AND input_claim_ids && CAST(:claim_ids AS uuid[])
    """
)

# A shared observation's statement is rewritten from a surviving supporter.
_REPLACE_SHARED_STATEMENTS = text(
    """
    WITH replacements AS (
      SELECT f.observation_id, replacement.statement
      FROM observations f
      CROSS JOIN LATERAL (
        SELECT coalesce(n.output->'observations'->a.output_ordinal->>'statement',
                        c.claim_text) AS statement
        FROM observation_evidence e
        JOIN claims c ON c.deployment_id=e.deployment_id AND c.claim_id=e.claim_id
        LEFT JOIN fact_applications a ON a.deployment_id=e.deployment_id
          AND a.claim_id=e.claim_id AND a.support_observation_id=e.observation_id
          AND a.applied_at IS NOT NULL AND a.support_stance='supports'
        LEFT JOIN normalization_outputs n ON n.deployment_id=a.deployment_id
          AND n.claim_id=a.claim_id AND n.normalizer_version=a.normalizer_version
        WHERE e.deployment_id=f.deployment_id AND e.observation_id=f.observation_id
          AND e.stance='supports' AND e.doc_id<>:doc_id
          AND NOT e.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[]))
          AND NOT e.claim_id=ANY(CAST(:claim_ids AS uuid[]))
        ORDER BY c.is_current_testimony DESC, (a.application_id IS NULL),
                 c.asserted_at NULLS LAST, c.claim_id, a.application_id
        LIMIT 1
      ) replacement
      WHERE f.deployment_id=:deployment_id
        AND f.observation_id=ANY(CAST(:shared_fact_ids AS uuid[]))
        AND f.statement IS DISTINCT FROM replacement.statement
    ) UPDATE observations f SET statement=replacements.statement, obs_label=NULL,
      embedding=NULL, embedding_model=NULL, embedding_input_policy_version=NULL,
      embedding_text_hash=NULL, updated_at=now()
    FROM replacements
    WHERE f.deployment_id=:deployment_id AND f.observation_id=replacements.observation_id
    """
)

# Surviving entities the document touched lose their profile and vector until
# ordinary work recomputes them from what survives.
_CLEAR_PROFILES = text(
    """
    WITH RECURSIVE touched(entity_id) AS (
      SELECT entity_id FROM entities
      WHERE deployment_id=:deployment_id
        AND entity_id=ANY(CAST(:resolved_entity_ids AS uuid[]))
      UNION
      SELECT e.merged_into FROM entities e JOIN touched t USING (entity_id)
      WHERE e.deployment_id=:deployment_id AND e.merged_into IS NOT NULL
    )
    UPDATE entities SET profile_summary=NULL, embedding=NULL, embedding_model=NULL,
      embedding_input_policy_version=NULL, embedding_text_hash=NULL, updated_at=now()
    WHERE deployment_id=:deployment_id
      AND entity_id IN (SELECT entity_id FROM touched)
      AND NOT entity_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
      AND (profile_summary IS NOT NULL OR embedding IS NOT NULL
           OR embedding_text_hash IS NOT NULL)
    RETURNING entity_id
    """
)

_EXCLUSIVE_FACT_DELETES = (
    text(
        """
        UPDATE relation_adjudications SET superseded_by = NULL
        WHERE deployment_id=:deployment_id AND superseded_by IN (
            SELECT adjudication_id FROM relation_adjudications
            WHERE deployment_id=:deployment_id
              AND (relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
                   OR related_relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))))
        """
    ),
    text(
        """
        DELETE FROM relation_adjudications
        WHERE deployment_id=:deployment_id
          AND (relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
               OR related_relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
        """
    ),
    text(
        """
        UPDATE observation_adjudications SET superseded_by = NULL
        WHERE deployment_id=:deployment_id AND superseded_by IN (
            SELECT adjudication_id FROM observation_adjudications
            WHERE deployment_id=:deployment_id
              AND (observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
                   OR related_observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))))
        """
    ),
    text(
        """
        DELETE FROM observation_adjudications
        WHERE deployment_id=:deployment_id
          AND (observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
               OR related_observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
        """
    ),
    text(
        """
        DELETE FROM fact_window_review_attempts
        WHERE deployment_id=:deployment_id
          AND fact_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
        """
    ),
    text(
        """
        DELETE FROM relation_evidence
        WHERE deployment_id=:deployment_id
          AND (doc_id=:doc_id OR relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
        """
    ),
    text(
        """
        DELETE FROM observation_evidence
        WHERE deployment_id=:deployment_id
          AND (doc_id=:doc_id
               OR observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
        """
    ),
    text(
        """
        DELETE FROM relations
        WHERE deployment_id=:deployment_id
          AND relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
        """
    ),
    text(
        """
        DELETE FROM observations
        WHERE deployment_id=:deployment_id
          AND observation_id=ANY(CAST(:exclusive_fact_ids AS uuid[]))
        """
    ),
)

# A source-generated `other:` predicate no remaining relation uses goes.
_PRUNE_PREDICATES = text(
    """
    DELETE FROM predicates p
    WHERE p.deployment_id=:deployment_id AND p.tier='other'
      AND p.predicate=ANY(CAST(:exclusive_predicates AS text[]))
      AND NOT EXISTS (SELECT 1 FROM relations r
                      WHERE r.deployment_id=p.deployment_id AND r.predicate=p.predicate)
      AND NOT EXISTS (SELECT 1 FROM predicates c
                      WHERE c.deployment_id=p.deployment_id
                        AND c.parent_predicate=p.predicate)
    """
)

# Exact spellings surviving mentions use, resolved to one entity or to any
# entity redirected into it (its merge closure).
_SURVIVING_FORMS = """
    SELECT v.form, m.normalized_lemma AS lemma
    FROM resolution_decisions d JOIN mentions m
      ON m.deployment_id=d.deployment_id AND m.mention_id=d.mention_id
    CROSS JOIN LATERAL (VALUES (m.surface_form), (m.canonical_name_form)) v(form)
    WHERE d.deployment_id=:deployment_id
      AND d.entity_id IN (
        WITH RECURSIVE closure(entity_id) AS (
          SELECT {entity}
          UNION
          SELECT x.entity_id FROM entities x JOIN closure c ON x.merged_into=c.entity_id
          WHERE x.deployment_id=:deployment_id
        )
        SELECT entity_id FROM closure)
      AND d.superseded_by IS NULL AND m.doc_id<>:doc_id
      AND NOT m.doc_id=ANY(CAST(:forgotten_doc_ids AS uuid[]))
      AND v.form IS NOT NULL
"""

_ENTITY_STATEMENTS = (
    # Exclusive entities: content-free retired rows (merge logs reference them).
    text(
        """
        DELETE FROM aliases
        WHERE deployment_id=:deployment_id
          AND entity_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
        """
    ),
    text(
        """
        UPDATE entities SET canonical_name='', normalized_name='', status='retired',
            merged_into=NULL, profile_summary=NULL, embedding=NULL,
            embedding_model=NULL, embedding_input_policy_version=NULL,
            embedding_text_hash=NULL, mention_count=0, graph_degree=0,
            updated_at=now()
        WHERE deployment_id=:deployment_id
          AND entity_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
          AND (status <> 'retired' OR canonical_name <> '' OR normalized_name <> '')
        """
    ),
    # Shared entities keep only spellings a surviving mention of that entity
    # uses. An alias spelled as this document spelled it takes a surviving
    # spelling of the same lemma, or goes; a canonical name spelled only by
    # this document is replaced by a surviving alias.
    text(
        f"""
        UPDATE aliases a SET alias_text = (
          SELECT s.form FROM ({_SURVIVING_FORMS.format(entity="a.entity_id")}) s
          WHERE lower(s.form) = a.normalized_lemma OR s.lemma = a.normalized_lemma
          ORDER BY s.form LIMIT 1)
        WHERE a.deployment_id=:deployment_id
          AND a.entity_id=ANY(CAST(:resolved_entity_ids AS uuid[]))
          AND a.alias_text=ANY(CAST(:doc_forms AS text[]))
          AND NOT EXISTS (
            SELECT 1 FROM ({_SURVIVING_FORMS.format(entity="a.entity_id")}) s
            WHERE s.form = a.alias_text)
          AND EXISTS (
            SELECT 1 FROM ({_SURVIVING_FORMS.format(entity="a.entity_id")}) s
            WHERE lower(s.form) = a.normalized_lemma OR s.lemma = a.normalized_lemma)
        """
    ),
    text(
        f"""
        DELETE FROM aliases a
        WHERE a.deployment_id=:deployment_id
          AND a.entity_id=ANY(CAST(:resolved_entity_ids AS uuid[]))
          AND (a.alias_text=ANY(CAST(:doc_forms AS text[]))
               OR a.normalized_lemma=ANY(CAST(:doc_lemmas AS text[])))
          AND NOT EXISTS (
            SELECT 1 FROM ({_SURVIVING_FORMS.format(entity="a.entity_id")}) s
            WHERE s.form = a.alias_text)
        """
    ),
    text(
        f"""
        UPDATE entities e SET (canonical_name, normalized_name, updated_at) = (
          SELECT x.name, x.lemma, now() FROM (
            SELECT a.alias_text AS name, a.normalized_lemma AS lemma, 0 AS rank,
                   (a.provenance='llm_canonical') AS llm, a.confidence AS confidence,
                   a.last_seen AS seen
            FROM aliases a
            WHERE a.deployment_id=e.deployment_id AND a.entity_id=e.entity_id
            UNION ALL
            SELECT s.form, s.lemma, 1, false, NULL, NULL
            FROM ({_SURVIVING_FORMS.format(entity="e.entity_id")}) s
          ) x
          ORDER BY x.rank, x.llm DESC, x.confidence DESC NULLS LAST,
                   x.seen DESC NULLS LAST, x.name
          LIMIT 1)
        WHERE e.deployment_id=:deployment_id
          AND e.entity_id=ANY(CAST(:resolved_entity_ids AS uuid[]))
          AND e.status <> 'retired'
          AND e.canonical_name=ANY(CAST(:doc_forms AS text[]))
          AND NOT EXISTS (
            SELECT 1 FROM ({_SURVIVING_FORMS.format(entity="e.entity_id")}) s
            WHERE s.form = e.canonical_name)
          AND (EXISTS (
                 SELECT 1 FROM aliases a
                 WHERE a.deployment_id=e.deployment_id AND a.entity_id=e.entity_id)
               OR EXISTS (
                 SELECT 1 FROM ({_SURVIVING_FORMS.format(entity="e.entity_id")}) s))
        """
    ),
    text(
        """
        UPDATE merge_events
        SET trigger_lemmas = '{}', evidence = NULL,
            pre_merge_membership_snapshot = '{}'::jsonb
        WHERE deployment_id=:deployment_id
          AND (survivor_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
               OR absorbed_id=ANY(CAST(:exclusive_entity_ids AS uuid[]))
               OR EXISTS (
                 SELECT 1 FROM unnest(CAST(:mention_ids AS uuid[])) AS ids(value)
                 WHERE pre_merge_membership_snapshot::text
                       LIKE '%' || CAST(ids.value AS text) || '%'))
          AND (cardinality(trigger_lemmas) > 0 OR evidence IS NOT NULL
               OR pre_merge_membership_snapshot <> '{}'::jsonb)
        """
    ),
    # A surviving decision's T4 rationale may quote a candidate this
    # document named; the verdict stays, the prose goes.
    text(
        """
        UPDATE resolution_decisions d
        SET features = (d.features - 'rationale') #- '{t4_selection,rationale}'
        WHERE d.deployment_id=:deployment_id
          AND (d.features ? 'rationale' OR d.features->'t4_selection' ? 'rationale')
          AND EXISTS (
            SELECT 1 FROM unnest(CAST(:rationale_entity_ids AS uuid[])) ids(value)
            WHERE d.features::text LIKE '%' || CAST(ids.value AS text) || '%')
        """
    ),
    text(
        """
        UPDATE resolution_exclusions SET reason = NULL
        WHERE deployment_id=:deployment_id AND reason IS NOT NULL
          AND (entity_id_low=ANY(CAST(:resolved_entity_ids AS uuid[]))
               OR entity_id_high=ANY(CAST(:resolved_entity_ids AS uuid[])))
        """
    ),
)

_DELETE_COMPILED_CITATIONS = text(
    """
    DELETE FROM knowledge_artifact_evidence
    WHERE deployment_id=:deployment_id
      AND artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
      AND (doc_id=:doc_id OR claim_lineage_id=:doc_id
           OR relation_id=ANY(CAST(:exclusive_fact_ids AS uuid[])))
    """
)

_COMPILED_ARTIFACT_STATEMENTS = (
    text(
        """
        DELETE FROM knowledge_compilations
        WHERE deployment_id=:deployment_id
          AND artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
        """
    ),
    text(
        """
        UPDATE knowledge_artifacts
        SET page_summary=NULL, content_hash=NULL, inputs_hash=NULL,
            last_compiled_at=NULL,
            status=CASE WHEN status='tombstoned' THEN status ELSE 'stale' END
        WHERE deployment_id=:deployment_id AND page_kind='compiled'
          AND artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
        """
    ),
    text(
        """
        DELETE FROM knowledge_refresh_queue refresh
        WHERE refresh.deployment_id=:deployment_id
          AND (refresh.artifact_id=ANY(CAST(:compiled_artifact_ids AS uuid[]))
               OR refresh.payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
               OR EXISTS (
                 SELECT 1 FROM unnest(CAST(:claim_ids AS uuid[])
                   || CAST(:exclusive_fact_ids AS uuid[])) AS ids(value)
                 WHERE refresh.payload::text LIKE '%' || CAST(ids.value AS text) || '%'))
        """
    ),
    text(
        """
        DELETE FROM knowledge_dispatches dispatch
        WHERE dispatch.deployment_id=:deployment_id
          AND (dispatch.payload::text LIKE '%' || CAST(:doc_id AS text) || '%'
               OR EXISTS (
                 SELECT 1 FROM unnest(CAST(:claim_ids AS uuid[])
                   || CAST(:exclusive_fact_ids AS uuid[])) AS ids(value)
                 WHERE dispatch.payload::text LIKE '%' || CAST(ids.value AS text) || '%'))
        """
    ),
)

# The document's subtree, children before parents. Foreign-key cascades that
# exist inside the subtree (representations, sections, metadata, tags, ...)
# do part of the work when the versions and the document row go.
_SUBTREE = (
    text(
        "DELETE FROM selection_results WHERE deployment_id=:deployment_id"
        " AND chunk_id=ANY(CAST(:chunk_ids AS uuid[]))"
    ),
    text(
        """
        DELETE FROM application_context_bindings
        WHERE deployment_id=:deployment_id AND application_id IN (
          SELECT application_id FROM fact_applications
          WHERE deployment_id=:deployment_id
            AND claim_id=ANY(CAST(:claim_ids AS uuid[])))
        """
    ),
    text(
        "DELETE FROM fact_applications WHERE deployment_id=:deployment_id"
        " AND claim_id=ANY(CAST(:claim_ids AS uuid[]))"
    ),
    text(
        "DELETE FROM normalization_outputs WHERE deployment_id=:deployment_id"
        " AND claim_id=ANY(CAST(:claim_ids AS uuid[]))"
    ),
    text(
        "DELETE FROM normalize_observation_staging"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        """
        DELETE FROM obs_flush_entity_units
        WHERE deployment_id=:deployment_id
          AND (doc_id=:doc_id OR version_id=ANY(CAST(:version_ids AS uuid[])))
        """
    ),
    text(
        "DELETE FROM obs_flush_version_state WHERE deployment_id=:deployment_id"
        " AND version_id=ANY(CAST(:version_ids AS uuid[]))"
    ),
    text(
        "DELETE FROM processing_layer_gaps"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_entity_bindings"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM resolution_decisions WHERE deployment_id=:deployment_id"
        " AND mention_id=ANY(CAST(:mention_ids AS uuid[]))"
    ),
    text("DELETE FROM mentions WHERE deployment_id=:deployment_id AND doc_id=:doc_id"),
    text(
        "DELETE FROM grounding_audits WHERE deployment_id=:deployment_id"
        " AND claim_id=ANY(CAST(:claim_ids AS uuid[]))"
    ),
    text(
        "DELETE FROM claim_extraction_decisions"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        """
        DELETE FROM chunk_claims
        WHERE deployment_id=:deployment_id
          AND (chunk_id=ANY(CAST(:chunk_ids AS uuid[]))
               OR claim_id=ANY(CAST(:claim_ids AS uuid[])))
        """
    ),
    text("DELETE FROM claims WHERE deployment_id=:deployment_id AND doc_id=:doc_id"),
    text(
        "DELETE FROM chunk_search WHERE deployment_id=:deployment_id"
        " AND chunk_id=ANY(CAST(:chunk_ids AS uuid[]))"
    ),
    text("DELETE FROM chunks WHERE deployment_id=:deployment_id AND doc_id=:doc_id"),
    text(
        """
        UPDATE document_representations r SET current_structure_generation_id = NULL
        WHERE r.deployment_id=:deployment_id
          AND r.version_id=ANY(CAST(:version_ids AS uuid[]))
          AND r.current_structure_generation_id IS NOT NULL
        """
    ),
    text(
        "DELETE FROM document_sections"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_structure_generations"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_skeleton_checks"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_effective_periods"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_effective_time_events"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_version_scope"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_crossrefs"
        " WHERE deployment_id=:deployment_id AND from_doc_id=:doc_id"
    ),
    # D140 §9: references other lineages made TO the forgotten one are their
    # sources' content; they are unbound, keeping the target as the source
    # names it.
    text(
        """
        UPDATE document_crossrefs SET to_doc_id = NULL, resolved = false
        WHERE deployment_id=:deployment_id AND to_doc_id=:doc_id
        """
    ),
    text(
        "DELETE FROM document_reference_generations"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        """
        DELETE FROM document_names WHERE deployment_id=:deployment_id
          AND version_id=ANY(CAST(:version_ids AS uuid[]))
        """
    ),
    text(
        """
        DELETE FROM document_people WHERE deployment_id=:deployment_id
          AND version_id=ANY(CAST(:version_ids AS uuid[]))
        """
    ),
    text(
        "DELETE FROM document_metadata"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        "DELETE FROM document_tags WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text(
        """
        UPDATE documents SET current_version_id = NULL
        WHERE deployment_id=:deployment_id AND doc_id=:doc_id
          AND current_version_id IS NOT NULL
        """
    ),
    text(
        "DELETE FROM document_versions"
        " WHERE deployment_id=:deployment_id AND doc_id=:doc_id"
    ),
    text("DELETE FROM documents WHERE deployment_id=:deployment_id AND doc_id=:doc_id"),
    # Content-free work-ledger rows stay; their error text and payloads go.
    text(
        """
        UPDATE processing_state SET payload = NULL, last_error = NULL
        WHERE deployment_id=:deployment_id AND stage <> 'forget'
          AND (payload IS NOT NULL OR last_error IS NOT NULL)
          AND (target_id=:doc_id
               OR target_id=ANY(CAST(:version_ids AS uuid[]))
               OR target_id=ANY(CAST(:chunk_ids AS uuid[]))
               OR target_id=ANY(CAST(:claim_ids AS uuid[]))
               OR payload->>'doc_id'=CAST(:doc_id AS text)
               OR payload->>'version_id'=ANY(CAST(CAST(:version_ids AS uuid[]) AS text[])))
        """
    ),
    text(
        """
        DELETE FROM review_queue review
        WHERE review.deployment_id=:deployment_id
          AND (review.candidate::text LIKE '%' || CAST(:doc_id AS text) || '%'
               OR EXISTS (
                 SELECT 1 FROM unnest(CAST(:claim_ids AS uuid[])
                   || CAST(:mention_ids AS uuid[])
                   || CAST(:exclusive_fact_ids AS uuid[])
                   || CAST(:exclusive_entity_ids AS uuid[])) AS ids(value)
                 WHERE review.candidate::text
                       LIKE '%' || CAST(ids.value AS text) || '%'))
        """
    ),
)

# Raw originals no surviving version uses are retired; their keys are kept on
# the record and purged in step 2. Runs after the subtree is gone.
_RETIRE_ORIGINALS = text(
    """
    UPDATE content_objects object
    SET mime = 'application/x-forgotten', byte_size = NULL,
        raw_uri = 'forgotten/' || object.content_hash,
        purged_at = COALESCE(object.purged_at, now())
    FROM (SELECT content_hash, raw_uri FROM content_objects
          WHERE deployment_id=:deployment_id
            AND content_hash=ANY(CAST(:hashes AS text[]))) original
    WHERE object.deployment_id=:deployment_id
      AND object.content_hash=original.content_hash
      AND object.purged_at IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM document_versions v
        WHERE v.deployment_id=object.deployment_id
          AND v.content_hash=object.content_hash
          -- a scrubbed version row an upgraded D74 forget left keeps nothing
          AND NOT EXISTS (SELECT 1 FROM forgets f
                          WHERE f.deployment_id=v.deployment_id
                            AND f.doc_id=v.doc_id))
    RETURNING original.raw_uri
    """
)

# The eval schema has no lineage-provenance column, so a forget drops the
# deployment's free-text eval fixtures and K plan transcripts rather than guess
# which prose came from the document (as D74 did). Not counted as residue.
_DEPLOYMENT_FIXTURES = (
    text("DELETE FROM eval_runs WHERE deployment_id=:deployment_id"),
    text("DELETE FROM canary_cases WHERE deployment_id=:deployment_id"),
    text("DELETE FROM golden_claim_labels WHERE deployment_id=:deployment_id"),
    text("DELETE FROM golden_pairs WHERE deployment_id=:deployment_id"),
    # A decision a page rule or quarantine still references stays, a valid
    # proposal with its prose rationale replaced.
    text(
        """
        UPDATE knowledge_plan_decisions d
        SET payload=jsonb_set(d.payload, '{rationale}', to_jsonb(CAST(:cleared AS text))),
            plan_run_id=NULL
        WHERE d.deployment_id=:deployment_id
          AND (d.payload->>'rationale' IS DISTINCT FROM :cleared
               OR d.plan_run_id IS NOT NULL)
          AND (EXISTS (SELECT 1 FROM knowledge_page_rules r
                       WHERE r.plan_decision_id=d.decision_id)
               OR EXISTS (SELECT 1 FROM knowledge_quarantines q
                          WHERE q.decision_id=d.decision_id))
        """
    ),
    text(
        """
        DELETE FROM knowledge_plan_decisions d
        WHERE d.deployment_id=:deployment_id
          AND NOT EXISTS (SELECT 1 FROM knowledge_page_rules r
                          WHERE r.plan_decision_id=d.decision_id)
          AND NOT EXISTS (SELECT 1 FROM knowledge_quarantines q
                          WHERE q.decision_id=d.decision_id)
        """
    ),
    text(
        """
        DELETE FROM knowledge_plan_runs r
        WHERE r.deployment_id=:deployment_id
          AND NOT EXISTS (SELECT 1 FROM knowledge_plan_decisions d
                          WHERE d.plan_run_id=r.run_id)
        """
    ),
)
