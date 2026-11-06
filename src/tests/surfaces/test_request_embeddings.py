"""Request-local vector reuse, isolation and actual provider-call accounting."""

from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from threading import Barrier
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing.model_provider import FakeModelProvider
from rememberstack.ports.p1_index import P1SearchPort
from rememberstack.spine.surface_cost import bind_surface_scope
from rememberstack.spine.surface_cost import current_surface_scope
from rememberstack.spine.surface_cost import next_surface_ordinal
from rememberstack.spine.surface_cost import open_surface_scope
from rememberstack.spine.surface_cost import SqlSurfaceCostRecorder
from rememberstack.spine.surface_cost import SurfaceCallSite
from rememberstack.spine.surface_cost import SurfaceCostKind
from rememberstack.surfaces.query_engine import QueryEngine


def _engine(
    *, provider: FakeModelProvider, recorder: MagicMock | None = None
) -> QueryEngine:
    """Compose the real embed path without requiring a database for reads."""
    return QueryEngine(
        engine=cast(Engine, MagicMock()),
        search_index=cast(P1SearchPort, MagicMock()),
        model_provider=provider,
        embedding_model="request-test",
        surface_cost=cast(SqlSurfaceCostRecorder | None, recorder),
    )


def test_nested_channels_share_one_provider_call_and_receipt() -> None:
    """Coverage and sibling channels must not independently bill identical text."""
    provider = FakeModelProvider()
    recorder = MagicMock()
    engine = _engine(provider=provider, recorder=recorder)
    deployment = uuid4()
    with open_surface_scope(surface=SurfaceCostKind.OPERATION) as parent:
        for site in (
            SurfaceCallSite.CLAIMS_AND_SOURCES_CLAIMS,
            SurfaceCallSite.CLAIMS_AND_SOURCES_CHUNKS,
            SurfaceCallSite.FACTS_CONTEXT,
        ):
            with open_surface_scope(surface=SurfaceCostKind.LIBRARY) as child:
                assert child is parent
                engine._embed(
                    query="same question", call_site=site, deployment_id=deployment
                )
    assert provider.embedded_texts == ["same question"]
    recorder.record.assert_called_once()
    assert (
        recorder.record.call_args.kwargs["call_site"]
        is SurfaceCallSite.CLAIMS_AND_SOURCES_CLAIMS
    )
    assert current_surface_scope() is None
    assert parent.work.embedding_vectors == {}
    with open_surface_scope(surface=SurfaceCostKind.OPERATION):
        engine._embed(
            query="same question",
            call_site=SurfaceCallSite.FACTS_CONTEXT,
            deployment_id=deployment,
        )
    assert provider.embedded_texts == ["same question", "same question"]
    assert recorder.record.call_count == 2


def test_cache_isolates_deployments_engines_and_exact_text() -> None:
    """Only the exact configured vector space and input can reuse a vector."""
    provider = FakeModelProvider()
    first = _engine(provider=provider)
    second = _engine(provider=provider)
    deployment = uuid4()
    with bind_surface_scope(request_id=uuid4(), surface=SurfaceCostKind.OPEN_QUERY):
        for engine, owner, query in (
            (first, deployment, "question"),
            (first, uuid4(), "question"),
            (second, deployment, "question"),
            (first, deployment, "Question"),
        ):
            engine._embed(
                query=query,
                call_site=SurfaceCallSite.FACTS_CONTEXT,
                deployment_id=owner,
            )
    assert provider.embedded_texts == ["question", "question", "question", "Question"]


def test_failed_receipt_does_not_populate_cache() -> None:
    """A query cannot conceal an accounting failure by reusing its vector."""
    provider = FakeModelProvider()
    recorder = MagicMock()
    recorder.record.side_effect = [RuntimeError("no durable receipt"), None]
    engine = _engine(provider=provider, recorder=recorder)
    deployment = uuid4()
    with open_surface_scope(surface=SurfaceCostKind.OPERATION):
        with pytest.raises(RuntimeError, match="no durable receipt"):
            engine._embed(
                query="retry",
                call_site=SurfaceCallSite.FACTS_CONTEXT,
                deployment_id=deployment,
            )
        engine._embed(
            query="retry",
            call_site=SurfaceCallSite.FACTS_CONTEXT,
            deployment_id=deployment,
        )
    assert provider.embedded_texts == ["retry", "retry"]
    assert recorder.record.call_count == 2


@pytest.mark.parametrize("same_query", [True, False])
def test_inherited_request_context_serializes_provider_calls(same_query: bool) -> None:
    """Sibling contexts share cached work and cannot duplicate receipt ordinals."""
    provider = FakeModelProvider()
    recorder = MagicMock()
    recorded: list[tuple[UUID, int]] = []

    def record(**_kwargs: object) -> None:
        """Observe the scope and ordinal used by an actual provider call."""
        scope = current_surface_scope()
        assert scope is not None
        recorded.append((scope.request_id, next_surface_ordinal()))

    recorder.record.side_effect = record
    engine = _engine(provider=provider, recorder=recorder)
    deployment = uuid4()
    ready = Barrier(parties=2)

    def embed(*, query: str) -> tuple[float, ...]:
        """Enter the embed path concurrently in two inherited thread contexts."""
        ready.wait(timeout=5)
        return engine._embed(
            query=query,
            call_site=SurfaceCallSite.FACTS_CONTEXT,
            deployment_id=deployment,
        )

    with open_surface_scope(surface=SurfaceCostKind.OPERATION) as scope:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(copy_context().run, embed, query="first")
            second = executor.submit(
                copy_context().run, embed, query="first" if same_query else "second"
            )
            first.result(timeout=10)
            second.result(timeout=10)
        expected_count = 1 if same_query else 2
        assert len(provider.embedded_texts) == expected_count
        assert sorted(recorded) == [
            (scope.request_id, i + 1) for i in range(expected_count)
        ]
