"""D155 activation: actual profile, ledger, readiness and normal deletion, no models."""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
from pathlib import Path
from typing import Any
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing.model_provider import FakeModelProvider
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.model import ForgetInProgressError
from rememberstack.model import ObjectKey
from rememberstack.model import ReadinessRequirements
from rememberstack.model import RunResultOutcome
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import PipelineReadinessCatalog
from rememberstack.spine.processing_layers import ProcessingLayerCatalog
from tests.workers.test_corpus_reader_export_pg import corpus as corpus
from tests.workers.test_corpus_reader_export_pg import engine as engine


@pytest.fixture
def active(
    engine: Engine, corpus, tmp_path: Path
) -> Iterator[tuple[SelfHostProfile, CorpusDocument, LocalFSObjectStore]]:
    source, artifacts, raw = corpus
    stores = [
        LocalFSObjectStore(root=tmp_path / name)
        for name in ("raw", "artifacts", "corpus")
    ]
    for store, authority in zip(stores, (raw, artifacts), strict=False):
        for key, content in authority.objects.items():
            store.write_bytes(key=ObjectKey(key), content=content, storage_class="hot")
    manifests = tmp_path / "portable-intent"
    manifests.mkdir()
    profile = SelfHostProfile(
        settings=SelfHostSettings(
            deployment_id=source.deployment_id, forget_manifest_root=manifests
        ),
        engine=engine,
        raw_store=stores[0],  # type: ignore[arg-type]
        artifact_store=stores[1],  # type: ignore[arg-type]
        corpusfs_store=stores[2],  # type: ignore[arg-type]
        model_provider=FakeModelProvider(),
        model_provider_settings=OpenRouterSettings(api_key="unused"),
    )
    yield profile, source, stores[2]


def _ready(engine: Engine, source: CorpusDocument, *versions):
    return PipelineReadinessCatalog(engine=engine, expected_components={}).inspect(
        deployment_id=source.deployment_id,
        version_ids=versions or (source.version_id,),
        require=ReadinessRequirements(
            pipeline=False, p1=False, live_graph=False, p3=True
        ),
    )


def test_readiness_matches_only_current_document_digest(active, engine: Engine) -> None:
    profile, source, store = active
    assert (
        _ready(engine, source).capabilities["p3"].reason
        == "corpus_publication_incomplete"
    )
    assert profile.run_projection(plane="p3") == {"p3": 1}
    ready = _ready(engine, source)
    assert ready.ready
    with engine.connect() as connection:
        assert (
            ready.capabilities["p3"].version
            == connection.execute(
                text(
                    "SELECT corpus_published_revision FROM documents WHERE doc_id=:doc"
                ),
                {"doc": source.doc_id},
            ).scalar_one()
        )
    assert ready.capabilities["p3"].built_at is None
    assert ready.capabilities["p3"].published_at is None
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_metadata SET title = 'A new title' WHERE version_id=:v"
            ),
            {"v": source.version_id},
        )
    assert not _ready(engine, source).ready
    profile.run_projection(plane="p3")
    assert _ready(engine, source).ready
    assert not list(store.iter_keys(prefix=source.path))


@pytest.mark.parametrize("claims,facts", [(True, True), (True, False), (False, False)])
def test_profile_publication_and_synchronous_delete_in_every_layer(
    active, engine: Engine, claims: bool, facts: bool
) -> None:
    profile, source, store = active
    ProcessingLayerCatalog(engine=engine).reconcile_settings(
        deployment_id=source.deployment_id, claims_enabled=claims, facts_enabled=facts
    )
    profile.seed_corpus_work()
    results = profile.worker_loop(worker_class="llm", concurrency=1).drain_due()
    assert results and all(row.outcome == RunResultOutcome.SUCCEEDED for row in results)
    assert _ready(engine, source).ready
    assert store.read_bytes(key=ObjectKey(source.path + "/original.pdf"))
    result = profile.api_deployment().deletion
    assert result is not None
    assert (
        result.delete_document(
            deployment_id=source.deployment_id, doc_id=source.doc_id
        ).doc_id
        == source.doc_id
    )
    assert not list(store.iter_keys(prefix=source.path))
    assert not _ready(engine, source).ready
    assert (
        str(source.doc_id).encode()
        not in store.read_bytes(key=ObjectKey("pdf/finance/annual-reports/_index.md"))
        if list(store.iter_keys(prefix="pdf/finance"))
        else True
    )


@pytest.mark.parametrize("failure", ["delete", "index"])
def test_delete_failure_reports_failure_and_retains_durable_retry(
    active, engine: Engine, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    profile, source, store = active
    profile.run_projection(plane="p3")

    def fail(**kwargs: Any) -> None:
        raise RuntimeError("injected storage failure")

    with monkeypatch.context() as patch:
        patch.setattr(
            store, "delete" if failure == "delete" else "replace_stream", fail
        )
        deletion = profile.api_deployment().deletion
        assert deletion is not None
        with pytest.raises(RuntimeError, match="injected"):
            deletion.delete_document(
                deployment_id=source.deployment_id, doc_id=source.doc_id
            )
    with engine.connect() as connection:
        event = connection.execute(
            text(
                "SELECT processing_id FROM processing_state WHERE stage='publish_corpus' AND status='pending' ORDER BY enqueued_at DESC LIMIT 1"
            )
        ).scalar_one()
        assert connection.execute(
            text(
                "SELECT deleted_at IS NOT NULL AND corpus_published_revision IS NULL FROM documents WHERE doc_id=:doc"
            ),
            {"doc": source.doc_id},
        ).scalar_one()
    assert profile.run_task(processing_id=event).outcome == RunResultOutcome.SUCCEEDED
    assert not list(store.iter_keys(prefix=source.path))


@pytest.mark.parametrize("delivery", ["class", "pushed"])
def test_periodic_verification_repairs_behind_marker_and_schedules_next(
    active, engine: Engine, delivery: str
) -> None:
    profile, source, store = active
    profile.run_projection(plane="p3")
    profile.seed_corpus_work()
    profile.seed_corpus_work()
    profile.worker_loop(worker_class="llm", concurrency=1).drain_due()
    with engine.begin() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT processing_id, not_before, status FROM processing_state WHERE stage='reconcile_corpus'"
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1 and rows[0]["status"] == "pending"
        assert (rows[0]["not_before"] - datetime.now(tz=UTC)).total_seconds() > 86_300
        event = rows[0]["processing_id"]
        # Place the prior cadence slot in the past without sleeping a day.
        connection.execute(
            text(
                "UPDATE processing_state SET not_before=now(), target_id=:old_slot WHERE processing_id=:p"
            ),
            {"p": event, "old_slot": uuid4()},
        )
    key = ObjectKey(source.path + "/original.pdf")
    original = store.read_bytes(key=key)
    store.replace_stream(
        key=key, chunks=(b"late stale bytes",), storage_class="hot", sha256="f" * 64
    )
    assert _ready(engine, source).ready  # marker certifies publication once
    if delivery == "pushed":
        assert (
            profile.run_task(processing_id=event).outcome == RunResultOutcome.SUCCEEDED
        )
    else:
        assert (
            profile.worker_loop(worker_class="llm", concurrency=1)
            .drain_due()[0]
            .outcome
            == RunResultOutcome.SUCCEEDED
        )
    assert store.read_bytes(key=key) == original
    with engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM processing_state WHERE stage='reconcile_corpus' AND status='pending'"
                )
            ).scalar_one()
            == 1
        )


def test_ordinary_reconcile_and_publisher_obey_forget_admission(
    active, engine: Engine
) -> None:
    profile, source, _ = active
    from rememberstack.spine import ForgetCatalog

    ForgetCatalog(engine=engine).prepare(
        deployment_id=source.deployment_id, doc_id=source.doc_id, forget_id=uuid4()
    )
    for operation in (
        lambda: profile.corpus_publisher().publish_corpus(
            deployment_id=source.deployment_id, doc_id=source.doc_id
        ),
        lambda: profile.corpus_publisher().reconcile(
            deployment_id=source.deployment_id
        ),
    ):
        with pytest.raises(ForgetInProgressError):
            operation()


def test_restore_authorizes_each_manifest_while_the_whole_batch_is_closed(
    active, engine: Engine
) -> None:
    from rememberstack.model import ForgetManifest
    from rememberstack.spine import ForgetCatalog
    from rememberstack.spine.corpus_publication import CorpusPublicationCatalog

    profile, source, _ = active
    catalog = ForgetCatalog(engine=engine)
    # UUID enumeration order differs from their original preparation order.
    manifests = [
        ForgetManifest(
            forget_id=UUID(int=number),
            deployment_id=source.deployment_id,
            doc_id=uuid4(),
            requested_at=datetime(2026, 11, 13, tzinfo=UTC),
        )
        for number in (9999, 1)
    ]
    for manifest in manifests:
        catalog.materialize_portable(manifest=manifest)
    with CorpusPublicationCatalog(engine=engine).locked(
        deployment_id=source.deployment_id, forget_id=manifests[1].forget_id
    ) as held:
        held.check()
    with pytest.raises(ForgetInProgressError):
        profile.corpus_publisher().reconcile(deployment_id=source.deployment_id)


def test_equivalent_configured_cadences_share_one_delayed_verification(
    active, engine: Engine
) -> None:
    from rememberstack.spine.corpus_publication import CorpusPublicationCatalog

    _, source, _ = active
    catalog = CorpusPublicationCatalog(engine=engine)
    first = catalog.schedule_verification(
        deployment_id=source.deployment_id, interval_s=86400
    )
    duplicate = catalog.schedule_verification(
        deployment_id=source.deployment_id, interval_s=86400.0
    )
    assert first.created and not duplicate.created
    assert first.processing_id == duplicate.processing_id
    for invalid in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="positive"):
            catalog.schedule_verification(
                deployment_id=source.deployment_id, interval_s=invalid
            )
