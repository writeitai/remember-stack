"""The ER golden-set suite (WP-I.3, D22/D95): global P/R over golden pairs.

Runs the cascade's decision function over every human-adjudicated pair,
computes one global precision/recall curve, and retains both blocking-stratum
and deciding-tier diagnostics. The result is recorded on the
`resolver_versions` row (the acceptance home) and in `eval_runs`. No threshold
ships as final without this curve; the floors here are starting points to
tighten as the golden set grows.
"""

from typing import Final
from typing import TypedDict
from uuid import UUID
from uuid import uuid4
from uuid import uuid5

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.spine.resolver import CascadeResolver

PRECISION_FLOOR: Final = 0.90
"""Suite-blocking global precision floor (starting point, D22/D96)."""

RECALL_FLOOR: Final = 0.80
"""Suite-blocking global recall floor (starting point, D22/D96)."""

_PAIR_NAMESPACE: Final = UUID("601de77a-0000-4000-8000-000000000000")


class ResolutionCurve(TypedDict):
    """The single suite-blocking global precision/recall curve."""

    precision: float | None
    recall: float | None
    pairs: int
    false_merges: int
    false_splits: int


class TierDiagnostic(TypedDict):
    """Non-gating error attribution for one blocking or deciding tier."""

    pairs: int
    correct: int
    false_merges: int
    false_splits: int


class TierDiagnostics(TypedDict):
    """Diagnostics grouped by expected blocking and actual deciding tier."""

    deciding: dict[str, TierDiagnostic]
    blocking: dict[str, TierDiagnostic]


class ResolutionGateGuards(TypedDict):
    """Non-curve safety conditions required before thresholds can pass."""

    positive_labels_measured: bool
    negative_labels_measured: bool
    t0_negative_canary_measured: bool
    t0_false_merge_free: bool


class ResolutionSuiteReport(TypedDict):
    """The complete result of one resolution golden-set run."""

    curve: ResolutionCurve
    tier_diagnostics: TierDiagnostics
    gate_guards: ResolutionGateGuards
    passed: bool


SYNTHETIC_GOLDEN_PAIRS: Final[tuple[dict[str, object], ...]] = (
    # exact / near-exact strata
    {
        "surface_a": "Acme Corporation",
        "surface_b": "Acme Corp",
        "label": "match",
        "hardness": "easy",
        "expected_blocking_tier": "T1",
        "context_a": "Acme Corporation, the industrial supplier.",
        "context_b": "Acme Corp announced quarterly results.",
    },
    {
        "surface_a": "Acme Corporation",
        "surface_b": "Zenith Industries",
        "label": "no_match",
        "hardness": "easy",
        "expected_blocking_tier": None,
        "context_a": None,
        "context_b": None,
    },
    # the Czech slice (registries §5): diacritics, inflection, family names
    {
        "surface_a": "Pavel Kovář",
        "surface_b": "Pavel Kovar",
        "label": "match",
        "hardness": "easy",
        "expected_blocking_tier": "T0",  # unaccent folds the diacritic
        "context_a": "Pavel Kovář of the Brno office.",
        "context_b": "an email signed Pavel Kovar, Brno office",
    },
    {
        "surface_a": "Jan Novák",
        "surface_b": "Jana Nováková",
        "label": "no_match",  # feminine surname: typically a different person
        "hardness": "hard_negative",
        "expected_blocking_tier": "T1",
        "context_a": "Jan Novák, the finance director.",
        "context_b": "Jana Nováková from the legal team.",
    },
    {
        "surface_a": "Petr Svoboda",
        "surface_b": "Petra Svobodu",  # accusative inflection of a NAME variant
        "label": "no_match",
        "hardness": "hard_negative",
        "expected_blocking_tier": "T1",
        "context_a": "Petr Svoboda leads the platform team.",
        "context_b": "the committee appointed Petra Svobodu",
    },
    {
        "surface_a": "Karel Dvořák",
        "surface_b": "Karel Dvorzak",  # phonetic spelling drift
        "label": "match",
        "hardness": "hard_positive",
        "expected_blocking_tier": "T2",
        "context_a": "Karel Dvořák, the composer's namesake in sales.",
        "context_b": "meeting notes mention Karel Dvorzak from sales",
    },
    # D95's load-bearing counterexamples: T0 reaches them but never decides.
    {
        "surface_a": "John Smith",
        "surface_b": "John Smith",
        "label": "no_match",
        "hardness": "hard_negative",
        "expected_blocking_tier": "T0",
        "context_a": "John Smith, the retired father living in Bristol.",
        "context_b": "John Smith, his son and an engineer living in Leeds.",
    },
    {
        "surface_a": "John",
        "surface_b": "John",
        "label": "no_match",
        "hardness": "hard_negative",
        "expected_blocking_tier": "T0",
        # This deliberately has no distinguishing evidence. T3 must not turn
        # identical name-only vectors into certainty; T4 remains fail-safe.
        "context_a": None,
        "context_b": None,
    },
)


def seed_synthetic_golden_pairs(*, engine: Engine, deployment_id: UUID) -> None:
    """Insert or refresh the synthetic starter pairs (stable ids).

    These bootstrap the machinery and the Czech slice; real deployments grow
    the set through human adjudication (WP-0.6 tooling) — synthetic pairs
    stay marked `is_synthetic` so measured curves can be stratified.
    """
    with engine.begin() as connection:
        for pair in SYNTHETIC_GOLDEN_PAIRS:
            connection.execute(
                _UPSERT_PAIR,
                {
                    "pair_id": uuid5(
                        _PAIR_NAMESPACE,
                        f"{deployment_id}:{pair['surface_a']}|{pair['surface_b']}",
                    ),
                    "deployment_id": deployment_id,
                    **pair,
                },
            )


def run_resolution_suite(
    *,
    engine: Engine,
    resolver: CascadeResolver,
    deployment_id: UUID,
    component_version: str,
) -> ResolutionSuiteReport:
    """Judge every golden pair, record curves + the run, return the report.

    Passing means the global curve meets both floors. Diagnostics preserve the
    expected blocking stratum and actual deciding tier for every outcome, so a
    T0-reachable same-name pair can still blame a false merge on T3 or T4.
    The report lands on the resolver_versions row (notes) — the D22 record the
    exit criterion names — and the run in eval_runs.
    """
    with engine.connect() as connection:
        pairs = (
            connection.execute(_SELECT_PAIRS, {"deployment_id": deployment_id})
            .mappings()
            .all()
        )
    global_counts = _empty_counts()
    by_deciding_tier: dict[str, dict[str, int]] = {}
    by_blocking_tier: dict[str, dict[str, int]] = {}
    for pair in pairs:
        matched, tier = resolver.judge_pair(
            surface_a=pair["surface_a"],
            surface_b=pair["surface_b"],
            context_a=pair["context_a"],
            context_b=pair["context_b"],
        )
        actual = pair["label"] == "match"
        blocking_tier = str(pair["expected_blocking_tier"] or "unreachable")
        deciding_counts = by_deciding_tier.setdefault(tier, _empty_counts())
        blocking_counts = by_blocking_tier.setdefault(blocking_tier, _empty_counts())
        for counts in (global_counts, deciding_counts, blocking_counts):
            _record_outcome(counts=counts, matched=matched, actual=actual)
    curve = _curve(counts=global_counts)
    tier_diagnostics: TierDiagnostics = {
        "deciding": {
            tier: _tier_diagnostic(counts=counts)
            for tier, counts in sorted(by_deciding_tier.items())
        },
        "blocking": {
            tier: _tier_diagnostic(counts=counts)
            for tier, counts in sorted(by_blocking_tier.items())
        },
    }
    t0_counts = by_blocking_tier.get("T0", _empty_counts())
    gate_guards = _gate_guards(global_counts=global_counts, t0_counts=t0_counts)
    # Undefined metrics and absent label classes block the suite. The D95 T0
    # negative canary is also zero-tolerance: its false merges cannot be
    # diluted below the global precision floor by adding easy positives.
    passed = (
        bool(pairs)
        and all(gate_guards.values())
        and (
            curve["precision"] is not None
            and curve["recall"] is not None
            and curve["precision"] >= PRECISION_FLOOR
            and curve["recall"] >= RECALL_FLOOR
        )
    )
    with engine.begin() as connection:
        connection.execute(
            _RECORD_RUN,
            {
                "eval_run_id": uuid4(),
                "deployment_id": deployment_id,
                "component_version": component_version,
                "metrics": {
                    "curve": curve,
                    "tier_diagnostics": tier_diagnostics,
                    "gate_guards": gate_guards,
                    "floors": {"precision": PRECISION_FLOOR, "recall": RECALL_FLOOR},
                },
                "passed": passed,
            },
        )
        connection.execute(
            _RECORD_CURVES,
            {
                "deployment_id": deployment_id,
                "resolver_version": component_version,
                "notes": {
                    "curve": curve,
                    "tier_diagnostics": tier_diagnostics,
                    "gate_guards": gate_guards,
                },
            },
        )
    return {
        "curve": curve,
        "tier_diagnostics": tier_diagnostics,
        "gate_guards": gate_guards,
        "passed": passed,
    }


def _empty_counts() -> dict[str, int]:
    """Return a fresh confusion-matrix accumulator."""
    return {"tp": 0, "fp": 0, "fn": 0, "tn": 0}


def _record_outcome(*, counts: dict[str, int], matched: bool, actual: bool) -> None:
    """Add one predicted/actual outcome to a confusion matrix."""
    if matched and actual:
        counts["tp"] += 1
    elif matched and not actual:
        counts["fp"] += 1
    elif not matched and actual:
        counts["fn"] += 1
    else:
        counts["tn"] += 1


def _curve(*, counts: dict[str, int]) -> ResolutionCurve:
    """Build the one suite-blocking global precision/recall curve."""
    return {
        "precision": _ratio(counts["tp"], counts["tp"] + counts["fp"]),
        "recall": _ratio(counts["tp"], counts["tp"] + counts["fn"]),
        "pairs": sum(counts.values()),
        "false_merges": counts["fp"],
        "false_splits": counts["fn"],
    }


def _tier_diagnostic(*, counts: dict[str, int]) -> TierDiagnostic:
    """Summarize errors for one blocking or deciding tier without gating it."""
    return {
        "pairs": sum(counts.values()),
        "correct": counts["tp"] + counts["tn"],
        "false_merges": counts["fp"],
        "false_splits": counts["fn"],
    }


def _gate_guards(
    *, global_counts: dict[str, int], t0_counts: dict[str, int]
) -> ResolutionGateGuards:
    """Require both label classes and a measured, false-merge-free T0 canary."""
    return {
        "positive_labels_measured": global_counts["tp"] + global_counts["fn"] > 0,
        "negative_labels_measured": global_counts["tn"] + global_counts["fp"] > 0,
        "t0_negative_canary_measured": t0_counts["tn"] + t0_counts["fp"] > 0,
        "t0_false_merge_free": t0_counts["fp"] == 0,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    """A ratio that is honestly None when its denominator is unmeasured."""
    return numerator / denominator if denominator else None


_UPSERT_PAIR = text(
    """
    INSERT INTO golden_pairs (
        pair_id, deployment_id, surface_a, surface_b,
        context_a, context_b, label, hardness, expected_blocking_tier,
        is_synthetic, adjudicated_by
    ) VALUES (
        :pair_id, :deployment_id, :surface_a, :surface_b,
        :context_a, :context_b, :label, :hardness, :expected_blocking_tier,
        true, 'synthetic-starter'
    )
    ON CONFLICT (pair_id) DO UPDATE
        SET label = EXCLUDED.label,
            hardness = EXCLUDED.hardness,
            context_a = EXCLUDED.context_a,
            context_b = EXCLUDED.context_b,
            expected_blocking_tier = EXCLUDED.expected_blocking_tier
    """
)

_SELECT_PAIRS = text(
    """
    SELECT surface_a, surface_b, context_a, context_b, label,
           expected_blocking_tier
    FROM golden_pairs
    WHERE deployment_id = :deployment_id
    ORDER BY pair_id
    """
)

_RECORD_RUN = text(
    """
    INSERT INTO eval_runs (
        eval_run_id, deployment_id, suite, component_version, metrics, passed
    ) VALUES (
        :eval_run_id, :deployment_id, 'resolution', :component_version,
        :metrics, :passed
    )
    """
).bindparams(bindparam("metrics", type_=JSON))

_RECORD_CURVES = text(
    """
    UPDATE resolver_versions
    SET notes = CAST(:notes AS jsonb)::text
    WHERE deployment_id = :deployment_id
      AND resolver_version = :resolver_version
    """
).bindparams(bindparam("notes", type_=JSON))
