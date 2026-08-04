"""The enumerated D48 deletion coverage matrix, generated and versioned.

"Deletion is fail-closed across every view" is only a real claim if the cases
are enumerated rather than sampled. This module enumerates them: six deletion
targets crossed with every public relation, one cell each, checked into the
repository as a reviewable artifact and executed in full by the schema gate.

Each cell asserts the same mechanical property, which is what makes the matrix
generatable instead of hand-written: **after the target's mutation, no row of
the surface carries any identifier from that target's forbidden set.** The
forbidden set is collected from the fixture corpus before the mutation, so a
cell cannot pass by accident — a leak anywhere in any column of any row of any
relation fails it.

The targets are chosen so that one global forbidden set is correct for every
surface. That matters for the representation target in particular: superseding
a reading is only a deletion for content derived from it, so its fixture
lineage deliberately carries no claims, and the claims of a superseded reading
staying visible elsewhere is correct D54/D55 behaviour rather than a leak.
"""

import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel
from pydantic import ConfigDict

from rememberstack.spine.query_space.canonical import CanonicalValue
from rememberstack.spine.query_space.catalog import QUERY_SPACE_SCHEMA
from rememberstack.spine.query_space.catalog import VIEW_CONTRACTS

#: Identifier of this artifact's layout.
MATRIX_CONTRACT: Final = "memory_v1.d48_deletion_matrix/1"

#: The checked-in coverage artifact the schema gate executes cell by cell.
MATRIX_PATH: Final = Path(__file__).with_name("d48_deletion_matrix.json")

#: The uniform property every cell asserts.
CELL_EXPECTATION: Final = (
    "After the mutation, no row of this relation carries any identifier from "
    "the target's forbidden set."
)


class DeletionTarget(BaseModel):
    """One enumerated deletion the whole query space must survive."""

    model_config = ConfigDict(frozen=True)

    target_id: str
    summary: str
    mutation: str
    forbidden_identifiers: str


DELETION_TARGETS: Final = (
    DeletionTarget(
        target_id="lineage",
        summary="A live document lineage is forgotten.",
        mutation="Set documents.deleted_at on the fixture lineage.",
        forbidden_identifiers=(
            "The lineage id and every identifier derived from it: its versions, "
            "representations, sections, chunks, claims, mentions, cross-references, "
            "testimony-currency events, the entity whose only provenance it was, and "
            "the fact whose only evidence lineage it was."
        ),
    ),
    DeletionTarget(
        target_id="version",
        summary="One non-current version of a live lineage is tombstoned.",
        mutation="Set document_versions.deleted_at on the fixture version.",
        forbidden_identifiers=(
            "The version id and every identifier derived from it: its representation, "
            "sections, chunks, claims, and mentions. The facts those claims evidenced "
            "keep their surviving historical provenance and are deliberately not "
            "forbidden."
        ),
    ),
    DeletionTarget(
        target_id="representation",
        summary="The current ready reading of a live version is superseded.",
        mutation=(
            "Insert a new ready representation, point the version's current "
            "representation at it, and leave the previous reading in place."
        ),
        forbidden_identifiers=(
            "The superseded representation id and the sections and chunks cut from "
            "it. The fixture lineage carries no claims, so no testimony legitimately "
            "outlives the reading and the forbidden set stays global."
        ),
    ),
    DeletionTarget(
        target_id="claim",
        summary="One claim is purged from the testimony record.",
        mutation="Delete the claim row and its chunk occurrences.",
        forbidden_identifiers=(
            "The claim id. The fact it evidenced keeps its association row and stays "
            "visible with a reduced count, which is the D54 behaviour rather than a leak."
        ),
    ),
    DeletionTarget(
        target_id="fact_provenance",
        summary="Every evidence lineage of one fact is removed.",
        mutation="Delete the fact's relation or observation evidence rows.",
        forbidden_identifiers=(
            "The fact id, which must disappear from the fact relations, the evidence "
            "bridge, the contradiction and graph projections, and the change feed."
        ),
    ),
    DeletionTarget(
        target_id="k_target",
        summary="The lineage a knowledge page cites is forgotten.",
        mutation="Set documents.deleted_at on the cited lineage.",
        forbidden_identifiers=(
            "The cited lineage id and its derived identifiers. The citing page stays "
            "visible; only the citation leaves, which is what proves a chunk-content "
            "hash locator cannot authorize a read on its own."
        ),
    ),
)

DELETION_TARGETS_BY_ID: Final = {
    target.target_id: target for target in DELETION_TARGETS
}


def build_matrix() -> dict[str, CanonicalValue]:
    """Enumerate every target × surface cell in a stable order."""
    surfaces = sorted(contract.name for contract in VIEW_CONTRACTS)
    return {
        "contract": MATRIX_CONTRACT,
        "schema": QUERY_SPACE_SCHEMA,
        "expectation": CELL_EXPECTATION,
        "target_count": len(DELETION_TARGETS),
        "surface_count": len(surfaces),
        "cell_count": len(DELETION_TARGETS) * len(surfaces),
        "targets": [
            {
                "target_id": target.target_id,
                "summary": target.summary,
                "mutation": target.mutation,
                "forbidden_identifiers": target.forbidden_identifiers,
            }
            for target in DELETION_TARGETS
        ],
        "surfaces": [surface for surface in surfaces],
        "cells": [
            {
                "target_id": target.target_id,
                "surface": f"{QUERY_SPACE_SCHEMA}.{surface}",
            }
            for target in DELETION_TARGETS
            for surface in surfaces
        ],
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
