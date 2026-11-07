"""D147: register a raw object a client uploaded directly, as ingest would.

The client writes the file to the raw store itself (multipart, by pre-signed
URL); the engine streams it from that key, hashes and classifies it, and
records the version through the ordinary ingest admission, versioning and
deduplication. The raw object stays at the client's key.
"""

from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.core.storage_routing import COLD
from rememberstack.model import ContentDetectionError
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import ObjectKey
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import RunResultOutcome
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine import WorkLedger
from rememberstack.spine import WorkLedgerSettings
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ConvertHandler
from rememberstack.workers import HandlerRegistry
from rememberstack.workers import UploadIngestor
from rememberstack.workers import Worker
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("d1470000-0000-0000-0000-0000000000a1")
_BODY = b"# Directly uploaded\n\nThe client wrote these bytes itself.\n"


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Structural head on the configured integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for ingest proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def deployment(database_engine: Engine) -> None:
    """A fresh deployment per proof."""
    with database_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="stored-ingest",
            name="Stored ingest proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


def _ingestor(*, engine: Engine, raw: LocalFSObjectStore) -> UploadIngestor:
    return UploadIngestor(
        catalog=DocumentCatalog(engine=engine),
        raw_store=raw,
        admission=ForgetCatalog(engine=engine),
        routable_mimes=frozenset({"text/markdown", "text/plain"}),
    )


def _put(raw: LocalFSObjectStore, key: str, content: bytes) -> ObjectKey:
    """What a client's direct upload leaves behind in the raw store."""
    object_key = ObjectKey(key)
    raw.write_bytes(key=object_key, content=content, storage_class=COLD)
    return object_key


def test_a_stored_object_is_registered_and_converted_where_it_lies(
    database_engine: Engine, tmp_path: Path
) -> None:
    raw = LocalFSObjectStore(root=tmp_path / "raw")
    artifacts = LocalFSObjectStore(root=tmp_path / "artifacts")
    ingestor = _ingestor(engine=database_engine, raw=raw)
    key = _put(raw, "uploads/7f3a0c2b9d4e6f81a2b3c4d5", _BODY)

    version = ingestor.ingest_stored(
        deployment_id=_DEPLOYMENT_ID,
        key=key,
        size=len(_BODY),
        mime="text/markdown",
        filename="notes.md",
    )
    assert version.created is True
    assert version.mime == "text/markdown"
    with database_engine.connect() as connection:
        assert connection.execute(
            text("SELECT raw_uri, byte_size FROM content_objects")
        ).one() == ("uploads/7f3a0c2b9d4e6f81a2b3c4d5", len(_BODY))

    # The same bytes through the ordinary path are the same version (D55).
    again = ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=DocumentUpload(filename="notes.md", mime="text/markdown", content=_BODY),
    )
    assert (again.doc_id, again.version_id, again.created) == (
        version.doc_id,
        version.version_id,
        False,
    )
    # Registering the same object again is the same no-op.
    assert (
        ingestor.ingest_stored(
            deployment_id=_DEPLOYMENT_ID,
            key=key,
            size=len(_BODY),
            mime="text/markdown",
            filename="notes.md",
        ).created
        is False
    )

    # Conversion reads the raw object from the client's key.
    registry = HandlerRegistry()
    registry.register(
        stage=PipelineStage.CONVERT,
        handler=ConvertHandler(
            catalog=DocumentCatalog(engine=database_engine),
            raw_store=raw,
            artifact_store=artifacts,
            router=ConversionRouter(
                routes={"text/markdown": MarkdownPassthroughConverter()}
            ),
        ),
    )
    worker = Worker(
        ledger=WorkLedger(engine=database_engine, settings=WorkLedgerSettings()),
        registry=registry,
    )
    result = worker.run_one(
        deployment_id=_DEPLOYMENT_ID,
        stage=PipelineStage.CONVERT,
        lane=ProcessingLane.STEADY,
    )
    assert result.outcome is RunResultOutcome.SUCCEEDED
    stored = sorted(
        path.read_bytes() for path in (tmp_path / "artifacts").rglob("document.md")
    )
    assert stored == [_BODY]


def test_a_stored_object_meets_the_same_checks_as_an_upload(
    database_engine: Engine, tmp_path: Path
) -> None:
    raw = LocalFSObjectStore(root=tmp_path / "raw")
    ingestor = _ingestor(engine=database_engine, raw=raw)
    key = _put(raw, "uploads/0123456789abcdef", _BODY)

    with pytest.raises(ValueError, match="declared"):
        ingestor.ingest_stored(
            deployment_id=_DEPLOYMENT_ID,
            key=key,
            size=len(_BODY) + 1,
            mime="text/markdown",
            filename="notes.md",
        )
    # Text declared as a PDF is refused, exactly as an upload would be.
    with pytest.raises(ContentDetectionError):
        ingestor.ingest_stored(
            deployment_id=_DEPLOYMENT_ID,
            key=key,
            size=len(_BODY),
            mime="application/pdf",
            filename="notes.pdf",
        )
    # Only opaque keys in the uploads namespace: hard forget purges a raw
    # key in every store, and file names must not reach forget manifests.
    for bad_key in (
        "forgotten/0123456789abcdef",
        "uploads/notes.md",
        "a/b/original.md",
    ):
        with pytest.raises(ValueError, match="uploads/"):
            ingestor.ingest_stored(
                deployment_id=_DEPLOYMENT_ID,
                key=ObjectKey(bad_key),
                size=len(_BODY),
                mime="text/markdown",
                filename="notes.md",
            )
    with pytest.raises(FileNotFoundError):
        ingestor.ingest_stored(
            deployment_id=_DEPLOYMENT_ID,
            key=ObjectKey("uploads/missingmissing0000"),
            size=1,
            mime="text/markdown",
            filename="missing.md",
        )
    with database_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM content_objects")
        ).one() == (0,)
