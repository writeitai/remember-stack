"""One admitted managed same-cell/same-T operation, including a one-scope batch."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from concurrent.futures import as_completed
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field

import psycopg
from pydantic import SecretStr

from rememberstack.adapters.fsit_storage_capture import ProviderRequest
from rememberstack.adapters.fsit_storage_capture_transport import (
    open_provider_capture_batch,
)
from rememberstack.adapters.fsit_storage_capture_transport import ProviderCaptureScope
from rememberstack.adapters.fsit_storage_capture_transport import (
    RawProviderCaptureTransport,
)
from rememberstack.core.storage_capture import capture_request_id
from rememberstack.model.storage_capture import StorageCaptureRequest
from rememberstack.ports.storage_capture import StorageCaptureEvidenceStore
from rememberstack.ports.storage_capture import StorageCaptureObjectStore
from rememberstack.profiles.managed_storage_capture import check_capture_configuration
from rememberstack.profiles.managed_storage_capture import CompletedStorageCapture
from rememberstack.profiles.managed_storage_capture import replay_completed_capture
from rememberstack.profiles.managed_storage_capture import StorageCaptureCatalog
from rememberstack.profiles.storage_capture_profile import ManagedStorageCaptureProfile


@dataclass(frozen=True, kw_only=True)
class ManagedStorageCaptureInput:
    """One selected Project's exact accepted documents and scoped capabilities."""

    request: StorageCaptureRequest
    profile: ManagedStorageCaptureProfile
    collector_connect: Callable[[], psycopg.Connection] = field(repr=False)
    stores: Mapping[str, StorageCaptureObjectStore] = field(repr=False)
    evidence_store: StorageCaptureEvidenceStore = field(repr=False)
    identity_key: SecretStr = field(repr=False)
    provider_grant: SecretStr = field(repr=False)

    def provider_scope(self) -> ProviderCaptureScope:
        """Derive the exact native provider scope from the bound four stores."""
        binding = self.profile.bindings
        return ProviderCaptureScope(
            request=ProviderRequest(
                cell_id=binding.provider_cell_id,
                cut_at_unix_us=self.request.cut_at_unix_us,
                filesystem_ids=tuple(
                    sorted(row.filesystem_id for row in binding.filesystems)
                ),
                organisation_id=binding.provider_organisation_id,
                protocol="fsit-capture-v1",
            ),
            grant=self.provider_grant,
        )


def capture_storage_batch(
    *,
    inputs: tuple[ManagedStorageCaptureInput, ...],
    provider_transport: RawProviderCaptureTransport,
    max_scopes: int,
    maximum_frame_bytes: int,
) -> tuple[CompletedStorageCapture, ...]:
    """Arm admitted original Project views concurrently around one provider cut.

    Use the SAME required ``max_scopes`` from the qualified job run profile as
    the due-Project SELECT. This is an operator session/memory admission bound,
    never a customer quota or a default. All exact scopes are known before any
    native pause; an oversized/mixed batch refuses before transport launch.
    Completed immutable evidence replays without a new native/provider view.
    Each live worker owns and finally closes its two ordinary native sessions.
    The raw provider context closes before failed workers are joined, so no
    blocked stream waiter or subprocess survives cancellation.
    """
    if (
        not inputs
        or isinstance(max_scopes, bool)
        or max_scopes <= 0
        or len(inputs) > max_scopes
        or isinstance(maximum_frame_bytes, bool)
        or maximum_frame_bytes <= 0
    ):
        raise ValueError("capture_batch_admission_bound")
    first = inputs[0]
    seen: set[str] = set()
    for item in inputs:
        if (
            item.request.cut_at_unix_us != first.request.cut_at_unix_us
            or item.profile.bindings.provider_cell_id
            != first.profile.bindings.provider_cell_id
            or item.request.deployment_id in seen
        ):
            raise ValueError("capture_batch_exact_scope")
        seen.add(item.request.deployment_id)

    results: dict[int, CompletedStorageCapture] = {}
    pending: list[tuple[int, ManagedStorageCaptureInput]] = []
    for index, item in enumerate(inputs):
        previous = item.evidence_store.read_manifest(
            capture_id=capture_request_id(request=item.request)
        )
        if previous is not None:
            results[index] = replay_completed_capture(
                evidence_store=item.evidence_store, request=item.request, body=previous
            )
        else:
            check_capture_configuration(
                request=item.request,
                profile=item.profile,
                stores=item.stores,
                identity_key=item.identity_key,
            )
            pending.append((index, item))
    if not pending:
        return tuple(results[index] for index in range(len(inputs)))

    executor = ThreadPoolExecutor(
        max_workers=len(pending), thread_name_prefix="remember-storage-capture"
    )
    futures: dict[Future[CompletedStorageCapture], int] = {}
    try:
        with open_provider_capture_batch(
            transport=provider_transport,
            scopes=tuple(item.provider_scope() for _, item in pending),
            max_scopes=max_scopes,
            maximum_frame_bytes=maximum_frame_bytes,
        ) as provider:
            for index, item in pending:
                catalog = StorageCaptureCatalog(
                    collector_connect=item.collector_connect,
                    provider=provider,
                    stores=item.stores,
                    evidence_store=item.evidence_store,
                    profile=item.profile,
                    identity_key=item.identity_key,
                )
                futures[executor.submit(catalog.capture_once, request=item.request)] = (
                    index
                )
            for future in as_completed(futures):
                results[futures[future]] = future.result()
    finally:
        # Exiting the source first closes blocking I/O; workers then unwind
        # their native contexts before shutdown returns to the caller.
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    return tuple(results[index] for index in range(len(inputs)))
