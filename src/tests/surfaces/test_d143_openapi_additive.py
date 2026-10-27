"""D143 §8: the published OpenAPI document changes only by additions.

The layer toggles add one route (``GET /processing-layers``), an optional
``layers`` query parameter on the operation and SQL routes, and the schemas
those reference. Removing exactly those from today's export must give back the
pre-D143 document, byte for byte after canonical serialisation; its hash is
pinned here because the old document itself is a build product of history.
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
    "c2bce70429554311e8564e59b2429e371798da1712e3be6aa3bb1c44eff5578f"
)
"""Canonical hash of ``openapi.json`` at fbcd489bd (main just before D143's code)."""

_ADDED_ROUTES: Final = frozenset({"/processing-layers"})
_ADDED_SCHEMAS: Final = frozenset(
    {"ProcessingLayer", "ProcessingLayerStates", "OutputProcessingLayerStates"}
)


def without_d143_additions(document: dict[str, Any]) -> dict[str, Any]:
    """The document with the D143 route, ``layers`` parameters and schemas removed."""
    stripped = json.loads(json.dumps(document))
    for path in _ADDED_ROUTES:
        stripped["paths"].pop(path, None)
    for operations in stripped["paths"].values():
        for operation in operations.values():
            if not isinstance(operation, dict) or "parameters" not in operation:
                continue
            kept = [p for p in operation["parameters"] if p.get("name") != "layers"]
            if kept:
                operation["parameters"] = kept
            else:
                del operation["parameters"]
    schemas = stripped.get("components", {}).get("schemas", {})
    for name in _ADDED_SCHEMAS:
        schemas.pop(name, None)
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
