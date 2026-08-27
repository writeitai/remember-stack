"""Static MCP tool schemas for the seven open-query facade operations (§3.1).

Infrastructure tools only — the eighteen `examples.*` identities remain
registry entries and never become top-level MCP tools. Local and remote MCP
servers share these descriptors and dispatch helpers so tools/list and
tools/call stay aligned.

Argument validation is strict: wrong types (including a key present with
JSON null when the schema declares string/integer), bool-as-int, out-of-range
integers, missing required fields, and unknown extra keys are rejected
rather than coerced. Omission of optional fields still applies defaults.

This module stays dependency-light for the base client wheel: descriptors and
argument validation import only the public error taxonomy. Server-only types
(``OpenQueryFacade``, discovery serialization, ``QueryResult``) are
TYPE_CHECKING-only or loaded lazily inside local dispatch.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections.abc import Sequence
import re
from typing import Any
from typing import Final
from typing import TYPE_CHECKING

from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.errors import SandboxRejection

if TYPE_CHECKING:
    # Runtime import avoided so base-wheel remote MCP stays dependency-light.
    from rememberstack.surfaces.query_sandbox.open_query import OpenQueryFacade


#: The seven open-query facade operations, also the static MCP tool names.
OPEN_QUERY_TOOL_NAMES: tuple[str, ...] = (
    "query_sql",
    "explain_sql",
    "describe_query_space",
    "search_query_space",
    "list_saved_queries",
    "describe_saved_query",
    "run_saved_query",
)

_SAVED_QUERY_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")

#: Per-tool allowed keys and required keys for strict argument validation.
_TOOL_ARGUMENT_SPECS: Final[dict[str, tuple[frozenset[str], frozenset[str]]]] = {
    "query_sql": (frozenset({"sql", "parameters", "max_rows"}), frozenset({"sql"})),
    "explain_sql": (frozenset({"sql", "parameters"}), frozenset({"sql"})),
    "describe_query_space": (frozenset({"pattern", "include_examples"}), frozenset()),
    "search_query_space": (frozenset({"query", "k"}), frozenset({"query"})),
    "list_saved_queries": (frozenset({"namespace", "status"}), frozenset()),
    "describe_saved_query": (
        frozenset({"namespace", "name", "version"}),
        frozenset({"namespace", "name"}),
    ),
    "run_saved_query": (
        frozenset({"namespace", "name", "version", "parameters", "max_rows"}),
        frozenset({"namespace", "name"}),
    ),
}


def open_query_tool_descriptors() -> list[dict[str, object]]:
    """MCP tools/list entries for the seven open-query infrastructure tools."""
    return [
        {
            "name": "query_sql",
            "description": (
                "Run one sandboxed read-only SQL statement over the memory_v1"
                " query space. Returns QueryResult/v1 (exploratory_tabular)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "parameters": {
                        "type": "array",
                        "items": {},
                        "description": "Positional bound parameters ($1, $2, …)",
                    },
                    "max_rows": {"type": "integer", "minimum": 0},
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
        },
        {
            "name": "explain_sql",
            "description": (
                "EXPLAIN (FORMAT JSON) one SQL statement without executing it;"
                " same parser, relation, function, and operator gates."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "sql": {"type": "string"},
                    "parameters": {"type": "array", "items": {}},
                },
                "required": ["sql"],
                "additionalProperties": False,
            },
        },
        {
            "name": "describe_query_space",
            "description": (
                "Manifest-backed exact schema, functions, comments, versions,"
                " hashes, and limits. Opens with the bound two-layer headline."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Optional fnmatch filter over view names",
                    },
                    "include_examples": {
                        "type": "boolean",
                        "default": False,
                        "description": "When true, list shipped examples.* names",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "search_query_space",
            "description": (
                "Search checked-in manifest text (names, comments, tags,"
                " examples); never tenant content. k in 1..25."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "k": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 25,
                        "default": 10,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_saved_queries",
            "description": (
                "List saved-query registry metadata. Default lists active"
                " versions only; drafts require an explicit status filter."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "status": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "describe_saved_query",
            "description": (
                "Describe one saved-query version: parameters, declared"
                " columns, validation state, and hashes."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                    "version": {"type": "integer", "minimum": 1},
                },
                "required": ["namespace", "name"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_saved_query",
            "description": (
                "Execute one active saved query through the same SQL sandbox."
                " Not a top-level intent operation; returns QueryResult/v1."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                    "version": {"type": "integer", "minimum": 1},
                    "parameters": {"type": "array", "items": {}},
                    "max_rows": {"type": "integer", "minimum": 0},
                },
                "required": ["namespace", "name"],
                "additionalProperties": False,
            },
        },
    ]


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
    if name not in _TOOL_ARGUMENT_SPECS:
        raise ValueError(f"unknown open-query tool {name!r}")
    allowed, required = _TOOL_ARGUMENT_SPECS[name]
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


def dispatch_open_query_tool(
    *, facade: OpenQueryFacade, name: str, arguments: Mapping[str, object]
) -> object:
    """Dispatch one open-query MCP tool call to the facade.

    Returns a JSON-serializable payload (QueryResult dump or description
    dicts). Raises SandboxRejection or ValueError for typed failures.
    Server-only discovery serialization is imported only on the local
    describe path so the base wheel can import descriptors/validation.
    """
    args = validate_open_query_arguments(name=name, arguments=arguments)
    if name == "query_sql":
        return _query_result_payload(
            facade.query_sql(
                sql=str(args["sql"]),
                parameters=_as_sequence(args.get("parameters", ())),
                max_rows=_optional_int(args.get("max_rows")),
            )
        )
    if name == "explain_sql":
        return _query_result_payload(
            facade.explain_sql(
                sql=str(args["sql"]),
                parameters=_as_sequence(args.get("parameters", ())),
            )
        )
    if name == "describe_query_space":
        # Lazy: discovery pulls the checked-in manifest (server-side).
        from rememberstack.surfaces.query_sandbox.discovery import (
            query_space_description_payload,
        )

        description = facade.describe_query_space(
            pattern=(str(args["pattern"]) if args.get("pattern") is not None else None),
            include_examples=bool(args.get("include_examples", False)),
        )
        return query_space_description_payload(description)
    if name == "search_query_space":
        k_value = _optional_int(args.get("k", 10))
        hits = facade.search_query_space(
            query=str(args["query"]), k=10 if k_value is None else k_value
        )
        return [
            {
                "kind": hit.kind,
                "name": hit.name,
                "score": hit.score,
                "purpose": hit.purpose,
                "tags": list(hit.tags),
            }
            for hit in hits
        ]
    if name == "list_saved_queries":
        rows = facade.list_saved_queries(
            namespace=(
                str(args["namespace"]) if args.get("namespace") is not None else None
            ),
            status=str(args["status"]) if args.get("status") is not None else None,
        )
        return [
            {
                "query_id": str(row.query_id),
                "namespace": row.namespace,
                "name": row.name,
                "version": row.version,
                "status": row.status,
                "description": row.description,
                "origin": row.origin,
                "assurance": row.assurance,
                "query_hash": row.query_hash,
                "validated_surface_manifest_hash": row.validated_surface_manifest_hash,
            }
            for row in rows
        ]
    if name == "describe_saved_query":
        detail = facade.describe_saved_query(
            namespace=str(args["namespace"]),
            name=str(args["name"]),
            version=_optional_int(args.get("version")),
        )
        return {
            "query_id": str(detail.query_id),
            "namespace": detail.namespace,
            "name": detail.name,
            "version": detail.version,
            "status": detail.status,
            "description": detail.description,
            "origin": detail.origin,
            "assurance": detail.assurance,
            "sql": detail.sql,
            "query_hash": detail.query_hash,
            "parameter_schema": detail.parameter_schema,
            "declared_result_schema": detail.declared_result_schema,
            "declared_interpretation": detail.declared_interpretation,
            "query_space_major": detail.query_space_major,
            "default_limits": detail.default_limits,
            "validated_surface_manifest_hash": detail.validated_surface_manifest_hash,
            "validation_report": detail.validation_report,
            "author_principal": detail.author_principal,
            "approver_principal": detail.approver_principal,
        }
    if name == "run_saved_query":
        return _query_result_payload(
            facade.run_saved_query(
                namespace=str(args["namespace"]),
                name=str(args["name"]),
                version=_optional_int(args.get("version")),
                parameters=_as_sequence(args.get("parameters", ())),
                max_rows=_optional_int(args.get("max_rows")),
            )
        )
    raise ValueError(f"unknown open-query tool {name!r}")


def _query_result_payload(result: object) -> dict[str, Any]:
    """Serialize QueryResult for MCP text content without private error detail."""
    dump = getattr(result, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        if isinstance(payload, dict):
            return payload
    raise TypeError("open-query result must be a QueryResult-like model")


def _as_sequence(value: object) -> Sequence[object]:
    """Return a sequence of bound SQL parameters after validation."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER,
            message="parameters must be a JSON array of bound values",
        )
    if isinstance(value, Sequence):
        return tuple(value)
    raise SandboxRejection(
        code=QueryErrorCode.INVALID_PARAMETER,
        message="parameters must be a JSON array of bound values",
    )


def _optional_int(value: object) -> int | None:
    """Accept only a non-bool integer, or None when the field is omitted."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SandboxRejection(
            code=QueryErrorCode.INVALID_PARAMETER, message="expected an integer"
        )
    return value


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
