"""Typed failures of the remember client.

Every failure a memory or account call can produce is a :class:`MemoryApiError`
(or a subclass), so one ``except`` covers them all; the subclasses exist for the
cases a caller branches on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from remember.models import PipelineReadinessReport


class MemoryApiError(RuntimeError):
    """The memory API returned an error response or the network failed.

    ``status_code`` is ``0`` when no HTTP response was received. ``code`` is the
    machine-readable error code when the server sent one.
    """

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int = 0,
        detail: str | None = None,
        code: str | None = None,
        response: object | None = None,
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


class RateLimited(MemoryApiError):
    """The engine's direct-path admission refused the call (``429``).

    ``code`` is ``rate_limited`` or ``concurrency_limited``; ``retry_after`` is
    the server's ``Retry-After`` in seconds when it sent one. The client does
    not retry by itself.
    """

    def __init__(
        self, *, detail: str, code: str | None, retry_after: float | None
    ) -> None:
        """Bind the admission code and the server's retry hint."""
        super().__init__(status_code=429, detail=detail, code=code)
        self.retry_after = retry_after


class ProjectResolutionError(MemoryApiError):
    """A signed key's project could not be resolved to a deployment.

    Raised for an unknown project, a project the key does not cover, an
    unusable answer, or an issuer that cannot be reached. A signed key never
    falls back to a local engine.
    """


class StoredKeyRefused(MemoryApiError):
    """The stored key may not be sent to the requested destination.

    A key read from the credential file goes only to its issuer, to the
    issuer-resolved deployment, or to the engine URL recorded beside it. A
    different destination needs a key supplied explicitly (argument or
    ``REMEMBER_API_KEY``).
    """


class AccountApiUnavailable(MemoryApiError):
    """``client.account`` has no account API to call.

    The key has no issuer (a self-hosted shared secret), or the issuer's
    metadata names no ``remember_account_endpoint``. Memory calls are
    unaffected.
    """


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
