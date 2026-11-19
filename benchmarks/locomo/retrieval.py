"""Complete public-read catalog and bounded P3 adapter for LoCoMo v17."""

from __future__ import annotations

from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
import copy
from dataclasses import asdict
from datetime import datetime
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import stat
from typing import cast
from typing import Final
from typing import Literal
from uuid import UUID

from pydantic import JsonValue
from pydantic import ValidationError

from benchmarks.locomo.corpus import CorpusPublication
from remember.client import MemoryClient
from remember.mcp_tools import OPEN_QUERY_TOOL_NAMES
from remember.mcp_tools import tool
from remember.models import ADJACENT_CHUNKS_MAX_WINDOW
from remember.models import ADJACENT_CHUNKS_MIN_WINDOW
from remember.models import ContextBundleV2
from remember.models import Envelope
from rememberstack.model import ToolDescriptor
from rememberstack.surfaces.query_sandbox.errors import QueryErrorCode
from rememberstack.surfaces.query_sandbox.result import QueryResult

PRIMITIVE_TOOL_NAMES: Final = (
    "resolve",
    "lookup_relations",
    "transcript_relation",
    "lookup_observations",
    "search_claims",
    "search_chunks",
    "adjacent_chunks",
    "hydrate_relation",
)
P3_TOOL_NAMES: Final = ("p3_list", "p3_search", "p3_read")
QUERY_RESULT_TOOL_NAMES: Final = frozenset(
    {"query_sql", "explain_sql", "run_saved_query"}
)
P3_LIST_LIMIT: Final = 200
P3_LIST_MAX_VISITED: Final = 2_000
P3_SEARCH_DEFAULT_RESULTS: Final = 20
P3_SEARCH_MAX_RESULTS: Final = 50
P3_SEARCH_MAX_FILES: Final = 2_000
P3_SEARCH_MAX_ENTRIES: Final = 10_000
P3_SEARCH_MAX_BYTES: Final = 8 * 1024 * 1024
P3_READ_DEFAULT_LINES: Final = 200
P3_READ_MAX_LINES: Final = 400
P3_READ_MAX_BYTES: Final = 256 * 1024
CORRECTABLE_QUERY_ERROR_CODES: Final = frozenset(
    {
        QueryErrorCode.PARSE_ERROR,
        QueryErrorCode.MULTIPLE_STATEMENTS,
        QueryErrorCode.STATEMENT_NOT_ALLOWED,
        QueryErrorCode.RELATION_NOT_ALLOWED,
        QueryErrorCode.FUNCTION_NOT_ALLOWED,
        QueryErrorCode.FUNCTION_PLACEMENT_NOT_ALLOWED,
        QueryErrorCode.OPERATOR_NOT_ALLOWED,
        QueryErrorCode.INVALID_PARAMETER,
        QueryErrorCode.UNBOUNDED_RECURSION,
        QueryErrorCode.SAVED_QUERY_NOT_FOUND,
        QueryErrorCode.SAVED_QUERY_DISABLED,
        QueryErrorCode.SAVED_QUERY_INCOMPATIBLE,
        QueryErrorCode.SAVED_QUERY_REVALIDATION_PENDING,
    }
)


class RetrievalToolError(ValueError):
    """One answer-tool call has caller-correctable arguments or target path."""


class RetrievalInfrastructureError(RuntimeError):
    """The public retrieval contract or its serving corpus failed."""


class _P3ReadLimitExceeded(RuntimeError):
    """One pinned regular file exceeds the caller's byte budget."""


def assured_tool_catalog() -> tuple[ToolDescriptor, ...]:
    """Return the exact four canonical assured-operation descriptors."""
    from rememberstack.spine.assured_operations import CANONICAL_OPERATIONS
    from rememberstack.surfaces.operation_surface import operation_descriptors

    return operation_descriptors(
        operations=tuple(
            sorted(CANONICAL_OPERATIONS, key=lambda operation: operation.name.value)
        )
    )


def answer_tool_catalog() -> tuple[ToolDescriptor, ...]:
    """Return the exact 22-tool read catalog exposed to the v17 answer seat."""
    tools = (
        *assured_tool_catalog(),
        *_primitive_tool_descriptors(),
        *_open_query_descriptors(),
        *_p3_tool_descriptors(),
    )
    names = tuple(tool.name for tool in tools)
    if len(names) != len(set(names)):
        raise RuntimeError("the LoCoMo answer-tool catalog contains duplicate names")
    return tools


def p3_tool_catalog() -> tuple[ToolDescriptor, ...]:
    """Return the benchmark's three bounded P3 read descriptors."""
    return _p3_tool_descriptors()


def tool_catalog_sha256() -> str:
    """Hash the canonical JSON representation of the complete answer catalog."""
    payload = [tool.model_dump(mode="json") for tool in answer_tool_catalog()]
    canonical = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def is_correctable_query_error(*, code: str | None) -> bool:
    """Return whether one public query error is a caller-correctable plan error."""
    if code is None:
        return False
    try:
        typed = QueryErrorCode(code)
    except ValueError:
        return False
    return typed in CORRECTABLE_QUERY_ERROR_CODES


def query_result_failure(
    response: Envelope | ContextBundleV2 | JsonValue,
) -> tuple[str, str] | None:
    """Extract a typed failure from an HTTP-200 QueryResult payload."""
    if not isinstance(response, dict) or response.get("contract") != "QueryResult/v1":
        return None
    code = response.get("error_code")
    if code is None:
        if response.get("termination_reason") != "completed":
            return (
                "invalid_query_result",
                "query result failed without a public error code",
            )
        return None
    if not isinstance(code, str):
        return "invalid_query_result", "query result carried a non-string error code"
    message = response.get("error_message")
    return code, message if isinstance(message, str) else code


def dispatch_answer_tool(
    *,
    client: MemoryClient,
    p3: P3Mount | None,
    name: str,
    arguments: Mapping[str, object],
) -> Envelope | ContextBundleV2 | JsonValue:
    """Dispatch one catalogued read through the public SDK or P3 mount."""
    if name in {tool.name for tool in assured_tool_catalog()}:
        operation_arguments = dict(arguments)
        if operation_arguments.get("entity_ids") == []:
            # Entity scope is optional. Treat the model's empty optional array as
            # the same unscoped call as omission before public schema validation.
            del operation_arguments["entity_ids"]
        return client.run_operation(name=name, arguments=operation_arguments)
    if name in PRIMITIVE_TOOL_NAMES:
        return _dispatch_primitive(client=client, name=name, arguments=arguments)
    if name in OPEN_QUERY_TOOL_NAMES:
        result = client.call_open_query(name=name, arguments=arguments)
        if isinstance(result, dict):
            if name in QUERY_RESULT_TOOL_NAMES:
                try:
                    QueryResult.model_validate(result, strict=False)
                except (ValidationError, TypeError) as error:
                    raise RetrievalInfrastructureError(
                        f"open-query tool {name!r} returned an invalid QueryResult/v1"
                    ) from error
            return cast(JsonValue, result)
        if isinstance(result, list) and all(isinstance(item, dict) for item in result):
            return cast(JsonValue, result)
        raise RetrievalInfrastructureError(
            f"open-query tool {name!r} returned invalid JSON"
        )
    if name in P3_TOOL_NAMES:
        if p3 is None:
            raise RetrievalInfrastructureError("the P3 mount is not available")
        return cast(JsonValue, p3.call(name=name, arguments=arguments))
    raise RetrievalToolError(f"unknown retrieval tool {name!r}")


class P3Mount:
    """Bounded reads over attested live folders with a frozen byte inventory."""

    def __init__(
        self, *, root: Path, publications: tuple[CorpusPublication, ...]
    ) -> None:
        """Match ready-folder identities to exact per-version publications."""
        self._root_fd: int | None = None
        if not publications or len({p.doc_id for p in publications}) != len(
            publications
        ):
            raise RetrievalInfrastructureError(
                "P3 requires distinct exact publications"
            )
        self._publications = publications
        try:
            if not root.exists() or not root.is_dir():
                raise RetrievalInfrastructureError(
                    "P3 root is not a readable directory"
                )
            self._root = root.resolve(strict=True)
        except (OSError, ValueError) as error:
            raise RetrievalInfrastructureError(
                "P3 root is not a readable directory"
            ) from error
        try:
            self._root_fd = os.open(
                self._root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
        except OSError as error:
            raise RetrievalInfrastructureError(
                "P3 root is not a readable directory"
            ) from error
        try:
            self._attest_folders()
            self._inventory = self._freeze_inventory()
        except (
            RetrievalToolError,
            RetrievalInfrastructureError,
            _P3ReadLimitExceeded,
            ValueError,
            KeyError,
            TypeError,
        ) as error:
            self.close()
            raise RetrievalInfrastructureError(
                "P3 ready folder or inventory differs from readiness"
            ) from error
        self._inventory_sha256 = hashlib.sha256(
            json.dumps(
                {
                    "publications": [asdict(p) for p in publications],
                    "files": self._inventory,
                },
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()

    def _attest_folders(self) -> None:
        """Verify ready state, exact identity and every reader-file checksum."""
        for publication in self._publications:
            parts = PurePosixPath(publication.path).parts
            first_folder = next(
                (
                    index
                    for index, part in enumerate(parts)
                    if re.fullmatch(r".+--[0-9a-f]{24}", part)
                ),
                None,
            )
            suffix = hashlib.sha256(publication.doc_id.bytes).hexdigest()[:24]
            if (
                not parts
                or first_folder != len(parts) - 1
                or not parts[-1].endswith("--" + suffix)
            ):
                raise ValueError("P3 path does not identify the document's own folder")
            folder = self._resolve(path=publication.path, expected_kind="directory")
            metadata, _ = self._read_pinned_file(
                path=folder / "document.json", max_bytes=P3_READ_MAX_BYTES
            )
            index, _ = self._read_pinned_file(
                path=folder / "_index.md", max_bytes=P3_READ_MAX_BYTES
            )
            record = json.loads(metadata)
            expected = {
                "doc_id": str(publication.doc_id),
                "version_id": str(publication.version_id),
                "representation_id": str(publication.representation_id),
                "export_revision": publication.revision,
            }
            if (
                any(record[key] != value for key, value in expected.items())
                or "State: ready" not in index.splitlines()
                or f"Export revision: {publication.revision}" not in index.splitlines()
            ):
                raise ValueError("P3 folder identity mismatch")
            for file in record["files"]:
                name = file["path"]
                if (
                    not isinstance(name, str)
                    or PurePosixPath(name).is_absolute()
                    or ".." in PurePosixPath(name).parts
                ):
                    raise ValueError("unsafe reader inventory path")
                target = self._resolve(
                    path=f"{publication.path}/{name}", expected_kind="file"
                )
                if self._hash_file(path=target) != file["sha256"]:
                    raise ValueError("P3 reader checksum mismatch")

    def _freeze_inventory(self) -> dict[str, str]:
        files, truncated, _ = self._bounded_files(origin=self._root)
        if truncated:
            raise ValueError("P3 inventory exceeds bounded traversal")
        return {
            self._lexical_relative(path=path): self._hash_file(path=path)
            for path in files
        }

    def verify_inventory(self) -> None:
        """Fail if a live tree changed since this benchmark's frozen reading."""
        try:
            self._attest_folders()
            if self._freeze_inventory() != self._inventory:
                raise ValueError("P3 inventory changed")
        except (ValueError, KeyError, TypeError, _P3ReadLimitExceeded) as error:
            raise RetrievalInfrastructureError("P3 frozen inventory changed") from error

    @property
    def inventory(self) -> dict[str, str]:
        """The frozen file hashes retained as run evidence."""
        return dict(self._inventory)

    def close(self) -> None:
        """Best-effort release of the descriptor that pins this corpus root."""
        root_fd = self._root_fd
        self._root_fd = None
        if root_fd is not None:
            try:
                os.close(root_fd)
            except OSError:
                pass

    def __del__(self) -> None:
        """Best-effort fallback for callers that do not close the mount explicitly."""
        self.close()

    @property
    def inventory_sha256(self) -> str:
        """Identity of this run's publications and frozen local inventory."""
        return self._inventory_sha256

    @property
    def root(self) -> Path:
        """Return the validated corpus root for an audited native reader."""
        self._resolve(path=".", expected_kind="directory")
        return self._root

    def call(self, *, name: str, arguments: Mapping[str, object]) -> dict[str, object]:
        """Validate and execute one of the three P3 filesystem motions."""
        if name == "p3_list":
            _require_keys(arguments=arguments, allowed={"path"}, required=set())
            return self._list(
                path=_optional_string(arguments=arguments, field="path", default=".")
            )
        if name == "p3_search":
            _require_keys(
                arguments=arguments,
                allowed={"query", "path", "max_results"},
                required={"query"},
            )
            return self._search(
                query=_required_string(arguments.get("query"), field="query"),
                path=_optional_string(arguments=arguments, field="path", default="."),
                max_results=_optional_bounded_int(
                    arguments=arguments,
                    field="max_results",
                    default=P3_SEARCH_DEFAULT_RESULTS,
                    minimum=1,
                    maximum=P3_SEARCH_MAX_RESULTS,
                ),
            )
        if name == "p3_read":
            _require_keys(
                arguments=arguments,
                allowed={"path", "start_line", "max_lines"},
                required={"path"},
            )
            return self._read(
                path=_required_string(arguments.get("path"), field="path"),
                start_line=_optional_bounded_int(
                    arguments=arguments,
                    field="start_line",
                    default=1,
                    minimum=1,
                    maximum=10_000_000,
                ),
                max_lines=_optional_bounded_int(
                    arguments=arguments,
                    field="max_lines",
                    default=P3_READ_DEFAULT_LINES,
                    minimum=1,
                    maximum=P3_READ_MAX_LINES,
                ),
            )
        raise RetrievalToolError(f"unknown P3 tool {name!r}")

    def _list(self, *, path: str) -> dict[str, object]:
        """List one directory with a fixed entry cap."""
        directory = self._resolve(path=path, expected_kind="directory")
        self._assert_corpus_kind(path=directory, expected_kind="directory")
        children: list[Path] = []
        visited = 0
        try:
            for child in islice(directory.iterdir(), P3_LIST_MAX_VISITED):
                visited += 1
                if not child.name.startswith("."):
                    children.append(child)
        except OSError as error:
            raise RetrievalInfrastructureError("P3 directory is unreadable") from error
        visit_truncated = visited >= P3_LIST_MAX_VISITED
        children.sort(key=lambda child: child.name)
        visible = children[:P3_LIST_LIMIT]
        return {
            "inventory_sha256": self._inventory_sha256,
            "path": self._relative(path=directory),
            "entries": [
                {
                    "name": child.name,
                    "path": self._relative(path=child),
                    "type": "directory" if child.is_dir() else "file",
                }
                for child in visible
            ],
            "visited_entries": visited,
            "truncated": visit_truncated or len(children) > len(visible),
        }

    def _search(self, *, query: str, path: str, max_results: int) -> dict[str, object]:
        """Literal-casefold grep over bounded UTF-8 P3 files."""
        origin = self._resolve(path=path, expected_kind="either")
        candidates, walk_truncated, visited_entries = self._bounded_files(origin=origin)
        needle = query.casefold()
        matches: list[dict[str, object]] = []
        scanned_files = 0
        scanned_bytes = 0
        truncated = walk_truncated
        for candidate in candidates:
            if candidate.name.startswith("."):
                continue
            relative = self._lexical_relative(path=candidate)
            remaining_bytes = P3_SEARCH_MAX_BYTES - scanned_bytes
            try:
                if scanned_files >= P3_SEARCH_MAX_FILES:
                    raise _P3ReadLimitExceeded
                content, size = self._read_pinned_file(
                    path=candidate, max_bytes=remaining_bytes
                )
            except _P3ReadLimitExceeded:
                truncated = True
                break
            scanned_files += 1
            scanned_bytes += size
            if needle in relative.casefold():
                matches.append(
                    {"path": relative, "line_number": None, "text": "path match"}
                )
            for line_number, line in enumerate(content.splitlines(), start=1):
                if needle in line.casefold():
                    matches.append(
                        {"path": relative, "line_number": line_number, "text": line}
                    )
                if len(matches) >= max_results:
                    truncated = True
                    break
            if len(matches) >= max_results:
                break
        return {
            "inventory_sha256": self._inventory_sha256,
            "path": self._relative(path=origin),
            "query": query,
            "matches": matches[:max_results],
            "scanned_files": scanned_files,
            "scanned_bytes": scanned_bytes,
            "visited_entries": visited_entries,
            "truncated": truncated,
        }

    def _bounded_files(self, *, origin: Path) -> tuple[list[Path], bool, int]:
        """Walk at most the pinned entry cap without following symlinks."""
        origin_kind = self._corpus_kind(path=origin)
        if origin_kind == "file":
            return [origin], False, 1
        if origin_kind != "directory":
            raise RetrievalInfrastructureError(
                "P3 corpus entry changed type during traversal"
            )
        pending = [origin]
        files: list[Path] = []
        visited = 0
        while pending:
            candidate = pending.pop()
            candidate_kind = self._corpus_kind(path=candidate)
            if candidate_kind == "file":
                files.append(candidate)
                continue
            if candidate_kind != "directory":
                raise RetrievalInfrastructureError(
                    "P3 corpus entry changed type during traversal"
                )
            if visited >= P3_SEARCH_MAX_ENTRIES:
                return files, True, visited
            children: list[Path] = []
            remaining = P3_SEARCH_MAX_ENTRIES - visited
            try:
                for child in islice(candidate.iterdir(), remaining):
                    visited += 1
                    if not child.name.startswith("."):
                        children.append(child)
            except OSError as error:
                raise RetrievalInfrastructureError(
                    "P3 directory is unreadable"
                ) from error
            pending.extend(
                reversed(sorted(children, key=lambda child: child.as_posix()))
            )
        return files, visited >= P3_SEARCH_MAX_ENTRIES, visited

    def _read(self, *, path: str, start_line: int, max_lines: int) -> dict[str, object]:
        """Read a bounded line window from one UTF-8 P3 file."""
        target = self._resolve(path=path, expected_kind="file")
        try:
            content, _ = self._read_pinned_file(
                path=target, max_bytes=P3_READ_MAX_BYTES
            )
        except _P3ReadLimitExceeded as error:
            raise RetrievalToolError(
                f"P3 file exceeds the {P3_READ_MAX_BYTES}-byte read limit"
            ) from error
        lines = content.splitlines()
        start = min(start_line - 1, len(lines))
        selected = lines[start : start + max_lines]
        return {
            "inventory_sha256": self._inventory_sha256,
            "path": self._lexical_relative(path=target),
            "start_line": start_line,
            "end_line": start + len(selected),
            "total_lines": len(lines),
            "content": "\n".join(selected),
            "truncated": start + len(selected) < len(lines),
        }

    def _resolve(
        self,
        *,
        path: str,
        expected_kind: Literal["file", "directory", "either"] = "either",
    ) -> Path:
        """Resolve one relative path while rejecting traversal and symlinks."""
        if "\x00" in path:
            raise RetrievalToolError("P3 path contains an invalid character")
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RetrievalToolError("P3 paths must stay relative to the mount root")
        current = self._root
        parts = tuple(part for part in relative.parts if part not in {"", "."})
        root_fd = self._root_fd
        if root_fd is None:
            raise RetrievalInfrastructureError("P3 mount is closed")
        try:
            root_metadata = os.fstat(root_fd)
            current_root = self._root.lstat()
            if not stat.S_ISDIR(current_root.st_mode) or (
                current_root.st_dev,
                current_root.st_ino,
            ) != (root_metadata.st_dev, root_metadata.st_ino):
                raise RetrievalInfrastructureError(
                    "P3 corpus root changed during access"
                )
        except OSError as error:
            raise RetrievalInfrastructureError(
                "P3 corpus root is unreadable"
            ) from error
        parent = self._root
        parent_identity = (root_metadata.st_dev, root_metadata.st_ino)
        for index, part in enumerate(parts):
            current = current / part
            try:
                metadata = current.lstat()
                if stat.S_ISLNK(metadata.st_mode):
                    raise RetrievalToolError("P3 paths may not traverse symlinks")
                if index < len(parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                    raise RetrievalToolError(
                        "P3 path contains a non-directory component"
                    )
            except FileNotFoundError as error:
                try:
                    current_parent = parent.lstat()
                except OSError as parent_error:
                    raise RetrievalInfrastructureError(
                        "P3 corpus path changed during inspection"
                    ) from parent_error
                if (
                    not stat.S_ISDIR(current_parent.st_mode)
                    or (current_parent.st_dev, current_parent.st_ino) != parent_identity
                ):
                    raise RetrievalInfrastructureError(
                        "P3 corpus path changed during inspection"
                    ) from error
                raise RetrievalToolError("P3 path does not exist") from error
            except OSError as error:
                raise RetrievalInfrastructureError(
                    "P3 corpus path could not be inspected"
                ) from error
            if index < len(parts) - 1:
                parent = current
                parent_identity = (metadata.st_dev, metadata.st_ino)
        try:
            resolved = current.resolve(strict=True)
        except (OSError, ValueError) as error:
            raise RetrievalInfrastructureError(
                "P3 corpus path changed during inspection"
            ) from error
        if not resolved.is_relative_to(self._root):
            raise RetrievalInfrastructureError("P3 corpus path escaped the mount root")
        try:
            resolved_kind = self._corpus_kind(path=resolved)
        except RetrievalInfrastructureError as error:
            raise RetrievalInfrastructureError(
                "P3 corpus path disappeared during inspection"
            ) from error
        if expected_kind != "either" and resolved_kind != expected_kind:
            raise RetrievalToolError(f"P3 path is not a {expected_kind}")
        return resolved

    def _corpus_kind(self, *, path: Path) -> Literal["file", "directory", "other"]:
        """Read one corpus entry type without suppressing filesystem failures."""
        try:
            if path.is_symlink():
                raise RetrievalInfrastructureError(
                    "P3 corpus contains an unexpected symlink"
                )
            mode = path.stat().st_mode
        except OSError as error:
            raise RetrievalInfrastructureError(
                "P3 corpus entry disappeared during inspection"
            ) from error
        if stat.S_ISREG(mode):
            return "file"
        if stat.S_ISDIR(mode):
            return "directory"
        return "other"

    def _assert_corpus_kind(
        self, *, path: Path, expected_kind: Literal["file", "directory"]
    ) -> Path:
        """Require an already-resolved corpus entry to retain its type."""
        if self._corpus_kind(path=path) != expected_kind:
            raise RetrievalInfrastructureError(
                "P3 corpus entry changed type during access"
            )
        return path

    @contextmanager
    def _open_pinned_file(self, *, path: Path) -> Iterator[int]:
        """Open relative to the pinned root, refusing symlinks at every level."""
        parts = PurePosixPath(self._lexical_relative(path=path)).parts
        if self._root_fd is None:
            raise RetrievalInfrastructureError("P3 mount is closed")
        owned: set[int] = set()
        try:
            directory_fd = os.dup(self._root_fd)
            owned.add(directory_fd)
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=directory_fd,
                )
                owned.add(next_fd)
                os.close(directory_fd)
                owned.remove(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory_fd,
            )
            owned.add(file_fd)
            if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                raise RetrievalInfrastructureError(
                    "P3 corpus entry changed type during access"
                )
            yield file_fd
        except (OSError, ValueError) as error:
            raise RetrievalInfrastructureError("P3 file is unreadable") from error
        finally:
            for descriptor in owned:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def _hash_file(self, *, path: Path) -> str:
        """Hash binary originals/media in bounded memory through the same guard."""
        digest = hashlib.sha256()
        with self._open_pinned_file(path=path) as descriptor:
            while block := os.read(descriptor, 1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    def _read_pinned_file(self, *, path: Path, max_bytes: int) -> tuple[str, int]:
        """Read bounded text and verify frozen bytes from the same descriptor."""
        with self._open_pinned_file(path=path) as descriptor:
            if os.fstat(descriptor).st_size > max_bytes:
                raise _P3ReadLimitExceeded
            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                block = os.read(descriptor, min(remaining, 64 * 1024))
                if not block:
                    break
                chunks.append(block)
                remaining -= len(block)
            raw = b"".join(chunks)
        if len(raw) > max_bytes:
            raise _P3ReadLimitExceeded
        frozen = getattr(self, "_inventory", None)
        if (
            frozen is not None
            and frozen.get(self._lexical_relative(path=path))
            != hashlib.sha256(raw).hexdigest()
        ):
            raise RetrievalInfrastructureError("P3 frozen inventory changed")
        try:
            return raw.decode("utf-8"), len(raw)
        except UnicodeError as error:
            raise RetrievalInfrastructureError("P3 file is unreadable") from error

    def _lexical_relative(self, *, path: Path) -> str:
        """Render an inspected path without another filesystem lookup."""
        try:
            relative = path.relative_to(self._root).as_posix()
        except ValueError as error:
            raise RetrievalInfrastructureError(
                "P3 corpus path escaped its root"
            ) from error
        if relative in {"", "."}:
            raise RetrievalInfrastructureError("P3 path does not identify a file")
        return relative

    def _relative(self, *, path: Path) -> str:
        """Render one resolved path relative to the pinned corpus root."""
        try:
            relative = path.resolve(strict=True).relative_to(self._root).as_posix()
        except (OSError, ValueError) as error:
            raise RetrievalInfrastructureError(
                "P3 corpus path escaped its root"
            ) from error
        return relative or "."


def _dispatch_primitive(
    *, client: MemoryClient, name: str, arguments: Mapping[str, object]
) -> Envelope:
    """Validate and dispatch one direct public primitive SDK operation."""
    if name == "resolve":
        _require_keys(
            arguments=arguments,
            allowed={"name", "context_entity_ids"},
            required={"name"},
        )
        raw_context = arguments.get("context_entity_ids", [])
        if not isinstance(raw_context, list) or len(raw_context) > 8:
            raise RetrievalToolError(
                "context_entity_ids must be an array of at most 8 UUIDs"
            )
        return client.resolve(
            name=_required_string(arguments.get("name"), field="name"),
            context_entity_ids=tuple(_uuid(value=value) for value in raw_context),
        )
    if name == "lookup_relations":
        _require_keys(
            arguments=arguments,
            allowed={"subject_entity_id", "predicate", "object_entity_id", "valid_at"},
            required=set(),
        )
        return client.lookup_relations(
            subject_entity_id=_optional_uuid(
                arguments=arguments, field="subject_entity_id"
            ),
            predicate=_optional_nullable_string(arguments=arguments, field="predicate"),
            object_entity_id=_optional_uuid(
                arguments=arguments, field="object_entity_id"
            ),
            valid_at=_optional_datetime(arguments=arguments, field="valid_at"),
        )
    if name == "transcript_relation":
        _require_keys(
            arguments=arguments, allowed={"relation_id"}, required={"relation_id"}
        )
        return client.transcript_relation(
            relation_id=_uuid(value=arguments.get("relation_id"))
        )
    if name == "lookup_observations":
        _require_keys(
            arguments=arguments,
            allowed={"entity_id", "property_query", "k"},
            required={"entity_id"},
        )
        return client.lookup_observations(
            entity_id=_uuid(value=arguments.get("entity_id")),
            property_query=_optional_nullable_string(
                arguments=arguments, field="property_query"
            ),
            k=_optional_bounded_int(
                arguments=arguments, field="k", default=10, minimum=1, maximum=400
            ),
        )
    if name in {"search_claims", "search_chunks"}:
        _require_keys(
            arguments=arguments, allowed={"query", "k", "channel"}, required={"query"}
        )
        query = _required_string(arguments.get("query"), field="query")
        k = _optional_bounded_int(
            arguments=arguments, field="k", default=10, minimum=1, maximum=400
        )
        channel = arguments.get("channel", "semantic")
        if channel not in {"semantic", "bm25"}:
            raise RetrievalToolError("channel must be 'semantic' or 'bm25'")
        typed_channel = cast(Literal["semantic", "bm25"], channel)
        if name == "search_claims":
            return client.search_claims(query=query, k=k, channel=typed_channel)
        return client.search_chunks(query=query, k=k, channel=typed_channel)
    if name == "adjacent_chunks":
        _require_keys(
            arguments=arguments, allowed={"chunk_id", "window"}, required={"chunk_id"}
        )
        return client.adjacent_chunks(
            chunk_id=_uuid(value=arguments.get("chunk_id")),
            window=_optional_bounded_int(
                arguments=arguments,
                field="window",
                default=1,
                minimum=ADJACENT_CHUNKS_MIN_WINDOW,
                maximum=ADJACENT_CHUNKS_MAX_WINDOW,
            ),
        )
    if name == "hydrate_relation":
        _require_keys(
            arguments=arguments, allowed={"relation_id"}, required={"relation_id"}
        )
        return client.hydrate_relation(
            relation_id=_uuid(value=arguments.get("relation_id"))
        )
    raise RetrievalToolError(f"unknown primitive tool {name!r}")


def _primitive_tool_descriptors() -> tuple[ToolDescriptor, ...]:
    """Describe the exact eight public direct primitive endpoints."""
    uuid = {"type": "string", "format": "uuid"}
    return (
        _descriptor(
            name="resolve",
            description="Resolve a name to ranked current entity candidates; ambiguity is returned, never guessed away.",
            properties={
                "name": {"type": "string"},
                "context_entity_ids": {"type": "array", "items": uuid, "maxItems": 8},
            },
            required=("name",),
            output_grain="entity",
            answer_intent="entity_resolution",
        ),
        _descriptor(
            name="lookup_relations",
            description="Read current relations matching an optional subject/predicate/object pattern, or relations valid at one time.",
            properties={
                "subject_entity_id": uuid,
                "predicate": {"type": "string"},
                "object_entity_id": uuid,
                "valid_at": {"type": "string", "format": "date-time"},
            },
            required=(),
            output_grain="fact",
            answer_intent="current_facts",
        ),
        _descriptor(
            name="transcript_relation",
            description="Read the bounded decision history explaining why one relation is or was believed.",
            properties={"relation_id": uuid},
            required=("relation_id",),
            output_grain="composite",
            answer_intent="audit_history",
        ),
        _descriptor(
            name="lookup_observations",
            description="Read live observations for one entity, optionally filtered by semantic property text.",
            properties={
                "entity_id": uuid,
                "property_query": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 400, "default": 10},
            },
            required=("entity_id",),
            output_grain="fact",
            answer_intent="current_facts",
        ),
        _descriptor(
            name="search_claims",
            description="Search current source testimony through one independent semantic or BM25 P1 channel.",
            properties=_search_properties(),
            required=("query",),
            output_grain="evidence",
            answer_intent="assertion_history",
        ),
        _descriptor(
            name="search_chunks",
            description="Search live source passages through one independent semantic or BM25 P1 channel.",
            properties=_search_properties(),
            required=("query",),
            output_grain="evidence",
            answer_intent="source_context",
        ),
        _descriptor(
            name="adjacent_chunks",
            description="Read the surrounding chunks for one chunk ordered by document sequence.",
            properties={
                "chunk_id": uuid,
                "window": {
                    "type": "integer",
                    "minimum": ADJACENT_CHUNKS_MIN_WINDOW,
                    "maximum": ADJACENT_CHUNKS_MAX_WINDOW,
                    "default": 1,
                },
            },
            required=("chunk_id",),
            output_grain="evidence",
            answer_intent="source_context",
        ),
        _descriptor(
            name="hydrate_relation",
            description="Hydrate one relation through its evidence claims to source documents.",
            properties={"relation_id": uuid},
            required=("relation_id",),
            output_grain="composite",
            answer_intent="fact_provenance",
        ),
    )


def _open_query_descriptors() -> tuple[ToolDescriptor, ...]:
    """Adapt the catalogue's open-query tools without duplicating their schemas."""
    discovery = {
        "describe_query_space",
        "search_query_space",
        "list_saved_queries",
        "describe_saved_query",
    }
    return tuple(
        ToolDescriptor(
            name=definition.name,
            description=definition.description,
            input_schema=copy.deepcopy(definition.input_schema),
            result_schema={"type": "object"},
            result_contract="QueryResult/v1-or-discovery",
            output_grain=(
                "discovery" if definition.name in discovery else "exploratory_tabular"
            ),
            answer_intent="query_infrastructure",
        )
        for definition in (tool(name) for name in OPEN_QUERY_TOOL_NAMES)
    )


def _p3_tool_descriptors() -> tuple[ToolDescriptor, ...]:
    """Describe bounded filesystem motions over the ordinary P3 mount."""
    return (
        _descriptor(
            name="p3_list",
            description=f"List one P3 corpus directory (scan at most {P3_LIST_MAX_VISITED} entries; return at most {P3_LIST_LIMIT}) for filesystem orientation.",
            properties={"path": {"type": "string", "default": "."}},
            required=(),
            output_grain="corpus_files",
            answer_intent="filesystem_navigation",
            runtime_limits={
                "visited_entries": P3_LIST_MAX_VISITED,
                "returned_entries": P3_LIST_LIMIT,
            },
        ),
        _descriptor(
            name="p3_search",
            description="Literal case-insensitive grep over the live P3 corpus; returns bounded matching paths and lines.",
            properties={
                "query": {"type": "string"},
                "path": {"type": "string", "default": "."},
                "max_results": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": P3_SEARCH_MAX_RESULTS,
                    "default": P3_SEARCH_DEFAULT_RESULTS,
                },
            },
            required=("query",),
            output_grain="corpus_files",
            answer_intent="filesystem_search",
            runtime_limits={
                "visited_entries": P3_SEARCH_MAX_ENTRIES,
                "scanned_files": P3_SEARCH_MAX_FILES,
                "scanned_bytes": P3_SEARCH_MAX_BYTES,
                "returned_matches": P3_SEARCH_MAX_RESULTS,
            },
        ),
        _descriptor(
            name="p3_read",
            description=f"Read a UTF-8 line window from one P3 file (at most {P3_READ_MAX_LINES} lines and {P3_READ_MAX_BYTES} bytes).",
            properties={
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1, "default": 1},
                "max_lines": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": P3_READ_MAX_LINES,
                    "default": P3_READ_DEFAULT_LINES,
                },
            },
            required=("path",),
            output_grain="corpus_files",
            answer_intent="filesystem_read",
            runtime_limits={
                "file_bytes": P3_READ_MAX_BYTES,
                "start_line": 10_000_000,
                "returned_lines": P3_READ_MAX_LINES,
            },
        ),
    )


def _descriptor(
    *,
    name: str,
    description: str,
    properties: dict[str, object],
    required: tuple[str, ...],
    output_grain: str,
    answer_intent: str,
    runtime_limits: dict[str, int] | None = None,
) -> ToolDescriptor:
    """Build one strict benchmark-facing descriptor."""
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
    if runtime_limits is not None:
        input_schema["x-runtime-limits"] = runtime_limits
    return ToolDescriptor(
        name=name,
        description=description,
        input_schema=input_schema,
        result_schema={"type": "object"},
        result_contract="envelope"
        if output_grain in {"fact", "evidence", "composite"}
        else "bounded_corpus_result",
        output_grain=output_grain,
        answer_intent=answer_intent,
    )


def _search_properties() -> dict[str, object]:
    """Return the shared direct claim/chunk search schema properties."""
    return {
        "query": {"type": "string"},
        "k": {"type": "integer", "minimum": 1, "maximum": 400, "default": 10},
        "channel": {
            "type": "string",
            "enum": ["semantic", "bm25"],
            "default": "semantic",
        },
    }


def _require_keys(
    *, arguments: Mapping[str, object], allowed: set[str], required: set[str]
) -> None:
    """Reject missing and unknown tool-argument keys."""
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise RetrievalToolError(f"unknown argument keys: {unknown}")
    missing = sorted(required - set(arguments))
    if missing:
        raise RetrievalToolError(f"missing required argument keys: {missing}")


def _required_string(value: object, *, field: str) -> str:
    """Require one non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise RetrievalToolError(f"{field} must be a non-empty string")
    return value


def _optional_string(
    *, arguments: Mapping[str, object], field: str, default: str
) -> str:
    """Read an optional non-empty string; only omission applies the default."""
    if field not in arguments:
        return default
    return _required_string(arguments[field], field=field)


def _optional_nullable_string(
    *, arguments: Mapping[str, object], field: str
) -> str | None:
    """Read an omitted optional string while rejecting explicit null."""
    if field not in arguments:
        return None
    return _required_string(arguments[field], field=field)


def _optional_bounded_int(
    *,
    arguments: Mapping[str, object],
    field: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Read an optional bounded integer; only omission applies the default."""
    if field not in arguments:
        return default
    value = arguments[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise RetrievalToolError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise RetrievalToolError(f"{field} must be in {minimum}..{maximum}")
    return value


def _uuid(*, value: object) -> UUID:
    """Parse one UUID string without coercing arbitrary values."""
    if not isinstance(value, str):
        raise RetrievalToolError("UUID arguments must be strings")
    try:
        return UUID(value)
    except ValueError as error:
        raise RetrievalToolError(f"invalid UUID: {value!r}") from error


def _optional_uuid(*, arguments: Mapping[str, object], field: str) -> UUID | None:
    """Parse one omitted optional UUID while rejecting explicit null."""
    if field not in arguments:
        return None
    return _uuid(value=arguments[field])


def _optional_datetime(
    *, arguments: Mapping[str, object], field: str
) -> datetime | None:
    """Parse one omitted optional timezone-aware ISO datetime."""
    if field not in arguments:
        return None
    value = arguments[field]
    if not isinstance(value, str):
        raise RetrievalToolError(f"{field} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RetrievalToolError(f"{field} must be an ISO datetime string") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RetrievalToolError(f"{field} must include a timezone offset")
    return parsed


__all__ = (
    "P3Mount",
    "P3_TOOL_NAMES",
    "PRIMITIVE_TOOL_NAMES",
    "RetrievalInfrastructureError",
    "RetrievalToolError",
    "answer_tool_catalog",
    "p3_tool_catalog",
    "assured_tool_catalog",
    "dispatch_answer_tool",
    "is_correctable_query_error",
    "query_result_failure",
    "tool_catalog_sha256",
)
