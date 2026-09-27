"""Pure deterministic Plane-K fact-sheet rendering (D45, WP-6.3)."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import TypeAdapter

from rememberstack.core.fact_windows import describe_fact_window
from rememberstack.model import KnowledgeFactSheetFact
from rememberstack.model import KnowledgeFactSheetSnapshot
from rememberstack.model import KnowledgeRenderedFactSheet
from rememberstack.model import UTCDateTime
from rememberstack.model.fact_windows import FactWindow

_UTC_ADAPTER = TypeAdapter(UTCDateTime)


class KnowledgeFactLifecycle(StrEnum):
    """A fact's literal state at the fixed evidence snapshot timestamp."""

    CURRENT = "within window"
    POSSIBLE = "date incomplete"
    ENDED = "after window"
    INVALIDATED = "invalidated"
    NOT_YET_VALID = "before window"


def render_knowledge_fact_sheet(
    *,
    snapshot: KnowledgeFactSheetSnapshot,
    compiled_at: UTCDateTime,
    citation_count: int,
    candidate_count: int | None = None,
) -> KnowledgeRenderedFactSheet:
    """Render dated fact history and belief status at an explicit fixed snapshot."""
    if citation_count < 0:
        raise ValueError("citation_count must be non-negative")
    if candidate_count is not None and candidate_count < 0:
        raise ValueError("candidate_count must be non-negative")
    compiled_at = _UTC_ADAPTER.validate_python(compiled_at)
    relations = tuple(
        sorted(
            (fact for fact in snapshot.facts if fact.kind == "relation"),
            key=lambda fact: (
                -fact.evidence_count,
                fact.label.casefold(),
                str(fact.fact_id),
            ),
        )
    )
    observations = tuple(
        sorted(
            (fact for fact in snapshot.facts if fact.kind == "observation"),
            key=lambda fact: (
                _time_key(value=fact.valid_from),
                _time_key(value=fact.ingested_at),
                str(fact.fact_id),
            ),
        )
    )
    contradiction_groups = _open_contradiction_groups(facts=snapshot.facts)

    lines = [
        "## Fact sheet (generated)",
        "",
        f"_Status evaluated at {_timestamp(snapshot.evidence_as_of)}; this is a saved snapshot._",
        "",
        "### Relation history",
        "",
        "| fact | world time | status at snapshot | evidence | reference |",
        "|---|---|---|---:|---|",
    ]
    if relations:
        lines.extend(
            f"| {_cell(fact.label)} | {_cell(_world_time(fact=fact))} | "
            f"{_lifecycle(fact=fact, evidence_as_of=snapshot.evidence_as_of).value} | "
            f"{fact.evidence_count} docs | `relation:{fact.fact_id}` |"
            for fact in relations
        )
    else:
        lines.append("| _None._ | — | — | 0 docs | — |")

    lines.extend(
        (
            "",
            "### Observation history",
            "",
            "| observation | world time | status at snapshot | evidence | reference |",
            "|---|---|---|---:|---|",
        )
    )
    if observations:
        lines.extend(
            f"| {_cell(fact.label)} | {_cell(_world_time(fact=fact))} | "
            f"{_lifecycle(fact=fact, evidence_as_of=snapshot.evidence_as_of).value} | "
            f"{fact.evidence_count} docs | `observation:{fact.fact_id}` |"
            for fact in observations
        )
    else:
        lines.append("| _None._ | — | — | 0 docs | — |")

    lines.extend(("", "### Open contradictions", ""))
    if contradiction_groups:
        lines.extend(
            (
                "| group | fact | state | evidence | reference |",
                "|---|---|---|---:|---|",
            )
        )
        for group_id, members in contradiction_groups:
            lines.extend(
                f"| `{group_id}` | {_cell(fact.label)} | "
                f"{_lifecycle(fact=fact, evidence_as_of=snapshot.evidence_as_of).value} | "
                f"{fact.evidence_count} docs | `{fact.kind}:{fact.fact_id}` |"
                for fact in members
            )
    else:
        lines.append("_None._")

    if candidate_count is None:
        candidate_count = len(snapshot.input_snapshot.facts) + len(
            snapshot.input_snapshot.claims
        )
    lines.extend(
        (
            "",
            "---",
            f"_compiled {_timestamp(compiled_at)} · "
            f"evidence as of {_timestamp(snapshot.evidence_as_of)} · "
            f"{candidate_count} candidates · {citation_count} citations_",
            "",
        )
    )
    return KnowledgeRenderedFactSheet(
        markdown="\n".join(lines),
        relation_count=len(relations),
        observation_count=len(observations),
        contradiction_group_count=len(contradiction_groups),
    )


def compose_knowledge_page(
    *, prose_markdown: str | None, fact_sheet_markdown: str
) -> str:
    """Compose a two-band page, or the fact-sheet band alone with zero prose."""
    fact_sheet = fact_sheet_markdown.strip()
    if not fact_sheet:
        raise ValueError("fact_sheet_markdown must be non-empty")
    if prose_markdown is None or not prose_markdown.strip():
        return f"{fact_sheet}\n"
    return f"{prose_markdown.rstrip()}\n\n---\n{fact_sheet}\n"


def _lifecycle(
    *, fact: KnowledgeFactSheetFact, evidence_as_of: datetime
) -> KnowledgeFactLifecycle:
    """Classify one fact without confusing validity end with invalidation."""
    if fact.invalidated_at is not None:
        return KnowledgeFactLifecycle.INVALIDATED
    if fact.valid_from is not None and fact.valid_from > evidence_as_of:
        return KnowledgeFactLifecycle.NOT_YET_VALID
    if fact.valid_until is not None and fact.valid_until <= evidence_as_of:
        return KnowledgeFactLifecycle.ENDED
    window = FactWindow(
        valid_from=fact.valid_from,
        valid_until=fact.valid_until,
        valid_precision=fact.valid_precision,
    )
    return (
        KnowledgeFactLifecycle.CURRENT
        if window.is_complete
        else KnowledgeFactLifecycle.POSSIBLE
    )


def _world_time(*, fact: KnowledgeFactSheetFact) -> str:
    """Display the chosen world window without inventing unknown endpoints."""
    return describe_fact_window(
        window=FactWindow(
            valid_from=fact.valid_from,
            valid_until=fact.valid_until,
            valid_precision=fact.valid_precision,
        )
    )


def _open_contradiction_groups(
    *, facts: tuple[KnowledgeFactSheetFact, ...]
) -> tuple[tuple[UUID, tuple[KnowledgeFactSheetFact, ...]], ...]:
    """Group every non-invalidated selected side of an unresolved contradiction."""
    grouped: dict[UUID, list[KnowledgeFactSheetFact]] = {}
    for fact in facts:
        if fact.contradiction_group is None or fact.invalidated_at is not None:
            continue
        grouped.setdefault(fact.contradiction_group, []).append(fact)
    return tuple(
        (
            group_id,
            tuple(
                sorted(
                    members,
                    key=lambda fact: (
                        fact.kind,
                        fact.label.casefold(),
                        str(fact.fact_id),
                    ),
                )
            ),
        )
        for group_id, members in sorted(grouped.items(), key=lambda item: str(item[0]))
    )


def _timestamp(value: datetime | None) -> str:
    """Render full UTC precision without locale or machine-timezone dependence."""
    if value is None:
        return "—"
    return value.isoformat().replace("+00:00", "Z")


def _time_key(*, value: datetime | None) -> tuple[int, str]:
    """Sort unknown starts first, then aware timestamps in chronological order."""
    return (0, "") if value is None else (1, value.isoformat())


def _cell(value: str) -> str:
    """Keep arbitrary fact labels inside one Markdown table cell."""
    return " ".join(value.split()).replace("\\", "\\\\").replace("|", "\\|")
