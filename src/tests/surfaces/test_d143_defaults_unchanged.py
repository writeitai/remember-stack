"""D143 §8 / §9.1: an unconfigured deployment's default surfaces are pre-D143 bytes.

``fixtures/d143_defaults.json`` was captured by
:func:`tests.surfaces.d143_defaults_capture.capture_defaults` on the pre-D143
base (615c942c). It holds the serialized ``GET /operations`` list, the
``GET /deployment`` body with its tools map, the in-process MCP tool list and
the ``remember-mcp`` engine-mode tool lists (names and input schemas), the
readiness expected stages with their component versions, and the
query-surface manifest hash. The current branch, composed the way the
self-host profile composes it (layers port included, reporting no
configuration), must reproduce every byte.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.surfaces.d143_defaults_capture import capture_defaults
from tests.surfaces.d143_defaults_capture import render

_FIXTURE = Path(__file__).with_name("fixtures") / "d143_defaults.json"


def test_unconfigured_defaults_equal_the_pre_d143_capture() -> None:
    """§9.1: every captured default is byte-identical to the pre-D143 base."""
    expected = _FIXTURE.read_text(encoding="utf-8")
    current = capture_defaults()
    baseline = json.loads(expected)
    # Member by member first, so a failure names the surface that drifted.
    assert set(current) == set(baseline)
    for member, value in baseline.items():
        assert current[member] == value, member
    assert render(current) == expected


def test_fixture_pins_the_full_default_catalogue() -> None:
    """The fixture is not vacuous: four operations, every tool, twelve stages."""
    baseline = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert [item["name"] for item in baseline["operations"]] == [
        "resolve_entity",
        "claims_and_sources_context",
        "facts_context",
        "combined_context",
    ]
    assert len(baseline["deployment"]["tools"]) == 18
    assert len(baseline["mcp_in_process_tools"]) == 18
    assert len(baseline["mcp_engine_tools"]["stdio"]) == 18
    assert len(baseline["readiness_expected_components"]) == 12
    assert len(baseline["query_surface_manifest"]["surface_manifest_hash"]) == 64
