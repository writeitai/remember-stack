"""Official judge handoff. Does not invoke paid Anthropic-compatible inference."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from benchmarks.workspacebench.errors import LiveGateError
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import contained_path
from benchmarks.workspacebench.hashing import require_absolute_path
from benchmarks.workspacebench.hashing import sha256_file
from benchmarks.workspacebench.models import TaskResult
from benchmarks.workspacebench.protocol import EXPECTED_UPSTREAM_FILE_SHA256
from benchmarks.workspacebench.task_contract import JUDGE_SCRIPT_RELPATH
from benchmarks.workspacebench.workspace import load_json_object

JUDGE_LIVE_GATE = (
    "official Workspace-Bench judge remains the upstream ClaudeCode/"
    "Anthropic-compatible scorer. Implementation prepares a documented "
    "invocation with --task-dir and --eval-yaml; it does not call the judge model."
)
JUDGE_REQUIRED_FLAGS: tuple[str, ...] = ("--task-dir", "--eval-yaml")


def official_judge_command(
    *, eval_root: Path, task_dir: Path, eval_yaml: Path
) -> tuple[str, ...]:
    """Validated upstream scorer invocation. Not executed by this adapter.

    The pinned ``agent_as_a_judge.py`` CLI requires both ``--task-dir`` and
    ``--eval-yaml``. The scorer prepares its own restricted judge view from
    case output, post-turn metadata, source task data, and agent.json.
    """
    require_absolute_path(eval_root, label="evaluation root")
    require_absolute_path(task_dir, label="task directory")
    require_absolute_path(eval_yaml, label="eval yaml")
    script = eval_root / "src" / "agent_as_a_judge.py"
    if not script.is_file():
        raise WorkspaceBenchError(f"official judge script missing: {script}")
    expected = EXPECTED_UPSTREAM_FILE_SHA256.get(JUDGE_SCRIPT_RELPATH)
    if expected is not None:
        observed = sha256_file(script)
        if observed != expected:
            raise WorkspaceBenchError(
                f"official judge script hash drift: expected {expected}, "
                f"observed {observed}"
            )
    if not eval_yaml.is_file():
        raise WorkspaceBenchError(f"eval yaml missing: {eval_yaml}")
    if not task_dir.is_dir():
        raise WorkspaceBenchError(f"judge task-dir is not a directory: {task_dir}")
    command = (
        "python3",
        str(script),
        "--task-dir",
        str(task_dir),
        "--eval-yaml",
        str(eval_yaml),
    )
    if "--task-dir" not in command or "--eval-yaml" not in command:
        raise WorkspaceBenchError("official judge command lost required flags")
    return command


def official_judge_invocations(
    *, eval_root: Path, native_case: Path, memory_case: Path, eval_yaml: Path
) -> dict[str, tuple[str, ...]]:
    """Copy-paste invocations for each arm case directory."""
    return {
        "native": official_judge_command(
            eval_root=eval_root, task_dir=native_case, eval_yaml=eval_yaml
        ),
        "memory": official_judge_command(
            eval_root=eval_root, task_dir=memory_case, eval_yaml=eval_yaml
        ),
    }


def prepare_judge_view(
    *,
    case_dir: Path,
    source_task_dir: Path,
    candidate_id: str,
    result: TaskResult | None = None,
) -> Path:
    """Optional local helper. The official scorer builds its own restricted view.

    If used, this copies or links ``source_task_dir/data`` into ``inputs``.
    It never creates a misleading empty input directory.
    """
    require_absolute_path(case_dir, label="case directory")
    require_absolute_path(source_task_dir, label="source task directory")
    full_metadata = load_json_object(source_task_dir / "metadata.json")
    view = case_dir / "judge_view" / candidate_id
    if view.exists():
        shutil.rmtree(view)
    view.mkdir(parents=True)
    data_dir = source_task_dir / "data"
    if data_dir.is_dir():
        _symlink_or_copy(source=data_dir, destination=view / "inputs")
    source_output = case_dir / "output"
    if source_output.is_dir():
        _symlink_or_copy(source=source_output, destination=view / "candidate_output")
    (view / "original_task_metadata.json").write_text(
        json.dumps(full_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if result is not None:
        trace_snapshot = {
            "attempt_id": result.attempt_id,
            "failure_class": result.failure_class,
            "item_types": list(result.traces.item_types),
            "command_count": result.traces.command_count,
            "file_change_count": result.traces.file_change_count,
            "mcp_tool_names": [call.tool for call in result.traces.mcp_calls],
        }
        (view / "trace_snapshot.json").write_text(
            json.dumps(trace_snapshot, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    (view / "README.md").write_text(
        "Restricted evaluation workspace for the official agent-as-a-judge.\n"
        "Do not treat this as a tested-agent filesystem.\n"
        "inputs/ is copied from the source task data directory when present.\n",
        encoding="utf-8",
    )
    _ = contained_path(root=case_dir, candidate=view)
    return view


def invoke_official_judge(
    *, eval_root: Path, case_dir: Path, eval_yaml: Path | None = None
) -> None:
    """Refuse to spend judge inference from this experimental adapter."""
    del eval_root, case_dir, eval_yaml
    raise LiveGateError(JUDGE_LIVE_GATE)


def pinned_resolve_original_task_source(meta: dict[str, object]) -> Path | None:
    """Faithful copy of the pinned ``_resolve_original_task_source``.

    The official judge uses ``metadata.json`` ``__metadata_path`` to expose
    original task ``data/`` inputs. This helper does not call the judge model.
    """
    metadata_path = meta.get("__metadata_path")
    if isinstance(metadata_path, str) and metadata_path.strip():
        directory = Path(metadata_path).resolve().parent
        if directory.is_dir():
            return directory
    return None


def pinned_build_trace_snapshot(*, task_dir: Path) -> dict[str, object]:
    """Faithful copy of the pinned ``_build_trace_snapshot`` field filter.

    Expects ``agent.json`` ``trace.executionTrace`` items with ``type`` ``tool``
    or ``text`` and the documented fields. Does not call the judge model.
    """
    agent_json = _safe_load_json(task_dir / "agent.json")
    if not isinstance(agent_json, dict):
        return {"taskDir": str(task_dir.resolve()), "workDir": None, "events": []}
    work_dir = (
        agent_json.get("workDir")
        if isinstance(agent_json.get("workDir"), str)
        else None
    )
    trace = agent_json.get("trace")
    raw_trace = trace.get("executionTrace") if isinstance(trace, dict) else None
    execution_trace = raw_trace if isinstance(raw_trace, list) else []
    events: list[dict[str, object]] = []
    for item in execution_trace:
        if not isinstance(item, dict):
            continue
        event_type = item.get("type")
        if event_type == "tool":
            events.append(
                {
                    "type": "tool",
                    "tool": item.get("tool"),
                    "input": (
                        item.get("input") if isinstance(item.get("input"), dict) else {}
                    ),
                    "output": (
                        item.get("output")
                        if isinstance(item.get("output"), dict)
                        else {}
                    ),
                    "timestamp": item.get("timestamp"),
                }
            )
        elif event_type == "text":
            content = item.get("content")
            if isinstance(content, str):
                events.append(
                    {
                        "type": "text",
                        "role": item.get("role"),
                        "content": content,
                        "timestamp": item.get("timestamp"),
                    }
                )
    return {"taskDir": str(task_dir.resolve()), "workDir": work_dir, "events": events}


def _safe_load_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _symlink_or_copy(*, source: Path, destination: Path) -> None:
    if destination.exists():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.symlink_to(source, target_is_directory=source.is_dir())
    except OSError:
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
