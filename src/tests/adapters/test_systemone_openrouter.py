"""System One (Jev) through OpenRouter: key, ZDR routing, and billed cost."""

from decimal import Decimal
import json

import httpx
import pytest

from rememberstack.adapters.openrouter import OpenRouterSettings
from rememberstack.adapters.systemone import build_systemone_provider
from rememberstack.adapters.typesafe import ConfigurationError
from rememberstack.adapters.typesafe import TypeSafeSettings
from rememberstack.adapters.typesafe import TypeSafeSystemOneClient
from rememberstack.model import ProviderAccountingError

_OPENROUTER_BODY: dict[str, object] = {
    "id": "gen-dec-1789738314-X5e5eKGQdvR9rblyX250",
    "model": "typesafe/jev-1.13-20260917",
    "provider": "TypeSafe",
    "answers": {"match": {"choice": "NEW", "confidence": 0.97}},
    "usage": {"input_tokens": 476, "output_tokens": 70, "cost": 0.000019992},
}


def _capture(
    *, body: dict[str, object], seen: list[httpx.Request]
) -> httpx.MockTransport:
    """Record each request and answer with one fixed body."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


def _routed_client(
    *, openrouter: OpenRouterSettings, transport: httpx.MockTransport
) -> TypeSafeSystemOneClient:
    """Build through the composition seam, then swap in the mock transport."""
    provider = build_systemone_provider(
        settings=TypeSafeSettings(
            route="openrouter",
            api_key="direct-typesafe-key",
            base_url="https://api.typesafe.ai/v1",
        ),
        openrouter=openrouter,
    )
    assert isinstance(provider, TypeSafeSystemOneClient)
    provider.close()
    provider._client = httpx.Client(transport=transport)  # noqa: SLF001 - test seam
    return provider


def test_openrouter_route_uses_deployment_key_and_zdr_routing() -> None:
    """The deployment's OpenRouter key and ZDR flag govern every Jev call."""
    seen: list[httpx.Request] = []
    client = _routed_client(
        openrouter=OpenRouterSettings(api_key="project-child-key", zdr=True),
        transport=_capture(body=_OPENROUTER_BODY, seen=seen),
    )
    try:
        answers, usage = client.evaluate(
            state={"text": "Ada moved to Paris."},
            questions={"match": {"type": "choice", "criteria": "Same fact?"}},
        )
    finally:
        client.close()

    request = seen[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/systemone"
    assert request.headers["Authorization"] == "Bearer project-child-key"
    assert json.loads(request.content) == {
        "model": "jev-latest",
        "state": {"text": "Ada moved to Paris."},
        "questions": {"match": {"type": "choice", "criteria": "Same fact?"}},
        "provider": {"zdr": True, "data_collection": "deny"},
    }
    assert answers == _OPENROUTER_BODY["answers"]
    assert usage.model_name == "typesafe/jev-1.13-20260917"
    assert usage.tokens_in == 476
    assert usage.tokens_out == 70
    assert usage.cost_usd == Decimal("0.000019992")


def test_openrouter_route_without_zdr_sends_no_provider_preferences() -> None:
    """ZDR is opt-in per deployment, mirroring OpenRouter chat and embeddings."""
    seen: list[httpx.Request] = []
    client = _routed_client(
        openrouter=OpenRouterSettings(
            api_key="project-key", base_url="https://gateway.example/api/v1"
        ),
        transport=_capture(body=_OPENROUTER_BODY, seen=seen),
    )
    try:
        client.evaluate(state={}, questions={})
    finally:
        client.close()

    assert str(seen[0].url) == "https://gateway.example/api/v1/systemone"
    assert "provider" not in json.loads(seen[0].content)


def test_each_deployment_pays_with_its_own_openrouter_key() -> None:
    """Two deployments in one process never share a System One credential."""
    seen: list[httpx.Request] = []
    for key in ("project-a-key", "project-b-key"):
        client = _routed_client(
            openrouter=OpenRouterSettings(api_key=key, zdr=True),
            transport=_capture(body=_OPENROUTER_BODY, seen=seen),
        )
        try:
            client.evaluate(state={}, questions={})
        finally:
            client.close()

    assert [request.headers["Authorization"] for request in seen] == [
        "Bearer project-a-key",
        "Bearer project-b-key",
    ]


def test_direct_typesafe_route_is_unchanged() -> None:
    """The default route keeps TypeSafe's key, URL, payload and list price."""
    seen: list[httpx.Request] = []
    provider = build_systemone_provider(
        settings=TypeSafeSettings(api_key="direct-key"),
        openrouter=OpenRouterSettings(api_key="openrouter-key", zdr=True),
    )
    assert isinstance(provider, TypeSafeSystemOneClient)
    provider.close()
    provider._client = httpx.Client(  # noqa: SLF001 - test seam
        transport=_capture(
            body={
                "model": "jev-latest",
                "answers": {},
                "usage": {"input_tokens": 1_000_000, "output_tokens": 3},
            },
            seen=seen,
        )
    )
    try:
        _answers, usage = provider.evaluate(state={}, questions={})
    finally:
        provider.close()

    assert str(seen[0].url) == "https://api.typesafe.ai/v1/systemone"
    assert seen[0].headers["Authorization"] == "Bearer direct-key"
    assert "provider" not in json.loads(seen[0].content)
    assert usage.model_name == "typesafe/jev-latest"
    assert usage.cost_usd == Decimal("0.042")


@pytest.mark.parametrize(
    "usage",
    (
        {"input_tokens": 10, "output_tokens": 1},
        {"input_tokens": 10, "output_tokens": 1, "cost": -0.1},
        {"input_tokens": 10, "output_tokens": 1, "cost": True},
        {"input_tokens": 10, "output_tokens": 1, "cost": "not-a-number"},
        {"input_tokens": 10, "output_tokens": 1, "cost": None},
    ),
)
def test_openrouter_route_requires_valid_billed_cost(usage: dict[str, object]) -> None:
    """A missing or invalid OpenRouter cost is an accounting failure, not a guess."""
    client = _routed_client(
        openrouter=OpenRouterSettings(api_key="project-key"),
        transport=_capture(
            body={"model": "typesafe/jev-1.13", "answers": {}, "usage": usage}, seen=[]
        ),
    )
    try:
        with pytest.raises(ProviderAccountingError):
            client.evaluate(state={}, questions={})
    finally:
        client.close()


def test_route_reads_its_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hosts select OpenRouter for System One with one process-wide variable."""
    monkeypatch.setenv("REMEMBERSTACK_TYPESAFE_ROUTE", "openrouter")

    assert TypeSafeSettings().route == "openrouter"


def test_openrouter_route_requires_deployment_settings() -> None:
    """Without OpenRouter settings there is no key to use; refuse to start."""
    with pytest.raises(ConfigurationError, match="OpenRouter settings"):
        build_systemone_provider(settings=TypeSafeSettings(route="openrouter"))


def test_zdr_refuses_the_native_decisions_backup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The OpenAI backup would carry the same content outside ZDR routing."""
    monkeypatch.setenv("REMEMBERSTACK_OPENAI_DECISIONS_API_KEY", "native-key")
    with pytest.raises(ConfigurationError, match="BACKUP must be none"):
        build_systemone_provider(
            settings=TypeSafeSettings(route="openrouter", backup="openai_decisions"),
            openrouter=OpenRouterSettings(api_key="project-key", zdr=True),
        )


def test_zdr_preferences_require_the_openrouter_route() -> None:
    """TypeSafe's own API has no provider field, so ZDR cannot be sent there."""
    with pytest.raises(ConfigurationError, match="ROUTE=openrouter"):
        TypeSafeSystemOneClient(settings=TypeSafeSettings(api_key="direct"), zdr=True)
