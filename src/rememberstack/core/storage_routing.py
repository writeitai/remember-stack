"""Storage-class routing for every immutable object (D51 guardrail 3).

Which originals stay cheap to read and which go cold is a *policy* decision
about the corpus, not a storage-provider mechanic — so it lives here, in
pure logic, and both the ingest path (which routes at the write) and the
provider adapters (which apply the class) depend on it rather than on each
other.

Media a multimodal harness actually reads stays hot: for a video, an audio
file, or a photo *input*, the original IS the artifact — conversion yields
only a lossy transcript or description. Text and office originals are kept
for audit and re-conversion, so they go cold. Routing at the write is what
kills the grep-the-archive cost bug at the source rather than on the bill.
"""

from typing import Final

HOT_MIME_PREFIXES: Final = ("video/", "audio/", "image/")
"""Originals a harness reads directly — the bytes themselves are the value."""

HOT: Final = "hot"
COLD: Final = "cold"


def storage_class_for_derived() -> str:
    """Keep canonical Markdown, indexes, and referenced media cheap to read."""
    return HOT


def storage_class_for_internal() -> str:
    """Keep replay checkpoints and private transcripts in archival storage."""
    return COLD


def storage_class_for_snapshot() -> str:
    """Keep the published corpus tree and its manifest cheap to browse."""
    return HOT


def storage_class_for(*, mime: str) -> str:
    """Route one original's storage class by mime (per-deployment policy)."""
    return HOT if mime.startswith(HOT_MIME_PREFIXES) else COLD
