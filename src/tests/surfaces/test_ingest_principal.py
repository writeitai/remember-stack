"""Ingest attribution: the typed principal that created a document version.

The HTTP proofs use a recording fake `IngestPort`, so they assert the surface
contract without a database. The catalog proofs need real PostgreSQL and skip
when `REMEMBERSTACK_DATABASE_URL` is absent, matching the rest of the suite.
"""

from collections.abc import Iterator
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

from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import IngestPrincipalKind
from rememberstack.model import ProcessingLane
from rememberstack.model import UploadRecord
from rememberstack.spine import DeploymentBootstrapper
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


def _client(ingest: _RecordingIngest, *, trusted: bool = True) -> TestClient:
    """Build the API over the recording ingest port."""
    return TestClient(
        build_api(
            engine=None,  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=_OpenBoundary(),
            readiness=_OpenBoundary(),
            ingest=ingest,
            trusted_principal_source=trusted,
        )
    )


def _post(client: TestClient, **headers: str) -> Response:
    """POST one byte payload, attribution carried in headers not the URL."""
    return client.post(
        "/ingest",
        params={"filename": "notes.txt", "mime": "text/plain"},
        content=b"hello",
        headers=headers,
    )


def _attribution(kind: str | None = None, ref: str | None = None) -> dict[str, str]:
    """Build the attribution headers actually sent on the wire."""
    out: dict[str, str] = {}
    if kind is not None:
        out["X-Ingest-Principal-Kind"] = kind
    if ref is not None:
        out["X-Ingest-Principal-Ref"] = ref
    return out


def test_principal_pair_reaches_the_ingest_port() -> None:
    """A complete principal pair is typed and forwarded, not dropped."""
    ingest = _RecordingIngest()
    response = _post(_client(ingest), **_attribution("user", "user:jiri"))
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
    "headers", [_attribution("user"), _attribution(ref="user:jiri")]
)
def test_half_a_principal_is_refused(headers: dict[str, str]) -> None:
    """Kind and ref are supplied together or not at all."""
    ingest = _RecordingIngest()
    response = _post(_client(ingest), **headers)
    assert response.status_code == 422
    assert ingest.calls == 0


def test_unknown_principal_kind_is_refused() -> None:
    """The kind is a closed vocabulary, so a typo cannot invent an actor type."""
    ingest = _RecordingIngest()
    response = _post(_client(ingest), **_attribution("robot", "r1"))
    assert response.status_code == 422
    assert ingest.calls == 0


def test_credential_principal_is_not_a_user() -> None:
    """A machine credential keeps its own kind; it is never a person."""
    ingest = _RecordingIngest()
    _post(_client(ingest), **_attribution("api_credential", "dpcred:tok-1"))
    assert ingest.last_principal is not None
    assert ingest.last_principal.kind is IngestPrincipalKind.API_CREDENTIAL
    assert ingest.last_principal.external_ref == "dpcred:tok-1"


def test_untrusted_perimeter_ignores_attribution_without_failing_ingest() -> None:
    """An untrusted claim is dropped, and the document is still ingested.

    The deployment bearer identifies a deployment, not a caller, so believing
    `X-Ingest-Principal-*` there would let any client record itself as a user.
    Refusing instead would be worse than ignoring: nothing forged is recorded
    either way, but a refusal would let a metadata concern reject a real
    document from a merely misconfigured deployment.
    """
    ingest = _RecordingIngest()
    response = _post(
        _client(ingest, trusted=False), **_attribution("user", "user:impostor")
    )
    assert response.status_code == 200
    assert ingest.calls == 1
    assert ingest.last_principal is None


def test_untrusted_perimeter_still_accepts_unattributed_ingest() -> None:
    """The gate refuses a claim, never ordinary ingest."""
    ingest = _RecordingIngest()
    assert _post(_client(ingest, trusted=False)).status_code == 200
    assert ingest.last_principal is None


def test_attribution_never_appears_in_the_request_url() -> None:
    """The reference is erasable PII, so it must not reach access logs.

    A URL is copied verbatim into server access logs, proxies and traces,
    where a later principal deletion cannot reach it. Headers are not.
    """
    ingest = _RecordingIngest()
    sentinel = "user:log-sentinel-do-not-leak"
    response = _post(_client(ingest), **_attribution("user", sentinel))
    assert response.status_code == 200
    assert sentinel not in str(response.request.url)
    assert sentinel not in (response.request.url.query or b"").decode()


def test_legacy_port_without_the_keyword_still_serves_unattributed_ingest() -> None:
    """An old IngestPort has no `ingested_by`; unattributed calls must not break."""

    class _LegacyIngest:
        """The pre-attribution port signature."""

        def __init__(self) -> None:
            self.calls = 0

        def ingest(
            self, *, deployment_id: UUID, upload: DocumentUpload
        ) -> IngestedVersion:
            """Accept only the original keywords."""
            _ = upload
            self.calls += 1
            return IngestedVersion(
                deployment_id=deployment_id,
                doc_id=uuid4(),
                version_id=uuid4(),
                content_hash="0" * 64,
                created=True,
            )

    legacy = _LegacyIngest()
    client = TestClient(
        build_api(
            engine=None,  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=_OpenBoundary(),
            readiness=_OpenBoundary(),
            ingest=legacy,  # type: ignore[arg-type]
        )
    )
    assert _post(client).status_code == 200
    assert legacy.calls == 1


def test_receipt_shape_is_unchanged_for_old_clients() -> None:
    """`IngestedVersion` gains no field, so an extra-forbid client still parses."""
    assert set(IngestedVersion.model_fields) == {
        "deployment_id",
        "doc_id",
        "version_id",
        "content_hash",
        "created",
    }


def test_empty_external_ref_is_refused() -> None:
    """An empty reference identifies nobody and must not be stored."""
    with pytest.raises(ValidationError):
        IngestPrincipal(kind=IngestPrincipalKind.USER, external_ref="")


def test_non_ascii_reference_is_a_clear_rejection_not_a_crash() -> None:
    """The header transport cannot carry non-ASCII, so the contract says so.

    Without the constraint this surfaced as a UnicodeEncodeError at the
    transport — a metadata field crashing an upload. Callers use opaque ids,
    so the restriction costs nothing real.
    """
    with pytest.raises(ValidationError):
        IngestPrincipal(kind=IngestPrincipalKind.USER, external_ref="user:Novák")


# --------------------------------------------------------------------------
# Catalog proofs (real PostgreSQL)
# --------------------------------------------------------------------------


def _database_url() -> str:
    """Resolve the integration database or skip."""
    try:
        return load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for catalog proofs")


@pytest.fixture()
def catalog() -> Iterator[DocumentCatalog]:
    """A migrated database with one bootstrapped deployment, fresh per test.

    The earlier version of this fixture only ran migrations. Every catalog
    insert then failed `documents_deployment_id_fkey`, so all three proofs
    errored before any principal SQL ran — they proved nothing.
    """
    url = _database_url()
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
        DeploymentBootstrapper(engine=engine).bootstrap_deployment(
            deployment_input=DeploymentBootstrapInput(
                deployment_id=_DEPLOYMENT_ID,
                slug="ingest-principal-test",
                name="Ingest principal proofs",
                default_language="en",
                raw_bucket="mem://raw",
                artifacts_bucket="mem://artifacts",
                corpusfs_bucket="mem://corpusfs",
            )
        )
        yield DocumentCatalog(engine=engine)
    finally:
        engine.dispose()


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


def test_new_version_records_its_principal_and_reads_back(
    catalog: DocumentCatalog,
) -> None:
    """A created version stores the principal; the catalog can return it."""
    principal = IngestPrincipal(kind=IngestPrincipalKind.USER, external_ref="user:jiri")
    landed = catalog.record_upload(
        record=_record(content_hash="a" * 64, principal=principal),
        convert_component_version="toy",
        lane=ProcessingLane.STEADY,
    )
    assert landed.created is True
    assert (
        catalog.version_principal(
            deployment_id=_DEPLOYMENT_ID, version_id=landed.version_id
        )
        == principal
    )


def test_identical_bytes_do_not_reattribute_the_version(
    catalog: DocumentCatalog,
) -> None:
    """D55's no-op must never let a second actor rewrite immutable attribution."""
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


def test_erasing_a_principal_keeps_the_document(catalog: DocumentCatalog) -> None:
    """Removing a person nulls attribution; it never destroys the version."""
    landed = catalog.record_upload(
        record=_record(
            content_hash="c" * 64,
            principal=IngestPrincipal(
                kind=IngestPrincipalKind.USER, external_ref="user:erase-me"
            ),
        ),
        convert_component_version="toy",
    )
    with catalog._engine.begin() as connection:  # noqa: SLF001
        connection.execute(
            text(
                "DELETE FROM ingest_principals WHERE deployment_id = :d"
                " AND external_ref = :r"
            ),
            {"d": _DEPLOYMENT_ID, "r": "user:erase-me"},
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
