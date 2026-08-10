"""WP-5.4 acceptance: surface parity (retrieval §7, D50-D51).

The API, CLI, and MCP surfaces render the SAME operation registry, so parity is a
property, not a promise:

- **The tool list IS the registry.** The MCP `tools/list`, the API `/operations`,
  and the registry's active rows are the same set.
- **One operation, one answer, every surface.** Running a operation through the API,
  through MCP, and through the CLI (an HTTP client of the API) returns the same
  envelope — because all three render one `OperationSurface`.
- **The API is the enforcement point.** With an auth port, every endpoint is
  gated on a perimeter credential for THIS deployment (retrieval §9).
- **Failures are typed, not crashes.** An unknown operation is a 404 / MCP error;
  a missing required argument is a 422 / MCP error.
"""

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any
from typing import cast
from uuid import UUID
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rememberstack.adapters.testing import FakeModelProvider
from rememberstack.core import AssuredOperationLintError
from rememberstack.model import AuthenticatedContext
from rememberstack.model import DeploymentBootstrapInput
from rememberstack.model import P1ChunkText
from rememberstack.model import PerimeterCredential
from rememberstack.spine import AssuredOperationRegistry
from rememberstack.spine import CANONICAL_OPERATIONS
from rememberstack.spine import DeploymentBootstrapper
from rememberstack.spine import seed_canonical_operations
from rememberstack.spine.settings import load_database_settings
from rememberstack.surfaces import build_api
from rememberstack.surfaces import OperationExecutor
from rememberstack.surfaces import OperationMcpServer
from rememberstack.surfaces import OperationSurface
from rememberstack.surfaces import QueryEngine
from rememberstack.surfaces.cli import _split_operation_arg
from rememberstack.surfaces.cli import operations_list
from rememberstack.surfaces.cli import operations_run

_ROOT = Path(__file__).resolve().parents[3]
_DEPLOYMENT_ID = UUID("54000000-0000-0000-0000-000000000001")
_OTHER_DEPLOYMENT = UUID("54000000-0000-0000-0000-0000000000ff")


class _OpenBoundary:
    """Keep surface fixtures open across readiness and admission checks."""

    def ensure_ready(self, *, deployment_id: UUID) -> tuple[UUID, ...]:
        return ()

    def assert_available(self, *, deployment_id: UUID) -> None:
        pass


class _NullSearchIndex:
    """Unused P1 stub."""

    def search_claims(
        self,
        *,
        deployment_id: str,
        vector: tuple[float, ...],
        k: int,
        current_only: bool,
    ) -> tuple[str, ...]:
        """Never called."""
        return ()

    def search_claims_lexical(self, **_: object) -> tuple[str, ...]:
        """Never called."""
        return ()

    def search_chunks(self, **_: object) -> tuple[str, ...]:
        """Never called."""
        return ()

    def search_chunks_lexical(self, **_: object) -> tuple[str, ...]:
        """Never called."""
        return ()

    def chunk_texts(self, **_: object) -> dict[str, P1ChunkText]:
        """Never called."""
        return {}

    def search_facts(
        self, *, deployment_id: str, vector: tuple[float, ...], k: int, kind: str | None
    ) -> tuple[str, ...]:
        """Never called."""
        return ()


class _FakeAuth:
    """A perimeter port: `good` authenticates here, `other` to a different
    deployment, anything else fails."""

    def authenticate(self, *, credential: PerimeterCredential) -> AuthenticatedContext:
        """Map the opaque credential to a principal, or raise."""
        value = credential.value.get_secret_value()
        if value == b"good":
            return AuthenticatedContext(deployment_id=_DEPLOYMENT_ID, principal="agent")
        if value == b"other":
            return AuthenticatedContext(
                deployment_id=_OTHER_DEPLOYMENT, principal="agent"
            )
        raise ValueError("unknown credential")


@pytest.fixture(scope="module")
def database_engine() -> Iterator[Engine]:
    """Apply structural head and expose the accepted PostgreSQL engine."""
    try:
        database_url = load_database_settings().sqlalchemy_url()
    except ValidationError:
        pytest.skip("REMEMBERSTACK_DATABASE_URL is required for real surface proofs")
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.downgrade(config=config, revision="base")
    command.upgrade(config=config, revision="head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


class _Deployment:
    """A seeded deployment: one relation, the canonical operations, the surfaces."""

    def __init__(self, *, engine: Engine) -> None:
        """Seed a fact, register the operations, and compose the surfaces."""
        self.engine = engine
        self.alice = uuid4()
        acme = uuid4()
        with engine.begin() as connection:
            for entity_id, kind, name in (
                (self.alice, "Person", "Alice"),
                (acme, "Organization", "Acme"),
            ):
                connection.execute(
                    text(
                        "INSERT INTO entities (entity_id, deployment_id, type,"
                        " canonical_name, normalized_name)"
                        " VALUES (:e, :d, :t, :n, lower(:n))"
                    ),
                    {"e": entity_id, "d": _DEPLOYMENT_ID, "t": kind, "n": name},
                )
            connection.execute(
                text(
                    "INSERT INTO relations (relation_id, deployment_id,"
                    " subject_entity_id, predicate, object_entity_id,"
                    " normalizer_version, fact_label, evidence_count, valid_from,"
                    " ingested_at) VALUES (:r, :d, :s, 'works_for', :o, 'toy',"
                    " 'Alice works for Acme.', 2, '2024-01-01+00', now())"
                ),
                {"r": uuid4(), "d": _DEPLOYMENT_ID, "s": self.alice, "o": acme},
            )
        self.registry = AssuredOperationRegistry(engine=engine)
        seed_canonical_operations(registry=self.registry, deployment_id=_DEPLOYMENT_ID)
        query_engine = QueryEngine(
            engine=engine,
            search_index=_NullSearchIndex(),
            model_provider=FakeModelProvider(generate_payloads={}),
            embedding_model="toy",
        )
        self.surface = OperationSurface(
            registry=self.registry,
            executor=OperationExecutor(query_engine=query_engine),
            deployment_id=_DEPLOYMENT_ID,
        )
        self.mcp = OperationMcpServer(surface=self.surface)
        self.app = build_api(
            engine=query_engine,
            deployment_id=_DEPLOYMENT_ID,
            admission=_OpenBoundary(),
            readiness=_OpenBoundary(),
            surface=self.surface,
        )
        self.client = TestClient(self.app)


@pytest.fixture()
def deployment(database_engine: Engine) -> _Deployment:
    """A fresh seeded deployment per proof."""
    with database_engine.begin() as connection:
        connection.execute(statement=text("TRUNCATE TABLE deployments CASCADE"))
    DeploymentBootstrapper(engine=database_engine).bootstrap_deployment(
        deployment_input=DeploymentBootstrapInput(
            deployment_id=_DEPLOYMENT_ID,
            slug="surface-test",
            name="Surface parity proofs",
            default_language="en",
            raw_bucket="mem://raw",
            artifacts_bucket="mem://artifacts",
            corpusfs_bucket="mem://corpusfs",
        )
    )
    return _Deployment(engine=database_engine)


def _payload(envelope: dict[str, object]) -> dict[str, object]:
    """An envelope dict minus the per-call wall-clock stamps."""
    payload = {key: value for key, value in envelope.items() if key != "freshness"}
    scope = payload.get("temporal_scope")
    if isinstance(scope, dict):
        payload["temporal_scope"] = {
            key: value
            for key, value in scope.items()
            if key not in {"evaluated_at", "believed_at"}
        }
    return payload


def test_the_tool_list_is_the_registry(deployment: _Deployment) -> None:
    """Operation tools on MCP match the registry and API (D50).

    This composition is operation-only (no ingest/readiness ports), so Layer 1
    write tools are correctly absent. When those ports are composed, static
    write tools lead the list — covered in test_mcp_memory_tools.
    """
    registry_names = {
        operation.name.value
        for operation in deployment.registry.active(deployment_id=_DEPLOYMENT_ID)
    }
    tools = cast("list[dict[str, Any]]", deployment.mcp.list_tools()["tools"])
    mcp_names = {tool["name"] for tool in tools}
    api_names = {
        descriptor["name"] for descriptor in deployment.client.get("/operations").json()
    }
    expected = {"resolve_entity", "testimony_context", "fact_context", "answer_context"}
    assert mcp_names == registry_names == expected
    assert api_names == registry_names
    # and the tool carries its JSON-Schema input contract
    tool = next(t for t in tools if t["name"] == "resolve_entity")
    assert tool["inputSchema"]["required"] == ["name"]


def test_all_three_surfaces_return_the_same_envelope(
    deployment: _Deployment, capsys: pytest.CaptureFixture[str]
) -> None:
    """Running one operation through API, MCP, and the CLI returns the same
    envelope — all three render one OperationSurface (parity is a property)."""
    arguments: dict[str, object] = {"name": "Alice"}

    api_envelope = deployment.client.post(
        "/operations/resolve_entity", json=arguments
    ).json()

    mcp_result = deployment.mcp.call_tool(name="resolve_entity", arguments=arguments)
    assert mcp_result["isError"] is False
    content = cast("list[dict[str, Any]]", mcp_result["content"])
    mcp_envelope = json.loads(content[0]["text"])

    # the CLI is an httpx.Client of the API; the TestClient is exactly that,
    # routed in-process to the ASGI app
    exit_code = operations_run(
        client=deployment.client, name="resolve_entity", arg_pairs=["name=Alice"]
    )
    assert exit_code == 0
    cli_envelope = json.loads(capsys.readouterr().out)

    assert _payload(api_envelope) == _payload(mcp_envelope) == _payload(cli_envelope)
    # and it is the real answer
    assert api_envelope["entities"][0]["canonical_name"] == "Alice"


def test_the_cli_operations_list_matches_the_api(
    deployment: _Deployment, capsys: pytest.CaptureFixture[str]
) -> None:
    """`remember operations list` prints exactly the API operation list."""
    assert operations_list(client=deployment.client) == 0
    listed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert {row["name"] for row in listed} == {
        descriptor["name"] for descriptor in deployment.client.get("/operations").json()
    }


def test_unknown_operation_and_missing_argument_are_typed_failures(
    deployment: _Deployment,
) -> None:
    """An unknown operation is a 404 / MCP error; a missing required argument is a
    422 / MCP error — never a crash across the wire."""
    assert deployment.client.post("/operations/teleport", json={}).status_code == 404
    assert (
        deployment.client.post("/operations/resolve_entity", json={}).status_code == 422
    )

    unknown = deployment.mcp.call_tool(name="teleport", arguments={})
    assert unknown["isError"] is True
    missing = deployment.mcp.call_tool(name="resolve_entity", arguments={})
    assert missing["isError"] is True


def test_the_auth_perimeter_gates_every_endpoint(deployment: _Deployment) -> None:
    """With an auth port the API enforces a perimeter credential for THIS
    deployment: no header 401, a wrong-deployment credential 403, a valid one
    200 (retrieval §9 — the single enforcement point)."""
    guarded = build_api(
        engine=QueryEngine(
            engine=deployment.engine,
            search_index=_NullSearchIndex(),
            model_provider=FakeModelProvider(generate_payloads={}),
            embedding_model="toy",
        ),
        deployment_id=_DEPLOYMENT_ID,
        admission=_OpenBoundary(),
        readiness=_OpenBoundary(),
        surface=deployment.surface,
        auth=_FakeAuth(),
    )
    client = TestClient(guarded)
    assert client.get("/operations").status_code == 401  # no credential
    assert (
        client.get("/operations", headers={"Authorization": "Bearer nope"}).status_code
        == 401  # authentication failed
    )
    assert (
        client.get("/operations", headers={"Authorization": "Bearer other"}).status_code
        == 403  # a credential for another deployment
    )
    assert (
        client.get("/operations", headers={"Authorization": "Bearer good"}).status_code
        == 200  # admitted
    )


# --- regression proofs for the Codex review fixes --------------------------


def test_invalid_and_unknown_arguments_are_typed_failures(
    deployment: _Deployment,
) -> None:
    """A wrong-typed argument or a misspelled parameter is a 422 / MCP error,
    never a 500 or a silently-broadened query (Codex findings)."""
    bad_integer = deployment.client.post(
        "/operations/fact_context", json={"query": "Alice", "k": "not-an-integer"}
    )
    assert bad_integer.status_code == 422
    assert bad_integer.json()["detail"]["code"] == "invalid_parameter"

    bad_query = deployment.client.post(
        "/operations/testimony_context", json={"query": 42}
    )
    assert bad_query.status_code == 422
    assert bad_query.json()["detail"]["code"] == "invalid_parameter"

    empty_time = deployment.client.post(
        "/operations/fact_context", json={"query": "Alice", "time": {}}
    )
    assert empty_time.status_code == 422
    assert empty_time.json()["detail"]["code"] == "invalid_parameter"

    for time in (
        {"mode": "at", "at": "2026-08-10T12:00:00"},
        {
            "mode": "overlap",
            "from": "2026-08-10T12:00:00",
            "to": "2026-08-10T13:00:00Z",
        },
    ):
        naive_time = deployment.client.post(
            "/operations/fact_context", json={"query": "Alice", "time": time}
        )
        assert naive_time.status_code == 422
        assert naive_time.json()["detail"]["code"] == "invalid_parameter"

    typo = deployment.client.post("/operations/resolve_entity", json={"naem": "Alice"})
    assert typo.status_code == 422  # a typo never silently broadens the query

    mcp_bad = deployment.mcp.call_tool(
        name="fact_context", arguments={"query": "Alice", "k": "not-an-integer"}
    )
    assert mcp_bad["isError"] is True

    wrong_json_types = (
        {"query": "Alice", "k": "2"},
        {"query": "Alice", "entity_ids": json.dumps([str(uuid4())])},
        {"query": "Alice", "time": json.dumps({"mode": "current"})},
        {"query": "Alice", "time": {"mode": "at", "at": 0}},
        {"query": "Alice", "time": {"mode": "at", "at": "0"}},
    )
    for arguments in wrong_json_types:
        response = deployment.client.post("/operations/fact_context", json=arguments)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "invalid_parameter"

    integral_json_number = deployment.client.post(
        "/operations/fact_context", json={"query": "Alice", "k": 2.0}
    )
    assert integral_json_number.status_code == 200


def test_cli_operation_arguments_parse_json_before_strict_dispatch() -> None:
    """CLI syntax adapts JSON literals; API and MCP dispatch never coerce them."""
    assert _split_operation_arg("query=Who leads?") == ("query", "Who leads?")
    assert _split_operation_arg("k=2") == ("k", 2)
    assert _split_operation_arg('time={"mode":"current"}') == (
        "time",
        {"mode": "current"},
    )


def test_the_api_and_surface_must_serve_one_deployment(deployment: _Deployment) -> None:
    """Composing an API with a surface bound to a DIFFERENT deployment is
    refused — one deployment is one trust domain (Codex finding)."""
    mismatched = OperationSurface(
        registry=deployment.registry,
        executor=OperationExecutor(
            query_engine=QueryEngine(
                engine=deployment.engine,
                search_index=_NullSearchIndex(),
                model_provider=FakeModelProvider(generate_payloads={}),
                embedding_model="toy",
            )
        ),
        deployment_id=_OTHER_DEPLOYMENT,
    )
    with pytest.raises(ValueError, match="trust domain"):
        build_api(
            engine=QueryEngine(
                engine=deployment.engine,
                search_index=_NullSearchIndex(),
                model_provider=FakeModelProvider(generate_payloads={}),
                embedding_model="toy",
            ),
            deployment_id=_DEPLOYMENT_ID,
            admission=_OpenBoundary(),
            readiness=_OpenBoundary(),
            surface=mismatched,
        )


def test_a_custom_version_cannot_replace_an_assured_operation(
    deployment: _Deployment,
) -> None:
    """The closed operation namespace serves only the canonical version."""
    with pytest.raises(AssuredOperationLintError, match="canonical version 1"):
        deployment.registry.register(
            deployment_id=_DEPLOYMENT_ID,
            operation=CANONICAL_OPERATIONS[0].model_copy(update={"version": 2}),
        )
    names = [descriptor.name for descriptor in deployment.surface.descriptors()]
    assert names.count("resolve_entity") == 1
    tool = next(
        d for d in deployment.surface.descriptors() if d.name == "resolve_entity"
    )
    assert not tool.description.startswith("v2")
    # and the schema forbids stray properties (a typo is a schema violation)
    assert tool.input_schema["additionalProperties"] is False


def test_the_cli_rejects_a_malformed_argument(deployment: _Deployment) -> None:
    """`--arg` without `key=value`, or with an empty key, is a usage error
    (exit 2), not a crash (Codex finding on _split_arg)."""
    assert (
        operations_run(
            client=deployment.client, name="resolve_entity", arg_pairs=["novalue"]
        )
        == 2
    )
    assert (
        operations_run(
            client=deployment.client, name="resolve_entity", arg_pairs=["=v"]
        )
        == 2
    )


def test_the_cli_reports_an_unreachable_api_as_an_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query against an API that is not up is a controlled exit code, not a
    traceback (Codex finding)."""
    from rememberstack.surfaces import cli

    monkeypatch.setenv(
        "REMEMBERSTACK_API_URL", "http://127.0.0.1:9"
    )  # nothing listening
    assert cli.main(["operations", "list"]) == 1
