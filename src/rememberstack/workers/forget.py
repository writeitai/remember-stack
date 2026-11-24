"""D159 forget: hide at once, then sweep until clean.

``ForgetService.request`` hides the document in one transaction under the
lineage row lock: the lineage cascade, the tombstone, the record and the first
sweep's work row. ``ForgetHandler`` runs one sweep per work row (PostgreSQL,
objects, corpus, K) and schedules the next one after a quiet period, until a
sweep after the quiet period finds nothing left. ``ForgetService.reapply`` re-runs the
sweeps of every record after an operator restores a store from a backup.
"""

from dataclasses import dataclass
import logging
from typing import Protocol
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.model import ClaimedWork
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import EnqueueWork
from rememberstack.model import Forget
from rememberstack.model import ForgetStatus
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.ports import KGitPurgePort
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ProjectionPurgePort
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.profile_refresher import ProfileRefresherPort
from rememberstack.spine.forget import ForgetCatalog
from rememberstack.spine.forget import retry_delay
from rememberstack.spine.forget import sweep_work
from rememberstack.spine.forget import utc_now
from rememberstack.spine.work_ledger import enqueue_on
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.knowledge_driver import KnowledgeCommitDriver
from rememberstack.workers.reconcile import remove_lineage_on

_logger = logging.getLogger(__name__)

DEFAULT_QUIET_PERIOD_S = 900.0
"""Starting value (15 minutes, to be measured): longer than a D145 work lease
plus the bounded object request time, so a worker that lost its lease at the
hide has stopped writing before the sweep that may complete the forget."""


class ForgetKnowledgeRebuilder(Protocol):
    """Recompile compiled K pages after their citations to a document went."""

    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """Publish sanitized current compiled files before history erasure."""
        ...


class KnowledgeCycleForgetRebuilder:
    """Compile the affected stale pages through the existing single K driver."""

    def __init__(self, *, driver: KnowledgeCommitDriver) -> None:
        """Bind the production commit cycle after PostgreSQL marks pages stale."""
        self._driver = driver

    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        """Run one K commit only when the sweep names affected artifacts."""
        if artifact_ids:
            self._driver.run_cycle(
                deployment_id=deployment_id, exclusions_by_artifact={}
            )


class ForgetService:
    """The hide: one transaction under the lineage row lock (D159 §3.2)."""

    def __init__(self, *, engine: Engine, catalog: ForgetCatalog) -> None:
        """Bind the spine database and the record catalog."""
        self._engine = engine
        self._catalog = catalog

    def request(
        self, *, deployment_id: UUID, doc_id: UUID, forget_id: UUID | None = None
    ) -> tuple[Forget, bool]:
        """Hide a document and start its sweeps; a repeat returns the same record.

        Returns the record and whether this call created it. An unknown
        document is ``DocumentNotFoundError``. From the commit the document,
        its versions, passages and testimony are gone from every engine read,
        and its work can no longer be claimed or renew a lease.
        """
        with self._engine.begin() as connection:
            existing = self._catalog.record_for_doc_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            if existing is not None:
                return existing, False
            removal = remove_lineage_on(
                connection=connection, deployment_id=deployment_id, doc_id=doc_id
            )
            if removal is None:
                raise DocumentNotFoundError(str(doc_id))
            forget, created = self._catalog.insert_on(
                connection=connection,
                deployment_id=deployment_id,
                doc_id=doc_id,
                forget_id=forget_id or uuid4(),
            )
            if created:
                enqueue_on(
                    connection=connection,
                    work=sweep_work(
                        deployment_id=deployment_id,
                        forget_id=forget.forget_id,
                        sweep=1,
                        failures=0,
                        not_before=None,
                        first=True,
                    ),
                )
            return forget, created

    def rehide(self, *, forget: Forget) -> None:
        """Hide again (finish the cascade) when a restored database holds the lineage."""
        with self._engine.begin() as connection:
            remove_lineage_on(
                connection=connection,
                deployment_id=forget.deployment_id,
                doc_id=forget.doc_id,
            )

    def reapply(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """``remember ops forget-reapply``: re-run every record's sweeps (§3.8).

        For each record: hide the document again if a restored database holds
        it, mark the record ``erasing`` and enqueue a new series of sweeps.
        Ordinary workers run them; nothing waits for them.
        """
        reapplied: list[UUID] = []
        for forget in self._catalog.records(deployment_id=deployment_id):
            with self._engine.begin() as connection:
                remove_lineage_on(
                    connection=connection,
                    deployment_id=deployment_id,
                    doc_id=forget.doc_id,
                )
                self._catalog.rearm_on(connection=connection, forget=forget)
                enqueue_on(
                    connection=connection,
                    work=sweep_work(
                        deployment_id=deployment_id,
                        forget_id=forget.forget_id,
                        sweep=_next_sweep_on(connection=connection, forget=forget),
                        failures=0,
                        not_before=None,
                        first=True,
                        reapply=True,
                    ),
                )
            reapplied.append(forget.forget_id)
        return tuple(reapplied)


@dataclass(frozen=True)
class _SweepPayload:
    forget_id: UUID
    sweep: int
    failures: int
    first: bool
    reapply: bool


class ForgetHandler:
    """Run one sweep; schedule the next until a sweep finds nothing (D159 §3.4).

    The work item has no attempt cap: a failed sweep records a content-free
    error code on the record and schedules the next sweep with backoff; a
    sweep whose worker died and used its attempts does the same through the
    terminal-failure finalizer. The document stays hidden throughout.
    """

    def __init__(
        self,
        *,
        service: ForgetService,
        catalog: ForgetCatalog,
        profile_refresher: ProfileRefresherPort,
        object_purgers: tuple[ObjectPurgePort, ...],
        projection_purger: ProjectionPurgePort,
        knowledge_rebuilder: ForgetKnowledgeRebuilder,
        k_git: KGitPurgePort,
        quiet_period_s: float = DEFAULT_QUIET_PERIOD_S,
    ) -> None:
        """Bind the record catalog and the selected stores' erasure capabilities."""
        if not object_purgers:
            raise ValueError("forget requires at least one object-store purger")
        if quiet_period_s < 0:
            raise ValueError("the forget quiet period cannot be negative")
        self._service = service
        self._catalog = catalog
        self._profile_refresher = profile_refresher
        self._object_purgers = object_purgers
        self._projection_purger = projection_purger
        self._knowledge_rebuilder = knowledge_rebuilder
        self._k_git = k_git
        self._quiet_period_s = quiet_period_s

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Sweep once and return the next sweep as a follow-up, unless clean."""
        payload = _payload(work=work)
        forget = self._catalog.record(
            deployment_id=work.deployment_id, forget_id=payload.forget_id
        )
        try:
            found = self.sweep(forget=forget, payload=payload, meter=meter)
        except Exception as error:  # noqa: BLE001 — retried with backoff, never capped
            _logger.warning(
                "forget.sweep_failed forget_id=%s sweep=%s",
                forget.forget_id,
                payload.sweep,
                exc_info=True,
            )
            self._catalog.record_error(forget=forget, code=type(error).__name__)
            return HandlerOutcome(
                follow_up=(
                    self._retry(deployment_id=work.deployment_id, payload=payload),
                )
            )
        if found or payload.first:
            return HandlerOutcome(
                follow_up=(
                    self._next(deployment_id=work.deployment_id, payload=payload),
                )
            )
        blocking = self._catalog.still_blocking(forget=forget)
        if not blocking:
            authored = self._catalog.authored_artifact_ids(forget=forget)
            if authored:
                # The owner's edit removed the citation: erase the path's history.
                self._k_git.purge_artifacts(
                    deployment_id=forget.deployment_id,
                    forget_id=forget.forget_id,
                    artifact_ids=authored,
                )
        status = self._catalog.finish(forget=forget, blocking_k_paths=blocking)
        if status is ForgetStatus.WAITING_FOR_REDACTION:
            # The one wait: authored pages. Look again after a quiet period.
            return HandlerOutcome(
                follow_up=(
                    self._next(deployment_id=work.deployment_id, payload=payload),
                )
            )
        return HandlerOutcome()

    def finalize_terminal_failure(self, *, work: ClaimedWork, error: str) -> None:
        """A sweep that used its attempts (a crashed worker) is rescheduled."""
        payload = _payload(work=work)
        forget = self._catalog.record(
            deployment_id=work.deployment_id, forget_id=payload.forget_id
        )
        self._catalog.record_error(forget=forget, code="SweepAttemptsExhausted")
        self._catalog.schedule(
            work=self._retry(deployment_id=work.deployment_id, payload=payload)
        )

    def sweep(
        self, *, forget: Forget, payload: _SweepPayload, meter: CostMeterPort | None
    ) -> bool:
        """One sweep (D159 §3.3); whether it removed any forgotten residue."""
        # A restored database, or an upgraded D74 forget, still holds the
        # lineage: hide it again (finish its cascade) before sweeping.
        self._service.rehide(forget=forget)
        findings = self._catalog.sweep_postgres(forget=forget)
        found = findings.removed
        if findings.profile_entity_ids:
            try:
                self._profile_refresher.refresh_many(
                    deployment_id=forget.deployment_id,
                    entity_ids=findings.profile_entity_ids,
                    meter=meter,
                    call_key=f"profile:forget:{forget.forget_id}:{payload.sweep}",
                )
            except Exception:  # noqa: BLE001 — cleared profiles stay cleared
                _logger.warning(
                    "forget.profile_refresh_failed forget_id=%s",
                    forget.forget_id,
                    exc_info=True,
                )
        keys, prefixes = self._object_targets(forget=forget, keys=findings.object_keys)
        for purger in self._object_purgers:
            found |= purger.purge_objects(keys=keys, prefixes=prefixes) > 0
        found |= self._projection_purger.erase_document(
            deployment_id=forget.deployment_id, doc_id=forget.doc_id
        )
        compiled = findings.compiled_artifact_ids
        if payload.reapply and payload.first:
            # A restored K repository may hold history the record names.
            compiled = self._catalog.collected_artifact_ids(forget=forget)
        if compiled:
            self._knowledge_rebuilder.recompile_without_lineage(
                deployment_id=forget.deployment_id, artifact_ids=compiled
            )
            self._k_git.purge_artifacts(
                deployment_id=forget.deployment_id,
                forget_id=forget.forget_id,
                artifact_ids=compiled,
            )
        return found

    def _object_targets(
        self, *, forget: Forget, keys: tuple[ObjectKey, ...]
    ) -> tuple[tuple[ObjectKey, ...], tuple[ObjectKey, ...]]:
        """Every object under the document's prefix, except live shared originals.

        A raw original another lineage still uses keeps the key it was first
        stored under, which can be under this document's prefix; it is never
        touched. Its conversion checkpoints are still this document's.
        """
        live = self._catalog.live_originals(forget=forget)
        targets = tuple(key for key in keys if key.root not in live)
        prefix = f"{forget.doc_id}/"
        if not any(key.startswith(prefix) for key in live):
            return targets, (ObjectKey(prefix),)
        prefixes: list[ObjectKey] = []
        for content_hash in forget.content_hashes:
            base = f"{forget.doc_id}/{content_hash}/"
            if any(key.startswith(base) for key in live):
                prefixes.append(ObjectKey(base + "conversion-checkpoints"))
            else:
                prefixes.append(ObjectKey(base))
        return targets, tuple(prefixes)

    def _next(self, *, deployment_id: UUID, payload: _SweepPayload) -> EnqueueWork:
        return sweep_work(
            deployment_id=deployment_id,
            forget_id=payload.forget_id,
            sweep=payload.sweep + 1,
            failures=0,
            not_before=utc_now()
            + retry_delay(quiet_period_s=self._quiet_period_s, failures=0),
            reapply=payload.reapply,
        )

    def _retry(self, *, deployment_id: UUID, payload: _SweepPayload) -> EnqueueWork:
        failures = payload.failures + 1
        return sweep_work(
            deployment_id=deployment_id,
            forget_id=payload.forget_id,
            sweep=payload.sweep + 1,
            failures=failures,
            not_before=utc_now()
            + retry_delay(quiet_period_s=self._quiet_period_s, failures=failures),
            first=payload.first,
            reapply=payload.reapply,
        )


def _next_sweep_on(*, connection: Connection, forget: Forget) -> int:
    """One more than the highest sweep ordinal this record's work rows used."""
    highest = connection.execute(
        text("""SELECT max(CAST(payload->>'sweep' AS integer)) FROM processing_state
        WHERE deployment_id = :d AND target_kind = 'forget' AND target_id = :f"""),
        {"d": forget.deployment_id, "f": forget.forget_id},
    ).scalar_one_or_none()
    return int(highest or 0) + 1


def _payload(*, work: ClaimedWork) -> _SweepPayload:
    """Read the sweep's identity from its work row."""
    payload = work.payload or {}
    try:
        return _SweepPayload(
            forget_id=UUID(str(payload["forget_id"])),
            sweep=int(str(payload["sweep"])),
            failures=int(str(payload.get("failures", 0))),
            first=bool(payload.get("first", False)),
            reapply=bool(payload.get("reapply", False)),
        )
    except (KeyError, ValueError) as error:
        raise NonRetryableHandlerError(
            f"forget work {work.processing_id} has an invalid payload"
        ) from error
