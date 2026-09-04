"""The enumerated D48 deletion coverage matrix, generated and versioned.

"Deletion is fail-closed across every view" is only a real claim if the cases
are enumerated *and* each one proves something. This module enumerates them:
every deletion target crossed with every surface, one cell each, checked into
the repository as a reviewable artifact and executed in full by the schema gate.

**Every cell states what it proves, and none of them can pass vacuously.** A
cell that simply asserted "no forbidden identifier is present after the
mutation" would pass trivially wherever the identifier could never have been
present in the first place — which is most of the grid. So each cell
carries its own status, and the gate holds each status to a different, explicit
obligation:

- ``applicable`` — the target's forbidden identifiers *are* reachable through
  this relation before the mutation, and every column of every row is scanned
  for them afterwards. The gate asserts both halves per cell: reachable before,
  absent after. A cell that stops being reachable before the mutation fails,
  rather than quietly becoming a no-op.
- ``not_applicable`` with basis ``no_identifier_of_this_class`` — this relation
  publishes none of the identifier classes the target forbids, so there is
  nothing here to leak. The gate does not take that on trust: it asserts the
  reachable set really is empty, before *and* after, so a wrong declaration
  fails loudly instead of hiding a leak.
- ``not_applicable`` with basis ``not_caller_reachable`` — the surface is a
  merge-redirect helper, which deliberately does
  *not* drop rows on a deletion: `entities_current` computes surviving
  provenance *from* it, so it must keep resolving an entity whose provenance is
  gone, and an "absent afterwards" obligation would be false of it. Its D48
  obligation is discharged the other way, by non-reachability — outside
  `memory_v1`, no grant, and (Batch B) never on a query role's `search_path` —
  which the gate asserts from `pg_class.relacl` and `information_schema`, while
  the public relations that read it carry their own cells.

The other private helpers are *not* discharged that way. `memory_v1` is where
a caller reads, but the mention, fact/evidence, and K citation helpers are where
deletion rules are actually *defined* — the public relations project them — so their
cells are executed exactly like a public relation's:
reachable before the mutation, absent after. Non-reachability is proven for
all seven helpers on top of that, by the gate that reads their grants.
- ``deferred`` — the target names an object class this batch does not build (a
  P1 nomination candidate, a live graph edge, a corpus body). The cell is
  recorded rather than omitted, with the batch that will execute it, so the
  artifact's coverage claim stays honest about its own scope.

The targets are chosen so that one global forbidden set is correct for every
surface. That matters for the representation target in particular: superseding
a reading is only a deletion for content derived from it, so its fixture
lineage deliberately carries no claims, and the claims of a superseded reading
staying visible elsewhere is correct D54/D55 behaviour rather than a leak.
"""

from enum import StrEnum
import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict

from rememberstack.spine.query_space.canonical import CanonicalValue
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS
from rememberstack.spine.query_space.source_definitions import (
    AUTHORIZATION_HELPER_VIEWS,
)

#: Identifier of this artifact's layout. Version 2 replaced one global
#: expectation and a per-target non-vacuity check with per-cell status,
#: per-cell reachability, and explicit not-applicable and deferred entries.
MATRIX_CONTRACT: Final = "memory_v1.d48_deletion_matrix/2"

#: The checked-in coverage artifact the schema gate executes cell by cell.
MATRIX_PATH: Final = Path(__file__).with_name("d48_deletion_matrix.json")

#: The merge authority and its deployment-labelled adapter. Their cells are
#: discharged by non-reachability because they deliberately keep resolving an
#: entity whose provenance is gone.
SURVIVOR_HELPERS: Final = ("public.v_graph_survivor", "public.v_memory_entity_survivor")

#: The private helpers that *do* compile a deletion rule — they are where the
#: rule is defined and the public relations project it — so their cells are
#: executed like a public relation's, before and after the mutation.
GATED_HELPERS: Final = (
    "public.v_memory_evidence_lineage_live",
    "public.v_memory_fact_claim_live",
    "public.v_memory_fact_visible",
    "public.v_memory_mention_current_content",
    "public.v_memory_page_citation_visible",
)


class CellStatus(StrEnum):
    """What a cell claims, and therefore what the gate must prove about it."""

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    DEFERRED = "deferred"


class NotApplicableBasis(StrEnum):
    """Why a cell is not applicable, and how the gate verifies that."""

    NO_IDENTIFIER_OF_THIS_CLASS = "no_identifier_of_this_class"
    NOT_CALLER_REACHABLE = "not_caller_reachable"


class MatrixSurface(BaseModel):
    """One surface every deletion target is crossed with."""

    model_config = ConfigDict(frozen=True)

    name: str
    """Schema-qualified relation name."""

    caller_reachable: bool
    """False for a private helper, which no query role can read."""

    compiles_deletion: bool = True
    """True when this surface itself drops rows on a deletion, and its cells are
    therefore executed before and after the mutation. False only for the
    merge-redirect helper, which must keep resolving an entity whose provenance
    is gone because `entities_current` computes that provenance from it."""


class DeletionTarget(BaseModel):
    """One enumerated deletion the whole query space must survive."""

    model_config = ConfigDict(frozen=True)

    target_id: str
    summary: str
    mutation: str
    identifier_classes: tuple[str, ...]
    """The kinds of identifier this target's forbidden set contains."""

    forbidden_identifiers: str
    executed_in: str
    """The batch whose gate executes this target's cells."""

    deferred_reason: str = ""
    """Why a later batch executes it: what this batch does not build yet."""

    applicable_surfaces: tuple[str, ...] = ()
    """Surfaces this target's identifiers are reachable through, before the
    mutation. Every other surface is an explicit `not_applicable` cell whose
    emptiness the gate proves."""

    @property
    def deferred(self) -> bool:
        """True until the named batch supplies this target's executable gate."""
        return bool(self.deferred_reason)


_PUBLIC_SURFACES: Final = tuple(
    MatrixSurface(name=f"{QUERY_SPACE_SCHEMA}.{contract.name}", caller_reachable=True)
    for contract in sorted(VIEW_CONTRACTS, key=lambda contract: contract.name)
)

_HELPER_SURFACES: Final = (
    *(
        MatrixSurface(name=helper, caller_reachable=False, compiles_deletion=False)
        for helper in sorted(SURVIVOR_HELPERS)
    ),
    *(
        MatrixSurface(name=helper, caller_reachable=False)
        for helper in sorted(GATED_HELPERS)
    ),
)

if {surface.name for surface in _HELPER_SURFACES} != {
    f"public.{name}" for name in AUTHORIZATION_HELPER_VIEWS
}:  # pragma: no cover -- a new helper must be classified before it can ship
    raise RuntimeError(
        "the deletion matrix does not enumerate every private helper the "
        "migration creates; classify the new one as gated or non-reachable"
    )

#: Every surface, public relations first and the private helpers last.
MATRIX_SURFACES: Final = (*_PUBLIC_SURFACES, *_HELPER_SURFACES)

_LINEAGE_DERIVED: Final = (
    "lineage",
    "version",
    "representation",
    "structure_generation",
    "section",
    "chunk",
    "claim",
)

DELETION_TARGETS: Final = (
    DeletionTarget(
        target_id="lineage",
        summary="A live document lineage is forgotten.",
        mutation="Set documents.deleted_at on the fixture lineage.",
        identifier_classes=(
            *_LINEAGE_DERIVED,
            "mention",
            "resolution_decision",
            "currency_event",
            "crossref",
            "fact",
            "entity",
            "alias",
        ),
        forbidden_identifiers=(
            "The lineage id and every identifier derived from it: its versions, "
            "representations, sections, chunks, claims, mentions, cross-references, "
            "testimony-currency events, the entity whose only provenance it was, and "
            "the fact whose only evidence lineage it was."
        ),
        executed_in="A",
        applicable_surfaces=(
            "memory_v1.changes_visible",
            "memory_v1.chunks_live",
            "memory_v1.claim_occurrences_live",
            "memory_v1.claims_canonical",
            "memory_v1.claims_live",
            "memory_v1.claims_visible_history",
            "memory_v1.contradiction_members_current",
            "memory_v1.document_crossrefs_live",
            "memory_v1.document_versions_visible",
            "memory_v1.documents_live",
            "memory_v1.entities_current",
            "memory_v1.entity_aliases_current",
            "memory_v1.entity_document_mentions",
            "memory_v1.evidence_lineage",
            "memory_v1.fact_claim_evidence_live",
            "memory_v1.facts_current",
            "memory_v1.facts_visible_history",
            "memory_v1.graph_edges_current",
            "memory_v1.graph_edges_visible_history",
            "memory_v1.identity_events_visible",
            "memory_v1.mentions_live",
            "memory_v1.page_evidence_visible",
            "memory_v1.sections_live",
            "memory_v1.testimony_currency_events_visible",
            "public.v_memory_evidence_lineage_live",
            "public.v_memory_fact_claim_live",
            "public.v_memory_mention_current_content",
            "public.v_memory_fact_visible",
            "public.v_memory_page_citation_visible",
        ),
    ),
    DeletionTarget(
        target_id="version",
        summary="One non-current version of a live lineage is tombstoned.",
        mutation="Set document_versions.deleted_at on the fixture version.",
        identifier_classes=(
            "version",
            "representation",
            "structure_generation",
            "section",
            "chunk",
            "claim",
            "mention",
            "currency_event",
        ),
        forbidden_identifiers=(
            "The version id and every identifier derived from it: its representation, "
            "sections, chunks, claims, and mentions. The facts those claims evidenced "
            "keep their surviving historical provenance and are deliberately not "
            "forbidden."
        ),
        executed_in="A",
        applicable_surfaces=(
            "memory_v1.changes_visible",
            "memory_v1.claims_canonical",
            "memory_v1.claims_visible_history",
            "memory_v1.document_versions_visible",
            "memory_v1.testimony_currency_events_visible",
        ),
    ),
    DeletionTarget(
        target_id="representation",
        summary="The current ready reading of a live version is superseded.",
        mutation=(
            "Insert a new ready representation, point the version's current "
            "representation at it, and leave the previous reading in place."
        ),
        identifier_classes=(
            "representation",
            "structure_generation",
            "section",
            "chunk",
        ),
        forbidden_identifiers=(
            "The superseded representation id and the sections and chunks cut from "
            "it. The fixture lineage carries no claims, so no testimony legitimately "
            "outlives the reading and the forbidden set stays global."
        ),
        executed_in="A",
        applicable_surfaces=(
            "memory_v1.chunks_live",
            "memory_v1.document_versions_visible",
            "memory_v1.documents_live",
            "memory_v1.sections_live",
        ),
    ),
    DeletionTarget(
        target_id="claim",
        summary="One claim is purged from the testimony record.",
        mutation="Delete the claim row and its chunk occurrences.",
        identifier_classes=("claim",),
        forbidden_identifiers=(
            "The claim id. The fact it evidenced keeps its association row and stays "
            "visible with a reduced count, which is the D54 behaviour rather than a leak."
        ),
        executed_in="A",
        applicable_surfaces=(
            "memory_v1.changes_visible",
            "memory_v1.claim_occurrences_live",
            "memory_v1.claims_canonical",
            "memory_v1.claims_live",
            "memory_v1.claims_visible_history",
            "memory_v1.evidence_lineage",
            "memory_v1.fact_claim_evidence_live",
            "memory_v1.mentions_live",
            "public.v_memory_evidence_lineage_live",
            "public.v_memory_fact_claim_live",
            "public.v_memory_mention_current_content",
        ),
    ),
    DeletionTarget(
        target_id="fact_provenance",
        summary="Every evidence lineage of one fact is removed.",
        mutation="Delete the fact's relation or observation evidence rows.",
        identifier_classes=("fact",),
        forbidden_identifiers=(
            "The fact id, which must disappear from the fact relations, the evidence "
            "bridge, the contradiction and graph projections, and the change feed."
        ),
        executed_in="A",
        applicable_surfaces=(
            "memory_v1.changes_visible",
            "memory_v1.evidence_lineage",
            "memory_v1.fact_claim_evidence_live",
            "memory_v1.facts_current",
            "memory_v1.facts_visible_history",
            "memory_v1.graph_edges_current",
            "memory_v1.graph_edges_visible_history",
            "memory_v1.page_evidence_visible",
            "public.v_memory_evidence_lineage_live",
            "public.v_memory_fact_claim_live",
            "public.v_memory_fact_visible",
            "public.v_memory_page_citation_visible",
        ),
    ),
    DeletionTarget(
        target_id="k_target",
        summary="The lineage a knowledge page cites is forgotten.",
        mutation="Set documents.deleted_at on the cited lineage.",
        identifier_classes=(*_LINEAGE_DERIVED, "content_hash"),
        forbidden_identifiers=(
            "The cited lineage id, its derived identifiers, and the chunk content "
            "hashes that locate the cited claims. The citing page keeps its other "
            "citations and stays visible; only the citation of the forgotten lineage "
            "leaves, which is what proves a chunk-content hash locator cannot "
            "authorize a read on its own."
        ),
        executed_in="A",
        applicable_surfaces=(
            "memory_v1.changes_visible",
            "memory_v1.chunks_live",
            "memory_v1.claim_occurrences_live",
            "memory_v1.claims_canonical",
            "memory_v1.claims_live",
            "memory_v1.claims_visible_history",
            "memory_v1.document_versions_visible",
            "memory_v1.documents_live",
            "memory_v1.page_evidence_visible",
            "memory_v1.sections_live",
            "public.v_memory_page_citation_visible",
        ),
    ),
    DeletionTarget(
        target_id="p1_candidate",
        summary="A stale P1 nomination candidate is deleted from PostgreSQL.",
        mutation=(
            "Delete the nominated chunk, claim, or entity behind a frozen P1 "
            "candidate set, then run the semantic and lexical functions."
        ),
        identifier_classes=("chunk", "claim", "entity", "content_hash"),
        forbidden_identifiers=(
            "Every nominated identifier and body byte whose PostgreSQL coordinate no "
            "longer confirms, which the in-function confirmation must drop and count."
        ),
        executed_in="C",
        deferred_reason=(
            "the P1 nomination path and the in-function PostgreSQL confirmation "
            "that drops a stale candidate are Batch C"
        ),
    ),
    DeletionTarget(
        target_id="graph_edge",
        summary="A live graph edge loses its last surviving evidence row.",
        mutation=(
            "Delete the surviving evidence behind a relation, then read the live "
            "graph authorities in the same transaction."
        ),
        identifier_classes=("fact",),
        forbidden_identifiers=(
            "The relation identifier after its last surviving evidence row is "
            "deleted; no graph generation or stale snapshot exception exists."
        ),
        executed_in="D",
        applicable_surfaces=(
            "memory_v1.changes_visible",
            "memory_v1.evidence_lineage",
            "memory_v1.fact_claim_evidence_live",
            "memory_v1.facts_current",
            "memory_v1.facts_visible_history",
            "memory_v1.graph_edges_current",
            "memory_v1.graph_edges_visible_history",
            "memory_v1.page_evidence_visible",
            "public.v_memory_evidence_lineage_live",
            "public.v_memory_fact_claim_live",
            "public.v_memory_fact_visible",
            "public.v_memory_page_citation_visible",
        ),
    ),
    DeletionTarget(
        target_id="corpus_body",
        summary="The body bytes behind a live chunk coordinate are forgotten.",
        mutation=(
            "Hard-forget the content object behind a chunk, then fetch that chunk's "
            "body by id."
        ),
        identifier_classes=("chunk", "content_hash"),
        forbidden_identifiers=(
            "Every byte of the forgotten body and every hash that would confirm it, "
            "across the id-addressed body fetch and the corpus filesystem."
        ),
        executed_in="C",
        deferred_reason=(
            "the id-addressed chunk-body fetch and the corpus filesystem that return "
            "body bytes are Batch C"
        ),
    ),
)

DELETION_TARGETS_BY_ID: Final = {
    target.target_id: target for target in DELETION_TARGETS
}

#: The targets this batch's gate executes; the rest are recorded as deferred.
EXECUTED_TARGETS: Final = tuple(
    target for target in DELETION_TARGETS if not target.deferred
)


def cell_expectation(*, target: DeletionTarget, surface: MatrixSurface) -> str:
    """State, in one line, exactly what this cell's status obliges the gate to."""
    if target.deferred:
        return (
            f"Deferred to Batch {target.executed_in}: {target.deferred_reason}, so "
            "this cell is recorded here with the batch that will execute it rather "
            "than left out of the enumeration."
        )
    if not surface.compiles_deletion:
        return (
            f"{surface.name} is a merge-redirect helper: it deliberately keeps "
            "resolving an entity whose provenance is gone, because entities_current "
            "computes that provenance from it, so its obligation is "
            f"non-reachability rather than absence — outside {QUERY_SPACE_SCHEMA}, "
            "no grant, and never on a query role's search_path — which the gate "
            "proves, while the public relations that read it carry their own cells "
            "for this target."
        )
    private = (
        ""
        if surface.caller_reachable
        else (
            " It is a private helper rather than a public relation, so this cell "
            "proves the rule where it is defined and the relations that project it "
            "prove it again where a caller reads."
        )
    )
    if surface.name in target.applicable_surfaces:
        return (
            f"The {target.target_id} target's forbidden identifiers are reachable "
            f"through {surface.name} before the mutation, and no column of any row "
            f"carries one of them after it.{private}"
        )
    return (
        f"{surface.name} publishes none of the identifier classes this target "
        f"forbids ({', '.join(target.identifier_classes)}); the gate proves that "
        "nothing from the forbidden set is reachable here, before or after the "
        f"mutation, rather than assuming it.{private}"
    )


def cell_status(
    *, target: DeletionTarget, surface: MatrixSurface
) -> tuple[CellStatus, NotApplicableBasis | None]:
    """Classify one cell and, when it is not applicable, say on what basis."""
    if target.deferred:
        return CellStatus.DEFERRED, None
    if not surface.compiles_deletion:
        return CellStatus.NOT_APPLICABLE, NotApplicableBasis.NOT_CALLER_REACHABLE
    if surface.name in target.applicable_surfaces:
        return CellStatus.APPLICABLE, None
    return CellStatus.NOT_APPLICABLE, NotApplicableBasis.NO_IDENTIFIER_OF_THIS_CLASS


def build_matrix() -> dict[str, CanonicalValue]:
    """Enumerate every target × surface cell, with its status, in a stable order."""
    cells: list[CanonicalValue] = []
    tally = {status.value: 0 for status in CellStatus}
    for target in DELETION_TARGETS:
        for surface in MATRIX_SURFACES:
            status, basis = cell_status(target=target, surface=surface)
            tally[status.value] += 1
            cells.append(
                {
                    "target_id": target.target_id,
                    "surface": surface.name,
                    "status": status.value,
                    "basis": None if basis is None else basis.value,
                    "expectation": cell_expectation(target=target, surface=surface),
                }
            )
    return {
        "contract": MATRIX_CONTRACT,
        "schema": QUERY_SPACE_SCHEMA,
        "target_count": len(DELETION_TARGETS),
        "surface_count": len(MATRIX_SURFACES),
        "cell_count": len(cells),
        "status_counts": {status: count for status, count in sorted(tally.items())},
        "targets": [
            {
                "target_id": target.target_id,
                "summary": target.summary,
                "mutation": target.mutation,
                "identifier_classes": list(target.identifier_classes),
                "forbidden_identifiers": target.forbidden_identifiers,
                "executed_in": target.executed_in,
                "applicable_surfaces": list(target.applicable_surfaces),
            }
            for target in DELETION_TARGETS
        ],
        "surfaces": [
            {
                "name": surface.name,
                "caller_reachable": surface.caller_reachable,
                "compiles_deletion": surface.compiles_deletion,
            }
            for surface in MATRIX_SURFACES
        ],
        "cells": cells,
    }


def render_matrix(matrix: dict[str, CanonicalValue]) -> str:
    """Render the coverage matrix as the reviewable checked-in file body."""
    return json.dumps(matrix, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def load_matrix() -> dict[str, CanonicalValue]:
    """Read the checked-in coverage matrix."""
    loaded: object = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("the checked-in deletion matrix is not a JSON object")
    return loaded


def write_matrix(matrix: dict[str, CanonicalValue]) -> None:
    """Overwrite the checked-in coverage matrix."""
    MATRIX_PATH.write_text(render_matrix(matrix), encoding="utf-8")
