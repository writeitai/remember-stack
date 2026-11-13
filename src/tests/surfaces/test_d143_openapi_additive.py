"""D143 §8: the published OpenAPI document changes only by additions.

The layer toggles add one route (``GET /processing-layers``), an optional
``layers`` query parameter on the operation and SQL routes, and the schemas
those reference. Removing exactly those from today's export must give back the
pre-D143 document, byte for byte after canonical serialisation; its hash is
pinned here because the old document itself is a build product of history.
D150 adds one more enum value, ``admission``, to a readiness stage's
``defer_reason``; it is stripped the same way. D156 adds the tag routes, the
optional ``tags`` query parameter on ``POST /ingest``, the tag schemas and a
``tags`` field on document inventory and search results; those are stripped
too.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Any
from typing import Final

_ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(_ROOT / "scripts"))

_PRE_D143_SHA256: Final = (
    "8f404c9ce3a37b32791c54477be1d24d50450cddb7014216ada9ce470821d1ef"
)
"""Canonical hash of ``openapi.json`` after stripping D143 additions.

Pinned against the D154 pagination rename baseline (browse ``limit`` /
``next_cursor``, Envelope ``next_cursor``, Truncation without
``continuation``, QueryResult without ``exact_total*``, graph keyset
cursor capacity 2,048). Earlier pin was
``c2bce704…`` at fbcd489bd (main just before D143's code).
"""

_PRE_D143_VERSION: Final = "0.17.2"
"""``info.version`` in that document; a release bump is not a D143 change."""

_ADDED_ROUTES: Final = frozenset(
    {"/processing-layers", "/tags", "/tags/{tag_id}", "/documents/{doc_id}/tags"}
)
_ADDED_SCHEMAS: Final = frozenset(
    {
        "ProcessingLayer",
        "ProcessingLayerStates",
        "OutputProcessingLayerStates",
        # D156
        "DocumentTags",
        "DocumentTagsUpdate",
        "Tag",
        "TagCreate",
        "TagList",
        "TagUpdate",
    }
)
_D156_TAGGED_SCHEMAS: Final = ("DocumentSummary", "DocumentSearchResult")
"""Schemas that gained a ``tags`` property in D156."""


def without_d143_additions(document: dict[str, Any]) -> dict[str, Any]:
    """The document with the D143 route, ``layers`` parameters and schemas removed.

    ``info.version`` is reset to the pre-D143 release, so the pinned hash
    survives version bumps.
    """
    stripped = json.loads(json.dumps(document))
    stripped["info"]["version"] = _PRE_D143_VERSION
    for path in _ADDED_ROUTES:
        stripped["paths"].pop(path, None)
    for operations in stripped["paths"].values():
        for operation in operations.values():
            if not isinstance(operation, dict) or "parameters" not in operation:
                continue
            kept = [
                p
                for p in operation["parameters"]
                if p.get("name") != "layers"
                and not (p.get("name") == "tags" and p.get("in") == "query")
            ]
            if kept:
                operation["parameters"] = kept
            else:
                del operation["parameters"]
    schemas = stripped.get("components", {}).get("schemas", {})
    for name in _ADDED_SCHEMAS:
        schemas.pop(name, None)
    for name in tuple(schemas):
        if name.removeprefix("Output") in _ADDED_SCHEMAS:
            schemas.pop(name)
        elif name.removeprefix("Output") in _D156_TAGGED_SCHEMAS:
            schemas[name]["properties"].pop("tags")
            if "tags" in schemas[name].get("required", ()):
                schemas[name]["required"].remove("tags")
    reasons = schemas["PipelineStageReadiness"]["properties"]["defer_reason"]["anyOf"]
    reasons[0]["enum"] = [value for value in reasons[0]["enum"] if value != "admission"]
    return stripped


def canonical_sha256(document: dict[str, Any]) -> str:
    """Hash of the document with sorted keys and no insignificant whitespace."""
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def test_the_export_is_the_pre_d143_document_plus_its_additions() -> None:
    """Strip the additions and nothing else may differ (descriptions included)."""
    from export_openapi import build_document

    assert canonical_sha256(without_d143_additions(build_document())) == (
        _PRE_D143_SHA256
    )
