"""A credential cannot reach a deployment the browser refuses to call (D59).

The managed product serves its app from one origin and each deployment answers
on its own, so every call the app makes is cross-origin. A browser refuses
those before the request is sent — the credential is never examined, the
perimeter never runs, and the failure looks like the deployment being down.

These tests pin the two halves that matter: nothing is advertised unless a
deployment was told about an origin, and what is advertised is exact.
"""

from __future__ import annotations

import pytest

from rememberstack.surfaces.http_api import _install_browser_origins


class _App:
    """Records what middleware would be installed."""

    def __init__(self) -> None:
        self.installed: list[dict[str, object]] = []

    def add_middleware(self, _cls: object, **kwargs: object) -> None:
        self.installed.append(kwargs)


def test_a_deployment_told_of_no_origin_advertises_nothing() -> None:
    """The self-host answer, and the safe default for a managed one too.

    A permissive default would hand every website on the internet the ability
    to make authenticated requests from a visitor's browser.
    """
    app = _App()
    _install_browser_origins(app=app, origins=())  # type: ignore[arg-type]
    assert app.installed == []


def test_the_named_origin_is_allowed_without_cookies() -> None:
    """Exactly what was named, and `Authorization` rather than a cookie.

    Credentialed mode would let a named origin ride a session cookie it should
    never see; the browser credential travels in the header instead.
    """
    app = _App()
    _install_browser_origins(
        app=app,  # type: ignore[arg-type]
        origins=("https://app.remember.dev",),
    )
    assert len(app.installed) == 1
    installed = app.installed[0]
    assert installed["allow_origins"] == ["https://app.remember.dev"]
    assert installed["allow_credentials"] is False
    assert "Authorization" in installed["allow_headers"]  # type: ignore[operator]


@pytest.mark.parametrize(
    "origin",
    ["*", "http://app.remember.dev", "app.remember.dev", "https://a.dev,https://b.dev"],
)
def test_a_wildcard_or_insecure_origin_is_refused(origin: str) -> None:
    """`*` is invalid with credentials and wrong in principle here.

    An http origin would let a network attacker on the customer's own network
    read their memory, and a comma-joined string is a caller that forgot to
    split — each would silently widen the perimeter.
    """
    app = _App()
    with pytest.raises(ValueError, match="exact https origin"):
        _install_browser_origins(app=app, origins=(origin,))  # type: ignore[arg-type]
