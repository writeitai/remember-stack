"""Closed registry for the four D87 assured operations."""

import json
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import RowMapping
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.core.assured_operation_linter import AssuredOperationLintError
from rememberstack.core.assured_operation_linter import lint_assured_operation
from rememberstack.model import AssuredAnswerIntent
from rememberstack.model import AssuredOperation
from rememberstack.model import AssuredOperationName
from rememberstack.model import AssuredResultContract
from rememberstack.model import ContextBundleV1
from rememberstack.model import Envelope
from rememberstack.model import Grain
from rememberstack.model import OperationBundlePlan
from rememberstack.model import OperationStep
from rememberstack.model import PrimitiveChainPlan


class AssuredOperationRegistry:
    """Write and read one deployment's four canonical operation rows."""

    def __init__(self, *, engine: Engine) -> None:
        """Bind the registry to the authoritative PostgreSQL spine."""
        self._engine = engine

    def register(self, *, deployment_id: UUID, operation: AssuredOperation) -> None:
        """Validate and idempotently register one canonical operation version."""
        _lint_canonical_operation(operation=operation)
        with self._engine.begin() as connection:
            connection.execute(
                _INSERT_OPERATION,
                _registration_values(deployment_id=deployment_id, operation=operation),
            )

    def active(self, *, deployment_id: UUID) -> tuple[AssuredOperation, ...]:
        """Return exactly the canonical active versions, ordered by name."""
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    _ACTIVE_OPERATIONS,
                    {
                        "deployment_id": deployment_id,
                        "names": [name.value for name in AssuredOperationName],
                    },
                )
                .mappings()
                .all()
            )
        return tuple(_operation_from_row(row) for row in rows)

    def by_name(self, *, deployment_id: UUID, name: str) -> AssuredOperation | None:
        """Return one canonical active operation, never an arbitrary row."""
        try:
            canonical = AssuredOperationName(name)
        except ValueError:
            return None
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    _OPERATION_BY_NAME,
                    {
                        "deployment_id": deployment_id,
                        "name": canonical.value,
                        "version": 1,
                    },
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _operation_from_row(row)

    def replace_all(
        self, *, deployment_id: UUID, operations: tuple[AssuredOperation, ...]
    ) -> int:
        """Atomically replace the deployment catalog with the canonical set."""
        if {operation.name for operation in operations} != set(AssuredOperationName):
            raise ValueError(
                "the assured-operation catalog must contain all four names"
            )
        for operation in operations:
            _lint_canonical_operation(operation=operation)
        with self._engine.begin() as connection:
            connection.execute(_DELETE_OPERATIONS, {"deployment_id": deployment_id})
            for operation in operations:
                connection.execute(
                    _INSERT_OPERATION,
                    _registration_values(
                        deployment_id=deployment_id, operation=operation
                    ),
                )
        return len(operations)


def _registration_values(
    *, deployment_id: UUID, operation: AssuredOperation
) -> dict[str, object]:
    """Render one typed operation into its registry row."""
    return {
        "operation_id": uuid4(),
        "deployment_id": deployment_id,
        "name": operation.name.value,
        "description": operation.description,
        "parameters": operation.parameters,
        "result_schema": operation.result_schema,
        "execution_plan": operation.execution_plan.model_dump(mode="json"),
        "result_contract": operation.result_contract.value,
        "output_grain": (
            None if operation.output_grain is None else operation.output_grain.value
        ),
        "answer_intent": operation.answer_intent.value,
        "version": operation.version,
    }


def _json_value(value: object) -> object:
    """Decode JSON text returned by drivers while accepting native jsonb values."""
    return json.loads(str(value)) if isinstance(value, str) else value


def _operation_from_row(row: RowMapping) -> AssuredOperation:
    """Rebuild one typed descriptor from its registry row."""
    operation = AssuredOperation.model_validate(
        {
            "name": row["name"],
            "description": row["description"],
            "parameters": _json_value(row["parameters"]),
            "result_schema": _json_value(row["result_schema"]),
            "execution_plan": _json_value(row["execution_plan"]),
            "result_contract": row["result_contract"],
            "output_grain": row["output_grain"],
            "answer_intent": row["answer_intent"],
            "version": row["version"],
        }
    )
    _lint_canonical_operation(operation=operation)
    return operation


_TIME_SCHEMA = {
    "type": "object",
    "default": {"mode": "current"},
    "oneOf": [
        {
            "properties": {"mode": {"const": "current"}},
            "required": ["mode"],
            "additionalProperties": False,
        },
        {
            "properties": {
                "mode": {"const": "at"},
                "at": {"type": "string", "format": "date-time"},
            },
            "required": ["mode", "at"],
            "additionalProperties": False,
        },
        {
            "properties": {
                "mode": {"const": "overlap"},
                "from": {"type": "string", "format": "date-time"},
                "to": {"type": "string", "format": "date-time"},
            },
            "required": ["mode", "from", "to"],
            "additionalProperties": False,
        },
        {
            "properties": {"mode": {"const": "history"}},
            "required": ["mode"],
            "additionalProperties": False,
        },
    ],
}

_ENTITY_IDS = {
    "type": "array",
    "required": False,
    "items": {"type": "string", "format": "uuid"},
    "minItems": 1,
    "maxItems": 20,
    "uniqueItems": True,
}

_QUERY = {"type": "string", "required": True, "minLength": 1, "maxLength": 8192}

_HOPS = {"type": "integer", "required": False, "default": 1, "minimum": 1, "maximum": 2}

_PREDICATE = {"type": "string", "required": False, "minLength": 1, "maxLength": 255}


def _envelope_schema() -> dict[str, object]:
    """Return the exact D49 envelope JSON schema stored in descriptors."""
    return Envelope.model_json_schema(mode="serialization")


CANONICAL_OPERATIONS: tuple[AssuredOperation, ...] = (
    AssuredOperation(
        name=AssuredOperationName.RESOLVE_ENTITY,
        description=(
            "Resolve a name to ranked current survivor candidates; never silently guess."
        ),
        parameters={"name": {"type": "string", "required": True, "minLength": 1}},
        result_schema=_envelope_schema(),
        execution_plan=PrimitiveChainPlan(steps=(OperationStep(op="resolve_entity"),)),
        result_contract=AssuredResultContract.ENVELOPE,
        output_grain=Grain.FACT,
        answer_intent=AssuredAnswerIntent.IDENTITY,
    ),
    AssuredOperation(
        name=AssuredOperationName.TESTIMONY_CONTEXT,
        description=(
            "High-recall current testimony: confirmed claims and source passages only."
        ),
        parameters={
            "query": _QUERY,
            "entity_ids": _ENTITY_IDS,
            "k": {
                "type": "integer",
                "required": False,
                "default": 50,
                "minimum": 1,
                "maximum": 100,
            },
            "candidate_k": {
                "type": "integer",
                "required": False,
                "default": 200,
                "minimum": 1,
                "maximum": 400,
            },
        },
        result_schema=_envelope_schema(),
        execution_plan=PrimitiveChainPlan(
            steps=(OperationStep(op="testimony_context"),)
        ),
        result_contract=AssuredResultContract.ENVELOPE,
        output_grain=Grain.EVIDENCE,
        answer_intent=AssuredAnswerIntent.TESTIMONY,
    ),
    AssuredOperation(
        name=AssuredOperationName.FACT_CONTEXT,
        description=(
            "Adjudicated relations and observations under an explicit world-time"
            " scope, with bounded P2 expansion for current or point-in-time entity"
            " anchors."
        ),
        parameters={
            "query": _QUERY,
            "entity_ids": _ENTITY_IDS,
            "k": {
                "type": "integer",
                "required": False,
                "default": 15,
                "minimum": 1,
                "maximum": 30,
            },
            "evidence_per_fact": {
                "type": "integer",
                "required": False,
                "default": 3,
                "minimum": 1,
                "maximum": 5,
            },
            "hops": _HOPS,
            "predicate": _PREDICATE,
            "time": {**_TIME_SCHEMA, "required": False},
        },
        result_schema=_envelope_schema(),
        execution_plan=PrimitiveChainPlan(
            steps=(
                OperationStep(op="graph_neighborhood"),
                OperationStep(op="fact_context"),
            )
        ),
        result_contract=AssuredResultContract.ENVELOPE,
        output_grain=Grain.FACT,
        answer_intent=AssuredAnswerIntent.FACTS,
    ),
    AssuredOperation(
        name=AssuredOperationName.ANSWER_CONTEXT,
        description=(
            "Complete testimony and neighborhood-aware fact responses side by side"
            " in ContextBundle/v1."
        ),
        parameters={
            "query": _QUERY,
            "entity_ids": _ENTITY_IDS,
            "hops": _HOPS,
            "predicate": _PREDICATE,
            "time": {**_TIME_SCHEMA, "required": False},
        },
        result_schema=ContextBundleV1.model_json_schema(mode="serialization"),
        execution_plan=OperationBundlePlan(),
        result_contract=AssuredResultContract.CONTEXT_BUNDLE_V1,
        output_grain=None,
        answer_intent=AssuredAnswerIntent.COMBINED_CONTEXT,
    ),
)


def _canonical_operation_json(*, operation: AssuredOperation) -> str:
    """Serialize a descriptor deterministically for immutable baseline checks."""
    return json.dumps(
        operation.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


_CANONICAL_OPERATION_JSON: tuple[tuple[AssuredOperationName, str], ...] = tuple(
    (operation.name, _canonical_operation_json(operation=operation))
    for operation in CANONICAL_OPERATIONS
)


def _lint_canonical_operation(*, operation: AssuredOperation) -> None:
    """Validate one row against the same-named canonical descriptor."""
    expected = next(
        candidate
        for candidate in CANONICAL_OPERATIONS
        if candidate.name is operation.name
    )
    lint_assured_operation(operation, expected=expected)
    expected_json = next(
        payload for name, payload in _CANONICAL_OPERATION_JSON if name is operation.name
    )
    if _canonical_operation_json(operation=operation) != expected_json:
        raise AssuredOperationLintError(
            f"operation {operation.name.value!r} must match its immutable canonical descriptor"
        )


def seed_canonical_operations(
    *, registry: AssuredOperationRegistry, deployment_id: UUID
) -> int:
    """Atomically reconcile the deployment catalog to the canonical four."""
    return registry.replace_all(
        deployment_id=deployment_id, operations=CANONICAL_OPERATIONS
    )


_INSERT_OPERATION = text(
    """
    INSERT INTO assured_operations (
        operation_id, deployment_id, name, description, parameters,
        result_schema, execution_plan, result_contract, output_grain,
        answer_intent, version
    ) VALUES (
        :operation_id, :deployment_id,
        CAST(:name AS assured_operation_name), :description, :parameters,
        :result_schema, :execution_plan,
        CAST(:result_contract AS assured_result_contract),
        CAST(:output_grain AS assured_output_grain),
        CAST(:answer_intent AS assured_answer_intent), :version
    )
    ON CONFLICT (deployment_id, name, version) DO NOTHING
    """
).bindparams(
    bindparam("parameters", type_=JSON),
    bindparam("result_schema", type_=JSON),
    bindparam("execution_plan", type_=JSON),
)

_OPERATION_COLUMNS = (
    "name::text AS name, description, parameters, result_schema, execution_plan,"
    " result_contract::text AS result_contract,"
    " output_grain::text AS output_grain,"
    " answer_intent::text AS answer_intent, version"
)

_ACTIVE_OPERATIONS = text(
    f"""
    SELECT {_OPERATION_COLUMNS}
    FROM assured_operations
    WHERE deployment_id = :deployment_id AND status = 'active'
      AND name = ANY(CAST(:names AS assured_operation_name[]))
      AND version = 1
    ORDER BY name
    """  # noqa: S608 -- columns are a module constant
)

_OPERATION_BY_NAME = text(
    f"""
    SELECT {_OPERATION_COLUMNS}
    FROM assured_operations
    WHERE deployment_id = :deployment_id
      AND name = CAST(:name AS assured_operation_name)
      AND version = :version AND status = 'active'
    LIMIT 1
    """  # noqa: S608 -- columns are a module constant
)

_DELETE_OPERATIONS = text(
    "DELETE FROM assured_operations WHERE deployment_id = :deployment_id"
)
