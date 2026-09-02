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


@pytest.mark.parametrize(
    "origin",
    [
        # A wildcard. Browsers never send one, so this could only ever match
        # nothing — and the operator who wrote it would believe they had
        # granted a whole subdomain tree.
        "https://*.example.com",
        # Userinfo. Never appears in an `Origin` header.
        "https://evil.example.com@app.example.com",
        # Browsers lowercase the scheme and host before sending.
        "HTTPS://App.Example.com",
        # A trailing dot is a different string to a byte comparison.
        "https://app.example.com.",
        # Not a secure origin.
        "http://app.example.com",
        # Not an origin at all.
        "*",
        "null",
        "https://",
        # A caller that forgot to split its configuration.
        "https://a.example.com,https://b.example.com",
        " https://app.example.com",
    ],
)
def test_an_origin_a_browser_could_never_send_is_refused(origin: str) -> None:
    """Rejected at startup, because the alternative fails silently.

    Matching is a byte comparison against the `Origin` header. Anything that
    is not exactly what a browser sends can never match, so accepting it
    produces a deployment that refuses every cross-origin request while its
    configuration looks correct — and nobody goes back to check a setting they
    watched start cleanly.

    The wildcard is the one that matters most: an operator who writes
    `https://*.example.com` and sees it accepted believes they have granted a
    subdomain tree. They have granted nothing, and will debug the app instead.
    """
    app = _App()

    with pytest.raises(ValueError, match="exact https origin"):
        _install_browser_origins(app=app, origins=(origin,))  # type: ignore[arg-type]

    assert app.installed == []


@pytest.mark.parametrize(
    "origin",
    [
        "https://app.example.com",
        "https://app.remember.dev",
        # A port is part of the origin and browsers send it.
        "https://app.example.com:8443",
        # A bracketed IPv6 literal is a form a browser does send.
        "https://[::1]",
        "https://a-b.example.co.uk",
    ],
)
def test_the_forms_a_browser_does_send_are_allowed(origin: str) -> None:
    """The validator must not be so strict it refuses legitimate origins.

    A rule tight enough to reject every impossible form is only useful if it
    still admits the ordinary ones; otherwise it just moves the outage.
    """
    app = _App()

    _install_browser_origins(app=app, origins=(origin,))  # type: ignore[arg-type]

    assert app.installed[0]["allow_origins"] == [origin]
