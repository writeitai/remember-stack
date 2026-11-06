"""Child-process entry for one supervised Codex task session."""

from __future__ import annotations

import argparse
from pathlib import Path

from benchmarks.workspacebench.codex import ArmConfiguration
from benchmarks.workspacebench.codex import CodexTaskRequest
from benchmarks.workspacebench.codex import run_codex_task
from benchmarks.workspacebench.hashing import atomic_write_json


def main(argv: list[str] | None = None) -> int:
    """Load a request envelope and write the Codex turn JSON."""
    parser = argparse.ArgumentParser(prog="python -m benchmarks.workspacebench.session")
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    payload = _load(args.request)
    request = CodexTaskRequest(
        prompt=str(payload["prompt"]),
        workspace=Path(str(payload["workspace"])),
        model=str(payload["model"]),
        reasoning_effort=str(payload["reasoning_effort"]),
        arm=ArmConfiguration.model_validate(payload["arm"]),
        timeout_seconds=_as_float(payload["timeout_seconds"]),
        grace_seconds=_as_float(payload["grace_seconds"]),
    )
    turn = run_codex_task(request=request)
    atomic_write_json(
        path=args.output,
        value={
            "status": turn.status,
            "error_message": turn.error_message,
            "final_response": turn.final_response,
            "tokens_in": turn.tokens_in,
            "tokens_out": turn.tokens_out,
            "item_types": list(turn.item_types),
            "events": [
                {"item_type": event.item_type, "payload": event.payload}
                for event in turn.events
            ],
            "timed_out": turn.timed_out,
        },
    )
    return 0


def _load(path: Path) -> dict[str, object]:
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("session request must be a JSON object")
    return payload


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SystemExit("timeout values must be numbers")
    return float(value)


if __name__ == "__main__":
    raise SystemExit(main())
