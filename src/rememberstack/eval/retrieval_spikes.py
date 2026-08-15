"""Persistence boundary for the WP-5.6 retrieval spike battery."""

from typing import Final
from uuid import UUID
from uuid import uuid4

from sqlalchemy import bindparam
from sqlalchemy import JSON
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.model import RetrievalSpikeReport

RETRIEVAL_SPIKE_VERSION: Final = "retrieval-spikes-2026.07b"
"""Version stamped on every complete retrieval-spike measurement record."""


def record_retrieval_spike_report(
    *,
    engine: Engine,
    deployment_id: UUID,
    report: RetrievalSpikeReport,
    component_version: str = RETRIEVAL_SPIKE_VERSION,
) -> UUID:
    """Append the complete spike report to D22's ``eval_runs`` history."""
    eval_run_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            _INSERT_RUN,
            {
                "eval_run_id": eval_run_id,
                "deployment_id": deployment_id,
                "component_version": component_version,
                "metrics": report.model_dump(mode="json"),
                "passed": report.passed,
            },
        )
    return eval_run_id


_INSERT_RUN = text(
    """
    INSERT INTO eval_runs (
        eval_run_id, deployment_id, suite, component_version, metrics, passed
    ) VALUES (
        :eval_run_id, :deployment_id, 'retrieval', :component_version,
        :metrics, :passed
    )
    """
).bindparams(bindparam("metrics", type_=JSON))
