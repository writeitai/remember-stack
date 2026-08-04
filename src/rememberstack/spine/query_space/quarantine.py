"""The operator-only quarantine report for legacy and orphaned rows.

The public views are fail-closed: a row whose lineage, version, reading, or
provenance is missing, tombstoned, or mismatched is simply absent. That is the
right behaviour for a caller, and the wrong behaviour for an operator, who
would otherwise have no way to notice that rows are quietly disappearing —
absence in a query surface looks exactly like absence in the corpus.

This report closes that gap without weakening the surface. Each probe counts
rows that exist in the base tables but can never reach `memory_v1`, and names
the repair the count implies. It is deliberately operator-only: it exposes
counts rather than content, it is not part of the query space, it is not
reachable from any agent surface, and nothing in it can be joined back into a
public result. Orphans stay omitted from every public path until they are
repaired; the report only makes them countable.
"""

from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict
from sqlalchemy import Connection
from sqlalchemy import text


class QuarantineCategory(BaseModel):
    """One kind of row that exists but can never reach the query space."""

    model_config = ConfigDict(frozen=True)

    category: str
    explanation: str
    repair: str
    row_count: int


class QuarantineReport(BaseModel):
    """Operator-only counts of rows omitted from every public path."""

    model_config = ConfigDict(frozen=True)

    deployment_id: str | None
    categories: tuple[QuarantineCategory, ...]

    @property
    def total_rows(self) -> int:
        """Total quarantined rows across every category."""
        return sum(category.row_count for category in self.categories)


class _Probe(BaseModel):
    """One counting query plus the operator-facing meaning of its result."""

    model_config = ConfigDict(frozen=True)

    category: str
    explanation: str
    repair: str
    sql: str


_DEPLOYMENT_FILTER: Final = (
    "(CAST(:deployment_id AS uuid) IS NULL OR {alias}.deployment_id = :deployment_id)"
)

_PROBES: Final = (
    _Probe(
        category="claim_without_chunk",
        explanation=(
            "A claim names a chunk that no longer exists, so its source coordinate "
            "cannot be resolved and it is absent from every claim relation."
        ),
        repair="Re-run extraction for the claim's version, or purge the claim.",
        sql=(
            "SELECT count(*) FROM claims c WHERE "
            + _DEPLOYMENT_FILTER.format(alias="c")
            + " AND NOT EXISTS (SELECT 1 FROM chunks ch"
            " WHERE ch.deployment_id = c.deployment_id AND ch.chunk_id = c.chunk_id)"
        ),
    ),
    _Probe(
        category="chunk_without_version",
        explanation=(
            "A chunk names a version row that no longer exists, so the version "
            "authorization chain cannot be completed and the chunk is absent."
        ),
        repair="Re-run the version's chain, or purge the orphaned chunks.",
        sql=(
            "SELECT count(*) FROM chunks c WHERE "
            + _DEPLOYMENT_FILTER.format(alias="c")
            + " AND NOT EXISTS (SELECT 1 FROM document_versions v"
            " WHERE v.deployment_id = c.deployment_id AND v.version_id = c.version_id)"
        ),
    ),
    _Probe(
        category="section_outside_current_generation",
        explanation=(
            "A section belongs to a structure generation its representation no "
            "longer points at, so it is superseded rather than current."
        ),
        repair=(
            "Expected after a structure re-run; purge superseded generations when "
            "their retention window closes."
        ),
        sql=(
            "SELECT count(*) FROM document_sections s"
            " JOIN document_representations r ON r.deployment_id = s.deployment_id"
            " AND r.representation_id = s.representation_id WHERE "
            + _DEPLOYMENT_FILTER.format(alias="s")
            + " AND r.current_structure_generation_id IS DISTINCT FROM"
            " s.structure_generation_id"
        ),
    ),
    _Probe(
        category="mention_without_chunk",
        explanation=(
            "A mention has no chunk coordinate or names a missing chunk, so it "
            "cannot be placed in current content and is absent from the transcript."
        ),
        repair="Re-run resolution for the mention's version, or purge the mention.",
        sql=(
            "SELECT count(*) FROM mentions m WHERE "
            + _DEPLOYMENT_FILTER.format(alias="m")
            + " AND (m.chunk_id IS NULL OR NOT EXISTS (SELECT 1 FROM chunks ch"
            " WHERE ch.deployment_id = m.deployment_id AND ch.chunk_id = m.chunk_id))"
        ),
    ),
    _Probe(
        category="evidence_lineage_mismatch",
        explanation=(
            "An evidence row's denormalized lineage disagrees with its claim's "
            "lineage, which is mismatched state and is dropped from the bridge."
        ),
        repair="Re-run the fact's normalization so the denormalized lineage agrees.",
        sql=(
            "SELECT count(*) FROM relation_evidence re JOIN claims c"
            " ON c.deployment_id = re.deployment_id AND c.claim_id = re.claim_id"
            " WHERE "
            + _DEPLOYMENT_FILTER.format(alias="re")
            + " AND re.doc_id IS DISTINCT FROM c.doc_id"
        ),
    ),
    _Probe(
        category="fact_without_surviving_provenance",
        explanation=(
            "A relation has no evidence row reaching a live lineage, so it has no "
            "surviving provenance and is absent from every fact relation."
        ),
        repair=(
            "Expected after a forget; retire the fact, or re-ingest a source that "
            "supports it."
        ),
        sql=(
            "SELECT count(*) FROM relations r WHERE "
            + _DEPLOYMENT_FILTER.format(alias="r")
            + " AND NOT EXISTS (SELECT 1 FROM relation_evidence re JOIN documents d"
            " ON d.deployment_id = re.deployment_id AND d.doc_id = re.doc_id"
            " AND d.deleted_at IS NULL WHERE re.deployment_id = r.deployment_id"
            " AND re.relation_id = r.relation_id)"
        ),
    ),
    _Probe(
        category="entity_without_surviving_provenance",
        explanation=(
            "An active entity has no live mention and no live document bridge, so "
            "it has no surviving provenance and is absent from the entity relation."
        ),
        repair="Retire the entity, or re-ingest a source that mentions it.",
        sql=(
            "SELECT count(*) FROM entities e WHERE "
            + _DEPLOYMENT_FILTER.format(alias="e")
            + " AND e.status = 'active'"
            " AND NOT EXISTS (SELECT 1 FROM resolution_decisions rd"
            " JOIN mentions m ON m.deployment_id = rd.deployment_id"
            " AND m.mention_id = rd.mention_id JOIN documents d"
            " ON d.deployment_id = m.deployment_id AND d.doc_id = m.doc_id"
            " AND d.deleted_at IS NULL WHERE rd.deployment_id = e.deployment_id"
            " AND rd.entity_id = e.entity_id AND rd.superseded_by IS NULL)"
            " AND NOT EXISTS (SELECT 1 FROM documents d"
            " WHERE d.deployment_id = e.deployment_id"
            " AND d.document_entity_id = e.entity_id AND d.deleted_at IS NULL)"
        ),
    ),
    _Probe(
        category="crossref_without_live_endpoints",
        explanation=(
            "A cross-reference has an unresolved or forgotten endpoint, so it is "
            "absent rather than half-resolved."
        ),
        repair=(
            "Expected for citations whose target was never ingested; re-resolve "
            "once the target exists."
        ),
        sql=(
            "SELECT count(*) FROM document_crossrefs x WHERE "
            + _DEPLOYMENT_FILTER.format(alias="x")
            + " AND (x.to_doc_id IS NULL OR NOT EXISTS (SELECT 1 FROM documents d"
            " WHERE d.deployment_id = x.deployment_id AND d.doc_id = x.to_doc_id"
            " AND d.deleted_at IS NULL) OR NOT EXISTS (SELECT 1 FROM documents d"
            " WHERE d.deployment_id = x.deployment_id AND d.doc_id = x.from_doc_id"
            " AND d.deleted_at IS NULL))"
        ),
    ),
    _Probe(
        category="knowledge_citation_without_visible_target",
        explanation=(
            "A knowledge citation points at a forgotten lineage or a relation with "
            "no surviving provenance, so the link is absent from the page."
        ),
        repair="Recompile the page so its citation set matches visible evidence.",
        sql=(
            "SELECT count(*) FROM knowledge_artifact_evidence e WHERE "
            + _DEPLOYMENT_FILTER.format(alias="e")
            + " AND NOT ("
            " (coalesce(e.claim_lineage_id, e.doc_id) IS NOT NULL"
            "  AND EXISTS (SELECT 1 FROM documents d"
            "   WHERE d.deployment_id = e.deployment_id"
            "   AND d.doc_id = coalesce(e.claim_lineage_id, e.doc_id)"
            "   AND d.deleted_at IS NULL))"
            " OR (e.relation_id IS NOT NULL AND EXISTS ("
            "   SELECT 1 FROM relation_evidence re JOIN documents d"
            "   ON d.deployment_id = re.deployment_id AND d.doc_id = re.doc_id"
            "   AND d.deleted_at IS NULL WHERE re.deployment_id = e.deployment_id"
            "   AND re.relation_id = e.relation_id))"
            ")"
        ),
    ),
    _Probe(
        category="currency_event_without_live_lineage",
        explanation=(
            "A testimony-currency transition names a forgotten lineage, so it is "
            "absent from the transition relation."
        ),
        repair="Expected after a forget; purge the events with the lineage.",
        sql=(
            "SELECT count(*) FROM testimony_currency_events e WHERE "
            + _DEPLOYMENT_FILTER.format(alias="e")
            + " AND NOT EXISTS (SELECT 1 FROM documents d"
            " WHERE d.deployment_id = e.deployment_id AND d.doc_id = e.doc_id"
            " AND d.deleted_at IS NULL)"
        ),
    ),
)

#: Every category this report can emit, in report order.
QUARANTINE_CATEGORIES: tuple[str, ...] = tuple(probe.category for probe in _PROBES)


def orphan_quarantine_report(
    *, connection: Connection, deployment_id: str | None = None
) -> QuarantineReport:
    """Count rows that exist but can never reach the public query space.

    Passing no deployment reports across the whole database, which is the
    operator view; passing one scopes every probe to that deployment.
    """
    categories = tuple(
        QuarantineCategory(
            category=probe.category,
            explanation=probe.explanation,
            repair=probe.repair,
            row_count=int(
                connection.execute(
                    statement=text(probe.sql),
                    parameters={"deployment_id": deployment_id},
                ).scalar_one()
            ),
        )
        for probe in _PROBES
    )
    return QuarantineReport(deployment_id=deployment_id, categories=categories)
