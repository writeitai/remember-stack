"""D110 short transactions: common locks, typed effects, support, and replay order.

Callers own the surrounding SQLAlchemy transaction. They declare the complete
canonical read/write block and fact set before inference or application; no
remote inference runs inside this context. Existing plane adjudications remain
the narrative authority and are inserted atomically with each journal effect.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone
import hashlib
import json
from typing import Literal
from uuid import UUID

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import RowMapping

from rememberstack.core.temporal import canonical_bounds
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.forget import ForgetInProgressError
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlock
from rememberstack.model.temporal_write import TemporalBlockState
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalEvidenceRef
from rememberstack.model.temporal_write import TemporalFactRef
from rememberstack.model.temporal_write import TemporalOperationKind
from rememberstack.model.temporal_write import TemporalSourceKind
from rememberstack.model.temporal_write import TemporalSourceRef
from rememberstack.spine.admission import active_forget_id_on

TEMPORAL_FACT_GENERATION = "temporal-facts-d107-d110-1"


class TemporalWriteConflict(RuntimeError):
    """A prepared input, declared lock set, or durable journal no longer agrees."""


class TemporalNotReadyError(RuntimeError):
    """Ordinary temporal writes cannot consume a store without completed conversion."""


def temporal_fingerprint(*, value: object) -> str:
    """Hash canonical structured inputs, preserving UUIDs and explicit UTC instants."""
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_scalar,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def temporal_block_key(*, deployment_id: UUID, block: TemporalBlock) -> str:
    """Encode the complete key without a lossy hash or delimiter ambiguity."""
    return json.dumps(
        [
            "temporal-block-v1",
            str(deployment_id),
            block.plane.value,
            str(block.subject_entity_id),
            block.predicate,
        ],
        separators=(",", ":"),
        ensure_ascii=True,
    )


@contextmanager
def temporal_write(
    *,
    connection: Connection,
    deployment_id: UUID,
    blocks: tuple[TemporalBlock, ...],
    facts: tuple[TemporalFactRef, ...],
    sources: tuple[TemporalSourceRef, ...] = (),
    new_facts: frozenset[TemporalFactRef] = frozenset(),
    maintenance: Literal["conversion", "forget"] | None = None,
    authority_id: UUID | None = None,
    identity_write: bool = False,
) -> Iterator["TemporalWriteSession"]:
    """Keep the complete application group atomic even if the caller catches its error.

    The caller must open the outer transaction and declare all candidate blocks,
    facts and routing sources. Fact leaves are added automatically; all sources
    are locked together after facts. Inference never runs inside this context.
    """
    if not connection.in_transaction():
        raise TemporalWriteConflict("temporal writes require an explicit transaction")
    with connection.begin_nested():
        with _temporal_write_locked(
            connection=connection,
            deployment_id=deployment_id,
            blocks=blocks,
            facts=facts,
            sources=sources,
            new_facts=new_facts,
            maintenance=maintenance,
            authority_id=authority_id,
            identity_write=identity_write,
        ) as session:
            yield session


def _lock_sources(
    *,
    connection: Connection,
    deployment_id: UUID,
    sources: tuple[TemporalSourceRef, ...],
    leaf_revisions: dict[tuple[TemporalSourceKind, UUID], int],
) -> dict[TemporalSourceRef, int]:
    """Create and lock the entire cache set in one order, checking registry collisions."""
    identities: dict[tuple[TemporalSourceKind, UUID], str] = {}
    registry: dict[tuple[TemporalSourceKind, str], UUID] = {}
    for source in sources:
        if (
            identities.setdefault((source.kind, source.source_id), source.key)
            != source.key
        ):
            raise TemporalWriteConflict("source UUID collision in declared cache keys")
        if (
            registry.setdefault((source.kind, source.key), source.source_id)
            != source.source_id
        ):
            raise TemporalWriteConflict("source registry key maps to different UUIDs")
        if source.kind in (TemporalSourceKind.RELATION, TemporalSourceKind.OBSERVATION):
            if (source.kind, source.source_id) not in leaf_revisions:
                raise TemporalWriteConflict(
                    "fact cache source requires the corresponding fact lock"
                )
    result: dict[TemporalSourceRef, int] = {}
    for source in sorted(
        set(sources), key=lambda item: (item.kind.value, item.source_id)
    ):
        expected = leaf_revisions.get((source.kind, source.source_id))
        params = {
            "deployment_id": deployment_id,
            "kind": source.kind.value,
            "source_id": source.source_id,
            "key": source.key,
            "revision": expected if expected is not None else 0,
        }
        connection.execute(_ENSURE_SOURCE, params)
        row = connection.execute(_LOCK_SOURCE, params).mappings().one_or_none()
        if row is None or row["source_key"] != source.key:
            raise TemporalWriteConflict("cache registry identity collision")
        if expected is not None and row["revision"] != expected:
            raise TemporalWriteConflict("fact source revision disagrees with authority")
        result[source] = row["revision"]
    return result


def _canonical_subject(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> UUID:
    """Follow deployment-local redirects under the identity epoch, rejecting broken chains."""
    root = connection.execute(
        _CANONICAL_SUBJECT, {"deployment_id": deployment_id, "entity_id": entity_id}
    ).scalar_one_or_none()
    if root is None:
        raise TemporalWriteConflict("fact subject has no active canonical survivor")
    return UUID(str(root))


def _require_fact_block(
    *,
    connection: Connection,
    deployment_id: UUID,
    fact: TemporalFactRef,
    row: RowMapping,
    keys: set[str],
) -> None:
    """Prove that the actual target belongs to a declared canonical subject block."""
    root = _canonical_subject(
        connection=connection,
        deployment_id=deployment_id,
        entity_id=row["subject_entity_id"],
    )
    block = TemporalBlock(
        plane=fact.plane, subject_entity_id=root, predicate=row["predicate"]
    )
    if temporal_block_key(deployment_id=deployment_id, block=block) not in keys:
        raise TemporalWriteConflict(
            "actual fact subject block is absent from the declared footprint"
        )


@contextmanager
def _temporal_write_locked(
    *,
    connection: Connection,
    deployment_id: UUID,
    blocks: tuple[TemporalBlock, ...],
    facts: tuple[TemporalFactRef, ...],
    sources: tuple[TemporalSourceRef, ...],
    new_facts: frozenset[TemporalFactRef] = frozenset(),
    maintenance: Literal["conversion", "forget"] | None = None,
    authority_id: UUID | None = None,
    identity_write: bool = False,
) -> Iterator["TemporalWriteSession"]:
    """Join a transaction under deployment, identity, block, fact, then leaf locks.

    A missing fact is legal only when declared as a new fact, created in this
    transaction, and committed at revision one with its seed receipt. Conversion
    and forget access must name the current durable maintenance authority.
    """
    if not connection.in_transaction():
        raise TemporalWriteConflict("temporal writes require an explicit transaction")
    if not blocks:
        raise TemporalWriteConflict(
            "a temporal session requires its complete block set"
        )
    if not new_facts.issubset(facts):
        raise TemporalWriteConflict(
            "new facts must belong to the declared fact lock set"
        )
    _lock_admission(
        connection=connection,
        deployment_id=deployment_id,
        maintenance=maintenance,
        authority_id=authority_id,
    )
    lock_identity = (
        "pg_advisory_xact_lock"
        if identity_write or maintenance == "forget"
        else "pg_advisory_xact_lock_shared"
    )
    connection.execute(
        text(f"SELECT {lock_identity}(hashtextextended(:key, 0))"),
        {"key": f"{deployment_id}:identity-epoch"},
    )
    for block in blocks:
        root = _canonical_subject(
            connection=connection,
            deployment_id=deployment_id,
            entity_id=block.subject_entity_id,
        )
        if root != block.subject_entity_id:
            raise TemporalWriteConflict(
                "declared block subject is not its canonical survivor"
            )
    keys = sorted(
        {
            temporal_block_key(deployment_id=deployment_id, block=block)
            for block in blocks
        }
    )
    heads: dict[str, TemporalBlockState] = {}
    for key in keys:
        connection.execute(_ENSURE_BLOCK, {"deployment_id": deployment_id, "key": key})
        row = (
            connection.execute(
                _LOCK_BLOCK, {"deployment_id": deployment_id, "key": key}
            )
            .mappings()
            .one()
        )
        head = TemporalBlockState(
            block_key=key,
            revision=row["revision"],
            sequence=row["last_sequence"],
            operation_id=row["operation_id"],
        )
        if head.sequence and head.operation_id is None:
            raise TemporalWriteConflict("block sequence has no corresponding effect")
        heads[key] = head
    states: dict[TemporalFactRef, FactTemporalState | None] = {}
    for fact in sorted(set(facts), key=lambda item: (item.plane.value, item.fact_id)):
        row = _load_fact(connection=connection, deployment_id=deployment_id, fact=fact)
        if row is not None and fact in new_facts:
            raise TemporalWriteConflict("a new fact identity already exists")
        if row is None and fact not in new_facts:
            raise TemporalWriteConflict(
                f"declared {fact.plane} {fact.fact_id} is missing"
            )
        if row is not None:
            _require_fact_block(
                connection=connection,
                deployment_id=deployment_id,
                fact=fact,
                row=row,
                keys=set(keys),
            )
        states[fact] = _state(row=row) if row is not None else None
    # Routing keys and fact leaves share ONE globally sorted source phase.
    # Acquiring a new source lock after yielding would violate this order.
    source_refs = list(sources)
    leaf_revisions: dict[tuple[TemporalSourceKind, UUID], int] = {}
    for fact, state in states.items():
        kind = TemporalSourceKind(fact.plane.value)
        leaf_revisions[kind, fact.fact_id] = state.revision if state is not None else 0
        source_refs.append(
            TemporalSourceRef(kind=kind, source_id=fact.fact_id, key=str(fact.fact_id))
        )
    source_states = _lock_sources(
        connection=connection,
        deployment_id=deployment_id,
        sources=tuple(source_refs),
        leaf_revisions=leaf_revisions,
    )
    session = TemporalWriteSession(
        connection=connection,
        deployment_id=deployment_id,
        heads=heads,
        states=states,
        new_facts=new_facts,
        maintenance=maintenance,
        identity_write=identity_write,
        source_states=source_states,
    )
    try:
        yield session
        session.verify_complete()
    finally:
        session.close()


class TemporalWriteSession:
    """One already-locked atomic application group, never a long-lived worker lease."""

    def __init__(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        heads: dict[str, TemporalBlockState],
        states: dict[TemporalFactRef, FactTemporalState | None],
        new_facts: frozenset[TemporalFactRef],
        maintenance: Literal["conversion", "forget"] | None,
        identity_write: bool,
        source_states: dict[TemporalSourceRef, int],
    ) -> None:
        """Retain only the transaction-local locked state and expected new facts."""
        self.connection = connection
        self.deployment_id = deployment_id
        self._heads = heads
        self._states = states
        self._new_facts = new_facts
        self._maintenance = maintenance
        self._identity_write = identity_write
        self._seeded: set[TemporalFactRef] = set()
        self._source_states = source_states
        self._open = True
        self._failed = False

    @property
    def block_states(self) -> tuple[TemporalBlockState, ...]:
        """Expose immutable block snapshots for preparation and exact replay witnesses."""
        return tuple(self._heads.values())

    def state(self, *, fact: TemporalFactRef) -> FactTemporalState | None:
        """Read a declared locked fact; None denotes an explicitly requested insertion."""
        if fact not in self._states:
            raise TemporalWriteConflict("fact was not included in the lock set")
        return self._states[fact]

    def require_revisions(self, *, prepared: tuple[TemporalBlockState, ...]) -> None:
        """Refuse changed or incomplete read blocks; read-only sequence moves are harmless."""
        if {item.block_key for item in prepared} != set(self._heads):
            raise TemporalWriteConflict(
                "prepared block footprint differs from the locked set"
            )
        for item in prepared:
            if self._heads[item.block_key].revision != item.revision:
                raise TemporalWriteConflict(
                    "a prepared block changed before application"
                )

    def close(self) -> None:
        """Prevent retaining a session after its locks or savepoint have ended."""
        self._open = False

    def source_revision(self, *, source: TemporalSourceRef) -> int:
        """Read an already locked cache source, never add locks after application starts."""
        if not self._open or source not in self._source_states:
            raise TemporalWriteConflict(
                "cache source was not locked in this live session"
            )
        return self._source_states[source]

    def apply(self, *, effect: TemporalEffect, written_blocks: frozenset[str]) -> None:
        """Apply one effect; a caught application error still prevents group commit."""
        if not self._open or self._failed:
            raise TemporalWriteConflict(
                "temporal session is closed or has a failed effect"
            )
        try:
            self._apply(effect=effect, written_blocks=written_blocks)
        except Exception:
            self._failed = True
            raise

    def _apply(self, *, effect: TemporalEffect, written_blocks: frozenset[str]) -> None:
        """Revalidate and atomically write state, narrative, support and complete footprint.

        The caller writes evidence membership in the same outer transaction.
        All live candidate blocks, including empty read-only blocks, must be in
        this session; only declared mutated blocks advance their revisions.
        """
        if not written_blocks.issubset(self._heads):
            raise TemporalWriteConflict("written block was not locked")
        if effect.result is TemporalResult.APPLIED and not written_blocks:
            raise TemporalWriteConflict("a mutation requires its affected block")
        if effect.result is not TemporalResult.APPLIED and written_blocks:
            raise TemporalWriteConflict(
                "a non-applied witness cannot advance block truth"
            )
        if effect.fact not in self._states:
            raise TemporalWriteConflict("effect target was not locked")
        if (
            effect.kind is TemporalOperationKind.MIGRATION
            and self._maintenance != "conversion"
        ):
            raise TemporalWriteConflict(
                "migration effect requires conversion authority"
            )
        if (
            effect.kind is TemporalOperationKind.FORGET_RECOMPUTE
            and self._maintenance != "forget"
        ):
            raise TemporalWriteConflict("checkpoint effect requires forget authority")
        if effect.kind is TemporalOperationKind.IDENTITY and not self._identity_write:
            raise TemporalWriteConflict(
                "identity effect requires the exclusive identity epoch"
            )
        actual = _load_fact(
            connection=self.connection,
            deployment_id=self.deployment_id,
            fact=effect.fact,
        )
        if actual is None or _state(row=actual) != effect.before:
            raise TemporalWriteConflict(
                "prepared fact tuple/revision no longer matches"
            )
        if (
            self._states[effect.fact] is None
            and effect.kind is not TemporalOperationKind.SEED
        ):
            raise TemporalWriteConflict("new fact must begin with its seed effect")
        _require_fact_block(
            connection=self.connection,
            deployment_id=self.deployment_id,
            fact=effect.fact,
            row=actual,
            keys=set(written_blocks)
            if effect.result is TemporalResult.APPLIED
            else set(self._heads),
        )
        self._check_evidence(effect=effect)
        if effect.kind is TemporalOperationKind.COMPENSATION:
            self._check_compensation(effect=effect)
        self._insert_effect(effect=effect)
        self._insert_decision(effect=effect)
        self._record_footprint(effect=effect, written_blocks=written_blocks)
        self._record_support(effect=effect)
        if effect.result is TemporalResult.APPLIED:
            self._write_state(effect=effect)
        self._states[effect.fact] = effect.after
        if effect.kind is TemporalOperationKind.SEED:
            self._seeded.add(effect.fact)

    def verify_complete(self) -> None:
        """Refuse a committed revision-zero placeholder or unused speculative leaf."""
        if not self._open or self._failed:
            raise TemporalWriteConflict("failed application group cannot commit")
        if self._seeded != self._new_facts:
            raise TemporalWriteConflict(
                "new fact insertion lacks its atomic seed receipt"
            )

    def _check_evidence(self, *, effect: TemporalEffect) -> None:
        """Ensure retained prepared claim fingerprints and currency still agree."""
        candidate_starts: set[datetime | None] = set()
        candidate_ends: set[datetime | None] = set()
        for evidence in effect.evidence:
            current = load_temporal_evidence(
                connection=self.connection,
                deployment_id=self.deployment_id,
                claim_id=evidence.claim_id,
                role=evidence.role,
            )
            if current != evidence:
                raise TemporalWriteConflict(
                    "prepared testimony changed before application"
                )
            if effect.kind in (
                TemporalOperationKind.CORRECTION,
                TemporalOperationKind.COMPENSATION,
            ):
                plane = effect.fact.plane.value
                stance = self.connection.execute(
                    text(f"""
                    SELECT stance::text FROM {plane}_evidence
                    WHERE deployment_id = :deployment_id AND {plane}_id = :fact_id
                      AND claim_id = :claim_id
                """),
                    {
                        "deployment_id": self.deployment_id,
                        "fact_id": effect.fact.fact_id,
                        "claim_id": evidence.claim_id,
                    },
                ).scalar_one_or_none()
                if stance is None or (
                    evidence.role in ("support", "candidate_from", "candidate_until")
                    and stance != "supports"
                ):
                    raise TemporalWriteConflict(
                        "correction testimony is not linked with the required stance"
                    )
                if evidence.was_current and evidence.role in (
                    "candidate_from",
                    "candidate_until",
                ):
                    claim = (
                        self.connection.execute(
                            _CLAIM_INPUT,
                            {
                                "deployment_id": self.deployment_id,
                                "claim_id": evidence.claim_id,
                            },
                        )
                        .mappings()
                        .one()
                    )
                    bounds = canonical_bounds(
                        valid_from=claim["claim_valid_from"],
                        valid_until=claim["claim_valid_until"],
                        precision=claim["claim_valid_precision"],
                    )
                    if evidence.role == "candidate_from":
                        candidate_starts.add(bounds.start)
                    else:
                        candidate_ends.add(bounds.end)
        if (
            effect.kind is TemporalOperationKind.CORRECTION
            and effect.result is TemporalResult.APPLIED
        ):
            old, new = effect.before.verdict, effect.after.verdict
            if (old.start, old.start_basis) != (
                new.start,
                new.start_basis,
            ) and new.start not in candidate_starts:
                raise TemporalWriteConflict(
                    "corrected start is not a current supporting endpoint candidate"
                )
            if (old.end, old.end_basis) != (
                new.end,
                new.end_basis,
            ) and new.end not in candidate_ends:
                raise TemporalWriteConflict(
                    "corrected end is not a current supporting endpoint candidate"
                )

    def _check_compensation(self, *, effect: TemporalEffect) -> None:
        """Prove reversal target identity and endpoint ownership against durable history."""
        row = (
            self.connection.execute(
                _COMPENSATION_TARGET,
                {
                    "deployment_id": self.deployment_id,
                    "operation_id": effect.reverses_operation_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or row["operation_kind"] not in ("correction", "compensation")
            or (
                row["result"] != "applied"
                or row["replay_class"] != "ordinary"
                or row[f"{effect.fact.plane.value}_id"] != effect.fact.fact_id
                or row["support_state"] != "complete"
                or not row["footprint_complete"]
            )
        ):
            raise TemporalWriteConflict(
                "compensation target is not a supported reversible effect on this fact"
            )
        if effect.result is not TemporalResult.APPLIED:
            return
        for component, endpoint, owner in (
            ("from", "start", effect.before.from_operation_id),
            ("until", "end", effect.before.until_operation_id),
        ):
            before = (
                getattr(effect.before.verdict, endpoint),
                getattr(effect.before.verdict, f"{endpoint}_basis").value,
            )
            after = (
                getattr(effect.after.verdict, endpoint),
                getattr(effect.after.verdict, f"{endpoint}_basis").value,
            )
            restore = (
                row[f"changed_{component}"] and owner == effect.reverses_operation_id
            )
            expected = (
                (row[f"old_valid_{component}"], row[f"old_{component}_basis"])
                if restore
                else before
            )
            if restore and before != (
                row[f"new_valid_{component}"],
                row[f"new_{component}_basis"],
            ):
                raise TemporalWriteConflict(
                    "owned endpoint does not match the recorded target output"
                )
            if after != expected:
                raise TemporalWriteConflict(
                    "compensation must restore only still-owned recorded components"
                )

    def _insert_effect(self, *, effect: TemporalEffect) -> None:
        """Persist one immutable typed before/after tuple before transferring owners."""
        old, new = effect.before.verdict, effect.after.verdict
        self.connection.execute(
            _INSERT_EFFECT,
            {
                "operation_id": effect.operation_id,
                "deployment_id": self.deployment_id,
                "relation_id": effect.fact.fact_id
                if effect.fact.plane is FactPlane.RELATION
                else None,
                "observation_id": effect.fact.fact_id
                if effect.fact.plane is FactPlane.OBSERVATION
                else None,
                "discrepancy_id": effect.discrepancy_id,
                "operation_kind": effect.kind.value,
                "result": effect.result.value,
                "expected_revision": effect.before.revision,
                "resulting_revision": effect.after.revision,
                "old_valid_from": old.start,
                "old_valid_until": old.end,
                "old_from_basis": old.start_basis.value,
                "old_until_basis": old.end_basis.value,
                "new_valid_from": new.start,
                "new_valid_until": new.end,
                "new_from_basis": new.start_basis.value,
                "new_until_basis": new.end_basis.value,
                "old_invalidated_at": effect.before.invalidated_at,
                "new_invalidated_at": effect.after.invalidated_at,
                "old_from_operation_id": effect.before.from_operation_id,
                "old_until_operation_id": effect.before.until_operation_id,
                "changed_from": (old.start, old.start_basis)
                != (new.start, new.start_basis),
                "changed_until": (old.end, old.end_basis) != (new.end, new.end_basis),
                "reverses_operation_id": effect.reverses_operation_id,
                "input_fingerprint": effect.input_fingerprint,
                "identity_generation": effect.identity_generation,
                "policy_generation": effect.policy_generation,
                "reason_code": effect.reason,
                "recorded_at": effect.recorded_at,
                "replay_class": effect.replay_class,
            },
        )

    def _insert_decision(self, *, effect: TemporalEffect) -> None:
        """Keep the complete semantic record in the existing fact-plane adjudication."""
        plane = effect.fact.plane.value
        decision = effect.decision
        columns = ""
        values = ""
        if effect.fact.plane is FactPlane.RELATION:
            columns = ", triggering_assertion_id"
            values = ", :triggering_assertion_id"
        statement = text(f"""
            INSERT INTO {plane}_adjudications (
              adjudication_id, deployment_id, {plane}_id, related_{plane}_id,
              outcome, method, confidence, triggering_claim_id, features,
              adjudicator_version, decided_by, decided_at, temporal_operation_id{columns}
            ) VALUES (
              :adjudication_id, :deployment_id, :fact_id, :related_fact_id,
              :outcome, :method, :confidence, :triggering_claim_id, :features,
              :generation, :decided_by, :recorded_at, :operation_id{values}
            )
        """).bindparams(bindparam("features", type_=JSON))
        features = dict(decision.features)
        features["temporal_effect"] = {
            "format": 1,
            "operation_id": str(effect.operation_id),
            "before": effect.before.model_dump(mode="json"),
            "after": effect.after.model_dump(mode="json"),
            "reason": effect.reason,
        }
        self.connection.execute(
            statement,
            {
                "adjudication_id": decision.adjudication_id,
                "deployment_id": self.deployment_id,
                "fact_id": effect.fact.fact_id,
                "related_fact_id": decision.related_fact_id,
                "outcome": decision.outcome,
                "method": decision.method,
                "confidence": decision.confidence,
                "triggering_claim_id": decision.triggering_claim_id,
                "triggering_assertion_id": decision.triggering_assertion_id,
                "features": features,
                "generation": effect.policy_generation,
                "decided_by": decision.decided_by,
                "recorded_at": effect.recorded_at,
                "operation_id": effect.operation_id,
            },
        )

    def _record_footprint(
        self, *, effect: TemporalEffect, written_blocks: frozenset[str]
    ) -> None:
        """Advance every observed sequence and retain ordering-only vs semantic edges."""
        predecessors = {item: True for item in effect.semantic_predecessors}
        for key, head in self._heads.items():
            if head.operation_id is not None:
                predecessors.setdefault(head.operation_id, False)
            writes = key in written_blocks
            revision = head.revision + int(writes)
            sequence = head.sequence + 1
            self.connection.execute(
                _INSERT_BLOCK_EFFECT,
                {
                    "deployment_id": self.deployment_id,
                    "operation_id": effect.operation_id,
                    "key": key,
                    "sequence": sequence,
                    "writes": writes,
                    "old_revision": head.revision,
                    "new_revision": revision,
                },
            )
            self.connection.execute(
                _ADVANCE_BLOCK,
                {
                    "deployment_id": self.deployment_id,
                    "key": key,
                    "sequence": sequence,
                    "revision": revision,
                },
            )
            self._heads[key] = TemporalBlockState(
                block_key=key,
                revision=revision,
                sequence=sequence,
                operation_id=effect.operation_id,
            )
        for predecessor, semantic in sorted(predecessors.items()):
            self.connection.execute(
                _INSERT_PREDECESSOR,
                {
                    "deployment_id": self.deployment_id,
                    "operation_id": effect.operation_id,
                    "predecessor": predecessor,
                    "semantic": semantic,
                },
            )

    def _record_support(self, *, effect: TemporalEffect) -> None:
        """Attest the complete consumed set; never silently shrink proof after erasure."""
        for evidence in effect.evidence:
            self.connection.execute(
                _INSERT_EVIDENCE,
                {
                    "deployment_id": self.deployment_id,
                    "operation_id": effect.operation_id,
                    "claim_id": evidence.claim_id,
                    "role": evidence.role,
                    "was_current": evidence.was_current,
                    "fingerprint": evidence.fingerprint,
                },
            )
        self.connection.execute(
            _INSERT_SUPPORT,
            {
                "deployment_id": self.deployment_id,
                "operation_id": effect.operation_id,
                "state": effect.support_state,
                "footprint_complete": effect.footprint_complete,
                "blocks": len(self._heads),
                "claims": len({item.claim_id for item in effect.evidence}),
                "semantic_dependencies": len(set(effect.semantic_predecessors)),
                "fingerprint": temporal_fingerprint(
                    value={
                        "claims": [
                            item.model_dump(mode="json") for item in effect.evidence
                        ],
                        "semantic_predecessors": sorted(
                            map(str, set(effect.semantic_predecessors))
                        ),
                        "block_keys": sorted(self._heads),
                    }
                ),
            },
        )

    def _write_state(self, *, effect: TemporalEffect) -> None:
        """Write only typed state fields and atomically mirror the fact leaf revision."""
        plane = effect.fact.plane.value
        after = effect.after
        statement = text(f"""
            UPDATE {plane}s SET
              temporal_kind = :kind, valid_from = :start, valid_until = :end,
              valid_from_basis = :start_basis, valid_until_basis = :end_basis,
              occurs_from = :occurs_from, occurs_until = :occurs_until,
              occurs_precision = :occurs_precision, seed_claim_id = :seed_claim_id,
              invalidated_at = :invalidated_at, temporal_revision = :revision,
              from_operation_id = :from_owner, until_operation_id = :until_owner,
              contradiction_group = :contradiction_group, updated_at = :recorded_at
            WHERE deployment_id = :deployment_id AND {plane}_id = :fact_id
              AND temporal_revision = :expected_revision
            RETURNING {plane}_id
        """)
        changed = self.connection.execute(
            statement,
            {
                "deployment_id": self.deployment_id,
                "fact_id": effect.fact.fact_id,
                "kind": after.kind.value,
                "start": after.verdict.start,
                "end": after.verdict.end,
                "start_basis": after.verdict.start_basis.value,
                "end_basis": after.verdict.end_basis.value,
                "occurs_from": after.occurrence.start,
                "occurs_until": after.occurrence.end,
                "occurs_precision": after.occurrence.precision.value
                if after.occurrence.precision
                else None,
                "seed_claim_id": after.seed_claim_id,
                "invalidated_at": after.invalidated_at,
                "revision": after.revision,
                "from_owner": after.from_operation_id,
                "until_owner": after.until_operation_id,
                "contradiction_group": after.contradiction_group,
                "recorded_at": effect.recorded_at,
                "expected_revision": effect.before.revision,
            },
        ).scalar_one_or_none()
        if changed is None:
            raise TemporalWriteConflict("fact revision changed during application")
        boundaries = [
            value
            for value in (after.verdict.start, after.verdict.end)
            if value is not None and value > effect.recorded_at
        ]
        deadline = (
            min(boundaries) if boundaries and after.invalidated_at is None else None
        )
        self.connection.execute(
            _UPDATE_FACT_SOURCE,
            {
                "deployment_id": self.deployment_id,
                "plane": plane,
                "fact_id": effect.fact.fact_id,
                "revision": after.revision,
                "next_boundary": deadline,
            },
        )
        source = TemporalSourceRef(
            kind=TemporalSourceKind(plane),
            source_id=effect.fact.fact_id,
            key=str(effect.fact.fact_id),
        )
        self._source_states[source] = after.revision


def load_temporal_evidence(
    *,
    connection: Connection,
    deployment_id: UUID,
    claim_id: UUID,
    role: Literal[
        "support", "contrary", "historical", "candidate_from", "candidate_until"
    ],
) -> TemporalEvidenceRef:
    """Capture the exact immutable testimony plus mutable current-support bit."""
    row = (
        connection.execute(
            _CLAIM_INPUT, {"deployment_id": deployment_id, "claim_id": claim_id}
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise TemporalWriteConflict(
            "consumed claim is missing or belongs to another deployment"
        )
    return TemporalEvidenceRef(
        claim_id=claim_id,
        role=role,
        was_current=row["is_current_testimony"],
        fingerprint=temporal_fingerprint(value=dict(row)),
    )


def _lock_admission(
    *,
    connection: Connection,
    deployment_id: UUID,
    maintenance: Literal["conversion", "forget"] | None,
    authority_id: UUID | None,
) -> None:
    """Share the existing forget lock and require a durable maintenance authority."""
    lock = (
        "pg_advisory_xact_lock"
        if maintenance == "forget"
        else "pg_advisory_xact_lock_shared"
    )
    connection.execute(
        text(f"SELECT {lock}(hashtextextended(:key, 0))"),
        {"key": f"hard-forget:{deployment_id}"},
    )
    active = active_forget_id_on(connection=connection, deployment_id=deployment_id)
    if maintenance == "forget":
        if authority_id is None or active != authority_id:
            raise TemporalWriteConflict(
                "forget writer does not own the active manifest"
            )
        return
    if active is not None:
        raise ForgetInProgressError(
            f"deployment {deployment_id} is honoring forget {active}"
        )
    if maintenance == "conversion":
        present = connection.execute(
            _ACTIVE_CONVERSION,
            {
                "deployment_id": deployment_id,
                "conversion_id": authority_id,
                "generation": TEMPORAL_FACT_GENERATION,
            },
        ).scalar_one_or_none()
        if present is None:
            raise TemporalWriteConflict(
                "conversion writer does not own an active campaign"
            )
        return
    generation = connection.execute(
        _FACT_GENERATION, {"deployment_id": deployment_id}
    ).scalar_one_or_none()
    if generation != TEMPORAL_FACT_GENERATION:
        raise TemporalNotReadyError(
            "fact conversion has not established the required generation"
        )


def _load_fact(
    *, connection: Connection, deployment_id: UUID, fact: TemporalFactRef
) -> RowMapping | None:
    """Lock the actual plane row, scoped by deployment and a closed table vocabulary."""
    plane = fact.plane.value
    return (
        connection.execute(
            text(f"""
        SELECT temporal_kind, valid_from, valid_until, valid_from_basis, valid_until_basis,
          occurs_from, occurs_until, occurs_precision, seed_claim_id, ingested_at,
          invalidated_at, temporal_revision, from_operation_id, until_operation_id,
          contradiction_group, subject_entity_id,
          {"predicate" if fact.plane is FactPlane.RELATION else "NULL::text"} AS predicate
        FROM {plane}s WHERE deployment_id = :deployment_id AND {plane}_id = :fact_id
        FOR UPDATE
    """),
            {"deployment_id": deployment_id, "fact_id": fact.fact_id},
        )
        .mappings()
        .one_or_none()
    )


def _state(*, row: RowMapping) -> FactTemporalState:
    """Decode the SQL tuple through the same strict model used by pure decisions."""
    return FactTemporalState.model_validate(
        {
            "kind": row["temporal_kind"],
            "verdict": {
                "start": _utc_timestamp(value=row["valid_from"]),
                "end": _utc_timestamp(value=row["valid_until"]),
                "start_basis": row["valid_from_basis"],
                "end_basis": row["valid_until_basis"],
            },
            "occurrence": {
                "start": _utc_timestamp(value=row["occurs_from"]),
                "end": _utc_timestamp(value=row["occurs_until"]),
                "precision": row["occurs_precision"],
            },
            "seed_claim_id": row["seed_claim_id"],
            "ingested_at": _utc_timestamp(value=row["ingested_at"]),
            "invalidated_at": _utc_timestamp(value=row["invalidated_at"]),
            "revision": row["temporal_revision"],
            "from_operation_id": row["from_operation_id"],
            "until_operation_id": row["until_operation_id"],
            "contradiction_group": row["contradiction_group"],
        }
    )


def _json_scalar(value: object) -> str:
    """Allow only explicit source instants and UUIDs as non-JSON fingerprint scalars."""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise ValueError("temporal fingerprints require timezone-aware instants")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    raise TypeError(f"unsupported fingerprint value {type(value).__name__}")


def _utc_timestamp(*, value: datetime | None) -> datetime | None:
    """Decode timestamptz independently of the connection's session time zone."""
    if value is None:
        return None
    if value.utcoffset() is None:
        raise TemporalWriteConflict("database temporal value must be timezone-aware")
    return value.astimezone(timezone.utc)


_ENSURE_BLOCK = text("""
    INSERT INTO temporal_blocks (deployment_id, block_key)
    VALUES (:deployment_id, :key) ON CONFLICT DO NOTHING
""")
_LOCK_BLOCK = text("""
    SELECT b.revision, b.last_sequence, e.operation_id
    FROM temporal_blocks b LEFT JOIN temporal_operation_blocks e
      ON e.deployment_id = b.deployment_id AND e.block_key = b.block_key
      AND e.sequence = b.last_sequence
    WHERE b.deployment_id = :deployment_id AND b.block_key = :key FOR UPDATE OF b
""")
_ENSURE_SOURCE = text("""
    INSERT INTO temporal_sources (deployment_id, source_kind, source_id, source_key, revision)
    VALUES (:deployment_id, :kind, :source_id, :key, :revision) ON CONFLICT DO NOTHING
""")
_LOCK_SOURCE = text("""
    SELECT source_key, revision FROM temporal_sources
    WHERE deployment_id = :deployment_id AND source_kind = :kind AND source_id = :source_id
    FOR UPDATE
""")
_UPDATE_FACT_SOURCE = text("""
    UPDATE temporal_sources SET revision = :revision, next_boundary_at = :next_boundary
    WHERE deployment_id = :deployment_id AND source_kind = :plane AND source_id = :fact_id
""")
_FACT_GENERATION = text(
    "SELECT generation FROM temporal_fact_generations WHERE deployment_id = :deployment_id"
)
_ACTIVE_CONVERSION = text("""
    SELECT conversion_id FROM temporal_conversion_runs
    WHERE deployment_id = :deployment_id AND conversion_id = :conversion_id
      AND generation = :generation AND state IN ('preparing', 'converting', 'verifying')
""")
_CLAIM_INPUT = text("""
    SELECT claim_id, doc_id, claim_text, asserted_at, claim_valid_from, claim_valid_until,
      claim_valid_precision::text, claim_valid_kind::text, is_current_testimony
    FROM claims WHERE deployment_id = :deployment_id AND claim_id = :claim_id
""")
_INSERT_EFFECT = text("""
    INSERT INTO temporal_operations (
      operation_id, deployment_id, relation_id, observation_id, discrepancy_id,
      operation_kind, result, expected_revision, resulting_revision,
      old_valid_from, old_valid_until, old_from_basis, old_until_basis,
      new_valid_from, new_valid_until, new_from_basis, new_until_basis,
      old_invalidated_at, new_invalidated_at, old_from_operation_id, old_until_operation_id,
      changed_from, changed_until, reverses_operation_id, input_fingerprint,
      identity_generation, policy_generation, reason_code, recorded_at, replay_class
    ) VALUES (
      :operation_id, :deployment_id, :relation_id, :observation_id, :discrepancy_id,
      :operation_kind, :result, :expected_revision, :resulting_revision,
      :old_valid_from, :old_valid_until, :old_from_basis, :old_until_basis,
      :new_valid_from, :new_valid_until, :new_from_basis, :new_until_basis,
      :old_invalidated_at, :new_invalidated_at, :old_from_operation_id, :old_until_operation_id,
      :changed_from, :changed_until, :reverses_operation_id, :input_fingerprint,
      :identity_generation, :policy_generation, :reason_code, :recorded_at, :replay_class
    )
""")
_INSERT_BLOCK_EFFECT = text("""
    INSERT INTO temporal_operation_blocks (
      deployment_id, operation_id, block_key, sequence, writes_block,
      expected_block_revision, resulting_block_revision
    ) VALUES (:deployment_id, :operation_id, :key, :sequence, :writes, :old_revision, :new_revision)
""")
_ADVANCE_BLOCK = text("""
    UPDATE temporal_blocks SET revision = :revision, last_sequence = :sequence
    WHERE deployment_id = :deployment_id AND block_key = :key
""")
_INSERT_PREDECESSOR = text("""
    INSERT INTO temporal_operation_dependencies (
      deployment_id, operation_id, predecessor_operation_id, required_for_semantics
    ) VALUES (:deployment_id, :operation_id, :predecessor, :semantic)
""")
_INSERT_EVIDENCE = text("""
    INSERT INTO temporal_operation_evidence (
      deployment_id, operation_id, claim_id, evidence_role, was_current, evidence_fingerprint
    ) VALUES (:deployment_id, :operation_id, :claim_id, :role, :was_current, :fingerprint)
""")
_INSERT_SUPPORT = text("""
    INSERT INTO temporal_operation_support (
      deployment_id, operation_id, support_state, footprint_complete, expected_block_count,
      expected_claim_count, expected_semantic_dependency_count, support_fingerprint
    ) VALUES (:deployment_id, :operation_id, :state, :footprint_complete, :blocks,
              :claims, :semantic_dependencies, :fingerprint)
""")

_CANONICAL_SUBJECT = text("""
    WITH RECURSIVE up(entity_id, status, merged_into, path) AS (
      SELECT entity_id, status, merged_into, ARRAY[entity_id]
      FROM entities WHERE deployment_id = :deployment_id AND entity_id = :entity_id
      UNION ALL
      SELECT parent.entity_id, parent.status, parent.merged_into, up.path || parent.entity_id
      FROM up JOIN entities parent ON parent.deployment_id = :deployment_id
        AND parent.entity_id = up.merged_into
      WHERE up.status = 'merged' AND NOT parent.entity_id = ANY(up.path)
    ) SELECT entity_id FROM up WHERE status = 'active'
""")

_COMPENSATION_TARGET = text("""
    SELECT o.*, s.support_state, s.footprint_complete
    FROM temporal_operations o JOIN temporal_operation_support s
      USING (deployment_id, operation_id)
    WHERE deployment_id = :deployment_id AND operation_id = :operation_id
""")
