"""Provider-neutral port for managed ingest admission and terminal receipts."""

from typing import Protocol

from rememberstack.model.metering import ManagedDocumentVersionOutcome
from rememberstack.model.metering import ManagedIngestMeasurement
from rememberstack.model.metering import MeterAdmissionResult


class MeterReceiptPort(Protocol):
    """Deliver content-free receipts to a commercial authority."""

    def admit_measurement(
        self, *, measurement: ManagedIngestMeasurement
    ) -> MeterAdmissionResult:
        """Return the stable two-hold admission decision for a measurement."""
        ...

    def acknowledge_outcome(self, *, outcome: ManagedDocumentVersionOutcome) -> None:
        """Acknowledge a terminal outcome or raise so it remains replayable."""
        ...
