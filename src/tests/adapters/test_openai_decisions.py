"""Native typed decisions, cost accounting, and bounded availability fallback."""

from decimal import Decimal
import json
from typing import Any

import httpx
from pydantic import SecretStr
import pytest

from rememberstack.adapters.openai_decisions import OpenAIDecisionsClient
from rememberstack.adapters.openai_decisions import OpenAIDecisionsSettings
from rememberstack.adapters.systemone import AvailabilityFallbackSystemOne
from rememberstack.adapters.systemone import build_systemone_provider
from rememberstack.adapters.typesafe import TypeSafeProviderError
from rememberstack.adapters.typesafe import TypeSafeSettings
from rememberstack.adapters.typesafe import TypeSafeSystemOneClient
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderInvalidResponseError

_QUESTIONS = {
    "match": {
        "type": "choice",
        "instructions": "Which existing item matches, or is it new?",
        "criteria": {"F1": "First item", "NEW": "A new item"},
    },
    "present": {
        "type": "noul",
        "instructions": "Is evidence present?",
        "criteria": {"true": "Evidence exists", "false": "No evidence"},
    },
    "severity": {
        "type": "score",
        "instructions": "How severe?",
        "criteria": ["Small", {"meaning": "Large"}],
    },
}


def _body() -> dict[str, Any]:
    """Return one real-contract response containing all three primitives."""
    return {
        "model": "gpt-6-luna",
        "answers": [
            {
                "type": "choice",
                "name": "match",
                "choice": "NEW",
                "confidence": 0.7,
                "probabilities": [
                    {"value": "F1", "probability": 0.15},
                    {"value": "NEW", "probability": 0.85},
                ],
            },
            {"type": "predicate", "name": "present", "probability": 0.8},
            {
                "type": "score",
                "name": "severity",
                "score": 0.75,
                "confidence": 0.5,
                "probabilities": [
                    {"value": 0, "probability": 0.25},
                    {"value": 1, "probability": 0.75},
                ],
            },
        ],
        "usage": {
            "input_tokens": 1_000,
            "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
            "output_tokens": 12,
        },
    }


def _native(
    *, transport: httpx.MockTransport, **settings: Any
) -> OpenAIDecisionsClient:
    """Build a native client whose transport cannot reach the network."""
    return OpenAIDecisionsClient(
        settings=OpenAIDecisionsSettings(api_key=SecretStr("native-test"), **settings),
        client=httpx.Client(transport=transport),
    )


def test_native_preserves_primitives_options_state_and_serving_usage() -> None:
    """Translation keeps the complete decision, not a generated JSON approximation."""
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Inspect the native request, including credentials and closed options."""
        assert str(request.url) == "https://api.openai.com/v1/decisions"
        assert request.headers["Authorization"] == "Bearer native-test"
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_body())

    client = _native(transport=httpx.MockTransport(handler))
    try:
        answers, usage = client.evaluate(
            state={"evidence": "source"}, questions=_QUESTIONS
        )
    finally:
        client.close()
    assert len(calls) == 1
    payload = calls[0]
    assert payload["model"] == "gpt-6-luna"
    assert payload["input"][1]["content"][0] == {
        "type": "input_json",
        "data": {"evidence": "source"},
    }
    assert [q["type"] for q in payload["questions"]] == ["choice", "predicate", "score"]
    assert [option["value"] for option in payload["questions"][0]["choices"]] == [
        "F1",
        "NEW",
    ]
    assert payload["prompt_cache_options"] == {"mode": "implicit", "ttl": "30m"}
    assert "reasoning" not in payload
    assert answers["match"]["choice"] == "NEW"
    assert answers["match"]["confidence"] == 0.7
    assert answers["present"] == {"type": "noul", "noul": 0.8}
    assert answers["severity"]["score"] == 0.75
    assert answers["severity"]["legend"] == {"0": "Small", "1": {"meaning": "Large"}}
    assert usage.model_name == "openai-decisions/gpt-6-luna"
    assert usage.cost_usd == Decimal("0.0001")


@pytest.mark.parametrize(
    ("incoming", "cached", "written", "endpoint", "expected"),
    [
        (200_000, 30_000, 20_000, "https://api.openai.com/v1", "0.0178"),
        (1_000_000, 100_000, 50_000, "https://api.openai.com/v1", "0.1845"),
        (200_000, 30_000, 20_000, "https://eu.api.openai.com/v1", "0.01958"),
    ],
)
def test_native_accounts_for_cache_writes_long_context_and_region(
    incoming: int, cached: int, written: int, endpoint: str, expected: str
) -> None:
    """No cache category is counted twice, and output tokens remain free."""
    body = _body()
    body["usage"] = {
        "input_tokens": incoming,
        "input_tokens_details": {
            "cached_tokens": cached,
            "cache_write_tokens": written,
        },
        "output_tokens": 100_000,
    }
    client = _native(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        base_url=endpoint,
    )
    try:
        _, usage = client.evaluate(state={}, questions=_QUESTIONS)
    finally:
        client.close()
    assert usage.cost_usd == Decimal(expected)
    assert usage.tokens_out == 100_000


@pytest.mark.parametrize(
    "fault", ["missing", "details", "boolean", "negative", "overflow", "unknown_model"]
)
def test_native_missing_or_inconsistent_accounting_stops_without_replay(
    fault: str,
) -> None:
    """An invalid price basis is never silently replaced with zero or another call."""
    body = _body()
    if fault == "missing":
        del body["usage"]
    elif fault == "details":
        del body["usage"]["input_tokens_details"]
    elif fault == "boolean":
        body["usage"]["input_tokens"] = True
    elif fault == "negative":
        body["usage"]["output_tokens"] = -1
    elif fault == "overflow":
        body["usage"]["input_tokens_details"]["cached_tokens"] = 1_001
    else:
        body["model"] = "unpriced-model"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Count requests to prove no paid replay occurs in the adapter."""
        calls.append(request.method)
        return httpx.Response(200, json=body)

    client = _native(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderAccountingError):
            client.evaluate(state={}, questions=_QUESTIONS)
    finally:
        client.close()
    assert calls == ["POST"]


@pytest.mark.parametrize(
    "fault",
    ["unknown_choice", "duplicate_name", "missing_option", "refusal", "wrong_score"],
)
def test_native_invalid_answers_keep_their_billable_usage(fault: str) -> None:
    """Every invalid closed decision still carries the metered native response."""
    body = _body()
    if fault == "unknown_choice":
        body["answers"][0]["choice"] = "invented"
    elif fault == "duplicate_name":
        body["answers"][1]["name"] = "match"
    elif fault == "missing_option":
        body["answers"][0]["probabilities"].pop()
    elif fault == "refusal":
        body["answers"][0] = {"type": "refusal", "name": "match"}
    else:
        body["answers"][2]["score"] = 0.2
    client = _native(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )
    try:
        with pytest.raises(ProviderInvalidResponseError) as failure:
            client.evaluate(state={}, questions=_QUESTIONS)
    finally:
        client.close()
    assert failure.value.usage is not None
    assert failure.value.usage.cost_usd == Decimal("0.0001")


@pytest.mark.parametrize("status", [429, 529])
def test_exhausted_primary_admission_rejection_uses_one_native_backup(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Only after primary retries, preserve the same state and choices in backup."""
    monkeypatch.setattr("rememberstack.adapters.typesafe.time.sleep", lambda _: None)
    calls: list[str] = []

    def primary_request(request: httpx.Request) -> httpx.Response:
        """Reject primary admission with a documented unavailability response."""
        calls.append("primary")
        assert json.loads(request.content)["model"] == "jev-pinned"
        return httpx.Response(status, json={"error": "capacity unavailable"})

    def backup_request(request: httpx.Request) -> httpx.Response:
        """The backup receives its own model, never the Jev model ID."""
        calls.append("backup")
        assert json.loads(request.content)["model"] == "gpt-6-luna"
        return httpx.Response(200, json=_body())

    primary = TypeSafeSystemOneClient(
        settings=TypeSafeSettings(api_key="primary-test"),
        client=httpx.Client(transport=httpx.MockTransport(primary_request)),
    )
    backup = _native(transport=httpx.MockTransport(backup_request))
    provider = AvailabilityFallbackSystemOne(
        primary=primary, backup=backup, backup_model="gpt-6-luna"
    )
    try:
        answers, usage = provider.evaluate(
            model="jev-pinned", state={}, questions=_QUESTIONS
        )
    finally:
        primary.close()
        backup.close()
    assert calls == ["primary"] * 4 + ["backup"]
    assert answers["match"]["choice"] == "NEW"
    assert usage.model_name == "openai-decisions/gpt-6-luna"


@pytest.mark.parametrize(
    "fault",
    [
        "low_confidence",
        "auth",
        "gateway",
        "missing_usage",
        "paid_rejection",
        "read_timeout",
    ],
)
def test_backup_never_masks_uncertainty_configuration_or_accounting(
    monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """A backup is not an answer-shopping or ambiguous-paid-response retry loop."""
    monkeypatch.setattr("rememberstack.adapters.typesafe.time.sleep", lambda _: None)
    calls: list[str] = []

    def primary_request(request: httpx.Request) -> httpx.Response:
        """Exercise distinct valid and failed primary outcomes."""
        calls.append("primary")
        if fault == "auth":
            return httpx.Response(401, json={"error": "invalid key"})
        if fault == "gateway":
            return httpx.Response(502, json={"error": "gateway failure"})
        if fault == "read_timeout":
            raise httpx.ReadTimeout("ambiguous response", request=request)
        body = {
            "model": "jev-latest",
            "answers": {"q": {"type": "choice", "choice": "NEW", "confidence": 0.1}},
            "usage": {"input_tokens": 1000, "output_tokens": 3},
        }
        if fault == "missing_usage":
            del body["usage"]
        return httpx.Response(429 if fault == "paid_rejection" else 200, json=body)

    def forbidden_backup(request: httpx.Request) -> httpx.Response:
        """A forbidden second provider call fails the test immediately."""
        del request
        pytest.fail("native backup was not authorized by this primary result")

    primary = TypeSafeSystemOneClient(
        settings=TypeSafeSettings(api_key="primary-test"),
        client=httpx.Client(transport=httpx.MockTransport(primary_request)),
    )
    backup = _native(transport=httpx.MockTransport(forbidden_backup))
    provider = AvailabilityFallbackSystemOne(
        primary=primary, backup=backup, backup_model="gpt-6-luna"
    )
    try:
        if fault == "low_confidence":
            answers, _ = provider.evaluate(state={}, questions={})
            assert answers["q"]["confidence"] == 0.1
        else:
            with pytest.raises((ProviderCallError, ProviderAccountingError)) as failure:
                provider.evaluate(state={}, questions={})
            if fault == "paid_rejection":
                assert isinstance(failure.value, TypeSafeProviderError)
                assert failure.value.usage is not None
        assert set(calls) == {"primary"}
    finally:
        primary.close()
        backup.close()


def test_unsupported_question_is_rejected_before_http() -> None:
    """Native Decisions cannot silently approximate an unsupported primitive."""
    client = _native(
        transport=httpx.MockTransport(lambda _: pytest.fail("HTTP called"))
    )
    try:
        with pytest.raises(ValueError, match="Unsupported"):
            client.evaluate(
                state={},
                questions={"q": {"type": "box", "instructions": "Find a region"}},
            )
    finally:
        client.close()


def test_backup_is_opt_in_and_requires_its_own_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An OpenRouter key or successful Jev configuration cannot imply native access."""
    monkeypatch.delenv("REMEMBERSTACK_OPENAI_DECISIONS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert TypeSafeSettings(api_key="primary").backup == "none"
    with pytest.raises(ValueError, match="DECISIONS_API_KEY"):
        build_systemone_provider(
            settings=TypeSafeSettings(api_key="primary", backup="openai_decisions")
        )
