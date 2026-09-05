"""The Vertex AI managed open-model generation adapter: keyless, priced by table.

Google's managed open models ("Model as a Service", MaaS) on the Gemini
Enterprise Agent Platform (formerly Vertex AI) expose an OpenAI-compatible
chat-completions route. This adapter binds ``ModelProviderPort.generate`` to
that route for one project and location. It deliberately does not embed:
embeddings stay with the provider that owns the deployment's vector space, so
swapping the generation model never silently changes stored vectors.

Two properties differ from the OpenRouter binding and shape this module:

* **No Google API or service-account key.** Authentication is a short-lived
  OAuth access token from Google Application Default Credentials (ADC). On the
  benchmark hosts ADC is X.509 Workload Identity Federation: the host protects
  a client certificate and private key, and the provider can revoke that narrow
  identity independently of everything else.
* **No inline charge.** The response reports token counts but never a dollar
  amount, so ``cost_usd`` is *computed* from a pinned per-model price table.
  The adapter refuses to call a model whose price is not pinned, and charges
  every prompt token at the full input rate (cached-token discounts are
  ignored) so the ledger can only over-report, never under-report.

Endpoint shape (retrieved 2026-09-04 from
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/call-open-model-apis):
``POST https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{LOCATION}/endpoints/openapi/chat/completions``;
the ``global`` location uses the bare ``aiplatform.googleapis.com`` host.
"""

from collections.abc import Callable
from collections.abc import Sequence
from decimal import Decimal
import hashlib
import threading
import time
from typing import Any
from typing import Final
from typing import TypeVar

import httpx
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict

from rememberstack.adapters.openrouter import _completion_content
from rememberstack.adapters.openrouter import _strict_json_schema
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

GEMMA_4_26B_A4B_IT_MAAS: Final = "google/gemma-4-26b-a4b-it-maas"
"""Request and response model name of Gemma 4 26B-A4B IT as a managed model.

Model card (retrieved 2026-09-04):
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/maas/google/gemma-4-26b-a4b-it
lists the id ``gemma-4-26b-a4b-it-maas``, structured output and function
calling as supported, a 262,144-token context, a 128,000-token output maximum,
``global`` availability with ``us`` multi-region processing, and launch stage
Experimental. Google currently contradicts itself about thinking support; this
adapter deliberately does not send provider-specific thinking controls.
"""
CLOUD_PLATFORM_SCOPE: Final = "https://www.googleapis.com/auth/cloud-platform"
_DEFAULT_MAX_COMPLETION_TOKENS: Final[int] = 4_096
_DEFAULT_THROTTLE_RETRY_DELAYS_S: Final[tuple[float, ...]] = (1.0, 2.0, 4.0, 8.0, 16.0)
_SAFE_FINISH_REASONS: Final[frozenset[str]] = frozenset(
    ("stop", "length", "content_filter", "tool_calls", "error", "cancelled")
)
_ONE_MILLION: Final = Decimal(1_000_000)

AccessTokenSource = Callable[[], str]
"""Return a currently valid bearer token; called before every request."""


class VertexModelPrice(BaseModel):
    """List price of one managed model in USD per million tokens."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_usd_per_million: Decimal = Field(ge=0)
    output_usd_per_million: Decimal = Field(ge=0)


DEFAULT_PRICE_TABLE_USD_PER_MILLION: Final[dict[str, VertexModelPrice]] = {
    GEMMA_4_26B_A4B_IT_MAAS: VertexModelPrice(
        input_usd_per_million=Decimal("0.15"), output_usd_per_million=Decimal("0.60")
    )
}
"""Pinned list prices (retrieved 2026-09-04 from
https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing:
Gemma 4 26B input $0.15, output $0.60, cache hit $0.015 per 1M tokens).

The cache-hit rate is deliberately not modelled: the adapter bills every
prompt token at the full input rate, which is conservative by construction.
"""


class VertexSettings(BaseSettings):
    """The Vertex binding: which project and location serve generations."""

    model_config = SettingsConfigDict(env_prefix="REMEMBERSTACK_VERTEX_")

    project_id: str = Field(min_length=1)
    """Google Cloud project that holds the model entitlement and is billed."""
    location: str = Field(default="global", min_length=1)
    """``global`` for the global endpoint; otherwise a regional location id."""
    timeout_s: float = Field(default=120.0, gt=0)
    max_completion_tokens: int = Field(default=_DEFAULT_MAX_COMPLETION_TOKENS, ge=1)
    """Output budget sent as ``max_tokens``; kept small because every
    structured benchmark step is short and the cap bounds per-call spend."""
    price_table_usd_per_million: dict[str, VertexModelPrice] = Field(
        default_factory=lambda: dict(DEFAULT_PRICE_TABLE_USD_PER_MILLION)
    )
    """Per-model prices; a model absent here is refused before any request.

    Env: ``REMEMBERSTACK_VERTEX_PRICE_TABLE_USD_PER_MILLION`` as a JSON object
    of model name to ``{"input_usd_per_million": "0.15",
    "output_usd_per_million": "0.60"}``.
    """
    throttle_retry_delays_s: tuple[float, ...] = _DEFAULT_THROTTLE_RETRY_DELAYS_S
    """Sleeps before re-sending a request the service refused with HTTP 429.

    The Experimental managed model answers "The request queue is full" under
    shared load. A 429 performs no work and reports no usage, so re-sending
    cannot bill twice; that is why this one status is retried while every
    other failure keeps the one-call-per-logical-call contract. An empty
    tuple disables retries.
    """


class VertexRequestError(ValueError):
    """A request cannot be sent as configured (unpriced model, unsupported field).

    This is a configuration or programmer error caught before any HTTP
    request and before any spend, so it is not a ``ProviderCallError``.
    """


class VertexProviderError(ProviderCallError):
    """Vertex returned an error or an unusable response body."""


class VertexAccessError(VertexProviderError):
    """The call was refused for identity or entitlement reasons (401/403).

    Distinguished from ordinary provider errors because it is what an operator
    sees after the spend guardian revokes access, disables the API, or
    unlinks billing: a run must stop, not record one failed item per call.
    """


class VertexInvalidResponseError(VertexProviderError, ProviderInvalidResponseError):
    """Vertex completed a generation without a schema-valid output."""


def endpoint_base_url(*, project_id: str, location: str) -> str:
    """Return the OpenAI-compatible base URL for one project and location."""
    host = (
        "aiplatform.googleapis.com"
        if location == "global"
        else f"{location}-aiplatform.googleapis.com"
    )
    return f"https://{host}/v1/projects/{project_id}/locations/{location}/endpoints/openapi"


def application_default_access_token_source(
    *, scopes: Sequence[str] = (CLOUD_PLATFORM_SCOPE,)
) -> AccessTokenSource:
    """Mint bearer tokens from Google ADC, refreshing only when expired.

    ADC resolves ``GOOGLE_APPLICATION_CREDENTIALS``; on the benchmark hosts
    that is an external-account file whose subject token is an X.509 client
    certificate exchanged over mTLS with Google STS. Nothing here ever reads a
    service-account key. ``google-auth`` is imported lazily so the core
    package keeps its small dependency set; the ``benchmark`` extra provides it.
    """
    try:
        import google.auth
        import google.auth.exceptions
        from google.auth.transport.requests import Request
    except ImportError as error:  # pragma: no cover - depends on the extra
        raise VertexRequestError(
            "google-auth with requests is required for Vertex ADC tokens;"
            " install the benchmark extra"
        ) from error
    try:
        credentials, _project = google.auth.default(scopes=list(scopes))
    except google.auth.exceptions.DefaultCredentialsError as error:
        raise VertexRequestError(
            "no Google Application Default Credentials; export"
            " GOOGLE_APPLICATION_CREDENTIALS to the workload credential file"
        ) from error
    transport = Request()
    lock = threading.Lock()

    def access_token() -> str:
        """Return the cached token, refreshing under a lock when needed."""
        with lock:
            if not credentials.valid:
                try:
                    credentials.refresh(transport)
                except google.auth.exceptions.GoogleAuthError as error:
                    raise VertexAccessError(
                        "Google ADC could not mint an access token:"
                        f" {type(error).__name__}"
                    ) from error
            token = credentials.token
        if not isinstance(token, str) or not token:
            raise VertexAccessError("Google ADC returned no access token")
        return token

    return access_token


class VertexModelProvider:
    """Structured generations over the Vertex OpenAI-compatible route."""

    def __init__(
        self,
        *,
        settings: VertexSettings,
        access_token_source: AccessTokenSource | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind one HTTP client to the project endpoint and a token source.

        ``access_token_source`` defaults to Application Default Credentials;
        tests pass a stub. ``transport`` exists only so tests can intercept
        requests without monkeypatching.
        """
        self._settings = settings
        self._access_token = (
            access_token_source
            if access_token_source is not None
            else application_default_access_token_source()
        )
        self._client = httpx.Client(
            base_url=endpoint_base_url(
                project_id=settings.project_id, location=settings.location
            ),
            timeout=settings.timeout_s,
            transport=transport,
        )

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """One chat completion constrained to the caller's declared JSON schema."""
        price = self._settings.price_table_usd_per_million.get(request.model)
        if price is None:
            raise VertexRequestError(
                f"no pinned price for {request.model!r}; refusing to generate"
                " usage the ledger cannot charge"
            )
        if request.reasoning_effort not in (None, "none"):
            raise VertexRequestError(
                f"reasoning effort {request.reasoning_effort!r} is not supported"
                " by the Vertex adapter; pin 'none' or leave it unset"
            )
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
            "max_tokens": self._settings.max_completion_tokens,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        started_ns = time.monotonic_ns()
        body = self._post(path="/chat/completions", payload=payload)
        usage = _usage(
            body=body,
            price=price,
            latency_ms=(time.monotonic_ns() - started_ns) // 1_000_000,
        )
        content = _completion_content(body=body)
        if content is None:
            raise VertexInvalidResponseError(
                f"{response_type.__name__}: provider returned no completion"
                f" content ({_diagnosis(body=body, content='', usage=usage)})",
                usage=usage,
            )
        try:
            output = response_type.model_validate_json(content)
        except ValidationError:
            raise VertexInvalidResponseError(
                f"completion content failed {response_type.__name__} validation"
                f" ({_diagnosis(body=body, content=content, usage=usage)})",
                usage=usage,
            ) from None
        return GeneratedResponse(output=output, usage=usage)

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Refuse: this adapter serves generation only, by design."""
        raise VertexProviderError(
            f"the Vertex adapter does not embed ({request.model!r}); route"
            " embeddings to the provider that owns the deployment's vector space"
        )

    def _post(self, *, path: str, payload: dict[str, object]) -> dict[str, Any]:
        """POST one JSON request with a fresh bearer token; map HTTP failures.

        Only HTTP 429 is re-sent, after the configured delays, because it is
        the one refusal that provably did no billable work.
        """
        delays = iter(self._settings.throttle_retry_delays_s)
        while True:
            response = self._client.post(
                path,
                json=payload,
                headers={"Authorization": f"Bearer {self._access_token()}"},
            )
            if response.status_code != 429:
                break
            delay_s = next(delays, None)
            if delay_s is None:
                break
            time.sleep(delay_s)
        if response.status_code in (401, 403):
            raise VertexAccessError(
                f"Vertex {path} returned {response.status_code}: {response.text[:500]}"
            )
        if response.status_code >= 400:
            raise VertexProviderError(
                f"Vertex {path} returned {response.status_code}: {response.text[:500]}"
            )
        try:
            body = response.json()
        except ValueError as error:
            raise VertexProviderError(
                f"Vertex {path} returned non-JSON body"
            ) from error
        if not isinstance(body, dict):
            raise VertexProviderError(f"Vertex {path} returned a malformed body")
        return body


def computed_cost_usd(
    *, tokens_in: int, tokens_out: int, price: VertexModelPrice
) -> Decimal:
    """Charge input and output tokens at list price, exactly, in Decimal."""
    return (
        Decimal(tokens_in) * price.input_usd_per_million
        + Decimal(tokens_out) * price.output_usd_per_million
    ) / _ONE_MILLION


def _usage(
    *, body: dict[str, Any], price: VertexModelPrice, latency_ms: int
) -> ProviderCallUsage:
    """Validate token accounting and attach the table-computed charge."""
    raw = body.get("usage")
    if not isinstance(raw, dict):
        raise ProviderAccountingError("Vertex response carries no usage accounting")
    model_name = body.get("model")
    if not isinstance(model_name, str) or not model_name.strip():
        raise ProviderAccountingError(
            "Vertex response carries no resolved model identity"
        )
    tokens_in = _token_count(raw=raw, key="prompt_tokens")
    tokens_out = _token_count(raw=raw, key="completion_tokens")
    return ProviderCallUsage(
        model_name=model_name,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=computed_cost_usd(
            tokens_in=tokens_in, tokens_out=tokens_out, price=price
        ),
        latency_ms=latency_ms,
    )


def _token_count(*, raw: dict[str, Any], key: str) -> int:
    """Read one non-negative integer token count or fail closed."""
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProviderAccountingError(
            f"Vertex response carries an unusable {key} token count"
        )
    return value


def _diagnosis(*, body: dict[str, Any], content: str, usage: ProviderCallUsage) -> str:
    """Describe an unusable completion with metadata only, never its text.

    Model output can restate source material and these strings reach run
    records and logs, so only enumerated reasons, lengths, and a digest appear.
    """
    finish: object = None
    try:
        choice = body["choices"][0]
        if isinstance(choice, dict):
            finish = choice.get("finish_reason")
    except (KeyError, IndexError, TypeError):
        finish = None
    safe_finish = (
        finish if isinstance(finish, str) and finish in _SAFE_FINISH_REASONS else None
    )
    digest = hashlib.sha256(
        content.encode("utf-8", errors="surrogatepass")
    ).hexdigest()[:12]
    return (
        f"len={len(content)}, sha256_12={digest}, finish_reason={safe_finish!r},"
        f" completion_tokens={usage.tokens_out}, model={usage.model_name!r}"
    )
