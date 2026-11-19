"""Repeatable S58 cold-harness protocol for the rendered consumption skill."""

from typing import Final
from uuid import UUID
from uuid import uuid5

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.eval.harness import CaseEvaluator
from rememberstack.eval.skeleton import make_skeleton_evaluator
from rememberstack.model import CanaryCase
from rememberstack.model import ModelRequest
from rememberstack.model import RenderedConsumptionSkill
from rememberstack.model import S58Answer
from rememberstack.ports.model_provider import ModelProviderPort
from rememberstack.surfaces.query_engine import QueryEngine

_CANARY_NAMESPACE: Final = UUID("55800000-0000-4000-8000-000000000000")

S58_CANARIES: Final[tuple[dict[str, object], ...]] = (
    {
        "description": "S58: a cold agent plans a grain-safe memory answer",
        "input": {
            "scenario": "s58",
            "task": (
                "Brief me on Acme, determine whether Alice currently works for "
                "Acme, and show what the sources said. Plane K may be empty. "
                "One candidate fact has withdrawn support and a live "
                "contradiction group. Choose how to orient, verify, and audit. "
                "Browse a source's original and derivatives from the corpus root, "
                "find it again after a title/topic move, and open an exact "
                "historical edition that is not the served folder."
            ),
        },
        "expected": {
            "orientation": "knowledge",
            "empty_knowledge": "fallback_p3_or_search",
            "current_truth": "fact_lookup",
            "grain_handling": "separate",
            "withdrawn_support": "caveat_and_transcript",
            "claims_as_of": "assertion_history_only",
            "contradictions": "report_co_members",
            "readable_content": "prefer_mounts",
            "audit": "hydrate_to_sources",
            "corpus_navigation": "indexes_to_reader_files",
            "moved_folder": "resolve_or_suffix_find",
            "exact_edition": "version_source_open",
        },
    },
)


def seed_s58_canaries(*, engine: Engine, deployment_id: UUID) -> None:
    """Insert or refresh the stable S58 retrieval canary for one deployment."""
    with engine.begin() as connection:
        for canary in S58_CANARIES:
            connection.execute(
                _INSERT_CANARY,
                {
                    "canary_id": uuid5(
                        _CANARY_NAMESPACE, f"{deployment_id}:{canary['description']}"
                    ),
                    "deployment_id": deployment_id,
                    "description": canary["description"],
                    "input": canary["input"],
                    "expected": canary["expected"],
                },
            )


def make_s58_evaluator(
    *, model_provider: ModelProviderPort, model: str, skill: RenderedConsumptionSkill
) -> CaseEvaluator:
    """Build the S58 evaluator whose only system context is the rendered skill."""

    def evaluate(case: CanaryCase) -> bool:
        """Ask one cold model for a plan and compare its structured decisions."""
        if case.input.get("scenario") != "s58":
            return False
        task = case.input.get("task")
        if not isinstance(task, str) or not task.strip():
            return False
        expected = S58Answer.model_validate(case.expected)
        answer = model_provider.generate(
            request=ModelRequest(model=model, prompt=_prompt(skill=skill, task=task)),
            response_type=S58Answer,
        )
        return answer.output == expected

    return evaluate


def make_retrieval_evaluator(
    *,
    query_engine: QueryEngine,
    deployment_id: UUID,
    model_provider: ModelProviderPort,
    model: str,
    skill: RenderedConsumptionSkill,
) -> CaseEvaluator:
    """Compose the walking-skeleton and S58 cases under one retrieval suite."""
    skeleton = make_skeleton_evaluator(
        query_engine=query_engine, deployment_id=deployment_id
    )
    s58 = make_s58_evaluator(model_provider=model_provider, model=model, skill=skill)

    def evaluate(case: CanaryCase) -> bool:
        """Dispatch S58 to the cold harness and earlier cases to the query engine."""
        if case.input.get("scenario") == "s58":
            return s58(case)
        return skeleton(case)

    return evaluate


def _prompt(*, skill: RenderedConsumptionSkill, task: str) -> str:
    """Build a cold prompt containing no project context beyond skill + task."""
    return (
        "You are a cold agent evaluating a memory-consumption skill. You have "
        "never seen this memory system. Treat the skill below as your only "
        "knowledge of it; do not rely on outside conventions or guess missing "
        "capabilities. Select the best structured action for every field.\n\n"
        "<consumption-skill>\n"
        f"{skill.content}"
        "</consumption-skill>\n\n"
        "<task>\n"
        f"{task}\n"
        "</task>"
    )


_INSERT_CANARY = text(
    """
    INSERT INTO canary_cases (
        canary_id, deployment_id, suite, description, input, expected
    ) VALUES (
        :canary_id, :deployment_id, 'retrieval', :description, :input, :expected
    )
    ON CONFLICT (canary_id) DO UPDATE
        SET description = EXCLUDED.description,
            input = EXCLUDED.input,
            expected = EXCLUDED.expected
    """
).bindparams(bindparam("input", type_=JSON), bindparam("expected", type_=JSON))
