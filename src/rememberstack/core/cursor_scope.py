"""Shared browse-cursor scope binding (D154).

A cursor may only continue the listing it came from. Callers put a short hash
of every filter that defines the result set into the cursor and refuse a
cursor whose hash does not match the current request. ``limit`` and ``cursor``
themselves are never part of the scope.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from typing import Mapping


def cursor_scope_hash(scope: Mapping[str, Any]) -> str:
    """Return a short, stable hash of the listing scope.

    ``scope`` must include every request field that defines membership of the
    result set (filters, ids, clocks, versions, time selectors).
    """
    payload = json.dumps(
        scope, sort_keys=True, separators=(",", ":"), default=_json_default
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def assert_cursor_scope(*, payload_scope: object, expected_scope: str) -> None:
    """Raise ``ValueError`` when the cursor's scope hash does not match."""
    if payload_scope != expected_scope:
        raise ValueError("cursor belongs to a different listing")


def _json_default(value: object) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)  # type: ignore[no-any-return]
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)
