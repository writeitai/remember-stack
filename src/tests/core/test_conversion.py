"""The D38 conversion router and passthrough route: pure behavior proofs."""

import pytest

from rememberstack.core import ConversionRouter
from rememberstack.core import MarkdownPassthroughConverter
from rememberstack.core import stock_passthrough_routes
from rememberstack.model import ConversionError
from rememberstack.model import UnroutableMimeError


def test_router_returns_the_configured_route() -> None:
    """A routed MIME type resolves to exactly its configured converter."""
    passthrough = MarkdownPassthroughConverter()
    router = ConversionRouter(routes={"text/markdown": passthrough})
    assert router.converter_for(mime="text/markdown") is passthrough


def test_unrouted_mime_is_a_typed_error() -> None:
    """An unconfigured MIME type never falls through to a default route."""
    router = ConversionRouter(routes={})
    with pytest.raises(UnroutableMimeError):
        router.converter_for(mime="application/x-unknown")


def test_passthrough_preserves_the_text_exactly() -> None:
    """The passthrough route is the identity on UTF-8 text."""
    source = "# Title\n\nBody with ünïcode.\n"
    result = MarkdownPassthroughConverter().convert(
        content=source.encode("utf-8"), mime="text/markdown"
    )
    assert result.document_md == source


def test_passthrough_rejects_non_utf8_bytes_as_typed_failure() -> None:
    """Undecodable bytes fail deterministically — never silently mangled."""
    with pytest.raises(ConversionError):
        MarkdownPassthroughConverter().convert(
            content=b"\xff\xfe\x00broken", mime="text/plain"
        )


def test_stock_passthrough_routes_accept_plain_text() -> None:
    """CLI/SDK .txt ingest is text/plain; stock convert must not dead-letter it."""
    router = ConversionRouter(routes=stock_passthrough_routes())
    converter = router.converter_for(mime="text/plain")
    assert converter.name == "passthrough"
    result = converter.convert(content=b"hello note\n", mime="text/plain")
    assert result.document_md == "hello note\n"
    assert router.converter_for(mime="text/markdown") is converter


def test_stock_passthrough_routes_still_reject_unknown_mime() -> None:
    """Unknown MIME stays UnroutableMimeError; no silent default."""
    router = ConversionRouter(routes=stock_passthrough_routes())
    with pytest.raises(UnroutableMimeError):
        router.converter_for(mime="application/pdf")
