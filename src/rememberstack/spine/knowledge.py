"""Plane-K Postgres control plane: manifests, routing, and staleness (D45).

The inverted key index deliberately narrows work; the typed candidate
manifest decides correctness. Some rule parameters (MIME/origin/time,
keywords, explicit evidence IDs) do not fit the schema's four key kinds, so
they receive exact secondary SQL evaluation instead of invented key types.
"""

from collections.abc import Collection
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from uuid import UUID
from uuid import uuid4

from pydantic import JsonValue
from pydantic import TypeAdapter
from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine import Engine
from sqlalchemy.engine import RowMapping
from sqlalchemy.sql.elements import TextClause

from rememberstack.core import authored_declaration_is_empty
from rememberstack.core import knowledge_citation_reference
from rememberstack.core import knowledge_inputs_hash
from rememberstack.core import knowledge_summary_hash
from rememberstack.core import route_knowledge_plan
from rememberstack.model import CommunityRuleParams
from rememberstack.model import DocSetRuleParams
from rememberstack.model import EnqueueWork
from rememberstack.model import EntityRuleParams
from rememberstack.model import EntitySubtreeRuleParams
from rememberstack.model import KnowledgeAdjustRuleProposal
from rememberstack.model import KnowledgeArtifactCreate
from rememberstack.model import KnowledgeArtifactHash
from rememberstack.model import KnowledgeArtifactPathState
from rememberstack.model import KnowledgeArtifactStatus
from rememberstack.model import KnowledgeAuthoredPageSync
from rememberstack.model import KnowledgeAuthoredPageSyncResult
from rememberstack.model import KnowledgeAuthoredReviewPayload
from rememberstack.model import KnowledgeAuthoredReviewReason
from rememberstack.model import KnowledgeAuthoredReviewState
from rememberstack.model import KnowledgeCandidateLayer
from rememberstack.model import KnowledgeCitation
from rememberstack.model import KnowledgeClaimFingerprint
from rememberstack.model import KnowledgeCompilationFailure
from rememberstack.model import KnowledgeCompilationWrite
from rememberstack.model import KnowledgeCompileArtifact
from rememberstack.model import KnowledgeCompileContext
from rememberstack.model import KnowledgeCompiledContentState
from rememberstack.model import KnowledgeConvertKindProposal
from rememberstack.model import KnowledgeCreatePageProposal
from rememberstack.model import KnowledgeDispatchMaterialization
from rememberstack.model import KnowledgeDispatchRecord
from rememberstack.model import KnowledgeDispatchStatus
from rememberstack.model import KnowledgeEvidenceDelta
from rememberstack.model import KnowledgeFactFingerprint
from rememberstack.model import KnowledgeFactSheetFact
from rememberstack.model import KnowledgeFactSheetSnapshot
from rememberstack.model import KnowledgeInputSnapshot
from rememberstack.model import KnowledgeMergePagesProposal
from rememberstack.model import KnowledgeMovePageProposal
from rememberstack.model import KnowledgeNotificationResult
from rememberstack.model import KnowledgeOrphanAggregate
from rememberstack.model import KnowledgePageKind
from rememberstack.model import KnowledgePageRuleCreate
from rememberstack.model import KnowledgePendingCycle
from rememberstack.model import KnowledgePendingPlanDecision
from rememberstack.model import KnowledgePlanBand
from rememberstack.model import KnowledgePlanDecisionCreate
from rememberstack.model import KnowledgePlanDecisionResult
from rememberstack.model import KnowledgePlannedPage
from rememberstack.model import KnowledgePlannerArtifactState
from rememberstack.model import KnowledgePlanningSnapshot
from rememberstack.model import KnowledgePlanProposal
from rememberstack.model import KnowledgePlanRunStatus
from rememberstack.model import KnowledgePlanRunWrite
from rememberstack.model import KnowledgePlanStatus
from rememberstack.model import KnowledgePlanTrigger
from rememberstack.model import KnowledgeQuarantineRecord
from rememberstack.model import KnowledgeQuarantineStatus
from rememberstack.model import KnowledgeRetirePageProposal
from rememberstack.model import KnowledgeRuleConfiguration
from rememberstack.model import KnowledgeRuleKey
from rememberstack.model import KnowledgeRuleKeyKind
from rememberstack.model import KnowledgeRuleKind
from rememberstack.model import KnowledgeRuleParams
from rememberstack.model import KnowledgeSplitPageProposal
from rememberstack.model import KnowledgeSubscriptionCreate
from rememberstack.model import KnowledgeWriterBundle
from rememberstack.model import KnowledgeWriterClaim
from rememberstack.model import KnowledgeWriterClaimGroup
from rememberstack.model import KnowledgeWriterFactReference
from rememberstack.model import KnowledgeWriterSuggestion
from rememberstack.model import ManualRuleParams
from rememberstack.model import merge_authored_review_payloads
from rememberstack.model import PipelineStage
from rememberstack.model import PredicateBeatRuleParams
from rememberstack.model import ProcessingTarget
from rememberstack.model import ScopeInterestsRuleParams
from rememberstack.spine.work_ledger import enqueue_on

_RULE_ADAPTER = TypeAdapter(KnowledgeRuleParams)
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, JsonValue])
_PLAN_PROPOSAL_ADAPTER = TypeAdapter(KnowledgePlanProposal)


class KnowledgeCompileContextMissingError(LookupError):
    """A compiled artifact lacks its current git/model hash inputs."""


class KnowledgeCompilationError(ValueError):
    """A compilation transcript violates the control-plane contract."""


class KnowledgeCommitBusyError(RuntimeError):
    """Another process already owns this deployment's K commit cycle."""


class KnowledgeDispatchUnavailableError(RuntimeError):
    """A materialized dispatch targets a paused or retired subscription."""


class KnowledgeControlPlane:
    """Own the deterministic Plane-K state kept in Postgres."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the control plane to the authoritative spine."""
        self._engine = engine

    def record_plan_decision(self, *, decision: KnowledgePlanDecisionCreate) -> None:
        """Append one planner structure decision without applying it implicitly."""
        with self._engine.begin() as connection:
            connection.execute(
                _INSERT_PLAN_DECISION,
                {
                    **decision.model_dump(mode="python", exclude={"payload"}),
                    "payload": decision.model_dump(mode="json")["payload"],
                },
            )

    def record_plan_run_failure(self, *, run: KnowledgePlanRunWrite) -> None:
        """Append one failed planner/reflection session with its complete traceback."""
        if run.status is not KnowledgePlanRunStatus.FAILED:
            raise KnowledgeCompilationError(
                "plan failure ledger requires failed status"
            )
        with self._engine.begin() as connection:
            connection.execute(_INSERT_PLAN_RUN, run.model_dump(mode="python"))

    def record_plan_proposals(
        self,
        *,
        run: KnowledgePlanRunWrite,
        proposals: Sequence[KnowledgePlanProposal],
        auto_apply_max_expected_impact: Decimal,
    ) -> tuple[KnowledgePlanDecisionResult, ...]:
        """Record one successful run and atomically route/apply all of its proposals."""
        if run.status is not KnowledgePlanRunStatus.SUCCEEDED:
            raise KnowledgeCompilationError(
                "successful plan batch requires succeeded run"
            )
        results: list[KnowledgePlanDecisionResult] = []
        with self._engine.begin() as connection:
            connection.execute(_INSERT_PLAN_RUN, run.model_dump(mode="python"))
            for proposal in proposals:
                self._validate_proposal_scope(
                    connection=connection,
                    deployment_id=run.deployment_id,
                    scope_id=run.scope_id,
                    proposal=proposal,
                )
                blast_radius = self._plan_blast_radius(
                    connection=connection,
                    deployment_id=run.deployment_id,
                    proposal=proposal,
                )
                band, expected_impact = route_knowledge_plan(
                    proposal=proposal,
                    run_kind=run.run_kind,
                    blast_radius=blast_radius,
                    auto_apply_max_expected_impact=auto_apply_max_expected_impact,
                )
                decision_id = uuid4()
                status = (
                    KnowledgePlanStatus.APPLIED
                    if band is KnowledgePlanBand.AUTO_APPLY
                    else KnowledgePlanStatus.PROPOSED
                )
                connection.execute(
                    _INSERT_ROUTED_PLAN_DECISION,
                    {
                        "decision_id": decision_id,
                        "deployment_id": run.deployment_id,
                        "scope_id": run.scope_id,
                        "action": proposal.action.value,
                        "payload": proposal.model_dump(mode="json"),
                        "trigger": run.trigger.value,
                        "planner_version": run.component_version,
                        "status": status.value,
                        "plan_run_id": run.run_id,
                        "confidence": proposal.confidence,
                        "blast_radius": blast_radius,
                        "expected_impact": expected_impact,
                    },
                )
                if status is KnowledgePlanStatus.APPLIED:
                    self._apply_plan_proposal(
                        connection=connection,
                        decision_id=decision_id,
                        deployment_id=run.deployment_id,
                        proposal=proposal,
                    )
                results.append(
                    KnowledgePlanDecisionResult(
                        decision_id=decision_id,
                        action=proposal.action,
                        status=status,
                        band=band,
                        blast_radius=blast_radius,
                        expected_impact=expected_impact,
                    )
                )
        return tuple(results)

    def accept_plan_decision(
        self, *, decision_id: UUID, reviewed_by: str, author_confirmed: bool = False
    ) -> None:
        """Apply one review-band proposal, requiring confirmation for handover."""
        if not reviewed_by:
            raise KnowledgeCompilationError("reviewed_by must be non-empty")
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    _SELECT_PLAN_DECISION_FOR_REVIEW, {"decision_id": decision_id}
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["status"] != KnowledgePlanStatus.PROPOSED.value:
                raise KnowledgeCompilationError("plan decision is not reviewable")
            if row["open_quarantine"]:
                raise KnowledgeCompilationError(
                    "quarantine decisions require an explicit quarantine resolution"
                )
            proposal = _PLAN_PROPOSAL_ADAPTER.validate_python(row["payload"])
            if (
                isinstance(proposal, KnowledgeConvertKindProposal)
                and proposal.to_kind is KnowledgePageKind.COMPILED
                and not author_confirmed
            ):
                raise KnowledgeCompilationError(
                    "authored-to-compiled handover requires author confirmation"
                )
            self._apply_plan_proposal(
                connection=connection,
                decision_id=decision_id,
                deployment_id=row["deployment_id"],
                proposal=proposal,
            )
            connection.execute(
                _ACCEPT_PLAN_DECISION,
                {"decision_id": decision_id, "reviewed_by": reviewed_by},
            )

    def reject_plan_decision(self, *, decision_id: UUID, reviewed_by: str) -> None:
        """Reject one review-band proposal without mutating Plane-K structure."""
        if not reviewed_by:
            raise KnowledgeCompilationError("reviewed_by must be non-empty")
        with self._engine.begin() as connection:
            rejected = connection.execute(
                _REJECT_PLAN_DECISION,
                {"decision_id": decision_id, "reviewed_by": reviewed_by},
            ).scalar_one_or_none()
            if rejected is None:
                raise KnowledgeCompilationError("plan decision is not reviewable")

    def quarantine_compiled_edit(
        self,
        *,
        artifact_id: UUID,
        detected_content_hash: str,
        edited_markdown: str,
        driver_version: str,
    ) -> KnowledgeQuarantineRecord:
        """Preserve a direct compiled-body edit and exclude the page from compilation."""
        if not edited_markdown or not driver_version:
            raise KnowledgeCompilationError("quarantine input must be non-empty")
        with self._engine.begin() as connection:
            existing = (
                connection.execute(
                    _SELECT_OPEN_QUARANTINE, {"artifact_id": artifact_id}
                )
                .mappings()
                .one_or_none()
            )
            if existing is not None:
                if existing["detected_content_hash"] != detected_content_hash:
                    raise KnowledgeCompilationError(
                        "quarantined body changed again before triage"
                    )
                return KnowledgeQuarantineRecord.model_validate(dict(existing))
            artifact = (
                connection.execute(
                    _SELECT_COMPILED_CONTENT_FOR_UPDATE, {"artifact_id": artifact_id}
                )
                .mappings()
                .one_or_none()
            )
            if artifact is None or artifact["content_hash"] is None:
                raise KnowledgeCompilationError(
                    "only previously compiled active/stale pages can be quarantined"
                )
            if artifact["content_hash"] == detected_content_hash:
                raise KnowledgeCompilationError("compiled body has not changed")
            proposal = KnowledgeConvertKindProposal(
                artifact_id=artifact_id,
                from_kind=KnowledgePageKind.COMPILED,
                to_kind=KnowledgePageKind.AUTHORED,
                rationale="A direct body edit crossed the compiled-page ownership boundary.",
                confidence=Decimal("1"),
            )
            decision_id = uuid4()
            quarantine_id = uuid4()
            blast_radius = self._artifact_impact(
                connection=connection,
                deployment_id=artifact["deployment_id"],
                artifact_id=artifact_id,
            )
            connection.execute(
                _INSERT_ROUTED_PLAN_DECISION,
                {
                    "decision_id": decision_id,
                    "deployment_id": artifact["deployment_id"],
                    "scope_id": artifact["scope_id"],
                    "action": proposal.action.value,
                    "payload": proposal.model_dump(mode="json"),
                    "trigger": KnowledgePlanTrigger.HUMAN.value,
                    "planner_version": driver_version,
                    "status": KnowledgePlanStatus.PROPOSED.value,
                    "plan_run_id": None,
                    "confidence": proposal.confidence,
                    "blast_radius": blast_radius,
                    "expected_impact": Decimal("0"),
                },
            )
            row = (
                connection.execute(
                    _INSERT_QUARANTINE,
                    {
                        "quarantine_id": quarantine_id,
                        "decision_id": decision_id,
                        "deployment_id": artifact["deployment_id"],
                        "artifact_id": artifact_id,
                        "recorded_content_hash": artifact["content_hash"],
                        "detected_content_hash": detected_content_hash,
                        "proposed_sidecar_entry": edited_markdown,
                    },
                )
                .mappings()
                .one()
            )
            connection.execute(_MARK_QUARANTINED, {"artifact_id": artifact_id})
            return KnowledgeQuarantineRecord.model_validate(dict(row))

    def adopt_quarantined_page(self, *, quarantine_id: UUID, reviewed_by: str) -> None:
        """Resolve a quarantined direct edit by transferring the page to its author."""
        if not reviewed_by:
            raise KnowledgeCompilationError("reviewed_by must be non-empty")
        with self._engine.begin() as connection:
            row, proposal = self._open_quarantine_proposal(
                connection=connection, quarantine_id=quarantine_id
            )
            connection.execute(
                _ACKNOWLEDGE_QUARANTINED_BODY,
                {
                    "artifact_id": row["artifact_id"],
                    "content_hash": row["detected_content_hash"],
                },
            )
            self._apply_plan_proposal(
                connection=connection,
                decision_id=row["decision_id"],
                deployment_id=row["deployment_id"],
                proposal=proposal,
            )
            connection.execute(
                _ACCEPT_PLAN_DECISION,
                {"decision_id": row["decision_id"], "reviewed_by": reviewed_by},
            )
            connection.execute(
                _RESOLVE_QUARANTINE,
                {
                    "quarantine_id": quarantine_id,
                    "status": KnowledgeQuarantineStatus.ADOPTED.value,
                    "resolution_note": "compiled page adopted as authored",
                    "curation_content_hash": None,
                },
            )

    def accept_quarantine_to_curation(
        self,
        *,
        quarantine_id: UUID,
        curation_markdown: str,
        curation_content_hash: str,
        reviewed_by: str,
    ) -> None:
        """Resume compilation only after git curation contains the preserved edit."""
        if not reviewed_by or not curation_content_hash:
            raise KnowledgeCompilationError(
                "curation resolution fields must be non-empty"
            )
        with self._engine.begin() as connection:
            row, _ = self._open_quarantine_proposal(
                connection=connection, quarantine_id=quarantine_id
            )
            if row["proposed_sidecar_entry"] not in curation_markdown:
                raise KnowledgeCompilationError(
                    "curation sidecar does not contain the quarantined edit"
                )
            connection.execute(
                _RESOLVE_REJECT_PLAN_DECISION,
                {"decision_id": row["decision_id"], "reviewed_by": reviewed_by},
            )
            connection.execute(
                _CLEAR_QUARANTINED_BODY_IDENTITY, {"artifact_id": row["artifact_id"]}
            )
            connection.execute(
                _RESUME_QUARANTINED_AS_STALE, {"artifact_id": row["artifact_id"]}
            )
            connection.execute(
                _RESOLVE_QUARANTINE,
                {
                    "quarantine_id": quarantine_id,
                    "status": KnowledgeQuarantineStatus.CURATION_ACCEPTED.value,
                    "resolution_note": "direct edit accepted into curation sidecar",
                    "curation_content_hash": curation_content_hash,
                },
            )

    def reject_quarantined_edit(self, *, quarantine_id: UUID, reviewed_by: str) -> None:
        """Reject one direct edit and return its compiled page to the stale queue."""
        if not reviewed_by:
            raise KnowledgeCompilationError("reviewed_by must be non-empty")
        with self._engine.begin() as connection:
            row, _ = self._open_quarantine_proposal(
                connection=connection, quarantine_id=quarantine_id
            )
            connection.execute(
                _RESOLVE_REJECT_PLAN_DECISION,
                {"decision_id": row["decision_id"], "reviewed_by": reviewed_by},
            )
            connection.execute(
                _CLEAR_QUARANTINED_BODY_IDENTITY, {"artifact_id": row["artifact_id"]}
            )
            connection.execute(
                _RESUME_QUARANTINED_AS_STALE, {"artifact_id": row["artifact_id"]}
            )
            connection.execute(
                _RESOLVE_QUARANTINE,
                {
                    "quarantine_id": quarantine_id,
                    "status": KnowledgeQuarantineStatus.REJECTED.value,
                    "resolution_note": "direct edit rejected",
                    "curation_content_hash": None,
                },
            )

    def create_artifact(self, *, artifact: KnowledgeArtifactCreate) -> None:
        """Register one git path in the K control plane."""
        with self._engine.begin() as connection:
            connection.execute(
                _INSERT_ARTIFACT,
                {
                    **artifact.model_dump(mode="python", exclude={"artifact_kind"}),
                    "kind": artifact.artifact_kind,
                },
            )

    def add_page_rule(self, *, rule: KnowledgePageRuleCreate) -> None:
        """Persist a typed page rule and materialize its coarse keys atomically."""
        params = _stored_params(params=rule.params)
        with self._engine.begin() as connection:
            connection.execute(
                _INSERT_PAGE_RULE,
                {
                    "rule_id": rule.rule_id,
                    "deployment_id": rule.deployment_id,
                    "artifact_id": rule.artifact_id,
                    "plan_decision_id": rule.plan_decision_id,
                    "rule_kind": rule.params.kind.value,
                    "params": params,
                },
            )
            self._replace_rule_keys(
                connection=connection,
                deployment_id=rule.deployment_id,
                rule_id=rule.rule_id,
                params=rule.params,
            )
            connection.execute(_MARK_STALE, {"artifact_id": rule.artifact_id})

    def rematerialize_rule_keys(self, *, rule_id: UUID) -> tuple[KnowledgeRuleKey, ...]:
        """Rebuild one rule's inverted keys from current authoritative state."""
        with self._engine.begin() as connection:
            row = (
                connection.execute(_SELECT_RULE, {"rule_id": rule_id}).mappings().one()
            )
            params = _parse_rule(row=row)
            return self._replace_rule_keys(
                connection=connection,
                deployment_id=row["deployment_id"],
                rule_id=rule_id,
                params=params,
            )

    def rematerialize_derived_rule_keys(
        self,
        *,
        deployment_id: UUID,
        kinds: tuple[KnowledgeRuleKind, ...] = (
            KnowledgeRuleKind.ENTITY_SUBTREE,
            KnowledgeRuleKind.COMMUNITY,
            KnowledgeRuleKind.SCOPE_INTERESTS,
        ),
    ) -> None:
        """Refresh subtree, community, and scope expansions after their inputs move."""
        with self._engine.begin() as connection:
            rows = connection.execute(
                _SELECT_DERIVED_RULES, {"deployment_id": deployment_id}
            ).mappings()
            for row in rows:
                if KnowledgeRuleKind(str(row["rule_kind"])) not in kinds:
                    continue
                self._replace_rule_keys(
                    connection=connection,
                    deployment_id=deployment_id,
                    rule_id=row["rule_id"],
                    params=_parse_rule(row=row),
                )

    def input_snapshot(
        self, *, artifact_id: UUID, context: KnowledgeCompileContext
    ) -> KnowledgeInputSnapshot:
        """Evaluate one artifact's active-rule union into its exact D45 manifest."""
        with self._engine.connect() as connection:
            return self._input_snapshot(
                connection=connection, artifact_id=artifact_id, context=context
            )

    def fact_sheet_snapshot(
        self,
        *,
        artifact_id: UUID,
        context: KnowledgeCompileContext,
        child_summary_hashes: tuple[str, ...] | None = None,
    ) -> KnowledgeFactSheetSnapshot:
        """Hydrate an artifact's exact rule-selected facts in one repeatable read."""
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                return self._fact_sheet_snapshot(
                    connection=connection,
                    artifact_id=artifact_id,
                    context=context,
                    child_summary_hashes=child_summary_hashes,
                )

    def writer_bundle(
        self,
        *,
        artifact_id: UUID,
        context: KnowledgeCompileContext,
        child_summary_hashes: tuple[str, ...] | None = None,
    ) -> KnowledgeWriterBundle:
        """Hydrate exact facts and current claim bodies in one repeatable read."""
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                fact_sheet = self._fact_sheet_snapshot(
                    connection=connection,
                    artifact_id=artifact_id,
                    context=context,
                    child_summary_hashes=child_summary_hashes,
                )
                claim_groups = self._writer_claim_groups(
                    connection=connection, fact_sheet=fact_sheet
                )
                return KnowledgeWriterBundle(
                    fact_sheet=fact_sheet,
                    claim_groups=claim_groups,
                    claim_candidate_count=len(fact_sheet.input_snapshot.claims),
                    claims_cut_count=0,
                )

    def artifact_hash(
        self, *, artifact_id: UUID, context: KnowledgeCompileContext
    ) -> KnowledgeArtifactHash:
        """Return the recorded and current hash for one compiled artifact."""
        with self._engine.connect() as connection:
            row = (
                connection.execute(_SELECT_ARTIFACT_HASH, {"artifact_id": artifact_id})
                .mappings()
                .one()
            )
            snapshot = self._input_snapshot(
                connection=connection, artifact_id=artifact_id, context=context
            )
        return KnowledgeArtifactHash(
            artifact_id=artifact_id,
            recorded_hash=row["inputs_hash"],
            computed_hash=knowledge_inputs_hash(snapshot=snapshot),
        )

    def stale_artifacts(
        self,
        *,
        deployment_id: UUID,
        contexts: Mapping[UUID, KnowledgeCompileContext],
        artifact_ids: tuple[UUID, ...] | None = None,
    ) -> tuple[KnowledgeArtifactHash, ...]:
        """Return exactly the compiled pages whose complete manifest changed."""
        if artifact_ids == ():
            return ()
        statement = (
            _SELECT_FILTERED_COMPILED_ARTIFACTS
            if artifact_ids is not None
            else _SELECT_COMPILED_ARTIFACTS
        )
        with self._engine.connect() as connection:
            rows = connection.execute(
                statement,
                {
                    "deployment_id": deployment_id,
                    "artifact_ids": list(artifact_ids or ()),
                },
            ).mappings()
            stale: list[KnowledgeArtifactHash] = []
            for row in rows:
                artifact_id = row["artifact_id"]
                context = contexts.get(artifact_id)
                if context is None:
                    raise KnowledgeCompileContextMissingError(str(artifact_id))
                computed = knowledge_inputs_hash(
                    snapshot=self._input_snapshot(
                        connection=connection, artifact_id=artifact_id, context=context
                    )
                )
                if computed != row["inputs_hash"]:
                    stale.append(
                        KnowledgeArtifactHash(
                            artifact_id=artifact_id,
                            recorded_hash=row["inputs_hash"],
                            computed_hash=computed,
                        )
                    )
        return tuple(stale)

    def mark_stale(
        self, *, artifacts: Sequence[KnowledgeArtifactHash]
    ) -> tuple[UUID, ...]:
        """Mark manifest-mismatched compiled pages stale, idempotently."""
        if not artifacts:
            return ()
        marked: list[UUID] = []
        with self._engine.begin() as connection:
            for artifact in artifacts:
                artifact_id = connection.execute(
                    _MARK_STALE, {"artifact_id": artifact.artifact_id}
                ).scalar_one_or_none()
                if artifact_id is not None:
                    marked.append(artifact_id)
        return tuple(marked)

    def route_delta(
        self, *, deployment_id: UUID, delta: KnowledgeEvidenceDelta
    ) -> tuple[UUID, ...]:
        """Narrow an evidence delta to candidate artifacts using keys and citations.

        Scope-interest rules are included as a deliberate coarse fallback because
        the schema has no entity-type/metadata/keyword key kinds. Manual rules are
        checked by their explicit IDs. The caller must recompute manifests before
        marking anything stale; a coarse route is never a correctness verdict.
        """
        derived_kinds: list[KnowledgeRuleKind] = []
        if delta.community_ids:
            derived_kinds.append(KnowledgeRuleKind.COMMUNITY)
        if delta.relation_ids and self._delta_contains_part_of(
            deployment_id=deployment_id, relation_ids=delta.relation_ids
        ):
            derived_kinds.append(KnowledgeRuleKind.ENTITY_SUBTREE)
        if derived_kinds:
            self.rematerialize_derived_rule_keys(
                deployment_id=deployment_id, kinds=tuple(derived_kinds)
            )
        with self._engine.connect() as connection:
            keys = self._delta_keys(
                connection=connection, deployment_id=deployment_id, delta=delta
            )
            artifact_ids: set[UUID] = set()
            for key in keys:
                artifact_ids.update(
                    connection.execute(
                        _SELECT_ARTIFACTS_FOR_KEY,
                        {
                            "deployment_id": deployment_id,
                            "key_kind": key.kind.value,
                            "key_value": key.value,
                        },
                    ).scalars()
                )
            artifact_ids.update(
                self._citation_artifacts(
                    connection=connection, deployment_id=deployment_id, delta=delta
                )
            )
            artifact_ids.update(
                connection.execute(
                    _SELECT_SCOPE_RULE_ARTIFACTS, {"deployment_id": deployment_id}
                ).scalars()
            )
            artifact_ids.update(
                self._manual_artifacts(
                    connection=connection, deployment_id=deployment_id, delta=delta
                )
            )
        return tuple(sorted(artifact_ids, key=str))

    def planning_snapshot(
        self,
        *,
        deployment_id: UUID,
        scope_id: UUID | None,
        delta: KnowledgeEvidenceDelta,
        page_sizes: Mapping[UUID, int],
        page_size_limit_bytes: int,
    ) -> KnowledgePlanningSnapshot:
        """Build exact structural triggers and health metrics for one scope."""
        if page_size_limit_bytes <= 0 or any(size < 0 for size in page_sizes.values()):
            raise KnowledgeCompilationError("planner page-size inputs must be positive")
        with self._engine.connect().execution_options(
            isolation_level="REPEATABLE READ"
        ) as connection:
            with connection.begin():
                rows = tuple(
                    connection.execute(
                        _SELECT_PLANNER_ARTIFACTS,
                        {"deployment_id": deployment_id, "scope_id": scope_id},
                    ).mappings()
                )
                artifact_ids = {row["artifact_id"] for row in rows}
                unknown_sizes = set(page_sizes).difference(artifact_ids)
                if unknown_sizes:
                    raise KnowledgeCompilationError(
                        "planner page sizes include an artifact outside the scope"
                    )
                artifacts = tuple(
                    KnowledgePlannerArtifactState(
                        **{
                            key: row[key]
                            for key in (
                                "artifact_id",
                                "layer",
                                "page_kind",
                                "status",
                                "git_path",
                                "scope_id",
                                "parent_artifact_id",
                                "artifact_kind",
                                "candidate_count",
                                "uncited_count",
                            )
                        },
                        page_size_bytes=page_sizes.get(row["artifact_id"], 0),
                    )
                    for row in rows
                )
                covered = self._scope_candidate_keys(
                    connection=connection,
                    deployment_id=deployment_id,
                    scope_id=scope_id,
                )
                orphan_rows = connection.execute(
                    _SELECT_DELTA_CANDIDATE_ENTITIES,
                    {
                        "deployment_id": deployment_id,
                        "relation_ids": [str(value) for value in delta.relation_ids],
                        "observation_ids": [
                            str(value) for value in delta.observation_ids
                        ],
                        "claim_ids": [str(value) for value in delta.claim_ids],
                        "doc_ids": [str(value) for value in delta.doc_ids],
                    },
                ).mappings()
                orphan_keys: dict[UUID, set[str]] = {}
                for row in orphan_rows:
                    candidate_key = str(row["candidate_key"])
                    if candidate_key in covered:
                        continue
                    orphan_keys.setdefault(row["entity_id"], set()).add(candidate_key)
                orphan_aggregates = tuple(
                    KnowledgeOrphanAggregate(
                        entity_id=entity_id, candidate_keys=tuple(candidate_keys)
                    )
                    for entity_id, candidate_keys in sorted(
                        orphan_keys.items(), key=lambda item: str(item[0])
                    )
                )
                suggestions = tuple(
                    KnowledgeWriterSuggestion.model_validate(suggestion)
                    for row in rows
                    for suggestion in row["suggestions"]
                )
        return KnowledgePlanningSnapshot(
            deployment_id=deployment_id,
            scope_id=scope_id,
            artifacts=artifacts,
            orphan_aggregates=orphan_aggregates,
            overflow_artifact_ids=tuple(
                artifact.artifact_id
                for artifact in artifacts
                if artifact.page_kind is KnowledgePageKind.COMPILED
                and artifact.page_size_bytes > page_size_limit_bytes
            ),
            community_ids=delta.community_ids,
            writer_suggestions=suggestions,
        )

    def _delta_contains_part_of(
        self, *, deployment_id: UUID, relation_ids: tuple[UUID, ...]
    ) -> bool:
        """Return whether a changed relation can alter subtree membership."""
        with self._engine.connect() as connection:
            return bool(
                connection.execute(
                    _SELECT_PART_OF_DELTA,
                    {
                        "deployment_id": deployment_id,
                        "relation_ids": list(relation_ids),
                    },
                ).scalar_one()
            )

    @contextmanager
    def commit_lease(self, *, deployment_id: UUID) -> Iterator[None]:
        """Hold the deployment-scoped Postgres advisory lock for one K cycle."""
        with self._engine.connect() as connection:
            acquired = bool(
                connection.execute(
                    _TRY_COMMIT_LEASE, {"deployment_id": deployment_id}
                ).scalar_one()
            )
            connection.commit()
            if not acquired:
                raise KnowledgeCommitBusyError(str(deployment_id))
            try:
                yield
            finally:
                released = bool(
                    connection.execute(
                        _RELEASE_COMMIT_LEASE, {"deployment_id": deployment_id}
                    ).scalar_one()
                )
                connection.commit()
                if not released:
                    raise KnowledgeCompilationError("Plane-K commit lease was lost")

    def compile_artifacts(
        self, *, deployment_id: UUID
    ) -> tuple[KnowledgeCompileArtifact, ...]:
        """Return the deployment's schedulable compiled-page graph."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_COMPILE_ARTIFACTS, {"deployment_id": deployment_id}
            ).mappings()
            return tuple(
                KnowledgeCompileArtifact.model_validate(dict(row)) for row in rows
            )

    def artifact_git_paths(self, *, deployment_id: UUID) -> tuple[str, ...]:
        """Return every live artifact path accepted by internal-link validation."""
        with self._engine.connect() as connection:
            return tuple(
                connection.execute(
                    _SELECT_ARTIFACT_GIT_PATHS, {"deployment_id": deployment_id}
                ).scalars()
            )

    def artifact_path_states(
        self, *, deployment_id: UUID
    ) -> tuple[KnowledgeArtifactPathState, ...]:
        """Return body and curation paths used to classify checkout Markdown files."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_ARTIFACT_PATH_STATES, {"deployment_id": deployment_id}
            ).mappings()
            return tuple(
                KnowledgeArtifactPathState.model_validate(dict(row)) for row in rows
            )

    def sync_authored_page(
        self, *, sync: KnowledgeAuthoredPageSync
    ) -> KnowledgeAuthoredPageSyncResult:
        """Atomically register/sync one authored body and its declared ground."""
        observed_hash = sha256(sync.markdown.encode("utf-8")).hexdigest()
        if observed_hash != sync.content_hash:
            raise KnowledgeCompilationError(
                "authored content hash does not match the supplied Markdown"
            )
        with self._engine.begin() as connection:
            artifact = (
                connection.execute(
                    _SELECT_ARTIFACT_BY_PATH_FOR_UPDATE,
                    {"deployment_id": sync.deployment_id, "git_path": sync.git_path},
                )
                .mappings()
                .one_or_none()
            )
            registered = artifact is None
            if registered:
                artifact_id = uuid4()
                connection.execute(
                    _INSERT_AUTHORED_ARTIFACT,
                    {
                        "artifact_id": artifact_id,
                        "deployment_id": sync.deployment_id,
                        "layer": sync.layer.value,
                        "git_path": sync.git_path,
                    },
                )
                prior_hash = None
            else:
                if (
                    artifact["page_kind"] != KnowledgePageKind.AUTHORED.value
                    or artifact["status"] == KnowledgeArtifactStatus.TOMBSTONED.value
                ):
                    raise KnowledgeCompilationError(
                        "authored sync path is owned by a non-live compiled artifact"
                    )
                artifact_id = artifact["artifact_id"]
                prior_hash = artifact["content_hash"]
            content_changed = prior_hash != sync.content_hash
            declaration = sync.declaration
            if declaration.citations is not None:
                self._validate_citations(
                    connection=connection,
                    deployment_id=sync.deployment_id,
                    citations=declaration.citations,
                )
                self._replace_authored_citations(
                    connection=connection,
                    deployment_id=sync.deployment_id,
                    artifact_id=artifact_id,
                    citations=declaration.citations,
                )
            if declaration.watch_rules is not None:
                self._replace_authored_rules(
                    connection=connection,
                    deployment_id=sync.deployment_id,
                    artifact_id=artifact_id,
                    git_revision=sync.git_revision,
                    rules=declaration.watch_rules,
                )
            if declaration.watched_page_paths is not None:
                watched_ids = self._resolve_watched_paths(
                    connection=connection,
                    deployment_id=sync.deployment_id,
                    paths=declaration.watched_page_paths,
                )
                connection.execute(
                    _DELETE_ARTIFACT_PAGE_WATCHES, {"artifact_id": artifact_id}
                )
                for watched_id in watched_ids:
                    connection.execute(
                        _INSERT_ARTIFACT_PAGE_WATCH,
                        {
                            "watch_id": uuid4(),
                            "deployment_id": sync.deployment_id,
                            "watcher_artifact_id": artifact_id,
                            "watched_artifact_id": watched_id,
                        },
                    )
            if content_changed:
                connection.execute(
                    _RESOLVE_AUTHORED_FLAGS, {"artifact_id": artifact_id}
                )
            counts = (
                connection.execute(
                    _SELECT_AUTHORED_DECLARATION_COUNTS, {"artifact_id": artifact_id}
                )
                .mappings()
                .one()
            )
            lint_flagged = authored_declaration_is_empty(
                citation_count=int(counts["citation_count"]),
                watch_rule_count=int(counts["watch_rule_count"]),
                page_watch_count=int(counts["page_watch_count"]),
            )
            if lint_flagged:
                self._upsert_authored_flag(
                    connection=connection,
                    deployment_id=sync.deployment_id,
                    artifact_id=artifact_id,
                    payload=KnowledgeAuthoredReviewPayload(
                        reasons=(KnowledgeAuthoredReviewReason.DECLARATION_MISSING,)
                    ),
                )
            else:
                self._resolve_declaration_lint(
                    connection=connection, artifact_id=artifact_id
                )
            connection.execute(
                _UPDATE_AUTHORED_CONTENT_HASH,
                {"artifact_id": artifact_id, "content_hash": sync.content_hash},
            )
        return KnowledgeAuthoredPageSyncResult(
            artifact_id=artifact_id,
            registered=registered,
            content_changed=content_changed,
            lint_flagged=lint_flagged,
        )

    def register_subscription(
        self, *, subscription: KnowledgeSubscriptionCreate
    ) -> None:
        """Register one endpoint with its rule/page-watch union atomically."""
        if not subscription.rules and not subscription.watched_page_paths:
            raise KnowledgeCompilationError(
                "knowledge subscription requires at least one rule or page watch"
            )
        with self._engine.begin() as connection:
            connection.execute(
                _INSERT_SUBSCRIPTION,
                subscription.model_dump(
                    mode="python", exclude={"rules", "watched_page_paths"}
                ),
            )
            for params in subscription.rules:
                rule_id = uuid4()
                connection.execute(
                    _INSERT_SUBSCRIPTION_RULE,
                    {
                        "rule_id": rule_id,
                        "deployment_id": subscription.deployment_id,
                        "subscription_id": subscription.subscription_id,
                        "rule_kind": params.kind.value,
                        "params": _stored_params(params=params),
                    },
                )
                self._replace_rule_keys(
                    connection=connection,
                    deployment_id=subscription.deployment_id,
                    rule_id=rule_id,
                    params=params,
                )
            watched_ids = self._resolve_watched_paths(
                connection=connection,
                deployment_id=subscription.deployment_id,
                paths=subscription.watched_page_paths,
            )
            for watched_id in watched_ids:
                connection.execute(
                    _INSERT_SUBSCRIPTION_PAGE_WATCH,
                    {
                        "watch_id": uuid4(),
                        "deployment_id": subscription.deployment_id,
                        "subscription_id": subscription.subscription_id,
                        "watched_artifact_id": watched_id,
                    },
                )

    def route_notifications(
        self,
        *,
        deployment_id: UUID,
        delta: KnowledgeEvidenceDelta,
        tombstone: bool = False,
    ) -> KnowledgeNotificationResult:
        """Route one evidence delta exactly to authored flags and subscriber batches."""
        derived_kinds: list[KnowledgeRuleKind] = []
        if delta.community_ids:
            derived_kinds.append(KnowledgeRuleKind.COMMUNITY)
        if delta.relation_ids and self._delta_contains_part_of(
            deployment_id=deployment_id, relation_ids=delta.relation_ids
        ):
            derived_kinds.append(KnowledgeRuleKind.ENTITY_SUBTREE)
        if derived_kinds:
            self.rematerialize_derived_rule_keys(
                deployment_id=deployment_id, kinds=tuple(derived_kinds)
            )
        reason = (
            KnowledgeAuthoredReviewReason.TOMBSTONE
            if tombstone
            else KnowledgeAuthoredReviewReason.EVIDENCE_CHANGED
        )
        payload = KnowledgeAuthoredReviewPayload(
            reasons=(reason,), delta=delta, redaction_required=tombstone
        )
        with self._engine.begin() as connection:
            candidate_rules = self._notification_candidate_rules(
                connection=connection, deployment_id=deployment_id, delta=delta
            )
            authored_ids = self._authored_citation_artifacts(
                connection=connection, deployment_id=deployment_id, delta=delta
            )
            subscription_ids: set[UUID] = set()
            for row in candidate_rules:
                if not self._rule_matches_delta(
                    connection=connection,
                    deployment_id=deployment_id,
                    params=_parse_rule(row=row),
                    delta=delta,
                ):
                    continue
                if row["artifact_id"] is not None:
                    authored_ids.add(row["artifact_id"])
                else:
                    subscription_ids.add(row["subscription_id"])
            for artifact_id in authored_ids:
                self._upsert_authored_flag(
                    connection=connection,
                    deployment_id=deployment_id,
                    artifact_id=artifact_id,
                    payload=payload,
                )
            dispatch_ids = tuple(
                self._upsert_dispatch(
                    connection=connection,
                    deployment_id=deployment_id,
                    subscription_id=subscription_id,
                    payload=payload,
                )
                for subscription_id in sorted(subscription_ids, key=str)
            )
        return KnowledgeNotificationResult(
            authored_artifact_ids=tuple(sorted(authored_ids, key=str)),
            dispatch_ids=dispatch_ids,
        )

    def authored_review_state(
        self, *, artifact_id: UUID
    ) -> KnowledgeAuthoredReviewState:
        """Return every unresolved review payload visible to authored-page readers."""
        with self._engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    _SELECT_AUTHORED_REVIEW_STATE, {"artifact_id": artifact_id}
                ).mappings()
            )
        payloads = tuple(
            KnowledgeAuthoredReviewPayload.model_validate(row["payload"])
            for row in rows
        )
        return KnowledgeAuthoredReviewState(
            artifact_id=artifact_id,
            open_flag_count=len(payloads),
            redaction_required=any(item.redaction_required for item in payloads),
            payloads=payloads,
        )

    def materialize_due_dispatches(
        self, *, deployment_id: UUID, component_version: str
    ) -> tuple[KnowledgeDispatchMaterialization, ...]:
        """Materialize due subscriber batches onto the generic unlaned D67 worker."""
        if not component_version:
            raise KnowledgeCompilationError(
                "dispatch component version must be non-empty"
            )
        materialized: list[KnowledgeDispatchMaterialization] = []
        with self._engine.begin() as connection:
            rows = tuple(
                connection.execute(
                    _SELECT_DUE_DISPATCHES_FOR_UPDATE, {"deployment_id": deployment_id}
                ).mappings()
            )
            for row in rows:
                dispatch_id = row["dispatch_id"]
                content_hash = sha256(
                    KnowledgeAuthoredReviewPayload.model_validate(row["payload"])
                    .model_dump_json()
                    .encode("utf-8")
                ).hexdigest()
                outcome = enqueue_on(
                    connection=connection,
                    work=EnqueueWork(
                        deployment_id=deployment_id,
                        target_kind=ProcessingTarget.KNOWLEDGE_DISPATCH,
                        target_id=dispatch_id,
                        stage=PipelineStage.DISPATCH_KNOWLEDGE,
                        component_version=component_version,
                        content_hash=content_hash,
                        lane=None,
                        payload={"dispatch_id": str(dispatch_id)},
                    ),
                )
                materialized.append(
                    KnowledgeDispatchMaterialization(
                        dispatch_id=dispatch_id,
                        processing_id=outcome.processing_id,
                        created=outcome.created,
                    )
                )
        return tuple(materialized)

    def begin_dispatch(self, *, dispatch_id: UUID) -> KnowledgeDispatchRecord:
        """Claim the domain dispatch for a running generic-worker attempt."""
        unavailable = False
        with self._engine.begin() as connection:
            row = (
                connection.execute(
                    _SELECT_DISPATCH_FOR_UPDATE, {"dispatch_id": dispatch_id}
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise KnowledgeCompilationError("knowledge dispatch does not exist")
            if (
                row["status"] != KnowledgeDispatchStatus.DONE.value
                and row["subscription_status"] != "active"
            ):
                connection.execute(
                    _REJECT_UNAVAILABLE_DISPATCH, {"dispatch_id": dispatch_id}
                )
                unavailable = True
            elif row["status"] != KnowledgeDispatchStatus.DONE.value:
                connection.execute(_MARK_DISPATCH_RUNNING, {"dispatch_id": dispatch_id})
            record = KnowledgeDispatchRecord(
                dispatch_id=dispatch_id,
                deployment_id=row["deployment_id"],
                subscription_id=row["subscription_id"],
                workflow_endpoint=row["workflow_endpoint"],
                payload=KnowledgeAuthoredReviewPayload.model_validate(row["payload"]),
                status=(
                    KnowledgeDispatchStatus.DONE
                    if row["status"] == KnowledgeDispatchStatus.DONE.value
                    else KnowledgeDispatchStatus.RUNNING
                ),
            )
        if unavailable:
            raise KnowledgeDispatchUnavailableError(
                "knowledge subscription is not active"
            )
        return record

    def complete_dispatch(self, *, dispatch_id: UUID) -> None:
        """Mirror successful external delivery in the append-only dispatch ledger."""
        with self._engine.begin() as connection:
            updated = connection.execute(
                _COMPLETE_DISPATCH, {"dispatch_id": dispatch_id}
            ).rowcount
            if updated == 0:
                raise KnowledgeCompilationError("knowledge dispatch is not running")

    def fail_dispatch(self, *, dispatch_id: UUID) -> None:
        """Mirror a visible failed delivery attempt while D67 owns retry details."""
        with self._engine.begin() as connection:
            updated = connection.execute(
                _FAIL_DISPATCH, {"dispatch_id": dispatch_id}
            ).rowcount
            if updated == 0:
                raise KnowledgeCompilationError("knowledge dispatch is not running")

    def compiled_content_states(
        self, *, deployment_id: UUID
    ) -> tuple[KnowledgeCompiledContentState, ...]:
        """Return accepted hashes for compiled bodies eligible for drift detection."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_COMPILED_CONTENT_STATES, {"deployment_id": deployment_id}
            ).mappings()
            return tuple(
                KnowledgeCompiledContentState.model_validate(dict(row)) for row in rows
            )

    def pending_plan_decisions(
        self, *, deployment_id: UUID
    ) -> tuple[KnowledgePendingPlanDecision, ...]:
        """Return applied structure decisions whose git reconciliation is unfinished."""
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_PENDING_PLAN_DECISIONS, {"deployment_id": deployment_id}
            ).mappings()
            decisions: list[KnowledgePendingPlanDecision] = []
            for row in rows:
                proposal = _PLAN_PROPOSAL_ADAPTER.validate_python(row["payload"])
                artifact_ids = _proposal_artifact_ids(proposal=proposal)
                paths = (
                    {}
                    if not artifact_ids
                    else {
                        artifact_id: git_path
                        for artifact_id, git_path in connection.execute(
                            _SELECT_PLAN_DECISION_ARTIFACT_PATHS,
                            {
                                "deployment_id": deployment_id,
                                "artifact_ids": list(artifact_ids),
                            },
                        ).tuples()
                    }
                )
                decisions.append(
                    KnowledgePendingPlanDecision(
                        decision_id=row["decision_id"],
                        proposal=proposal,
                        decided_at=row["decided_at"],
                        artifact_paths=paths,
                    )
                )
            return tuple(decisions)

    def stamp_ready_plan_decisions(
        self, *, deployment_id: UUID, git_commit: str, present_paths: Collection[str]
    ) -> tuple[UUID, ...]:
        """Bind structurally and physically complete decisions to one git revision."""
        if not git_commit:
            raise KnowledgeCompilationError("plan application commit must be non-empty")
        paths = set(present_paths)
        stamped: list[UUID] = []
        with self._engine.begin() as connection:
            rows = tuple(
                connection.execute(
                    _SELECT_PENDING_PLAN_DECISIONS_FOR_UPDATE,
                    {"deployment_id": deployment_id},
                ).mappings()
            )
            for row in rows:
                proposal = _PLAN_PROPOSAL_ADAPTER.validate_python(row["payload"])
                if not self._plan_decision_ready(
                    connection=connection,
                    deployment_id=deployment_id,
                    decision_id=row["decision_id"],
                    decided_at=row["decided_at"],
                    proposal=proposal,
                    present_paths=paths,
                ):
                    continue
                connection.execute(
                    _STAMP_PLAN_DECISION,
                    {
                        "decision_id": row["decision_id"],
                        "application_commit": git_commit,
                    },
                )
                stamped.append(row["decision_id"])
        return tuple(stamped)

    def validate_citations(
        self, *, deployment_id: UUID, citations: tuple[KnowledgeCitation, ...]
    ) -> None:
        """Reject unknown or cross-deployment writer citation targets before publish."""
        with self._engine.connect() as connection:
            self._validate_citations(
                connection=connection,
                deployment_id=deployment_id,
                citations=_unique_citations(citations=citations),
            )

    def record_failed_compilation(
        self, *, failure: KnowledgeCompilationFailure
    ) -> None:
        """Append one terminal writer failure without changing live page state."""
        with self._engine.begin() as connection:
            inserted = connection.execute(
                _INSERT_FAILED_COMPILATION, failure.model_dump(mode="python")
            ).scalar_one_or_none()
            if inserted is None:
                raise KnowledgeCompilationError(
                    "failed compilation target is not an active/stale compiled artifact"
                )

    def pending_cycles(
        self, *, deployment_id: UUID
    ) -> tuple[KnowledgePendingCycle, ...]:
        """Rehydrate every unfailed, uncommitted cycle from durable finalize payloads."""
        grouped: dict[UUID, list[KnowledgeCompilationWrite]] = {}
        with self._engine.connect() as connection:
            rows = connection.execute(
                _SELECT_PENDING_COMPILATIONS, {"deployment_id": deployment_id}
            ).mappings()
            for row in rows:
                cycle_id = row["cycle_id"]
                grouped.setdefault(cycle_id, []).append(
                    KnowledgeCompilationWrite.model_validate(
                        {
                            key: row[key]
                            for key in (
                                "compilation_id",
                                "deployment_id",
                                "artifact_id",
                                "inputs_hash",
                                "candidate_count",
                                "uncited_count",
                                "claims_cut_count",
                                "citations",
                                "suggestions",
                                "evidence_invalidated",
                                "writer_version",
                                "page_summary",
                                "content_hash",
                                "tokens",
                                "cost_usd",
                                "session_transcript_uri",
                            )
                        }
                    )
                )
        return tuple(
            KnowledgePendingCycle(
                cycle_id=cycle_id,
                deployment_id=deployment_id,
                compilations=tuple(compilations),
            )
            for cycle_id, compilations in grouped.items()
        )

    def fail_pending_cycle(
        self, *, deployment_id: UUID, cycle_id: UUID, failure: str
    ) -> None:
        """Record an unpublished or unverifiable cycle without changing live pages."""
        if not failure:
            raise KnowledgeCompilationError("pending-cycle failure must be non-empty")
        with self._engine.begin() as connection:
            connection.execute(
                _FAIL_PENDING_CYCLE,
                {
                    "deployment_id": deployment_id,
                    "cycle_id": cycle_id,
                    "failure": failure,
                },
            )

    def record_pending_compilation(
        self, *, compilation: KnowledgeCompilationWrite
    ) -> None:
        """Record a validated compilation before git push without changing live state.

        ``git_commit IS NULL`` is the schema's pending marker. The currently
        committed page, citations, and artifact hash remain untouched so a
        failed push leaves the previous page internally consistent (D45 §6).
        """
        self.record_pending_compilations(
            cycle_id=compilation.compilation_id, compilations=(compilation,)
        )

    def record_pending_compilations(
        self, *, cycle_id: UUID, compilations: Sequence[KnowledgeCompilationWrite]
    ) -> None:
        """Record a complete publish batch atomically without changing live state."""
        if not compilations:
            raise KnowledgeCompilationError("pending cycle requires at least one page")
        deployment_ids = {item.deployment_id for item in compilations}
        artifact_ids = {item.artifact_id for item in compilations}
        compilation_ids = {item.compilation_id for item in compilations}
        if len(deployment_ids) != 1:
            raise KnowledgeCompilationError("pending cycle crosses deployments")
        if len(artifact_ids) != len(compilations):
            raise KnowledgeCompilationError("pending cycle repeats an artifact")
        if len(compilation_ids) != len(compilations):
            raise KnowledgeCompilationError("pending cycle repeats a compilation ID")
        with self._engine.begin() as connection:
            for compilation in compilations:
                citations = _unique_citations(citations=compilation.citations)
                self._validate_citations(
                    connection=connection,
                    deployment_id=compilation.deployment_id,
                    citations=citations,
                )
                prior = set(
                    connection.execute(
                        _SELECT_CITATIONS, {"artifact_id": compilation.artifact_id}
                    ).tuples()
                )
                current = {_citation_tuple(citation=item) for item in citations}
                connection.execute(
                    _INSERT_COMPILATION,
                    {
                        **compilation.model_dump(
                            mode="python",
                            exclude={
                                "citations",
                                "suggestions",
                                "page_summary",
                                "content_hash",
                            },
                        ),
                        "cycle_id": cycle_id,
                        "citations": [
                            item.model_dump(mode="json") for item in citations
                        ],
                        "suggestions": [
                            item.model_dump(mode="json")
                            for item in compilation.suggestions
                        ],
                        "page_summary": compilation.page_summary,
                        "content_hash": compilation.content_hash,
                        "cited_count": (
                            compilation.candidate_count - compilation.uncited_count
                        ),
                        "evidence_added": len(current - prior),
                        "evidence_removed": len(prior - current),
                    },
                )

    def commit_compilation(
        self, *, compilation: KnowledgeCompilationWrite, git_commit: str
    ) -> None:
        """Publish pending citations/artifact state after the git push succeeds."""
        self.commit_compilations(compilations=(compilation,), git_commit=git_commit)

    def commit_compilations(
        self, *, compilations: Sequence[KnowledgeCompilationWrite], git_commit: str
    ) -> None:
        """Finalize every page in one published cycle atomically and idempotently."""
        if not git_commit:
            raise KnowledgeCompilationError("git commit must be non-empty")
        if not compilations:
            raise KnowledgeCompilationError("commit cycle requires at least one page")
        if len({item.compilation_id for item in compilations}) != len(compilations):
            raise KnowledgeCompilationError("commit cycle repeats a compilation ID")
        if len({item.artifact_id for item in compilations}) != len(compilations):
            raise KnowledgeCompilationError("commit cycle repeats an artifact")
        if len({item.deployment_id for item in compilations}) != 1:
            raise KnowledgeCompilationError("commit cycle crosses deployments")
        with self._engine.begin() as connection:
            to_finalize: list[
                tuple[KnowledgeCompilationWrite, tuple[KnowledgeCitation, ...]]
            ] = []
            cycle_ids: set[UUID] = set()
            for compilation in compilations:
                citations = _unique_citations(citations=compilation.citations)
                pending = (
                    connection.execute(
                        _SELECT_COMPILATION_STATE,
                        {"compilation_id": compilation.compilation_id},
                    )
                    .mappings()
                    .one_or_none()
                )
                if pending is None:
                    raise KnowledgeCompilationError(
                        "pending compilation does not exist"
                    )
                cycle_ids.add(pending["cycle_id"])
                if pending["git_commit"] is not None:
                    if pending["git_commit"] == git_commit:
                        continue
                    raise KnowledgeCompilationError(
                        "compilation is already bound to a different git commit"
                    )
                if pending["failed_at"] is not None:
                    raise KnowledgeCompilationError("pending compilation was abandoned")
                stored_citations = _unique_citations(
                    citations=tuple(
                        KnowledgeCitation.model_validate(item)
                        for item in pending["citations"]
                    )
                )
                stored_suggestions = tuple(
                    KnowledgeWriterSuggestion.model_validate(item)
                    for item in pending["suggestions"]
                )
                if (
                    pending["deployment_id"] != compilation.deployment_id
                    or pending["artifact_id"] != compilation.artifact_id
                    or pending["inputs_hash"] != compilation.inputs_hash
                    or pending["writer_version"] != compilation.writer_version
                    or pending["candidate_count"] != compilation.candidate_count
                    or pending["cited_count"]
                    != compilation.candidate_count - compilation.uncited_count
                    or pending["uncited_count"] != compilation.uncited_count
                    or pending["claims_cut_count"] != compilation.claims_cut_count
                    or pending["evidence_invalidated"]
                    != compilation.evidence_invalidated
                    or pending["page_summary"] != compilation.page_summary
                    or pending["content_hash"] != compilation.content_hash
                    or stored_suggestions != compilation.suggestions
                    or tuple(
                        _citation_tuple(citation=item) for item in stored_citations
                    )
                    != tuple(_citation_tuple(citation=item) for item in citations)
                ):
                    raise KnowledgeCompilationError(
                        "pending compilation does not match the finalize payload"
                    )
                self._validate_citations(
                    connection=connection,
                    deployment_id=compilation.deployment_id,
                    citations=citations,
                )
                to_finalize.append((compilation, citations))

            if len(cycle_ids) != 1:
                raise KnowledgeCompilationError("commit payload crosses pending cycles")
            cycle_id = next(iter(cycle_ids))
            if cycle_id is not None:
                stored_compilation_ids = set(
                    connection.execute(
                        _SELECT_CYCLE_COMPILATION_IDS,
                        {
                            "deployment_id": compilations[0].deployment_id,
                            "cycle_id": cycle_id,
                        },
                    ).scalars()
                )
                requested_compilation_ids = {
                    compilation.compilation_id for compilation in compilations
                }
                if stored_compilation_ids != requested_compilation_ids:
                    raise KnowledgeCompilationError(
                        "commit payload does not contain the complete pending cycle"
                    )

            for compilation, citations in to_finalize:
                prior_citations = tuple(
                    KnowledgeCitation(
                        role=row[0],
                        claim_lineage_id=row[1],
                        claim_chunk_content_hash=row[2],
                        relation_id=row[3],
                        doc_id=row[4],
                    )
                    for row in connection.execute(
                        _SELECT_CITATIONS, {"artifact_id": compilation.artifact_id}
                    ).tuples()
                )
                prior_set = set(prior_citations)
                current_set = set(citations)
                connection.execute(
                    _DELETE_CITATIONS, {"artifact_id": compilation.artifact_id}
                )
                for citation in citations:
                    connection.execute(
                        _INSERT_CITATION,
                        {
                            "evidence_link_id": uuid4(),
                            "deployment_id": compilation.deployment_id,
                            "artifact_id": compilation.artifact_id,
                            **citation.model_dump(mode="python"),
                        },
                    )
                updated = connection.execute(
                    _UPDATE_COMPILED_ARTIFACT,
                    {
                        "artifact_id": compilation.artifact_id,
                        "inputs_hash": compilation.inputs_hash,
                        "writer_version": compilation.writer_version,
                        "page_summary": compilation.page_summary,
                        "content_hash": compilation.content_hash,
                    },
                ).scalar_one_or_none()
                if updated is None:
                    raise KnowledgeCompilationError(
                        "compilation target is not an active/stale compiled artifact"
                    )
                connection.execute(
                    _STAMP_COMPILATION_COMMIT,
                    {
                        "compilation_id": compilation.compilation_id,
                        "git_commit": git_commit,
                    },
                )
                self._notify_page_watchers(
                    connection=connection,
                    deployment_id=compilation.deployment_id,
                    watched_artifact_id=compilation.artifact_id,
                    citations_added=tuple(
                        knowledge_citation_reference(citation=citation)
                        for citation in sorted(
                            current_set.difference(prior_set),
                            key=lambda item: _citation_tuple(citation=item),
                        )
                    ),
                    citations_removed=tuple(
                        knowledge_citation_reference(citation=citation)
                        for citation in sorted(
                            prior_set.difference(current_set),
                            key=lambda item: _citation_tuple(citation=item),
                        )
                    ),
                    evidence_invalidated=compilation.evidence_invalidated,
                )

    def _replace_authored_citations(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        artifact_id: UUID,
        citations: tuple[KnowledgeCitation, ...],
    ) -> None:
        """Replace frontmatter-owned citations inside the authored sync transaction."""
        connection.execute(_DELETE_CITATIONS, {"artifact_id": artifact_id})
        for citation in citations:
            connection.execute(
                _INSERT_CITATION,
                {
                    "evidence_link_id": uuid4(),
                    "deployment_id": deployment_id,
                    "artifact_id": artifact_id,
                    **citation.model_dump(mode="python"),
                },
            )

    def _replace_authored_rules(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        artifact_id: UUID,
        git_revision: str,
        rules: tuple[KnowledgeRuleParams, ...],
    ) -> None:
        """Replace authored watch rules with a human-origin structure transcript."""
        self._retire_rules(connection=connection, artifact_id=artifact_id)
        if not rules:
            return
        decision_id = uuid4()
        proposal = KnowledgeAdjustRuleProposal(
            artifact_id=artifact_id,
            rules=rules,
            rationale="synced from authored page frontmatter",
            confidence=Decimal("1"),
        )
        connection.execute(
            _INSERT_AUTHORED_RULE_DECISION,
            {
                "decision_id": decision_id,
                "deployment_id": deployment_id,
                "payload": proposal.model_dump(mode="json"),
                "application_commit": git_revision,
            },
        )
        for params in rules:
            rule_id = uuid4()
            connection.execute(
                _INSERT_PAGE_RULE,
                {
                    "rule_id": rule_id,
                    "deployment_id": deployment_id,
                    "artifact_id": artifact_id,
                    "plan_decision_id": decision_id,
                    "rule_kind": params.kind.value,
                    "params": _stored_params(params=params),
                },
            )
            self._replace_rule_keys(
                connection=connection,
                deployment_id=deployment_id,
                rule_id=rule_id,
                params=params,
            )

    def _resolve_watched_paths(
        self, *, connection: Connection, deployment_id: UUID, paths: tuple[str, ...]
    ) -> tuple[UUID, ...]:
        """Resolve exact live compiled page-watch paths or reject the declaration."""
        if not paths:
            return ()
        resolved = {
            path: artifact_id
            for path, artifact_id in connection.execute(
                _SELECT_WATCHED_PATHS,
                {"deployment_id": deployment_id, "paths": list(paths)},
            ).tuples()
        }
        missing = set(paths).difference(resolved)
        if missing:
            raise KnowledgeCompilationError(
                f"page watch targets no live compiled artifact: {sorted(missing)!r}"
            )
        return tuple(resolved[path] for path in paths)

    def _notification_candidate_rules(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        delta: KnowledgeEvidenceDelta,
    ) -> tuple[RowMapping, ...]:
        """Narrow notification rules through their inverted keys before exact matching."""
        rules: dict[UUID, RowMapping] = {}
        for key in self._delta_keys(
            connection=connection, deployment_id=deployment_id, delta=delta
        ):
            for row in connection.execute(
                _SELECT_NOTIFICATION_RULES_FOR_KEY,
                {
                    "deployment_id": deployment_id,
                    "key_kind": key.kind.value,
                    "key_value": key.value,
                },
            ).mappings():
                rules[row["rule_id"]] = row
        for row in connection.execute(
            _SELECT_NOTIFICATION_FALLBACK_RULES, {"deployment_id": deployment_id}
        ).mappings():
            rules[row["rule_id"]] = row
        return tuple(rules[key] for key in sorted(rules, key=str))

    def _rule_matches_delta(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        params: KnowledgeRuleParams,
        delta: KnowledgeEvidenceDelta,
    ) -> bool:
        """Apply the rule's full secondary filters to one narrowed evidence delta."""
        if isinstance(params, CommunityRuleParams) and params.community_id in set(
            delta.community_ids
        ):
            return True
        if isinstance(params, ManualRuleParams) and (
            set(params.relation_ids).intersection(delta.relation_ids)
            or set(params.observation_ids).intersection(delta.observation_ids)
            or set(params.claim_ids).intersection(delta.claim_ids)
            or set(params.doc_ids).intersection(delta.doc_ids)
        ):
            return True
        facts, claims = self._candidates_for_rule(
            connection=connection, deployment_id=deployment_id, params=params
        )
        changed_facts = {
            *(("relation", value) for value in delta.relation_ids),
            *(("observation", value) for value in delta.observation_ids),
        }
        changed_facts.update(
            (fact.kind, fact.fact_id)
            for fact in self._facts_for_claim_ids(
                connection=connection,
                deployment_id=deployment_id,
                claim_ids=delta.claim_ids,
            )
        )
        changed_claims = {
            (claim.lineage_id, claim.chunk_content_hash)
            for claim in self._claims_for_ids(
                connection=connection,
                deployment_id=deployment_id,
                claim_ids=delta.claim_ids,
            )
        }
        if delta.doc_ids:
            doc_facts, doc_claims = self._document_candidates(
                connection=connection,
                deployment_id=deployment_id,
                doc_ids=delta.doc_ids,
            )
            changed_facts.update((fact.kind, fact.fact_id) for fact in doc_facts)
            changed_claims.update(
                (claim.lineage_id, claim.chunk_content_hash) for claim in doc_claims
            )
        return bool(
            {(fact.kind, fact.fact_id) for fact in facts}.intersection(changed_facts)
            or {
                (claim.lineage_id, claim.chunk_content_hash) for claim in claims
            }.intersection(changed_claims)
        )

    def _authored_citation_artifacts(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        delta: KnowledgeEvidenceDelta,
    ) -> set[UUID]:
        """Find live authored pages whose declared citation coordinates changed."""
        artifact_ids: set[UUID] = set()
        if delta.claim_ids:
            artifact_ids.update(
                connection.execute(
                    _SELECT_AUTHORED_CITATIONS_FOR_CLAIMS,
                    {
                        "deployment_id": deployment_id,
                        "claim_ids": list(delta.claim_ids),
                    },
                ).scalars()
            )
        for column, values in (
            ("relation_id", delta.relation_ids),
            ("doc_id", delta.doc_ids),
        ):
            if values:
                artifact_ids.update(
                    connection.execute(
                        _authored_citation_lookup(column=column),
                        {"deployment_id": deployment_id, "evidence_ids": list(values)},
                    ).scalars()
                )
        return artifact_ids

    def _upsert_authored_flag(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        artifact_id: UUID,
        payload: KnowledgeAuthoredReviewPayload,
    ) -> UUID:
        """Merge one standing authored-review flag while holding the owner lock."""
        connection.execute(_LOCK_ARTIFACT, {"artifact_id": artifact_id}).one()
        row = (
            connection.execute(
                _SELECT_OPEN_AUTHORED_FLAG_FOR_UPDATE,
                {"deployment_id": deployment_id, "artifact_id": artifact_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            refresh_id = uuid4()
            connection.execute(
                _INSERT_AUTHORED_FLAG,
                {
                    "refresh_id": refresh_id,
                    "deployment_id": deployment_id,
                    "artifact_id": artifact_id,
                    "payload": payload.model_dump(mode="json"),
                },
            )
            return refresh_id
        merged = merge_authored_review_payloads(
            left=KnowledgeAuthoredReviewPayload.model_validate(row["payload"]),
            right=payload,
        )
        connection.execute(
            _UPDATE_AUTHORED_FLAG,
            {
                "refresh_id": row["refresh_id"],
                "payload": merged.model_dump(mode="json"),
            },
        )
        return row["refresh_id"]

    def _resolve_declaration_lint(
        self, *, connection: Connection, artifact_id: UUID
    ) -> None:
        """Remove only the declaration-lint reason from a possibly merged open flag."""
        row = (
            connection.execute(
                _SELECT_OPEN_AUTHORED_FLAG_BY_ARTIFACT_FOR_UPDATE,
                {"artifact_id": artifact_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return
        payload = KnowledgeAuthoredReviewPayload.model_validate(row["payload"])
        reasons = tuple(
            reason
            for reason in payload.reasons
            if reason is not KnowledgeAuthoredReviewReason.DECLARATION_MISSING
        )
        if not reasons:
            connection.execute(
                _RESOLVE_AUTHORED_FLAG, {"refresh_id": row["refresh_id"]}
            )
            return
        connection.execute(
            _UPDATE_AUTHORED_FLAG,
            {
                "refresh_id": row["refresh_id"],
                "payload": payload.model_copy(update={"reasons": reasons}).model_dump(
                    mode="json"
                ),
            },
        )

    def _upsert_dispatch(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        subscription_id: UUID,
        payload: KnowledgeAuthoredReviewPayload,
    ) -> UUID:
        """Merge one subscription's still-debouncing pending dispatch batch."""
        connection.execute(
            _LOCK_SUBSCRIPTION, {"subscription_id": subscription_id}
        ).one()
        row = (
            connection.execute(
                _SELECT_PENDING_DISPATCH_FOR_UPDATE,
                {"deployment_id": deployment_id, "subscription_id": subscription_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            dispatch_id = uuid4()
            connection.execute(
                _INSERT_DISPATCH,
                {
                    "dispatch_id": dispatch_id,
                    "deployment_id": deployment_id,
                    "subscription_id": subscription_id,
                    "payload": payload.model_dump(mode="json"),
                },
            )
            return dispatch_id
        merged = merge_authored_review_payloads(
            left=KnowledgeAuthoredReviewPayload.model_validate(row["payload"]),
            right=payload,
        )
        connection.execute(
            _UPDATE_PENDING_DISPATCH,
            {
                "dispatch_id": row["dispatch_id"],
                "payload": merged.model_dump(mode="json"),
            },
        )
        return row["dispatch_id"]

    def _notify_page_watchers(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        watched_artifact_id: UUID,
        citations_added: tuple[str, ...],
        citations_removed: tuple[str, ...],
        evidence_invalidated: int,
    ) -> None:
        """Route one committed compilation's exact citation delta to page watchers."""
        rows = tuple(
            connection.execute(
                _SELECT_PAGE_WATCHERS,
                {
                    "deployment_id": deployment_id,
                    "watched_artifact_id": watched_artifact_id,
                },
            ).mappings()
        )
        if not rows:
            return
        payload = KnowledgeAuthoredReviewPayload(
            reasons=(KnowledgeAuthoredReviewReason.PAGE_RECOMPILED,),
            page_refs=(str(rows[0]["watched_git_path"]),),
            citations_added=citations_added,
            citations_removed=citations_removed,
            evidence_invalidated=evidence_invalidated,
        )
        authored_ids = sorted(
            {
                row["watcher_artifact_id"]
                for row in rows
                if row["watcher_artifact_id"] is not None
            },
            key=str,
        )
        subscription_ids = sorted(
            {
                row["subscription_id"]
                for row in rows
                if row["subscription_id"] is not None
            },
            key=str,
        )
        for artifact_id in authored_ids:
            self._upsert_authored_flag(
                connection=connection,
                deployment_id=deployment_id,
                artifact_id=artifact_id,
                payload=payload,
            )
        for subscription_id in subscription_ids:
            self._upsert_dispatch(
                connection=connection,
                deployment_id=deployment_id,
                subscription_id=subscription_id,
                payload=payload,
            )

    def _validate_proposal_scope(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        scope_id: UUID | None,
        proposal: KnowledgePlanProposal,
    ) -> None:
        """Reject cross-deployment/scope structural mutations before banding."""
        pages: tuple[KnowledgePlannedPage, ...] = ()
        if isinstance(proposal, KnowledgeCreatePageProposal):
            pages = (proposal.page,)
        elif isinstance(proposal, KnowledgeSplitPageProposal):
            pages = proposal.pages
        elif isinstance(proposal, KnowledgeMergePagesProposal):
            pages = (proposal.page,)
        if any(page.scope_id != scope_id for page in pages):
            raise KnowledgeCompilationError("planned page crosses the run scope")
        artifact_ids = set(_proposal_artifact_ids(proposal=proposal))
        artifact_ids.update(
            page.parent_artifact_id
            for page in pages
            if page.parent_artifact_id is not None
        )
        if (
            isinstance(proposal, KnowledgeMovePageProposal)
            and proposal.new_parent_artifact_id is not None
        ):
            artifact_ids.add(proposal.new_parent_artifact_id)
        if not artifact_ids:
            return
        rows = tuple(
            connection.execute(
                _SELECT_PLAN_ARTIFACT_SCOPES,
                {"deployment_id": deployment_id, "artifact_ids": list(artifact_ids)},
            ).mappings()
        )
        if len(rows) != len(artifact_ids) or any(
            row["scope_id"] != scope_id for row in rows
        ):
            raise KnowledgeCompilationError("plan proposal crosses deployment or scope")

    def _plan_decision_ready(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        decision_id: UUID,
        decided_at: datetime,
        proposal: KnowledgePlanProposal,
        present_paths: set[str],
    ) -> bool:
        """Check that an accepted DB mutation and its files are both complete."""
        existing = {
            row["artifact_id"]: row
            for row in connection.execute(
                _SELECT_PLAN_EFFECT_TARGETS,
                {
                    "deployment_id": deployment_id,
                    "artifact_ids": list(_proposal_artifact_ids(proposal=proposal)),
                },
            ).mappings()
        }
        created = tuple(
            connection.execute(
                _SELECT_PLAN_CREATED_ARTIFACTS,
                {"deployment_id": deployment_id, "decision_id": decision_id},
            ).mappings()
        )

        def compiled(row: RowMapping) -> bool:
            last_compiled_at = row["last_compiled_at"]
            return (
                row["page_kind"] == KnowledgePageKind.COMPILED.value
                and row["status"] == KnowledgeArtifactStatus.ACTIVE.value
                and last_compiled_at is not None
                and last_compiled_at >= decided_at
                and row["git_path"] in present_paths
            )

        def authored(row: RowMapping) -> bool:
            return (
                row["page_kind"] == KnowledgePageKind.AUTHORED.value
                and row["status"] == KnowledgeArtifactStatus.ACTIVE.value
                and row["git_path"] in present_paths
            )

        if isinstance(proposal, KnowledgeCreatePageProposal):
            return len(created) == 1 and compiled(created[0])
        if isinstance(proposal, KnowledgeSplitPageProposal):
            source = existing.get(proposal.source_artifact_id)
            return (
                source is not None
                and compiled(source)
                and len(created) == len(proposal.pages)
                and all(compiled(row) for row in created)
            )
        if isinstance(proposal, KnowledgeMergePagesProposal):
            return (
                len(created) == 1
                and compiled(created[0])
                and all(
                    (row := existing.get(artifact_id)) is not None
                    and row["status"] == KnowledgeArtifactStatus.TOMBSTONED.value
                    and row["git_path"] not in present_paths
                    for artifact_id in proposal.source_artifact_ids
                )
            )
        target_id = proposal.artifact_id
        target = existing.get(target_id)
        if target is None:
            return False
        if isinstance(proposal, KnowledgeMovePageProposal):
            return (
                (compiled(target) or authored(target))
                and target["git_path"] == proposal.new_git_path
                and proposal.new_git_path in present_paths
                and (
                    proposal.old_git_path == proposal.new_git_path
                    or proposal.old_git_path not in present_paths
                )
                and target["curation_path"] == proposal.new_curation_path
                and (
                    (
                        proposal.new_curation_path in present_paths
                        and (
                            proposal.old_curation_path == proposal.new_curation_path
                            or proposal.old_curation_path not in present_paths
                        )
                    )
                    or (
                        proposal.new_curation_path not in present_paths
                        and proposal.old_curation_path not in present_paths
                    )
                )
            )
        if isinstance(proposal, KnowledgeRetirePageProposal):
            return (
                target["status"] == KnowledgeArtifactStatus.TOMBSTONED.value
                and target["git_path"] not in present_paths
            )
        if isinstance(proposal, KnowledgeAdjustRuleProposal):
            return compiled(target) or authored(target)
        if proposal.to_kind is KnowledgePageKind.AUTHORED:
            return (
                target["page_kind"] == KnowledgePageKind.AUTHORED.value
                and target["status"] == KnowledgeArtifactStatus.ACTIVE.value
                and target["git_path"] in present_paths
            )
        return compiled(target) and bool(
            target["curation_path"] and target["curation_path"] in present_paths
        )

    def _plan_blast_radius(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        proposal: KnowledgePlanProposal,
    ) -> int:
        """Estimate affected candidates/pages mechanically from current committed state."""
        if isinstance(proposal, KnowledgeCreatePageProposal):
            return 1
        if isinstance(proposal, KnowledgeSplitPageProposal):
            return self._artifact_impact(
                connection=connection,
                deployment_id=deployment_id,
                artifact_id=proposal.source_artifact_id,
            ) + len(proposal.pages)
        if isinstance(proposal, KnowledgeMergePagesProposal):
            return 1 + sum(
                self._artifact_impact(
                    connection=connection,
                    deployment_id=deployment_id,
                    artifact_id=artifact_id,
                )
                for artifact_id in proposal.source_artifact_ids
            )
        if isinstance(proposal, KnowledgeMovePageProposal):
            descendants = int(
                connection.execute(
                    _SELECT_DESCENDANT_COUNT,
                    {
                        "deployment_id": deployment_id,
                        "artifact_id": proposal.artifact_id,
                    },
                ).scalar_one()
            )
            return (
                self._artifact_impact(
                    connection=connection,
                    deployment_id=deployment_id,
                    artifact_id=proposal.artifact_id,
                )
                + descendants
            )
        artifact_id = _proposal_artifact_ids(proposal=proposal)[0]
        return self._artifact_impact(
            connection=connection, deployment_id=deployment_id, artifact_id=artifact_id
        )

    def _artifact_impact(
        self, *, connection: Connection, deployment_id: UUID, artifact_id: UUID
    ) -> int:
        """Return at least one unit for a live artifact and its latest candidate set."""
        value = connection.execute(
            _SELECT_ARTIFACT_IMPACT,
            {"deployment_id": deployment_id, "artifact_id": artifact_id},
        ).scalar_one_or_none()
        if value is None:
            raise KnowledgeCompilationError("plan proposal targets an unknown artifact")
        return max(1, int(value))

    def _apply_plan_proposal(
        self,
        *,
        connection: Connection,
        decision_id: UUID,
        deployment_id: UUID,
        proposal: KnowledgePlanProposal,
    ) -> None:
        """Apply one accepted structural proposal without generating page content."""
        if isinstance(proposal, KnowledgeCreatePageProposal):
            self._create_planned_page(
                connection=connection,
                decision_id=decision_id,
                deployment_id=deployment_id,
                page=proposal.page,
            )
            return
        if isinstance(proposal, KnowledgeSplitPageProposal):
            source = self._compiled_plan_artifact(
                connection=connection,
                deployment_id=deployment_id,
                artifact_id=proposal.source_artifact_id,
            )
            self._retire_rules(
                connection=connection, artifact_id=proposal.source_artifact_id
            )
            connection.execute(
                _MARK_STALE, {"artifact_id": proposal.source_artifact_id}
            )
            for page in proposal.pages:
                if page.scope_id != source["scope_id"]:
                    raise KnowledgeCompilationError("split page crosses source scope")
                self._create_planned_page(
                    connection=connection,
                    decision_id=decision_id,
                    deployment_id=deployment_id,
                    page=page,
                )
            return
        if isinstance(proposal, KnowledgeMergePagesProposal):
            sources = tuple(
                self._compiled_plan_artifact(
                    connection=connection,
                    deployment_id=deployment_id,
                    artifact_id=artifact_id,
                )
                for artifact_id in proposal.source_artifact_ids
            )
            if any(row["scope_id"] != proposal.page.scope_id for row in sources):
                raise KnowledgeCompilationError("merge page crosses source scope")
            if proposal.page.parent_artifact_id is not None and any(
                self._is_descendant(
                    connection=connection,
                    deployment_id=deployment_id,
                    ancestor_id=source_id,
                    candidate_id=proposal.page.parent_artifact_id,
                )
                for source_id in proposal.source_artifact_ids
            ):
                raise KnowledgeCompilationError(
                    "merge target parent cannot descend from a merge source"
                )
            target_artifact_id = self._create_planned_page(
                connection=connection,
                decision_id=decision_id,
                deployment_id=deployment_id,
                page=proposal.page,
            )
            for artifact_id in proposal.source_artifact_ids:
                self._retire_rules(connection=connection, artifact_id=artifact_id)
                connection.execute(
                    _REPARENT_CHILDREN,
                    {
                        "source_artifact_id": artifact_id,
                        "target_artifact_id": target_artifact_id,
                    },
                )
                connection.execute(_TOMBSTONE_ARTIFACT, {"artifact_id": artifact_id})
            return
        if isinstance(proposal, KnowledgeMovePageProposal):
            artifact = self._compiled_plan_artifact(
                connection=connection,
                deployment_id=deployment_id,
                artifact_id=proposal.artifact_id,
            )
            if artifact["git_path"] != proposal.old_git_path:
                raise KnowledgeCompilationError("move proposal has a stale source path")
            if artifact["curation_path"] != proposal.old_curation_path:
                raise KnowledgeCompilationError(
                    "move proposal has a stale curation path"
                )
            if artifact["parent_artifact_id"] != proposal.old_parent_artifact_id:
                raise KnowledgeCompilationError(
                    "move proposal has a stale source parent"
                )
            if proposal.new_parent_artifact_id is not None and self._is_descendant(
                connection=connection,
                deployment_id=deployment_id,
                ancestor_id=proposal.artifact_id,
                candidate_id=proposal.new_parent_artifact_id,
            ):
                raise KnowledgeCompilationError(
                    "move proposal would create an artifact cycle"
                )
            connection.execute(
                _MOVE_ARTIFACT,
                {
                    "artifact_id": proposal.artifact_id,
                    "git_path": proposal.new_git_path,
                    "curation_path": proposal.new_curation_path,
                    "parent_artifact_id": proposal.new_parent_artifact_id,
                },
            )
            return
        if isinstance(proposal, KnowledgeRetirePageProposal):
            self._compiled_plan_artifact(
                connection=connection,
                deployment_id=deployment_id,
                artifact_id=proposal.artifact_id,
            )
            child_count = int(
                connection.execute(
                    _SELECT_LIVE_CHILD_COUNT, {"artifact_id": proposal.artifact_id}
                ).scalar_one()
            )
            if child_count:
                raise KnowledgeCompilationError(
                    "retire proposal must move or retire live children first"
                )
            self._retire_rules(connection=connection, artifact_id=proposal.artifact_id)
            connection.execute(
                _TOMBSTONE_ARTIFACT, {"artifact_id": proposal.artifact_id}
            )
            return
        if isinstance(proposal, KnowledgeAdjustRuleProposal):
            self._compiled_plan_artifact(
                connection=connection,
                deployment_id=deployment_id,
                artifact_id=proposal.artifact_id,
            )
            self._replace_planned_rules(
                connection=connection,
                decision_id=decision_id,
                deployment_id=deployment_id,
                artifact_id=proposal.artifact_id,
                rules=proposal.rules,
            )
            return
        artifact = self._plan_artifact(
            connection=connection,
            deployment_id=deployment_id,
            artifact_id=proposal.artifact_id,
        )
        if artifact["page_kind"] != proposal.from_kind.value:
            raise KnowledgeCompilationError("convert-kind proposal has stale ownership")
        if proposal.to_kind is KnowledgePageKind.AUTHORED:
            connection.execute(_ADOPT_ARTIFACT, {"artifact_id": proposal.artifact_id})
            return
        connection.execute(
            _HANDOVER_ARTIFACT,
            {
                "artifact_id": proposal.artifact_id,
                "writer_version": proposal.writer_version,
                "curation_path": proposal.curation_path,
            },
        )
        self._replace_planned_rules(
            connection=connection,
            decision_id=decision_id,
            deployment_id=deployment_id,
            artifact_id=proposal.artifact_id,
            rules=proposal.rules,
        )

    def _is_descendant(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        ancestor_id: UUID,
        candidate_id: UUID,
    ) -> bool:
        """Return whether one live artifact is below another in the page tree."""
        return bool(
            connection.execute(
                _SELECT_DESCENDANT_MEMBERSHIP,
                {
                    "deployment_id": deployment_id,
                    "ancestor_id": ancestor_id,
                    "candidate_id": candidate_id,
                },
            ).scalar_one()
        )

    def _create_planned_page(
        self,
        *,
        connection: Connection,
        decision_id: UUID,
        deployment_id: UUID,
        page: KnowledgePlannedPage,
    ) -> UUID:
        """Create one stale compiled artifact and its plan-authorized rule union."""
        artifact_id = uuid4()
        connection.execute(
            _INSERT_PLANNED_ARTIFACT,
            {
                "artifact_id": artifact_id,
                "deployment_id": deployment_id,
                "layer": page.layer.value,
                "scope_id": page.scope_id,
                "parent_artifact_id": page.parent_artifact_id,
                "git_path": page.git_path,
                "curation_path": page.curation_path,
                "kind": page.artifact_kind,
                "writer_version": page.writer_version,
            },
        )
        self._replace_planned_rules(
            connection=connection,
            decision_id=decision_id,
            deployment_id=deployment_id,
            artifact_id=artifact_id,
            rules=page.rules,
        )
        return artifact_id

    def _replace_planned_rules(
        self,
        *,
        connection: Connection,
        decision_id: UUID,
        deployment_id: UUID,
        artifact_id: UUID,
        rules: tuple[KnowledgeRuleParams, ...],
    ) -> None:
        """Replace a page's rule union and inverted keys inside one plan transaction."""
        self._retire_rules(connection=connection, artifact_id=artifact_id)
        for params in rules:
            rule_id = uuid4()
            connection.execute(
                _INSERT_PAGE_RULE,
                {
                    "rule_id": rule_id,
                    "deployment_id": deployment_id,
                    "artifact_id": artifact_id,
                    "plan_decision_id": decision_id,
                    "rule_kind": params.kind.value,
                    "params": _stored_params(params=params),
                },
            )
            self._replace_rule_keys(
                connection=connection,
                deployment_id=deployment_id,
                rule_id=rule_id,
                params=params,
            )
        connection.execute(_MARK_STALE, {"artifact_id": artifact_id})

    def _retire_rules(self, *, connection: Connection, artifact_id: UUID) -> None:
        """Deprecate one page's old rules and remove their routing keys."""
        rule_ids = tuple(
            connection.execute(
                _SELECT_ACTIVE_RULE_IDS, {"artifact_id": artifact_id}
            ).scalars()
        )
        for rule_id in rule_ids:
            connection.execute(_DELETE_RULE_KEYS, {"rule_id": rule_id})
        connection.execute(_DEPRECATE_ARTIFACT_RULES, {"artifact_id": artifact_id})

    def _plan_artifact(
        self, *, connection: Connection, deployment_id: UUID, artifact_id: UUID
    ) -> RowMapping:
        """Lock one live structural target and preserve its typed ownership fields."""
        row = (
            connection.execute(
                _SELECT_PLAN_ARTIFACT_FOR_UPDATE,
                {"deployment_id": deployment_id, "artifact_id": artifact_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise KnowledgeCompilationError("plan proposal targets no live artifact")
        return row

    def _compiled_plan_artifact(
        self, *, connection: Connection, deployment_id: UUID, artifact_id: UUID
    ) -> RowMapping:
        """Lock one live compiled structural target."""
        row = self._plan_artifact(
            connection=connection, deployment_id=deployment_id, artifact_id=artifact_id
        )
        if row["page_kind"] != KnowledgePageKind.COMPILED.value:
            raise KnowledgeCompilationError(
                "planner may mutate only compiled structure"
            )
        if row["status"] == KnowledgeArtifactStatus.QUARANTINED.value:
            raise KnowledgeCompilationError(
                "quarantined pages require explicit quarantine triage"
            )
        return row

    def _open_quarantine_proposal(
        self, *, connection: Connection, quarantine_id: UUID
    ) -> tuple[RowMapping, KnowledgeConvertKindProposal]:
        """Lock and parse one unresolved compiled-to-authored quarantine proposal."""
        row = (
            connection.execute(
                _SELECT_QUARANTINE_FOR_UPDATE, {"quarantine_id": quarantine_id}
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["status"] != KnowledgeQuarantineStatus.PROPOSED.value:
            raise KnowledgeCompilationError("quarantine is not open")
        proposal = _PLAN_PROPOSAL_ADAPTER.validate_python(row["payload"])
        if not isinstance(proposal, KnowledgeConvertKindProposal):
            raise KnowledgeCompilationError("quarantine decision is not an adoption")
        return row, proposal

    def _input_snapshot(
        self,
        *,
        connection: Connection,
        artifact_id: UUID,
        context: KnowledgeCompileContext,
        child_summary_hashes: tuple[str, ...] | None = None,
    ) -> KnowledgeInputSnapshot:
        """Assemble a manifest on an existing connection."""
        rule_rows = tuple(
            connection.execute(
                _SELECT_ARTIFACT_RULES, {"artifact_id": artifact_id}
            ).mappings()
        )
        facts: dict[tuple[str, UUID], KnowledgeFactFingerprint] = {}
        claims: dict[tuple[UUID, str], KnowledgeClaimFingerprint] = {}
        rules: list[KnowledgeRuleConfiguration] = []
        for row in rule_rows:
            params = _parse_rule(row=row)
            stored = _stored_params(params=params)
            rules.append(
                KnowledgeRuleConfiguration(
                    rule_id=row["rule_id"], kind=params.kind, params=stored
                )
            )
            rule_facts, rule_claims = self._candidates_for_rule(
                connection=connection, deployment_id=row["deployment_id"], params=params
            )
            for fact in rule_facts:
                facts[(fact.kind, fact.fact_id)] = fact
            for claim in rule_claims:
                claims[(claim.lineage_id, claim.chunk_content_hash)] = claim
        child_hashes = (
            tuple(
                knowledge_summary_hash(summary=summary)
                for summary in connection.execute(
                    _SELECT_CHILD_SUMMARIES, {"artifact_id": artifact_id}
                ).scalars()
            )
            if child_summary_hashes is None
            else tuple(sorted(child_summary_hashes))
        )
        return KnowledgeInputSnapshot(
            facts=tuple(facts.values()),
            claims=tuple(claims.values()),
            rules=tuple(rules),
            curation_hash=context.curation_hash,
            child_summary_hashes=child_hashes,
            shared_model_summary_hash=context.shared_model_summary_hash,
            writer_version=context.writer_version,
        )

    def _fact_sheet_snapshot(
        self,
        *,
        connection: Connection,
        artifact_id: UUID,
        context: KnowledgeCompileContext,
        child_summary_hashes: tuple[str, ...] | None,
    ) -> KnowledgeFactSheetSnapshot:
        """Build the exact display-fact snapshot on an existing transaction."""
        artifact = (
            connection.execute(
                _SELECT_FACT_SHEET_ARTIFACT, {"artifact_id": artifact_id}
            )
            .mappings()
            .one_or_none()
        )
        if artifact is None:
            raise KnowledgeCompilationError(
                "fact-sheet target is not an active/stale compiled artifact"
            )
        evidence_as_of = connection.execute(_SELECT_TRANSACTION_TIMESTAMP).scalar_one()
        input_snapshot = self._input_snapshot(
            connection=connection,
            artifact_id=artifact_id,
            context=context,
            child_summary_hashes=child_summary_hashes,
        )
        relation_ids = tuple(
            fact.fact_id for fact in input_snapshot.facts if fact.kind == "relation"
        )
        observation_ids = tuple(
            fact.fact_id for fact in input_snapshot.facts if fact.kind == "observation"
        )
        facts = (
            *self._fact_sheet_relations(
                connection=connection,
                deployment_id=artifact["deployment_id"],
                relation_ids=relation_ids,
            ),
            *self._fact_sheet_observations(
                connection=connection,
                deployment_id=artifact["deployment_id"],
                observation_ids=observation_ids,
            ),
        )
        expected = {(fact.kind, fact.fact_id) for fact in input_snapshot.facts}
        hydrated = {(fact.kind, fact.fact_id) for fact in facts}
        if hydrated != expected:
            raise KnowledgeCompilationError(
                "fact-sheet hydration does not match the rule candidate set"
            )
        return KnowledgeFactSheetSnapshot(
            artifact_id=artifact_id,
            deployment_id=artifact["deployment_id"],
            evidence_as_of=evidence_as_of,
            input_snapshot=input_snapshot,
            facts=facts,
        )

    def _writer_claim_groups(
        self, *, connection: Connection, fact_sheet: KnowledgeFactSheetSnapshot
    ) -> tuple[KnowledgeWriterClaimGroup, ...]:
        """Hydrate every current claim row at each selected D54 coordinate."""
        candidates = fact_sheet.input_snapshot.claims
        if not candidates:
            return ()
        rows = tuple(
            connection.execute(
                _SELECT_WRITER_CLAIMS,
                {
                    "deployment_id": fact_sheet.deployment_id,
                    "candidates": [item.model_dump(mode="json") for item in candidates],
                },
            ).mappings()
        )
        claim_ids = tuple(row["claim_id"] for row in rows)
        references = self._writer_fact_references(
            connection=connection,
            deployment_id=fact_sheet.deployment_id,
            facts=fact_sheet.facts,
            claim_ids=claim_ids,
        )
        grouped: dict[tuple[UUID, str], list[KnowledgeWriterClaim]] = {}
        for row in rows:
            claim_id = row["claim_id"]
            claim = KnowledgeWriterClaim.model_validate(
                {**dict(row), "fact_references": references.get(claim_id, ())}
            )
            grouped.setdefault((claim.lineage_id, claim.chunk_content_hash), []).append(
                claim
            )
        expected = {(item.lineage_id, item.chunk_content_hash) for item in candidates}
        if set(grouped) != expected:
            raise KnowledgeCompilationError(
                "writer claim hydration does not match the D54 candidate set"
            )
        return tuple(
            KnowledgeWriterClaimGroup(
                lineage_id=lineage_id,
                chunk_content_hash=chunk_hash,
                claims=tuple(sorted(claims, key=lambda claim: str(claim.claim_id))),
            )
            for (lineage_id, chunk_hash), claims in sorted(
                grouped.items(), key=lambda item: (str(item[0][0]), item[0][1])
            )
        )

    def _writer_fact_references(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        facts: tuple[KnowledgeFactSheetFact, ...],
        claim_ids: tuple[UUID, ...],
    ) -> dict[UUID, tuple[KnowledgeWriterFactReference, ...]]:
        """Attach selected facts to the candidate claims that evidence them."""
        if not claim_ids:
            return {}
        rows: list[RowMapping] = []
        relation_ids = tuple(fact.fact_id for fact in facts if fact.kind == "relation")
        observation_ids = tuple(
            fact.fact_id for fact in facts if fact.kind == "observation"
        )
        if relation_ids:
            rows.extend(
                connection.execute(
                    _SELECT_WRITER_RELATION_REFERENCES,
                    {
                        "deployment_id": deployment_id,
                        "fact_ids": list(relation_ids),
                        "claim_ids": list(claim_ids),
                    },
                ).mappings()
            )
        if observation_ids:
            rows.extend(
                connection.execute(
                    _SELECT_WRITER_OBSERVATION_REFERENCES,
                    {
                        "deployment_id": deployment_id,
                        "fact_ids": list(observation_ids),
                        "claim_ids": list(claim_ids),
                    },
                ).mappings()
            )
        grouped: dict[UUID, list[KnowledgeWriterFactReference]] = {}
        for row in rows:
            grouped.setdefault(row["claim_id"], []).append(
                KnowledgeWriterFactReference.model_validate(
                    {key: row[key] for key in ("kind", "fact_id", "stance")}
                )
            )
        return {
            claim_id: tuple(
                sorted(
                    items, key=lambda item: (item.kind, str(item.fact_id), item.stance)
                )
            )
            for claim_id, items in grouped.items()
        }

    def _fact_sheet_relations(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        relation_ids: tuple[UUID, ...],
    ) -> tuple[KnowledgeFactSheetFact, ...]:
        """Hydrate exact relation candidates with stable display labels."""
        if not relation_ids:
            return ()
        rows = connection.execute(
            _SELECT_FACT_SHEET_RELATIONS,
            {"deployment_id": deployment_id, "relation_ids": list(relation_ids)},
        ).mappings()
        return tuple(KnowledgeFactSheetFact.model_validate(dict(row)) for row in rows)

    def _fact_sheet_observations(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        observation_ids: tuple[UUID, ...],
    ) -> tuple[KnowledgeFactSheetFact, ...]:
        """Hydrate exact observation candidates with stable display labels."""
        if not observation_ids:
            return ()
        rows = connection.execute(
            _SELECT_FACT_SHEET_OBSERVATIONS,
            {"deployment_id": deployment_id, "observation_ids": list(observation_ids)},
        ).mappings()
        return tuple(KnowledgeFactSheetFact.model_validate(dict(row)) for row in rows)

    def _replace_rule_keys(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        rule_id: UUID,
        params: KnowledgeRuleParams,
    ) -> tuple[KnowledgeRuleKey, ...]:
        """Compute and transactionally replace one rule's materialized keys."""
        keys = self._rule_keys(
            connection=connection, deployment_id=deployment_id, params=params
        )
        connection.execute(_DELETE_RULE_KEYS, {"rule_id": rule_id})
        for key in keys:
            connection.execute(
                _INSERT_RULE_KEY,
                {
                    "deployment_id": deployment_id,
                    "rule_id": rule_id,
                    "key_kind": key.kind.value,
                    "key_value": key.value,
                },
            )
        return keys

    def _rule_keys(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        params: KnowledgeRuleParams,
    ) -> tuple[KnowledgeRuleKey, ...]:
        """Expand one rule into every coarse key the schema can represent."""
        keys: set[tuple[KnowledgeRuleKeyKind, str]] = set()
        if isinstance(params, EntityRuleParams):
            keys.add((KnowledgeRuleKeyKind.ENTITY, str(params.entity_id)))
            keys.update(
                (KnowledgeRuleKeyKind.PREDICATE, predicate)
                for predicate in params.predicates
            )
        elif isinstance(params, EntitySubtreeRuleParams):
            members = connection.execute(
                _SELECT_SUBTREE_MEMBERS,
                {
                    "deployment_id": deployment_id,
                    "root_entity_id": params.root_entity_id,
                },
            ).scalars()
            keys.update(
                (KnowledgeRuleKeyKind.ENTITY, str(member)) for member in members
            )
            keys.update(
                (KnowledgeRuleKeyKind.PREDICATE, predicate)
                for predicate in params.predicates
            )
        elif isinstance(params, PredicateBeatRuleParams):
            keys.add((KnowledgeRuleKeyKind.PREDICATE, params.predicate))
            for entity_id in (params.subject_entity_id, params.object_entity_id):
                if entity_id is not None:
                    keys.add((KnowledgeRuleKeyKind.ENTITY, str(entity_id)))
        elif isinstance(params, CommunityRuleParams):
            keys.add((KnowledgeRuleKeyKind.COMMUNITY, str(params.community_id)))
            members = connection.execute(
                _SELECT_COMMUNITY_MEMBERS,
                {"deployment_id": deployment_id, "community_id": params.community_id},
            ).scalars()
            keys.update(
                (KnowledgeRuleKeyKind.ENTITY, str(member)) for member in members
            )
        elif isinstance(params, DocSetRuleParams):
            keys.add((KnowledgeRuleKeyKind.DOC_SOURCE, params.source_kind))
        elif isinstance(params, ScopeInterestsRuleParams):
            keys.update(
                self._scope_interest_keys(
                    connection=connection,
                    deployment_id=deployment_id,
                    scope_id=params.scope_id,
                )
            )
        elif isinstance(params, ManualRuleParams):
            keys.update(
                (KnowledgeRuleKeyKind.ENTITY, str(entity_id))
                for entity_id in params.entity_ids
            )
        return tuple(
            KnowledgeRuleKey(kind=kind, value=value)
            for kind, value in sorted(keys, key=lambda item: (item[0].value, item[1]))
        )

    def _scope_interest_keys(
        self, *, connection: Connection, deployment_id: UUID, scope_id: UUID
    ) -> set[tuple[KnowledgeRuleKeyKind, str]]:
        """Materialize the subset of registry interests representable as keys."""
        keys: set[tuple[KnowledgeRuleKeyKind, str]] = set()
        interests = connection.execute(
            _SELECT_SCOPE_INTERESTS, {"scope_id": scope_id}
        ).mappings()
        for interest in interests:
            interest_type = str(interest["interest_type"])
            value = str(interest["value"])
            if interest_type == "predicate":
                keys.add((KnowledgeRuleKeyKind.PREDICATE, value))
            elif interest_type == "entity_type":
                raise KnowledgeCompilationError(
                    "scope interest entity_type is unsupported after D96; "
                    "select entities through predicate, metadata, keyword, or fact text"
                )
            elif interest_type == "metadata":
                sources = connection.execute(
                    _SELECT_DOC_SOURCES_FOR_METADATA,
                    {"deployment_id": deployment_id, "value": value},
                ).scalars()
                keys.update(
                    (KnowledgeRuleKeyKind.DOC_SOURCE, str(source)) for source in sources
                )
            # Keyword interests have no corresponding rule_key_kind. They are
            # still evaluated exactly in the candidate manifest.
        return keys

    def _candidates_for_rule(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        params: KnowledgeRuleParams,
    ) -> tuple[
        tuple[KnowledgeFactFingerprint, ...], tuple[KnowledgeClaimFingerprint, ...]
    ]:
        """Evaluate one typed rule into fact and stable claim coordinates."""
        if isinstance(params, EntityRuleParams):
            return self._entity_candidates(
                connection=connection,
                deployment_id=deployment_id,
                entity_ids=(params.entity_id,),
                layers=params.layers,
                predicates=params.predicates,
            )
        if isinstance(params, EntitySubtreeRuleParams):
            entity_ids = tuple(
                connection.execute(
                    _SELECT_SUBTREE_MEMBERS,
                    {
                        "deployment_id": deployment_id,
                        "root_entity_id": params.root_entity_id,
                    },
                ).scalars()
            )
            return self._entity_candidates(
                connection=connection,
                deployment_id=deployment_id,
                entity_ids=entity_ids,
                layers=params.layers,
                predicates=params.predicates,
            )
        if isinstance(params, PredicateBeatRuleParams):
            rows = connection.execute(
                _SELECT_PREDICATE_RELATIONS,
                {
                    "deployment_id": deployment_id,
                    "predicate": params.predicate,
                    "subject_entity_id": params.subject_entity_id,
                    "object_entity_id": params.object_entity_id,
                },
            ).mappings()
            return (_fact_fingerprints(rows=rows), ())
        if isinstance(params, CommunityRuleParams):
            entity_ids = tuple(
                connection.execute(
                    _SELECT_COMMUNITY_MEMBERS,
                    {
                        "deployment_id": deployment_id,
                        "community_id": params.community_id,
                    },
                ).scalars()
            )
            return self._entity_candidates(
                connection=connection,
                deployment_id=deployment_id,
                entity_ids=entity_ids,
                layers=params.layers,
            )
        if isinstance(params, DocSetRuleParams):
            doc_ids = tuple(
                connection.execute(
                    _SELECT_DOC_SET,
                    {
                        "deployment_id": deployment_id,
                        "source_kind": params.source_kind,
                        "mime": params.mime,
                        "origin": params.origin,
                        "source_modified_from": params.source_modified_from,
                        "source_modified_until": params.source_modified_until,
                    },
                ).scalars()
            )
            return self._document_candidates(
                connection=connection, deployment_id=deployment_id, doc_ids=doc_ids
            )
        if isinstance(params, ScopeInterestsRuleParams):
            return self._scope_candidates(
                connection=connection,
                deployment_id=deployment_id,
                scope_id=params.scope_id,
            )
        return self._manual_candidates(
            connection=connection, deployment_id=deployment_id, params=params
        )

    def _scope_candidate_keys(
        self, *, connection: Connection, deployment_id: UUID, scope_id: UUID | None
    ) -> set[str]:
        """Evaluate the compiled rule union that already houses scope evidence."""
        covered: set[str] = set()
        rows = connection.execute(
            _SELECT_SCOPE_COMPILED_RULES,
            {"deployment_id": deployment_id, "scope_id": scope_id},
        ).mappings()
        for row in rows:
            facts, claims = self._candidates_for_rule(
                connection=connection,
                deployment_id=deployment_id,
                params=_parse_rule(row=row),
            )
            covered.update(f"fact:{fact.kind}:{fact.fact_id}" for fact in facts)
            covered.update(
                f"claim:{claim.lineage_id}:{claim.chunk_content_hash}"
                for claim in claims
            )
        return covered

    def _entity_candidates(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        entity_ids: tuple[UUID, ...],
        layers: tuple[KnowledgeCandidateLayer, ...],
        predicates: tuple[str, ...] = (),
    ) -> tuple[
        tuple[KnowledgeFactFingerprint, ...], tuple[KnowledgeClaimFingerprint, ...]
    ]:
        """Read selected candidate layers for a set of canonical entities."""
        if not entity_ids:
            return (), ()
        facts: list[KnowledgeFactFingerprint] = []
        claims: tuple[KnowledgeClaimFingerprint, ...] = ()
        values = {"deployment_id": deployment_id, "entity_ids": list(entity_ids)}
        if KnowledgeCandidateLayer.RELATIONS in layers:
            relation_statement = (
                _SELECT_FILTERED_ENTITY_RELATIONS
                if predicates
                else _SELECT_ENTITY_RELATIONS
            )
            facts.extend(
                _fact_fingerprints(
                    rows=connection.execute(
                        relation_statement, {**values, "predicates": list(predicates)}
                    ).mappings()
                )
            )
        if KnowledgeCandidateLayer.OBSERVATIONS in layers:
            facts.extend(
                _fact_fingerprints(
                    rows=connection.execute(
                        _SELECT_ENTITY_OBSERVATIONS,
                        {
                            "deployment_id": deployment_id,
                            "entity_ids": list(entity_ids),
                        },
                    ).mappings()
                )
            )
        if KnowledgeCandidateLayer.CLAIMS in layers:
            claims = _claim_fingerprints(
                rows=connection.execute(
                    _SELECT_ENTITY_CLAIMS,
                    {"deployment_id": deployment_id, "entity_ids": list(entity_ids)},
                ).mappings()
            )
        return tuple(facts), claims

    def _document_candidates(
        self, *, connection: Connection, deployment_id: UUID, doc_ids: tuple[UUID, ...]
    ) -> tuple[
        tuple[KnowledgeFactFingerprint, ...], tuple[KnowledgeClaimFingerprint, ...]
    ]:
        """Read facts supported by and claims belonging to a document set."""
        if not doc_ids:
            return (), ()
        values = {"deployment_id": deployment_id, "doc_ids": list(doc_ids)}
        facts = _fact_fingerprints(
            rows=connection.execute(_SELECT_DOCUMENT_FACTS, values).mappings()
        )
        claims = _claim_fingerprints(
            rows=connection.execute(_SELECT_DOCUMENT_CLAIMS, values).mappings()
        )
        return facts, claims

    def _scope_candidates(
        self, *, connection: Connection, deployment_id: UUID, scope_id: UUID
    ) -> tuple[
        tuple[KnowledgeFactFingerprint, ...], tuple[KnowledgeClaimFingerprint, ...]
    ]:
        """Evaluate every registry interest and union its deterministic matches."""
        facts: dict[tuple[str, UUID], KnowledgeFactFingerprint] = {}
        claims: dict[tuple[UUID, str], KnowledgeClaimFingerprint] = {}
        interests = tuple(
            connection.execute(
                _SELECT_SCOPE_INTERESTS, {"scope_id": scope_id}
            ).mappings()
        )
        for interest in interests:
            interest_type = str(interest["interest_type"])
            value = str(interest["value"])
            if interest_type == "entity_type":
                raise KnowledgeCompilationError(
                    "scope interest entity_type is unsupported after D96; "
                    "select entities through predicate, metadata, keyword, or fact text"
                )
            elif interest_type == "predicate":
                selected_facts = _fact_fingerprints(
                    rows=connection.execute(
                        _SELECT_PREDICATE_RELATIONS,
                        {
                            "deployment_id": deployment_id,
                            "predicate": value,
                            "subject_entity_id": None,
                            "object_entity_id": None,
                        },
                    ).mappings()
                )
                selected_claims = _claim_fingerprints(
                    rows=connection.execute(
                        _SELECT_PREDICATE_CLAIMS,
                        {"deployment_id": deployment_id, "predicate": value},
                    ).mappings()
                )
            elif interest_type == "metadata":
                doc_ids = tuple(
                    connection.execute(
                        _SELECT_DOCS_FOR_METADATA,
                        {"deployment_id": deployment_id, "value": value},
                    ).scalars()
                )
                selected_facts, selected_claims = self._document_candidates(
                    connection=connection, deployment_id=deployment_id, doc_ids=doc_ids
                )
            else:
                claim_ids = tuple(
                    connection.execute(
                        _SELECT_KEYWORD_CLAIMS,
                        {"deployment_id": deployment_id, "keyword": value},
                    ).scalars()
                )
                selected_facts = self._facts_for_claim_ids(
                    connection=connection,
                    deployment_id=deployment_id,
                    claim_ids=claim_ids,
                )
                selected_claims = self._claims_for_ids(
                    connection=connection,
                    deployment_id=deployment_id,
                    claim_ids=claim_ids,
                )
            for fact in selected_facts:
                facts[(fact.kind, fact.fact_id)] = fact
            for claim in selected_claims:
                claims[(claim.lineage_id, claim.chunk_content_hash)] = claim
        return tuple(facts.values()), tuple(claims.values())

    def _manual_candidates(
        self, *, connection: Connection, deployment_id: UUID, params: ManualRuleParams
    ) -> tuple[
        tuple[KnowledgeFactFingerprint, ...], tuple[KnowledgeClaimFingerprint, ...]
    ]:
        """Union an explicit manual assignment without requiring indexable keys."""
        facts: dict[tuple[str, UUID], KnowledgeFactFingerprint] = {}
        claims: dict[tuple[UUID, str], KnowledgeClaimFingerprint] = {}
        entity_facts, entity_claims = self._entity_candidates(
            connection=connection,
            deployment_id=deployment_id,
            entity_ids=params.entity_ids,
            layers=tuple(KnowledgeCandidateLayer),
        )
        direct_facts: tuple[KnowledgeFactFingerprint, ...] = ()
        if params.relation_ids:
            direct_facts = _fact_fingerprints(
                rows=connection.execute(
                    _SELECT_RELATIONS_BY_IDS,
                    {
                        "deployment_id": deployment_id,
                        "relation_ids": list(params.relation_ids),
                    },
                ).mappings()
            )
        if params.observation_ids:
            direct_facts = (
                *direct_facts,
                *_fact_fingerprints(
                    rows=connection.execute(
                        _SELECT_OBSERVATIONS_BY_IDS,
                        {
                            "deployment_id": deployment_id,
                            "observation_ids": list(params.observation_ids),
                        },
                    ).mappings()
                ),
            )
        claim_facts = self._facts_for_claim_ids(
            connection=connection,
            deployment_id=deployment_id,
            claim_ids=params.claim_ids,
        )
        doc_facts, doc_claims = self._document_candidates(
            connection=connection, deployment_id=deployment_id, doc_ids=params.doc_ids
        )
        direct_claims = self._claims_for_ids(
            connection=connection,
            deployment_id=deployment_id,
            claim_ids=params.claim_ids,
        )
        for fact in (*entity_facts, *direct_facts, *claim_facts, *doc_facts):
            facts[(fact.kind, fact.fact_id)] = fact
        for claim in (*entity_claims, *direct_claims, *doc_claims):
            claims[(claim.lineage_id, claim.chunk_content_hash)] = claim
        return tuple(facts.values()), tuple(claims.values())

    def _facts_for_claim_ids(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        claim_ids: tuple[UUID, ...],
    ) -> tuple[KnowledgeFactFingerprint, ...]:
        """Resolve explicit claim IDs to the facts they support or contradict."""
        if not claim_ids:
            return ()
        return _fact_fingerprints(
            rows=connection.execute(
                _SELECT_FACTS_FOR_CLAIMS,
                {"deployment_id": deployment_id, "claim_ids": list(claim_ids)},
            ).mappings()
        )

    def _claims_for_ids(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        claim_ids: tuple[UUID, ...],
    ) -> tuple[KnowledgeClaimFingerprint, ...]:
        """Resolve raw claim IDs immediately into stable lineage/chunk coordinates."""
        if not claim_ids:
            return ()
        return _claim_fingerprints(
            rows=connection.execute(
                _SELECT_CLAIMS_BY_IDS,
                {"deployment_id": deployment_id, "claim_ids": list(claim_ids)},
            ).mappings()
        )

    def _delta_keys(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        delta: KnowledgeEvidenceDelta,
    ) -> tuple[KnowledgeRuleKey, ...]:
        """Derive the four coarse labels carried by changed evidence."""
        keys: set[tuple[KnowledgeRuleKeyKind, str]] = set()
        if delta.relation_ids:
            rows = connection.execute(
                _SELECT_RELATION_DELTA_KEYS,
                {
                    "deployment_id": deployment_id,
                    "relation_ids": list(delta.relation_ids),
                },
            ).mappings()
            for row in rows:
                keys.add((KnowledgeRuleKeyKind.ENTITY, str(row["subject_entity_id"])))
                keys.add((KnowledgeRuleKeyKind.ENTITY, str(row["object_entity_id"])))
                keys.add((KnowledgeRuleKeyKind.PREDICATE, str(row["predicate"])))
            sources = connection.execute(
                _SELECT_RELATION_DELTA_DOC_SOURCES,
                {
                    "deployment_id": deployment_id,
                    "relation_ids": list(delta.relation_ids),
                },
            ).scalars()
            keys.update(
                (KnowledgeRuleKeyKind.DOC_SOURCE, str(source)) for source in sources
            )
        if delta.observation_ids:
            entities = connection.execute(
                _SELECT_OBSERVATION_DELTA_KEYS,
                {
                    "deployment_id": deployment_id,
                    "observation_ids": list(delta.observation_ids),
                },
            ).scalars()
            keys.update(
                (KnowledgeRuleKeyKind.ENTITY, str(entity_id)) for entity_id in entities
            )
            sources = connection.execute(
                _SELECT_OBSERVATION_DELTA_DOC_SOURCES,
                {
                    "deployment_id": deployment_id,
                    "observation_ids": list(delta.observation_ids),
                },
            ).scalars()
            keys.update(
                (KnowledgeRuleKeyKind.DOC_SOURCE, str(source)) for source in sources
            )
        if delta.claim_ids:
            rows = connection.execute(
                _SELECT_CLAIM_DELTA_KEYS,
                {"deployment_id": deployment_id, "claim_ids": list(delta.claim_ids)},
            ).mappings()
            for row in rows:
                if row["entity_id"] is not None:
                    keys.add((KnowledgeRuleKeyKind.ENTITY, str(row["entity_id"])))
                keys.add((KnowledgeRuleKeyKind.DOC_SOURCE, str(row["source_kind"])))
        if delta.doc_ids:
            sources = connection.execute(
                _SELECT_DOCUMENT_DELTA_KEYS,
                {"deployment_id": deployment_id, "doc_ids": list(delta.doc_ids)},
            ).scalars()
            keys.update(
                (KnowledgeRuleKeyKind.DOC_SOURCE, str(source)) for source in sources
            )
        keys.update(
            (KnowledgeRuleKeyKind.COMMUNITY, str(community_id))
            for community_id in delta.community_ids
        )
        return tuple(
            KnowledgeRuleKey(kind=kind, value=value)
            for kind, value in sorted(keys, key=lambda item: (item[0].value, item[1]))
        )

    def _citation_artifacts(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        delta: KnowledgeEvidenceDelta,
    ) -> set[UUID]:
        """Find compiled pages citing schema-supported changed evidence."""
        artifacts: set[UUID] = set()
        if delta.claim_ids:
            artifacts.update(
                connection.execute(
                    _SELECT_CITATION_ARTIFACTS_FOR_CLAIMS,
                    {
                        "deployment_id": deployment_id,
                        "claim_ids": list(delta.claim_ids),
                    },
                ).scalars()
            )
        for column, values in (
            ("relation_id", delta.relation_ids),
            ("doc_id", delta.doc_ids),
        ):
            if not values:
                continue
            statement = _citation_lookup(column=column)
            artifacts.update(
                connection.execute(
                    statement,
                    {"deployment_id": deployment_id, "evidence_ids": list(values)},
                ).scalars()
            )
        return artifacts

    def _manual_artifacts(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        delta: KnowledgeEvidenceDelta,
    ) -> set[UUID]:
        """Match explicit manual IDs that have no representable inverted key."""
        artifacts: set[UUID] = set()
        rows = connection.execute(
            _SELECT_MANUAL_RULES, {"deployment_id": deployment_id}
        ).mappings()
        changed = {
            "relation": set(delta.relation_ids),
            "observation": set(delta.observation_ids),
            "claim": set(delta.claim_ids),
            "doc": set(delta.doc_ids),
        }
        for row in rows:
            params = _parse_rule(row=row)
            if not isinstance(params, ManualRuleParams):
                continue
            if (
                changed["relation"].intersection(params.relation_ids)
                or changed["observation"].intersection(params.observation_ids)
                or changed["claim"].intersection(params.claim_ids)
                or changed["doc"].intersection(params.doc_ids)
            ):
                artifacts.add(row["artifact_id"])
        return artifacts

    def _validate_citations(
        self,
        *,
        connection: Connection,
        deployment_id: UUID,
        citations: tuple[KnowledgeCitation, ...],
    ) -> None:
        """Reject citation IDs absent from this deployment's spine."""
        for citation in citations:
            if citation.claim_lineage_id is not None:
                exists = connection.execute(
                    _CLAIM_COORDINATE_EXISTS,
                    {
                        "deployment_id": deployment_id,
                        "lineage_id": citation.claim_lineage_id,
                        "chunk_content_hash": citation.claim_chunk_content_hash,
                    },
                ).scalar_one()
            elif citation.relation_id is not None:
                exists = connection.execute(
                    _RELATION_EXISTS,
                    {
                        "deployment_id": deployment_id,
                        "evidence_id": citation.relation_id,
                    },
                ).scalar_one()
            else:
                exists = connection.execute(
                    _DOCUMENT_EXISTS,
                    {"deployment_id": deployment_id, "evidence_id": citation.doc_id},
                ).scalar_one()
            if not exists:
                raise KnowledgeCompilationError(
                    f"citation target does not exist in deployment: {citation}"
                )


def _proposal_artifact_ids(*, proposal: KnowledgePlanProposal) -> tuple[UUID, ...]:
    """Return the existing artifact targets named by one structural proposal."""
    if isinstance(proposal, KnowledgeCreatePageProposal):
        return ()
    if isinstance(proposal, KnowledgeSplitPageProposal):
        return (proposal.source_artifact_id,)
    if isinstance(proposal, KnowledgeMergePagesProposal):
        return proposal.source_artifact_ids
    return (proposal.artifact_id,)


def _stored_params(*, params: KnowledgeRuleParams) -> dict[str, JsonValue]:
    """Serialize a discriminated rule without duplicating its DB enum kind."""
    return _JSON_OBJECT_ADAPTER.validate_python(
        params.model_dump(mode="json", exclude={"kind"})
    )


def _parse_rule(*, row: RowMapping) -> KnowledgeRuleParams:
    """Validate a rule row against the typed JSON contract before evaluation."""
    stored = _JSON_OBJECT_ADAPTER.validate_python(row["params"])
    return _RULE_ADAPTER.validate_python({"kind": str(row["rule_kind"]), **stored})


def _fact_fingerprints(
    *, rows: Iterable[RowMapping]
) -> tuple[KnowledgeFactFingerprint, ...]:
    """Validate and deduplicate database fact-state rows."""
    facts: dict[tuple[str, UUID], KnowledgeFactFingerprint] = {}
    for row in rows:
        fact = KnowledgeFactFingerprint.model_validate(dict(row))
        facts[(fact.kind, fact.fact_id)] = fact
    return tuple(facts.values())


def _claim_fingerprints(
    *, rows: Iterable[RowMapping]
) -> tuple[KnowledgeClaimFingerprint, ...]:
    """Validate and deduplicate stable claim coordinates from SQL rows."""
    claims: dict[tuple[UUID, str], KnowledgeClaimFingerprint] = {}
    for row in rows:
        claim = KnowledgeClaimFingerprint.model_validate(dict(row))
        claims[(claim.lineage_id, claim.chunk_content_hash)] = claim
    return tuple(claims.values())


def _unique_citations(
    *, citations: tuple[KnowledgeCitation, ...]
) -> tuple[KnowledgeCitation, ...]:
    """Apply set semantics to repeated writer citations."""
    unique = {_citation_tuple(citation=citation): citation for citation in citations}
    return tuple(unique[key] for key in sorted(unique, key=lambda value: str(value)))


def _citation_tuple(
    *, citation: KnowledgeCitation
) -> tuple[str, UUID | None, str | None, UUID | None, UUID | None]:
    """Return the database uniqueness coordinates for one citation."""
    return (
        citation.role.value,
        citation.claim_lineage_id,
        citation.claim_chunk_content_hash,
        citation.relation_id,
        citation.doc_id,
    )


def _citation_lookup(*, column: str) -> TextClause:
    """Build one allow-listed citation reverse lookup with an expanding bind."""
    if column not in {"relation_id", "doc_id"}:
        raise ValueError(f"unsupported citation column: {column}")
    return text(
        f"""
        SELECT DISTINCT a.artifact_id
        FROM knowledge_artifact_evidence e
        JOIN knowledge_artifacts a
          ON a.deployment_id = e.deployment_id
         AND a.artifact_id = e.artifact_id
        WHERE e.deployment_id = :deployment_id
          AND e.{column} IN :evidence_ids
          AND a.page_kind = 'compiled'
          AND a.status NOT IN ('quarantined', 'tombstoned')
        """  # noqa: S608 -- column is checked against the fixed schema allow-list above
    ).bindparams(bindparam("evidence_ids", expanding=True))


def _authored_citation_lookup(*, column: str) -> TextClause:
    """Build one allow-listed reverse citation lookup for live authored pages."""
    if column not in {"relation_id", "doc_id"}:
        raise ValueError(f"unsupported citation column: {column}")
    return text(
        f"""
        SELECT DISTINCT a.artifact_id
        FROM knowledge_artifact_evidence e
        JOIN knowledge_artifacts a
          ON a.deployment_id = e.deployment_id
         AND a.artifact_id = e.artifact_id
        WHERE e.deployment_id = :deployment_id
          AND e.{column} IN :evidence_ids
          AND a.page_kind = 'authored'
          AND a.status <> 'tombstoned'
        ORDER BY a.artifact_id
        """  # noqa: S608 -- column is checked against the fixed schema allow-list above
    ).bindparams(bindparam("evidence_ids", expanding=True))


_INSERT_PLAN_DECISION = text(
    """
    INSERT INTO knowledge_plan_decisions (
        decision_id, deployment_id, scope_id, action, payload,
        trigger, planner_version, status
    ) VALUES (
        :decision_id, :deployment_id, :scope_id, :action, :payload,
        :trigger, :planner_version, :status
    )
    """
).bindparams(bindparam("payload", type_=JSON))

_INSERT_PLAN_RUN = text(
    """
    INSERT INTO knowledge_plan_runs (
        run_id, deployment_id, scope_id, run_kind, trigger,
        component_version, input_hash, session_transcript_uri,
        status, failure, tokens, cost_usd
    ) VALUES (
        :run_id, :deployment_id, :scope_id, :run_kind, :trigger,
        :component_version, :input_hash, :session_transcript_uri,
        :status, :failure, :tokens, :cost_usd
    )
    """
)

_INSERT_ROUTED_PLAN_DECISION = text(
    """
    INSERT INTO knowledge_plan_decisions (
        decision_id, deployment_id, scope_id, action, payload,
        trigger, planner_version, status, plan_run_id,
        confidence, blast_radius, expected_impact
    ) VALUES (
        :decision_id, :deployment_id, :scope_id, :action, :payload,
        :trigger, :planner_version, :status, :plan_run_id,
        :confidence, :blast_radius, :expected_impact
    )
    """
).bindparams(bindparam("payload", type_=JSON))

_SELECT_PLAN_DECISION_FOR_REVIEW = text(
    """
    SELECT d.decision_id, d.deployment_id, d.payload, d.status::text AS status,
           EXISTS(
             SELECT 1 FROM knowledge_quarantines q
             WHERE q.decision_id = d.decision_id AND q.status = 'proposed'
           ) AS open_quarantine
    FROM knowledge_plan_decisions d
    WHERE d.decision_id = :decision_id
    FOR UPDATE
    """
)

_ACCEPT_PLAN_DECISION = text(
    """
    UPDATE knowledge_plan_decisions
    SET status = 'applied', reviewed_by = :reviewed_by, reviewed_at = now()
    WHERE decision_id = :decision_id AND status = 'proposed'
    RETURNING decision_id
    """
)

_REJECT_PLAN_DECISION = text(
    """
    UPDATE knowledge_plan_decisions
    SET status = 'rejected', reviewed_by = :reviewed_by, reviewed_at = now()
    WHERE decision_id = :decision_id AND status = 'proposed'
      AND NOT EXISTS (
        SELECT 1 FROM knowledge_quarantines q
        WHERE q.decision_id = knowledge_plan_decisions.decision_id
          AND q.status = 'proposed'
      )
    RETURNING decision_id
    """
)

_RESOLVE_REJECT_PLAN_DECISION = text(
    """
    UPDATE knowledge_plan_decisions
    SET status = 'rejected', reviewed_by = :reviewed_by, reviewed_at = now()
    WHERE decision_id = :decision_id AND status = 'proposed'
    RETURNING decision_id
    """
)

_SELECT_PENDING_PLAN_DECISIONS = text(
    """
    SELECT decision_id, payload, decided_at
    FROM knowledge_plan_decisions
    WHERE deployment_id = :deployment_id
      AND status = 'applied'
      AND confidence IS NOT NULL
      AND application_commit IS NULL
    ORDER BY decided_at, decision_id
    """
)

_SELECT_PENDING_PLAN_DECISIONS_FOR_UPDATE = text(
    """
    SELECT decision_id, payload, decided_at
    FROM knowledge_plan_decisions
    WHERE deployment_id = :deployment_id
      AND status = 'applied'
      AND confidence IS NOT NULL
      AND application_commit IS NULL
    ORDER BY decided_at, decision_id
    FOR UPDATE
    """
)

_STAMP_PLAN_DECISION = text(
    """
    UPDATE knowledge_plan_decisions
    SET application_commit = :application_commit
    WHERE decision_id = :decision_id
      AND status = 'applied'
      AND application_commit IS NULL
    """
)

_SELECT_PLAN_EFFECT_TARGETS = text(
    """
    SELECT artifact_id, git_path, curation_path,
           page_kind::text AS page_kind, status::text AS status, last_compiled_at
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND artifact_id IN :artifact_ids
    ORDER BY artifact_id
    """
).bindparams(bindparam("artifact_ids", expanding=True))

_SELECT_PLAN_CREATED_ARTIFACTS = text(
    """
    SELECT DISTINCT a.artifact_id, a.git_path, a.curation_path,
           a.page_kind::text AS page_kind, a.status::text AS status,
           a.last_compiled_at
    FROM knowledge_artifacts a
    JOIN knowledge_page_rules r
      ON r.deployment_id = a.deployment_id
     AND r.artifact_id = a.artifact_id
    WHERE a.deployment_id = :deployment_id
      AND r.plan_decision_id = :decision_id
    ORDER BY a.artifact_id
    """
)

_SELECT_PLAN_DECISION_ARTIFACT_PATHS = text(
    """
    SELECT artifact_id, git_path
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND artifact_id IN :artifact_ids
    ORDER BY artifact_id
    """
).bindparams(bindparam("artifact_ids", expanding=True))

_SELECT_PLAN_ARTIFACT_SCOPES = text(
    """
    SELECT artifact_id, scope_id
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND artifact_id IN :artifact_ids
      AND status <> 'tombstoned'
    ORDER BY artifact_id
    """
).bindparams(bindparam("artifact_ids", expanding=True))

_SELECT_DESCENDANT_COUNT = text(
    """
    WITH RECURSIVE descendants(artifact_id) AS (
        SELECT artifact_id
        FROM knowledge_artifacts
        WHERE deployment_id = :deployment_id
          AND parent_artifact_id = :artifact_id
          AND status <> 'tombstoned'
        UNION ALL
        SELECT child.artifact_id
        FROM knowledge_artifacts child
        JOIN descendants parent ON parent.artifact_id = child.parent_artifact_id
        WHERE child.deployment_id = :deployment_id
          AND child.status <> 'tombstoned'
    )
    SELECT count(*) FROM descendants
    """
)

_SELECT_DESCENDANT_MEMBERSHIP = text(
    """
    WITH RECURSIVE descendants(artifact_id) AS (
        SELECT artifact_id
        FROM knowledge_artifacts
        WHERE deployment_id = :deployment_id
          AND parent_artifact_id = :ancestor_id
          AND status <> 'tombstoned'
        UNION ALL
        SELECT child.artifact_id
        FROM knowledge_artifacts child
        JOIN descendants parent ON parent.artifact_id = child.parent_artifact_id
        WHERE child.deployment_id = :deployment_id
          AND child.status <> 'tombstoned'
    )
    SELECT EXISTS(
        SELECT 1 FROM descendants WHERE artifact_id = :candidate_id
    )
    """
)

_SELECT_ARTIFACT_IMPACT = text(
    """
    SELECT COALESCE(
        (
          SELECT c.candidate_count
          FROM knowledge_compilations c
          WHERE c.artifact_id = a.artifact_id AND c.git_commit IS NOT NULL
          ORDER BY c.compiled_at DESC
          LIMIT 1
        ),
        (
          SELECT count(*)
          FROM knowledge_artifact_evidence e
          WHERE e.artifact_id = a.artifact_id
        ),
        0
    ) AS impact
    FROM knowledge_artifacts a
    WHERE a.deployment_id = :deployment_id
      AND a.artifact_id = :artifact_id
      AND a.status <> 'tombstoned'
    """
)

_SELECT_PLAN_ARTIFACT_FOR_UPDATE = text(
    """
    SELECT artifact_id, deployment_id, scope_id, parent_artifact_id,
           git_path, curation_path, page_kind::text AS page_kind,
           status::text AS status
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND artifact_id = :artifact_id
      AND status <> 'tombstoned'
    FOR UPDATE
    """
)

_INSERT_PLANNED_ARTIFACT = text(
    """
    INSERT INTO knowledge_artifacts (
        artifact_id, deployment_id, layer, page_kind, scope_id,
        parent_artifact_id, git_path, curation_path, kind,
        writer_version, status
    ) VALUES (
        :artifact_id, :deployment_id, :layer, 'compiled', :scope_id,
        :parent_artifact_id, :git_path, :curation_path, :kind,
        :writer_version, 'stale'
    )
    """
)

_SELECT_ACTIVE_RULE_IDS = text(
    """
    SELECT rule_id
    FROM knowledge_page_rules
    WHERE artifact_id = :artifact_id AND status = 'active'
    ORDER BY rule_id
    """
)

_DEPRECATE_ARTIFACT_RULES = text(
    """
    UPDATE knowledge_page_rules
    SET status = 'deprecated'
    WHERE artifact_id = :artifact_id AND status = 'active'
    """
)

_TOMBSTONE_ARTIFACT = text(
    """
    UPDATE knowledge_artifacts
    SET status = 'tombstoned'
    WHERE artifact_id = :artifact_id AND page_kind = 'compiled'
    """
)

_SELECT_LIVE_CHILD_COUNT = text(
    """
    SELECT count(*)
    FROM knowledge_artifacts
    WHERE parent_artifact_id = :artifact_id AND status <> 'tombstoned'
    """
)

_REPARENT_CHILDREN = text(
    """
    UPDATE knowledge_artifacts
    SET parent_artifact_id = :target_artifact_id,
        status = CASE
          WHEN page_kind = 'compiled' THEN 'stale'::knowledge_artifact_status
          ELSE status
        END
    WHERE parent_artifact_id = :source_artifact_id AND status <> 'tombstoned'
    """
)

_MOVE_ARTIFACT = text(
    """
    UPDATE knowledge_artifacts
    SET git_path = :git_path,
        curation_path = :curation_path,
        parent_artifact_id = :parent_artifact_id,
        status = 'stale'
    WHERE artifact_id = :artifact_id AND page_kind = 'compiled'
    """
)

_ADOPT_ARTIFACT = text(
    """
    UPDATE knowledge_artifacts
    SET page_kind = 'authored',
        status = 'active',
        curation_path = NULL,
        page_summary = NULL,
        inputs_hash = NULL,
        writer_version = NULL,
        last_compiled_at = NULL
    WHERE artifact_id = :artifact_id AND page_kind = 'compiled'
    """
)

_HANDOVER_ARTIFACT = text(
    """
    UPDATE knowledge_artifacts
    SET page_kind = 'compiled',
        status = 'stale',
        curation_path = :curation_path,
        page_summary = NULL,
        inputs_hash = NULL,
        content_hash = NULL,
        writer_version = :writer_version,
        last_compiled_at = NULL
    WHERE artifact_id = :artifact_id AND page_kind = 'authored'
    """
)

_SELECT_OPEN_QUARANTINE = text(
    """
    SELECT quarantine_id, decision_id, deployment_id, artifact_id,
           recorded_content_hash, detected_content_hash,
           proposed_sidecar_entry, status, detected_at, resolved_at
    FROM knowledge_quarantines
    WHERE artifact_id = :artifact_id AND status = 'proposed'
    """
)

_SELECT_COMPILED_CONTENT_FOR_UPDATE = text(
    """
    SELECT artifact_id, deployment_id, scope_id, content_hash
    FROM knowledge_artifacts
    WHERE artifact_id = :artifact_id
      AND page_kind = 'compiled'
      AND status IN ('active','stale')
    FOR UPDATE
    """
)

_INSERT_QUARANTINE = text(
    """
    INSERT INTO knowledge_quarantines (
        quarantine_id, decision_id, deployment_id, artifact_id,
        recorded_content_hash, detected_content_hash, proposed_sidecar_entry
    ) VALUES (
        :quarantine_id, :decision_id, :deployment_id, :artifact_id,
        :recorded_content_hash, :detected_content_hash, :proposed_sidecar_entry
    )
    RETURNING quarantine_id, decision_id, deployment_id, artifact_id,
              recorded_content_hash, detected_content_hash,
              proposed_sidecar_entry, status, detected_at, resolved_at
    """
)

_MARK_QUARANTINED = text(
    """
    UPDATE knowledge_artifacts
    SET status = 'quarantined'
    WHERE artifact_id = :artifact_id AND page_kind = 'compiled'
    """
)

_SELECT_QUARANTINE_FOR_UPDATE = text(
    """
    SELECT q.quarantine_id, q.decision_id, q.deployment_id, q.artifact_id,
           q.detected_content_hash, q.proposed_sidecar_entry, q.status, d.payload
    FROM knowledge_quarantines q
    JOIN knowledge_plan_decisions d ON d.decision_id = q.decision_id
    WHERE q.quarantine_id = :quarantine_id
    FOR UPDATE OF q, d
    """
)

_ACKNOWLEDGE_QUARANTINED_BODY = text(
    """
    UPDATE knowledge_artifacts
    SET content_hash = :content_hash
    WHERE artifact_id = :artifact_id
      AND page_kind = 'compiled'
      AND status = 'quarantined'
    """
)

_CLEAR_QUARANTINED_BODY_IDENTITY = text(
    """
    UPDATE knowledge_artifacts
    SET content_hash = NULL
    WHERE artifact_id = :artifact_id
      AND page_kind = 'compiled'
      AND status = 'quarantined'
    """
)

_RESUME_QUARANTINED_AS_STALE = text(
    """
    UPDATE knowledge_artifacts
    SET status = 'stale'
    WHERE artifact_id = :artifact_id
      AND page_kind = 'compiled'
      AND status = 'quarantined'
    """
)

_RESOLVE_QUARANTINE = text(
    """
    UPDATE knowledge_quarantines
    SET status = :status,
        resolution_note = :resolution_note,
        curation_content_hash = :curation_content_hash,
        resolved_at = now()
    WHERE quarantine_id = :quarantine_id AND status = 'proposed'
    """
)

_SELECT_ARTIFACT_PATH_STATES = text(
    """
    SELECT artifact_id, git_path, page_kind::text AS page_kind, curation_path
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id AND status <> 'tombstoned'
    ORDER BY git_path
    """
)

_SELECT_ARTIFACT_BY_PATH_FOR_UPDATE = text(
    """
    SELECT artifact_id, page_kind::text AS page_kind,
           status::text AS status, content_hash
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id AND git_path = :git_path
    FOR UPDATE
    """
)

_INSERT_AUTHORED_ARTIFACT = text(
    """
    INSERT INTO knowledge_artifacts (
        artifact_id, deployment_id, layer, page_kind, git_path, status
    ) VALUES (
        :artifact_id, :deployment_id, CAST(:layer AS knowledge_layer),
        'authored', :git_path, 'active'
    )
    """
)

_UPDATE_AUTHORED_CONTENT_HASH = text(
    """
    UPDATE knowledge_artifacts
    SET content_hash = :content_hash
    WHERE artifact_id = :artifact_id AND page_kind = 'authored'
    """
)

_SELECT_WATCHED_PATHS = text(
    """
    SELECT git_path, artifact_id
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND git_path IN :paths
      AND page_kind = 'compiled'
      AND status <> 'tombstoned'
    ORDER BY git_path
    """
).bindparams(bindparam("paths", expanding=True))

_DELETE_ARTIFACT_PAGE_WATCHES = text(
    "DELETE FROM knowledge_page_watches WHERE watcher_artifact_id = :artifact_id"
)

_INSERT_ARTIFACT_PAGE_WATCH = text(
    """
    INSERT INTO knowledge_page_watches (
        watch_id, deployment_id, watcher_artifact_id, watched_artifact_id
    ) VALUES (
        :watch_id, :deployment_id, :watcher_artifact_id, :watched_artifact_id
    )
    """
)

_SELECT_AUTHORED_DECLARATION_COUNTS = text(
    """
    SELECT
      (SELECT count(*) FROM knowledge_artifact_evidence
       WHERE artifact_id = :artifact_id) AS citation_count,
      (SELECT count(*) FROM knowledge_page_rules
       WHERE artifact_id = :artifact_id AND status = 'active') AS watch_rule_count,
      (SELECT count(*) FROM knowledge_page_watches
       WHERE watcher_artifact_id = :artifact_id) AS page_watch_count
    """
)

_INSERT_AUTHORED_RULE_DECISION = text(
    """
    INSERT INTO knowledge_plan_decisions (
        decision_id, deployment_id, action, payload, trigger,
        planner_version, status, application_commit
    ) VALUES (
        :decision_id, :deployment_id, 'adjust_rule', :payload, 'human',
        'authored-frontmatter', 'applied', :application_commit
    )
    """
).bindparams(bindparam("payload", type_=JSON))

_INSERT_SUBSCRIPTION = text(
    """
    INSERT INTO knowledge_subscriptions (
        subscription_id, deployment_id, scope_id, name, workflow_endpoint,
        debounce_seconds, created_by
    ) VALUES (
        :subscription_id, :deployment_id, :scope_id, :name, :workflow_endpoint,
        :debounce_seconds, :created_by
    )
    """
)

_INSERT_SUBSCRIPTION_RULE = text(
    """
    INSERT INTO knowledge_page_rules (
        rule_id, deployment_id, subscription_id, rule_kind, params
    ) VALUES (
        :rule_id, :deployment_id, :subscription_id, :rule_kind, :params
    )
    """
).bindparams(bindparam("params", type_=JSON))

_INSERT_SUBSCRIPTION_PAGE_WATCH = text(
    """
    INSERT INTO knowledge_page_watches (
        watch_id, deployment_id, subscription_id, watched_artifact_id
    ) VALUES (
        :watch_id, :deployment_id, :subscription_id, :watched_artifact_id
    )
    """
)

_SELECT_NOTIFICATION_RULES_FOR_KEY = text(
    """
    SELECT r.rule_id, r.deployment_id, r.artifact_id, r.subscription_id,
           r.rule_kind::text AS rule_kind, r.params
    FROM knowledge_rule_keys k
    JOIN knowledge_page_rules r ON r.rule_id = k.rule_id
    LEFT JOIN knowledge_artifacts a
      ON a.deployment_id = r.deployment_id AND a.artifact_id = r.artifact_id
    LEFT JOIN knowledge_subscriptions s
      ON s.subscription_id = r.subscription_id
    WHERE k.deployment_id = :deployment_id
      AND k.key_kind = CAST(:key_kind AS rule_key_kind)
      AND k.key_value = :key_value
      AND r.status = 'active'
      AND (
        (a.page_kind = 'authored' AND a.status <> 'tombstoned')
        OR s.status = 'active'
      )
    ORDER BY r.rule_id
    """
)

_SELECT_NOTIFICATION_FALLBACK_RULES = text(
    """
    SELECT r.rule_id, r.deployment_id, r.artifact_id, r.subscription_id,
           r.rule_kind::text AS rule_kind, r.params
    FROM knowledge_page_rules r
    LEFT JOIN knowledge_artifacts a
      ON a.deployment_id = r.deployment_id AND a.artifact_id = r.artifact_id
    LEFT JOIN knowledge_subscriptions s
      ON s.subscription_id = r.subscription_id
    WHERE r.deployment_id = :deployment_id
      AND r.rule_kind IN ('manual', 'scope_interests')
      AND r.status = 'active'
      AND (
        (a.page_kind = 'authored' AND a.status <> 'tombstoned')
        OR s.status = 'active'
      )
    ORDER BY r.rule_id
    """
)

_SELECT_AUTHORED_CITATIONS_FOR_CLAIMS = text(
    """
    SELECT DISTINCT a.artifact_id
    FROM claims c
    JOIN chunks ch
      ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
    JOIN knowledge_artifact_evidence e
      ON e.deployment_id = c.deployment_id
     AND e.claim_lineage_id = c.doc_id
     AND e.claim_chunk_content_hash = ch.chunk_content_hash
    JOIN knowledge_artifacts a
      ON a.deployment_id = e.deployment_id AND a.artifact_id = e.artifact_id
    WHERE c.deployment_id = :deployment_id
      AND c.claim_id IN :claim_ids
      AND a.page_kind = 'authored'
      AND a.status <> 'tombstoned'
    ORDER BY a.artifact_id
    """
).bindparams(bindparam("claim_ids", expanding=True))

_LOCK_ARTIFACT = text(
    "SELECT artifact_id FROM knowledge_artifacts WHERE artifact_id = :artifact_id FOR UPDATE"
)

_SELECT_OPEN_AUTHORED_FLAG_FOR_UPDATE = text(
    """
    SELECT refresh_id, payload
    FROM knowledge_refresh_queue
    WHERE deployment_id = :deployment_id
      AND artifact_id = :artifact_id
      AND trigger = 'authored_review'
      AND processed_at IS NULL
    FOR UPDATE
    """
)

_SELECT_OPEN_AUTHORED_FLAG_BY_ARTIFACT_FOR_UPDATE = text(
    """
    SELECT refresh_id, payload
    FROM knowledge_refresh_queue
    WHERE artifact_id = :artifact_id
      AND trigger = 'authored_review'
      AND processed_at IS NULL
    FOR UPDATE
    """
)

_INSERT_AUTHORED_FLAG = text(
    """
    INSERT INTO knowledge_refresh_queue (
        refresh_id, deployment_id, artifact_id, trigger, payload
    ) VALUES (
        :refresh_id, :deployment_id, :artifact_id, 'authored_review', :payload
    )
    """
).bindparams(bindparam("payload", type_=JSON))

_UPDATE_AUTHORED_FLAG = text(
    """
    UPDATE knowledge_refresh_queue
    SET payload = :payload
    WHERE refresh_id = :refresh_id AND processed_at IS NULL
    """
).bindparams(bindparam("payload", type_=JSON))

_RESOLVE_AUTHORED_FLAG = text(
    """
    UPDATE knowledge_refresh_queue
    SET status = 'done', processed_at = now()
    WHERE refresh_id = :refresh_id AND processed_at IS NULL
    """
)

_RESOLVE_AUTHORED_FLAGS = text(
    """
    UPDATE knowledge_refresh_queue
    SET status = 'done', processed_at = now()
    WHERE artifact_id = :artifact_id
      AND trigger = 'authored_review'
      AND processed_at IS NULL
    """
)

_SELECT_AUTHORED_REVIEW_STATE = text(
    """
    SELECT payload
    FROM knowledge_refresh_queue q
    JOIN knowledge_artifacts a ON a.artifact_id = q.artifact_id
    WHERE q.artifact_id = :artifact_id
      AND a.page_kind = 'authored'
      AND a.status <> 'tombstoned'
      AND q.trigger = 'authored_review'
      AND q.processed_at IS NULL
    ORDER BY q.enqueued_at, q.refresh_id
    """
)

_LOCK_SUBSCRIPTION = text(
    """
    SELECT subscription_id
    FROM knowledge_subscriptions
    WHERE subscription_id = :subscription_id AND status = 'active'
    FOR UPDATE
    """
)

_SELECT_PENDING_DISPATCH_FOR_UPDATE = text(
    """
    SELECT dispatch_id, payload
    FROM knowledge_dispatches
    WHERE deployment_id = :deployment_id
      AND subscription_id = :subscription_id
      AND status = 'pending'
    FOR UPDATE
    """
)

_INSERT_DISPATCH = text(
    """
    INSERT INTO knowledge_dispatches (
        dispatch_id, deployment_id, subscription_id, payload
    ) VALUES (
        :dispatch_id, :deployment_id, :subscription_id, :payload
    )
    """
).bindparams(bindparam("payload", type_=JSON))

_UPDATE_PENDING_DISPATCH = text(
    """
    UPDATE knowledge_dispatches
    SET payload = :payload
    WHERE dispatch_id = :dispatch_id AND status = 'pending'
    """
).bindparams(bindparam("payload", type_=JSON))

_SELECT_DUE_DISPATCHES_FOR_UPDATE = text(
    """
    SELECT d.dispatch_id, d.payload
    FROM knowledge_dispatches d
    JOIN knowledge_subscriptions s ON s.subscription_id = d.subscription_id
    WHERE d.deployment_id = :deployment_id
      AND d.status = 'pending'
      AND s.status = 'active'
      AND d.enqueued_at + make_interval(secs => s.debounce_seconds) <= now()
      AND NOT EXISTS (
        SELECT 1 FROM processing_state p
        WHERE p.deployment_id = d.deployment_id
          AND p.target_kind = 'knowledge_dispatch'
          AND p.target_id = d.dispatch_id
          AND p.stage = 'dispatch_knowledge'
      )
    ORDER BY d.enqueued_at, d.dispatch_id
    FOR UPDATE OF d SKIP LOCKED
    """
)

_SELECT_DISPATCH_FOR_UPDATE = text(
    """
    SELECT d.dispatch_id, d.deployment_id, d.subscription_id, d.payload,
           d.status::text AS status, s.workflow_endpoint,
           s.status::text AS subscription_status
    FROM knowledge_dispatches d
    JOIN knowledge_subscriptions s ON s.subscription_id = d.subscription_id
    WHERE d.dispatch_id = :dispatch_id
    FOR UPDATE OF d
    """
)

_MARK_DISPATCH_RUNNING = text(
    """
    UPDATE knowledge_dispatches
    SET status = 'running'
    WHERE dispatch_id = :dispatch_id AND status IN ('pending', 'failed', 'running')
    """
)

_REJECT_UNAVAILABLE_DISPATCH = text(
    """
    UPDATE knowledge_dispatches
    SET status = 'failed'
    WHERE dispatch_id = :dispatch_id AND status IN ('pending', 'running', 'failed')
    """
)

_COMPLETE_DISPATCH = text(
    """
    UPDATE knowledge_dispatches
    SET status = 'done', delivered_at = now()
    WHERE dispatch_id = :dispatch_id AND status = 'running'
    """
)

_FAIL_DISPATCH = text(
    """
    UPDATE knowledge_dispatches
    SET status = 'failed'
    WHERE dispatch_id = :dispatch_id AND status = 'running'
    """
)

_SELECT_PAGE_WATCHERS = text(
    """
    SELECT w.watcher_artifact_id, w.subscription_id,
           watched.git_path AS watched_git_path
    FROM knowledge_page_watches w
    JOIN knowledge_artifacts watched
      ON watched.deployment_id = w.deployment_id
     AND watched.artifact_id = w.watched_artifact_id
    LEFT JOIN knowledge_artifacts watcher
      ON watcher.deployment_id = w.deployment_id
     AND watcher.artifact_id = w.watcher_artifact_id
    LEFT JOIN knowledge_subscriptions s ON s.subscription_id = w.subscription_id
    WHERE w.deployment_id = :deployment_id
      AND w.watched_artifact_id = :watched_artifact_id
      AND (
        (watcher.page_kind = 'authored' AND watcher.status <> 'tombstoned')
        OR s.status = 'active'
      )
    ORDER BY w.watch_id
    """
)

_INSERT_ARTIFACT = text(
    """
    INSERT INTO knowledge_artifacts (
        artifact_id, deployment_id, layer, page_kind, scope_id,
        parent_artifact_id, git_path, curation_path, kind, writer_version
    ) VALUES (
        :artifact_id, :deployment_id, :layer, :page_kind, :scope_id,
        :parent_artifact_id, :git_path, :curation_path, :kind, :writer_version
    )
    """
)

_INSERT_PAGE_RULE = text(
    """
    INSERT INTO knowledge_page_rules (
        rule_id, deployment_id, artifact_id, rule_kind, params,
        plan_decision_id
    ) VALUES (
        :rule_id, :deployment_id, :artifact_id, :rule_kind, :params,
        :plan_decision_id
    )
    """
).bindparams(bindparam("params", type_=JSON))

_SELECT_RULE = text(
    """
    SELECT rule_id, deployment_id, artifact_id, rule_kind::text AS rule_kind, params
    FROM knowledge_page_rules
    WHERE rule_id = :rule_id AND status = 'active'
    """
)

_SELECT_DERIVED_RULES = text(
    """
    SELECT rule_id, deployment_id, artifact_id, subscription_id,
           rule_kind::text AS rule_kind, params
    FROM knowledge_page_rules
    WHERE deployment_id = :deployment_id
      AND status = 'active'
      AND rule_kind IN ('entity_subtree', 'community', 'scope_interests')
    ORDER BY rule_id
    """
)

_DELETE_RULE_KEYS = text("DELETE FROM knowledge_rule_keys WHERE rule_id = :rule_id")

_INSERT_RULE_KEY = text(
    """
    INSERT INTO knowledge_rule_keys (
        deployment_id, rule_id, key_kind, key_value
    ) VALUES (
        :deployment_id, :rule_id, :key_kind, :key_value
    )
    """
)

_SELECT_SUBTREE_MEMBERS = text(
    """
    WITH RECURSIVE members(entity_id) AS (
        SELECT e.entity_id
        FROM entities e
        WHERE e.deployment_id = :deployment_id
          AND e.entity_id = CAST(:root_entity_id AS uuid)
          AND e.status = 'active'
        UNION
        SELECT r.subject_entity_id
        FROM relations r
        JOIN members m ON m.entity_id = r.object_entity_id
        WHERE r.deployment_id = :deployment_id
          AND r.predicate = 'part_of'
          AND r.invalidated_at IS NULL
          AND r.valid_until IS NULL
    )
    SELECT entity_id FROM members ORDER BY entity_id
    """
)

_SELECT_COMMUNITY_MEMBERS = text(
    """
    SELECT entity_id
    FROM entity_graph_metrics
    WHERE deployment_id = :deployment_id AND community_id = :community_id
    ORDER BY entity_id
    """
)

_SELECT_SCOPE_INTERESTS = text(
    """
    SELECT interest_type::text AS interest_type, value
    FROM scope_interests
    WHERE scope_id = :scope_id
    ORDER BY interest_type, value
    """
)

_SELECT_DOC_SOURCES_FOR_METADATA = text(
    """
    SELECT DISTINCT d.source_kind
    FROM documents d
    LEFT JOIN document_versions v ON v.version_id = d.current_version_id
    LEFT JOIN content_objects o
      ON o.deployment_id = v.deployment_id AND o.content_hash = v.content_hash
    WHERE d.deployment_id = :deployment_id
      AND d.deleted_at IS NULL
      AND (
          d.source_kind = :value
          OR d.origin::text = :value
          OR o.mime = :value
      )
    ORDER BY d.source_kind
    """
)

_SELECT_ARTIFACT_RULES = text(
    """
    SELECT rule_id, deployment_id, rule_kind::text AS rule_kind, params
    FROM knowledge_page_rules
    WHERE artifact_id = :artifact_id AND status = 'active'
    ORDER BY rule_id
    """
)

_SELECT_CHILD_SUMMARIES = text(
    """
    SELECT page_summary
    FROM knowledge_artifacts
    WHERE parent_artifact_id = :artifact_id
      AND page_kind = 'compiled'
      AND status <> 'tombstoned'
      AND page_summary IS NOT NULL
    ORDER BY artifact_id
    """
)

_SELECT_FACT_SHEET_ARTIFACT = text(
    """
    SELECT deployment_id
    FROM knowledge_artifacts
    WHERE artifact_id = :artifact_id
      AND page_kind = 'compiled'
      AND status IN ('active', 'stale')
    """
)

_SELECT_TRANSACTION_TIMESTAMP = text("SELECT transaction_timestamp()")

_SELECT_ARTIFACT_HASH = text(
    """
    SELECT inputs_hash
    FROM knowledge_artifacts
    WHERE artifact_id = :artifact_id AND page_kind = 'compiled'
    """
)

_SELECT_COMPILED_ARTIFACTS = text(
    """
    SELECT artifact_id, inputs_hash
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND page_kind = 'compiled'
      AND status NOT IN ('quarantined', 'tombstoned')
    ORDER BY artifact_id
    """
)

_SELECT_FILTERED_COMPILED_ARTIFACTS = text(
    """
    SELECT artifact_id, inputs_hash
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND page_kind = 'compiled'
      AND status NOT IN ('quarantined', 'tombstoned')
      AND artifact_id IN :artifact_ids
    ORDER BY artifact_id
    """
).bindparams(bindparam("artifact_ids", expanding=True))

_MARK_STALE = text(
    """
    UPDATE knowledge_artifacts
    SET status = 'stale'
    WHERE artifact_id = :artifact_id
      AND page_kind = 'compiled'
      AND status = 'active'
    RETURNING artifact_id
    """
)

_FACT_COLUMNS_RELATION = """
    'relation' AS kind,
    r.relation_id AS fact_id,
    r.valid_from,
    r.valid_until,
    r.invalidated_at,
    r.evidence_count,
    r.contradict_count,
    r.contradiction_group
"""

_FACT_COLUMNS_OBSERVATION = """
    'observation' AS kind,
    o.observation_id AS fact_id,
    o.valid_from,
    o.valid_until,
    o.invalidated_at,
    o.evidence_count,
    o.contradict_count,
    o.contradiction_group
"""

_SELECT_FACT_SHEET_RELATIONS = text(
    """
    SELECT 'relation' AS kind,
           r.relation_id AS fact_id,
           COALESCE(
               NULLIF(btrim(r.fact_label), ''),
               subject.canonical_name || ' ' || replace(r.predicate, '_', ' ')
                 || ' ' || object.canonical_name
           ) AS label,
           r.valid_from, r.valid_until, r.ingested_at, r.invalidated_at,
           r.evidence_count, r.contradict_count, r.contradiction_group
    FROM relations r
    JOIN entities subject
      ON subject.deployment_id = r.deployment_id
     AND subject.entity_id = r.subject_entity_id
    JOIN entities object
      ON object.deployment_id = r.deployment_id
     AND object.entity_id = r.object_entity_id
    WHERE r.deployment_id = :deployment_id
      AND r.relation_id IN :relation_ids
    ORDER BY r.relation_id
    """
).bindparams(bindparam("relation_ids", expanding=True))

_SELECT_FACT_SHEET_OBSERVATIONS = text(
    """
    SELECT 'observation' AS kind,
           o.observation_id AS fact_id,
           COALESCE(NULLIF(btrim(o.obs_label), ''), o.statement) AS label,
           o.valid_from, o.valid_until, o.ingested_at, o.invalidated_at,
           o.evidence_count, o.contradict_count, o.contradiction_group
    FROM observations o
    WHERE o.deployment_id = :deployment_id
      AND o.observation_id IN :observation_ids
    ORDER BY o.observation_id
    """
).bindparams(bindparam("observation_ids", expanding=True))

_SELECT_WRITER_CLAIMS = text(
    """
    WITH wanted AS (
        SELECT *
        FROM jsonb_to_recordset(CAST(:candidates AS jsonb))
          AS item(lineage_id uuid, chunk_content_hash text)
    )
    SELECT c.claim_id, c.doc_id AS lineage_id, ch.chunk_content_hash,
           c.claim_text, c.source_span,
           COALESCE(NULLIF(btrim(d.title), ''), d.source_ref, d.doc_id::text)
             AS document_title,
           d.source_kind
    FROM wanted w
    JOIN claims c ON c.doc_id = w.lineage_id
    JOIN chunks ch
      ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
     AND ch.chunk_content_hash = w.chunk_content_hash
    JOIN documents d
      ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id
    WHERE c.deployment_id = :deployment_id
      AND c.is_current_testimony
    ORDER BY c.doc_id, ch.chunk_content_hash, c.claim_id
    """
).bindparams(bindparam("candidates", type_=JSON))

_SELECT_WRITER_RELATION_REFERENCES = text(
    """
    SELECT e.claim_id, 'relation' AS kind, e.relation_id AS fact_id,
           e.stance::text AS stance
    FROM relation_evidence e
    WHERE e.deployment_id = :deployment_id
      AND e.relation_id IN :fact_ids
      AND e.claim_id IN :claim_ids
    ORDER BY e.claim_id, e.relation_id, e.stance
    """
).bindparams(
    bindparam("fact_ids", expanding=True), bindparam("claim_ids", expanding=True)
)

_SELECT_WRITER_OBSERVATION_REFERENCES = text(
    """
    SELECT e.claim_id, 'observation' AS kind, e.observation_id AS fact_id,
           e.stance::text AS stance
    FROM observation_evidence e
    WHERE e.deployment_id = :deployment_id
      AND e.observation_id IN :fact_ids
      AND e.claim_id IN :claim_ids
    ORDER BY e.claim_id, e.observation_id, e.stance
    """
).bindparams(
    bindparam("fact_ids", expanding=True), bindparam("claim_ids", expanding=True)
)

_SELECT_ENTITY_RELATIONS = text(
    f"""
    SELECT {_FACT_COLUMNS_RELATION}
    FROM relations r
    WHERE r.deployment_id = :deployment_id
      AND (
          r.subject_entity_id IN :entity_ids
          OR r.object_entity_id IN :entity_ids
      )
    ORDER BY r.relation_id
    """  # noqa: S608 -- fixed local projection fragment, no external input
).bindparams(bindparam("entity_ids", expanding=True))

_SELECT_FILTERED_ENTITY_RELATIONS = text(
    f"""
    SELECT {_FACT_COLUMNS_RELATION}
    FROM relations r
    WHERE r.deployment_id = :deployment_id
      AND (
          r.subject_entity_id IN :entity_ids
          OR r.object_entity_id IN :entity_ids
      )
      AND r.predicate IN :predicates
    ORDER BY r.relation_id
    """  # noqa: S608 -- fixed local projection fragment, no external input
).bindparams(
    bindparam("entity_ids", expanding=True), bindparam("predicates", expanding=True)
)

_SELECT_ENTITY_OBSERVATIONS = text(
    f"""
    SELECT {_FACT_COLUMNS_OBSERVATION}
    FROM observations o
    WHERE o.deployment_id = :deployment_id
      AND o.subject_entity_id IN :entity_ids
    ORDER BY o.observation_id
    """  # noqa: S608 -- fixed local projection fragment, no external input
).bindparams(bindparam("entity_ids", expanding=True))

_SELECT_ENTITY_CLAIMS = text(
    """
    SELECT DISTINCT c.doc_id AS lineage_id, ch.chunk_content_hash
    FROM mentions m
    JOIN resolution_decisions rd
      ON rd.deployment_id = m.deployment_id
     AND rd.mention_id = m.mention_id
     AND rd.superseded_by IS NULL
    JOIN claims c
      ON c.deployment_id = m.deployment_id AND c.claim_id = m.claim_id
    JOIN chunks ch
      ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
    WHERE m.deployment_id = :deployment_id
      AND rd.entity_id IN :entity_ids
      AND c.is_current_testimony
    ORDER BY c.doc_id, ch.chunk_content_hash
    """
).bindparams(bindparam("entity_ids", expanding=True))

_SELECT_PREDICATE_RELATIONS = text(
    f"""
    SELECT {_FACT_COLUMNS_RELATION}
    FROM relations r
    WHERE r.deployment_id = :deployment_id
      AND r.predicate = :predicate
      AND (
          CAST(:subject_entity_id AS uuid) IS NULL
          OR r.subject_entity_id = CAST(:subject_entity_id AS uuid)
      )
      AND (
          CAST(:object_entity_id AS uuid) IS NULL
          OR r.object_entity_id = CAST(:object_entity_id AS uuid)
      )
    ORDER BY r.relation_id
    """  # noqa: S608 -- fixed local projection fragment, no external input
)

_SELECT_PREDICATE_CLAIMS = text(
    """
    SELECT DISTINCT c.doc_id AS lineage_id, ch.chunk_content_hash
    FROM relations r
    JOIN relation_evidence e
      ON e.deployment_id = r.deployment_id AND e.relation_id = r.relation_id
    JOIN claims c
      ON c.deployment_id = e.deployment_id AND c.claim_id = e.claim_id
    JOIN chunks ch
      ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
    WHERE r.deployment_id = :deployment_id
      AND r.predicate = :predicate
      AND c.is_current_testimony
    ORDER BY c.doc_id, ch.chunk_content_hash
    """
)

_SELECT_DOC_SET = text(
    """
    SELECT d.doc_id
    FROM documents d
    LEFT JOIN document_versions v ON v.version_id = d.current_version_id
    LEFT JOIN content_objects o
      ON o.deployment_id = v.deployment_id AND o.content_hash = v.content_hash
    WHERE d.deployment_id = :deployment_id
      AND d.deleted_at IS NULL
      AND d.source_kind = :source_kind
      AND (CAST(:mime AS text) IS NULL OR o.mime = CAST(:mime AS text))
      AND (
          CAST(:origin AS text) IS NULL
          OR d.origin::text = CAST(:origin AS text)
      )
      AND (
          CAST(:source_modified_from AS timestamptz) IS NULL
          OR v.source_modified_at >= CAST(:source_modified_from AS timestamptz)
      )
      AND (
          CAST(:source_modified_until AS timestamptz) IS NULL
          OR v.source_modified_at <= CAST(:source_modified_until AS timestamptz)
      )
    ORDER BY d.doc_id
    """
)

_SELECT_DOCUMENT_FACTS = text(
    f"""
    SELECT {_FACT_COLUMNS_RELATION}
    FROM relations r
    WHERE r.deployment_id = :deployment_id
      AND EXISTS (
          SELECT 1
          FROM relation_evidence e
          JOIN claims c
            ON c.deployment_id = e.deployment_id AND c.claim_id = e.claim_id
          WHERE e.relation_id = r.relation_id
            AND e.deployment_id = r.deployment_id
            AND e.doc_id IN :doc_ids
            AND c.is_current_testimony
      )
    UNION ALL
    SELECT {_FACT_COLUMNS_OBSERVATION}
    FROM observations o
    WHERE o.deployment_id = :deployment_id
      AND EXISTS (
          SELECT 1
          FROM observation_evidence e
          JOIN claims c
            ON c.deployment_id = e.deployment_id AND c.claim_id = e.claim_id
          WHERE e.observation_id = o.observation_id
            AND e.deployment_id = o.deployment_id
            AND e.doc_id IN :doc_ids
            AND c.is_current_testimony
      )
    """  # noqa: S608 -- fixed local projection fragments, no external input
).bindparams(bindparam("doc_ids", expanding=True))

_SELECT_DOCUMENT_CLAIMS = text(
    """
    SELECT DISTINCT c.doc_id AS lineage_id, ch.chunk_content_hash
    FROM claims c
    JOIN chunks ch
      ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
    WHERE c.deployment_id = :deployment_id
      AND c.doc_id IN :doc_ids
      AND c.is_current_testimony
    ORDER BY c.doc_id, ch.chunk_content_hash
    """
).bindparams(bindparam("doc_ids", expanding=True))

_SELECT_DOCS_FOR_METADATA = text(
    """
    SELECT d.doc_id
    FROM documents d
    LEFT JOIN document_versions v ON v.version_id = d.current_version_id
    LEFT JOIN content_objects o
      ON o.deployment_id = v.deployment_id AND o.content_hash = v.content_hash
    WHERE d.deployment_id = :deployment_id
      AND d.deleted_at IS NULL
      AND (
          d.source_kind = :value
          OR d.origin::text = :value
          OR o.mime = :value
      )
    ORDER BY d.doc_id
    """
)

_SELECT_KEYWORD_CLAIMS = text(
    """
    SELECT claim_id
    FROM claims
    WHERE deployment_id = :deployment_id
      AND is_current_testimony
      AND position(lower(:keyword) IN lower(claim_text)) > 0
    ORDER BY claim_id
    """
)

_SELECT_FACTS_FOR_CLAIMS = text(
    f"""
    SELECT {_FACT_COLUMNS_RELATION}
    FROM relations r
    WHERE r.deployment_id = :deployment_id
      AND EXISTS (
          SELECT 1 FROM relation_evidence e
          WHERE e.relation_id = r.relation_id
            AND e.deployment_id = r.deployment_id
            AND e.claim_id IN :claim_ids
      )
    UNION ALL
    SELECT {_FACT_COLUMNS_OBSERVATION}
    FROM observations o
    WHERE o.deployment_id = :deployment_id
      AND EXISTS (
          SELECT 1 FROM observation_evidence e
          WHERE e.observation_id = o.observation_id
            AND e.deployment_id = o.deployment_id
            AND e.claim_id IN :claim_ids
      )
    """  # noqa: S608 -- fixed local projection fragments, no external input
).bindparams(bindparam("claim_ids", expanding=True))

_SELECT_CLAIMS_BY_IDS = text(
    """
    SELECT DISTINCT c.doc_id AS lineage_id, ch.chunk_content_hash
    FROM claims c
    JOIN chunks ch
      ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
    WHERE c.deployment_id = :deployment_id AND c.claim_id IN :claim_ids
    ORDER BY c.doc_id, ch.chunk_content_hash
    """
).bindparams(bindparam("claim_ids", expanding=True))

_SELECT_RELATIONS_BY_IDS = text(
    f"""
    SELECT {_FACT_COLUMNS_RELATION}
    FROM relations r
    WHERE r.deployment_id = :deployment_id AND r.relation_id IN :relation_ids
    """  # noqa: S608 -- fixed local projection fragment, no external input
).bindparams(bindparam("relation_ids", expanding=True))

_SELECT_OBSERVATIONS_BY_IDS = text(
    f"""
    SELECT {_FACT_COLUMNS_OBSERVATION}
    FROM observations o
    WHERE o.deployment_id = :deployment_id
      AND o.observation_id IN :observation_ids
    """  # noqa: S608 -- fixed local projection fragment, no external input
).bindparams(bindparam("observation_ids", expanding=True))

_SELECT_RELATION_DELTA_KEYS = text(
    """
    SELECT subject_entity_id, object_entity_id, predicate
    FROM relations
    WHERE deployment_id = :deployment_id AND relation_id IN :relation_ids
    """
).bindparams(bindparam("relation_ids", expanding=True))

_SELECT_RELATION_DELTA_DOC_SOURCES = text(
    """
    SELECT DISTINCT d.source_kind
    FROM relation_evidence e
    JOIN documents d
      ON d.deployment_id = e.deployment_id AND d.doc_id = e.doc_id
    WHERE e.deployment_id = :deployment_id AND e.relation_id IN :relation_ids
    """
).bindparams(bindparam("relation_ids", expanding=True))

_SELECT_PART_OF_DELTA = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM relations
        WHERE deployment_id = :deployment_id
          AND relation_id IN :relation_ids
          AND predicate = 'part_of'
    )
    """
).bindparams(bindparam("relation_ids", expanding=True))

_SELECT_OBSERVATION_DELTA_KEYS = text(
    """
    SELECT subject_entity_id
    FROM observations
    WHERE deployment_id = :deployment_id AND observation_id IN :observation_ids
    """
).bindparams(bindparam("observation_ids", expanding=True))

_SELECT_OBSERVATION_DELTA_DOC_SOURCES = text(
    """
    SELECT DISTINCT d.source_kind
    FROM observation_evidence e
    JOIN documents d
      ON d.deployment_id = e.deployment_id AND d.doc_id = e.doc_id
    WHERE e.deployment_id = :deployment_id
      AND e.observation_id IN :observation_ids
    """
).bindparams(bindparam("observation_ids", expanding=True))

_SELECT_CLAIM_DELTA_KEYS = text(
    """
    SELECT DISTINCT d.source_kind, rd.entity_id
    FROM claims c
    JOIN documents d
      ON d.deployment_id = c.deployment_id AND d.doc_id = c.doc_id
    LEFT JOIN mentions m
      ON m.deployment_id = c.deployment_id AND m.claim_id = c.claim_id
    LEFT JOIN resolution_decisions rd
      ON rd.deployment_id = m.deployment_id
     AND rd.mention_id = m.mention_id
     AND rd.superseded_by IS NULL
    WHERE c.deployment_id = :deployment_id AND c.claim_id IN :claim_ids
    """
).bindparams(bindparam("claim_ids", expanding=True))

_SELECT_DOCUMENT_DELTA_KEYS = text(
    """
    SELECT DISTINCT source_kind
    FROM documents
    WHERE deployment_id = :deployment_id AND doc_id IN :doc_ids
    """
).bindparams(bindparam("doc_ids", expanding=True))

_SELECT_CITATION_ARTIFACTS_FOR_CLAIMS = text(
    """
    SELECT DISTINCT a.artifact_id
    FROM claims c
    JOIN chunks ch
      ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
    JOIN knowledge_artifact_evidence e
      ON e.deployment_id = c.deployment_id
     AND e.claim_lineage_id = c.doc_id
     AND e.claim_chunk_content_hash = ch.chunk_content_hash
    JOIN knowledge_artifacts a
      ON a.deployment_id = e.deployment_id AND a.artifact_id = e.artifact_id
    WHERE c.deployment_id = :deployment_id
      AND c.claim_id IN :claim_ids
      AND a.page_kind = 'compiled'
      AND a.status <> 'tombstoned'
    """
).bindparams(bindparam("claim_ids", expanding=True))

_SELECT_ARTIFACTS_FOR_KEY = text(
    """
    SELECT DISTINCT a.artifact_id
    FROM knowledge_rule_keys k
    JOIN knowledge_page_rules r ON r.rule_id = k.rule_id
    JOIN knowledge_artifacts a
      ON a.deployment_id = r.deployment_id AND a.artifact_id = r.artifact_id
    WHERE k.deployment_id = :deployment_id
      AND k.key_kind = CAST(:key_kind AS rule_key_kind)
      AND k.key_value = :key_value
      AND r.status = 'active'
      AND a.page_kind = 'compiled'
      AND a.status NOT IN ('quarantined', 'tombstoned')
    """
)

_SELECT_SCOPE_RULE_ARTIFACTS = text(
    """
    SELECT DISTINCT a.artifact_id
    FROM knowledge_page_rules r
    JOIN knowledge_artifacts a
      ON a.deployment_id = r.deployment_id AND a.artifact_id = r.artifact_id
    WHERE r.deployment_id = :deployment_id
      AND r.rule_kind = 'scope_interests'
      AND r.status = 'active'
      AND a.page_kind = 'compiled'
      AND a.status NOT IN ('quarantined', 'tombstoned')
    """
)

_SELECT_MANUAL_RULES = text(
    """
    SELECT r.rule_id, r.deployment_id, r.artifact_id,
           r.rule_kind::text AS rule_kind, r.params
    FROM knowledge_page_rules r
    JOIN knowledge_artifacts a
      ON a.deployment_id = r.deployment_id AND a.artifact_id = r.artifact_id
    WHERE r.deployment_id = :deployment_id
      AND r.rule_kind = 'manual'
      AND r.status = 'active'
      AND a.page_kind = 'compiled'
      AND a.status NOT IN ('quarantined', 'tombstoned')
    """
)

_SELECT_CITATIONS = text(
    """
    SELECT role::text, claim_lineage_id, claim_chunk_content_hash,
           relation_id, doc_id
    FROM knowledge_artifact_evidence
    WHERE artifact_id = :artifact_id
    """
)

_DELETE_CITATIONS = text(
    "DELETE FROM knowledge_artifact_evidence WHERE artifact_id = :artifact_id"
)

_INSERT_CITATION = text(
    """
    INSERT INTO knowledge_artifact_evidence (
        evidence_link_id, deployment_id, artifact_id,
        claim_lineage_id, claim_chunk_content_hash, relation_id, doc_id, role
    ) VALUES (
        :evidence_link_id, :deployment_id, :artifact_id,
        :claim_lineage_id, :claim_chunk_content_hash, :relation_id, :doc_id, :role
    )
    """
)

_INSERT_COMPILATION = text(
    """
    INSERT INTO knowledge_compilations (
        compilation_id, cycle_id, deployment_id, artifact_id, inputs_hash,
        candidate_count, cited_count, uncited_count, claims_cut_count,
        evidence_added, evidence_removed, evidence_invalidated,
        writer_version, tokens, cost_usd, session_transcript_uri,
        page_summary, content_hash, citations, suggestions
    ) VALUES (
        :compilation_id, :cycle_id, :deployment_id, :artifact_id, :inputs_hash,
        :candidate_count, :cited_count, :uncited_count, :claims_cut_count,
        :evidence_added, :evidence_removed, :evidence_invalidated,
        :writer_version, :tokens, :cost_usd, :session_transcript_uri,
        :page_summary, :content_hash, :citations, :suggestions
    )
    """
).bindparams(bindparam("citations", type_=JSON), bindparam("suggestions", type_=JSON))

_INSERT_FAILED_COMPILATION = text(
    """
    INSERT INTO knowledge_compilations (
        compilation_id, deployment_id, artifact_id, inputs_hash,
        candidate_count, cited_count, uncited_count, claims_cut_count,
        evidence_added, evidence_removed, evidence_invalidated,
        writer_version, session_transcript_uri, failed_at, failure
    )
    SELECT :compilation_id, :deployment_id, :artifact_id, :inputs_hash,
           :candidate_count, 0, :candidate_count, :claims_cut_count,
           0, 0, 0, :writer_version, :session_transcript_uri, now(), :failure
    FROM knowledge_artifacts
    WHERE artifact_id = :artifact_id
      AND deployment_id = :deployment_id
      AND page_kind = 'compiled'
      AND status IN ('active', 'stale')
    RETURNING compilation_id
    """
)

_SELECT_COMPILATION_STATE = text(
    """
    SELECT cycle_id, deployment_id, artifact_id, inputs_hash, writer_version,
           candidate_count, cited_count, uncited_count, claims_cut_count,
           evidence_invalidated, page_summary, content_hash, citations,
           suggestions, git_commit, failed_at
    FROM knowledge_compilations
    WHERE compilation_id = :compilation_id
    FOR UPDATE
    """
)

_SELECT_CYCLE_COMPILATION_IDS = text(
    """
    SELECT compilation_id
    FROM knowledge_compilations
    WHERE deployment_id = :deployment_id AND cycle_id = :cycle_id
    ORDER BY compilation_id
    FOR UPDATE
    """
)

_STAMP_COMPILATION_COMMIT = text(
    """
    UPDATE knowledge_compilations
    SET git_commit = :git_commit
    WHERE compilation_id = :compilation_id AND git_commit IS NULL
    """
)

_SELECT_COMPILE_ARTIFACTS = text(
    """
    SELECT artifact_id, deployment_id, scope_id, parent_artifact_id,
           git_path, curation_path, kind AS artifact_kind, page_summary, content_hash,
           status = 'stale' AS stale
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND page_kind = 'compiled'
      AND status IN ('active', 'stale')
    ORDER BY artifact_id
    """
)

_SELECT_ARTIFACT_GIT_PATHS = text(
    """
    SELECT git_path
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id AND status <> 'tombstoned'
    ORDER BY git_path
    """
)

_SELECT_COMPILED_CONTENT_STATES = text(
    """
    SELECT artifact_id, deployment_id, git_path, content_hash
    FROM knowledge_artifacts
    WHERE deployment_id = :deployment_id
      AND page_kind = 'compiled'
      AND status IN ('active','stale')
      AND content_hash IS NOT NULL
    ORDER BY artifact_id
    """
)

_SELECT_PLANNER_ARTIFACTS = text(
    """
    SELECT a.artifact_id, a.layer::text AS layer,
           a.page_kind::text AS page_kind, a.status::text AS status,
           a.git_path, a.scope_id, a.parent_artifact_id,
           a.kind AS artifact_kind,
           COALESCE(latest.candidate_count, 0) AS candidate_count,
           COALESCE(latest.uncited_count, 0) AS uncited_count,
           COALESCE(latest.suggestions, '[]'::jsonb) AS suggestions
    FROM knowledge_artifacts a
    LEFT JOIN LATERAL (
        SELECT c.candidate_count, c.uncited_count, c.suggestions
        FROM knowledge_compilations c
        WHERE c.artifact_id = a.artifact_id AND c.git_commit IS NOT NULL
        ORDER BY c.compiled_at DESC, c.compilation_id DESC
        LIMIT 1
    ) latest ON true
    WHERE a.deployment_id = :deployment_id
      AND a.scope_id IS NOT DISTINCT FROM :scope_id
      AND a.status <> 'tombstoned'
    ORDER BY a.git_path, a.artifact_id
    """
)

_SELECT_SCOPE_COMPILED_RULES = text(
    """
    SELECT r.rule_id, r.deployment_id,
           r.rule_kind::text AS rule_kind, r.params
    FROM knowledge_page_rules r
    JOIN knowledge_artifacts a
      ON a.deployment_id = r.deployment_id AND a.artifact_id = r.artifact_id
    WHERE r.deployment_id = :deployment_id
      AND a.scope_id IS NOT DISTINCT FROM :scope_id
      AND a.page_kind = 'compiled'
      AND a.status <> 'tombstoned'
      AND r.status = 'active'
    ORDER BY r.rule_id
    """
)

_SELECT_DELTA_CANDIDATE_ENTITIES = text(
    """
    WITH relation_candidates AS (
      SELECT DISTINCT entity_id,
             'fact:relation:' || r.relation_id::text AS candidate_key
      FROM relations r
      CROSS JOIN LATERAL (
        VALUES (r.subject_entity_id), (r.object_entity_id)
      ) endpoint(entity_id)
      WHERE r.deployment_id = :deployment_id
        AND (
          r.relation_id::text IN :relation_ids
          OR EXISTS (
            SELECT 1 FROM relation_evidence e
            WHERE e.deployment_id = r.deployment_id
              AND e.relation_id = r.relation_id
              AND e.doc_id::text IN :doc_ids
          )
        )
    ), observation_candidates AS (
      SELECT DISTINCT o.subject_entity_id AS entity_id,
             'fact:observation:' || o.observation_id::text AS candidate_key
      FROM observations o
      WHERE o.deployment_id = :deployment_id
        AND (
          o.observation_id::text IN :observation_ids
          OR EXISTS (
            SELECT 1 FROM observation_evidence e
            WHERE e.deployment_id = o.deployment_id
              AND e.observation_id = o.observation_id
              AND e.doc_id::text IN :doc_ids
          )
        )
    ), claim_candidates AS (
      SELECT DISTINCT rd.entity_id,
             'claim:' || c.doc_id::text || ':' || ch.chunk_content_hash
               AS candidate_key
      FROM claims c
      JOIN chunks ch
        ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
      JOIN mentions m
        ON m.deployment_id = c.deployment_id AND m.claim_id = c.claim_id
      JOIN resolution_decisions rd
        ON rd.deployment_id = m.deployment_id
       AND rd.mention_id = m.mention_id
       AND rd.superseded_by IS NULL
      WHERE c.deployment_id = :deployment_id
        AND c.is_current_testimony
        AND (c.claim_id::text IN :claim_ids OR c.doc_id::text IN :doc_ids)
    )
    SELECT entity_id, candidate_key FROM relation_candidates
    UNION
    SELECT entity_id, candidate_key FROM observation_candidates
    UNION
    SELECT entity_id, candidate_key FROM claim_candidates
    ORDER BY entity_id, candidate_key
    """
).bindparams(
    bindparam("relation_ids", expanding=True, type_=String()),
    bindparam("observation_ids", expanding=True, type_=String()),
    bindparam("claim_ids", expanding=True, type_=String()),
    bindparam("doc_ids", expanding=True, type_=String()),
)

_SELECT_PENDING_COMPILATIONS = text(
    """
    SELECT compilation_id, cycle_id, deployment_id, artifact_id, inputs_hash,
           candidate_count, uncited_count, claims_cut_count,
           evidence_invalidated, writer_version,
           page_summary, content_hash, citations, suggestions, tokens, cost_usd,
           session_transcript_uri
    FROM knowledge_compilations
    WHERE deployment_id = :deployment_id
      AND git_commit IS NULL
      AND failed_at IS NULL
      AND cycle_id IS NOT NULL
      AND page_summary IS NOT NULL
      AND content_hash IS NOT NULL
      AND citations IS NOT NULL
    ORDER BY compiled_at, artifact_id
    """
)

_FAIL_PENDING_CYCLE = text(
    """
    UPDATE knowledge_compilations
    SET failed_at = now(), failure = :failure
    WHERE deployment_id = :deployment_id
      AND cycle_id = :cycle_id
      AND git_commit IS NULL
      AND failed_at IS NULL
    """
)

_TRY_COMMIT_LEASE = text(
    """
    SELECT pg_try_advisory_lock(
        hashtextextended('k-commit:' || CAST(:deployment_id AS text), 0)
    )
    """
)

_RELEASE_COMMIT_LEASE = text(
    """
    SELECT pg_advisory_unlock(
        hashtextextended('k-commit:' || CAST(:deployment_id AS text), 0)
    )
    """
)

_UPDATE_COMPILED_ARTIFACT = text(
    """
    UPDATE knowledge_artifacts
    SET inputs_hash = :inputs_hash,
        writer_version = :writer_version,
        page_summary = :page_summary,
        content_hash = :content_hash,
        last_compiled_at = now(),
        status = 'active'
    WHERE artifact_id = :artifact_id
      AND page_kind = 'compiled'
      AND status IN ('active', 'stale')
    RETURNING artifact_id
    """
)

_CLAIM_COORDINATE_EXISTS = text(
    """
    SELECT EXISTS (
        SELECT 1
        FROM claims c
        JOIN chunks ch
          ON ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id
        WHERE c.deployment_id = :deployment_id
          AND c.doc_id = :lineage_id
          AND ch.chunk_content_hash = :chunk_content_hash
          AND c.is_current_testimony
    )
    """
)

_RELATION_EXISTS = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM relations
        WHERE deployment_id = :deployment_id AND relation_id = :evidence_id
    )
    """
)

_DOCUMENT_EXISTS = text(
    """
    SELECT EXISTS (
        SELECT 1 FROM documents
        WHERE deployment_id = :deployment_id AND doc_id = :evidence_id
    )
    """
)
