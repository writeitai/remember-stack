"""Pure WP-6.5 planner proposal and blast-band contracts."""

from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
import pytest

from rememberstack.core import knowledge_planning_input_hash
from rememberstack.core import primary_knowledge_plan_trigger
from rememberstack.core import route_knowledge_plan
from rememberstack.model import EntityRuleParams
from rememberstack.model import KnowledgeConvertKindProposal
from rememberstack.model import KnowledgeCreatePageProposal
from rememberstack.model import KnowledgeLayer
from rememberstack.model import KnowledgeOrphanAggregate
from rememberstack.model import KnowledgePageKind
from rememberstack.model import KnowledgePlanBand
from rememberstack.model import KnowledgePlannedPage
from rememberstack.model import KnowledgePlanningSnapshot
from rememberstack.model import KnowledgePlanRunKind
from rememberstack.model import KnowledgePlanTrigger

_DEPLOYMENT_ID = UUID("65000000-0000-0000-0000-000000000001")
_ENTITY_ID = UUID("65000000-0000-0000-0000-000000000002")
_ARTIFACT_ID = UUID("65000000-0000-0000-0000-000000000003")


def _page() -> KnowledgePlannedPage:
    """Return one complete planner-created compiled page."""
    return KnowledgePlannedPage(
        layer=KnowledgeLayer.K1,
        git_path="k/entities/acme.md",
        curation_path="k/entities/acme.curation.md",
        writer_version="writer-v1",
        rules=(EntityRuleParams(entity_id=_ENTITY_ID),),
    )


def test_planning_snapshot_hash_and_trigger_are_canonical() -> None:
    """Set ordering cannot perturb replay identity or the primary ledger trigger."""
    first = KnowledgePlanningSnapshot(
        deployment_id=_DEPLOYMENT_ID,
        artifacts=(),
        orphan_aggregates=(
            KnowledgeOrphanAggregate(
                entity_id=_ENTITY_ID,
                candidate_keys=("relation:b", "relation:a", "relation:a"),
            ),
        ),
    )
    second = KnowledgePlanningSnapshot(
        deployment_id=_DEPLOYMENT_ID,
        artifacts=(),
        orphan_aggregates=(
            KnowledgeOrphanAggregate(
                entity_id=_ENTITY_ID, candidate_keys=("relation:a", "relation:b")
            ),
        ),
    )

    assert knowledge_planning_input_hash(
        snapshot=first
    ) == knowledge_planning_input_hash(snapshot=second)
    assert (
        primary_knowledge_plan_trigger(
            snapshot=first, run_kind=KnowledgePlanRunKind.PLANNER
        )
        is KnowledgePlanTrigger.ORPHAN_EVIDENCE
    )
    assert (
        primary_knowledge_plan_trigger(
            snapshot=first, run_kind=KnowledgePlanRunKind.REFLECTION
        )
        is KnowledgePlanTrigger.REFLECTION
    )


def test_blast_radius_routes_only_low_impact_planner_work_automatically() -> None:
    """Expected impact is mechanical and the configured threshold is inclusive."""
    proposal = KnowledgeCreatePageProposal(
        rationale="House the new Acme evidence.",
        confidence=Decimal("0.8"),
        page=_page(),
    )

    band, impact = route_knowledge_plan(
        proposal=proposal,
        run_kind=KnowledgePlanRunKind.PLANNER,
        blast_radius=10,
        auto_apply_max_expected_impact=Decimal("2"),
    )
    review_band, review_impact = route_knowledge_plan(
        proposal=proposal,
        run_kind=KnowledgePlanRunKind.PLANNER,
        blast_radius=11,
        auto_apply_max_expected_impact=Decimal("2"),
    )

    assert (band, impact) == (KnowledgePlanBand.AUTO_APPLY, Decimal("2.0"))
    assert (review_band, review_impact) == (KnowledgePlanBand.REVIEW, Decimal("2.2"))


def test_reflection_and_authored_handover_never_auto_apply() -> None:
    """Fresh-eyes proposals and ownership surrender always reach an accountable reviewer."""
    create = KnowledgeCreatePageProposal(
        rationale="Reflection found a navigation dead end.",
        confidence=Decimal("1"),
        page=_page(),
    )
    handover = KnowledgeConvertKindProposal(
        artifact_id=_ARTIFACT_ID,
        from_kind=KnowledgePageKind.AUTHORED,
        to_kind=KnowledgePageKind.COMPILED,
        writer_version="writer-v1",
        curation_path="k/decision.curation.md",
        rules=(EntityRuleParams(entity_id=_ENTITY_ID),),
        rationale="The author says the page is fully evidence-backed.",
        confidence=Decimal("1"),
    )

    reflection_band, _ = route_knowledge_plan(
        proposal=create,
        run_kind=KnowledgePlanRunKind.REFLECTION,
        blast_radius=1,
        auto_apply_max_expected_impact=Decimal("100"),
    )
    handover_band, _ = route_knowledge_plan(
        proposal=handover,
        run_kind=KnowledgePlanRunKind.PLANNER,
        blast_radius=1,
        auto_apply_max_expected_impact=Decimal("100"),
    )

    assert reflection_band is KnowledgePlanBand.REVIEW
    assert handover_band is KnowledgePlanBand.REVIEW


def test_kind_conversion_rejects_hybrid_ownership_inputs() -> None:
    """Adoption cannot retain writer state and handover cannot omit its compile contract."""
    with pytest.raises(ValidationError):
        KnowledgeConvertKindProposal(
            artifact_id=_ARTIFACT_ID,
            from_kind=KnowledgePageKind.COMPILED,
            to_kind=KnowledgePageKind.AUTHORED,
            writer_version="writer-v1",
            rationale="Invalid hybrid adoption.",
            confidence=Decimal("1"),
        )
    with pytest.raises(ValidationError):
        KnowledgeConvertKindProposal(
            artifact_id=_ARTIFACT_ID,
            from_kind=KnowledgePageKind.AUTHORED,
            to_kind=KnowledgePageKind.COMPILED,
            rationale="Incomplete handover.",
            confidence=Decimal("1"),
        )
