"""Bounded read access to one immutable source, without buffering it whole.

The converter contract begins with complete `bytes` (D38/D57), which is
workable for text and unworkable for media: the client, the HTTP process, the
object store, and the converter each materialize the whole file. This port is
the seam that lets a route read a source by stream, by range, or as a bounded
local file, so a large source costs bounded memory rather than its own size.
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import SourceIdentity


@runtime_checkable
class SourceHandlePort(Protocol):
    """Read one immutable source without requiring its size in memory.

    Deliberately absent: any method returning the whole source as `bytes`.
    A caller that genuinely needs every byte streams them and decides what to
    keep; the port never makes whole-file buffering the easy path.
    """

    @property
    def identity(self) -> SourceIdentity:
        """The source's recorded key, content hash, size, and declared type."""
        ...

    def open_stream(self, *, chunk_bytes: int = 1024 * 1024) -> Iterator[bytes]:
        """Yield the source in order, holding at most one chunk at a time."""
        ...

    def read_range(self, *, start: int, end: int) -> bytes:
        """Read the half-open byte interval ``[start, end)``.

        Half-open to match every other interval in the system (D65 locators),
        so a caller never has to remember which end is inclusive.
        """
        ...

    def materialize_seekable(self, *, max_bytes: int) -> AbstractContextManager[Path]:
        """Write the source to a temporary local file, removed on exit.

        Decoders such as FFmpeg need a seekable path, not a stream. The bound
        is checked against the recorded size before any bytes move, and the
        context manager owns cleanup so a failing converter cannot leak the
        file it asked for.
        """
        ...
