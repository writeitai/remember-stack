"""D88: claim-level normalize fan-out unit coverage (no Postgres)."""

from __future__ import annotations

from uuid import uuid4

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.adapters.testing import NoopCostMeter
from rememberstack.model import ClaimedWork
from rememberstack.model import ClaimForNormalization
from rememberstack.model import PipelineStage
from rememberstack.model import ProcessingLane
from rememberstack.model import ProcessingTarget
from rememberstack.workers.e3 import E3_NORMALIZER_VERSION
from rememberstack.workers.e3 import E3Settings
from rememberstack.workers.e3 import NormalizeRelationsHandler
from rememberstack.workers.e3 import OBS_FLUSH_VERSION


def test_e3_version_includes_claim_fanout_suffix() -> None:
    """Fan-out generation is part of the component version string."""
    assert "claim-fanout-1" in E3_NORMALIZER_VERSION
    assert OBS_FLUSH_VERSION.startswith("e3-obs-flush")


def test_obs_flush_version_is_single_source_for_ledger_fanout() -> None:
    """Ledger fan-out imports OBS_FLUSH_VERSION (no drifted literal)."""
    import inspect

    from rememberstack.spine import work_ledger

    source = inspect.getsource(work_ledger._enqueue_claim_normalize_fanout)
    assert "OBS_FLUSH_VERSION" in source
    assert "e3-obs-flush-2026.08a:claim-fanout-1" not in source.replace(
        "OBS_FLUSH_VERSION", ""
    )


def test_normalize_barrier_uses_dedicated_advisory_lock() -> None:
    """Last-claim race is closed by a D88-scoped xact advisory lock."""
    from rememberstack.spine import work_ledger

    sql = str(work_ledger._ADVISORY_LOCK_NORMALIZE_BARRIER)
    assert "d88-normalize-barrier" in sql
    assert "pg_advisory_xact_lock" in sql


def test_handle_claim_grain_returns_barrier() -> None:
    """Claim-target work stages observations and returns claim barrier."""
    claim_id = uuid4()
    version_id = uuid4()
    representation_id = uuid4()
    doc_id = uuid4()
    chunk_id = uuid4()
    deployment_id = uuid4()

    claim = ClaimForNormalization(
        claim_id=claim_id,
        doc_id=doc_id,
        chunk_id=chunk_id,
        claim_text="Acme hired Bob.",
        is_attributed=False,
    )

    class _Claims:
        def claim_for_normalization(self, *, claim_id: object) -> ClaimForNormalization:
            assert claim_id == claim.claim_id
            return claim

    class _Chunk:
        def __init__(self, cid: object) -> None:
            self.chunk_id = cid

    class _Chunks:
        def chunks_for_embedding(self, **kwargs: object) -> tuple[_Chunk, ...]:
            del kwargs
            return (_Chunk(chunk_id),)

    class _Registry:
        def normalized_claim_ids(self, *, claim_ids: object) -> frozenset:
            del claim_ids
            return frozenset()

    class _Facts:
        staged: list[object]

        def __init__(self) -> None:
            self.staged = []

        def active_predicates(self, **kwargs: object) -> dict[str, str | None]:
            del kwargs
            return {"works_at": None}

        def predicate_prompt_lines(self, **kwargs: object) -> str:
            del kwargs
            return "works_at"

        def predicate_signatures(self, **kwargs: object) -> dict:
            del kwargs
            return {}

        def entity_type_parents(self, **kwargs: object) -> dict[str, str | None]:
            del kwargs
            return {"Person": None, "Organization": None}

        def ensure_other_predicate(self, **kwargs: object) -> None:
            del kwargs

        def upsert_relation(self, **kwargs: object) -> object:
            del kwargs

            class _U:
                created = True
                relation_id = uuid4()

            return _U()

        def stage_normalize_observation(self, **kwargs: object) -> None:
            self.staged.append(kwargs)

    class _Resolver:
        def resolve(self, **kwargs: object) -> object:
            reference = kwargs["reference"]

            class _R:
                entity_id = uuid4()
                created = True
                entity_type = reference.type  # type: ignore[attr-defined]

            return _R()

    legal = {
        "relations": [],
        "observations": [
            {
                "subject": {"name": "Bob", "type": "Person"},
                "statement": "Bob works at Acme",
            }
        ],
    }
    provider = FakeModelProvider(generate_payload=legal)
    facts = _Facts()
    handler = NormalizeRelationsHandler(
        claim_catalog=_Claims(),  # type: ignore[arg-type]
        chunk_catalog=_Chunks(),  # type: ignore[arg-type]
        registry=_Registry(),  # type: ignore[arg-type]
        resolver=_Resolver(),  # type: ignore[arg-type]
        facts=facts,  # type: ignore[arg-type]
        observation_adjudicator=None,  # type: ignore[arg-type]
        model_provider=provider,
        settings=E3Settings(normalize_model="test"),
        chunker_version="test-chunker",
    )
    work = ClaimedWork(
        processing_id=uuid4(),
        deployment_id=deployment_id,
        target_kind=ProcessingTarget.CLAIM,
        target_id=claim_id,
        stage=PipelineStage.NORMALIZE_RELATIONS,
        component_version=E3_NORMALIZER_VERSION,
        content_hash="h",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={
            "version_id": str(version_id),
            "representation_id": str(representation_id),
            "claim_id": str(claim_id),
            "doc_id": str(doc_id),
            "chunker_version": "test-chunker",
        },
    )
    outcome = handler.handle(work=work, meter=NoopCostMeter())
    assert outcome.claim_normalize_barrier is not None
    assert outcome.claim_normalize_barrier.version_id == version_id
    assert outcome.claim_normalize_barrier.normalize_component_version == (
        E3_NORMALIZER_VERSION
    )
    assert facts.staged
    assert outcome.follow_up == ()
