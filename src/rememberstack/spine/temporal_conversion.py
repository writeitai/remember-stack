"""D107/D110 durable in-place fact conversion behind the committed schema fence.

The upgrade orchestrator stops intake and drains legacy work before invoking
this catalog. These methods own bounded database batches, not leases or a new
scheduler. They prepare immutable shadows, apply through the temporal journal,
and verify all recorded state before step D can publish a generation.
"""

from collections.abc import Generator
from collections.abc import Iterator
from contextlib import closing
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import heapq
from itertools import zip_longest
import json
from typing import Literal
from uuid import NAMESPACE_URL
from uuid import UUID
from uuid import uuid5

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping

from rememberstack.core.temporal import canonical_bounds
from rememberstack.core.temporal_conversion import convert_legacy_fact
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.forget import ForgetInProgressError
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlock
from rememberstack.model.temporal_write import TemporalDecision
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalEvidenceRef
from rememberstack.model.temporal_write import TemporalFactRef
from rememberstack.model.temporal_write import TemporalOperationKind
from rememberstack.spine.admission import active_forget_id_on
from rememberstack.spine.temporal_journal import _canonical_subject
from rememberstack.spine.temporal_journal import _state
from rememberstack.spine.temporal_journal import _utc_timestamp
from rememberstack.spine.temporal_journal import load_temporal_evidence
from rememberstack.spine.temporal_journal import temporal_block_key
from rememberstack.spine.temporal_journal import TEMPORAL_FACT_GENERATION
from rememberstack.spine.temporal_journal import temporal_fingerprint
from rememberstack.spine.temporal_journal import temporal_write
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from rememberstack.spine.temporal_schema import require_temporal_constraints_on
from rememberstack.spine.temporal_schema import TEMPORAL_FINAL_REVISION

CONVERSION_SCHEMA_REVISION = "p9_29_0050"
CONVERSION_POLICY = "recorded-legacy-authority-2"
CONVERSION_POLICY_FINGERPRINT = temporal_fingerprint(
    value={
        "fact_generation": TEMPORAL_FACT_GENERATION,
        "policy": CONVERSION_POLICY,
        "canonical_bounds": "D106-UTC",
        "legacy_schema": "p9_27_0048",
    }
)


@dataclass(frozen=True)
class TemporalConversionProgress:
    """Durable campaign progress; complete means data verified, not serving ready."""

    conversion_id: UUID
    phase: str
    expected: int
    prepared: int
    applied: int
    verified: int


@dataclass(frozen=True)
class _History:
    """Bounded selected legacy verdicts; ambiguous records never establish authority."""

    creator: RowMapping | None
    cap: RowMapping | None
    withdrawal: RowMapping | None


def initialize_empty_temporal_generation_on(
    *, connection: Connection, deployment_id: UUID
) -> None:
    """Certify a newly bootstrapped empty deployment inside its creation transaction.

    At C the ordinary converter records the explicit empty campaign and D later
    certifies it. After D, a new deployment has no legacy data to convert; verify
    the finalized constraints and record its zero-row campaign atomically.
    Existing deployments never use this creation-only path on bootstrap retry.
    """
    revision = connection.execute(
        text("SELECT version_num FROM alembic_version")
    ).scalar_one()
    if revision == CONVERSION_SCHEMA_REVISION:
        return
    if revision != TEMPORAL_FINAL_REVISION:
        raise TemporalWriteConflict(
            "deployment bootstrap requires the current temporal schema"
        )
    require_temporal_constraints_on(connection=connection)
    connection.execute(
        text(
            "SELECT deployment_id FROM deployments WHERE deployment_id = :dep FOR UPDATE"
        ),
        {"dep": deployment_id},
    ).scalar_one()
    if connection.execute(
        text("""
        SELECT EXISTS (SELECT 1 FROM relations WHERE deployment_id = :dep)
            OR EXISTS (SELECT 1 FROM observations WHERE deployment_id = :dep)
            OR EXISTS (SELECT 1 FROM temporal_conversion_runs WHERE deployment_id = :dep)
        """),
        {"dep": deployment_id},
    ).scalar_one():
        raise TemporalWriteConflict(
            "empty bootstrap cannot certify existing fact or conversion state"
        )
    conversion_id = uuid5(
        NAMESPACE_URL,
        f"rememberstack:temporal-conversion:{deployment_id}:{TEMPORAL_FACT_GENERATION}",
    )
    parameters = {
        "dep": deployment_id,
        "id": conversion_id,
        "generation": TEMPORAL_FACT_GENERATION,
        "policy": CONVERSION_POLICY_FINGERPRINT,
    }
    connection.execute(
        text("""
        INSERT INTO temporal_conversion_runs (conversion_id, deployment_id, generation,
            input_generation, policy_fingerprint, state, expected_relations, expected_observations,
            captured_at, completed_at)
        VALUES (:id, :dep, :generation, 'empty-current', :policy, 'complete', 0, 0,
                transaction_timestamp(), transaction_timestamp())
    """),
        parameters,
    )
    connection.execute(
        text("""
        INSERT INTO temporal_fact_generations (deployment_id, generation, conversion_id, verified_at)
        VALUES (:dep, :generation, :id, transaction_timestamp())
    """),
        parameters,
    )


class TemporalFactConverter:
    """Prepare, resume and verify one deployment's recorded fact conversion."""

    def __init__(self, *, engine: Engine, batch_size: int = 64) -> None:
        """Bind bounded work to the existing engine without creating a worker executor."""
        if not 1 <= batch_size <= 1000:
            raise ValueError("conversion batch_size must be between 1 and 1000")
        self._engine = engine
        self._batch_size = batch_size

    def begin(self, *, deployment_id: UUID) -> TemporalConversionProgress:
        """Capture a campaign only at committed C, with no unfinished legacy work."""
        with self._engine.begin() as connection:
            self._lock_fence(
                connection=connection, deployment_id=deployment_id, exclusive=True
            )
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            if revision != CONVERSION_SCHEMA_REVISION:
                raise TemporalWriteConflict(
                    "conversion requires the committed step C revision"
                )
            if (
                connection.execute(
                    text("""
                SELECT 1 FROM processing_state WHERE deployment_id = :dep
                  AND status IN ('pending', 'running', 'failed') LIMIT 1
            """),
                    {"dep": deployment_id},
                ).scalar_one_or_none()
                is not None
            ):
                raise TemporalWriteConflict(
                    "legacy work must drain before conversion capture"
                )
            existing = (
                connection.execute(_CAMPAIGN, {"dep": deployment_id})
                .mappings()
                .one_or_none()
            )
            if existing is None:
                counts = self._fact_counts(
                    connection=connection, deployment_id=deployment_id
                )
                conversion_id = uuid5(
                    NAMESPACE_URL,
                    f"rememberstack:temporal-conversion:{deployment_id}:{TEMPORAL_FACT_GENERATION}",
                )
                connection.execute(
                    text("""
                    INSERT INTO temporal_conversion_runs (conversion_id, deployment_id, generation,
                        input_generation, policy_fingerprint, state, expected_relations, expected_observations,
                        captured_at) VALUES (:id, :dep, :generation, 'p9_27_0048', :policy, 'preparing', :relations,
                                             :observations, clock_timestamp())
                """),
                    {
                        "id": conversion_id,
                        "dep": deployment_id,
                        "generation": TEMPORAL_FACT_GENERATION,
                        "policy": CONVERSION_POLICY_FINGERPRINT,
                        **counts,
                    },
                )
            else:
                self._check_policy(row=existing)
        return self.progress(deployment_id=deployment_id)

    def progress(self, *, deployment_id: UUID) -> TemporalConversionProgress:
        """Inspect persisted counts without claiming schema finalization or cache readiness."""
        with self._engine.connect() as connection:
            row = connection.execute(_PROGRESS, {"dep": deployment_id}).mappings().one()
            return TemporalConversionProgress(
                conversion_id=row["conversion_id"],
                phase=row["state"],
                expected=row["expected_relations"] + row["expected_observations"],
                prepared=row["prepared"],
                applied=row["applied"],
                verified=row["verified"],
            )

    def prepare_batch(self, *, deployment_id: UUID) -> TemporalConversionProgress:
        """Persist immutable converted tuples before any fact tuple is replaced."""
        with self._campaign_connection(
            deployment_id=deployment_id, phase="preparing"
        ) as (connection, campaign):
            pending = self._pending_facts(
                connection=connection,
                deployment_id=deployment_id,
                conversion_id=campaign["conversion_id"],
                state=None,
            )
        for fact in pending:
            with self._campaign_connection(
                deployment_id=deployment_id, phase="preparing"
            ) as (connection, campaign):
                blocks, facts = self._footprint(
                    connection=connection, deployment_id=deployment_id, fact=fact
                )
                with temporal_write(
                    connection=connection,
                    deployment_id=deployment_id,
                    blocks=blocks,
                    facts=facts,
                    maintenance="conversion",
                    authority_id=campaign["conversion_id"],
                ):
                    effect = self._prepare_effect(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=fact,
                        campaign=campaign,
                    )
                    connection.execute(
                        _INSERT_SHADOW,
                        {
                            "dep": deployment_id,
                            "conversion": campaign["conversion_id"],
                            "plane": fact.plane.value,
                            "fact": fact.fact_id,
                            "revision": effect.before.revision,
                            "fingerprint": effect.input_fingerprint,
                            "state": effect.after.model_dump_json(),
                        },
                    )
                    shadow = self._shadow(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=fact,
                        conversion_id=campaign["conversion_id"],
                    )
                    if (
                        shadow["input_fingerprint"] != effect.input_fingerprint
                        or _decode_state(value=shadow["converted_state"])
                        != effect.after
                    ):
                        raise TemporalWriteConflict(
                            "conversion shadow cannot be replaced by different inputs"
                        )
        if not pending:
            with self._campaign_connection(
                deployment_id=deployment_id, phase="preparing"
            ) as (connection, campaign):
                self._require_inventory(
                    connection=connection,
                    deployment_id=deployment_id,
                    campaign=campaign,
                    required_state="prepared",
                )
                connection.execute(
                    _ADVANCE_PHASE,
                    {"dep": deployment_id, "old": "preparing", "new": "converting"},
                )
        return self.progress(deployment_id=deployment_id)

    def apply_batch(self, *, deployment_id: UUID) -> TemporalConversionProgress:
        """Revalidate original inputs and atomically apply each prepared migration receipt."""
        with self._campaign_connection(
            deployment_id=deployment_id, phase="converting"
        ) as (connection, campaign):
            pending = self._pending_facts(
                connection=connection,
                deployment_id=deployment_id,
                conversion_id=campaign["conversion_id"],
                state="prepared",
            )
        for fact in pending:
            with self._campaign_connection(
                deployment_id=deployment_id, phase="converting"
            ) as (connection, campaign):
                blocks, facts = self._footprint(
                    connection=connection, deployment_id=deployment_id, fact=fact
                )
                with temporal_write(
                    connection=connection,
                    deployment_id=deployment_id,
                    blocks=blocks,
                    facts=facts,
                    maintenance="conversion",
                    authority_id=campaign["conversion_id"],
                ) as session:
                    shadow = self._shadow(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=fact,
                        conversion_id=campaign["conversion_id"],
                    )
                    if shadow["state"] != "prepared":
                        continue
                    effect = self._prepare_effect(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=fact,
                        campaign=campaign,
                    )
                    if effect.input_fingerprint != shadow[
                        "input_fingerprint"
                    ] or effect.after != _decode_state(value=shadow["converted_state"]):
                        raise TemporalWriteConflict(
                            "prepared conversion inputs changed; serving stays fenced"
                        )
                    target_block = self._block(
                        connection=connection, deployment_id=deployment_id, fact=fact
                    )
                    session.apply(
                        effect=effect,
                        written_blocks=frozenset(
                            (
                                temporal_block_key(
                                    deployment_id=deployment_id, block=target_block
                                ),
                            )
                        ),
                        evidence_stream=self._evidence_refs(
                            connection=connection,
                            deployment_id=deployment_id,
                            fact=fact,
                        ),
                    )
                    connection.execute(
                        text("""
                        UPDATE temporal_conversion_rows SET state = 'applied', operation_id = :operation
                        WHERE deployment_id = :dep AND conversion_id = :conversion AND fact_kind = :plane
                          AND fact_id = :fact AND state = 'prepared'
                    """),
                        {
                            "dep": deployment_id,
                            "conversion": campaign["conversion_id"],
                            "plane": fact.plane.value,
                            "fact": fact.fact_id,
                            "operation": effect.operation_id,
                        },
                    )
        if not pending:
            with self._campaign_connection(
                deployment_id=deployment_id, phase="converting"
            ) as (connection, campaign):
                self._require_inventory(
                    connection=connection,
                    deployment_id=deployment_id,
                    campaign=campaign,
                    required_state="applied",
                )
                connection.execute(
                    _ADVANCE_PHASE,
                    {"dep": deployment_id, "old": "converting", "new": "verifying"},
                )
        return self.progress(deployment_id=deployment_id)

    def verify_batch(self, *, deployment_id: UUID) -> TemporalConversionProgress:
        """Verify exact fact/shadow/receipt/evidence agreement before campaign completion."""
        with self._campaign_connection(
            deployment_id=deployment_id, phase="verifying"
        ) as (connection, campaign):
            pending = self._pending_facts(
                connection=connection,
                deployment_id=deployment_id,
                conversion_id=campaign["conversion_id"],
                state="applied",
            )
        for fact in pending:
            with self._campaign_connection(
                deployment_id=deployment_id, phase="verifying"
            ) as (connection, campaign):
                blocks, facts = self._footprint(
                    connection=connection, deployment_id=deployment_id, fact=fact
                )
                with temporal_write(
                    connection=connection,
                    deployment_id=deployment_id,
                    blocks=blocks,
                    facts=facts,
                    maintenance="conversion",
                    authority_id=campaign["conversion_id"],
                ) as session:
                    shadow = self._shadow(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=fact,
                        conversion_id=campaign["conversion_id"],
                    )
                    if shadow["state"] == "verified":
                        continue
                    expected = _decode_state(value=shadow["converted_state"])
                    recomputed = self._prepare_effect(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=fact,
                        campaign=campaign,
                    )
                    if (
                        recomputed.input_fingerprint != shadow["input_fingerprint"]
                        or recomputed.after != expected
                    ):
                        raise TemporalWriteConflict(
                            "verified conversion no longer matches its original policy inputs"
                        )
                    if session.state(fact=fact) != expected:
                        raise TemporalWriteConflict(
                            "converted fact differs from its immutable shadow"
                        )
                    receipt = (
                        connection.execute(
                            _VERIFY_RECEIPT,
                            {"dep": deployment_id, "operation": shadow["operation_id"]},
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if (
                        receipt is None
                        or receipt["operation_kind"] != "migration"
                        or receipt["result"] != "applied"
                        or (
                            receipt[f"{fact.plane.value}_id"] != fact.fact_id
                            or receipt["resulting_revision"] != expected.revision
                            or receipt["input_fingerprint"]
                            != shadow["input_fingerprint"]
                            or receipt["policy_generation"] != CONVERSION_POLICY
                            or receipt["expected_block_count"]
                            != receipt["actual_blocks"]
                            or receipt["expected_claim_count"]
                            != receipt["actual_claims"]
                            or receipt["expected_semantic_dependency_count"]
                            != receipt["actual_semantic"]
                        )
                    ):
                        raise TemporalWriteConflict(
                            "migration effect or support receipt is incomplete"
                        )
                    narrative = connection.execute(
                        text(f"""
                        SELECT features -> 'temporal_effect' -> 'after' FROM {fact.plane.value}_adjudications
                        WHERE deployment_id = :dep AND temporal_operation_id = :operation
                          AND outcome = 'migrate' AND method = 'migration'
                    """),
                        {"dep": deployment_id, "operation": shadow["operation_id"]},
                    ).scalar_one_or_none()
                    if narrative is None or _decode_state(value=narrative) != expected:
                        raise TemporalWriteConflict(
                            "migration narrative differs from the converted tuple"
                        )
                    actual = connection.execute(
                        text("""
                        SELECT claim_id, evidence_role AS role, was_current, evidence_fingerprint AS fingerprint
                        FROM temporal_operation_evidence WHERE deployment_id = :dep AND operation_id = :operation
                        ORDER BY claim_id, evidence_role COLLATE "C"
                    """).execution_options(stream_results=True),
                        {"dep": deployment_id, "operation": shadow["operation_id"]},
                    ).yield_per(512)
                    expected_evidence = self._evidence_refs(
                        connection=connection, deployment_id=deployment_id, fact=fact
                    )
                    try:
                        for source, recorded in zip_longest(
                            expected_evidence, actual.mappings()
                        ):
                            if (
                                source is None
                                or recorded is None
                                or source
                                != TemporalEvidenceRef.model_validate(dict(recorded))
                            ):
                                raise TemporalWriteConflict(
                                    "migration support differs from retained attached testimony"
                                )
                    finally:
                        actual.close()
                        expected_evidence.close()
                    connection.execute(
                        text("""
                        UPDATE temporal_conversion_rows SET state = 'verified' WHERE deployment_id = :dep
                          AND conversion_id = :conversion AND fact_kind = :plane AND fact_id = :fact AND state = 'applied'
                    """),
                        {
                            "dep": deployment_id,
                            "conversion": campaign["conversion_id"],
                            "plane": fact.plane.value,
                            "fact": fact.fact_id,
                        },
                    )
        if not pending:
            with self._campaign_connection(
                deployment_id=deployment_id, phase="verifying"
            ) as (connection, campaign):
                self._require_inventory(
                    connection=connection,
                    deployment_id=deployment_id,
                    campaign=campaign,
                    required_state="verified",
                )
                connection.execute(
                    text("""
                    UPDATE temporal_conversion_runs SET state = 'complete', completed_at = clock_timestamp()
                    WHERE deployment_id = :dep AND conversion_id = :conversion AND state = 'verifying'
                """),
                    {"dep": deployment_id, "conversion": campaign["conversion_id"]},
                )
        return self.progress(deployment_id=deployment_id)

    @contextmanager
    def _campaign_connection(
        self, *, deployment_id: UUID, phase: str
    ) -> Iterator[tuple[Connection, RowMapping]]:
        """Hold the existing deployment and identity fences before discovering lock keys."""
        with self._engine.begin() as connection:
            self._lock_fence(
                connection=connection, deployment_id=deployment_id, exclusive=False
            )
            campaign = (
                connection.execute(_CAMPAIGN, {"dep": deployment_id}).mappings().one()
            )
            self._check_policy(row=campaign)
            if campaign["state"] != phase:
                raise TemporalWriteConflict(
                    f"conversion phase is {campaign['state']}, expected {phase}"
                )
            yield connection, campaign

    def _lock_fence(
        self, *, connection: Connection, deployment_id: UUID, exclusive: bool
    ) -> None:
        """Share the established forget fence; never overlap accepted erasure with conversion."""
        lock = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        connection.execute(
            text(f"SELECT {lock}(hashtextextended(:key, 0))"),
            {"key": f"hard-forget:{deployment_id}"},
        )
        if (
            active_forget_id_on(connection=connection, deployment_id=deployment_id)
            is not None
        ):
            raise ForgetInProgressError("conversion cannot overlap an active forget")
        connection.execute(
            text("SELECT pg_advisory_xact_lock_shared(hashtextextended(:key, 0))"),
            {"key": f"{deployment_id}:identity-epoch"},
        )

    def _check_policy(self, *, row: RowMapping) -> None:
        """A resumed campaign consumes the same generation and pure conversion policy."""
        if (
            row["generation"] != TEMPORAL_FACT_GENERATION
            or row["policy_fingerprint"] != CONVERSION_POLICY_FINGERPRINT
        ):
            raise TemporalWriteConflict(
                "conversion policy changed during a recorded campaign"
            )

    def _pending_facts(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        conversion_id: UUID,
        state: str | None,
    ) -> tuple[TemporalFactRef, ...]:
        """Select at most one batch using the indexed conversion frontier or missing shadows."""
        if state is not None:
            rows = connection.execute(
                text("""
                SELECT fact_kind, fact_id FROM temporal_conversion_rows
                WHERE deployment_id = :dep AND conversion_id = :conversion AND state = :state
                ORDER BY fact_kind, fact_id LIMIT :limit
            """),
                {
                    "dep": deployment_id,
                    "conversion": conversion_id,
                    "state": state,
                    "limit": self._batch_size,
                },
            ).mappings()
            return tuple(
                TemporalFactRef(plane=row["fact_kind"], fact_id=row["fact_id"])
                for row in rows
            )
        found: list[TemporalFactRef] = []
        for plane in FactPlane:
            rows = connection.execute(
                text(f"""
                SELECT f.{plane.value}_id FROM {plane.value}s f WHERE f.deployment_id = :dep
                  AND NOT EXISTS (SELECT 1 FROM temporal_conversion_rows c WHERE c.deployment_id = f.deployment_id
                    AND c.conversion_id = :conversion AND c.fact_kind = :plane AND c.fact_id = f.{plane.value}_id)
                ORDER BY f.{plane.value}_id LIMIT :limit
            """),
                {
                    "dep": deployment_id,
                    "conversion": conversion_id,
                    "plane": plane.value,
                    "limit": self._batch_size - len(found),
                },
            ).scalars()
            found.extend(
                TemporalFactRef(plane=plane, fact_id=fact_id) for fact_id in rows
            )
        return tuple(found)

    def _block(
        self, *, connection: Connection, deployment_id: UUID, fact: TemporalFactRef
    ) -> TemporalBlock:
        """Resolve the exact canonical identity coordinate under the identity epoch."""
        plane = fact.plane.value
        row = (
            connection.execute(
                text(f"""
            SELECT subject_entity_id, {"predicate" if fact.plane is FactPlane.RELATION else "NULL::text"} AS predicate
            FROM {plane}s WHERE deployment_id = :dep AND {plane}_id = :fact
        """),
                {"dep": deployment_id, "fact": fact.fact_id},
            )
            .mappings()
            .one()
        )
        return TemporalBlock(
            plane=fact.plane,
            subject_entity_id=_canonical_subject(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=row["subject_entity_id"],
                allow_retired=True,
            ),
            predicate=row["predicate"],
        )

    def _footprint(
        self, *, connection: Connection, deployment_id: UUID, fact: TemporalFactRef
    ) -> tuple[tuple[TemporalBlock, ...], tuple[TemporalFactRef, ...]]:
        """Include a recorded successor's read block before acquiring any fact/source lock."""
        facts = [fact]
        history = self._history(
            connection=connection, deployment_id=deployment_id, fact=fact
        )
        if history.cap is not None and history.cap["related_id"] is not None:
            successor = TemporalFactRef(
                plane=fact.plane, fact_id=history.cap["related_id"]
            )
            if (
                connection.execute(
                    text(
                        f"SELECT 1 FROM {fact.plane.value}s WHERE deployment_id = :dep AND {fact.plane.value}_id = :id"
                    ),
                    {"dep": deployment_id, "id": successor.fact_id},
                ).scalar_one_or_none()
                is not None
            ):
                facts.append(successor)
        return tuple(
            self._block(connection=connection, deployment_id=deployment_id, fact=item)
            for item in facts
        ), tuple(facts)

    def _history(
        self, *, connection: Connection, deployment_id: UUID, fact: TemporalFactRef
    ) -> _History:
        """Select unique recorded authorities; tied incompatible records remain unrecoverable."""
        plane = fact.plane.value

        def select(*, outcomes: tuple[str, ...], ascending: bool) -> RowMapping | None:
            """Read only the two extremal active records needed to detect an ambiguous tie."""
            extremum = "MIN" if ascending else "MAX"
            rows = (
                connection.execute(
                    text(f"""
                WITH eligible AS MATERIALIZED (
                    SELECT adjudication_id, outcome::text, triggering_claim_id,
                           related_{plane}_id AS related_id, decided_at
                    FROM {plane}_adjudications WHERE deployment_id = :dep AND {plane}_id = :fact
                      AND (:creator OR superseded_by IS NULL) AND temporal_operation_id IS NULL
                      AND outcome::text = ANY(:outcomes)
                )
                SELECT DISTINCT ON (outcome, triggering_claim_id, related_id) * FROM eligible
                WHERE decided_at = (SELECT {extremum}(decided_at) FROM eligible)
                ORDER BY outcome, triggering_claim_id, related_id, adjudication_id LIMIT 2
            """),
                    {
                        "dep": deployment_id,
                        "fact": fact.fact_id,
                        "outcomes": list(outcomes),
                        "creator": ascending,
                    },
                )
                .mappings()
                .all()
            )
            if not rows:
                return None
            if (
                len(rows) > 1
                and rows[0]["decided_at"] == rows[1]["decided_at"]
                and (
                    rows[0]["triggering_claim_id"],
                    rows[0]["related_id"],
                    rows[0]["outcome"],
                )
                != (
                    rows[1]["triggering_claim_id"],
                    rows[1]["related_id"],
                    rows[1]["outcome"],
                )
            ):
                return None
            return rows[0]

        return _History(
            creator=select(outcomes=("add",), ascending=True),
            cap=select(
                outcomes=("supersede", "retracted_source_removal")
                if fact.plane is FactPlane.RELATION
                else ("supersede",),
                ascending=False,
            ),
            withdrawal=select(outcomes=("retracted_source_removal",), ascending=False),
        )

    def _seed(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        fact: TemporalFactRef,
        history: _History,
    ) -> ClaimTemporalWindow | None:
        """Recover only a retained attached creator named by an actual add adjudication."""
        if history.creator is None or history.creator["triggering_claim_id"] is None:
            return None
        row = (
            connection.execute(
                text(f"""
            SELECT c.claim_id, c.claim_valid_kind, c.claim_valid_from, c.claim_valid_until, c.claim_valid_precision
            FROM claims c JOIN {fact.plane.value}_evidence e USING (deployment_id, claim_id)
            WHERE c.deployment_id = :dep AND e.{fact.plane.value}_id = :fact AND c.claim_id = :claim
        """),
                {
                    "dep": deployment_id,
                    "fact": fact.fact_id,
                    "claim": history.creator["triggering_claim_id"],
                },
            )
            .mappings()
            .one_or_none()
        )
        return _claim_window(row=row) if row is not None else None

    def _identity_snapshot(
        self, *, connection: Connection, deployment_id: UUID, fact: TemporalFactRef
    ) -> dict[str, object]:
        """Fingerprint stable fact identity and canonical redirects, not only its temporal tuple."""
        plane = fact.plane.value
        fields = (
            "predicate, object_entity_id, NULL::text AS statement"
            if fact.plane is FactPlane.RELATION
            else "NULL::text AS predicate, NULL::uuid AS object_entity_id, statement"
        )
        row = (
            connection.execute(
                text(f"""
            SELECT subject_entity_id, normalizer_version, {fields} FROM {plane}s
            WHERE deployment_id = :dep AND {plane}_id = :fact
        """),
                {"dep": deployment_id, "fact": fact.fact_id},
            )
            .mappings()
            .one()
        )
        return {
            "fact": fact.model_dump(mode="json"),
            **dict(row),
            "canonical_subject": _canonical_subject(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=row["subject_entity_id"],
                allow_retired=True,
            ),
            "canonical_object": _canonical_subject(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=row["object_entity_id"],
                allow_retired=True,
            )
            if row["object_entity_id"] is not None
            else None,
        }

    def _original(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        fact: TemporalFactRef,
        conversion_id: UUID,
    ) -> FactTemporalState:
        """Read the unchanged legacy tuple or this campaign's recorded before image."""
        plane = fact.plane.value
        row = (
            connection.execute(
                text(
                    f"SELECT * FROM {plane}s WHERE deployment_id = :dep AND {plane}_id = :fact"
                ),
                {"dep": deployment_id, "fact": fact.fact_id},
            )
            .mappings()
            .one()
        )
        if row["temporal_revision"] == 0:
            return _state(row=row)
        original = connection.execute(
            text(f"""
            SELECT a.features -> 'temporal_effect' -> 'before' FROM temporal_conversion_rows c
            JOIN {plane}_adjudications a ON a.deployment_id = c.deployment_id AND a.temporal_operation_id = c.operation_id
            WHERE c.deployment_id = :dep AND c.conversion_id = :conversion AND c.fact_kind = :plane AND c.fact_id = :fact
        """),
            {
                "dep": deployment_id,
                "conversion": conversion_id,
                "plane": plane,
                "fact": fact.fact_id,
            },
        ).scalar_one_or_none()
        if original is None:
            raise TemporalWriteConflict(
                "changed fact lacks this campaign's original receipt"
            )
        return _decode_state(value=original)

    def _prepare_effect(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        fact: TemporalFactRef,
        campaign: RowMapping,
    ) -> TemporalEffect:
        """Compute exact policy inputs and fingerprints with constant evidence working storage."""
        before = self._original(
            connection=connection,
            deployment_id=deployment_id,
            fact=fact,
            conversion_id=campaign["conversion_id"],
        )
        history = self._history(
            connection=connection, deployment_id=deployment_id, fact=fact
        )
        seed = self._seed(
            connection=connection,
            deployment_id=deployment_id,
            fact=fact,
            history=history,
        )
        successor_start = None
        successor_original = None
        successor_seed = None
        successor_history = None
        successor_id = None
        successor_input = None
        successor_identity = None
        cause: Literal["supersession", "source_removal", "unknown"] = "unknown"
        if history.cap is not None:
            cause = (
                "source_removal"
                if history.cap["outcome"] == "retracted_source_removal"
                else "supersession"
            )
            if cause == "supersession" and history.cap["related_id"] is not None:
                successor = TemporalFactRef(
                    plane=fact.plane, fact_id=history.cap["related_id"]
                )
                if (
                    successor
                    in self._footprint(
                        connection=connection, deployment_id=deployment_id, fact=fact
                    )[1]
                ):
                    successor_id = successor.fact_id
                    successor_identity = self._identity_snapshot(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=successor,
                    )
                    successor_original = self._original(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=successor,
                        conversion_id=campaign["conversion_id"],
                    )
                    successor_history = self._history(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=successor,
                    )
                    successor_seed = self._seed(
                        connection=connection,
                        deployment_id=deployment_id,
                        fact=successor,
                        history=successor_history,
                    )
                    if successor_seed is not None:
                        successor_input = load_temporal_evidence(
                            connection=connection,
                            deployment_id=deployment_id,
                            claim_id=successor_seed.claim_id,
                            role="support",
                        )
                        successor_start = canonical_bounds(
                            valid_from=successor_seed.valid_from,
                            valid_until=successor_seed.valid_until,
                            precision=successor_seed.precision,
                        ).start
        operation_id = uuid5(
            campaign["conversion_id"], f"migration:{fact.plane.value}:{fact.fact_id}"
        )
        evidence_digest = hashlib.sha256()

        def evidence_windows() -> Generator[ClaimTemporalWindow, None, None]:
            """Hash all consumed claim versions while reducing their occurrence bounds."""
            with closing(
                self._evidence_rows(
                    connection=connection, deployment_id=deployment_id, fact=fact
                )
            ) as rows:
                for row in rows:
                    evidence_digest.update(
                        temporal_fingerprint(value=dict(row)).encode("ascii")
                    )
                    yield _claim_window(row=row)

        with closing(evidence_windows()) as windows:
            decision = convert_legacy_fact(
                legacy=before,
                evidence=windows,
                recorded_seed=seed,
                legacy_cap_cause=cause,
                successor_world_start=successor_start,
                recorded_withdrawal_at=_utc_timestamp(
                    value=history.withdrawal["decided_at"]
                )
                if history.withdrawal is not None
                else None,
                operation_id=operation_id,
            )
        fingerprint = temporal_fingerprint(
            value={
                "identity": self._identity_snapshot(
                    connection=connection, deployment_id=deployment_id, fact=fact
                ),
                "original": before.model_dump(mode="json"),
                "seed": seed.model_dump(mode="json") if seed else None,
                "history": [
                    dict(item) if item is not None else None
                    for item in (history.creator, history.cap, history.withdrawal)
                ],
                "successor_identity": successor_identity,
                "successor_original": successor_original.model_dump(mode="json")
                if successor_original
                else None,
                "successor_seed": successor_seed.model_dump(mode="json")
                if successor_seed
                else None,
                "successor_creator": dict(successor_history.creator)
                if successor_history is not None
                and successor_history.creator is not None
                else None,
                "successor_input": successor_input.model_dump(mode="json")
                if successor_input
                else None,
                "evidence_digest": evidence_digest.hexdigest(),
                "policy": CONVERSION_POLICY_FINGERPRINT,
            }
        )
        recorded_at = _utc_timestamp(value=campaign["captured_at"])
        assert recorded_at is not None
        return TemporalEffect(
            operation_id=operation_id,
            fact=fact,
            kind=TemporalOperationKind.MIGRATION,
            result=TemporalResult.APPLIED,
            before=before,
            after=decision.state,
            decision=TemporalDecision(
                adjudication_id=uuid5(operation_id, "narrative"),
                outcome="migrate",
                method="migration",
                triggering_claim_id=seed.claim_id if seed else None,
                related_fact_id=successor_id,
                features={
                    "conversion_id": str(campaign["conversion_id"]),
                    "diagnostics": list(decision.diagnostics),
                    "legacy_cap_cause": cause,
                    "legacy_adjudication_ids": [
                        str(item["adjudication_id"])
                        for item in (
                            history.creator,
                            history.cap,
                            history.withdrawal,
                            successor_history.creator if successor_history else None,
                        )
                        if item is not None
                    ],
                    "legacy_successor_seed_claim_id": str(successor_seed.claim_id)
                    if successor_seed
                    else None,
                },
            ),
            evidence=(),
            input_fingerprint=fingerprint,
            identity_generation="legacy-fenced",
            policy_generation=CONVERSION_POLICY,
            reason="legacy_conversion",
            recorded_at=recorded_at,
            support_state="unproven",
            footprint_complete=True,
        )

    def _evidence_rows(
        self, *, connection: Connection, deployment_id: UUID, fact: TemporalFactRef
    ) -> Generator[RowMapping, None, None]:
        """Stream every retained linked claim, with stance and mutable testimony currency."""
        result = connection.execute(
            text(f"""
            SELECT c.claim_id, c.doc_id, c.claim_text, c.asserted_at, c.claim_valid_from, c.claim_valid_until,
                c.claim_valid_precision::text, c.claim_valid_kind::text, c.is_current_testimony, e.stance::text
            FROM {fact.plane.value}_evidence e JOIN claims c USING (deployment_id, claim_id)
            WHERE e.deployment_id = :dep AND e.{fact.plane.value}_id = :fact ORDER BY c.claim_id
        """).execution_options(stream_results=True),
            {"dep": deployment_id, "fact": fact.fact_id},
        ).yield_per(512)
        try:
            yield from result.mappings()
        finally:
            result.close()

    def _evidence_refs(
        self, *, connection: Connection, deployment_id: UUID, fact: TemporalFactRef
    ) -> Generator[TemporalEvidenceRef, None, None]:
        """Include successor testimony actually consumed by cap recovery in the operation proof."""
        extra: tuple[TemporalEvidenceRef, ...] = ()
        history = self._history(
            connection=connection, deployment_id=deployment_id, fact=fact
        )
        if (
            history.cap is not None
            and history.cap["outcome"] == "supersede"
            and history.cap["related_id"] is not None
        ):
            successor = TemporalFactRef(
                plane=fact.plane, fact_id=history.cap["related_id"]
            )
            if (
                successor
                in self._footprint(
                    connection=connection, deployment_id=deployment_id, fact=fact
                )[1]
            ):
                successor_history = self._history(
                    connection=connection, deployment_id=deployment_id, fact=successor
                )
                seed = self._seed(
                    connection=connection,
                    deployment_id=deployment_id,
                    fact=successor,
                    history=successor_history,
                )
                if seed is not None:
                    witness = load_temporal_evidence(
                        connection=connection,
                        deployment_id=deployment_id,
                        claim_id=seed.claim_id,
                        role="support",
                    )
                    extra = (
                        witness
                        if witness.was_current
                        else witness.model_copy(update={"role": "historical"}),
                    )
        with closing(
            self._attached_evidence_refs(
                connection=connection, deployment_id=deployment_id, fact=fact
            )
        ) as attached:
            previous: TemporalEvidenceRef | None = None
            for witness in heapq.merge(
                attached, extra, key=lambda item: (item.claim_id, item.role)
            ):
                if previous is not None and (previous.claim_id, previous.role) == (
                    witness.claim_id,
                    witness.role,
                ):
                    if previous != witness:
                        raise TemporalWriteConflict(
                            "consumed successor testimony changed while recording support"
                        )
                    continue
                previous = witness
                yield witness

    def _attached_evidence_refs(
        self, *, connection: Connection, deployment_id: UUID, fact: TemporalFactRef
    ) -> Generator[TemporalEvidenceRef, None, None]:
        """Preserve consumed stance/currency without retaining the full evidence collection."""
        with closing(
            self._evidence_rows(
                connection=connection, deployment_id=deployment_id, fact=fact
            )
        ) as rows:
            for row in rows:
                fields = dict(row)
                stance = fields.pop("stance")
                role = (
                    "contrary"
                    if stance == "contradicts"
                    else ("support" if row["is_current_testimony"] else "historical")
                )
                yield TemporalEvidenceRef(
                    claim_id=row["claim_id"],
                    role=role,
                    was_current=row["is_current_testimony"],
                    fingerprint=temporal_fingerprint(value=fields),
                )

    def _shadow(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        fact: TemporalFactRef,
        conversion_id: UUID,
    ) -> RowMapping:
        """Read the one immutable campaign shadow after its fact/block locks are held."""
        return (
            connection.execute(
                text("""
            SELECT * FROM temporal_conversion_rows WHERE deployment_id = :dep AND conversion_id = :conversion
              AND fact_kind = :plane AND fact_id = :fact FOR UPDATE
        """),
                {
                    "dep": deployment_id,
                    "conversion": conversion_id,
                    "plane": fact.plane.value,
                    "fact": fact.fact_id,
                },
            )
            .mappings()
            .one()
        )

    def _fact_counts(
        self, *, connection: Connection, deployment_id: UUID
    ) -> dict[str, int]:
        """Count current identities without excluding withdrawn or unsupported history."""
        return {
            f"{plane.value}s": connection.execute(
                text(f"SELECT count(*) FROM {plane.value}s WHERE deployment_id = :dep"),
                {"dep": deployment_id},
            ).scalar_one()
            for plane in FactPlane
        }

    def _require_inventory(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        campaign: RowMapping,
        required_state: str,
    ) -> None:
        """Require exact fact identities, counts and phase; no empty or orphan shadow can hide drift."""
        counts = self._fact_counts(connection=connection, deployment_id=deployment_id)
        for plane in FactPlane:
            expected = campaign[f"expected_{plane.value}s"]
            if counts[f"{plane.value}s"] != expected:
                raise TemporalWriteConflict(
                    "fact identity count changed during conversion"
                )
            row = connection.execute(
                text(f"""
                SELECT count(*) AS total, count(*) FILTER (WHERE c.state = :state) AS ready,
                       count(f.{plane.value}_id) AS present
                FROM temporal_conversion_rows c LEFT JOIN {plane.value}s f
                  ON f.deployment_id = c.deployment_id AND f.{plane.value}_id = c.fact_id
                WHERE c.deployment_id = :dep AND c.conversion_id = :conversion AND c.fact_kind = :plane
            """),
                {
                    "dep": deployment_id,
                    "conversion": campaign["conversion_id"],
                    "plane": plane.value,
                    "state": required_state,
                },
            ).one()
            if tuple(row) != (expected, expected, expected):
                raise TemporalWriteConflict(
                    "conversion identity inventory is incomplete or contains orphan shadows"
                )


def _decode_state(*, value: object) -> FactTemporalState:
    """Decode persisted JSON through the strict temporal tuple model, including UTC fields."""
    return FactTemporalState.model_validate_json(json.dumps(value))


def _claim_window(*, row: RowMapping) -> ClaimTemporalWindow:
    """Decode raw immutable D41 evidence without source-clock substitution."""
    return ClaimTemporalWindow(
        claim_id=row["claim_id"],
        kind=row["claim_valid_kind"],
        valid_from=_utc_timestamp(value=row["claim_valid_from"]),
        valid_until=_utc_timestamp(value=row["claim_valid_until"]),
        precision=row["claim_valid_precision"],
    )


_CAMPAIGN = text(
    "SELECT * FROM temporal_conversion_runs WHERE deployment_id = :dep AND generation = :generation"
).bindparams(generation=TEMPORAL_FACT_GENERATION)
_PROGRESS = text("""
    SELECT c.*, count(r.fact_id) FILTER (WHERE r.state = 'prepared') AS prepared,
      count(r.fact_id) FILTER (WHERE r.state = 'applied') AS applied,
      count(r.fact_id) FILTER (WHERE r.state = 'verified') AS verified
    FROM temporal_conversion_runs c LEFT JOIN temporal_conversion_rows r USING (deployment_id, conversion_id)
    WHERE c.deployment_id = :dep AND c.generation = :generation GROUP BY c.conversion_id
""").bindparams(generation=TEMPORAL_FACT_GENERATION)
_ADVANCE_PHASE = text(
    "UPDATE temporal_conversion_runs SET state = :new WHERE deployment_id = :dep AND generation = :generation AND state = :old"
).bindparams(generation=TEMPORAL_FACT_GENERATION)
_INSERT_SHADOW = text("""
    INSERT INTO temporal_conversion_rows (deployment_id, conversion_id, fact_kind, fact_id,
        expected_revision, input_fingerprint, converted_state, state)
    VALUES (:dep, :conversion, :plane, :fact, :revision, :fingerprint, CAST(:state AS jsonb), 'prepared')
    ON CONFLICT DO NOTHING
""")
_VERIFY_RECEIPT = text("""
    SELECT o.*, s.expected_block_count, s.expected_claim_count, s.expected_semantic_dependency_count,
      (SELECT count(*) FROM temporal_operation_blocks b WHERE b.deployment_id = o.deployment_id AND b.operation_id = o.operation_id) AS actual_blocks,
      (SELECT count(DISTINCT e.claim_id) FROM temporal_operation_evidence e WHERE e.deployment_id = o.deployment_id AND e.operation_id = o.operation_id) AS actual_claims,
      (SELECT count(*) FROM temporal_operation_dependencies d WHERE d.deployment_id = o.deployment_id AND d.operation_id = o.operation_id AND d.required_for_semantics) AS actual_semantic
    FROM temporal_operations o JOIN temporal_operation_support s USING (deployment_id, operation_id)
    WHERE o.deployment_id = :dep AND o.operation_id = :operation
""")
