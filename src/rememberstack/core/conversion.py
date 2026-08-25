"""The D38 conversion router: raw bytes → Markdown, one pluggable route per MIME.

The router is the per-deployment routing table; converters are interchangeable
implementations of one protocol. The route taken and the converter's identity
are recorded on every representation (D65), so a converter change is always a
version bump, never a silent difference.
"""

from collections.abc import Mapping
from typing import Final
from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import UnroutableMimeError

PASSTHROUGH_CONVERTER_VERSION: Final = "passthrough-2026.07"
"""Pins the passthrough route's behavior (strict UTF-8 decode, no rewriting)."""


@runtime_checkable
class Converter(Protocol):
    """One conversion route: raw bytes of a supported MIME type → Markdown."""

    @property
    def name(self) -> str:
        """The route name recorded on representations (e.g. ``markitdown``)."""
        ...

    @property
    def version(self) -> str:
        """The converter version (D38): a bump creates new representations."""
        ...

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Produce the Markdown reading; raise ``ConversionError`` on bad input."""
        ...


class ConversionRouter:
    """Route an input MIME type to its configured converter (D38 routing table)."""

    def __init__(self, *, routes: Mapping[str, Converter]) -> None:
        """Bind the deployment's MIME → converter table."""
        self._routes = dict(routes)

    def converter_for(self, *, mime: str) -> Converter:
        """Return the route for a MIME type; an unrouted type is a typed error."""
        converter = self._routes.get(mime)
        if converter is None:
            raise UnroutableMimeError(f"no conversion route accepts mime {mime!r}")
        return converter


class MarkdownPassthroughConverter:
    """The identity route for inputs that already are Markdown or plain text."""

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "passthrough"

    @property
    def version(self) -> str:
        """The pinned passthrough behavior version."""
        return PASSTHROUGH_CONVERTER_VERSION

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Decode the bytes as UTF-8 text; undecodable input is a typed failure."""
        try:
            return ConversionResult(document_md=content.decode("utf-8"))
        except UnicodeDecodeError as err:
            raise ConversionError(
                f"input declared {mime!r} is not valid UTF-8 text"
            ) from err


def stock_passthrough_routes() -> dict[str, Converter]:
    """Stock self-host MIME table: UTF-8 text is identity, not MarkItDown.

    The CLI/SDK guess ``.txt`` as ``text/plain`` and MCP ``text`` ingest
    defaults to the same. Routing only ``text/markdown`` dead-letters those
    converts (UMC #228 / RememberStack #301).
    """
    passthrough = MarkdownPassthroughConverter()
    return {"text/markdown": passthrough, "text/plain": passthrough}
