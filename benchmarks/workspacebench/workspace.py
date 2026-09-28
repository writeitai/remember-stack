"""Isolated task workspaces, manifest staging, and metadata hiding."""

from __future__ import annotations

from collections.abc import Mapping
import json
import mimetypes
from pathlib import Path
import shutil
from typing import Any

from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import atomic_write_json
from benchmarks.workspacebench.hashing import contained_path
from benchmarks.workspacebench.hashing import sha256_bytes
from benchmarks.workspacebench.hashing import sha256_file
from benchmarks.workspacebench.hashing import tree_digest
from benchmarks.workspacebench.hashing import WorkspacePathError
from benchmarks.workspacebench.models import OutputFile
from benchmarks.workspacebench.models import OutputManifest
from benchmarks.workspacebench.protocol import EVALUATION_ONLY_METADATA_FILENAME
from benchmarks.workspacebench.protocol import EVALUATION_ONLY_METADATA_KEYS
from benchmarks.workspacebench.protocol import EVALUATION_ONLY_METADATA_PREFIXES
from benchmarks.workspacebench.protocol import OWNER_ONLY_FILE_MODE
from benchmarks.workspacebench.protocol import TARGET_OUTPUT_DIR
from benchmarks.workspacebench.task_contract import local_task_contract
from benchmarks.workspacebench.task_contract import TaskContract


def load_json_object(path: Path) -> dict[str, Any]:
    """Load one JSON object or fail closed."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceBenchError(f"invalid JSON object {path}: {error}") from error
    if not isinstance(payload, dict):
        raise WorkspaceBenchError(f"JSON root must be an object: {path}")
    return payload


def agent_visible_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Strip evaluator-only fields. Not used as the production agent view."""
    return {
        key: value
        for key, value in metadata.items()
        if key not in EVALUATION_ONLY_METADATA_KEYS
        and not key.startswith(EVALUATION_ONLY_METADATA_PREFIXES)
    }


def data_manifest_sha256(metadata: Mapping[str, Any]) -> str:
    """Fingerprint the task data-manifest list without gold fields."""
    manifest = metadata.get("data_manifest")
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, default=str)
    return sha256_bytes(encoded.encode())


def expected_output_relpaths(metadata: Mapping[str, Any]) -> tuple[str, ...]:
    """Read output paths from task metadata. Filenames are not hard-coded."""
    raw = metadata.get("output_files")
    if raw is None:
        return ()
    if isinstance(raw, list):
        paths: list[str] = []
        for item in raw:
            if isinstance(item, str) and item.strip():
                paths.append(item.strip().replace("\\", "/"))
            elif isinstance(item, dict):
                candidate = item.get("path") or item.get("filename") or item.get("name")
                if isinstance(candidate, str) and candidate.strip():
                    paths.append(candidate.strip().replace("\\", "/"))
        return tuple(paths)
    if isinstance(raw, dict):
        return tuple(
            str(value).replace("\\", "/")
            for value in raw.values()
            if isinstance(value, str) and value.strip()
        )
    raise WorkspaceBenchError("output_files must be a list or object")


def official_output_root(*, workspace: Path, metadata: Mapping[str, Any]) -> Path:
    """Return the task output directory, defaulting to ``model_output``."""
    configured = (
        metadata.get("output_dir")
        or metadata.get("output_directory")
        or TARGET_OUTPUT_DIR
    )
    if isinstance(configured, str) and configured.strip():
        relative = configured.strip().replace("\\", "/").lstrip("/")
    else:
        relative = TARGET_OUTPUT_DIR
    root = contained_path(root=workspace, candidate=workspace / relative)
    root.mkdir(parents=True, exist_ok=True)
    return root


def collect_outputs(
    *, workspace: Path, metadata: Mapping[str, Any], output_root: Path | None = None
) -> OutputManifest:
    """Collect expected outputs under the official output root with path safety."""
    root = (
        output_root
        if output_root is not None
        else official_output_root(workspace=workspace, metadata=metadata)
    )
    expected = expected_output_relpaths(metadata)
    files: list[OutputFile] = []
    missing: list[str] = []
    rejected: list[str] = []
    for relative in expected:
        try:
            path = _output_path(output_root=root, relative=relative)
        except WorkspacePathError:
            rejected.append(relative)
            continue
        if not path.exists():
            missing.append(relative)
            continue
        if path.is_symlink() or not path.is_file():
            rejected.append(relative)
            continue
        mime, _encoding = mimetypes.guess_type(path.name)
        files.append(
            OutputFile(
                relative_path=relative,
                size_bytes=path.stat().st_size,
                mime_type=mime or "application/octet-stream",
                sha256=sha256_file(path),
            )
        )
    return OutputManifest(
        output_root=_relative_or_name(workspace=workspace, path=root),
        files=tuple(files),
        missing=tuple(missing),
        rejected=tuple(rejected),
    )


def _output_path(*, output_root: Path, relative: str) -> Path:
    cleaned = relative.replace("\\", "/")
    if cleaned.startswith("/") or ".." in Path(cleaned).parts:
        raise WorkspacePathError(f"output path escapes output root: {relative}")
    candidate = output_root.joinpath(*cleaned.split("/"))
    return contained_path(root=output_root, candidate=candidate, follow_symlinks=True)


def _relative_or_name(*, workspace: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.name


def clone_pristine_workspace(*, source: Path, destination: Path) -> str:
    """Copy a workspace without following escaping symlinks and return its digest."""
    if destination.exists():
        raise WorkspaceBenchError(f"clone destination already exists: {destination}")
    source_digest = tree_digest(source)
    shutil.copytree(source, destination, symlinks=True)
    cloned = tree_digest(destination)
    if cloned != source_digest:
        raise WorkspaceBenchError("cloned workspace digest drifted from source")
    return cloned


def stage_manifest_inputs(
    *,
    source_task_dir: Path,
    destination: Path,
    metadata: Mapping[str, Any] | None = None,
    contract: TaskContract | None = None,
    target_output_dir: str = TARGET_OUTPUT_DIR,
) -> tuple[str, ...]:
    """Stage data_manifest files into ``destination`` and create the output dir.

    Writes no ``metadata.json``. Evaluator metadata stays out of band until the
    tested agent exits.
    """
    source_task_dir = source_task_dir.resolve()
    raw = (
        metadata
        if metadata is not None
        else load_json_object(source_task_dir / "metadata.json")
    )
    helper = contract or local_task_contract()
    created = helper.stage_manifest(
        source_task_dir=source_task_dir, destination=destination, metadata=raw
    )
    output_dir = contained_path(
        root=destination, candidate=destination / target_output_dir
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    return created


def write_agent_task_view(
    *,
    source_task_dir: Path,
    destination: Path,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage manifest inputs only. Does not write evaluator metadata.json."""
    raw = (
        metadata
        if metadata is not None
        else load_json_object(source_task_dir / "metadata.json")
    )
    stage_manifest_inputs(
        source_task_dir=source_task_dir, destination=destination, metadata=raw
    )
    return agent_visible_metadata(raw)


def evaluation_metadata_leaks(*, root: Path) -> tuple[str, ...]:
    """Return relative paths of metadata.json files that still carry gold fields."""
    hits: list[str] = []
    if not root.exists():
        return ()
    for path in sorted(root.rglob(EVALUATION_ONLY_METADATA_FILENAME)):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            text = path.read_text(encoding="utf-8", errors="replace")
            if _text_has_evaluation_markers(text):
                hits.append(path.relative_to(root).as_posix())
            continue
        if isinstance(payload, dict) and _mapping_has_evaluation_fields(payload):
            hits.append(path.relative_to(root).as_posix())
            continue
        dumped = json.dumps(payload, default=str)
        if _text_has_evaluation_markers(dumped):
            hits.append(path.relative_to(root).as_posix())
    return tuple(hits)


def assert_no_evaluation_metadata(*, root: Path) -> None:
    """Fail closed if evaluator metadata is visible under ``root``."""
    leaks = evaluation_metadata_leaks(root=root)
    if leaks:
        raise WorkspaceBenchError(
            "evaluation-only metadata.json is visible during the tested turn: "
            + ", ".join(leaks)
        )


def write_full_evaluator_metadata(*, path: Path, metadata: Mapping[str, Any]) -> None:
    """Write complete evaluator metadata only after the tested agent exits."""
    atomic_write_json(path=path, value=dict(metadata), mode=OWNER_ONLY_FILE_MODE)


def _mapping_has_evaluation_fields(payload: Mapping[str, Any]) -> bool:
    for key in payload:
        if key in EVALUATION_ONLY_METADATA_KEYS:
            return True
        if any(key.startswith(prefix) for prefix in EVALUATION_ONLY_METADATA_PREFIXES):
            return True
    return False


def _text_has_evaluation_markers(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "rubrics",
        "rubric_types",
        "ground_truth",
        "reference_output",
        "file_dep_graph",
        "dependency_graph",
        "judge_metadata",
    )
    return any(marker in lowered for marker in markers)
