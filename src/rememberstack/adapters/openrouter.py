"""The OpenRouter model-provider adapter (D63/D70): the shipped default binding."""

from decimal import Decimal
from decimal import InvalidOperation
import hashlib
import json
import time
from typing import Any
from typing import Final
from typing import Literal
from typing import TypeAlias
from typing import TypeVar

import httpx
from pydantic import Field
from pydantic import field_validator
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderAccountingError
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model import StructuredResponseModel

ResponseT = TypeVar("ResponseT", bound=StructuredResponseModel)

ReasoningEffort: TypeAlias = Literal[
    "none", "minimal", "low", "medium", "high", "xhigh", "max"
]
"""Allowed OpenRouter reasoning-effort values (global pin or per-model map)."""

_ALLOWED_REASONING_EFFORTS: Final[frozenset[str]] = frozenset(
    ("none", "minimal", "low", "medium", "high", "xhigh", "max")
)


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
    embedding_provider: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    reasoning_effort_map: dict[str, ReasoningEffort] | None = None
    """Optional per-model effort overrides as a JSON object env var
    (`REMEMBERSTACK_OPENROUTER_REASONING_EFFORT_MAP`, e.g.
    `{"z-ai/glm-4.7-flash":"none","openai/gpt-5.6-luna":"high"}`). A model's
    entry wins over the global `reasoning_effort` for requests to that model;
    absent entries fall back to the global pin (or the model default when the
    global pin is also unset). Values must be one of the allowed effort
    literals."""

    @field_validator("embedding_provider", "reasoning_effort", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> object:
        """Treat Compose's empty optional values as unset."""
        if not isinstance(value, str):
            return value
        return value.strip() or None

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


class OpenRouterInvalidResponseError(
    OpenRouterProviderError, ProviderInvalidResponseError
):
    """OpenRouter completed a generation without a schema-valid output."""


class OpenRouterModelProvider:
    """Structured generations and embeddings over the OpenRouter HTTP API."""

    def __init__(self, *, settings: OpenRouterSettings) -> None:
        """Bind one HTTP client to the configured endpoint and key."""
        self._settings = settings
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
        effort = self._reasoning_effort_for(model=request.model)
        if effort is not None:
            payload["reasoning"] = {"effort": effort}

        content, usage = self._completion_text(
            payload=payload, response_type=response_type, started_ns=started_ns
        )

        try:
            decoded = json.loads(content)
        except json.JSONDecodeError as err:
            raise OpenRouterInvalidResponseError(
                f"{response_type.__name__}: completion content is not JSON"
                f" ({_content_fingerprint(content=content)})",
                usage=usage,
            ) from err
        try:
            output = response_type.model_validate(decoded)
        except ValidationError as error:
            raise OpenRouterInvalidResponseError(
                f"completion body failed {response_type.__name__} validation",
                usage=usage,
            ) from error
        return GeneratedResponse(output=output, usage=usage)

    def _reasoning_effort_for(self, *, model: str) -> ReasoningEffort | None:
        """Resolve effort for one model: per-model map entry, else global pin."""
        mapped = self._settings.reasoning_effort_map
        if mapped is not None and model in mapped:
            return mapped[model]
        return self._settings.reasoning_effort

    def _completion_text(
        self,
        *,
        payload: dict[str, object],
        response_type: type[ResponseT],
        started_ns: int,
    ) -> tuple[str, ProviderCallUsage]:
        """Post once and return usable completion text, or raise saying why.

        One provider call per logical call: usage accounting stays one-to-one and
        a caller cannot be billed twice for work it asked for once. Retrying is
        the work ledger's job, which already grants each item several attempts.
        """
        body = self._post(path="/chat/completions", payload=payload)
        usage = _usage(
            body=body,
            requested_model=str(payload["model"]),
            latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
        )
        content = _completion_content(body=body)
        if content is None:
            raise OpenRouterInvalidResponseError(
                f"{response_type.__name__}: provider returned no completion"
                f" content ({_completion_diagnosis(body=body)})",
                usage=usage,
            )
        return content, usage

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """One embeddings call for the caller's batch."""
        started_ns = time.monotonic_ns()
        payload: dict[str, object] = {
            "model": request.model,
            "input": list(request.texts),
        }
        if self._settings.embedding_provider:
            payload["provider"] = {
                "only": [self._settings.embedding_provider],
                "allow_fallbacks": False,
            }
        body = self._post(path="/embeddings", payload=payload)
        usage = _usage(
            body=body,
            requested_model=request.model,
            latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
        )
        try:
            ordered = sorted(body["data"], key=lambda item: item["index"])
            return EmbeddingResponse(
                vectors=tuple(tuple(item["embedding"]) for item in ordered), usage=usage
            )
        except (KeyError, TypeError, ValueError) as err:
            raise OpenRouterProviderError(
                "unusable embeddings body", usage=usage
            ) from err

    def _post(self, *, path: str, payload: dict[str, object]) -> dict[str, Any]:
        """POST one JSON request; non-2xx responses become typed errors."""
        response = self._client.post(path, json=payload)
        if response.status_code >= 400:
            raise OpenRouterProviderError(
                f"OpenRouter {path} returned {response.status_code}: "
                f"{response.text[:500]}"
            )
        return response.json()


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
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
    return f"len={len(content)}, sha256_12={digest}"


def _usage(
    *, body: dict[str, Any], requested_model: str, latency_ms: int
) -> ProviderCallUsage:
    """Validate OpenRouter accounting; missing usage must not silently disable budgets."""
    raw = body.get("usage")
    if not isinstance(raw, dict):
        raise ProviderAccountingError("OpenRouter response carries no usage accounting")
    model_name = body.get("model", requested_model)
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
