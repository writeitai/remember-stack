"""Offline §8 hybrid noninferiority gate for open query space cutover.

This module never calls a model, starts a benchmark run, or touches network-
billed evaluation. Operators collect same-condition arm metrics elsewhere and
feed them here. The real paid noninferiority run remains operator-gated.

The overall criterion is the already-collected lower 95% confidence bound of
the open-vs-legacy success *delta* (``success_delta_lower_95``), compared
directly to -2 absolute points. Product code does not invent or recompute a
CI from insufficient aggregates.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
from typing import Final
from typing import Mapping

#: Overall open-vs-legacy success delta lower 95% bound may be no worse than
#: this many absolute percentage points (§8).
OVERALL_LOWER_95_MIN_DELTA: Final = -2.0

#: Every critical category may be no worse than this many absolute points.
CRITICAL_CATEGORY_MIN_DELTA: Final = -5.0

#: p95 latency and metered cost may be at most this multiple of legacy.
LATENCY_COST_MAX_RATIO: Final = 1.25

#: Invalid/repaired SQL and Cypher rates each have this ceiling.
INVALID_LANGUAGE_RATE_MAX: Final = 0.05

_SHARED_ARM_KEYS: Final = frozenset(
    {
        "success_rate",
        "critical_categories",
        "d41_violations",
        "d48_violations",
        "d54_violations",
        "cross_deployment_violations",
        "p95_latency_ms",
        "metered_cost",
        "invalid_sql_rate",
        "invalid_cypher_rate",
        "caps_and_drops_visible",
    }
)

#: Open arm additionally requires the pre-computed success-delta lower bound.
_OPEN_ONLY_KEYS: Final = frozenset({"success_delta_lower_95"})


def estimate_paid_run(
    *, cases: int, arms: int = 2, calls_per_case: int = 1, unit_cost: float = 0.0
) -> dict[str, object]:
    """Surface case/arm/call totals and operator unit-cost before any paid run.

    Does not call a model or start a run. Operators supply unit cost in their
    own currency; this only multiplies the arithmetic. Rejects bools and
    non-integers for case/arm counts, and non-finite or negative unit cost.
    """
    cases_n = _require_nonneg_int(cases, field="cases")
    if isinstance(arms, bool) or not isinstance(arms, int) or arms < 1:
        raise ValueError("arms must be an integer >= 1")
    calls_n = _require_nonneg_int(calls_per_case, field="calls_per_case")
    cost = _require_nonneg_number(unit_cost, field="unit_cost")
    total_calls = cases_n * arms * calls_n
    return {
        "paid_run": False,
        "operator_gated": True,
        "note": (
            "This is an offline estimate only. The real paid noninferiority"
            " run remains operator-gated and is never started by this command."
        ),
        "cases": cases_n,
        "arms": arms,
        "calls_per_case": calls_n,
        "total_model_calls": total_calls,
        "unit_cost": cost,
        "estimated_total_cost": total_calls * cost,
        "arms_expected": ("legacy", "open_hybrid"),
        "deferred": (
            "A v10 open-only protocol is an explicit design deferral (§10);"
            " this machinery does not invent one."
        ),
    }


def load_arm_metrics(*, path: Path) -> dict[str, Any]:
    """Load already-collected same-condition arm metrics from a JSON file."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("metrics file must be a JSON object")
    return payload


def evaluate_noninferiority(*, metrics: Mapping[str, Any]) -> dict[str, object]:
    """Enforce the exact §8 hybrid noninferiority gates on collected metrics.

    Expected shape::

        {
          "legacy": { ...arm metrics... },
          "open": {
            ...arm metrics...,
            "success_delta_lower_95": <already-collected lower 95% of open-vs-legacy success delta>
          }
        }

    Arm metrics keys are listed in ``_SHARED_ARM_KEYS``; the open arm also
    requires ``success_delta_lower_95``. Returns a report with ``passed`` and
    per-gate results. Never starts a run and never invents a CI.
    """
    legacy = _require_arm(metrics, "legacy", open_arm=False)
    open_arm = _require_arm(metrics, "open", open_arm=True)
    gates: list[dict[str, object]] = []

    # Overall: the pre-collected lower 95% bound of the success delta vs -2.
    delta_lower = float(open_arm["success_delta_lower_95"])
    gates.append(
        _gate(
            name="overall_success_delta_lower_95",
            passed=delta_lower >= OVERALL_LOWER_95_MIN_DELTA,
            detail={
                "success_delta_lower_95": delta_lower,
                "minimum": OVERALL_LOWER_95_MIN_DELTA,
            },
        )
    )

    critical_ok = True
    critical_detail: dict[str, object] = {}
    legacy_cats = legacy["critical_categories"]
    open_cats = open_arm["critical_categories"]
    assert isinstance(legacy_cats, Mapping) and isinstance(open_cats, Mapping)
    for name, legacy_rate in legacy_cats.items():
        open_rate = open_cats.get(name)
        if open_rate is None:
            critical_ok = False
            critical_detail[str(name)] = {"error": "missing in open arm"}
            continue
        delta = float(open_rate) - float(legacy_rate)
        critical_detail[str(name)] = {
            "legacy": legacy_rate,
            "open": open_rate,
            "delta_points": delta,
            "minimum": CRITICAL_CATEGORY_MIN_DELTA,
        }
        if delta < CRITICAL_CATEGORY_MIN_DELTA:
            critical_ok = False
    gates.append(
        _gate(name="critical_categories", passed=critical_ok, detail=critical_detail)
    )

    for code in (
        "d41_violations",
        "d48_violations",
        "d54_violations",
        "cross_deployment_violations",
    ):
        value = int(open_arm[code])
        gates.append(
            _gate(name=code, passed=value == 0, detail={"open": value, "required": 0})
        )

    for metric, label in (
        ("p95_latency_ms", "p95_latency"),
        ("metered_cost", "metered_cost"),
    ):
        legacy_value = float(legacy[metric])
        open_value = float(open_arm[metric])
        ratio = (
            math.inf
            if legacy_value == 0 and open_value > 0
            else (1.0 if legacy_value == 0 else open_value / legacy_value)
        )
        gates.append(
            _gate(
                name=label,
                passed=ratio <= LATENCY_COST_MAX_RATIO,
                detail={
                    "legacy": legacy_value,
                    "open": open_value,
                    "ratio": ratio,
                    "maximum": LATENCY_COST_MAX_RATIO,
                },
            )
        )

    for rate_key in ("invalid_sql_rate", "invalid_cypher_rate"):
        rate = float(open_arm[rate_key])
        gates.append(
            _gate(
                name=rate_key,
                passed=rate <= INVALID_LANGUAGE_RATE_MAX,
                detail={"open": rate, "maximum": INVALID_LANGUAGE_RATE_MAX},
            )
        )

    caps_visible = open_arm["caps_and_drops_visible"]
    assert isinstance(caps_visible, bool)
    gates.append(
        _gate(
            name="caps_and_drops_visible",
            passed=caps_visible is True,
            detail={"open": caps_visible, "required": True},
        )
    )

    passed = all(bool(gate["passed"]) for gate in gates)
    return {
        "passed": passed,
        "gates": gates,
        "paid_run": False,
        "note": (
            "Offline evaluation of already-collected metrics only."
            " Failing a gate keeps the dual surface active (§8)."
            " The real paid noninferiority run remains operator-gated."
        ),
    }


def _require_arm(
    metrics: Mapping[str, Any], name: str, *, open_arm: bool
) -> dict[str, Any]:
    """Return one arm's metrics object after shape and range validation."""
    arm = metrics.get(name)
    if not isinstance(arm, Mapping):
        raise ValueError(f"metrics must include an object arm named {name!r}")
    required = set(_SHARED_ARM_KEYS)
    if open_arm:
        required |= _OPEN_ONLY_KEYS
    missing = sorted(required - set(arm))
    if missing:
        raise ValueError(f"arm {name!r} is missing keys: {missing}")
    validated = dict(arm)
    _validate_arm_values(name=name, arm=validated, open_arm=open_arm)
    return validated


def _validate_arm_values(*, name: str, arm: Mapping[str, Any], open_arm: bool) -> None:
    """Reject non-finite, out-of-range, or wrong-typed arm metrics."""
    _require_rate_0_100(arm["success_rate"], field=f"{name}.success_rate")
    if open_arm:
        _require_finite_number(
            arm["success_delta_lower_95"], field=f"{name}.success_delta_lower_95"
        )

    cats = arm["critical_categories"]
    if not isinstance(cats, Mapping) or not cats:
        raise ValueError(f"{name}.critical_categories must be a non-empty object")
    for cat_name, rate in cats.items():
        _require_rate_0_100(rate, field=f"{name}.critical_categories.{cat_name}")

    for code in (
        "d41_violations",
        "d48_violations",
        "d54_violations",
        "cross_deployment_violations",
    ):
        _require_nonneg_int(arm[code], field=f"{name}.{code}")

    for metric in ("p95_latency_ms", "metered_cost"):
        _require_nonneg_number(arm[metric], field=f"{name}.{metric}")

    for rate_key in ("invalid_sql_rate", "invalid_cypher_rate"):
        _require_rate_0_1(arm[rate_key], field=f"{name}.{rate_key}")

    caps = arm["caps_and_drops_visible"]
    if not isinstance(caps, bool):
        raise ValueError(f"{name}.caps_and_drops_visible must be a boolean")


def _require_finite_number(value: object, *, field: str) -> float:
    """Accept a real finite number (not bool)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _require_rate_0_100(value: object, *, field: str) -> float:
    """Success/category rates are absolute percentage points in 0..100."""
    number = _require_finite_number(value, field=field)
    if not 0.0 <= number <= 100.0:
        raise ValueError(f"{field} must be in 0..100")
    return number


def _require_rate_0_1(value: object, *, field: str) -> float:
    """Invalid-language rates are fractions in 0..1."""
    number = _require_finite_number(value, field=field)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{field} must be in 0..1")
    return number


def _require_nonneg_number(value: object, *, field: str) -> float:
    """Latency and cost must be finite and nonnegative."""
    number = _require_finite_number(value, field=field)
    if number < 0:
        raise ValueError(f"{field} must be nonnegative")
    return number


def _require_nonneg_int(value: object, *, field: str) -> int:
    """Violation counts must be nonnegative non-bool integers."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be a nonnegative integer")
    if value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _gate(
    *, name: str, passed: bool, detail: Mapping[str, object]
) -> dict[str, object]:
    """Build one named gate result for the offline noninferiority report."""
    return {"name": name, "passed": passed, "detail": dict(detail)}
