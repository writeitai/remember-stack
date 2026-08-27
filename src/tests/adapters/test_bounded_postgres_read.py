"""Proofs for shared interactive PostgreSQL read admission."""

from time import monotonic

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from rememberstack.adapters import BoundedPostgresReadPool


def test_read_admission_cannot_outwait_the_operation_deadline() -> None:
    """A queued retrieval read fails at its absolute deadline, not pool_timeout."""
    engine = create_engine("sqlite://")
    pool = BoundedPostgresReadPool(
        engine=engine, max_concurrency=1, pool_wait_seconds=1.0
    )
    try:
        with pool.connect(deadline=monotonic() + 1.0):
            started = monotonic()
            with pytest.raises(SQLAlchemyTimeoutError, match="admission"):
                with pool.connect(deadline=monotonic() + 0.02):
                    pytest.fail("a second read must not bypass the shared slot")
            assert monotonic() - started < 0.2
    finally:
        engine.dispose()
