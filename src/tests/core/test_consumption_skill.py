"""Pure contract tests for the open-query consumption skill (Batch F rewrite)."""

from uuid import UUID

from rememberstack.core import CONSUMPTION_SKILL_VERSION
from rememberstack.core import render_consumption_skill
from rememberstack.core.open_query_prose import TWO_LAYER_HEADLINE_FULL
from rememberstack.core.open_query_prose import TWO_LAYER_HEADLINE_NOTE
from rememberstack.core.open_query_prose import WRONG_CLAIM_WINDOW_CURRENT_TRUTH_SQL
from rememberstack.model import AssuredAnswerIntent
from rememberstack.model import ConsumptionDeployment
from rememberstack.model import ConsumptionOperation
from rememberstack.model import ConsumptionScope
from rememberstack.model import ConsumptionSkillContext
from rememberstack.model import Grain
from rememberstack.model import PublishedMounts

_DEPLOYMENT_ID = UUID("55000000-0000-0000-0000-000000000001")


def _context(
    *, mounted: bool, knowledge_page_count: int, operations: bool = True
) -> ConsumptionSkillContext:
    """Build one small deployment context for renderer proofs."""
    mounts = (
        PublishedMounts(
            deployment_id=_DEPLOYMENT_ID,
            p3="/memory/corpus",
            artifacts="/memory/artifacts",
            raw="/memory/raw",
            knowledge="/memory/knowledge",
            read_only=True,
        )
        if mounted
        else None
    )
    return ConsumptionSkillContext(
        deployment=ConsumptionDeployment(
            deployment_id=_DEPLOYMENT_ID,
            slug="acme-memory",
            name="Acme migration",
            description="Evidence for the migration programme",
            default_language="en",
            scopes=(
                ConsumptionScope(
                    slug="target-state",
                    name="Target state",
                    git_path="scopes/target-state",
                ),
            ),
            knowledge_page_count=knowledge_page_count,
        ),
        operations=(
            (
                ConsumptionOperation(
                    name="resolve_entity",
                    description="Resolve a name to survivor entities.",
                    output_grain=Grain.FACT,
                    answer_intent=AssuredAnswerIntent.IDENTITY,
                ),
                ConsumptionOperation(
                    name="testimony_context",
                    description="High-recall source testimony.",
                    output_grain=Grain.EVIDENCE,
                    answer_intent=AssuredAnswerIntent.TESTIMONY,
                ),
                ConsumptionOperation(
                    name="fact_context",
                    description="Evidence-backed facts across a requested time scope.",
                    output_grain=Grain.FACT,
                    answer_intent=AssuredAnswerIntent.FACTS,
                ),
                ConsumptionOperation(
                    name="answer_context",
                    description="Both authorities, kept side by side.",
                    output_grain=None,
                    answer_intent=AssuredAnswerIntent.COMBINED_CONTEXT,
                ),
            )
            if operations
            else ()
        ),
        mounts=mounts,
    )


def test_rendered_skill_opens_with_bound_headline_and_open_surface() -> None:
    """The skill leads with the two-layer headline and open-query choices."""
    skill = render_consumption_skill(
        context=_context(mounted=True, knowledge_page_count=2)
    )

    assert skill.version == CONSUMPTION_SKILL_VERSION == "3.0.0"
    assert skill.filename == "SKILL.md"
    assert TWO_LAYER_HEADLINE_FULL in skill.content
    assert TWO_LAYER_HEADLINE_NOTE in skill.content
    assert "Choose how to query" in skill.content
    assert "query_sql" in skill.content
    assert "query_cypher" not in skill.content
    assert WRONG_CLAIM_WINDOW_CURRENT_TRUTH_SQL in skill.content
    assert 'Questions of the form "is this true now?" go here' in skill.content
    assert "`support: withdrawn`" in skill.content
    assert "report the competing sides" in skill.content
    assert "prefer them for navigation, reading, and grep" in skill.content
    assert "/memory/corpus" in skill.content
    assert "`target-state`" in skill.content
    assert "`resolve_entity`" in skill.content
    assert "`testimony_context`" in skill.content
    assert "`fact_context`" in skill.content
    # The old intent-first steering is gone.
    assert "Default motion: orient, verify, audit" not in skill.content


def test_empty_k_and_unmounted_surfaces_degrade_honestly() -> None:
    """No K pages or mounts produces explicit fallbacks, never fake availability."""
    skill = render_consumption_skill(
        context=_context(mounted=False, knowledge_page_count=0)
    )

    assert "known empty: no K pages are registered" in skill.content
    assert "Use open SQL" in skill.content
    assert "Prefer open" not in skill.content
    assert "No mounts are available in this harness" in skill.content
    assert "/memory/corpus" not in skill.content


def test_only_enabled_operations_are_advertised() -> None:
    """An empty registry falls back to the closed canonical operation names."""
    skill = render_consumption_skill(
        context=_context(mounted=False, knowledge_page_count=0, operations=False)
    )

    assert "`testimony_context`" in skill.content
    assert "`fact_context`" in skill.content
    assert "there is no compatibility operation catalog" in skill.content
    assert "include_superseded_testimony" not in skill.content


def test_noncore_rows_are_not_advertised_as_assured_operations() -> None:
    """A stale noncore row cannot revive the removed compatibility catalog."""
    context = _context(mounted=False, knowledge_page_count=0, operations=False)
    context = context.model_copy(
        update={
            "operations": (
                ConsumptionOperation(
                    name="claims_as_of",
                    description="Historical source assertions.",
                    output_grain=Grain.EVIDENCE,
                    answer_intent=AssuredAnswerIntent.TESTIMONY,
                ),
            )
        }
    )

    skill = render_consumption_skill(context=context)

    assert "`claims_as_of` —" not in skill.content
    assert "`examples.claims_as_of`" in skill.content
