"""Control-plane metadata spend lease (D46). Never sends a memory payload."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from rememberstack.model import SpendLeaseRefused
from rememberstack.model import SpendLeaseUnavailable


class ControlPlaneSpendLease:
    """POST ``{base}/reserve|commit|release`` with the inbound Bearer."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_s: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """``base_url`` is the spend prefix, e.g. ``https://host/app/api/v1/spend``."""
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def reserve(
        self,
        *,
        authorization: str,
        path_id: str,
        content_length: int | None = None,
        mime: str | None = None,
        operation_name: str | None = None,
    ) -> UUID:
        """Open a metadata hold. Raises refused/unavailable; never sends a body."""
        payload: dict[str, Any] = {"path_id": path_id}
        if content_length is not None:
            payload["content_length"] = content_length
        if mime:
            payload["mime"] = mime
        if operation_name:
            payload["operation_name"] = operation_name
        body = self._post(path="/reserve", authorization=authorization, json=payload)
        try:
            return UUID(str(body["reservation_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise SpendLeaseUnavailable(
                "spend lease reserve returned no reservation"
            ) from error

    def commit(self, *, authorization: str, reservation_id: UUID) -> None:
        """Commit a hold after successful work."""
        self._post(
            path="/commit",
            authorization=authorization,
            json={"reservation_id": str(reservation_id)},
        )

    def release(self, *, authorization: str, reservation_id: UUID) -> None:
        """Release a hold after failed or refused work."""
        self._post(
            path="/release",
            authorization=authorization,
            json={"reservation_id": str(reservation_id)},
        )

    def _post(
        self, *, path: str, authorization: str, json: dict[str, Any]
    ) -> dict[str, Any]:
        """POST JSON. Forbidden keys never belong in ``json``."""
        forbidden = {"content", "query", "arguments", "filename"}
        if forbidden.intersection(json):
            raise RuntimeError("spend lease payload must not carry memory fields")
        try:
            response = self._client.post(
                f"{self._base_url}{path}",
                headers={"Authorization": authorization},
                json=json,
            )
        except httpx.TimeoutException as error:
            raise SpendLeaseUnavailable("spend lease timed out") from error
        except httpx.HTTPError as error:
            raise SpendLeaseUnavailable("spend lease unreachable") from error
        if response.status_code in {401, 403, 423}:
            raise SpendLeaseRefused(
                status_code=response.status_code,
                detail=_detail_from_response(response=response),
            )
        if response.status_code >= 400:
            raise SpendLeaseUnavailable(f"spend lease HTTP {response.status_code}")
        if not response.content:
            return {}
        try:
            parsed = response.json()
        except ValueError as error:
            raise SpendLeaseUnavailable("spend lease returned non-JSON") from error
        if not isinstance(parsed, dict):
            raise SpendLeaseUnavailable("spend lease returned a non-object")
        return parsed


def _detail_from_response(*, response: httpx.Response) -> str:
    """Surface CP ``detail`` when it is a string; otherwise a stable code."""
    try:
        parsed = response.json()
    except ValueError:
        return "spend_lease_refused"
    if isinstance(parsed, dict):
        detail = parsed.get("detail")
        if isinstance(detail, str) and detail:
            return detail
    return "spend_lease_refused"
