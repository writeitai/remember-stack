"""ChatGPT-subscription generation through the official local Codex runtime.

This benchmark-only adapter deliberately delegates authentication to Codex.
It never reads ``~/.codex/auth.json``, never handles a bearer token, and never
accepts an API key. Every generation uses a fresh ephemeral, read-only Codex
thread with approvals denied and a turn-scoped JSON Schema.
"""

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import cast
from typing import Protocol
from typing import TYPE_CHECKING
from typing import TypeVar

from pydantic import ValidationError

from rememberstack.adapters.openrouter import _strict_json_schema
from rememberstack.model import EmbeddingRequest
from rememberstack.model import EmbeddingResponse
from rememberstack.model import GeneratedResponse
from rememberstack.model import ModelRequest
from rememberstack.model import ProviderCallError
from rememberstack.model import ProviderCallUsage
from rememberstack.model import ProviderInvalidResponseError
from rememberstack.model import StructuredResponseModel

if TYPE_CHECKING:
    from openai_codex.models import JsonObject

ResponseT = TypeVar("ResponseT", bound=StructuredResponseModel)

_ALLOWED_ITEM_TYPES = frozenset(
    {
        "AgentMessageThreadItem",
        "PlanThreadItem",
        "ReasoningThreadItem",
        "UserMessageThreadItem",
    }
)
_RUNTIME_RESULT_FIELDS = frozenset(
    {"aggregatedOutput", "contentItems", "output", "result", "results"}
)


class CodexSubscriptionProviderError(ProviderCallError):
    """The local Codex runtime could not complete an allowed generation."""


class CodexSubscriptionAccessError(CodexSubscriptionProviderError):
    """Codex is not authenticated with an eligible ChatGPT subscription."""


@dataclass(frozen=True)
class _CodexTurn:
    """Provider-neutral facts returned by one isolated Codex turn."""

    status: str
    error_message: str | None
    final_response: str | None
    tokens_in: int | None
    tokens_out: int | None
    item_types: tuple[str, ...]
    runtime_actions: tuple[dict[str, object], ...]


class TurnRunner(Protocol):
    """Execute one Codex turn without exposing SDK types to the provider."""

    def __call__(
        self, *, request: ModelRequest, output_schema: dict[str, object]
    ) -> _CodexTurn:
        """Return the isolated turn's response and accounting facts."""
        ...


class CodexSubscriptionModelProvider:
    """Bind generation to the current machine's Codex ChatGPT login.

    The subscription does not report a per-call USD charge, so successful calls
    record ``cost_usd=0`` while retaining token counts and wall-clock latency.
    That zero is an accounting basis, not a claim that the subscription seat is
    free. LoCoMo call ceilings remain the quota guard.
    """

    def __init__(
        self,
        *,
        turn_runner: TurnRunner | None = None,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        audit_path: Path | None = None,
        audit_stage: str = "generation",
    ) -> None:
        """Create an adapter with injectable turn and clock seams for tests."""
        self._turn_runner = turn_runner or _run_codex_turn
        self._monotonic_ns = monotonic_ns
        self._audit_path = audit_path
        self._audit_stage = audit_stage

    def generate(
        self, *, request: ModelRequest, response_type: type[ResponseT]
    ) -> GeneratedResponse[ResponseT]:
        """Run one schema-constrained ephemeral Codex turn and validate it."""
        if request.temperature is not None:
            raise CodexSubscriptionProviderError(
                "Codex app-server does not expose temperature; the protocol must pin "
                "temperature to null"
            )
        if request.reasoning_effort is None:
            raise CodexSubscriptionProviderError(
                "Codex subscription generation requires an explicit reasoning effort"
            )

        started = self._monotonic_ns()
        try:
            turn = self._turn_runner(
                request=request, output_schema=_strict_json_schema(response_type)
            )
        except CodexSubscriptionProviderError:
            raise
        except Exception as error:
            raise CodexSubscriptionProviderError(
                f"Codex subscription generation failed: {error}"
            ) from error
        latency_ms = max(0, (self._monotonic_ns() - started) // 1_000_000)
        self._record_runtime_audit(request=request, turn=turn, latency_ms=latency_ms)
        if turn.status != "completed":
            detail = turn.error_message or f"turn ended with status {turn.status!r}"
            usage = _optional_usage(
                turn=turn, model=request.model, latency_ms=latency_ms
            )
            raise CodexSubscriptionProviderError(
                f"Codex subscription generation failed: {detail}", usage=usage
            )
        usage = _usage(turn=turn, model=request.model, latency_ms=latency_ms)
        disallowed = tuple(
            item_type
            for item_type in turn.item_types
            if item_type not in _ALLOWED_ITEM_TYPES
        )
        if disallowed:
            raise CodexSubscriptionProviderError(
                "Codex subscription generation attempted a disallowed agent action: "
                + ", ".join(disallowed),
                usage=usage,
            )
        if turn.final_response is None:
            raise ProviderInvalidResponseError(
                "Codex subscription generation returned no final response", usage=usage
            )
        try:
            output = response_type.model_validate_json(turn.final_response)
        except ValidationError as error:
            raise ProviderInvalidResponseError(
                f"Codex subscription response did not match the requested schema: {error}",
                usage=usage,
            ) from error
        return GeneratedResponse(output=output, usage=usage)

    def _record_runtime_audit(
        self, *, request: ModelRequest, turn: _CodexTurn, latency_ms: int
    ) -> None:
        """Append one complete Codex item summary before accepting its output."""
        if self._audit_path is None:
            return
        record = {
            "schema": "CodexRuntimeAudit/v1",
            "stage": self._audit_stage,
            "model": request.model,
            "reasoning_effort": request.reasoning_effort,
            "prompt_sha256": hashlib.sha256(request.prompt.encode("utf-8")).hexdigest(),
            "status": turn.status,
            "error_message": turn.error_message,
            "tokens_in": turn.tokens_in,
            "tokens_out": turn.tokens_out,
            "latency_ms": latency_ms,
            "item_types": list(turn.item_types),
            "runtime_actions": [
                _without_runtime_results(value=action)
                for action in turn.runtime_actions
            ],
        }
        encoded = (
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(
                self._audit_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600
            )
            with os.fdopen(descriptor, "ab") as audit_file:
                audit_file.write(encoded)
        except OSError as error:
            raise CodexSubscriptionProviderError(
                f"could not record Codex runtime audit: {error}",
                usage=_optional_usage(
                    turn=turn, model=request.model, latency_ms=latency_ms
                ),
            ) from error

    def embed(self, *, request: EmbeddingRequest) -> EmbeddingResponse:
        """Reject embeddings: this adapter owns generation seats only."""
        raise CodexSubscriptionProviderError(
            f"Codex subscription adapter cannot embed with {request.model!r}"
        )


def _usage(*, turn: _CodexTurn, model: str, latency_ms: int) -> ProviderCallUsage:
    """Build accounting only when Codex reported both token counters."""
    if turn.tokens_in is None or turn.tokens_out is None:
        raise CodexSubscriptionProviderError(
            "Codex subscription response omitted token accounting"
        )
    return ProviderCallUsage(
        model_name=model,
        tokens_in=turn.tokens_in,
        tokens_out=turn.tokens_out,
        cost_usd=Decimal(0),
        latency_ms=latency_ms,
    )


def _optional_usage(
    *, turn: _CodexTurn, model: str, latency_ms: int
) -> ProviderCallUsage | None:
    """Return failed-turn accounting when Codex supplied both counters."""
    if turn.tokens_in is None or turn.tokens_out is None:
        return None
    return _usage(turn=turn, model=model, latency_ms=latency_ms)


def _run_codex_turn(
    *, request: ModelRequest, output_schema: dict[str, object]
) -> _CodexTurn:
    """Invoke the official SDK without exposing its authentication material."""
    try:
        from openai_codex import ApprovalMode
        from openai_codex import Codex
        from openai_codex import CodexConfig
        from openai_codex import Sandbox
        from openai_codex.generated.v2_all import ReasoningEffort
    except ImportError as error:
        raise CodexSubscriptionProviderError(
            "Codex subscription generation requires the benchmark extra: "
            "uv sync --extra benchmark"
        ) from error

    try:
        effort = ReasoningEffort(request.reasoning_effort)
    except ValueError as error:
        raise CodexSubscriptionProviderError(
            f"Codex does not support reasoning effort {request.reasoning_effort!r}"
        ) from error

    with tempfile.TemporaryDirectory(prefix="remember-locomo-codex-") as scratch:
        with Codex(
            CodexConfig(
                cwd=scratch,
                client_name="rememberstack_locomo",
                client_title="RememberStack LoCoMo",
            )
        ) as codex:
            account = codex.account(refresh_token=False).account
            account_type = (
                None if account is None else account.model_dump(mode="json").get("type")
            )
            if account_type != "chatgpt":
                raise CodexSubscriptionAccessError(
                    "Codex must be logged in with ChatGPT; run `codex login`"
                )
            thread = codex.thread_start(
                approval_mode=ApprovalMode.deny_all,
                cwd=scratch,
                ephemeral=True,
                model=request.model,
                sandbox=Sandbox.read_only,
                service_name="rememberstack-locomo",
            )
            result = thread.run(
                request.prompt,
                effort=effort,
                output_schema=cast("JsonObject", output_schema),
                # The pinned SDK serializes this turn policy as readOnly with
                # networkAccess=false; the thread-level preset matches it.
                sandbox=Sandbox.read_only,
            )

    total_usage = None if result.usage is None else result.usage.total
    roots = tuple(item.root for item in result.items)
    return _CodexTurn(
        status=result.status.value,
        error_message=None if result.error is None else result.error.message,
        final_response=result.final_response,
        tokens_in=None if total_usage is None else total_usage.input_tokens,
        tokens_out=None if total_usage is None else total_usage.output_tokens,
        item_types=tuple(type(root).__name__ for root in roots),
        runtime_actions=tuple(
            _runtime_action(item=root)
            for root in roots
            if type(root).__name__ not in _ALLOWED_ITEM_TYPES
        ),
    )


def _runtime_action(*, item: object) -> dict[str, object]:
    """Serialize one action request while excluding returned content."""
    item_type = type(item).__name__
    serializer = getattr(item, "model_dump", None)
    if not callable(serializer):
        return {"item_type": item_type, "payload": {}}
    dumped = serializer(mode="json", by_alias=True, exclude_none=True)
    payload = dumped if isinstance(dumped, dict) else {"value": dumped}
    return {"item_type": item_type, "payload": _without_runtime_results(value=payload)}


def _without_runtime_results(*, value: object) -> object:
    """Remove tool-returned bodies that may contain source or secret material."""
    if isinstance(value, dict):
        return {
            key: _without_runtime_results(value=nested)
            for key, nested in value.items()
            if isinstance(key, str) and key not in _RUNTIME_RESULT_FIELDS
        }
    if isinstance(value, list):
        return [_without_runtime_results(value=item) for item in value]
    return value
