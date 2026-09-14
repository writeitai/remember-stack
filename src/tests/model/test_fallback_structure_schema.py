"""Fallback wire field is subsections; Python tree remains children."""

import json

from rememberstack.adapters.openrouter import _strict_json_schema
from rememberstack.model import FallbackAnchor
from rememberstack.model import FallbackStructureResponse
from rememberstack.workers.e0 import _FALLBACK_PROMPT
from rememberstack.workers.e0 import E0_SKELETON_VERSION
from rememberstack.workers.e0 import E0_STRUCTURE_VERSION


def test_strict_schema_exposes_subsections_not_children() -> None:
    """The constrained decoder sees subsections, which sorts after occurrence_index."""
    schema = _strict_json_schema(FallbackStructureResponse)
    defs = schema.get("$defs") or schema.get("definitions") or {}
    anchor = defs["FallbackAnchor"]
    properties = anchor["properties"]
    assert "children" not in properties
    assert list(properties) == ["anchor", "occurrence_index", "subsections"]
    assert list(anchor["required"]) == ["anchor", "occurrence_index", "subsections"]
    items = properties["subsections"]["items"]
    ref = items.get("$ref")
    if ref is None:
        options = items.get("anyOf", [])
        ref = options[0].get("$ref") if options else None
    assert isinstance(ref, str) and ref.endswith("FallbackAnchor")


def test_nested_subsections_json_preserves_indices_and_internal_children() -> None:
    """Live Gemma wire JSON maps onto the existing internal tree."""
    payload = {
        "sections": [
            {
                "anchor": "LoCoMo conv-42 — session D1",
                "occurrence_index": 0,
                "subsections": [
                    {
                        "anchor": "Nate: Hey Joanna!",
                        "occurrence_index": 0,
                        "subsections": [],
                    },
                    {
                        "anchor": "Nate: I love action and sci-fi movies",
                        "occurrence_index": 1,
                        "subsections": [],
                    },
                ],
            }
        ]
    }
    output = FallbackStructureResponse.model_validate_json(json.dumps(payload))
    root = output.sections[0]
    assert root.anchor == "LoCoMo conv-42 — session D1"
    assert root.occurrence_index == 0
    assert len(root.children) == 2
    assert root.children[0].anchor == "Nate: Hey Joanna!"
    assert root.children[0].occurrence_index == 0
    assert root.children[0].children == ()
    assert root.children[1].occurrence_index == 1


def test_internal_children_construction_still_works() -> None:
    """Python callers keep the existing children attribute."""
    nested = FallbackAnchor(
        anchor="Child marker",
        occurrence_index=0,
        children=(),
    )
    parent = FallbackAnchor(
        anchor="Parent marker",
        occurrence_index=2,
        children=(nested,),
    )
    assert parent.children[0] is nested
    assert parent.model_dump(by_alias=True)["subsections"][0]["anchor"] == "Child marker"


def test_fallback_prompt_names_subsections() -> None:
    """The human-readable prompt matches the strict schema field name."""
    assert "- subsections: nested nodes using the same shape" in _FALLBACK_PROMPT
    assert "- children:" not in _FALLBACK_PROMPT


def test_structure_generation_identity_includes_subsections_contract() -> None:
    """Work ledger and skeleton cache keys change with the fallback wire field."""
    assert E0_STRUCTURE_VERSION == "e0-structure-2026.07g:d79-wave2"
    assert ":anchor-v2-depth" in E0_SKELETON_VERSION
    assert ":anchor-v1-depth" not in E0_SKELETON_VERSION
