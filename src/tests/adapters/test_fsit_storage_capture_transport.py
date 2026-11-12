"""Batched byte-transport behavior using explicit synthetic provider vectors.

These check stream ordering and cleanup, never a native cut or scope authority.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from threading import Barrier
from threading import Event
from unittest.mock import Mock
from unittest.mock import patch

from pydantic import SecretStr
import pytest

from rememberstack.adapters.fsit_storage_capture import provider_canonical_bytes
from rememberstack.adapters.fsit_storage_capture import provider_page_digest
from rememberstack.adapters.fsit_storage_capture import provider_record_digest
from rememberstack.adapters.fsit_storage_capture import ProviderItem
from rememberstack.adapters.fsit_storage_capture import ProviderManifest
from rememberstack.adapters.fsit_storage_capture import ProviderPage
from rememberstack.adapters.fsit_storage_capture import ProviderPageReference
from rememberstack.adapters.fsit_storage_capture import ProviderStorageFlags
from rememberstack.adapters.fsit_storage_capture_transport import (
    open_provider_capture_batch,
)
from rememberstack.adapters.fsit_storage_capture_transport import ProviderCaptureScope
from rememberstack.adapters.fsit_storage_capture_transport import (
    ProviderCaptureUnavailable,
)
from rememberstack.adapters.fsit_storage_capture_transport import (
    RawProviderCaptureProcess,
)
from rememberstack.profiles.storage_capture_batch import _capture_project
from rememberstack.profiles.storage_capture_batch import ManagedStorageCaptureInput


def _manifest(*, organisation: str) -> ProviderManifest:
    """Derive an empty synthetic byte case from the accepted actual Go vector."""
    vector = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/storage_capture/fsit_bc8482ab_manifest.json"
        ).read_bytes()
    )
    manifest = ProviderManifest.model_validate_json(json.dumps(vector["manifest"]))
    request = manifest.request.model_copy(update={"organisation_id": organisation})
    scope = request.model_dump(mode="json", exclude={"cut_at_unix_us", "protocol"})
    manifest = manifest.model_copy(
        update={
            "request": request,
            "capture_id": hashlib.sha256(
                b"fsit-capture-v1\0" + provider_canonical_bytes(record=request)
            ).hexdigest(),
            "scope_digest": hashlib.sha256(
                b"fsit-scope-v1\0"
                + json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "item_count": "0",
            "page_count": "0",
            "pages": (),
            "sets": tuple(
                row.model_copy(update={"entry_count": "0", "root_materialized": False})
                for row in manifest.sets
            ),
        }
    )
    return manifest.model_copy(
        update={
            "manifest_digest": provider_record_digest(
                domain="fsit-manifest-v1", record=manifest, omit="manifest_digest"
            )
        }
    )


def _scope(*, manifest: ProviderManifest) -> ProviderCaptureScope:
    """Bind a clearly synthetic finite grant used only in transport unit cases."""
    return ProviderCaptureScope(
        request=manifest.request,
        grant=SecretStr(
            "mtblc_" + base64.urlsafe_b64encode(b"x" * 32).decode().rstrip("=")
        ),
    )


def _one_page(*, manifest: ProviderManifest) -> tuple[ProviderManifest, ProviderPage]:
    """Add one synthetic regular item to exercise a partially consumed group."""
    item = ProviderItem(
        alias_group=None,
        entry_kind="regular",
        filesystem_id=manifest.request.filesystem_ids[0],
        inline_bytes="0",
        logical_bytes="0",
        path_id="a" * 64,
        version_id="b" * 64,
        storage_flags=ProviderStorageFlags(
            chunk_backed=False,
            chunk_manifest=False,
            inline_content=False,
            metadata_gzip=False,
            remote_backed=False,
        ),
    )
    page = ProviderPage(
        capture_id=manifest.capture_id,
        item_count="1",
        items=(item,),
        page_digest="0" * 64,
        page_ordinal="0",
        protocol="fsit-page-v1",
    )
    page = page.model_copy(update={"page_digest": provider_page_digest(page=page)})
    manifest = manifest.model_copy(
        update={
            "item_count": "1",
            "page_count": "1",
            "pages": (
                ProviderPageReference(
                    item_count="1", page_digest=page.page_digest, page_ordinal="0"
                ),
            ),
            "sets": (
                manifest.sets[0].model_copy(
                    update={"entry_count": "1", "root_materialized": True}
                ),
                *manifest.sets[1:],
            ),
        }
    )
    manifest = manifest.model_copy(
        update={
            "manifest_digest": provider_record_digest(
                domain="fsit-manifest-v1", record=manifest, omit="manifest_digest"
            )
        }
    )
    return manifest, page


class _RawTransport:
    """Finite in-test bytes, never an operational provider implementation."""

    def __init__(
        self,
        *,
        manifests: tuple[ProviderManifest, ...],
        pages: tuple[ProviderPage, ...] = (),
    ) -> None:
        """Serialize the sole accepted fixed-host frame spelling."""
        frames: list[dict[str, object]] = []
        for manifest in manifests:
            frames.append(
                {
                    "capture_id": manifest.capture_id,
                    "manifest": manifest.model_dump(mode="json"),
                    "protocol": "fsit-capture-v1",
                    "type": "manifest",
                }
            )
            for page in pages:
                if page.capture_id == manifest.capture_id:
                    frames.append(
                        {
                            "capture_id": manifest.capture_id,
                            "page": page.model_dump(mode="json"),
                            "protocol": "fsit-capture-v1",
                            "type": "page",
                        }
                    )
        self.lines = iter(
            json.dumps(
                frame, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
            + b"\n"
            for frame in frames
        )
        self.stdin = b""
        self.closed = False
        self.reaped = False

    @contextmanager
    def open(self, *, stdin: bytes) -> Iterator[RawProviderCaptureProcess]:
        """Record exact bytes and unconditionally close this test-only stream."""
        self.stdin = stdin
        try:
            yield self
        finally:
            self.closed = True

    def read_line(self, *, maximum_bytes: int) -> bytes | None:
        """Return the next finite line; engine still enforces its actual bound."""
        return next(self.lines, None)

    def check_success(self) -> None:
        """Record that successful EOF includes the original child's exit check."""
        self.reaped = True


def test_two_scopes_claim_before_waiting_and_share_one_exact_stream() -> None:
    """Arm both local operations before ordered groups; use one envelope/EOF."""
    manifests = (_manifest(organisation="1" * 32), _manifest(organisation="2" * 32))
    scopes = tuple(_scope(manifest=manifest) for manifest in manifests)
    transport = _RawTransport(manifests=manifests)
    barrier = Barrier(parties=2)
    with open_provider_capture_batch(
        transport=transport, scopes=scopes, max_scopes=2, maximum_frame_bytes=65536
    ) as source:

        def collect(*, index: int) -> str:
            """Represent post-guard byte consumption only, not native acquisition."""
            with source.capture(request=scopes[index].request) as pending:
                barrier.wait(timeout=3)
                manifest = pending.complete()
                assert tuple(pending.iter_pages(manifest=manifest)) == ()
                assert not transport.reaped
                pending.check_transport_complete()
                return manifest.capture_id

        with ThreadPoolExecutor(max_workers=2) as executor:
            second = executor.submit(collect, index=1)
            first = executor.submit(collect, index=0)
            assert first.result(timeout=3) == manifests[0].capture_id
            assert second.result(timeout=3) == manifests[1].capture_id
    envelope = json.loads(transport.stdin)
    assert envelope["operation"] == "capture"
    assert len(envelope["requests"]) == 2
    assert tuple(row["request"] for row in envelope["requests"]) == tuple(
        scope.request.model_dump(mode="json") for scope in scopes
    )
    assert transport.closed and transport.reaped


def test_scope_failure_wakes_other_group_waiter_and_closes_transport() -> None:
    """Protocol loss wakes ordered waiters and closes the original transport."""
    manifests = (_manifest(organisation="1" * 32), _manifest(organisation="2" * 32))
    scopes = tuple(_scope(manifest=manifest) for manifest in manifests)
    transport = _RawTransport(manifests=())
    claimed = Event()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with pytest.raises(
            ProviderCaptureUnavailable, match="provider_batch_cancelled"
        ):
            with open_provider_capture_batch(
                transport=transport,
                scopes=scopes,
                max_scopes=2,
                maximum_frame_bytes=65536,
            ) as source:

                def wait_second() -> None:
                    """Wait for a prior group that is deliberately cancelled."""
                    with source.capture(request=scopes[1].request) as pending:
                        claimed.set()
                        pending.complete()

                future = executor.submit(wait_second)
                assert claimed.wait(timeout=3)
                with pytest.raises(
                    ProviderCaptureUnavailable, match="provider_frame_bound_or_eof"
                ):
                    with source.capture(request=scopes[0].request) as pending:
                        pending.complete()
                with pytest.raises(
                    ProviderCaptureUnavailable, match="provider_batch_cancelled"
                ):
                    future.result(timeout=3)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    assert transport.closed


def test_local_scope_failure_drains_only_its_group_and_preserves_next() -> None:
    """One Project failure cannot discard the other original provider group."""
    first, page = _one_page(manifest=_manifest(organisation="1" * 32))
    manifests = (first, _manifest(organisation="2" * 32))
    scopes = tuple(_scope(manifest=manifest) for manifest in manifests)
    transport = _RawTransport(manifests=manifests, pages=(page,))
    with open_provider_capture_batch(
        transport=transport, scopes=scopes, max_scopes=2, maximum_frame_bytes=65536
    ) as source:
        with pytest.raises(RuntimeError, match="test_project_failure"):
            with source.capture(request=scopes[0].request) as pending:
                manifest = pending.complete()
                original = pending.iter_pages(manifest=manifest)
                assert next(original) == page
                raise RuntimeError("test_project_failure")
        with source.capture(request=scopes[1].request) as pending:
            manifest = pending.complete()
            assert manifest.capture_id == manifests[1].capture_id
            assert tuple(pending.iter_pages(manifest=manifest)) == ()
            pending.check_transport_complete()
    assert transport.closed and transport.reaped


def test_project_constructor_failure_is_claimed_and_drained_before_other_group() -> (
    None
):
    """An early setup failure cannot leave an unclaimed ordered group behind."""
    manifests = (_manifest(organisation="1" * 32), _manifest(organisation="2" * 32))
    scopes = tuple(_scope(manifest=manifest) for manifest in manifests)
    transport = _RawTransport(manifests=manifests)
    item = Mock(spec=ManagedStorageCaptureInput)
    item.collector_connect = Mock()
    item.request = Mock()
    item.profile = Mock()
    item.stores = {}
    item.evidence_store = Mock()
    item.identity_key = SecretStr("unit-fixture-only")
    with open_provider_capture_batch(
        transport=transport, scopes=scopes, max_scopes=2, maximum_frame_bytes=65536
    ) as source:
        with patch(
            "rememberstack.profiles.storage_capture_batch.StorageCaptureCatalog",
            side_effect=OSError("unit_constructor_failure"),
        ):
            with pytest.raises(OSError, match="unit_constructor_failure"):
                _capture_project(item=item, provider=source, scope=scopes[0])
        item.collector_connect.assert_not_called()
        with source.capture(request=scopes[1].request) as pending:
            manifest = pending.complete()
            assert manifest.capture_id == manifests[1].capture_id
            assert tuple(pending.iter_pages(manifest=manifest)) == ()
            assert not transport.reaped
            pending.check_transport_complete()
    assert transport.closed and transport.reaped
