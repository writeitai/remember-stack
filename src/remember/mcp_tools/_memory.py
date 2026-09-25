"""Argument parsing and execution of the memory write tools.

``ingest``, ``pipeline_readiness`` and ``delete_document`` share one parser
each and one structured error envelope (:mod:`._errors`), whichever host runs
them.

Size preflight uses limits from a served capability document when the backend
exposes one. When no capability document is available, the client does not
invent a body-size ceiling for wire payloads — the server rejects and the mapped
error is returned (client-access design §3.1; design-owner ruling O1).

Path bodies are a separate local concern: a host offers the ``path`` argument
only when it runs on the caller's machine (``path_ingest``), and it is accepted
only when the operator configures allowlisted roots
(``REMEMBERSTACK_MCP_INGEST_ROOTS``). Reading a path always applies a
process-local resource guard so a hostile or accidental path cannot hang or OOM
the MCP process; that guard is **not** a cloud body ceiling.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
import json
import logging
import os
from pathlib import Path
import stat
from typing import Final
from typing import Literal
from typing import Protocol
from uuid import UUID

from pydantic import Field
from pydantic import field_validator
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from remember.mcp_tools._definitions import DELETE_DOCUMENT_TOOL_NAME
from remember.mcp_tools._definitions import FILENAME_MAX_LEN
from remember.mcp_tools._definitions import INGEST_TOOL_NAME
from remember.mcp_tools._definitions import MEMORY_WRITE_TOOL_NAMES
from remember.mcp_tools._definitions import MIME_MAX_LEN
from remember.mcp_tools._definitions import PIPELINE_READINESS_TOOL_NAME
from remember.mcp_tools._definitions import SOURCE_KIND_MAX_LEN
from remember.mcp_tools._definitions import SOURCE_REF_MAX_LEN
from remember.mcp_tools._definitions import SOURCE_VERSION_REF_MAX_LEN
from remember.mcp_tools._definitions import TITLE_MAX_LEN
from remember.mcp_tools._definitions import VERSION_IDS_MAX
from remember.mcp_tools._errors import error_result
from remember.mcp_tools._errors import invalid_arguments
from remember.mcp_tools._errors import map_error
from remember.mcp_tools._errors import ToolArgumentError
from remember.mcp_tools._errors import ToolError
from remember.mime import infer_upload_mime
from remember.models import DocumentDeletion
from remember.models import IngestedVersion
from remember.models import PipelineReadinessReport
from remember.models import ReadinessRequirements

logger = logging.getLogger(__name__)

# Default LOCAL RESOURCE GUARD for path bodies when no served capability limit
# is available. This is process safety for the MCP host — not a cloud/O1 body
# ceiling. Override via REMEMBERSTACK_MCP_PATH_READ_MAX_BYTES.
_DEFAULT_PATH_READ_MAX_BYTES: Final = 256 * 1024 * 1024


class McpMemorySettings(BaseSettings):
    """Operator settings for MCP memory-write path safety.

    Env prefix ``REMEMBERSTACK_MCP_``:

    - ``INGEST_ROOTS`` — JSON array (or comma-separated) of directory roots
      allowed for the ``path`` body mode. Empty / unset refuses ``path`` (fail
      closed; no CWD/home default).
    - ``PATH_READ_MAX_BYTES`` — LOCAL RESOURCE GUARD for path reads when no
      served capability body limit is available. Not a cloud ceiling (O1).
    """

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_MCP_", extra="ignore")

    ingest_roots: tuple[Path, ...] = ()
    path_read_max_bytes: int = Field(default=_DEFAULT_PATH_READ_MAX_BYTES, gt=0)

    @field_validator("ingest_roots", mode="before")
    @classmethod
    def parse_ingest_roots(cls, value: object) -> object:
        """Accept JSON arrays or comma-separated path lists from env."""
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return ()
            if stripped.startswith("["):
                parsed = json.loads(stripped)
                if not isinstance(parsed, list):
                    raise ValueError("INGEST_ROOTS JSON value must be an array")
                return tuple(str(item) for item in parsed)
            return tuple(part.strip() for part in stripped.split(",") if part.strip())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return tuple(str(item) for item in value)
        return value


def load_mcp_memory_settings() -> McpMemorySettings:
    """Load MCP path-safety settings from the process environment."""
    return McpMemorySettings()


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
        self, *, version_ids: tuple[UUID, ...], require: ReadinessRequirements
    ) -> PipelineReadinessReport:
        """Inspect explicitly requested capabilities for version ids."""
        ...

    def max_ingest_body_bytes(self) -> int | None:
        """Served capability max body size, or ``None`` when none is available.

        ``None`` means do not invent a cloud body-size ceiling for wire payloads
        — let the server reject and map the error. Path mode still applies the
        local process resource guard from settings.
        """
        ...


class DocumentDeleteBackend(Protocol):
    """Authority that deletes one document for one MCP composition (D135)."""

    def delete_document(self, *, doc_id: UUID) -> DocumentDeletion:
        """Remove the document or raise a 404-shaped ``document_not_found``."""
        ...


def handle_memory_write_tool(
    *,
    name: str,
    arguments: Mapping[str, object],
    backend: MemoryWriteBackend | None,
    path_ingest: bool,
    settings: McpMemorySettings | None = None,
) -> dict[str, object]:
    """Dispatch one write/readiness tool to a success or structured error result.

    When ``backend`` is ``None`` the tools are not composed (operation-only local
    MCP). Unknown names are the caller's responsibility — this function only
    handles ``MEMORY_WRITE_TOOL_NAMES``. ``path_ingest`` must match what the
    host rendered: without it, ``path`` is an unknown argument.

    ``settings`` is optional so tests can inject roots without mutating the
    process environment; production callers leave it unset and load from env.
    """
    if name not in MEMORY_WRITE_TOOL_NAMES:
        raise ValueError(f"not a memory write tool: {name!r}")
    if backend is None:
        return error_result(
            ToolError(
                code="tool_not_composed",
                detail=(
                    f"MCP tool {name!r} is not composed on this server"
                    " (ingest/readiness ports absent)."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Use remote MCP against a deployment that exposes write, or"
                    " compose the full local MCP profile with ingest and"
                    " pipeline_readiness ports."
                ),
            )
        )
    resolved_settings = settings if settings is not None else load_mcp_memory_settings()
    try:
        if name == INGEST_TOOL_NAME:
            payload = _run_ingest(
                arguments=arguments,
                backend=backend,
                path_ingest=path_ingest,
                settings=resolved_settings,
            )
        else:
            payload = _run_pipeline_readiness(arguments=arguments, backend=backend)
    except ToolArgumentError as error:
        return error_result(error.error)
    except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
        mapped = map_error(error)
        # Failures never disappear: unexpected / local-backend defects keep a
        # full traceback at the MCP wire boundary (core value 6).
        if mapped.code in {"internal_error", "local_backend_error"}:
            logger.exception("MCP memory tool %s failed with %s", name, mapped.code)
        return error_result(mapped)
    return {
        "content": [{"type": "text", "text": json.dumps(payload, default=str)}],
        "isError": False,
    }


def handle_delete_document_tool(
    *, arguments: Mapping[str, object], backend: DocumentDeleteBackend | None
) -> dict[str, object]:
    """Dispatch ``delete_document`` to a success or structured error result."""
    if backend is None:
        return error_result(
            ToolError(
                code="tool_not_composed",
                detail=(
                    f"MCP tool {DELETE_DOCUMENT_TOOL_NAME!r} is not composed on this"
                    " server (read-only, or no deletion port)."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Tell the user deletion is not available on this server; do"
                    " not retry."
                ),
            )
        )
    try:
        doc_id = parse_delete_document_arguments(arguments=arguments)
        deletion = backend.delete_document(doc_id=doc_id)
    except ToolArgumentError as error:
        return error_result(error.error)
    except Exception as error:  # noqa: BLE001 — mapped at the MCP wire boundary
        if (
            getattr(error, "status_code", None) == 404
            and getattr(error, "detail", None) == "document_not_found"
        ):
            return error_result(
                ToolError(
                    code="document_not_found",
                    detail=(
                        f"No live document {arguments.get('doc_id')}: the id is"
                        " unknown or the document is already deleted."
                    ),
                    status_code=404,
                    retryable=False,
                    agent_action=(
                        "Do not retry. Check the doc_id; if the user meant this"
                        " document, it is already gone from memory."
                    ),
                )
            )
        if getattr(error, "status_code", None) == 503 and "forget_in_progress" in str(
            getattr(error, "detail", "")
        ):
            return error_result(
                ToolError(
                    code="forget_in_progress",
                    detail=(
                        "A hard forget is running on this deployment; nothing was"
                        " deleted."
                    ),
                    status_code=503,
                    retryable=True,
                    agent_action=(
                        "Retry the same delete later with back-off; the"
                        " deployment accepts no changes until the forget finishes."
                    ),
                )
            )
        mapped = map_error(error)
        if mapped.code in {"internal_error", "local_backend_error"}:
            logger.exception(
                "MCP tool %s failed with %s", DELETE_DOCUMENT_TOOL_NAME, mapped.code
            )
        return error_result(mapped)
    return {
        "content": [{"type": "text", "text": deletion.model_dump_json()}],
        "isError": False,
    }


def parse_delete_document_arguments(*, arguments: Mapping[str, object]) -> UUID:
    """Validate ``delete_document`` arguments and return the document id."""
    reject_unknown_keys(arguments=arguments, allowed={"doc_id"})
    raw = arguments.get("doc_id")
    if not isinstance(raw, str) or not raw:
        raise ToolArgumentError(
            error=invalid_arguments(detail="doc_id must be a non-empty string.")
        )
    try:
        return UUID(raw)
    except ValueError as error:
        raise ToolArgumentError(
            error=invalid_arguments(detail="doc_id must be a UUID.")
        ) from error


def _run_ingest(
    *,
    arguments: Mapping[str, object],
    backend: MemoryWriteBackend,
    path_ingest: bool,
    settings: McpMemorySettings,
) -> dict[str, object]:
    """Parse args (preflighting size from capability limits), then ingest."""
    parsed = parse_ingest_arguments(
        arguments=arguments,
        path_ingest=path_ingest,
        settings=settings,
        capability_limit=backend.max_ingest_body_bytes(),
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


def _check_body_size(*, content: bytes, capability_limit: int | None) -> None:
    """Refuse an empty body, or one over the served capability limit."""
    if not content:
        raise ToolArgumentError(
            error=ToolError(
                code="empty_body",
                detail="Ingest body is empty.",
                status_code=None,
                retryable=False,
                agent_action=(
                    "Provide non-empty path / text / content_base64 content."
                ),
            )
        )
    if capability_limit is not None and len(content) > capability_limit:
        raise ToolArgumentError(
            error=ToolError(
                code="body_too_large",
                detail=(
                    f"Ingest body exceeds the deployment capability limit of"
                    f" {capability_limit} bytes."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Split or shorten the document; do not retry the same payload."
                ),
            )
        )


def _run_pipeline_readiness(
    *, arguments: Mapping[str, object], backend: MemoryWriteBackend
) -> dict[str, object]:
    """Parse readiness args and return the report as a plain JSON dict."""
    version_ids, require = parse_pipeline_readiness_arguments(arguments=arguments)
    report = backend.pipeline_readiness(version_ids=version_ids, require=require)
    return report.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class ParsedIngest:
    """Validated ``ingest`` arguments with the body resolved to bytes."""

    content: bytes
    filename: str
    mime: str
    title: str | None
    source_kind: str | None
    source_ref: str | None
    source_modified_at: datetime | None
    versioning_mode: Literal["snapshot", "living"]
    source_version_ref: str | None


def parse_ingest_arguments(
    *,
    arguments: Mapping[str, object],
    path_ingest: bool,
    settings: McpMemorySettings,
    capability_limit: int | None,
) -> ParsedIngest:
    """Validate mutual exclusion, lineage pairing, and resolve body bytes.

    Without ``path_ingest`` the ``path`` argument is unknown, exactly as it is
    absent from the schema the host rendered.
    """
    reject_unknown_keys(
        arguments=arguments,
        allowed={
            *(("path",) if path_ingest else ()),
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
        raise ToolArgumentError(
            error=ToolError(
                code="invalid_arguments",
                detail=(
                    "Pass exactly one of path, text, or content_base64"
                    f" (got {body_modes or 'none'})."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Supply exactly one body source: path, text, or content_base64."
                ),
            )
        )

    filename = _optional_nonempty_string(
        arguments, key="filename", max_length=FILENAME_MAX_LEN
    )
    mime = _optional_nonempty_string(arguments, key="mime", max_length=MIME_MAX_LEN)
    title = _optional_string(arguments, key="title")
    if title is not None and len(title) > TITLE_MAX_LEN:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=f"title must be at most {TITLE_MAX_LEN} characters."
            )
        )

    source_kind = _optional_nonempty_string(
        arguments, key="source_kind", max_length=SOURCE_KIND_MAX_LEN
    )
    source_ref = _optional_nonempty_string(
        arguments, key="source_ref", max_length=SOURCE_REF_MAX_LEN
    )
    source_version_ref = _optional_nonempty_string(
        arguments, key="source_version_ref", max_length=SOURCE_VERSION_REF_MAX_LEN
    )
    versioning_mode = _parse_versioning_mode(arguments.get("versioning_mode"))
    source_modified_at = _parse_source_modified_at(arguments.get("source_modified_at"))

    if (source_kind is None) != (source_ref is None):
        raise ToolArgumentError(
            error=ToolError(
                code="source_lineage_pair",
                detail="source_kind and source_ref must be supplied together.",
                status_code=None,
                retryable=False,
                agent_action=("Send both source_kind and source_ref, or neither."),
            )
        )
    if source_kind is None and (
        source_modified_at is not None
        or source_version_ref is not None
        or versioning_mode != "snapshot"
    ):
        raise ToolArgumentError(
            error=ToolError(
                code="source_lineage_pair",
                detail=(
                    "source timestamps, revisions, and living mode require"
                    " source_kind/source_ref."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Provide source_kind and source_ref together with lineage fields."
                ),
            )
        )

    if path is not None:
        content, resolved_filename, resolved_mime = _resolve_path_body(
            path=path,
            filename=filename,
            mime=mime,
            settings=settings,
            capability_limit=capability_limit,
        )
    elif text is not None:
        if not text:
            raise ToolArgumentError(
                error=invalid_arguments(
                    detail="text must be non-empty when used as the body source."
                )
            )
        try:
            content = text.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ToolArgumentError(
                error=ToolError(
                    code="encoding_error",
                    detail=f"text is not encodable as UTF-8: {error}",
                    status_code=None,
                    retryable=False,
                    agent_action=(
                        "Remove lone surrogates / invalid code points, or send"
                        " content_base64 for binary."
                    ),
                )
            ) from error
        if filename is None:
            raise ToolArgumentError(
                error=invalid_arguments(
                    detail="filename is required when text is used."
                )
            )
        resolved_filename = filename
        # UTF-8 text takes a textual type from its filename (notes.md →
        # text/markdown); anything else is sent as text/plain.
        guessed = infer_upload_mime(filename)
        resolved_mime = mime or (
            guessed if guessed and guessed.startswith("text/") else "text/plain"
        )
    else:
        assert content_base64 is not None
        content = _decode_base64(content_base64)
        if filename is None:
            raise ToolArgumentError(
                error=invalid_arguments(
                    detail="filename is required when content_base64 is used."
                )
            )
        resolved_filename = filename
        # Bytes match the SDK: the type follows the filename unless the caller
        # supplied mime; an unknown extension is application/octet-stream.
        resolved_mime = (
            mime or infer_upload_mime(filename) or "application/octet-stream"
        )

    _check_body_size(content=content, capability_limit=capability_limit)
    return ParsedIngest(
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
    *,
    path: str,
    filename: str | None,
    mime: str | None,
    settings: McpMemorySettings,
    capability_limit: int | None,
) -> tuple[bytes, str, str]:
    """Read a local path under configured roots with fail-closed safety checks.

    Rules (design-owner fail-closed):

    1. ``path`` is accepted only when ``ingest_roots`` is non-empty.
    2. The path is fully resolved; after resolution it must stay inside a root
       (symlink escape fails).
    3. The target must be a regular file (not FIFO/device/directory).
    4. Size is checked before reading; the read cap is the served capability
       limit when one exists, otherwise ``path_read_max_bytes`` (LOCAL RESOURCE
       GUARD — not a cloud ceiling).
    """
    if "\x00" in path:
        raise ToolArgumentError(
            error=ToolError(
                code="path_not_allowed",
                detail="path must not contain embedded NUL bytes.",
                status_code=None,
                retryable=False,
                agent_action="Pass a clean filesystem path without NUL characters.",
            )
        )
    roots = tuple(settings.ingest_roots)
    if not roots:
        raise ToolArgumentError(
            error=ToolError(
                code="path_not_allowed",
                detail=(
                    "path body mode is disabled: no ingest roots are configured."
                    " Set REMEMBERSTACK_MCP_INGEST_ROOTS to a JSON array of"
                    " absolute directories the operator allows this MCP process to"
                    ' read (example: ["/var/remember/inbox"]), or send the body'
                    " as text / content_base64 instead."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Use text or content_base64, or ask the operator to configure"
                    " REMEMBERSTACK_MCP_INGEST_ROOTS. Do not retry path until roots"
                    " are set."
                ),
            )
        )

    try:
        target = Path(path).expanduser()
        resolved = target.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ToolArgumentError(
            error=ToolError(
                code="path_unreadable",
                detail=(
                    f"Path is not readable on the MCP host filesystem: {path}"
                    f" ({error})."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Check path on the machine running the MCP server (not the"
                    " remote engine host)."
                ),
            )
        ) from error

    if not _path_is_under_roots(resolved=resolved, roots=roots):
        raise ToolArgumentError(
            error=ToolError(
                code="path_not_allowed",
                detail=(
                    f"Resolved path {str(resolved)!r} is outside the configured"
                    " REMEMBERSTACK_MCP_INGEST_ROOTS allowlist (symlink escape and"
                    " absolute paths outside roots are rejected)."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Place the file under an allowlisted root, or use"
                    " text/content_base64. Ask the operator to extend roots only"
                    " when intentional."
                ),
            )
        )

    read_cap = (
        capability_limit
        if capability_limit is not None
        else settings.path_read_max_bytes
    )
    # Stat before open so FIFOs/devices never hang the MCP process on open().
    try:
        pre_stat = resolved.stat()
    except OSError as error:
        raise ToolArgumentError(
            error=ToolError(
                code="path_unreadable",
                detail=(
                    f"Path is not readable on the MCP host filesystem: {path}"
                    f" ({error})."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Check path on the machine running the MCP server (not the"
                    " remote engine host)."
                ),
            )
        ) from error
    if not stat.S_ISREG(pre_stat.st_mode):
        raise ToolArgumentError(
            error=ToolError(
                code="path_not_regular_file",
                detail=(
                    f"Path is not a regular file (directories, FIFOs, devices, and"
                    f" special files are rejected): {path}."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Point path at a regular file, or send text/content_base64."
                ),
            )
        )
    if pre_stat.st_size > read_cap:
        raise ToolArgumentError(
            error=ToolError(
                code="path_too_large",
                detail=(
                    f"Path file is {pre_stat.st_size} bytes, which exceeds"
                    f" the read cap of {read_cap} bytes"
                    + (
                        " (deployment capability limit)."
                        if capability_limit is not None
                        else (
                            " (LOCAL RESOURCE GUARD"
                            " REMEMBERSTACK_MCP_PATH_READ_MAX_BYTES — process"
                            " safety, not a cloud body ceiling)."
                        )
                    )
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Split the file, raise the local resource guard only if"
                    " intentional, or use a deployment that publishes a"
                    " higher capability limit."
                ),
            )
        )
    try:
        with resolved.open("rb") as handle:
            # Re-check via fstat after open (TOCTOU belt).
            file_stat = _fstat_regular_file(handle=handle, path=str(resolved))
            if file_stat.st_size > read_cap:
                raise ToolArgumentError(
                    error=ToolError(
                        code="path_too_large",
                        detail=(
                            f"Path file is {file_stat.st_size} bytes, which exceeds"
                            f" the read cap of {read_cap} bytes."
                        ),
                        status_code=None,
                        retryable=False,
                        agent_action=(
                            "Split the file or raise the configured read cap."
                        ),
                    )
                )
            content = handle.read(read_cap + 1)
    except ToolArgumentError:
        raise
    except OSError as error:
        raise ToolArgumentError(
            error=ToolError(
                code="path_unreadable",
                detail=(
                    f"Path is not readable on the MCP host filesystem: {path}"
                    f" ({error})."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Check path on the machine running the MCP server (not the"
                    " remote engine host)."
                ),
            )
        ) from error

    if len(content) > read_cap:
        raise ToolArgumentError(
            error=ToolError(
                code="path_too_large",
                detail=(
                    f"Path file exceeded the read cap of {read_cap} bytes during read."
                ),
                status_code=None,
                retryable=False,
                agent_action="Split the file or raise the configured read cap.",
            )
        )

    resolved_filename = filename or resolved.name
    if not resolved_filename:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail="filename could not be inferred from path; pass filename."
            )
        )
    # MIME matches the SDK: guess from the real target path name, not an
    # overridden filename, unless the caller supplied mime explicitly.
    resolved_mime = (
        mime or infer_upload_mime(resolved.name) or "application/octet-stream"
    )
    return content, resolved_filename, resolved_mime


def _path_is_under_roots(*, resolved: Path, roots: Sequence[Path]) -> bool:
    """Return True when ``resolved`` is equal to or inside a configured root."""
    for root in roots:
        try:
            root_resolved = root.expanduser().resolve(strict=False)
        except (OSError, RuntimeError):
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return True
    return False


def _fstat_regular_file(*, handle: object, path: str) -> os.stat_result:
    """fstat an open file and require a regular file (reject FIFO/device/dir)."""
    fileno_attr = getattr(handle, "fileno", None)
    if fileno_attr is None or not callable(fileno_attr):
        raise ToolArgumentError(
            error=ToolError(
                code="path_unreadable",
                detail=f"Path handle cannot be fstat'd: {path}.",
                status_code=None,
                retryable=False,
                agent_action="Pass a regular filesystem file path.",
            )
        )
    fd = fileno_attr()
    if not isinstance(fd, int):
        raise ToolArgumentError(
            error=ToolError(
                code="path_unreadable",
                detail=f"Path handle fileno is not an int: {path}.",
                status_code=None,
                retryable=False,
                agent_action="Pass a regular filesystem file path.",
            )
        )
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ToolArgumentError(
            error=ToolError(
                code="path_not_regular_file",
                detail=(
                    f"Path is not a regular file (directories, FIFOs, devices, and"
                    f" special files are rejected): {path}."
                ),
                status_code=None,
                retryable=False,
                agent_action=(
                    "Point path at a regular file, or send text/content_base64."
                ),
            )
        )
    return file_stat


def _decode_base64(value: str) -> bytes:
    """Decode standard base64; reject data-URL prefixes and bad padding."""
    if value.startswith("data:"):
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=("content_base64 must be raw standard base64, not a data: URL.")
            )
        )
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail="content_base64 is not valid standard base64."
            )
        ) from error


def parse_pipeline_readiness_arguments(
    *, arguments: Mapping[str, object]
) -> tuple[tuple[UUID, ...], ReadinessRequirements]:
    """Validate readiness tool args and parse UUID version ids."""
    reject_unknown_keys(arguments=arguments, allowed={"version_ids", "require"})
    raw_ids = arguments.get("version_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail="version_ids must be a non-empty array of UUID strings."
            )
        )
    if len(raw_ids) > VERSION_IDS_MAX:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=(f"version_ids must contain at most {VERSION_IDS_MAX} entries.")
            )
        )
    version_ids: list[UUID] = []
    for index, item in enumerate(raw_ids):
        if not isinstance(item, str) or not item.strip():
            raise ToolArgumentError(
                error=invalid_arguments(
                    detail=f"version_ids[{index}] must be a non-empty string."
                )
            )
        try:
            version_ids.append(UUID(item))
        except ValueError as error:
            raise ToolArgumentError(
                error=invalid_arguments(
                    detail=f"version_ids[{index}] is not a valid UUID: {item!r}."
                )
            ) from error
    raw_require = arguments.get("require")
    try:
        require = ReadinessRequirements.model_validate(raw_require)
    except ValidationError as error:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=(
                    "require must contain exactly the Boolean keys pipeline, p1,"
                    " live_graph, and p3."
                )
            )
        ) from error
    return tuple(version_ids), require


def _parse_versioning_mode(value: object) -> Literal["snapshot", "living"]:
    """Default snapshot; reject unknown modes."""
    if value is None:
        return "snapshot"
    if value in ("snapshot", "living"):
        return value  # type: ignore[return-value]
    raise ToolArgumentError(
        error=invalid_arguments(
            detail="versioning_mode must be 'snapshot' or 'living'."
        )
    )


def _parse_source_modified_at(value: object) -> datetime | None:
    """Parse optional ISO-8601 UTC timestamp."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError(
            error=invalid_arguments(
                detail="source_modified_at must be an ISO-8601 timestamp string."
            )
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail="source_modified_at must be a valid ISO-8601 timestamp."
            )
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ToolArgumentError(
            error=invalid_arguments(
                detail="source_modified_at must be timezone-aware UTC."
            )
        )
    return parsed.astimezone(timezone.utc)


def _ingest_success_payload(*, ingested: IngestedVersion) -> dict[str, object]:
    """IngestedVersion JSON plus async pipeline guidance for the agent."""
    version_id = str(ingested.version_id)
    identity: dict[str, object] = {
        "deployment_id": str(ingested.deployment_id),
        "doc_id": str(ingested.doc_id),
        "version_id": version_id,
        "content_hash": ingested.content_hash,
        "created": ingested.created,
        "parked": ingested.parked,
    }
    if ingested.parked == "no_route":
        return {
            **identity,
            "pipeline": {
                "status": "parked_no_route",
                "guidance": (
                    "The original is stored, but its conversion is parked"
                    " waiting for a conversion route for its MIME type; until"
                    " it is released it is not converted, searched or"
                    " extracted. Do not poll pipeline_readiness. Tell the user"
                    " now: an operator adds a route for this type if needed"
                    " (for example an OCR route for PDFs and images), then runs"
                    " `remember ops resume-no-route`. Report the version_id."
                ),
            },
        }
    if ingested.created:
        guidance = (
            "Ingest accepted. Wait until pipeline_readiness.ready is true before"
            " treating this content as recallable. Require pipeline, p1, and"
            " live_graph; set p3=false unless CorpusFS publication is required."
            " Poll algorithm: wait ~30s, then poll every 30–60s with mild back-off"
            " (floor ~15s). STOP immediately if any stages[].status is dead_letter"
            " and report version_id and that stage. A failed stage is retrying"
            " (a retry is scheduled): keep polling. After ~20–30 minutes without"
            " ready=true, stop and escalate to the operator with version_id and"
            " last stages[]."
        )
    else:
        guidance = (
            "Ingest was a content-hash no-op (created=false): this version already"
            " exists and no new pipeline run was started. An earlier run of the"
            " same bytes may still be processing. Call pipeline_readiness with"
            " pipeline/p1/live_graph required and p3=false: ready=true means the"
            " content is already recallable; otherwise keep polling it with the"
            " same algorithm as a new ingest (every 30–60s, floor ~15s, escalate"
            " after ~20–30 minutes). STOP and report if any stage is dead_letter;"
            " a failed stage is retrying."
        )
    return {
        **identity,
        "pipeline": {
            "status": "accepted_not_ready",
            "next_tool": PIPELINE_READINESS_TOOL_NAME,
            "poll_with": {
                "version_ids": [version_id],
                "require": {
                    "pipeline": True,
                    "p1": True,
                    "live_graph": True,
                    "p3": False,
                },
            },
            "guidance": guidance,
        },
    }


def reject_unknown_keys(*, arguments: Mapping[str, object], allowed: set[str]) -> None:
    """Fail closed on unexpected keys (matches open-query strictness)."""
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=f"Unknown argument keys: {', '.join(unknown)}."
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
        raise ToolArgumentError(
            error=invalid_arguments(detail=f"{key} must be a string.")
        )
    if not value:
        raise ToolArgumentError(
            error=invalid_arguments(detail=f"{key} must be non-empty when set.")
        )
    if max_length is not None and len(value) > max_length:
        raise ToolArgumentError(
            error=invalid_arguments(
                detail=f"{key} must be at most {max_length} characters."
            )
        )
    return value


def _optional_string(arguments: Mapping[str, object], *, key: str) -> str | None:
    """Read an optional string that may be empty (text body can still be empty)."""
    if key not in arguments or arguments[key] is None:
        return None
    value = arguments[key]
    if not isinstance(value, str):
        raise ToolArgumentError(
            error=invalid_arguments(detail=f"{key} must be a string.")
        )
    return value
