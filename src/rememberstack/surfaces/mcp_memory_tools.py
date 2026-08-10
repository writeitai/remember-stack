"""Shared MCP tools for store/write and pipeline readiness (Layer 1 memory verbs).

Both MCP servers advertise and dispatch the same two static tools — ``ingest``
and ``pipeline_readiness`` — from this module so schemas, argument parsing, and
structured error envelopes cannot drift. Recipe and open-query tools stay in
their own modules; this is only the write/readiness pair D37 requires on every
general-purpose memory surface.

Size preflight uses limits from a served capability document when the backend
exposes one. When no capability document is available, the client does not
invent a body-size ceiling — the server rejects and the mapped error is
returned (client-access design §3.1; design-owner ruling O1).

Dependency-light: safe for the base client wheel that hosts remote MCP.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
import mimetypes
from pathlib import Path
from typing import Final
from typing import Literal
from typing import Protocol
from uuid import UUID

from rememberstack.model.client import PipelineReadinessReport
from rememberstack.model.documents import IngestedVersion

INGEST_TOOL_NAME: Final = "ingest"
PIPELINE_READINESS_TOOL_NAME: Final = "pipeline_readiness"
MEMORY_WRITE_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {INGEST_TOOL_NAME, PIPELINE_READINESS_TOOL_NAME}
)

_FILENAME_MAX_LEN: Final = 512
_MIME_MAX_LEN: Final = 255
_TITLE_MAX_LEN: Final = 512
_SOURCE_KIND_MAX_LEN: Final = 128
_SOURCE_REF_MAX_LEN: Final = 512
_SOURCE_VERSION_REF_MAX_LEN: Final = 512
_VERSION_IDS_MAX: Final = 1000

_INGEST_DESCRIPTION: Final = (
    "Store a document into this deployment's memory (E0 write). Returns a"
    " version_id immediately; the indexing pipeline is asynchronous and may"
    " take many minutes (structure alone has been measured at ~11 minutes on a"
    " ~2.5KB file). Do NOT call recipe recall tools expecting this content until"
    " pipeline_readiness reports ready=true for the version_id. Prefer path when"
    " the file exists on the local machine running this MCP server; use text for"
    " short UTF-8 notes already in context; use content_base64 for binary."
    " Bodies must be non-empty; deployments may enforce a maximum body size"
    " (oversized or empty bodies map to structured body_too_large / empty_body"
    " errors). source_kind and source_ref must be supplied together when either"
    " is set (stable lineage)."
)

_PIPELINE_READINESS_DESCRIPTION: Final = (
    "Inspect whether one or more document version_ids have finished the"
    " continuous pipeline (and optionally projections) and are safe to recall."
    " Call after ingest with the returned version_id. ready=true means recipe"
    " tools may see the content (subject to retrieval relevance). ready=false is"
    " normal while workers run — keep polling; inspect stages[].status for"
    " failed/dead_letter."
)

_INGEST_INPUT_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "path": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Local filesystem path readable by this MCP process. Mutually"
                " exclusive with text and content_base64. Filename and mime are"
                " inferred when omitted."
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
            "maxLength": _FILENAME_MAX_LEN,
            "description": (
                "Required when text or content_base64 is used. Optional with"
                " path (defaults to the path basename)."
            ),
        },
        "mime": {
            "type": "string",
            "minLength": 1,
            "maxLength": _MIME_MAX_LEN,
            "description": (
                "Optional. Default: guessed from path/filename, else text/plain"
                " for text, application/octet-stream for content_base64."
            ),
        },
        "title": {
            "type": "string",
            "maxLength": _TITLE_MAX_LEN,
            "description": "Optional human title forwarded to the engine.",
        },
        "source_kind": {
            "type": "string",
            "minLength": 1,
            "maxLength": _SOURCE_KIND_MAX_LEN,
            "description": (
                "Lineage class (e.g. agent, cli, feeder). Must be paired with"
                " source_ref."
            ),
        },
        "source_ref": {
            "type": "string",
            "minLength": 1,
            "maxLength": _SOURCE_REF_MAX_LEN,
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
            "maxLength": _SOURCE_VERSION_REF_MAX_LEN,
            "description": (
                "Optional upstream revision label. Requires source_kind/source_ref."
            ),
        },
    },
    "oneOf": [
        {"required": ["path"]},
        {"required": ["text", "filename"]},
        {"required": ["content_base64", "filename"]},
    ],
}

_PIPELINE_READINESS_INPUT_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["version_ids"],
    "properties": {
        "version_ids": {
            "type": "array",
            "minItems": 1,
            "maxItems": _VERSION_IDS_MAX,
            "items": {"type": "string", "minLength": 1},
            "description": "Document version UUIDs from ingest.",
        },
        "require_projections": {
            "type": "boolean",
            "default": True,
            "description": (
                "When true, report ready only if aggregate projections are also"
                " fresh (engine default)."
            ),
        },
    },
}


class MemoryWriteBackend(Protocol):
    """Authority that performs ingest and readiness for one MCP composition."""

    def ingest(
        self,
        *,
        content: bytes,
        filename: str,
        mime: str,
        title: str | None,
        source_kind: str | None,
        source_ref: str | None,
        source_modified_at: datetime | None,
        versioning_mode: Literal["snapshot", "living"],
        source_version_ref: str | None,
    ) -> IngestedVersion:
        """Accept one document body into E0 and return the version identity."""
        ...

    def pipeline_readiness(
        self, *, version_ids: tuple[UUID, ...], require_projections: bool
    ) -> PipelineReadinessReport:
        """Inspect continuous-stage and projection readiness for version ids."""
        ...

    def max_ingest_body_bytes(self) -> int | None:
        """Served capability max body size, or ``None`` when none is available.

        ``None`` means do not client-preflight body size — let the server reject
        and map the error. Never invent a hard-coded ceiling here.
        """
        ...


@dataclass(frozen=True, slots=True)
class ToolError:
    """Structured MCP tool error for write/readiness tools."""

    code: str
    message: str
    http_status: int
    retryable: bool
    agent_action: str
    reason_code: str | None = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-serializable envelope fields (omit unset optionals)."""
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "agent_action": self.agent_action,
        }
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload


class MemoryToolArgumentError(Exception):
    """Client-side argument or body resolution failed before a backend call."""

    def __init__(self, *, error: ToolError) -> None:
        super().__init__(error.message)
        self.error = error


def memory_write_tool_descriptors() -> list[dict[str, object]]:
    """MCP ``tools/list`` entries for ``ingest`` and ``pipeline_readiness``."""
    return [
        {
            "name": INGEST_TOOL_NAME,
            "description": _INGEST_DESCRIPTION,
            "inputSchema": _INGEST_INPUT_SCHEMA,
        },
        {
            "name": PIPELINE_READINESS_TOOL_NAME,
            "description": _PIPELINE_READINESS_DESCRIPTION,
            "inputSchema": _PIPELINE_READINESS_INPUT_SCHEMA,
        },
    ]


def handle_memory_write_tool(
    *, name: str, arguments: Mapping[str, object], backend: MemoryWriteBackend | None
) -> dict[str, object]:
    """Dispatch one write/readiness tool to a success or structured error result.

    When ``backend`` is ``None`` the tools are not composed (recipe-only local
    MCP). Unknown names are the caller's responsibility — this function only
    handles ``MEMORY_WRITE_TOOL_NAMES``.
    """
    if name not in MEMORY_WRITE_TOOL_NAMES:
        raise ValueError(f"not a memory write tool: {name!r}")
    if backend is None:
        return _error_result(
            ToolError(
                code="tool_not_composed",
                message=(
                    f"MCP tool {name!r} is not composed on this server"
                    " (ingest/readiness ports absent)."
                ),
                http_status=404,
                retryable=False,
                agent_action=(
                    "Use remote MCP against a deployment that exposes write, or"
                    " compose the full local MCP profile with ingest and"
                    " pipeline_readiness ports."
                ),
            )
        )
    try:
        if name == INGEST_TOOL_NAME:
            payload = _run_ingest(arguments=arguments, backend=backend)
        else:
            payload = _run_pipeline_readiness(arguments=arguments, backend=backend)
    except MemoryToolArgumentError as error:
        return _error_result(error.error)
    except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
        mapped = map_backend_error(error)
        return _error_result(mapped)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
        "isError": False,
    }


def map_backend_error(error: BaseException) -> ToolError:
    """Map an SDK/HTTP/backend failure into the structured tool error envelope.

    Accepts ``MemoryApiError``-shaped objects (``status_code`` + ``detail``)
    without importing the SDK type, so local port adapters can raise ordinary
    exceptions that still map when they carry the same attributes.
    """
    status_code = getattr(error, "status_code", None)
    detail = getattr(error, "detail", None)
    if isinstance(status_code, int) and isinstance(detail, str):
        return _map_http_style_error(status_code=status_code, detail=detail)
    if isinstance(status_code, int) and detail is not None:
        return _map_http_style_error(status_code=status_code, detail=str(detail))
    return ToolError(
        code="transport_error",
        message=str(error) or error.__class__.__name__,
        http_status=0,
        retryable=True,
        agent_action=(
            "Retry with back-off; check REMEMBERSTACK_API_URL, credentials, and"
            " network reachability."
        ),
    )


def _run_ingest(
    *, arguments: Mapping[str, object], backend: MemoryWriteBackend
) -> dict[str, object]:
    """Parse args, optionally preflight size from capability limits, ingest."""
    parsed = _parse_ingest_arguments(arguments=arguments)
    limit = backend.max_ingest_body_bytes()
    if not parsed.content:
        raise MemoryToolArgumentError(
            error=ToolError(
                code="empty_body",
                message="Ingest body is empty.",
                http_status=422,
                retryable=False,
                agent_action=(
                    "Provide non-empty path / text / content_base64 content."
                ),
            )
        )
    if limit is not None and len(parsed.content) > limit:
        raise MemoryToolArgumentError(
            error=ToolError(
                code="body_too_large",
                message=(
                    f"Ingest body exceeds the deployment capability limit of"
                    f" {limit} bytes."
                ),
                http_status=413,
                retryable=False,
                agent_action=(
                    "Split or shorten the document; do not retry the same payload."
                ),
            )
        )
    ingested = backend.ingest(
        content=parsed.content,
        filename=parsed.filename,
        mime=parsed.mime,
        title=parsed.title,
        source_kind=parsed.source_kind,
        source_ref=parsed.source_ref,
        source_modified_at=parsed.source_modified_at,
        versioning_mode=parsed.versioning_mode,
        source_version_ref=parsed.source_version_ref,
    )
    return _ingest_success_payload(ingested=ingested)


def _run_pipeline_readiness(
    *, arguments: Mapping[str, object], backend: MemoryWriteBackend
) -> dict[str, object]:
    """Parse readiness args and return the report as a plain JSON dict."""
    version_ids, require_projections = _parse_pipeline_readiness_arguments(
        arguments=arguments
    )
    report = backend.pipeline_readiness(
        version_ids=version_ids, require_projections=require_projections
    )
    return report.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class _ParsedIngest:
    content: bytes
    filename: str
    mime: str
    title: str | None
    source_kind: str | None
    source_ref: str | None
    source_modified_at: datetime | None
    versioning_mode: Literal["snapshot", "living"]
    source_version_ref: str | None


def _parse_ingest_arguments(*, arguments: Mapping[str, object]) -> _ParsedIngest:
    """Validate mutual exclusion, lineage pairing, and resolve body bytes."""
    _reject_unknown_keys(
        arguments=arguments,
        allowed={
            "path",
            "text",
            "content_base64",
            "filename",
            "mime",
            "title",
            "source_kind",
            "source_ref",
            "versioning_mode",
            "source_modified_at",
            "source_version_ref",
        },
    )
    path = _optional_nonempty_string(arguments, key="path", max_length=None)
    text = _optional_string(arguments, key="text")
    content_base64 = _optional_nonempty_string(
        arguments, key="content_base64", max_length=None
    )
    body_modes = [
        name
        for name, value in (
            ("path", path),
            ("text", text),
            ("content_base64", content_base64),
        )
        if value is not None
    ]
    if len(body_modes) != 1:
        raise MemoryToolArgumentError(
            error=ToolError(
                code="invalid_arguments",
                message=(
                    "Pass exactly one of path, text, or content_base64"
                    f" (got {body_modes or 'none'})."
                ),
                http_status=422,
                retryable=False,
                agent_action=(
                    "Supply exactly one body source: path, text, or content_base64."
                ),
            )
        )

    filename = _optional_nonempty_string(
        arguments, key="filename", max_length=_FILENAME_MAX_LEN
    )
    mime = _optional_nonempty_string(arguments, key="mime", max_length=_MIME_MAX_LEN)
    title = _optional_string(arguments, key="title")
    if title is not None and len(title) > _TITLE_MAX_LEN:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message=f"title must be at most {_TITLE_MAX_LEN} characters."
            )
        )

    source_kind = _optional_nonempty_string(
        arguments, key="source_kind", max_length=_SOURCE_KIND_MAX_LEN
    )
    source_ref = _optional_nonempty_string(
        arguments, key="source_ref", max_length=_SOURCE_REF_MAX_LEN
    )
    source_version_ref = _optional_nonempty_string(
        arguments, key="source_version_ref", max_length=_SOURCE_VERSION_REF_MAX_LEN
    )
    versioning_mode = _parse_versioning_mode(arguments.get("versioning_mode"))
    source_modified_at = _parse_source_modified_at(arguments.get("source_modified_at"))

    if (source_kind is None) != (source_ref is None):
        raise MemoryToolArgumentError(
            error=ToolError(
                code="source_lineage_pair",
                message="source_kind and source_ref must be supplied together.",
                http_status=422,
                retryable=False,
                agent_action=("Send both source_kind and source_ref, or neither."),
            )
        )
    if source_kind is None and (
        source_modified_at is not None
        or source_version_ref is not None
        or versioning_mode != "snapshot"
    ):
        raise MemoryToolArgumentError(
            error=ToolError(
                code="source_lineage_pair",
                message=(
                    "source timestamps, revisions, and living mode require"
                    " source_kind/source_ref."
                ),
                http_status=422,
                retryable=False,
                agent_action=(
                    "Provide source_kind and source_ref together with lineage fields."
                ),
            )
        )

    if path is not None:
        content, resolved_filename, resolved_mime = _resolve_path_body(
            path=path, filename=filename, mime=mime
        )
    elif text is not None:
        if not text:
            raise MemoryToolArgumentError(
                error=_invalid_arguments(
                    message="text must be non-empty when used as the body source."
                )
            )
        content = text.encode("utf-8")
        if filename is None:
            raise MemoryToolArgumentError(
                error=_invalid_arguments(
                    message="filename is required when text is used."
                )
            )
        resolved_filename = filename
        resolved_mime = mime or "text/plain"
    else:
        assert content_base64 is not None
        content = _decode_base64(content_base64)
        if filename is None:
            raise MemoryToolArgumentError(
                error=_invalid_arguments(
                    message="filename is required when content_base64 is used."
                )
            )
        resolved_filename = filename
        resolved_mime = mime or "application/octet-stream"

    return _ParsedIngest(
        content=content,
        filename=resolved_filename,
        mime=resolved_mime,
        title=title if title else None,
        source_kind=source_kind,
        source_ref=source_ref,
        source_modified_at=source_modified_at,
        versioning_mode=versioning_mode,
        source_version_ref=source_version_ref,
    )


def _resolve_path_body(
    *, path: str, filename: str | None, mime: str | None
) -> tuple[bytes, str, str]:
    """Read a local path and apply filename/mime defaults."""
    target = Path(path)
    try:
        content = target.read_bytes()
    except OSError as error:
        raise MemoryToolArgumentError(
            error=ToolError(
                code="path_unreadable",
                message=(
                    f"Path is not readable on the MCP host filesystem: {path}"
                    f" ({error})."
                ),
                http_status=400,
                retryable=False,
                agent_action=(
                    "Check path on the machine running the MCP server (not the"
                    " remote engine host)."
                ),
            )
        ) from error
    resolved_filename = filename or target.name
    if not resolved_filename:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message="filename could not be inferred from path; pass filename."
            )
        )
    if mime:
        resolved_mime = mime
    else:
        guessed = mimetypes.guess_type(resolved_filename)[0]
        resolved_mime = guessed or "application/octet-stream"
    return content, resolved_filename, resolved_mime


def _decode_base64(value: str) -> bytes:
    """Decode standard base64; reject data-URL prefixes and bad padding."""
    if value.startswith("data:"):
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message=("content_base64 must be raw standard base64, not a data: URL.")
            )
        )
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message="content_base64 is not valid standard base64."
            )
        ) from error


def _parse_pipeline_readiness_arguments(
    *, arguments: Mapping[str, object]
) -> tuple[tuple[UUID, ...], bool]:
    """Validate readiness tool args and parse UUID version ids."""
    _reject_unknown_keys(
        arguments=arguments, allowed={"version_ids", "require_projections"}
    )
    raw_ids = arguments.get("version_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message="version_ids must be a non-empty array of UUID strings."
            )
        )
    if len(raw_ids) > _VERSION_IDS_MAX:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message=(
                    f"version_ids must contain at most {_VERSION_IDS_MAX} entries."
                )
            )
        )
    version_ids: list[UUID] = []
    for index, item in enumerate(raw_ids):
        if not isinstance(item, str) or not item.strip():
            raise MemoryToolArgumentError(
                error=_invalid_arguments(
                    message=f"version_ids[{index}] must be a non-empty string."
                )
            )
        try:
            version_ids.append(UUID(item))
        except ValueError as error:
            raise MemoryToolArgumentError(
                error=_invalid_arguments(
                    message=f"version_ids[{index}] is not a valid UUID: {item!r}."
                )
            ) from error
    require_projections = arguments.get("require_projections", True)
    # bool is a subclass of int; reject 0/1 integers explicitly.
    if type(require_projections) is not bool:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message="require_projections must be a boolean when provided."
            )
        )
    return tuple(version_ids), require_projections


def _parse_versioning_mode(value: object) -> Literal["snapshot", "living"]:
    """Default snapshot; reject unknown modes."""
    if value is None:
        return "snapshot"
    if value in ("snapshot", "living"):
        return value  # type: ignore[return-value]
    raise MemoryToolArgumentError(
        error=_invalid_arguments(
            message="versioning_mode must be 'snapshot' or 'living'."
        )
    )


def _parse_source_modified_at(value: object) -> datetime | None:
    """Parse optional ISO-8601 UTC timestamp."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message="source_modified_at must be an ISO-8601 timestamp string."
            )
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message="source_modified_at must be a valid ISO-8601 timestamp."
            )
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message="source_modified_at must be timezone-aware UTC."
            )
        )
    return parsed.astimezone(timezone.utc)


def _ingest_success_payload(*, ingested: IngestedVersion) -> dict[str, object]:
    """IngestedVersion JSON plus async pipeline guidance for the agent."""
    version_id = str(ingested.version_id)
    if ingested.created:
        guidance = (
            "Ingest accepted. Wait until pipeline_readiness.ready is true before"
            " treating this content as recallable. Stages can take many minutes;"
            " poll with exponential back-off (start ~30s, then 30–60s)."
            " created=false means content-hash no-op: no new pipeline run."
        )
    else:
        guidance = (
            "Ingest was a content-hash no-op (created=false): this version already"
            " exists and no new pipeline run was started. Call pipeline_readiness"
            " once; if ready=true the content is already recallable."
        )
    return {
        "deployment_id": str(ingested.deployment_id),
        "doc_id": str(ingested.doc_id),
        "version_id": version_id,
        "content_hash": ingested.content_hash,
        "created": ingested.created,
        "pipeline": {
            "status": "accepted_not_ready",
            "next_tool": PIPELINE_READINESS_TOOL_NAME,
            "poll_with": {"version_ids": [version_id], "require_projections": True},
            "guidance": guidance,
        },
    }


def _map_http_style_error(*, status_code: int, detail: str) -> ToolError:
    """Map status + detail string (including cloud prefix codes) to ToolError."""
    code, reason_code = _split_detail_code(detail=detail)

    if code == "body_too_large" or status_code == 413:
        return ToolError(
            code="body_too_large",
            message=(
                detail
                if code == "body_too_large"
                else "Ingest body exceeds the deployment size limit."
            ),
            http_status=413,
            retryable=False,
            agent_action=(
                "Split or shorten the document; do not retry the same payload."
            ),
            reason_code=reason_code,
        )
    if code == "empty_body":
        return ToolError(
            code="empty_body",
            message=detail if detail else "Ingest body is empty.",
            http_status=422,
            retryable=False,
            agent_action=("Provide non-empty path / text / content_base64 content."),
            reason_code=reason_code,
        )
    if code == "dispatch_refused" or (
        status_code == 403 and detail.startswith("dispatch_refused")
    ):
        return ToolError(
            code="dispatch_refused",
            message=detail,
            http_status=403,
            retryable=False,
            agent_action=(
                "Surface the reason to the user/operator; do not busy-retry."
                " Typical causes: spend cap, missing policy, halt."
            ),
            reason_code=reason_code,
        )
    if code == "dispatch_parked" or (
        status_code == 423 and detail.startswith("dispatch_parked")
    ):
        return ToolError(
            code="dispatch_parked",
            message=detail,
            http_status=423,
            retryable=False,
            agent_action=(
                "Stop automated retries and notify a human; park is policy, not"
                " a transient blip."
            ),
            reason_code=reason_code,
        )
    if status_code == 401:
        return ToolError(
            code="unauthorized",
            message=detail or "Unauthorized.",
            http_status=401,
            retryable=False,
            agent_action=(
                "Refresh or replace REMEMBERSTACK_API_AUTHORIZATION; re-mint if"
                " the token was revoked."
            ),
            reason_code=reason_code,
        )
    if status_code == 403:
        return ToolError(
            code="forbidden",
            message=detail or "Forbidden.",
            http_status=403,
            retryable=False,
            agent_action=(
                "Use a token for the configured deployment; check origin and"
                " scope constraints."
            ),
            reason_code=reason_code,
        )
    if status_code == 0:
        return ToolError(
            code="transport_error",
            message=detail or "Transport failure talking to the deployment API.",
            http_status=0,
            retryable=True,
            agent_action=(
                "Retry with back-off; check REMEMBERSTACK_API_URL and network."
            ),
            reason_code=reason_code,
        )
    if 400 <= status_code < 500:
        return ToolError(
            code="engine_client_error",
            message=detail or f"Client error from engine (HTTP {status_code}).",
            http_status=status_code,
            retryable=False,
            agent_action="Read the message; fix arguments. Do not retry blindly.",
            reason_code=reason_code,
        )
    if status_code >= 500:
        return ToolError(
            code="engine_unavailable",
            message=detail or f"Engine unavailable (HTTP {status_code}).",
            http_status=status_code,
            retryable=True,
            agent_action=(
                "Retry with back-off (3–5 attempts, 2s→30s). If still failing,"
                " report an operator outage."
            ),
            reason_code=reason_code,
        )
    return ToolError(
        code="engine_client_error",
        message=detail or f"Unexpected status {status_code}.",
        http_status=status_code,
        retryable=False,
        agent_action="Read the message; fix arguments or report to an operator.",
        reason_code=reason_code,
    )


def _split_detail_code(*, detail: str) -> tuple[str, str | None]:
    """Split ``code`` or ``code:reason`` cloud detail forms."""
    if ":" in detail:
        head, tail = detail.split(":", 1)
        if head in {
            "dispatch_refused",
            "dispatch_parked",
            "body_too_large",
            "empty_body",
        }:
            return head, tail or None
    known = {
        "body_too_large",
        "empty_body",
        "dispatch_refused",
        "dispatch_parked",
        "data_plane_upstream_error",
    }
    if detail in known:
        return detail, None
    return detail, None


def _error_result(error: ToolError) -> dict[str, object]:
    """MCP tools/call error result with one JSON text block."""
    return {
        "content": [{"type": "text", "text": json.dumps(error.as_dict())}],
        "isError": True,
    }


def _invalid_arguments(*, message: str) -> ToolError:
    """Common 422 invalid_arguments envelope."""
    return ToolError(
        code="invalid_arguments",
        message=message,
        http_status=422,
        retryable=False,
        agent_action="Fix the tool arguments and retry.",
    )


def _reject_unknown_keys(*, arguments: Mapping[str, object], allowed: set[str]) -> None:
    """Fail closed on unexpected keys (matches open-query strictness)."""
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message=f"Unknown argument keys: {', '.join(unknown)}."
            )
        )


def _optional_nonempty_string(
    arguments: Mapping[str, object], *, key: str, max_length: int | None
) -> str | None:
    """Read an optional non-empty string field with length bounds."""
    if key not in arguments or arguments[key] is None:
        return None
    value = arguments[key]
    if not isinstance(value, str):
        raise MemoryToolArgumentError(
            error=_invalid_arguments(message=f"{key} must be a string.")
        )
    if not value:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(message=f"{key} must be non-empty when set.")
        )
    if max_length is not None and len(value) > max_length:
        raise MemoryToolArgumentError(
            error=_invalid_arguments(
                message=f"{key} must be at most {max_length} characters."
            )
        )
    return value


def _optional_string(arguments: Mapping[str, object], *, key: str) -> str | None:
    """Read an optional string that may be empty (text body can still be empty)."""
    if key not in arguments or arguments[key] is None:
        return None
    value = arguments[key]
    if not isinstance(value, str):
        raise MemoryToolArgumentError(
            error=_invalid_arguments(message=f"{key} must be a string.")
        )
    return value
