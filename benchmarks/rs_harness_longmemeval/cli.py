"""CLI for prepare / ingest / run_cc / score."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from benchmarks.rs_harness_longmemeval.dataset import DatasetError
from benchmarks.rs_harness_longmemeval.runner import BenchmarkRunError
from benchmarks.rs_harness_longmemeval.runner import build_cc_prompt
from benchmarks.rs_harness_longmemeval.runner import prepare_run
from benchmarks.rs_harness_longmemeval.runner import write_manifests


def main(argv: list[str] | None = None) -> int:
    """Entrypoint."""
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            run = prepare_run(
                output=Path(args.output),
                tier=args.tier,
                dataset_root=Path(args.dataset_root),
                rebuild_manifests=not args.skip_manifest_rebuild,
            )
            print(json.dumps(run, indent=2))
            return 0
        if args.command == "write-manifests":
            meta = write_manifests(dataset_root=Path(args.dataset_root))
            print(json.dumps(meta, indent=2))
            return 0
        if args.command == "ingest":
            return _ingest(args)
        if args.command == "run_cc":
            return _run_cc(args)
        if args.command == "score":
            return _score(args)
    except (BenchmarkRunError, DatasetError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _ingest(args: argparse.Namespace) -> int:
    """Ingest prepared documents through `remember ingest` / HTTP."""
    if not args.execute:
        print("dry-run: pass --execute to upload documents")
        return 0
    run_dir = Path(args.run)
    docs = sorted((run_dir / "documents").glob("*.md"))
    if not docs:
        raise BenchmarkRunError("no documents to ingest")
    api = args.api_url.rstrip("/")
    for path in docs:
        cmd = [
            "uv",
            "run",
            "remember",
            "ingest",
            str(path),
            "--mime",
            "text/markdown",
            "--source-kind",
            "longmemeval",
            "--source-ref",
            path.stem,
            "--title",
            path.stem,
        ]
        env = {**dict(**{k: v for k, v in __import__("os").environ.items()})}
        env["REMEMBERSTACK_API_URL"] = api
        print(f"ingesting {path.name} …")
        completed = subprocess.run(cmd, env=env, check=False)
        if completed.returncode != 0:
            raise BenchmarkRunError(f"ingest failed for {path}")
    print(f"ingested {len(docs)} documents; drain workers then run projections + K")
    return 0


def _run_cc(args: argparse.Namespace) -> int:
    """Invoke Claude Code once per question for one arm."""
    if not args.execute:
        print("dry-run: pass --execute to call `claude -p`")
        return 0
    run_dir = Path(args.run)
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    questions = json.loads((run_dir / "questions.json").read_text(encoding="utf-8"))
    state_path = run_dir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    arm = args.arm
    preamble = (run_dir / "workspaces" / arm / "PROMPT_PREFIX.md").read_text(
        encoding="utf-8"
    )
    out_dir = run_dir / "answers" / arm
    out_dir.mkdir(parents=True, exist_ok=True)
    cwd = (run_dir / "workspaces" / arm).resolve()
    limit = int(getattr(args, "limit", 0) or 0)
    if limit > 0:
        questions = questions[:limit]
    for item in questions:
        qid = item["question_id"]
        if qid in state["answers"].get(arm, {}) and not args.force:
            print(f"skip {qid} (already answered)")
            continue
        prompt = build_cc_prompt(
            arm=arm, question=str(item["question"]), preamble=preamble
        )
        prompt_path = out_dir / f"{qid}.prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        # Claude Code: -p prompt; --dangerously-skip-permissions for batch
        cmd = [
            "claude",
            "--dangerously-skip-permissions",
            "-p",
            prompt,
        ]
        print(f"claude arm={arm} id={qid} …")
        started = time.time()
        completed = subprocess.run(
            cmd,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        elapsed = time.time() - started
        transcript = (completed.stdout or "") + (completed.stderr or "")
        (out_dir / f"{qid}.raw.txt").write_text(transcript, encoding="utf-8")
        answer = _extract_answer(transcript)
        state["answers"].setdefault(arm, {})[qid] = {
            "answer": answer,
            "exit_code": completed.returncode,
            "elapsed_seconds": elapsed,
            "raw_path": str(out_dir / f"{qid}.raw.txt"),
        }
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        print(f"  -> {answer!r} ({elapsed:.1f}s exit={completed.returncode})")
    return 0


def _score(args: argparse.Namespace) -> int:
    """Simple exact/substring score vs gold (placeholder before official scorer)."""
    run_dir = Path(args.run)
    questions = {
        q["question_id"]: q
        for q in json.loads((run_dir / "questions.json").read_text(encoding="utf-8"))
    }
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {"arms": {}}
    for arm, answers in state.get("answers", {}).items():
        ok = 0
        n = 0
        rows = []
        for qid, payload in answers.items():
            gold = questions[qid].get("answer")
            pred = payload.get("answer") or ""
            n += 1
            hit = _match(gold=gold, pred=pred)
            ok += int(hit)
            rows.append(
                {
                    "question_id": qid,
                    "ability": questions[qid].get("ability"),
                    "gold": gold,
                    "pred": pred,
                    "ok": hit,
                }
            )
        report["arms"][arm] = {
            "n": n,
            "ok": ok,
            "accuracy": (ok / n) if n else 0.0,
            "items": rows,
        }
    out = run_dir / "score_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "items"} for k, v in report["arms"].items()}, indent=2))
    print(f"wrote {out}")
    return 0


def _extract_answer(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip().upper().startswith("ANSWER:"):
            return line.split(":", 1)[1].strip()
    # fallback: last non-empty line
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def _match(*, gold: object, pred: str) -> bool:
    if gold is None:
        return False
    if isinstance(gold, list):
        return any(_match(gold=g, pred=pred) for g in gold)
    g = str(gold).strip().lower()
    p = pred.strip().lower()
    if not g or not p:
        return False
    return g in p or p in g


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m benchmarks.rs_harness_longmemeval")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("prepare")
    p.add_argument("--output", required=True)
    p.add_argument("--tier", choices=("smoke", "development", "publication"), required=True)
    p.add_argument("--dataset-root", required=True)
    p.add_argument("--skip-manifest-rebuild", action="store_true")

    w = sub.add_parser("write-manifests")
    w.add_argument("--dataset-root", required=True)

    i = sub.add_parser("ingest")
    i.add_argument("--run", required=True)
    i.add_argument("--api-url", default="http://127.0.0.1:8000")
    i.add_argument("--execute", action="store_true")
    i.add_argument("--confirm-isolated-deployment", default="")

    r = sub.add_parser("run_cc")
    r.add_argument("--run", required=True)
    r.add_argument("--arm", choices=("bare", "rs"), required=True)
    r.add_argument("--execute", action="store_true")
    r.add_argument("--force", action="store_true")
    r.add_argument(
        "--limit",
        type=int,
        default=0,
        help="max questions (0 = all); use 1–2 for local Max smoke",
    )

    s = sub.add_parser("score")
    s.add_argument("--run", required=True)
    return parser


# late import for type
from typing import Any  # noqa: E402
