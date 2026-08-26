"""D46 spend-lease failures shared by the HTTP surface and the CP adapter."""


class SpendLeaseRefused(Exception):
    """The control plane refused or parked the hold."""

    def __init__(self, *, status_code: int, detail: str) -> None:
        """Record the HTTP status the engine should surface."""
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class SpendLeaseUnavailable(Exception):
    """Timeout or transport failure talking to the lease API."""
