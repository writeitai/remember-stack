"""Failures a control-plane call can produce, typed so a caller can branch.

D41 binds one error envelope for machine traffic:

    {"detail": {"code": …, "message": …, "retryable": bool, "request_id": …}}

Clients branch on ``code``, never on ``message`` — the message is operator-safe
prose and explicitly non-authoritative. These exceptions carry the code through
so a caller can do the same, and expose ``retryable`` rather than making every
caller re-derive it from a status number.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from remember.models import PipelineReadinessReport


class CloudError(Exception):
    """Base for every control-plane failure.

    Carries the D41 envelope fields when the server sent them. A response that
    is not shaped like the envelope still produces one of these — with
    ``code=None`` — so a caller never has to handle two failure shapes.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        """Bind the envelope fields that were present."""
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable
        self.request_id = request_id

    def __str__(self) -> str:
        """Include the request id, because support will ask for it."""
        base = super().__str__()
        return f"{base} (request_id={self.request_id})" if self.request_id else base


class Unauthenticated(CloudError):
    """No credential, or one the server will not accept (401).

    Also raised for a revoked or expired credential, and for one whose
    membership has ended — from a client's point of view these are the same
    situation: sign in again and mint a fresh credential.
    """


class NotPermitted(CloudError):
    """Authenticated, but this credential may not do this (403).

    Distinct from :class:`Unauthenticated` because retrying will not help and a
    fresh credential of the same profile will not either — the profile itself
    does not permit the call, or the credential belongs to a different
    organisation.
    """


class RateLimited(CloudError):
    """Admission refused the call (429).

    ``retry_after`` is the server's own advice in seconds when it gave any.
    """

    def __init__(
        self, message: str, *, retry_after: float | None = None, **kwargs: object
    ) -> None:
        """Bind the retry hint alongside the envelope fields."""
        super().__init__(message, **kwargs)  # type: ignore[arg-type]
        self.retry_after = retry_after


class MemoryApiError(RuntimeError):
    """The memory API returned an error response or network failed."""

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int = 0,
        detail: str | None = None,
        code: str | None = None,
        response: object | None = None,
        retry_after: float | None = None,
    ) -> None:
        eff_detail = detail if detail is not None else (message or "")
        msg = (
            f"API {status_code}: {eff_detail}"
            if (detail is not None or status_code != 0)
            else eff_detail
        )
        super().__init__(msg)
        self.status_code = status_code
        self.detail = eff_detail
        self.code = code
        self.response = response
        #: Seconds from ``Retry-After`` on a ``429`` admission refusal
        #: (``rate_limited`` / ``concurrency_limited``), when the server sent it.
        self.retry_after = retry_after


class ConnectorNotFoundError(Exception):
    """A connector id is not present in this deployment."""


class PipelineDeadLettered(RuntimeError):
    """A required pipeline stage of a waited-on version is ``dead_letter``.

    ``dead_letter`` means the stage used up its retry attempts; unlike
    ``failed`` (a retry is scheduled), it never heals by waiting, so a wait
    stops here instead of running out its timeout. ``dead_lettered`` lists
    every ``(version_id, stage, status)`` found, and ``report`` is the
    readiness report that showed them. A deployment operator can inspect and
    replay dead-lettered work once the cause is fixed.
    """

    def __init__(
        self,
        *,
        dead_lettered: tuple[tuple[UUID, str, str], ...],
        report: PipelineReadinessReport,
    ) -> None:
        """Bind the dead-lettered stages and the report that showed them."""
        listed = "; ".join(
            f"version {version_id} stage {stage} is {status}"
            for version_id, stage, status in dead_lettered
        )
        super().__init__(
            f"pipeline processing stopped: {listed}. A dead-lettered stage has"
            " used all its retries and will not become ready by waiting"
        )
        self.dead_lettered = dead_lettered
        self.report = report
