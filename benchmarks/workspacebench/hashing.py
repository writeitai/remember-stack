"""Streamed hashing, symlink-safe trees, and atomic JSON artifacts."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import os
from pathlib import Path
from typing import Final

from pydantic import BaseModel

from benchmarks.workspacebench.errors import WorkspaceBenchError

_CHUNK_SIZE: Final = 1024 * 1024


class WorkspacePathError(WorkspaceBenchError):
    """A path escaped its allowed root or used a forbidden symlink."""


def require_absolute_path(path: Path, *, label: str) -> Path:
    """Reject relative external paths. The adapter never infers dataset roots."""
    if not path.is_absolute():
        raise WorkspaceBenchError(f"{label} must be an absolute path: {path}")
    return path


# Immutable inputs. task-dir may nest under upstream (documented standard layout).
_SOURCE_ROOT_LABELS: Final = frozenset({"upstream", "task-dir", "workspace"})
_MUTABLE_ROOT_LABELS: Final = frozenset({"output"})
_ALLOWED_SOURCE_NESTING: Final = frozenset({("task-dir", "upstream")})


def require_path_topology(paths: Mapping[str, Path | None]) -> None:
    """Validate source/output topology before creating directories or cloning.

    Upstream, task-dir, and the role workspace are immutable source roots.
    The documented standard layout places task-dir under the pinned upstream
    checkout (``.../Workspace-Bench/evaluation/tasks_lite/300``); that nesting
    is allowed. Every other source/source overlap fails closed.

    Output is the mutable result root. It must be disjoint from every source
    root and from other mutable roots so a nested output cannot rewrite the
    role workspace, upstream checkout, or task corpus before rejection.
    """
    sources: list[tuple[str, Path]] = []
    mutables: list[tuple[str, Path]] = []
    for label, path in paths.items():
        if path is None:
            continue
        require_absolute_path(path, label=label)
        resolved = path.resolve()
        if label in _MUTABLE_ROOT_LABELS:
            mutables.append((label, resolved))
        elif label in _SOURCE_ROOT_LABELS:
            sources.append((label, resolved))
        else:
            raise WorkspacePathError(f"unknown path-topology label: {label}")
    for index, (left_label, left) in enumerate(sources):
        for right_label, right in sources[index + 1 :]:
            if not _paths_overlap(left, right):
                continue
            child_parent = _source_child_parent(
                left_label=left_label, left=left, right_label=right_label, right=right
            )
            if child_parent in _ALLOWED_SOURCE_NESTING:
                continue
            raise WorkspacePathError(
                f"{left_label} {left} overlaps {right_label} {right}; "
                "refusing recursive clone or result-artifact exposure"
            )
    for mutable_label, mutable in mutables:
        for source_label, source in sources:
            if _paths_overlap(mutable, source):
                raise WorkspacePathError(
                    f"{mutable_label} {mutable} overlaps {source_label} {source}; "
                    "refusing recursive clone or result-artifact exposure"
                )
        for other_label, other in mutables:
            if mutable_label == other_label and mutable == other:
                continue
            if _paths_overlap(mutable, other):
                raise WorkspacePathError(
                    f"{mutable_label} {mutable} overlaps {other_label} {other}; "
                    "refusing recursive clone or result-artifact exposure"
                )


def require_output_outside_workspace(*, workspace: Path, output: Path) -> None:
    """Refuse result output nested in a workspace. Workspace nested in a case dir is allowed."""
    require_absolute_path(workspace, label="workspace")
    require_absolute_path(output, label="output")
    workspace_resolved = workspace.resolve()
    output_resolved = output.resolve()
    nested_in_workspace = workspace_resolved in output_resolved.parents
    if output_resolved == workspace_resolved or nested_in_workspace:
        raise WorkspacePathError(
            f"output {output_resolved} overlaps workspace {workspace_resolved}; "
            "refusing recursive clone or result-artifact exposure"
        )


def require_disjoint_roots(paths: Mapping[str, Path | None]) -> None:
    """Validate path topology. Source nesting is allowed only for task-dir under upstream."""
    require_path_topology(paths)


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _source_child_parent(
    *, left_label: str, left: Path, right_label: str, right: Path
) -> tuple[str, str] | None:
    if left == right:
        return None
    if right in left.parents:
        return (left_label, right_label)
    if left in right.parents:
        return (right_label, left_label)
    return None


def contained_path(
    *, root: Path, candidate: Path, follow_symlinks: bool = False
) -> Path:
    """Resolve ``candidate`` and require it to stay inside ``root``."""
    root_resolved = root.resolve()
    probe = candidate if candidate.is_absolute() else root / candidate
    if follow_symlinks:
        resolved = probe.resolve()
    else:
        resolved = probe.parent.resolve() / probe.name
        if probe.is_symlink():
            target = probe.resolve()
            if not _is_relative_to(target, root_resolved):
                raise WorkspacePathError(
                    f"symlink escapes allowed root {root_resolved}: {probe}"
                )
            return probe
    if not _is_relative_to(resolved, root_resolved):
        raise WorkspacePathError(f"path escapes allowed root {root_resolved}: {probe}")
    return resolved if follow_symlinks else probe


def sha256_file(path: Path) -> str:
    """Hash a regular file as streamed bytes."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def sha256_bytes(content: bytes) -> str:
    """Hash an in-memory payload."""
    return hashlib.sha256(content).hexdigest()


def tree_digest(root: Path) -> str:
    """Content-root digest of a workspace tree.

    Regular files contribute ``path + NUL + sha256``. Symlinks that stay inside
    the root contribute ``path + NUL + symlink + NUL + relative target``.
    Escaping symlinks fail closed. Directories are visited without following
    directory symlinks.
    """
    root_resolved = root.resolve()
    if not root_resolved.is_dir():
        raise WorkspaceBenchError(f"workspace root is not a directory: {root}")
    hasher = hashlib.sha256()
    for relative, kind, payload in _tree_entries(root=root_resolved):
        hasher.update(relative.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(kind.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(payload.encode("utf-8") if isinstance(payload, str) else payload)
        hasher.update(b"\n")
    return hasher.hexdigest()


def atomic_write_bytes(*, path: Path, content: bytes, mode: int | None = None) -> None:
    """Flush, fsync, and replace without a partial destination."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if mode is not None:
        os.chmod(path, mode)


def atomic_write_json(
    *, path: Path, value: Mapping[str, object] | BaseModel, mode: int | None = None
) -> None:
    """Persist one JSON artifact with a stable encoding."""
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json")
    else:
        payload = dict(value)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    atomic_write_bytes(path=path, content=encoded, mode=mode)


def _tree_entries(*, root: Path) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        kept_dirs: list[str] = []
        for name in sorted(dirnames):
            child = current / name
            if child.is_symlink():
                _reject_escaping_symlink(root=root, link=child)
                rel = _relative_posix(root=root, path=child)
                target = os.readlink(child)
                entries.append((rel, "dir-symlink", target))
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in sorted(filenames):
            child = current / name
            rel = _relative_posix(root=root, path=child)
            if child.is_symlink():
                _reject_escaping_symlink(root=root, link=child)
                entries.append((rel, "symlink", os.readlink(child)))
                continue
            if not child.is_file():
                continue
            entries.append((rel, "file", sha256_file(child)))
    entries.sort(key=lambda item: item[0])
    return entries


def _reject_escaping_symlink(*, root: Path, link: Path) -> None:
    try:
        resolved = link.resolve()
    except (OSError, RuntimeError) as error:
        raise WorkspacePathError(f"unresolvable symlink {link}: {error}") from error
    if not _is_relative_to(resolved, root):
        raise WorkspacePathError(f"symlink escapes allowed root {root}: {link}")


def _relative_posix(*, root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
