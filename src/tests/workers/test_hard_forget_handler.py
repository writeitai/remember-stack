"""Focused all-store ordering and restore-replay tests for D74."""

from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from rememberstack.model import ForgetManifest
from rememberstack.model import ForgetManifestRecord
from rememberstack.model import ForgetManifestStatus
from rememberstack.model import ObjectKey
from rememberstack.ports import ForgetManifestPort
from rememberstack.ports import KGitPurgePort
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ProjectionPurgePort
from rememberstack.spine import ForgetCatalog
from rememberstack.workers import CorpusFsBuilder
from rememberstack.workers import DeletionService
from rememberstack.workers import ForgetKnowledgeRebuilder
from rememberstack.workers import ForgetProjectionRebuilder
from rememberstack.workers import GraphRebuildWorker
from rememberstack.workers import HardForgetHandler
from rememberstack.workers import HardForgetReadiness
from rememberstack.workers import HardForgetService
from rememberstack.workers import KnowledgeCommitDriver
from rememberstack.workers import KnowledgeCycleForgetRebuilder
from rememberstack.workers import ProjectionPairForgetRebuilder

_DEPLOYMENT_ID = UUID("75000000-0000-0000-0000-000000000001")
_DOC_ID = UUID("75000000-0000-0000-0000-000000000002")
_FORGET_ID = UUID("75000000-0000-0000-0000-000000000003")
_ARTIFACT_ID = UUID("75000000-0000-0000-0000-000000000004")
_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _manifest() -> ForgetManifest:
    return ForgetManifest(
        forget_id=_FORGET_ID,
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_DOC_ID,
        requested_at=_NOW,
        object_keys=(ObjectKey("raw/forgotten"),),
        projection_prefixes=(ObjectKey("snapshots/forgotten"),),
        k_artifact_ids=(_ARTIFACT_ID,),
    )


class _Catalog:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def scrub_postgres(self, *, manifest: ForgetManifest) -> None:
        self.events.append("scrub-postgres")

    def verify_postgres_scrubbed(self, *, manifest: ForgetManifest) -> None:
        self.events.append("verify-postgres")

    def mark_complete(self, *, manifest: ForgetManifest) -> None:
        self.events.append("complete")


class _Deletion:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def delete_lineage(self, *, deployment_id: UUID, doc_id: UUID) -> None:
        self.events.append("delete-lineage")


class _Objects:
    def __init__(self, *, events: list[str], name: str, fail: bool = False) -> None:
        self.events = events
        self.name = name
        self.fail = fail

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        assert keys == (ObjectKey("raw/forgotten"),)
        assert prefixes == ()
        self.events.append(f"objects-{self.name}")
        if self.fail:
            raise RuntimeError("object store unavailable")

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        self.events.append(f"verify-objects-{self.name}")


class _ProjectionRebuilder:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def rebuild_without_lineage(self, *, deployment_id: UUID, forget_id: UUID) -> None:
        self.events.append("projection-rebuild")


class _ProjectionPurger:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def purge_projections(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        assert prefixes == (ObjectKey("snapshots/forgotten"),)
        self.events.append("projection-purge")

    def verify_projections_purged(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        self.events.append("verify-projections")


class _KnowledgeRebuilder:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        assert artifact_ids == (_ARTIFACT_ID,)
        self.events.append("knowledge-rebuild")


class _KGit:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def blocking_redaction_paths(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[str, ...]:
        return ()

    def purge_artifacts(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        self.events.append("k-purge")

    def verify_artifacts_purged(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        self.events.append("verify-k")


def _handler(*, events: list[str], fail_objects: bool = False) -> HardForgetHandler:
    return HardForgetHandler(
        catalog=cast(ForgetCatalog, _Catalog(events=events)),
        deletion=cast(DeletionService, _Deletion(events=events)),
        object_purgers=(
            cast(
                ObjectPurgePort, _Objects(events=events, name="raw", fail=fail_objects)
            ),
            cast(ObjectPurgePort, _Objects(events=events, name="transcripts")),
        ),
        projection_rebuilder=cast(
            ForgetProjectionRebuilder, _ProjectionRebuilder(events=events)
        ),
        projection_purger=cast(ProjectionPurgePort, _ProjectionPurger(events=events)),
        knowledge_rebuilder=cast(
            ForgetKnowledgeRebuilder, _KnowledgeRebuilder(events=events)
        ),
        k_git=cast(KGitPurgePort, _KGit(events=events)),
    )


def test_handler_runs_the_single_all_store_sequence_before_reopening() -> None:
    events: list[str] = []

    _handler(events=events).honor(manifest=_manifest())

    assert events == [
        "delete-lineage",
        "scrub-postgres",
        "objects-raw",
        "objects-transcripts",
        "projection-rebuild",
        "projection-purge",
        "knowledge-rebuild",
        "k-purge",
        "verify-objects-raw",
        "verify-objects-transcripts",
        "verify-projections",
        "verify-k",
        "verify-postgres",
        "complete",
    ]


def test_handler_failure_leaves_admission_closed_and_preserves_exception() -> None:
    events: list[str] = []

    with pytest.raises(RuntimeError, match="object store unavailable"):
        _handler(events=events, fail_objects=True).honor(manifest=_manifest())

    assert events == ["delete-lineage", "scrub-postgres", "objects-raw"]


class _Graph:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def rebuild(
        self, *, deployment_id: UUID, workdir: Path, version: str | None = None
    ) -> dict[str, object]:
        self.events.append(f"graph:{workdir.name}")
        return {}


class _Corpus:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def build(
        self, *, deployment_id: UUID, version: str | None = None
    ) -> dict[str, object]:
        self.events.append("corpus")
        return {}


class _KnowledgeDriver:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def run_cycle(
        self, *, deployment_id: UUID, exclusions_by_artifact: object
    ) -> object:
        self.events.append("knowledge-cycle")
        return object()


def test_rebuilders_delegate_to_existing_production_cycles(tmp_path: Path) -> None:
    """Hard-forget introduces no second P2/P3 or K compilation implementation."""
    events: list[str] = []
    ProjectionPairForgetRebuilder(
        graph=cast(GraphRebuildWorker, _Graph(events=events)),
        corpus=cast(CorpusFsBuilder, _Corpus(events=events)),
        workdir=tmp_path,
    ).rebuild_without_lineage(deployment_id=_DEPLOYMENT_ID, forget_id=_FORGET_ID)
    knowledge = KnowledgeCycleForgetRebuilder(
        driver=cast(KnowledgeCommitDriver, _KnowledgeDriver(events=events))
    )
    knowledge.recompile_without_lineage(
        deployment_id=_DEPLOYMENT_ID, artifact_ids=(_ARTIFACT_ID,)
    )
    knowledge.recompile_without_lineage(deployment_id=_DEPLOYMENT_ID, artifact_ids=())

    assert events == [f"graph:{_FORGET_ID}", "corpus", "knowledge-cycle"]


class _ReadinessCatalog:
    def __init__(
        self, *, events: list[str], pending: ForgetManifestRecord | None = None
    ) -> None:
        self.events = events
        self.pending = pending

    def preparing_record(self, *, deployment_id: UUID) -> ForgetManifestRecord | None:
        self.events.append("find-preparing")
        return self.pending

    def materialize_portable(self, *, manifest: ForgetManifest) -> None:
        self.events.append("materialize-portable")


class _ReadinessStore:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def append(self, *, manifest: ForgetManifest) -> None:
        self.events.append("append")

    def manifests(self, *, deployment_id: UUID) -> tuple[ForgetManifest, ...]:
        self.events.append("enumerate")
        return (_manifest(),)


class _ReadinessService:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def request(
        self,
        *,
        deployment_id: UUID,
        doc_id: UUID,
        forget_id: UUID,
        requested_at: datetime,
    ) -> ForgetManifest:
        self.events.append("resume-preparing")
        return _manifest()


class _ReadinessHandler:
    def __init__(self, *, events: list[str]) -> None:
        self.events = events

    def honor(self, *, manifest: ForgetManifest) -> None:
        self.events.append("honor")


def test_readiness_resumes_preparation_and_rehonors_even_complete_intent() -> None:
    events: list[str] = []
    manifest = _manifest()
    pending = ForgetManifestRecord(
        forget_id=manifest.forget_id,
        deployment_id=manifest.deployment_id,
        doc_id=manifest.doc_id,
        manifest=manifest,
        manifest_hash=manifest.sha256(),
        status=ForgetManifestStatus.PREPARING,
        prepared_at=_NOW,
        accepted_at=None,
        completed_at=None,
        last_verified_at=None,
    )
    readiness = HardForgetReadiness(
        catalog=cast(ForgetCatalog, _ReadinessCatalog(events=events, pending=pending)),
        manifest_store=cast(ForgetManifestPort, _ReadinessStore(events=events)),
        request_service=cast(HardForgetService, _ReadinessService(events=events)),
        handler=cast(HardForgetHandler, _ReadinessHandler(events=events)),
    )

    honored = readiness.ensure_ready(deployment_id=_DEPLOYMENT_ID)

    assert honored == (_FORGET_ID,)
    assert events == [
        "find-preparing",
        "resume-preparing",
        "enumerate",
        "materialize-portable",
        "honor",
    ]
