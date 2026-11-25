"""Fast D159 proof that ingest checks the re-ingest guard before persisting bytes."""

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import cast
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from pydantic import ValidationError
import pytest

from rememberstack.model import DocumentForgottenError
from rememberstack.model import DocumentUpload
from rememberstack.model import ObjectKey
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.workers.e0 import UploadIngestor

_DEPLOYMENT_ID = UUID("74000000-0000-0000-0000-000000000001")


class DenyingAdmission:
    """Reject every input while retaining what was checked."""

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
        allow_forgotten: bool = False,
    ) -> None:
        """Raise the typed re-ingest guard result."""
        raise DocumentForgottenError(
            forget_id=UUID(int=1),
            forgotten_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

    def lineage_doc_id(
        self, *, deployment_id: UUID, source_kind: str, source_ref: str
    ) -> UUID:
        """Never reached: the guard refuses first."""
        raise AssertionError("the guard must refuse before a lineage is derived")


class AllowingAdmission:
    """Accept every input so boundary validation can run."""

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
        allow_forgotten: bool = False,
    ) -> None:
        """Permit the attempted observation."""

    def lineage_doc_id(
        self, *, deployment_id: UUID, source_kind: str, source_ref: str
    ) -> UUID:
        """The base derivation: no lineage of this identity was forgotten."""
        return uuid5(
            NAMESPACE_URL, f"rememberstack:{source_kind}:{deployment_id}:{source_ref}"
        )


class RecordingStore:
    """Object-store fake proving rejection happens before the first write."""

    def __init__(self) -> None:
        self.writes = 0

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        raise AssertionError(f"unexpected read of {key.root}")

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str
    ) -> None:
        self.writes += 1


@pytest.mark.parametrize("observed", (False, True))
def test_guard_runs_before_upload_and_observed_raw_writes(observed: bool) -> None:
    """Never recreate forgotten content during either E0 ingest form."""
    store = RecordingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, object()),
        raw_store=store,
        admission=DenyingAdmission(),
        routable_mimes=frozenset({"text/markdown"}),
    )
    upload = DocumentUpload(
        filename="forgotten.md", mime="text/markdown", content=b"forgotten"
    )

    with pytest.raises(DocumentForgottenError):
        if observed:
            ingestor.ingest_observed(
                deployment_id=_DEPLOYMENT_ID,
                source_kind="drive",
                source_ref="file-1",
                upload=upload,
                versioning_mode="living",
                source_modified_at=None,
                source_version_ref=None,
                sync_cycle_id=None,
            )
        else:
            ingestor.ingest(deployment_id=_DEPLOYMENT_ID, upload=upload)

    assert store.writes == 0


@pytest.mark.parametrize(
    "source_modified_at",
    (
        datetime(2023, 5, 1, 13),
        datetime(2023, 5, 1, 13, tzinfo=timezone(timedelta(hours=2))),
    ),
)
def test_observed_ingest_rejects_non_utc_time_before_raw_write(
    source_modified_at: datetime,
) -> None:
    """Internal callers cannot bypass UTC validation or leave orphaned bytes."""
    store = RecordingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, object()),
        raw_store=store,
        admission=AllowingAdmission(),
        routable_mimes=frozenset({"text/markdown"}),
    )

    with pytest.raises(ValidationError, match="timezone-aware UTC"):
        ingestor.ingest_observed(
            deployment_id=_DEPLOYMENT_ID,
            source_kind="drive",
            source_ref="file-1",
            upload=DocumentUpload(
                filename="invalid-time.md",
                mime="text/markdown",
                content=b"must not persist",
            ),
            versioning_mode="living",
            source_modified_at=source_modified_at,
            source_version_ref="v1",
            sync_cycle_id=None,
        )

    assert store.writes == 0
