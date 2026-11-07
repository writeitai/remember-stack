"""Route cost classes and the doc-text character count."""

from __future__ import annotations

import pytest

from rememberstack.core.conversion import STOCK_CONVERSION_ROUTE_NAMES
from rememberstack.core.metering import cost_class_for_route
from rememberstack.core.metering import FREE_ROUTES
from rememberstack.core.metering import normalized_character_count
from rememberstack.core.metering import UnmeteredRouteError


def test_every_stock_route_is_billed_or_free() -> None:
    """A metered deployment can run every route the engine ships by default."""
    for route in set(STOCK_CONVERSION_ROUTE_NAMES.values()) - FREE_ROUTES:
        cost_class_for_route(route=route)


def test_pdf_is_scan_and_local_readers_are_text() -> None:
    """Every PDF bills pages; reading routes bill characters."""
    assert cost_class_for_route(route="pdf") == "doc-scan"
    for route in ("text", "markitdown", "office", "spreadsheet", "email"):
        assert cost_class_for_route(route=route) == "doc-text"


@pytest.mark.parametrize("route", ["image_ocr_description", "mistral_ocr", "x"])
def test_provider_routes_without_a_class_are_refused(route: str) -> None:
    """A route that would call a provider unbilled never runs under metering."""
    with pytest.raises(UnmeteredRouteError, match="rate_class_unavailable"):
        cost_class_for_route(route=route)


def test_whitespace_runs_count_once() -> None:
    """Each maximal whitespace run is one character; every other scalar is one."""
    assert normalized_character_count(text="a  b\n\n\tc") == 5
    assert normalized_character_count(text="  é") == 2
    assert normalized_character_count(text="") == 0
