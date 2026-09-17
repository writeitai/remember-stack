"""Unit tests for TypeSafe System One Jev fact adjudication engine (D126)."""

from datetime import datetime
from datetime import timezone
from decimal import Decimal
import json
from typing import Any
from typing import Mapping
from uuid import UUID
from uuid import uuid4

import httpx
import pytest

from rememberstack.adapters.typesafe import ConfigurationError
from rememberstack.adapters.typesafe import TypeSafeProviderError
from rememberstack.adapters.typesafe import TypeSafeSettings
from rememberstack.adapters.typesafe import TypeSafeSystemOneClient
from rememberstack.core.concise_adjudication import project_concise_inputs
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model.concise_adjudication import PromptFactDecision
from rememberstack.ports.cost_meter import CostMeterPort
from rememberstack.ports.systemone import SystemOnePort
from rememberstack.spine.fact_adjudication import active_adjudicator_versions
from rememberstack.spine.fact_adjudication import active_flush_version
from rememberstack.spine.fact_adjudication import active_question_identity
from rememberstack.spine.fact_adjudication import FactAdjudicationSettings
from rememberstack.spine.fact_adjudication import FactAdjudicator
from rememberstack.spine.fact_adjudication import JEV_ADJUDICATOR_VERSION
from rememberstack.spine.fact_adjudication import OBSERVATION_APPLICATION_VERSION
from rememberstack.spine.fact_adjudication import OBSERVATION_APPLICATION_VERSION_JEV
from rememberstack.spine.fact_adjudication import RELATION_APPLICATION_VERSION
from rememberstack.spine.fact_adjudication import RELATION_APPLICATION_VERSION_JEV
from rememberstack.spine.fact_applications import PreparedApplication
from rememberstack.spine.fact_applications import snapshot_hash


class _MockCostMeter(CostMeterPort):
    """Test double recording metering calls in memory."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str | None, ProviderCallUsage, str]] = []

    def record(
        self,
        *,
        call_key: str,
        tier: str | None,
        usage: ProviderCallUsage,
        outcome: str = "ok",
    ) -> None:
        self.records.append((call_key, tier, usage, outcome))


class _MockModelProvider:
    """Test double for generative prompt engine fallback."""

    def __init__(self, response: PromptFactDecision | None = None) -> None:
        self.calls: list[Any] = []
        self._response = response or PromptFactDecision(
            target="F1", confidence=0.95, rationale="Fallback prompt decision"
        )

    def generate(self, *, request: Any, response_type: type[Any]) -> Any:
        self.calls.append(request)

        class _Result:
            def __init__(self, output: Any) -> None:
                self.output = output
                self.usage = ProviderCallUsage(
                    model_name="openai/gpt-5.6-luna",
                    tokens_in=500,
                    tokens_out=50,
                    cost_usd=Decimal("0.005"),
                    latency_ms=250,
                )

        return _Result(self._response)

    def embed(self, *, request: Any) -> Any:
        raise NotImplementedError


class _MockSystemOneClient(SystemOnePort):
    """Test double for SystemOnePort returning configured answers."""

    def __init__(
        self,
        answers: Mapping[str, Any] | None = None,
        usage: ProviderCallUsage | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._answers = answers or {}
        self._usage = usage or ProviderCallUsage(
            model_name="typesafe/jev-latest",
            tokens_in=200,
            tokens_out=15,
            cost_usd=Decimal("0.000008"),
            latency_ms=120,
        )
        self._error = error

    def evaluate(
        self,
        *,
        model: str | None = None,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
        timeout_s: float | None = None,
    ) -> tuple[Mapping[str, Any], ProviderCallUsage]:
        self.calls.append(
            {
                "model": model,
                "state": state,
                "questions": questions,
                "timeout_s": timeout_s,
            }
        )
        if self._error is not None:
            raise self._error
        return self._answers, self._usage


def _sample_snapshot(*, with_candidate: bool = True) -> dict[str, Any]:
    """Return a minimal valid snapshot dictionary for project_concise_inputs."""
    app_id = str(uuid4())
    claim_id = str(uuid4())
    entity_id = str(uuid4())
    doc_id = str(uuid4())
    fact_id = str(uuid4())
    at = datetime(2026, 5, 12, 10, 0, tzinfo=timezone.utc)

    facts = (
        [
            {
                "fact_id": fact_id,
                "subject_entity_id": entity_id,
                "statement": "Nate won the tournament",
                "valid_from": datetime(2026, 5, 10, tzinfo=timezone.utc),
                "valid_until": datetime(2026, 5, 11, tzinfo=timezone.utc),
                "valid_precision": "day",
                "window_claim_ids": [claim_id],
                "ingested_at": at,
                "invalidated_at": None,
                "contradiction_group": None,
                "evidence_count": 1,
                "contradict_count": 0,
            }
        ]
        if with_candidate
        else []
    )

    claims = [
        {
            "claim_id": claim_id,
            "doc_id": doc_id,
            "claim_text": "Nate won first place in the tournament on 10 May.",
            "source_span": "Nate won first place in the tournament on 10 May.",
            "asserted_at": at,
            "claim_valid_from": datetime(2026, 5, 10, tzinfo=timezone.utc),
            "claim_valid_until": datetime(2026, 5, 11, tzinfo=timezone.utc),
            "claim_valid_precision": "day",
            "claim_valid_kind": "event_time",
            "is_current_testimony": True,
            "is_attributed": False,
            "extractor_version": "test",
        }
    ]

    assertions = [
        {
            "application_id": app_id,
            "claim_id": claim_id,
            "subject_entity_id": entity_id,
            "object_entity_id": None,
            "canonical_subject_id": entity_id,
            "canonical_object_id": None,
            "output_kind": "observation",
            "output_ordinal": 0,
            "normalizer_version": "test",
            "adjudicator_version": "test",
            "support_relation_id": None,
            "support_observation_id": None,
            "support_stance": None,
            "assertion": {
                "subject": {"name": "Nate"},
                "statement": "Nate took first place in the tournament",
                "uses_claim_window": True,
            },
        }
    ]

    return {
        "deployment_id": str(uuid4()),
        "root": entity_id,
        "kind": "observation",
        "application_id": app_id,
        "application": {
            "application_id": app_id,
            "subject_entity_id": entity_id,
            "output_kind": "observation",
            "statement": "Nate took first place in the tournament",
            "claim_id": claim_id,
            "context_bindings": [],
        },
        "normalizer_version": "test",
        "adjudicator_version": "test",
        "membership_hash": "m",
        "evidence_hash": "e",
        "support_hash": "s",
        "limits": {"facts": 20, "claims": 100, "assertions": 100},
        "potentially_truncated": False,
        "facts": facts,
        "claims": claims,
        "assertions": assertions,
        "evidence": [{"fact_id": fact_id, "claim_id": claim_id, "stance": "supports"}]
        if with_candidate
        else [],
        "contradictions": [],
        "entities": [{"entity_id": entity_id, "primary_name": "Nate", "aliases": []}],
        "sources": [],
    }


# ============================================================================
# 1. Configuration and Settings Tests
# ============================================================================


def test_typesafe_settings_defaults() -> None:
    """Verify default values of TypeSafeSettings."""
    settings = TypeSafeSettings()
    assert settings.api_key is None
    assert settings.model == "jev-latest"
    assert settings.base_url == "https://api.typesafe.ai/v1"
    assert settings.timeout_s == 30.0
    assert settings.fallback_to_prompt is False


def test_typesafe_settings_env_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify env aliases for TypeSafeSettings."""
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_API_KEY", "key-from-rem")
    settings = TypeSafeSettings()
    assert settings.api_key == "key-from-rem"

    monkeypatch.delenv("REMEMBERSTACK_TYPESAFE_API_KEY")
    monkeypatch.setenv("TYPESAFE_AI_API_KEY", "key-from-typesafe")
    settings = TypeSafeSettings()
    assert settings.api_key == "key-from-typesafe"


def test_typesafe_client_missing_api_key_raises_configuration_error() -> None:
    """Client initialization refuses to start without an API key."""
    with pytest.raises(
        ConfigurationError, match="REMEMBERSTACK_TYPESAFE_API_KEY is required"
    ):
        TypeSafeSystemOneClient(settings=TypeSafeSettings(api_key=None))

    with pytest.raises(
        ConfigurationError, match="REMEMBERSTACK_TYPESAFE_API_KEY is required"
    ):
        TypeSafeSystemOneClient(settings=TypeSafeSettings(api_key="   "))


def test_fact_adjudicator_startup_validation() -> None:
    """FactAdjudicator requires systemone_provider when engine='jev'."""
    settings = FactAdjudicationSettings(engine="jev")
    with pytest.raises(
        ValueError,
        match="systemone_provider must be supplied when fact adjudication engine is 'jev'",
    ):
        FactAdjudicator(
            engine=None,  # type: ignore[arg-type]
            model_provider=_MockModelProvider(),  # type: ignore[arg-type]
            settings=settings,
            systemone_provider=None,
        )

    # With engine="prompt", systemone_provider=None is valid
    prompt_settings = FactAdjudicationSettings(engine="prompt")
    # Will construct without ValueError
    adj = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=prompt_settings,
        systemone_provider=None,
    )
    assert adj._settings.engine == "prompt"


# ============================================================================
# 2. Generation Identity and Version Authorities Tests
# ============================================================================


def test_generation_identity_helpers() -> None:
    """Generation identity authorities return isolated strings per engine."""
    assert active_adjudicator_versions("jev") == (
        RELATION_APPLICATION_VERSION_JEV,
        OBSERVATION_APPLICATION_VERSION_JEV,
    )
    assert active_adjudicator_versions("prompt") == (
        RELATION_APPLICATION_VERSION,
        OBSERVATION_APPLICATION_VERSION,
    )

    assert active_question_identity("jev") == JEV_ADJUDICATOR_VERSION
    assert active_question_identity("prompt") == "fact-prompt-v1"

    jev_flush = active_flush_version("jev")
    prompt_flush = active_flush_version("prompt")
    assert RELATION_APPLICATION_VERSION_JEV in jev_flush
    assert OBSERVATION_APPLICATION_VERSION_JEV in jev_flush
    assert RELATION_APPLICATION_VERSION in prompt_flush
    assert OBSERVATION_APPLICATION_VERSION in prompt_flush
    assert jev_flush != prompt_flush


def test_snapshot_hash_engine_isolation_and_backward_compatibility() -> None:
    """snapshot_hash strictly isolates prompt and Jev attempts while preserving backwards compatibility."""
    snap = _sample_snapshot()

    # Backwards compatibility: default engine and question_identity produce existing hash
    hash_default = snapshot_hash(snapshot=snap)
    hash_explicit_prompt = snapshot_hash(
        snapshot=snap, engine="prompt", question_identity="fact-prompt-v1"
    )
    assert hash_default == hash_explicit_prompt

    # Jev isolation
    hash_jev = snapshot_hash(
        snapshot=snap, engine="jev", question_identity=active_question_identity("jev")
    )
    assert hash_jev != hash_default
    assert len(hash_jev) == 64


# ============================================================================
# 3. TypeSafe Client HTTP Adapter & Pricing Tests
# ============================================================================


def test_typesafe_client_evaluate_success_and_pricing() -> None:
    """TypeSafeSystemOneClient parses response, usage, and calculates $0.042/1M token cost."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://api.typesafe.ai/v1/systemone"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.read())
        assert body["model"] == "jev-latest"
        assert "questions" in body

        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {
                    "match": {"choice": "F1", "confidence": 0.95},
                    "stance": {"choice": "supports", "confidence": 0.98},
                    "window_action": {"choice": "keep", "confidence": 0.92},
                },
                "usage": {"input_tokens": 1_000_000, "output_tokens": 200},
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    ts_client = TypeSafeSystemOneClient(
        settings=TypeSafeSettings(api_key="test-key"), client=client
    )

    answers, usage = ts_client.evaluate(
        model="jev-latest",
        state={"text": "hello"},
        questions={"q1": {"type": "choice"}},
    )

    assert answers["match"]["choice"] == "F1"
    assert usage.model_name == "typesafe/jev-latest"
    assert usage.tokens_in == 1_000_000
    assert usage.tokens_out == 200
    # Exactly $0.042 for 1M tokens
    assert usage.cost_usd == Decimal("0.042")


def test_typesafe_client_missing_usage_raises_provider_accounting_error() -> None:
    """Response missing usage raises ProviderAccountingError."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"answers": {"match": {"choice": "NEW"}}})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    ts_client = TypeSafeSystemOneClient(
        settings=TypeSafeSettings(api_key="test-key"), client=client
    )

    with pytest.raises(ProviderAccountingError, match="carries no token usage"):
        ts_client.evaluate(model="jev-latest", state={}, questions={})


def test_typesafe_client_http_errors_raise_typesafe_provider_error() -> None:
    """HTTP 4xx/5xx errors raise TypeSafeProviderError."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(401, text="Unauthorized")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    ts_client = TypeSafeSystemOneClient(
        settings=TypeSafeSettings(api_key="test-key"), client=client
    )

    with pytest.raises(TypeSafeProviderError, match="TypeSafe returned 401"):
        ts_client.evaluate(model="jev-latest", state={}, questions={})


def test_typesafe_client_retries_on_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client retries on HTTP 429 and succeeds on subsequent attempt."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(429, text="Rate limited")
        return httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {"match": {"choice": "NEW", "confidence": 0.9}},
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    ts_client = TypeSafeSystemOneClient(
        settings=TypeSafeSettings(api_key="test-key"), client=client
    )

    answers, usage = ts_client.evaluate(
        state={"text": "hello"}, questions={"match": {}}
    )
    assert call_count == 2
    assert answers["match"]["choice"] == "NEW"
    assert usage.tokens_in == 100


def test_typesafe_client_retries_exhaustion_on_429_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client retries on 429 up to limit and raises TypeSafeProviderError upon exhaustion."""
    monkeypatch.setattr("time.sleep", lambda _: None)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, text="Rate limited")

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    ts_client = TypeSafeSystemOneClient(
        settings=TypeSafeSettings(api_key="test-key"), client=client
    )

    with pytest.raises(TypeSafeProviderError, match="TypeSafe returned 429"):
        ts_client.evaluate(state={}, questions={})

    # Initial attempt + 3 retries = 4 attempts total
    assert call_count == 4


def test_typesafe_client_default_model_honored() -> None:
    """When model is omitted in evaluate, TypeSafeSettings.model is honored."""
    captured_payload: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        captured_payload = json.loads(request.read())
        return httpx.Response(
            200,
            json={
                "answers": {"match": {"choice": "NEW"}},
                "usage": {"input_tokens": 50, "output_tokens": 5},
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    ts_client = TypeSafeSystemOneClient(
        settings=TypeSafeSettings(api_key="test-key", model="custom-jev-model"),
        client=client,
    )

    ts_client.evaluate(state={}, questions={})
    assert captured_payload["model"] == "custom-jev-model"


# ============================================================================
# 4. Jev Translation into PromptFactDecision and FactApplicationDecision
# ============================================================================


def test_adjudicate_jev_new_proposition() -> None:
    """Choice 'NEW' with confidence >= 0.75 maps to target 'N1' new fact."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "NEW", "confidence": 0.91},
            "stance": {"choice": "not_applicable", "confidence": 0.99},
            "window_action": {"choice": "keep", "confidence": 0.95},
        }
    )
    meter = _MockCostMeter()
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    decision = adjudicator._adjudicate_jev(
        prepared=prepared,
        presentation=presentation,
        mapping=mapping,
        meter=meter,
        call_key="test_call",
    )

    assert decision.target.new_handle == "N1"
    assert len(decision.new_facts) == 1
    assert decision.new_facts[0].handle == "N1"
    assert decision.confidence == 0.91
    assert "new proposition" in decision.rationale

    # Check metering
    assert len(meter.records) == 1
    assert meter.records[0][1] == "fact_adjudication_jev"
    assert meter.records[0][0].endswith(":jev")


def test_adjudicate_jev_matched_candidate_supports() -> None:
    """Choice matching existing fact 'F1' with supports stance maps to that fact."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)
    candidate_fact_id = UUID(snap["facts"][0]["fact_id"])

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F1", "confidence": 0.88},
            "stance": {"choice": "supports", "confidence": 0.95},
            "window_action": {"choice": "keep", "confidence": 0.92},
        }
    )
    meter = _MockCostMeter()
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    decision = adjudicator._adjudicate_jev(
        prepared=prepared,
        presentation=presentation,
        mapping=mapping,
        meter=meter,
        call_key="test_call",
    )

    assert decision.target.fact_id == candidate_fact_id
    assert decision.stance == "supports"
    assert decision.confidence == 0.88
    assert decision.window is None
    assert "matched F1 (supports)" in decision.rationale


def test_adjudicate_jev_matched_candidate_contradicts() -> None:
    """Choice matching existing fact 'F1' with contradicts stance maps to contradict."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)
    candidate_fact_id = UUID(snap["facts"][0]["fact_id"])

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F1", "confidence": 0.84},
            "stance": {"choice": "contradicts", "confidence": 0.91},
            "window_action": {"choice": "keep", "confidence": 0.95},
        }
    )
    meter = _MockCostMeter()
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    decision = adjudicator._adjudicate_jev(
        prepared=prepared,
        presentation=presentation,
        mapping=mapping,
        meter=meter,
        call_key="test_call",
    )

    assert decision.target.fact_id == candidate_fact_id
    assert decision.stance == "contradicts"
    assert decision.confidence == 0.84


def test_adjudicate_jev_window_action_clear() -> None:
    """Window action 'clear' builds an empty FactWindow."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F1", "confidence": 0.85},
            "stance": {"choice": "supports", "confidence": 0.90},
            "window_action": {"choice": "clear", "confidence": 0.80},
        }
    )
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    decision = adjudicator._adjudicate_jev(
        prepared=prepared,
        presentation=presentation,
        mapping=mapping,
        meter=_MockCostMeter(),
        call_key="test_call",
    )

    assert decision.window is not None
    assert decision.window.window.valid_from is None
    assert decision.window.window.valid_until is None


def test_adjudicate_jev_window_action_use_claim() -> None:
    """Window action 'use_claim' parses claim world dates with fact_window_from_raw."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F1", "confidence": 0.90},
            "stance": {"choice": "supports", "confidence": 0.95},
            "window_action": {"choice": "use_claim", "confidence": 0.88},
        }
    )
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    decision = adjudicator._adjudicate_jev(
        prepared=prepared,
        presentation=presentation,
        mapping=mapping,
        meter=_MockCostMeter(),
        call_key="test_call",
    )

    assert decision.window is not None
    assert decision.window.window.valid_from is not None


def test_adjudicate_jev_sub_floor_confidence_forces_coexist() -> None:
    """Match confidence below floor (0.75) forces new fact coexistence with preserved confidence."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F1", "confidence": 0.62},
            "stance": {"choice": "supports", "confidence": 0.95},
            "window_action": {"choice": "keep", "confidence": 0.90},
        }
    )
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev", confidence_floor=0.75),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    decision = adjudicator._adjudicate_jev(
        prepared=prepared,
        presentation=presentation,
        mapping=mapping,
        meter=_MockCostMeter(),
        call_key="test_call",
    )

    # Forced new fact with sub-floor confidence
    assert decision.target.new_handle == "N1"
    assert decision.confidence == 0.62
    assert "sub-floor confidence" in decision.rationale


# ============================================================================
# 5. Invalid Response and Error Handling Tests
# ============================================================================


def test_adjudicate_jev_unrecognized_match_handle_raises() -> None:
    """Unrecognized candidate handle in match choice raises ProviderInvalidResponseError."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F99", "confidence": 0.90},
            "stance": {"choice": "supports", "confidence": 0.90},
            "window_action": {"choice": "keep", "confidence": 0.90},
        }
    )
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    with pytest.raises(
        ProviderInvalidResponseError, match="unrecognized match handle: F99"
    ):
        adjudicator._adjudicate_jev(
            prepared=prepared,
            presentation=presentation,
            mapping=mapping,
            meter=_MockCostMeter(),
            call_key="test_call",
        )


def test_adjudicate_jev_matched_with_not_applicable_stance_raises() -> None:
    """Matched candidate with not_applicable stance raises ProviderInvalidResponseError."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F1", "confidence": 0.90},
            "stance": {"choice": "not_applicable", "confidence": 0.90},
            "window_action": {"choice": "keep", "confidence": 0.90},
        }
    )
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    with pytest.raises(
        ProviderInvalidResponseError,
        match="matched candidate cannot have not_applicable stance",
    ):
        adjudicator._adjudicate_jev(
            prepared=prepared,
            presentation=presentation,
            mapping=mapping,
            meter=_MockCostMeter(),
            call_key="test_call",
        )


def test_adjudicate_jev_empty_candidate_short_circuits() -> None:
    """When presentation['facts'] is empty, short-circuit deterministically without network call."""
    snap = _sample_snapshot(with_candidate=False)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient()
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    decision = adjudicator._adjudicate_jev(
        prepared=prepared,
        presentation=presentation,
        mapping=mapping,
        meter=_MockCostMeter(),
        call_key="test_call",
    )

    assert len(mock_client.calls) == 0  # Zero network calls
    assert decision.target.new_handle == "assertion"
    assert decision.confidence == 1.0


def test_adjudicate_jev_fallback_to_prompt_on_provider_error() -> None:
    """When fallback_to_prompt=True, provider error delegates attempt to generative prompt."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        error=TypeSafeProviderError("TypeSafe upstream 503 unavailable")
    )
    mock_prompt_provider = _MockModelProvider()
    meter = _MockCostMeter()

    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=mock_prompt_provider,  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev", fallback_to_prompt=True),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    decision = adjudicator._adjudicate_jev(
        prepared=prepared,
        presentation=presentation,
        mapping=mapping,
        meter=meter,
        call_key="test_call",
    )

    assert len(mock_prompt_provider.calls) == 1
    assert decision.confidence == 0.95
    assert "Fallback prompt decision" in decision.rationale


def test_adjudicate_jev_invalid_stance_raises_provider_invalid_response_error() -> None:
    """Invalid stance choice raises ProviderInvalidResponseError."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F1", "confidence": 0.88},
            "stance": {"choice": "invalid_stance", "confidence": 0.95},
            "window_action": {"choice": "keep", "confidence": 0.90},
        }
    )
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    with pytest.raises(
        ProviderInvalidResponseError, match="unrecognized stance: invalid_stance"
    ):
        adjudicator._adjudicate_jev(
            prepared=prepared,
            presentation=presentation,
            mapping=mapping,
            meter=_MockCostMeter(),
            call_key="test_call",
        )


def test_adjudicate_jev_invalid_window_action_raises_provider_invalid_response_error() -> (
    None
):
    """Invalid window_action choice raises ProviderInvalidResponseError."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        answers={
            "match": {"choice": "F1", "confidence": 0.88},
            "stance": {"choice": "supports", "confidence": 0.95},
            "window_action": {"choice": "invalid_action", "confidence": 0.90},
        }
    )
    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=_MockModelProvider(),  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev"),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    with pytest.raises(
        ProviderInvalidResponseError, match="unrecognized window action: invalid_action"
    ):
        adjudicator._adjudicate_jev(
            prepared=prepared,
            presentation=presentation,
            mapping=mapping,
            meter=_MockCostMeter(),
            call_key="test_call",
        )


def test_adjudicate_jev_invalid_response_does_not_fallback_to_prompt() -> None:
    """When fallback_to_prompt=True, ProviderInvalidResponseError does NOT trigger fallback."""
    snap = _sample_snapshot(with_candidate=True)
    presentation, mapping = project_concise_inputs(snapshot=snap)

    mock_client = _MockSystemOneClient(
        error=ProviderInvalidResponseError("Malformed TypeSafe answer structure")
    )
    mock_prompt_provider = _MockModelProvider()

    adjudicator = FactAdjudicator(
        engine=None,  # type: ignore[arg-type]
        model_provider=mock_prompt_provider,  # type: ignore[arg-type]
        settings=FactAdjudicationSettings(engine="jev", fallback_to_prompt=True),
        systemone_provider=mock_client,
    )
    prepared = PreparedApplication(
        application_id=UUID(snap["application"]["application_id"]),
        attempt_id=uuid4(),
        input_hash="hash1",
        inputs=snap,
        decision=None,
    )

    with pytest.raises(
        ProviderInvalidResponseError, match="Malformed TypeSafe answer structure"
    ):
        adjudicator._adjudicate_jev(
            prepared=prepared,
            presentation=presentation,
            mapping=mapping,
            meter=_MockCostMeter(),
            call_key="test_call",
        )

    # Fallback prompt provider was never called
    assert len(mock_prompt_provider.calls) == 0
