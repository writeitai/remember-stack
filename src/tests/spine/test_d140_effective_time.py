"""PostgreSQL proofs for D140 effective periods, version keys and time scope.

The running example is the design's Travel Policy: editions of one lineage
declared in force over consecutive periods. Every proof writes through the
spine (``DocumentCatalog.record_upload`` and ``EffectiveTimeCatalog``) and
reads through the objects the migration created: the ledgers, the
``document_version_scope`` projection and the ``memory_v1`` functions. After
each kind of write the projection must equal a fresh rebuild from the ledgers.
"""

from collections.abc import Iterator
from datetime import datetime
from datetime import UTC
import hashlib
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from alembic import command
from alembic.config import Config
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from remember.models import EffectivePeriodInput
from rememberstack.model import DocumentNotFoundError
from rememberstack.model import DocumentVersionNotFoundError
from rememberstack.model import EffectivePeriodConflictError
from rememberstack.model import EffectiveTimeNotSupportedError
from rememberstack.model import IngestedVersion
from rememberstack.model import UploadRecord
from rememberstack.model import VersionKeyConflictError
from rememberstack.spine.document_catalog import DocumentCatalog
from rememberstack.spine.effective_time import EffectiveTimeCatalog
from rememberstack.spine.settings import load_database_settings
from tests.database_reset import reset_database

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("14000000-0000-0000-0000-0000000000d1")
_Y2024 = datetime(2024, 1, 1, tzinfo=UTC)
_Y2025 = datetime(2025, 1, 1, tzinfo=UTC)
_Y2026 = datetime(2026, 1, 1, tzinfo=UTC)
_MAR2026 = datetime(2026, 3, 1, tzinfo=UTC)
_FEB2026 = datetime(2026, 2, 1, tzinfo=UTC)
_FUTURE = datetime(2099, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """Apply structural head once for these proofs."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for PostgreSQL proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    reset_database(config=config)
    command.upgrade(config=config, revision="head")
    created = create_engine(database_url)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture(autouse=True)
def deployment(engine: Engine) -> None:
    """Each proof starts from one empty deployment."""
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE deployments CASCADE"))
        connection.execute(
            text(
                "INSERT INTO deployments (deployment_id, slug, name, raw_bucket,"
                " artifacts_bucket, corpusfs_bucket) VALUES (:d, 'd140', 'D140',"
                " 'mem://raw', 'mem://artifacts', 'mem://corpusfs')"
            ),
            {"d": _DEPLOYMENT_ID},
        )


def _ingest(
    engine: Engine,
    *,
    content: bytes,
    ref: str = "policy/travel",
    mode: str = "snapshot",
    version_key: str | None = None,
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
) -> IngestedVersion:
    """Observe one snapshot of a lineage through the real catalog."""
    content_hash = hashlib.sha256(content).hexdigest()
    doc_id = uuid5(NAMESPACE_URL, f"d140:{_DEPLOYMENT_ID}:{ref}")
    return DocumentCatalog(engine=engine).record_upload(
        record=UploadRecord(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=doc_id,
            source_kind="intranet",
            source_ref=ref,
            source_uri=ref,
            title="Travel Policy",
            content_hash=content_hash,
            mime="text/markdown",
            byte_size=len(content),
            raw_uri=f"{doc_id}/{content_hash}/original.md",
            versioning_mode=mode,
            version_key=version_key,
            effective_from=effective_from,
            effective_until=effective_until,
        ),
        convert_component_version="d140-test",
    )


def _ready(engine: Engine, *, version: IngestedVersion, serve: bool = True) -> UUID:
    """Finish processing a version: a ready reading, and (by default) serve it."""
    representation_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO document_representations (representation_id,"
                " deployment_id, version_id, route, status)"
                " VALUES (:r, :d, :v, 'passthrough', 'ready')"
            ),
            {"r": representation_id, "d": _DEPLOYMENT_ID, "v": version.version_id},
        )
        connection.execute(
            text(
                "UPDATE document_versions SET status = 'ready',"
                " current_representation_id = :r WHERE version_id = :v"
            ),
            {"r": representation_id, "v": version.version_id},
        )
        if serve:
            connection.execute(
                text(
                    "UPDATE documents SET current_version_id = :v WHERE doc_id = :doc"
                ),
                {"v": version.version_id, "doc": version.doc_id},
            )
    return representation_id


def _periods(*periods: tuple[datetime, datetime | None]) -> list[EffectivePeriodInput]:
    """Period inputs from (start, end) pairs."""
    return [
        EffectivePeriodInput(effective_from=start, effective_until=end)
        for start, end in periods
    ]


def _scope_rows(connection: Connection, *, doc_id: UUID) -> dict[UUID, Any]:
    """The projection rows of one lineage, as plain comparable values."""
    return {
        row["version_id"]: (
            tuple((part.lower, part.upper) for part in row["in_force"]),
            row["periodised"],
            row["selectable"],
        )
        for row in connection.execute(
            text(
                "SELECT version_id, in_force, periodised, selectable"
                " FROM document_version_scope WHERE doc_id = :doc"
            ),
            {"doc": doc_id},
        ).mappings()
    }


def _scope(engine: Engine, *, doc_id: UUID) -> dict[UUID, Any]:
    """The maintained projection, proven equal to a rebuild from the ledgers.

    The rebuild runs in a transaction that is rolled back, so the maintained
    rows are what the triggers left behind, not what the check wrote.
    """
    with engine.connect() as connection:
        maintained = _scope_rows(connection, doc_id=doc_id)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text("SELECT refresh_document_version_scope(:d, :doc)"),
                {"d": _DEPLOYMENT_ID, "doc": doc_id},
            )
            rebuilt = _scope_rows(connection, doc_id=doc_id)
        finally:
            transaction.rollback()
    assert maintained == rebuilt
    return maintained


def _in_scope(
    engine: Engine,
    *,
    mode: str,
    at: datetime | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    believed_at: datetime | None = None,
    doc_ids: list[UUID] | None = None,
) -> list[tuple[UUID, datetime | None, datetime | None]]:
    """``memory_v1.versions_in_scope`` as (version, from, until) rows."""
    with engine.connect() as connection:
        return [
            (row[0], row[1], row[2])
            for row in connection.execute(
                text(
                    "SELECT version_id, effective_from, effective_until"
                    " FROM memory_v1.versions_in_scope(:d, :mode, :at, :rs, :re,"
                    " now(), :b, CAST(:docs AS uuid[]))"
                ),
                {
                    "d": _DEPLOYMENT_ID,
                    "mode": mode,
                    "at": at,
                    "rs": range_start,
                    "re": range_end,
                    "b": believed_at,
                    "docs": doc_ids,
                },
            )
        ]


def _intervals(
    engine: Engine, *, doc_id: UUID, believed_at: datetime | None = None
) -> list[tuple[UUID, datetime, datetime | None, bool]]:
    """``memory_v1.effective_intervals`` as (version, from, until, declared) rows."""
    with engine.connect() as connection:
        return [
            (row[0], row[1], row[2], row[3])
            for row in connection.execute(
                text(
                    "SELECT version_id, effective_from, effective_until,"
                    " until_declared FROM memory_v1.effective_intervals(:d,"
                    " ARRAY[CAST(:doc AS uuid)], coalesce(:b, now()))"
                ),
                {"d": _DEPLOYMENT_ID, "doc": doc_id, "b": believed_at},
            )
        ]


def _now(engine: Engine) -> datetime:
    """A belief instant between two committed writes."""
    with engine.connect() as connection:
        return connection.execute(text("SELECT clock_timestamp()")).scalar_one()


def _count(engine: Engine, sql: str, **parameters: object) -> int:
    """One scalar count."""
    with engine.connect() as connection:
        return connection.execute(text(sql), parameters).scalar_one()


# --- version keys --------------------------------------------------------------


def test_a_new_key_on_identical_bytes_creates_a_version(engine: Engine) -> None:
    """Two editions with identical text are two versions sharing one object."""
    first = _ingest(engine, content=b"# Policy", version_key="edition-1")
    second = _ingest(engine, content=b"# Policy", version_key="edition-2")
    unkeyed = _ingest(engine, content=b"# Policy")

    assert first.created and second.created
    assert second.version_id != first.version_id
    assert second.content_hash == first.content_hash
    assert (first.version_key, second.version_key) == ("edition-1", "edition-2")
    # without a key D55 is unchanged: identical bytes to the latest are a no-op
    assert not unkeyed.created
    assert (unkeyed.version_id, unkeyed.version_key) == (second.version_id, "edition-2")


def test_an_existing_key_is_only_an_idempotent_retry_of_the_latest(
    engine: Engine,
) -> None:
    """Same key and bytes of the latest: no-op. Any other use names the owner."""
    a = _ingest(engine, content=b"A", version_key="edition-1")
    retry = _ingest(engine, content=b"A", version_key="edition-1")
    b = _ingest(engine, content=b"B", version_key="edition-2")

    assert not retry.created and retry.version_id == a.version_id
    # A -> B -> A under the first edition's key: the key names an older version
    with pytest.raises(VersionKeyConflictError) as older:
        _ingest(engine, content=b"A", version_key="edition-1")
    assert older.value.version_id == a.version_id
    # the latest version's key with different bytes
    with pytest.raises(VersionKeyConflictError) as changed:
        _ingest(engine, content=b"C", version_key="edition-2")
    assert changed.value.version_id == b.version_id
    # A -> B -> A without a key is still a new observation (D55)
    again = _ingest(engine, content=b"A")
    assert again.created and again.version_key is None
    assert (
        _count(
            engine,
            "SELECT count(*) FROM document_versions WHERE doc_id = :doc",
            doc=a.doc_id,
        )
        == 3
    )


def test_a_deleted_latest_version_never_takes_its_key_back(engine: Engine) -> None:
    """Re-sending a deleted version's bytes under its key is not a retry."""
    a = _ingest(engine, content=b"A", version_key="edition-1")
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now(), status = 'deleted'"
                " WHERE version_id = :v"
            ),
            {"v": a.version_id},
        )
    with pytest.raises(VersionKeyConflictError):
        _ingest(engine, content=b"A", version_key="edition-1")


# --- declaring at ingest ----------------------------------------------------


def test_the_first_declaration_periodises_the_lineage(engine: Engine) -> None:
    """Edition 2 from 2026 needs no write to edition 1: its end is derived."""
    one = _ingest(
        engine, content=b"edition 1", version_key="edition-1", effective_from=_Y2024
    )
    two = _ingest(
        engine, content=b"edition 2", version_key="edition-2", effective_from=_Y2026
    )

    assert _intervals(engine, doc_id=one.doc_id) == [
        (one.version_id, _Y2024, _Y2026, False),
        (two.version_id, _Y2026, None, False),
    ]
    assert (
        _count(
            engine,
            "SELECT count(*) FROM document_effective_time_events"
            " WHERE doc_id = :doc AND event = 'declared'",
            doc=one.doc_id,
        )
        == 1
    )
    scope = _scope(engine, doc_id=one.doc_id)
    assert scope[one.version_id] == (((_Y2024, _Y2026),), True, False)
    assert scope[two.version_id] == (((_Y2026, None),), True, False)


def test_a_d55_no_op_records_the_declaration_on_the_existing_version(
    engine: Engine,
) -> None:
    """Identical bytes create no version; the period lands on the latest."""
    first = _ingest(engine, content=b"edition 1")
    same = _ingest(engine, content=b"edition 1", effective_from=_Y2024)

    assert not same.created and same.version_id == first.version_id
    assert _intervals(engine, doc_id=first.doc_id) == [
        (first.version_id, _Y2024, None, False)
    ]


def test_an_identical_declaration_is_a_no_op_and_a_new_end_corrects_it(
    engine: Engine,
) -> None:
    """Same version and start: equal end changes nothing, another end replaces."""
    first = _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    assert (
        _count(
            engine,
            "SELECT count(*) FROM document_effective_periods WHERE doc_id = :doc",
            doc=first.doc_id,
        )
        == 1
    )

    _ingest(engine, content=b"edition 1", effective_from=_Y2024, effective_until=_Y2025)
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT period_id, effective_until, retracted_at,"
                " retracted_by_period_id FROM document_effective_periods"
                " WHERE doc_id = :doc ORDER BY declared_at"
            ),
            {"doc": first.doc_id},
        ).all()
    assert len(rows) == 2
    assert rows[0].retracted_at is not None
    assert rows[0].retracted_by_period_id == rows[1].period_id
    assert (rows[1].effective_until, rows[1].retracted_at) == (_Y2025, None)


def test_a_start_held_by_another_version_is_refused_and_nothing_is_written(
    engine: Engine,
) -> None:
    """Two live declarations of one lineage may not share a start."""
    one = _ingest(engine, content=b"edition 1", effective_from=_Y2026)

    with pytest.raises(EffectivePeriodConflictError) as refused:
        _ingest(engine, content=b"edition 2", effective_from=_Y2026)

    assert refused.value.version_id == one.version_id
    # the whole ingest rolled back: no second version, no second declaration
    assert (
        _count(
            engine,
            "SELECT count(*) FROM document_versions WHERE doc_id = :doc",
            doc=one.doc_id,
        )
        == 1
    )
    assert (
        _count(
            engine,
            "SELECT count(*) FROM document_effective_periods WHERE doc_id = :doc",
            doc=one.doc_id,
        )
        == 1
    )


def test_a_living_lineage_refuses_periods(engine: Engine) -> None:
    """``living`` means the newest version is current; a period would compete."""
    with pytest.raises(EffectiveTimeNotSupportedError):
        _ingest(engine, content=b"wiki", mode="living", effective_from=_Y2026)
    wiki = _ingest(engine, content=b"wiki", mode="living", version_key="rev-1")

    with pytest.raises(EffectiveTimeNotSupportedError):
        EffectiveTimeCatalog(engine=engine).set_effective_periods(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=wiki.doc_id,
            version_id=wiki.version_id,
            periods=_periods((_Y2026, None)),
        )
    # the version key itself is valid on a living lineage
    assert wiki.version_key == "rev-1"


# --- the period API ----------------------------------------------------------------


def test_versions_may_be_declared_in_any_order(engine: Engine) -> None:
    """An archive back-filled newest first derives the same intervals."""
    newer = _ingest(engine, content=b"edition 2")
    older = _ingest(engine, content=b"edition 1")
    catalog = EffectiveTimeCatalog(engine=engine)

    catalog.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=newer.doc_id,
        version_id=newer.version_id,
        periods=_periods((_Y2026, None)),
    )
    result = catalog.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=older.doc_id,
        version_id=older.version_id,
        periods=_periods((_Y2024, None)),
    )

    assert (result.declared, result.retracted) == (1, 0)
    assert [period.effective_from for period in result.periods] == [_Y2024]
    assert _intervals(engine, doc_id=newer.doc_id) == [
        (older.version_id, _Y2024, _Y2026, False),
        (newer.version_id, _Y2026, None, False),
    ]


def test_gaps_overlaps_and_readiness_decide_what_a_scope_selects(
    engine: Engine,
) -> None:
    """A declared end is a gap; an end past the next start is a transition."""
    one = _ingest(engine, content=b"edition 1")
    two = _ingest(engine, content=b"edition 2")
    catalog = EffectiveTimeCatalog(engine=engine)
    # edition 1 until March overlaps edition 2 from January (a transition)
    catalog.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=one.doc_id,
        version_id=one.version_id,
        periods=_periods((_Y2024, _MAR2026)),
    )
    # edition 2 is withdrawn at the end of 2099 (a gap after it)
    catalog.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=two.doc_id,
        version_id=two.version_id,
        periods=_periods((_Y2026, _FUTURE)),
    )

    # nothing is ready yet: the in-force text is still processing
    assert _in_scope(engine, mode="at", at=_FEB2026) == []
    _ready(engine, version=one, serve=False)
    _ready(engine, version=two)

    both = _in_scope(engine, mode="at", at=_FEB2026)
    assert both == [
        (one.version_id, _Y2024, _MAR2026),
        (two.version_id, _Y2026, _FUTURE),
    ]
    assert _in_scope(engine, mode="at", at=_Y2025) == [
        (one.version_id, _Y2024, _MAR2026)
    ]
    assert _in_scope(engine, mode="at", at=datetime(2100, 1, 1, tzinfo=UTC)) == []
    assert [row[0] for row in _in_scope(engine, mode="current")] == [two.version_id]
    assert [
        row[0]
        for row in _in_scope(
            engine, mode="overlap", range_start=_Y2025, range_end=_Y2026
        )
    ] == [one.version_id, two.version_id]
    assert [row[0] for row in _in_scope(engine, mode="history")] == [
        one.version_id,
        two.version_id,
    ]


def test_an_undeclared_lineage_selects_its_served_version_in_every_mode(
    engine: Engine,
) -> None:
    """Existing corpora see today's behaviour: the served version, unbounded."""
    first = _ingest(engine, content=b"v1")
    second = _ingest(engine, content=b"v2")
    _ready(engine, version=first)
    _ready(engine, version=second)

    expected = [(second.version_id, None, None)]
    assert _in_scope(engine, mode="current") == expected
    assert _in_scope(engine, mode="at", at=_Y2024) == expected
    assert (
        _in_scope(engine, mode="overlap", range_start=_Y2024, range_end=_Y2025)
        == expected
    )
    assert _in_scope(engine, mode="history") == expected
    scope = _scope(engine, doc_id=first.doc_id)
    assert scope[first.version_id] == ((), False, True)
    assert scope[second.version_id] == (((None, None),), False, True)


def test_retracting_the_last_period_keeps_the_lineage_periodised(
    engine: Engine,
) -> None:
    """A withdrawn edition never resurrects as current by retraction alone."""
    only = _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    _ready(engine, version=only)
    assert [row[0] for row in _in_scope(engine, mode="current")] == [only.version_id]

    result = EffectiveTimeCatalog(engine=engine).set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=only.doc_id,
        version_id=only.version_id,
        periods=[],
    )

    assert (result.periods, result.declared, result.retracted) == ((), 0, 1)
    assert _scope(engine, doc_id=only.doc_id)[only.version_id] == ((), True, True)
    assert _in_scope(engine, mode="current") == []


def test_set_effective_periods_keeps_unchanged_and_replaces_changed(
    engine: Engine,
) -> None:
    """Only the difference is written; a replaced start links its successor."""
    one = _ingest(engine, content=b"edition 1")
    catalog = EffectiveTimeCatalog(engine=engine)
    catalog.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=one.doc_id,
        version_id=one.version_id,
        periods=_periods((_Y2024, None), (_Y2026, None)),
    )

    result = catalog.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=one.doc_id,
        version_id=one.version_id,
        periods=_periods((_Y2024, None), (_Y2026, _FUTURE)),
    )

    assert (result.declared, result.retracted) == (1, 1)
    assert [
        (period.effective_from, period.effective_until) for period in result.periods
    ] == [(_Y2024, None), (_Y2026, _FUTURE)]


def test_a_version_must_belong_to_the_lineage(engine: Engine) -> None:
    """The ownership check and the lock are one statement; absence is typed."""
    travel = _ingest(engine, content=b"travel")
    expense = _ingest(engine, content=b"expense", ref="policy/expense")
    catalog = EffectiveTimeCatalog(engine=engine)

    with pytest.raises(DocumentVersionNotFoundError):
        catalog.set_effective_periods(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=travel.doc_id,
            version_id=expense.version_id,
            periods=_periods((_Y2024, None)),
        )
    with pytest.raises(DocumentNotFoundError):
        catalog.set_effective_periods(
            deployment_id=_DEPLOYMENT_ID,
            doc_id=uuid4(),
            version_id=travel.version_id,
            periods=[],
        )
    with pytest.raises(DocumentNotFoundError):
        catalog.clear_effective_time(deployment_id=_DEPLOYMENT_ID, doc_id=uuid4())
    # and the database refuses a cross-lineage declaration outright
    with pytest.raises(DBAPIError), engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO document_effective_periods (period_id, deployment_id,"
                " doc_id, version_id, effective_from, declared_by)"
                " VALUES (:p, :d, :doc, :v, :f, 'period_api')"
            ),
            {
                "p": uuid4(),
                "d": _DEPLOYMENT_ID,
                "doc": travel.doc_id,
                "v": expense.version_id,
                "f": _Y2024,
            },
        )


def test_clear_and_redeclare_are_reproducible_at_every_belief_instant(
    engine: Engine,
) -> None:
    """The two ledgers reconstruct exactly what an earlier reader saw."""
    one = _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    two = _ingest(engine, content=b"edition 2", effective_from=_FUTURE)
    _ready(engine, version=one, serve=False)
    _ready(engine, version=two)
    catalog = EffectiveTimeCatalog(engine=engine)
    periodised = _now(engine)

    cleared = catalog.clear_effective_time(
        deployment_id=_DEPLOYMENT_ID, doc_id=one.doc_id
    )
    after_clear = _now(engine)
    catalog.set_effective_periods(
        deployment_id=_DEPLOYMENT_ID,
        doc_id=one.doc_id,
        version_id=two.version_id,
        periods=_periods((_Y2026, None)),
    )
    redeclared = _now(engine)

    assert cleared.retracted == 2 and cleared.cleared_at is not None
    doc_ids = [one.doc_id]
    # before the clear: edition 1 in force now, edition 2 only in the future
    assert _in_scope(
        engine, mode="current", believed_at=periodised, doc_ids=doc_ids
    ) == [(one.version_id, _Y2024, _FUTURE)]
    assert len(_intervals(engine, doc_id=one.doc_id, believed_at=periodised)) == 2
    # after the clear: served-version semantics
    assert _in_scope(
        engine, mode="current", believed_at=after_clear, doc_ids=doc_ids
    ) == [(two.version_id, None, None)]
    assert _intervals(engine, doc_id=one.doc_id, believed_at=after_clear) == []
    # after the new declaration: edition 2 from 2026
    assert _in_scope(
        engine, mode="current", believed_at=redeclared, doc_ids=doc_ids
    ) == [(two.version_id, _Y2026, None)]
    assert _in_scope(engine, mode="current") == [(two.version_id, _Y2026, None)]
    with engine.connect() as connection:
        events = connection.execute(
            text(
                "SELECT event FROM document_effective_time_events"
                " WHERE doc_id = :doc ORDER BY event_at"
            ),
            {"doc": one.doc_id},
        ).scalars()
        assert list(events) == ["declared", "cleared", "declared"]


def test_a_past_belief_instant_requires_named_lineages(engine: Engine) -> None:
    """The projection only knows current belief; the ledgers are per lineage."""
    _ingest(engine, content=b"edition 1", effective_from=_Y2024)

    with pytest.raises(DBAPIError, match="requires doc_ids"):
        _in_scope(engine, mode="current", believed_at=_now(engine))
    with pytest.raises(DBAPIError, match="mode must be"):
        _in_scope(engine, mode="sometime")
    with pytest.raises(DBAPIError, match="requires at"):
        _in_scope(engine, mode="at")


def test_clearing_an_undeclared_lineage_changes_nothing(engine: Engine) -> None:
    """No event is written for a lineage that never declared anything."""
    plain = _ingest(engine, content=b"plain")

    cleared = EffectiveTimeCatalog(engine=engine).clear_effective_time(
        deployment_id=_DEPLOYMENT_ID, doc_id=plain.doc_id
    )

    assert (cleared.retracted, cleared.cleared_at) == (0, None)
    assert _count(engine, "SELECT count(*) FROM document_effective_time_events") == 0


# --- projection maintenance --------------------------------------------------------


def test_the_projection_follows_every_kind_of_write(engine: Engine) -> None:
    """Each write kind leaves the projection equal to a rebuild."""
    one = _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    # version insert and declaration
    assert _scope(engine, doc_id=one.doc_id)[one.version_id] == (
        ((_Y2024, None),),
        True,
        False,
    )
    # version ready and current pointer move
    representation_id = _ready(engine, version=one)
    assert _scope(engine, doc_id=one.doc_id)[one.version_id][2] is True
    # representation status change
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_representations SET status = 'failed'"
                " WHERE representation_id = :r"
            ),
            {"r": representation_id},
        )
    assert _scope(engine, doc_id=one.doc_id)[one.version_id][2] is False
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_representations SET status = 'ready'"
                " WHERE representation_id = :r"
            ),
            {"r": representation_id},
        )
    # a second edition shortens the first
    two = _ingest(engine, content=b"edition 2", effective_from=_Y2026)
    scope = _scope(engine, doc_id=one.doc_id)
    assert scope[one.version_id][0] == ((_Y2024, _Y2026),)
    # version soft delete: its declaration leaves the derivation
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now(), status = 'deleted'"
                " WHERE version_id = :v"
            ),
            {"v": two.version_id},
        )
    scope = _scope(engine, doc_id=one.doc_id)
    assert two.version_id not in scope
    assert scope[one.version_id][0] == ((_Y2024, None),)
    # lineage soft delete removes every row; resurrection restores them
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET deleted_at = now() WHERE doc_id = :doc"),
            {"doc": one.doc_id},
        )
    assert _scope(engine, doc_id=one.doc_id) == {}
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE documents SET deleted_at = NULL WHERE doc_id = :doc"),
            {"doc": one.doc_id},
        )
    assert _scope(engine, doc_id=one.doc_id)[one.version_id] == (
        ((_Y2024, None),),
        True,
        True,
    )
    # a mode event: clear returns the served version to an unbounded range
    EffectiveTimeCatalog(engine=engine).clear_effective_time(
        deployment_id=_DEPLOYMENT_ID, doc_id=one.doc_id
    )
    assert _scope(engine, doc_id=one.doc_id)[one.version_id] == (
        ((None, None),),
        False,
        True,
    )


# --- public views and the evidence gate -----------------------------------------


def test_the_periods_view_derives_ends_from_live_declarations(engine: Engine) -> None:
    """One row per live declaration, ended by the next declared start."""
    one = _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    two = _ingest(engine, content=b"edition 2", effective_from=_Y2026)
    three = _ingest(
        engine, content=b"edition 3", effective_from=_FUTURE, effective_until=None
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE document_versions SET deleted_at = now() WHERE version_id = :v"
            ),
            {"v": two.version_id},
        )
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT version_id, effective_from, effective_until, until_declared"
                " FROM memory_v1.document_effective_periods_live"
                " WHERE doc_id = :doc ORDER BY effective_from"
            ),
            {"doc": one.doc_id},
        ).all()
    assert [tuple(row) for row in rows] == [
        (one.version_id, _Y2024, _FUTURE, False),
        (three.version_id, _FUTURE, None, False),
    ]


def _chunk_with_claim(
    connection: Connection, *, version: IngestedVersion, representation_id: UUID
) -> UUID:
    """One chunk of the version carrying one fresh claim occurrence."""
    chunk_id, claim_id = uuid4(), uuid4()
    connection.execute(
        text(
            "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
            " representation_id, ordinal, block_start, block_end,"
            " chunk_content_hash, extraction_input_hash, char_start, char_end,"
            " chunker_version) VALUES (:c, :d, :doc, :v, :r, 0, 0, 0, 'h', 'h',"
            " 0, 8, 'test')"
        ),
        {
            "c": chunk_id,
            "d": _DEPLOYMENT_ID,
            "doc": version.doc_id,
            "v": version.version_id,
            "r": representation_id,
        },
    )
    connection.execute(
        text(
            "INSERT INTO chunk_claims (deployment_id, chunk_id, claim_id,"
            " evidence_spans) VALUES (:d, :c, :claim,"
            ' CAST(\'[{"char_start": 0, "char_end": 4}]\' AS jsonb))'
        ),
        {"d": _DEPLOYMENT_ID, "c": chunk_id, "claim": claim_id},
    )
    return claim_id


def _support(
    connection: Connection,
    *,
    relation_id: UUID,
    claim_id: UUID,
    doc_id: UUID,
    stance: str = "supports",
) -> None:
    """Link one claim to one relation as evidence."""
    connection.execute(
        text(
            "INSERT INTO relation_evidence (deployment_id, relation_id, claim_id,"
            " doc_id, stance, normalizer_version)"
            " VALUES (:d, :rel, :claim, :doc, CAST(:stance AS evidence_stance), 't')"
        ),
        {
            "d": _DEPLOYMENT_ID,
            "rel": relation_id,
            "claim": claim_id,
            "doc": doc_id,
            "stance": stance,
        },
    )


def _gate(
    engine: Engine,
    *,
    relation_id: UUID,
    mode: str,
    at: datetime | None = None,
    believed_at: datetime | None = None,
) -> bool:
    """``memory_v1.fact_in_scope_support`` for one relation."""
    with engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT memory_v1.fact_in_scope_support(:d, 'relation', :r, :mode,"
                " :at, NULL, NULL, now(), :b)"
            ),
            {
                "d": _DEPLOYMENT_ID,
                "r": relation_id,
                "mode": mode,
                "at": at,
                "b": believed_at,
            },
        ).scalar_one()


def test_the_evidence_gate_follows_the_editions_in_force(engine: Engine) -> None:
    """A fact supported only by an edition not in force has no in-scope evidence."""
    before_declarations = _now(engine)
    one = _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    two = _ingest(engine, content=b"edition 2", effective_from=_FUTURE)
    other = _ingest(engine, content=b"undeclared", ref="policy/expense")
    readings = {
        version.version_id: _ready(engine, version=version, serve=version is not one)
        for version in (one, two, other)
    }
    old_fact, future_fact, plain_fact, contradicted = uuid4(), uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        for version, relation_id in (
            (one, old_fact),
            (two, future_fact),
            (other, plain_fact),
        ):
            claim_id = _chunk_with_claim(
                connection,
                version=version,
                representation_id=readings[version.version_id],
            )
            _support(
                connection,
                relation_id=relation_id,
                claim_id=claim_id,
                doc_id=version.doc_id,
            )
        # a fact with no supporting evidence at all (D54 zero-support,
        # contradiction-only) is judged by its evidence of either stance
        contradiction = _chunk_with_claim(
            connection, version=one, representation_id=readings[one.version_id]
        )
        _support(
            connection,
            relation_id=contradicted,
            claim_id=contradiction,
            doc_id=one.doc_id,
            stance="contradicts",
        )

    assert _gate(engine, relation_id=old_fact, mode="current") is True
    assert _gate(engine, relation_id=future_fact, mode="current") is False
    assert _gate(engine, relation_id=future_fact, mode="at", at=_FUTURE) is True
    assert _gate(engine, relation_id=old_fact, mode="at", at=_FUTURE) is False
    assert _gate(engine, relation_id=future_fact, mode="history") is False
    assert _gate(engine, relation_id=plain_fact, mode="at", at=_Y2024) is True
    assert _gate(engine, relation_id=contradicted, mode="history") is True
    assert _gate(engine, relation_id=contradicted, mode="at", at=_FUTURE) is False
    # before any declaration the lineage was undeclared: evidence is unrestricted
    assert (
        _gate(
            engine,
            relation_id=future_fact,
            mode="current",
            believed_at=before_declarations,
        )
        is True
    )


def test_chunks_of_every_ready_version_are_readable_for_scoped_reads(
    engine: Engine,
) -> None:
    """``chunks_all_versions_live`` holds editions that are not served."""
    one = _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    two = _ingest(engine, content=b"edition 2", effective_from=_Y2026)
    pending = _ingest(engine, content=b"edition 3", effective_from=_FUTURE)
    readings = {
        one.version_id: _ready(engine, version=one, serve=False),
        two.version_id: _ready(engine, version=two),
    }
    with engine.begin() as connection:
        for version in (one, two):
            _chunk_with_claim(
                connection,
                version=version,
                representation_id=readings[version.version_id],
            )
        connection.execute(
            text(
                "INSERT INTO chunks (chunk_id, deployment_id, doc_id, version_id,"
                " representation_id, ordinal, block_start, block_end,"
                " chunk_content_hash, extraction_input_hash, char_start, char_end,"
                " chunker_version) VALUES (:c, :d, :doc, :v, :r, 0, 0, 0, 'h',"
                " 'h', 0, 8, 'test')"
            ),
            {
                "c": uuid4(),
                "d": _DEPLOYMENT_ID,
                "doc": pending.doc_id,
                "v": pending.version_id,
                "r": readings[one.version_id],
            },
        )
    with engine.connect() as connection:
        every = set(
            connection.execute(
                text(
                    "SELECT version_id FROM memory_v1.chunks_all_versions_live"
                    " WHERE doc_id = :doc"
                ),
                {"doc": one.doc_id},
            ).scalars()
        )
        served = set(
            connection.execute(
                text(
                    "SELECT version_id FROM memory_v1.chunks_live WHERE doc_id = :doc"
                ),
                {"doc": one.doc_id},
            ).scalars()
        )
        text_origin = connection.execute(
            text("SELECT text_origin_at FROM memory_v1.chunks_live LIMIT 1")
        ).scalar_one()
    assert every == {one.version_id, two.version_id}
    assert served == {two.version_id}
    assert text_origin is None


def test_the_query_role_reads_the_time_scope_surface(engine: Engine) -> None:
    """The public functions and views run for the deployment's query login.

    The functions read private ledgers, so they are definer-rights functions
    owned by the view owner; the query role holds only EXECUTE and SELECT.
    """
    one = _ingest(engine, content=b"edition 1", effective_from=_Y2024)
    _ready(engine, version=one)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "SELECT set_config('role', 'rememberstack_query_' ||"
                    " current_database(), true)"
                )
            )
            in_scope = connection.execute(
                text(
                    "SELECT version_id FROM memory_v1.versions_in_scope("
                    " CAST(:d AS uuid), 'current')"
                ),
                {"d": _DEPLOYMENT_ID},
            ).scalars()
            assert list(in_scope) == [one.version_id]
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM memory_v1.effective_intervals("
                        " CAST(:d AS uuid), ARRAY[CAST(:doc AS uuid)])"
                    ),
                    {"d": _DEPLOYMENT_ID, "doc": one.doc_id},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT memory_v1.fact_in_scope_support(CAST(:d AS uuid),"
                        " 'relation', CAST(:r AS uuid), 'current')"
                    ),
                    {"d": _DEPLOYMENT_ID, "r": uuid4()},
                ).scalar_one()
                is False
            )
            for view in (
                "document_effective_periods_live",
                "chunks_all_versions_live",
                "document_crossrefs_live",
            ):
                connection.execute(text(f"SELECT count(*) FROM memory_v1.{view}"))
            with pytest.raises(DBAPIError, match="permission denied"):
                connection.execute(
                    text("SELECT count(*) FROM public.document_version_scope")
                )
        finally:
            transaction.rollback()
