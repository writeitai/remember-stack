"""D106: E0 parks convert work for input it has no converter for.

The document itself is always ingested. An unroutable MIME is not a bad
upload — the bytes are durable, and the corpus projection emits a stub
carrying `raw_uri` even with no representation, so an agent can mount and read
the original. What changes is only the *work*: its convert row is enqueued
already parked, so no attempt is spent failing a conversion that cannot
succeed and nothing reaches the dead-letter queue.

These proofs assert the decision and its wiring with a recording catalog. The
durable consequences — the row really lands parked, the claim really skips it,
resuming really releases it — are proved against PostgreSQL in
`test_e0_chain.py`.
"""

from typing import cast
from uuid import UUID
from uuid import uuid4

import pytest

from rememberstack.adapters.converters import build_conversion_routes
from rememberstack.model import DeferReason
from rememberstack.model import DocumentUpload
from rememberstack.model import IngestedVersion
from rememberstack.model import ObjectKey
from rememberstack.model import ProcessingLane
from rememberstack.model import UploadRecord
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.workers.e0 import UploadIngestor

_DEPLOYMENT_ID = UUID("106a0000-0000-0000-0000-000000000001")
_ROUTES = {"text/markdown": "passthrough", "text/plain": "passthrough"}


class _RecordingCatalog:
    """A `DocumentCatalog` stand-in that records how convert was scheduled."""

    def __init__(self) -> None:
        """Start with nothing recorded."""
        self.calls = 0
        self.defer_reason: DeferReason | None = None

    def record_upload(
        self,
        *,
        record: UploadRecord,
        convert_component_version: str,
        lane: ProcessingLane = ProcessingLane.STEADY,
        metering: object | None = None,
        convert_defer_reason: DeferReason | None = None,
    ) -> IngestedVersion:
        """Record the scheduling decision and return a fixed receipt."""
        _ = convert_component_version, lane, metering
        self.calls += 1
        self.defer_reason = convert_defer_reason
        return IngestedVersion(
            deployment_id=record.deployment_id,
            doc_id=uuid4(),
            version_id=uuid4(),
            content_hash=record.content_hash,
            created=True,
        )


class _AllowingAdmission:
    """Accept every input so these proofs stay about routing, not D74."""

    def guard_ingest(
        self,
        *,
        deployment_id: UUID,
        source_kind: str,
        source_ref: str,
        content_hash: str,
    ) -> None:
        """Permit the attempted observation."""


class _CountingStore:
    """Object-store fake proving the bytes are written, not refused."""

    def __init__(self) -> None:
        self.writes = 0

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        raise AssertionError(f"unexpected read of {key.root}")

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str | None = None
    ) -> None:
        self.writes += 1


def _ingest(mime: str, *, observed: bool) -> tuple[_RecordingCatalog, _CountingStore]:
    """Drive one E0 entry point and return what it recorded."""
    catalog, store = _RecordingCatalog(), _CountingStore()
    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, catalog),
        raw_store=store,
        admission=_AllowingAdmission(),
        routable_mimes=frozenset(_ROUTES),
    )
    upload = DocumentUpload(filename="input.bin", mime=mime, content=b"hello")
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
    return catalog, store


@pytest.mark.parametrize("observed", (False, True))
def test_unroutable_input_is_still_stored_and_recorded(observed: bool) -> None:
    """The document is kept. Refusing it would delete a working capability.

    An unconverted version still reaches the corpus filesystem with its
    `raw_uri`, so the original stays mountable and readable by an agent even
    though nothing extracted from it.
    """
    catalog, store = _ingest("audio/mpeg", observed=observed)
    assert store.writes == 1
    assert catalog.calls == 1


@pytest.mark.parametrize("observed", (False, True))
def test_unroutable_input_parks_its_convert_work(observed: bool) -> None:
    """Both E0 entry points park, so no ingress can enqueue a doomed attempt."""
    catalog, _ = _ingest("audio/mpeg", observed=observed)
    assert catalog.defer_reason is DeferReason.NO_ROUTE


@pytest.mark.parametrize("observed", (False, True))
def test_routable_input_is_scheduled_immediately(observed: bool) -> None:
    """The control: a type the deployment converts is not deferred at all."""
    catalog, _ = _ingest("text/plain", observed=observed)
    assert catalog.defer_reason is None


def test_matching_is_exact_so_ingest_agrees_with_the_router() -> None:
    """A parameterised MIME parks, because the router would not route it.

    `ConversionRouter.converter_for` is an exact dict lookup. If ingest
    normalised `text/plain; charset=utf-8` down to `text/plain` and the worker
    did not, the row would be scheduled immediately and then dead-letter —
    the outcome D106 exists to remove. Normalisation belongs in the router,
    where both callers inherit it.
    """
    catalog, _ = _ingest("text/plain; charset=utf-8", observed=False)
    assert catalog.defer_reason is DeferReason.NO_ROUTE


def test_ingest_and_the_router_read_the_same_key_set() -> None:
    """The equivalence D106 rests on: configured keys are the router's keys.

    Ingest tests membership in the configured route-name table while the
    worker tests membership in the built router. That is only safe because
    `build_conversion_routes` materialises exactly the configured keys — it
    refuses composition on an unknown adapter rather than silently dropping a
    route. If this stopped holding, ingest could schedule what the worker
    cannot convert.
    """
    assert frozenset(build_conversion_routes(route_names=_ROUTES)) == frozenset(_ROUTES)
