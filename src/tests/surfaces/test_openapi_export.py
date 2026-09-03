"""The published schema must describe the API this repo actually serves.

The deployment's OpenAPI document is what clients are generated from, so a
document that drifts from the code is worse than no document: consumers compile
against it and the mismatch only surfaces at runtime, on someone else's
machine.

Two ways it can drift, and one test each. It can go stale, because the checked
in file is a build product that nobody regenerates. And it can go *quietly
incomplete*, because routes mount conditionally on ports — an export that
forgot a port publishes a schema missing those endpoints and looks entirely
valid, so a generated client simply would not have them.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
import sys
from typing import Any

_ROOT = Path(__file__).parents[3]
_SCHEMA = _ROOT / "openapi.json"

sys.path.insert(0, str(_ROOT / "scripts"))


def _routes(document: dict[str, Any]) -> set[tuple[str, str]]:
    """(METHOD, path) pairs the document declares."""
    return {
        (method.upper(), path)
        for path, operations in document.get("paths", {}).items()
        for method in operations
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }


def _exported() -> dict[str, Any]:
    """A freshly built document, straight from the app."""
    from export_openapi import build_document

    return build_document()


def test_this_file_still_defines_every_test_it_should() -> None:
    """Guard against a test silently disappearing from this file.

    Twice while writing these checks, an edit that rewrote a block of this file
    deleted a test along with it. Nothing failed — a deleted test cannot fail —
    and the claim it had supported stayed in the commit message, describing a
    guarantee that no longer existed. That is the quietest way for a suite to
    rot: not a test that breaks, but one that stops being there.

    Counting is crude, and deliberately so: it costs one line to update when a
    test is added on purpose, and it is the only thing that notices when one
    vanishes by accident.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    defined = {
        line.removeprefix("def ").split("(")[0]
        for line in source.splitlines()
        if line.startswith("def test_")
    }
    assert len(defined) == 8, (
        f"this file defines {len(defined)} tests: {sorted(defined)}. If you "
        f"added or removed one deliberately, update this count; if you did not, "
        f"an edit has silently dropped a test."
    )


def test_the_checked_in_schema_matches_the_app() -> None:
    """Regenerate and compare, so the committed document cannot go stale.

    The file is a build product, and build products rot silently — a route
    added to `http_api.py` without rerunning the export leaves consumers
    generating clients against yesterday's surface. Comparing the parsed
    documents rather than the bytes means reformatting the file is fine while
    changing what it says is not.
    """
    assert _SCHEMA.exists(), (
        "openapi.json is missing; regenerate it with "
        "`uv run python scripts/export_openapi.py -o openapi.json`"
    )
    committed = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    assert committed == _exported(), (
        "openapi.json no longer matches the app; regenerate it with "
        "`uv run python scripts/export_openapi.py -o openapi.json`"
    )


#: Every route the exported document must declare, and no others.
#:
#: Frozen as a set rather than spot-checked. An earlier version named four
#: routes it cared about, and passed happily while the exporter omitted the
#: operations surface, all seven open-query endpoints and `/deployment` —
#: because a missing route is invisible to a test that only asks about the
#: routes it remembered.
#:
#: This list is **reviewed, not derived**, and the distinction is load-bearing.
#: Whether a route is one a real deployment serves cannot be settled by reading
#: source: capabilities are resolved at run time from settings, so the profile
#: names some it may not compose, and an attempt to check the call sites
#: mechanically was defeated six different ways before it was abandoned. What
#: a test *can* do is refuse to let the published surface change without a
#: person editing this list and saying why — which is how the phantom
#: `/connectors` routes, absent from every shipped deployment, would have been
#: caught: not by the machine, but by the edit being visible.
_SURFACE: frozenset[tuple[str, str]] = frozenset(
    (
        ("GET", "/deployment"),
        ("GET", "/documents"),
        ("GET", "/hydrate/relation/{relation_id}"),
        ("GET", "/lookup/observations"),
        ("GET", "/lookup/relations"),
        ("GET", "/operations"),
        ("GET", "/query/saved"),
        ("GET", "/query/saved/{namespace}/{name}"),
        ("GET", "/query/space"),
        ("GET", "/query/space/search"),
        ("GET", "/resolve"),
        ("GET", "/search/chunks"),
        ("GET", "/search/claims"),
        ("GET", "/transcript/relation/{relation_id}"),
        ("POST", "/graph/citation-path"),
        ("POST", "/graph/neighborhood"),
        ("POST", "/graph/path"),
        ("POST", "/ingest"),
        ("POST", "/operations/{name}"),
        ("POST", "/query/saved/{namespace}/{name}/run"),
        ("POST", "/query/sql"),
        ("POST", "/query/sql/explain"),
        ("POST", "/readiness"),
        ("POST", "/search/chunks"),
        ("POST", "/search/claims"),
    )
)


#: `build_api` parameters that compose a capability.
#:
#: Most mount routes. `auth` and `spend_lease` do not — the first adds a
#: dependency and the bearer scheme, the second installs middleware — but they
#: are capabilities all the same, which is why this is not called a route list.
#:
#: Listed rather than inferred, because "optional parameter" and "capability"
#: are not the same thing: a body-size cap is optional and composes nothing.
#: The signature test below fails on an optional parameter that appears in
#: neither list, so a capability added later cannot pass unnoticed.
_CAPABILITY_PORTS = frozenset(
    {
        "surface",
        "open_query",
        "auth",
        "spend_lease",
        "ingest",
        "connectors",
        "pipeline_readiness",
        "documents",
        "graph",
        "build_info",
    }
)

#: Optional `build_api` parameters that tune behaviour without composing one.
#:
#: A body-size cap changes how a request is handled, not what the deployment
#: can do. Keeping the two lists apart is what lets the signature check be
#: exhaustive: every optional parameter must be one or the other, deliberately.
_POLICY_PARAMETERS = frozenset({"ingest_body_max_bytes"})


def test_every_optional_capability_is_classified() -> None:
    """The two lists above must still cover every optional `build_api` parameter.

    They are hand-kept, so they rot the moment someone adds a capability and
    does not touch them. This does not check that the exporter and the profile
    compose the same things — nothing here does, because run-time settings
    decide that — it checks the smaller thing that is actually knowable: a new
    optional parameter has been consciously classified as a capability or a
    policy knob, rather than slipping in unconsidered.

    This test was itself deleted once by an edit that rewrote the surrounding
    block, and nothing noticed until review: the claim that the mutation failed
    stayed in the commit message while the check that made it true was gone.
    Hence the count assertion in `test_this_file_still_defines_every_test_it_should`.
    """
    from rememberstack.surfaces import http_api

    optional = {
        name
        for name, parameter in inspect.signature(http_api.build_api).parameters.items()
        if parameter.default is None
    }
    assert _CAPABILITY_PORTS <= optional, (
        f"named but not an optional build_api parameter: "
        f"{sorted(_CAPABILITY_PORTS - optional)}"
    )
    unlisted = optional - _CAPABILITY_PORTS - _POLICY_PARAMETERS
    assert not unlisted, (
        f"build_api gained optional parameters not classified here: "
        f"{sorted(unlisted)}. Add a capability to _CAPABILITY_PORTS, or a knob "
        f"that composes nothing to _POLICY_PARAMETERS. If it mounts routes, "
        f"regenerate openapi.json and _SURFACE too."
    )


def test_the_exported_surface_is_exactly_the_surface_we_publish() -> None:
    """The document must declare every route, and nothing extra.

    Routes mount conditionally on ports, so an export that forgot a port
    publishes a schema missing those endpoints and looks entirely valid: a
    generated client would simply lack them, and nothing would fail until
    someone called one. Comparing the whole set is what turns that into a test
    failure instead of a support ticket.

    The unexpected direction matters too. A route appearing here that nobody
    meant to publish is a surface expansion, and the schema is exactly where
    that should be noticed.
    """
    exported = _routes(_exported())
    assert exported == set(_SURFACE), (
        f"missing: {sorted(set(_SURFACE) - exported)}\n"
        f"unexpected: {sorted(exported - set(_SURFACE))}"
    )


def test_the_schema_declares_the_credential_a_guarded_deployment_requires() -> None:
    """A contract without its auth is a contract clients cannot satisfy.

    A deployment that composes an auth perimeter rejects every unauthenticated
    call, so a client generated from a document that never mentions credentials
    compiles cleanly and then fails on the first request. Declaring the bearer
    scheme is what lets generation produce a client that can actually talk to a
    guarded deployment.
    """
    schemes = _exported().get("components", {}).get("securitySchemes", {})
    assert "HTTPBearer" in schemes, (
        "no bearer security scheme; a generated client would not send a credential"
    )


def test_the_schema_reports_the_package_version() -> None:
    """A document labelled 0.1.0 tells a consumer nothing about what it describes.

    FastAPI defaults the version, and a published asset carrying that default
    would be indistinguishable across releases — exactly the wrong property for
    an artifact whose whole job is to pin a contract to a version.
    """
    from rememberstack import __version__

    assert _exported()["info"]["version"] == __version__


def test_the_schema_carries_the_shapes_those_routes_answer_with() -> None:
    """A route without its response schema generates a client returning `any`.

    Declaring the path is only half a contract. If the component schemas were
    missing, generation would still succeed and produce untyped responses —
    the drift this whole file exists to prevent, arriving through the back
    door.
    """
    schemas = _exported().get("components", {}).get("schemas", {})
    for name in ("DocumentPage", "DocumentSummary", "DocumentVersionSummary"):
        assert name in schemas, f"{name} is missing from the published schema"
    assert "SearchRequest" in schemas, (
        "SearchRequest is missing; the POST search bodies would generate untyped"
    )


def test_documenting_the_credential_did_not_start_enforcing_it() -> None:
    """The scheme must describe the contract without ever refusing a request.

    `HTTPBearer` refuses a missing credential by default (401 on the pinned
    FastAPI, 403 on some versions — the code varies, the refusal does not).

    On a gated route that refusal is merely redundant, because `_perimeter` is
    registered first and already answers 401, so a test checking only a gated
    route would pass either way and prove nothing. The real damage is on the
    one route the perimeter deliberately exempts: `GET /healthz` is the Compose
    liveness probe and is reached without a credential by design. An
    `auto_error=True` scheme sits outside that exemption and would refuse it,
    failing the container's health check while every other route still worked —
    the kind of breakage that reads as an infrastructure fault for a long time.

    So the scheme is declared with `auto_error=False`: it never raises, and
    `_perimeter` stays the sole enforcement point. This checks both routes,
    because only the exempt one can tell the difference.
    """
    from uuid import UUID

    from export_openapi import _Boundary
    from export_openapi import _Unused
    from fastapi.testclient import TestClient

    from rememberstack.model import AuthenticatedContext
    from rememberstack.model import PerimeterCredential
    from rememberstack.surfaces.http_api import build_api

    served = UUID("11111111-2222-3333-4444-555555555555")

    class _Auth:
        """Accepts exactly one credential for the deployment under test."""

        def authenticate(
            self, *, credential: PerimeterCredential
        ) -> AuthenticatedContext:
            """Authenticate `good`; refuse anything else."""
            if credential.value.get_secret_value() == b"good":
                return AuthenticatedContext(deployment_id=served, principal="agent")
            raise ValueError("unknown credential")

    boundary = _Boundary(deployment_id=served)
    app = build_api(
        engine=_Unused(),  # type: ignore[arg-type]
        deployment_id=served,
        admission=boundary,  # type: ignore[arg-type]
        readiness=boundary,  # type: ignore[arg-type]
        auth=_Auth(),  # type: ignore[arg-type]
        documents=_Unused(),  # type: ignore[arg-type]
    )

    # The self-host profile adds this route to the built app; the liveness
    # probe's exemption lives in `_perimeter`, so reproduce the shape here.
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        """Stand in for the profile's probe, which needs no credential."""
        return {"status": "ok"}

    client = TestClient(app)

    # Gated route: refused, and with the code clients already handle.
    assert client.get("/documents").status_code == 401
    # Exempt route: still reachable with no credential at all.
    assert client.get("/healthz").status_code == 200
