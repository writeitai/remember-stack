"""D143 §8 / §9.1: an unconfigured deployment's default surfaces are pre-D143 bytes.

``fixtures/d143_defaults.json`` was captured by
:func:`tests.surfaces.d143_defaults_capture.capture_defaults`. It holds the
serialized ``GET /operations`` list, the ``GET /deployment`` body with its
tools map, the in-process MCP tool list and the ``remember-mcp`` engine-mode
tool lists (names and input schemas), the readiness expected stages with
their component versions, and the query-surface manifest hash. Originally
taken on the pre-D143 base (fbcd489bd); refreshed for the p9_42/p9_45/D153
view optimization and again for D154's pagination rename (browse ``limit`` /
``next_cursor``, MCP tool schemas), and for D156 document tags (five tag
tools, ``ingest`` ``tags`` at ``tool_version`` 3, two tag views; then the
``search_documents`` ``tags`` filter at ``tool_version`` 4). The current branch, composed the way the
self-host profile composes it (layers port included, reporting no
configuration), must reproduce every byte.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.surfaces.d143_defaults_capture import capture_defaults
from tests.surfaces.d143_defaults_capture import capture_layers_off
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
    assert len(baseline["deployment"]["tools"]) == 23
    assert len(baseline["mcp_in_process_tools"]) == 23
    assert len(baseline["mcp_engine_tools"]["stdio"]) == 23
    assert len(baseline["readiness_expected_components"]) == 12
    assert len(baseline["query_surface_manifest"]["surface_manifest_hash"]) == 64


def test_layers_off_catalogues_equal_generated_capture() -> None:
    """Source discovery and its D155 guard survive either higher-layer switch."""
    fixture = _FIXTURE.with_name("d155_layers_off_catalogues.json")
    current = capture_layers_off()
    assert render(current) == fixture.read_text()
    for profile in current.values():
        for tools in (
            profile["mcp_in_process_tools"],
            *profile["mcp_engine_tools"].values(),
        ):
            search = next(tool for tool in tools if tool["name"] == "search_documents")
            assert "matching publication marker" in search["description"]
            assert "only the suffix is stable" in search["description"]
