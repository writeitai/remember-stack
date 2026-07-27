"""#154: extraction-class model calls pin temperature=0.0 (measurement surface)."""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_EXTRACTION_MODULES = (
    _ROOT / "src/rememberstack/workers/e0.py",
    _ROOT / "src/rememberstack/workers/e1.py",
    _ROOT / "src/rememberstack/workers/e2.py",
    _ROOT / "src/rememberstack/workers/e3.py",
    _ROOT / "src/rememberstack/workers/p1.py",
    _ROOT / "src/rememberstack/spine/observation_adjudication.py",
    _ROOT / "src/rememberstack/spine/resolver.py",
    _ROOT / "src/rememberstack/spine/supersession.py",
)
"""Every E-layer / P1 extract-or-label / adjudication generate path.

Deliberately excludes p2_analytics community labels and benchmarks' answer-
agent / judge calls (those already pin temperature separately).
"""


def _model_request_temperatures(*, path: Path) -> list[float | None]:
    """Return the temperature keyword value for each ModelRequest(...) call."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    temperatures: list[float | None] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        if not isinstance(callee, ast.Name) or callee.id != "ModelRequest":
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
    seen = 0
    for path in _EXTRACTION_MODULES:
        temps = _model_request_temperatures(path=path)
        assert temps, f"expected ModelRequest call sites in {path}"
        for temperature in temps:
            assert temperature == 0.0, (
                f"{path}: ModelRequest temperature={temperature!r}, expected 0.0"
            )
        seen += len(temps)
    # Guard against vacuous green: the known extraction surface has many calls.
    assert seen >= 12
