"""Shared HTTP/SDK/CLI/MCP rendering for the four assured operations."""

from datetime import datetime
from datetime import UTC
import hashlib
import json
from typing import cast
from uuid import UUID

from pydantic import TypeAdapter
from pydantic import ValidationError

from rememberstack.model import AssuredOperation
from rememberstack.model import ContextBundleV1
from rememberstack.model import Envelope
from rememberstack.model.assured_operations import AtFactTime
from rememberstack.model.assured_operations import FactTime
from rememberstack.model.assured_operations import OverlapFactTime
from rememberstack.model.client import ToolDescriptor
from rememberstack.spine.assured_operations import AssuredOperationRegistry
from rememberstack.spine.query_space.canonical import canonical_json_bytes
from rememberstack.spine.query_space.canonical import CanonicalValue
from rememberstack.surfaces.operation_executor import OperationExecutor


class UnknownOperationError(Exception):
    """The requested name is outside the closed operation catalog."""


class MissingArgumentError(Exception):
    """A required operation parameter was omitted."""


class InvalidArgumentError(Exception):
    """An operation argument violates its closed input schema."""


OperationResult = Envelope | ContextBundleV1

_FACT_TIME: TypeAdapter[FactTime] = TypeAdapter(FactTime)


class OperationSurface:
    """Render and run one deployment's canonical assured operations."""

    def __init__(
        self,
        *,
        registry: AssuredOperationRegistry,
        executor: OperationExecutor,
        deployment_id: UUID,
    ) -> None:
        """Bind one registry, executor, and deployment trust domain."""
        self._registry = registry
        self._executor = executor
        self._deployment_id = deployment_id

    @property
    def deployment_id(self) -> UUID:
        """Return the one deployment this surface serves."""
        return self._deployment_id

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        """Render the complete four-operation catalog."""
        return operation_descriptors(
            operations=self._registry.active(deployment_id=self._deployment_id)
        )

    def run(self, *, name: str, arguments: dict[str, object]) -> OperationResult:
        """Validate transport arguments and run one canonical operation."""
        operation = self._registry.by_name(deployment_id=self._deployment_id, name=name)
        if operation is None:
            raise UnknownOperationError(name)
        return self._executor.execute(
            deployment_id=self._deployment_id,
            operation=operation,
            arguments=_coerce_arguments(operation=operation, arguments=arguments),
        )


def operation_descriptors(
    *, operations: tuple[AssuredOperation, ...]
) -> tuple[ToolDescriptor, ...]:
    """Render canonical registry rows with stable, child-aware plan hashes."""
    plan_hashes = {
        operation.name.value: _plan_hash(operation=operation, child_hashes={})
        for operation in operations
        if operation.name.value != "answer_context"
    }
    return tuple(
        _descriptor(operation=operation, child_hashes=plan_hashes)
        for operation in operations
    )


def _descriptor(
    *, operation: AssuredOperation, child_hashes: dict[str, str]
) -> ToolDescriptor:
    """Render one descriptor into the shared public tool contract."""
    properties: dict[str, object] = {}
    required: list[str] = []
    for name, raw in operation.parameters.items():
        spec = dict(raw) if isinstance(raw, dict) else {"type": "string"}
        if spec.pop("required", False):
            required.append(name)
        properties[name] = spec
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        input_schema["required"] = sorted(required)
    return ToolDescriptor(
        name=operation.name.value,
        description=operation.description,
        input_schema=input_schema,
        result_schema=operation.result_schema,
        result_contract=operation.result_contract.value,
        output_grain=(
            None if operation.output_grain is None else operation.output_grain.value
        ),
        answer_intent=operation.answer_intent.value,
        version=operation.version,
        implementation_plan_hash=_plan_hash(
            operation=operation, child_hashes=child_hashes
        ),
    )


def _plan_hash(*, operation: AssuredOperation, child_hashes: dict[str, str]) -> str:
    """Hash one plan; bundle identity includes the ordered child plan hashes."""
    payload: dict[str, object] = {
        "plan": operation.execution_plan.model_dump(mode="json")
    }
    if operation.name.value == "answer_context":
        payload["child_plan_hashes"] = [
            child_hashes[name] for name in ("testimony_context", "fact_context")
        ]
    return hashlib.sha256(
        canonical_json_bytes(cast("CanonicalValue", payload))
    ).hexdigest()


def _coerce_arguments(
    *, operation: AssuredOperation, arguments: dict[str, object]
) -> dict[str, object]:
    """Coerce one transport object according to the canonical parameter schema."""
    unknown = set(arguments) - set(operation.parameters)
    if unknown:
        raise InvalidArgumentError(f"unknown argument(s): {', '.join(sorted(unknown))}")
    coerced: dict[str, object] = {}
    for name, raw in operation.parameters.items():
        spec = raw if isinstance(raw, dict) else {}
        if name not in arguments:
            if spec.get("required"):
                raise MissingArgumentError(name)
            continue
        try:
            coerced[name] = _coerce_value(
                name=name, value=arguments[name], declared=str(spec.get("type"))
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise InvalidArgumentError(f"invalid {name}: {error}") from error
        _validate_facets(name=name, value=coerced[name], spec=spec)
    if "candidate_k" in coerced and cast(int, coerced["candidate_k"]) < cast(
        int, coerced.get("k", 50)
    ):
        raise InvalidArgumentError("candidate_k cannot be smaller than k")
    return coerced


def _coerce_value(*, name: str, value: object, declared: str) -> object:
    """Coerce one value without accepting lossy or ambiguous conversions."""
    if declared == "string":
        if not isinstance(value, str):
            raise TypeError("expected a string")
        return value
    if declared == "integer":
        if isinstance(value, bool):
            raise ValueError("expected an integer, got a boolean")
        integer = int(str(value))
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("expected an integer")
        return integer
    if declared == "array":
        raw = json.loads(value) if isinstance(value, str) else value
        if not isinstance(raw, (list, tuple)):
            raise TypeError("expected an array")
        return tuple(UUID(str(item)) for item in raw)
    if declared == "object" and name == "time":
        raw = json.loads(value) if isinstance(value, str) else value
        fact_time = _FACT_TIME.validate_python(raw)
        if isinstance(fact_time, AtFactTime):
            at = (
                fact_time.at.replace(tzinfo=UTC)
                if fact_time.at.tzinfo is None
                else fact_time.at.astimezone(UTC)
            )
            fact_time = fact_time.model_copy(update={"at": at})
        if isinstance(fact_time, OverlapFactTime):
            from_ = fact_time.from_
            to = fact_time.to
            from_ = (
                from_.replace(tzinfo=UTC)
                if from_.tzinfo is None
                else from_.astimezone(UTC)
            )
            to = to.replace(tzinfo=UTC) if to.tzinfo is None else to.astimezone(UTC)
            if to < from_:
                raise ValueError("time.to must be at or after time.from")
            fact_time = fact_time.model_copy(update={"from_": from_, "to": to})
        return fact_time
    if declared == "timestamp":
        instant = (
            value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        )
        return instant.replace(tzinfo=UTC) if instant.tzinfo is None else instant
    return value


def _validate_facets(*, name: str, value: object, spec: dict[str, object]) -> None:
    """Apply the descriptor bounds not covered by type coercion."""
    size = len(value) if isinstance(value, (str, tuple)) else None
    for facet, comparison in (
        ("minLength", lambda actual, limit: actual >= limit),
        ("maxLength", lambda actual, limit: actual <= limit),
        ("minItems", lambda actual, limit: actual >= limit),
        ("maxItems", lambda actual, limit: actual <= limit),
    ):
        if (
            facet in spec
            and size is not None
            and not comparison(size, cast(int, spec[facet]))
        ):
            raise InvalidArgumentError(f"{name} violates {facet}={spec[facet]}")
    if (
        spec.get("uniqueItems")
        and isinstance(value, tuple)
        and len(set(value)) != len(value)
    ):
        raise InvalidArgumentError(f"{name} must contain unique values")
    if isinstance(value, int):
        if "minimum" in spec and value < cast(int, spec["minimum"]):
            raise InvalidArgumentError(f"{name} is below its minimum")
        if "maximum" in spec and value > cast(int, spec["maximum"]):
            raise InvalidArgumentError(f"{name} exceeds its maximum")
