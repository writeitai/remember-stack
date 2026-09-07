"""Run synthetic E2 temporal cases using the configured development provider key."""

import argparse
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import Any

from rememberstack.adapters.openrouter import OpenRouterModelProvider
from rememberstack.adapters.openrouter import OpenRouterSettings
from rememberstack.model import ClaimifyResponse
from rememberstack.model import ModelRequest
from rememberstack.workers.e2 import _CLAIMIFY_PROMPT
from rememberstack.workers.e2 import _parse_claim_valid_time


def run_probe(*, cases_path: Path, output_path: Path, model: str) -> int:
    """Evaluate both endpoints, kind, and precision; save synthetic outputs and usage."""
    fixtures = json.loads(cases_path.read_text())
    provider = OpenRouterModelProvider(
        settings=OpenRouterSettings.model_validate(
            {
                "max_completion_tokens": 4096,
                "timeout_s": 120,
                "reasoning_effort": "none",
            }
        )
    )
    records: list[dict[str, Any]] = []
    for case in fixtures["cases"]:
        source = case["source"]
        bundle = (
            "DOCUMENT HEADER: title Synthetic timing probe; source upload;"
            f" date {case['source_timestamp']}; language en\nTARGET CHUNK:\n{source}"
        )
        result = provider.generate(
            request=ModelRequest(
                model=model,
                temperature=0.0,
                prompt=_CLAIMIFY_PROMPT.format(keeps=f"- {source}", bundle=bundle),
            ),
            response_type=ClaimifyResponse,
        )
        outputs = []
        for claim in result.output.claims:
            start, end, precision, kind = _parse_claim_valid_time(candidate=claim)
            outputs.append(
                {
                    "claim": claim.model_dump(mode="json"),
                    "parsed_start": start.isoformat() if start else None,
                    "parsed_end": end.isoformat() if end else None,
                    "parsed_precision": precision.value,
                    "parsed_kind": kind.value if kind else None,
                }
            )
        matched = any(
            all(
                row[f"parsed_{field}"] == case[f"expected_{field}"]
                for field in ("kind", "precision", "start", "end")
            )
            for row in outputs
        )
        record = {
            key: value
            for key, value in case.items()
            if key not in {"outputs", "usage", "matched"}
        }
        record.update(
            matched=matched, outputs=outputs, usage=result.usage.model_dump(mode="json")
        )
        records.append(record)
        print(case["case"], "PASS" if matched else "FAIL", flush=True)
        output_path.write_text(
            json.dumps(
                {
                    "model": model,
                    "date": datetime.now(UTC).date().isoformat(),
                    "cases": records,
                },
                indent=2,
            )
            + "\n"
        )
    return 0 if all(record["matched"] for record in records) else 1


def main() -> int:
    """Accept explicit fixture/output paths; credentials use standard adapter settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="openai/gpt-5.6-luna")
    args = parser.parse_args()
    return run_probe(cases_path=args.cases, output_path=args.output, model=args.model)


if __name__ == "__main__":
    raise SystemExit(main())
