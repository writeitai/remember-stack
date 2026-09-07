"""D113 verification of current assertion support before preparation or reuse."""

import hashlib
import json
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import RowMapping

from rememberstack.core.observation_temporal import observation_bounds
from rememberstack.core.observation_temporal import observation_kind
from rememberstack.model.fact_temporal import FactTemporalKind
from rememberstack.model.observation_application import ObservationCurrentSupport
from rememberstack.model.observation_application import ObservationSupportMove
from rememberstack.model.observation_application import StagedObservation
from rememberstack.model.temporal_write import TemporalEvidenceRef
from rememberstack.spine.temporal_journal import _canonical_subject
from rememberstack.spine.temporal_journal import temporal_fingerprint
from rememberstack.spine.temporal_journal import TemporalWriteConflict


def observation_support_fingerprint(
    *,
    assertion_id: UUID,
    adjudicator_version: str,
    support_state: str,
    current_observation_id: UUID | None,
) -> str:
    """Hash D113's exact UTF-8 JSON assignment tuple, independently of fact endpoint roots."""
    payload = json.dumps(
        [
            str(assertion_id),
            adjudicator_version,
            support_state,
            str(current_observation_id) if current_observation_id is not None else None,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def require_observation_support_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    application: RowMapping,
    source: StagedObservation,
) -> ObservationCurrentSupport | None:
    """Verify a linked assignment or explicit erased checkpoint; never repair a missing pointer.

    The caller owns the deployment/identity and canonical block locks. This
    verifies assignment authority, not the entire original application group or
    a version's membership inventory; completion must validate those separately.
    """
    if (
        application["completed_at"] is None
        or application["assertion_id"] != source.assertion_id
    ):
        raise TemporalWriteConflict(
            "observation support requires its completed source application"
        )
    values = {
        "dep": deployment_id,
        "assertion": source.assertion_id,
        "generation": application["adjudicator_version"],
        "fact": application["current_observation_id"],
        "owner": application["support_owner_operation_id"],
        "checkpoint": application["support_checkpoint_id"],
        "claim": source.testimony.claim_id,
    }
    checkpoint = None
    if values["checkpoint"] is not None:
        checkpoint = connection.execute(_CHECKPOINT, values).mappings().one_or_none()
        expected = observation_support_fingerprint(
            assertion_id=source.assertion_id,
            adjudicator_version=application["adjudicator_version"],
            support_state=application["support_state"],
            current_observation_id=application["current_observation_id"],
        )
        if (
            checkpoint is None
            or checkpoint["checkpoint_state"] != "verified"
            or checkpoint["support_state"] != application["support_state"]
            or checkpoint["current_observation_id"] != values["fact"]
            or checkpoint["root_operation_id"] != values["owner"]
            or checkpoint["value_fingerprint"] != expected
        ):
            raise TemporalWriteConflict(
                "observation support checkpoint does not certify this exact assignment"
            )
    if application["support_state"] == "erased":
        if (
            checkpoint is None
            or values["fact"] is not None
            or values["owner"] is not None
        ):
            raise TemporalWriteConflict(
                "erased observation support requires its active verified disposition"
            )
        return None
    if application["support_state"] != "linked":
        raise TemporalWriteConflict(
            "completed observation application has no support disposition"
        )
    target = connection.execute(_TARGET, values).mappings().one_or_none()
    if (
        target is None
        or target["stance"] != "supports"
        or target["doc_id"] != source.testimony.doc_id
        or _canonical_subject(
            connection=connection,
            deployment_id=deployment_id,
            entity_id=target["subject_entity_id"],
        )
        != source.subject_entity_id
    ):
        raise TemporalWriteConflict(
            "observation support has no matching canonical fact and evidence link"
        )
    if checkpoint is not None:
        if (
            checkpoint["root_fact_id"] != values["fact"]
            or checkpoint["root_fact_kind"] != "observation"
            or checkpoint["root_kind"] != "forget_recompute"
            or checkpoint["root_result"] != "applied"
            or checkpoint["root_replay_class"] != "checkpoint_root"
            or checkpoint["root_observation_id"] != values["fact"]
            or checkpoint["supporting_observation_id"] != values["fact"]
            or checkpoint["supporting_kind"] not in ("seed", "evidence")
            or checkpoint["supporting_result"] != "applied"
        ):
            raise TemporalWriteConflict(
                "observation assignment checkpoint names a different fact root"
            )
        # An endpoint root's union of evidence cannot establish this assertion's
        # assignment. Resolve the per-assignment supporting operation instead.
        _require_complete_support_on(
            connection=connection,
            deployment_id=deployment_id,
            operation_id=checkpoint["supporting_operation_id"],
            claim_id=source.testimony.claim_id,
        )
    else:
        original_exists = connection.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM observations WHERE deployment_id=:dep AND observation_id=:original)"
            ),
            {"dep": deployment_id, "original": application["original_observation_id"]},
        ).scalar_one()
        if not original_exists and not _has_accepted_assignment_history_on(
            connection=connection, deployment_id=deployment_id, application=application
        ):
            raise TemporalWriteConflict(
                "original observation disappeared without an assignment checkpoint"
            )
        owner = connection.execute(_OWNER, values).mappings().one_or_none()
        if (
            owner is None
            or owner["observation_id"] != values["fact"]
            or owner["operation_kind"] not in ("seed", "evidence")
            or owner["result"] != "applied"
            or owner["replay_class"] != "ordinary"
            or owner["triggering_assertion_id"] != source.assertion_id
            or owner["triggering_claim_id"] != source.testimony.claim_id
            or not isinstance(owner["features"], dict)
            or owner["features"].get(
                "source_adjudicator_version", owner["policy_generation"]
            )
            != application["adjudicator_version"]
        ):
            raise TemporalWriteConflict(
                "observation support owner does not establish this source assignment"
            )
        _require_complete_support_on(
            connection=connection,
            deployment_id=deployment_id,
            operation_id=application["support_owner_operation_id"],
            claim_id=source.testimony.claim_id,
        )
        payload = owner["features"].get("support_move")
        if payload is None:
            expected_kind = (
                "seed" if application["identity_outcome"] == "new" else "evidence"
            )
            if (
                values["fact"] != application["original_observation_id"]
                or owner["operation_kind"] != expected_kind
                or owner["input_fingerprint"] != application["committed_input_digest"]
                or not connection.execute(_ORIGINAL_MEMBER, values).scalar_one()
            ):
                raise TemporalWriteConflict(
                    "initial support differs from its immutable original receipt"
                )
        else:
            try:
                move = ObservationSupportMove.model_validate(payload)
            except ValidationError as exc:
                raise TemporalWriteConflict(
                    "observation support owner has an invalid move payload"
                ) from exc
            if (
                move.assertion_id != source.assertion_id
                or move.adjudicator_version != application["adjudicator_version"]
                or move.destination_observation_id != values["fact"]
                or move.establishing_operation_id != values["owner"]
                or move.previous_observation_id == move.destination_observation_id
            ):
                raise TemporalWriteConflict(
                    "observation support move names a different assignment"
                )
            dependencies = set(connection.execute(_DEPENDENCIES, values).scalars())
            if not {
                move.previous_support_owner_operation_id,
                move.causal_cap_operation_id,
            }.issubset(dependencies):
                raise TemporalWriteConflict(
                    "observation support move lost its owner or cap dependency"
                )
            cap = (
                connection.execute(
                    text(
                        "SELECT observation_id,operation_kind,result,new_valid_until FROM temporal_operations WHERE deployment_id=:dep AND operation_id=:cap"
                    ),
                    {"dep": deployment_id, "cap": move.causal_cap_operation_id},
                )
                .mappings()
                .one_or_none()
            )
            if cap is None or (
                cap["observation_id"],
                cap["operation_kind"],
                cap["result"],
            ) != (move.previous_observation_id, "cap", "applied"):
                raise TemporalWriteConflict(
                    "observation support move has no applied cap on its previous fact"
                )
            start = observation_bounds(assertion=source).start
            if (
                observation_kind(assertion=source) is not FactTemporalKind.STATE
                or start is None
                or cap["new_valid_until"] is None
                or start < cap["new_valid_until"]
            ):
                raise TemporalWriteConflict(
                    "observation support source is not displaced by its causal world-time cap"
                )
            previous_target = connection.execute(
                text(
                    "SELECT observation_id FROM temporal_operations WHERE deployment_id=:dep AND operation_id=:previous"
                ),
                {
                    "dep": deployment_id,
                    "previous": move.previous_support_owner_operation_id,
                },
            ).scalar_one_or_none()
            if previous_target != move.previous_observation_id:
                raise TemporalWriteConflict(
                    "observation support move's previous owner belongs to another fact"
                )
            if not connection.execute(_COMPLETED_OWNER_GROUP, values).scalar_one():
                raise TemporalWriteConflict(
                    "observation support move has no completed atomic application group"
                )
    return ObservationCurrentSupport(
        assertion=source,
        adjudicator_version=application["adjudicator_version"],
        original_observation_id=application["original_observation_id"],
        current_observation_id=application["current_observation_id"],
        support_owner_operation_id=application["support_owner_operation_id"],
        support_checkpoint_id=application["support_checkpoint_id"],
    )


def _has_accepted_assignment_history_on(
    *, connection: Connection, deployment_id: UUID, application: RowMapping
) -> bool:
    """Recognize sanitized original history after a later move clears the active support checkpoint.

    Historical acceptance can explain a missing original target, but cannot
    certify the current assignment or revive an erased one. Those checks use
    the exact current owner/disposition above.
    """
    rows = connection.execute(
        text("""SELECT s.* FROM temporal_checkpoint_observation_support s
            JOIN temporal_forget_checkpoints c ON c.deployment_id=s.deployment_id AND c.checkpoint_id=s.checkpoint_id
            WHERE s.deployment_id=:dep AND s.assertion_id=:assertion AND s.adjudicator_version=:generation
              AND c.state IN ('verified','superseded') AND c.verified_at >= :completed
              AND s.support_state='linked'"""),
        {
            "dep": deployment_id,
            "assertion": application["assertion_id"],
            "generation": application["adjudicator_version"],
            "completed": application["completed_at"],
        },
    ).mappings()
    return any(
        row["value_fingerprint"]
        == observation_support_fingerprint(
            assertion_id=application["assertion_id"],
            adjudicator_version=application["adjudicator_version"],
            support_state=row["support_state"],
            current_observation_id=row["current_observation_id"],
        )
        for row in rows
    )


def _require_complete_support_on(
    *, connection: Connection, deployment_id: UUID, operation_id: UUID, claim_id: UUID
) -> None:
    """Validate the full semantic premise graph, including cycles and erased transitive support."""
    pending = [(operation_id, False)]
    active: set[UUID] = set()
    verified: set[UUID] = set()
    while pending:
        current, closing = pending.pop()
        if closing:
            active.remove(current)
            verified.add(current)
            continue
        if current in verified:
            continue
        if current in active:
            raise TemporalWriteConflict(
                "observation assignment has cyclic semantic support"
            )
        predecessors = _complete_support_predecessors_on(
            connection=connection,
            deployment_id=deployment_id,
            operation_id=current,
            claim_id=claim_id if current == operation_id else None,
        )
        active.add(current)
        pending.append((current, True))
        pending.extend((predecessor, False) for predecessor in predecessors)


def _complete_support_predecessors_on(
    *,
    connection: Connection,
    deployment_id: UUID,
    operation_id: UUID,
    claim_id: UUID | None,
) -> tuple[UUID, ...]:
    """Recompute one retained inventory and return only its required semantic predecessors."""
    values = {"dep": deployment_id, "owner": operation_id}
    support = (
        connection.execute(
            text(
                "SELECT * FROM temporal_operation_support WHERE deployment_id=:dep AND operation_id=:owner"
            ),
            values,
        )
        .mappings()
        .one_or_none()
    )
    blocks = sorted(
        connection.execute(
            text(
                "SELECT block_key FROM temporal_operation_blocks WHERE deployment_id=:dep AND operation_id=:owner ORDER BY block_key"
            ),
            values,
        ).scalars()
    )
    evidence = tuple(
        TemporalEvidenceRef(
            claim_id=row["claim_id"],
            role=row["evidence_role"],
            was_current=row["was_current"],
            fingerprint=row["evidence_fingerprint"],
        )
        for row in connection.execute(
            text(
                "SELECT * FROM temporal_operation_evidence WHERE deployment_id=:dep AND operation_id=:owner ORDER BY claim_id,evidence_role"
            ),
            values,
        ).mappings()
    )
    predecessors = list(connection.execute(_DEPENDENCIES, values).scalars())
    digest = temporal_fingerprint(
        value={
            "block_keys": blocks,
            "claims": [item.model_dump(mode="json") for item in evidence],
            "semantic_predecessors": sorted(map(str, predecessors)),
        }
    )
    if (
        support is None
        or support["support_state"] != "complete"
        or not support["footprint_complete"]
        or support["expected_block_count"] != len(blocks)
        or not blocks
        or support["expected_claim_count"] != len({item.claim_id for item in evidence})
        or support["expected_semantic_dependency_count"] != len(predecessors)
        or support["support_fingerprint"] != digest
        or (
            claim_id is not None
            and not any(
                item.claim_id == claim_id and item.role == "support"
                for item in evidence
            )
        )
    ):
        raise TemporalWriteConflict(
            "observation assignment has incomplete or altered operation support"
        )
    return tuple(predecessors)


_TARGET = text("""SELECT o.subject_entity_id,e.stance::text,e.doc_id
    FROM observations o JOIN observation_evidence e ON e.deployment_id=o.deployment_id AND e.observation_id=o.observation_id
    WHERE o.deployment_id=:dep AND o.observation_id=:fact AND e.claim_id=:claim""")
_OWNER = text("""SELECT op.*,n.triggering_assertion_id,n.triggering_claim_id,n.features
    FROM temporal_operations op JOIN observation_adjudications n
      ON n.deployment_id=op.deployment_id AND n.temporal_operation_id=op.operation_id
    WHERE op.deployment_id=:dep AND op.operation_id=:owner""")
_ORIGINAL_MEMBER = text("""SELECT EXISTS (SELECT 1 FROM observation_application_adjudications j
    JOIN observation_adjudications n ON n.deployment_id=j.deployment_id AND n.adjudication_id=j.adjudication_id
    WHERE j.deployment_id=:dep AND j.assertion_id=:assertion AND j.adjudicator_version=:generation
      AND n.temporal_operation_id=:owner)""")
_COMPLETED_OWNER_GROUP = text("""SELECT EXISTS (SELECT 1 FROM observation_application_adjudications j
    JOIN observation_applications a ON a.deployment_id=j.deployment_id AND a.assertion_id=j.assertion_id
      AND a.adjudicator_version=j.adjudicator_version AND a.completed_at IS NOT NULL
    JOIN observation_adjudications n ON n.deployment_id=j.deployment_id AND n.adjudication_id=j.adjudication_id
    JOIN temporal_operations op ON op.deployment_id=n.deployment_id AND op.operation_id=n.temporal_operation_id
    WHERE j.deployment_id=:dep AND n.temporal_operation_id=:owner AND j.adjudicator_version=op.policy_generation)""")
_DEPENDENCIES = text("""SELECT predecessor_operation_id FROM temporal_operation_dependencies
    WHERE deployment_id=:dep AND operation_id=:owner AND required_for_semantics ORDER BY predecessor_operation_id""")
_CHECKPOINT = text("""SELECT s.*,c.state AS checkpoint_state,f.fact_kind AS root_fact_kind,
    f.fact_id AS root_fact_id,op.operation_kind AS root_kind,op.result AS root_result,
    op.replay_class AS root_replay_class,op.observation_id AS root_observation_id,
    proof.observation_id AS supporting_observation_id,proof.operation_kind AS supporting_kind,
    proof.result AS supporting_result
    FROM temporal_checkpoint_observation_support s
    JOIN temporal_forget_checkpoints c ON c.deployment_id=s.deployment_id AND c.checkpoint_id=s.checkpoint_id
    LEFT JOIN temporal_checkpoint_facts f ON f.deployment_id=s.deployment_id AND f.checkpoint_id=s.checkpoint_id AND f.root_operation_id=s.root_operation_id
    LEFT JOIN temporal_operations op ON op.deployment_id=f.deployment_id AND op.operation_id=f.root_operation_id
    LEFT JOIN temporal_operations proof ON proof.deployment_id=s.deployment_id AND proof.operation_id=s.supporting_operation_id
    WHERE s.deployment_id=:dep AND s.checkpoint_id=:checkpoint AND s.assertion_id=:assertion AND s.adjudicator_version=:generation""")
