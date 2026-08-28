"""D99 convergence lock policy without a PostgreSQL fixture."""

from contextlib import AbstractContextManager
from contextlib import nullcontext
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import RecordingProfileRefresher
from rememberstack.model import ClusterConfig
from rememberstack.ports.p1_index import EntityIndexPort
from rememberstack.spine.clustering import _identity_epoch_lock
from rememberstack.spine.clustering import EntityClusterer
from rememberstack.spine.profile_refresher import current_profile_entity_ids

_DEPLOYMENT_ID = UUID("c0000000-0000-0000-0000-000000000001")


class _EmptyResult:
    """Minimal empty SQL result for the early-return convergence path."""

    def mappings(self) -> tuple[object, ...]:
        """Return no gathered neighborhood members."""
        return ()

    def scalar_one(self) -> bool:
        """Model an uncontended try-lock acquisition."""
        return True


class _RecordingConnection:
    """Record SQL strings while returning an empty gather result."""

    def __init__(self, *, try_lock_acquired: bool = True) -> None:
        """Start with no executed statements."""
        self._try_lock_acquired = try_lock_acquired
        self.executions: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement: object, parameters: object) -> object:
        """Record one statement and return the empty result."""
        self.executions.append((str(statement), cast(dict[str, object], parameters)))
        if "pg_try_advisory_xact_lock" in str(statement):
            return _BooleanResult(acquired=self._try_lock_acquired)
        return _EmptyResult()


class _RecordingEngine:
    """Expose one recording connection through the Engine begin contract."""

    def __init__(self) -> None:
        """Create the shared recording connection."""
        self.connection = _RecordingConnection()

    def begin(self) -> AbstractContextManager[_RecordingConnection]:
        """Open a no-op transaction context around the recording connection."""
        return nullcontext(self.connection)


class _UnusedEntityIndex:
    """Entity index double for a convergence path with no gathered members."""

    def entity_vectors(
        self, *, deployment_id: str, entity_ids: tuple[str, ...]
    ) -> dict[str, tuple[float, ...]]:
        """Return no vectors; the early-return path never calls this method."""
        del deployment_id, entity_ids
        return {}


@pytest.mark.parametrize(
    ("auto_merge_enabled", "shared_expected"), ((False, True), (True, False))
)
def test_recluster_executes_lock_selected_by_mutation_policy(
    *, auto_merge_enabled: bool, shared_expected: bool
) -> None:
    """The public convergence call site must pass its actual mutation policy."""
    engine = _RecordingEngine()
    clusterer = EntityClusterer(
        engine=cast(Engine, engine),
        entity_index=cast(EntityIndexPort, _UnusedEntityIndex()),
        profile_refresher=RecordingProfileRefresher(),
        config=ClusterConfig(auto_merge_enabled=auto_merge_enabled),
    )

    clusterer.recluster_neighborhood(deployment_id=_DEPLOYMENT_ID, surface="Caroline")

    selected = next(
        statement
        for statement, parameters in engine.connection.executions
        if parameters["key"] == f"{_DEPLOYMENT_ID}:identity-epoch"
    )
    assert ("pg_advisory_xact_lock_shared" in selected) is shared_expected


@pytest.mark.parametrize(
    ("auto_merge_enabled", "try_lock_expected"), ((False, True), (True, False))
)
def test_recluster_neighborhood_lock_matches_mutation_policy(
    *, auto_merge_enabled: bool, try_lock_expected: bool
) -> None:
    """Proposal nomination coalesces while mutation keeps global serialization."""
    engine = _RecordingEngine()
    clusterer = EntityClusterer(
        engine=cast(Engine, engine),
        entity_index=cast(EntityIndexPort, _UnusedEntityIndex()),
        profile_refresher=RecordingProfileRefresher(),
        config=ClusterConfig(auto_merge_enabled=auto_merge_enabled),
    )

    clusterer.recluster_neighborhood(deployment_id=_DEPLOYMENT_ID, surface="Caroline")

    statement, parameters = engine.connection.executions[0]
    assert ("pg_try_advisory_xact_lock" in statement) is try_lock_expected
    expected_key = (
        f"{_DEPLOYMENT_ID}:cluster:caroline"
        if try_lock_expected
        else f"{_DEPLOYMENT_ID}:cluster"
    )
    assert parameters["key"] == expected_key


def test_busy_proposal_neighborhood_is_coalesced_before_gather() -> None:
    """A duplicate proposal nomination exits after its nonblocking lemma lock."""
    engine = _RecordingEngine()
    engine.connection = _RecordingConnection(try_lock_acquired=False)
    clusterer = EntityClusterer(
        engine=cast(Engine, engine),
        entity_index=cast(EntityIndexPort, _UnusedEntityIndex()),
        profile_refresher=RecordingProfileRefresher(),
        config=ClusterConfig(auto_merge_enabled=False),
    )

    report = clusterer.recluster_neighborhood(
        deployment_id=_DEPLOYMENT_ID, surface="Caroline"
    )

    assert report.members == 0
    assert len(engine.connection.executions) == 1


def test_proposal_only_convergence_uses_shared_identity_epoch_lock() -> None:
    """Review proposal generation must not block ordinary shared identity work."""
    statement = _identity_epoch_lock(auto_merge_enabled=False)

    assert "pg_advisory_xact_lock_shared" in str(statement)


def test_automatic_merge_convergence_keeps_exclusive_identity_epoch_lock() -> None:
    """A convergence pass that may merge or split must retain exclusive authority."""
    statement = _identity_epoch_lock(auto_merge_enabled=True)

    assert "pg_advisory_xact_lock_shared" not in str(statement)
    assert "pg_advisory_xact_lock(" in str(statement)


class _ScalarResult:
    """Scalar iterable result for scripted identity closures."""

    def __init__(self, values: tuple[UUID, ...]) -> None:
        """Store the scripted scalar values."""
        self._values = values

    def scalars(self) -> tuple[UUID, ...]:
        """Return the scripted scalar values."""
        return self._values


class _BooleanResult:
    """One scalar boolean result for a try-lock."""

    def __init__(self, *, acquired: bool) -> None:
        """Store whether the scripted lock was acquired."""
        self._acquired = acquired

    def scalar_one(self) -> bool:
        """Return the scripted acquisition result."""
        return self._acquired


class _OptionalMappingResult:
    """Empty mapping result for the post-lock profile read."""

    def mappings(self) -> "_OptionalMappingResult":
        """Support SQLAlchemy's mappings chain."""
        return self

    def one_or_none(self) -> None:
        """Model a missing entity after the lock-order assertion point."""
        return None


class _ClosureConnection:
    """Script closure membership and record observation advisory-lock order."""

    def __init__(
        self,
        *,
        closures: dict[UUID, tuple[UUID, ...]],
        busy_member_id: UUID | None = None,
    ) -> None:
        """Bind closures and start with no recorded observation locks."""
        self._closures = closures
        self._busy_member_id = busy_member_id
        self.observation_lock_keys: list[str] = []

    def execute(self, statement: object, parameters: object) -> object:
        """Serve closure/entity reads and record observation locks."""
        values = cast(dict[str, object], parameters)
        sql = str(statement)
        if "WITH RECURSIVE members" in sql:
            entity_id = cast(UUID, values["entity_id"])
            return _ScalarResult(self._closures[entity_id])
        if "pg_try_advisory_xact_lock" in sql:
            key = cast(str, values["key"])
            self.observation_lock_keys.append(key)
            return _BooleanResult(
                acquired=key != f"{_DEPLOYMENT_ID}:obs:{self._busy_member_id}"
            )
        if "pg_advisory_xact_lock(hashtextextended" in sql:
            self.observation_lock_keys.append(cast(str, values["key"]))
            return _EmptyResult()
        if "FROM entities" in sql:
            return _OptionalMappingResult()
        return _EmptyResult()


def test_profile_attestation_prelocks_union_of_closures_in_global_order() -> None:
    """Overlapping merged closures cannot acquire observation locks out of order."""
    first = UUID("00000000-0000-0000-0000-000000000001")
    middle = UUID("00000000-0000-0000-0000-000000000002")
    root = UUID("00000000-0000-0000-0000-000000000005")
    connection = _ClosureConnection(
        closures={middle: (middle,), root: (first, middle, root)}
    )

    current = current_profile_entity_ids(
        connection=cast(Connection, connection),
        deployment_id=_DEPLOYMENT_ID,
        entity_ids=(root, middle),
    )

    assert current == frozenset()
    assert connection.observation_lock_keys == [
        f"{_DEPLOYMENT_ID}:obs:{first}",
        f"{_DEPLOYMENT_ID}:obs:{middle}",
        f"{_DEPLOYMENT_ID}:obs:{root}",
    ]


def test_proposal_attestation_skips_busy_member_without_waiting() -> None:
    """A proposal-only pass defers instead of convoying behind hot evidence."""
    first = UUID("00000000-0000-0000-0000-000000000001")
    busy = UUID("00000000-0000-0000-0000-000000000002")
    later = UUID("00000000-0000-0000-0000-000000000003")
    connection = _ClosureConnection(
        closures={first: (first, busy, later)}, busy_member_id=busy
    )

    current = current_profile_entity_ids(
        connection=cast(Connection, connection),
        deployment_id=_DEPLOYMENT_ID,
        entity_ids=(first,),
        wait_for_locks=False,
    )

    assert current is None
    assert connection.observation_lock_keys == [
        f"{_DEPLOYMENT_ID}:obs:{first}",
        f"{_DEPLOYMENT_ID}:obs:{busy}",
    ]
