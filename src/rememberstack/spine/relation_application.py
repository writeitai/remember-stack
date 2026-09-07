"""D110 relation admission and short prepare/infer/revalidate application transactions."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timezone
import json
from uuid import UUID
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine

from rememberstack.core.fact_temporal import cap_fact
from rememberstack.core.fact_temporal import occurrence_union
from rememberstack.core.fact_temporal import seed_fact
from rememberstack.core.relation_temporal import assertion_bounds
from rememberstack.core.relation_temporal import nominated_candidate
from rememberstack.core.relation_temporal import permits_evidence
from rememberstack.core.relation_temporal import relation_kind
from rememberstack.core.relation_temporal import union_occurrence
from rememberstack.model import ModelRequest
from rememberstack.model.fact_temporal import ClaimTemporalWindow
from rememberstack.model.fact_temporal import FactTemporalBasis
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.fact_temporal import FactTemporalState
from rememberstack.model.fact_temporal import TemporalResult
from rememberstack.model.relation_application import RelationApplicationCandidate
from rememberstack.model.relation_application import RelationApplicationInputs
from rememberstack.model.relation_application import RelationApplicationOutput
from rememberstack.model.relation_application import RelationApplicationPreparation
from rememberstack.model.relation_application import RelationApplicationResult
from rememberstack.model.relation_application import RelationIdentityVerdict
from rememberstack.model.relation_application import RelationPairDecision
from rememberstack.model.relation_application import RelationTestimony
from rememberstack.model.relation_application import StagedRelation
from rememberstack.model.temporal_write import FactPlane
from rememberstack.model.temporal_write import TemporalBlock
from rememberstack.model.temporal_write import TemporalDecision
from rememberstack.model.temporal_write import TemporalEffect
from rememberstack.model.temporal_write import TemporalFactRef
from rememberstack.model.temporal_write import TemporalOperationKind
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.spine.supersession import ADJUDICATOR_VERSION
from rememberstack.spine.supersession import SupersessionSettings
from rememberstack.spine.temporal_journal import _canonical_subject
from rememberstack.spine.temporal_journal import _evidence_ref
from rememberstack.spine.temporal_journal import _load_claim_input
from rememberstack.spine.temporal_journal import _utc_timestamp
from rememberstack.spine.temporal_journal import temporal_block_key
from rememberstack.spine.temporal_journal import temporal_fingerprint
from rememberstack.spine.temporal_journal import temporal_identity_admission
from rememberstack.spine.temporal_journal import temporal_write
from rememberstack.spine.temporal_journal import TemporalWriteConflict
from rememberstack.spine.temporal_journal import TemporalWriteSession

_TESTIMONY_LIMIT = 8

_PROMPT = """Adjudicate an unattached relation assertion against existing facts.
Return one decision for each relevant existing candidate by its relation_id:
evidence = the same state or occurrence (at most one evidence target);
incoming_succeeds = the incoming state, or ending event, ends that existing state;
existing_succeeds = the incoming dated historical state ends when that later dated state begins;
contradict = incompatible claims about the same situation, including one event with a disputed date;
coexist = independent facts or insufficient evidence of succession or contradiction.
No evidence target means create a separate fact. Repeated events may overlap in
time yet be different events. Identical triples do not prove occurrence identity.
Never use said-on timestamps as world-time boundaries. Missing world time stays
unknown. A mixed dated/undated pair or disjoint dated windows cannot be evidence.
Only states can be ended. A state may end at a successor's world-time start;
a time supplied by publication or ingestion is never a successor boundary.
When uncertain prefer coexist. Omitted testimony is disclosed: do not assert a
conflict is resolved when unseen evidence could be needed to resolve it.

The input JSON keeps world-time, belief-time and testimony timestamps separate:
{inputs}
"""


class OrderedRelationApplier:
    """Apply a materialized unit by helping the canonical block's recorded head."""

    def __init__(
        self,
        *,
        engine: Engine,
        model_provider: ModelProviderPort,
        settings: SupersessionSettings,
    ) -> None:
        """Bind the existing provider ladder and fact journal to staged relation work."""
        self._engine = engine
        self._model_provider = model_provider
        self._settings = settings

    def prepare(
        self, *, deployment_id: UUID, unit_id: UUID
    ) -> RelationApplicationPreparation | None:
        """Admit a closed batch and persist its head snapshot, or certify this unit already applied."""
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=deployment_id
            ),
        ):
            unit = (
                connection.execute(_LOAD_UNIT, {"dep": deployment_id, "unit": unit_id})
                .mappings()
                .one_or_none()
            )
            if unit is None or unit["adjudicator_version"] != ADJUDICATOR_VERSION:
                raise TemporalWriteConflict(
                    "relation unit is missing or belongs to another adjudicator generation"
                )
            if (
                connection.execute(
                    text(
                        "SELECT count(*) FROM relation_flush_inputs WHERE deployment_id=:dep AND unit_id=:unit"
                    ),
                    {"dep": deployment_id, "unit": unit_id},
                ).scalar_one()
                == 0
            ):
                raise TemporalWriteConflict(
                    "materialized relation unit has lost its assertion membership"
                )
            if not connection.execute(
                _UNIT_PENDING, {"dep": deployment_id, "unit": unit_id}
            ).scalar_one():
                return None
            subject_id = _canonical_subject(
                connection=connection,
                deployment_id=deployment_id,
                entity_id=unit["subject_entity_id"],
            )
            block = TemporalBlock(
                plane=FactPlane.RELATION,
                subject_entity_id=subject_id,
                predicate=unit["predicate"],
            )
            with temporal_write(
                connection=connection,
                deployment_id=deployment_id,
                blocks=(block,),
                facts=(),
            ):
                if not connection.execute(
                    _UNIT_PENDING, {"dep": deployment_id, "unit": unit_id}
                ).scalar_one():
                    return None
                batch_id = _admit_on(
                    connection=connection, deployment_id=deployment_id, block=block
                )
                head = (
                    connection.execute(_HEAD, {"dep": deployment_id, "batch": batch_id})
                    .mappings()
                    .one_or_none()
                )
                if head is None:
                    raise TemporalWriteConflict(
                        "active relation batch has no unapplied head"
                    )
                with self._inputs_on(
                    connection=connection,
                    deployment_id=deployment_id,
                    block=block,
                    assertion_id=head["assertion_id"],
                ) as (inputs, _session):
                    fingerprint = _input_digest(inputs=inputs)
                    if (
                        head["prepared_snapshot"] is not None
                        and head["prepared_fingerprint"] == fingerprint
                    ):
                        prepared = RelationApplicationPreparation.model_validate_json(
                            json.dumps(head["prepared_snapshot"])
                        )
                        _require_preparation(
                            prepared=prepared,
                            batch_id=batch_id,
                            ordinal=head["ordinal"],
                            assertion_id=head["assertion_id"],
                            fingerprint=fingerprint,
                        )
                        return prepared
                    prepared = RelationApplicationPreparation(
                        batch_id=batch_id,
                        ordinal=head["ordinal"],
                        adjudicator_version=ADJUDICATOR_VERSION,
                        preparation_id=uuid4(),
                        input_fingerprint=fingerprint,
                        inputs=inputs,
                        new_relation_id=uuid4(),
                        recorded_at=connection.execute(text("SELECT clock_timestamp()"))
                        .scalar_one()
                        .astimezone(timezone.utc),
                    )
                    connection.execute(
                        _PREPARE,
                        {
                            "dep": deployment_id,
                            "batch": batch_id,
                            "ordinal": prepared.ordinal,
                            "attempt": prepared.preparation_id,
                            "fingerprint": fingerprint,
                            "snapshot": prepared.model_dump_json(),
                        },
                    )
                    return prepared

    def infer(
        self, *, prepared: RelationApplicationPreparation, meter: CostMeterPort
    ) -> RelationApplicationOutput:
        """Use exact compatible-state identity or the semantic ladder outside database transactions."""
        inputs = prepared.inputs
        candidates = tuple(
            candidate
            for candidate in inputs.candidates
            if nominated_candidate(
                assertion=inputs.assertion,
                candidate=candidate,
                is_change_prone=inputs.is_change_prone,
            )
        )
        exact = tuple(
            candidate
            for candidate in candidates
            if candidate.state.kind is FactTemporalKind.STATE
            and permits_evidence(assertion=inputs.assertion, candidate=candidate)
        )
        if len(exact) == 1 and len(candidates) == 1:
            return RelationApplicationOutput(
                verdict=RelationIdentityVerdict(
                    decisions=(
                        RelationPairDecision(
                            relation_id=exact[0].relation_id, outcome="evidence"
                        ),
                    ),
                    confidence=1,
                    rationale="same canonical state value with compatible world-time",
                ),
                method="exact",
            )
        if not candidates:
            return RelationApplicationOutput(
                verdict=RelationIdentityVerdict(
                    confidence=1, rationale="no eligible blocked candidate"
                ),
                method="novelty_gate",
            )
        prompt = _PROMPT.format(
            inputs=inputs.model_copy(
                update={"candidates": candidates}
            ).model_dump_json()
        )
        if len(exact) == 1:
            prompt += (
                "\nDeterministic compatible-state identity is already established: "
                f"evidence target {exact[0].relation_id}. Preserve this identity; "
                "judge only the remaining candidates' semantic relationships."
            )
        key = f"relation:{inputs.assertion.assertion_id}:{prepared.preparation_id}"
        call = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.small_model, prompt=prompt, temperature=0.0
            ),
            response_type=RelationIdentityVerdict,
        )
        meter.record(call_key=f"{key}:small", tier="small_model", usage=call.usage)
        if call.output.confidence >= self._settings.confidence_floor:
            return RelationApplicationOutput(
                verdict=call.output,
                method="small_model",
                model=self._settings.small_model,
            )
        call = self._model_provider.generate(
            request=ModelRequest(
                model=self._settings.frontier_model, prompt=prompt, temperature=0.0
            ),
            response_type=RelationIdentityVerdict,
        )
        meter.record(call_key=f"{key}:frontier", tier="frontier_llm", usage=call.usage)
        return RelationApplicationOutput(
            verdict=call.output,
            method="frontier_llm",
            model=self._settings.frontier_model,
        )

    def recorded_output(
        self, *, prepared: RelationApplicationPreparation
    ) -> RelationApplicationOutput | None:
        """Reuse the first complete stored model answer for this exact prepared attempt."""
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=prepared.inputs.deployment_id
            ),
        ):
            value = connection.execute(
                _LOAD_OUTPUT, _attempt_parameters(prepared=prepared)
            ).scalar_one_or_none()
            return (
                RelationApplicationOutput.model_validate(value)
                if value is not None
                else None
            )

    def publish_output(
        self,
        *,
        prepared: RelationApplicationPreparation,
        output: RelationApplicationOutput,
    ) -> RelationApplicationOutput:
        """Compare-and-swap the first complete answer; a stale attempt cannot replace the head."""
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=prepared.inputs.deployment_id
            ),
        ):
            parameters = {
                **_attempt_parameters(prepared=prepared),
                "output": output.model_dump_json(),
            }
            value = connection.execute(_PUBLISH_OUTPUT, parameters).scalar_one_or_none()
            if value is None:
                value = connection.execute(
                    _LOAD_OUTPUT, parameters
                ).scalar_one_or_none()
            if value is None:
                raise TemporalWriteConflict(
                    "relation preparation was superseded before output publication"
                )
            return RelationApplicationOutput.model_validate(value)

    def apply(
        self, *, prepared: RelationApplicationPreparation
    ) -> RelationApplicationResult:
        """Revalidate the head and atomically commit its fact effects and application receipt."""
        dep = prepared.inputs.deployment_id
        assertion = prepared.inputs.assertion
        block = TemporalBlock(
            plane=FactPlane.RELATION,
            subject_entity_id=assertion.subject_entity_id,
            predicate=assertion.predicate,
        )
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(connection=connection, deployment_id=dep),
        ):
            existing = _application_result_on(
                connection=connection,
                deployment_id=dep,
                assertion_id=assertion.assertion_id,
            )
            if existing is not None:
                return existing
            with temporal_write(
                connection=connection, deployment_id=dep, blocks=(block,), facts=()
            ):
                head = (
                    connection.execute(_HEAD, {"dep": dep, "batch": prepared.batch_id})
                    .mappings()
                    .one_or_none()
                )
                if (
                    head is None
                    or head["ordinal"] != prepared.ordinal
                    or head["assertion_id"] != assertion.assertion_id
                    or head["preparation_id"] != prepared.preparation_id
                    or head["prepared_fingerprint"] != prepared.input_fingerprint
                ):
                    raise TemporalWriteConflict(
                        "only the recorded unapplied relation head may commit"
                    )
                if (
                    RelationApplicationPreparation.model_validate_json(
                        json.dumps(head["prepared_snapshot"])
                    )
                    != prepared
                ):
                    raise TemporalWriteConflict(
                        "caller preparation differs from the recorded snapshot"
                    )
                _require_preparation(
                    prepared=prepared,
                    batch_id=prepared.batch_id,
                    ordinal=prepared.ordinal,
                    assertion_id=assertion.assertion_id,
                    fingerprint=prepared.input_fingerprint,
                )
                if head["prepared_output"] is None:
                    raise TemporalWriteConflict(
                        "relation head has no complete recorded output"
                    )
                output = RelationApplicationOutput.model_validate(
                    head["prepared_output"]
                )
                evidence_target, decisions = _validated_decisions(
                    prepared=prepared,
                    output=output,
                    confidence_floor=self._settings.confidence_floor,
                )
                new_fact = (
                    TemporalFactRef(
                        plane=FactPlane.RELATION, fact_id=prepared.new_relation_id
                    )
                    if evidence_target is None
                    else None
                )
                with self._inputs_on(
                    connection=connection,
                    deployment_id=dep,
                    block=block,
                    assertion_id=assertion.assertion_id,
                    new_fact=new_fact,
                ) as (inputs, session):
                    if _input_digest(inputs=inputs) != prepared.input_fingerprint:
                        raise TemporalWriteConflict(
                            "relation source, identity, candidates or policy changed before application"
                        )
                    result, adjudications = _apply_on(
                        connection=connection,
                        session=session,
                        prepared=prepared,
                        output=output,
                        evidence_target=evidence_target,
                        decisions=decisions,
                    )
                    connection.execute(
                        _INSERT_APPLICATION,
                        {
                            "dep": dep,
                            "assertion": assertion.assertion_id,
                            "generation": ADJUDICATOR_VERSION,
                            "batch": prepared.batch_id,
                            "ordinal": prepared.ordinal,
                            "fact": result.relation_id,
                            "outcome": result.identity_outcome,
                            "fingerprint": prepared.input_fingerprint,
                        },
                    )
                    for adjudication_id in adjudications:
                        connection.execute(
                            text("""INSERT INTO relation_application_adjudications (deployment_id, assertion_id, adjudicator_version, adjudication_id)
                            VALUES (:dep, :assertion, :generation, :id)"""),
                            {
                                "dep": dep,
                                "assertion": assertion.assertion_id,
                                "generation": ADJUDICATOR_VERSION,
                                "id": adjudication_id,
                            },
                        )
                    connection.execute(
                        _RETIRE_MEMBERSHIPS,
                        {
                            "dep": dep,
                            "assertion": assertion.assertion_id,
                            "generation": ADJUDICATOR_VERSION,
                        },
                    )
                    connection.execute(
                        _COMPLETE_BATCH, {"dep": dep, "batch": prepared.batch_id}
                    )
                    return result

    def affected_for_unit(
        self, *, deployment_id: UUID, unit_id: UUID
    ) -> tuple[UUID, ...]:
        """Recover every committed fact touched by this unit after a post-commit worker crash."""
        with (
            self._engine.begin() as connection,
            temporal_identity_admission(
                connection=connection, deployment_id=deployment_id
            ),
        ):
            result: set[UUID] = set()
            for assertion_id in connection.execute(
                text(
                    "SELECT assertion_id FROM relation_flush_inputs WHERE deployment_id=:dep AND unit_id=:unit"
                ),
                {"dep": deployment_id, "unit": unit_id},
            ).scalars():
                applied = _application_result_on(
                    connection=connection,
                    deployment_id=deployment_id,
                    assertion_id=assertion_id,
                )
                if applied is None:
                    raise TemporalWriteConflict(
                        "unit still contains an unapplied assertion"
                    )
                result.update(applied.affected_relation_ids)
            return tuple(sorted(result))

    @contextmanager
    def _inputs_on(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        block: TemporalBlock,
        assertion_id: UUID,
        new_fact: TemporalFactRef | None = None,
    ) -> Iterator[tuple[RelationApplicationInputs, TemporalWriteSession]]:
        """Discover the complete fact lock set under its block, then snapshot exact inputs."""
        rows = tuple(
            connection.execute(
                _BLOCK_FACTS,
                {
                    "dep": deployment_id,
                    "subject": block.subject_entity_id,
                    "predicate": block.predicate,
                },
            ).mappings()
        )
        facts = tuple(
            TemporalFactRef(plane=FactPlane.RELATION, fact_id=row["relation_id"])
            for row in rows
        )
        if new_fact is not None:
            facts += (new_fact,)
        with temporal_write(
            connection=connection,
            deployment_id=deployment_id,
            blocks=(block,),
            facts=facts,
            new_facts=frozenset((new_fact,)) if new_fact is not None else frozenset(),
        ) as session:
            assertion = _assertion_on(
                connection=connection,
                deployment_id=deployment_id,
                assertion_id=assertion_id,
            )
            if (
                assertion.subject_entity_id != block.subject_entity_id
                or assertion.predicate != block.predicate
            ):
                raise TemporalWriteConflict(
                    "assertion no longer belongs to the admitted canonical block"
                )
            candidates: list[RelationApplicationCandidate] = []
            for row in rows:
                fact = TemporalFactRef(
                    plane=FactPlane.RELATION, fact_id=row["relation_id"]
                )
                state = session.state(fact=fact)
                if state is None:
                    raise TemporalWriteConflict(
                        "existing relation disappeared during preparation"
                    )
                object_id = _canonical_subject(
                    connection=connection,
                    deployment_id=deployment_id,
                    entity_id=row["object_entity_id"],
                )
                witnesses = tuple(
                    connection.execute(
                        _CANDIDATE_TESTIMONY,
                        {
                            "dep": deployment_id,
                            "fact": fact.fact_id,
                            "limit": _TESTIMONY_LIMIT,
                        },
                    ).mappings()
                )
                total = connection.execute(
                    text(
                        "SELECT count(*) FROM relation_evidence WHERE deployment_id = :dep AND relation_id = :fact"
                    ),
                    {"dep": deployment_id, "fact": fact.fact_id},
                ).scalar_one()
                operation_id = connection.execute(
                    _FACT_OPERATION,
                    {
                        "dep": deployment_id,
                        "fact": fact.fact_id,
                        "revision": state.revision,
                    },
                ).scalar_one_or_none()
                if operation_id is None:
                    raise TemporalWriteConflict(
                        "candidate relation has no verified temporal authority operation"
                    )
                candidates.append(
                    RelationApplicationCandidate(
                        relation_id=fact.fact_id,
                        object_entity_id=object_id,
                        object_name=_entity_name_on(
                            connection=connection,
                            deployment_id=deployment_id,
                            entity_id=object_id,
                        ),
                        state=state,
                        testimony=tuple(
                            _testimony_on(
                                connection=connection,
                                deployment_id=deployment_id,
                                claim_id=row["claim_id"],
                                role="historical",
                            )
                            for row in witnesses
                        ),
                        omitted_testimony=max(0, total - len(witnesses)),
                        operation_id=operation_id,
                    )
                )
            predicate = (
                connection.execute(
                    text(
                        "SELECT status::text, is_change_prone FROM predicates WHERE deployment_id = :dep AND predicate = :predicate FOR SHARE"
                    ),
                    {"dep": deployment_id, "predicate": block.predicate},
                )
                .mappings()
                .one()
            )
            if predicate["status"] != "active":
                raise TemporalWriteConflict("admitted predicate is no longer active")
            yield (
                RelationApplicationInputs(
                    deployment_id=deployment_id,
                    assertion=assertion,
                    candidates=tuple(candidates),
                    blocks=session.block_states,
                    is_change_prone=predicate["is_change_prone"],
                    predicate_status=predicate["status"],
                    policy_fingerprint=temporal_fingerprint(
                        value={
                            "generation": ADJUDICATOR_VERSION,
                            "settings": self._settings.model_dump(mode="json"),
                            "testimony_limit": _TESTIMONY_LIMIT,
                        }
                    ),
                ),
                session,
            )


def _testimony_on(
    *, connection: Connection, deployment_id: UUID, claim_id: UUID, role: str
) -> RelationTestimony:
    """Load one source tuple with the same fingerprint used by guarded journal validation."""
    row = _load_claim_input(
        connection=connection, deployment_id=deployment_id, claim_id=claim_id
    )
    return RelationTestimony(
        claim_id=claim_id,
        doc_id=row["doc_id"],
        text=row["claim_text"],
        asserted_at=_utc_timestamp(value=row["asserted_at"]),
        window=ClaimTemporalWindow(
            claim_id=claim_id,
            kind=row["claim_valid_kind"],
            valid_from=_utc_timestamp(value=row["claim_valid_from"]),
            valid_until=_utc_timestamp(value=row["claim_valid_until"]),
            precision=row["claim_valid_precision"],
        ),
        evidence=_evidence_ref(
            row=row, role="support" if role == "support" else "historical"
        ),
    )


def _entity_name_on(
    *, connection: Connection, deployment_id: UUID, entity_id: UUID
) -> str:
    """Read the deployment-local canonical name included in the model snapshot."""
    return connection.execute(
        text(
            "SELECT canonical_name FROM entities WHERE deployment_id = :dep AND entity_id = :id"
        ),
        {"dep": deployment_id, "id": entity_id},
    ).scalar_one()


def _assertion_on(
    *, connection: Connection, deployment_id: UUID, assertion_id: UUID
) -> StagedRelation:
    """Resolve a retained immutable assertion under the deployment identity epoch."""
    row = (
        connection.execute(
            _ASSERTION, {"dep": deployment_id, "assertion": assertion_id}
        )
        .mappings()
        .one()
    )
    subject = _canonical_subject(
        connection=connection,
        deployment_id=deployment_id,
        entity_id=row["subject_entity_id"],
    )
    object_id = _canonical_subject(
        connection=connection,
        deployment_id=deployment_id,
        entity_id=row["object_entity_id"],
    )
    return StagedRelation(
        assertion_id=assertion_id,
        normalizer_version=row["normalizer_version"],
        subject_entity_id=subject,
        predicate=row["predicate"],
        object_entity_id=object_id,
        subject_name=_entity_name_on(
            connection=connection, deployment_id=deployment_id, entity_id=subject
        ),
        object_name=_entity_name_on(
            connection=connection, deployment_id=deployment_id, entity_id=object_id
        ),
        shape_kind=row["shape_kind"],
        testimony=_testimony_on(
            connection=connection,
            deployment_id=deployment_id,
            claim_id=row["claim_id"],
            role="support",
        ),
    )


def _input_digest(*, inputs: RelationApplicationInputs) -> str:
    """Ignore read-only sequence advances while retaining all truth revisions and source inputs."""
    payload = inputs.model_dump(mode="json")
    payload["blocks"] = [
        {"block_key": block.block_key, "revision": block.revision}
        for block in inputs.blocks
    ]
    return temporal_fingerprint(value=payload)


def _attempt_parameters(
    *, prepared: RelationApplicationPreparation
) -> dict[str, object]:
    """Name one pinned preparation without deriving identity from array positions."""
    return {
        "dep": prepared.inputs.deployment_id,
        "batch": prepared.batch_id,
        "ordinal": prepared.ordinal,
        "attempt": prepared.preparation_id,
        "fingerprint": prepared.input_fingerprint,
    }


def _require_preparation(
    *,
    prepared: RelationApplicationPreparation,
    batch_id: UUID,
    ordinal: int,
    assertion_id: UUID,
    fingerprint: str,
) -> None:
    """Reject a corrupted saved attempt instead of silently repeating or changing its meaning."""
    if (
        prepared.batch_id != batch_id
        or prepared.ordinal != ordinal
        or prepared.inputs.assertion.assertion_id != assertion_id
        or prepared.input_fingerprint != fingerprint
        or _input_digest(inputs=prepared.inputs) != fingerprint
        or prepared.adjudicator_version != ADJUDICATOR_VERSION
    ):
        raise TemporalWriteConflict(
            "recorded relation preparation disagrees with its head or source fingerprint"
        )


def _admit_on(
    *, connection: Connection, deployment_id: UUID, block: TemporalBlock
) -> UUID:
    """Freeze all materialized unapplied members in stable source order under the canonical block."""
    parameters = {
        "dep": deployment_id,
        "subject": block.subject_entity_id,
        "predicate": block.predicate,
        "generation": ADJUDICATOR_VERSION,
    }
    active = tuple(
        connection.execute(
            text(
                _SUBJECT_FAMILY
                + """
        SELECT b.batch_id, b.subject_entity_id, b.adjudicator_version
        FROM relation_apply_batches b JOIN family f ON f.entity_id = b.subject_entity_id
        WHERE b.deployment_id = :dep AND b.predicate = :predicate AND b.completed_at IS NULL
        ORDER BY b.admitted_at, b.batch_id
    """
            ),
            parameters,
        ).mappings()
    )
    if active:
        if (
            len(active) != 1
            or active[0]["subject_entity_id"] != block.subject_entity_id
            or active[0]["adjudicator_version"] != ADJUDICATOR_VERSION
        ):
            raise TemporalWriteConflict(
                "an active relation batch requires generation/identity reconciliation before new admission"
            )
        return active[0]["batch_id"]
    batch_id = uuid4()
    parameters["batch"] = batch_id
    # Version materialization does not take the fact block lock. One statement
    # therefore freezes the eligible set, its count, and all batch inputs from
    # one MVCC snapshot even while another version becomes eligible.
    admitted = connection.execute(
        text(
            "WITH eligible AS MATERIALIZED ("
            + _ELIGIBLE_ASSERTIONS
            + """),
        new_batch AS (
            INSERT INTO relation_apply_batches (batch_id, deployment_id, subject_entity_id,
                predicate, adjudicator_version, expected_inputs)
            SELECT :batch, :dep, :subject, :predicate, :generation, count(*)
            FROM eligible HAVING count(*) > 0 RETURNING batch_id
        ), admitted_inputs AS (
            INSERT INTO relation_apply_batch_inputs (deployment_id, batch_id, adjudicator_version, ordinal, assertion_id)
            SELECT :dep, b.batch_id, :generation,
                row_number() OVER (ORDER BY e.asserted_at NULLS LAST, e.claim_id, e.predicate COLLATE "C", e.object_entity_id, e.assertion_id),
                e.assertion_id FROM eligible e CROSS JOIN new_batch b RETURNING assertion_id
        ) SELECT batch_id FROM new_batch WHERE (SELECT count(*) FROM admitted_inputs) > 0
        """
        ),
        parameters,
    ).scalar_one_or_none()
    if admitted is None:
        raise TemporalWriteConflict("relation unit has no eligible unapplied assertion")
    return admitted


def _validated_decisions(
    *,
    prepared: RelationApplicationPreparation,
    output: RelationApplicationOutput,
    confidence_floor: float,
) -> tuple[UUID | None, dict[UUID, str]]:
    """Enforce identity and temporal permission independently of a model's confidence or wording."""
    assertion = prepared.inputs.assertion
    candidates = {
        candidate.relation_id: candidate for candidate in prepared.inputs.candidates
    }
    decisions: dict[UUID, str] = {}
    evidence: UUID | None = None
    exact = tuple(
        candidate.relation_id
        for candidate in candidates.values()
        if candidate.state.kind is FactTemporalKind.STATE
        and nominated_candidate(
            assertion=assertion,
            candidate=candidate,
            is_change_prone=prepared.inputs.is_change_prone,
        )
        and permits_evidence(assertion=assertion, candidate=candidate)
    )
    for decision in output.verdict.decisions:
        if decision.relation_id in decisions or decision.relation_id not in candidates:
            raise TemporalWriteConflict(
                "identity verdict repeats or invents a candidate"
            )
        candidate = candidates[decision.relation_id]
        if not nominated_candidate(
            assertion=assertion,
            candidate=candidate,
            is_change_prone=prepared.inputs.is_change_prone,
        ):
            raise TemporalWriteConflict(
                "identity verdict references an ineligible temporal candidate"
            )
        outcome = (
            decision.outcome
            if output.verdict.confidence >= confidence_floor
            else "coexist"
        )
        if len(exact) == 1 and candidate.relation_id == exact[0]:
            outcome = "evidence"
        if outcome == "evidence":
            if evidence is not None or not permits_evidence(
                assertion=assertion, candidate=candidate
            ):
                raise TemporalWriteConflict(
                    "identity verdict cannot attach disjoint/mixed inputs or select multiple evidence targets"
                )
            evidence = candidate.relation_id
        elif outcome == "incoming_succeeds":
            if candidate.state.kind is not FactTemporalKind.STATE:
                raise TemporalWriteConflict(
                    "an occurrence or unknown fact cannot be superseded"
                )
        elif outcome == "existing_succeeds":
            incoming = assertion_bounds(assertion=assertion)
            if (
                relation_kind(assertion=assertion) is not FactTemporalKind.STATE
                or candidate.state.kind is not FactTemporalKind.STATE
                or incoming.start is None
                or candidate.state.verdict.start is None
                or candidate.state.verdict.start <= incoming.start
            ):
                raise TemporalWriteConflict(
                    "historical state succession requires two correctly ordered world-time starts"
                )
        decisions[decision.relation_id] = outcome
    if len(exact) == 1:
        if evidence is not None and evidence != exact[0]:
            raise TemporalWriteConflict(
                "semantic verdict conflicts with deterministic compatible-state identity"
            )
        evidence = exact[0]
        decisions[evidence] = "evidence"
    return evidence, decisions


def _apply_on(
    *,
    connection: Connection,
    session: TemporalWriteSession,
    prepared: RelationApplicationPreparation,
    output: RelationApplicationOutput,
    evidence_target: UUID | None,
    decisions: dict[UUID, str],
) -> tuple[RelationApplicationResult, tuple[UUID, ...]]:
    """Apply the chosen identity, caps and contradiction metadata as one journal group."""
    inputs = prepared.inputs
    assertion = inputs.assertion
    dep = inputs.deployment_id
    target = TemporalFactRef(
        plane=FactPlane.RELATION, fact_id=evidence_target or prepared.new_relation_id
    )
    candidate_map = {
        candidate.relation_id: candidate for candidate in inputs.candidates
    }
    block = TemporalBlock(
        plane=FactPlane.RELATION,
        subject_entity_id=assertion.subject_entity_id,
        predicate=assertion.predicate,
    )
    written = frozenset((temporal_block_key(deployment_id=dep, block=block),))
    witnesses = {
        (testimony.evidence.claim_id, testimony.evidence.role): testimony.evidence
        for candidate in inputs.candidates
        for testimony in candidate.testimony
    }
    witnesses[(assertion.testimony.claim_id, "support")] = assertion.testimony.evidence
    evidence = tuple(witnesses[key] for key in sorted(witnesses))
    predecessors = tuple(
        sorted({candidate.operation_id for candidate in inputs.candidates})
    )
    adjudications: list[UUID] = []
    affected: set[UUID] = {target.fact_id}

    def record(
        *,
        fact: TemporalFactRef,
        before: FactTemporalState,
        after: FactTemporalState,
        operation_id: UUID,
        kind: TemporalOperationKind,
        outcome: str,
        reason: str,
        related_id: UUID | None = None,
    ) -> None:
        """Append narrative and exact support with the same fact mutation and block revision."""
        adjudication_id = uuid4()
        applied = before != after
        session.apply(
            effect=TemporalEffect(
                operation_id=operation_id,
                fact=fact,
                kind=kind,
                result=TemporalResult.APPLIED if applied else TemporalResult.NOOP,
                before=before,
                after=after,
                decision=TemporalDecision(
                    adjudication_id=adjudication_id,
                    outcome=outcome,
                    method=output.method,
                    confidence=output.verdict.confidence,
                    triggering_claim_id=assertion.testimony.claim_id,
                    triggering_assertion_id=assertion.assertion_id,
                    related_fact_id=related_id,
                    features={
                        "rationale": output.verdict.rationale,
                        "model": output.model,
                    },
                ),
                evidence=evidence,
                semantic_predecessors=predecessors,
                input_fingerprint=prepared.input_fingerprint,
                identity_generation=inputs.policy_fingerprint,
                policy_generation=ADJUDICATOR_VERSION,
                reason=reason,
                recorded_at=prepared.recorded_at,
            ),
            written_blocks=written if applied else frozenset(),
        )
        adjudications.append(adjudication_id)
        affected.add(fact.fact_id)

    if evidence_target is None:
        connection.execute(
            text("""INSERT INTO relations (relation_id, deployment_id, subject_entity_id, predicate, object_entity_id, normalizer_version, ingested_at)
            VALUES (:id, :dep, :subject, :predicate, :object, :normalizer, :at)"""),
            {
                "id": target.fact_id,
                "dep": dep,
                "subject": assertion.subject_entity_id,
                "predicate": assertion.predicate,
                "object": assertion.object_entity_id,
                "normalizer": assertion.normalizer_version,
                "at": prepared.recorded_at,
            },
        )
    # Group a temporal/semantic contradiction before inserting a possibly
    # overlapping state. No state exclusion constraint is relaxed or bypassed.
    contradictions = {
        identity for identity, decision in decisions.items() if decision == "contradict"
    }
    incoming_boundary = assertion_bounds(assertion=assertion).start
    if (
        evidence_target is not None
        and relation_kind(assertion=assertion) is FactTemporalKind.STATE
    ):
        successor = candidate_map[evidence_target].state.verdict
        incoming_boundary = (
            successor.start
            if successor.start_basis
            in (FactTemporalBasis.WORLD_TIME, FactTemporalBasis.VERDICT)
            else None
        )
    for identity, decision in decisions.items():
        if decision != "incoming_succeeds":
            continue
        candidate = candidate_map[identity]
        fact = TemporalFactRef(plane=FactPlane.RELATION, fact_id=identity)
        before = session.state(fact=fact)
        if before is None:
            raise TemporalWriteConflict("succession target disappeared")
        operation_id = uuid4()
        mutation = cap_fact(
            state=before, boundary=incoming_boundary, operation_id=operation_id
        )
        record(
            fact=fact,
            before=before,
            after=mutation.state,
            operation_id=operation_id,
            kind=TemporalOperationKind.CAP,
            outcome="supersede"
            if mutation.result is TemporalResult.APPLIED
            else "noop",
            reason=mutation.reason,
            related_id=target.fact_id,
        )
        if (
            mutation.result is not TemporalResult.APPLIED
            and incoming_boundary is not None
            and candidate.object_entity_id != assertion.object_entity_id
        ):
            contradictions.add(identity)
    groups = {
        group_id
        for identity in contradictions
        if (group_id := candidate_map[identity].state.contradiction_group) is not None
    }
    group = min(groups) if groups else uuid4() if contradictions else None
    if group is not None:
        contradictions.update(
            candidate.relation_id
            for candidate in inputs.candidates
            if candidate.state.contradiction_group in groups
        )
        for identity in sorted(contradictions):
            fact = TemporalFactRef(plane=FactPlane.RELATION, fact_id=identity)
            before = session.state(fact=fact)
            if before is None:
                raise TemporalWriteConflict("contradiction target disappeared")
            after = (
                before
                if before.contradiction_group == group
                else before.model_copy(
                    update={
                        "contradiction_group": group,
                        "revision": before.revision + 1,
                    }
                )
            )
            record(
                fact=fact,
                before=before,
                after=after,
                operation_id=uuid4(),
                kind=TemporalOperationKind.EVIDENCE,
                outcome="contradict",
                reason="recorded identity/date contradiction",
                related_id=target.fact_id,
            )

    if evidence_target is None:
        before = FactTemporalState(
            kind=FactTemporalKind.UNKNOWN, ingested_at=prepared.recorded_at
        )
        operation_id = uuid4()
        seeded = seed_fact(
            seed=assertion.testimony.window,
            shape=assertion.shape_kind,
            ingested_at=prepared.recorded_at,
        )
        after = seeded.model_copy(
            update={
                "revision": 1,
                "contradiction_group": group,
                "from_operation_id": operation_id
                if seeded.verdict != before.verdict
                and (seeded.verdict.start, seeded.verdict.start_basis)
                != (before.verdict.start, before.verdict.start_basis)
                else None,
                "until_operation_id": operation_id
                if (seeded.verdict.end, seeded.verdict.end_basis)
                != (before.verdict.end, before.verdict.end_basis)
                else None,
            }
        )
        _attach_evidence_on(
            connection=connection,
            deployment_id=dep,
            fact_id=target.fact_id,
            assertion=assertion,
        )
        record(
            fact=target,
            before=before,
            after=after,
            operation_id=operation_id,
            kind=TemporalOperationKind.SEED,
            outcome="add",
            reason="admitted assertion creates a distinct temporal identity",
        )
    else:
        before = session.state(fact=target)
        if before is None:
            raise TemporalWriteConflict("evidence target disappeared")
        inserted = _attach_evidence_on(
            connection=connection,
            deployment_id=dep,
            fact_id=target.fact_id,
            assertion=assertion,
        )
        occurrence = union_occurrence(
            left=before.occurrence,
            right=occurrence_union(claims=(assertion.testimony.window,)),
        )
        changed = (
            inserted
            or occurrence != before.occurrence
            or (group is not None and group != before.contradiction_group)
        )
        after = (
            before.model_copy(
                update={
                    "occurrence": occurrence,
                    "revision": before.revision + 1,
                    "contradiction_group": group or before.contradiction_group,
                }
            )
            if changed
            else before
        )
        record(
            fact=target,
            before=before,
            after=after,
            operation_id=uuid4(),
            kind=TemporalOperationKind.EVIDENCE,
            outcome="noop",
            reason="identity verdict attaches testimony without changing verdict bounds",
        )

    for identity, decision in decisions.items():
        if decision != "existing_succeeds":
            continue
        before = session.state(fact=target)
        if before is None:
            raise TemporalWriteConflict("historical state disappeared")
        candidate = candidate_map[identity]
        boundary = (
            candidate.state.verdict.start
            if candidate.state.verdict.start_basis
            in (FactTemporalBasis.WORLD_TIME, FactTemporalBasis.VERDICT)
            else None
        )
        operation_id = uuid4()
        mutation = cap_fact(state=before, boundary=boundary, operation_id=operation_id)
        record(
            fact=target,
            before=before,
            after=mutation.state,
            operation_id=operation_id,
            kind=TemporalOperationKind.CAP,
            outcome="supersede"
            if mutation.result is TemporalResult.APPLIED
            else "noop",
            reason=mutation.reason,
            related_id=identity,
        )
    return RelationApplicationResult(
        assertion_id=assertion.assertion_id,
        relation_id=target.fact_id,
        identity_outcome="new" if evidence_target is None else "evidence",
        affected_relation_ids=tuple(sorted(affected)),
    ), tuple(adjudications)


def _attach_evidence_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    fact_id: UUID,
    assertion: StagedRelation,
) -> bool:
    """Attach the accepted testimony once and refresh D54 counts of current document lineages."""
    parameters = {
        "dep": deployment_id,
        "fact": fact_id,
        "claim": assertion.testimony.claim_id,
        "doc": assertion.testimony.doc_id,
        "normalizer": assertion.normalizer_version,
    }
    inserted = connection.execute(
        text("""INSERT INTO relation_evidence (deployment_id, relation_id, claim_id, doc_id, stance, normalizer_version)
        VALUES (:dep, :fact, :claim, :doc, 'supports', :normalizer) ON CONFLICT DO NOTHING RETURNING claim_id"""),
        parameters,
    ).scalar_one_or_none()
    connection.execute(
        text("""UPDATE relations r SET
        evidence_count = (SELECT count(DISTINCT e.doc_id) FROM relation_evidence e JOIN claims c ON c.deployment_id=e.deployment_id AND c.claim_id=e.claim_id
            WHERE e.deployment_id=:dep AND e.relation_id=:fact AND e.stance='supports' AND c.is_current_testimony),
        contradict_count = (SELECT count(DISTINCT e.doc_id) FROM relation_evidence e JOIN claims c ON c.deployment_id=e.deployment_id AND c.claim_id=e.claim_id
            WHERE e.deployment_id=:dep AND e.relation_id=:fact AND e.stance='contradicts' AND c.is_current_testimony)
        WHERE r.deployment_id=:dep AND r.relation_id=:fact"""),
        parameters,
    )
    return inserted is not None


def _application_result_on(
    *, connection: Connection, deployment_id: UUID, assertion_id: UUID
) -> RelationApplicationResult | None:
    """Return the committed identity and complete affected set without rerunning its verdict."""
    parameters = {
        "dep": deployment_id,
        "assertion": assertion_id,
        "generation": ADJUDICATOR_VERSION,
    }
    row = (
        connection.execute(
            text(
                "SELECT relation_id, identity_outcome FROM relation_application_receipts WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation"
            ),
            parameters,
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    if (
        connection.execute(
            text(
                "SELECT 1 FROM relations WHERE deployment_id=:dep AND relation_id=:fact"
            ),
            {"dep": deployment_id, "fact": row["relation_id"]},
        ).scalar_one_or_none()
        is None
    ):
        raise TemporalWriteConflict(
            "retained relation receipt cannot resurrect a missing fact"
        )
    affected = set(
        connection.execute(
            text("""SELECT a.relation_id FROM relation_application_adjudications x
        JOIN relation_adjudications a ON a.deployment_id=x.deployment_id AND a.adjudication_id=x.adjudication_id
        WHERE x.deployment_id=:dep AND x.assertion_id=:assertion AND x.adjudicator_version=:generation"""),
            parameters,
        ).scalars()
    )
    affected.add(row["relation_id"])
    return RelationApplicationResult(
        assertion_id=assertion_id,
        relation_id=row["relation_id"],
        identity_outcome=row["identity_outcome"],
        affected_relation_ids=tuple(sorted(affected)),
    )


_SUBJECT_FAMILY = """WITH RECURSIVE family(entity_id) AS (
    SELECT entity_id FROM entities WHERE deployment_id=:dep AND entity_id=:subject
    UNION SELECT e.entity_id FROM entities e JOIN family f ON e.merged_into=f.entity_id WHERE e.deployment_id=:dep
) """
_ELIGIBLE_ASSERTIONS = (
    _SUBJECT_FAMILY
    + """
    SELECT DISTINCT a.assertion_id, c.asserted_at, c.claim_id, a.predicate, a.object_entity_id
    FROM relation_flush_inputs i JOIN relation_flush_block_units u ON u.unit_id=i.unit_id AND u.deployment_id=i.deployment_id
    JOIN family f ON f.entity_id=u.subject_entity_id
    JOIN normalize_relation_assertions a ON a.deployment_id=i.deployment_id AND a.assertion_id=i.assertion_id
    JOIN normalize_claim_receipts r ON r.deployment_id=a.deployment_id AND r.receipt_id=a.receipt_id
    JOIN claims c ON c.deployment_id=r.deployment_id AND c.claim_id=r.claim_id
    WHERE i.deployment_id=:dep AND u.predicate=:predicate AND i.adjudicator_version=:generation AND i.applied_at IS NULL
      AND NOT EXISTS (SELECT 1 FROM relation_application_receipts ar WHERE ar.deployment_id=i.deployment_id AND ar.assertion_id=i.assertion_id AND ar.adjudicator_version=i.adjudicator_version)
"""
)
_LOAD_UNIT = text(
    "SELECT * FROM relation_flush_block_units WHERE deployment_id=:dep AND unit_id=:unit"
)
_UNIT_PENDING = text("""SELECT EXISTS (SELECT 1 FROM relation_flush_inputs i WHERE i.deployment_id=:dep AND i.unit_id=:unit
    AND NOT EXISTS (SELECT 1 FROM relation_application_receipts a WHERE a.deployment_id=i.deployment_id AND a.assertion_id=i.assertion_id AND a.adjudicator_version=i.adjudicator_version))""")
_HEAD = text("""SELECT i.* FROM relation_apply_batch_inputs i
    JOIN relation_apply_batches b ON b.deployment_id=i.deployment_id AND b.batch_id=i.batch_id AND b.completed_at IS NULL
    WHERE i.deployment_id=:dep AND i.batch_id=:batch AND NOT EXISTS (
        SELECT 1 FROM relation_application_receipts r WHERE r.deployment_id=i.deployment_id AND r.assertion_id=i.assertion_id AND r.adjudicator_version=i.adjudicator_version)
    ORDER BY i.ordinal LIMIT 1""")
_PREPARE = text("""UPDATE relation_apply_batch_inputs SET preparation_id=:attempt, prepared_fingerprint=:fingerprint,
    prepared_snapshot=CAST(:snapshot AS jsonb), prepared_output=NULL WHERE deployment_id=:dep AND batch_id=:batch AND ordinal=:ordinal""")
_LOAD_OUTPUT = text("""SELECT prepared_output FROM relation_apply_batch_inputs WHERE deployment_id=:dep AND batch_id=:batch AND ordinal=:ordinal
    AND preparation_id=:attempt AND prepared_fingerprint=:fingerprint""")
_PUBLISH_OUTPUT = text("""UPDATE relation_apply_batch_inputs SET prepared_output=CAST(:output AS jsonb)
    WHERE deployment_id=:dep AND batch_id=:batch AND ordinal=:ordinal AND preparation_id=:attempt AND prepared_fingerprint=:fingerprint AND prepared_output IS NULL
    RETURNING prepared_output""")
_ASSERTION = text("""SELECT a.*, r.claim_id FROM normalize_relation_assertions a JOIN normalize_claim_receipts r
    ON r.deployment_id=a.deployment_id AND r.receipt_id=a.receipt_id AND r.normalizer_version=a.normalizer_version
    WHERE a.deployment_id=:dep AND a.assertion_id=:assertion""")
_BLOCK_FACTS = text(
    _SUBJECT_FAMILY
    + """SELECT r.relation_id, r.object_entity_id FROM relations r JOIN family f ON f.entity_id=r.subject_entity_id
    WHERE r.deployment_id=:dep AND r.predicate=:predicate AND r.invalidated_at IS NULL ORDER BY r.relation_id"""
)
_CANDIDATE_TESTIMONY = text("""SELECT c.claim_id FROM relation_evidence e JOIN claims c ON c.deployment_id=e.deployment_id AND c.claim_id=e.claim_id
    WHERE e.deployment_id=:dep AND e.relation_id=:fact ORDER BY c.is_current_testimony DESC, c.asserted_at NULLS LAST, c.claim_id LIMIT :limit""")
_FACT_OPERATION = text("""SELECT operation_id FROM temporal_operations WHERE deployment_id=:dep AND relation_id=:fact
    AND resulting_revision=:revision AND result='applied' ORDER BY recorded_at DESC, operation_id DESC LIMIT 1""")
_INSERT_APPLICATION = text("""INSERT INTO relation_application_receipts
    (deployment_id, assertion_id, adjudicator_version, batch_id, ordinal, relation_id, identity_outcome, input_digest)
    VALUES (:dep, :assertion, :generation, :batch, :ordinal, :fact, :outcome, :fingerprint)""")
_RETIRE_MEMBERSHIPS = text("""UPDATE relation_flush_inputs SET applied_at=clock_timestamp()
    WHERE deployment_id=:dep AND assertion_id=:assertion AND adjudicator_version=:generation AND applied_at IS NULL""")
_COMPLETE_BATCH = text("""UPDATE relation_apply_batches b SET completed_at=clock_timestamp() WHERE b.deployment_id=:dep AND b.batch_id=:batch
    AND b.expected_inputs = (SELECT count(*) FROM relation_apply_batch_inputs c WHERE c.deployment_id=b.deployment_id AND c.batch_id=b.batch_id)
    AND NOT EXISTS (SELECT 1 FROM relation_apply_batch_inputs i WHERE i.deployment_id=b.deployment_id AND i.batch_id=b.batch_id
        AND NOT EXISTS (SELECT 1 FROM relation_application_receipts r WHERE r.deployment_id=i.deployment_id AND r.assertion_id=i.assertion_id AND r.adjudicator_version=i.adjudicator_version))""")
