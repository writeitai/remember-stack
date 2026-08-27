"""S55/WP-7.7 deterministic whole- and independent-store restore canary."""

from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from typing import cast
from uuid import UUID

from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import current_temporal_scope
from rememberstack.model import Envelope
from rememberstack.model import ForgetManifest
from rememberstack.model import Freshness
from rememberstack.model import Grain
from rememberstack.model import Negative
from rememberstack.model import NegativeKind
from rememberstack.model import ObjectKey
from rememberstack.ports import ForgetManifestPort
from rememberstack.ports import KGitPurgePort
from rememberstack.ports import ObjectPurgePort
from rememberstack.ports import ProjectionPurgePort
from rememberstack.spine import ForgetCatalog
from rememberstack.workers import DeletionService
from rememberstack.workers import ForgetKnowledgeRebuilder
from rememberstack.workers import ForgetProjectionRebuilder
from rememberstack.workers import HardForgetHandler
from rememberstack.workers import HardForgetReadiness
from rememberstack.workers import HardForgetService

_DEPLOYMENT_ID = UUID("55000000-0000-0000-0000-000000000001")
_DOC_ID = UUID("55000000-0000-0000-0000-000000000002")
_FORGET_ID = UUID("55000000-0000-0000-0000-000000000003")
_CHUNK_ID = UUID("55000000-0000-0000-0000-000000000004")
_CLAIM_ID = UUID("55000000-0000-0000-0000-000000000005")
_FACT_ID = UUID("55000000-0000-0000-0000-000000000006")
_ENTITY_ID = UUID("55000000-0000-0000-0000-000000000007")
_ARTIFACT_ID = UUID("55000000-0000-0000-0000-000000000008")
_NOW = datetime(2026, 7, 21, 13, 0, tzinfo=timezone.utc)
_FORGOTTEN = "S55_UNIQUE_FORGOTTEN_TOKEN"
_CONTROL = "S55_INDEPENDENT_CONTROL_FACT"
_NEVER_SEEN = "S55_NEVER_SEEN_TOKEN"
_CHANNELS = ("semantic", "verbatim", "graph", "knowledge", "browse")


def _manifest() -> ForgetManifest:
    return ForgetManifest(
        forget_id=_FORGET_ID,
        deployment_id=_DEPLOYMENT_ID,
        doc_id=_DOC_ID,
        requested_at=_NOW,
        content_hashes=("5" * 64,),
        chunk_ids=(_CHUNK_ID,),
        claim_ids=(_CLAIM_ID,),
        fact_ids=(_FACT_ID,),
        entity_ids=(_ENTITY_ID,),
        object_keys=(ObjectKey("raw/forgotten"),),
        projection_prefixes=(ObjectKey("snapshots/pre-forget"),),
        k_artifact_ids=(_ARTIFACT_ID,),
    )


@dataclass
class _ServingState:
    channels: dict[str, set[str]]

    @classmethod
    def before_forget(cls) -> "_ServingState":
        return cls(channels={name: {_FORGOTTEN, _CONTROL} for name in _CHANNELS})

    def restore(self, *channels: str) -> None:
        for channel in channels:
            self.channels[channel].add(_FORGOTTEN)

    def remove(self, *channels: str) -> None:
        for channel in channels:
            self.channels[channel].discard(_FORGOTTEN)

    def envelope(self, *, token: str) -> Envelope:
        assert not any(token in values for values in self.channels.values())
        return Envelope(
            grain=Grain.EVIDENCE,
            temporal_scope=current_temporal_scope(evaluated_at=_NOW),
            freshness=Freshness(pg_live_ts=_NOW),
            negative=Negative(
                kind=NegativeKind.UNKNOWN_ENTITY,
                explanation="No matching memory exists.",
            ),
        )


class _Catalog:
    def __init__(self, *, state: _ServingState) -> None:
        self.state = state
        self.complete = False
        self.materializations = 0

    def preparing_record(self, *, deployment_id: UUID) -> None:
        return None

    def materialize_portable(self, *, manifest: ForgetManifest) -> None:
        self.materializations += 1

    def scrub_postgres(self, *, manifest: ForgetManifest) -> None:
        self.state.remove("semantic", "verbatim")

    def verify_postgres_scrubbed(self, *, manifest: ForgetManifest) -> None:
        assert _FORGOTTEN not in self.state.channels["semantic"]
        assert _FORGOTTEN not in self.state.channels["verbatim"]

    def mark_complete(self, *, manifest: ForgetManifest) -> None:
        self.complete = True


class _ManifestStore:
    def append(self, *, manifest: ForgetManifest) -> None:
        return None

    def manifests(self, *, deployment_id: UUID) -> tuple[ForgetManifest, ...]:
        return (_manifest(),)


class _Deletion:
    def delete_lineage(self, *, deployment_id: UUID, doc_id: UUID, **_: object) -> None:
        return None


class _Objects:
    def __init__(self, *, state: _ServingState) -> None:
        self.state = state

    def purge_objects(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        self.state.remove("verbatim")

    def verify_objects_purged(
        self, *, keys: tuple[ObjectKey, ...], prefixes: tuple[ObjectKey, ...]
    ) -> None:
        assert _FORGOTTEN not in self.state.channels["verbatim"]


class _ProjectionRebuilder:
    def __init__(self, *, state: _ServingState) -> None:
        self.state = state

    def rebuild_without_lineage(self, *, deployment_id: UUID, forget_id: UUID) -> None:
        self.state.remove("graph", "browse")


class _ProjectionPurger:
    def purge_projections(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        return None

    def verify_projections_purged(
        self, *, deployment_id: UUID, prefixes: tuple[ObjectKey, ...]
    ) -> None:
        return None


class _KnowledgeRebuilder:
    def __init__(self, *, state: _ServingState) -> None:
        self.state = state

    def recompile_without_lineage(
        self, *, deployment_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        self.state.remove("knowledge")


class _KGit:
    def blocking_redaction_paths(
        self, *, deployment_id: UUID, doc_id: UUID
    ) -> tuple[str, ...]:
        return ()

    def purge_artifacts(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        return None

    def verify_artifacts_purged(
        self, *, deployment_id: UUID, forget_id: UUID, artifact_ids: tuple[UUID, ...]
    ) -> None:
        return None


class _UnusedRequestService:
    def request(self, **_: object) -> ForgetManifest:
        raise AssertionError("the canary has no crash-stranded preparation")


def _readiness(*, state: _ServingState) -> tuple[HardForgetReadiness, _Catalog]:
    catalog = _Catalog(state=state)
    handler = HardForgetHandler(
        catalog=cast(ForgetCatalog, catalog),
        deletion=cast(DeletionService, _Deletion()),
        profile_refresher=RecordingProfileRefresher(),
        object_purgers=(cast(ObjectPurgePort, _Objects(state=state)),),
        projection_rebuilder=cast(
            ForgetProjectionRebuilder, _ProjectionRebuilder(state=state)
        ),
        projection_purger=cast(ProjectionPurgePort, _ProjectionPurger()),
        knowledge_rebuilder=cast(
            ForgetKnowledgeRebuilder, _KnowledgeRebuilder(state=state)
        ),
        k_git=cast(KGitPurgePort, _KGit()),
    )
    readiness = HardForgetReadiness(
        catalog=cast(ForgetCatalog, catalog),
        manifest_store=cast(ForgetManifestPort, _ManifestStore()),
        request_service=cast(HardForgetService, _UnusedRequestService()),
        handler=handler,
    )
    return readiness, catalog


def _assert_s55(state: _ServingState) -> None:
    for channel in _CHANNELS:
        assert _FORGOTTEN not in state.channels[channel]
        assert _CONTROL in state.channels[channel]
    assert state.envelope(token=_FORGOTTEN) == state.envelope(token=_NEVER_SEEN)


def test_s55_survives_whole_and_independent_external_store_restores() -> None:
    """Operator restores stay closed until readiness re-honors portable intent."""
    state = _ServingState.before_forget()
    readiness, catalog = _readiness(state=state)

    assert readiness.ensure_ready(deployment_id=_DEPLOYMENT_ID) == (_FORGET_ID,)
    assert catalog.complete
    _assert_s55(state)

    restore_groups = (
        ("semantic", "verbatim", "graph", "knowledge", "browse"),
        ("verbatim",),
        ("semantic",),
        ("graph", "browse"),
        ("knowledge",),
    )
    for group in restore_groups:
        state.restore(*group)
        readiness.ensure_ready(deployment_id=_DEPLOYMENT_ID)
        _assert_s55(state)

    assert catalog.materializations == 1 + len(restore_groups)
