"""D134 document metadata through the real E0 chain on PostgreSQL.

Ingest writes each new version's ``document_metadata`` row and first observed
name; identical bytes under a new name append a name without a version; the
convert stage merges what the converter read from the file, idempotently.
"""

from collections.abc import Iterator
from datetime import datetime
from datetime import timezone
from pathlib import Path
import threading
from typing import Any
from uuid import UUID

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost import LocalFSObjectStore
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.core import ConversionRouter
from rememberstack.core import Converter
from rememberstack.core import entire_document_labeling
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.core.document_metadata import INGEST_METADATA_MAPPING_VERSION
from rememberstack.model import ClaimedWork
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import ManifestComponent
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.model.document_metadata import DocumentMetadata
from rememberstack.model.document_metadata import DocumentPerson
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import DocumentCatalog
from rememberstack.spine import ForgetCatalog
from rememberstack.spine.document_metadata import merge_converter_metadata_on
from rememberstack.spine.settings import load_database_settings
from rememberstack.workers import ConvertHandler
from rememberstack.workers import E0_CONVERT_VERSION
from rememberstack.workers import UploadIngestor
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("60000000-0000-0000-0000-0000000d0134")
_MAIL_MIME = "application/x-fake-mail"
_SENT = datetime(2025, 4, 2, 8, 15, tzinfo=timezone.utc)
_MAIL_METADATA = DocumentMetadata(
    title="Audit findings",
    authors=(DocumentPerson(name="Jiří Novák", address="Jiri@ACME.com"),),
    recipients=(DocumentPerson(name="Bob Chen"), DocumentPerson(address="@carol")),
    created_at=_SENT,
    language="en",
    thread_ref="<root@acme.com>",
    extra={"reply_to": "audit@acme.com"},
)


class _FakeMailConverter:
    """A route that reads general metadata from the file, like an email route."""

    name = "fake-mail"
    version = "fake-mail-1"

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Pass the bytes through and declare fixed metadata."""
        del mime
        document_md = content.decode("utf-8")
        return ConversionResult(
            document_md=document_md,
            metadata=_MAIL_METADATA,
            manifest=ConverterManifest(
                components=(
                    ManifestComponent(
                        name="fake-mail", version="1", execution="library-local"
                    ),
                ),
                coverage=ConversionCoverage(policy="full", complete=True),
                derivation_ranges=entire_document_labeling(
                    document_md=document_md,
                    derivation_kind="passthrough",
                    evidence_mode="source_expression",
                ),
            ),
        )


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head over the integration database."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for D134 chain proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def bootstrapped_deployment(database_engine: Engine) -> None:
    """Give every proof a fresh deployment."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="d134-metadata-test",
            name="D134 metadata proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )


class _Rig:
    """Ingestor plus convert handler over one database and one store root."""

    def __init__(self, *, engine: Engine, root: Path) -> None:
        """Compose ingest and convert with a passthrough and a fake mail route."""
        self.engine = engine
        self.catalog = DocumentCatalog(engine=engine)
        raw_store = LocalFSObjectStore(root=root / "raw")
        routes: dict[str, Converter] = {
            "text/markdown": MarkdownPassthroughConverter(),
            _MAIL_MIME: _FakeMailConverter(),
        }
        self.ingestor = UploadIngestor(
            catalog=self.catalog,
            raw_store=raw_store,
            admission=ForgetCatalog(engine=engine),
            routable_mimes=frozenset(routes),
        )
        self.converter = ConvertHandler(
            catalog=self.catalog,
            raw_store=raw_store,
            artifact_store=LocalFSObjectStore(root=root / "artifacts"),
            router=ConversionRouter(routes=routes),
        )

    def convert(self, *, ingested: IngestedVersion) -> None:
        """Run the convert handler for one version (a replay when repeated)."""
        self.converter.handle(
            work=ClaimedWork(
                processing_id=ingested.version_id,
                deployment_id=_DEPLOYMENT_ID,
                target_kind=ProcessingTarget.DOCUMENT_VERSION,
                target_id=ingested.version_id,
                stage=PipelineStage.CONVERT,
                component_version=E0_CONVERT_VERSION,
                content_hash=ingested.content_hash,
                lane=ProcessingLane.STEADY,
                attempt=1,
                payload={"version_id": str(ingested.version_id)},
            ),
            meter=NoopCostMeter(),
        )

    def metadata(self, *, version_id: UUID) -> dict[str, Any]:
        """The version's metadata row."""
        with self.engine.connect() as connection:
            return dict(
                connection.execute(
                    text("SELECT * FROM document_metadata WHERE version_id = :v"),
                    {"v": version_id},
                )
                .mappings()
                .one()
            )

    def names(self, *, version_id: UUID) -> list[tuple[object, ...]]:
        """The version's observed names, oldest first."""
        with self.engine.connect() as connection:
            return [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT file_name, title, source_path, name_text"
                        " FROM document_names WHERE version_id = :v"
                        " ORDER BY observed_at"
                    ),
                    {"v": version_id},
                )
            ]

    def people(self, *, version_id: UUID) -> list[tuple[object, ...]]:
        """The version's people rows in role/ordinal order."""
        with self.engine.connect() as connection:
            return [
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT role, ordinal, display_name, address,"
                        " normalized_name, normalized_address, provenance"
                        " FROM document_people WHERE version_id = :v"
                        " ORDER BY role, ordinal"
                    ),
                    {"v": version_id},
                )
            ]


@pytest.fixture()
def rig(database_engine: Engine, tmp_path: Path) -> _Rig:
    """A fresh composed ingest/convert pair per proof."""
    return _Rig(engine=database_engine, root=tmp_path)


def _upload(
    *,
    filename: str,
    title: str | None,
    source_path: str | None,
    content: bytes = b"# Q3 sales\n\nRevenue grew.\n",
    mime: str = "text/markdown",
) -> DocumentUpload:
    return DocumentUpload(
        filename=filename,
        mime=mime,
        content=content,
        title=title,
        source_path=source_path,
    )


def test_ingest_writes_metadata_and_first_name(rig: _Rig) -> None:
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(
            filename="Q3_sales.md", title="Q3 sales", source_path="finance/2025"
        ),
    )

    metadata = rig.metadata(version_id=ingested.version_id)
    assert metadata["doc_id"] == ingested.doc_id
    assert metadata["family"] == "markdown"
    assert metadata["file_name"] == "Q3_sales.md"
    assert metadata["title"] == "Q3 sales"
    assert metadata["source_path"] == "finance/2025"
    assert metadata["metadata_mapping_version"] == INGEST_METADATA_MAPPING_VERSION
    assert metadata["provenance"] == {
        "file_name": "connector",
        "source_path": "connector",
        "title": "connector",
    }
    assert rig.names(version_id=ingested.version_id) == [
        ("Q3_sales.md", "Q3 sales", "finance/2025", "Q3_sales.md Q3 sales finance/2025")
    ]


def test_undeclared_title_is_not_the_file_stem(rig: _Rig) -> None:
    """The lineage title falls back to the stem; the declared title stays empty."""
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(filename="notes.md", title=None, source_path=None),
    )
    assert ingested.title == "notes"
    assert rig.metadata(version_id=ingested.version_id)["title"] is None
    assert rig.names(version_id=ingested.version_id) == [
        ("notes.md", None, None, "notes.md")
    ]


def test_identical_bytes_under_a_new_name_append_a_name_only(rig: _Rig) -> None:
    first = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(filename="draft.md", title="Draft", source_path="inbox"),
    )
    renamed = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(filename="final.md", title=None, source_path="archive"),
    )

    assert not renamed.created
    assert renamed.version_id == first.version_id
    assert renamed.title == "Draft"  # the lineage title stays first-write-wins
    assert rig.names(version_id=first.version_id) == [
        ("draft.md", "Draft", "inbox", "draft.md Draft inbox"),
        ("final.md", None, "archive", "final.md archive"),
    ]
    # the version's metadata keeps what was observed when it was ingested
    assert rig.metadata(version_id=first.version_id)["file_name"] == "draft.md"


def test_identical_bytes_under_the_same_name_record_nothing(rig: _Rig) -> None:
    upload = _upload(filename="same.md", title="Same", source_path="docs")
    first = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
    rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
    # a value not sent was not observed, so it is not a rename either
    rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(filename="same.md", title=None, source_path=None),
    )
    assert len(rig.names(version_id=first.version_id)) == 1


def test_watched_observation_records_file_name_and_path(rig: _Rig) -> None:
    ingested = rig.ingestor.ingest_observed(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="local_folder",
        source_ref="team/plan.md",
        upload=_upload(filename="plan.md", title=None, source_path="team/plan.md"),
        versioning_mode="snapshot",
        source_modified_at=_SENT,
        source_version_ref="rev-1",
        sync_cycle_id=None,
    )
    metadata = rig.metadata(version_id=ingested.version_id)
    assert metadata["file_name"] == "plan.md"
    assert metadata["source_path"] == "team/plan.md"
    assert metadata["modified_at"] == _SENT
    assert metadata["provenance"]["modified_at"] == "connector"


def test_convert_merges_converter_metadata_idempotently(rig: _Rig) -> None:
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(
            filename="audit.eml",
            title=None,
            source_path=None,
            content=b"Audit findings body.\n",
            mime=_MAIL_MIME,
        ),
    )
    rig.convert(ingested=ingested)
    merged = rig.metadata(version_id=ingested.version_id)
    names = rig.names(version_id=ingested.version_id)
    people = rig.people(version_id=ingested.version_id)

    assert merged["family"] == "other"
    assert merged["title"] == "Audit findings"
    assert merged["created_at"] == _SENT
    assert merged["language"] == "en"
    assert merged["thread_ref"] == "<root@acme.com>"
    assert merged["extra"] == {"reply_to": "audit@acme.com"}
    assert merged["metadata_mapping_version"] == "fake-mail@fake-mail-1"
    assert merged["provenance"] == {
        "file_name": "connector",
        "title": "source",
        "created_at": "source",
        "language": "source",
        "thread_ref": "source",
    }
    assert names == [
        ("audit.eml", None, None, "audit.eml"),
        ("audit.eml", "Audit findings", None, "audit.eml Audit findings"),
    ]
    assert people == [
        (
            "author",
            0,
            "Jiří Novák",
            "Jiri@ACME.com",
            "jiri novak",
            "jiri@acme.com",
            "source",
        ),
        ("recipient", 0, "Bob Chen", None, "bob chen", None, "source"),
        ("recipient", 1, None, "@carol", None, "@carol", "source"),
    ]

    # a replayed convert reuses the representation and changes nothing
    rig.convert(ingested=ingested)
    # and merging the same metadata again is a no-op on every table
    with rig.engine.begin() as connection:
        merge_converter_metadata_on(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            version_id=ingested.version_id,
            metadata=_MAIL_METADATA,
            mapping_version="fake-mail@fake-mail-1",
        )
    assert rig.metadata(version_id=ingested.version_id) == merged
    assert rig.names(version_id=ingested.version_id) == names
    assert rig.people(version_id=ingested.version_id) == people


def test_converter_title_never_replaces_a_declared_title(rig: _Rig) -> None:
    ingested = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(
            filename="audit.eml",
            title="Caller title",
            source_path=None,
            content=b"Another audit body.\n",
            mime=_MAIL_MIME,
        ),
    )
    rig.convert(ingested=ingested)

    merged = rig.metadata(version_id=ingested.version_id)
    assert merged["title"] == "Caller title"
    assert merged["provenance"]["title"] == "connector"
    # the file's own title is still a name the document can be found by
    assert rig.names(version_id=ingested.version_id)[-1] == (
        "audit.eml",
        "Audit findings",
        None,
        "audit.eml Audit findings",
    )


def test_mime_repair_refreshes_family_for_every_lineage(rig: _Rig) -> None:
    """Re-sending parked bytes with a routable MIME re-derives every family."""
    content = b"# Notes\n"
    parked = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(
            filename="notes",
            title=None,
            source_path=None,
            content=content,
            mime="application/x-unknown",
        ),
    )
    other_lineage = rig.ingestor.ingest_observed(
        deployment_id=_DEPLOYMENT_ID,
        source_kind="drive",
        source_ref="notes",
        upload=_upload(
            filename="notes",
            title=None,
            source_path=None,
            content=content,
            mime="application/x-unknown",
        ),
        versioning_mode="snapshot",
        source_modified_at=None,
        source_version_ref=None,
        sync_cycle_id=None,
    )
    for version_id in (parked.version_id, other_lineage.version_id):
        assert rig.metadata(version_id=version_id)["family"] == "other"

    resent = rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=_upload(
            filename="notes.md", title=None, source_path=None, content=content
        ),
    )

    assert resent.created is False
    assert resent.mime == "text/markdown"
    for version_id in (parked.version_id, other_lineage.version_id):
        assert rig.metadata(version_id=version_id)["family"] == "markdown"


def test_convert_names_carry_the_latest_observed_file_name(rig: _Rig) -> None:
    """The converter's name row builds on the newest observation, not a stale one."""
    upload = _upload(
        filename="audit.eml",
        title=None,
        source_path=None,
        content=b"Renamed audit body.\n",
        mime=_MAIL_MIME,
    )
    ingested = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
    rig.ingestor.ingest(
        deployment_id=_DEPLOYMENT_ID,
        upload=upload.model_copy(update={"filename": "renamed.eml"}),
    )
    rig.convert(ingested=ingested)

    assert rig.names(version_id=ingested.version_id) == [
        ("audit.eml", None, None, "audit.eml"),
        ("renamed.eml", None, None, "renamed.eml"),
        ("renamed.eml", "Audit findings", None, "renamed.eml Audit findings"),
    ]


def test_name_writers_serialize_on_the_metadata_row(rig: _Rig) -> None:
    """An observation waits for an open convert merge, then compares to its row."""
    upload = _upload(
        filename="audit.eml",
        title=None,
        source_path=None,
        content=b"Concurrent audit body.\n",
        mime=_MAIL_MIME,
    )
    ingested = rig.ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)
    observation_errors: list[BaseException] = []

    def observe_rename() -> None:
        try:
            rig.ingestor.ingest(
                deployment_id=_DEPLOYMENT_ID,
                upload=upload.model_copy(update={"filename": "renamed.eml"}),
            )
        except BaseException as error:  # surfaced to the test thread below
            observation_errors.append(error)

    with rig.engine.connect() as connection:
        transaction = connection.begin()
        merge_converter_metadata_on(
            connection=connection,
            deployment_id=_DEPLOYMENT_ID,
            version_id=ingested.version_id,
            metadata=_MAIL_METADATA,
            mapping_version="fake-mail@fake-mail-1",
        )
        observer = threading.Thread(target=observe_rename)
        observer.start()
        observer.join(timeout=1.0)
        # the observation is blocked on the merge's metadata-row lock
        assert observer.is_alive()
        transaction.commit()
    observer.join(timeout=30.0)
    assert not observer.is_alive()
    assert observation_errors == []

    assert rig.names(version_id=ingested.version_id) == [
        ("audit.eml", None, None, "audit.eml"),
        ("audit.eml", "Audit findings", None, "audit.eml Audit findings"),
        ("renamed.eml", None, None, "renamed.eml"),
    ]
