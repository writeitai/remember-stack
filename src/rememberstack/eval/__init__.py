"""Evaluation package: the D22 harness and the golden suites.

Exports are loaded lazily so base-wheel consumers (for example
the remote CLI) do not import SQLAlchemy or
other server-only modules unless they actually touch those symbols.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any
from typing import Final
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Present for type checkers / __all__; runtime uses ``__getattr__``.
    from rememberstack.eval.consumption import make_retrieval_evaluator
    from rememberstack.eval.consumption import make_s58_evaluator
    from rememberstack.eval.consumption import S58_CANARIES
    from rememberstack.eval.consumption import seed_s58_canaries
    from rememberstack.eval.contradiction import CONTRADICTION_PRECISION_FLOOR
    from rememberstack.eval.contradiction import CONTRADICTION_RECALL_FLOOR
    from rememberstack.eval.contradiction import run_contradiction_suite
    from rememberstack.eval.contradiction import seed_contradiction_cases
    from rememberstack.eval.harness import CaseEvaluator
    from rememberstack.eval.harness import EvalHarness
    from rememberstack.eval.lifecycle import flag_rate_by_extractor
    from rememberstack.eval.lifecycle import register_lifecycle_evaluator
    from rememberstack.eval.lifecycle import run_lifecycle_suite
    from rememberstack.eval.operational_scale import OPERATIONAL_SCALE_VERSION
    from rememberstack.eval.operational_scale import record_operational_scale_report
    from rememberstack.eval.resolution import PRECISION_FLOOR
    from rememberstack.eval.resolution import RECALL_FLOOR
    from rememberstack.eval.resolution import ResolutionSuiteRecordError
    from rememberstack.eval.resolution import run_resolution_suite
    from rememberstack.eval.resolution import seed_synthetic_golden_pairs
    from rememberstack.eval.retrieval_spikes import record_retrieval_spike_report
    from rememberstack.eval.retrieval_spikes import RETRIEVAL_SPIKE_VERSION
    from rememberstack.eval.skeleton import make_skeleton_evaluator
    from rememberstack.eval.skeleton import seed_skeleton_canaries
    from rememberstack.eval.skeleton import SKELETON_CANARIES

__all__ = (
    "CONTRADICTION_PRECISION_FLOOR",
    "CONTRADICTION_RECALL_FLOOR",
    "CaseEvaluator",
    "make_retrieval_evaluator",
    "make_s58_evaluator",
    "run_contradiction_suite",
    "flag_rate_by_extractor",
    "register_lifecycle_evaluator",
    "run_lifecycle_suite",
    "seed_contradiction_cases",
    "seed_s58_canaries",
    "S58_CANARIES",
    "EvalHarness",
    "OPERATIONAL_SCALE_VERSION",
    "PRECISION_FLOOR",
    "RECALL_FLOOR",
    "ResolutionSuiteRecordError",
    "record_retrieval_spike_report",
    "record_operational_scale_report",
    "RETRIEVAL_SPIKE_VERSION",
    "run_resolution_suite",
    "seed_synthetic_golden_pairs",
    "SKELETON_CANARIES",
    "make_skeleton_evaluator",
    "seed_skeleton_canaries",
)

#: Public name → (module path, attribute name) for lazy package exports.
_EXPORTS: Final[dict[str, tuple[str, str]]] = {
    "CONTRADICTION_PRECISION_FLOOR": (
        "rememberstack.eval.contradiction",
        "CONTRADICTION_PRECISION_FLOOR",
    ),
    "CONTRADICTION_RECALL_FLOOR": (
        "rememberstack.eval.contradiction",
        "CONTRADICTION_RECALL_FLOOR",
    ),
    "CaseEvaluator": ("rememberstack.eval.harness", "CaseEvaluator"),
    "make_retrieval_evaluator": (
        "rememberstack.eval.consumption",
        "make_retrieval_evaluator",
    ),
    "make_s58_evaluator": ("rememberstack.eval.consumption", "make_s58_evaluator"),
    "run_contradiction_suite": (
        "rememberstack.eval.contradiction",
        "run_contradiction_suite",
    ),
    "flag_rate_by_extractor": (
        "rememberstack.eval.lifecycle",
        "flag_rate_by_extractor",
    ),
    "register_lifecycle_evaluator": (
        "rememberstack.eval.lifecycle",
        "register_lifecycle_evaluator",
    ),
    "run_lifecycle_suite": ("rememberstack.eval.lifecycle", "run_lifecycle_suite"),
    "seed_contradiction_cases": (
        "rememberstack.eval.contradiction",
        "seed_contradiction_cases",
    ),
    "seed_s58_canaries": ("rememberstack.eval.consumption", "seed_s58_canaries"),
    "S58_CANARIES": ("rememberstack.eval.consumption", "S58_CANARIES"),
    "EvalHarness": ("rememberstack.eval.harness", "EvalHarness"),
    "OPERATIONAL_SCALE_VERSION": (
        "rememberstack.eval.operational_scale",
        "OPERATIONAL_SCALE_VERSION",
    ),
    "PRECISION_FLOOR": ("rememberstack.eval.resolution", "PRECISION_FLOOR"),
    "RECALL_FLOOR": ("rememberstack.eval.resolution", "RECALL_FLOOR"),
    "ResolutionSuiteRecordError": (
        "rememberstack.eval.resolution",
        "ResolutionSuiteRecordError",
    ),
    "record_retrieval_spike_report": (
        "rememberstack.eval.retrieval_spikes",
        "record_retrieval_spike_report",
    ),
    "record_operational_scale_report": (
        "rememberstack.eval.operational_scale",
        "record_operational_scale_report",
    ),
    "RETRIEVAL_SPIKE_VERSION": (
        "rememberstack.eval.retrieval_spikes",
        "RETRIEVAL_SPIKE_VERSION",
    ),
    "run_resolution_suite": ("rememberstack.eval.resolution", "run_resolution_suite"),
    "seed_synthetic_golden_pairs": (
        "rememberstack.eval.resolution",
        "seed_synthetic_golden_pairs",
    ),
    "SKELETON_CANARIES": ("rememberstack.eval.skeleton", "SKELETON_CANARIES"),
    "make_skeleton_evaluator": (
        "rememberstack.eval.skeleton",
        "make_skeleton_evaluator",
    ),
    "seed_skeleton_canaries": ("rememberstack.eval.skeleton", "seed_skeleton_canaries"),
}


def __getattr__(name: str) -> Any:
    """Load one public export on first access; cache it on the package module."""
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy exports to ``dir(rememberstack.eval)`` and autocomplete."""
    return sorted({*globals().keys(), *__all__})
