"""WP-6.3 pure deterministic fact-sheet rendering proofs."""

from datetime import datetime
from datetime import UTC
import re
from uuid import UUID

import pytest

from rememberstack.core import compose_knowledge_page
from rememberstack.core import render_knowledge_fact_sheet
from rememberstack.model import KnowledgeFactFingerprint
from rememberstack.model import KnowledgeFactSheetFact
from rememberstack.model import KnowledgeFactSheetSnapshot
from rememberstack.model import KnowledgeInputSnapshot

_ARTIFACT_ID = UUID("63000000-0000-0000-0000-000000000001")
_DEPLOYMENT_ID = UUID("63000000-0000-0000-0000-000000000002")
_GROUP_ID = UUID("63000000-0000-0000-0000-000000000003")
_AS_OF = datetime(2026, 7, 20, 12, tzinfo=UTC)
_COMPILED_AT = datetime(2026, 7, 20, 12, 5, tzinfo=UTC)


def _fact(
    *,
    kind: str,
    suffix: int,
    label: str,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    invalidated_at: datetime | None = None,
    evidence_count: int = 1,
    contradiction_group: UUID | None = None,
) -> KnowledgeFactSheetFact:
    """Build one display fact with stable IDs and timestamps."""
    return KnowledgeFactSheetFact.model_validate(
        {
            "kind": kind,
            "fact_id": UUID(f"63000000-0000-0000-0000-{suffix:012d}"),
            "label": label,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "valid_precision": "day"
            if valid_from is not None or valid_until is not None
            else "unknown",
            "ingested_at": datetime(2026, 1, 1, tzinfo=UTC),
            "invalidated_at": invalidated_at,
            "evidence_count": evidence_count,
            "contradict_count": 0,
            "contradiction_group": contradiction_group,
        }
    )


def _snapshot() -> tuple[
    KnowledgeFactSheetSnapshot, tuple[KnowledgeFactSheetFact, ...]
]:
    """Return a mixed current/history/future/contradiction candidate set."""
    facts = (
        _fact(
            kind="relation",
            suffix=11,
            label="Alice | works for Acme",
            valid_from=datetime(2024, 3, 1, tzinfo=UTC),
            evidence_count=12,
        ),
        _fact(
            kind="relation",
            suffix=12,
            label="Bob worked for Acme",
            valid_until=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        _fact(
            kind="observation",
            suffix=21,
            label="Headcount was 500",
            valid_from=datetime(2024, 1, 1, tzinfo=UTC),
            valid_until=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        _fact(
            kind="observation",
            suffix=22,
            label="FY2023 revenue was $5M",
            valid_from=datetime(2025, 1, 2, tzinfo=UTC),
            contradiction_group=_GROUP_ID,
        ),
        _fact(
            kind="observation",
            suffix=23,
            label="FY2023 revenue was $7M",
            valid_from=datetime(2025, 1, 3, tzinfo=UTC),
            contradiction_group=_GROUP_ID,
        ),
        _fact(
            kind="observation",
            suffix=24,
            label="Retracted estimate",
            invalidated_at=datetime(2026, 2, 1, tzinfo=UTC),
        ),
        _fact(
            kind="observation",
            suffix=25,
            label="Scheduled state",
            valid_from=datetime(2027, 1, 1, tzinfo=UTC),
        ),
    )
    fingerprints = tuple(
        KnowledgeFactFingerprint(
            kind=fact.kind,
            fact_id=fact.fact_id,
            valid_from=fact.valid_from,
            valid_until=fact.valid_until,
            valid_precision=fact.valid_precision,
            invalidated_at=fact.invalidated_at,
            evidence_count=fact.evidence_count,
            contradict_count=fact.contradict_count,
            contradiction_group=fact.contradiction_group,
        )
        for fact in facts
    )
    return (
        KnowledgeFactSheetSnapshot(
            artifact_id=_ARTIFACT_ID,
            deployment_id=_DEPLOYMENT_ID,
            evidence_as_of=_AS_OF,
            input_snapshot=KnowledgeInputSnapshot(
                facts=fingerprints, writer_version="fact-sheet-test"
            ),
            facts=facts,
        ),
        facts,
    )


def test_render_is_byte_stable_and_matches_each_section_query() -> None:
    """Every displayed row is exact, ordered, escaped, and lifecycle-labelled."""
    snapshot, facts = _snapshot()

    first = render_knowledge_fact_sheet(
        snapshot=snapshot, compiled_at=_COMPILED_AT, citation_count=0
    )
    second = render_knowledge_fact_sheet(
        snapshot=snapshot, compiled_at=_COMPILED_AT, citation_count=0
    )

    assert first == second
    assert first.relation_count == 2
    assert first.observation_count == 5
    assert first.contradiction_group_count == 1
    assert "Alice \\| works for Acme" in first.markdown
    assert f"relation:{facts[0].fact_id}" in first.markdown
    assert f"relation:{facts[1].fact_id}" in first.markdown
    assert "date incomplete" in first.markdown
    assert "saved snapshot" in first.markdown
    assert "Headcount was 500" in first.markdown
    assert "| after window |" in first.markdown
    assert "Retracted estimate" in first.markdown
    assert "| invalidated |" in first.markdown
    assert "Scheduled state" in first.markdown
    assert "| before window |" in first.markdown
    assert first.markdown.index("$5M") < first.markdown.index("$7M")
    assert f"`{_GROUP_ID}`" in first.markdown
    assert "7 candidates · 0 citations" in first.markdown
    displayed_observations = {
        UUID(value)
        for value in re.findall(r"observation:([0-9a-f-]{36})", first.markdown)
    }
    assert displayed_observations == {
        fact.fact_id for fact in facts if fact.kind == "observation"
    }


def test_compose_supports_two_bands_and_fact_sheet_only() -> None:
    """The same generated band composes below prose or stands alone with zero LLM."""
    snapshot, _ = _snapshot()
    band = render_knowledge_fact_sheet(
        snapshot=snapshot, compiled_at=_COMPILED_AT, citation_count=2
    ).markdown

    fact_only = compose_knowledge_page(prose_markdown=None, fact_sheet_markdown=band)
    two_band = compose_knowledge_page(
        prose_markdown="# Acme\n\nNarrative.", fact_sheet_markdown=band
    )

    assert fact_only.startswith("## Fact sheet (generated)\n")
    assert fact_only.endswith("\n")
    assert two_band.startswith("# Acme\n\nNarrative.\n\n---\n## Fact sheet")
    assert two_band.count("## Fact sheet (generated)") == 1


def test_renderer_rejects_negative_citation_count() -> None:
    """Provenance counts can never fabricate a negative amount."""
    snapshot, _ = _snapshot()

    with pytest.raises(ValueError, match="non-negative"):
        render_knowledge_fact_sheet(
            snapshot=snapshot, compiled_at=_COMPILED_AT, citation_count=-1
        )
