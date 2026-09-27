"""Strict argument validation for the seven open-query tools.

Wrong types (including a key present with JSON null when the schema declares
string/integer), bool-as-int, out-of-range integers, missing required fields,
and unknown extra keys are rejected rather than coerced. Omission of optional
fields still applies defaults. Allowed and required keys are read from the
catalogue schema, so validation and ``tools/list`` cannot drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import re
from typing import cast

from remember.mcp_tools._definitions import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools._definitions import tool
from remember.query_sandbox.errors import QueryErrorCode
from remember.query_sandbox.errors import SandboxRejection

_SAVED_QUERY_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_open_query_arguments(
    *, name: str, arguments: Mapping[str, object]
) -> dict[str, object]:
    """Strictly validate MCP open-query tool arguments without coercion.

    Rejects non-string text fields, non-boolean flags, non-array SQL
    parameters, bool-as-int, out-of-range
    integers (``max_rows`` min 0, ``version`` min 1, ``k`` in 1..25),
    missing required fields, and unknown extra keys. Shared by local MCP
    dispatch and remote MCP-to-SDK dispatch.
    """
    if name not in OPEN_QUERY_TOOL_NAMES:
        raise ValueError(f"unknown open-query tool {name!r}")
    schema = tool(name).input_schema
    allowed = set(cast("dict[str, object]", schema["properties"]))
    required = set(cast("list[str]", schema.get("required", [])))
    if not isinstance(arguments, Mapping):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="arguments must be a JSON object",
        )
    args = dict(arguments)
    unknown = sorted(set(args) - allowed)
    if unknown:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"unknown argument keys: {unknown}",
        )
    missing = sorted(required - set(args))
    if missing:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"missing required argument keys: {missing}",
        )
    validated: dict[str, object] = {}
    if name in ("query_sql", "explain_sql"):
        validated["sql"] = _require_str(args["sql"], field="sql")
        if "parameters" in args:
            validated["parameters"] = _require_sql_parameters(args["parameters"])
        else:
            validated["parameters"] = ()
        if "max_rows" in args:
            validated["max_rows"] = _require_int(
                args["max_rows"], field="max_rows", minimum=0
            )
    elif name == "describe_query_space":
        # Explicit null is a type error for string fields; only omission defaults.
        if "pattern" in args:
            validated["pattern"] = _require_str(args["pattern"], field="pattern")
        else:
            validated["pattern"] = None
        if "include_examples" in args:
            validated["include_examples"] = _require_bool(
                args["include_examples"], field="include_examples"
            )
        else:
            validated["include_examples"] = False
    elif name == "search_query_space":
        validated["query"] = _require_str(args["query"], field="query")
        if "k" in args:
            validated["k"] = _require_int(args["k"], field="k", minimum=1, maximum=25)
        else:
            validated["k"] = 10
    elif name == "list_saved_queries":
        if "namespace" in args:
            validated["namespace"] = validate_saved_query_identifier(
                value=args["namespace"], field="namespace"
            )
        else:
            validated["namespace"] = None
        if "status" in args:
            validated["status"] = _require_str(args["status"], field="status")
        else:
            validated["status"] = None
    elif name == "describe_saved_query":
        validated["namespace"] = validate_saved_query_identifier(
            value=args["namespace"], field="namespace"
        )
        validated["name"] = validate_saved_query_identifier(
            value=args["name"], field="name"
        )
        if "version" in args:
            validated["version"] = _require_int(
                args["version"], field="version", minimum=1
            )
        else:
            validated["version"] = None
    elif name == "run_saved_query":
        validated["namespace"] = validate_saved_query_identifier(
            value=args["namespace"], field="namespace"
        )
        validated["name"] = validate_saved_query_identifier(
            value=args["name"], field="name"
        )
        if "version" in args:
            validated["version"] = _require_int(
                args["version"], field="version", minimum=1
            )
        else:
            validated["version"] = None
        if "parameters" in args:
            validated["parameters"] = _require_sql_parameters(args["parameters"])
        else:
            validated["parameters"] = ()
        if "max_rows" in args:
            validated["max_rows"] = _require_int(
                args["max_rows"], field="max_rows", minimum=0
            )
    return validated


def _require_str(value: object, *, field: str) -> str:
    """Require a real string; never coerce arbitrary objects via ``str()``."""
    if not isinstance(value, str):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message=f"{field} must be a string"
        )
    return value


def validate_saved_query_identifier(*, value: object, field: str) -> str:
    """Require the same safe identifier shape enforced by the registry schema."""
    text = _require_str(value, field=field)
    if _SAVED_QUERY_IDENTIFIER.fullmatch(text) is None:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"{field} must match ^[a-z][a-z0-9_]*$",
        )
    return text


def _require_bool(value: object, *, field: str) -> bool:
    """Require a real boolean; reject string/int stand-ins such as ``\"false\"``."""
    if not isinstance(value, bool):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message=f"{field} must be a boolean"
        )
    return value


def _require_int(
    value: object, *, field: str, minimum: int | None = None, maximum: int | None = None
) -> int:
    """Require a non-bool integer (JSON numbers only), optionally ranged."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message=f"{field} must be an integer"
        )
    if minimum is not None and value < minimum:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"{field} must be >= {minimum}",
        )
    if maximum is not None and value > maximum:
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message=f"{field} must be <= {maximum}",
        )
    return value


def _require_sql_parameters(value: object) -> tuple[object, ...]:
    """Require a JSON array of bound SQL parameters (not a string or object)."""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="parameters must be a JSON array of bound values",
        )
    return tuple(value)
