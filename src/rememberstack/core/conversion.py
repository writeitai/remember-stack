"""The D38 conversion router: raw bytes → Markdown, one pluggable route per MIME.

The router is the per-deployment routing table; converters are interchangeable
implementations of one protocol. The route taken and the converter's identity
are recorded on every representation (D65), so a converter change is always a
version bump, never a silent difference.
"""

from collections.abc import Mapping
from typing import Final
from typing import Literal
from typing import Protocol
from typing import runtime_checkable

from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import ManifestComponent
from rememberstack.model import UnroutableMimeError

PASSTHROUGH_CONVERTER_VERSION: Final = "passthrough-2026.07"
"""Pins the passthrough route's behavior (strict UTF-8 decode, no rewriting)."""

STOCK_CONVERSION_ROUTE_NAMES: Final[dict[str, str]] = {
    "text/markdown": "passthrough",
    "text/plain": "passthrough",
}
"""The stock self-host MIME → converter-name table (the settings default).

The CLI/SDK guess ``.txt`` as ``text/plain`` and MCP ``text`` ingest defaults
to the same. Routing only ``text/markdown`` dead-letters those converts
(UMC #228 / RememberStack #301).
"""


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
            document_md = content.decode("utf-8")
        except UnicodeDecodeError as err:
            raise ConversionError(
                f"input declared {mime!r} is not valid UTF-8 text"
            ) from err
        return ConversionResult(
            document_md=document_md,
            manifest=ConverterManifest(
                components=(
                    ManifestComponent(
                        name="passthrough",
                        version=PASSTHROUGH_CONVERTER_VERSION,
                        execution="library-local",
                    ),
                ),
                coverage=ConversionCoverage(policy="identity-full-text", complete=True),
                derivation_ranges=entire_document_labeling(
                    document_md=document_md,
                    derivation_kind="passthrough",
                    evidence_mode="source_expression",
                ),
            ),
        )


def entire_document_labeling(
    *,
    document_md: str,
    derivation_kind: str,
    evidence_mode: Literal[
        "source_expression", "model_observation", "model_interpretation"
    ],
) -> tuple[DerivationRange, ...]:
    """Label the whole output as one range — labeling is total, even for text (§5)."""
    if not document_md:
        return ()
    return (
        DerivationRange(
            start=0,
            end=len(document_md),
            derivation_kind=derivation_kind,
            evidence_mode=evidence_mode,
        ),
    )


def stock_passthrough_routes() -> dict[str, Converter]:
    """Materialize ``STOCK_CONVERSION_ROUTE_NAMES`` (all passthrough) as a table."""
    passthrough = MarkdownPassthroughConverter()
    return {mime: passthrough for mime in STOCK_CONVERSION_ROUTE_NAMES}
