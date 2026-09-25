"""The OpenRouter model-provider adapter (D63/D70): the shipped default binding."""

from decimal import Decimal
from decimal import InvalidOperation
import hashlib
import json
import logging
import os
from pathlib import Path
import random
import time
from typing import Any
from typing import Final
from typing import Protocol
from typing import TypeVar

import httpx
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator
from pydantic import ValidationError
from pydantic import ValidationInfo
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.adapters.generation_recorder import GenerationOutcome
from rememberstack.adapters.generation_recorder import GenerationRecord
from rememberstack.adapters.generation_recorder import GenerationRecorder
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model import ReasoningEffort
from rememberstack.model import record_embedding_usage
from rememberstack.model import StructuredResponseModel

ResponseT = TypeVar("ResponseT", bound=StructuredResponseModel)

"""Allowed OpenRouter reasoning-effort values (global pin or per-model map)."""

_ALLOWED_REASONING_EFFORTS: Final[frozenset[str]] = frozenset(
    ("none", "minimal", "low", "medium", "high", "xhigh", "max")
)
_DEFAULT_MAX_COMPLETION_TOKENS: Final[int] = 32_000
_GENERATION_USAGE_POLL_DELAYS_S: Final[tuple[float, ...]] = (0.0, 1.0, 2.0, 3.0, 5.0)
_GENERATION_USAGE_TIMEOUT_S: Final[float] = 10.0
_IN_FLIGHT_BUDGET_MAX_RETRY_AFTER_S: Final[float] = 120.0
_UPSTREAM_OVERLOAD_MAX_RETRY_AFTER_S: Final[float] = 30.0
_IN_FLIGHT_BUDGET_RETRIES: Final[int] = 3
_SAFE_FINISH_REASONS: Final[frozenset[str]] = frozenset(
    ("stop", "length", "content_filter", "tool_calls", "error", "cancelled")
)
_logger = logging.getLogger(__name__)


def _parse_provider_name_list(*, value: object, field_name: str) -> object:
    """Parse comma-separated or JSON list of OpenRouter provider slugs.

    Empty strings (Compose unset optionals) become ``None``. Used by embedding
    (and future chat) ordered shortlists.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.startswith("["):
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"{field_name} must be comma-separated names or a JSON list"
                ) from error
        else:
            value = [part.strip() for part in stripped.split(",") if part.strip()]
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field_name} must be a non-empty list of strings")
    names = [str(item).strip() for item in value if str(item).strip()]
    return names or None


class StrictSchemaError(ValueError):
    """A response model cannot be expressed under strict structured output.

    This is a programmer error caught before any HTTP request — never a model
    or provider failure — so it gets its own type: broad except blocks around
    provider calls must not be able to misclassify it as a flaky reply.
    """


class OpenRouterSettings(BaseSettings):
    """The OpenRouter binding: key and endpoint, per deployment (D61)."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_OPENROUTER_")

    api_key: str = Field(min_length=1)
    base_url: str = Field(default="https://openrouter.ai/api/v1")
    timeout_s: float = Field(default=120.0, gt=0)
    max_completion_tokens: int | None = Field(
        default=_DEFAULT_MAX_COMPLETION_TOKENS, ge=1
    )
    """Combined reasoning-and-content budget for chat completions.

    The 32k default gives reasoning models deliberate generation headroom;
    the provider account cap remains the deployment's monetary boundary.
    Explicit ``None`` omits ``max_tokens`` from the provider payload.
    """
    embedding_provider: str | None = None
    """Optional single OpenRouter embedding provider pin (``provider.only``).

    Prefer ``embedding_provider_order`` when you want a priced shortlist with
    failover (e.g. Nebius then DeepInfra then SiliconFlow). When both are set,
    the order wins.
    """
    embedding_provider_order: list[str] | None = None
    """Ordered OpenRouter embedding providers (``provider.order`` + fallbacks).

    Env: ``REMEMBERSTACK_OPENROUTER_EMBEDDING_PROVIDER_ORDER`` as a
    comma-separated list of *provider slugs* (not quantization tags): e.g.
    ``nebius,deepinfra,siliconflow``. Tags like ``siliconflow/fp8`` are
    endpoint labels; routing uses the base provider slug from the endpoints
    API. See ``design/operations/openrouter-embedding-routing.md``.
    """
    chat_provider_only: list[str] | None = None
    """Hard OpenRouter chat provider allowlist (``provider.only``, no escape).

    Env: ``REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ONLY`` as a comma-separated
    list of *provider slugs*. Unlike the embedding order, there is no
    marketplace fallback: ``allow_fallbacks`` is False, so a request the
    listed hosts cannot serve fails instead of routing to an unapproved host.
    """
    chat_provider_order: list[str] | None = None
    """Ordered OpenRouter chat providers (``provider.order`` + fallbacks).

    Env: ``REMEMBERSTACK_OPENROUTER_CHAT_PROVIDER_ORDER`` as a comma-separated
    list of *provider slugs* (same slug rules as the embedding order). Default
    ``None`` keeps automatic routing: a hardcoded list here would break every
    other model on this shared adapter, so per-model orders (e.g. GLM) belong
    in deployment configuration, not in code. Mutually exclusive with
    ``chat_provider_only``.
    """
    chat_throttle_retries: int = Field(default=3, ge=0)
    """Dedicated 429 attempts per rotating chat call, separate from the ledger.

    Env: ``REMEMBERSTACK_OPENROUTER_CHAT_THROTTLE_RETRIES``. Throttles delay
    work (rotate + wait); they never consume the caller's attempt budget.
    Unconfigured routing keeps the historic in-post bound (numerically
    identical at this default), so this setting only bites once slugs exist.
    """
    chat_upstream_overload_max_retry_after_s: float = Field(default=30.0, gt=0)
    """Per-wait cap for upstream-overload backoff.

    Env: ``REMEMBERSTACK_OPENROUTER_CHAT_UPSTREAM_OVERLOAD_MAX_RETRY_AFTER_S``. An explicit
    Retry-After below the cap wins; anything larger is clamped to the cap.
    """
    zdr: bool = False
    """Restrict chat routing to zero-data-retention endpoints when true.

    Env: ``REMEMBERSTACK_OPENROUTER_ZDR``. Sends ``zdr: true``; provider picks
    already exclude retaining hosts separately — this flag is the enforcement.
    """
    reasoning_effort: ReasoningEffort | None = None
    reasoning_effort_map: dict[str, ReasoningEffort] | None = None
    """Optional per-model effort overrides as a JSON object env var
    (`REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP`, e.g.
    `{"z-ai/glm-4.7-flash":"none","openai/gpt-5.6-luna":"high"}`). A model's
    entry wins over the global `reasoning_effort` for requests to that model;
    absent entries fall back to the global pin (or the model default when the
    global pin is also unset). Values must be one of the allowed effort
    literals."""
    invalid_completion_capture_dir: Path | None = None
    """Private opt-in directory for raw schema-invalid chat completions.

    Disabled by default because a completion can repeat source material. Files
    are created mode 0600 and contain no prompt, but operators must still treat
    the directory as customer data.
    """

    @field_validator(
        "embedding_provider",
        "reasoning_effort",
        "invalid_completion_capture_dir",
        mode="before",
    )
    @classmethod
    def normalize_optional_string(cls, value: object) -> object:
        """Treat Compose's empty optional values as unset."""
        if not isinstance(value, str):
            return value
        return value.strip() or None

    @field_validator("invalid_completion_capture_dir")
    @classmethod
    def require_absolute_capture_dir(cls, value: Path | None) -> Path | None:
        """Keep debug artifacts in one explicit, predictable private location."""
        if value is not None and not value.is_absolute():
            raise ValueError("invalid_completion_capture_dir must be absolute")
        return value

    @field_validator("embedding_provider_order", mode="before")
    @classmethod
    def parse_embedding_provider_order(cls, value: object) -> object:
        """Parse comma-separated or JSON list of OpenRouter provider slugs."""
        return _parse_provider_name_list(
            value=value, field_name="embedding_provider_order"
        )

    @field_validator("chat_provider_only", mode="before")
    @classmethod
    def parse_chat_provider_only(cls, value: object, info: ValidationInfo) -> object:
        """Parse comma-separated or JSON list of OpenRouter provider slugs."""
        return _parse_provider_name_list(
            value=value, field_name=info.field_name or "chat_provider_only"
        )

    @field_validator("chat_provider_order", mode="before")
    @classmethod
    def parse_chat_provider_order(cls, value: object, info: ValidationInfo) -> object:
        """Parse comma-separated or JSON list of OpenRouter provider slugs."""
        return _parse_provider_name_list(
            value=value, field_name=info.field_name or "chat_provider_order"
        )

    @model_validator(mode="after")
    def require_single_chat_routing(self) -> "OpenRouterSettings":
        """Reject a chat allowlist together with an ordered shortlist."""
        if self.chat_provider_only and self.chat_provider_order:
            raise ValueError(
                "chat_provider_only and chat_provider_order are mutually exclusive"
            )
        return self

    @field_validator("max_completion_tokens", mode="before")
    @classmethod
    def default_empty_max_completion_tokens(cls, value: object) -> object:
        """Treat an empty env value as the deliberate 32k default."""
        if isinstance(value, str) and not value.strip():
            return _DEFAULT_MAX_COMPLETION_TOKENS
        return value

    @field_validator("reasoning_effort_map", mode="before")
    @classmethod
    def parse_reasoning_effort_map(cls, value: object) -> object:
        """Parse the JSON object env form and reject unknown effort literals.

        Compose often supplies empty strings for unset optionals; treat those
        as unset. A JSON string is accepted because pydantic-settings may hand
        the raw env value through before typed decoding on some paths.
        """
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            try:
                value = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise ValueError(
                    "reasoning_effort_map must be a JSON object of model-id → effort"
                ) from error
        if not isinstance(value, dict):
            raise ValueError(
                "reasoning_effort_map must be a JSON object of model-id → effort"
            )
        parsed: dict[str, str] = {}
        for model_id, effort in value.items():
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError(
                    "reasoning_effort_map keys must be non-empty model id strings"
                )
            if not isinstance(effort, str) or effort not in _ALLOWED_REASONING_EFFORTS:
                raise ValueError(
                    f"reasoning_effort_map[{model_id!r}]={effort!r} is not an"
                    f" allowed effort"
                    f" ({', '.join(sorted(_ALLOWED_REASONING_EFFORTS))})"
                )
            parsed[model_id.strip()] = effort
        return parsed or None


class OpenRouterProviderError(ProviderCallError):
    """OpenRouter returned an error or an unusable response body."""

    def __init__(
        self,
        message: str,
        *,
        usage: ProviderCallUsage | None = None,
        provider_host: str | None = None,
    ) -> None:
        """Keep usage for the meter and the serving host for diagnosis."""
        super().__init__(message, usage=usage)
        self.provider_host = provider_host


class OpenRouterInvalidResponseError(
    OpenRouterProviderError, ProviderInvalidResponseError
):
    """OpenRouter completed a generation without a schema-valid output."""


class OpenRouterModelProvider:
    """Structured generations and embeddings over the OpenRouter HTTP API."""

    def __init__(
        self,
        *,
        settings: OpenRouterSettings,
        recorder: GenerationRecorder | None = None,
    ) -> None:
        """Bind one HTTP client to the configured endpoint and key.

        ``recorder`` is an opt-in full-payload sink for benchmark diagnosis;
        ``None`` (the default) records nothing.
        """
        self._settings = settings
        self._recorder = recorder
        self._client = httpx.Client(
            base_url=settings.base_url,
            headers={"Authorization": f"Bearer {settings.api_key}"},
            timeout=settings.timeout_s,
        )

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """One chat completion constrained to the caller's declared JSON schema."""
        started_ns = time.monotonic_ns()
        payload: dict[str, object] = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": response_type.__name__,
                    "strict": True,
                    "schema": _strict_json_schema(response_type),
                },
            },
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if self._settings.max_completion_tokens is not None:
            payload["max_tokens"] = self._settings.max_completion_tokens
        effort = self._reasoning_effort_for(request=request)
        if effort is not None:
            payload["reasoning"] = {"effort": effort}
        provider = self._chat_provider_payload()
        if provider is not None:
            payload["provider"] = provider

        try:
            content, usage, body, provider_host = self._completion_text(
                payload=payload,
                response_type=response_type,
                started_ns=started_ns,
                request=request,
            )
        except OpenRouterInvalidResponseError as error:
            self._record_generation(
                request=request,
                response_type_name=response_type.__name__,
                raw_content=None,
                outcome="invalid",
                error=str(error),
                usage=error.usage,
                latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
                provider_host=error.provider_host,
            )
            raise
        except OpenRouterProviderError as error:
            self._record_generation(
                request=request,
                response_type_name=response_type.__name__,
                raw_content=None,
                outcome="transport_error",
                error=str(error),
                usage=error.usage,
                latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
                provider_host=error.provider_host,
            )
            raise

        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as err:
            decoded = None
            try:
                stripped = content.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    decoded, _ = json.JSONDecoder().raw_decode(stripped)
            except Exception:
                decoded = None

            if decoded is None:
                self._capture_invalid_completion(
                    body=body,
                    content=content,
                    failure_kind="json_decode",
                    request=request,
                    response_type=response_type,
                    usage=usage,
                )
                self._record_generation(
                    request=request,
                    response_type_name=response_type.__name__,
                    raw_content=content,
                    outcome="invalid",
                    error=f"{response_type.__name__}: completion content is not JSON",
                    usage=usage,
                    latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
                    provider_host=provider_host,
                )
                raise OpenRouterInvalidResponseError(
                    f"{response_type.__name__}: completion content is not JSON"
                    " ("
                    f"{_invalid_completion_diagnosis(body=body, content=content, request=request, usage=usage)}"
                    ")",
                    usage=usage,
                    provider_host=provider_host,
                ) from err
        try:
            output = response_type.model_validate(decoded)
        except ValidationError as error:
            self._capture_invalid_completion(
                body=body,
                content=content,
                failure_kind="schema_validation",
                request=request,
                response_type=response_type,
                usage=usage,
            )
            self._record_generation(
                request=request,
                response_type_name=response_type.__name__,
                raw_content=content,
                outcome="invalid",
                error=(
                    f"completion body failed {response_type.__name__} validation"
                    f" ({_validation_error_names(error=error)})"
                ),
                usage=usage,
                latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
                provider_host=provider_host,
            )
            raise OpenRouterInvalidResponseError(
                f"completion body failed {response_type.__name__} validation"
                " ("
                f"{_invalid_completion_diagnosis(body=body, content=content, request=request, usage=usage)}"
                f"; {_validation_error_names(error=error)}"
                ")",
                usage=usage,
                provider_host=provider_host,
            ) from None
        self._record_generation(
            request=request,
            response_type_name=response_type.__name__,
            raw_content=content,
            outcome="succeeded",
            error=None,
            usage=usage,
            latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
            provider_host=provider_host,
        )
        return GeneratedResponse(output=output, usage=usage)

    def _record_generation(
        self,
        *,
        request: ModelRequest,
        response_type_name: str,
        raw_content: str | None,
        outcome: GenerationOutcome,
        error: str | None,
        usage: ProviderCallUsage | None,
        latency_ms: int,
        provider_host: str | None = None,
    ) -> None:
        """Report one generation to the opt-in recorder, if any is bound.

        A failing recorder must never turn a good generation into an
        exception, nor replace the provider error the ledger needs.
        ``provider_host`` names the serving host when known (rotated attempt
        or resolved response); ``None`` records no host rather than a guess.
        """
        if self._recorder is None:
            return
        try:
            self._recorder.record(
                record=GenerationRecord(
                    provider="openrouter",
                    requested_model=request.model,
                    resolved_model=(usage.model_name if usage is not None else None),
                    response_type_name=response_type_name,
                    prompt=request.prompt,
                    raw_content=raw_content,
                    outcome=outcome,
                    error=error,
                    usage=usage,
                    latency_ms=latency_ms,
                    run_tag="",
                    provider_host=provider_host,
                )
            )
        except Exception as emit_error:
            _logger.warning("openrouter generation record dropped: %s", emit_error)

    def _capture_invalid_completion(
        self,
        *,
        body: dict[str, Any],
        content: str,
        failure_kind: str,
        request: ModelRequest,
        response_type: type[ResponseT],
        usage: ProviderCallUsage,
    ) -> None:
        """Persist one raw invalid completion only when explicitly enabled."""
        capture_dir = self._settings.invalid_completion_capture_dir
        if capture_dir is None:
            return
        captured_at_ns = time.time_ns()
        digest = _content_sha256(content=content)
        artifact = {
            "captured_at_unix_ns": captured_at_ns,
            "failure_kind": failure_kind,
            "response_type": response_type.__name__,
            "requested_model": request.model,
            "resolved_model": usage.model_name,
            "finish_reason": _choice_value(body=body, key="finish_reason"),
            "native_finish_reason": _choice_value(
                body=body, key="native_finish_reason"
            ),
            "tokens_in": usage.tokens_in,
            "tokens_out": usage.tokens_out,
            "cost_usd": str(usage.cost_usd),
            "content_length": len(content),
            "content_sha256": digest,
            "content": content,
        }
        target: Path | None = None
        created = False
        try:
            capture_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            target = capture_dir / (
                f"{captured_at_ns}-{os.getpid()}-{digest[:12]}.json"
            )
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            created = True
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(artifact, stream, ensure_ascii=True, indent=2)
                stream.write("\n")
        except (OSError, TypeError, ValueError):
            if created and target is not None:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
            _logger.warning("could not capture invalid OpenRouter completion")

    def _reasoning_effort_for(self, *, request: ModelRequest) -> ReasoningEffort | None:
        """Resolve explicit request effort before deployment-level defaults.

        An explicitly supplied ``None`` means omit the provider field. Requests
        from existing engine seats do not set the field, so they retain the
        per-model-map and global-setting behavior unchanged.
        """
        if "reasoning_effort" in request.model_fields_set:
            return request.reasoning_effort
        mapped = self._settings.reasoning_effort_map
        if mapped is not None and request.model in mapped:
            return mapped[request.model]
        return self._settings.reasoning_effort

    def _completion_text(
        self,
        *,
        payload: dict[str, object],
        response_type: type[ResponseT],
        started_ns: int,
        request: ModelRequest,
    ) -> tuple[str, ProviderCallUsage, dict[str, Any], str | None]:
        """Post, rotate past overloaded providers, and return usable completion text.

        Without configured chat slugs this is one logical call: usage accounting
        stays one-to-one and 429s draw from the dedicated throttle budget. With
        slugs, each overloaded host is retired after one attempt and the call
        advances to the next survivor; every absorbed 429 is recorded with
        ``outcome="transport_error"`` so throttles stay visible as attempts,
        never as gaps. Retrying a terminal failure remains the work ledger's
        job. Returns the serving host alongside the parsed triple; ``None``
        when the host could not be determined.
        """
        slugs = self._chat_rotation_slugs()
        if not slugs:
            body = self._post(path="/chat/completions", payload=payload)
            usage = self._completion_usage(body=body, started_ns=started_ns)
            content = _completion_content(body=body)
            if content is None:
                raise OpenRouterInvalidResponseError(
                    f"{response_type.__name__}: provider returned no completion"
                    f" content ({_completion_diagnosis(body=body)})",
                    usage=usage,
                    provider_host=self._resolve_provider_host(
                        body=body, targeted_slug=None
                    ),
                )
            return (
                content,
                usage,
                body,
                self._resolve_provider_host(body=body, targeted_slug=None),
            )
        return self._completion_text_rotating(
            payload=payload,
            response_type=response_type,
            started_ns=started_ns,
            request=request,
            slugs=slugs,
        )

    def _completion_text_rotating(
        self,
        *,
        payload: dict[str, object],
        response_type: type[ResponseT],
        started_ns: int,
        request: ModelRequest,
        slugs: tuple[str, ...],
    ) -> tuple[str, ProviderCallUsage, dict[str, Any], str | None]:
        """Advance past overloaded chat providers, retrying the last survivor.

        Distinct slugs are tried at most once each; when no survivors remain,
        the last slug is retried in place until the throttle budget runs out
        (preserving today's single-slug behavior). The terminal failure is
        never recorded here and never slept on: it raises with its host
        attached so ``generate()`` records it exactly once.
        """
        settings = self._settings
        remaining = list(slugs)
        throttle_used = 0
        budget402_used = 0
        last_error = "no chat providers configured"
        last_slug: str | None = None
        while remaining:
            slug = remaining[0]
            last_slug = slug
            rotation_payload = self._chat_provider_payload_for(slugs=tuple(remaining))
            assert rotation_payload is not None  # slugs non-empty by loop guard
            payload["provider"] = rotation_payload
            attempt_start_ns = time.monotonic_ns()
            response = self._post_once(path="/chat/completions", payload=payload)
            attempt_ms = (time.monotonic_ns() - attempt_start_ns) // 1_000_000
            if response.status_code == 402:
                retry_after = _in_flight_budget_retry_after(response=response)
                if (
                    retry_after is not None
                    and budget402_used < _IN_FLIGHT_BUDGET_RETRIES
                ):
                    _logger.warning(
                        "OpenRouter in-flight budget exhausted; retrying after %.1fs",
                        retry_after,
                    )
                    time.sleep(retry_after)
                    budget402_used += 1
                    continue
                raise OpenRouterProviderError(
                    f"OpenRouter /chat/completions returned 402: {response.text[:500]}",
                    provider_host=slug,
                )
            if response.status_code == 429:
                last_error = (
                    f"OpenRouter /chat/completions returned 429: {response.text[:500]}"
                )
                if throttle_used >= settings.chat_throttle_retries:
                    raise OpenRouterProviderError(last_error, provider_host=slug)
                self._record_generation(
                    request=request,
                    response_type_name=response_type.__name__,
                    raw_content=None,
                    outcome="transport_error",
                    error=last_error,
                    usage=None,
                    latency_ms=attempt_ms,
                    provider_host=slug,
                )
                wait_s = _throttle_wait_s(
                    throttle_used=throttle_used,
                    response=response,
                    cap_s=settings.chat_upstream_overload_max_retry_after_s,
                )
                if len(remaining) > 1:
                    _logger.warning(
                        "OpenRouter %s overloaded (429); rotating after %.1fs",
                        slug,
                        wait_s,
                    )
                    time.sleep(wait_s)
                    remaining.pop(0)
                else:
                    _logger.warning(
                        "OpenRouter %s overloaded (429); retrying last "
                        "survivor after %.1fs",
                        slug,
                        wait_s,
                    )
                    time.sleep(wait_s)
                throttle_used += 1
                continue
            if response.status_code >= 400:
                raise OpenRouterProviderError(
                    f"OpenRouter /chat/completions returned {response.status_code}: "
                    f"{response.text[:500]}",
                    provider_host=slug,
                )
            body = response.json()
            usage = self._completion_usage(body=body, started_ns=started_ns)
            content = _completion_content(body=body)
            if content is None:
                raise OpenRouterInvalidResponseError(
                    f"{response_type.__name__}: provider returned no completion"
                    f" content ({_completion_diagnosis(body=body)})",
                    usage=usage,
                    provider_host=self._resolve_provider_host(
                        body=body, targeted_slug=slug
                    ),
                )
            return (
                content,
                usage,
                body,
                self._resolve_provider_host(body=body, targeted_slug=slug),
            )
        raise OpenRouterProviderError(last_error, provider_host=last_slug)

    def _resolve_provider_host(
        self, *, body: dict[str, Any], targeted_slug: str | None
    ) -> str | None:
        """Identify the serving host: response field, lookup, targeted slug.

        The response-body ``provider`` field wins when present. Otherwise a
        generation lookup by response id returns the authoritative
        ``provider_name`` — attempted only when a recorder is bound, so
        production calls pay no extra request. The targeted rotation slug is
        the last resort (a hint under ``allow_fallbacks``, not proof).
        """
        provider = body.get("provider")
        if isinstance(provider, str) and provider:
            return provider
        if self._recorder is not None:
            generation_id = body.get("id")
            if isinstance(generation_id, str) and generation_id:
                try:
                    metadata = self._get_generation(generation_id=generation_id)
                except OpenRouterProviderError:
                    metadata = None
                if isinstance(metadata, dict):
                    name = metadata.get("provider_name")
                    if isinstance(name, str) and name:
                        return name
                    data = metadata.get("data")
                    if isinstance(data, dict):
                        name = data.get("provider_name")
                        if isinstance(name, str) and name:
                            return name
        return targeted_slug

    def _completion_usage(
        self, *, body: dict[str, Any], started_ns: int
    ) -> ProviderCallUsage:
        """Use inline accounting, or recover it by the existing generation id."""
        return recover_completion_usage(
            body=body, started_ns=started_ns, fetch_generation=self._get_generation
        )

    def _chat_provider_payload(self) -> dict[str, object] | None:
        """Build the chat provider routing from settings, or leave it alone.

        ``chat_provider_order`` names an ordered shortlist with fallbacks;
        ``chat_provider_only`` names approved hosts with no marketplace
        escape; unset keeps automatic routing (and keeps the embedding pin
        from constraining chat, as before). Every emitted dict carries
        ``data_collection: deny``; ``zdr`` adds the zero-retention restriction.
        """
        return self._chat_provider_payload_for(slugs=None)

    def _chat_provider_payload_for(
        self, *, slugs: tuple[str, ...] | None
    ) -> dict[str, object] | None:
        """Build one chat provider routing dict, optionally pruned to survivors.

        ``slugs=None`` uses the configured lists verbatim (the initial
        attempt); a tuple restricts routing to those survivors (one rotation
        step). Returns ``None`` only when nothing is configured and ZDR is off.
        """
        settings = self._settings
        order = settings.chat_provider_order
        only = settings.chat_provider_only
        if slugs is None:
            if order:
                slugs = tuple(order)
            elif only:
                slugs = tuple(only)
            else:
                slugs = ()
        if not slugs and not settings.zdr:
            return None
        payload: dict[str, object] = {"data_collection": "deny"}
        if order:
            payload["order"] = list(slugs)
            payload["allow_fallbacks"] = True
        elif only:
            payload["only"] = list(slugs)
            payload["allow_fallbacks"] = False
        if settings.zdr:
            payload["zdr"] = True
        return payload

    def _chat_rotation_slugs(self) -> tuple[str, ...]:
        """Return the chat slugs eligible for overload rotation, in order."""
        order = self._settings.chat_provider_order
        if order:
            return tuple(order)
        only = self._settings.chat_provider_only
        return tuple(only) if only else ()

    def _embedding_provider_payload(self) -> dict[str, object] | None:
        """Build OpenRouter provider routing for embedding requests.

        ``embedding_provider_order`` prefers named hosts first (price/latency
        shortlist) but keeps ``allow_fallbacks`` on so a single-host 5xx/429 can
        move to the next slug rather than dead-letter the stage. A single
        ``embedding_provider`` remains hard-only (no marketplace escape).
        """
        order = self._settings.embedding_provider_order
        if order:
            return {"order": list(order), "allow_fallbacks": True}
        if self._settings.embedding_provider:
            return {
                "only": [self._settings.embedding_provider],
                "allow_fallbacks": False,
            }
        return None

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """One embeddings call for the caller's batch."""
        started_ns = time.monotonic_ns()
        payload: dict[str, object] = {
            "model": request.model,
            "input": list(request.texts),
        }
        if request.dimensions is not None:
            payload["dimensions"] = request.dimensions
        provider = self._embedding_provider_payload()
        if provider is not None:
            payload["provider"] = provider
        body = self._post(path="/embeddings", payload=payload)
        usage = _usage(
            body=body, latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000
        )
        # Billed from here on, even if the body below proves unusable.
        record_embedding_usage(usage=usage)
        try:
            ordered = sorted(body["data"], key=lambda item: item["index"])
            vectors = tuple(tuple(item["embedding"]) for item in ordered)
        except (KeyError, TypeError, ValueError) as err:
            # Malformed response body: content/shape failure so embed poison-split
            # can isolate a bad batch (not a total-outage retry of every chunk).
            raise OpenRouterInvalidResponseError(
                "unusable embeddings body", usage=usage
            ) from err
        if len(vectors) != len(request.texts):
            raise OpenRouterInvalidResponseError(
                f"embedding count {len(vectors)} != batch size {len(request.texts)}",
                usage=usage,
            )
        if any(len(vector) == 0 for vector in vectors):
            raise OpenRouterInvalidResponseError(
                "provider returned an empty embedding vector", usage=usage
            )
        if request.dimensions is not None and any(
            len(vector) != request.dimensions for vector in vectors
        ):
            raise OpenRouterInvalidResponseError(
                "provider returned an embedding dimension that differs from the request",
                usage=usage,
            )
        return EmbeddingResponse(vectors=vectors, usage=usage)

    def _post(self, *, path: str, payload: dict[str, object]) -> dict[str, Any]:
        """POST one JSON request; non-2xx responses become typed errors.

        Call shape is pinned by adapter tests: unconfigured chat routing and
        embeddings share this historic bound (numerically identical to the
        default chat throttle budget). Rotation uses ``_post_once`` plus the
        dedicated throttle budget instead.
        """
        for attempt in range(_IN_FLIGHT_BUDGET_RETRIES + 1):
            response = self._post_once(path=path, payload=payload)
            retry_after = _in_flight_budget_retry_after(response=response)
            if retry_after is not None and attempt < _IN_FLIGHT_BUDGET_RETRIES:
                _logger.warning(
                    "OpenRouter in-flight budget exhausted; retrying after %.1fs",
                    retry_after,
                )
                time.sleep(retry_after)
                continue
            overload_wait = _upstream_overload_retry_after(
                response=response, attempt=attempt
            )
            if overload_wait is not None and attempt < _IN_FLIGHT_BUDGET_RETRIES:
                _logger.warning(
                    "OpenRouter upstream overloaded (429); retrying after %.1fs",
                    overload_wait,
                )
                time.sleep(overload_wait)
                continue
            if response.status_code >= 400:
                raise OpenRouterProviderError(
                    f"OpenRouter {path} returned {response.status_code}: "
                    f"{response.text[:500]}"
                )
            return response.json()
        raise AssertionError("bounded OpenRouter POST retry loop did not return")

    def _post_once(self, *, path: str, payload: dict[str, object]) -> httpx.Response:
        """POST once and return the raw response for the caller to classify."""
        return self._client.post(path, json=payload)

    def _get_generation(self, *, generation_id: str) -> dict[str, Any]:
        """Fetch metadata for one already-created generation without its content."""
        return read_generation_metadata(
            client=self._client,
            generation_id=generation_id,
            timeout_s=self._settings.timeout_s,
        )


def _in_flight_budget_retry_after(*, response: httpx.Response) -> float | None:
    """Return one bounded wait only for OpenRouter's transient credit reservation."""
    if response.status_code != 402:
        return None
    try:
        body = response.json()
        metadata = body["error"]["metadata"]
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(metadata, dict) or metadata.get("reason") != (
        "in_flight_budget_exhausted"
    ):
        return None
    retry_after: object = response.headers.get("Retry-After")
    provider_headers = metadata.get("headers")
    if retry_after is None and isinstance(provider_headers, dict):
        retry_after = provider_headers.get("Retry-After")
    try:
        parsed = (
            float(retry_after) if isinstance(retry_after, (int, float, str)) else None
        )
    except (TypeError, ValueError):
        parsed = None
    if parsed is None or parsed < 0:
        return _IN_FLIGHT_BUDGET_MAX_RETRY_AFTER_S
    return min(parsed, _IN_FLIGHT_BUDGET_MAX_RETRY_AFTER_S)


def _response_retry_after_s(*, response: httpx.Response) -> float | None:
    """Parse an explicit Retry-After from headers or provider metadata."""
    retry_after: object = response.headers.get("Retry-After")
    try:
        body = response.json()
        metadata = body["error"]["metadata"]
    except (KeyError, TypeError, ValueError):
        metadata = None
    if isinstance(metadata, dict):
        if retry_after is None:
            retry_after = metadata.get("retry_after") or metadata.get("Retry-After")
        provider_headers = metadata.get("headers")
        if retry_after is None and isinstance(provider_headers, dict):
            retry_after = provider_headers.get("Retry-After")
    try:
        parsed = (
            float(retry_after) if isinstance(retry_after, (int, float, str)) else None
        )
    except (TypeError, ValueError):
        return None
    return parsed


def _upstream_overload_retry_after(
    *, response: httpx.Response, attempt: int
) -> float | None:
    """Return one bounded wait for a transient upstream 429 overload.

    Only 429 responses are retried; anything else falls through to the typed
    error. An explicit Retry-After (header or provider metadata) wins, capped
    at the bound; otherwise a small linear backoff keeps a hot shared pool
    from being hammered while the worker's own attempt budget still applies.
    """
    if response.status_code != 429:
        return None
    parsed = _response_retry_after_s(response=response)
    if parsed is None or parsed < 0:
        parsed = min(2.0 * (attempt + 1), _UPSTREAM_OVERLOAD_MAX_RETRY_AFTER_S)
    return min(parsed, _UPSTREAM_OVERLOAD_MAX_RETRY_AFTER_S)


def _throttle_wait_s(
    *, throttle_used: int, response: httpx.Response, cap_s: float
) -> float:
    """Return one bounded, jittered wait for an upstream-overload throttle.

    An explicit non-negative Retry-After wins, clamped to ``cap_s``; otherwise
    exponential backoff with full jitter keeps retries from marching in lockstep
    into the same hot pool.
    """
    parsed = _response_retry_after_s(response=response)
    if parsed is not None and parsed >= 0:
        return min(parsed, cap_s)
    return min(cap_s, random.uniform(0.0, 2.0**throttle_used))


def _strict_json_schema(response_type: type[StructuredResponseModel]) -> dict[str, Any]:
    """Adapt Pydantic defaults to the strict schema subset used by OpenAI routes."""
    schema = response_type.model_json_schema()
    _require_all_object_properties(schema)
    return schema


def _require_all_object_properties(node: object) -> None:
    """Make every declared property required and remove unsupported defaults."""
    if isinstance(node, list):
        for item in node:
            _require_all_object_properties(item)
        return
    if not isinstance(node, dict):
        return

    node.pop("default", None)
    if (
        "$ref" in node
        and set(node).issubset({"$ref", "description", "title"})
        and len(node) > 1
    ):
        # Azure strict output rejects metadata siblings on $ref. A one-branch
        # anyOf preserves the referenced type and the field-specific guidance.
        node["anyOf"] = [{"$ref": node.pop("$ref")}]
    properties = node.get("properties")
    if isinstance(properties, dict):
        node["required"] = list(properties)
        node["additionalProperties"] = False
    elif node.get("type") == "object":
        # A free-form object cannot be expressed under strict mode: compliant
        # providers require every object closed (Azure rejects the request with
        # HTTP 400), and closing an object with no properties would forbid all
        # content. Encode arbitrary payloads as a JSON string field instead.
        raise StrictSchemaError(
            "strict schema contains an open object (no properties); free-form"
            " objects are unrepresentable under strict structured output"
        )
    for value in node.values():
        _require_all_object_properties(value)


def _completion_content(*, body: dict[str, Any]) -> str | None:
    """Return usable completion text, or None when the provider sent none.

    Blank-but-present content counts as none: an empty string is not a partial
    answer, it is the provider declining to answer.
    """
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(content, str) or not content.strip():
        return None
    return content


def _completion_diagnosis(*, body: dict[str, Any]) -> str:
    """Summarise why a completion was unusable, using provider metadata only.

    The previous error said only "unusable completion body", which could not
    distinguish truncation from a refusal from an empty response, so a recurring
    production failure had no diagnosable cause.

    This is deliberately metadata-only. Model output can restate customer
    material, and these strings reach `processing_state.last_error` and the logs,
    so no completion text, prompt, provider error message, or credential is ever
    included -- only flags, lengths, and enumerated reasons.
    """
    parts: list[str] = []
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError):
        return "choices=absent"
    if not isinstance(choice, dict):
        return "choices[0]=malformed"
    finish = choice.get("finish_reason")
    native = choice.get("native_finish_reason")
    parts.append(f"finish_reason={finish!r}")
    if native != finish:
        parts.append(f"native_finish_reason={native!r}")
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if content is None:
            parts.append("content=null")
        else:
            parts.append(f"content=blank(len={len(str(content))})")
        parts.append(f"reasoning_present={bool(message.get('reasoning'))}")
        parts.append(f"refusal_present={bool(message.get('refusal'))}")
    # Only the shape of a provider error: its message can echo the prompt.
    error = body.get("error")
    if isinstance(error, dict):
        parts.append(f"error_code={error.get('code')!r}")
    elif error:
        parts.append("error_present=True")
    parts.append(f"model={body.get('model')!r}")
    return ", ".join(parts)


def _content_fingerprint(*, content: str) -> str:
    """Identify non-JSON content without reproducing it.

    A length and digest let an operator tell "the same refusal every time" from
    "different prose each time" and correlate occurrences across runs, while
    keeping possibly-customer-derived model output out of errors and logs.
    """
    digest = _content_sha256(content=content)[:12]
    return f"len={len(content)}, sha256_12={digest}"


def _content_sha256(*, content: str) -> str:
    """Hash any provider string, including an unpaired Unicode surrogate."""
    return hashlib.sha256(content.encode("utf-8", errors="surrogatepass")).hexdigest()


def _choice_value(*, body: dict[str, Any], key: str) -> object:
    """Read one non-content completion-choice field for safe diagnostics."""
    try:
        choice = body["choices"][0]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(choice, dict):
        return None
    return choice.get(key)


def _validation_error_names(*, error: ValidationError, limit: int = 5) -> str:
    """Name the failing fields of a rejected completion without its text.

    Only each error's location path and enumerated type appear: the offending
    input values and the raw content stay out because model output can restate
    source material and these strings reach run records and logs. At most
    ``limit`` entries are listed so one pathological answer cannot flood a log.
    """
    parts: list[str] = []
    for entry in error.errors()[:limit]:
        raw_loc = entry.get("loc", ())
        loc = ".".join(str(step) for step in raw_loc) if raw_loc else "<root>"
        parts.append(f"{loc}.{entry.get('type', 'unknown')}")
    remaining = error.error_count() - len(parts)
    if remaining > 0:
        parts.append(f"+{remaining} more")
    return f"validation_errors=[{', '.join(parts)}]"


def _invalid_completion_diagnosis(
    *,
    body: dict[str, Any],
    content: str,
    request: ModelRequest,
    usage: ProviderCallUsage,
) -> str:
    """Describe malformed content without copying source-derived text to logs."""
    return ", ".join(
        (
            _content_fingerprint(content=content),
            f"finish_reason={_safe_finish_reason(body=body, key='finish_reason')!r}",
            "native_finish_reason="
            f"{_safe_finish_reason(body=body, key='native_finish_reason')!r}",
            f"completion_tokens={usage.tokens_out}",
            f"requested_model={request.model!r}",
        )
    )


def _safe_finish_reason(*, body: dict[str, Any], key: str) -> str | None:
    """Return a known finish reason without logging arbitrary provider text."""
    value = _choice_value(body=body, key=key)
    if value is None:
        return None
    if isinstance(value, str) and value in _SAFE_FINISH_REASONS:
        return value
    return "unexpected"


class GenerationMetadataFetcher(Protocol):
    """Lookup OpenRouter generation metadata without creating a new generation."""

    def __call__(self, *, generation_id: str) -> dict[str, Any]:
        """Return the ``/generation`` JSON body for one existing generation id."""
        ...


def recover_completion_usage(
    *,
    body: dict[str, Any],
    started_ns: int,
    fetch_generation: GenerationMetadataFetcher,
) -> ProviderCallUsage:
    """Use inline accounting, or recover it by the existing generation id.

    OpenRouter documents inline usage on every non-streaming response, but
    also exposes the same accounting asynchronously by generation id. The
    metadata fallback never creates another paid generation. It remains
    fail-closed when the response has no id or metadata stays unavailable.
    """
    try:
        return _usage(
            body=body,
            latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
            require_output_tokens=True,
        )
    except ProviderAccountingError as inline_error:
        generation_id = body.get("id")
        if not isinstance(generation_id, str) or not generation_id.strip():
            raise inline_error
        last_error: Exception = inline_error

    for delay_s in _GENERATION_USAGE_POLL_DELAYS_S:
        if delay_s:
            time.sleep(delay_s)
        try:
            metadata = fetch_generation(generation_id=generation_id)
            return _generation_usage(
                body=metadata,
                fallback_model=body.get("model"),
                generation_id=generation_id,
                latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
            )
        except (
            httpx.HTTPError,
            OpenRouterProviderError,
            ProviderAccountingError,
        ) as error:
            last_error = error
    raise ProviderAccountingError(
        "OpenRouter response carries unusable usage accounting and generation"
        " metadata did not recover it"
    ) from last_error


def read_generation_metadata(
    *, client: httpx.Client, generation_id: str, timeout_s: float
) -> dict[str, Any]:
    """Fetch metadata for one already-created generation without its content."""
    response = client.get(
        "/generation",
        params={"id": generation_id},
        timeout=min(timeout_s, _GENERATION_USAGE_TIMEOUT_S),
    )
    if response.status_code >= 400:
        raise OpenRouterProviderError(
            f"OpenRouter /generation returned {response.status_code}"
        )
    try:
        body = response.json()
    except ValueError as error:
        raise OpenRouterProviderError(
            "OpenRouter /generation returned non-JSON metadata"
        ) from error
    if not isinstance(body, dict):
        raise OpenRouterProviderError(
            "OpenRouter /generation returned malformed metadata"
        )
    return body


def _usage(
    *, body: dict[str, Any], latency_ms: int, require_output_tokens: bool = False
) -> ProviderCallUsage:
    """Validate accounting; chat requires output tokens while embeddings do not."""
    raw = body.get("usage")
    if not isinstance(raw, dict):
        raise ProviderAccountingError("OpenRouter response carries no usage accounting")
    model_name = body.get("model")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ProviderAccountingError(
            "OpenRouter response carries no resolved model identity"
        )
    if require_output_tokens and "completion_tokens" not in raw:
        raise ProviderAccountingError(
            "OpenRouter response carries incomplete completion usage accounting"
        )
    try:
        return ProviderCallUsage(
            model_name=model_name,
            tokens_in=raw["prompt_tokens"],
            tokens_out=raw.get("completion_tokens", 0),
            cost_usd=Decimal(str(raw["cost"])),
            latency_ms=latency_ms,
        )
    except (InvalidOperation, KeyError, TypeError, ValueError) as err:
        raise ProviderAccountingError(
            "OpenRouter response carries invalid usage accounting"
        ) from err


def _generation_usage(
    *, body: dict[str, Any], fallback_model: object, generation_id: str, latency_ms: int
) -> ProviderCallUsage:
    """Normalize OpenRouter generation metadata into the ordinary usage proof."""
    data = body.get("data")
    if not isinstance(data, dict):
        raise ProviderAccountingError(
            "OpenRouter generation metadata carries no usage accounting"
        )
    if data.get("id") != generation_id:
        raise ProviderAccountingError(
            "OpenRouter generation metadata does not match the requested generation"
        )
    model_name = data.get("model") or fallback_model
    return _usage(
        body={
            "model": model_name,
            "usage": {
                "prompt_tokens": data.get("tokens_prompt"),
                "completion_tokens": data.get("tokens_completion"),
                "cost": data.get("total_cost"),
            },
        },
        latency_ms=latency_ms,
        require_output_tokens=True,
    )
