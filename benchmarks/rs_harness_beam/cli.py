"""CLI for BEAM harness scoring (containment placeholder + official nugget judge)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def main(argv: list[str] | None = None) -> int:
    """Entrypoint."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "score":
        return _score_containment(args)
    if args.command == "score-official":
        return _score_official(args)
    if args.command == "answer-retrieval":
        return _answer_retrieval(args)
    parser.print_help()
    return 2


def _answer_retrieval(args: argparse.Namespace) -> int:
    """Answer BEAM probes using the full recipe + open-query retrieval plane."""
    import os

    from benchmarks.rs_harness_beam.answer_agent import answer_run_dir
    from benchmarks.rs_harness_beam.answer_agent import AnswerAgentError

    api_key = args.api_key or os.environ.get(  # noqa: TID251 — CLI boundary only
        "REMEMBERSTACK_OPENROUTER_API_KEY"
    )
    if not api_key:
        print(
            "error: pass --api-key or set REMEMBERSTACK_OPENROUTER_API_KEY",
            file=sys.stderr,
        )
        return 1
    try:
        summary = answer_run_dir(
            run_dir=args.run,
            api_url=args.api_url,
            api_key=api_key,
            arm=args.arm,
            model=args.model,
            force=args.force,
        )
    except AnswerAgentError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


def _score_containment(args: argparse.Namespace) -> int:
    """Legacy substring/containment placeholder scorer."""
    run_dir = Path(args.run)
    questions = {
        (q.get("question_id") or q.get("item_id")): q
        for q in json.loads((run_dir / "questions.json").read_text(encoding="utf-8"))
    }
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "protocol": "RS-Harness-BEAM-v1",
        "note": "containment scorer; use score-official for BEAM paper metric",
        "surfaces_expected_on_rs": ["P1_mcp", "P2_mcp", "P3_mount", "K1_mount"],
        "arms": {},
    }
    for arm, answers in (state.get("answers") or {}).items():
        ok = 0
        n = 0
        rows: list[dict[str, Any]] = []
        for qid, payload in (answers or {}).items():
            gold = questions[qid].get("answer") or questions[qid].get("gold")
            pred = (payload or {}).get("answer") or ""
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
    summary = {
        arm: {k: v for k, v in data.items() if k != "items"}
        for arm, data in report["arms"].items()
    }
    print(json.dumps(summary, indent=2))
    print(f"wrote {out}")
    return 0


def _score_official(args: argparse.Namespace) -> int:
    """BEAM official nugget LLM-judge (+ Kendall τ-b for event ordering)."""
    import os

    from benchmarks.rs_harness_beam.official_score import OfficialScoreError
    from benchmarks.rs_harness_beam.official_score import score_run_dir_official

    api_key = args.api_key or os.environ.get(  # noqa: TID251 — CLI boundary only
        "REMEMBERSTACK_OPENROUTER_API_KEY"
    )
    if not api_key:
        print(
            "error: pass --api-key or set REMEMBERSTACK_OPENROUTER_API_KEY",
            file=sys.stderr,
        )
        return 1
    try:
        report = score_run_dir_official(
            run_dir=Path(args.run),
            api_key=api_key,
            arm=args.arm,
            rubrics_path=Path(args.rubrics) if args.rubrics else None,
            judge_model=args.judge_model,
        )
    except OfficialScoreError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    summary = {
        "overall_mean": report["overall_mean"],
        "n": report["n"],
        "by_ability": report["by_ability"],
        "report_path": report["report_path"],
        "scorer": report["scorer"],
        "judge_model": report["judge_model"],
    }
    print(json.dumps(summary, indent=2))
    print(f"wrote {report['report_path']}")
    return 0


def _match(*, gold: object, pred: str) -> bool:
    """Substring / containment match (placeholder scorer)."""
    if gold is None:
        return False
    if isinstance(gold, list):
        return any(_match(gold=item, pred=pred) for item in gold)
    g = str(gold).strip().lower()
    p = pred.strip().lower()
    if not g or not p:
        return False
    if "no information" in g or "not mentioned" in g or "no evidence" in g:
        if p in {"n/a", "none", "unknown"} or "no information" in p or "not" in p:
            return True
    return g in p or p in g


def _parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(prog="python -m benchmarks.rs_harness_beam")
    sub = parser.add_subparsers(dest="command")
    score = sub.add_parser("score", help="containment placeholder scorer")
    score.add_argument("--run", required=True)
    answer = sub.add_parser(
        "answer-retrieval",
        help="answer probes via full retrieval plane (recipes + open query)",
    )
    answer.add_argument("--run", required=True)
    answer.add_argument("--api-url", default="http://127.0.0.1:18000")
    answer.add_argument("--arm", default="rs")
    answer.add_argument("--model", default="openai/gpt-5.6-luna")
    answer.add_argument("--force", action="store_true")
    answer.add_argument("--api-key", default=None)
    official = sub.add_parser(
        "score-official",
        help="BEAM paper scorer (nugget LLM-judge + Kendall τ-b for event_ordering)",
    )
    official.add_argument("--run", required=True)
    official.add_argument("--arm", default="rs")
    official.add_argument(
        "--rubrics",
        default=None,
        help="path to BEAM probing_questions.json (default: smoke 100K/1 fixture)",
    )
    official.add_argument(
        "--judge-model",
        default="openai/gpt-5.6-luna",
        help="OpenRouter model id for the LLM judge",
    )
    official.add_argument(
        "--api-key",
        default=None,
        help="OpenRouter API key (else REMEMBERSTACK_OPENROUTER_API_KEY)",
    )
    return parser
