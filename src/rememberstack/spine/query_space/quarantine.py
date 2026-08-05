"""The operator-only quarantine report for legacy and orphaned rows.

The public views are fail-closed: a row whose lineage, version, reading, or
provenance is missing, tombstoned, or mismatched is simply absent. That is the
right behaviour for a caller, and the wrong behaviour for an operator, who
would otherwise have no way to notice that rows are quietly disappearing —
absence in a query surface looks exactly like absence in the corpus.

This report closes that gap without weakening the surface. Each probe counts
rows that exist in the base tables but can never reach `memory_v1`, and names
the repair the count implies. Two of the probes exist precisely because a
fail-closed rule would otherwise be silent: an entity whose merge chain does
not terminate resolves to no survivor, and a knowledge page whose every cited
target has been forgotten is not published — in both cases the row is right to
be absent and wrong to be invisible to an operator.

It is deliberately operator-only: it exposes counts rather than content, it is
not part of the query space, it is not reachable from any agent surface, and
nothing in it can be joined back into a public result. Orphans stay omitted
from every public path until they are repaired; the report only makes them
countable.
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
        category="fact_without_visible_membership",
        explanation=(
            "A relation or observation is absent from the authoritative visible-fact "
            "set because it has no claim-bound surviving provenance or an entity "
            "endpoint is not externally visible."
        ),
        repair=(
            "Repair the mismatched association or entity provenance; after a forget, "
            "retire the fact or re-ingest a source that supports it."
        ),
        sql=(
            "SELECT count(*) FROM ("
            " SELECT r.deployment_id, 'relation' AS fact_kind,"
            " r.relation_id AS fact_id FROM relations AS r"
            " UNION ALL"
            " SELECT o.deployment_id, 'observation' AS fact_kind,"
            " o.observation_id AS fact_id FROM observations AS o"
            ") AS candidate WHERE "
            + _DEPLOYMENT_FILTER.format(alias="candidate")
            + " AND NOT EXISTS (SELECT 1 FROM v_memory_fact_visible AS visible"
            " WHERE visible.deployment_id = candidate.deployment_id"
            " AND visible.fact_kind = candidate.fact_kind"
            " AND visible.fact_id = candidate.fact_id)"
        ),
    ),
    _Probe(
        category="entity_without_surviving_provenance",
        explanation=(
            "An active survivor entity is mentioned in no visible version of any "
            "live lineage and is bridged to no live document, so it has no "
            "surviving provenance and is absent from the entity relation. A "
            "mention that survives only in a superseded version of a live lineage "
            "is still provenance — the D48 floor is a surviving lineage, not "
            "current content — so an entity is counted here only when every "
            "source that named it is gone."
        ),
        repair=(
            "Expected after a forget or a re-extraction that dropped the mention; "
            "retire the entity, or re-ingest a source that mentions it."
        ),
        # Counted as the complement of the public relation itself, so the report
        # cannot describe a different set from the one the surface omits. The
        # survivor clause keeps the two entity categories disjoint: an entity
        # absent because its merge chain never terminates is the next probe's
        # subject, not this one's.
        sql=(
            "SELECT count(*) FROM entities e WHERE "
            + _DEPLOYMENT_FILTER.format(alias="e")
            + " AND e.status = 'active'"
            " AND EXISTS (SELECT 1 FROM v_memory_entity_survivor s"
            " WHERE s.deployment_id = e.deployment_id AND s.entity_id = e.entity_id"
            " AND s.survivor_entity_id = e.entity_id)"
            " AND NOT EXISTS (SELECT 1 FROM memory_v1.entities_current c"
            " WHERE c.deployment_id = e.deployment_id AND c.entity_id = e.entity_id)"
        ),
    ),
    _Probe(
        category="entity_merge_chain_unresolved",
        explanation=(
            "An entity's merge redirect never reaches an unmerged entity — a "
            "cycle or a redirect to a missing row — so it "
            "resolves to no survivor and is absent from every entity relation."
        ),
        repair=(
            "Repair the merged_into chain so it terminates at an unmerged entity; "
            "a cycle means two merges were recorded in both directions."
        ),
        sql=(
            "SELECT count(*) FROM entities e WHERE "
            + _DEPLOYMENT_FILTER.format(alias="e")
            + " AND NOT EXISTS (SELECT 1 FROM v_memory_entity_survivor s"
            " WHERE s.deployment_id = e.deployment_id AND s.entity_id = e.entity_id)"
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
        category="page_without_visible_citation",
        explanation=(
            "A live knowledge artifact cites nothing that is still visible, so "
            "its compiled prose has no surviving provenance and the page is "
            "absent from the knowledge relation."
        ),
        repair=(
            "Expected after a forget; recompile the page against visible evidence, "
            "or retire it. A page that never cited anything was compiled wrongly."
        ),
        sql=(
            "SELECT count(*) FROM knowledge_artifacts a WHERE "
            + _DEPLOYMENT_FILTER.format(alias="a")
            + " AND a.status <> 'tombstoned'"
            " AND NOT EXISTS (SELECT 1 FROM v_memory_page_citation_visible c"
            " WHERE c.deployment_id = a.deployment_id"
            " AND c.artifact_id = a.artifact_id)"
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
