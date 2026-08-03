"""Unit tests for the D80 deterministic embedding-input policy."""

from uuid import uuid4

from rememberstack.core.embedding_input_policy import EMBEDDING_INPUT_POLICY_VERSION
from rememberstack.core.embedding_input_policy import EmbedHeaderMode
from rememberstack.core.embedding_input_policy import LocationFacts
from rememberstack.core.embedding_input_policy import render_embedding_input


def _facts(**overrides: object) -> LocationFacts:
    base = {
        "chunk_id": uuid4(),
        "doc_id": uuid4(),
        "version_id": uuid4(),
        "title": "Project Atlas memo",
        "source_kind": "upload",
        "source_shape": "document",
        "section_title": "Results",
        "section_path": "0.1",
        "section_role": "body",
        "chunk_count": 5,
    }
    base.update(overrides)
    return LocationFacts.model_validate(base)


def test_multi_chunk_document_gets_location_header() -> None:
    """Long multi-chunk docs include a deterministic location header."""
    body = (
        "Project Atlas launched in three markets last year with a careful rollout "
        "plan and subsequent measurement of adoption across each region."
    )
    rendered = render_embedding_input(facts=_facts(), body=body)
    assert rendered.mode is EmbedHeaderMode.LOCATION_HEADER
    assert rendered.location_header is not None
    assert "Project Atlas memo" in rendered.location_header
    assert "Results" in rendered.location_header
    assert rendered.embedding_text.startswith(rendered.location_header)
    assert rendered.body in rendered.embedding_text
    assert rendered.policy_generation == EMBEDDING_INPUT_POLICY_VERSION
    assert any(
        element.kind.value == "document_title" for element in rendered.location_elements
    )


def test_short_message_atom_without_coords_is_body_only() -> None:
    """Short message atoms without connector coords embed body only."""
    rendered = render_embedding_input(
        facts=_facts(
            source_shape="message_atom",
            title=None,
            section_title=None,
            section_path="0",
            chunk_count=1,
            channel_ref=None,
            author_ref=None,
        ),
        body="yes, ship it",
    )
    assert rendered.mode is EmbedHeaderMode.BODY_ONLY
    assert rendered.location_header is None
    assert rendered.embedding_text == "yes, ship it"


def test_short_message_atom_with_channel_gets_compact_header() -> None:
    """Provisional D80 default: short atoms with coords use a compact header."""
    rendered = render_embedding_input(
        facts=_facts(
            source_shape="message_atom",
            chunk_count=1,
            section_title=None,
            title=None,
            channel_ref="C123",
            author_ref="U9",
            message_ts="2026-08-01T12:00:00Z",
        ),
        body="yes, ship it",
    )
    assert rendered.mode is EmbedHeaderMode.LOCATION_HEADER
    assert rendered.location_header is not None
    assert "C123" in rendered.location_header
    # Whole-field fit under H_MAX — never mid-field "Time: 2026-"
    assert "Time: 2026-" not in (rendered.location_header + "x")
    assert rendered.embedding_text.endswith("yes, ship it")


def test_header_drops_whole_fields_not_mid_slice() -> None:
    """Long field sets shrink by omitting trailing fields entirely."""
    rendered = render_embedding_input(
        facts=_facts(
            title="Quarterly business review for the enterprise division",
            section_title="Detailed results and regional breakdown",
            channel_ref="C-ENGINEERING-ALERTS",
            author_ref="U-ALICE-VERY-LONG",
            message_ts="2026-08-01T12:00:00.000000Z",
            chunk_count=3,
        ),
        body="A" * 80,
    )
    header = rendered.location_header or ""
    assert "Document:" in header or "Channel:" in header
    # No partial field endings from code-point slicing.
    assert not header.endswith((": ", "for th", "2026-"))


def test_empty_body_skips() -> None:
    """Empty bodies do not embed."""
    rendered = render_embedding_input(facts=_facts(), body="   \n")
    assert rendered.skip_reason == "empty_body"
    assert rendered.embedding_text == ""


def test_header_never_includes_summary_text() -> None:
    """Summaries are not location-fact fields and cannot appear in headers."""
    rendered = render_embedding_input(
        facts=_facts(),
        body="A body long enough that the full header path may apply " * 3,
    )
    assert rendered.location_header is not None
    assert "summary" not in rendered.location_header.lower()
