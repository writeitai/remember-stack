"""The markitdown conversion route (D38): office/html/email formats → Markdown."""

import io
from typing import Final

from markitdown import MarkItDown
from markitdown import StreamInfo
from markitdown._exceptions import MarkItDownException

from rememberstack.core import entire_document_labeling
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import ManifestComponent

MARKITDOWN_CONVERTER_VERSION: Final = "markitdown-0.2"
"""Pins this route's generation: the markitdown 0.1.x library line plus D65
envelope emission. Bumped whenever library or output contract changes so
replay never reuses artifacts from an older shape."""


class MarkitdownConverter:
    """The default local route for structured text formats (html, office, email)."""

    def __init__(self) -> None:
        """Build the converter once; markitdown instances are reusable."""
        self._markitdown = MarkItDown(enable_plugins=False)

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "markitdown"

    @property
    def version(self) -> str:
        """The pinned markitdown route version (D38)."""
        return MARKITDOWN_CONVERTER_VERSION

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Convert one input via markitdown; its failures become typed failures."""
        try:
            result = self._markitdown.convert_stream(
                io.BytesIO(content), stream_info=StreamInfo(mimetype=mime)
            )
        except MarkItDownException as err:
            raise ConversionError(f"markitdown could not convert {mime!r}") from err
        document_md = result.text_content
        return ConversionResult(
            document_md=document_md,
            manifest=ConverterManifest(
                components=(
                    ManifestComponent(
                        name="markitdown",
                        version=MARKITDOWN_CONVERTER_VERSION,
                        execution="library-local",
                    ),
                ),
                coverage=ConversionCoverage(
                    policy="markitdown-full-stream", complete=True
                ),
                derivation_ranges=entire_document_labeling(
                    document_md=document_md,
                    derivation_kind="markitdown",
                    evidence_mode="source_expression",
                ),
            ),
        )
