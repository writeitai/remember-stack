"""The one structured tool error envelope every MCP host reports failures with.

A failed ``tools/call`` is a result with ``isError: true`` and one JSON text
block::

    {"error": {"code": "rate_limited", "status_code": 429, "detail": "…",
               "retryable": true, "agent_action": "…", "retry_after": 3}}

- ``code`` — a stable machine-readable code: the engine's own code when it
  sent one (``relation_not_allowed``, ``rate_limited``, …), else one of the
  codes below.
- ``status_code`` — the engine's HTTP status; ``0`` when no answer arrived;
  ``null`` when there was no HTTP exchange (the host refused the call itself,
  or an in-process engine answered).
- ``detail`` — what went wrong, for the agent to read.
- ``retryable`` / ``agent_action`` — whether a retry can help, and what to do.
- ``reason_code``, ``request_id``, ``retry_after`` — only when known.

Every tool family — writes, assured operations, SQL query tools — and every
host uses this one shape.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Final

from pydantic import ValidationError

from remember.query_sandbox.errors import SandboxRejection

#: Open-query codes for which the same call can succeed later.
_RETRYABLE_QUERY_CODES: Final = frozenset(
    {
        "quota_exceeded",
        "concurrency_exceeded",
        "saved_query_revalidation_pending",
        "statement_timeout",
        "lock_timeout",
        "pg_unavailable",
        "p1_unavailable",
        "graph_unavailable",
        "corpus_body_unavailable",
        "generation_unavailable",
    }
)
_SPEND_CODES: Final = frozenset(
    {
        "spend_safety",
        "reservation_refused",
        "spend_cap",
        "budget_exceeded",
        "spend_reservation_refused",
    }
)
#: ``code:reason`` detail prefixes a deployment may send instead of a code.
_PREFIX_CODES: Final = _SPEND_CODES | {
    "dispatch_refused",
    "dispatch_parked",
    "body_too_large",
    "empty_body",
}


@dataclass(frozen=True, slots=True)
class ToolError:
    """One structured MCP tool error (see the module docstring)."""

    code: str
    detail: str
    status_code: int | None
    retryable: bool
    agent_action: str
    reason_code: str | None = None
    request_id: str | None = None
    retry_after: float | None = None

    def as_dict(self) -> dict[str, object]:
        """The JSON envelope ``{"error": {…}}``, unset optionals omitted."""
        error: dict[str, object] = {
            "code": self.code,
            "status_code": self.status_code,
            "detail": self.detail,
            "retryable": self.retryable,
            "agent_action": self.agent_action,
        }
        if self.reason_code is not None:
            error["reason_code"] = self.reason_code
        if self.request_id is not None:
            error["request_id"] = self.request_id
        if self.retry_after is not None:
            error["retry_after"] = self.retry_after
        return {"error": error}


class ToolArgumentError(Exception):
    """Client-side argument or body resolution failed before a backend call."""

    def __init__(self, *, error: ToolError) -> None:
        super().__init__(error.detail)
        self.error = error


def error_result(error: ToolError) -> dict[str, object]:
    """The MCP ``tools/call`` error result carrying ``error``."""
    return {
        "content": [
            {"type": "text", "text": json.dumps(error.as_dict(), sort_keys=True)}
        ],
        "isError": True,
    }


def invalid_arguments(*, detail: str) -> ToolError:
    """The ``invalid_arguments`` refusal of a call the host did not send."""
    return ToolError(
        code="invalid_arguments",
        detail=detail,
        status_code=None,
        retryable=False,
        agent_action="Fix the tool arguments and retry.",
    )


def map_error(error: BaseException) -> ToolError:
    """Map any failure of a tool call into the envelope.

    - An HTTP-style error (``status_code`` and ``detail`` attributes, such as
      ``MemoryApiError``) keeps the engine's status and, when it sent one,
      its code; ``429`` becomes ``rate_limited`` / ``concurrency_limited``
      with ``retry_after``.
    - ``SandboxRejection`` keeps its open-query code.
    - ``ValidationError`` — the backend answered in an unexpected shape.
    - ``ValueError`` — a typed client-side contract failure.
    - ``ConnectionError`` / ``TimeoutError`` — retryable ``transport_error``.
    - Anything else — non-retryable ``internal_error`` with a generic
      detail (no internals); callers log the traceback.

    Duck-typed on the attributes so an in-process port adapter can raise an
    ordinary exception that maps the same way.
    """
    status_code = getattr(error, "status_code", None)
    detail = getattr(error, "detail", None)
    if isinstance(status_code, int) and detail is not None:
        explicit = getattr(error, "code", None)
        retry_after = getattr(error, "retry_after", None)
        return _map_http_error(
            status_code=status_code,
            detail=str(detail),
            explicit_code=explicit if isinstance(explicit, str) and explicit else None,
            retry_after=float(retry_after)
            if isinstance(retry_after, int | float)
            else None,
        )
    if isinstance(error, SandboxRejection):
        code = error.code.value
        retryable = code in _RETRYABLE_QUERY_CODES
        return ToolError(
            code=code,
            detail=error.message,
            status_code=None,
            retryable=retryable,
            agent_action=(
                "Retry later with back-off."
                if retryable
                else "Read the detail and fix the query or its arguments."
            ),
        )
    if isinstance(error, ValidationError):
        return ToolError(
            code="local_backend_error",
            detail=f"Local backend validation failed: {error}",
            status_code=None,
            retryable=False,
            agent_action=(
                "Report a composition/contract defect; do not retry the same call."
            ),
        )
    if isinstance(error, UnicodeEncodeError):
        return ToolError(
            code="encoding_error",
            detail=f"Body is not encodable as UTF-8: {error}",
            status_code=None,
            retryable=False,
            agent_action=(
                "Remove lone surrogates / invalid code points, or send"
                " content_base64 for binary."
            ),
        )
    if isinstance(error, ValueError):
        return invalid_arguments(detail=str(error) or "Invalid arguments.")
    if isinstance(error, (ConnectionError, TimeoutError)):
        return _transport_error(detail=str(error) or error.__class__.__name__)
    return ToolError(
        code="internal_error",
        # Never the exception text: it may carry internals. Callers log it.
        detail="Unexpected internal failure; the server logged the details.",
        status_code=None,
        retryable=False,
        agent_action=(
            "Unexpected internal failure. Do not busy-retry; report the error"
            " (and any request_id) to an operator or as a product defect."
        ),
    )


def _transport_error(*, detail: str) -> ToolError:
    return ToolError(
        code="transport_error",
        detail=detail,
        status_code=0,
        retryable=True,
        agent_action="Retry with back-off; check the deployment URL and network.",
    )


def _map_http_error(
    *,
    status_code: int,
    detail: str,
    explicit_code: str | None,
    retry_after: float | None,
) -> ToolError:
    """Map an engine (or cloud) HTTP failure to the envelope."""
    code, reason_code = _split_detail_code(detail=detail)
    if explicit_code is not None:
        code = explicit_code
    if status_code == 0:
        return _transport_error(
            detail=detail or "Transport failure talking to the deployment."
        )
    if code in _SPEND_CODES or detail.startswith("spend_safety"):
        return ToolError(
            code="spend_safety",
            detail=detail or "Spend or reservation safety refused this work.",
            status_code=status_code,
            retryable=False,
            agent_action=(
                "Surface the spend/reservation refusal to the user/operator; do"
                " not busy-retry. Adjust budgets or wait for a new reservation."
            ),
            reason_code=reason_code,
        )
    if status_code == 429:
        return ToolError(
            code=code
            if code in ("rate_limited", "concurrency_limited")
            else "rate_limited",
            detail=detail or "Too many requests.",
            status_code=429,
            retryable=True,
            agent_action=(
                "Wait retry_after seconds (if given), then retry; do not retry"
                " sooner. Lower the request rate or run fewer calls at once."
            ),
            retry_after=retry_after,
        )
    if code == "body_too_large" or status_code == 413:
        return ToolError(
            code="body_too_large",
            detail=detail or "Ingest body exceeds the deployment size limit.",
            status_code=status_code,
            retryable=False,
            agent_action="Split or shorten the document; do not retry the same payload.",
            reason_code=reason_code,
        )
    if code == "empty_body":
        return ToolError(
            code="empty_body",
            detail=detail or "Ingest body is empty.",
            status_code=status_code,
            retryable=False,
            agent_action="Provide non-empty path / text / content_base64 content.",
            reason_code=reason_code,
        )
    if code == "dispatch_refused":
        return ToolError(
            code="dispatch_refused",
            detail=detail,
            status_code=status_code,
            retryable=False,
            agent_action=(
                "Surface the reason to the user/operator; do not busy-retry."
                " Typical causes: spend cap, missing policy, halt."
            ),
            reason_code=reason_code,
        )
    if code == "dispatch_parked":
        return ToolError(
            code="dispatch_parked",
            detail=detail,
            status_code=status_code,
            retryable=False,
            agent_action=(
                "Stop automated retries and notify a human; park is policy, not"
                " a transient blip."
            ),
            reason_code=reason_code,
        )
    if status_code == 401:
        return ToolError(
            code="unauthorized",
            detail=detail or "Unauthorized.",
            status_code=401,
            retryable=False,
            agent_action=(
                "The key is missing, expired or revoked: run `remember login`"
                " or replace REMEMBER_API_KEY."
            ),
        )
    if status_code == 403:
        return ToolError(
            code="insufficient_permission",
            detail=detail or "Forbidden.",
            status_code=403,
            retryable=False,
            agent_action=(
                "The key may not do this here: use a key with the needed"
                " permission for this deployment."
            ),
        )
    retryable = status_code >= 500 or code in _RETRYABLE_QUERY_CODES
    if explicit_code is None:
        code = "engine_unavailable" if status_code >= 500 else "engine_client_error"
    return ToolError(
        code=code,
        detail=detail or f"Engine answered HTTP {status_code}.",
        status_code=status_code,
        retryable=retryable,
        agent_action=(
            "Retry with back-off (3–5 attempts, 2s→30s). If still failing,"
            " report an operator outage."
            if retryable
            else "Read the detail; fix the call. Do not retry it unchanged."
        ),
        reason_code=reason_code,
    )


def _split_detail_code(*, detail: str) -> tuple[str, str | None]:
    """Split a ``code:reason`` detail sent in place of a code."""
    head, separator, tail = detail.partition(":")
    if separator and head in _PREFIX_CODES:
        return head, tail or None
    return detail, None
