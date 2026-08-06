"""LongMemEval-S loader (local HF/json path; not vendored)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class DatasetError(ValueError):
    """Missing or invalid LongMemEval material."""


def find_dataset_files(dataset_root: Path) -> Path:
    """Locate a JSON/JSONL file that holds LongMemEval questions."""
    candidates = [
        dataset_root / "longmemeval_s.json",
        dataset_root / "longmemeval_s_cleaned.json",
        dataset_root / "data" / "longmemeval_s.json",
        dataset_root / "longmemeval_s.jsonl",
    ]
    # Also accept any single *.json with question_id fields
    for path in candidates:
        if path.is_file():
            return path
    json_files = sorted(dataset_root.rglob("*.json"))
    jsonl_files = sorted(dataset_root.rglob("*.jsonl"))
    for path in [*json_files, *jsonl_files]:
        if path.stat().st_size < 1000:
            continue
        # peek
        text = path.read_text(encoding="utf-8")[:2000]
        if "question" in text and (
            "haystack" in text or "session" in text or "answer" in text
        ):
            return path
    raise DatasetError(
        f"no LongMemEval-looking JSON/JSONL under {dataset_root}; "
        "download xiaowu0162/longmemeval-cleaned and pass --dataset-root"
    )


def load_items(path: Path) -> list[dict[str, Any]]:
    """Load list-or-jsonl LongMemEval items."""
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl" or raw.lstrip().startswith("{"):
        # try jsonl first if multiple lines of objects
        lines = [line for line in raw.splitlines() if line.strip()]
        if len(lines) > 1 and lines[0].lstrip().startswith("{"):
            return [json.loads(line) for line in lines]
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "examples", "questions"):
            if isinstance(data.get(key), list):
                return data[key]
    raise DatasetError(f"unrecognized dataset shape in {path}")


def item_id(item: dict[str, Any]) -> str:
    """Stable id for one question."""
    for key in ("question_id", "id", "qid"):
        value = item.get(key)
        if value is not None:
            return str(value)
    # hash question text
    q = str(item.get("question") or item.get("query") or "")
    return "q_" + hashlib.sha256(q.encode()).hexdigest()[:12]


def ability_label(item: dict[str, Any]) -> str:
    """Best-effort ability / question type label (LongMemEval uses question_type)."""
    for key in (
        "question_type",
        "ability",
        "category",
        "type",
        "question_class",
    ):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return "unknown"


def history_text(item: dict[str, Any]) -> str:
    """Extract the conversation history as plain text for ingest."""
    # Common LongMemEval fields
    for key in (
        "haystack_sessions",
        "haystack",
        "sessions",
        "conversation",
        "context",
    ):
        if key not in item:
            continue
        value = item[key]
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list):
            return _render_sessions(value)
    raise DatasetError(f"item {item_id(item)} has no history field")


def _render_sessions(sessions: list[Any]) -> str:
    chunks: list[str] = []
    for index, session in enumerate(sessions, start=1):
        chunks.append(f"## Session {index}\n")
        if isinstance(session, str):
            chunks.append(session)
            continue
        if isinstance(session, list):
            for turn in session:
                chunks.append(_render_turn(turn))
            continue
        if isinstance(session, dict):
            turns = session.get("turns") or session.get("messages") or session.get("dialog")
            if isinstance(turns, list):
                for turn in turns:
                    chunks.append(_render_turn(turn))
            else:
                chunks.append(json.dumps(session, ensure_ascii=False, indent=2))
    return "\n".join(chunks).strip() + "\n"


def _render_turn(turn: Any) -> str:
    if isinstance(turn, str):
        return turn
    if not isinstance(turn, dict):
        return str(turn)
    role = turn.get("role") or turn.get("speaker") or "unknown"
    content = turn.get("content") or turn.get("text") or turn.get("message") or ""
    return f"**{role}:** {content}\n"
