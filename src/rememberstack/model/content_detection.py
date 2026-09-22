"""Typed ingest refusals for contradictory or unrecognizable file bytes."""


class ContentDetectionError(ValueError):
    """A file cannot be safely assigned a supported content class."""

    def __init__(self, *, code: str) -> None:
        """Expose a stable client-facing reason without echoing source bytes."""
        self.code = code
        super().__init__(code)
