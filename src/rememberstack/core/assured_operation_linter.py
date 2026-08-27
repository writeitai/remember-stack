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
    AssuredOperationName.TESTIMONY_CONTEXT: (
        AssuredResultContract.ENVELOPE,
        Grain.EVIDENCE,
        AssuredAnswerIntent.TESTIMONY,
    ),
    AssuredOperationName.FACT_CONTEXT: (
        AssuredResultContract.ENVELOPE,
        Grain.FACT,
        AssuredAnswerIntent.FACTS,
    ),
    AssuredOperationName.ANSWER_CONTEXT: (
        AssuredResultContract.CONTEXT_BUNDLE_V1,
        None,
        AssuredAnswerIntent.COMBINED_CONTEXT,
    ),
}

_PRIMITIVE_CHAINS = {
    AssuredOperationName.RESOLVE_ENTITY: ("resolve_entity",),
    AssuredOperationName.TESTIMONY_CONTEXT: ("testimony_context",),
    AssuredOperationName.FACT_CONTEXT: ("graph_neighborhood", "fact_context"),
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
    if operation.version != 1:
        raise AssuredOperationLintError(
            f"operation {operation.name.value!r} must use canonical version 1"
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
    if operation.name is AssuredOperationName.ANSWER_CONTEXT:
        if not isinstance(operation.execution_plan, OperationBundlePlan):
            raise AssuredOperationLintError(
                "answer_context must use the exact operation_bundle plan"
            )
        if operation.execution_plan.children != ("testimony_context", "fact_context"):
            raise AssuredOperationLintError(
                "answer_context must bundle testimony_context then fact_context"
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
