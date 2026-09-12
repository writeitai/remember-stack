"""Mechanical validation for the closed D87 assured-operation catalog."""

from rememberstack.model import AssuredAnswerIntent
from rememberstack.model import AssuredOperation
from rememberstack.model import AssuredOperationName
from rememberstack.model import AssuredResultContract
from rememberstack.model import Grain
from rememberstack.model import OperationBundlePlan
from rememberstack.model import PrimitiveChainPlan


class AssuredOperationLintError(Exception):
    """An operation descriptor does not match its closed authority contract."""


_CONTRACTS = {
    AssuredOperationName.RESOLVE_ENTITY: (
        AssuredResultContract.ENVELOPE,
        Grain.FACT,
        AssuredAnswerIntent.IDENTITY,
    ),
    AssuredOperationName.CLAIMS_AND_SOURCES_CONTEXT: (
        AssuredResultContract.ENVELOPE,
        Grain.EVIDENCE,
        AssuredAnswerIntent.CLAIMS_AND_SOURCES,
    ),
    AssuredOperationName.FACTS_CONTEXT: (
        AssuredResultContract.ENVELOPE,
        Grain.FACT,
        AssuredAnswerIntent.FACTS,
    ),
    AssuredOperationName.COMBINED_CONTEXT: (
        AssuredResultContract.CONTEXT_BUNDLE_V2,
        None,
        AssuredAnswerIntent.COMBINED_CONTEXT,
    ),
}

_PRIMITIVE_CHAINS = {
    AssuredOperationName.RESOLVE_ENTITY: ("resolve_entity",),
    AssuredOperationName.CLAIMS_AND_SOURCES_CONTEXT: ("claims_and_sources_context",),
    AssuredOperationName.FACTS_CONTEXT: ("graph_neighborhood", "facts_context"),
}

_VERSIONS = {
    AssuredOperationName.RESOLVE_ENTITY: 1,
    AssuredOperationName.CLAIMS_AND_SOURCES_CONTEXT: 2,
    AssuredOperationName.FACTS_CONTEXT: 3,
    AssuredOperationName.COMBINED_CONTEXT: 4,
}


def lint_assured_operation(
    operation: AssuredOperation, *, expected: AssuredOperation
) -> None:
    """Reject any descriptor that diverges from its canonical operation."""
    if operation.name is not expected.name:
        raise AssuredOperationLintError(
            f"expected canonical operation {expected.name.value!r},"
            f" got {operation.name.value!r}"
        )
    expected_version = _VERSIONS[operation.name]
    if operation.version != expected_version:
        raise AssuredOperationLintError(
            f"operation {operation.name.value!r} must use canonical version"
            f" {expected_version}"
        )
    expected_contract = _CONTRACTS[operation.name]
    actual = (
        operation.result_contract,
        operation.output_grain,
        operation.answer_intent,
    )
    if actual != expected_contract:
        raise AssuredOperationLintError(
            f"operation {operation.name.value!r} has contract tuple {actual!r};"
            f" expected {expected_contract!r}"
        )
    if operation.name is AssuredOperationName.COMBINED_CONTEXT:
        if not isinstance(operation.execution_plan, OperationBundlePlan):
            raise AssuredOperationLintError(
                "combined_context must use the exact operation_bundle plan"
            )
        if operation.execution_plan.children != (
            "claims_and_sources_context",
            "facts_context",
        ):
            raise AssuredOperationLintError(
                "combined_context must bundle claims_and_sources_context then facts_context"
            )
    else:
        if not isinstance(operation.execution_plan, PrimitiveChainPlan):
            raise AssuredOperationLintError(
                f"{operation.name.value} must use a primitive_chain plan"
            )
        expected_steps = _PRIMITIVE_CHAINS[operation.name]
        if tuple(step.op for step in operation.execution_plan.steps) != expected_steps:
            raise AssuredOperationLintError(
                f"{operation.name.value} must use canonical primitive chain"
                f" {expected_steps!r}"
            )
    if operation != expected:
        raise AssuredOperationLintError(
            f"operation {operation.name.value!r} must match its canonical descriptor exactly"
        )
