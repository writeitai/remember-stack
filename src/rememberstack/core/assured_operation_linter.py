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


def lint_assured_operation(operation: AssuredOperation) -> None:
    """Reject any descriptor that diverges from the four canonical tuples."""
    expected = _CONTRACTS[operation.name]
    actual = (
        operation.result_contract,
        operation.output_grain,
        operation.answer_intent,
    )
    if actual != expected:
        raise AssuredOperationLintError(
            f"operation {operation.name.value!r} has contract tuple {actual!r};"
            f" expected {expected!r}"
        )
    if operation.name is AssuredOperationName.ANSWER_CONTEXT:
        if not isinstance(operation.execution_plan, OperationBundlePlan):
            raise AssuredOperationLintError(
                "answer_context must use the exact operation_bundle plan"
            )
        if operation.execution_plan.children != (
            "testimony_context",
            "fact_context",
        ):
            raise AssuredOperationLintError(
                "answer_context must bundle testimony_context then fact_context"
            )
        return
    if not isinstance(operation.execution_plan, PrimitiveChainPlan):
        raise AssuredOperationLintError(
            f"{operation.name.value} must use a primitive_chain plan"
        )
    if tuple(step.op for step in operation.execution_plan.steps) != (
        operation.name.value,
    ):
        raise AssuredOperationLintError(
            f"{operation.name.value} must delegate to its same-named authority"
        )
