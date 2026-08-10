"""Unit tests for BEAM full-plane answer agent helpers (no live API)."""

from __future__ import annotations

from benchmarks.rs_harness_beam.answer_agent import _extract_answer
from benchmarks.rs_harness_beam.answer_agent import _ingest_envelope
from benchmarks.rs_harness_beam.answer_agent import _is_pointer_answer
from benchmarks.rs_harness_beam.answer_agent import _name_candidates
from benchmarks.rs_harness_beam.answer_agent import _recover_ordered_list
from benchmarks.rs_harness_beam.answer_agent import RetrievalBundle


def test_extract_answer_prefers_answer_line() -> None:
    """Final ANSWER: line wins over earlier prose."""
    raw = "Reasoning about sprint dates.\nANSWER: March 29\n"
    assert _extract_answer(raw) == "March 29"


def test_extract_answer_handles_inline_answer_token() -> None:
    """Some models emit 'March 29. ANSWER: March 29.' on one line."""
    raw = "March 29. ANSWER: March 29."
    assert _extract_answer(raw) == "March 29."


def test_extract_answer_recovers_from_listed_above() -> None:
    """Pointer answers fall back to a numbered list in the body."""
    raw = (
        "1) authentication and expense tracking\n"
        "2) transaction CRUD and analytics\n"
        "3) category budgets\n"
        "ANSWER: Listed above.\n"
    )
    recovered = _extract_answer(raw)
    assert "authentication" in recovered
    assert "CRUD" in recovered
    assert not _is_pointer_answer(recovered)


def test_is_pointer_answer_detects_deferrals() -> None:
    """Common deferral phrases are not valid final answers."""
    assert _is_pointer_answer("Listed above.")
    assert _is_pointer_answer("see above")
    assert not _is_pointer_answer("March 29")


def test_recover_ordered_list_requires_two_items() -> None:
    """A single bullet is not treated as an ordered reconstruction."""
    assert _recover_ordered_list("1) only one") is None
    assert _recover_ordered_list("1) a\n2) b") is not None


def test_name_candidates_include_domain_tokens() -> None:
    """BEAM questions surface Flask/sprint-style tokens even without Title Case."""
    names = _name_candidates(
        "Have I worked with Flask routes and handled HTTP requests?"
    )
    assert "Flask" in names
    assert "HTTP" in names


def test_ingest_envelope_folds_evidence_facts_entities() -> None:
    """Operation envelopes map into the retrieval bundle fields."""
    bundle = RetrievalBundle()
    _ingest_envelope(
        bundle,
        operation="testimony_context",
        envelope={
            "grain": "evidence",
            "negative": None,
            "evidence": [{"claim_text": "The first sprint ends on March 29."}],
            "facts": [{"label": "sprint ends March 29"}],
            "chunks": [{"chunk_text": "sprint ends on March 29", "context_prefix": ""}],
            "entities": [
                {
                    "entity_id": "11111111-1111-4111-8111-111111111111",
                    "canonical_name": "Sprint",
                    "entity_type": "concept",
                }
            ],
            "edges": [],
            "paths": [],
            "pages": [],
            "ranking": [],
            "sources": [],
        },
    )
    assert bundle.claims == ["The first sprint ends on March 29."]
    assert bundle.facts == ["sprint ends March 29"]
    assert any("March 29" in chunk for chunk in bundle.chunks)
    assert len(bundle.entities) == 1
    assert bundle.operation_calls[0]["operation"] == "testimony_context"


def test_cli_registers_answer_retrieval_command() -> None:
    """answer-retrieval is a first-class subcommand."""
    from benchmarks.rs_harness_beam.cli import _parser

    parser = _parser()
    args = parser.parse_args(
        [
            "answer-retrieval",
            "--run",
            "/tmp/run",
            "--api-url",
            "http://127.0.0.1:18000",
            "--force",
        ]
    )
    assert args.command == "answer-retrieval"
    assert args.force is True
    assert args.api_url == "http://127.0.0.1:18000"
