"""One admitted managed same-cell/same-T operation, including a one-scope batch."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Mapping
from concurrent.futures import as_completed
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import field
import time

import psycopg
from pydantic import SecretStr

from rememberstack.adapters.fsit_storage_capture import ProviderRequest
from rememberstack.adapters.fsit_storage_capture_transport import (
    FsitStorageCaptureSource,
)
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
from rememberstack.spine.storage_capture_view import _check_clock_step
from rememberstack.spine.storage_capture_view import _GUARD_LIFETIME_US
from rememberstack.spine.storage_capture_view import _prepare
from rememberstack.spine.storage_capture_view import _read_clock
from rememberstack.spine.storage_capture_view import NativeCaptureProfile
from rememberstack.spine.storage_capture_view import NativeStorageCaptureRefused


def _prepared_collector(
    *,
    connect: Callable[[], psycopg.Connection],
    request: StorageCaptureRequest,
    profile: NativeCaptureProfile,
    check_active: Callable[[], None],
) -> Callable[[], psycopg.Connection]:
    """Wait on the original idle guard session before the finite native cut.

    The fixed D63 host admits at most60s preparation (host_cut_linux.go), while
    the existing native guard admits at most10s including its release margin.
    Only autocommit native clock reads occur here. Return that SAME connection
    as the original guard; no RR/lock/view is acquired, borrowed or replaced.
    """
    first = True

    def ordinary_connection() -> psycopg.Connection:
        """Open exactly one guard and one reader under their normal ownership."""
        nonlocal first
        check_active()
        connection = connect()
        if not first:
            return connection
        first = False
        try:
            _prepare(connection=connection, transaction_timeout_ms=10_000)
            previous = _read_clock(connection=connection)
            cut, error = int(request.cut_at_unix_us), int(profile.clock_error_us)
            if cut - previous.unix_us > 60_000_000 or previous.unix_us >= cut - error:
                raise NativeStorageCaptureRefused(
                    "native_cut_outside_preparation_budget"
                )
            deadline = cut + error + profile.release_margin_us
            while deadline - previous.unix_us > _GUARD_LIFETIME_US:
                check_active()
                time.sleep(
                    min(
                        (deadline - previous.unix_us - _GUARD_LIFETIME_US) / 1_000_000,
                        0.025,
                    )
                )
                current = _read_clock(connection=connection)
                _check_clock_step(previous=previous, current=current, error_us=error)
                previous = current
            check_active()
            return connection
        except BaseException:
            connection.close()
            raise

    return ordinary_connection


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


@dataclass(frozen=True, kw_only=True)
class ManagedStorageCaptureResult:
    """One selected Project's completed evidence or explicit unavailability."""

    request: StorageCaptureRequest
    completed: CompletedStorageCapture | None


def _capture_project(
    *,
    item: ManagedStorageCaptureInput,
    provider: FsitStorageCaptureSource,
    scope: ProviderCaptureScope,
) -> CompletedStorageCapture:
    """Claim the exact group before any Project setup can fail or allocate I/O."""
    with provider.capture(request=scope.request) as pending:
        catalog = StorageCaptureCatalog(
            collector_connect=_prepared_collector(
                connect=item.collector_connect,
                request=item.request,
                profile=item.profile.native,
                check_active=provider.check_active,
            ),
            provider=pending,
            stores=item.stores,
            evidence_store=item.evidence_store,
            profile=item.profile,
            identity_key=item.identity_key,
        )
        return catalog.capture_once(request=item.request)


def capture_storage_batch(
    *,
    inputs: tuple[ManagedStorageCaptureInput, ...],
    provider_transport: RawProviderCaptureTransport,
    max_scopes: int,
    maximum_frame_bytes: int,
) -> tuple[ManagedStorageCaptureResult, ...]:
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

    results: dict[int, CompletedStorageCapture | None] = {}
    pending: list[tuple[int, ManagedStorageCaptureInput]] = []
    for index, item in enumerate(inputs):
        try:
            previous = item.evidence_store.read_manifest(
                capture_id=capture_request_id(request=item.request)
            )
            if previous is not None:
                results[index] = replay_completed_capture(
                    evidence_store=item.evidence_store,
                    request=item.request,
                    body=previous,
                )
            else:
                check_capture_configuration(
                    request=item.request,
                    profile=item.profile,
                    stores=item.stores,
                    identity_key=item.identity_key,
                )
                pending.append((index, item))
        except Exception:
            results[index] = None
    if not pending:
        return tuple(
            ManagedStorageCaptureResult(request=item.request, completed=results[index])
            for index, item in enumerate(inputs)
        )

    executor = ThreadPoolExecutor(
        max_workers=len(pending), thread_name_prefix="remember-storage-capture"
    )
    futures: dict[Future[CompletedStorageCapture], int] = {}
    try:
        scopes = tuple(item.provider_scope() for _, item in pending)
        with open_provider_capture_batch(
            transport=provider_transport,
            scopes=scopes,
            max_scopes=max_scopes,
            maximum_frame_bytes=maximum_frame_bytes,
        ) as provider:
            for (index, item), scope in zip(pending, scopes, strict=True):
                futures[
                    executor.submit(
                        _capture_project, item=item, provider=provider, scope=scope
                    )
                ] = index
            for future in as_completed(futures):
                try:
                    results[futures[future]] = future.result()
                except Exception:
                    results[futures[future]] = None
    except Exception:
        # A broken original stream/context cannot qualify an incomplete scope.
        # Already completed replay stays independent of this new invocation.
        for index, _ in pending:
            results[index] = None
    finally:
        # Exiting the source first closes blocking I/O; workers then unwind
        # their native contexts before shutdown returns to the caller.
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    return tuple(
        ManagedStorageCaptureResult(request=item.request, completed=results[index])
        for index, item in enumerate(inputs)
    )
