"""D104: E0 refuses a MIME the deployment has no conversion route for.

The gate lives in `UploadIngestor`, not on any one surface, because every
ingress writes through it — HTTP `POST /ingest`, the local MCP `ingest` tool,
and the connector sync worker all call the same object. A check placed on the
HTTP handler would leave the other two admitting bytes the convert stage can
only dead-letter.

The decisive assertion in these proofs is `store.writes == 0`: a refusal that
still wrote the raw object would have left exactly the durable garbage D104
exists to prevent.
"""

from typing import cast
from uuid import UUID

import pytest

from rememberstack.adapters.converters import build_conversion_routes
from rememberstack.model import DocumentUpload
from rememberstack.model import ObjectKey
from rememberstack.model import UnroutableMimeError
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.workers.e0 import UploadIngestor

_DEPLOYMENT_ID = UUID("103a0000-0000-0000-0000-000000000001")
_ROUTES = {"text/markdown": "passthrough", "text/plain": "passthrough"}


class AllowingAdmission:
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


class RecordingStore:
    """Object-store fake proving refusal happens before the first write."""

    def __init__(self) -> None:
        self.writes = 0

    def read_bytes(self, *, key: ObjectKey) -> bytes:
        raise AssertionError(f"unexpected read of {key.root}")

    def write_bytes(
        self, *, key: ObjectKey, content: bytes, storage_class: str | None = None
    ) -> None:
        self.writes += 1


def _ingestor(store: RecordingStore, *, routable: frozenset[str]) -> UploadIngestor:
    """Build the E0 gate over a recording store and an open admission."""
    return UploadIngestor(
        catalog=cast(DocumentCatalog, object()),
        raw_store=store,
        admission=AllowingAdmission(),
        routable_mimes=routable,
    )


def _run(ingestor: UploadIngestor, *, mime: str, observed: bool) -> None:
    """Drive whichever E0 entry point the case is proving."""
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


@pytest.mark.parametrize("observed", (False, True))
def test_unrouted_mime_is_refused_before_any_raw_write(observed: bool) -> None:
    """Both E0 entry points refuse, so no surface can bypass the gate.

    `ingest` is the one-shot upload path the HTTP surface and the MCP tool
    use; `ingest_observed` is the lineage path the connector sync worker uses.
    Proving both is what makes the gate universal rather than HTTP-only.
    """
    store = RecordingStore()
    with pytest.raises(UnroutableMimeError):
        _run(
            _ingestor(store, routable=frozenset(_ROUTES)),
            mime="audio/mpeg",
            observed=observed,
        )
    assert store.writes == 0


def test_the_refusal_carries_what_the_deployment_does_convert() -> None:
    """The error is renderable: a surface can tell the caller what to send."""
    with pytest.raises(UnroutableMimeError) as caught:
        _run(
            _ingestor(RecordingStore(), routable=frozenset(_ROUTES)),
            mime="application/zip",
            observed=False,
        )
    assert caught.value.mime == "application/zip"
    assert caught.value.supported_mimes == ("text/markdown", "text/plain")


def test_a_composition_cannot_omit_the_route_table() -> None:
    """There is no opt-out: `routable_mimes` is a required argument.

    An earlier draft defaulted it to None, meaning "no check". That made the
    gate as universal as every composer remembering to pass it — and an
    invariant with a silent opt-out is not an invariant. Every deployment has
    a route table (the settings default is the stock text table), so omission
    expresses nothing except a mistake, and this proves it is refused loudly.
    """
    with pytest.raises(TypeError, match="routable_mimes"):
        UploadIngestor(  # type: ignore[call-arg]
            catalog=cast(DocumentCatalog, object()),
            raw_store=RecordingStore(),
            admission=AllowingAdmission(),
        )


def test_matching_is_exact_so_the_gate_is_never_looser_than_the_worker() -> None:
    """A parameterised MIME is refused, because the router would refuse it too.

    `ConversionRouter.converter_for` is an exact dict lookup. If the gate
    normalised `text/plain; charset=utf-8` down to `text/plain` and the worker
    did not, the upload would be admitted and then dead-lettered — the exact
    outcome D104 exists to prevent. Normalisation belongs in the router, where
    both callers inherit it.
    """
    store = RecordingStore()
    with pytest.raises(UnroutableMimeError):
        _run(
            _ingestor(store, routable=frozenset(_ROUTES)),
            mime="text/plain; charset=utf-8",
            observed=False,
        )
    assert store.writes == 0


def test_routability_is_decided_before_the_d74_admission_query() -> None:
    """An unroutable input is refused whatever its forget state.

    Ordering is a decision, not an accident: routability is the cheaper and
    more fundamental question, so answering it first avoids an admission query
    for a request that cannot be accepted, and stops a forget-state error from
    masking a plain "we do not convert that". This admission fake fails the
    test if it is ever consulted for an unroutable type.
    """

    class ExplodingAdmission:
        def guard_ingest(
            self,
            *,
            deployment_id: UUID,
            source_kind: str,
            source_ref: str,
            content_hash: str,
        ) -> None:
            raise AssertionError("D74 consulted for an unroutable input")

    ingestor = UploadIngestor(
        catalog=cast(DocumentCatalog, object()),
        raw_store=RecordingStore(),
        admission=ExplodingAdmission(),
        routable_mimes=frozenset(_ROUTES),
    )
    with pytest.raises(UnroutableMimeError):
        _run(ingestor, mime="audio/mpeg", observed=False)


def test_the_gate_and_the_router_read_the_same_key_set() -> None:
    """The equivalence D104 rests on: configured keys are the router's keys.

    The gate tests membership in the configured route-name table while the
    worker tests membership in the built router. That is only safe because
    `build_conversion_routes` materialises exactly the configured keys — it
    refuses composition on an unknown adapter rather than silently dropping a
    route. If this stopped holding, the gate could admit what the worker
    dead-letters.
    """
    assert frozenset(build_conversion_routes(route_names=_ROUTES)) == frozenset(_ROUTES)
