"""D159 forget: hide at once, then sweep until clean.

``ForgetService.request`` hides the document in one transaction under the
lineage row lock: the lineage cascade, the tombstone, the record and the first
sweep's work row. ``ForgetHandler`` runs one sweep per work row (PostgreSQL,
objects, corpus, K) and schedules the next one after a quiet period, until a
sweep after the quiet period finds nothing left. ``ForgetService.reapply`` re-runs the
sweeps of every record after an operator restores a store from a backup.
``ForgetKnowledgeEraser`` is the K step, and the K sync's check that finishes a
record waiting for an owner to redact authored pages.
"""

from dataclasses import dataclass
import logging
from pathlib import Path
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID
from uuid import uuid4

from sqlalchemy.engine import Engine

from rememberstack.core import cites_forgotten
from rememberstack.model import ClaimedWork
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import EnqueueWork
from rememberstack.model import Forget
from rememberstack.model import ForgetStatus
from rememberstack.model import KnowledgeErasure
from rememberstack.model import KnowledgePageKind
from rememberstack.model import NonRetryableHandlerError
from rememberstack.model import ObjectKey
from rememberstack.ports import KGitPurgePort
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ProjectionPurgePort
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.profile_refresher import ProfileRefresherPort
from rememberstack.spine.forget import ForgetCatalog
from rememberstack.spine.forget import locator_hash
from rememberstack.spine.forget import retry_delay
from rememberstack.spine.forget import sweep_work
from rememberstack.spine.forget import utc_now
from rememberstack.spine.knowledge import KnowledgeControlPlane
from rememberstack.spine.work_ledger import enqueue_on
from rememberstack.workers.base import HandlerOutcome
from rememberstack.workers.reconcile import remove_lineage_on

_logger = logging.getLogger(__name__)

DEFAULT_QUIET_PERIOD_S = 900.0
"""Starting value (15 minutes, to be measured): longer than a D145 work lease
plus the bounded object request time, so a worker that lost its lease at the
hide has stopped writing before the sweep that may complete the forget."""


class ForgetKnowledge(Protocol):
    """The K step of a sweep (D159 §3.3 step 4) over the composed K store."""

    def erase(
        self, *, forget: Forget, drop_artifact_ids: tuple[UUID, ...], first: bool
    ) -> KnowledgeErasure:
        """Delete these compiled pages, rewrite history, report what still cites.

        ``first`` (the first sweep of a series) also drops every compiled page
        whose generated band references a fact the document touched, shared
        ones included: its wording may have come from the document.
        """
        ...

    def flag(self, *, forget: Forget, paths: tuple[str, ...]) -> None:
        """Ask the owners of these authored pages to redact them."""
        ...


class ForgetKnowledgeEraser:
    """The K step and the authored-page wait over one K repository.

    Compiled pages are a snapshot without history, so the step deletes the
    pending ones and ordinary K work rebuilds them. History is rewritten only
    for authored pages and sidecars whose current file no longer names the
    document; those that still do block the record until their owner edits
    them. Work is prepared outside the K lease, which is held only to publish.
    """

    def __init__(
        self,
        *,
        catalog: ForgetCatalog,
        control_plane: KnowledgeControlPlane,
        repository: KGitPurgePort,
    ) -> None:
        """Bind the records, the K control plane (lease, flags) and the repository."""
        self._catalog = catalog
        self._control_plane = control_plane
        self._repository = repository

    def erase(
        self, *, forget: Forget, drop_artifact_ids: tuple[UUID, ...], first: bool
    ) -> KnowledgeErasure:
        """Drop the pending compiled pages and rewrite history that names the document.

        Later sweeps drop only pages naming IDs that no longer exist, so a
        rebuilt page rendering a surviving shared fact is never dropped again.
        """
        deployment_id = forget.deployment_id
        needles = self._catalog.needles(forget=forget)
        return self._repository.erase(
            needles=needles,
            page_needles=needles
            if first
            else self._catalog.page_needles(forget=forget),
            compiled=compiled_paths(
                control_plane=self._control_plane, deployment_id=deployment_id
            ),
            drop=frozenset(drop_artifact_ids),
            accepted=lambda ids: self._catalog.accepted_hashes(
                deployment_id=deployment_id, artifact_ids=ids
            ),
            dropped=lambda ids: self._catalog.pages_dropped(
                forget=forget, artifact_ids=ids
            ),
            lease=self._control_plane.commit_lease(deployment_id=deployment_id),
        )

    def flag(self, *, forget: Forget, paths: tuple[str, ...]) -> None:
        """Raise a redaction-required review flag on each listed authored page."""
        self._control_plane.require_redaction(
            deployment_id=forget.deployment_id, git_paths=paths
        )

    def sync_redactions(self, *, deployment_id: UUID, worktree: Path) -> None:
        """The K sync's check (D159 §3.4): finish records whose owners redacted.

        ``worktree`` is the K sync's checkout. A waiting record is looked at
        again only when one of its listed files no longer names the document;
        then that path's history is rewritten and the record completes once
        nothing cites the document. While a page is still listed its
        redaction flag stays raised, even if an edit kept the citation. A
        failure is kept on the record and the next K sync looks again; it
        never fails the K cycle.
        """
        for forget in self._catalog.waiting(deployment_id=deployment_id):
            needles = self._catalog.needles(forget=forget)
            try:
                if all(
                    _cites(worktree=worktree, git_path=path, needles=needles)
                    for path in forget.blocking_k_paths
                ):
                    self.flag(forget=forget, paths=forget.blocking_k_paths)
                    continue
                erasure = self.erase(forget=forget, drop_artifact_ids=(), first=False)
                status = self._catalog.redaction_progress(
                    forget=forget, blocking_k_paths=erasure.blocking_paths
                )
                if status is ForgetStatus.WAITING_FOR_REDACTION:
                    self.flag(forget=forget, paths=erasure.blocking_paths)
            except Exception as error:  # noqa: BLE001 — the next K sync looks again
                _logger.exception(
                    "forget.redaction_sync_failed forget_id=%s",
                    forget.forget_id,
                    extra={"error_code": "forget_redaction_sync_failed"},
                )
                self._catalog.record_error(forget=forget, code=type(error).__name__)


def compiled_paths(
    *, control_plane: KnowledgeControlPlane, deployment_id: UUID
) -> dict[str, UUID]:
    """Each live compiled page's path and artifact: what the snapshot holds."""
    return {
        state.git_path: state.artifact_id
        for state in control_plane.artifact_path_states(deployment_id=deployment_id)
        if state.page_kind is KnowledgePageKind.COMPILED
    }


def _cites(*, worktree: Path, git_path: str, needles: tuple[str, ...]) -> bool:
    """Whether the checkout's file at ``git_path`` still names the document."""
    target = worktree.joinpath(*PurePosixPath(git_path).parts)
    if target.is_symlink() or not target.is_file():
        return False
    return cites_forgotten(content=target.read_bytes(), needles=needles)


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
                series = self._catalog.rearm_on(connection=connection, forget=forget)
                enqueue_on(
                    connection=connection,
                    work=sweep_work(
                        deployment_id=deployment_id,
                        forget_id=forget.forget_id,
                        sweep=1,
                        failures=0,
                        not_before=None,
                        first=True,
                        reapply=True,
                        series=series,
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
    series: str


class ForgetHandler:
    """Run one sweep; schedule the next until a sweep finds nothing (D159 §3.4).

    The work item has no practical attempt cap: a failed sweep records a
    content-free error code on the record and schedules the next sweep with
    backoff, and a sweep whose worker died is retried when its lease expires.
    The document stays hidden throughout.
    """

    def __init__(
        self,
        *,
        service: ForgetService,
        catalog: ForgetCatalog,
        profile_refresher: ProfileRefresherPort,
        object_purgers: tuple[ObjectPurgePort, ...],
        projection_purger: ProjectionPurgePort,
        knowledge: ForgetKnowledge,
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
        self._knowledge = knowledge
        self._quiet_period_s = quiet_period_s

    def handle(self, *, work: ClaimedWork, meter: CostMeterPort) -> HandlerOutcome:
        """Sweep once and return the next sweep as a follow-up, unless clean."""
        payload = _payload(work=work)
        forget = self._catalog.record(
            deployment_id=work.deployment_id, forget_id=payload.forget_id
        )
        if payload.series != self._catalog.current_series(forget=forget):
            # A forget-reapply started a newer series; this one stops here.
            return HandlerOutcome()
        try:
            found, blocking = self.sweep(forget=forget, payload=payload, meter=meter)
            if not found and not payload.first:
                status = self._catalog.finish(
                    forget=forget, blocking_k_paths=blocking, series=payload.series
                )
                if status is ForgetStatus.WAITING_FOR_REDACTION:
                    # The one wait: authored pages. The K sync finishes it.
                    self._knowledge.flag(forget=forget, paths=blocking)
                return HandlerOutcome()
        except Exception as error:  # noqa: BLE001 — retried with backoff, never capped
            _logger.exception(
                "forget.sweep_failed forget_id=%s sweep=%s",
                forget.forget_id,
                payload.sweep,
                extra={"error_code": "forget_sweep_failed"},
            )
            self._catalog.record_error(forget=forget, code=type(error).__name__)
            return HandlerOutcome(
                follow_up=(
                    self._retry(deployment_id=work.deployment_id, payload=payload),
                )
            )
        # Residue found, or the first sweep: look again after a quiet period.
        return HandlerOutcome(
            follow_up=(self._next(deployment_id=work.deployment_id, payload=payload),)
        )

    def sweep(
        self, *, forget: Forget, payload: _SweepPayload, meter: CostMeterPort | None
    ) -> tuple[bool, tuple[str, ...]]:
        """One sweep (D159 §3.3): whether it removed residue, and what still cites.

        The second value lists the authored pages and sidecars whose current
        file still names the document.
        """
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
                _logger.exception(
                    "forget.profile_refresh_failed forget_id=%s",
                    forget.forget_id,
                    extra={"error_code": "forget_profile_refresh_failed"},
                )
        # Everything under the document's prefix and every collected original
        # key, except an original another lineage still uses: it keeps the
        # key it was first stored under, which can be under this prefix.
        live = tuple(
            ObjectKey(key)
            for key in sorted(self._catalog.live_originals(forget=forget))
        )
        keys = tuple(key for key in findings.object_keys if not key.root.endswith("/"))
        foreign = tuple(key for key in findings.object_keys if key.root.endswith("/"))
        # Another lineage's <owner>/<hash>/ prefix is purged only while no
        # surviving version (an archived one included) uses that content.
        in_use = self._catalog.hashes_in_use(
            forget=forget,
            hashes=tuple(
                value
                for key in foreign
                if (value := locator_hash(locator=key.root)) is not None
            ),
        )
        prefixes = (
            ObjectKey(f"{forget.doc_id}/"),
            *(key for key in foreign if locator_hash(locator=key.root) not in in_use),
        )
        for purger in self._object_purgers:
            removed = purger.purge_objects(keys=keys, prefixes=prefixes, keep=live)
            found |= removed > 0
        if findings.fixture_keys:
            # Deployment-wide K plan transcripts: purged, never counted.
            for purger in self._object_purgers:
                purger.purge_objects(keys=findings.fixture_keys, prefixes=())
        found |= self._projection_purger.erase_document(
            deployment_id=forget.deployment_id, doc_id=forget.doc_id
        )
        compiled = findings.compiled_artifact_ids
        if payload.reapply and payload.first:
            # A restored K repository may hold the pages the record names;
            # they are deleted and rebuilt, never kept as owner edits.
            marked = self._catalog.mark_stale(
                forget=forget,
                artifact_ids=self._catalog.collected_artifact_ids(forget=forget),
            )
            compiled = tuple(sorted({*compiled, *marked}, key=str))
        erasure = self._knowledge.erase(
            forget=forget, drop_artifact_ids=compiled, first=payload.first
        )
        if compiled:
            self._catalog.clear_k_pending(forget=forget, artifact_ids=compiled)
        return found or erasure.removed, erasure.blocking_paths

    def _next(self, *, deployment_id: UUID, payload: _SweepPayload) -> EnqueueWork:
        return sweep_work(
            deployment_id=deployment_id,
            forget_id=payload.forget_id,
            sweep=payload.sweep + 1,
            failures=0,
            not_before=utc_now()
            + retry_delay(quiet_period_s=self._quiet_period_s, failures=0),
            reapply=payload.reapply,
            series=payload.series,
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
            series=payload.series,
        )


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
            series=str(payload.get("series", "")),
        )
    except (KeyError, ValueError) as error:
        raise NonRetryableHandlerError(
            f"forget work {work.processing_id} has an invalid payload"
        ) from error
