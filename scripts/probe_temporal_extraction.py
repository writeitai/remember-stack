"""Run synthetic E2 temporal cases using the configured development provider key."""

import argparse
from datetime import datetime
from datetime import UTC
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from rememberstack.adapters.openrouter import OpenRouterModelProvider
from rememberstack.adapters.openrouter import OpenRouterSettings
from rememberstack.model import CandidateClaim
from rememberstack.model import ChunkForEmbedding
from rememberstack.model import ChunkSource
from rememberstack.model import ClaimifyResponse
from rememberstack.model import ModelRequest
from rememberstack.workers.e2 import _CLAIMIFY_PROMPT
from rememberstack.workers.e2 import _grounded_claim
from rememberstack.workers.e2 import _header_text
from rememberstack.workers.e2 import _parse_claim_valid_time
from rememberstack.workers.e2 import GroundingRejection

_PROBE_ID = UUID("7e000000-0000-0000-0000-000000000001")


def _probe_source(*, source_timestamp: str | None) -> ChunkSource:
    """A one-chunk synthetic source whose header date is the case timestamp.

    A missing, empty, or literal ``unknown`` timestamp renders the header's
    ``date unknown`` form, the no-anchor situation the prompt must handle.
    """
    modified = (
        datetime.fromisoformat(source_timestamp).astimezone(UTC)
        if source_timestamp and source_timestamp != "unknown"
        else None
    )
    return ChunkSource(
        deployment_id=_PROBE_ID,
        doc_id=_PROBE_ID,
        version_id=_PROBE_ID,
        representation_id=_PROBE_ID,
        markdown_uri="mem://probe.md",
        blocks_uri="mem://probe.json",
        title="Synthetic timing probe",
        source_kind="upload",
        source_modified_at=modified,
        published_at=None,
        language="en",
        structurer_version="probe",
        sections=(),
    )


def _probe_chunk(*, source: str) -> ChunkForEmbedding:
    """One chunk spanning the whole synthetic source text."""
    return ChunkForEmbedding(
        chunk_id=_PROBE_ID,
        doc_id=_PROBE_ID,
        version_id=_PROBE_ID,
        ordinal=0,
        char_start=0,
        char_end=len(source),
        chunk_content_hash="sha256:probe",
        extraction_input_hash="sha256:probe-in",
        section_role="body",
        section_path="0",
        context_prefix=None,
        prefixer_version="probe",
    )


def _gate_verdict(
    *, claim: CandidateClaim, source: str, source_timestamp: str | None
) -> dict[str, Any]:
    """Run the real D32 grounding gate on one live model claim."""
    chunk_source = _probe_source(source_timestamp=source_timestamp)
    chunk = _probe_chunk(source=source)
    result = _grounded_claim(
        candidate=claim,
        source=chunk_source,
        chunk=chunk,
        chunks=(chunk,),
        index=0,
        document_md=source,
        flagged_spans=set(),
        kept_ranges=((0, len(source)),),
    )
    if isinstance(result, GroundingRejection):
        return {
            "accepted": False,
            "gate": result.gate.value,
            "failed_tokens": list(result.failed_tokens),
        }
    return {"accepted": True}


def _text_matches(*, claim_text: str, case: dict[str, Any]) -> bool:
    """Apply the optional claim-text expectations of one case."""
    contains = case.get("expected_text_contains", [])
    excludes = case.get("expected_text_excludes", [])
    return all(fragment in claim_text for fragment in contains) and not any(
        fragment in claim_text for fragment in excludes
    )


def run_probe(*, cases_path: Path, output_path: Path, model: str) -> int:
    """Evaluate endpoints, kind, precision, claim text, and the grounding gate.

    A case passes when at least one returned claim matches every expected
    ``kind``/``precision``/``start``/``end`` value, satisfies the optional
    ``expected_text_contains`` / ``expected_text_excludes`` fragments, and is
    accepted by the deterministic D32 grounding gate exactly as E2 applies it.
    Saves synthetic outputs, gate verdicts, and usage.
    """
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
        source_timestamp = case.get("source_timestamp")
        header = _header_text(source=_probe_source(source_timestamp=source_timestamp))
        bundle = f"DOCUMENT HEADER: {header}\nTARGET CHUNK:\n{source}"
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
                    "gate": _gate_verdict(
                        claim=claim, source=source, source_timestamp=source_timestamp
                    ),
                    "text_matches": _text_matches(
                        claim_text=claim.claim_text, case=case
                    ),
                }
            )
        matched = any(
            all(
                row[f"parsed_{field}"] == case.get(f"expected_{field}")
                for field in ("kind", "precision", "start", "end")
            )
            and row["text_matches"]
            and row["gate"]["accepted"]
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
