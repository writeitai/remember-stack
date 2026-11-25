"""D159 sweep-until-clean: quiet period, no attempt cap, the authored-page wait."""

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from typing import cast
from uuid import UUID

import pytest

from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClaimedWork
from rememberstack.model import EnqueueWork
from rememberstack.model import Forget
from rememberstack.model import ForgetStatus
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
_AUTHORED_ID = UUID("59100000-0000-0000-0000-000000000005")
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
    blocking: list[tuple[str, ...]] = field(default_factory=list)
    live: frozenset[str] = frozenset()
    compiled: tuple[UUID, ...] = ()
    record_value: Forget = _FORGET
    series: str = ""
    in_use: frozenset[str] = frozenset()
    extra_keys: tuple[str, ...] = ()
    errors: list[str] = field(default_factory=list)
    finished: list[tuple[str, ...]] = field(default_factory=list)
    scheduled: list[EnqueueWork] = field(default_factory=list)

    def record(self, *, deployment_id: UUID, forget_id: UUID) -> Forget:
        return self.record_value

    def sweep_postgres(self, *, forget: Forget) -> SweepFindings:
        return SweepFindings(
            removed=self.removed.pop(0),
            object_keys=(
                ObjectKey(f"{_DOC_ID}/{_OWN_HASH}/original.txt"),
                ObjectKey(f"{_DOC_ID}/{_SHARED_HASH}/original.txt"),
                *(ObjectKey(key) for key in self.extra_keys),
            ),
            compiled_artifact_ids=self.compiled,
            blocking_k_paths=(),
            profile_entity_ids=(),
        )

    def live_originals(self, *, forget: Forget) -> frozenset[str]:
        return self.live

    def hashes_in_use(
        self, *, forget: Forget, hashes: tuple[str, ...]
    ) -> frozenset[str]:
        return frozenset(hashes) & self.in_use

    def collected_artifact_ids(self, *, forget: Forget) -> tuple[UUID, ...]:
        return (_COMPILED_ID,)

    def still_blocking(self, *, forget: Forget) -> tuple[str, ...]:
        return self.blocking.pop(0) if self.blocking else ()

    def authored_artifact_ids(self, *, forget: Forget) -> tuple[UUID, ...]:
        return (_AUTHORED_ID,)

    def current_series(self, *, forget: Forget) -> str:
        return self.series

    def mark_stale(self, **_: object) -> None:
        return None

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
    rebuilt: list[tuple[UUID, ...]] = field(default_factory=list)
    purged: list[tuple[UUID, ...]] = field(default_factory=list)
    fail: bool = False

    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        if self.fail:
            self.fail = False
            raise RuntimeError("K driver unavailable")
        self.rebuilt.append(artifact_ids)

    def purge_artifacts(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        self.purged.append(artifact_ids)

    def verify_artifacts_purged(self, **_: object) -> None:
        return None


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
            knowledge_rebuilder=self.knowledge,
            k_git=self.knowledge,
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
    """Pages marked for a rebuild are rebuilt even if that sweep died after marking."""
    rig = _Rig(catalog=_Catalog(removed=[True, False, False], compiled=(_COMPILED_ID,)))
    rig.knowledge.fail = True
    payload = _run(rig, _FIRST)
    assert payload is not None and payload["failures"] == 1
    assert rig.knowledge.purged == []
    payload = _run(rig, payload)
    assert rig.knowledge.purged == [(_COMPILED_ID,)]
    assert payload is not None
    assert _run(rig, payload) is None


def test_compiled_pages_are_rebuilt_and_their_history_erased() -> None:
    """Interim K step (slice B replaces it): rebuild, then erase history."""
    rig = _Rig(catalog=_Catalog(removed=[True], compiled=(_COMPILED_ID,)))
    _run(rig, _FIRST)
    assert rig.knowledge.rebuilt == [(_COMPILED_ID,)]
    assert rig.knowledge.purged == [(_COMPILED_ID,)]


def test_a_reapply_purges_the_recorded_k_history_once() -> None:
    """§3.8: a restored K repository is cleaned on the reapply's first sweep."""
    rig = _Rig(catalog=_Catalog(removed=[False, False]))
    second = _run(rig, {**_FIRST, "reapply": True})
    assert rig.knowledge.purged == [(_COMPILED_ID,)]
    assert second is not None and second["reapply"] is True
    assert _run(rig, second) is None
    # Completion erases the collected authored pages' history as well.
    assert rig.knowledge.purged == [(_COMPILED_ID,), (_AUTHORED_ID,)]


@pytest.mark.parametrize("edits", (1, 3))
def test_authored_pages_make_the_forget_wait_until_the_owner_edits(edits: int) -> None:
    """§5 test 8: everything else is clean; only the citing page waits."""
    still = tuple(("k/authored.md",) for _ in range(edits))
    rig = _Rig(
        catalog=_Catalog(removed=[True] + [False] * (edits + 1), blocking=[*still])
    )
    payload = _run(rig, _FIRST)
    for _ in range(edits):
        assert payload is not None
        payload = _run(rig, payload)
        assert rig.catalog.finished[-1] == ("k/authored.md",)
        assert rig.knowledge.purged == []
    assert payload is not None
    assert _run(rig, payload) is None
    assert rig.catalog.finished[-1] == ()
    # The owner's edit removed the citation: that path's history is erased.
    assert rig.knowledge.purged == [(_AUTHORED_ID,)]


def test_the_record_keeps_content_free_original_locators() -> None:
    """A stored file name's extension never lands on the forget record."""
    from rememberstack.spine.forget import original_locator

    other = UUID("59100000-0000-0000-0000-0000000000ff")
    assert (
        original_locator(key=f"{_DOC_ID}/{_OWN_HASH}/original.pdf", doc_id=_DOC_ID)
        is None
    )
    assert (
        original_locator(key=f"{other}/{_OWN_HASH}/original.pdf", doc_id=_DOC_ID)
        == f"{other}/{_OWN_HASH}/"
    )
    assert original_locator(key="uploads/abcdefghijklmnop", doc_id=_DOC_ID) == (
        "uploads/abcdefghijklmnop"
    )


def test_a_foreign_prefix_still_in_use_is_never_purged() -> None:
    """Another lineage's <owner>/<hash>/ goes only when no surviving version uses it."""
    owner = "59100000-0000-0000-0000-0000000000aa"
    kept, gone = f"{owner}/{_SHARED_HASH}/", f"{owner}/{_OWN_HASH}/"
    rig = _Rig(
        catalog=_Catalog(
            removed=[True], extra_keys=(kept, gone), in_use=frozenset({_SHARED_HASH})
        )
    )
    _run(rig, _FIRST)
    ((_, prefixes, _),) = rig.objects.calls
    assert prefixes == (f"{_DOC_ID}/", gone)
