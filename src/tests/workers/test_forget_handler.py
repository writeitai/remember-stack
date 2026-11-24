"""D159 sweep-until-clean: quiet period, no attempt cap, the authored-page wait."""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from typing import cast
from uuid import UUID

from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClaimedWork
from rememberstack.model import EnqueueWork
from rememberstack.model import Forget
from rememberstack.model import ForgetStatus
from rememberstack.model import KnowledgeErasure
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingTarget
from rememberstack.spine import ForgetCatalog
from rememberstack.spine.forget import SweepFindings
from rememberstack.workers import ForgetHandler
from rememberstack.workers import ForgetService

_DEPLOYMENT_ID = UUID("59100000-0000-0000-0000-000000000001")
_DOC_ID = UUID("59100000-0000-0000-0000-000000000002")
_FORGET_ID = UUID("59100000-0000-0000-0000-000000000003")
_COMPILED_ID = UUID("59100000-0000-0000-0000-000000000004")
_SHARED_HASH = "c" * 64
_OWN_HASH = "d" * 64
_FORGET = Forget(
    forget_id=_FORGET_ID,
    deployment_id=_DEPLOYMENT_ID,
    doc_id=_DOC_ID,
    version_ids=(),
    content_hashes=(_OWN_HASH, _SHARED_HASH),
    source_identity_hash=None,
    status=ForgetStatus.ERASING,
    requested_at=datetime(2026, 11, 24, tzinfo=timezone.utc),
)


@dataclass
class _Catalog:
    """The record and the PostgreSQL step, scripted per sweep."""

    removed: list[bool]
    live: frozenset[str] = frozenset()
    compiled: tuple[UUID, ...] = ()
    record_value: Forget = _FORGET
    series: str = ""
    errors: list[str] = field(default_factory=list)
    finished: list[tuple[str, ...]] = field(default_factory=list)
    scheduled: list[EnqueueWork] = field(default_factory=list)

    def record(self, *, deployment_id: UUID, forget_id: UUID) -> Forget:
        return self.record_value

    stale: list[tuple[UUID, ...]] = field(default_factory=list)

    def sweep_postgres(self, *, forget: Forget) -> SweepFindings:
        return SweepFindings(
            removed=self.removed.pop(0),
            object_keys=(
                ObjectKey(f"{_DOC_ID}/{_OWN_HASH}/original.txt"),
                ObjectKey(f"{_DOC_ID}/{_SHARED_HASH}/original.txt"),
            ),
            compiled_artifact_ids=self.compiled,
            profile_entity_ids=(),
        )

    def live_originals(self, *, forget: Forget) -> frozenset[str]:
        return self.live

    def current_series(self, *, forget: Forget) -> str:
        return self.series

    def collected_artifact_ids(self, *, forget: Forget) -> tuple[UUID, ...]:
        return (_COMPILED_ID,)

    def mark_stale(self, *, forget: Forget, artifact_ids: tuple[UUID, ...]) -> None:
        self.stale.append(artifact_ids)

    def clear_k_pending(
        self, *, forget: Forget, artifact_ids: tuple[UUID, ...]
    ) -> None:
        self.compiled = tuple(i for i in self.compiled if i not in artifact_ids)

    def finish(
        self, *, forget: Forget, blocking_k_paths: tuple[str, ...], series: str
    ) -> ForgetStatus:
        self.finished.append(blocking_k_paths)
        return (
            ForgetStatus.WAITING_FOR_REDACTION
            if blocking_k_paths
            else ForgetStatus.COMPLETE
        )

    def record_error(self, *, forget: Forget, code: str) -> None:
        self.errors.append(code)

    def schedule(self, *, work: EnqueueWork) -> None:
        self.scheduled.append(work)


class _Service:
    def __init__(self) -> None:
        self.rehidden = 0

    def rehide(self, *, forget: Forget) -> None:
        self.rehidden += 1


@dataclass
class _Objects:
    removed: list[int]
    fail: bool = False
    calls: list[tuple[tuple[str, ...], ...]] = field(default_factory=list)

    def purge_objects(
        self,
        *,
        keys: tuple[ObjectKey, ...],
        prefixes: tuple[ObjectKey, ...],
        keep: tuple[ObjectKey, ...] = (),
        **_: object,
    ) -> int:
        if self.fail:
            self.fail = False
            raise OSError("object store unavailable")
        self.calls.append(
            (
                tuple(key.root for key in keys),
                tuple(key.root for key in prefixes),
                tuple(key.root for key in keep),
            )
        )
        return self.removed.pop(0) if self.removed else 0


@dataclass
class _Corpus:
    removed: list[bool] = field(default_factory=list)

    def erase_document(self, *, deployment_id: UUID, doc_id: UUID) -> bool:
        return self.removed.pop(0) if self.removed else False


@dataclass
class _Knowledge:
    """The K step: scripted removals and blocking paths per sweep."""

    removed: list[bool] = field(default_factory=list)
    blocking: list[tuple[str, ...]] = field(default_factory=list)
    dropped: list[tuple[UUID, ...]] = field(default_factory=list)
    flagged: list[tuple[str, ...]] = field(default_factory=list)
    fail: bool = False

    def erase(
        self, *, forget: Forget, drop_artifact_ids: tuple[UUID, ...]
    ) -> KnowledgeErasure:
        if self.fail:
            self.fail = False
            raise RuntimeError("K repository unavailable")
        self.dropped.append(drop_artifact_ids)
        return KnowledgeErasure(
            removed=self.removed.pop(0) if self.removed else False,
            blocking_paths=self.blocking.pop(0) if self.blocking else (),
        )

    def flag(self, *, forget: Forget, paths: tuple[str, ...]) -> None:
        self.flagged.append(paths)


@dataclass
class _Rig:
    catalog: _Catalog
    objects: _Objects = field(default_factory=lambda: _Objects(removed=[]))
    corpus: _Corpus = field(default_factory=_Corpus)
    knowledge: _Knowledge = field(default_factory=_Knowledge)
    service: _Service = field(default_factory=_Service)

    def handler(self) -> ForgetHandler:
        return ForgetHandler(
            service=cast(ForgetService, self.service),
            catalog=cast(ForgetCatalog, self.catalog),
            profile_refresher=RecordingProfileRefresher(),
            object_purgers=(self.objects,),  # type: ignore[arg-type]
            projection_purger=self.corpus,
            knowledge=self.knowledge,
            quiet_period_s=900,
        )


def _work(payload: dict[str, object]) -> ClaimedWork:
    return ClaimedWork(
        processing_id=UUID(int=int(str(payload["sweep"]))),
        deployment_id=_DEPLOYMENT_ID,
        target_kind=ProcessingTarget.FORGET,
        target_id=_FORGET_ID,
        stage=PipelineStage.FORGET,
        component_version=f"forget-v1/{payload['sweep']}",
        content_hash=str(_FORGET_ID),
        lane=None,
        attempt=1,
        payload=payload,
    )


def _run(rig: _Rig, payload: dict[str, object]) -> dict[str, object] | None:
    """Run one sweep; return the next sweep's payload, if any."""
    outcome = rig.handler().handle(work=_work(payload), meter=NoopCostMeter())
    if not outcome.follow_up:
        return None
    (follow_up,) = outcome.follow_up
    assert follow_up.target_kind is ProcessingTarget.FORGET
    assert follow_up.target_id == _FORGET_ID
    assert follow_up.not_before is not None
    assert follow_up.payload is not None
    return follow_up.payload


_FIRST: dict[str, object] = {
    "forget_id": str(_FORGET_ID),
    "sweep": 1,
    "failures": 0,
    "first": True,
    "reapply": False,
}


def test_sweeps_repeat_after_the_quiet_period_until_one_finds_nothing() -> None:
    """§3.4: the first sweep never completes; a late write costs one more sweep."""
    rig = _Rig(catalog=_Catalog(removed=[True, True, False]))
    second = _run(rig, _FIRST)
    assert second is not None and second["sweep"] == 2 and not second["first"]
    third = _run(rig, second)
    assert third is not None and third["sweep"] == 3
    assert _run(rig, third) is None
    assert rig.catalog.finished == [()]
    assert rig.service.rehidden == 3


def test_object_step_spares_only_a_live_shared_original() -> None:
    """§3.3 step 2: everything under the prefix, except a live shared original."""
    shared = f"{_DOC_ID}/{_SHARED_HASH}/original.txt"
    rig = _Rig(catalog=_Catalog(removed=[True], live=frozenset({shared})))
    _run(rig, _FIRST)
    ((keys, prefixes, keep),) = rig.objects.calls
    assert keys == (f"{_DOC_ID}/{_OWN_HASH}/original.txt", shared)
    assert prefixes == (f"{_DOC_ID}/",)
    assert keep == (shared,)


def test_residue_in_any_store_keeps_the_forget_erasing() -> None:
    """A late object or corpus file is found by the next sweep, never ignored."""
    rig = _Rig(
        catalog=_Catalog(removed=[True, False, False, False]),
        objects=_Objects(removed=[0, 1, 0, 0]),
        corpus=_Corpus(removed=[False, False, True, False]),
    )
    payload = _run(rig, _FIRST)
    for _ in range(3):
        assert payload is not None
        payload = _run(rig, payload)
    assert payload is None
    assert rig.catalog.finished == [()]


def test_a_failed_sweep_is_retried_with_backoff_and_no_attempt_cap() -> None:
    """§5 test 7: a failure inside any step records a code and sweeps again."""
    rig = _Rig(
        catalog=_Catalog(removed=[True, True, False]),
        objects=_Objects(removed=[], fail=True),
    )
    retry = _run(rig, _FIRST)
    assert retry is not None
    assert retry["failures"] == 1 and retry["first"] is True
    assert rig.catalog.errors == ["OSError"]
    second = _run(rig, retry)
    assert second is not None and second["failures"] == 0
    assert _run(rig, second) is None


def test_a_sweep_row_has_no_practical_attempt_cap() -> None:
    """A sweep whose worker keeps dying is retried on lease expiry, not dead-lettered."""
    rig = _Rig(catalog=_Catalog(removed=[True]))
    outcome = rig.handler().handle(work=_work(_FIRST), meter=NoopCostMeter())
    (follow_up,) = outcome.follow_up
    assert follow_up.max_attempts == 32_767


def test_a_sweep_of_an_older_series_does_nothing() -> None:
    """§3.8: after forget-reapply, an earlier series cannot finish the record."""
    rig = _Rig(catalog=_Catalog(removed=[], series="newer"))
    assert _run(rig, _FIRST) is None
    assert rig.catalog.finished == []
    assert rig.service.rehidden == 0


def test_an_interrupted_k_step_stays_pending_until_it_succeeds() -> None:
    """§5 test 7: pages marked in PostgreSQL are dropped even if that sweep died."""
    rig = _Rig(catalog=_Catalog(removed=[True, False, False], compiled=(_COMPILED_ID,)))
    rig.knowledge.fail = True
    payload = _run(rig, _FIRST)
    assert payload is not None and payload["failures"] == 1
    assert rig.knowledge.dropped == []
    assert rig.catalog.compiled == (_COMPILED_ID,)
    payload = _run(rig, payload)
    assert rig.knowledge.dropped == [(_COMPILED_ID,)]
    assert rig.catalog.compiled == ()
    assert payload is not None
    assert _run(rig, payload) is None
    assert rig.catalog.finished == [()]


def test_k_residue_keeps_the_forget_erasing() -> None:
    """A late compiled page or history is found by the next sweep, never ignored."""
    rig = _Rig(
        catalog=_Catalog(removed=[True, False, False]),
        knowledge=_Knowledge(removed=[False, True, False]),
    )
    payload = _run(rig, _FIRST)
    assert payload is not None
    payload = _run(rig, payload)
    assert payload is not None
    assert _run(rig, payload) is None


def test_a_reapply_marks_the_recorded_pages_on_its_first_sweep_only() -> None:
    """§3.8: a restored K repository's recorded pages are dropped again."""
    rig = _Rig(catalog=_Catalog(removed=[False, False]))
    second = _run(rig, {**_FIRST, "reapply": True})
    assert second is not None and second["reapply"] is True
    assert _run(rig, second) is None
    assert rig.catalog.stale == [(_COMPILED_ID,)]
    assert rig.knowledge.dropped == [(_COMPILED_ID,), ()]


def test_authored_pages_make_the_forget_wait_for_the_k_sync() -> None:
    """§5 test 8: everything else is clean; the citing page is flagged and listed.

    The job then stops: the K sync finishes the record after the owner's edit.
    """
    rig = _Rig(
        catalog=_Catalog(removed=[True, False]),
        knowledge=_Knowledge(blocking=[("k/authored.md",), ("k/authored.md",)]),
    )
    payload = _run(rig, _FIRST)
    assert payload is not None
    assert _run(rig, payload) is None
    assert rig.catalog.finished == [("k/authored.md",)]
    assert rig.knowledge.flagged == [("k/authored.md",)]
