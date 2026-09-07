"""Pinned Workspace-Bench task prompt, staging, and output-collection contract.

Production binds the hashed upstream ``evaluation/src/agent_runner.py`` helpers
after preflight verifies the pin. Tests inject a contract. The local wrappers
match the English working-directory / final path-list semantics and the
``model_output`` target directory used at the pin.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
import importlib.util
import json
import mimetypes
from pathlib import Path
import shutil
import types
from typing import Any
from typing import Protocol

from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import contained_path
from benchmarks.workspacebench.hashing import sha256_bytes
from benchmarks.workspacebench.hashing import sha256_file
from benchmarks.workspacebench.hashing import WorkspacePathError
from benchmarks.workspacebench.models import OutputFile
from benchmarks.workspacebench.models import OutputManifest
from benchmarks.workspacebench.protocol import EXPECTED_UPSTREAM_FILE_SHA256
from benchmarks.workspacebench.protocol import TARGET_OUTPUT_DIR
from benchmarks.workspacebench.protocol import UPSTREAM_COMMIT

AGENT_RUNNER_RELPATH = "evaluation/src/agent_runner.py"
JUDGE_SCRIPT_RELPATH = "evaluation/src/agent_as_a_judge.py"


class TaskContract(Protocol):
    """Upstream-compatible task view, prompt wrap, and output collection."""

    def wrap_prompt(
        self, *, task_prompt: str, work_dir: Path, target_output_dir: str
    ) -> str:
        """Return the tested-agent prompt with working-directory requirements."""
        ...

    def expected_output_basenames(self, metadata: Mapping[str, Any]) -> tuple[str, ...]:
        """Return expected output basenames from task metadata."""
        ...

    def stage_manifest(
        self, *, source_task_dir: Path, destination: Path, metadata: Mapping[str, Any]
    ) -> tuple[str, ...]:
        """Copy data_manifest files to target_path locations. Write no metadata."""
        ...

    def collect_output_paths(
        self,
        *,
        work_dir: Path,
        expected_files: Sequence[str],
        target_output_dir: str,
        returned_paths: Sequence[str],
        last_text: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return collected absolute paths and retrieval methods."""
        ...

    def copy_outputs(
        self, *, output_paths: Sequence[str], out_dir: Path, preserve_root: Path
    ) -> list[dict[str, Any]]:
        """Copy candidate artifacts into the arm case output directory."""
        ...


@dataclass(frozen=True)
class BoundTaskContract:
    """Concrete contract, optionally wrapping hashed upstream callables."""

    wrap_prompt_impl: Any
    expected_impl: Any
    stage_impl: Any
    collect_impl: Any
    copy_impl: Any
    source: str

    def wrap_prompt(
        self, *, task_prompt: str, work_dir: Path, target_output_dir: str
    ) -> str:
        """Return the tested-agent prompt with working-directory requirements."""
        return str(
            self.wrap_prompt_impl(
                prompt=task_prompt,
                work_dir=str(work_dir),
                prompt_head="",
                prompt_tail="",
                task_target_output_dir=target_output_dir,
                language="en",
            )
        )

    def expected_output_basenames(self, metadata: Mapping[str, Any]) -> tuple[str, ...]:
        """Return expected output basenames from task metadata."""
        return tuple(self.expected_impl(dict(metadata)))

    def stage_manifest(
        self, *, source_task_dir: Path, destination: Path, metadata: Mapping[str, Any]
    ) -> tuple[str, ...]:
        """Copy data_manifest files to target_path locations. Write no metadata."""
        validate_data_manifest(
            metadata=metadata, source_task_dir=source_task_dir, destination=destination
        )
        payload = dict(metadata)
        payload["__metadata_path"] = str(source_task_dir / "metadata.json")
        created = self.stage_impl(payload, work_dir=str(destination))
        return tuple(str(item) for item in created)

    def collect_output_paths(
        self,
        *,
        work_dir: Path,
        expected_files: Sequence[str],
        target_output_dir: str,
        returned_paths: Sequence[str],
        last_text: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Return collected absolute paths and retrieval methods."""
        paths, methods = self.collect_impl(
            task_target_output_dir=target_output_dir,
            work_dir=str(work_dir),
            expected_files=list(expected_files),
            returned_paths=list(returned_paths),
            last_text=last_text,
            min_mtime=None,
        )
        return tuple(str(item) for item in paths), tuple(str(item) for item in methods)

    def copy_outputs(
        self, *, output_paths: Sequence[str], out_dir: Path, preserve_root: Path
    ) -> list[dict[str, Any]]:
        """Copy candidate artifacts into the arm case output directory."""
        manifest = self.copy_impl(
            output_paths=list(output_paths),
            out_dir=str(out_dir),
            preserve_root=str(preserve_root),
        )
        return [dict(item) for item in manifest]


def local_task_contract() -> BoundTaskContract:
    """English-equivalent helpers used by tests and as the documented fallback."""
    return BoundTaskContract(
        wrap_prompt_impl=_wrap_prompt_en,
        expected_impl=_expected_output_files,
        stage_impl=_copy_from_manifest,
        collect_impl=_collect_output_paths,
        copy_impl=_copy_outputs,
        source="local_equivalent",
    )


def load_upstream_task_contract(*, upstream: Path) -> BoundTaskContract:
    """Bind hashed ``agent_runner.py`` helpers from the pinned checkout."""
    relative = AGENT_RUNNER_RELPATH
    expected = EXPECTED_UPSTREAM_FILE_SHA256[relative]
    path = upstream / relative
    if not path.is_file():
        raise WorkspaceBenchError(f"upstream helper missing: {relative}")
    observed = sha256_file(path)
    if observed != expected:
        raise WorkspaceBenchError(
            f"upstream file hash drift for {relative}: expected {expected}, "
            f"observed {observed}"
        )
    module = _load_agent_runner(path)
    for name in (
        "_wrap_prompt",
        "_expected_output_files",
        "_copy_from_manifest",
        "_collect_output_paths",
        "_copy_outputs",
    ):
        if not callable(getattr(module, name, None)):
            raise WorkspaceBenchError(f"upstream {relative} is missing {name}")
    return BoundTaskContract(
        wrap_prompt_impl=module._wrap_prompt,
        expected_impl=module._expected_output_files,
        stage_impl=module._copy_from_manifest,
        collect_impl=module._collect_output_paths,
        copy_impl=module._copy_outputs,
        source=f"upstream:{UPSTREAM_COMMIT}:{relative}",
    )


def task_prompt_sha256(prompt: str) -> str:
    """Fingerprint the wrapped or raw task prompt bytes."""
    return sha256_bytes(prompt.encode())


def metadata_sha256(metadata: Mapping[str, Any]) -> str:
    """Fingerprint the full task metadata object."""
    encoded = json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
    return sha256_bytes(encoded.encode())


def collect_output_manifest(
    *,
    workspace: Path,
    case_output_dir: Path,
    metadata: Mapping[str, Any],
    last_text: str,
    contract: TaskContract,
    target_output_dir: str = TARGET_OUTPUT_DIR,
    returned_paths: Sequence[str] = (),
) -> OutputManifest:
    """Collect using upstream semantics and copy into the arm case output dir."""
    expected = contract.expected_output_basenames(metadata)
    preserve_root = workspace / target_output_dir
    preserve_root.mkdir(parents=True, exist_ok=True)
    paths, _methods = contract.collect_output_paths(
        work_dir=workspace,
        expected_files=expected,
        target_output_dir=target_output_dir,
        returned_paths=returned_paths,
        last_text=last_text,
    )
    safe_paths, rejected = filter_collected_paths(workspace=workspace, paths=paths)
    case_output_dir.mkdir(parents=True, exist_ok=True)
    copied = contract.copy_outputs(
        output_paths=safe_paths, out_dir=case_output_dir, preserve_root=preserve_root
    )
    files: list[OutputFile] = []
    seen: set[str] = set()
    for item in copied:
        relative = str(item.get("outputPath") or item.get("sourcePath") or "")
        if not relative or relative in seen:
            continue
        seen.add(relative)
        dest = case_output_dir / relative
        if dest.is_symlink() or not dest.is_file():
            rejected.append(relative)
            continue
        try:
            contained_path(root=case_output_dir, candidate=dest, follow_symlinks=True)
        except WorkspaceBenchError:
            rejected.append(relative)
            continue
        mime, _encoding = mimetypes.guess_type(dest.name)
        files.append(
            OutputFile(
                relative_path=relative.replace("\\", "/"),
                size_bytes=dest.stat().st_size,
                mime_type=mime or "application/octet-stream",
                sha256=sha256_file(dest),
            )
        )
    present_names = {Path(item.relative_path).name for item in files}
    missing = tuple(name for name in expected if name not in present_names)
    return OutputManifest(
        output_root=target_output_dir,
        files=tuple(files),
        missing=missing,
        rejected=tuple(dict.fromkeys(rejected)),
    )


def filter_collected_paths(
    *, workspace: Path, paths: Sequence[str]
) -> tuple[tuple[str, ...], list[str]]:
    """Keep regular in-workspace files; report symlink and escape rejections."""
    safe: list[str] = []
    rejected: list[str] = []
    for raw in paths:
        candidate = Path(raw)
        try:
            contained = contained_path(
                root=workspace, candidate=candidate, follow_symlinks=False
            )
        except WorkspaceBenchError:
            rejected.append(str(raw))
            continue
        if contained.is_symlink() or not contained.is_file():
            rejected.append(str(raw))
            continue
        try:
            contained_path(root=workspace, candidate=contained, follow_symlinks=True)
        except WorkspaceBenchError:
            rejected.append(str(raw))
            continue
        safe.append(str(contained))
    return tuple(dict.fromkeys(safe)), rejected


def required_task_id(metadata: Mapping[str, Any]) -> str:
    """Return the exact task id; empty or missing ids fail closed."""
    value = metadata.get("id")
    if value in (None, ""):
        value = metadata.get("absolute_id")
    if not isinstance(value, str) or not value.strip():
        raise WorkspaceBenchError("task metadata is missing an exact id")
    return value.strip()


def validate_data_manifest(
    *,
    metadata: Mapping[str, Any],
    source_task_dir: Path,
    destination: Path | None = None,
) -> tuple[dict[str, str], ...]:
    """Require a well-formed in-root manifest. Missing or skipped inputs fail.

    The pinned upstream ``_copy_from_manifest`` allows ``..`` escape. This
    wrapper validates every source and target before that helper runs.
    """
    manifest = metadata.get("data_manifest")
    if not isinstance(manifest, list):
        raise WorkspaceBenchError("task data_manifest must be a list")
    source_root = source_task_dir.resolve()
    dest_root = None if destination is None else destination.resolve()
    entries: list[dict[str, str]] = []
    for index, item in enumerate(manifest):
        if not isinstance(item, dict):
            raise WorkspaceBenchError(
                f"data_manifest[{index}] must be an object with stored_relpath "
                "and target_path"
            )
        stored = item.get("stored_relpath")
        target = item.get("target_path")
        if not isinstance(stored, str) or not stored.strip():
            raise WorkspaceBenchError(
                f"data_manifest[{index}] stored_relpath must be a nonempty string"
            )
        if not isinstance(target, str) or not target.strip():
            raise WorkspaceBenchError(
                f"data_manifest[{index}] target_path must be a nonempty string"
            )
        stored_rel = stored.strip().replace("\\", "/")
        target_rel = target.strip().replace("\\", "/")
        if stored_rel.startswith("/") or ".." in Path(stored_rel).parts:
            raise WorkspacePathError(
                f"data_manifest[{index}] stored_relpath escapes the task directory"
            )
        if target_rel.startswith("/") or ".." in Path(target_rel).parts:
            raise WorkspacePathError(
                f"data_manifest[{index}] target_path escapes the staged workspace"
            )
        source = contained_path(
            root=source_root, candidate=source_root / stored_rel, follow_symlinks=False
        )
        if source.is_symlink() or not source.is_file():
            raise WorkspaceBenchError(
                f"data_manifest[{index}] source is not a regular in-root file: "
                f"{stored_rel}"
            )
        if dest_root is not None:
            contained_path(
                root=dest_root, candidate=dest_root / target_rel, follow_symlinks=False
            )
        entries.append({"stored_relpath": stored_rel, "target_path": target_rel})
    return tuple(entries)


def _load_agent_runner(path: Path) -> types.ModuleType:
    import sys

    try:
        import yaml as _yaml  # noqa: F401
    except ImportError as error:
        raise WorkspaceBenchError(
            "pinned agent_runner.py requires PyYAML; install with "
            "uv sync --extra benchmark"
        ) from error
    stubs = {
        "agent_as_a_judge": _module_stub(
            "agent_as_a_judge", evaluate_task=lambda *args, **kwargs: {"success": False}
        ),
        "filesys_utils": _module_stub(
            "filesys_utils", filesys_rollback=lambda *args, **kwargs: None
        ),
        "tqdm": _module_stub(
            "tqdm", tqdm=lambda *args, **kwargs: args[0] if args else None
        ),
    }
    saved: dict[str, types.ModuleType | None] = {
        name: sys.modules.get(name) for name in stubs
    }
    for name, stub in stubs.items():
        sys.modules[name] = stub
    spec = importlib.util.spec_from_file_location(
        "workspacebench_upstream_agent_runner", path
    )
    if spec is None or spec.loader is None:
        _restore_sys_modules(saved)
        raise WorkspaceBenchError(f"cannot load upstream helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        raise WorkspaceBenchError(
            f"failed to load pinned {AGENT_RUNNER_RELPATH}: {error}"
        ) from error
    finally:
        if sys.path and sys.path[0] == str(path.parent):
            sys.path.pop(0)
        _restore_sys_modules(saved)
    return module


def _module_stub(name: str, **attrs: Any) -> types.ModuleType:
    stub = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(stub, key, value)
    return stub


def _restore_sys_modules(saved: Mapping[str, types.ModuleType | None]) -> None:
    import sys

    for name, previous in saved.items():
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous


def _wrap_prompt_en(
    *,
    prompt: str,
    work_dir: str,
    prompt_head: str,
    prompt_tail: str,
    task_target_output_dir: str,
    language: str,
) -> str:
    del language
    if task_target_output_dir != "":
        path_requirement = (
            "Ignore any output-file save path requirements inside the task. "
            f"Place all output files under: {str(Path(work_dir) / task_target_output_dir)}\n"
        )
    else:
        path_requirement = ""
    head = (
        "[Important Requirement 1: Working Directory]\n"
        f"The working directory you may access for this test is: {str(Path(work_dir).resolve())}\n"
        "Use relative paths inside this directory to read and write files; do not access locations outside it.\n"
        "If you see any other workspace path instructions, ignore them and use this working directory as authoritative.\n"
        f"{path_requirement}"
    )
    tail = (
        "\n[Important Requirement 2: Output Path List]\n"
        "At the final step, output only one Python list (list[str]) containing every output file path you generated.\n"
        "Use paths relative to the working directory (do not start with /). Example: ['output/a.txt','report.md']\n"
    )
    body = (
        str(prompt_head or "")
        + ("\n" if prompt_head else "")
        + str(prompt or "").strip()
        + ("\n" if prompt_tail else "")
        + str(prompt_tail or "")
    ).strip()
    return head + "\n" + body + "\n" + tail


def _expected_output_files(meta: Mapping[str, Any]) -> list[str]:
    output_files = meta.get("output_files")
    if isinstance(output_files, list):
        out = [
            Path(str(item)).name.strip() for item in output_files if str(item).strip()
        ]
        if out:
            return out
    single = meta.get("output_file")
    if isinstance(single, str) and single.strip():
        return [single.strip()]
    return []


def _copy_from_manifest(meta: Mapping[str, Any], *, work_dir: str) -> list[str]:
    created: list[str] = []
    metadata_path = meta.get("__metadata_path")
    source_base = (
        Path(metadata_path).resolve().parent
        if isinstance(metadata_path, str) and metadata_path
        else None
    )
    if source_base is None:
        return created
    destination = Path(work_dir).resolve()
    manifest = meta.get("data_manifest")
    if not isinstance(manifest, list):
        return created
    for item in manifest:
        if not isinstance(item, dict):
            continue
        target_path = item.get("target_path")
        stored_relpath = item.get("stored_relpath")
        if not isinstance(target_path, str) or not isinstance(stored_relpath, str):
            continue
        stored = stored_relpath.strip().replace("\\", "/").lstrip("/")
        source = contained_path(
            root=source_base, candidate=source_base / stored, follow_symlinks=True
        )
        if not source.is_file():
            continue
        relative = target_path.strip().replace("\\", "/").lstrip("/")
        dest = contained_path(root=destination, candidate=destination / relative)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        created.append(str(dest))
    return created


def _parse_python_list_paths(text: str) -> list[str]:
    stripped = str(text or "").strip()
    if not stripped:
        return []
    try:
        obj = ast.literal_eval(stripped)
    except (SyntaxError, ValueError):
        start = stripped.find("[")
        end = stripped.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            obj = ast.literal_eval(stripped[start : end + 1])
        except (SyntaxError, ValueError):
            return []
    if not isinstance(obj, list):
        return []
    out: list[str] = []
    for item in obj:
        if isinstance(item, str) and item.strip() and not item.strip().startswith("/"):
            out.append(item.strip())
    return out


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _collect_output_paths(
    *,
    task_target_output_dir: str,
    work_dir: str,
    expected_files: Sequence[str],
    returned_paths: Sequence[str],
    last_text: str,
    min_mtime: float | None,
) -> tuple[list[str], list[str]]:
    del min_mtime
    workspace = Path(work_dir).resolve()
    out: list[str] = []
    methods: list[str] = []
    expected_names = {Path(item).name for item in expected_files}

    def skip_name(name: str) -> bool:
        if name in {
            "trace.txt",
            "trace.json",
            "phase1_file_discovery.md",
            "phase1_files.json",
            "phase2_data_summary.md",
        }:
            return True
        return name not in expected_names and name.lower().endswith((".bak", "~"))

    for relative in _parse_python_list_paths(last_text):
        try:
            candidate = contained_path(root=workspace, candidate=workspace / relative)
        except WorkspaceBenchError:
            continue
        if candidate.is_file() and not skip_name(candidate.name):
            out.append(str(candidate))
    if out:
        methods.append("last_text_paths")

    for path in workspace.rglob("*"):
        if path.is_file() and path.name in expected_names:
            out.append(str(path.resolve()))
    if any(Path(item).name in expected_names for item in out):
        methods.append("expected_filenames_recent")

    for relative in returned_paths:
        try:
            candidate = contained_path(root=workspace, candidate=workspace / relative)
        except WorkspaceBenchError:
            continue
        if candidate.is_file() and not skip_name(candidate.name):
            out.append(str(candidate))
            methods.append("returned_paths_recent")

    if task_target_output_dir:
        target = workspace / task_target_output_dir
        if target.is_dir():
            for path in target.rglob("*"):
                if path.is_file() and not skip_name(path.name):
                    out.append(str(path.resolve()))
            methods.append("task_target_output_dir")

    unique = sorted({item for item in out if Path(item).is_file()})
    return unique, list(dict.fromkeys(methods))


def _copy_outputs(
    *, output_paths: Sequence[str], out_dir: str, preserve_root: str | None = None
) -> list[dict[str, Any]]:
    destination = Path(out_dir)
    destination.mkdir(parents=True, exist_ok=True)
    preserve = Path(preserve_root).resolve() if preserve_root else None
    manifest: list[dict[str, Any]] = []
    for source in output_paths:
        src = Path(source)
        if not src.is_file():
            continue
        if preserve is not None and _is_under(src, preserve):
            relative = src.resolve().relative_to(preserve).as_posix()
        else:
            relative = src.name
        dest = contained_path(root=destination, candidate=destination / relative)
        if dest.resolve() == src.resolve():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        manifest.append(
            {
                "sourcePath": relative,
                "outputPath": dest.relative_to(destination).as_posix(),
                "sizeBytes": dest.stat().st_size,
            }
        )
    return manifest
