"""The D138 ``notebook`` route: a Jupyter notebook's cells in order.

Markdown cells are prose and are claim-extracted. Code cells become fenced
code blocks labelled ``code`` and raw cells fenced text labelled
``other_text``; both are search-only. Outputs are dropped (coverage says how
many cells had them). The title is the first ``# `` heading in a Markdown
cell. Each cell is its own Markdown block, so extraction eligibility changes
only at block boundaries.
"""

import json
import re
from typing import Any
from typing import Final

from rememberstack.adapters.converters.time_limit import run_with_time_limit
from rememberstack.model import ConversionCoverage
from rememberstack.model import ConversionError
from rememberstack.model import ConversionResult
from rememberstack.model import ConverterManifest
from rememberstack.model import DerivationRange
from rememberstack.model import ManifestComponent
from rememberstack.model.document_metadata import DocumentMetadata

_LANGUAGE: Final = re.compile(r"[\w+#.-]+")
"""A code-fence info word: python, c++, c#, f#, objective-c, …"""

NOTEBOOK_CONVERTER_VERSION: Final = "notebook-2026.09"
"""Pins the notebook route: cell rendering, labels, dropped outputs, title."""


class NotebookConverter:
    """Read an ``.ipynb`` notebook (nbformat 4) cell by cell."""

    @property
    def name(self) -> str:
        """The route name recorded on representations."""
        return "notebook"

    @property
    def version(self) -> str:
        """The pinned notebook route version (D38)."""
        return NOTEBOOK_CONVERTER_VERSION

    def convert(self, *, content: bytes, mime: str) -> ConversionResult:
        """Convert one notebook; invalid JSON or no cell list fails."""
        return run_with_time_limit(
            work=lambda: _convert(content=content), what="notebook conversion"
        )


def _convert(*, content: bytes) -> ConversionResult:
    """Render the cells in order and label each one by its kind."""
    try:
        notebook = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise ConversionError("the notebook is not valid JSON") from err
    cells = notebook.get("cells") if isinstance(notebook, dict) else None
    if not isinstance(cells, list):
        raise ConversionError("the notebook has no cell list (not nbformat 4)")
    language = _language(notebook=notebook)
    document_md = ""
    ranges: list[DerivationRange] = []
    title: str | None = None
    with_outputs = 0
    for cell in cells:
        if not isinstance(cell, dict):
            raise ConversionError("a notebook cell is not a JSON object")
        cell_type = cell.get("cell_type")
        source = _source(cell=cell).strip("\n")
        if cell.get("outputs"):
            with_outputs += 1
        if not source.strip():
            continue
        if cell_type == "markdown":
            text, kind = source, "prose"
            if title is None:
                title = _first_heading(markdown=source)
        elif cell_type == "code":
            text, kind = _fenced(text=source, info=language), "code"
        else:
            text, kind = _fenced(text=source, info="text"), "other_text"
        start = len(document_md)
        document_md += text + "\n\n"
        ranges.append(
            DerivationRange(
                start=start,
                end=len(document_md),
                derivation_kind=kind,
                evidence_mode="source_expression",
            )
        )
    return ConversionResult(
        document_md=document_md,
        metadata=DocumentMetadata(title=title) if title else None,
        manifest=ConverterManifest(
            components=(
                ManifestComponent(
                    name="notebook",
                    version=NOTEBOOK_CONVERTER_VERSION,
                    execution="library-local",
                ),
            ),
            coverage=(
                ConversionCoverage(
                    policy="notebook-cells",
                    complete=False,
                    gaps=(f"outputs of {with_outputs} cells dropped",),
                )
                if with_outputs
                else ConversionCoverage(policy="notebook-cells", complete=True)
            ),
            derivation_ranges=tuple(ranges),
        ),
    )


def _source(*, cell: dict[str, Any]) -> str:
    """A cell's source, stored as one string or a list of lines."""
    source = cell.get("source") or ""
    if isinstance(source, list):
        return "".join(str(line) for line in source)
    return str(source)


def _language(*, notebook: dict[str, Any]) -> str:
    """The kernel's language for code fences; empty when not declared."""
    metadata = notebook.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    for key in ("language_info", "kernelspec"):
        section = metadata.get(key)
        if isinstance(section, dict):
            name = section.get("name" if key == "language_info" else "language")
            if isinstance(name, str) and _LANGUAGE.fullmatch(name):
                return name
    return ""


def _first_heading(*, markdown: str) -> str | None:
    """The text of the first ``# `` heading line, or None."""
    for line in markdown.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
    return None


def _fenced(*, text: str, info: str) -> str:
    """A fenced block longer than any backtick run inside the text."""
    lines = [line.lstrip(" ") for line in text.splitlines()]
    longest = max((len(line) - len(line.lstrip("`")) for line in lines), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{info}\n{text}\n{fence}"
