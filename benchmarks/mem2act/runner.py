"""Prepare Mem2Act runs and summarize deterministic scores."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any
from typing import Mapping

from benchmarks.mem2act.dataset import assert_dataset_layout
from benchmarks.mem2act.dataset import DatasetError
from benchmarks.mem2act.dataset import load_qa_index
from benchmarks.mem2act.dataset import load_session_index
from benchmarks.mem2act.dataset import render_session_markdown
from benchmarks.mem2act.model import ArmKey
from benchmarks.mem2act.model import RunConfiguration
from benchmarks.mem2act.model import ScoreRecord
from benchmarks.mem2act.model import TaskManifest
from benchmarks.mem2act.model import Tier
from benchmarks.mem2act.protocol import ADAPTER_VERSION
from benchmarks.mem2act.protocol import DATASET_COMMIT
from benchmarks.mem2act.protocol import DEFAULT_RECIPE_NAME
from benchmarks.mem2act.protocol import DEFAULT_TOP_K
from benchmarks.mem2act.protocol import PROTOCOL_NAME
from benchmarks.mem2act.protocol import TIER_COUNTS
from benchmarks.mem2act.score import score_tool_call

MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"
REPO_ROOT = Path(__file__).resolve().parents[2]


class BenchmarkRunError(ValueError):
    """User-facing prepare/score failure."""


def load_manifest(*, tier: Tier) -> TaskManifest:
    """Load a committed tier manifest."""
    path = MANIFEST_DIR / f"{tier}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TaskManifest(
        version=int(raw["version"]),
        tier=raw["tier"],
        protocol=raw["protocol"],
        dataset_commit=raw["dataset_commit"],
        dataset_subdir=raw["dataset_subdir"],
        item_ids=tuple(raw["item_ids"]),
        item_ids_sha256=raw["item_ids_sha256"],
        item_count=int(raw["item_count"]),
        level_counts={k: int(v) for k, v in raw["level_counts"].items()},
        resolved_only=bool(raw.get("resolved_only", True)),
    )


def validate_manifest(manifest: TaskManifest) -> None:
    """Check protocol pin, counts, and content hash."""
    if manifest.protocol != PROTOCOL_NAME:
        raise BenchmarkRunError(f"manifest protocol {manifest.protocol!r}")
    if manifest.dataset_commit != DATASET_COMMIT:
        raise BenchmarkRunError("manifest dataset_commit does not match protocol pin")
    expected = TIER_COUNTS[manifest.tier]
    if len(manifest.item_ids) != expected:
        raise BenchmarkRunError(
            f"{manifest.tier}: expected {expected} items, got {len(manifest.item_ids)}"
        )
    if len(set(manifest.item_ids)) != len(manifest.item_ids):
        raise BenchmarkRunError("duplicate item ids")
    digest = hashlib.sha256(
        json.dumps(list(manifest.item_ids), separators=(",", ":")).encode()
    ).hexdigest()
    if digest != manifest.item_ids_sha256:
        raise BenchmarkRunError("item_ids_sha256 mismatch")
    session_map = load_session_map()
    missing = [item_id for item_id in manifest.item_ids if item_id not in session_map]
    if missing:
        raise BenchmarkRunError(
            f"session_map missing {len(missing)} ids, e.g. {missing[:3]}"
        )


def load_session_map() -> dict[str, str]:
    """Load committed qa_id → session_id map."""
    path = MANIFEST_DIR / "session_map.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in raw.items()}


def repository_revision() -> str:
    """Git HEAD of the adapter repository."""
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def prepare_run(
    *,
    output: Path,
    tier: Tier,
    arm: ArmKey,
    dataset_root: Path,
    reader_model: str,
    top_k: int = DEFAULT_TOP_K,
    recipe_name: str = DEFAULT_RECIPE_NAME,
    max_evaluator_cost_usd: float = 25.0,
) -> RunConfiguration:
    """Write an immutable run directory (no provider calls)."""
    manifest = load_manifest(tier=tier)
    validate_manifest(manifest)
    data_dir = assert_dataset_layout(dataset_root=dataset_root)
    qa_index = load_qa_index(data_dir=data_dir)
    session_index = load_session_index(data_dir=data_dir)
    session_map = load_session_map()

    for qa_id in manifest.item_ids:
        if qa_id not in qa_index:
            raise BenchmarkRunError(f"qa_id not in dataset: {qa_id}")
        session_id = session_map[qa_id]
        if session_id not in session_index:
            raise BenchmarkRunError(f"session not in dataset: {session_id}")

    if output.exists() and any(output.iterdir()):
        raise BenchmarkRunError(f"output not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)

    # Materialize only sessions needed by this tier.
    needed_sessions = sorted({session_map[qa_id] for qa_id in manifest.item_ids})
    documents_dir = output / "documents"
    documents_dir.mkdir(parents=True, exist_ok=True)
    for session_id in needed_sessions:
        markdown = render_session_markdown(session=session_index[session_id])
        (documents_dir / f"{session_id}.md").write_text(markdown, encoding="utf-8")

    configuration = RunConfiguration(
        protocol=PROTOCOL_NAME,
        adapter_version=ADAPTER_VERSION,
        dataset_commit=DATASET_COMMIT,
        dataset_root=str(dataset_root.resolve()),
        tier=tier,
        arm=arm,
        repository_revision=repository_revision(),
        reader_model=reader_model,
        recipe_name=recipe_name,
        top_k=top_k,
        manifest_sha256=manifest.item_ids_sha256,
        max_evaluator_cost_usd=max_evaluator_cost_usd,
        item_ids=manifest.item_ids,
    )
    (output / "run.json").write_text(
        configuration.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "tier": manifest.tier,
                "item_ids": list(manifest.item_ids),
                "item_ids_sha256": manifest.item_ids_sha256,
                "session_ids": needed_sessions,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output / "state.json").write_text(
        json.dumps({"phase": "prepared", "scores": {}}, indent=2) + "\n",
        encoding="utf-8",
    )
    return configuration


def summarize_scores(records: list[ScoreRecord]) -> dict[str, Any]:
    """Aggregate accuracy overall and by level."""
    if not records:
        return {"n": 0, "accuracy": 0.0, "by_level": {}}
    ok = sum(1 for record in records if record.item_ok)
    by_level: dict[str, dict[str, float | int]] = {}
    for record in records:
        bucket = by_level.setdefault(record.level, {"n": 0, "ok": 0})
        bucket["n"] = int(bucket["n"]) + 1
        if record.item_ok:
            bucket["ok"] = int(bucket["ok"]) + 1
    for _level, bucket in by_level.items():
        n = int(bucket["n"])
        bucket["accuracy"] = (int(bucket["ok"]) / n) if n else 0.0
    return {
        "n": len(records),
        "ok": ok,
        "accuracy": ok / len(records),
        "tool_name_accuracy": sum(1 for r in records if r.tool_name_ok) / len(records),
        "args_accuracy": sum(1 for r in records if r.args_ok) / len(records),
        "by_level": by_level,
    }


def score_prediction(
    *,
    qa: Mapping[str, Any],
    predicted_name: str | None,
    predicted_arguments: Mapping[str, Any] | None,
    failure: str | None = None,
) -> ScoreRecord:
    """Score one prediction against gold tool_call."""
    gold = qa["tool_call"]
    gold_name = str(gold["name"])
    gold_arguments = dict(gold.get("arguments") or {})
    level = str((qa.get("complexity_metadata") or {}).get("level") or "L?")
    if failure:
        return ScoreRecord(
            qa_id=str(qa["qa_id"]),
            level=level,
            tool_name_ok=False,
            args_ok=False,
            item_ok=False,
            gold_name=gold_name,
            predicted_name=predicted_name,
            gold_arguments=gold_arguments,
            predicted_arguments=dict(predicted_arguments or {})
            if predicted_arguments
            else None,
            failure=failure,
        )
    tool_name_ok, args_ok, item_ok = score_tool_call(
        gold_name=gold_name,
        gold_arguments=gold_arguments,
        predicted_name=predicted_name,
        predicted_arguments=predicted_arguments,
    )
    return ScoreRecord(
        qa_id=str(qa["qa_id"]),
        level=level,
        tool_name_ok=tool_name_ok,
        args_ok=args_ok,
        item_ok=item_ok,
        gold_name=gold_name,
        predicted_name=predicted_name,
        gold_arguments=gold_arguments,
        predicted_arguments=dict(predicted_arguments or {})
        if predicted_arguments
        else None,
        failure=None if item_ok else "mismatch",
    )


__all__ = [
    "BenchmarkRunError",
    "DatasetError",
    "load_manifest",
    "validate_manifest",
    "load_session_map",
    "prepare_run",
    "summarize_scores",
    "score_prediction",
    "repository_revision",
]
