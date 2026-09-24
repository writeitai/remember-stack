"""The structured tool error envelopes every MCP host reports failures with.

Two shapes exist:

- :class:`ToolError`, rendered by :func:`error_result` — the envelope of the
  memory write tools (``ingest``, ``pipeline_readiness``, ``delete_document``):
  a stable ``code``, the HTTP status, whether a retry can help, and what the
  agent should do next.
- :func:`status_error_result` — ``{"error": {"status_code", "code", "detail"}}``
  for a failed engine HTTP call (assured operations and query tools).
"""

from __future__ import annotations

from dataclasses import dataclass
import json

from pydantic import ValidationError


@dataclass(frozen=True, slots=True)
class ToolError:
    """Structured MCP tool error for write/readiness tools."""

    code: str
    message: str
    http_status: int
    retryable: bool
    agent_action: str
    reason_code: str | None = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-serializable envelope fields (omit unset optionals)."""
        payload: dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "http_status": self.http_status,
            "retryable": self.retryable,
            "agent_action": self.agent_action,
        }
        if self.reason_code is not None:
            payload["reason_code"] = self.reason_code
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload


class ToolArgumentError(Exception):
    """Client-side argument or body resolution failed before a backend call."""

    def __init__(self, *, error: ToolError) -> None:
        super().__init__(error.message)
        self.error = error


def map_error(error: BaseException) -> ToolError:
    """Map an SDK/HTTP/backend failure into the structured tool error envelope.

    Failure classes:

    - HTTP/API style (``status_code`` + ``detail``) — engine and cloud wire errors
    - ``spend_safety`` — cloud reservation / spend refusals (not flattened into
      ``engine_client_error``)
    - ``ValidationError`` — local backend/Pydantic contract defects
    - ``ValueError`` — typed client-side contract failures from the SDK
    - transport-ish OS/network errors — retryable ``transport_error``
    - everything else — non-retryable ``internal_error`` (programmer defect /
      unexpected); callers log the full traceback at the MCP boundary

    Accepts ``MemoryApiError``-shaped objects without importing the SDK type, so
    local port adapters can raise ordinary exceptions that still map when they
    carry the same attributes.
    """
    status_code = getattr(error, "status_code", None)
    detail = getattr(error, "detail", None)
    explicit_code = getattr(error, "code", None)
    if isinstance(status_code, int) and isinstance(detail, str):
        return _map_http_style_error(
            status_code=status_code, detail=detail, explicit_code=explicit_code
        )
    if isinstance(status_code, int) and detail is not None:
        return _map_http_style_error(
            status_code=status_code, detail=str(detail), explicit_code=explicit_code
        )
    if isinstance(error, ValidationError):
        return ToolError(
            code="local_backend_error",
            message=f"Local backend validation failed: {error}",
            http_status=500,
            retryable=False,
            agent_action=(
                "Report a composition/contract defect; do not retry the same call."
            ),
        )
    if isinstance(error, UnicodeEncodeError):
        return ToolError(
            code="encoding_error",
            message=f"Body is not encodable as UTF-8: {error}",
            http_status=422,
            retryable=False,
            agent_action=(
                "Remove lone surrogates / invalid code points, or send"
                " content_base64 for binary."
            ),
        )
    if isinstance(error, ValueError):
        return ToolError(
            code="invalid_arguments",
            message=str(error) or "Invalid arguments.",
            http_status=422,
            retryable=False,
            agent_action="Fix the tool arguments and retry.",
        )
    if isinstance(error, (ConnectionError, TimeoutError)):
        return ToolError(
            code="transport_error",
            message=str(error) or error.__class__.__name__,
            http_status=0,
            retryable=True,
            agent_action=(
                "Retry with back-off; check REMEMBER_API_URL, credentials, and"
                " network reachability."
            ),
        )
    return ToolError(
        code="internal_error",
        message=str(error) or error.__class__.__name__,
        http_status=500,
        retryable=False,
        agent_action=(
            "Unexpected internal failure. Do not busy-retry; report the error"
            " (and any request_id) to an operator or as a product defect."
        ),
    )


def _map_http_style_error(
    *, status_code: int, detail: str, explicit_code: object = None
) -> ToolError:
    """Map status + detail string (including cloud prefix codes) to ToolError."""
    code, reason_code = _split_detail_code(detail=detail)
    if isinstance(explicit_code, str) and explicit_code:
        code = explicit_code

    if _is_spend_safety(code=code, detail=detail, reason_code=reason_code):
        return ToolError(
            code="spend_safety",
            message=detail or "Spend or reservation safety refused this write.",
            http_status=status_code if status_code else 403,
            retryable=False,
            agent_action=(
                "Surface the spend/reservation refusal to the user/operator; do"
                " not busy-retry. Adjust budgets or wait for a new reservation."
            ),
            reason_code=reason_code
            if reason_code and code != "spend_safety"
            else (reason_code or _reason_from_spend_detail(detail=detail)),
        )
    if code == "body_too_large" or status_code == 413:
        return ToolError(
            code="body_too_large",
            message=(
                detail
                if code == "body_too_large"
                else "Ingest body exceeds the deployment size limit."
            ),
            http_status=413,
            retryable=False,
            agent_action=(
                "Split or shorten the document; do not retry the same payload."
            ),
            reason_code=reason_code,
        )
    if code == "empty_body":
        return ToolError(
            code="empty_body",
            message=detail if detail else "Ingest body is empty.",
            http_status=422,
            retryable=False,
            agent_action=("Provide non-empty path / text / content_base64 content."),
            reason_code=reason_code,
        )
    if code == "dispatch_refused" or (
        status_code == 403 and detail.startswith("dispatch_refused")
    ):
        return ToolError(
            code="dispatch_refused",
            message=detail,
            http_status=403,
            retryable=False,
            agent_action=(
                "Surface the reason to the user/operator; do not busy-retry."
                " Typical causes: spend cap, missing policy, halt."
            ),
            reason_code=reason_code,
        )
    if code == "dispatch_parked" or (
        status_code == 423 and detail.startswith("dispatch_parked")
    ):
        return ToolError(
            code="dispatch_parked",
            message=detail,
            http_status=423,
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
            message=detail or "Unauthorized.",
            http_status=401,
            retryable=False,
            agent_action=(
                "Refresh or replace REMEMBER_API_KEY; re-mint if the token was revoked."
            ),
            reason_code=reason_code,
        )
    if status_code == 403:
        return ToolError(
            code="forbidden",
            message=detail or "Forbidden.",
            http_status=403,
            retryable=False,
            agent_action=(
                "Use a token for the configured deployment; check origin and"
                " scope constraints."
            ),
            reason_code=reason_code,
        )
    if status_code == 0:
        return ToolError(
            code="transport_error",
            message=detail or "Transport failure talking to the deployment API.",
            http_status=0,
            retryable=True,
            agent_action=("Retry with back-off; check REMEMBER_API_URL and network."),
            reason_code=reason_code,
        )
    if 400 <= status_code < 500:
        return ToolError(
            code="engine_client_error",
            message=detail or f"Client error from engine (HTTP {status_code}).",
            http_status=status_code,
            retryable=False,
            agent_action="Read the message; fix arguments. Do not retry blindly.",
            reason_code=reason_code,
        )
    if status_code >= 500:
        return ToolError(
            code="engine_unavailable",
            message=detail or f"Engine unavailable (HTTP {status_code}).",
            http_status=status_code,
            retryable=True,
            agent_action=(
                "Retry with back-off (3–5 attempts, 2s→30s). If still failing,"
                " report an operator outage."
            ),
            reason_code=reason_code,
        )
    return ToolError(
        code="engine_client_error",
        message=detail or f"Unexpected status {status_code}.",
        http_status=status_code,
        retryable=False,
        agent_action="Read the message; fix arguments or report to an operator.",
        reason_code=reason_code,
    )


def _is_spend_safety(*, code: str, detail: str, reason_code: str | None) -> bool:
    """True when the cloud refused work for spend / reservation safety."""
    spend_codes = {
        "spend_safety",
        "reservation_refused",
        "spend_cap",
        "budget_exceeded",
        "spend_reservation_refused",
    }
    if code in spend_codes:
        return True
    if detail.startswith("spend_safety"):
        return True
    if reason_code in {"cap_hit", "reservation_refused", "spend_cap"} and code in {
        "dispatch_refused",
        "spend_safety",
        "engine_client_error",
    }:
        # Only elevate bare spend reason codes when the detail is spend-shaped;
        # dispatch_refused:cap_hit stays dispatch_refused (already mapped first).
        return code != "dispatch_refused" and (
            "spend" in detail.lower() or "reservation" in detail.lower()
        )
    return False


def _reason_from_spend_detail(*, detail: str) -> str | None:
    """Pull a reason tail from ``spend_safety:reason`` forms."""
    if ":" in detail:
        head, tail = detail.split(":", 1)
        if head in {
            "spend_safety",
            "reservation_refused",
            "spend_cap",
            "budget_exceeded",
            "spend_reservation_refused",
        }:
            return tail or None
    return None


def _split_detail_code(*, detail: str) -> tuple[str, str | None]:
    """Split ``code`` or ``code:reason`` cloud detail forms."""
    if ":" in detail:
        head, tail = detail.split(":", 1)
        if head in {
            "dispatch_refused",
            "dispatch_parked",
            "body_too_large",
            "empty_body",
            "spend_safety",
            "reservation_refused",
            "spend_cap",
            "budget_exceeded",
            "spend_reservation_refused",
        }:
            return head, tail or None
    known = {
        "body_too_large",
        "empty_body",
        "dispatch_refused",
        "dispatch_parked",
        "data_plane_upstream_error",
        "spend_safety",
        "reservation_refused",
        "spend_cap",
        "budget_exceeded",
        "spend_reservation_refused",
    }
    if detail in known:
        return detail, None
    return detail, None


def error_result(error: ToolError) -> dict[str, object]:
    """MCP tools/call error result with one JSON text block."""
    return {
        "content": [{"type": "text", "text": json.dumps(error.as_dict())}],
        "isError": True,
    }


def invalid_arguments(*, message: str) -> ToolError:
    """Common 422 invalid_arguments envelope."""
    return ToolError(
        code="invalid_arguments",
        message=message,
        http_status=422,
        retryable=False,
        agent_action="Fix the tool arguments and retry.",
    )


def status_error_result(
    *, status_code: int | None, detail: str, code: str | None
) -> dict[str, object]:
    """MCP tools/call error result for a failed engine call.

    ``status_code`` is ``None`` when the call was refused before it reached the
    engine (a local argument check); ``code`` is omitted when the engine gave
    none.
    """
    error: dict[str, object] = {"status_code": status_code, "detail": detail}
    if code is not None:
        error["code"] = code
    return {
        "content": [
            {"type": "text", "text": json.dumps({"error": error}, sort_keys=True)}
        ],
        "isError": True,
    }
