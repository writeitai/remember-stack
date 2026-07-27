"""#154: extraction-class model calls pin temperature=0.0 (measurement surface)."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SCANNED_TREES = (
    _ROOT / "src/rememberstack/workers",
    _ROOT / "src/rememberstack/spine",
)
"""Every module under workers/ and spine/ is scanned — a new extraction-class
module is covered the day it appears, without editing this list. Benchmarks'
answer-agent / judge calls pin temperature separately; eval/ is a consumption
harness, not extraction."""


def _extraction_modules() -> list[Path]:
    """Every python module under the scanned trees, discovered, not listed."""
    return sorted(path for tree in _SCANNED_TREES for path in tree.rglob("*.py"))


def _model_request_temperatures(*, path: Path) -> list[float | None]:
    """Return the temperature keyword value for each ModelRequest(...) call.

    Matches both the bare name and any attribute-qualified spelling
    (`model.ModelRequest(...)`) so an import alias cannot dodge the scan.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    temperatures: list[float | None] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        name = (
            callee.id
            if isinstance(callee, ast.Name)
            else callee.attr
            if isinstance(callee, ast.Attribute)
            else None
        )
        if name != "ModelRequest":
            continue
        temperature: float | None = None
        for keyword in node.keywords:
            if keyword.arg != "temperature":
                continue
            if not isinstance(keyword.value, ast.Constant):
                raise AssertionError(
                    f"{path}: ModelRequest temperature must be a constant, got"
                    f" {ast.dump(keyword.value)}"
                )
            value = keyword.value.value
            if not isinstance(value, (int, float)):
                raise AssertionError(
                    f"{path}: ModelRequest temperature must be numeric, got {value!r}"
                )
            temperature = float(value)
        temperatures.append(temperature)
    return temperatures


def test_extraction_model_request_sites_pin_temperature_zero() -> None:
    """Structurer, prefix, Claimify, normalize, labels, adjudicators: temp=0.0.

    Extraction is measurement, not creativity. Leaving temperature unset left
    gold-span coverage wandering 7/8 → 4/8 across identical-binding runs
    (issue #154). This AST check fails closed if a new extraction-class
    ModelRequest is added without the pin.
    """
    modules = _extraction_modules()
    assert len(modules) >= 8, "workers/ and spine/ trees moved — fix the scan roots"
    seen = 0
    for path in modules:
        for temperature in _model_request_temperatures(path=path):
            assert temperature == 0.0, (
                f"{path}: ModelRequest temperature={temperature!r}, expected 0.0"
            )
            seen += 1
    # Guard against vacuous green: the known extraction surface has many calls.
    assert seen >= 15
