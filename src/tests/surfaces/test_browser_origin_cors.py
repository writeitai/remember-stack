"""A credential cannot reach a deployment the browser refuses to call (D59).

The managed product serves its app from one origin and each deployment answers
on its own, so every call the app makes is cross-origin. A browser refuses
those before the request is sent — the credential is never examined, the
perimeter never runs, and the failure looks like the deployment being down.

These tests pin the two halves that matter: nothing is advertised unless a
deployment was told about an origin, and what is advertised is exact.
"""

from __future__ import annotations

from uuid import UUID

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
    with pytest.raises(ValueError, match="exactly as a browser serializes"):
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
        # Not a secure origin.
        "http://app.example.com",
        # Not an origin at all.
        "*",
        "null",
        "https://",
        # A caller that forgot to split its configuration.
        "https://a.example.com,https://b.example.com",
        " https://app.example.com",
        # Ports outside the range, and a host longer than DNS allows. Neither
        # can appear in a real `Origin`, so both are the same silent failure
        # as a wildcard: accepted, matching nothing, and invisible.
        "https://app.example.com:0",
        "https://app.example.com:99999",
        "https://" + "a" * 300 + ".example.com",
        # A malformed bracketed literal.
        "https://[::::]",
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

    with pytest.raises(ValueError, match="exactly as a browser serializes"):
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
        # A self-hoster developing against a local https listener. A single
        # label is a legitimate host and refusing it would push people to
        # weaken the scheme check instead.
        "https://localhost",
        "https://localhost:3000",
        # Punycode is what a browser sends for an internationalised domain.
        "https://xn--bcher-kva.example",
        # Browsers do serialize an underscore and a trailing dot, whatever
        # RFC 1123 says about hostnames, so refusing them would reject an
        # origin a real browser sends.
        "https://my_app.example.com",
        "https://app.example.com.",
        # A valid IDNA2008 label. The stdlib's legacy `idna` codec rejects
        # this, which is why hostname syntax is left to the resolver: a check
        # that refuses real origins is worse than one that admits a name DNS
        # will simply fail to resolve.
        "https://xn--fa-hia.de",
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


def test_a_stray_comma_is_refused_rather_than_skipped() -> None:
    """A list the operator wrote deliberately is validated as a whole.

    Quietly dropping an empty segment makes `https://a.example,,https://b.example`
    start cleanly with two of the three things the operator thought they
    configured — and the missing one is invisible until somebody's browser is
    refused. Startup failure is only a guarantee if it covers the whole list.
    """
    from rememberstack.profiles.selfhost import _browser_origins

    assert _browser_origins("") == ()
    assert _browser_origins("   ") == ()
    assert _browser_origins("https://a.example") == ("https://a.example",)

    # The typo survives the split so validation can refuse it.
    assert "" in _browser_origins("https://a.example,,https://b.example")
    assert "" in _browser_origins("https://a.example,")

    app = _App()
    with pytest.raises(ValueError, match="exactly as a browser serializes"):
        _install_browser_origins(
            app=app,  # type: ignore[arg-type]
            origins=_browser_origins("https://a.example,"),
        )
    assert app.installed == []


def test_only_the_headers_a_browser_client_sends_are_advertised() -> None:
    """A header no route reads is an invitation to depend on one.

    `OPTIONS` is absent because the middleware answers preflight itself, so
    advertising it names a method no route serves.
    """
    app = _App()

    _install_browser_origins(
        app=app,  # type: ignore[arg-type]
        origins=("https://app.example.com",),
    )

    installed = app.installed[0]
    assert installed["allow_headers"] == ["Authorization", "Content-Type"]
    assert installed["allow_methods"] == ["GET", "POST"]


def test_cors_is_installed_outermost() -> None:
    """Otherwise the errors a browser most needs to read are unreadable.

    Starlette runs middleware in reverse order of addition, so a CORS layer
    installed early sits *inside* everything added after it. The ingest body
    limiter's 413 and the spend lease's 503 would then return without CORS
    headers, and the browser would surface them as opaque network failures —
    the app would show "could not reach the deployment" for a deployment that
    answered clearly.
    """
    from unittest.mock import MagicMock

    from rememberstack.surfaces.http_api import build_api

    boundary = MagicMock()
    boundary.ensure_ready.return_value = ()
    app = build_api(
        engine=MagicMock(),
        deployment_id=UUID("11111111-1111-1111-1111-111111111111"),
        admission=boundary,
        readiness=boundary,
        ingest_body_max_bytes=1_000,
        browser_origins=("https://app.example.com",),
    )

    classes = [getattr(m.cls, "__name__", str(m.cls)) for m in app.user_middleware]
    # `user_middleware` is in reverse execution order: the last added is first
    # in the list and outermost at runtime.
    assert classes[0] == "CORSMiddleware", classes
