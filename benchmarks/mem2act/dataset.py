"""Load Mem2Act released jsonl without vendoring it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from typing import Mapping

from benchmarks.mem2act.protocol import DATASET_COMMIT
from benchmarks.mem2act.protocol import DATASET_SUBDIR


class DatasetError(ValueError):
    """Invalid or incomplete Mem2Act dataset root."""


def assert_dataset_layout(*, dataset_root: Path) -> Path:
    """Return the Mem2ActBench data directory inside a pinned checkout."""
    root = dataset_root
    candidate = root / DATASET_SUBDIR
    if candidate.is_dir():
        data_dir = candidate
    elif (root / "qa_dataset.jsonl").is_file():
        data_dir = root
    else:
        raise DatasetError(
            f"expected {DATASET_SUBDIR}/ or qa_dataset.jsonl under {dataset_root}"
        )
    for name in ("qa_dataset.jsonl", "toolmem_conversation.jsonl"):
        if not (data_dir / name).is_file():
            raise DatasetError(f"missing {data_dir / name}")
    return data_dir


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file into a list of objects."""
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as error:
            raise DatasetError(f"{path}:{line_no}: {error}") from error
        if not isinstance(payload, dict):
            raise DatasetError(f"{path}:{line_no}: expected object")
        rows.append(payload)
    return rows


def load_qa_index(*, data_dir: Path) -> dict[str, dict[str, Any]]:
    """Index QA items by qa_id."""
    rows = load_jsonl(data_dir / "qa_dataset.jsonl")
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        qa_id = row.get("qa_id")
        if not isinstance(qa_id, str) or not qa_id:
            raise DatasetError("qa row missing qa_id")
        index[qa_id] = row
    return index


def load_session_index(*, data_dir: Path) -> dict[str, dict[str, Any]]:
    """Index sessions by session_id."""
    rows = load_jsonl(data_dir / "toolmem_conversation.jsonl")
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        session_id = row.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise DatasetError("session row missing session_id")
        index[session_id] = row
    return index


def render_session_markdown(*, session: Mapping[str, Any]) -> str:
    """Render session turns as an ingestible markdown document."""
    session_id = session.get("session_id", "unknown")
    lines = [
        f"# Mem2Act session `{session_id}`",
        "",
        f"- token_count: {session.get('token_count')}",
        f"- turn_count: {session.get('turn_count')}",
        "",
        "## Turns",
        "",
    ]
    for index, turn in enumerate(session.get("turns") or [], start=1):
        if not isinstance(turn, dict):
            continue
        role = turn.get("role", "unknown")
        content = turn.get("content") or ""
        lines.append(f"### Turn {index} ({role})")
        lines.append("")
        if isinstance(content, str) and content.strip():
            lines.append(content.strip())
            lines.append("")
        tool_calls = turn.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            lines.append("Tool calls:")
            lines.append("")
            lines.append(
                f"```json\n{json.dumps(tool_calls, ensure_ascii=False, indent=2)}\n```"
            )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def pin_note() -> str:
    """Human-readable pin string."""
    return f"{DATASET_COMMIT} ({DATASET_SUBDIR})"
