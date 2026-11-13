"""D155 consumer paths across real PostgreSQL, MinIO, HTTP, SDK and MCP."""

from dataclasses import replace
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

from fastapi.testclient import TestClient
from markdown_it import MarkdownIt
import pytest
from sqlalchemy import text

from remember import MemoryClient
from remember.mcp_engine import EngineMcpServer
from remember.models import DocumentSearchFilters
from remember.models import EffectivePeriodInput
from remember.models import ReadinessRequirements
from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.model import ObjectKey
from rememberstack.profiles import SelfHostProfile
from rememberstack.profiles import SelfHostSettings
from rememberstack.spine import DocumentCatalog
from rememberstack.workers.corpus_publication import document_folder
from tests.adapters.test_minio_corpus_publication import bucket as bucket
from tests.adapters.test_minio_corpus_publication import corpus as corpus
from tests.adapters.test_minio_corpus_publication import engine as engine
from tests.adapters.test_minio_corpus_publication import (
    minio_settings as minio_settings,
)
from tests.adapters.test_minio_corpus_publication import Publication
from tests.adapters.test_minio_corpus_publication import publication as publication
from tests.workers.test_corpus_reader_export_pg import _seed as seed_reading


def _names(database, source):
    with database.begin() as connection:
        connection.execute(
            text("""INSERT INTO document_names
            (deployment_id, version_id, observed_at, file_name, name_text, origin)
            VALUES (:d, :v, clock_timestamp(), 'report.pdf', 'report.pdf', 'ingest')"""),
            {
                "id": uuid4(),
                "d": source.deployment_id,
                "doc": source.doc_id,
                "v": source.version_id,
            },
        )


def _seed(database, source):
    seed_reading(database, source)
    _names(database, source)


@pytest.fixture
def consumer(publication: Publication, tmp_path: Path):
    state = publication
    _names(state.engine, state.source)
    manifests = tmp_path / "intent"
    manifests.mkdir()
    profile = SelfHostProfile(
        settings=SelfHostSettings(
            deployment_id=state.source.deployment_id, forget_manifest_root=manifests
        ),
        engine=state.engine,
        raw_store=state.raw,
        artifact_store=state.artifacts,
        corpusfs_store=state.bucket.store,
        model_provider=FakeModelProvider(),
        model_provider_settings=OpenRouterSettings(api_key="unused"),
    )
    with TestClient(profile.api()) as http:
        yield MemoryClient(client=http), profile


def _result(client: MemoryClient, state: Publication, **kwargs: Any):
    return client.search_documents(
        filters=DocumentSearchFilters(doc_ids=(state.source.doc_id,)), **kwargs
    ).documents


def _ready(client: MemoryClient, *versions):
    return client.pipeline_readiness(
        version_ids=versions,
        require=ReadinessRequirements(
            pipeline=False, p1=False, live_graph=False, p3=True
        ),
    )


@pytest.mark.parametrize("boundary", ["original", "ready", "navigation"])
def test_path_and_readiness_remain_null_during_and_after_failure(
    publication: Publication, consumer, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    state = publication
    client, _ = consumer
    assert _result(client, state)[0].p3_path is None
    assert not _ready(client, state.source.version_id).ready
    assert state.publish()
    assert _result(client, state)[0].p3_path == state.source.path
    assert _ready(client, state.source.version_id).ready
    state.change(title="Moved title")
    target = state.selected()[0].path
    store = state.bucket.store
    original = store.replace_stream

    def fail(*, key: ObjectKey, **kwargs: Any):
        trigger = {
            "original": target + "/original.pdf",
            "ready": target + "/_index.md",
            "navigation": "pdf/finance/annual-reports/_index.md",
        }[boundary]
        if key.root == trigger:
            row = _result(client, state)[0]
            assert row.served_version and row.p3_path is None
            assert not _ready(client, state.source.version_id).ready
            raise RuntimeError("injected consumer publication failure")
        return original(key=key, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "replace_stream", fail)
        with pytest.raises(RuntimeError, match="injected consumer"):
            state.publish()
    assert _result(client, state)[0].p3_path is None
    assert not _ready(client, state.source.version_id).ready
    assert state.publish()
    assert _result(client, state)[0].p3_path == target
    report = _ready(client, state.source.version_id)
    assert report.ready and report.capabilities["p3"].version == state.marker()
    assert report.capabilities["p3"].built_at is None
    assert report.capabilities["p3"].published_at is None
    server = EngineMcpServer(client=client, read_only=True, path_ingest=False)
    response = server.call_tool(
        name="search_documents", arguments={"doc_ids": [str(state.source.doc_id)]}
    )
    assert not response["isError"]
    assert (
        json.loads(response["content"][0]["text"])["documents"][0]["p3_path"] == target
    )


def test_pending_raw_only_history_and_unrelated_publication(
    publication: Publication, consumer
) -> None:
    state = publication
    client, _ = consumer
    state.publish()
    second = replace(
        state.source,
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        version_no=2,
        title="Pending v2",
    )
    _seed(state.engine, second)
    with state.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET current_version_id=:v WHERE doc_id=:doc"),
            {"v": state.source.version_id, "doc": state.source.doc_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET current_representation_id=NULL, status='ingesting' WHERE version_id=:v"
            ),
            {"v": second.version_id},
        )
    # Ranked current selects served v1; paged newest/all can describe raw v2.
    assert _result(client, state, query="report")[0].p3_path == state.source.path
    pending = _result(client, state, versions="all")[0]
    assert pending.version_id == second.version_id and pending.p3_path is None
    assert not pending.served_version
    assert _ready(client, state.source.version_id).ready
    assert not _ready(client, second.version_id).ready
    assert not _ready(client, state.source.version_id, second.version_id).ready
    with state.engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET current_version_id=:v WHERE doc_id=:doc"),
            {"v": second.version_id, "doc": state.source.doc_id},
        )
    assert _result(client, state)[0].p3_path is None
    assert not _ready(client, second.version_id).ready
    control = replace(
        state.source,
        doc_id=uuid4(),
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
    )
    _seed(state.engine, control)
    assert state.publish(control.doc_id)
    assert _ready(client, control.version_id).ready
    assert not _ready(client, second.version_id).ready
    assert not _ready(client, control.version_id, second.version_id).ready
    assert (
        _ready(client, control.version_id, second.version_id).capabilities["p3"].version
        is None
    )


def test_periodised_history_and_no_edition_preserve_exact_source_handles(
    publication: Publication, consumer
) -> None:
    state = publication
    client, _ = consumer
    old = state.source
    current = replace(
        old,
        version_id=uuid4(),
        representation_id=uuid4(),
        structure_generation_id=uuid4(),
        version_no=2,
        title="Current edition",
    )
    _seed(state.engine, current)
    client.set_effective_periods(
        doc_id=old.doc_id,
        version_id=old.version_id,
        periods=(
            EffectivePeriodInput(
                effective_from=datetime(2020, 1, 1, tzinfo=UTC),
                effective_until=datetime(2021, 1, 1, tzinfo=UTC),
            ),
        ),
    )
    client.set_effective_periods(
        doc_id=old.doc_id,
        version_id=current.version_id,
        periods=(
            EffectivePeriodInput(effective_from=datetime(2025, 1, 1, tzinfo=UTC)),
        ),
    )
    state.publish()
    for query in (None, "report"):
        historical = _result(
            client,
            state,
            query=query,
            time={"mode": "at", "at": "2020-06-01T00:00:00Z"},
        )[0]
        assert historical.version_id == old.version_id
        assert not historical.served_version and historical.p3_path is None
        assert historical.representation_id == old.representation_id
        assert (
            _result(
                client,
                state,
                query=query,
                time={"mode": "at", "at": "2022-01-01T00:00:00Z"},
            )
            == ()
        )
        served = _result(
            client, state, query=query, versions="all", time={"mode": "history"}
        )[0]
        assert (
            served.version_id == current.version_id and served.p3_path == current.path
        )
    assert (
        _ready(client, old.version_id).capabilities["p3"].reason
        == "p3_version_not_served"
    )
    # source_open is the design's name for these exact version/representation
    # handles. There is no public source_open RPC; exercise their E0 authority.
    catalog = DocumentCatalog(engine=state.engine)
    assert catalog.convert_source(version_id=old.version_id).raw_uri == old.raw_uri
    assert (
        catalog.structure_source(representation_id=old.representation_id).markdown_uri
        == old.markdown_uri
    )
    assert state.raw.read_bytes(key=ObjectKey(old.raw_uri)) == state.raw.read_bytes(
        key=ObjectKey(current.raw_uri)
    )
    client.delete_document(doc_id=old.doc_id)
    assert _result(client, state, versions="all", time={"mode": "history"}) == ()
    assert not _ready(client, current.version_id).ready


@pytest.mark.parametrize("claims,facts", [(True, True), (True, False), (False, False)])
def test_navigation_to_originals_and_derivatives_and_suffix_moves(
    publication: Publication, consumer, tmp_path: Path, claims: bool, facts: bool
) -> None:
    from rememberstack.spine.processing_layers import ProcessingLayerCatalog

    state = publication
    client, _ = consumer
    ProcessingLayerCatalog(engine=state.engine).reconcile_settings(
        deployment_id=state.source.deployment_id,
        claims_enabled=claims,
        facts_enabled=facts,
    )
    state.publish()
    root = tmp_path / "mount"

    def download():
        for key in state.bucket.store.iter_keys(prefix=""):
            target = root / key.root
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(state.bucket.store.read_bytes(key=key))

    download()
    assert "llms.txt" in {path.name for path in root.iterdir()}
    # Follow actual rendered links, without knowing the document's location.
    visited: set[Path] = set()

    def walk(path: Path):
        if path in visited:
            return
        visited.add(path)
        if path.suffix != ".md" and path.name != "llms.txt":
            return
        for token in MarkdownIt().parse(path.read_text()):
            for child in token.children or ():
                if child.type == "link_open":
                    href = child.attrGet("href")
                    assert href
                    target = (path.parent / unquote(href)).resolve()
                    assert target.is_relative_to(root.resolve()) and target.exists()
                    walk(target)

    walk(root / "llms.txt")
    folder = root / state.source.path
    for name in (
        "original.pdf",
        "document.md",
        "pageindex.json",
        "source_map.json",
        "media/nested/plot.png",
    ):
        assert (folder / name).resolve() in visited
    suffix = corpus_suffix(state.source.doc_id)
    assert subprocess.check_output(
        ["find", str(root), "-type", "d", "-name", f"*--{suffix}"], text=True
    ).strip() == str(folder)
    state.change(title="Renamed", topic="Moved/topic", source_kind="email")
    state.publish()
    # A real mount observes removal; refresh the downloaded view accordingly.

    shutil.rmtree(root)
    download()
    moved = root / _result(client, state)[0].p3_path
    assert moved != folder and moved.name.endswith("--" + suffix)
    assert subprocess.check_output(
        ["find", str(root), "-name", f"*--{suffix}"], text=True
    ).strip() == str(moved)
    # A similarly named media directory is beneath the first document folder.
    impostor = (
        str(moved.relative_to(root))
        + f"/media/fake--{corpus_suffix(uuid4())}/asset.png"
    )
    assert document_folder(impostor) == (str(moved.relative_to(root)), suffix)
    with state.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE documents SET title=NULL, source_ref=NULL, source_uri=NULL WHERE doc_id=:doc"
            ),
            {"doc": state.source.doc_id},
        )
    assert corpus_suffix(state.source.doc_id) == suffix
    assert subprocess.check_output(
        ["find", str(root), "-name", f"*--{suffix}"], text=True
    ).strip() == str(moved)
