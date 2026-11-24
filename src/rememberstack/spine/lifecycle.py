"""The lifecycle catalog (D54/D55/D159): currency, recount, closure, archive.

Spine-owned SQL for the reconciliation flow (lifecycle §5) and the removal
grains (§8): archive, unarchive and the internal version removal. The `testimony_currency_events` ledger is truth and
`claims.is_current_testimony` is cache; every write here is idempotent under
a stable `reconciliation_id` (a retried run re-emits its rows as no-ops) so
reconciliation can ride the ordinary work ledger.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import UTC
from typing import Self
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.sql.elements import TextClause

from rememberstack.model import CurrencyTransition
from rememberstack.model import ReconciliationDelta
from rememberstack.spine.document_bindings import live_binding_rows
from rememberstack.spine.fact_applications import application_fence

CURRENCY_CACHE_MISMATCH_SQL = """
    SELECT cl.claim_id,
           cl.is_current_testimony AS cached_current,
           coalesce(last.became_current, true) AS ledger_current
    FROM claims cl
    LEFT JOIN LATERAL (
        SELECT e.became_current
        FROM testimony_currency_events e
        WHERE e.claim_id = cl.claim_id
        ORDER BY e.occurred_at DESC, e.event_id DESC
        LIMIT 1
    ) last ON true
    WHERE cl.deployment_id = :deployment_id
      AND cl.is_current_testimony <> coalesce(last.became_current, true)
"""
"""Single source for the D33 currency-cache versus append-only-ledger invariant."""


def archive_reconciliation_id(*, doc_id: UUID, archived_at: datetime) -> UUID:
    """The archive episode's reconciliation id, from ``doc_id`` and ``archived_at``.

    Every transition and closure an archive causes — late retirements
    included — is recorded under it, so unarchive finds them (D159 §2.1). A
    repeat of the same archive reuses it; a later archive gets a new one. The
    instant is rendered in UTC so the id never depends on a session time
    zone; the D159 upgrade derives the same id (its migration keeps a frozen
    copy of this rule).
    """
    return uuid5(
        NAMESPACE_URL,
        f"rememberstack:archive:{doc_id}:{archived_at.astimezone(UTC).isoformat()}",
    )


class _PinnedConnection:
    """Serve ``begin``/``connect`` from one open transaction (savepoints).

    Lets every catalog method below run inside a caller's transaction, so a
    multi-step operation — archive or unarchive (D159) — commits or rolls
    back as one unit and holds its locks for its whole duration.
    """

    def __init__(self, *, connection: Connection) -> None:
        self._connection = connection

    @contextmanager
    def begin(self) -> Iterator[Connection]:
        with self._connection.begin_nested():
            yield self._connection

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        yield self._connection


class LifecycleCatalog:
    """Currency transitions, the D54 recount, per-shape closure, archive."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the catalog to the spine database."""
        self._engine = engine

    @classmethod
    def on_connection(cls, *, connection: Connection) -> Self:
        """A catalog whose every statement runs in ``connection``'s transaction."""
        catalog = cls.__new__(cls)
        catalog._engine = _PinnedConnection(connection=connection)  # type: ignore[assignment]
        return catalog

    @contextmanager
    def transaction(self) -> Iterator["LifecycleCatalog"]:
        """A catalog whose statements share one transaction, committed on exit."""
        with self._engine.begin() as connection:
            yield LifecycleCatalog.on_connection(connection=connection)

    def request_window_review(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        reason: str,
        request_id: UUID | None = None,
    ) -> bool:
        """Queue a D140 §8.2 fact window review of a lineage that had periods.

        Returns whether one was queued; a lineage that never declared an
        effective period queues nothing.
        """
        from rememberstack.spine.period_adjudication import request_window_review_on

        with self._engine.begin() as connection:
            return request_window_review_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                reason=reason,
                request_id=request_id,
            )

    def lineage_ever_periodised(self, *, deployment_id: UUID, doc_id: UUID) -> bool:
        """Whether the lineage ever recorded an effective-time event (D140)."""
        from rememberstack.spine.period_adjudication import lineage_ever_periodised_on

        with self._engine.connect() as connection:
            return lineage_ever_periodised_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )

    def reconciliation_context(self, *, version_id: UUID) -> dict[str, object]:
        """What reconciling one completed version needs to know."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(_SELECT_CONTEXT, {"version_id": version_id})
                .mappings()
                .one()
            )
        return dict(row)

    def layers_configured(self, *, deployment_id: UUID) -> bool:
        """Whether the deployment has a D143 processing-layer configuration."""
        from rememberstack.spine.processing_layers import layer_configuration_on

        with self._engine.connect() as connection:
            return layer_configuration_on(
                connection=connection, deployment_id=deployment_id
            ).configured

    def apply_guarded_supersession(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        version_id: UUID,
        reconciliation_id: UUID,
    ) -> tuple[CurrencyTransition, ...]:
        """D143 §4.2: living supersession against a settled current version only.

        In one transaction under the lineage lock: read the current version,
        require it to be ``version_id`` or to have complete claim occurrences,
        compute the stale set and apply it. Otherwise nothing is retired; the
        current version's own reconcile settles the lineage when it runs.
        """
        from rememberstack.spine.processing_layers import occurrences_complete_on

        with self.transaction() as catalog, catalog._engine.connect() as connection:
            current = connection.execute(
                text(
                    "SELECT current_version_id FROM documents"
                    " WHERE doc_id = :doc_id FOR SHARE"
                ),
                {"doc_id": doc_id},
            ).scalar_one_or_none()
            if current is None:
                return ()
            if current != version_id and not occurrences_complete_on(
                connection=connection, version_id=current
            ):
                return ()
            stale = catalog.stale_for_supersession(
                deployment_id=deployment_id, doc_id=doc_id, current_version_id=current
            )
            catalog.apply_transitions(
                deployment_id=deployment_id,
                reconciliation_id=reconciliation_id,
                transitions=stale,
            )
            return stale

    def stale_for_supersession(
        self, *, deployment_id: UUID, doc_id: UUID, current_version_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """Living-mode §3 rule: current claims the current version left behind.

        A claim stays current iff SOME chunk of the current version carries
        it — by origin or by a reuse occurrence link. Everything else of the
        lineage flips `version_superseded`.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_STALE_SUPERSEDED,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "current_version_id": current_version_id,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=doc_id,
                became_current=False,
                reason="version_superseded",
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def stale_for_reextraction(
        self,
        *,
        version_id: UUID,
        representation_id: UUID,
        chunker_version: str,
        extractor_version: str,
    ) -> tuple[CurrencyTransition, ...]:
        """Re-derivation §3 rule: older-BASIS claims on a re-read version.

        The reason covers ANY basis-coordinate change (§3): an extractor
        bump, a converter bump (a different representation), or a
        blockizer/chunker bump (a different packing generation) — a claim
        of this version whose (representation, chunker generation,
        extractor generation) differs from the completing basis flips
        `reextracted`, wholesale, by coordinates, no content matching.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_STALE_REEXTRACTED,
                    {
                        "version_id": version_id,
                        "representation_id": representation_id,
                        "chunker_version": chunker_version,
                        "extractor_version": extractor_version,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=row["doc_id"],
                became_current=False,
                reason="reextracted",
                from_extractor_version=row["from_extractor_version"],
            )
            for row in rows
        )

    def stale_for_version_deletion(
        self, *, deployment_id: UUID, version_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """§8 version grain: ONLY the deleted version's exclusive testimony ends.

        A claim flips `version_archived` iff it is carried by the deleted
        version and by no other live version of the lineage — snapshot
        lineages keep every other version's testimony current.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_STALE_VERSION_DELETED,
                    {"deployment_id": deployment_id, "version_id": version_id},
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=row["doc_id"],
                became_current=False,
                reason="version_archived",
                from_version_id=version_id,
            )
            for row in rows
        )

    def regained_by_current_version(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        current_version_id: UUID,
        superseded_only: bool = False,
    ) -> tuple[CurrencyTransition, ...]:
        """Non-current claims the (new) current version carries — regained.

        Deleting the newest version repoints the lineage at its predecessor
        (§8: "the lineage continues"); the predecessor's testimony IS the
        current basis again, so its claims regain currency — an append-only
        `became_current = true` event, never a rewrite.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_REGAINED_SUPERSEDED
                    if superseded_only
                    else _SELECT_REGAINED,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "current_version_id": current_version_id,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=doc_id,
                became_current=True,
                reason="version_archived",
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def recorded_transitions(
        self, *, reconciliation_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """This run's already-ledgered transitions (the retry recovery read).

        A crash between the currency transaction and the downstream steps
        must not orphan the run: a retry unions what the ledger already
        holds under this reconciliation_id with whatever it recomputes, so
        recount/closure/flags/emission always see the full set.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_RUN_EVENTS, {"reconciliation_id": reconciliation_id}
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=row["doc_id"],
                became_current=row["became_current"],
                reason=row["reason"],
                from_extractor_version=row["from_extractor_version"],
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def apply_transitions(
        self,
        *,
        deployment_id: UUID,
        reconciliation_id: UUID,
        transitions: tuple[CurrencyTransition, ...],
    ) -> int:
        """Append ledger rows and update the cache flags (D54, F11-idempotent).

        The guarded insert makes a retried run's re-emission a no-op: an
        event with the same (claim, reconciliation, reason, direction)
        already in the ledger is never duplicated. The cache follows the
        ledger in the same transaction. Returns how many events were new.
        """
        if not transitions:
            return 0
        # Collapse exact repeats before the set write. The former row loop
        # made the first repeat win through its NOT EXISTS guard.
        unique: dict[tuple[UUID, str, bool], CurrencyTransition] = {}
        for transition in transitions:
            unique.setdefault(
                (transition.claim_id, transition.reason, transition.became_current),
                transition,
            )
        event_rows = [
            {
                "event_id": str(uuid4()),
                "claim_id": str(transition.claim_id),
                "doc_id": str(transition.doc_id),
                "became_current": transition.became_current,
                "reason": transition.reason,
                "from_extractor_version": transition.from_extractor_version,
                "from_version_id": (
                    str(transition.from_version_id)
                    if transition.from_version_id is not None
                    else None
                ),
            }
            for transition in unique.values()
        ]
        # A claim can legitimately flip more than once in a composed run.
        # Match the former loop: the final transition owns the cache value.
        cache_rows = [
            {"claim_id": str(claim_id), "is_current": is_current}
            for claim_id, is_current in {
                transition.claim_id: transition.became_current
                for transition in transitions
            }.items()
        ]
        with (
            self._engine.begin() as connection,
            application_fence(connection=connection, deployment_id=deployment_id),
        ):
            connection.execute(
                text(
                    "SELECT claim_id FROM claims WHERE deployment_id=:deployment_id AND claim_id=ANY(:ids) ORDER BY claim_id FOR UPDATE"
                ),
                {
                    "deployment_id": deployment_id,
                    "ids": sorted({transition.claim_id for transition in transitions}),
                },
            ).all()
            applied = connection.execute(
                _INSERT_CURRENCY_EVENTS,
                {
                    "deployment_id": deployment_id,
                    "reconciliation_id": reconciliation_id,
                    "events": event_rows,
                },
            ).scalar_one()
            connection.execute(_UPDATE_CURRENCY_CACHES, {"claims": cache_rows})
        return int(applied)

    def affected_relation_ids(self, *, claim_ids: tuple[UUID, ...]) -> tuple[UUID, ...]:
        """Relations evidenced by any of these claims (the recount scope)."""
        if not claim_ids:
            return ()
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_AFFECTED_RELATIONS, {"claim_ids": list(claim_ids)}
                ).scalars()
            )

    def affected_observation_ids(
        self, *, claim_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        """Observations evidenced by any of these claims (the recount scope)."""
        if not claim_ids:
            return ()
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_AFFECTED_OBSERVATIONS, {"claim_ids": list(claim_ids)}
                ).scalars()
            )

    def recount(
        self, *, relation_ids: tuple[UUID, ...], observation_ids: tuple[UUID, ...]
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        """Recompute the D54 counts for the touched facts (bounded, indexed).

        Returns the facts whose counts actually CHANGED — the stale-storm
        guard's input: a re-extraction that changes no fact state must
        stale nothing, so only changed facts belong in the emitted delta.
        """
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT relation_id FROM relations WHERE relation_id=ANY(:ids) ORDER BY relation_id FOR UPDATE"
                ),
                {"ids": sorted(relation_ids)},
            ).all()
            connection.execute(
                text(
                    "SELECT observation_id FROM observations WHERE observation_id=ANY(:ids) ORDER BY observation_id FOR UPDATE"
                ),
                {"ids": sorted(observation_ids)},
            ).all()
            changed_relation_ids = (
                frozenset(
                    connection.execute(
                        _RECOUNT_RELATIONS, {"relation_ids": list(relation_ids)}
                    ).scalars()
                )
                if relation_ids
                else frozenset()
            )
            changed_observation_ids = (
                frozenset(
                    connection.execute(
                        _RECOUNT_OBSERVATIONS,
                        {"observation_ids": list(observation_ids)},
                    ).scalars()
                )
                if observation_ids
                else frozenset()
            )
        # RETURNING order is not a SQL contract; retain the prior row-loop's
        # caller-visible input order while keeping the write set-based.
        return (
            tuple(item for item in relation_ids if item in changed_relation_ids),
            tuple(item for item in observation_ids if item in changed_observation_ids),
        )

    def open_zero_support_relations(
        self, *, deployment_id: UUID, relation_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        """Still-open, still-believed relations whose support hit zero."""
        if not relation_ids:
            return ()
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_ZERO_RELATIONS,
                    {
                        "deployment_id": deployment_id,
                        "relation_ids": list(relation_ids),
                    },
                ).scalars()
            )

    def open_zero_support_observations(
        self, *, deployment_id: UUID, observation_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        """Still-believed observations whose support hit zero."""
        if not observation_ids:
            return ()
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_ZERO_OBSERVATIONS,
                    {
                        "deployment_id": deployment_id,
                        "observation_ids": list(observation_ids),
                    },
                ).scalars()
            )

    def close_relations(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        boundary: object,
        reconciliation_id: UUID,
        archive: bool = False,
    ) -> tuple[UUID, ...]:
        """Withdraw system belief at zero support without inventing a world-time end.

        ``archive`` marks the closure as an archive's (D159), the closures an
        unarchive or a returning claim may reopen.
        """
        closed: list[UUID] = []
        with self._engine.begin() as connection:
            for relation_id in sorted(relation_ids):
                closed_at = connection.execute(
                    _INVALIDATE_RELATION,
                    {
                        "relation_id": relation_id,
                        "deployment_id": deployment_id,
                        "reconciliation_id": str(reconciliation_id),
                    },
                ).scalar_one_or_none()
                if closed_at is None:
                    continue  # already closed by an earlier attempt
                closed.append(relation_id)
                _record_adjudication(
                    connection=connection,
                    sql=_INSERT_RELATION_RETRACTION,
                    deployment_id=deployment_id,
                    fact_id=relation_id,
                    reconciliation_id=reconciliation_id,
                    archive=archive,
                    decided_at=closed_at,
                )
        return tuple(closed)

    def close_observations(
        self,
        *,
        deployment_id: UUID,
        observation_ids: tuple[UUID, ...],
        reconciliation_id: UUID,
        archive: bool = False,
    ) -> tuple[UUID, ...]:
        """Close solely-supported observations per shape (§4, D43 no-cap).

        Observations are untyped (state vs measurement is semantic), so the
        mechanical exit that is safe for BOTH shapes is `invalidated_at` —
        belief ends without asserting a world-time end (capping a
        measurement's window is forbidden by the no-cap rule).
        """
        closed: list[UUID] = []
        with self._engine.begin() as connection:
            for observation_id in sorted(observation_ids):
                closed_at = connection.execute(
                    _INVALIDATE_OBSERVATION,
                    {
                        "observation_id": observation_id,
                        "reconciliation_id": str(reconciliation_id),
                    },
                ).scalar_one_or_none()
                if closed_at is None:
                    continue
                closed.append(observation_id)
                _record_adjudication(
                    connection=connection,
                    sql=_INSERT_OBSERVATION_RETRACTION,
                    deployment_id=deployment_id,
                    fact_id=observation_id,
                    reconciliation_id=reconciliation_id,
                    archive=archive,
                    decided_at=closed_at,
                )
        return tuple(closed)

    def emit_evidence_changed(
        self, *, deployment_id: UUID, delta: ReconciliationDelta
    ) -> bool:
        """Queue one fact-level `evidence_changed` trigger (D45).

        Guarded by the reconciliation id inside the payload, so a retried
        run never double-fires the K compile driver. Nothing is emitted for
        an empty delta ("a new claim row for the same testimony is not an
        evidence change").
        """
        if not (
            delta.recounted_relations
            or delta.recounted_observations
            or delta.relations_closed
            or delta.observations_closed
            or delta.flags_raised
        ):
            return False
        with self._engine.begin() as connection:
            inserted = connection.execute(
                _INSERT_EVIDENCE_CHANGED,
                {
                    "refresh_id": uuid4(),
                    "deployment_id": deployment_id,
                    "reconciliation_id": str(delta.reconciliation_id),
                    "payload": delta.model_dump(mode="json"),
                },
            ).scalar_one_or_none()
        return inserted is not None

    def delete_version(self, *, version_id: UUID) -> dict[str, object]:
        """Mark one version deleted; repoint currency if it was current (§8).

        The lineage continues: if the deleted version held the current
        pointer, the latest remaining live version takes it (NULL when none
        remain). Returns the lineage context for the caller's cascade.
        """
        from rememberstack.spine.corpus_publication import corpus_changed_on
        from rememberstack.spine.processing_layers import lineage_changed_on
        from rememberstack.spine.processing_layers import lock_lineage_for_layers_on

        with self._engine.begin() as connection:
            lineage = (
                connection.execute(_SELECT_VERSION_LINEAGE, {"version_id": version_id})
                .mappings()
                .one()
            )
            # D143 §6.3: the layer fence and lineage lock precede the tombstone,
            # so the repoint and the gap wake-up commit together.
            lock_lineage_for_layers_on(
                connection=connection,
                deployment_id=lineage["deployment_id"],
                doc_id=lineage["doc_id"],
            )
            row = (
                connection.execute(_TOMBSTONE_VERSION, {"version_id": version_id})
                .mappings()
                .one()
            )
            connection.execute(
                _REPOINT_AFTER_VERSION_DELETE,
                {"doc_id": row["doc_id"], "version_id": version_id},
            )
            connection.execute(
                _CLEAR_DOCUMENT_BINDINGS,
                {"deployment_id": row["deployment_id"], "doc_id": row["doc_id"]},
            )
            # D140 §8.2: the lineage continues with different in-force
            # intervals; queued in the tombstone's transaction (crash-safe)
            _request_review_on(
                connection=connection,
                deployment_id=row["deployment_id"],
                doc_id=row["doc_id"],
                reason="version_deleted",
            )
            # D143: a deleted head never blocks its successors; a repointed
            # living lineage processes its new current version.
            lineage_changed_on(
                connection=connection,
                deployment_id=row["deployment_id"],
                doc_id=row["doc_id"],
            )
            corpus_changed_on(
                connection=connection,
                deployment_id=row["deployment_id"],
                doc_id=row["doc_id"],
            )
        return dict(row)

    def delete_lineage(self, *, doc_id: UUID) -> datetime:
        """Tombstone a lineage and every version (the operator forget's hide).

        The caller runs the currency cascade. Returns the lineage's deletion
        instant (the first one, on a repeated call).
        """
        from rememberstack.spine.corpus_publication import corpus_changed_on
        from rememberstack.spine.processing_layers import lineage_changed_on
        from rememberstack.spine.processing_layers import lock_lineage_for_layers_on

        with self._engine.begin() as connection:
            deployment_id = connection.execute(
                text("SELECT deployment_id FROM documents WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            ).scalar_one()
            # D143 §6.3: fence and lineage lock first; gaps go with the tombstone.
            lock_lineage_for_layers_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            connection.execute(_TOMBSTONE_LINEAGE_BY_ID, {"doc_id": doc_id})
            connection.execute(_TOMBSTONE_LINEAGE_VERSIONS, {"doc_id": doc_id})
            connection.execute(_CLEAR_DOCUMENT_BINDINGS_BY_DOC, {"doc_id": doc_id})
            # D140 §8.2: facts that also rest on other sources lose this
            # lineage's periods; queued in the tombstone's transaction
            _request_review_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                reason="lineage_deleted",
            )
            lineage_changed_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            corpus_changed_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            return connection.execute(
                _SELECT_LINEAGE_DELETED_AT, {"doc_id": doc_id}
            ).scalar_one()

    def clear_document_bindings(self, *, deployment_id: UUID, doc_id: UUID) -> None:
        """Drop T4 anchors a deleted lineage's late pipeline work created (D102)."""
        with self._engine.begin() as connection:
            connection.execute(
                _CLEAR_DOCUMENT_BINDINGS,
                {"deployment_id": deployment_id, "doc_id": doc_id},
            )

    def stale_for_lineage_removal(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """Current claims of the lineage that no live version carries.

        "Live" excludes an archived lineage and a deleted version (D159), so
        for an archived lineage this is every claim still current, while
        testimony of a lineage that is live again is never retired by an
        archive that started before it came back.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_STALE_UNCARRIED,
                    {"deployment_id": deployment_id, "doc_id": doc_id},
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=doc_id,
                became_current=False,
                reason="version_archived",
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def set_archived(
        self, *, doc_id: UUID, archived_by: str, cycle_id: UUID | None = None
    ) -> datetime:
        """Record the lineage archived (D159 §2.1); returns its archive instant.

        A repeat keeps the first instant (so the episode's reconciliation id
        does not move); a caller archiving a source-archived lineage takes it
        over, so identical bytes from the source no longer undo it, and its
        cascade no longer waits for the observing sync cycle (``cycle_id``,
        stamped on a source archive for the finalization barrier). The T4
        anchors go, and a new archive queues the D140 window review, the D143
        layer advance and the D155 corpus removal in the same transaction.
        """
        from rememberstack.spine.corpus_publication import corpus_changed_on
        from rememberstack.spine.processing_layers import lineage_changed_on

        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    _SET_ARCHIVED,
                    {
                        "doc_id": doc_id,
                        "archived_by": archived_by,
                        "cycle_id": cycle_id,
                    },
                )
                .mappings()
                .one()
            )
            deployment_id: UUID = row["deployment_id"]
            connection.execute(_CLEAR_DOCUMENT_BINDINGS_BY_DOC, {"doc_id": doc_id})
            if not row["newly_archived"]:
                return row["archived_at"]
            _request_review_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                reason="lineage_archived",
            )
            lineage_changed_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            corpus_changed_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            return row["archived_at"]

    def clear_archived(self, *, deployment_id: UUID, doc_id: UUID) -> None:
        """The lineage is live again (D159 §2.3): clear the archive marks.

        Queues the D140 window review, the D143 layer advance and the D155
        corpus publication in the same transaction.
        """
        from rememberstack.spine.corpus_publication import corpus_changed_on
        from rememberstack.spine.processing_layers import lineage_changed_on

        with self._engine.begin() as connection:
            connection.execute(_CLEAR_ARCHIVED, {"doc_id": doc_id})
            _request_review_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                reason="lineage_unarchived",
            )
            lineage_changed_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            corpus_changed_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )

    def archived_testimony(
        self, *, deployment_id: UUID, doc_id: UUID, archive_reconciliation_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """The claims this archive ended that a live version carries again.

        A claim qualifies when its latest currency event is the archive's
        retirement (found through the archive reconciliation id, including
        late work retired after the archive) — so a claim something else has
        changed since is left alone. Read after the archive marks are
        cleared: a claim of an internally removed version stays retired.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_ARCHIVED_TESTIMONY,
                    {
                        "deployment_id": deployment_id,
                        "doc_id": doc_id,
                        "reconciliation_id": archive_reconciliation_id,
                    },
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=doc_id,
                became_current=True,
                reason="version_archived",
                from_version_id=row["from_version_id"],
            )
            for row in rows
        )

    def regained_after_archive(
        self, *, deployment_id: UUID, version_id: UUID
    ) -> tuple[CurrencyTransition, ...]:
        """Claims a completing version carries whose currency an archive ended.

        A changed version of an archived document reuses the archived
        versions' claims (D56); a reused claim becomes current again here
        (D159 §2.3) — only for a version that arrived after the archive, and
        only while the lineage is live.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_REGAINED_AFTER_ARCHIVE,
                    {"deployment_id": deployment_id, "version_id": version_id},
                )
                .mappings()
                .all()
            )
        return tuple(
            CurrencyTransition(
                claim_id=row["claim_id"],
                doc_id=row["doc_id"],
                became_current=True,
                reason="version_archived",
                from_version_id=version_id,
            )
            for row in rows
        )

    def reopen_archive_closures(
        self,
        *,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
        observation_ids: tuple[UUID, ...],
        reconciliation_id: UUID,
    ) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        """Reopen facts whose current closure came from an archive (D159 §2.3).

        A fact reopens when its latest close or reopen event is an archive's
        closure (any archive) and it has current support again after the
        recount, or an open ``support_withdrawn`` review on a live claim held
        it open. Reopening clears ``invalidated_at`` and appends a
        ``reopened_unarchive`` adjudication, never erasing the close, so a
        belief-as-of read inside the archive interval still sees it closed. A
        fact something else has closed since is left as it is.
        """
        reopened: dict[str, list[UUID]] = {"relation": [], "observation": []}
        with self._engine.begin() as connection:
            for kind, ids in (
                ("relation", relation_ids),
                ("observation", observation_ids),
            ):
                if not ids:
                    continue
                lock, select, reopen, record = _REOPEN_SQL[kind]
                # The fact row locks a recount or a review verdict also takes,
                # then the eligibility read as a new statement (a fresh
                # snapshot that sees whatever committed before the locks).
                connection.execute(lock, {"fact_ids": sorted(ids)})
                for fact_id in connection.execute(
                    select, {"deployment_id": deployment_id, "fact_ids": sorted(ids)}
                ).scalars():
                    if connection.execute(reopen, {"fact_id": fact_id}).rowcount != 1:
                        continue
                    connection.execute(
                        record,
                        {
                            "adjudication_id": uuid4(),
                            "deployment_id": deployment_id,
                            "fact_id": fact_id,
                            "features": {"reconciliation_id": str(reconciliation_id)},
                        },
                    )
                    reopened[kind].append(fact_id)
        return tuple(reopened["relation"]), tuple(reopened["observation"])

    def reopen_cut_short_work(self, *, doc_id: UUID, archived_at: datetime) -> int:
        """Run again the work of live versions the archive may have cut short.

        Any reconcile that ran (or is running) while the lineage was archived
        may have retired late work and ended the chain; only one that finished
        before the archive surely ran normally. Work that dead-lettered at or
        after the archive instant (a D135 deletion refused publication) is
        retried too. Those rows go back to pending — the same rows, so layer
        frontiers and readiness see their completion. Timing-independent: a
        run reopened while it is still running cannot complete, and repeating
        a normal reconcile changes nothing. Returns how many rows were reopened.
        """
        with self._engine.begin() as connection:
            return len(
                connection.execute(
                    _REOPEN_CUT_SHORT_WORK,
                    {"doc_id": doc_id, "archived_at": archived_at},
                ).all()
            )

    def resolve_deleted_support_reviews(
        self, *, deployment_id: UUID, claim_ids: tuple[UUID, ...]
    ) -> tuple[UUID, ...]:
        """Auto-resolve support_withdrawn reviews whose claim was removed.

        A support_withdrawn review asks whether an extractor was right to
        stop deriving a claim from an unchanged file. Once a removal leaves no
        live version carrying the claim (an internal version removal, or the
        operator forget), the source itself has acted and the question is
        moot: the review closes as ``auto_resolved`` (no verdict, history
        appended). An archive never calls this: it leaves reviews as they are,
        moot only while the document is archived (D159 §2.1).
        """
        if not claim_ids:
            return ()
        with self._engine.begin() as connection:
            return tuple(
                connection.execute(
                    _AUTO_RESOLVE_DELETED_REVIEWS,
                    {
                        "deployment_id": deployment_id,
                        "claim_ids": [str(claim_id) for claim_id in claim_ids],
                    },
                ).scalars()
            )

    def rebuild_live_document_bindings(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> None:
        """Rebuild a live lineage's D102 anchors from its live testimony only.

        Late work of a deleted version can add bindings to a lineage that is
        live again. Rather than dropping the live version's anchors with them,
        the lineage's rows are recomputed from resolution decisions whose
        mention belongs to testimony a live version carries.
        """

        with self._engine.begin() as connection:
            connection.execute(
                _CLEAR_DOCUMENT_BINDINGS,
                {"deployment_id": deployment_id, "doc_id": doc_id},
            )
            decisions = (
                connection.execute(
                    _SELECT_LIVE_BINDING_DECISIONS,
                    {"deployment_id": deployment_id, "doc_id": doc_id},
                )
                .mappings()
                .all()
            )
            rows = live_binding_rows(
                deployment_id=deployment_id, decisions=[dict(row) for row in decisions]
            )
            if rows:
                connection.execute(_INSERT_BINDING, rows)

    def lineage_state(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> dict[str, object] | None:
        """``archived_at``, ``archived_by`` and ``deleted_at`` of a lineage.

        ``None`` when the deployment never held the lineage.
        """
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _SELECT_LINEAGE_STATE,
                    {"deployment_id": deployment_id, "doc_id": doc_id},
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else dict(row)

    def cycles_ready_to_finalize(
        self, *, deployment_id: UUID
    ) -> tuple[tuple[UUID, int], ...]:
        """Completed, unfinalized cycles whose observed versions are done.

        A cycle is ready when no version it stamped still has pending,
        running, or retrying (failed) chain work — lineages still
        extracting defer the cycle to the next finalization pass (the
        recorded grace). Each entry carries the cycle's `failed_items`
        count: a LOSSY cycle's observation set is incomplete and must not
        drive absence-based closure.
        """
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _SELECT_READY_CYCLES, {"deployment_id": deployment_id}
                )
                .mappings()
                .all()
            )
        return tuple((row["cycle_id"], row["failed_items"]) for row in rows)

    def cycle_reopened_at(self, *, cycle_id: UUID) -> datetime | None:
        """When a D143 layer entry last reopened the cycle, if it ever did."""
        with self._engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT layers_reopened_at FROM connector_sync_cycles"
                    " WHERE cycle_id = :cycle_id"
                ),
                {"cycle_id": cycle_id},
            ).scalar_one_or_none()

    def cycle_lineages(self, *, cycle_id: UUID) -> tuple[UUID, ...]:
        """Lineages the cycle observed via ingested versions."""
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_CYCLE_LINEAGES, {"cycle_id": cycle_id}
                ).scalars()
            )

    def lock_lineage(self, *, doc_id: UUID) -> None:
        """Lock the lineage row, serializing with E0 (re)ingest of it (D135)."""
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT doc_id FROM documents WHERE doc_id = :doc_id FOR UPDATE"),
                {"doc_id": doc_id},
            )

    def current_version_if_removed_since(
        self, *, doc_id: UUID, since: datetime
    ) -> UUID | None:
        """The lineage's current version when a newer version (the head) was
        removed at or after ``since``, repointing it; ``None`` otherwise."""
        with self._engine.connect() as connection:
            return connection.execute(
                text(
                    "SELECT d.current_version_id FROM documents d"
                    " JOIN document_versions cur"
                    "   ON cur.version_id = d.current_version_id"
                    " WHERE d.doc_id = :doc_id AND EXISTS ("
                    "   SELECT 1 FROM document_versions v"
                    "   WHERE v.doc_id = d.doc_id AND v.deleted_at >= :since"
                    "     AND v.version_no > cur.version_no)"
                ),
                {"doc_id": doc_id, "since": since},
            ).scalar_one_or_none()

    def archive_waits_for_cycle(self, *, doc_id: UUID) -> bool:
        """Whether the lineage's source archive waits for its observing cycle."""
        with self._engine.connect() as connection:
            return bool(
                connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM documents d"
                        " WHERE d.doc_id = :doc_id AND "
                        + archive_cycle_open(doc="d")
                        + ")"
                    ),
                    {"doc_id": doc_id},
                ).scalar_one()
            )

    def lineage_archived_at(self, *, doc_id: UUID) -> datetime | None:
        """When the lineage was archived; ``None`` while it is live."""
        with self._engine.connect() as connection:
            return connection.execute(
                text("SELECT archived_at FROM documents WHERE doc_id = :doc_id"),
                {"doc_id": doc_id},
            ).scalar_one_or_none()

    def version_claim_ids(
        self, *, deployment_id: UUID, version_id: UUID
    ) -> tuple[UUID, ...]:
        """Every claim a version carries, by origin or by a reuse link."""
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_VERSION_CLAIMS,
                    {"deployment_id": deployment_id, "version_id": version_id},
                ).scalars()
            )

    def pending_archives(
        self, *, deployment_id: UUID
    ) -> tuple[tuple[UUID, datetime], ...]:
        """Archived lineages whose cascade still has work left (D159).

        A source archive defers its cascade until its observing cycle is
        finalized, and work still in flight can attach support after any
        archive: a lineage is pending while
        one of its claims is current, or evidences an open fact that has no
        current support and no live review holding it open. Each row is
        ``(doc_id, archived_at)``, which names the archive episode.
        Deployment-wide, not per cycle: the cascade re-derives from current
        state, so a finalizer crash self-heals on the next pass.
        """
        with self._engine.connect() as connection:
            return tuple(
                (row["doc_id"], row["archived_at"])
                for row in connection.execute(
                    _SELECT_PENDING_ARCHIVES, {"deployment_id": deployment_id}
                ).mappings()
            )

    def lineage_claim_ids(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[UUID, ...]:
        """Every claim the lineage ever produced (the closure-scan scope)."""
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_LINEAGE_CLAIMS,
                    {"deployment_id": deployment_id, "doc_id": doc_id},
                ).scalars()
            )

    def closure_boundary(self, *, doc_id: UUID) -> object:
        """The §4 cap boundary: the current version's source-modified time."""
        with self._engine.connect() as connection:
            return connection.execute(
                _SELECT_CLOSURE_BOUNDARY, {"doc_id": doc_id}
            ).scalar_one_or_none()

    def claim_ready_finalization(self, *, deployment_id: UUID, cycle_id: UUID) -> bool:
        """Claim a cycle only if it is still ready, serialized against layers (D143).

        Used on a pinned transaction that also runs the cycle's closure. The
        shared layer fence keeps a configuration change out until commit; the
        cycle row lock and the barrier re-read inside it make a candidate list
        a hint only: a layer entry or new work since selection keeps the cycle
        open, and a later entry reopens what this commits.
        """
        from rememberstack.spine.processing_layers import take_layer_fence_on

        with self._engine.begin() as connection:
            take_layer_fence_on(connection=connection, deployment_id=deployment_id)
            connection.execute(_LOCK_CYCLE, {"cycle_id": cycle_id})
            still_ready = cycle_id in {
                row["cycle_id"]
                for row in connection.execute(
                    _SELECT_READY_CYCLES, {"deployment_id": deployment_id}
                ).mappings()
            }
            if not still_ready:
                return False
            return (
                connection.execute(
                    _CLAIM_FINALIZATION, {"cycle_id": cycle_id}
                ).scalar_one_or_none()
                is not None
            )


def _record_adjudication(
    *,
    connection: Connection,
    sql: object,
    deployment_id: UUID,
    fact_id: UUID,
    reconciliation_id: UUID,
    archive: bool,
    decided_at: datetime,
) -> None:
    """Append one retraction adjudication, once per (fact, reconciliation).

    ``decided_at`` is the closure's own instant (the fact's ``invalidated_at``),
    so the column and the event agree to the microsecond (D159).
    """
    features: dict[str, object] = {"reconciliation_id": str(reconciliation_id)}
    if archive:
        features["archive"] = True
    connection.execute(
        sql,  # type: ignore[arg-type]
        {
            "adjudication_id": uuid4(),
            "deployment_id": deployment_id,
            "fact_id": fact_id,
            "features": features,
            "reconciliation_id": str(reconciliation_id),
            "decided_at": decided_at,
        },
    )


def _live_carriage(*, claim: str) -> str:
    """SQL predicate: claim ``claim`` is carried by a live version of a live lineage.

    A lineage is live while it is neither archived nor hidden by forget, and a
    version while it is not removed.
    """
    return f"""
    EXISTS (
        SELECT 1
        FROM chunks lc
        JOIN document_versions lv
          ON lv.version_id = lc.version_id AND lv.deleted_at IS NULL
        JOIN documents ld
          ON ld.doc_id = lv.doc_id
         AND ld.deleted_at IS NULL AND ld.archived_at IS NULL
        WHERE lc.chunk_id = {claim}.chunk_id
           OR lc.chunk_id IN (SELECT lcc.chunk_id FROM chunk_claims lcc
                              WHERE lcc.claim_id = {claim}.claim_id)
    )
"""


LIVE_CARRIAGE_SQL = _live_carriage(claim="cl")
"""SQL predicate: claim ``cl`` is carried by a live version of a live lineage."""


REVIEW_MOOT_SQL = """EXISTS (
            SELECT 1 FROM claims mc
            JOIN documents md
              ON md.deployment_id = mc.deployment_id AND md.doc_id = mc.doc_id
            WHERE mc.deployment_id = q.deployment_id
              AND mc.claim_id = CAST(q.candidate ->> 'claim_id' AS uuid)
              AND md.archived_at IS NOT NULL
        )"""
"""SQL predicate: review ``q``'s claim belongs to an archived document (D159).

A support_withdrawn review asks whether an extractor was right to stop
deriving a claim from an unchanged file. While the document is archived the
question is moot: the review neither holds its fact open nor blocks a review
of live material. It is left as it is and applies again if the document comes
back (§2.1).
"""


def _review_holds(*, kind: str, fact: str, deployment: str) -> str:
    """SQL predicate: an open support_withdrawn review that is not moot holds the fact."""
    return f"""EXISTS (
          SELECT 1 FROM review_queue q
          WHERE q.deployment_id = {deployment}
            AND q.item_kind = 'support_withdrawn'
            AND q.status IN ('pending', 'deferred')
            AND q.candidate ->> 'fact_kind' = '{kind}'
            AND q.candidate ->> 'fact_id' = {fact}::text
            AND NOT {REVIEW_MOOT_SQL}
      )"""


def _live_review_holds(*, kind: str, fact: str, deployment: str) -> str:
    """SQL predicate: an open support_withdrawn review on a live-carried claim."""
    return f"""EXISTS (
          SELECT 1 FROM review_queue q
          JOIN claims rc
            ON rc.claim_id = CAST(q.candidate ->> 'claim_id' AS uuid)
          WHERE q.deployment_id = {deployment}
            AND q.item_kind = 'support_withdrawn'
            AND q.status IN ('pending', 'deferred')
            AND q.candidate ->> 'fact_kind' = '{kind}'
            AND q.candidate ->> 'fact_id' = {fact}::text
            AND {_live_carriage(claim="rc")}
      )"""


_SELECT_CONTEXT = text(
    """
    SELECT v.deployment_id, v.doc_id, v.version_id, v.version_no,
           v.sync_cycle_id, d.versioning_mode::text AS versioning_mode,
           d.current_version_id,
           d.deleted_at AS lineage_deleted_at,
           d.archived_at AS lineage_archived_at,
           v.deleted_at AS version_deleted_at,
           (SELECT cv.source_modified_at FROM document_versions cv
            WHERE cv.version_id = d.current_version_id) AS current_source_modified_at
    FROM document_versions v
    JOIN documents d ON d.doc_id = v.doc_id
    WHERE v.version_id = :version_id
    """
)

_SELECT_STALE_SUPERSEDED = text(
    """
    SELECT cl.claim_id, c.version_id AS from_version_id
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE cl.deployment_id = :deployment_id
      AND cl.doc_id = :doc_id
      AND cl.is_current_testimony
      AND c.version_id <> :current_version_id
      AND NOT EXISTS (SELECT 1 FROM chunk_claims cc
                      JOIN chunks cur ON cur.chunk_id = cc.chunk_id
                      WHERE cc.claim_id = cl.claim_id
                        AND cur.version_id = :current_version_id)
    """
)

_SELECT_STALE_REEXTRACTED = text(
    """
    SELECT cl.claim_id, cl.doc_id, cl.extractor_version AS from_extractor_version
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE c.version_id = :version_id
      AND cl.is_current_testimony
      AND (c.representation_id <> :representation_id
           OR c.chunker_version <> :chunker_version
           OR cl.extractor_version <> :extractor_version)
      AND NOT (
          -- carried into the completing basis by a same-generation reuse
          -- link: the claim IS part of the current transcription. An OLD
          -- generation's own links never count — reuse keys embed the
          -- extractor version, so re-attachment is same-generation only.
          cl.extractor_version = :extractor_version
          AND EXISTS (
              SELECT 1 FROM chunk_claims cc
              JOIN chunks cur ON cur.chunk_id = cc.chunk_id
              WHERE cc.claim_id = cl.claim_id
                AND cur.representation_id = :representation_id
                AND cur.chunker_version = :chunker_version
          )
      )
    """
)

_SELECT_STALE_VERSION_DELETED = text(
    """
    SELECT cl.claim_id, cl.doc_id
    FROM claims cl
    WHERE cl.deployment_id = :deployment_id
      AND cl.is_current_testimony
      AND EXISTS (SELECT 1 FROM chunks c
                  WHERE c.chunk_id = cl.chunk_id
                    AND c.version_id = :version_id
                  UNION ALL
                  SELECT 1 FROM chunk_claims cc
                  JOIN chunks oc ON oc.chunk_id = cc.chunk_id
                  WHERE cc.claim_id = cl.claim_id
                    AND oc.version_id = :version_id)
      AND NOT EXISTS (
          SELECT 1 FROM chunk_claims cc2
          JOIN chunks other ON other.chunk_id = cc2.chunk_id
          JOIN document_versions ov ON ov.version_id = other.version_id
          WHERE cc2.claim_id = cl.claim_id
            AND other.version_id <> :version_id
            AND ov.deleted_at IS NULL
          UNION ALL
          SELECT 1 FROM chunks origin
          JOIN document_versions ov2 ON ov2.version_id = origin.version_id
          WHERE origin.chunk_id = cl.chunk_id
            AND origin.version_id <> :version_id
            AND ov2.deleted_at IS NULL
      )
    """
)

_SELECT_REGAINED = text(
    """
    SELECT cl.claim_id, c.version_id AS from_version_id
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE cl.deployment_id = :deployment_id
      AND cl.doc_id = :doc_id
      AND NOT cl.is_current_testimony
      AND (c.version_id = :current_version_id
           OR EXISTS (SELECT 1 FROM chunk_claims cc
                      JOIN chunks cur ON cur.chunk_id = cc.chunk_id
                      WHERE cc.claim_id = cl.claim_id
                        AND cur.version_id = :current_version_id))
    """
)

_SELECT_REGAINED_SUPERSEDED = text(
    _SELECT_REGAINED.text
    + """
      AND (SELECT e.reason FROM testimony_currency_events e
           WHERE e.claim_id = cl.claim_id
           ORDER BY e.occurred_at DESC, e.event_id DESC
           LIMIT 1) = 'version_superseded'
    """
)
"""Only claims the head's supersession retired (never a re-extraction's)."""

_SELECT_RUN_EVENTS = text(
    """
    SELECT claim_id, doc_id, became_current, reason::text AS reason,
           from_extractor_version, from_version_id
    FROM testimony_currency_events
    WHERE reconciliation_id = :reconciliation_id
    """
)

_INSERT_CURRENCY_EVENTS = text(
    """
    WITH input AS (
        SELECT *
        FROM jsonb_to_recordset(CAST(:events AS jsonb)) AS event (
            event_id uuid,
            claim_id uuid,
            doc_id uuid,
            became_current boolean,
            reason text,
            from_extractor_version text,
            from_version_id uuid
        )
    ), inserted AS (
        INSERT INTO testimony_currency_events (
            event_id, deployment_id, claim_id, doc_id, reconciliation_id,
            became_current, reason, from_extractor_version, from_version_id,
            occurred_at
        )
        -- the clock, not the transaction start: currency writes take the
        -- claims' row locks first, so event order follows lock order
        SELECT input.event_id, :deployment_id, input.claim_id, input.doc_id,
               :reconciliation_id, input.became_current,
               CAST(input.reason AS currency_reason),
               input.from_extractor_version, input.from_version_id,
               clock_timestamp()
        FROM input
        WHERE NOT EXISTS (
            SELECT 1 FROM testimony_currency_events existing
            WHERE existing.claim_id = input.claim_id
              AND existing.reconciliation_id = :reconciliation_id
              AND existing.reason = CAST(input.reason AS currency_reason)
              AND existing.became_current = input.became_current
        )
        RETURNING event_id
    )
    SELECT count(*) FROM inserted
    """
).bindparams(bindparam("events", type_=JSON))

_UPDATE_CURRENCY_CACHES = text(
    """
    WITH input AS (
        SELECT *
        FROM jsonb_to_recordset(CAST(:claims AS jsonb)) AS claim (
            claim_id uuid,
            is_current boolean
        )
    )
    UPDATE claims
    SET is_current_testimony = input.is_current
    FROM input
    WHERE claims.claim_id = input.claim_id
      AND claims.is_current_testimony <> input.is_current
    """
).bindparams(bindparam("claims", type_=JSON))

_SELECT_AFFECTED_RELATIONS = text(
    """
    SELECT DISTINCT relation_id FROM relation_evidence
    WHERE claim_id = ANY(:claim_ids)
    """
)

_SELECT_AFFECTED_OBSERVATIONS = text(
    """
    SELECT DISTINCT observation_id FROM observation_evidence
    WHERE claim_id = ANY(:claim_ids)
    """
)

_RECOUNT_RELATIONS = text(
    """
    WITH fresh AS (
        SELECT requested.relation_id,
               count(DISTINCT evidence.doc_id) FILTER (
                   WHERE evidence.stance = 'supports'
                     AND claims.is_current_testimony
               ) AS supports,
               count(DISTINCT evidence.doc_id) FILTER (
                   WHERE evidence.stance = 'contradicts'
                     AND claims.is_current_testimony
               ) AS contradicts
        FROM unnest(CAST(:relation_ids AS uuid[])) AS requested(relation_id)
        LEFT JOIN relation_evidence evidence
          ON evidence.relation_id = requested.relation_id
        LEFT JOIN claims ON claims.claim_id = evidence.claim_id
        GROUP BY requested.relation_id
    )
    UPDATE relations r
    SET evidence_count = fresh.supports,
        contradict_count = fresh.contradicts,
        updated_at = now()
    FROM fresh
    WHERE r.relation_id = fresh.relation_id
      AND (r.evidence_count IS DISTINCT FROM fresh.supports
           OR r.contradict_count IS DISTINCT FROM fresh.contradicts)
    RETURNING r.relation_id
    """
)

_RECOUNT_OBSERVATIONS = text(
    """
    WITH fresh AS (
        SELECT requested.observation_id,
               count(DISTINCT evidence.doc_id) FILTER (
                   WHERE evidence.stance = 'supports'
                     AND claims.is_current_testimony
               ) AS supports,
               count(DISTINCT evidence.doc_id) FILTER (
                   WHERE evidence.stance = 'contradicts'
                     AND claims.is_current_testimony
               ) AS contradicts
        FROM unnest(CAST(:observation_ids AS uuid[]))
             AS requested(observation_id)
        LEFT JOIN observation_evidence evidence
          ON evidence.observation_id = requested.observation_id
        LEFT JOIN claims ON claims.claim_id = evidence.claim_id
        GROUP BY requested.observation_id
    )
    UPDATE observations o
    SET evidence_count = fresh.supports,
        contradict_count = fresh.contradicts,
        updated_at = now()
    FROM fresh
    WHERE o.observation_id = fresh.observation_id
      AND (o.evidence_count IS DISTINCT FROM fresh.supports
           OR o.contradict_count IS DISTINCT FROM fresh.contradicts)
    RETURNING o.observation_id
    """
)

_SELECT_ZERO_RELATIONS = text(
    f"""
    SELECT relation_id FROM relations
    WHERE deployment_id = :deployment_id
      AND relation_id = ANY(:relation_ids)
      AND evidence_count = 0
      AND invalidated_at IS NULL
      -- a fact under an open support_withdrawn review is the
      -- transcription-only branch: a reviewer decides, never mechanics
      AND NOT {_review_holds(kind="relation", fact="relations.relation_id", deployment="relations.deployment_id")}
    """
)

_SELECT_ZERO_OBSERVATIONS = text(
    f"""
    SELECT observation_id FROM observations
    WHERE deployment_id = :deployment_id
      AND observation_id = ANY(:observation_ids)
      AND evidence_count = 0
      AND invalidated_at IS NULL
      AND NOT {_review_holds(kind="observation", fact="observations.observation_id", deployment="observations.deployment_id")}
    """
)

_INVALIDATE_RELATION = text(
    """
    UPDATE relations
    SET invalidated_at = clock_timestamp(), updated_at=now()
    WHERE deployment_id=:deployment_id AND relation_id = :relation_id
      AND evidence_count=0 AND invalidated_at IS NULL
      -- a run closes a fact once: one its closure was reopened since is
      -- another run's to close (D159)
      AND NOT EXISTS (
          SELECT 1 FROM relation_adjudications a
          WHERE a.relation_id = relations.relation_id
            AND a.outcome = 'retracted_source_removal'
            AND a.features ->> 'reconciliation_id' = :reconciliation_id
      )
    RETURNING invalidated_at
    """
)

_INVALIDATE_OBSERVATION = text(
    """
    UPDATE observations
    SET invalidated_at = clock_timestamp()
    WHERE observation_id = :observation_id AND evidence_count=0 AND invalidated_at IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM observation_adjudications a
          WHERE a.observation_id = observations.observation_id
            AND a.outcome = 'retracted_source_removal'
            AND a.features ->> 'reconciliation_id' = :reconciliation_id
      )
    RETURNING invalidated_at
    """
)

_INSERT_RELATION_RETRACTION = text(
    """
    INSERT INTO relation_adjudications (
        adjudication_id, deployment_id, relation_id, outcome, method,
        features, adjudicator_version, decided_at
    )
    SELECT :adjudication_id, :deployment_id, :fact_id,
           'retracted_source_removal', 'exact', :features, 'reconcile-2026.07',
           :decided_at
    WHERE NOT EXISTS (
        SELECT 1 FROM relation_adjudications a
        WHERE a.relation_id = :fact_id
          AND a.outcome = 'retracted_source_removal'
          AND a.features ->> 'reconciliation_id' = :reconciliation_id
    )
    """
).bindparams(bindparam("features", type_=JSON))

_INSERT_OBSERVATION_RETRACTION = text(
    """
    INSERT INTO observation_adjudications (
        adjudication_id, deployment_id, observation_id, outcome, method,
        features, adjudicator_version, decided_at
    )
    SELECT :adjudication_id, :deployment_id, :fact_id,
           'retracted_source_removal', 'exact', :features, 'reconcile-2026.07',
           :decided_at
    WHERE NOT EXISTS (
        SELECT 1 FROM observation_adjudications a
        WHERE a.observation_id = :fact_id
          AND a.outcome = 'retracted_source_removal'
          AND a.features ->> 'reconciliation_id' = :reconciliation_id
    )
    """
).bindparams(bindparam("features", type_=JSON))

_INSERT_EVIDENCE_CHANGED = text(
    """
    INSERT INTO knowledge_refresh_queue (
        refresh_id, deployment_id, trigger, payload
    )
    SELECT :refresh_id, :deployment_id, 'evidence_changed', :payload
    WHERE NOT EXISTS (
        SELECT 1 FROM knowledge_refresh_queue q
        WHERE q.deployment_id = :deployment_id
          AND q.trigger = 'evidence_changed'
          AND q.payload ->> 'reconciliation_id' = :reconciliation_id
    )
    RETURNING refresh_id
    """
).bindparams(bindparam("payload", type_=JSON))

_SELECT_VERSION_LINEAGE = text(
    """
    SELECT deployment_id, doc_id FROM document_versions WHERE version_id = :version_id
    """
)

_TOMBSTONE_VERSION = text(
    """
    UPDATE document_versions SET deleted_at = coalesce(deleted_at, clock_timestamp())
    WHERE version_id = :version_id
    RETURNING deployment_id, doc_id, version_id
    """
)

_REPOINT_AFTER_VERSION_DELETE = text(
    """
    UPDATE documents d
    SET current_version_id = (
        SELECT v.version_id FROM document_versions v
        WHERE v.doc_id = d.doc_id
          AND v.deleted_at IS NULL
          AND v.status = 'ready'
        ORDER BY v.version_no DESC
        LIMIT 1
    )
    WHERE d.doc_id = :doc_id AND d.current_version_id = :version_id
    """
)

_TOMBSTONE_LINEAGE_BY_ID = text(
    """
    UPDATE documents SET deleted_at = now()
    WHERE doc_id = :doc_id AND deleted_at IS NULL
    """
)

_TOMBSTONE_LINEAGE_VERSIONS = text(
    """
    UPDATE document_versions SET deleted_at = now()
    WHERE doc_id = :doc_id AND deleted_at IS NULL
    """
)

_SELECT_LINEAGE_DELETED_AT = text(
    "SELECT deleted_at FROM documents WHERE doc_id = :doc_id"
)

_SELECT_STALE_UNCARRIED = text(
    f"""
    SELECT cl.claim_id, c.version_id AS from_version_id
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE cl.deployment_id = :deployment_id
      AND cl.doc_id = :doc_id
      AND cl.is_current_testimony
      AND NOT {LIVE_CARRIAGE_SQL}
    """
)

_AUTO_RESOLVE_DELETED_REVIEWS = text(
    f"""
    UPDATE review_queue q
    SET status = 'auto_resolved',
        verdict_note = 'the source material was removed',
        resolved_at = now(),
        candidate = jsonb_set(
            q.candidate,
            '{{verdict_history}}',
            coalesce(q.candidate -> 'verdict_history', '[]'::jsonb)
                || jsonb_build_array(jsonb_build_object(
                    'verdict', NULL, 'reviewer', 'deletion',
                    'note', 'the source material was removed')),
            true
        )
    FROM claims cl
    WHERE q.deployment_id = :deployment_id
      AND q.item_kind = 'support_withdrawn'
      AND q.status IN ('pending', 'deferred')
      AND q.candidate ->> 'claim_id' = ANY(CAST(:claim_ids AS text[]))
      AND cl.deployment_id = q.deployment_id
      AND cl.claim_id = CAST(q.candidate ->> 'claim_id' AS uuid)
      AND NOT {LIVE_CARRIAGE_SQL}
    RETURNING q.review_id
    """
)

_SET_ARCHIVED = text(
    """
    WITH prior AS (
        SELECT archived_at AS was FROM documents WHERE doc_id = :doc_id FOR UPDATE
    )
    UPDATE documents d
    -- the clock, not the transaction start: taken under the lineage lock, it
    -- orders this archive after an unarchive that held the lock first
    SET archived_at = coalesce(d.archived_at, clock_timestamp()),
        archived_by = CASE
            WHEN d.archived_at IS NULL OR :archived_by = 'caller' THEN :archived_by
            ELSE d.archived_by
        END,
        archived_cycle_id = CASE
            WHEN :archived_by = 'caller' THEN NULL
            WHEN d.archived_at IS NULL THEN CAST(:cycle_id AS uuid)
            ELSE d.archived_cycle_id
        END
    FROM prior
    WHERE d.doc_id = :doc_id
    RETURNING d.deployment_id, d.archived_at, prior.was IS NULL AS newly_archived
    """
)

_CLEAR_ARCHIVED = text(
    """
    UPDATE documents SET archived_at = NULL, archived_by = NULL, archived_cycle_id = NULL
    WHERE doc_id = :doc_id
    """
)

_LATEST_EVENT = """
    JOIN LATERAL (
        SELECT e.reconciliation_id, e.reason, e.became_current, e.occurred_at
        FROM testimony_currency_events e
        WHERE e.claim_id = cl.claim_id
        ORDER BY e.occurred_at DESC, e.event_id DESC
        LIMIT 1
    ) last ON true
"""

_SELECT_ARCHIVED_TESTIMONY = text(
    f"""
    SELECT cl.claim_id, c.version_id AS from_version_id
    FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    {_LATEST_EVENT}
    WHERE cl.deployment_id = :deployment_id
      AND cl.doc_id = :doc_id
      AND NOT cl.is_current_testimony
      AND last.reconciliation_id = :reconciliation_id
      AND NOT last.became_current
      AND {LIVE_CARRIAGE_SQL}
    """
)

_SELECT_REGAINED_AFTER_ARCHIVE = text(
    f"""
    WITH carried AS (
        SELECT cc.claim_id
        FROM chunk_claims cc
        JOIN chunks c ON c.chunk_id = cc.chunk_id
        WHERE c.deployment_id = :deployment_id AND c.version_id = :version_id
        UNION
        SELECT origin.claim_id
        FROM claims origin
        JOIN chunks c ON c.chunk_id = origin.chunk_id
        WHERE c.deployment_id = :deployment_id AND c.version_id = :version_id
    )
    SELECT cl.claim_id, cl.doc_id
    FROM carried
    JOIN claims cl ON cl.claim_id = carried.claim_id
    JOIN document_versions arrived ON arrived.version_id = :version_id
    {_LATEST_EVENT}
    WHERE cl.deployment_id = :deployment_id
      AND NOT cl.is_current_testimony
      AND last.reason = 'version_archived'
      AND NOT last.became_current
      -- only a version that arrived after the archive brings testimony back;
      -- an archived version's own late reconcile never does
      AND arrived.ingested_at > last.occurred_at
      AND {LIVE_CARRIAGE_SQL}
    """
)


def _reopen_sql(
    *, kind: str, table: str, adjudications: str
) -> tuple[TextClause, TextClause, TextClause, TextClause]:
    """The lock, select, reopen and record statements for one fact shape."""
    select = text(
        f"""
        SELECT f.{kind}_id FROM {table} f
        WHERE f.deployment_id = :deployment_id
          AND f.{kind}_id = ANY(CAST(:fact_ids AS uuid[]))
          AND f.invalidated_at IS NOT NULL
          AND (f.evidence_count > 0
               OR {_live_review_holds(kind=kind, fact=f"f.{kind}_id", deployment="f.deployment_id")})
          AND coalesce((
              SELECT a.outcome = 'retracted_source_removal'
                     AND coalesce((a.features ->> 'archive')::boolean, false)
              FROM {adjudications} a
              WHERE a.{kind}_id = f.{kind}_id
                AND a.outcome IN (
                    'retracted_source_removal', 'invalidated', 'reopened_unarchive'
                )
              ORDER BY a.decided_at DESC, a.adjudication_id DESC
              LIMIT 1
          ), false)
        ORDER BY f.{kind}_id
        """
    )
    lock = text(
        f"""
        SELECT f.{kind}_id FROM {table} f
        WHERE f.{kind}_id = ANY(CAST(:fact_ids AS uuid[]))
          AND f.invalidated_at IS NOT NULL
        ORDER BY f.{kind}_id
        FOR UPDATE OF f
        """
    )
    reopen = text(
        f"""
        UPDATE {table} SET invalidated_at = NULL, updated_at = now()
        WHERE {kind}_id = :fact_id AND invalidated_at IS NOT NULL
        """
    )
    record = text(
        f"""
        INSERT INTO {adjudications} (
            adjudication_id, deployment_id, {kind}_id, outcome, method,
            features, adjudicator_version, decided_at
        ) VALUES (
            :adjudication_id, :deployment_id, :fact_id, 'reopened_unarchive',
            'exact', :features, 'reconcile-2026.07', clock_timestamp()
        )
        """
    ).bindparams(bindparam("features", type_=JSON))
    return lock, select, reopen, record


_REOPEN_SQL = {
    "relation": _reopen_sql(
        kind="relation", table="relations", adjudications="relation_adjudications"
    ),
    "observation": _reopen_sql(
        kind="observation",
        table="observations",
        adjudications="observation_adjudications",
    ),
}

_REOPEN_CUT_SHORT_WORK = text(
    """
    WITH reopened AS (
        UPDATE processing_state p
        SET status = 'pending',
            defer_reason = NULL,
            not_before = now(),
            max_attempts = greatest(p.max_attempts, p.attempts + 3),
            finished_at = NULL
        WHERE (
              (p.target_kind = 'document_version' AND p.target_id IN (
                  SELECT v.version_id FROM document_versions v
                  WHERE v.doc_id = :doc_id AND v.deleted_at IS NULL))
              OR (p.target_kind = 'chunk' AND p.target_id IN (
                  SELECT c.chunk_id FROM chunks c
                  JOIN document_versions v ON v.version_id = c.version_id
                  WHERE v.doc_id = :doc_id AND v.deleted_at IS NULL))
              OR (p.target_kind = 'claim' AND p.target_id IN (
                  SELECT cl.claim_id FROM claims cl WHERE cl.doc_id = :doc_id))
          )
          AND (
              (p.stage = 'reconcile' AND (
                  p.status IN ('running', 'dead_letter')
                  OR (p.status = 'succeeded' AND p.finished_at >= :archived_at)))
              OR (p.status = 'dead_letter' AND p.finished_at >= :archived_at)
          )
        RETURNING p.processing_id
    )
    SELECT pg_notify('queue_wake', CAST(processing_id AS text)) FROM reopened
    """
)

_SELECT_LIVE_BINDING_DECISIONS = text(
    f"""
    SELECT decision.decision_id, decision.decided_at, mention.doc_id,
           decision.entity_id, decision.method::text AS method,
           decision.is_new_entity, decision.features
    FROM resolution_decisions decision
    JOIN mentions mention
      ON mention.deployment_id = decision.deployment_id
     AND mention.mention_id = decision.mention_id
    JOIN claims cl
      ON cl.deployment_id = mention.deployment_id
     AND cl.claim_id = mention.claim_id
    WHERE decision.deployment_id = :deployment_id
      AND mention.doc_id = :doc_id
      AND decision.features -> 'document_t0' ->> 'contract' = 'document-t0-v1'
      AND {LIVE_CARRIAGE_SQL}
    ORDER BY decision.decided_at, decision.decision_id
    """
)

_INSERT_BINDING = text(
    """
    INSERT INTO document_entity_bindings (
        deployment_id, doc_id, canonical_lemma, entity_id,
        anchor_decision_id, anchor_decided_at
    ) VALUES (
        :deployment_id, :doc_id, :canonical_lemma, :entity_id,
        :anchor_decision_id, :anchor_decided_at
    )
    """
).bindparams(bindparam("anchor_decision_id"), bindparam("anchor_decided_at"))

_SELECT_LINEAGE_STATE = text(
    """
    SELECT archived_at, archived_by, deleted_at
    FROM documents
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_CLEAR_DOCUMENT_BINDINGS = text(
    """
    DELETE FROM document_entity_bindings
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_CLEAR_DOCUMENT_BINDINGS_BY_DOC = text(
    "DELETE FROM document_entity_bindings WHERE doc_id = :doc_id"
)

_SELECT_READY_CYCLES = text(
    """
    SELECT y.cycle_id, y.failed_items
    FROM connector_sync_cycles y
    WHERE y.deployment_id = :deployment_id
      AND y.completed_at IS NOT NULL
      AND y.finalized_at IS NULL
      AND NOT EXISTS (
          SELECT 1
          FROM document_versions v
          JOIN processing_state w
            ON w.target_id = v.version_id
           AND w.target_kind = 'document_version'
          WHERE v.sync_cycle_id = y.cycle_id
            AND w.status IN ('pending', 'running', 'failed')
            -- D117: live no-route work still lacks conversion evidence and
            -- holds the retraction barrier; so does work parked for the
            -- meter's approval (D150). Archived or forgotten sources no
            -- longer participate, and must not strand their cycle forever.
            AND (
                w.defer_reason IS NULL
                OR w.defer_reason::text NOT IN ('no_route', 'admission')
                OR (
                    v.deleted_at IS NULL
                    AND EXISTS (
                        SELECT 1 FROM documents d
                        WHERE d.deployment_id = v.deployment_id
                          AND d.doc_id = v.doc_id AND d.deleted_at IS NULL
                          AND d.archived_at IS NULL
                    )
                )
            )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM document_versions v
          JOIN chunks c ON c.version_id = v.version_id
          JOIN processing_state w
            ON w.deployment_id = y.deployment_id
           AND w.target_kind = 'chunk'
           AND w.target_id = c.chunk_id
           AND w.stage IN ('extract_claims', 'ground_claims')
          WHERE v.sync_cycle_id = y.cycle_id
            AND w.status IN ('pending', 'running', 'failed', 'dead_letter')
      )
      AND NOT EXISTS (
          -- D88: when claim-grain normalize rows exist, they must finish
          -- (incl. dead_letter) before cycle finalization. Missing rows are
          -- intentional for pre-fanout serial normalize (legacy drain) — do
          -- not treat absence as incomplete. D56: discover claims via
          -- chunk_claims occurrence map, not origin claims.chunk_id.
          SELECT 1
          FROM document_versions v
          JOIN chunks c ON c.version_id = v.version_id
          JOIN chunk_claims cc ON cc.chunk_id = c.chunk_id
          JOIN claims cl ON cl.claim_id = cc.claim_id
          JOIN processing_state w
            ON w.deployment_id = y.deployment_id
           AND w.target_kind = 'claim'
           AND w.target_id = cl.claim_id
           AND w.stage = 'normalize_relations'
          WHERE v.sync_cycle_id = y.cycle_id
            AND w.status IN ('pending', 'running', 'failed', 'dead_letter')
      )
      AND NOT EXISTS (
          -- D90: entity-grain obs flush units must reach succeeded before
          -- cycle finalization. Missing processing rows are non-terminal
          -- (membership is the expected set). Pin the fan-out generation.
          SELECT 1
          FROM document_versions v
          JOIN obs_flush_entity_units u
            ON u.deployment_id = y.deployment_id
           AND u.version_id = v.version_id
          LEFT JOIN processing_state w
            ON w.deployment_id = u.deployment_id
           AND w.target_kind = 'entity'
           AND w.target_id = u.unit_id
           AND w.stage = 'adjudicate_observations'
           -- Match D90 fanout generation without using SQLAlchemy bind syntax.
           AND w.component_version LIKE '%entity-fanout%'
          WHERE v.sync_cycle_id = y.cycle_id
            AND (w.status IS NULL OR w.status <> 'succeeded')
      )
      AND NOT EXISTS (
          -- D143: a live version waiting to enter an enabled layer has work
          -- still to come (no row exists yet), and one inside a layer holds
          -- until that layer's frontier (a claims tail may still be owed); an
          -- unentered gap of a disabled layer never holds.
          SELECT 1
          FROM document_versions v
          JOIN documents d ON d.doc_id = v.doc_id
          JOIN processing_layer_gaps g
            ON g.deployment_id = y.deployment_id AND g.version_id = v.version_id
          LEFT JOIN deployment_processing_layers l
            ON l.deployment_id = y.deployment_id
          WHERE v.sync_cycle_id = y.cycle_id
            AND v.deleted_at IS NULL AND d.deleted_at IS NULL
            AND d.archived_at IS NULL
            AND g.discharged_at IS NULL
            AND (
                g.entered_at IS NOT NULL
                OR CASE g.layer
                     WHEN 'claims' THEN coalesce(l.claims_enabled, true)
                     ELSE coalesce(l.facts_enabled, true)
                   END
            )
      )
    ORDER BY y.started_at
    """
)

_SELECT_CYCLE_LINEAGES = text(
    """
    SELECT DISTINCT doc_id FROM document_versions
    WHERE sync_cycle_id = :cycle_id
    """
)


def archive_cycle_open(*, doc: str) -> str:
    """SQL predicate: lineage ``doc``'s source archive waits for its cycle.

    A source archive settles once its observing cycle is finalized (the
    retract-timing barrier). A poll that died before completing never
    finalizes; a later finalized poll of the same source settles it then. A
    completed poll always settles on its own finalization.
    """
    return f"""EXISTS (
          SELECT 1 FROM connector_sync_cycles y
          WHERE y.cycle_id = {doc}.archived_cycle_id AND y.finalized_at IS NULL
            AND (y.completed_at IS NOT NULL OR NOT EXISTS (
                SELECT 1 FROM connector_sync_cycles later
                WHERE later.deployment_id = y.deployment_id
                  AND later.source_kind = y.source_kind
                  AND later.started_at > y.started_at
                  AND later.finalized_at IS NOT NULL
            ))
      )"""


_SELECT_PENDING_ARCHIVES = text(
    f"""
    SELECT d.doc_id, d.archived_at
    FROM documents d
    WHERE d.deployment_id = :deployment_id
      AND d.archived_at IS NOT NULL
      AND d.deleted_at IS NULL
      -- a source archive waits for its observing cycle's finalization (the
      -- retract-timing barrier): a move resolves as a support swap
      AND NOT {archive_cycle_open(doc="d")}
      AND EXISTS (
          SELECT 1 FROM claims cl
          WHERE cl.deployment_id = d.deployment_id
            AND cl.doc_id = d.doc_id
            AND (
                cl.is_current_testimony
                OR EXISTS (
                    SELECT 1 FROM relation_evidence e
                    JOIN relations r ON r.relation_id = e.relation_id
                    WHERE e.claim_id = cl.claim_id
                      AND r.invalidated_at IS NULL AND r.evidence_count = 0
                      AND NOT {_review_holds(kind="relation", fact="r.relation_id", deployment="r.deployment_id")}
                )
                OR EXISTS (
                    SELECT 1 FROM observation_evidence e
                    JOIN observations o ON o.observation_id = e.observation_id
                    WHERE e.claim_id = cl.claim_id
                      AND o.invalidated_at IS NULL AND o.evidence_count = 0
                      AND NOT {_review_holds(kind="observation", fact="o.observation_id", deployment="o.deployment_id")}
                )
            )
      )
    ORDER BY d.doc_id
    """
)

_SELECT_VERSION_CLAIMS = text(
    """
    SELECT cl.claim_id FROM claims cl
    JOIN chunks c ON c.chunk_id = cl.chunk_id
    WHERE cl.deployment_id = :deployment_id AND c.version_id = :version_id
    UNION
    SELECT cc.claim_id FROM chunk_claims cc
    JOIN chunks c ON c.chunk_id = cc.chunk_id
    WHERE c.deployment_id = :deployment_id AND c.version_id = :version_id
    """
)

_SELECT_LINEAGE_CLAIMS = text(
    """
    SELECT claim_id FROM claims
    WHERE deployment_id = :deployment_id AND doc_id = :doc_id
    """
)

_SELECT_CLOSURE_BOUNDARY = text(
    """
    SELECT v.source_modified_at
    FROM documents d
    JOIN document_versions v ON v.version_id = d.current_version_id
    WHERE d.doc_id = :doc_id
    """
)

_LOCK_CYCLE = text(
    "SELECT cycle_id FROM connector_sync_cycles WHERE cycle_id = :cycle_id FOR UPDATE"
)

_CLAIM_FINALIZATION = text(
    """
    UPDATE connector_sync_cycles SET finalized_at = now()
    WHERE cycle_id = :cycle_id AND finalized_at IS NULL
    RETURNING cycle_id
    """
)


def _request_review_on(
    *, connection: Connection, deployment_id: UUID, doc_id: UUID, reason: str
) -> None:
    """Queue a D140 §8.2 window review in the caller's transaction (if periodised)."""
    from rememberstack.spine.period_adjudication import request_window_review_on

    request_window_review_on(
        connection=connection, deployment_id=deployment_id, doc_id=doc_id, reason=reason
    )
