"""Ingest attribution: the typed principal that created a document version.

The HTTP proofs use a recording fake `IngestPort`, so they assert the surface
contract without a database. The catalog proofs need real PostgreSQL and skip
when `REMEMBERSTACK_DATABASE_URL` is absent, matching the rest of the suite.
"""

from datetime import datetime
from pathlib import Path
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text

from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import IngestPrincipalKind
from rememberstack.model import ProcessingLane
from rememberstack.model import UploadRecord
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("00000000-0000-0000-0000-0000000000a1")
_ROOT = Path(__file__).parents[3]


class _RecordingIngest:
    """An `IngestPort` that records what the surface handed it."""

    def __init__(self) -> None:
        """Start with no recorded call."""
        self.last_principal: IngestPrincipal | None = None
        self.calls = 0

    def _result(self) -> IngestedVersion:
        """Return a fixed accepted-version receipt."""
        return IngestedVersion(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=uuid4(),
            version_id=uuid4(),
            content_hash="0" * 64,
            created=True,
        )

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        """Record the one-shot upload path's principal."""
        _ = deployment_id, upload
        self.calls += 1
        self.last_principal = ingested_by
        return self._result()

    def ingest_observed(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        upload: DocumentUpload,
        versioning_mode: str,
        source_modified_at: datetime | None,
        source_version_ref: str | None,
        sync_cycle_id: UUID | None,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        """Record the lineage path's principal."""
        _ = (
            deployment_id,
            source_kind,
            source_ref,
            upload,
            versioning_mode,
            source_modified_at,
            source_version_ref,
            sync_cycle_id,
        )
        self.calls += 1
        self.last_principal = ingested_by
        return self._result()


class _OpenBoundary:
    """Admission/readiness that never refuses, so proofs stay about ingest."""

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Never close the D74 admission barrier during these proofs."""
        _ = deployment_id

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Report no outstanding forget manifests to replay."""
        _ = deployment_id
        return ()


def _client(ingest: _RecordingIngest) -> TestClient:
    """Build the API over the recording ingest port."""
    return TestClient(
        build_api(
            engine=None,  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=_OpenBoundary(),
            readiness=_OpenBoundary(),
            ingest=ingest,
        )
    )


def _post(client: TestClient, **params: str) -> Response:
    """POST one byte payload with the given query parameters."""
    return client.post(
        "/ingest",
        params={"filename": "notes.txt", "mime": "text/plain", **params},
        content=b"hello",
    )


def test_principal_pair_reaches_the_ingest_port() -> None:
    """A complete principal pair is typed and forwarded, not dropped."""
    ingest = _RecordingIngest()
    response = _post(_client(ingest), principal_kind="user", principal_ref="user:jiri")
    assert response.status_code == 200
    assert ingest.last_principal == IngestPrincipal(
        kind=IngestPrincipalKind.USER, external_ref="user:jiri"
    )


def test_absent_principal_stays_none() -> None:
    """Attribution is optional: self-hosted callers keep today's behaviour."""
    ingest = _RecordingIngest()
    assert _post(_client(ingest)).status_code == 200
    assert ingest.last_principal is None


@pytest.mark.parametrize(
    "params", [{"principal_kind": "user"}, {"principal_ref": "user:jiri"}]
)
def test_half_a_principal_is_refused(params: dict[str, str]) -> None:
    """Kind and ref are supplied together or not at all."""
    ingest = _RecordingIngest()
    response = _post(_client(ingest), **params)
    assert response.status_code == 422
    assert ingest.calls == 0


def test_unknown_principal_kind_is_refused() -> None:
    """The kind is a closed vocabulary, so a typo cannot invent an actor type."""
    ingest = _RecordingIngest()
    response = _post(_client(ingest), principal_kind="robot", principal_ref="r1")
    assert response.status_code == 422
    assert ingest.calls == 0


def test_credential_principal_is_not_a_user() -> None:
    """A machine credential keeps its own kind; it is never a person."""
    ingest = _RecordingIngest()
    _post(
        _client(ingest), principal_kind="api_credential", principal_ref="dpcred:tok-1"
    )
    assert ingest.last_principal is not None
    assert ingest.last_principal.kind is IngestPrincipalKind.API_CREDENTIAL


def test_empty_external_ref_is_refused() -> None:
    """An empty reference identifies nobody and must not be stored."""
    with pytest.raises(ValidationError):
        IngestPrincipal(kind=IngestPrincipalKind.USER, external_ref="")


# --------------------------------------------------------------------------
# Catalog proofs (real PostgreSQL)
# --------------------------------------------------------------------------


def _database_url() -> str:
    """Resolve the integration database or skip."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for catalog proofs")


def _migrated_engine():  # type: ignore[no-untyped-def]
    """Return an engine with the full migration chain applied."""
    url = _database_url()
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    return create_engine(url)


def _record(*, content_hash: str, principal: IngestPrincipal | None) -> UploadRecord:
    """Build an upload record for a fixed lineage."""
    return UploadRecord(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=UUID("00000000-0000-0000-0000-0000000000d1"),
        source_kind="upload",
        source_ref="rcpt-1",
        source_uri=None,
        title="Notes",
        content_hash=content_hash,
        mime="text/plain",
        byte_size=5,
        raw_uri=f"raw/{content_hash}",
        ingested_by=principal,
    )


def test_new_version_records_its_principal_and_reads_back() -> None:
    """A created version stores the principal; the catalog can return it."""
    engine = _migrated_engine()
    catalog = DocumentCatalog(engine=engine)
    principal = IngestPrincipal(kind=IngestPrincipalKind.USER, external_ref="user:jiri")
    landed = catalog.record_upload(
        record=_record(content_hash="a" * 64, principal=principal),
        convert_component_version="toy",
        lane=ProcessingLane.STEADY,
    )
    assert landed.created is True
    assert landed.ingested_by_principal_id is not None
    assert (
        catalog.version_principal(
            deployment_id=_DEPLOYMENT_ID, version_id=landed.version_id
        )
        == principal
    )


def test_identical_bytes_do_not_reattribute_the_version() -> None:
    """D55's no-op must never let a second actor rewrite immutable attribution."""
    engine = _migrated_engine()
    catalog = DocumentCatalog(engine=engine)
    first = IngestPrincipal(kind=IngestPrincipalKind.USER, external_ref="user:one")
    second = IngestPrincipal(kind=IngestPrincipalKind.USER, external_ref="user:two")
    landed = catalog.record_upload(
        record=_record(content_hash="b" * 64, principal=first),
        convert_component_version="toy",
    )
    again = catalog.record_upload(
        record=_record(content_hash="b" * 64, principal=second),
        convert_component_version="toy",
    )
    assert again.created is False
    assert (
        catalog.version_principal(
            deployment_id=_DEPLOYMENT_ID, version_id=landed.version_id
        )
        == first
    )


def test_erasing_a_principal_keeps_the_document() -> None:
    """Removing a person nulls attribution; it never destroys the version."""
    engine = _migrated_engine()
    catalog = DocumentCatalog(engine=engine)
    landed = catalog.record_upload(
        record=_record(
            content_hash="c" * 64,
            principal=IngestPrincipal(
                kind=IngestPrincipalKind.USER, external_ref="user:erase-me"
            ),
        ),
        convert_component_version="toy",
    )
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM ingest_principals WHERE principal_id = :p"),
            {"p": landed.ingested_by_principal_id},
        )
        surviving = connection.execute(
            text(
                "SELECT ingested_by_principal_id FROM document_versions"
                " WHERE version_id = :v"
            ),
            {"v": landed.version_id},
        ).one()
    assert surviving[0] is None
    assert (
        catalog.version_principal(
            deployment_id=_DEPLOYMENT_ID, version_id=landed.version_id
        )
        is None
    )
