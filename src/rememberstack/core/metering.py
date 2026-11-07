"""Which cost class a conversion route bills, and how its quantity is counted.

A managed deployment reports one measurement per new source version before
the work that calls model providers (D139 and the cloud's D94 meter). The
conversion route decides the class:

- ``pdf`` — every PDF, every page through OCR — is ``doc-scan``: the page
  count from the PDF page tree, taken before any OCR call.
- A local reading route (text, Markdown, code, HTML, office documents, email,
  notebooks, spreadsheets, delimited files, datasets) is ``doc-text``: the
  whitespace-normalized character count of the ``document.md`` it produced.
  These routes call no model provider, so counting their output still
  precedes every paid stage.
- ``card`` reads nothing and is free: it sends no measurement.

Any other route has no managed cost class, so a metered deployment refuses
to run it rather than process unbilled provider work.
"""

from __future__ import annotations

from typing import Final

from rememberstack.model.metering import CostClass

METERING_CLASSIFIER_VERSION: Final = "route-cost-class-v1"
"""Pins the route → cost-class table below."""

DOC_TEXT_ALGORITHM_VERSION: Final = "document-md-unicode-whitespace-scalars-v1"
"""Pins how ``doc-text`` is counted: :func:`normalized_character_count` of
the route's ``document.md``."""

DOC_SCAN_ALGORITHM_VERSION: Final = "pdf-page-tree-v1"
"""Pins how ``doc-scan`` is counted: the PDF's structural page count."""

FREE_ROUTES: Final = frozenset({"card"})
"""Routes that read nothing; their storage is billed, their processing is not."""

_ROUTE_CLASSES: Final[dict[str, CostClass]] = {
    "pdf": "doc-scan",
    "passthrough": "doc-text",
    "text": "doc-text",
    "markitdown": "doc-text",
    "office": "doc-text",
    "email": "doc-text",
    "notebook": "doc-text",
    "spreadsheet": "doc-text",
    "table": "doc-text",
    "dataset": "doc-text",
}


class UnmeteredRouteError(ValueError):
    """A metered deployment was asked to run a route that has no cost class."""


def cost_class_for_route(*, route: str) -> CostClass:
    """The cost class a non-free route bills; refuse a route with none."""
    try:
        return _ROUTE_CLASSES[route]
    except KeyError:
        raise UnmeteredRouteError(
            f"route {route!r} has no managed cost class (rate_class_unavailable)"
        ) from None


def normalized_character_count(*, text: str) -> int:
    """Count Unicode scalars after collapsing each maximal whitespace run to one."""
    count = 0
    in_whitespace = False
    for scalar in text:
        whitespace = scalar.isspace()
        if whitespace:
            if not in_whitespace:
                count += 1
        else:
            count += 1
        in_whitespace = whitespace
    return count
