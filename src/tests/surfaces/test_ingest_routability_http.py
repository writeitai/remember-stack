"""D104 at the HTTP surface: the E0 gate's refusal is rendered as 415.

The routing verdict itself belongs to `UploadIngestor` and is proved in
`tests/workers/test_ingest_routability.py`. This module asserts only what the
HTTP surface adds: that an `UnroutableMimeError` raised by the composed ingest
port becomes an HTTP 415 whose body tells the caller what may be sent instead.
"""

from datetime import datetime
from uuid import UUID
from uuid import uuid4

from fastapi.testclient import TestClient
from httpx import Response
import pytest

from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import IngestPrincipal
from rememberstack.model import UnroutableMimeError
from rememberstack.surfaces.http_api import build_api

_DEPLOYMENT_ID = UUID("103b0000-0000-0000-0000-000000000001")
_SUPPORTED = ("text/markdown", "text/plain")


class _RefusingIngest:
    """An `IngestPort` standing in for a gate that refuses this MIME."""

    def _refuse(self, upload: DocumentUpload) -> IngestedVersion:
        """Raise exactly what the E0 gate raises for an unrouted type."""
        if upload.mime in _SUPPORTED:
            return IngestedVersion(
                deployment_id=_DEPLOYMENT_ID,
                doc_id=uuid4(),
                version_id=uuid4(),
                content_hash="0" * 64,
                created=True,
            )
        raise UnroutableMimeError(
            f"no conversion route accepts mime {upload.mime!r}",
            mime=upload.mime,
            supported_mimes=_SUPPORTED,
        )

    def ingest(
        self,
        *,
        deployment_id: UUID,
        upload: DocumentUpload,
        ingested_by: IngestPrincipal | None = None,
    ) -> IngestedVersion:
        """Refuse or accept the one-shot upload path."""
        _ = deployment_id, ingested_by
        return self._refuse(upload)

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
        """Refuse or accept the lineage path."""
        _ = (
            deployment_id,
            source_kind,
            source_ref,
            versioning_mode,
            source_modified_at,
            source_version_ref,
            sync_cycle_id,
            ingested_by,
        )
        return self._refuse(upload)


class _OpenBoundary:
    """Admission/readiness that never refuses, so proofs stay about ingest."""

    def assert_available(self, *, deployment_id: UUID) -> None:
        """Never close the D74 admission barrier during these proofs."""
        _ = deployment_id

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        """Report no outstanding forget manifests to replay."""
        _ = deployment_id
        return ()


def _client() -> TestClient:
    """Build the API over the refusing ingest port."""
    return TestClient(
        build_api(
            engine=None,  # type: ignore[arg-type]
            deployment_id=_DEPLOYMENT_ID,
            admission=_OpenBoundary(),
            readiness=_OpenBoundary(),
            ingest=_RefusingIngest(),
        )
    )


def _post(mime: str, *, lineage: bool = False) -> Response:
    """POST one byte payload declaring `mime`, optionally as a lineage push."""
    params: dict[str, str] = {"filename": "input.bin", "mime": mime}
    if lineage:
        params |= {"source_kind": "drive", "source_ref": "file-1"}
    return _client().post("/ingest", params=params, content=b"hello")


def test_a_routed_mime_still_succeeds() -> None:
    """The control: the surface does not refuse what the gate accepted."""
    assert _post("text/plain").status_code == 200


@pytest.mark.parametrize("lineage", (False, True))
def test_the_gates_refusal_becomes_415(lineage: bool) -> None:
    """Both HTTP ingest shapes render the refusal, not just the one-shot path."""
    assert _post("audio/mpeg", lineage=lineage).status_code == 415


def test_the_415_body_names_what_the_deployment_converts() -> None:
    """A caller that guessed wrong is told what it may send instead."""
    detail = _post("application/zip").json()["detail"]
    # `code` (not `error`) is this surface's key for a structured refusal.
    assert detail["code"] == "unsupported_media_type"
    assert detail["mime"] == "application/zip"
    assert detail["supported_mimes"] == ["text/markdown", "text/plain"]
