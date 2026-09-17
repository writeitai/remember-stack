"""D61/D126 substrate adapter for TypeSafe AI System One models."""

from decimal import Decimal
import time
from typing import Any
from typing import Mapping

import httpx
from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.ports.systemone import SystemOnePort


class ConfigurationError(ValueError):
    """Raised when required TypeSafe configuration is missing or invalid."""


class TypeSafeProviderError(ProviderCallError):
    """A TypeSafe AI provider call failed."""


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


class TypeSafeSystemOneClient(SystemOnePort):
    """TypeSafe AI System One client implementing SystemOnePort (D61/D126)."""

    def __init__(
        self,
        settings: TypeSafeSettings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        """Initialize the client, failing fast if API key is missing."""
        self._settings = settings or TypeSafeSettings()
        if not self._settings.api_key or not self._settings.api_key.strip():
            raise ConfigurationError(
                "REMEMBERSTACK_TYPESAFE_API_KEY is required for TypeSafe AI System One client"
            )
        self._client = client or httpx.Client(timeout=self._settings.timeout_s)

    def evaluate(
        self,
        *,
        model: str,
        state: Mapping[str, Any],
        questions: Mapping[str, Any],
        timeout_s: float | None = None,
    ) -> tuple[Mapping[str, Any], ProviderCallUsage]:
        """Evaluate state against typed questions, returning answers and usage."""
        url = f"{self._settings.base_url.rstrip('/')}/systemone"
        headers = {
            "Authorization": f"Bearer {self._settings.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"model": model, "state": dict(state), "questions": dict(questions)}
        timeout = timeout_s if timeout_s is not None else self._settings.timeout_s
        retry_delays = (0.5, 1.0, 2.0)
        last_error: Exception | None = None

        for attempt, delay in enumerate((0.0, *retry_delays)):
            if delay > 0.0:
                time.sleep(delay)
            started_at = time.monotonic()
            try:
                response = self._client.post(
                    url, headers=headers, json=payload, timeout=timeout
                )
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = error
                if attempt < len(retry_delays):
                    continue
                raise TypeSafeProviderError(
                    f"TypeSafe request failed after {len(retry_delays)} retries: {error}"
                ) from error

            if response.status_code in (429, 500, 502, 503, 504, 529):
                last_error = TypeSafeProviderError(
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
                raise ProviderInvalidResponseError(
                    f"TypeSafe returned malformed JSON: {error}"
                ) from error

            usage = data.get("usage")
            if not isinstance(usage, dict) or "input_tokens" not in usage:
                raise ProviderAccountingError(
                    "TypeSafe response carries no token usage"
                )

            tokens_in = int(usage["input_tokens"])
            tokens_out = int(usage.get("output_tokens", 0))
            cost_usd = Decimal(
                str(round(Decimal(tokens_in) * Decimal("0.000000042"), 6))
            )
            latency_ms = int(elapsed_s * 1000)

            call_usage = ProviderCallUsage(
                model_name=f"typesafe/{data.get('model', model)}",
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
            )

            answers = data.get("answers")
            if not isinstance(answers, dict):
                raise ProviderInvalidResponseError(
                    "TypeSafe response missing 'answers'"
                )

            return answers, call_usage

        if last_error is not None:
            raise last_error
        raise TypeSafeProviderError("TypeSafe request failed")
