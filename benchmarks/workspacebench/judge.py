"""Official judge handoff. Does not invoke paid Anthropic-compatible inference."""

from __future__ import annotations

import json
from pathlib import Path

from benchmarks.workspacebench.errors import LiveGateError
from benchmarks.workspacebench.errors import WorkspaceBenchError
from benchmarks.workspacebench.hashing import require_absolute_path
from benchmarks.workspacebench.hashing import sha256_file
from benchmarks.workspacebench.protocol import EXPECTED_UPSTREAM_FILE_SHA256
from benchmarks.workspacebench.task_contract import JUDGE_SCRIPT_RELPATH

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
