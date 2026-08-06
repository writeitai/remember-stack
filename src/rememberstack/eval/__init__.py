"""Evaluation package: the D22 harness and the golden suites."""

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
from rememberstack.eval.open_query_noninferiority import estimate_paid_run
from rememberstack.eval.open_query_noninferiority import evaluate_noninferiority
from rememberstack.eval.open_query_noninferiority import load_arm_metrics
from rememberstack.eval.operational_scale import OPERATIONAL_SCALE_VERSION
from rememberstack.eval.operational_scale import record_operational_scale_report
from rememberstack.eval.resolution import PRECISION_FLOOR
from rememberstack.eval.resolution import RECALL_FLOOR
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
    "record_retrieval_spike_report",
    "record_operational_scale_report",
    "RETRIEVAL_SPIKE_VERSION",
    "run_resolution_suite",
    "seed_synthetic_golden_pairs",
    "SKELETON_CANARIES",
    "make_skeleton_evaluator",
    "seed_skeleton_canaries",
    "estimate_paid_run",
    "evaluate_noninferiority",
    "load_arm_metrics",
)
