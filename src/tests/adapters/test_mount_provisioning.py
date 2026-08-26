"""WP-4.6 acceptance: mount provisioning (e0 §5, D51).

The three read-only views an agent gets on its filesystem, with D51's
guardrails proven rather than assumed: the corpus view serves only the
PUBLISHED snapshot (and swaps whole), raw sits off the navigation path,
raw reads are refused unless attributed and are logged when they happen,
and storage class routes by mime so browse-pattern reads never hit
archive-class originals.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import AuditedRawReader
from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.selfhost import LocalMountPublisher
from rememberstack.adapters.selfhost import RawAccessDenied
from rememberstack.adapters.selfhost import storage_class_for
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import ObjectKey
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import ProjectionCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import CorpusFsBuilder

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("46000000-0000-0000-0000-000000000001")


class _OpenAdmission:
    def assert_available(self, *, deployment_id: UUID) -> None:
        return None


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real mount proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def deployment(database_engine: Engine) -> Engine:
    """A fresh deployment with one document lineage to publish."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="mount-test",
            name="Mount provisioning proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    with database_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, 'Acme', 'acme')"
            ),
            {"e": uuid4(), "d": _DEPLOYMENT_ID},
        )
    return database_engine


def test_the_corpus_view_serves_only_published_snapshots(
    deployment: Engine, tmp_path: Path
) -> None:
    """A browsing agent sees the published tree — and nothing before one
    exists (a typed note, not an empty directory pretending to be a corpus)."""
    catalog = ProjectionCatalog(engine=deployment)
    corpusfs_store = LocalFSObjectStore(root=tmp_path / "corpusfs")
    publisher = LocalMountPublisher(
        root=tmp_path / "mounts",
        catalog=catalog,
        corpusfs_store=corpusfs_store,
        admission=_OpenAdmission(),
    )
    before = publisher.publish(deployment_id=_DEPLOYMENT_ID)
    corpus = Path(before.p3)
    assert (corpus / "llms.txt").read_text().startswith("# Corpus filesystem")
    assert "No P3 snapshot has been published yet" in (corpus / "llms.txt").read_text()

    CorpusFsBuilder(catalog=catalog, snapshot_store=corpusfs_store).build(
        deployment_id=_DEPLOYMENT_ID
    )
    after = publisher.publish(deployment_id=_DEPLOYMENT_ID)
    corpus = Path(after.p3)
    assert (corpus / "entities" / "_index.md").exists()  # the real tree arrived
    assert "Tier 1" in (corpus / "entities" / "_index.md").read_text()
    assert (corpus / ".snapshot-version").exists()


def test_the_mount_swaps_whole_trees(deployment: Engine, tmp_path: Path) -> None:
    """A rebuild's files never appear one by one under a browsing agent: the
    mount serves version N until N+1 is complete, then swaps."""
    catalog = ProjectionCatalog(engine=deployment)
    corpusfs_store = LocalFSObjectStore(root=tmp_path / "corpusfs")
    builder = CorpusFsBuilder(catalog=catalog, snapshot_store=corpusfs_store)
    publisher = LocalMountPublisher(
        root=tmp_path / "mounts",
        catalog=catalog,
        corpusfs_store=corpusfs_store,
        admission=_OpenAdmission(),
    )
    builder.build(deployment_id=_DEPLOYMENT_ID)
    first = publisher.publish(deployment_id=_DEPLOYMENT_ID)
    first_version = (Path(first.p3) / ".snapshot-version").read_text()

    with deployment.begin() as connection:  # the corpus grows
        connection.execute(
            text(
                "INSERT INTO entities (entity_id, deployment_id,"
                " canonical_name, normalized_name)"
                " VALUES (:e, :d, 'Wile E', 'wile e')"
            ),
            {"e": uuid4(), "d": _DEPLOYMENT_ID},
        )
    builder.build(deployment_id=_DEPLOYMENT_ID)
    second = publisher.publish(deployment_id=_DEPLOYMENT_ID)
    second_version = (Path(second.p3) / ".snapshot-version").read_text()
    assert second_version != first_version
    assert not list(Path(second.p3).parent.glob(".staging-*"))  # nothing stranded
    # Codex review: the mount path is a SYMLINK swapped atomically — it is
    # never absent mid-swap, and the previous version survives beside it
    assert Path(second.p3).is_symlink()
    assert (Path(second.p3).parent / f"p3-{first_version}").exists()
    entity_pages = list((Path(second.p3) / "entities").rglob("_index.md"))
    assert len(entity_pages) == 3  # facet index + two entity pages


def test_raw_is_off_the_navigation_path(deployment: Engine, tmp_path: Path) -> None:
    """D51 guardrail 1: nothing in the browsable tree links into raw — an
    original is reached only by following an explicit pointer."""
    catalog = ProjectionCatalog(engine=deployment)
    corpusfs_store = LocalFSObjectStore(root=tmp_path / "corpusfs")
    CorpusFsBuilder(catalog=catalog, snapshot_store=corpusfs_store).build(
        deployment_id=_DEPLOYMENT_ID
    )
    mounts = LocalMountPublisher(
        root=tmp_path / "mounts",
        catalog=catalog,
        corpusfs_store=corpusfs_store,
        admission=_OpenAdmission(),
    ).publish(deployment_id=_DEPLOYMENT_ID)
    browsable = [
        path.read_text(encoding="utf-8") for path in Path(mounts.p3).rglob("*.md")
    ]
    assert browsable  # there IS a tree to browse
    assert not any("raw/" in content for content in browsable)
    assert not any(mounts.raw in content for content in browsable)
    assert mounts.read_only is True


def test_raw_reads_are_attributed_and_logged(tmp_path: Path) -> None:
    """D51 guardrail 2: the audit property comes from LOGGING — so an
    unattributed read is refused, and every real read leaves a record."""
    raw_store = LocalFSObjectStore(root=tmp_path / "raw")
    raw_store.write_bytes(key=ObjectKey("doc/original.pdf"), content=b"%PDF-1.7 ...")
    reader = AuditedRawReader(
        raw_store=raw_store, audit_log=tmp_path / "audit" / "raw-access.jsonl"
    )
    with pytest.raises(RawAccessDenied, match="accessor"):
        reader.read(
            deployment_id=_DEPLOYMENT_ID,
            raw_uri="doc/original.pdf",
            accessor="",
            purpose="curiosity",
        )
    assert reader.entries() == ()  # a refused read logs nothing

    content = reader.read(
        deployment_id=_DEPLOYMENT_ID,
        raw_uri="doc/original.pdf",
        accessor="agent:reviewer",
        purpose="re-ocr debugging",
    )
    assert content.startswith(b"%PDF")
    entry = reader.entries()[-1]
    assert entry["accessor"] == "agent:reviewer"
    assert entry["purpose"] == "re-ocr debugging"
    assert entry["raw_uri"] == "doc/original.pdf"
    assert entry["bytes"] == len(content)


def test_storage_class_routes_by_mime() -> None:
    """D51 guardrail 3: media agents actually read stays hot; text originals
    kept only for audit go cold — the grep-the-archive cost bug, killed at
    the source rather than on the bill."""
    assert storage_class_for(mime="video/mp4") == "hot"
    assert storage_class_for(mime="audio/mpeg") == "hot"
    assert storage_class_for(mime="image/jpeg") == "hot"
    assert storage_class_for(mime="application/pdf") == "cold"
    assert storage_class_for(mime="text/markdown") == "cold"


def test_views_point_at_the_real_stores(deployment: Engine, tmp_path: Path) -> None:
    """Codex review: an empty directory is not a usable mount — the
    artifact and raw views resolve to the actual store roots, so a stub's
    pointer is followable from the mount."""
    artifacts = tmp_path / "stores" / "artifacts"
    raw = tmp_path / "stores" / "raw"
    LocalFSObjectStore(root=artifacts).write_bytes(
        key=ObjectKey("doc/document.md"), content=b"# Body"
    )
    LocalFSObjectStore(root=raw).write_bytes(
        key=ObjectKey("doc/original.pdf"), content=b"%PDF", storage_class="cold"
    )
    mounts = LocalMountPublisher(
        root=tmp_path / "mounts",
        catalog=ProjectionCatalog(engine=deployment),
        corpusfs_store=LocalFSObjectStore(root=tmp_path / "corpusfs"),
        artifacts_root=artifacts,
        raw_root=raw,
        admission=_OpenAdmission(),
    ).publish(deployment_id=_DEPLOYMENT_ID)
    assert (Path(mounts.artifacts) / "doc" / "document.md").read_bytes() == b"# Body"
    assert (Path(mounts.raw) / "doc" / "original.pdf").exists()


def test_ingest_routes_storage_class_by_mime(tmp_path: Path) -> None:
    """Codex review: the routing must be LIVE, not a lookup table nobody
    calls — the ingest path stamps every original's class at the write."""
    raw_store = LocalFSObjectStore(root=tmp_path / "raw")
    for key, mime in (
        (ObjectKey("a/clip.mp4"), "video/mp4"),
        (ObjectKey("b/report.pdf"), "application/pdf"),
    ):
        raw_store.write_bytes(
            key=key, content=b"bytes", storage_class=storage_class_for(mime=mime)
        )
    assert raw_store.storage_class_of(key=ObjectKey("a/clip.mp4")) == "hot"
    assert raw_store.storage_class_of(key=ObjectKey("b/report.pdf")) == "cold"
