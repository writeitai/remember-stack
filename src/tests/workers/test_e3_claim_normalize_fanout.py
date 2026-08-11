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


def test_fanout_and_barrier_pin_extractor_version() -> None:
    """Expected claim set is closed at the extract generation (D88 §5.1)."""
    from rememberstack.spine import work_ledger

    for sql in (
        str(work_ledger._SELECT_CLAIMS_FOR_NORMALIZE_FANOUT),
        str(work_ledger._BARRIER_EXPECTED_CLAIMS),
        str(work_ledger._BARRIER_READY_CLAIMS),
    ):
        assert "cl.extractor_version = :extractor_version" in sql
        assert "c.deployment_id = :deployment_id" in sql
        assert "c.version_id = :version_id" in sql


def test_readiness_normalize_status_pins_extractor_version() -> None:
    """Derived normalize readiness does not count later extract generations."""
    from rememberstack.spine import readiness

    sql = str(readiness._NORMALIZE_CLAIM_STATUS)
    assert "cl.extractor_version = :extractor_version" in sql


def test_claim_normalize_requires_extractor_version_pin() -> None:
    """Missing extract pin on claim work is non-retryable (closed handoff)."""
    from rememberstack.model import NonRetryableHandlerError

    claim_id = uuid4()
    handler = NormalizeRelationsHandler(
        claim_catalog=type(
            "C",
            (),
            {
                "claim_for_normalization": staticmethod(
                    lambda **kwargs: ClaimForNormalization(
                        claim_id=claim_id,
                        deployment_id=uuid4(),
                        doc_id=uuid4(),
                        chunk_id=uuid4(),
                        claim_text="x",
                        is_attributed=False,
                        extractor_version="e2-test-extractor",
                    )
                )
            },
        )(),  # type: ignore[arg-type]
        chunk_catalog=type(
            "K", (), {"chunks_for_embedding": staticmethod(lambda **kwargs: ())}
        )(),  # type: ignore[arg-type]
        registry=type(
            "R", (), {"normalized_claim_ids": staticmethod(lambda **kwargs: frozenset())}
        )(),  # type: ignore[arg-type]
        resolver=None,  # type: ignore[arg-type]
        facts=None,  # type: ignore[arg-type]
        observation_adjudicator=None,  # type: ignore[arg-type]
        model_provider=FakeModelProvider(generate_payload={}),
        settings=E3Settings(normalize_model="test"),
        chunker_version="test-chunker",
    )
    work = ClaimedWork(
        processing_id=uuid4(),
        deployment_id=uuid4(),
        target_kind=ProcessingTarget.CLAIM,
        target_id=claim_id,
        stage=PipelineStage.NORMALIZE_RELATIONS,
        component_version=E3_NORMALIZER_VERSION,
        content_hash="h",
        lane=ProcessingLane.STEADY,
        attempt=1,
        payload={
            "version_id": str(uuid4()),
            "representation_id": str(uuid4()),
            "claim_id": str(claim_id),
            "doc_id": str(uuid4()),
            "chunker_version": "test-chunker",
        },
    )
    try:
        handler.handle(work=work, meter=NoopCostMeter())
    except NonRetryableHandlerError as error:
        assert "extractor_version" in str(error)
    else:
        raise AssertionError("expected NonRetryableHandlerError")


def test_supersession_orients_by_asserted_at_not_process_order() -> None:
    """Source-older relation is predecessor even when processed second."""
    from datetime import datetime
    from datetime import UTC

    from rememberstack.spine.supersession import _is_source_successor

    older = {
        "relation_id": uuid4(),
        "asserted_at": datetime(2019, 1, 1, tzinfo=UTC),
    }
    newer = {
        "relation_id": uuid4(),
        "asserted_at": datetime(2024, 6, 1, tzinfo=UTC),
    }
    assert _is_source_successor(left=newer, right=older)
    assert not _is_source_successor(left=older, right=newer)
    # Equal times: stable id order, not arrival order.
    same_time = datetime(2020, 1, 1, tzinfo=UTC)
    a = {"relation_id": uuid4(), "asserted_at": same_time}
    b = {"relation_id": uuid4(), "asserted_at": same_time}
    assert _is_source_successor(left=a, right=b) == (
        str(a["relation_id"]) > str(b["relation_id"])
    )


def test_observation_reverse_arrival_detects_source_earlier() -> None:
    """Cross-version: source-older assertion is not treated as successor."""
    from datetime import datetime
    from datetime import UTC

    from rememberstack.spine.observation_adjudication import _is_strictly_earlier

    older = datetime(2019, 1, 1, tzinfo=UTC)
    newer = datetime(2024, 6, 1, tzinfo=UTC)
    assert _is_strictly_earlier(older, newer)
    assert not _is_strictly_earlier(newer, older)
    assert not _is_strictly_earlier(None, newer)
    assert not _is_strictly_earlier(older, None)


def test_ordinary_observation_inserts_pass_valid_from() -> None:
    """First/novelty paths store asserted_at as valid_from (D88 reverse-arrival)."""
    import inspect

    from rememberstack.spine import observation_adjudication

    source = inspect.getsource(observation_adjudication.ObservationAdjudicator._add_with_block)
    # Ordinary first-mention / novelty inserts must not drop source time.
    assert source.count("valid_from=asserted_at") >= 3


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
        deployment_id=deployment_id,
        doc_id=doc_id,
        chunk_id=chunk_id,
        claim_text="Acme hired Bob.",
        is_attributed=False,
        extractor_version="e2-test-extractor",
    )

    class _Claims:
        def claim_for_normalization(self, *, claim_id: object) -> ClaimForNormalization:
            assert claim_id == claim.claim_id
            return claim

    class _Chunk:
        def __init__(self, cid: object, vid: object) -> None:
            self.chunk_id = cid
            self.version_id = vid

    class _Chunks:
        def chunks_for_embedding(self, **kwargs: object) -> tuple[_Chunk, ...]:
            del kwargs
            return (_Chunk(chunk_id, version_id),)

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
            "extractor_version": "e2-test-extractor",
        },
    )
    outcome = handler.handle(work=work, meter=NoopCostMeter())
    assert outcome.claim_normalize_barrier is not None
    assert outcome.claim_normalize_barrier.version_id == version_id
    assert outcome.claim_normalize_barrier.normalize_component_version == (
        E3_NORMALIZER_VERSION
    )
    assert outcome.claim_normalize_barrier.extractor_version == "e2-test-extractor"
    assert facts.staged
    assert outcome.follow_up == ()
