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

from collections.abc import Iterator
from dataclasses import replace
import inspect
import json
from pathlib import Path
import re
import sys
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import Mock
from uuid import UUID

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from rememberstack.adapters import OpenRouterSettings
from rememberstack.adapters.selfhost import MinIOObjectStore
from rememberstack.ports.queue import TaskQueuePort
from rememberstack.profiles.selfhost import SelfHostProfile
from rememberstack.profiles.selfhost import SelfHostSettings
from rememberstack.surfaces.direct_admission import DirectPathAdmission
from rememberstack.surfaces.http_api import ApiDeployment
from rememberstack.surfaces.http_api import build_api
from rememberstack.surfaces.http_api import ConnectorManagementPort
from rememberstack.surfaces.http_api import SpendLeasePort

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
    assert len(defined) == 10, (
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
#: The list makes additions and removals explicit during review. The self-host
#: test also checks what the shipped profile actually mounts.
_SURFACE: frozenset[tuple[str, str]] = frozenset(
    (
        ("POST", "/documents/{doc_id}/archive"),
        ("POST", "/documents/{doc_id}/unarchive"),
        ("DELETE", "/documents/{doc_id}/effective-periods"),
        ("PUT", "/documents/{doc_id}/versions/{version_id}/effective-periods"),
        ("GET", "/chunks/{chunk_id}/adjacent"),
        ("GET", "/deployment"),
        ("GET", "/documents"),
        ("POST", "/documents/search"),
        ("GET", "/documents/{doc_id}/sections/{section_key}/history"),
        ("PUT", "/documents/{doc_id}/versions/{version_id}/references"),
        ("GET", "/documents/{doc_id}/versions/{version_id}/references"),
        ("POST", "/documents/references"),
        ("GET", "/hydrate/relation/{relation_id}"),
        ("GET", "/lookup/observations"),
        ("GET", "/lookup/relations"),
        ("GET", "/operations"),
        ("GET", "/processing-layers"),
        ("GET", "/query/saved"),
        ("GET", "/query/saved/{namespace}/{name}"),
        ("GET", "/query/space"),
        ("GET", "/query/space/search"),
        ("GET", "/resolve"),
        ("GET", "/search/chunks"),
        ("GET", "/search/claims"),
        ("GET", "/transcript/relation/{relation_id}"),
        ("POST", "/chunks/adjacent"),
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
        # D156: the tag registry and document tag writes.
        ("GET", "/tags"),
        ("POST", "/tags"),
        ("PATCH", "/tags/{tag_id}"),
        ("DELETE", "/tags/{tag_id}"),
        ("POST", "/documents/{doc_id}/tags"),
        # D159: forget and its records.
        ("POST", "/documents/{doc_id}/forget"),
        ("GET", "/forgets"),
        ("GET", "/forgets/{forget_id}"),
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
        "document_search",
        "section_history",
        "references",
        "document_references",
        "archive",
        "forget",
        "effective_time",
        "tags",
        "graph",
        "build_info",
        "processing_layers",
    }
)

#: Optional `build_api` parameters that tune behaviour without composing one.
#:
#: A body-size cap changes how a request is handled, not what the deployment
#: can do. Keeping the two lists apart is what lets the signature check be
#: exhaustive: every optional parameter must be one or the other, deliberately.
#: A task queue only announces the work a request created (D147); it mounts
#: no route.
_POLICY_PARAMETERS = frozenset(
    {"ingest_body_max_bytes", "direct_admission", "task_queue", "ingest_scope_routes"}
)


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
    for name in (
        "DocumentPage",
        "DocumentSummary",
        "DocumentVersionSummary",
        "AdjacentChunksRequest",
    ):
        assert name in schemas, f"{name} is missing from the published schema"
    assert "SearchRequest" in schemas, (
        "SearchRequest is missing; the POST search bodies would generate untyped"
    )


_DEPLOYMENT = UUID("11111111-2222-3333-4444-555555555555")
_DIAGNOSTICS = {
    ("GET", "/deployment"),
    ("GET", "/processing-layers"),
    ("POST", "/readiness"),
}
_LIVENESS = ("GET", "/healthz")
_OPERATOR_ROUTES = {
    ("GET", "/connectors"),
    ("POST", "/connectors"),
    ("GET", "/connectors/{connector_id}"),
    ("POST", "/connectors/{connector_id}/pause"),
}


@pytest.fixture
def selfhost_api() -> Iterator[tuple[FastAPI, Engine]]:
    """Compose the shipped profile with a real perimeter and no external services."""
    store = Mock(spec=MinIOObjectStore)
    database = create_engine("postgresql+psycopg://localhost/unused")

    def refuse_connect() -> None:
        """Fail before opening any database connection."""
        raise AssertionError(
            "offline API boundary test tried to connect to the database"
        )

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(database, "connect", refuse_connect)
        profile = SelfHostProfile(
            settings=SelfHostSettings(
                deployment_id=_DEPLOYMENT,
                require_api_auth=True,
                api_bearer_token=SecretStr("test-perimeter-token"),
            ),
            engine=database,
            raw_store=store,
            artifact_store=store,
            corpusfs_store=store,
            model_provider=Mock(),
            model_provider_settings=OpenRouterSettings(api_key="unused-test-key"),
        )
        try:
            yield profile.api(), database
        finally:
            database.dispose()


def _app_routes(*, app: FastAPI) -> dict[tuple[str, str], APIRoute]:
    """Inventory every registered operation, including ones hidden from OpenAPI."""
    assert all(isinstance(route, APIRoute) for route in app.routes)
    return {
        (method, route.path_format): route
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }


def _assert_rejected_before_routing(
    *, app: FastAPI, routes: set[tuple[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the ASGI auth gate before routing, admission, spending or queueing."""
    deployment: ApiDeployment = app.state.api_deployment
    admission = Mock(spec=DirectPathAdmission)
    lease = Mock(spec=SpendLeasePort)
    queue = Mock(spec=TaskQueuePort)
    app.state.api_deployment = replace(
        deployment, direct_admission=admission, spend_lease=lease, task_queue=queue
    )
    router = AsyncMock(side_effect=AssertionError("request reached the router"))
    monkeypatch.setattr(app.router, "middleware_stack", router)

    client = TestClient(app)
    for method, template in sorted(routes):
        path = re.sub(r"\{[^}]+\}", str(_DEPLOYMENT), template)
        for headers, detail in (
            ({}, "a perimeter credential is required"),
            (
                {"Authorization": "Bearer wrong-token"},
                "perimeter authentication failed",
            ),
        ):
            response = client.request(
                method, path, headers=headers, follow_redirects=False
            )
            assert (response.status_code, response.json()) == (
                401,
                {"detail": detail},
            ), (method, path, detail)

    router.assert_not_awaited()
    assert not admission.mock_calls
    assert not lease.mock_calls
    assert not queue.mock_calls


def test_selfhost_inventory_and_auth_match_the_export(
    selfhost_api: tuple[FastAPI, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Check audience and bearer metadata and reject both credential cases on every route."""
    app, _ = selfhost_api
    routes = _app_routes(app=app)
    assert set(routes) == _SURFACE | {_LIVENESS}

    exported = _exported()
    assert {
        operation for operation, route in routes.items() if route.include_in_schema
    } == _routes(exported)
    assert app.openapi() == exported
    for method, path in _SURFACE:
        expected_tags = (
            ["client", "diagnostics"] if (method, path) in _DIAGNOSTICS else ["client"]
        )
        operation = exported["paths"][path][method.lower()]
        assert routes[method, path].tags == operation["tags"] == expected_tags
        assert operation["security"] == [{"HTTPBearer": []}]

    _assert_rejected_before_routing(
        app=app, routes=set(_SURFACE), monkeypatch=monkeypatch
    )


def test_selfhost_liveness_is_a_public_probe(
    selfhost_api: tuple[FastAPI, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Compose probe can reach PostgreSQL without a perimeter credential."""
    app, database = selfhost_api
    route = _app_routes(app=app)[_LIVENESS]
    assert route.tags == ["liveness"]
    assert not route.include_in_schema

    connect = MagicMock()
    monkeypatch.setattr(database, "connect", connect)
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    execute = connect.return_value.__enter__.return_value.execute
    execute.assert_called_once()
    assert str(execute.call_args.args[0]) == "SELECT 1"
    assert client.post("/healthz").status_code == 401


def test_opt_in_connector_routes_are_protected_operator_operations(
    selfhost_api: tuple[FastAPI, Engine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A host can mount connector management without publishing it to stock clients."""
    stock, _ = selfhost_api
    deployment: ApiDeployment = stock.state.api_deployment
    connectors = Mock(spec=ConnectorManagementPort)
    app = build_api(
        engine=deployment.engine,
        deployment_id=deployment.deployment_id,
        auth=deployment.auth,
        connectors=connectors,
    )
    routes = _app_routes(app=app)
    assert set(routes) - _SURFACE == _OPERATOR_ROUTES
    exported = app.openapi()
    for method, path in _OPERATOR_ROUTES:
        operation = exported["paths"][path][method.lower()]
        assert routes[method, path].tags == operation["tags"] == ["operator"]
        assert operation["security"] == [{"HTTPBearer": []}]
    _assert_rejected_before_routing(
        app=app, routes=_OPERATOR_ROUTES, monkeypatch=monkeypatch
    )
    assert not connectors.mock_calls
