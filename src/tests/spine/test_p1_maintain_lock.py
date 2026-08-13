"""Real-PostgreSQL proofs for D91 table-scoped maintain lock bounds."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from time import monotonic
from uuid import UUID
from uuid import uuid4

from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import event
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.selfhost.p1_locked_purge import LockingP1Purge
from rememberstack.spine.p1_maintain_lock import hold_p1_table_maintain_locks
from rememberstack.spine.p1_maintain_lock import p1_table_maintain_lock_key
from rememberstack.spine.p1_maintain_lock import P1MaintainLockTimeout
from rememberstack.spine.settings import load_database_settings

_ROOT = Path("/tmp/rememberstack-test-lance")
_TRY = text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))")
_UNLOCK = text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))")
_SHORT = timedelta(milliseconds=400)
_POLL = timedelta(milliseconds=20)


@pytest.fixture()
def database_engine() -> Iterator[Engine]:
    """A live Postgres engine; advisory locks do not need the app schema."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip(
            "REMEMBERSTACK_DATABASE_URL is required for P1 maintain lock proofs"
        )
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


def _key(table_name: str, *, lance_root: Path = _ROOT) -> str:
    """Lock material for one table under the test root."""
    return p1_table_maintain_lock_key(lance_root=lance_root, table_name=table_name)


def _try_lock(*, engine: Engine, key: str) -> bool:
    """Probe one session lock from a connection that is not the helper's."""
    with engine.connect() as connection:
        locked = connection.execute(_TRY, {"key": key}).scalar()
        if locked is True:
            connection.execute(_UNLOCK, {"key": key})
        connection.commit()
    return locked is True


class _RecordingIndex:
    """Stand-in P1 adapter that records purge calls without touching Lance."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def purge_rows(
        self,
        *,
        deployment_id: UUID,
        chunk_ids: tuple[UUID, ...],
        claim_ids: tuple[UUID, ...],
        fact_ids: tuple[UUID, ...],
        entity_ids: tuple[UUID, ...],
    ) -> None:
        """Record one locked purge invocation."""
        self.calls.append(
            {
                "deployment_id": deployment_id,
                "chunk_ids": chunk_ids,
                "claim_ids": claim_ids,
                "fact_ids": fact_ids,
                "entity_ids": entity_ids,
            }
        )

    def verify_rows_purged(
        self,
        *,
        deployment_id: UUID,
        chunk_ids: tuple[UUID, ...],
        claim_ids: tuple[UUID, ...],
        fact_ids: tuple[UUID, ...],
        entity_ids: tuple[UUID, ...],
    ) -> None:
        """Unused verification surface for the recording fake."""
        return None


def test_lock_timeout_releases_earlier_keys(database_engine: Engine) -> None:
    """A blocked second acquire fails inside the bound and frees the first key."""
    facts = _key("facts")
    chunks = _key("chunks")
    holder = database_engine.connect()
    assert holder.execute(_TRY, {"key": facts}).scalar() is True
    holder.commit()
    try:
        started = monotonic()
        with pytest.raises(P1MaintainLockTimeout, match="facts"):
            with hold_p1_table_maintain_locks(
                engine=database_engine,
                lance_root=_ROOT,
                tables=("chunks", "facts"),
                timeout=_SHORT,
                poll_interval=_POLL,
            ):
                raise AssertionError("must not enter the locked body")
        assert monotonic() - started < 2.0
        assert _try_lock(engine=database_engine, key=chunks)
        assert _try_lock(engine=database_engine, key=facts) is False
    finally:
        holder.execute(_UNLOCK, {"key": facts})
        holder.commit()
        holder.close()
    assert _try_lock(engine=database_engine, key=facts)


def test_failed_first_release_invalidates_and_frees_keys(
    database_engine: Engine,
) -> None:
    """An unlock error still drops the session so leftover keys do not leak."""
    facts = _key("facts")
    chunks = _key("chunks")
    unlocks = {"n": 0}

    def _inject(
        _conn: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if "pg_advisory_unlock" not in statement:
            return
        unlocks["n"] += 1
        if unlocks["n"] == 1:
            raise RuntimeError("injected unlock failure")

    event.listen(database_engine, "before_cursor_execute", _inject)
    try:
        with pytest.raises(RuntimeError, match="injected unlock failure"):
            with hold_p1_table_maintain_locks(
                engine=database_engine,
                lance_root=_ROOT,
                tables=("chunks", "facts"),
                timeout=_SHORT,
                poll_interval=_POLL,
            ):
                pass
    finally:
        event.remove(database_engine, "before_cursor_execute", _inject)
    assert _try_lock(engine=database_engine, key=chunks)
    assert _try_lock(engine=database_engine, key=facts)


def test_purge_times_out_on_held_table_then_proceeds_after_release(
    database_engine: Engine,
) -> None:
    """Hard-forget fails inside the bound while a peer holds the table lock."""
    index = _RecordingIndex()
    purge = LockingP1Purge(
        index=index,  # type: ignore[arg-type]
        engine=database_engine,
        lance_root=_ROOT,
        lock_timeout=_SHORT,
    )
    fact_id = uuid4()
    holder = database_engine.connect()
    assert holder.execute(_TRY, {"key": _key("facts")}).scalar() is True
    holder.commit()
    try:
        started = monotonic()
        with pytest.raises(P1MaintainLockTimeout):
            purge.purge_rows(
                deployment_id=uuid4(),
                chunk_ids=(),
                claim_ids=(),
                fact_ids=(fact_id,),
                entity_ids=(),
            )
        assert monotonic() - started < 2.0
        assert index.calls == []
    finally:
        holder.execute(_UNLOCK, {"key": _key("facts")})
        holder.commit()
        holder.close()
    purge.purge_rows(
        deployment_id=uuid4(),
        chunk_ids=(),
        claim_ids=(),
        fact_ids=(fact_id,),
        entity_ids=(),
    )
    assert len(index.calls) == 1
    assert index.calls[0]["fact_ids"] == (fact_id,)


def test_purge_does_not_wait_on_unaffected_table(database_engine: Engine) -> None:
    """A facts-only purge must not take the chunks lock."""
    index = _RecordingIndex()
    purge = LockingP1Purge(
        index=index,  # type: ignore[arg-type]
        engine=database_engine,
        lance_root=_ROOT,
        lock_timeout=_SHORT,
    )
    holder = database_engine.connect()
    assert holder.execute(_TRY, {"key": _key("chunks")}).scalar() is True
    holder.commit()
    try:
        purge.purge_rows(
            deployment_id=uuid4(),
            chunk_ids=(),
            claim_ids=(),
            fact_ids=(uuid4(),),
            entity_ids=(),
        )
    finally:
        holder.execute(_UNLOCK, {"key": _key("chunks")})
        holder.commit()
        holder.close()
    assert len(index.calls) == 1


def test_purge_and_finalizer_share_custom_root_key() -> None:
    """Forget and backfill must hash the same non-default lance_root."""
    custom = Path("/var/tmp/rememberstack-custom-lance")
    assert _key("entities", lance_root=custom) == p1_table_maintain_lock_key(
        lance_root=str(custom), table_name="entities"
    )
    assert _key("entities", lance_root=custom) != _key("entities")
