"""Prepare runs and stratified LongMemEval manifests."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from benchmarks.rs_harness_longmemeval.dataset import ability_label
from benchmarks.rs_harness_longmemeval.dataset import DatasetError
from benchmarks.rs_harness_longmemeval.dataset import find_dataset_files
from benchmarks.rs_harness_longmemeval.dataset import history_text
from benchmarks.rs_harness_longmemeval.dataset import item_id
from benchmarks.rs_harness_longmemeval.dataset import load_items
from benchmarks.rs_harness_longmemeval.protocol import ADAPTER_VERSION
from benchmarks.rs_harness_longmemeval.protocol import AGENT_TASK_PREAMBLE
from benchmarks.rs_harness_longmemeval.protocol import PROTOCOL_NAME
from benchmarks.rs_harness_longmemeval.protocol import TIER_COUNTS

MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"
REPO_ROOT = Path(__file__).resolve().parents[2]


class BenchmarkRunError(ValueError):
    """Prepare/run failure."""


def repository_revision() -> str:
    """Adapter repo HEAD."""
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def build_stratified_ids(
    items: list[dict[str, Any]], *, n: int
) -> list[str]:
    """Round-robin sample by ability for a fixed n."""
    by_ability: dict[str, list[str]] = defaultdict(list)
    for item in items:
        by_ability[ability_label(item)].append(item_id(item))
    for ability in by_ability:
        by_ability[ability].sort()
    abilities = sorted(by_ability)
    out: list[str] = []
    idx = {a: 0 for a in abilities}
    while len(out) < n and any(idx[a] < len(by_ability[a]) for a in abilities):
        for ability in abilities:
            if idx[ability] < len(by_ability[ability]) and len(out) < n:
                out.append(by_ability[ability][idx[ability]])
                idx[ability] += 1
    return sorted(out)


def write_manifests(*, dataset_root: Path) -> dict[str, Any]:
    """Write smoke/dev/pub manifests from a local dataset tree."""
    path = find_dataset_files(dataset_root)
    items = load_items(path)
    if len(items) < TIER_COUNTS["smoke"]:
        raise DatasetError(f"only {len(items)} items; need more for smoke")
    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    meta: dict[str, Any] = {
        "dataset_path": str(path.resolve()),
        "dataset_sha256": file_hash,
        "item_count_total": len(items),
        "tiers": {},
    }
    for tier, n in TIER_COUNTS.items():
        take = min(n, len(items))
        ids = build_stratified_ids(items, n=take)
        blob = json.dumps(ids, separators=(",", ":")).encode()
        payload = {
            "version": 1,
            "tier": tier,
            "protocol": PROTOCOL_NAME,
            "dataset_sha256": file_hash,
            "item_ids": ids,
            "item_ids_sha256": hashlib.sha256(blob).hexdigest(),
            "item_count": len(ids),
        }
        (MANIFEST_DIR / f"{tier}.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )
        meta["tiers"][tier] = {
            "item_count": len(ids),
            "item_ids_sha256": payload["item_ids_sha256"],
        }
    (MANIFEST_DIR / "dataset_meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def prepare_run(
    *,
    output: Path,
    tier: str,
    dataset_root: Path,
    rebuild_manifests: bool = True,
) -> dict[str, Any]:
    """Materialize documents and questions for one run directory."""
    if rebuild_manifests or not (MANIFEST_DIR / f"{tier}.json").is_file():
        write_manifests(dataset_root=dataset_root)
    manifest = json.loads((MANIFEST_DIR / f"{tier}.json").read_text(encoding="utf-8"))
    path = find_dataset_files(dataset_root)
    items = {item_id(item): item for item in load_items(path)}
    selected = []
    for qid in manifest["item_ids"]:
        if qid not in items:
            raise BenchmarkRunError(f"manifest id missing from dataset: {qid}")
        selected.append(items[qid])

    if output.exists() and any(output.iterdir()):
        raise BenchmarkRunError(f"output not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    docs = output / "documents"
    docs.mkdir()
    questions = []
    for item in selected:
        qid = item_id(item)
        history = history_text(item)
        (docs / f"{qid}.md").write_text(
            f"# LongMemEval history `{qid}`\n\n{history}",
            encoding="utf-8",
        )
        questions.append(
            {
                "question_id": qid,
                "question": item.get("question") or item.get("query"),
                "answer": item.get("answer") or item.get("gold_answer"),
                "ability": ability_label(item),
                "document_path": f"documents/{qid}.md",
            }
        )
    run = {
        "protocol": PROTOCOL_NAME,
        "adapter_version": ADAPTER_VERSION,
        "tier": tier,
        "repository_revision": repository_revision(),
        "dataset_sha256": manifest["dataset_sha256"],
        "item_ids_sha256": manifest["item_ids_sha256"],
        "item_ids": manifest["item_ids"],
        "agent_preamble": AGENT_TASK_PREAMBLE,
        "dataset_root": str(dataset_root.resolve()),
    }
    (output / "run.json").write_text(json.dumps(run, indent=2) + "\n", encoding="utf-8")
    (output / "questions.json").write_text(
        json.dumps(questions, indent=2) + "\n", encoding="utf-8"
    )
    (output / "state.json").write_text(
        json.dumps(
            {
                "phase": "prepared",
                "answers": {"bare": {}, "rs": {}},
                "surfaces": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Workspace templates for Claude Code
    ws = output / "workspaces"
    (ws / "bare").mkdir(parents=True)
    (ws / "rs").mkdir(parents=True)
    (ws / "bare" / "PROMPT_PREFIX.md").write_text(
        AGENT_TASK_PREAMBLE
        + "\n\nRememberStack is **not** available. Answer from general knowledge only "
        "if the question allows; otherwise Unknown.\n",
        encoding="utf-8",
    )
    (ws / "rs" / "PROMPT_PREFIX.md").write_text(
        AGENT_TASK_PREAMBLE
        + "\n\nRememberStack **is** available via MCP tools and mounts under "
        "`mounts/p3` (corpus filesystem) and `mounts/k` (Plane K / K1 pages). "
        "You must use them before answering.\n",
        encoding="utf-8",
    )
    return run


def build_cc_prompt(*, arm: str, question: str, preamble: str) -> str:
    """Single-shot prompt text for `claude -p`."""
    return (
        f"{preamble.strip()}\n\n"
        f"## Arm\n`{arm}`\n\n"
        f"## Question\n{question.strip()}\n\n"
        f"## Output\n"
        f"Reply with ONLY the final short answer on the last line, "
        f"prefixed by `ANSWER:`.\n"
    )
