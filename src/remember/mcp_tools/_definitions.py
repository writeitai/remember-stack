"""The memory tools' definitions: one ``ToolDefinition`` per MCP tool.

Every MCP host renders its memory tools from here (design
one_key_client_surfaces §3); none defines a memory tool of its own. The engine's
assured-operation registry generates the agent-facing fields of its four
operations from these definitions too, so ``GET /operations`` and MCP can never
describe the same operation differently.

The eighteen ``examples.*`` saved queries are never top-level tools: they run
only through ``run_saved_query`` (D83/D87).
"""

from __future__ import annotations

from collections.abc import Iterable
import copy
from dataclasses import dataclass
from typing import Final
from typing import Literal

INGEST_TOOL_NAME: Final = "ingest"
PIPELINE_READINESS_TOOL_NAME: Final = "pipeline_readiness"
DELETE_DOCUMENT_TOOL_NAME: Final = "delete_document"
SEARCH_DOCUMENTS_TOOL_NAME: Final = "search_documents"
ADJACENT_CHUNKS_TOOL_NAME: Final = "adjacent_chunks"
MEMORY_WRITE_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {INGEST_TOOL_NAME, PIPELINE_READINESS_TOOL_NAME}
)
#: The four assured operations (D87), run through ``POST /operations/{name}``.
OPERATION_TOOL_NAMES: Final[tuple[str, ...]] = (
    "resolve_entity",
    "claims_and_sources_context",
    "facts_context",
    "combined_context",
)
#: The seven open-query facade operations (open query space §3.1).
OPEN_QUERY_TOOL_NAMES: Final[tuple[str, ...]] = (
    "query_sql",
    "explain_sql",
    "describe_query_space",
    "search_query_space",
    "list_saved_queries",
    "describe_saved_query",
    "run_saved_query",
)

FILENAME_MAX_LEN: Final = 512
MIME_MAX_LEN: Final = 255
TITLE_MAX_LEN: Final = 512
SOURCE_KIND_MAX_LEN: Final = 128
SOURCE_REF_MAX_LEN: Final = 512
SOURCE_VERSION_REF_MAX_LEN: Final = 512
VERSION_IDS_MAX: Final = 1000

Permission = Literal["memory:read", "memory:write"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ToolDefinition:
    """One memory tool, as every MCP host renders it.

    ``tool_version`` rises whenever the input schema admits a call an engine
    serving the previous version cannot handle, or an existing call could fail
    or change meaning; description-only edits need no bump. ``http_route`` is
    the engine route the tool calls, as ``"METHOD /path/{template}"``.

    ``input_schema`` is shared: read it, never mutate it.
    :func:`render_tools_list` hands hosts their own copies.
    """

    name: str
    description: str
    input_schema: dict[str, object]
    permission: Permission
    tool_version: int
    http_route: str
    destructive: bool = False

    @property
    def mutates(self) -> bool:
        """Whether the tool changes memory: exactly the write-permission tools."""
        return self.permission == "memory:write"

    @property
    def annotations(self) -> dict[str, bool]:
        """The MCP tool annotations every host renders unchanged."""
        return {"readOnlyHint": not self.mutates, "destructiveHint": self.destructive}


#: The routing argument a host serving several deployments adds to every tool
#: (design §4). The host resolves and removes it; the engine never accepts it.
PROJECT_ARGUMENT: Final[dict[str, object]] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 200,
    "description": (
        "Which project's memory to use, by id or name. Omit it to use the"
        " default project."
    ),
}

_INGEST_DESCRIPTION: Final = (
    "Store a document into this deployment's memory (E0 write). Returns a"
    " version_id immediately; the indexing pipeline is asynchronous and may"
    " take many minutes (structure alone has been measured at ~11 minutes on a"
    " ~2.5KB file). Do NOT call assured recall operations expecting this content until"
    " pipeline_readiness reports ready=true for the version_id."
    " Prefer source_kind plus a stable source_ref for durable agent memory so"
    " later writes become new versions of the same document; omit both only for"
    " intentionally anonymous one-shot ingest."
    " Body sources (exactly one): text for short UTF-8 notes already in context;"
    " content_base64 for binary; path, where this server offers it, only when"
    " the operator has configured REMEMBERSTACK_MCP_INGEST_ROOTS allowlisted"
    " directories on this MCP host —"
    " with no roots configured, path is rejected (use text/content_base64 or ask"
    " the operator to set roots). Path reads resolve fully, must stay inside a"
    " configured root after symlink resolution, must be regular files, and are"
    " size-bounded (served capability limit when present, otherwise a local"
    " process resource guard). Bodies must be non-empty; deployments may enforce"
    " a maximum body size (oversized or empty bodies map to structured"
    " body_too_large / empty_body errors). source_kind and source_ref must be"
    " supplied together when either is set (stable lineage)."
    ' If the result has parked="no_route", the original is stored but its'
    " conversion is parked waiting for a conversion route for its MIME type."
    " Tell the user now instead of polling readiness."
)

_PIPELINE_READINESS_DESCRIPTION: Final = (
    "Inspect whether one or more document version_ids have finished the"
    " requested pipeline and serving capabilities and are safe to recall."
    " Call after ingest with the returned version_id."
    " ready=true means assured recall operations may see the content (subject to retrieval"
    " relevance)."
    " require must explicitly name all four capabilities: pipeline, p1,"
    " live_graph, and p3. For ordinary recall polling require pipeline, p1, and"
    " live_graph, but set p3=false unless a published CorpusFS snapshot is part"
    " of the caller's contract. The live graph is PostgreSQL state and never"
    " waits for a projection build."
    " Terminal stop: if any stages[].status is dead_letter, STOP polling and"
    " report the version_id and that stage to the user — a dead-lettered stage"
    " has used all its retries and never becomes ready by waiting."
    " status=failed is NOT terminal: the last attempt failed and a retry is"
    " scheduled with back-off, so keep polling and describe it as retrying."
    " Bounded poll: wait ~30s after ingest, then poll every 30–60s with mild"
    " back-off (floor ~15s). After ~20–30 minutes without ready=true and without"
    " a dead_letter stage, stop and escalate to the operator (include"
    " version_id and last stages[])."
    " An ingest that returned created=false started no new run, but an earlier"
    " run of the same bytes may still be processing: poll that version_id the"
    " same way."
)

_DELETE_DOCUMENT_DESCRIPTION: Final = (
    "Remove one document from this deployment's memory. Use only when the user"
    " asks to delete or forget a specific document, or it is plainly wrong or"
    " unwanted — never to tidy up, and never to change a fact (ingest a"
    " correcting document instead). Takes the doc_id that ingest returned or"
    " that a claim or source cites. The effect is immediate: the document leaves"
    " search, facts and the document list; its claims stop counting as"
    " evidence; facts that no other document supports are closed. Facts other"
    " documents also support stay. The claims and stored original are kept as"
    " history, so this is not an erasure. Ingesting the same document again later"
    " adds it back as a new version. Returns claims_retired, relations_closed and"
    " observations_closed. A document_not_found error means the id is unknown or"
    " the document is already deleted: do not retry it."
)

_SEARCH_DOCUMENTS_DESCRIPTION: Final = (
    "Find files: documents by name, metadata and content. Use it when the user"
    ' names or describes a file ("find Q3_sales_2025.xlsx", "the audit'
    ' report from last spring", "emails from Alice") rather than asking'
    " about its content. query matches every name a document was stored under"
    " (file name, title, source path; old names too after a rename; partial and"
    " misspelled names work) and its text. Filters narrow by family (text,"
    " markdown, html, pdf, image, audio, video, office, other), authors,"
    ' recipients (a name or an address; "alice" matches "Alice Novak"),'
    " created/modified date ranges, language, thread_ref and doc_ids. Each"
    ' result is a document judged by its current version (versions="all"'
    " searches every live version and returns the newest match); it carries"
    " doc_id, version_id, file_name, title, family, processing status, authors,"
    " recipients, dates, p3_path (documents/<doc_id> in the corpus filesystem"
    " view, where one is published) and a short overview when one exists."
    " When a people"
    " filter matches several different people, people_matched lists each with"
    " a document count: narrow the filter (for example by address) instead of"
    " guessing. Without query, results are newest first and cursor pages them."
)

_DATE_TIME: Final[dict[str, object]] = {
    "type": "string",
    "format": "date-time",
    "description": "An ISO 8601 instant with a timezone, inclusive.",
}

_STRING_LIST: Final[dict[str, object]] = {
    "type": "array",
    "items": {"type": "string", "minLength": 1},
}

_SEARCH_DOCUMENTS_INPUT_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4096,
            "description": "Words from the file's name, title, path or text.",
        },
        "family": {**_STRING_LIST, "description": "Keep these format families."},
        "authors": {**_STRING_LIST, "description": "Any of these authors."},
        "recipients": {**_STRING_LIST, "description": "Any of these recipients."},
        "created_from": _DATE_TIME,
        "created_to": _DATE_TIME,
        "modified_from": _DATE_TIME,
        "modified_to": _DATE_TIME,
        "language": {"type": "string", "minLength": 1},
        "thread_ref": {"type": "string", "minLength": 1},
        "doc_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "description": "Only these documents (UUIDs).",
        },
        "versions": {
            "type": "string",
            "enum": ["current", "all"],
            "description": "current (default) or all live versions.",
        },
        "k": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {
            "type": "string",
            "minLength": 1,
            "description": "The previous page's cursor; only without query.",
        },
    },
}

_DELETE_DOCUMENT_INPUT_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["doc_id"],
    "properties": {
        "doc_id": {
            "type": "string",
            "minLength": 1,
            "description": "The document's UUID (doc_id), as ingest returned it.",
        }
    },
}

_PIPELINE_READINESS_INPUT_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["version_ids", "require"],
    "properties": {
        "version_ids": {
            "type": "array",
            "minItems": 1,
            "maxItems": VERSION_IDS_MAX,
            "items": {"type": "string", "minLength": 1},
            "description": "Document version UUIDs from ingest.",
        },
        "require": {
            "type": "object",
            "additionalProperties": False,
            "required": ["pipeline", "p1", "live_graph", "p3"],
            "properties": {
                "pipeline": {"type": "boolean"},
                "p1": {"type": "boolean"},
                "live_graph": {"type": "boolean"},
                "p3": {"type": "boolean"},
            },
            "description": (
                "Exhaustive capability request. Ordinary recall polling uses"
                " pipeline=true, p1=true, live_graph=true, p3=false."
            ),
        },
    },
}

_INGEST_PROPERTIES: Final[dict[str, object]] = {
    "path": {
        "type": "string",
        "minLength": 1,
        "description": (
            "Local filesystem path readable by this MCP process, only when"
            " REMEMBERSTACK_MCP_INGEST_ROOTS is configured. Mutually exclusive"
            " with text and content_base64. Path is resolved fully; symlink"
            " escape outside a configured root is rejected. Must be a regular"
            " file (not a directory, FIFO, or device). Size is checked before"
            " read. Filename defaults to the path basename; mime is inferred"
            " from the real path name unless mime is supplied (SDK parity)."
        ),
    },
    "text": {
        "type": "string",
        "minLength": 1,
        "description": (
            "UTF-8 document body. Mutually exclusive with path and"
            " content_base64. Requires filename."
        ),
    },
    "content_base64": {
        "type": "string",
        "minLength": 1,
        "description": (
            "Standard base64-encoded bytes (no data: URL prefix). Mutually"
            " exclusive with path and text. Requires filename. Use for"
            " binary; for plain text prefer text."
        ),
    },
    "filename": {
        "type": "string",
        "minLength": 1,
        "maxLength": FILENAME_MAX_LEN,
        "description": (
            "Required when text or content_base64 is used. Optional with"
            " path (defaults to the path basename). Does not change mime"
            " inference for path mode — mime follows the real path name"
            " unless mime is set."
        ),
    },
    "mime": {
        "type": "string",
        "minLength": 1,
        "maxLength": MIME_MAX_LEN,
        "description": (
            "Optional; an explicit value always wins. Default: for path,"
            " inferred from the real path name; for content_base64, inferred"
            " from filename (.md → text/markdown, .pdf → application/pdf,"
            " .png → image/png, …), else application/octet-stream; for text,"
            " a text/* type inferred from filename, else text/plain."
        ),
    },
    "title": {
        "type": "string",
        "maxLength": TITLE_MAX_LEN,
        "description": "Optional human title forwarded to the engine.",
    },
    "source_kind": {
        "type": "string",
        "minLength": 1,
        "maxLength": SOURCE_KIND_MAX_LEN,
        "description": (
            "Lineage class (e.g. agent, cli, feeder). Must be paired with"
            " source_ref. Prefer setting this for durable agent memory."
        ),
    },
    "source_ref": {
        "type": "string",
        "minLength": 1,
        "maxLength": SOURCE_REF_MAX_LEN,
        "description": (
            "Stable id within source_kind. Reuse creates a new version of"
            " the same document when bytes change (engine D55 / SDK"
            " contract)."
        ),
    },
    "versioning_mode": {
        "type": "string",
        "enum": ["snapshot", "living"],
        "default": "snapshot",
        "description": "Requires source_kind/source_ref when not snapshot.",
    },
    "source_modified_at": {
        "type": "string",
        "description": (
            "Optional ISO-8601 UTC timestamp (timezone-aware). Requires"
            " source_kind/source_ref."
        ),
    },
    "source_version_ref": {
        "type": "string",
        "minLength": 1,
        "maxLength": SOURCE_VERSION_REF_MAX_LEN,
        "description": (
            "Optional upstream revision label. Requires source_kind/source_ref."
        ),
    },
}
_INGEST_BODY_SOURCES: Final = ("path", "text", "content_base64")


def _ingest_input_schema(*, path: bool) -> dict[str, object]:
    """The ingest schema, with or without the local ``path`` body source.

    Each ``oneOf`` branch requires its body key (and ``filename`` for the
    non-path bodies) and forbids the other body keys, so a host validating
    against the schema rejects multi-body payloads.
    """
    bodies = tuple(body for body in _INGEST_BODY_SOURCES if path or body != "path")
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            key: value
            for key, value in _INGEST_PROPERTIES.items()
            if path or key != "path"
        },
        "oneOf": [
            {
                "required": [body] if body == "path" else [body, "filename"],
                "not": {
                    "anyOf": [
                        {"required": [other]} for other in bodies if other != body
                    ]
                },
            }
            for body in bodies
        ],
    }


_INGEST_INPUT_SCHEMA: Final = _ingest_input_schema(path=True)
_INGEST_INPUT_SCHEMA_WITHOUT_PATH: Final = _ingest_input_schema(path=False)


_TIME_SCHEMA: Final[dict[str, object]] = {
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

_ENTITY_IDS: Final[dict[str, object]] = {
    "type": "array",
    "items": {"type": "string", "format": "uuid"},
    "minItems": 1,
    "maxItems": 20,
    "uniqueItems": True,
}

_NEIGHBORHOOD_ENTITY_IDS: Final[dict[str, object]] = {**_ENTITY_IDS, "maxItems": 19}

_QUERY: Final[dict[str, object]] = {"type": "string", "minLength": 1, "maxLength": 8192}

_HOPS: Final[dict[str, object]] = {
    "type": "integer",
    "default": 1,
    "minimum": 1,
    "maximum": 2,
}

_PREDICATE: Final[dict[str, object]] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 200,
}


def _object_schema(
    *, properties: dict[str, object], required: tuple[str, ...] = ()
) -> dict[str, object]:
    """A closed argument object: unknown arguments are a validation error."""
    schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


_SQL_PARAMETERS: Final[dict[str, object]] = {"type": "array", "items": {}}
_SAVED_QUERY_VERSION: Final[dict[str, object]] = {"type": "integer", "minimum": 1}
_MAX_ROWS: Final[dict[str, object]] = {"type": "integer", "minimum": 0}

_TOOLS: Final[tuple[ToolDefinition, ...]] = (
    ToolDefinition(
        name=INGEST_TOOL_NAME,
        description=_INGEST_DESCRIPTION,
        input_schema=_INGEST_INPUT_SCHEMA,
        permission="memory:write",
        tool_version=1,
        http_route="POST /ingest",
    ),
    ToolDefinition(
        name=PIPELINE_READINESS_TOOL_NAME,
        description=_PIPELINE_READINESS_DESCRIPTION,
        input_schema=_PIPELINE_READINESS_INPUT_SCHEMA,
        permission="memory:read",
        tool_version=1,
        http_route="POST /readiness",
    ),
    ToolDefinition(
        name=DELETE_DOCUMENT_TOOL_NAME,
        description=_DELETE_DOCUMENT_DESCRIPTION,
        input_schema=_DELETE_DOCUMENT_INPUT_SCHEMA,
        permission="memory:write",
        tool_version=1,
        http_route="DELETE /documents/{doc_id}",
        destructive=True,
    ),
    ToolDefinition(
        name=SEARCH_DOCUMENTS_TOOL_NAME,
        description=_SEARCH_DOCUMENTS_DESCRIPTION,
        input_schema=_SEARCH_DOCUMENTS_INPUT_SCHEMA,
        permission="memory:read",
        tool_version=1,
        http_route="POST /documents/search",
    ),
    ToolDefinition(
        name="resolve_entity",
        description=(
            "Resolve a name to ranked current survivor candidates; never silently guess."
        ),
        input_schema=_object_schema(
            properties={"name": {"type": "string", "minLength": 1}}, required=("name",)
        ),
        permission="memory:read",
        tool_version=1,
        http_route="POST /operations/resolve_entity",
    ),
    ToolDefinition(
        name="claims_and_sources_context",
        description="High-recall current claims and confirmed source passages.",
        input_schema=_object_schema(
            properties={
                "query": _QUERY,
                "entity_ids": _ENTITY_IDS,
                "k": {"type": "integer", "default": 50, "minimum": 1, "maximum": 100},
                "candidate_k": {
                    "type": "integer",
                    "default": 200,
                    "minimum": 1,
                    "maximum": 400,
                },
            },
            required=("query",),
        ),
        permission="memory:read",
        tool_version=2,
        http_route="POST /operations/claims_and_sources_context",
    ),
    ToolDefinition(
        name="facts_context",
        description=(
            "Adjudicated relations and observations under an explicit world-time"
            " scope, with bounded live-graph expansion for current or point-in-time"
            " entity anchors."
        ),
        input_schema=_object_schema(
            properties={
                "query": _QUERY,
                "entity_ids": _NEIGHBORHOOD_ENTITY_IDS,
                "k": {"type": "integer", "default": 15, "minimum": 1, "maximum": 30},
                "evidence_per_fact": {
                    "type": "integer",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 5,
                },
                "hops": _HOPS,
                "predicate": _PREDICATE,
                "time": _TIME_SCHEMA,
            },
            required=("query",),
        ),
        permission="memory:read",
        tool_version=3,
        http_route="POST /operations/facts_context",
    ),
    ToolDefinition(
        name="combined_context",
        description=(
            "Complete claims-and-sources and neighborhood-aware fact responses side by side"
            " in ContextBundle/v2."
        ),
        input_schema=_object_schema(
            properties={
                "query": _QUERY,
                "entity_ids": _NEIGHBORHOOD_ENTITY_IDS,
                "hops": _HOPS,
                "predicate": _PREDICATE,
                "time": _TIME_SCHEMA,
            },
            required=("query",),
        ),
        permission="memory:read",
        tool_version=4,
        http_route="POST /operations/combined_context",
    ),
    ToolDefinition(
        name=ADJACENT_CHUNKS_TOOL_NAME,
        description=(
            "Retrieve neighbouring chunks preceding and succeeding a target chunk within the"
            " same document to expand conversational or narrative context."
        ),
        input_schema=_object_schema(
            properties={
                "chunk_id": {
                    "type": "string",
                    "description": "UUID of the target chunk to expand around.",
                },
                "window": {
                    "type": "integer",
                    "default": 1,
                    "minimum": 1,
                    "maximum": 2,
                    "description": (
                        "Number of neighbouring chunks to retrieve on each side (1 or 2, default 1)."
                    ),
                },
            },
            required=("chunk_id",),
        ),
        permission="memory:read",
        tool_version=1,
        http_route="GET /chunks/{chunk_id}/adjacent",
    ),
    ToolDefinition(
        name="query_sql",
        description=(
            "Run one sandboxed read-only SQL statement over the memory_v1"
            " query space. Returns QueryResult/v1 (exploratory_tabular)."
        ),
        input_schema=_object_schema(
            properties={
                "sql": {"type": "string"},
                "parameters": {
                    **_SQL_PARAMETERS,
                    "description": "Positional bound parameters ($1, $2, …)",
                },
                "max_rows": _MAX_ROWS,
            },
            required=("sql",),
        ),
        permission="memory:read",
        tool_version=1,
        http_route="POST /query/sql",
    ),
    ToolDefinition(
        name="explain_sql",
        description=(
            "EXPLAIN (FORMAT JSON) one SQL statement without executing it;"
            " same parser, relation, function, and operator gates."
        ),
        input_schema=_object_schema(
            properties={"sql": {"type": "string"}, "parameters": _SQL_PARAMETERS},
            required=("sql",),
        ),
        permission="memory:read",
        tool_version=1,
        http_route="POST /query/sql/explain",
    ),
    ToolDefinition(
        name="describe_query_space",
        description=(
            "Manifest-backed exact schema, functions, comments, versions,"
            " hashes, and limits. Opens with the bound two-layer headline."
        ),
        input_schema=_object_schema(
            properties={
                "pattern": {
                    "type": "string",
                    "description": "Optional fnmatch filter over view names",
                },
                "include_examples": {
                    "type": "boolean",
                    "default": False,
                    "description": "When true, list shipped examples.* names",
                },
            }
        ),
        permission="memory:read",
        tool_version=1,
        http_route="GET /query/space",
    ),
    ToolDefinition(
        name="search_query_space",
        description=(
            "Search checked-in manifest text (names, comments, tags,"
            " examples); never tenant content. k in 1..25."
        ),
        input_schema=_object_schema(
            properties={
                "query": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
            },
            required=("query",),
        ),
        permission="memory:read",
        tool_version=1,
        http_route="GET /query/space/search",
    ),
    ToolDefinition(
        name="list_saved_queries",
        description=(
            "List saved-query registry metadata. Default lists active"
            " versions only; drafts require an explicit status filter."
        ),
        input_schema=_object_schema(
            properties={"namespace": {"type": "string"}, "status": {"type": "string"}}
        ),
        permission="memory:read",
        tool_version=1,
        http_route="GET /query/saved",
    ),
    ToolDefinition(
        name="describe_saved_query",
        description=(
            "Describe one saved-query version: parameters, declared"
            " columns, validation state, and hashes."
        ),
        input_schema=_object_schema(
            properties={
                "namespace": {"type": "string"},
                "name": {"type": "string"},
                "version": _SAVED_QUERY_VERSION,
            },
            required=("namespace", "name"),
        ),
        permission="memory:read",
        tool_version=1,
        http_route="GET /query/saved/{namespace}/{name}",
    ),
    ToolDefinition(
        name="run_saved_query",
        description=(
            "Execute one active saved query through the same SQL sandbox."
            " Not a top-level intent operation; returns QueryResult/v1."
        ),
        input_schema=_object_schema(
            properties={
                "namespace": {"type": "string"},
                "name": {"type": "string"},
                "version": _SAVED_QUERY_VERSION,
                "parameters": _SQL_PARAMETERS,
                "max_rows": _MAX_ROWS,
            },
            required=("namespace", "name"),
        ),
        permission="memory:read",
        tool_version=1,
        http_route="POST /query/saved/{namespace}/{name}/run",
    ),
)

_BY_NAME: Final[dict[str, ToolDefinition]] = {
    definition.name: definition for definition in _TOOLS
}


def memory_tools() -> tuple[ToolDefinition, ...]:
    """Every memory tool, in the order hosts list them."""
    return _TOOLS


def tool(name: str) -> ToolDefinition:
    """The definition of one memory tool; ``KeyError`` for any other name."""
    return _BY_NAME[name]


def render_tools_list(
    tools: Iterable[ToolDefinition],
    *,
    project: bool,
    path_ingest: bool,
    read_only: bool,
) -> list[dict[str, object]]:
    """The MCP ``tools/list`` entries for ``tools``, in the order given.

    ``path_ingest`` keeps the local ``path`` body source on ``ingest`` — only a
    host running on the caller's machine sets it. ``read_only`` omits every
    ``memory:write`` tool. ``project`` adds the routing argument
    (:data:`PROJECT_ARGUMENT`) for a host that serves several deployments.
    """
    rendered: list[dict[str, object]] = []
    for definition in tools:
        if read_only and definition.permission == "memory:write":
            continue
        schema = copy.deepcopy(
            _INGEST_INPUT_SCHEMA_WITHOUT_PATH
            if definition.name == INGEST_TOOL_NAME and not path_ingest
            else definition.input_schema
        )
        if project:
            properties = schema["properties"]
            assert isinstance(properties, dict)
            properties["project"] = copy.deepcopy(PROJECT_ARGUMENT)
        rendered.append(
            {
                "name": definition.name,
                "description": definition.description,
                "inputSchema": schema,
                "annotations": definition.annotations,
            }
        )
    return rendered
