"""Provider-neutral port for managed ingest admission and terminal receipts."""

from typing import Protocol

from rememberstack.model.metering import ManagedDocumentVersionOutcomeV2
from rememberstack.model.metering import ManagedIngestMeasurementV2
from rememberstack.model.metering import MeterAdmissionResult


class MeterReceiptPort(Protocol):
    """Deliver content-free receipts to a commercial authority."""

    def admit_measurement(
        self, *, measurement: ManagedIngestMeasurementV2
    ) -> MeterAdmissionResult:
        """Return the stable two-hold admission decision for a measurement."""
        ...

    def acknowledge_outcome(self, *, outcome: ManagedDocumentVersionOutcomeV2) -> None:
        """Acknowledge a terminal outcome or raise so it remains replayable."""
        ...
