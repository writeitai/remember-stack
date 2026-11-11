"""Original-view lifecycle for the ordinary D152 PostgreSQL capture adapter.

The operator supplies an independently qualified profile and ordinary collector
connection factory. This seam does real native acquisition, allocation sampling
and fixed catalog reads; it does not qualify clocks/profiles, classify provider
objects, persist completion or create a complete financial capture. Arm the
provider independently before entering this context. No provider work runs
under the engine's short guard.
"""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import time
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from rememberstack.core.storage_capture import capture_request_id
from rememberstack.core.storage_capture import member_payload_digest
from rememberstack.core.storage_capture import terminal_receipt_digest
from rememberstack.model.storage_capture import Digest
from rememberstack.model.storage_capture import StorageCaptureRequest
from rememberstack.model.storage_capture import StorageNativeWitness
from rememberstack.model.storage_capture import StorageTerminalMember
from rememberstack.model.storage_capture import UnsignedDecimal
from rememberstack.spine.storage_capture_catalog import CATALOG_FUNCTION
from rememberstack.spine.storage_capture_catalog import CATALOG_PAGE_LIMIT
from rememberstack.spine.storage_capture_catalog import NativeCatalogAcquisition
from rememberstack.spine.storage_capture_catalog import NativeCatalogIdentity
from rememberstack.spine.storage_capture_catalog import NativeReferencePage
from rememberstack.spine.storage_capture_catalog import NativeStorageConfiguration
from rememberstack.spine.storage_capture_catalog import NativeStoreReference
from rememberstack.spine.storage_capture_catalog import NativeTerminalPage
from rememberstack.spine.storage_capture_guard import NativeRelationGuardSample

_GUARD_FUNCTION = "rememberstack_capture_internal.guard_relation_allocation"
_GUARD_LIFETIME_US = 10_000_000


class NativeStorageCaptureRefused(RuntimeError):
    """A content-free refusal; no reconnect, re-lock or historical repair."""


class NativeCaptureProfile(BaseModel):
    """Operator-selected qualification, never request-selected clock or role.

    Loading this model is not qualification. The caller must authenticate the
    accepted immutable profile documents and their source/schema/clock evidence.
    The request is checked against this configured profile before acquisition.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )
    profile_digest: Digest
    source_digest: Digest
    schema_digest: Digest
    schema_profile_digest: Digest
    clock_profile_digest: Digest
    clock_error_us: UnsignedDecimal
    view_lifetime_ms: int = Field(gt=0)
    release_margin_us: int = Field(gt=0, lt=_GUARD_LIFETIME_US)


@dataclass(frozen=True, kw_only=True)
class _ClockRead:
    """One native UTC clock reading bracketed only for clock-step detection."""

    unix_us: int
    backend_pid: int
    transaction_id: str | None
    database_oid: str
    postmaster_started_unix_us: str
    sent_monotonic_ns: int
    received_monotonic_ns: int


class NativeCapturedStorageView:
    """The retained reader after definite short-guard release.

    Allocations are the immutable records sampled under that guard. Streaming
    never re-reads file sizes, opens another pool or exports a snapshot handle.
    A context owns this object; readers cannot resume after loss or close.
    """

    def __init__(
        self,
        *,
        connection: psycopg.Connection,
        acquisition: NativeCatalogAcquisition,
        allocation_sample: NativeRelationGuardSample,
        witness: StorageNativeWitness,
    ) -> None:
        """Bind only the original native connection and completed boundary."""
        if acquisition.storage is None or acquisition.deployment_id is None:
            raise NativeStorageCaptureRefused("native_view_not_bound")
        self._connection = connection
        self._identity = acquisition.identity
        self._deployment_id = acquisition.deployment_id
        self.storage: NativeStorageConfiguration = acquisition.storage
        self.allocation_sample = allocation_sample
        self.witness = witness
        self._closed = False

    def iter_store_references(self) -> Iterator[NativeStoreReference]:
        """Read all native locator seeds, including failed/superseded rows.

        This is not the complete classified member set: immutable artifact
        manifests, store trees, K history and unmatched provider resources still
        require the engine's qualified store/profile classifier.
        """
        after = ("", "")
        while True:
            page = NativeReferencePage.model_validate_json(
                self._page(section="references", after=after)
            )
            self._check_identity(
                identity=page.identity, deployment_id=page.deployment_id
            )
            if not page.rows:
                return
            for row in page.rows:
                key = (row.native_class, row.native_id.get_secret_value())
                if key <= after:
                    raise NativeStorageCaptureRefused("native_reference_order")
                after = key
                yield row

    def iter_terminal_members(self) -> Iterator[StorageTerminalMember]:
        """Emit every actually visible committed outcome, without ACK/range cuts."""
        after = ("", "")
        while True:
            page = NativeTerminalPage.model_validate_json(
                self._page(section="terminals", after=after)
            )
            self._check_identity(
                identity=page.identity, deployment_id=page.deployment_id
            )
            if not page.rows:
                return
            for outcome in page.rows:
                key = str(outcome.measurement_id)
                if key <= after[1]:
                    raise NativeStorageCaptureRefused("native_terminal_order")
                after = ("", key)
                member = StorageTerminalMember(
                    opaque_id=outcome.measurement_id.hex,
                    payload_digest="0" * 64,
                    measurement_id=outcome.measurement_id.hex,
                    outcome=outcome.outcome,
                    receipt_digest=terminal_receipt_digest(outcome=outcome),
                )
                yield member.model_copy(
                    update={"payload_digest": member_payload_digest(member=member)}
                )

    def close(self) -> None:
        """End the original view; cleanup never reconnects or reconstructs it."""
        self._closed = True
        self._connection.close()

    def check_continuity(self) -> None:
        """Prove this original reader still lives without resampling allocation."""
        page = NativeReferencePage.model_validate_json(
            self._page(section="identity", after=("", ""))
        )
        self._check_identity(identity=page.identity, deployment_id=page.deployment_id)

    def _page(self, *, section: str, after: tuple[str, str]) -> str:
        """Read one bounded page through the only fixed catalog operation."""
        if (
            self._closed
            or self._connection.closed
            or self._connection.info.transaction_status != TransactionStatus.INTRANS
        ):
            raise NativeStorageCaptureRefused("native_view_lost")
        row = self._connection.execute(
            f"SELECT {CATALOG_FUNCTION}(%s,%s,%s,%s)",
            (section, *after, CATALOG_PAGE_LIMIT),
        ).fetchone()
        if row is None:
            raise NativeStorageCaptureRefused("native_page_missing")
        return json.dumps(row[0])

    def _check_identity(
        self, *, identity: NativeCatalogIdentity, deployment_id: UUID
    ) -> None:
        """Refuse any new backend, transaction, scope or snapshot between pages."""
        if identity != self._identity or deployment_id != self._deployment_id:
            raise NativeStorageCaptureRefused("native_view_identity_changed")


class NativeStorageCaptureCatalog:
    """Acquire the real native cut, then stream from its one original view."""

    def __init__(
        self,
        *,
        connect: Callable[[], psycopg.Connection],
        profile: NativeCaptureProfile,
    ) -> None:
        """Bind an ordinary collector connection factory and accepted profile."""
        self._connect = connect
        self._profile = profile

    @contextmanager
    def open_view(
        self, *, request: StorageCaptureRequest
    ) -> Iterator[NativeCapturedStorageView]:
        """Acquire before earliest T, release after latest T, retain original RR.

        Both connections must be fresh and idle; prior configuration queries
        complete before BEGIN. The first data SELECT of the reader is bracketed
        by the still-held original guard's actual native clock. The guard and
        view close on every exit, including cancellation and provider failure.
        """
        profile = self._profile
        if (
            request.native_capture_profile_digest != profile.profile_digest
            or request.schema_profile_digest != profile.schema_profile_digest
        ):
            raise NativeStorageCaptureRefused("native_profile_mismatch")
        guard = self._connect()
        reader: psycopg.Connection | None = None
        captured: NativeCapturedStorageView | None = None
        try:
            reader = self._connect()
            if reader is guard:
                raise NativeStorageCaptureRefused("native_distinct_sessions_required")
            _prepare(connection=guard, transaction_timeout_ms=10_000)
            _prepare(connection=reader, transaction_timeout_ms=profile.view_lifetime_ms)
            initial = _read_clock(connection=guard)
            error_us = int(profile.clock_error_us)
            cut = int(request.cut_at_unix_us)
            deadline = cut + error_us + profile.release_margin_us
            if (
                initial.unix_us >= cut - error_us
                or deadline - initial.unix_us > _GUARD_LIFETIME_US
            ):
                raise NativeStorageCaptureRefused("native_cut_outside_boundary_budget")
            guard.execute("BEGIN ISOLATION LEVEL READ COMMITTED READ WRITE")
            row = guard.execute(f"SELECT {_GUARD_FUNCTION}(%s)", (deadline,)).fetchone()
            if row is None:
                raise NativeStorageCaptureRefused("native_guard_missing")
            allocation = NativeRelationGuardSample.model_validate_json(
                json.dumps(row[0])
            )
            if UUID(allocation.deployment_id).hex != request.deployment_id:
                raise NativeStorageCaptureRefused("native_deployment_mismatch")
            before = _checked_clock(
                connection=guard,
                previous=initial,
                allocation=allocation,
                error_us=error_us,
                deadline=deadline,
            )
            reader.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = reader.execute(
                f"SELECT {CATALOG_FUNCTION}('acquire','','',%s)", (CATALOG_PAGE_LIMIT,)
            ).fetchone()
            if row is None:
                raise NativeStorageCaptureRefused("native_view_missing")
            acquisition = NativeCatalogAcquisition.model_validate_json(
                json.dumps(row[0])
            )
            after = _checked_clock(
                connection=guard,
                previous=before,
                allocation=allocation,
                error_us=error_us,
                deadline=deadline,
            )
            _check_acquisition(
                acquisition=acquisition,
                allocation=allocation,
                clock=after,
                deployment_id=request.deployment_id,
            )
            if (
                int(allocation.sample_before_unix_us) >= cut - error_us
                or after.unix_us >= cut - error_us
            ):
                raise NativeStorageCaptureRefused("native_acquisition_missed_cut")
            # The complete immutable allocation tuple is already outside the
            # measured database. No physical size query follows this point.
            allocation_digest = native_allocation_records_digest(sample=allocation)
            checked = after
            while checked.unix_us <= cut + error_us:
                time.sleep(
                    min((cut + error_us + 1 - checked.unix_us) / 1_000_000, 0.025)
                )
                checked = _checked_clock(
                    connection=guard,
                    previous=checked,
                    allocation=allocation,
                    error_us=error_us,
                    deadline=deadline,
                )
            guard.execute("ROLLBACK")
            released = _read_clock(connection=guard)
            _check_clock_step(previous=checked, current=released, error_us=error_us)
            if (
                released.backend_pid != allocation.backend_pid
                or released.transaction_id is not None
                or guard.info.transaction_status != TransactionStatus.IDLE
                or released.unix_us >= deadline
            ):
                raise NativeStorageCaptureRefused("native_release_unproved")
            row = reader.execute(
                f"SELECT {CATALOG_FUNCTION}('bind','','',%s)", (CATALOG_PAGE_LIMIT,)
            ).fetchone()
            if row is None:
                raise NativeStorageCaptureRefused("native_binding_missing")
            bound = NativeCatalogAcquisition.model_validate_json(json.dumps(row[0]))
            if (
                bound.identity != acquisition.identity
                or bound.deployment_id is None
                or bound.deployment_id.hex != request.deployment_id
                or bound.storage is None
            ):
                raise NativeStorageCaptureRefused("native_binding_scope_mismatch")
            identity = acquisition.identity
            witness = StorageNativeWitness(
                capture_id=capture_request_id(request=request),
                deployment_id=request.deployment_id,
                profile_digest=profile.profile_digest,
                source_digest=profile.source_digest,
                schema_digest=profile.schema_digest,
                backend_identity=f"pid:{identity.backend_pid}:postmaster:{identity.postmaster_started_unix_us}",
                transaction_identity="virtual:"
                + identity.virtual_transaction_id.replace("/", ":"),
                view_identity="snapshot:"
                + hashlib.sha256(
                    identity.snapshot_identity.encode("ascii")
                ).hexdigest(),
                view_acquired_before_unix_us=str(before.unix_us),
                view_acquired_after_unix_us=str(after.unix_us),
                guard_acquired_before_unix_us=allocation.guard_started_unix_us,
                guard_acquired_after_unix_us=allocation.sample_before_unix_us,
                guard_checked_after_unix_us=str(checked.unix_us),
                release_before_unix_us=str(checked.unix_us),
                release_after_unix_us=str(released.unix_us),
                clock_profile_digest=profile.clock_profile_digest,
                clock_error_us=profile.clock_error_us,
                allocation_records_digest=allocation_digest,
            )
            captured = NativeCapturedStorageView(
                connection=reader,
                acquisition=bound,
                allocation_sample=allocation,
                witness=witness,
            )
            guard.close()
            captured.check_continuity()
            yield captured
        finally:
            if captured is not None:
                captured.close()
            elif reader is not None:
                reader.close()
            guard.close()


def native_allocation_records_digest(*, sample: NativeRelationGuardSample) -> str:
    """Bind exact in-guard records under the private native profile's schema."""
    payload = {
        "contract": "rememberstack.native_relation_allocations.v1",
        "deployment_id": UUID(sample.deployment_id).hex,
        "schema_head": sample.schema_head,
        "allocated_fork_bytes": sample.allocated_fork_bytes,
        "physical_table_total_bytes": sample.physical_table_total_bytes,
        "relations": [row.model_dump(mode="json") for row in sample.relations],
    }
    encoded = json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(
        b"rememberstack.native_relation_allocations.v1\0" + encoded
    ).hexdigest()


def _prepare(*, connection: psycopg.Connection, transaction_timeout_ms: int) -> None:
    """Bound a fresh connection before its original capture transaction begins."""
    if (
        connection.closed
        or connection.info.transaction_status != TransactionStatus.IDLE
    ):
        raise NativeStorageCaptureRefused("native_connection_not_fresh")
    connection.autocommit = True
    for name, value in (
        ("transaction_timeout", f"{transaction_timeout_ms}ms"),
        ("statement_timeout", "10s"),
        ("lock_timeout", "250ms"),
    ):
        connection.execute("SELECT pg_catalog.set_config(%s,%s,false)", (name, value))


def _read_clock(*, connection: psycopg.Connection) -> _ClockRead:
    """Observe native UTC and transaction identity, never a host-wall timestamp."""
    sent = time.monotonic_ns()
    row = connection.execute(
        "SELECT floor(extract(epoch FROM pg_catalog.clock_timestamp())*1000000)::bigint,"
        "pg_catalog.pg_backend_pid(),pg_catalog.pg_current_xact_id_if_assigned()::text,"
        "(SELECT oid::text FROM pg_catalog.pg_database WHERE datname=current_database()),"
        "floor(extract(epoch FROM pg_catalog.pg_postmaster_start_time())*1000000)::text"
    ).fetchone()
    received = time.monotonic_ns()
    if row is None:
        raise NativeStorageCaptureRefused("native_clock_missing")
    return _ClockRead(
        unix_us=row[0],
        backend_pid=row[1],
        transaction_id=row[2],
        database_oid=row[3],
        postmaster_started_unix_us=row[4],
        sent_monotonic_ns=sent,
        received_monotonic_ns=received,
    )


def _checked_clock(
    *,
    connection: psycopg.Connection,
    previous: _ClockRead,
    allocation: NativeRelationGuardSample,
    error_us: int,
    deadline: int,
) -> _ClockRead:
    """Prove the original guard transaction still lives within its deadline."""
    current = _read_clock(connection=connection)
    _check_clock_step(previous=previous, current=current, error_us=error_us)
    if (
        current.backend_pid != allocation.backend_pid
        or current.transaction_id != allocation.transaction_id
        or current.unix_us >= deadline
    ):
        raise NativeStorageCaptureRefused("native_guard_continuity_lost")
    return current


def _check_clock_step(
    *, previous: _ClockRead, current: _ClockRead, error_us: int
) -> None:
    """Refuse native clock steps outside the qualified error and I/O brackets."""
    delta = current.unix_us - previous.unix_us
    lower = (current.sent_monotonic_ns - previous.received_monotonic_ns) // 1000
    upper = (current.received_monotonic_ns - previous.sent_monotonic_ns) // 1000 + 1
    if delta < 0 or not lower - 2 * error_us <= delta <= upper + 2 * error_us:
        raise NativeStorageCaptureRefused("native_clock_discontinuity")


def _check_acquisition(
    *,
    acquisition: NativeCatalogAcquisition,
    allocation: NativeRelationGuardSample,
    clock: _ClockRead,
    deployment_id: str,
) -> None:
    """Require the same actual deployment, collector and database server."""
    identity = acquisition.identity
    if (
        acquisition.deployment_id is not None
        or acquisition.storage is not None
        or UUID(allocation.deployment_id).hex != deployment_id
        or identity.session_login != allocation.session_login
        or identity.backend_pid == allocation.backend_pid
        or identity.database_oid != clock.database_oid
        or identity.postmaster_started_unix_us != clock.postmaster_started_unix_us
        or not identity.virtual_transaction_id
        or not identity.snapshot_identity
    ):
        raise NativeStorageCaptureRefused("native_view_scope_mismatch")
