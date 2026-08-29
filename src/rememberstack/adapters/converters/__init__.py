"""Conversion adapters: the converters a deployment can bind per MIME type (D38).

Every conversion adapter implements the one `Converter` protocol and returns
the full D65 envelope (`document.md`, source map, derived assets, manifest);
`build_conversion_routes` materializes a deployment's configured
MIME-type → adapter-name table into the router's route table. What each
adapter does internally (text extraction, OCR, a hosted provider call) is its
own business — the engine only sees the envelope.
"""

from collections.abc import Callable
from collections.abc import Mapping
from typing import Final

from rememberstack.core import Converter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.model import UnknownConverterError


def build_conversion_routes(*, route_names: Mapping[str, str]) -> dict[str, Converter]:
    """Materialize the configured MIME → converter-name map as a route table.

    One converter instance is shared across every MIME type that names it.
    An unknown name refuses composition — a misconfigured deployment fails at
    startup, never by silently dead-lettering uploads later.
    """
    built: dict[str, Converter] = {}
    routes: dict[str, Converter] = {}
    for mime, name in sorted(route_names.items()):
        if name not in built:
            builder = _CONVERTER_BUILDERS.get(name)
            if builder is None:
                raise UnknownConverterError(
                    f"route {mime!r} names unknown converter adapter {name!r}; "
                    f"known adapters: {sorted(_CONVERTER_BUILDERS)}"
                )
            built[name] = builder()
        routes[mime] = built[name]
    return routes


def _passthrough() -> Converter:
    """The identity route for text that already is Markdown/plain text."""
    return MarkdownPassthroughConverter()


def _markitdown() -> Converter:
    """The markitdown route, imported only when a deployment routes to it."""
    from rememberstack.adapters.converters.markitdown import MarkitdownConverter

    return MarkitdownConverter()


_CONVERTER_BUILDERS: Final[dict[str, Callable[[], Converter]]] = {
    "passthrough": _passthrough,
    "markitdown": _markitdown,
}
"""Every converter-adapter name a route table may bind (D38)."""
