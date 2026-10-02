"""D140 effective periods and version keys: the spine writes.

A caller may declare when each version of a ``snapshot`` lineage is in force
(its effective periods) and may give a version an immutable key. This module
owns every write of those facts:

- **At ingest**, inside ``DocumentCatalog.record_upload``'s transaction:
  :func:`resolve_version_key_on` applies the one version-key rule and
  :func:`declare_at_ingest_on` records a declared period on the new or D55
  no-op version.
- **After ingest**, through :class:`EffectiveTimeCatalog`:
  ``set_effective_periods`` replaces one version's live declarations and
  ``clear_effective_time`` retracts all of a lineage's and returns it to
  served-version semantics.

Declarations and mode changes are ledgers: a correction retracts a row and
inserts its replacement, and the lineage's mode is a second ledger of
``declared``/``cleared`` events. Every write happens with the lineage's
``documents`` row locked, and all rows one call writes carry one instant read
after that lock (``clock_timestamp()``), so writers of one lineage are ordered
on the belief axis in the order they committed. The selection projection
``document_version_scope`` is maintained by database triggers deferred to the
commit of the same transaction; nothing here writes it, and nothing here reads
it back before committing.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from remember.models import DeclaredEffectivePeriod
from remember.models import EffectivePeriodInput
from remember.models import EffectivePeriodsSet
from remember.models import EffectiveTimeCleared
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentVersionNotFoundError
from rememberstack.model import EffectivePeriodConflictError
from rememberstack.model import EffectiveTimeNotSupportedError
from rememberstack.model import VersionKeyConflictError


class EffectiveTimeCatalog:
    """The period API's writes (D140 §2.3) over an explicitly composed engine."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    def set_effective_periods(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        periods: Sequence[EffectivePeriodInput],
    ) -> EffectivePeriodsSet:
        """Replace one version's live declarations with ``periods``, atomically.

        Declarations already live with the same start and end are kept;
        the others are retracted and the new ones declared. An empty set
        leaves the version with no in-force period and the lineage
        periodised. A start equal to a live declaration of another version
        of the lineage raises ``EffectivePeriodConflictError`` and writes
        nothing. Raises ``DocumentNotFoundError`` for an absent lineage,
        ``DocumentVersionNotFoundError`` when the version is not a live
        version of it, and ``EffectiveTimeNotSupportedError`` for a
        ``living`` lineage.
        """
        with self._engine.begin() as connection:
            lineage = _lock_version(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                version_id=version_id,
            )
            if lineage["versioning_mode"] != "snapshot":
                raise EffectiveTimeNotSupportedError(
                    "effective periods require a snapshot lineage;"
                    f" {doc_id} is {lineage['versioning_mode']}"
                )
            instant = _instant(connection=connection, deployment_id=deployment_id)
            live = _live_declarations(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            for period in periods:
                holder = live.get(period.effective_from)
                if holder is not None and holder["version_id"] != version_id:
                    raise EffectivePeriodConflictError(
                        effective_from=period.effective_from,
                        version_id=holder["version_id"],
                    )
            wanted = {
                (period.effective_from, period.effective_until) for period in periods
            }
            own = [row for row in live.values() if row["version_id"] == version_id]
            kept = {
                (row["effective_from"], row["effective_until"])
                for row in own
                if (row["effective_from"], row["effective_until"]) in wanted
            }
            replacements = {
                start: uuid4() for start, end in wanted if (start, end) not in kept
            }
            retracted = 0
            for row in own:
                key = (row["effective_from"], row["effective_until"])
                if key in kept:
                    continue
                connection.execute(
                    _RETRACT_PERIOD,
                    {
                        "period_id": row["period_id"],
                        "retracted_at": instant,
                        "replacement": replacements.get(row["effective_from"]),
                    },
                )
                retracted += 1
            for start, end in sorted(wanted - kept):
                connection.execute(
                    _INSERT_PERIOD,
                    {
                        "period_id": replacements[start],
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "version_id": version_id,
                        "effective_from": start,
                        "effective_until": end,
                        "declared_at": instant,
                        "declared_by": "period_api",
                    },
                )
            if replacements:
                _mark_declared_on(
                    connection=connection,
                    deployment_id=deployment_id,
                    doc_id=doc_id,
                    instant=instant,
                )
            return EffectivePeriodsSet(
                doc_id=doc_id,
                version_id=version_id,
                periods=_version_declarations(
                    connection=connection,
                    deployment_id=deployment_id,
                    version_id=version_id,
                ),
                declared=len(replacements),
                retracted=retracted,
            )

    def clear_effective_time(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> EffectiveTimeCleared:
        """Retract every live declaration of the lineage and leave effective time.

        Records a ``cleared`` event when the lineage was periodised, so a read
        pinned to an earlier belief instant still sees it periodised. A
        lineage that never declared anything is left unchanged. Raises
        ``DocumentNotFoundError`` for an absent or deleted lineage.
        """
        with self._engine.begin() as connection:
            locked = connection.execute(
                _LOCK_LINEAGE, {"deployment_id": deployment_id, "doc_id": doc_id}
            ).one_or_none()
            if locked is None:
                raise DocumentNotFoundError(f"document {doc_id} does not exist")
            instant = _instant(connection=connection, deployment_id=deployment_id)
            retracted = connection.execute(
                _RETRACT_LINEAGE,
                {
                    "deployment_id": deployment_id,
                    "doc_id": doc_id,
                    "retracted_at": instant,
                },
            ).rowcount
            if not _periodised_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            ):
                return EffectiveTimeCleared(
                    doc_id=doc_id, retracted=retracted, cleared_at=None
                )
            connection.execute(
                _INSERT_EVENT,
                {
                    "deployment_id": deployment_id,
                    "doc_id": doc_id,
                    "event_at": instant,
                    "event": "cleared",
                },
            )
            return EffectiveTimeCleared(
                doc_id=doc_id, retracted=retracted, cleared_at=instant
            )


def resolve_version_key_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    version_key: str,
    content_hash: str,
    latest: RowMapping | None,
) -> bool:
    """Apply the one version-key rule; return whether a new version is created.

    Runs with the lineage row locked. A key new to the lineage always creates
    a version, even for bytes identical to the latest. A key that names the
    lineage's latest, non-deleted version observed with the same bytes is an
    idempotent retry (the D55 no-op). Any other use of an existing key —
    different bytes, an older version, a deleted one — raises
    ``VersionKeyConflictError`` naming the version that owns it.
    """
    owner = connection.execute(
        _SELECT_KEYED_VERSION,
        {"deployment_id": deployment_id, "doc_id": doc_id, "version_key": version_key},
    ).scalar_one_or_none()
    if owner is None:
        return True
    if (
        latest is not None
        and latest["version_id"] == owner
        and latest["content_hash"] == content_hash
        and latest["deleted_at"] is None
    ):
        return False
    raise VersionKeyConflictError(version_key=version_key, version_id=owner)


def declare_at_ingest_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    doc_id: UUID,
    version_id: UUID,
    versioning_mode: str,
    effective_from: datetime,
    effective_until: datetime | None,
) -> None:
    """Declare one period for the ingested version (new or D55 no-op).

    Runs with the lineage row locked, in the ingest's transaction. A
    declaration identical to a live one of the same version is a no-op; one
    with the same start but another end corrects it (the old row is
    retracted). A start held by a live declaration of another version raises
    ``EffectivePeriodConflictError`` and the whole ingest rolls back. The
    first declaration of an undeclared lineage records a ``declared`` event.
    """
    if versioning_mode != "snapshot":
        raise EffectiveTimeNotSupportedError(
            "effective periods require a snapshot lineage;"
            f" {doc_id} is {versioning_mode}"
        )
    live = _live_declarations(
        connection=connection, deployment_id=deployment_id, doc_id=doc_id
    )
    holder = live.get(effective_from)
    if holder is not None and holder["version_id"] != version_id:
        raise EffectivePeriodConflictError(
            effective_from=effective_from, version_id=holder["version_id"]
        )
    if holder is not None and holder["effective_until"] == effective_until:
        return
    instant = _instant(connection=connection, deployment_id=deployment_id)
    period_id = uuid4()
    if holder is not None:
        connection.execute(
            _RETRACT_PERIOD,
            {
                "period_id": holder["period_id"],
                "retracted_at": instant,
                "replacement": period_id,
            },
        )
    connection.execute(
        _INSERT_PERIOD,
        {
            "period_id": period_id,
            "deployment_id": deployment_id,
            "doc_id": doc_id,
            "version_id": version_id,
            "effective_from": effective_from,
            "effective_until": effective_until,
            "declared_at": instant,
            "declared_by": "ingest",
        },
    )
    _mark_declared_on(
        connection=connection,
        deployment_id=deployment_id,
        doc_id=doc_id,
        instant=instant,
    )


def _lock_version(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, version_id: UUID
) -> RowMapping:
    """Lock the live lineage and check, in the same statement, its version.

    The lineage row is the serialization point every D140 writer and ingest
    share; the version row takes only a key-share lock, so processing and
    deletion of the version are never blocked by a declaration.
    """
    row = (
        connection.execute(
            _LOCK_LINEAGE_VERSION,
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
            _LOCK_LINEAGE, {"deployment_id": deployment_id, "doc_id": doc_id}
        ).one_or_none()
        is None
    ):
        raise DocumentNotFoundError(f"document {doc_id} does not exist")
    raise DocumentVersionNotFoundError(
        f"version {version_id} is not a live version of document {doc_id}"
    )


_BELIEF_KEY = "'d140-belief:' || CAST(:deployment_id AS text)"

_STAMP_GUARD = text(
    f"SELECT pg_advisory_xact_lock_shared(hashtextextended({_BELIEF_KEY}, 0))"
)
_WATERMARK_LOCK = text(f"SELECT pg_advisory_lock(hashtextextended({_BELIEF_KEY}, 0))")
_WATERMARK_UNLOCK = text(
    f"SELECT pg_advisory_unlock(hashtextextended({_BELIEF_KEY}, 0))"
)


def _instant(*, connection: Connection, deployment_id: UUID) -> datetime:
    """The one instant a locked write stamps on every row it writes.

    The write holds the deployment's belief guard (shared) from this stamp
    until it commits, so :func:`belief_watermark` can never return an instant
    later than the stamp of a declaration that is still uncommitted.
    """
    connection.execute(_STAMP_GUARD, {"deployment_id": deployment_id})
    return connection.execute(text("SELECT clock_timestamp()")).scalar_one()


def belief_watermark(*, connection: Connection, deployment_id: UUID) -> datetime:
    """A belief instant a paged read can pin across pages (§3.6).

    Every effective-time ledger row stamped at or before the returned instant
    is committed when this returns, and every later write stamps after it. A
    pre-commit stamp alone does not give this: a writer that stamped before a
    reader's ``now()`` but committed after the reader's first page would
    appear on the second page only. Taking the deployment's belief guard
    exclusively waits for such writers to commit; the instant is read while
    the guard is held. The guard is a session lock released before returning,
    and the call ends its transaction, so the caller's next statement opens a
    snapshot that sees every row stamped up to the instant. The database's
    clock is used throughout, so application clock skew cannot hide a
    committed declaration either.
    """
    parameters = {"deployment_id": deployment_id}
    connection.execute(_WATERMARK_LOCK, parameters)
    try:
        instant = connection.execute(text("SELECT clock_timestamp()")).scalar_one()
    finally:
        connection.execute(_WATERMARK_UNLOCK, parameters)
        connection.commit()
    return instant


def _live_declarations(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> dict[datetime, RowMapping]:
    """The lineage's live declarations, keyed by start (unique while live)."""
    rows = (
        connection.execute(
            _SELECT_LIVE_DECLARATIONS,
            {"deployment_id": deployment_id, "doc_id": doc_id},
        )
        .mappings()
        .all()
    )
    return {row["effective_from"]: row for row in rows}


def _periodised_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID
) -> bool:
    """Whether the lineage's latest effective-time event is ``declared``."""
    return connection.execute(
        _SELECT_PERIODISED, {"deployment_id": deployment_id, "doc_id": doc_id}
    ).scalar_one()


def _mark_declared_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, instant: datetime
) -> None:
    """Record the ``declared`` event unless the lineage is already periodised."""
    if _periodised_on(
        connection=connection, deployment_id=deployment_id, doc_id=doc_id
    ):
        return
    connection.execute(
        _INSERT_EVENT,
        {
            "deployment_id": deployment_id,
            "doc_id": doc_id,
            "event_at": instant,
            "event": "declared",
        },
    )


def _version_declarations(
    *, connection: Connection, deployment_id: UUID, version_id: UUID
) -> tuple[DeclaredEffectivePeriod, ...]:
    """The version's live declarations, by start."""
    return tuple(
        DeclaredEffectivePeriod.model_validate(dict(row))
        for row in connection.execute(
            _SELECT_VERSION_DECLARATIONS,
            {"deployment_id": deployment_id, "version_id": version_id},
        ).mappings()
    )


_LOCK_LINEAGE = text(
    """
    SELECT doc_id FROM documents
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      AND deleted_at IS NULL
    FOR UPDATE
    """
)

_LOCK_LINEAGE_VERSION = text(
    """
    SELECT d.doc_id, d.versioning_mode::text AS versioning_mode, v.version_id
    FROM documents AS d
    JOIN document_versions AS v
      ON v.deployment_id = d.deployment_id
     AND v.doc_id = d.doc_id
     AND v.version_id = :version_id
     AND v.deleted_at IS NULL
    WHERE d.deployment_id = :deployment_id AND d.doc_id = :doc_id
      AND d.deleted_at IS NULL
    FOR UPDATE OF d
    FOR KEY SHARE OF v
    """
)

_SELECT_KEYED_VERSION = text(
    """
    SELECT version_id FROM document_versions
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      AND version_key = :version_key
    """
)

_SELECT_LIVE_DECLARATIONS = text(
    """
    SELECT period_id, version_id, effective_from, effective_until
    FROM document_effective_periods
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      AND retracted_at IS NULL
    """
)

_SELECT_VERSION_DECLARATIONS = text(
    """
    SELECT period_id, effective_from, effective_until, declared_at
    FROM document_effective_periods
    WHERE deployment_id = :deployment_id AND version_id = :version_id
      AND retracted_at IS NULL
    ORDER BY effective_from
    """
)

_SELECT_PERIODISED = text(
    """
    SELECT coalesce(
        (SELECT event = 'declared' FROM document_effective_time_events
         WHERE deployment_id = :deployment_id AND doc_id = :doc_id
         ORDER BY event_at DESC LIMIT 1),
        false
    )
    """
)

_INSERT_PERIOD = text(
    """
    INSERT INTO document_effective_periods (
        period_id, deployment_id, doc_id, version_id, effective_from,
        effective_until, declared_at, declared_by
    ) VALUES (
        :period_id, :deployment_id, :doc_id, :version_id, :effective_from,
        :effective_until, :declared_at, :declared_by
    )
    """
)

_RETRACT_PERIOD = text(
    """
    UPDATE document_effective_periods
    SET retracted_at = :retracted_at, retracted_by_period_id = :replacement
    WHERE period_id = :period_id AND retracted_at IS NULL
    """
)

_RETRACT_LINEAGE = text(
    """
    UPDATE document_effective_periods
    SET retracted_at = :retracted_at
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
      AND retracted_at IS NULL
    """
)

_INSERT_EVENT = text(
    """
    INSERT INTO document_effective_time_events (deployment_id, doc_id, event_at, event)
    VALUES (:deployment_id, :doc_id, :event_at, :event)
    """
)
