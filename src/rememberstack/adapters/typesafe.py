"""D61/D126 substrate adapter for TypeSafe AI System One models."""

from decimal import Decimal
from decimal import InvalidOperation
import time
from typing import Any
from typing import Literal
from typing import Mapping

import httpx
from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.adapters.openrouter import OpenRouterSettings
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.ports.systemone import SystemOnePort


class ConfigurationError(ValueError):
    """Raised when required TypeSafe configuration is missing or invalid."""


class TypeSafeProviderError(ProviderCallError):
    """A TypeSafe AI provider call failed."""


class TypeSafeUnavailableError(TypeSafeProviderError):
    """A connection or explicit rate/overload rejection exhausted bounded retries."""


class TypeSafeSettings(BaseSettings):
    """TypeSafe AI System One provider settings."""

    model_config = SettingsConfigDict(
        env_prefix="REMEMBERSTACK_TYPESAFE_", extra="ignore"
    )

    api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "REMEMBERSTACK_TYPESAFE_API_KEY",
            "TYPESAFE_AI_API_KEY",
            "typesafe_ai_api_key",
            "api_key",
        ),
    )
    model: str = "jev-latest"
    base_url: str = "https://api.typesafe.ai/v1"
    timeout_s: float = 30.0
    fallback_to_prompt: bool = False
    backup: Literal["none", "openai_decisions"] = "none"
    route: Literal["typesafe", "openrouter"] = "typesafe"
    """Who serves System One. Env: ``REMEMBERSTACK_TYPESAFE_ROUTE``.

    ``typesafe`` (default) calls TypeSafe's own API with ``api_key`` and
    ``base_url``. ``openrouter`` calls OpenRouter's TypeSafe-compatible
    ``/systemone`` endpoint with the deployment's OpenRouter settings: its
    key, base URL and ZDR flag. ``api_key`` and ``base_url`` here are then
    ignored, so a host serving several deployments bills each one to its own
    OpenRouter key. Cost comes from OpenRouter's reported ``usage.cost``.
    """


class TypeSafeSystemOneClient(SystemOnePort):
    """TypeSafe AI System One client implementing SystemOnePort (D61/D126)."""

    def __init__(
        self,
        settings: TypeSafeSettings | None = None,
        *,
        client: httpx.Client | None = None,
        openrouter: OpenRouterSettings | None = None,
    ) -> None:
        """Initialize the client, failing fast on missing or unsafe configuration.

        On the ``openrouter`` route the deployment's ``openrouter`` settings
        are required and are the only source of the key, base URL and ZDR
        flag; the TypeSafe key and URL are never used, so no construction
        path can fall back to a process-wide TypeSafe key. Under ZDR every
        request carries OpenRouter's zero-data-retention routing preferences.
        On the ``typesafe`` route a deployment that asks for ZDR is refused:
        TypeSafe's own API has no such control.
        """
        resolved = settings or TypeSafeSettings()
        zdr = False
        if resolved.route == "openrouter":
            if openrouter is None:
                raise ConfigurationError(
                    "REMEMBERSTACK_TYPESAFE_ROUTE=openrouter needs the deployment's"
                    " OpenRouter settings"
                )
            resolved = resolved.model_copy(
                update={"api_key": openrouter.api_key, "base_url": openrouter.base_url}
            )
            zdr = openrouter.zdr
        elif openrouter is not None and openrouter.zdr:
            raise ConfigurationError(
                "REMEMBERSTACK_OPENROUTER_ZDR=true needs"
                " REMEMBERSTACK_TYPESAFE_ROUTE=openrouter for Jev: TypeSafe's own"
                " API cannot be held to zero data retention"
            )
        self._settings = resolved
        if not self._settings.api_key or not self._settings.api_key.strip():
            raise ConfigurationError(
                "REMEMBERSTACK_OPENROUTER_API_KEY is required for System One via"
                " OpenRouter"
                if self._settings.route == "openrouter"
                else "REMEMBERSTACK_TYPESAFE_API_KEY is required for TypeSafe AI"
                " System One client"
            )
        self._zdr = zdr
        self._client = client or httpx.Client(timeout=self._settings.timeout_s)

    def evaluate(
        self,
        *,
        model: str | None = None,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
        timeout_s: float | None = None,
    ) -> tuple[Mapping[str, Any], ProviderCallUsage]:
        """Evaluate state against typed questions, returning answers and usage."""
        target_model = model or self._settings.model
        url = f"{self._settings.base_url.rstrip('/')}/systemone"
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, object] = {
            "model": target_model,
            "state": dict(state),
            "questions": dict(questions),
        }
        if self._zdr:
            # Jev is TypeSafe-only on OpenRouter; zdr selects its ZDR endpoint.
            payload["provider"] = {"zdr": True, "data_collection": "deny"}
        reported_cost = self._settings.route == "openrouter"
        timeout = timeout_s if timeout_s is not None else self._settings.timeout_s
        retry_delays = (0.5, 1.0, 2.0)
        last_error: Exception | None = None
        backup_eligible = True

        for attempt, delay in enumerate((0.0, *retry_delays)):
            if delay > 0.0:
                time.sleep(delay)
            started_at = time.monotonic()
            try:
                response = self._client.post(
                    url, headers=headers, json=payload, timeout=timeout
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                connection_failure = isinstance(
                    error, (httpx.ConnectError, httpx.ConnectTimeout)
                )
                backup_eligible = backup_eligible and connection_failure
                last_error = error
                if attempt < len(retry_delays):
                    continue
                error_type = (
                    TypeSafeUnavailableError
                    if backup_eligible
                    else TypeSafeProviderError
                )
                raise error_type(
                    f"TypeSafe request failed after {len(retry_delays)} retries: {error}"
                ) from error

            if response.status_code in (429, 500, 502, 503, 504, 529):
                try:
                    rejected = response.json()
                except ValueError:
                    rejected = None
                if isinstance(rejected, dict) and any(
                    key in rejected for key in ("answers", "usage")
                ):
                    usage = _typesafe_usage(
                        body=rejected,
                        target_model=target_model,
                        elapsed_s=time.monotonic() - started_at,
                        reported_cost=reported_cost,
                    )
                    raise TypeSafeProviderError(
                        "TypeSafe error response carried completion accounting",
                        usage=usage,
                    )
                unavailable = response.status_code in (429, 529) and isinstance(
                    rejected, dict
                )
                backup_eligible = backup_eligible and unavailable
                error_type = (
                    TypeSafeUnavailableError
                    if backup_eligible
                    else TypeSafeProviderError
                )
                last_error = error_type(
                    f"TypeSafe returned {response.status_code}: {response.text}"
                )
                if attempt < len(retry_delays):
                    continue
                raise last_error

            if response.status_code >= 400:
                raise TypeSafeProviderError(
                    f"TypeSafe returned {response.status_code}: {response.text}"
                )

            elapsed_s = time.monotonic() - started_at
            try:
                data = response.json()
            except Exception as error:
                raise ProviderAccountingError(
                    f"TypeSafe returned malformed JSON: {error}"
                ) from error

            call_usage = _typesafe_usage(
                body=data,
                target_model=target_model,
                elapsed_s=elapsed_s,
                reported_cost=reported_cost,
            )

            answers = data.get("answers")
            if not isinstance(answers, dict):
                raise ProviderInvalidResponseError(
                    "TypeSafe response missing 'answers'", usage=call_usage
                )

            return answers, call_usage

        if last_error is not None:
            raise last_error
        raise TypeSafeProviderError("TypeSafe request failed")

    def close(self) -> None:
        """Release the owned transport at process shutdown."""
        self._client.close()


def _typesafe_usage(
    *, body: object, target_model: str, elapsed_s: float, reported_cost: bool
) -> ProviderCallUsage:
    """Preserve known accounting before parsing answers or considering fallback.

    TypeSafe's own API is priced locally ($0.042 per million input tokens).
    Through OpenRouter (``reported_cost``) the billed ``usage.cost`` is
    required, as for OpenRouter chat; OpenRouter already names the serving
    model in the ``typesafe/`` namespace, so it is not prefixed twice.
    """
    if not isinstance(body, Mapping) or not isinstance(body.get("usage"), Mapping):
        raise ProviderAccountingError("TypeSafe response carries no token usage")
    usage = body["usage"]
    incoming = usage.get("input_tokens")
    outgoing = usage.get("output_tokens", 0)
    if (
        type(incoming) is not int
        or type(outgoing) is not int
        or incoming < 0
        or outgoing < 0
    ):
        raise ProviderAccountingError("TypeSafe response carries no valid token usage")
    model = str(body.get("model") or target_model).removeprefix("~")
    if reported_cost:
        cost_usd = _reported_cost(raw=usage.get("cost"))
    else:
        cost_usd = Decimal(incoming) * Decimal("0.000000042")
    return ProviderCallUsage(
        model_name=model if model.startswith("typesafe/") else f"typesafe/{model}",
        tokens_in=incoming,
        tokens_out=outgoing,
        cost_usd=cost_usd,
        latency_ms=int(elapsed_s * 1000),
    )


def _reported_cost(*, raw: object) -> Decimal:
    """Parse OpenRouter's billed ``usage.cost``; absence is an accounting gap."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ProviderAccountingError("OpenRouter System One response carries no cost")
    try:
        cost = Decimal(str(raw))
    except InvalidOperation as error:
        raise ProviderAccountingError(
            "OpenRouter System One response carries an invalid cost"
        ) from error
    if not cost.is_finite() or cost < 0:
        raise ProviderAccountingError(
            "OpenRouter System One response carries an invalid cost"
        )
    return cost
