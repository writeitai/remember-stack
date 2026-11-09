"""Prove outcome sequence allocation cannot slip past a storage capture fence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from threading import Event
from time import monotonic
from time import sleep
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from rememberstack.ports.metering import MeterReceiptPort
from rememberstack.spine.managed_metering import ManagedMeterCatalog
from rememberstack.spine.readiness import PipelineReadinessCatalog
from rememberstack.spine.settings import load_database_settings


class ReadyVersion:
    """Report a completed version without invoking unrelated pipeline stages."""

    def inspect(self, **kwargs: object) -> SimpleNamespace:
        """Return a single successful profile for the catalog's version."""
        del kwargs
        stage = SimpleNamespace(
            status="succeeded", finished_at=datetime.now(timezone.utc)
        )
        return SimpleNamespace(versions=(SimpleNamespace(ready=True, stages=(stage,)),))


@pytest.mark.parametrize(
    "capture_timeout,expect_timeout", [("5s", False), ("250ms", True)]
)
def test_snapshot_fence_waits_for_allocated_success_sequence(
    monkeypatch: pytest.MonkeyPatch, capture_timeout: str, expect_timeout: bool
) -> None:
    """A capture cannot observe a maximum before an earlier allocated insert commits."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for the PostgreSQL proof")
    schema = f"watermark_test_{uuid4().hex}"
    administration = create_engine(database_url)
    with administration.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema}"}
    )
    sequence_allocated = Event()
    release = Event()
    capture_started = Event()

    def pause_after_sequence(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        """Hold the writer after the sequence SQL has run and before it commits."""
        if "nextval('managed_version_commit_sequence_seq')" in statement:
            sequence_allocated.set()
            if not release.wait(timeout=10):
                raise TimeoutError("test did not release the outcome writer")

    def capture() -> int | None:
        """Capture in one volatile SQL function, as the UMC collector does."""
        with engine.begin() as connection:
            connection.execute(text(f"SET LOCAL lock_timeout = '{capture_timeout}'"))
            capture_started.set()
            return cast(
                "int | None", connection.scalar(text("SELECT test_capture_watermark()"))
            )

    def capture_is_waiting() -> bool:
        """Inspect only this temporary table's waiting SHARE locks."""
        with administration.connect() as connection:
            return bool(
                connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_locks l "
                        "JOIN pg_class c ON c.oid=l.relation "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname=:schema AND c.relname='managed_ingest_outcomes' "
                        "AND l.mode='ShareLock' AND NOT l.granted)"
                    ),
                    {"schema": schema},
                )
            )

    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE SEQUENCE managed_version_commit_sequence_seq")
            )
            connection.execute(
                text(
                    "CREATE TABLE document_versions (version_id uuid PRIMARY KEY, status text NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE managed_ingest_measurements (measurement_id uuid PRIMARY KEY, deployment_id uuid NOT NULL, version_id uuid NOT NULL, processing_profile_id text NOT NULL, delivery_state text NOT NULL, outcome_next_attempt_at timestamptz, accepted_at timestamptz NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "CREATE TABLE managed_ingest_outcomes (measurement_id uuid PRIMARY KEY, document_version_id text, outcome text NOT NULL, completed_at timestamptz NOT NULL, reason_code text, profile_complete boolean NOT NULL, version_commit_sequence bigint UNIQUE)"
                )
            )
            connection.execute(
                text(
                    "CREATE FUNCTION test_capture_watermark() RETURNS bigint "
                    "LANGUAGE plpgsql AS $$ BEGIN "
                    "LOCK TABLE managed_ingest_outcomes IN SHARE MODE; "
                    "RETURN (SELECT max(version_commit_sequence) FROM managed_ingest_outcomes); "
                    "END $$"
                )
            )
            version_id, measurement_id, deployment_id = uuid4(), uuid4(), uuid4()
            connection.execute(
                text("INSERT INTO document_versions VALUES (:v, 'ready')"),
                {"v": version_id},
            )
            connection.execute(
                text(
                    "INSERT INTO managed_ingest_measurements VALUES (:m, :d, :v, 'doc-text-standard-v1', 'accepted', NULL, now())"
                ),
                {"m": measurement_id, "d": deployment_id, "v": version_id},
            )
        catalog = ManagedMeterCatalog(
            engine=engine,
            receipts=cast(MeterReceiptPort, object()),
            readiness=cast(PipelineReadinessCatalog, ReadyVersion()),
        )
        monkeypatch.setattr(catalog, "_profile_unreachable", lambda **_: False)
        event.listen(engine, "after_cursor_execute", pause_after_sequence)
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(catalog._materialize_outcomes, limit=1)
            try:
                assert sequence_allocated.wait(timeout=5), (
                    "writer never allocated a sequence"
                )
                reader = pool.submit(capture)
                assert capture_started.wait(timeout=5), "capture did not start"
                if expect_timeout:
                    with pytest.raises(OperationalError) as error:
                        reader.result(timeout=5)
                    assert getattr(error.value.orig, "sqlstate", None) == "55P03"
                else:
                    deadline = monotonic() + 3
                    while (
                        not reader.done()
                        and not capture_is_waiting()
                        and monotonic() < deadline
                    ):
                        sleep(0.02)
                    assert not reader.done() and capture_is_waiting(), (
                        "capture bypassed an allocated outcome"
                    )
            finally:
                release.set()
            assert writer.result(timeout=5) == 1
            if expect_timeout:
                assert capture() == 1
            else:
                assert reader.result(timeout=5) == 1

        # Replay does not create a second outcome, and a failed outcome does
        # not consume a success sequence.
        assert catalog._materialize_outcomes(limit=1) == 0
        failed_version, failed_measurement = uuid4(), uuid4()
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO document_versions VALUES (:v, 'failed')"),
                {"v": failed_version},
            )
            connection.execute(
                text(
                    "INSERT INTO managed_ingest_measurements VALUES "
                    "(:m, :d, :v, 'doc-text-standard-v1', 'accepted', NULL, now())"
                ),
                {"m": failed_measurement, "d": deployment_id, "v": failed_version},
            )
        assert catalog._materialize_outcomes(limit=1) == 1
        with engine.connect() as connection:
            failed_row = connection.execute(
                text(
                    "SELECT outcome, version_commit_sequence FROM managed_ingest_outcomes "
                    "WHERE measurement_id=:m"
                ),
                {"m": failed_measurement},
            ).one()
            last_sequence = connection.scalar(
                text("SELECT last_value FROM managed_version_commit_sequence_seq")
            )
        assert tuple(failed_row) == ("failed", None)
        assert last_sequence == 1
    finally:
        release.set()
        engine.dispose()
        with administration.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        administration.dispose()
