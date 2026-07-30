"""Deterministic train-trajectory serialization (shared write units)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from typing import Mapping

from benchmarks.state_bench.model import TrajectoryDocument
from benchmarks.state_bench.protocol import learning_string
from benchmarks.state_bench.protocol import SOURCE_KIND
from benchmarks.state_bench.protocol import source_ref


class TrajectoryError(ValueError):
    """Invalid trajectory bytes or leakage against a test-task set."""


def load_trajectory_json(path: Path) -> Mapping[str, Any]:
    """Load one upstream train trajectory file."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TrajectoryError(f"cannot read trajectory {path}: {error}") from error
    if not isinstance(payload, dict):
        raise TrajectoryError(f"trajectory root must be an object: {path}")
    conversation = payload.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        raise TrajectoryError(f"trajectory missing conversation[]: {path}")
    return payload


def render_trajectory_markdown(
    *, domain: str, task_id: str, trajectory: Mapping[str, Any]
) -> str:
    """Render a stable markdown learning document from a train trajectory.

    No LLM call. Tool call arguments are included when present so procedural
    patterns (order, policy checks) survive into the shared unit corpus.
    """
    lines: list[str] = [
        "# STATE-Bench train trajectory",
        "",
        f"- domain: `{domain}`",
        f"- task_id: `{task_id}`",
        f"- source_kind: `{SOURCE_KIND}`",
        f"- source_ref: `{source_ref(domain=domain, task_id=task_id)}`",
        "",
        "## Conversation",
        "",
    ]
    conversation = trajectory.get("conversation")
    if not isinstance(conversation, list) or not conversation:
        raise TrajectoryError("trajectory missing non-empty conversation[]")
    for index, turn in enumerate(conversation, start=1):
        if not isinstance(turn, dict):
            raise TrajectoryError(f"{task_id}: turn {index} is not an object")
        role = str(turn.get("role", "unknown"))
        content = turn.get("content")
        text = content if isinstance(content, str) else ""
        lines.append(f"### Turn {index} ({role})")
        lines.append("")
        if text.strip():
            lines.append(text.strip())
            lines.append("")
        tool_calls = turn.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            lines.append("Tool calls:")
            lines.append("")
            for call in tool_calls:
                lines.append(f"- `{_compact_json(call)}`")
            lines.append("")
        tool_results = turn.get("tool_results") or turn.get("tool_outputs")
        if isinstance(tool_results, list) and tool_results:
            lines.append("Tool results:")
            lines.append("")
            for result in tool_results:
                lines.append(f"- `{_compact_json(result)}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def serialize_trajectory_document(
    *, domain: str, task_id: str, trajectory_path: Path
) -> TrajectoryDocument:
    """Load and fingerprint one train trajectory as an ingest document."""
    payload = load_trajectory_json(path=trajectory_path)
    markdown = render_trajectory_markdown(
        domain=domain, task_id=task_id, trajectory=payload
    )
    digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    return TrajectoryDocument(
        domain=domain,  # type: ignore[arg-type]
        task_id=task_id,
        source_ref=source_ref(domain=domain, task_id=task_id),
        title=f"STATE-Bench {domain} {task_id}",
        markdown=markdown,
        content_sha256=digest,
    )


def assert_no_test_leakage(
    *, train_task_ids: set[str], test_task_ids: set[str]
) -> None:
    """Refuse overlapping train/test task IDs (gold leakage guard)."""
    overlap = sorted(train_task_ids & test_task_ids)
    if overlap:
        preview = ", ".join(overlap[:10])
        raise TrajectoryError(
            f"train/test leakage on {len(overlap)} task id(s): {preview}"
        )


def list_train_task_ids(*, trajectories_dir: Path) -> tuple[str, ...]:
    """Sorted train task IDs from a domain trajectories directory."""
    if not trajectories_dir.is_dir():
        raise TrajectoryError(f"missing trajectories dir: {trajectories_dir}")
    return tuple(sorted(path.stem for path in trajectories_dir.glob("*.json")))


def bm25_learning_strings(
    *, documents: tuple[TrajectoryDocument, ...], query: str, top_k: int
) -> list[str]:
    """Tiny lexical floor over shared documents (no external deps)."""
    tokens = _tokenize(query)
    if not tokens:
        ranked = documents[:top_k]
    else:
        scored: list[tuple[int, TrajectoryDocument]] = []
        for document in documents:
            hay = document.markdown.lower()
            score = sum(hay.count(token) for token in tokens)
            scored.append((score, document))
        scored.sort(key=lambda item: (-item[0], item[1].task_id))
        ranked = tuple(document for score, document in scored if score > 0)[:top_k]
        if not ranked:
            ranked = documents[:top_k]
    return [
        learning_string(
            source_id=document.source_ref,
            text=_excerpt(document.markdown, limit=1200),
            rank=index,
        )
        for index, document in enumerate(ranked, start=1)
    ]


def _tokenize(text: str) -> tuple[str, ...]:
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return tuple(token for token in cleaned.split() if len(token) > 2)


def _excerpt(text: str, *, limit: int) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[: limit - 1].rstrip() + "…"


def _compact_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except TypeError:
        return repr(value)
