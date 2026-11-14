"""Real PostgreSQL native cuts and MinIO live-tree capture classification.

Only the explicitly configured integration DB is used, sequentially. Provider
wire is synthetic: these tests do not qualify MinIO as a provider-at-T source.
"""

import base64
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import replace
import json
from pathlib import Path
from typing import Any
from typing import cast
from uuid import UUID
from uuid import uuid4

import psycopg
from psycopg import sql
from pydantic import SecretStr
from pydantic import TypeAdapter
import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.fsit_storage_capture import ProviderRequest
from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.adapters.selfhost import MinIOSettings
from rememberstack.core.corpus_export import corpus_suffix
from rememberstack.core.corpus_export import CorpusDocument
from rememberstack.core.corpus_export import CorpusPolicy
from rememberstack.core.corpus_export import DEFAULT_CORPUS_POLICY
from rememberstack.core.corpus_export import desired_export_revision
from rememberstack.core.corpus_export import json_bytes
from rememberstack.core.storage_capture import capture_request_id
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import ObjectKey
from rememberstack.model.storage_capture import StorageMember
from rememberstack.profiles.managed_storage_capture import _digest
from rememberstack.profiles.managed_storage_capture import (
    ManagedStorageCaptureUnavailable,
)
from rememberstack.profiles.managed_storage_capture import StorageCaptureCatalog
from rememberstack.profiles.storage_capture_profile import build_managed_storage_profile
from rememberstack.profiles.storage_capture_profile import ManagedFilesystemBinding
from rememberstack.profiles.storage_capture_profile import ManagedStorageCaptureProfile
from rememberstack.profiles.storage_capture_profile import ManagedStoreBindings
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine.corpus_export import CorpusReaderCatalog
from rememberstack.spine.corpus_publication import CorpusPublicationCatalog
from rememberstack.spine.storage_capture_catalog import install_native_capture_catalog
from rememberstack.spine.storage_capture_guard import install_native_relation_guard
from rememberstack.spine.storage_capture_view import NativeStorageCaptureCatalog
from rememberstack.workers.corpus_publication import CorpusPublisher
from tests.adapters.test_minio_object_operations import minio_settings as minio_settings
from tests.corpus_export_support import authority
from tests.corpus_export_support import digest
from tests.storage_capture_support import CaptureStore
from tests.storage_capture_support import DiskEvidence
from tests.storage_capture_support import FIXTURES
from tests.storage_capture_support import SyntheticProvider
from tests.workers.test_corpus_reader_export_pg import _seed
from tests.workers.test_corpus_reader_export_pg import engine as engine


@dataclass
class NativeRoles:
    engine: Engine
    collector: str

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self.engine.url.set(
                drivername="postgresql", username=self.collector
            ).render_as_string(hide_password=False),
            autocommit=True,
            options="-c transaction_timeout=10s -c statement_timeout=10s -c lock_timeout=250ms",
        )


@pytest.fixture(scope="module")
def native_roles(engine: Engine) -> Iterator[NativeRoles]:
    """Install actual restricted LOGIN helpers in the one shared test database."""
    suffix = uuid4().hex[:12]
    definer, collector = f"capture_definer_{suffix}", f"capture_collector_{suffix}"
    conninfo = engine.url.set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    with psycopg.connect(conninfo, autocommit=True) as admin:
        admin.execute("DROP SCHEMA IF EXISTS rememberstack_capture_internal CASCADE")
        admin.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER INHERIT").format(
                sql.Identifier(definer)
            )
        )
        admin.execute(
            sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOINHERIT PASSWORD {}").format(
                sql.Identifier(collector), sql.Literal(engine.url.password)
            )
        )
        owner = admin.execute(
            "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid='public.deployments'::regclass"
        ).fetchone()
        assert owner is not None
        admin.execute(
            sql.SQL("GRANT {} TO {} WITH INHERIT TRUE, SET FALSE").format(
                sql.Identifier(owner[0]), sql.Identifier(definer)
            )
        )
        admin.execute(
            sql.SQL(
                "GRANT pg_read_all_settings TO {} WITH INHERIT TRUE, SET FALSE"
            ).format(sql.Identifier(definer))
        )
        install_native_relation_guard(
            connection=admin, definer_role=definer, collector_role=collector
        )
        install_native_capture_catalog(
            connection=admin, definer_role=definer, collector_role=collector
        )
        admin.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(str(engine.url.database)), sql.Identifier(collector)
            )
        )
        # Repeated migration resets leave catalog churn for autovacuum. Finish
        # it before arming cuts: the real NOWAIT guard must refuse overlapping
        # maintenance, and a success-path fixture must not depend on its timing.
        admin.execute(
            "VACUUM (ANALYZE) pg_catalog.pg_class, pg_catalog.pg_namespace, "
            "pg_catalog.pg_proc, pg_catalog.pg_extension"
        )
    try:
        yield NativeRoles(engine, collector)
    finally:
        with psycopg.connect(conninfo, autocommit=True) as admin:
            admin.execute("DROP SCHEMA rememberstack_capture_internal CASCADE")
            for role in (collector, definer):
                admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


@dataclass
class LiveTree:
    roles: NativeRoles
    stores: dict[str, CaptureStore]
    documents: tuple[CorpusDocument, ...]
    profile: ManagedStorageCaptureProfile
    evidence: DiskEvidence

    def request(self):
        with self.roles.engine.connect() as connection:
            now = connection.execute(
                text("SELECT floor(extract(epoch FROM clock_timestamp()))::bigint")
            ).scalar_one()
        return self.profile.request(cut_at_unix_us=str((now + 4) * 1_000_000))

    def capture(self):
        request = self.request()
        provider = SyntheticProvider(
            request=ProviderRequest(
                cell_id=self.profile.bindings.provider_cell_id,
                cut_at_unix_us=request.cut_at_unix_us,
                filesystem_ids=tuple(
                    sorted(store.filesystem for store in self.stores.values())
                ),
                organisation_id=self.profile.bindings.provider_organisation_id,
                protocol="fsit-capture-v1",
            ),
            stores=self.stores,
        )
        catalog = StorageCaptureCatalog(
            collector_connect=self.roles.connect,
            provider=provider,
            stores=self.stores,
            evidence_store=self.evidence,
            profile=self.profile,
            identity_key=SecretStr("fixture-only"),
        )
        return catalog, request, provider


@pytest.fixture
def live_tree(
    native_roles: NativeRoles,
    minio_settings: MinIOSettings,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> Iterator[LiveTree]:
    """Publish four real document folders sharing immutable originals/artifacts."""
    roles = native_roles
    stores: dict[str, CaptureStore] = {}
    for role in ("artifacts", "corpus", "forget", "raw"):
        filesystem = base64.b32encode(uuid4().bytes).decode().rstrip("=").lower()
        store = MinIOObjectStore(bucket=filesystem, settings=minio_settings)
        store.ensure_bucket()
        stores[role] = CaptureStore(store, filesystem)
    source, artifacts, raw = authority()
    inventory = json.loads(artifacts.objects[source.conversion_uri])
    base = source.conversion_uri.rsplit("/", 1)[0]
    for entry in [inventory["source_map"], *inventory["derived_assets"]]:
        old = entry["uri"]
        entry["uri"] = base + (
            "/media/" + entry["name"] if "name" in entry else "/source_map.json"
        )
        artifacts.objects[entry["uri"]] = artifacts.objects.pop(old)
    for number in range(getattr(request, "param", 0)):
        name = f"pages/page-{number:04d}.png"
        content = f"page {number}".encode()
        uri = base + "/media/" + name
        inventory["derived_assets"].append(
            {
                "name": name,
                "kind": "page_image",
                "media_type": "image/png",
                "uri": uri,
                "sha256": digest(content),
            }
        )
        artifacts.objects[uri] = content
    artifacts.objects[source.conversion_uri] = json_bytes(inventory)
    source = replace(
        source, manifest_hash=digest(artifacts.objects[source.conversion_uri])
    )
    for name in (
        "private/blocks.json",
        "private/meta.json",
        "legacy/stale-pageindex.json",
    ):
        artifacts.objects[name] = b"{}"
    # Only referenced objects: arbitrary nearby E0 fixture keys are not part of
    # this complete store. Unknown unrelated keys have their own refusal test.
    names = {
        source.markdown_uri,
        source.conversion_uri,
        source.pageindex_uri,
        inventory["source_map"]["uri"],
        *(entry["uri"] for entry in inventory["derived_assets"]),
        "private/blocks.json",
        "private/meta.json",
        "legacy/stale-pageindex.json",
    }
    for name in names:
        stores["artifacts"].store.write_bytes(
            key=ObjectKey(name), content=artifacts.objects[name], storage_class="cold"
        )
    stores["raw"].store.write_bytes(
        key=ObjectKey(source.raw_uri),
        content=raw.objects[source.raw_uri],
        storage_class="cold",
    )
    engine = roles.engine
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE deployments CASCADE"))
    DeploymentBootstrapper(engine=engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=source.deployment_id,
            slug="live-capture",
            name="Live capture",
            default_language="en",
            raw_bucket="s3://" + stores["raw"].filesystem,
            artifacts_bucket="s3://" + stores["artifacts"].filesystem,
            corpusfs_bucket="s3://" + stores["corpus"].filesystem,
        ),
        sole_deployment=True,
    )
    for number in range(4):
        _seed(
            engine,
            replace(
                source,
                doc_id=UUID(int=number),
                version_id=UUID(int=2000 + number),
                representation_id=UUID(int=3000 + number),
                structure_generation_id=UUID(int=4000 + number),
                title=("Published", "Updating", "Forgotten", "Stale")[number],
                placement_path=None if number == 0 else "Finance/Reports",
            ),
        )
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE content_objects SET byte_size=:bytes"),
            {"bytes": len(raw.objects[source.raw_uri])},
        )
        # This historical operations row must never become a corpus locator.
        connection.execute(
            text("""INSERT INTO projection_snapshots
            (snapshot_id, deployment_id, plane, version, gcs_uri, status, is_latest)
            VALUES (:snapshot, :deployment, 'P3_corpusfs', 'retired',
                    'snapshots/P3/retired', 'published', true)"""),
            {"snapshot": uuid4(), "deployment": source.deployment_id},
        )
    readers = CorpusReaderCatalog(engine=engine)
    publisher = CorpusPublisher(
        catalog=CorpusPublicationCatalog(engine=engine),
        readers=readers,
        raw_store=stores["raw"].store,
        artifact_store=stores["artifacts"].store,
        corpus_store=stores["corpus"].store,
    )
    with readers.documents(deployment_id=source.deployment_id) as rows:
        documents = tuple(rows)
    for document in documents:
        assert publisher.publish_corpus(
            deployment_id=source.deployment_id, doc_id=document.doc_id
        )
    with readers.documents(deployment_id=source.deployment_id) as rows:
        documents = tuple(rows)
    native = ManagedStorageCaptureProfile.model_validate_json(
        json.dumps(
            json.loads((FIXTURES / "live_corpus_profile.json").read_bytes())["profile"]
        )
    ).native
    profile = build_managed_storage_profile(
        native=native,
        bindings=ManagedStoreBindings(
            deployment_id=source.deployment_id.hex,
            postgres_database_name=str(engine.url.database),
            provider_organisation_id="1" * 32,
            provider_cell_id="cell-1",
            filesystems=tuple(
                ManagedFilesystemBinding(
                    role=role, filesystem_id=stores[role].filesystem
                )
                for role in ("artifacts", "corpus", "forget", "raw")
            ),
        ),
        managed_runtime_source_digest="6" * 64,
        provider_profile_digest="a" * 64,
        provider_clock_profile_digest="b" * 64,
    )
    try:
        yield LiveTree(
            roles, stores, documents, profile, DiskEvidence(tmp_path / "evidence")
        )
    finally:
        for store in stores.values():
            store.store.purge_objects(
                keys=tuple(store.store.iter_keys(prefix="")), prefixes=()
            )
            client = cast(Any, store.store._client)
            client.delete_bucket(Bucket=store.filesystem)
            client.close()


def _put(state: LiveTree, name: str) -> None:
    state.stores["corpus"].store.replace_stream(
        key=ObjectKey(name), chunks=(b"retained copy",), storage_class="hot"
    )


def test_live_tree_classes_and_separate_original_quantities(
    live_tree: LiveTree,
) -> None:
    state = live_tree
    published, updating, forgotten, stale = state.documents
    with state.roles.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE documents SET corpus_published_revision=NULL WHERE doc_id=:id"
            ),
            {"id": updating.doc_id},
        )
        # Simulate retained provider copies after a forget/restore: no live
        # served reading or friendly cleanup path survives in DB authority.
        connection.execute(
            text(
                "UPDATE documents SET deleted_at=now(), current_version_id=NULL, corpus_paths=NULL WHERE doc_id=:id"
            ),
            {"id": forgotten.doc_id},
        )
        # A non-null old marker must fail even if invalidation was omitted.
        connection.execute(
            text(
                "UPDATE document_metadata SET title='Changed inputs' WHERE version_id=:id"
            ),
            {"id": stale.version_id},
        )
    orphan = "pdf/unrecorded--" + corpus_suffix(uuid4()) + "/original.pdf"
    _put(state, orphan)
    extra = published.path + "/unrecognized.bin"
    _put(state, extra)
    _put(state, "pdf/~/ab/~/abc/_index.md")
    catalog, request, provider = state.capture()
    result = catalog.capture_once(request=request)
    assert provider.completed
    members = [
        member
        for body in result.iter_page_bytes()
        for member in json.loads(body)["members"]
    ]
    typed = [TypeAdapter(StorageMember).validate_python(member) for member in members]
    observations = {
        member.resource_evidence_digest: member.purpose
        for member in typed
        if member.kind == "resource_observation"
    }
    expected_p3 = 0
    for page in provider.pages:
        for item in page.items:
            role, name = next(
                key for key, path in provider.keys.items() if path == item.path_id
            )
            purpose = observations[
                _digest(domain="rememberstack.storage_provider_item.v1", record=item)
            ]
            if role == "corpus":
                excluded = (
                    name == orphan
                    or name == extra
                    or any(
                        name.startswith(document.path + "/")
                        for document in (updating, forgotten, stale)
                    )
                )
                assert purpose == ("staging" if excluded else "projection_origin"), name
                if not excluded:
                    expected_p3 += int(item.logical_bytes)
                assert (
                    state.stores[role].store.storage_class_of(key=ObjectKey(name))
                    == "hot"
                )
            else:
                assert purpose == (
                    "canonical_raw" if role == "raw" else "referenced_artifact"
                )
    totals = {
        record.component: int(record.measured_bytes)
        for record in result.manifest.component_records
    }
    assert totals["p3_origin"] == expected_p3
    expected_canonical = sum(
        int(item.logical_bytes)
        for page in provider.pages
        for item in page.items
        if item.filesystem_id != state.stores["corpus"].filesystem
    )
    assert totals["canonical_object"] == expected_canonical
    raw = [
        member
        for member in typed
        if member.kind == "reference" and member.purpose == "canonical_raw"
    ]
    assert len(raw) == 1  # four lineages share one actual canonical original
    original_path = provider.keys[("corpus", published.path + "/original.pdf")]
    copies = [
        member
        for member in typed
        if member.kind == "reference" and member.resource_identity == original_path
    ]
    assert len(copies) == 1 and copies[0].component == "p3_origin"
    assert (
        copies[0].purpose == "projection_origin"
        and copies[0].logical_bytes == raw[0].logical_bytes
    )
    assert copies[0].opaque_id == catalog._native_opaque(
        native_class="documents.corpus_original",
        native_id=f"{published.doc_id}:{published.corpus_published_revision}:original.pdf",
    )
    for name, native_class in (
        ("document.md", "documents.corpus_derivative"),
        ("_index.md", "corpus.navigation"),
    ):
        path = provider.keys[("corpus", published.path + "/" + name)]
        reference = next(
            member
            for member in typed
            if member.kind == "reference" and member.resource_identity == path
        )
        assert reference.opaque_id == catalog._native_opaque(
            native_class=native_class,
            native_id=f"{published.doc_id}:{published.corpus_published_revision}:{name}",
        )


@pytest.mark.parametrize(
    "live_tree", [DEFAULT_CORPUS_POLICY.member_limit], indirect=True
)
def test_published_document_subindexes_are_navigation(live_tree: LiveTree) -> None:
    state = live_tree
    document = state.documents[0]
    catalog, request, provider = state.capture()
    result = catalog.capture_once(request=request)
    members = [
        TypeAdapter(StorageMember).validate_python(member)
        for body in result.iter_page_bytes()
        for member in json.loads(body)["members"]
    ]
    references = {
        member.resource_identity: member
        for member in members
        if member.kind == "reference" and member.store_binding_id == "corpus"
    }
    observations = {
        member.resource_evidence_digest: member.purpose
        for member in members
        if member.kind == "resource_observation"
    }
    paths = {
        name: path
        for (role, name), path in provider.keys.items()
        if role == "corpus" and name.startswith(document.path + "/")
    }
    assert len(paths) > DEFAULT_CORPUS_POLICY.member_limit
    indexes = {
        name.removeprefix(document.path + "/"): path
        for name, path in paths.items()
        if name.endswith("/_index.md")
    }
    assert "_index.md" in indexes and len(indexes) > 1
    assert any(name.startswith("~/") for name in indexes)
    for name, path in indexes.items():
        reference = references[path]
        assert reference.opaque_id == catalog._native_opaque(
            native_class="corpus.navigation",
            native_id=f"{document.doc_id}:{document.corpus_published_revision}:{name}",
        )
        assert reference.purpose == "projection_origin"
        assert reference.component == "p3_origin"
    for page in provider.pages:
        for item in page.items:
            if item.path_id in paths.values():
                assert (
                    observations[
                        _digest(
                            domain="rememberstack.storage_provider_item.v1", record=item
                        )
                    ]
                    == "projection_origin"
                )


@pytest.mark.parametrize(
    "key",
    [
        "unknown",
        "llms.txt.bak",
        "_paths/doc.json",
        "pdf/not-a-document/document.md",
        "snapshots/P3/latest/MANIFEST.json",
    ],
)
def test_unknown_corpus_keys_fail_closed(live_tree: LiveTree, key: str) -> None:
    _put(live_tree, key)
    catalog, request, _ = live_tree.capture()
    with pytest.raises(
        ManagedStorageCaptureUnavailable, match="capture_writer_class_unknown"
    ):
        catalog.capture_once(request=request)
    assert (
        live_tree.evidence.read_manifest(capture_id=capture_request_id(request=request))
        is None
    )


def test_missing_published_copy_never_completes(live_tree: LiveTree) -> None:
    state = live_tree
    state.stores["corpus"].store.delete(
        key=ObjectKey(state.documents[0].path + "/original.pdf")
    )
    catalog, request, _ = state.capture()
    with pytest.raises(
        ManagedStorageCaptureUnavailable, match="capture_native_reference_unmatched"
    ):
        catalog.capture_once(request=request)
    assert (
        state.evidence.read_manifest(capture_id=capture_request_id(request=request))
        is None
    )


@pytest.mark.parametrize("case", ["unrecorded", "cleanup_incomplete", "old_policy"])
def test_marker_and_recorded_folder_must_both_match(
    live_tree: LiveTree, case: str
) -> None:
    state = live_tree
    document = state.documents[0]
    with state.roles.engine.begin() as connection:
        if case == "old_policy":
            connection.execute(
                text(
                    "UPDATE documents SET corpus_published_revision=:revision WHERE doc_id=:id"
                ),
                {
                    "id": document.doc_id,
                    "revision": desired_export_revision(
                        document, policy=CorpusPolicy(builder_version="retired-policy")
                    ),
                },
            )
        else:
            paths = (
                None
                if case == "unrecorded"
                else [document.path, "pdf/old--" + corpus_suffix(document.doc_id)]
            )
            connection.execute(
                text("UPDATE documents SET corpus_paths=:paths WHERE doc_id=:id"),
                {"id": document.doc_id, "paths": paths},
            )
    catalog, request, provider = state.capture()
    result = catalog.capture_once(request=request)
    observations = {
        member["resource_evidence_digest"]: member["purpose"]
        for body in result.iter_page_bytes()
        for member in json.loads(body)["members"]
        if member["kind"] == "resource_observation"
    }
    paths = {
        path
        for (role, key), path in provider.keys.items()
        if role == "corpus" and key.startswith(document.path + "/")
    }
    assert paths
    for page in provider.pages:
        for item in page.items:
            if item.path_id in paths:
                assert (
                    observations[
                        _digest(
                            domain="rememberstack.storage_provider_item.v1", record=item
                        )
                    ]
                    == "staging"
                )


def test_capture_helpers_refuse_any_other_schema_head(live_tree: LiveTree) -> None:
    state = live_tree
    with state.roles.engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='p9_52_0073'"))
    try:
        with state.roles.connect() as collector:
            collector.execute("BEGIN")
            with pytest.raises(
                psycopg.errors.ObjectNotInPrerequisiteState, match="native_guard_schema"
            ):
                collector.execute(
                    "SELECT rememberstack_capture_internal.guard_relation_allocation(floor(extract(epoch FROM clock_timestamp())*1000000)::bigint+9000000)"
                )
            collector.execute("ROLLBACK")
            collector.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            with pytest.raises(
                psycopg.errors.ObjectNotInPrerequisiteState,
                match="native_catalog_scope",
            ):
                collector.execute(
                    "SELECT rememberstack_capture_internal.read_capture_catalog('bind','','',1)"
                )
            collector.execute("ROLLBACK")
    finally:
        with state.roles.engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num='p9_53_0074'")
            )


def test_corpus_catalog_keeps_original_view_across_pages(
    live_tree: LiveTree, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = live_tree
    # Small pages exercise real SQL keyset cursors, not just the first page.
    monkeypatch.setattr(
        "rememberstack.spine.storage_capture_view.CATALOG_PAGE_LIMIT", 1
    )
    with NativeStorageCaptureCatalog(
        connect=state.roles.connect, profile=state.profile.native
    ).open_view(request=state.request()) as native:
        rows = native.iter_corpus_documents()
        first = next(rows)
        with state.roles.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE documents SET corpus_published_revision=NULL, corpus_paths=NULL"
                )
            )
        captured = (first, *rows)
        assert tuple(row.doc_id for row in captured) == tuple(
            document.doc_id for document in state.documents
        )
        assert all(
            row.paths == (document.path,)
            and row.document
            and row.document.corpus_published_revision
            == document.corpus_published_revision
            for row, document in zip(captured, state.documents, strict=True)
        )
        assert all(
            seed.store_role != "corpus" for seed in native.iter_store_references()
        )
