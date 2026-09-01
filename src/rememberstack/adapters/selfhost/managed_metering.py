"""HTTP adapter for content-free managed ingest and outcome receipts."""

from __future__ import annotations

import httpx
from pydantic import SecretStr

from rememberstack.model.metering import ManagedDocumentVersionOutcomeV2
from rememberstack.model.metering import ManagedIngestMeasurementV2
from rememberstack.model.metering import MeterAdmissionResult
from rememberstack.model.metering import MeterReceiptConflict
from rememberstack.model.metering import MeterReceiptUnauthorized
from rememberstack.model.metering import MeterReceiptUnavailable


class ControlPlaneMeterReceipts:
    """Deliver v2 receipts with a deployment-scoped ``umc_mi_`` bearer."""

    def __init__(
        self,
        *,
        base_url: str,
        token: SecretStr,
        timeout_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Bind the meter-ingest root and single-show machine credential."""
        self._base_url = base_url.rstrip("/")
        self._authorization = f"Bearer {token.get_secret_value()}"
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    def admit_measurement(
        self, *, measurement: ManagedIngestMeasurementV2
    ) -> MeterAdmissionResult:
        """Post one canonical measurement and validate its stable decision."""
        body = self._post(
            path="/processed-input/measurements",
            payload=measurement.model_dump(mode="json"),
        )
        try:
            return MeterAdmissionResult.model_validate(body)
        except ValueError as error:
            raise MeterReceiptUnavailable(
                "meter admission returned an invalid decision"
            ) from error

    def acknowledge_outcome(self, *, outcome: ManagedDocumentVersionOutcomeV2) -> None:
        """Post one terminal receipt and require an explicit acknowledgement."""
        body = self._post(
            path="/processed-input/outcomes", payload=outcome.model_dump(mode="json")
        )
        if body.get("acknowledged") is not True:
            raise MeterReceiptUnavailable("meter outcome returned no acknowledgement")

    def _post(self, *, path: str, payload: dict[str, object]) -> dict[str, object]:
        """Post an allowlisted receipt shape without logging request material."""
        forbidden = {"content", "text", "filename", "source_uri", "content_hash"}
        if forbidden.intersection(payload):
            raise RuntimeError("meter receipt contains a forbidden content field")
        try:
            response = self._client.post(
                f"{self._base_url}{path}",
                headers={"Authorization": self._authorization},
                json=payload,
            )
        except (httpx.TimeoutException, httpx.HTTPError) as error:
            raise MeterReceiptUnavailable("meter ingest unavailable") from error
        if response.status_code in {409, 422}:
            raise MeterReceiptConflict("meter receipt rejected")
        if response.status_code in {401, 403}:
            raise MeterReceiptUnauthorized("meter producer credential rejected")
        if response.status_code >= 400:
            raise MeterReceiptUnavailable(
                f"meter ingest returned HTTP {response.status_code}"
            )
        try:
            parsed = response.json()
        except ValueError as error:
            raise MeterReceiptUnavailable("meter ingest returned non-JSON") from error
        if not isinstance(parsed, dict):
            raise MeterReceiptUnavailable("meter ingest returned a non-object")
        return parsed
