"""Real original-view cuts using only the explicit capture_guard_* scratch DB.

Reuse the ordinary guard fixture, never the optional permissions regression.
No provider or UTC/profile qualification follows from these native fixtures.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from uuid import UUID
from uuid import uuid4

import psycopg
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from rememberstack.core.storage_capture import terminal_receipt_digest
from rememberstack.model.documents import UploadRecord
from rememberstack.model.metering import ManagedDocumentVersionOutcome
from rememberstack.model.metering import ManagedMeasurementDraft
from rememberstack.model.storage_capture import StorageCaptureRequest
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.spine.storage_capture_catalog import install_native_capture_catalog
from rememberstack.spine.storage_capture_view import NativeCaptureProfile
from rememberstack.spine.storage_capture_view import NativeStorageCaptureCatalog
from rememberstack.spine.storage_capture_view import NativeStorageCaptureRefused
from tests.spine.test_storage_capture_guard_pg import _Scope
from tests.spine.test_storage_capture_guard_pg import native_scope as native_scope


@pytest.fixture(scope="module")
def capture_scope(native_scope: _Scope) -> _Scope:
    """Add only the fixed reader beside the already installed guard."""
    with native_scope.connect(role=native_scope.admin_role) as admin:
        install_native_capture_catalog(
            connection=admin,
            definer_role=native_scope.definer_role,
            collector_role=native_scope.collector_role,
        )
    return native_scope


def _profile(*, lifetime_ms: int = 30_000) -> NativeCaptureProfile:
    """Use an explicit synthetic profile; it makes no production-clock claim."""
    return NativeCaptureProfile(
        profile_digest="1" * 64,
        source_digest="2" * 64,
        schema_digest="3" * 64,
        schema_profile_digest="4" * 64,
        clock_profile_digest="5" * 64,
        clock_error_us="1000",
        view_lifetime_ms=lifetime_ms,
        release_margin_us=500_000,
    )


def _request(*, scope: _Scope, lead_us: int = 1_000_000) -> StorageCaptureRequest:
    """Choose one future cut from native PostgreSQL UTC, not the host clock."""
    with scope.connect(role=scope.admin_role) as admin:
        row = admin.execute(
            "SELECT deployment_id,floor(extract(epoch FROM clock_timestamp())*1000000)"
            "::bigint FROM public.deployments"
        ).fetchone()
        assert row is not None
    return StorageCaptureRequest(
        deployment_id=row[0].hex,
        cut_at_unix_us=str(row[1] + lead_us),
        store_binding_digest="6" * 64,
        schema_profile_digest="4" * 64,
        classification_digest="7" * 64,
        native_capture_profile_digest="1" * 64,
        operator_mapping_digest="8" * 64,
    )


def _catalog(
    *,
    scope: _Scope,
    connect: Callable[[], psycopg.Connection] | None = None,
    lifetime_ms: int = 30_000,
) -> NativeStorageCaptureCatalog:
    """Connect as the actual collector, with no SET ROLE or owner credential."""
    return NativeStorageCaptureCatalog(
        connect=connect or (lambda: scope.connect(role=scope.collector_role)),
        profile=_profile(lifetime_ms=lifetime_ms),
    )


@dataclass(frozen=True, kw_only=True)
class _NativeVersion:
    """A real normal-upload lineage and managed measurement for terminal tests."""

    measurement_id: UUID
    version_id: UUID
    opaque_version: str


def _seed_version(*, scope: _Scope) -> _NativeVersion:
    """Use normal catalog writes; no model/provider or fake native witness."""
    engine = create_engine(
        make_url(scope.conninfo).set(drivername="postgresql+psycopg")
    )
    try:
        with scope.connect(role=scope.admin_role) as admin:
            row = admin.execute(
                "SELECT deployment_id FROM public.deployments"
            ).fetchone()
            assert row is not None
            deployment_id = row[0]
        catalog = DocumentCatalog(engine=engine)
        upload = catalog.record_upload(
            record=UploadRecord(
                deployment_id=deployment_id,
                doc_id=uuid4(),
                source_kind="synthetic-storage-capture",
                source_ref=uuid4().hex,
                source_uri=None,
                title=None,
                content_hash=uuid4().hex * 2,
                mime="text/plain",
                byte_size=0,
                raw_uri="s3://synthetic-capture-raw/empty/" + uuid4().hex,
            ),
            convert_component_version="capture-fixture",
        )
        measurement_id = uuid4()
        catalog.record_measurement(
            draft=ManagedMeasurementDraft(
                measurement_id=measurement_id,
                ingest_attempt_id=uuid4().hex,
                org_id=uuid4(),
                project_id=uuid4(),
                format_family="text",
                cost_class="doc-text",
                native_quantity=0,
                canonical_source_bytes=0,
                classifier_version="fixture",
                measurement_algorithm_version="fixture",
                processing_profile_id="fixture",
                measured_at=datetime.now(timezone.utc),
                identity_key=SecretStr("fixture-only"),
            ),
            deployment_id=deployment_id,
            doc_id=upload.doc_id,
            version_id=upload.version_id,
            convert_component_version="capture-fixture",
            lane="steady",
        )
        with scope.connect(role=scope.admin_role) as admin:
            row = admin.execute(
                "SELECT opaque_source_version_id FROM public.managed_ingest_measurements "
                "WHERE measurement_id=%s",
                (measurement_id,),
            ).fetchone()
            assert row is not None
        return _NativeVersion(
            measurement_id=measurement_id,
            version_id=upload.version_id,
            opaque_version=row[0],
        )
    finally:
        engine.dispose()


def _terminal(
    *,
    connection: psycopg.Connection,
    version: _NativeVersion,
    outcome: str,
    sequence: int | None,
) -> ManagedDocumentVersionOutcome:
    """Commit the normal immutable v3 columns, with no storage-specific receipt."""
    row = connection.execute(
        "INSERT INTO public.managed_ingest_outcomes(measurement_id,document_version_id,"
        "outcome,completed_at,reason_code,profile_complete,version_commit_sequence) "
        "VALUES(%s,%s,%s,clock_timestamp(),NULL,%s,%s) RETURNING completed_at",
        (
            version.measurement_id,
            version.opaque_version,
            outcome,
            outcome == "succeeded",
            sequence,
        ),
    ).fetchone()
    assert row is not None
    return ManagedDocumentVersionOutcome.model_validate(
        {
            "measurement_id": version.measurement_id,
            "document_version_id": version.opaque_version,
            "outcome": outcome,
            "completed_at": row[0],
            "profile_complete": outcome == "succeeded",
            "version_commit_sequence": sequence,
        }
    )


def test_original_view_streams_multiple_pages_after_release_without_resampling(
    capture_scope: _Scope, record_property: Callable[[str, object], None]
) -> None:
    """Post-cut DML changes storage while the pinned original view stays fixed."""
    with capture_scope.connect(role=capture_scope.admin_role) as admin:
        deployment = admin.execute(
            "SELECT deployment_id FROM public.deployments"
        ).fetchone()
        assert deployment is not None
        deployment_id = deployment[0]
        admin.execute(
            "INSERT INTO public.content_objects(deployment_id,content_hash,mime,byte_size,raw_uri) "
            "SELECT %s,md5('before-'||i::text)||md5('before-'||i::text),'text/plain',0,"
            "'s3://synthetic-capture-raw/before/'||i::text FROM generate_series(1,1030) i",
            (deployment_id,),
        )
        request = _request(scope=capture_scope)
        with _catalog(scope=capture_scope).open_view(request=request) as view:
            witness = view.witness
            assert (
                int(witness.guard_acquired_after_unix_us)
                < int(request.cut_at_unix_us) - 1000
            )
            assert (
                int(witness.view_acquired_after_unix_us)
                < int(request.cut_at_unix_us) - 1000
            )
            assert (
                int(witness.guard_checked_after_unix_us)
                > int(request.cut_at_unix_us) + 1000
            )
            assert int(witness.release_after_unix_us) >= int(
                witness.release_before_unix_us
            )
            sample = view.allocation_sample
            assert sample.allocated_fork_bytes == sample.physical_table_total_bytes
            captured_raw_allocation = sum(
                int(row.main_bytes)
                + int(row.fsm_bytes)
                + int(row.vm_bytes)
                + int(row.init_bytes)
                for row in sample.relations
                if row.root_relation == "content_objects"
            )
            admin.execute(
                "INSERT INTO public.content_objects(deployment_id,content_hash,mime,byte_size,raw_uri) "
                "SELECT %s,md5('after-'||i::text)||md5('after-'||i::text),'text/plain',10,"
                "'s3://synthetic-capture-raw/after/'||i::text FROM generate_series(1,3000) i",
                (deployment_id,),
            )
            admin.execute("UPDATE public.content_objects SET byte_size=10")
            current_allocation = admin.execute(
                "SELECT pg_total_relation_size('public.content_objects')"
            ).fetchone()
            assert current_allocation is not None
            assert current_allocation[0] > captured_raw_allocation
            refs = tuple(view.iter_store_references())
            assert len(refs) == 1030
            assert all(row.recorded_bytes == "0" for row in refs)
            assert all("before/" in row.locator.get_secret_value() for row in refs)
            assert view.allocation_sample is sample
            assert tuple(view.iter_terminal_members()) == ()
            admin.execute("SET lock_timeout='50ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                admin.execute(
                    "ALTER TABLE public.content_objects ADD COLUMN capture_unwanted integer"
                )
            record_property("native_reference_count", len(refs))
            record_property("native_allocation_count", len(sample.relations))
            record_property(
                "native_view_acquisition_us",
                int(witness.view_acquired_after_unix_us)
                - int(witness.view_acquired_before_unix_us),
            )
        with pytest.raises(NativeStorageCaptureRefused, match="native_view_lost"):
            tuple(view.iter_store_references())
        admin.execute("DELETE FROM public.content_objects")


def test_terminal_members_are_original_committed_rows_not_ack_or_sequence_prefix(
    capture_scope: _Scope,
) -> None:
    """Post-cut lower sequence/ACK/deletion changes cannot alter the captured set."""
    first = _seed_version(scope=capture_scope)
    second = _seed_version(scope=capture_scope)
    late = _seed_version(scope=capture_scope)
    with capture_scope.connect(role=capture_scope.admin_role) as admin:
        admin.execute(
            "INSERT INTO public.document_representations(representation_id,deployment_id,"
            "version_id,route,markdown_uri,status) SELECT %s,deployment_id,%s,'fixture',"
            "'s3://synthetic-capture-artifacts/failed.md','failed' FROM public.deployments",
            (uuid4(), first.version_id),
        )
        succeeded = _terminal(
            connection=admin, version=first, outcome="succeeded", sequence=2
        )
        failed = _terminal(
            connection=admin, version=second, outcome="failed", sequence=4
        )
        admin.execute(
            "UPDATE public.managed_ingest_outcomes SET delivery_state='accepted',"
            "accepted_at=clock_timestamp() WHERE measurement_id=%s",
            (first.measurement_id,),
        )
        with _catalog(scope=capture_scope).open_view(
            request=_request(scope=capture_scope)
        ) as view:
            _terminal(connection=admin, version=late, outcome="succeeded", sequence=3)
            admin.execute(
                "DELETE FROM public.managed_ingest_outcomes WHERE measurement_id=%s",
                (second.measurement_id,),
            )
            rows = tuple(view.iter_terminal_members())
            assert {row.measurement_id for row in rows} == {
                first.measurement_id.hex,
                second.measurement_id.hex,
            }
            assert {row.receipt_digest for row in rows} == {
                terminal_receipt_digest(outcome=succeeded),
                terminal_receipt_digest(outcome=failed),
            }
            assert all(row.opaque_id == row.measurement_id for row in rows)
            assert any(
                row.native_class == "document_representations.markdown_uri"
                and row.native_state == "failed"
                and row.locator.get_secret_value()
                == "s3://synthetic-capture-artifacts/failed.md"
                for row in view.iter_store_references()
            )


def test_expired_original_reader_never_yields_a_replacement(
    capture_scope: _Scope,
) -> None:
    """A native reader timeout during the short boundary wait aborts the attempt."""
    with pytest.raises(psycopg.Error) as expired:
        with _catalog(scope=capture_scope, lifetime_ms=250).open_view(
            request=_request(scope=capture_scope)
        ):
            pytest.fail("an expired original reader cannot be yielded")
    assert expired.value.sqlstate == "25P04"


def test_context_exception_closes_both_original_sessions(capture_scope: _Scope) -> None:
    """Cancellation cleanup leaves no retained backend or background continuation."""
    opened: list[psycopg.Connection] = []

    def connect() -> psycopg.Connection:
        """Record just this test's two actual ordinary collector sessions."""
        connection = capture_scope.connect(role=capture_scope.collector_role)
        opened.append(connection)
        return connection

    with pytest.raises(RuntimeError, match="synthetic_cancel"):
        with _catalog(scope=capture_scope, connect=connect).open_view(
            request=_request(scope=capture_scope)
        ) as view:
            view.check_continuity()
            raise RuntimeError("synthetic_cancel")
    assert len(opened) == 2 and all(connection.closed for connection in opened)


def test_past_cut_and_borrowed_transaction_refuse(capture_scope: _Scope) -> None:
    """Neither a missed cut nor an earlier frozen reader can become evidence."""
    with pytest.raises(
        NativeStorageCaptureRefused, match="native_cut_outside_boundary_budget"
    ):
        with _catalog(scope=capture_scope).open_view(
            request=_request(scope=capture_scope, lead_us=-1)
        ):
            pytest.fail("past cut cannot complete")
    borrowed = capture_scope.connect(role=capture_scope.collector_role)
    borrowed.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
    borrowed.execute("SELECT pg_current_snapshot()")
    calls = 0

    def connect() -> psycopg.Connection:
        """Return the pre-existing transaction only as the attempted reader."""
        nonlocal calls
        calls += 1
        return (
            borrowed
            if calls == 2
            else capture_scope.connect(role=capture_scope.collector_role)
        )

    with pytest.raises(
        NativeStorageCaptureRefused, match="native_connection_not_fresh"
    ):
        with _catalog(scope=capture_scope, connect=connect).open_view(
            request=_request(scope=capture_scope)
        ):
            pytest.fail("borrowed original snapshot cannot complete")
    assert borrowed.closed
